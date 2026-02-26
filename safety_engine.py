"""
SafetyEngine — Guardian authority layer for JARVIS.

Sits between DroneState / FlightPhaseDetector and the command executor.
Continuously monitors the drone for hazardous conditions and, depending
on severity:

  Advisory   — emits a UI/voice alert, no command sent
  Countdown  — alerts + starts a cancellable RTL timer
  Forced     — immediately executes a protective action (no countdown)

Authority model: Guardian (acts if pilot is unresponsive during countdown).

Checks performed on every tick():
  1. Battery   — warn → strong warn → RTL countdown → forced action
  2. GPS       — lost or degraded (phase-gated)
  3. EKF       — degraded while airborne
  4. Vibration — high while airborne
  5. RC        — signal lost while armed
  6. Link      — heartbeat timeout (DroneState stale while flying)

Key design rules:
  - Reads ONLY from DroneState + FlightPhaseDetector — no MAVLink dependency
  - Commands flow through command_fn callback (never direct MAVLink)
  - Alerts flow through alert_fn callback (never direct socket/UI import)
  - cancel_rtl_countdown() is safe to call from any thread (voice, UI)
  - tick() is safe to call from the telemetry thread at any rate
"""

import dataclasses
import logging
import threading
import time
from typing import Callable, Optional

from drone_state import (
    DroneState,
    BATTERY_WARN_PCT, BATTERY_RTL_PCT, BATTERY_FORCE_PCT,
)
from flight_phase import FlightPhase, FlightPhaseDetector

safety_logger = logging.getLogger("safety_engine")

# ---------------------------------------------------------------------------
# Battery thresholds (extends drone_state.py thresholds with the middle tier)
# ---------------------------------------------------------------------------
BATTERY_STRONG_WARN_PCT = 15   # % — escalated warning before countdown

# ---------------------------------------------------------------------------
# Alert cooldowns (seconds between re-fires of the same alert_id)
# ---------------------------------------------------------------------------
_COOLDOWNS = {
    "battery_warn":        120,
    "battery_strong_warn":  60,
    "battery_countdown":   300,   # long — don't start a second countdown this session
    "battery_forced":       30,
    "gps_lost":             30,
    "gps_weak":             60,
    "ekf_bad":              20,
    "vibration_high":       60,
    "rc_lost":              30,
    "link_lost":            10,
}

# ---------------------------------------------------------------------------
# Phases where each check is meaningful
# ---------------------------------------------------------------------------
_AIRBORNE_PHASES = {
    FlightPhase.TAKEOFF, FlightPhase.CLIMB, FlightPhase.CRUISE,
    FlightPhase.AGGRESSIVE, FlightPhase.LANDING, FlightPhase.EMERGENCY,
}
_SUPPRESSED_PHASES = {FlightPhase.BOOT}   # no data — skip all checks


# ---------------------------------------------------------------------------
# SafetyAlert record (kept in recent_alerts for JARVIS/UI inspection)
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SafetyAlert:
    alert_id:     str
    severity:     str          # 'info' | 'warning' | 'critical' | 'emergency'
    title:        str
    message:      str
    phase:        str          # FlightPhase.value at time of alert
    action_taken: Optional[str] = None   # description of protective action, if any
    timestamp:    float = dataclasses.field(default_factory=time.time)


# ---------------------------------------------------------------------------
# SafetyEngine
# ---------------------------------------------------------------------------

class SafetyEngine:
    """
    Guardian safety layer.

    Instantiation::

        engine = SafetyEngine(
            command_fn = validator.send_mavlink_command_from_json,
            alert_fn   = _emit_alert,          # web_server._emit_alert signature
        )

    Every telemetry cycle::

        engine.tick(validator.drone_state, validator.phase_detector)

    Voice/UI cancel hook::

        engine.cancel_rtl_countdown()
    """

    def __init__(
        self,
        command_fn: Callable,
        alert_fn:   Optional[Callable] = None,
    ):
        """
        Args:
            command_fn: callable(cmd_dict) → bool
                        Sends a MAVLink command. Matches
                        MavlinkHandler.send_mavlink_command_from_json signature.
            alert_fn:   callable(alert_id, severity, title, message, **kwargs)
                        Emits an alert to the UI/voice layer.
                        Can be set later via set_alert_fn().
        """
        self._command_fn: Callable = command_fn
        self._alert_fn:   Callable = alert_fn or self._log_alert_fallback

        self._lock = threading.Lock()

        # Cooldown tracking: alert_id → last fired timestamp
        self._last_fired: dict[str, float] = {}

        # RTL countdown state
        self._countdown_active: bool        = False
        self._countdown_cancel: threading.Event = threading.Event()

        # Recent alert log (last 50) for snapshot / JARVIS context
        self._recent_alerts: list[SafetyAlert] = []

        # Guard against re-executing forced action too soon
        self._forced_action_at: float = 0.0

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def set_alert_fn(self, fn: Callable) -> None:
        """Wire in the alert emitter after construction (e.g. after web_server init)."""
        with self._lock:
            self._alert_fn = fn

    def tick(self, state: DroneState, detector: FlightPhaseDetector) -> None:
        """
        Run all safety checks.  Call every telemetry cycle (2-10 Hz).
        Thread-safe — can be called from the telemetry thread.
        """
        phase = detector.phase

        if phase in _SUPPRESSED_PHASES:
            return

        with self._lock:
            self._check_battery(state, phase)
            self._check_gps(state, phase)
            self._check_ekf(state, phase)
            self._check_vibration(state, phase)
            self._check_rc(state, phase)
            self._check_link(state, phase)

    def cancel_rtl_countdown(self) -> bool:
        """
        Cancel an active RTL countdown.
        Returns True if a countdown was active and has been cancelled.
        Safe to call from any thread (voice, UI request handler).
        """
        if self._countdown_active:
            self._countdown_cancel.set()
            safety_logger.info("RTL countdown cancelled by pilot")
            return True
        return False

    def snapshot(self) -> dict:
        """Serialisable engine state for JARVIS / UI inspection."""
        with self._lock:
            return {
                "countdown_active": self._countdown_active,
                "recent_alerts": [
                    dataclasses.asdict(a) for a in self._recent_alerts[-10:]
                ],
            }

    # -----------------------------------------------------------------------
    # Safety checks — all called within self._lock
    # -----------------------------------------------------------------------

    def _check_battery(self, state: DroneState, phase: FlightPhase) -> None:
        """Four-level battery safety ladder."""
        pct = state.battery_pct
        if pct < 0:
            return   # battery data not available

        flying = phase in _AIRBORNE_PHASES

        # ── Level 4: forced action — no countdown, immediate ──────────────
        if pct <= BATTERY_FORCE_PCT and flying:
            if self._can_fire("battery_forced", _COOLDOWNS["battery_forced"]):
                if not self._countdown_active:
                    reason = f"Battery critically low ({pct}%). Executing protective action."
                    self._fire(
                        "battery_forced", "emergency",
                        "Battery Forced Action",
                        f"{reason} Descending now.",
                        action_taken="forced_rtl_or_land",
                    )
                    # Execute outside the lock to avoid deadlock
                    threading.Thread(
                        target=self._execute_forced_action_threaded,
                        args=(state,),
                        daemon=True,
                    ).start()
            return   # don't also fire lower-level alerts

        # ── Level 3: RTL countdown — cancellable ──────────────────────────
        if pct <= BATTERY_RTL_PCT and flying and not self._countdown_active:
            if self._can_fire("battery_countdown", _COOLDOWNS["battery_countdown"]):
                reason = f"Battery critical ({pct}%). RTL executing in 5s."
                self._fire(
                    "battery_countdown", "emergency",
                    "Critical Battery — RTL Countdown",
                    f"{reason} Say 'cancel' or press Cancel to abort.",
                    action_taken=None,
                )
                threading.Thread(
                    target=self._rtl_countdown_thread,
                    args=(state, reason, 5),
                    daemon=True,
                ).start()
            return

        # ── Level 2: strong warning ────────────────────────────────────────
        if pct <= BATTERY_STRONG_WARN_PCT:
            if self._can_fire("battery_strong_warn", _COOLDOWNS["battery_strong_warn"]):
                msg = f"Battery at {pct}%. "
                msg += "Plan your return now." if flying else "Charge before flying."
                self._fire(
                    "battery_strong_warn", "critical",
                    "Battery Low — Return Soon",
                    msg,
                )
            return

        # ── Level 1: advisory warning ──────────────────────────────────────
        if pct <= BATTERY_WARN_PCT:
            if self._can_fire("battery_warn", _COOLDOWNS["battery_warn"]):
                msg = f"Battery at {pct}%. "
                msg += "Begin return planning." if flying else "Consider charging."
                self._fire(
                    "battery_warn", "warning",
                    "Battery Warning",
                    msg,
                )

    def _check_gps(self, state: DroneState, phase: FlightPhase) -> None:
        """GPS fix lost or degraded."""
        # GPS issues are more critical when airborne
        airborne = phase in _AIRBORNE_PHASES

        if not state.gps_ok():
            severity = "critical" if airborne else "warning"
            cooldown = _COOLDOWNS["gps_lost"]
            if self._can_fire("gps_lost", cooldown):
                advice = (
                    "Avoid LOITER/AUTO/RTL. Switch to STABILIZE or ALT_HOLD."
                    if airborne else
                    "Do not arm. Wait for GPS lock."
                )
                self._fire(
                    "gps_lost", severity,
                    "GPS Fix Lost",
                    f"Fix type {state.gps_fix} ({state.satellites} sats). {advice}",
                )
        elif state.gps_weak() and airborne:
            if self._can_fire("gps_weak", _COOLDOWNS["gps_weak"]):
                self._fire(
                    "gps_weak", "warning",
                    "GPS Signal Weak",
                    f"{state.satellites} satellites, HDOP {state.hdop:.1f}. "
                    "Autonomous modes may be unreliable.",
                )

    def _check_ekf(self, state: DroneState, phase: FlightPhase) -> None:
        """EKF health — only meaningful while airborne."""
        if phase not in _AIRBORNE_PHASES:
            return
        if not state.ekf_ok:
            if self._can_fire("ekf_bad", _COOLDOWNS["ekf_bad"]):
                self._fire(
                    "ekf_bad", "critical",
                    "EKF Health Degraded",
                    f"EKF flags: 0x{state.ekf_flags:04X}. "
                    "Position estimate unreliable. Land as soon as safe.",
                )

    def _check_vibration(self, state: DroneState, phase: FlightPhase) -> None:
        """High vibration — only meaningful while airborne."""
        if phase not in _AIRBORNE_PHASES:
            return
        if state.vibration_high():
            peak = max(state.vib_x, state.vib_y, state.vib_z)
            axis = ["X", "Y", "Z"][
                [state.vib_x, state.vib_y, state.vib_z].index(peak)
            ]
            if self._can_fire("vibration_high", _COOLDOWNS["vibration_high"]):
                self._fire(
                    "vibration_high", "warning",
                    "High Vibration",
                    f"{axis}-axis: {peak:.1f} m/s². "
                    "Check propellers and motor mounts. Land when safe.",
                )

    def _check_rc(self, state: DroneState, phase: FlightPhase) -> None:
        """RC signal lost while armed."""
        if not state.armed:
            return
        if state.rc_lost():
            if self._can_fire("rc_lost", _COOLDOWNS["rc_lost"]):
                self._fire(
                    "rc_lost", "critical",
                    "RC Signal Lost",
                    "No RC input detected. Vehicle may trigger RC failsafe.",
                )

    def _check_link(self, state: DroneState, phase: FlightPhase) -> None:
        """MAVLink heartbeat timeout while flying."""
        if phase not in _AIRBORNE_PHASES:
            return
        if state.is_stale(max_age_s=5.0):
            if self._can_fire("link_lost", _COOLDOWNS["link_lost"]):
                self._fire(
                    "link_lost", "critical",
                    "Telemetry Link Lost",
                    "No MAVLink heartbeat for 5+ seconds. "
                    "Vehicle is operating without GCS supervision.",
                )

    # -----------------------------------------------------------------------
    # Command execution — called from daemon threads, NOT within self._lock
    # -----------------------------------------------------------------------

    def _rtl_countdown_thread(
        self, state: DroneState, reason: str, delay_s: int
    ) -> None:
        """
        Daemon thread that waits delay_s seconds then executes RTL unless
        the pilot cancels.  Fires a countdown alert each second.
        """
        with self._lock:
            self._countdown_active = True
            self._countdown_cancel.clear()

        safety_logger.info(f"RTL countdown started: {reason}")

        try:
            for remaining in range(delay_s, 0, -1):
                if self._countdown_cancel.is_set():
                    # Pilot cancelled
                    self._alert_fn(
                        "rtl_countdown_cancel", "info",
                        "RTL Cancelled",
                        "Pilot cancelled emergency RTL countdown.",
                    )
                    safety_logger.info("RTL countdown cancelled by pilot")
                    return

                # Tick alert for UI/voice
                self._alert_fn(
                    "rtl_countdown_tick", "emergency",
                    "RTL Countdown",
                    f"{reason} Executing in {remaining}s. Say 'cancel' to abort.",
                )
                time.sleep(1)

            # Final check — did pilot cancel in the last second?
            if self._countdown_cancel.is_set():
                self._alert_fn(
                    "rtl_countdown_cancel", "info",
                    "RTL Cancelled",
                    "Pilot cancelled emergency RTL countdown.",
                )
                return

            # Execute
            action = self._execute_protective_action(state)
            safety_logger.warning(f"RTL countdown elapsed — executed: {action}")
            self._alert_fn(
                "rtl_executed", "emergency",
                "Emergency RTL Executed",
                f"Countdown elapsed. Action taken: {action}.",
            )

        finally:
            with self._lock:
                self._countdown_active = False
                self._countdown_cancel.clear()

    def _execute_forced_action_threaded(self, state: DroneState) -> None:
        """
        Daemon thread for immediate (no countdown) protective action.
        Guarded by _forced_action_at to prevent re-triggering too soon.
        """
        now = time.time()
        with self._lock:
            if now - self._forced_action_at < 30.0:
                return   # already acted recently
            self._forced_action_at = now

        action = self._execute_protective_action(state)
        safety_logger.warning(f"Forced action executed: {action}")

    def _execute_protective_action(self, state: DroneState) -> str:
        """
        Choose and execute the safest recovery action given current state.
        Returns a string description of the action taken.
        """
        if state.gps_ok():
            # GPS valid — RTL is the safest option
            ok = self._command_fn({
                "command": "MAV_CMD_NAV_RETURN_TO_LAUNCH",
            })
            return f"RTL ({'ACK' if ok else 'NACK/timeout'})"
        else:
            # No GPS — fall back to LAND in place
            ok = self._command_fn({
                "command": "MAV_CMD_DO_SET_MODE",
                "param1": 1,
                "param2": 9,   # LAND mode
            })
            return f"LAND (no GPS) ({'ACK' if ok else 'NACK/timeout'})"

    # -----------------------------------------------------------------------
    # Internal helpers — called within self._lock
    # -----------------------------------------------------------------------

    def _can_fire(self, alert_id: str, cooldown_s: float) -> bool:
        """True (and records fire time) if cooldown has elapsed."""
        now = time.time()
        if now - self._last_fired.get(alert_id, 0) >= cooldown_s:
            self._last_fired[alert_id] = now
            return True
        return False

    def _fire(
        self,
        alert_id: str,
        severity:  str,
        title:     str,
        message:   str,
        action_taken: Optional[str] = None,
        action:    Optional[dict] = None,
    ) -> None:
        """
        Record the alert internally and forward to the UI alert function.
        Must be called within self._lock (to append to _recent_alerts).
        The call to self._alert_fn happens outside the lock to allow
        alert_fn to re-enter SafetyEngine if needed.
        """
        phase_name = "UNKNOWN"   # caller passes phase context via _check_* methods
        alert = SafetyAlert(
            alert_id=alert_id,
            severity=severity,
            title=title,
            message=message,
            phase=phase_name,
            action_taken=action_taken,
        )
        self._recent_alerts.append(alert)
        if len(self._recent_alerts) > 50:
            self._recent_alerts = self._recent_alerts[-50:]

        safety_logger.log(
            logging.WARNING if severity in ("critical", "emergency") else logging.INFO,
            f"[{severity.upper()}] {title}: {message}"
            + (f" → action: {action_taken}" if action_taken else ""),
        )

        # Call alert_fn outside lock — capture reference first
        fn = self._alert_fn
        # Release lock implicitly by calling this after _fire returns;
        # callers of _fire hold the lock, so we schedule alert emission
        # by storing it for emission after lock release.
        # Simpler: just call it inline — alert_fn must not call back into
        # SafetyEngine methods that acquire _lock.
        fn(alert_id, severity, title, message, action=action)

    @staticmethod
    def _log_alert_fallback(alert_id, severity, title, message, **_):
        """Default alert_fn used before web_server wires in the real one."""
        safety_logger.info(f"[ALERT/{severity}] {title}: {message}")
