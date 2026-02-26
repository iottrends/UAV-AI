"""
FlightPhaseDetector — situational awareness layer for JARVIS.

Converts raw DroneState into a named flight phase that describes what the
drone is doing right now.  This context is consumed by:

  - JARVIS reasoning engine  (response tone, advice relevance)
  - Safety Engine            (action thresholds per phase)
  - Anomaly Detector         (sensitivity per phase)
  - Voice Copilot            (proactive alert priority)

Phases (ordered by evaluation priority):

  BOOT        — no telemetry data yet or link is stale
  EMERGENCY   — failsafe active, battery forced RTL, RC lost in flight,
                or EKF bad while airborne
  LANDING     — LAND mode, or descending to ground during RTL
  TAKEOFF     — just armed, climbing through the first 10 m AGL
  AGGRESSIVE  — high speed / climb rate / attitude angle sustained
  CLIMB       — sustained climb above the takeoff window
  CRUISE      — stable airborne flight (default)
  ARMED_IDLE  — armed but on the ground
  DISARMED    — disarmed with healthy preflight state
  PREFLIGHT   — disarmed with GPS or EKF not yet ready

Design principles:
  - DroneState is the only input — no MAVLink dependency here
  - Hysteresis timers prevent phase flapping on borderline conditions
  - Phase change observers allow zero-coupling notification to other layers
  - Thread-safe: update() can be called from the telemetry thread;
    observers are called inline (keep them fast)
"""

import enum
import logging
import time
import threading

from drone_state import DroneState

phase_logger = logging.getLogger("flight_phase")

# ---------------------------------------------------------------------------
# Phase enum
# ---------------------------------------------------------------------------

class FlightPhase(enum.Enum):
    BOOT       = "BOOT"
    PREFLIGHT  = "PREFLIGHT"
    DISARMED   = "DISARMED"
    ARMED_IDLE = "ARMED_IDLE"
    TAKEOFF    = "TAKEOFF"
    CLIMB      = "CLIMB"
    CRUISE     = "CRUISE"
    AGGRESSIVE = "AGGRESSIVE"
    LANDING    = "LANDING"
    EMERGENCY  = "EMERGENCY"

    def __str__(self):
        return self.value


# ---------------------------------------------------------------------------
# Tunable thresholds
# ---------------------------------------------------------------------------

# Takeoff window: time after arming during which low-altitude climb = TAKEOFF
TAKEOFF_WINDOW_S   = 45.0   # seconds
TAKEOFF_ALT_M      = 10.0   # m AGL — above this, TAKEOFF becomes CLIMB

# Climb detection
CLIMB_RATE_MIN_MS  = 0.8    # m/s sustained upward → CLIMB phase

# Landing detection for RTL/SMART_RTL/AUTO_RTL modes (not LAND mode which is always LANDING)
LANDING_ALT_M      = 8.0    # m AGL — below this + descending → LANDING
LANDING_DESCENT_MS = -0.3   # m/s — must be descending at least this fast

# Aggressive flight thresholds
AGGRESSIVE_SPEED_MS    = 8.0    # m/s groundspeed
AGGRESSIVE_CLIMB_MS    = 4.0    # m/s climb or descent rate (absolute)
AGGRESSIVE_ATTITUDE    = 45.0   # degrees roll or pitch (absolute)

# ArduCopter mode IDs that indicate the FC has commanded a return + land
_RTL_MODE_IDS  = {6, 21, 27}   # RTL, SMART_RTL, AUTO_RTL
_LAND_MODE_ID  = 9

# Hysteresis durations (seconds) — prevent flapping on borderline values
DWELL_AGGRESSIVE_ENTER  = 0.5   # conditions must sustain before entering AGGRESSIVE
DWELL_AGGRESSIVE_EXIT   = 1.5   # conditions must clear before leaving AGGRESSIVE
DWELL_CLIMB_ENTER       = 1.0   # sustained climb before entering CLIMB phase
DWELL_LANDING_DESCENT   = 2.0   # sustained descent at low alt before LANDING
DWELL_EMERGENCY_EXIT    = 3.0   # conditions must be clear this long to leave EMERGENCY


# ---------------------------------------------------------------------------
# FlightPhaseDetector
# ---------------------------------------------------------------------------

class FlightPhaseDetector:
    """
    Stateful evaluator that maps DroneState → FlightPhase every tick.

    Usage::

        detector = FlightPhaseDetector()

        # Register a listener (called on every phase change):
        def on_change(old: FlightPhase, new: FlightPhase, state: DroneState):
            print(f"Phase: {old} → {new}")
        detector.add_phase_listener(on_change)

        # Call every telemetry cycle:
        phase = detector.update(drone_state)
    """

    def __init__(self):
        self._lock = threading.Lock()

        self.phase: FlightPhase          = FlightPhase.BOOT
        self._prev_phase: FlightPhase    = FlightPhase.BOOT
        self._phase_entered_at: float    = time.time()

        # Arm-event tracking
        self._was_armed: bool            = False
        self._armed_at: float | None     = None   # timestamp of last arm

        # Hysteresis clocks — all None means "not yet started"
        self._aggressive_since: float | None      = None   # when conditions entered AGGRESSIVE band
        self._aggressive_exit_since: float | None = None   # when conditions left AGGRESSIVE band
        self._climbing_since: float | None        = None   # when sustained climb started
        self._landing_descent_since: float | None = None   # when descent-at-low-alt started
        self._emergency_clear_since: float | None = None   # when emergency conditions cleared

        # Observer callbacks: fn(old_phase, new_phase, state) → None
        self._listeners: list = []

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def update(self, state: DroneState) -> FlightPhase:
        """
        Evaluate the current flight phase from DroneState.
        Applies hysteresis, fires change listeners, and returns the phase.
        Thread-safe — can be called from the telemetry thread.
        """
        with self._lock:
            now = time.time()
            self._track_arm_event(state, now)

            new_phase = self._evaluate(state, now)

            if new_phase != self.phase:
                old = self.phase
                self.phase = new_phase
                self._prev_phase = old
                self._phase_entered_at = now
                phase_logger.info(
                    f"Phase: {old} → {new_phase}  "
                    f"[armed={state.armed} alt={state.rel_altitude_m:.1f}m "
                    f"spd={state.groundspeed_ms:.1f}m/s "
                    f"climb={state.climb_rate_ms:.1f}m/s "
                    f"mode={state.flight_mode}]"
                )
                # Notify listeners outside the lock to avoid deadlocks
                listeners = list(self._listeners)
            else:
                listeners = []

        # Fire listeners outside the lock
        for cb in listeners:
            try:
                cb(self._prev_phase, self.phase, state)
            except Exception:
                phase_logger.exception("Phase listener raised an exception")

        return self.phase

    def add_phase_listener(self, fn) -> None:
        """Register a callback: fn(old: FlightPhase, new: FlightPhase, state: DroneState)."""
        with self._lock:
            self._listeners.append(fn)

    def remove_phase_listener(self, fn) -> None:
        with self._lock:
            self._listeners = [cb for cb in self._listeners if cb is not fn]

    @property
    def phase_duration_s(self) -> float:
        """Seconds spent in the current phase."""
        return time.time() - self._phase_entered_at

    @property
    def previous_phase(self) -> FlightPhase:
        return self._prev_phase

    def is_airborne(self) -> bool:
        """True if current phase implies the drone is off the ground."""
        return self.phase in {
            FlightPhase.TAKEOFF, FlightPhase.CLIMB, FlightPhase.CRUISE,
            FlightPhase.AGGRESSIVE, FlightPhase.LANDING, FlightPhase.EMERGENCY,
        }

    def is_safe_to_command(self) -> bool:
        """
        True if it is safe to send flight commands from AI or copilot.
        EMERGENCY and BOOT are not safe — commands may be overridden by FC.
        """
        return self.phase not in {FlightPhase.BOOT, FlightPhase.EMERGENCY}

    def snapshot(self) -> dict:
        """Serialisable phase summary for JARVIS / UI / logging."""
        with self._lock:
            return {
                "phase":          self.phase.value,
                "previous_phase": self._prev_phase.value,
                "phase_duration_s": round(self.phase_duration_s, 1),
                "is_airborne":    self.is_airborne(),
                "is_safe_to_command": self.is_safe_to_command(),
                "armed_since_s":  (
                    round(time.time() - self._armed_at, 1)
                    if self._armed_at else None
                ),
            }

    def __repr__(self):
        return f"FlightPhaseDetector(phase={self.phase}, duration={self.phase_duration_s:.1f}s)"

    # -----------------------------------------------------------------------
    # Internal helpers — all called within self._lock
    # -----------------------------------------------------------------------

    def _track_arm_event(self, state: DroneState, now: float) -> None:
        """Detect arm transitions to anchor the TAKEOFF window."""
        if state.armed and not self._was_armed:
            self._armed_at = now
            phase_logger.info(f"Arm event detected at t={now:.1f}")
        self._was_armed = state.armed

    def _evaluate(self, state: DroneState, now: float) -> FlightPhase:
        """
        Core evaluation logic.  Returns the candidate phase after applying
        hysteresis.  Called while holding self._lock.
        """

        # ── BOOT ──────────────────────────────────────────────────────────
        # No data yet, or link has gone silent.
        if state.is_stale() or state.update_count == 0:
            self._reset_hysteresis()
            return FlightPhase.BOOT

        armed   = state.armed
        flying  = state.is_flying()
        rel_alt = state.rel_altitude_m
        climb   = state.climb_rate_ms
        speed   = state.groundspeed_ms
        mode_id = state.flight_mode_id

        # ── EMERGENCY ─────────────────────────────────────────────────────
        # Highest priority while armed or already flying.
        emergency_conditions = (
            state.failsafe
            or (armed and state.battery_force_land())
            or (state.rc_lost() and flying)
            or (not state.ekf_ok and flying and mode_id != _LAND_MODE_ID)
        )

        if emergency_conditions:
            self._emergency_clear_since = None
            return FlightPhase.EMERGENCY

        # If we were in EMERGENCY, wait for a clear dwell before exiting.
        if self.phase == FlightPhase.EMERGENCY:
            if self._emergency_clear_since is None:
                self._emergency_clear_since = now
            if now - self._emergency_clear_since < DWELL_EMERGENCY_EXIT:
                return FlightPhase.EMERGENCY
            # Clear dwell elapsed — fall through to normal evaluation.

        # Reset emergency clock when not in EMERGENCY
        if self.phase != FlightPhase.EMERGENCY:
            self._emergency_clear_since = None

        # ── LANDING ───────────────────────────────────────────────────────
        # FC commanded LAND mode is definitive.
        if mode_id == _LAND_MODE_ID:
            self._landing_descent_since = None
            return FlightPhase.LANDING

        # RTL family: only LANDING once we're actually descending at low altitude.
        if mode_id in _RTL_MODE_IDS and rel_alt < LANDING_ALT_M and climb < LANDING_DESCENT_MS:
            if self._landing_descent_since is None:
                self._landing_descent_since = now
            if now - self._landing_descent_since >= DWELL_LANDING_DESCENT:
                return FlightPhase.LANDING
        else:
            self._landing_descent_since = None

        # ── Not armed ─────────────────────────────────────────────────────
        if not armed:
            self._armed_at = None  # clear so next arm event is fresh
            if state.gps_ok() and state.ekf_ok:
                return FlightPhase.DISARMED
            return FlightPhase.PREFLIGHT

        # ── Armed and on the ground ───────────────────────────────────────
        if not flying:
            return FlightPhase.ARMED_IDLE

        # ── TAKEOFF ───────────────────────────────────────────────────────
        # Valid only within the first TAKEOFF_WINDOW_S after arming and
        # while still below TAKEOFF_ALT_M.
        time_since_arm = (now - self._armed_at) if self._armed_at else float("inf")
        if (
            time_since_arm < TAKEOFF_WINDOW_S
            and rel_alt < TAKEOFF_ALT_M
            and climb > 0.5
        ):
            return FlightPhase.TAKEOFF

        # ── AGGRESSIVE ────────────────────────────────────────────────────
        aggressive_now = (
            speed          > AGGRESSIVE_SPEED_MS
            or abs(climb)  > AGGRESSIVE_CLIMB_MS
            or abs(state.pitch_deg) > AGGRESSIVE_ATTITUDE
            or abs(state.roll_deg)  > AGGRESSIVE_ATTITUDE
        )

        if aggressive_now:
            # Start or continue the entry dwell timer
            self._aggressive_since      = self._aggressive_since or now
            self._aggressive_exit_since = None
            if now - self._aggressive_since >= DWELL_AGGRESSIVE_ENTER:
                return FlightPhase.AGGRESSIVE
            # Conditions present but dwell not yet met — fall through
        else:
            self._aggressive_since = None
            # If we're currently in AGGRESSIVE, apply the exit dwell
            if self.phase == FlightPhase.AGGRESSIVE:
                self._aggressive_exit_since = self._aggressive_exit_since or now
                if now - self._aggressive_exit_since < DWELL_AGGRESSIVE_EXIT:
                    return FlightPhase.AGGRESSIVE
            else:
                self._aggressive_exit_since = None

        # ── CLIMB ─────────────────────────────────────────────────────────
        if climb > CLIMB_RATE_MIN_MS:
            self._climbing_since = self._climbing_since or now
            if now - self._climbing_since >= DWELL_CLIMB_ENTER:
                return FlightPhase.CLIMB
        else:
            self._climbing_since = None

        # ── CRUISE (default airborne) ──────────────────────────────────────
        return FlightPhase.CRUISE

    def _reset_hysteresis(self) -> None:
        """Clear all dwell timers on BOOT/link-loss."""
        self._aggressive_since      = None
        self._aggressive_exit_since = None
        self._climbing_since        = None
        self._landing_descent_since = None
        self._emergency_clear_since = None
