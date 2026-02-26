"""
DroneState — canonical real-time state of the drone.

Single source of truth for all live telemetry. Updated at the telemetry
loop rate (2-10 Hz) from ai_mavlink_ctx via update_from_ctx(). All
consumers (web_server, JARVIS, copilot, FlightPhaseDetector, SafetyEngine)
read from this object instead of parsing raw MAVLink dicts themselves.

Thread-safe: all reads and writes are protected by a single RLock.
No MAVLink dependency — only understands field names from message dicts.
"""

import math
import threading
import time
import logging

state_logger = logging.getLogger("drone_state")

# ---------------------------------------------------------------------------
# ArduCopter custom_mode → name mapping (matches copilot.py)
# ---------------------------------------------------------------------------
COPTER_MODES = {
    0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO",
    4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE",
    9: "LAND", 11: "DRIFT", 13: "SPORT", 14: "FLIP",
    15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 18: "THROW",
    19: "AVOID_ADSB", 20: "GUIDED_NOGPS", 21: "SMART_RTL",
    22: "FLOWHOLD", 23: "FOLLOW", 24: "ZIGZAG", 25: "SYSTEMID",
    26: "AUTOROTATE", 27: "AUTO_RTL",
}

# ---------------------------------------------------------------------------
# EKF health bitmask constants (MAV_ESTIMATOR_STATUS_FLAGS)
# ---------------------------------------------------------------------------
EKF_ATTITUDE         = 0x0001
EKF_VELOCITY_HORIZ   = 0x0002
EKF_VELOCITY_VERT    = 0x0004
EKF_POS_HORIZ_REL    = 0x0008
EKF_POS_HORIZ_ABS    = 0x0010
EKF_POS_VERT_ABS     = 0x0020
EKF_POS_VERT_AGL     = 0x0040
EKF_CONST_POS_MODE   = 0x0080   # bad — position estimate frozen
EKF_PRED_POS_HORIZ_REL = 0x0100
EKF_PRED_POS_HORIZ_ABS = 0x0200
EKF_UNINITIALIZED    = 0x0400   # bad — EKF not running

# Bits that must all be set for a healthy EKF in GPS-assisted flight
_EKF_HEALTHY_BITS = (
    EKF_ATTITUDE | EKF_VELOCITY_HORIZ | EKF_VELOCITY_VERT |
    EKF_POS_HORIZ_ABS | EKF_POS_VERT_ABS
)
# Bits that must NOT be set
_EKF_BAD_BITS = EKF_CONST_POS_MODE | EKF_UNINITIALIZED

# ---------------------------------------------------------------------------
# Safety thresholds — tunable defaults
# ---------------------------------------------------------------------------
BATTERY_WARN_PCT    = 20    # % — issue a low-battery warning
BATTERY_RTL_PCT     = 10    # % — initiate RTL countdown
BATTERY_FORCE_PCT   = 7     # % — execute RTL unconditionally
GPS_WEAK_SATS       = 8     # below this → weak GPS warning
GPS_WEAK_HDOP       = 2.0   # above this → weak GPS warning
VIB_WARN_MS2        = 30.0  # m/s² vibration peak — warning threshold
FLYING_ALT_M        = 0.5   # m above home — consider airborne
FLYING_SPEED_MS     = 0.5   # m/s groundspeed — consider airborne


class DroneState:
    """
    Thread-safe, structured representation of the live drone state.

    Lifecycle:
        state = DroneState()
        # called every telemetry loop cycle:
        state.update_from_ctx(validator.ai_mavlink_ctx)
        # consumers read:
        if state.battery_low():
            warn(state.battery_pct)
        payload = state.snapshot()   # for JARVIS / UI
    """

    def __init__(self):
        self._lock = threading.RLock()

        # ── Flight ────────────────────────────────────────────────────────
        self.armed          = False
        self.flight_mode    = "UNKNOWN"
        self.flight_mode_id = -1
        self.altitude_m     = 0.0     # barometric / VFR_HUD altitude (m)
        self.rel_altitude_m = 0.0     # relative altitude from GLOBAL_POSITION_INT (m)
        self.groundspeed_ms = 0.0     # m/s
        self.airspeed_ms    = 0.0     # m/s
        self.climb_rate_ms  = 0.0     # m/s — positive = climbing
        self.heading_deg    = 0.0     # 0-359°

        # ── Attitude ──────────────────────────────────────────────────────
        self.roll_deg       = 0.0
        self.pitch_deg      = 0.0
        self.yaw_deg        = 0.0

        # ── Power ─────────────────────────────────────────────────────────
        self.battery_voltage = 0.0    # Volts
        self.battery_pct     = -1     # % remaining, -1 = unknown
        self.current_a       = 0.0    # Amps

        # ── GPS ───────────────────────────────────────────────────────────
        self.gps_fix        = 0       # 0=no GPS, 2=2D, 3=3D, 4=DGPS, 5/6=RTK
        self.satellites     = 0
        self.hdop           = 99.99   # dilution of precision
        self.lat            = 0.0     # degrees
        self.lon            = 0.0     # degrees

        # ── Home ──────────────────────────────────────────────────────────
        self.home_lat       = None    # set from HOME_POSITION message
        self.home_lon       = None
        self.home_distance_m = None   # computed; None until home is known

        # ── Health ────────────────────────────────────────────────────────
        self.ekf_ok         = False
        self.ekf_flags      = 0
        self.rc_rssi        = 255     # 0-254; 255 = not available
        self.failsafe       = False
        self.vib_x          = 0.0    # m/s²
        self.vib_y          = 0.0
        self.vib_z          = 0.0
        self.vib_clipping   = 0      # total clipping events

        # ── System ────────────────────────────────────────────────────────
        self.system_load_pct = 0.0   # FC CPU load %

        # ── Metadata ──────────────────────────────────────────────────────
        self.last_updated   = 0.0    # Unix timestamp of last update_from_ctx call
        self.update_count   = 0      # total number of updates received

    # -----------------------------------------------------------------------
    # Update — called every telemetry loop cycle
    # -----------------------------------------------------------------------

    def update_from_ctx(self, ctx: dict) -> None:
        """
        Parse ai_mavlink_ctx (dict of msg_type → latest MAVLink msg dict)
        and update internal state fields atomically.
        """
        if not ctx:
            return

        with self._lock:
            self._parse_heartbeat(ctx.get("HEARTBEAT"))
            self._parse_vfr_hud(ctx.get("VFR_HUD"))
            self._parse_attitude(ctx.get("ATTITUDE"))
            self._parse_sys_status(ctx.get("SYS_STATUS"))
            self._parse_battery_status(ctx.get("BATTERY_STATUS"))
            self._parse_gps_raw(ctx.get("GPS_RAW_INT"))
            self._parse_global_position(ctx.get("GLOBAL_POSITION_INT"))
            self._parse_home_position(ctx.get("HOME_POSITION"))
            self._parse_ekf_status(ctx.get("EKF_STATUS_REPORT"))
            self._parse_rc_channels(ctx.get("RC_CHANNELS"))
            self._parse_vibration(ctx.get("VIBRATION"))

            # Recompute home distance whenever lat/lon or home changes
            self._update_home_distance()

            self.last_updated = time.time()
            self.update_count += 1

    # -----------------------------------------------------------------------
    # Internal parsers — all called within the lock
    # -----------------------------------------------------------------------

    def _parse_heartbeat(self, msg):
        if not msg:
            return
        base_mode   = msg.get("base_mode", 0)
        custom_mode = msg.get("custom_mode", 0)
        sys_status  = msg.get("system_status", 0)  # MAV_STATE

        self.armed          = bool(base_mode & 0x80)
        self.flight_mode_id = custom_mode
        self.flight_mode    = COPTER_MODES.get(custom_mode, f"MODE_{custom_mode}")
        # MAV_STATE_EMERGENCY = 6
        self.failsafe       = (sys_status == 6)

    def _parse_vfr_hud(self, msg):
        if not msg:
            return
        self.altitude_m     = float(msg.get("alt", self.altitude_m))
        self.groundspeed_ms = float(msg.get("groundspeed", self.groundspeed_ms))
        self.airspeed_ms    = float(msg.get("airspeed", self.airspeed_ms))
        self.climb_rate_ms  = float(msg.get("climb", self.climb_rate_ms))
        self.heading_deg    = float(msg.get("heading", self.heading_deg))

    def _parse_attitude(self, msg):
        if not msg:
            return
        # MAVLink attitude angles are in radians
        self.roll_deg  = math.degrees(msg.get("roll",  0.0))
        self.pitch_deg = math.degrees(msg.get("pitch", 0.0))
        self.yaw_deg   = math.degrees(msg.get("yaw",   0.0))

    def _parse_sys_status(self, msg):
        if not msg:
            return
        raw_voltage = msg.get("voltage_battery", 0)
        raw_current = msg.get("current_battery", 0)
        remaining   = msg.get("battery_remaining", -1)
        load        = msg.get("load", 0)

        if raw_voltage > 0:
            self.battery_voltage = raw_voltage / 1000.0   # mV → V
        if raw_current >= 0:
            self.current_a = raw_current / 100.0          # cA → A
        if remaining >= 0:
            self.battery_pct = int(remaining)
        self.system_load_pct = load / 10.0                # per-mil → %

    def _parse_battery_status(self, msg):
        """BATTERY_STATUS is used when SYS_STATUS doesn't carry voltage."""
        if not msg:
            return
        # Only update if SYS_STATUS hasn't already set voltage this cycle
        voltages = msg.get("voltages", [])
        if voltages and voltages[0] not in (0, 65535) and self.battery_voltage == 0.0:
            self.battery_voltage = voltages[0] / 1000.0   # mV → V
        remaining = msg.get("battery_remaining", -1)
        if remaining >= 0 and self.battery_pct == -1:
            self.battery_pct = int(remaining)
        current = msg.get("current_battery", -1)
        if current >= 0 and self.current_a == 0.0:
            self.current_a = current / 100.0               # cA → A

    def _parse_gps_raw(self, msg):
        if not msg:
            return
        self.gps_fix    = int(msg.get("fix_type", self.gps_fix))
        self.satellites = int(msg.get("satellites_visible", self.satellites))
        raw_eph = msg.get("eph", 0)
        if raw_eph and raw_eph != 65535:
            self.hdop = raw_eph / 100.0   # cm → dimensionless (MAVLink EPH is cm)
        # GPS_RAW_INT lat/lon are in 1e7 degrees
        raw_lat = msg.get("lat", 0)
        raw_lon = msg.get("lon", 0)
        if raw_lat != 0:
            self.lat = raw_lat / 1e7
        if raw_lon != 0:
            self.lon = raw_lon / 1e7

    def _parse_global_position(self, msg):
        if not msg:
            return
        # GLOBAL_POSITION_INT has higher precision lat/lon
        raw_lat = msg.get("lat", 0)
        raw_lon = msg.get("lon", 0)
        if raw_lat != 0:
            self.lat = raw_lat / 1e7
        if raw_lon != 0:
            self.lon = raw_lon / 1e7
        raw_rel_alt = msg.get("relative_alt", 0)
        self.rel_altitude_m = raw_rel_alt / 1000.0   # mm → m

    def _parse_home_position(self, msg):
        if not msg:
            return
        raw_lat = msg.get("latitude", 0)
        raw_lon = msg.get("longitude", 0)
        if raw_lat != 0:
            self.home_lat = raw_lat / 1e7
        if raw_lon != 0:
            self.home_lon = raw_lon / 1e7

    def _parse_ekf_status(self, msg):
        if not msg:
            return
        flags = int(msg.get("flags", 0))
        self.ekf_flags = flags
        healthy_bits_present = (flags & _EKF_HEALTHY_BITS) == _EKF_HEALTHY_BITS
        bad_bits_absent      = not (flags & _EKF_BAD_BITS)
        self.ekf_ok = healthy_bits_present and bad_bits_absent

    def _parse_rc_channels(self, msg):
        if not msg:
            return
        self.rc_rssi = int(msg.get("rssi", self.rc_rssi))

    def _parse_vibration(self, msg):
        if not msg:
            return
        self.vib_x = float(msg.get("vibration_x", self.vib_x))
        self.vib_y = float(msg.get("vibration_y", self.vib_y))
        self.vib_z = float(msg.get("vibration_z", self.vib_z))
        self.vib_clipping = (
            int(msg.get("clipping_0", 0)) +
            int(msg.get("clipping_1", 0)) +
            int(msg.get("clipping_2", 0))
        )

    def _update_home_distance(self):
        """Haversine distance from current position to home (m). Internal."""
        if (self.home_lat is None or self.home_lon is None or
                self.lat == 0.0 or self.lon == 0.0):
            self.home_distance_m = None
            return
        self.home_distance_m = _haversine_m(
            self.lat, self.lon, self.home_lat, self.home_lon
        )

    # -----------------------------------------------------------------------
    # Computed properties — thread-safe convenience accessors
    # -----------------------------------------------------------------------

    def is_flying(self) -> bool:
        """True if the drone is likely airborne."""
        with self._lock:
            if not self.armed:
                return False
            return (
                self.rel_altitude_m > FLYING_ALT_M or
                abs(self.climb_rate_ms) > FLYING_SPEED_MS or
                self.groundspeed_ms > FLYING_SPEED_MS
            )

    def battery_low(self) -> bool:
        """True if battery % is below the warning threshold."""
        with self._lock:
            if self.battery_pct < 0:
                return False
            return self.battery_pct < BATTERY_WARN_PCT

    def battery_critical(self) -> bool:
        """True if battery % is at or below the RTL-trigger threshold."""
        with self._lock:
            if self.battery_pct < 0:
                return False
            return self.battery_pct <= BATTERY_RTL_PCT

    def battery_force_land(self) -> bool:
        """True if battery % is so low that forced RTL should execute."""
        with self._lock:
            if self.battery_pct < 0:
                return False
            return self.battery_pct <= BATTERY_FORCE_PCT

    def gps_ok(self) -> bool:
        """True if GPS fix is 3D or better."""
        with self._lock:
            return self.gps_fix >= 3

    def gps_weak(self) -> bool:
        """True if GPS is fixed but weak (few sats or high HDOP)."""
        with self._lock:
            if self.gps_fix < 3:
                return False   # not fixed at all — handled by gps_ok()
            return self.satellites < GPS_WEAK_SATS or self.hdop > GPS_WEAK_HDOP

    def vibration_high(self) -> bool:
        """True if any vibration axis exceeds the warning threshold."""
        with self._lock:
            return max(self.vib_x, self.vib_y, self.vib_z) > VIB_WARN_MS2

    def rc_lost(self) -> bool:
        """True if RC signal is confirmed lost (rssi = 0 and drone is armed)."""
        with self._lock:
            return self.armed and self.rc_rssi == 0

    def is_stale(self, max_age_s: float = 3.0) -> bool:
        """True if state hasn't been updated within max_age_s seconds."""
        return (time.time() - self.last_updated) > max_age_s

    # -----------------------------------------------------------------------
    # Snapshot — structured dict for JARVIS / UI
    # -----------------------------------------------------------------------

    def snapshot(self) -> dict:
        """
        Return a clean, serialisable snapshot of all state fields.
        Safe to pass directly to JARVIS as telemetry context.
        """
        with self._lock:
            fix_names = {
                0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix",
                4: "DGPS",   5: "RTK Float", 6: "RTK Fixed",
            }
            vib_peak = round(max(self.vib_x, self.vib_y, self.vib_z), 2)

            return {
                # Flight
                "armed":           self.armed,
                "flight_mode":     self.flight_mode,
                "flight_mode_id":  self.flight_mode_id,
                "altitude_m":      round(self.altitude_m, 2),
                "rel_altitude_m":  round(self.rel_altitude_m, 2),
                "groundspeed_ms":  round(self.groundspeed_ms, 2),
                "airspeed_ms":     round(self.airspeed_ms, 2),
                "climb_rate_ms":   round(self.climb_rate_ms, 2),
                "heading_deg":     round(self.heading_deg, 1),
                "is_flying":       self.is_flying(),

                # Attitude
                "roll_deg":        round(self.roll_deg, 2),
                "pitch_deg":       round(self.pitch_deg, 2),
                "yaw_deg":         round(self.yaw_deg, 2),

                # Power
                "battery_voltage": round(self.battery_voltage, 2),
                "battery_pct":     self.battery_pct,
                "current_a":       round(self.current_a, 2),
                "battery_low":     self.battery_low(),
                "battery_critical": self.battery_critical(),

                # GPS
                "gps_fix":         self.gps_fix,
                "gps_fix_str":     fix_names.get(self.gps_fix, "Unknown"),
                "satellites":      self.satellites,
                "hdop":            round(self.hdop, 2),
                "lat":             self.lat,
                "lon":             self.lon,
                "gps_ok":          self.gps_ok(),
                "gps_weak":        self.gps_weak(),

                # Home
                "home_distance_m": (
                    round(self.home_distance_m, 1)
                    if self.home_distance_m is not None else None
                ),

                # Health
                "ekf_ok":          self.ekf_ok,
                "ekf_flags":       self.ekf_flags,
                "rc_rssi":         self.rc_rssi,
                "rc_lost":         self.rc_lost(),
                "failsafe":        self.failsafe,
                "vib_x":           round(self.vib_x, 2),
                "vib_y":           round(self.vib_y, 2),
                "vib_z":           round(self.vib_z, 2),
                "vib_peak":        vib_peak,
                "vibration_high":  self.vibration_high(),
                "vib_clipping":    self.vib_clipping,

                # System
                "system_load_pct": round(self.system_load_pct, 1),

                # Metadata
                "last_updated":    self.last_updated,
                "state_stale":     self.is_stale(),
            }

    def __repr__(self):
        with self._lock:
            return (
                f"DroneState(armed={self.armed}, mode={self.flight_mode}, "
                f"alt={self.altitude_m:.1f}m, bat={self.battery_pct}%, "
                f"gps={self.gps_fix}/sats={self.satellites}, "
                f"ekf_ok={self.ekf_ok})"
            )


# ---------------------------------------------------------------------------
# Haversine helper — no external dependencies
# ---------------------------------------------------------------------------

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Return great-circle distance in metres between two WGS-84 points."""
    R = 6_371_000  # Earth radius in metres
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi       = math.radians(lat2 - lat1)
    dlambda    = math.radians(lon2 - lon1)
    a = (math.sin(dphi / 2) ** 2 +
         math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2)
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
