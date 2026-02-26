"""
AnomalyDetector — trend-analysis layer for JARVIS.

Distinct from SafetyEngine, which checks instantaneous thresholds.
AnomalyDetector asks: "Is this metric behaving unusually compared to its
recent history?"

Detectors:
  1. battery_sag          — voltage dropping faster than normal discharge rate
  2. battery_current_spike— current suddenly much higher than recent baseline
  3. vibration_escalation — vibration trend increasing (bearing/prop wear)
  4. ekf_instability      — EKF oscillating in/out of healthy state (flapping)
  5. gps_degradation      — satellite count or HDOP trending in wrong direction
  6. uncontrolled_descent — descending rapidly outside of LANDING / AGGRESSIVE
  7. altitude_hold_failure— altitude drifting in a hold mode (LOITER / POSHOLD)

Anomaly lifecycle:
  INACTIVE → ACTIVE (condition first detected, callback fired once)
  ACTIVE   → RESOLVED (condition cleared + dwell elapsed, callback fired once)
  RESOLVED → ACTIVE (re-fires if condition returns)

Feeds:
  - JARVIS  : anomaly snapshot included in LLM context
  - UI/Voice: anomaly_fn callback emits alert
  - (Future): direct Safety Engine input for early RTL triggers

Design principles:
  - No MAVLink dependency — only DroneState + FlightPhaseDetector
  - No NumPy — pure-Python linear regression (O(n), n ≤ 120)
  - Rate-independent: self-limits to ≤ 2.5 Hz regardless of call frequency
  - Thread-safe: tick() can be called from the telemetry thread
"""

import dataclasses
import logging
import threading
import time
from collections import deque
from typing import Callable, Optional

from drone_state import DroneState
from flight_phase import FlightPhase, FlightPhaseDetector

anomaly_logger = logging.getLogger("anomaly_detector")

# ---------------------------------------------------------------------------
# Tick rate guard — windows are calibrated for ~2 Hz
# ---------------------------------------------------------------------------
_MIN_TICK_INTERVAL_S = 0.4   # seconds — process at most ~2.5 Hz

# ---------------------------------------------------------------------------
# Airborne phases (context guard — many anomalies only make sense airborne)
# ---------------------------------------------------------------------------
_AIRBORNE_PHASES = {
    FlightPhase.TAKEOFF, FlightPhase.CLIMB, FlightPhase.CRUISE,
    FlightPhase.AGGRESSIVE, FlightPhase.LANDING, FlightPhase.EMERGENCY,
}

# ---------------------------------------------------------------------------
# Detection thresholds
# ---------------------------------------------------------------------------

# Battery sag (voltage slope in V/sample at ~2 Hz)
BATT_SAG_WARN_V_PER_S    = -0.025  # V/s  — ~1.5 V/min (warn)
BATT_SAG_CRIT_V_PER_S    = -0.06   # V/s  — ~3.6 V/min (critical)
BATT_SAG_MIN_VOLTAGE     =  3.0    # V    — ignore below this (dead/invalid)
BATT_SAG_WINDOW          = 20      # samples for slope (~10 s at 2 Hz)

# Battery current spike (multiplier of rolling baseline)
BATT_SPIKE_MULTIPLIER    =  3.0    # current > 3× baseline → spike
BATT_SPIKE_BASELINE_WIN  = 60      # samples for baseline (~30 s)
BATT_SPIKE_DETECT_WIN    = 3       # consecutive samples above multiplier

# Vibration escalation (slope in m/s² per sample at ~2 Hz)
VIB_ESCALATION_WARN      =  0.08   # m/s²/s — vibration increasing (warn)
VIB_ESCALATION_CRIT      =  0.20   # m/s²/s — vibration increasing rapidly (critical)
VIB_MIN_TRIGGER          = 10.0    # m/s²   — only track if already above this
VIB_WINDOW               = 60      # samples (~30 s at 2 Hz)

# EKF instability (flap counting)
EKF_FLAP_THRESHOLD       =  4      # True→False transitions in window → flapping
EKF_FLAP_WINDOW          = 20      # samples (~10 s at 2 Hz)

# GPS degradation
GPS_SAT_DROP_THRESHOLD   =  3      # satellites lost in window → degrading
GPS_HDOP_RISE_THRESHOLD  =  0.8    # HDOP rise in window → degrading
GPS_WINDOW               = 30      # samples (~15 s at 2 Hz)

# Uncontrolled descent
DESCENT_WARN_MS          = -2.5    # m/s  — below this in cruise → concerning
DESCENT_CRIT_MS          = -4.0    # m/s  — below this in cruise → critical
DESCENT_SUSTAIN_SAMPLES  =  4      # consecutive samples required

# Altitude hold failure (in position-hold modes)
ALT_HOLD_DRIFT_M         =  2.5    # metres drift in window → hold failing
ALT_HOLD_WINDOW          = 10      # samples (~5 s at 2 Hz)
_ALT_HOLD_MODE_IDS       = {2, 5, 16}  # ALT_HOLD, LOITER, POSHOLD

# Dwell before resolving an active anomaly (seconds)
RESOLVE_DWELL_S          =  5.0


# ---------------------------------------------------------------------------
# Anomaly record
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Anomaly:
    anomaly_id:   str
    severity:     str           # 'info' | 'warning' | 'critical'
    title:        str
    description:  str           # human-readable detail with numbers
    phase:        str           # FlightPhase.value when detected
    metric_value: float         # the value that triggered detection
    metric_trend: float         # slope / rate of change (0.0 if N/A)
    active:       bool = True
    detected_at:  float = dataclasses.field(default_factory=time.time)
    resolved_at:  Optional[float] = None


# ---------------------------------------------------------------------------
# AnomalyDetector
# ---------------------------------------------------------------------------

class AnomalyDetector:
    """
    Trend-analysis anomaly detector.

    Usage::

        detector = AnomalyDetector()
        detector.set_anomaly_fn(lambda a: emit_alert(a.anomaly_id, a.severity, ...))

        # Called every telemetry cycle — self-rate-limits internally:
        detector.tick(drone_state, phase_detector)

        # For JARVIS context:
        context = detector.snapshot()
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._last_tick: float = 0.0

        # ── Sliding windows ───────────────────────────────────────────────
        self._volt_hist   = deque(maxlen=120)   # battery voltage (V)
        self._curr_hist   = deque(maxlen=60)    # current draw (A)
        self._vib_hist    = deque(maxlen=60)    # vibration peak (m/s²)
        self._ekf_hist    = deque(maxlen=20)    # EKF ok flag (bool)
        self._sat_hist    = deque(maxlen=30)    # satellite count
        self._hdop_hist   = deque(maxlen=30)    # HDOP
        self._climb_hist  = deque(maxlen=10)    # climb rate (m/s)
        self._alt_hist    = deque(maxlen=10)    # relative altitude (m)

        # ── Active anomaly registry: anomaly_id → Anomaly ─────────────────
        self._active:   dict[str, Anomaly] = {}

        # ── Resolved anomaly history (last 20) ────────────────────────────
        self._resolved: list[Anomaly] = []

        # ── Per-anomaly resolve-dwell timers: id → timestamp when clear ───
        self._clear_since: dict[str, float] = {}

        # ── Callback ──────────────────────────────────────────────────────
        self._anomaly_fn: Optional[Callable] = None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def set_anomaly_fn(self, fn: Callable) -> None:
        """
        Register a callback invoked on every anomaly state change:
            fn(anomaly: Anomaly) → None

        Called on ACTIVE (new anomaly) and RESOLVED (condition cleared).
        """
        with self._lock:
            self._anomaly_fn = fn

    def tick(self, state: DroneState, detector: FlightPhaseDetector) -> None:
        """
        Run all detectors. Self-rate-limits to ≤ 2.5 Hz.
        Thread-safe — safe to call from the telemetry thread.
        """
        now = time.time()
        if now - self._last_tick < _MIN_TICK_INTERVAL_S:
            return
        self._last_tick = now

        phase = detector.phase

        if phase == FlightPhase.BOOT:
            return

        with self._lock:
            self._ingest(state)
            self._detect_battery_sag(state, phase, now)
            self._detect_battery_current_spike(state, phase, now)
            self._detect_vibration_escalation(state, phase, now)
            self._detect_ekf_instability(state, phase, now)
            self._detect_gps_degradation(state, phase, now)
            self._detect_uncontrolled_descent(state, phase, now)
            self._detect_altitude_hold_failure(state, phase, now)

    @property
    def active_anomalies(self) -> list:
        """Current active anomaly list (thread-safe copy)."""
        with self._lock:
            return list(self._active.values())

    def snapshot(self) -> dict:
        """
        Serialisable summary for JARVIS context and UI inspection.
        Includes active anomalies and recent resolved ones.
        """
        with self._lock:
            active = [dataclasses.asdict(a) for a in self._active.values()]
            recent = [dataclasses.asdict(a) for a in self._resolved[-5:]]
            return {
                "active_anomaly_count": len(active),
                "active_anomalies":     active,
                "recent_resolved":      recent,
            }

    # -----------------------------------------------------------------------
    # Data ingestion — called within self._lock
    # -----------------------------------------------------------------------

    def _ingest(self, state: DroneState) -> None:
        """Push latest state values into all sliding windows."""
        if state.battery_voltage > BATT_SAG_MIN_VOLTAGE:
            self._volt_hist.append(state.battery_voltage)
        if state.current_a >= 0:
            self._curr_hist.append(state.current_a)

        vib_peak = max(state.vib_x, state.vib_y, state.vib_z)
        if vib_peak >= 0:
            self._vib_hist.append(vib_peak)

        self._ekf_hist.append(state.ekf_ok)
        self._sat_hist.append(state.satellites)

        if state.hdop < 99.0:
            self._hdop_hist.append(state.hdop)

        self._climb_hist.append(state.climb_rate_ms)
        self._alt_hist.append(state.rel_altitude_m)

    # -----------------------------------------------------------------------
    # Individual detectors — all called within self._lock
    # -----------------------------------------------------------------------

    def _detect_battery_sag(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """Voltage dropping faster than normal discharge."""
        if len(self._volt_hist) < BATT_SAG_WINDOW:
            self._resolve("battery_sag", now)
            return

        recent = list(self._volt_hist)[-BATT_SAG_WINDOW:]
        slope_per_sample = _slope(recent)
        slope_per_sec    = slope_per_sample * (1.0 / _MIN_TICK_INTERVAL_S)

        if slope_per_sec <= BATT_SAG_CRIT_V_PER_S:
            self._raise(
                "battery_sag", "critical",
                "Battery Voltage Sag — Critical",
                f"Voltage dropping at {slope_per_sec:.3f} V/s "
                f"({slope_per_sec * 60:.1f} V/min). "
                "Possible cell damage or severe overcurrent.",
                phase=phase.value,
                metric_value=state.battery_voltage,
                metric_trend=slope_per_sec,
                now=now,
            )
        elif slope_per_sec <= BATT_SAG_WARN_V_PER_S:
            self._raise(
                "battery_sag", "warning",
                "Battery Voltage Sag — Elevated",
                f"Voltage dropping at {slope_per_sec:.3f} V/s "
                f"({slope_per_sec * 60:.1f} V/min). "
                "Monitor closely and consider early return.",
                phase=phase.value,
                metric_value=state.battery_voltage,
                metric_trend=slope_per_sec,
                now=now,
            )
        else:
            self._resolve("battery_sag", now)

    def _detect_battery_current_spike(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """Current suddenly much higher than recent baseline."""
        if len(self._curr_hist) < BATT_SPIKE_BASELINE_WIN:
            return

        baseline_samples = list(self._curr_hist)[:-BATT_SPIKE_DETECT_WIN]
        if not baseline_samples:
            return
        baseline_avg = sum(baseline_samples) / len(baseline_samples)

        if baseline_avg < 0.5:
            # No meaningful baseline current (on ground / idle)
            self._resolve("battery_current_spike", now)
            return

        recent_spike = list(self._curr_hist)[-BATT_SPIKE_DETECT_WIN:]
        all_above = all(
            v > baseline_avg * BATT_SPIKE_MULTIPLIER for v in recent_spike
        )

        if all_above:
            peak = max(recent_spike)
            self._raise(
                "battery_current_spike", "warning",
                "Abnormal Current Draw",
                f"Current {peak:.1f} A is {peak / baseline_avg:.1f}× "
                f"above baseline ({baseline_avg:.1f} A). "
                "Check for motor or ESC fault.",
                phase=phase.value,
                metric_value=peak,
                metric_trend=peak - baseline_avg,
                now=now,
            )
        else:
            self._resolve("battery_current_spike", now)

    def _detect_vibration_escalation(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """Vibration trend increasing over time — indicates mechanical wear."""
        if phase not in _AIRBORNE_PHASES:
            self._resolve("vibration_escalation", now)
            return

        if len(self._vib_hist) < VIB_WINDOW:
            return

        vals = list(self._vib_hist)
        current_peak = vals[-1]

        if current_peak < VIB_MIN_TRIGGER:
            self._resolve("vibration_escalation", now)
            return

        slope_per_sample = _slope(vals)
        slope_per_sec    = slope_per_sample * (1.0 / _MIN_TICK_INTERVAL_S)

        if slope_per_sec >= VIB_ESCALATION_CRIT:
            self._raise(
                "vibration_escalation", "critical",
                "Vibration Escalating — Critical",
                f"Vibration peak {current_peak:.1f} m/s² rising at "
                f"{slope_per_sec:.2f} m/s²/s. "
                "Possible propeller failure or motor bearing issue. Land now.",
                phase=phase.value,
                metric_value=current_peak,
                metric_trend=slope_per_sec,
                now=now,
            )
        elif slope_per_sec >= VIB_ESCALATION_WARN:
            self._raise(
                "vibration_escalation", "warning",
                "Vibration Escalating",
                f"Vibration peak {current_peak:.1f} m/s² rising at "
                f"{slope_per_sec:.2f} m/s²/s. "
                "Check propeller balance and motor mounts.",
                phase=phase.value,
                metric_value=current_peak,
                metric_trend=slope_per_sec,
                now=now,
            )
        else:
            self._resolve("vibration_escalation", now)

    def _detect_ekf_instability(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """EKF flapping in/out of healthy state (oscillation, not sustained failure)."""
        if phase not in _AIRBORNE_PHASES:
            self._resolve("ekf_instability", now)
            return

        if len(self._ekf_hist) < EKF_FLAP_WINDOW:
            return

        history = list(self._ekf_hist)
        # Count True→False transitions (healthy → degraded)
        flap_count = sum(
            1 for a, b in zip(history, history[1:]) if a and not b
        )

        if flap_count >= EKF_FLAP_THRESHOLD:
            self._raise(
                "ekf_instability", "warning",
                "EKF Instability Detected",
                f"EKF toggled {flap_count} times in the last "
                f"{EKF_FLAP_WINDOW / (1.0 / _MIN_TICK_INTERVAL_S):.0f}s. "
                "Position estimate is unreliable. Avoid autonomous modes.",
                phase=phase.value,
                metric_value=float(flap_count),
                metric_trend=0.0,
                now=now,
            )
        else:
            self._resolve("ekf_instability", now)

    def _detect_gps_degradation(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """Satellite count falling or HDOP rising over the observation window."""
        if len(self._sat_hist) < GPS_WINDOW or len(self._hdop_hist) < GPS_WINDOW:
            return

        sats  = list(self._sat_hist)
        hdops = list(self._hdop_hist)

        sat_drop  = sats[0]  - sats[-1]    # positive = sats fell
        hdop_rise = hdops[-1] - hdops[0]   # positive = HDOP worsened

        sat_degrading  = sat_drop  >= GPS_SAT_DROP_THRESHOLD and sats[-1] < 12
        hdop_degrading = hdop_rise >= GPS_HDOP_RISE_THRESHOLD and hdops[-1] > 1.5

        if sat_degrading or hdop_degrading:
            parts = []
            if sat_degrading:
                parts.append(f"satellites {sats[0]}→{sats[-1]} (−{sat_drop})")
            if hdop_degrading:
                parts.append(f"HDOP {hdops[0]:.1f}→{hdops[-1]:.1f} (+{hdop_rise:.1f})")
            self._raise(
                "gps_degradation", "warning",
                "GPS Signal Degrading",
                f"GPS worsening: {', '.join(parts)}. "
                "Avoid mission-critical autonomous modes.",
                phase=phase.value,
                metric_value=float(sats[-1]),
                metric_trend=float(-sat_drop),
                now=now,
            )
        else:
            self._resolve("gps_degradation", now)

    def _detect_uncontrolled_descent(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """Rapid descent in CRUISE/CLIMB that was not commanded."""
        # Only meaningful in cruise-like phases, not when we expect descent
        if phase not in {FlightPhase.CRUISE, FlightPhase.CLIMB, FlightPhase.ARMED_IDLE}:
            self._resolve("uncontrolled_descent", now)
            return

        if len(self._climb_hist) < DESCENT_SUSTAIN_SAMPLES:
            return

        recent = list(self._climb_hist)[-DESCENT_SUSTAIN_SAMPLES:]
        all_descending_crit = all(v <= DESCENT_CRIT_MS for v in recent)
        all_descending_warn = all(v <= DESCENT_WARN_MS for v in recent)

        if all_descending_crit:
            avg_rate = sum(recent) / len(recent)
            self._raise(
                "uncontrolled_descent", "critical",
                "Uncontrolled Descent — Critical",
                f"Sustained descent at {avg_rate:.1f} m/s in {phase.value}. "
                "Switch to ALT_HOLD or LOITER immediately.",
                phase=phase.value,
                metric_value=avg_rate,
                metric_trend=avg_rate,
                now=now,
            )
        elif all_descending_warn:
            avg_rate = sum(recent) / len(recent)
            self._raise(
                "uncontrolled_descent", "warning",
                "Unexpected Descent",
                f"Descending at {avg_rate:.1f} m/s in {phase.value}. "
                "Verify altitude hold is active.",
                phase=phase.value,
                metric_value=avg_rate,
                metric_trend=avg_rate,
                now=now,
            )
        else:
            self._resolve("uncontrolled_descent", now)

    def _detect_altitude_hold_failure(
        self, state: DroneState, phase: FlightPhase, now: float
    ) -> None:
        """Altitude drifting in a position-hold flight mode."""
        if state.flight_mode_id not in _ALT_HOLD_MODE_IDS:
            self._resolve("altitude_hold_failure", now)
            return

        if phase not in _AIRBORNE_PHASES:
            self._resolve("altitude_hold_failure", now)
            return

        if len(self._alt_hist) < ALT_HOLD_WINDOW:
            return

        alts  = list(self._alt_hist)
        drift = abs(alts[-1] - alts[0])

        if drift >= ALT_HOLD_DRIFT_M:
            self._raise(
                "altitude_hold_failure", "warning",
                "Altitude Hold Degraded",
                f"{state.flight_mode} mode: altitude drifted {drift:.1f} m "
                f"over ~{ALT_HOLD_WINDOW * _MIN_TICK_INTERVAL_S:.0f}s. "
                "Check barometer interference and EKF health.",
                phase=phase.value,
                metric_value=drift,
                metric_trend=alts[-1] - alts[0],
                now=now,
            )
        else:
            self._resolve("altitude_hold_failure", now)

    # -----------------------------------------------------------------------
    # Anomaly state machine — called within self._lock
    # -----------------------------------------------------------------------

    def _raise(
        self,
        anomaly_id:   str,
        severity:     str,
        title:        str,
        description:  str,
        phase:        str,
        metric_value: float,
        metric_trend: float,
        now:          float,
    ) -> None:
        """
        Activate an anomaly.  If it transitions INACTIVE→ACTIVE, fires
        the callback.  If it's already active but severity escalated,
        fires the callback again with the updated record.
        """
        # Clear any pending resolve dwell for this id
        self._clear_since.pop(anomaly_id, None)

        existing = self._active.get(anomaly_id)

        if existing is None:
            # New anomaly
            anomaly = Anomaly(
                anomaly_id=anomaly_id,
                severity=severity,
                title=title,
                description=description,
                phase=phase,
                metric_value=metric_value,
                metric_trend=metric_trend,
                active=True,
                detected_at=now,
            )
            self._active[anomaly_id] = anomaly
            anomaly_logger.warning(
                f"[ANOMALY ACTIVE] {title}: {description} "
                f"(phase={phase}, value={metric_value:.3f}, trend={metric_trend:.3f})"
            )
            self._fire(anomaly)

        elif existing.severity != severity:
            # Severity changed (e.g. warning → critical) — update and re-fire
            existing.severity     = severity
            existing.title        = title
            existing.description  = description
            existing.metric_value = metric_value
            existing.metric_trend = metric_trend
            anomaly_logger.warning(
                f"[ANOMALY ESCALATED] {title}: {description} → {severity}"
            )
            self._fire(existing)

        else:
            # Already active at same severity — just update values silently
            existing.metric_value = metric_value
            existing.metric_trend = metric_trend

    def _resolve(self, anomaly_id: str, now: float) -> None:
        """
        Begin or advance the resolve dwell for an anomaly.
        Only removes the anomaly once conditions have been clear for
        RESOLVE_DWELL_S seconds, preventing premature resolution.
        """
        if anomaly_id not in self._active:
            return   # not active — nothing to do

        first_clear = self._clear_since.setdefault(anomaly_id, now)
        if now - first_clear < RESOLVE_DWELL_S:
            return   # still in dwell — keep anomaly active

        # Dwell elapsed — resolve it
        anomaly = self._active.pop(anomaly_id)
        self._clear_since.pop(anomaly_id, None)
        anomaly.active      = False
        anomaly.resolved_at = now

        self._resolved.append(anomaly)
        if len(self._resolved) > 20:
            self._resolved = self._resolved[-20:]

        anomaly_logger.info(
            f"[ANOMALY RESOLVED] {anomaly.title} "
            f"(was active for {now - anomaly.detected_at:.1f}s)"
        )
        self._fire(anomaly)

    def _fire(self, anomaly: Anomaly) -> None:
        """Call the anomaly callback if registered. Called within _lock."""
        fn = self._anomaly_fn
        if fn is None:
            return
        # Call outside lock — capture fn reference, then fire after return.
        # Because _fire is called from _raise/_resolve which hold _lock,
        # we schedule it via a minimal daemon thread to avoid deadlock if
        # the callback re-enters AnomalyDetector (e.g. to call snapshot()).
        a = anomaly  # local ref for closure
        threading.Thread(
            target=lambda: fn(a),
            daemon=True,
        ).start()


# ---------------------------------------------------------------------------
# Pure-Python linear regression slope (no NumPy dependency)
# ---------------------------------------------------------------------------

def _slope(values: list) -> float:
    """
    Least-squares linear regression slope over the values list.
    Returns slope in units-per-sample (units/sample).
    Multiply by samples-per-second to get units/second.
    O(n) — safe at 2 Hz with window sizes up to 120 samples.
    """
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = sum(values) / n
    numerator   = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n))
    return numerator / denominator if denominator != 0.0 else 0.0
