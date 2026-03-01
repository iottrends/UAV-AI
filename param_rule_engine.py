"""
param_rule_engine.py — ArduPilot configuration audit engine.

Scans the full parameter set and returns a structured report of issues —
like an SEO scanner but for drone configuration.

Design
------
- One function per rule, each returns a list of issue dicts (empty = passed)
- ALL_RULES registry — add a function, it runs automatically
- Zero external dependencies (stdlib only)
- Stateless: takes a flat param dict, returns a report dict
- Called via DroneValidator.run_audit() from web_server.py

Issue dict shape
----------------
{
    "severity":        "critical" | "warning" | "suggestion",
    "category":        str,          # maps to UI chips
    "title":           str,          # one-line summary
    "detail":          str,          # explanation + why it matters
    "params_involved": [str, ...],   # param names highlighted in table
    "fix":             {str: val}    # auto-applicable fix, or None
    "action":          str | None    # manual action hint when fix=None
}
"""

# ── Severity constants ────────────────────────────────────────────────────────
CRITICAL   = "critical"    # will prevent arming or cause a crash
WARNING    = "warning"     # suboptimal, may cause problems in flight
SUGGESTION = "suggestion"  # best-practice, not immediately dangerous


# ── Safe param getters ────────────────────────────────────────────────────────

def _p(params, key, default=None):
    """Return param as float, or default if missing/unconvertible."""
    val = params.get(key)
    if val is None:
        return default
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def _pi(params, key, default=0):
    """Return param as int."""
    v = _p(params, key, None)
    return int(round(v)) if v is not None else default


# ── Rule functions ────────────────────────────────────────────────────────────
# Each accepts the flat params dict and returns list[dict].

def rule_arming_checks(p):
    """ARMING_CHECK=0 disables ALL pre-arm safety checks."""
    val = _pi(p, "ARMING_CHECK", -1)
    if val == -1:
        return []
    if val == 0:
        return [{
            "severity": CRITICAL,
            "category": "safety",
            "title": "All arming checks disabled (ARMING_CHECK=0)",
            "detail": "ARMING_CHECK=0 bypasses every pre-arm safety check — the drone "
                      "can arm with sensor failures, missing calibration, or bad EKF. "
                      "This is only acceptable on a bench with props removed.",
            "params_involved": ["ARMING_CHECK"],
            "fix": {"ARMING_CHECK": 1},
            "action": None,
        }]
    return []


def rule_failsafe_rc(p):
    """RC throttle failsafe must be enabled with a sensible trigger value."""
    issues = []
    fs = _pi(p, "FS_THR_ENABLE", -1)
    if fs == -1:
        return []
    if fs == 0:
        issues.append({
            "severity": CRITICAL,
            "category": "safety",
            "title": "RC throttle failsafe disabled (FS_THR_ENABLE=0)",
            "detail": "If RC link is lost the drone will not trigger failsafe and may "
                      "fly away uncontrolled. Enable RC failsafe for all outdoor flights.",
            "params_involved": ["FS_THR_ENABLE"],
            "fix": {"FS_THR_ENABLE": 1},
            "action": None,
        })
    else:
        thr = _pi(p, "FS_THR_VALUE", 975)
        if thr < 800 or thr > 1100:
            issues.append({
                "severity": WARNING,
                "category": "safety",
                "title": f"RC failsafe throttle value out of range (FS_THR_VALUE={thr})",
                "detail": "FS_THR_VALUE should be ~50-75 PWM below your RC system's "
                          "minimum throttle — typically 925-975. Current value looks wrong.",
                "params_involved": ["FS_THR_VALUE"],
                "fix": {"FS_THR_VALUE": 975},
                "action": None,
            })
    return issues


def rule_failsafe_battery(p):
    """Battery failsafe voltages must be set and in the correct order."""
    issues = []
    low_v = _p(p, "BATT_LOW_VOLT", -1)
    crt_v = _p(p, "BATT_CRT_VOLT", -1)
    if low_v == -1 or crt_v == -1:
        return []

    if low_v == 0:
        issues.append({
            "severity": WARNING,
            "category": "battery",
            "title": "Battery low-voltage threshold not set (BATT_LOW_VOLT=0)",
            "detail": "No low-voltage warning will trigger. "
                      "Set to ~3.5 V × cell count (e.g. 14.0 V for 4S).",
            "params_involved": ["BATT_LOW_VOLT"],
            "fix": None,
            "action": "Set BATT_LOW_VOLT to 3.5 × number of cells",
        })
    if crt_v == 0:
        issues.append({
            "severity": WARNING,
            "category": "battery",
            "title": "Battery critical-voltage failsafe not set (BATT_CRT_VOLT=0)",
            "detail": "No critical-voltage RTL or land will trigger. "
                      "Set to ~3.3 V × cell count.",
            "params_involved": ["BATT_CRT_VOLT"],
            "fix": None,
            "action": "Set BATT_CRT_VOLT to 3.3 × number of cells",
        })
    if low_v > 0 and crt_v > 0 and crt_v >= low_v:
        issues.append({
            "severity": CRITICAL,
            "category": "battery",
            "title": "Critical battery voltage ≥ low-voltage threshold — thresholds inverted",
            "detail": f"BATT_CRT_VOLT ({crt_v:.1f} V) must be LOWER than "
                      f"BATT_LOW_VOLT ({low_v:.1f} V). Currently inverted — "
                      "the critical action will fire before the low-voltage warning.",
            "params_involved": ["BATT_LOW_VOLT", "BATT_CRT_VOLT"],
            "fix": None,
            "action": "Ensure BATT_CRT_VOLT < BATT_LOW_VOLT",
        })
    return issues


def rule_battery_capacity(p):
    """BATT_CAPACITY=0 means no mAh monitoring or consumed-mAh failsafe."""
    cap = _pi(p, "BATT_CAPACITY", -1)
    if cap == -1:
        return []
    if cap == 0:
        return [{
            "severity": WARNING,
            "category": "battery",
            "title": "Battery capacity not set (BATT_CAPACITY=0)",
            "detail": "BATT_CAPACITY=0 disables mAh tracking and capacity-based failsafe. "
                      "Set to your battery's rated capacity in mAh for accurate remaining-charge "
                      "estimates and capacity failsafe.",
            "params_involved": ["BATT_CAPACITY"],
            "fix": None,
            "action": "Set BATT_CAPACITY to your battery mAh (e.g. 5000 for a 5000 mAh pack)",
        }]
    return []


def rule_battery_monitor(p):
    """BATT_MONITOR=0 means no voltage or current is measured."""
    monitor = _pi(p, "BATT_MONITOR", -1)
    if monitor == -1:
        return []
    if monitor == 0:
        return [{
            "severity": WARNING,
            "category": "battery",
            "title": "Battery monitor disabled (BATT_MONITOR=0)",
            "detail": "No battery voltage or current is being measured. "
                      "All battery failsafes are inactive. "
                      "Set to 4 for voltage + current monitoring.",
            "params_involved": ["BATT_MONITOR"],
            "fix": {"BATT_MONITOR": 4},
            "action": None,
        }]
    return []


def rule_failsafe_gcs(p):
    """GCS (telemetry) failsafe should be enabled for autonomous missions."""
    fs = _pi(p, "FS_GCS_ENABLE", -1)
    if fs == -1:
        return []
    if fs == 0:
        return [{
            "severity": SUGGESTION,
            "category": "safety",
            "title": "GCS failsafe disabled (FS_GCS_ENABLE=0)",
            "detail": "If telemetry link is lost during an AUTO or GUIDED mission "
                      "no failsafe action will occur. Recommended for autonomous flights.",
            "params_involved": ["FS_GCS_ENABLE"],
            "fix": {"FS_GCS_ENABLE": 1},
            "action": None,
        }]
    return []


def rule_calibration_compass(p):
    """Compass offsets all zero = factory default = never calibrated."""
    ox = _p(p, "COMPASS_OFS_X", None)
    oy = _p(p, "COMPASS_OFS_Y", None)
    oz = _p(p, "COMPASS_OFS_Z", None)
    if ox is None:
        return []
    issues = []
    if ox == 0.0 and oy == 0.0 and oz == 0.0:
        issues.append({
            "severity": CRITICAL,
            "category": "sensors",
            "title": "Compass not calibrated — all offsets are zero (factory default)",
            "detail": "COMPASS_OFS X=0 Y=0 Z=0 are the factory defaults, meaning compass "
                      "calibration has never been run. The EKF will have incorrect heading "
                      "which can cause fly-aways in GPS-assisted modes.",
            "params_involved": ["COMPASS_OFS_X", "COMPASS_OFS_Y", "COMPASS_OFS_Z"],
            "fix": None,
            "action": "Run compass calibration from the Calibration tab",
        })
    elif abs(ox) > 600 or abs(oy) > 600 or abs(oz) > 600:
        issues.append({
            "severity": WARNING,
            "category": "sensors",
            "title": f"Compass offsets are very large (X={ox:.0f} Y={oy:.0f} Z={oz:.0f})",
            "detail": "Offsets >400 G suggest strong magnetic interference near the compass. "
                      "Consider relocating the compass or running MagFit after a flight.",
            "params_involved": ["COMPASS_OFS_X", "COMPASS_OFS_Y", "COMPASS_OFS_Z"],
            "fix": None,
            "action": "Relocate compass away from power wires or run MagFit",
        })
    return issues


def rule_compass_use(p):
    """COMPASS_USE=0 — compass disabled."""
    val = _pi(p, "COMPASS_USE", -1)
    if val == -1:
        return []
    if val == 0:
        return [{
            "severity": WARNING,
            "category": "sensors",
            "title": "Compass disabled (COMPASS_USE=0)",
            "detail": "EKF will estimate heading from GPS velocity instead of the magnetometer. "
                      "This degrades heading accuracy at low speed. Only acceptable if using "
                      "a dual-GPS heading setup.",
            "params_involved": ["COMPASS_USE"],
            "fix": None,
            "action": "Enable compass unless using dual-GPS heading",
        }]
    return []


def rule_calibration_accel(p):
    """Accel offsets all zero = uncalibrated."""
    ox = _p(p, "INS_ACCOFFS_X", None)
    oy = _p(p, "INS_ACCOFFS_Y", None)
    oz = _p(p, "INS_ACCOFFS_Z", None)
    if ox is None:
        return []
    if ox == 0.0 and oy == 0.0 and oz == 0.0:
        return [{
            "severity": CRITICAL,
            "category": "sensors",
            "title": "Accelerometer not calibrated — all offsets are zero",
            "detail": "INS_ACCOFFS X=0 Y=0 Z=0 are factory defaults. "
                      "Level calibration at minimum must be completed before first flight. "
                      "Uncalibrated accel causes attitude estimation errors.",
            "params_involved": ["INS_ACCOFFS_X", "INS_ACCOFFS_Y", "INS_ACCOFFS_Z"],
            "fix": None,
            "action": "Run accelerometer calibration from the Calibration tab",
        }]
    return []


def rule_rc_calibration(p):
    """RC1_MIN/MAX at exact defaults (1100/1900) = RC never calibrated."""
    rc1_min = _pi(p, "RC1_MIN", -1)
    rc1_max = _pi(p, "RC1_MAX", -1)
    if rc1_min == -1 or rc1_max == -1:
        return []
    if rc1_min == 1100 and rc1_max == 1900:
        return [{
            "severity": WARNING,
            "category": "rc",
            "title": "RC not calibrated — default values detected (RC1_MIN=1100, RC1_MAX=1900)",
            "detail": "These are factory defaults. RC calibration maps your transmitter's "
                      "actual output range to ArduPilot's expected range. Without it, "
                      "stick scaling is incorrect and failsafe may not trigger reliably.",
            "params_involved": ["RC1_MIN", "RC1_MAX"],
            "fix": None,
            "action": "Run RC calibration",
        }]
    return []


def rule_dshot_consistency(p):
    """DShot: MOT_PWM_TYPE, SERVO_BLH_OTYPE and SERVO_BLH_MASK must all be consistent."""
    DSHOT = {4: "DShot150", 5: "DShot300", 6: "DShot600", 7: "DShot1200"}
    mot_type = _pi(p, "MOT_PWM_TYPE", -1)
    if mot_type not in DSHOT:
        return []

    issues = []
    blh_otype = _pi(p, "SERVO_BLH_OTYPE", -1)
    if blh_otype != -1 and blh_otype != mot_type:
        issues.append({
            "severity": CRITICAL,
            "category": "motors",
            "title": f"DShot type mismatch: MOT_PWM_TYPE={mot_type} ({DSHOT[mot_type]}) "
                     f"but SERVO_BLH_OTYPE={blh_otype}",
            "detail": "Both parameters must be set to the same DShot variant. "
                      "Motors will not respond correctly with this mismatch.",
            "params_involved": ["MOT_PWM_TYPE", "SERVO_BLH_OTYPE"],
            "fix": {"SERVO_BLH_OTYPE": mot_type},
            "action": None,
        })

    blh_mask = _pi(p, "SERVO_BLH_MASK", 0)
    if blh_mask == 0:
        issues.append({
            "severity": WARNING,
            "category": "motors",
            "title": f"SERVO_BLH_MASK=0 — no outputs assigned to {DSHOT[mot_type]}",
            "detail": f"DShot ({DSHOT[mot_type]}) is selected but no servo outputs are "
                      "assigned to BLHeli protocol. Set to 15 for a standard quad "
                      "(outputs 1-4).",
            "params_involved": ["SERVO_BLH_MASK"],
            "fix": {"SERVO_BLH_MASK": 15},
            "action": None,
        })
    return issues


def rule_mot_spin(p):
    """MOT_SPIN_ARM must be less than MOT_SPIN_MIN."""
    spin_min = _p(p, "MOT_SPIN_MIN", None)
    spin_arm = _p(p, "MOT_SPIN_ARM", None)
    if spin_min is None or spin_arm is None:
        return []
    issues = []
    if spin_arm >= spin_min:
        issues.append({
            "severity": WARNING,
            "category": "motors",
            "title": f"MOT_SPIN_ARM ({spin_arm:.3f}) ≥ MOT_SPIN_MIN ({spin_min:.3f})",
            "detail": "Arm spin should be lower than minimum flight spin to avoid "
                      "an abrupt throttle jump when first applying throttle.",
            "params_involved": ["MOT_SPIN_ARM", "MOT_SPIN_MIN"],
            "fix": None,
            "action": "Set MOT_SPIN_ARM slightly below MOT_SPIN_MIN",
        })
    if spin_min < 0.05:
        issues.append({
            "severity": WARNING,
            "category": "motors",
            "title": f"MOT_SPIN_MIN is very low ({spin_min:.3f})",
            "detail": "Very low values risk motor stall during aggressive maneuvers. "
                      "Typical range is 0.10–0.15.",
            "params_involved": ["MOT_SPIN_MIN"],
            "fix": None,
            "action": None,
        })
    return issues


def rule_serial_gps(p):
    """If GPS is enabled, a serial port must be configured for GPS protocol (5)."""
    gps_type = _pi(p, "GPS_TYPE", 0)
    if gps_type == 0:
        return []

    gps_uart = None
    gps_baud = None
    for i in range(8):
        if _pi(p, f"SERIAL{i}_PROTOCOL", -1) == 5:
            gps_uart = i
            gps_baud = _pi(p, f"SERIAL{i}_BAUD", 38)
            break

    if gps_uart is None:
        return [{
            "severity": CRITICAL,
            "category": "gps",
            "title": "GPS enabled (GPS_TYPE>0) but no serial port set to GPS protocol",
            "detail": f"GPS_TYPE={gps_type} but no SERIALX_PROTOCOL=5 found. "
                      "The GPS module cannot communicate with the flight controller.",
            "params_involved": ["GPS_TYPE"],
            "fix": None,
            "action": "Set SERIALX_PROTOCOL=5 on the UART where your GPS is wired",
        }]

    issues = []
    # NMEA GPS (type 5) typical baud is 9 (9600) or 38 (38400)
    if gps_type == 5 and gps_baud not in (4, 9, 38):
        issues.append({
            "severity": WARNING,
            "category": "gps",
            "title": f"NMEA GPS baud rate unusual (SERIAL{gps_uart}_BAUD={gps_baud})",
            "detail": "NMEA GPS modules typically run at 9600 (9) or 38400 (38).",
            "params_involved": [f"SERIAL{gps_uart}_BAUD"],
            "fix": {f"SERIAL{gps_uart}_BAUD": 9},
            "action": None,
        })
    return issues


def rule_elrs_crsf(p):
    """
    ELRS/CRSF: SERIALX_PROTOCOL=23, baud must be 420 (420 kbaud),
    RC_PROTOCOLS must include CRSF bit, RSSI_TYPE should be 3.
    """
    elrs_uart = None
    elrs_baud = None
    for i in range(8):
        if _pi(p, f"SERIAL{i}_PROTOCOL", -1) == 23:
            elrs_uart = i
            elrs_baud = _pi(p, f"SERIAL{i}_BAUD", -1)
            break
    if elrs_uart is None:
        return []

    issues = []
    if elrs_baud != 420:
        issues.append({
            "severity": CRITICAL,
            "category": "rc",
            "title": f"ELRS baud rate wrong — SERIAL{elrs_uart}_BAUD={elrs_baud}, need 420",
            "detail": "ELRS uses 420000 baud (SERIAL_BAUD=420). "
                      "At the wrong baud rate RC will not bind or will drop packets.",
            "params_involved": [f"SERIAL{elrs_uart}_BAUD"],
            "fix": {f"SERIAL{elrs_uart}_BAUD": 420},
            "action": None,
        })

    rc_proto = _pi(p, "RC_PROTOCOLS", 1)
    # 1 = All protocols; 512 = CRSF bit. Either is fine.
    if rc_proto != 1 and not (rc_proto & 512):
        issues.append({
            "severity": WARNING,
            "category": "rc",
            "title": f"RC_PROTOCOLS={rc_proto} does not include CRSF (bit 9 = 512)",
            "detail": "ELRS uses CRSF protocol. Set RC_PROTOCOLS=1 (all) or add bit 512.",
            "params_involved": ["RC_PROTOCOLS"],
            "fix": {"RC_PROTOCOLS": rc_proto | 512},
            "action": None,
        })

    rssi_type = _pi(p, "RSSI_TYPE", -1)
    if rssi_type != -1 and rssi_type not in (0, 3):
        issues.append({
            "severity": SUGGESTION,
            "category": "rc",
            "title": f"RSSI_TYPE={rssi_type} — set to 3 for ELRS link quality",
            "detail": "RSSI_TYPE=3 reports ELRS link quality (LQ) in the GCS. "
                      "Other values give incorrect or no RSSI readout.",
            "params_involved": ["RSSI_TYPE"],
            "fix": {"RSSI_TYPE": 3},
            "action": None,
        })
    return issues


def rule_duplicate_serial_protocols(p):
    """Two serial ports with the same non-MAVLink protocol is usually a mistake."""
    ALLOWED_MULTI = {1, 2}  # MAVLink1 + MAVLink2 can legitimately run on multiple ports
    PROTO_NAMES = {
        5: "GPS", 7: "Alexmos Gimbal", 8: "SToRM32 Gimbal",
        9: "Rangefinder", 10: "FrSky SPort", 19: "FrSky FPort",
        23: "RCIN (ELRS/CRSF)", 24: "EFI",
    }

    proto_map = {}  # proto_id → [uart_indices]
    for i in range(8):
        proto = _pi(p, f"SERIAL{i}_PROTOCOL", -1)
        if proto <= 0:
            continue
        proto_map.setdefault(proto, []).append(i)

    issues = []
    for proto, uarts in proto_map.items():
        if len(uarts) > 1 and proto not in ALLOWED_MULTI:
            name = PROTO_NAMES.get(proto, f"Protocol {proto}")
            uart_str = " and ".join(f"SERIAL{i}" for i in uarts)
            issues.append({
                "severity": WARNING,
                "category": "comms",
                "title": f"Duplicate serial protocol: {name} on {uart_str}",
                "detail": f"Both {uart_str} are set to {name} (protocol {proto}). "
                          "Only one peripheral of each type should be connected. "
                          "Disable the unused port by setting its protocol to -1.",
                "params_involved": [f"SERIAL{i}_PROTOCOL" for i in uarts],
                "fix": None,
                "action": f"Set unused port's SERIALX_PROTOCOL to -1 (disabled)",
            })
    return issues


def rule_imu_orientation(p):
    """Non-zero AHRS_ORIENTATION — flag and warn about axis reversals."""
    orient = _pi(p, "AHRS_ORIENTATION", -1)
    if orient == -1 or orient == 0:
        return []

    NAMES = {
        1: "Yaw45", 2: "Yaw90", 3: "Yaw135", 4: "Yaw180",
        5: "Yaw225", 6: "Yaw270", 7: "Yaw315",
        8: "Roll180", 12: "Pitch180",
        16: "Roll90", 20: "Roll270", 24: "Pitch90", 25: "Pitch270",
    }
    # These orientations reverse the pitch axis (nose-up = nose-down in GCS)
    PITCH_FLIP = {8, 9, 10, 11, 12, 13, 14, 15}
    name = NAMES.get(orient, f"value {orient}")
    detail = (f"AHRS_ORIENTATION={orient} ({name}). "
              "Only set this if the FC is physically mounted non-standard. ")
    if orient in PITCH_FLIP:
        detail += "⚠ This orientation reverses the pitch axis — verify HUD shows correct attitude."

    return [{
        "severity": WARNING,
        "category": "sensors",
        "title": f"Non-standard IMU orientation: {name} (AHRS_ORIENTATION={orient})",
        "detail": detail,
        "params_involved": ["AHRS_ORIENTATION"],
        "fix": None,
        "action": "Verify HUD attitude matches physical drone orientation",
    }]


def rule_ekf_config(p):
    """EKF2 and EKF3 should not both be on (wastes CPU) or both off (no attitude)."""
    ek2 = _pi(p, "EK2_ENABLE", -1)
    ek3 = _pi(p, "EK3_ENABLE", -1)
    if ek2 == -1 or ek3 == -1:
        return []
    issues = []
    if ek2 == 1 and ek3 == 1:
        issues.append({
            "severity": WARNING,
            "category": "ekf",
            "title": "Both EKF2 and EKF3 enabled — redundant CPU load",
            "detail": "ArduPilot 4.x uses EKF3 by default. Running EKF2 simultaneously "
                      "wastes CPU and memory with no benefit. Disable EKF2.",
            "params_involved": ["EK2_ENABLE", "EK3_ENABLE"],
            "fix": {"EK2_ENABLE": 0},
            "action": None,
        })
    if ek2 == 0 and ek3 == 0:
        issues.append({
            "severity": CRITICAL,
            "category": "ekf",
            "title": "Both EKF2 and EKF3 disabled — no attitude estimation",
            "detail": "No EKF is running. Attitude estimation will fail and arming "
                      "will be refused.",
            "params_involved": ["EK2_ENABLE", "EK3_ENABLE"],
            "fix": {"EK3_ENABLE": 1},
            "action": None,
        })
    return issues


def rule_notch_filter(p):
    """If ESC telemetry is streaming RPM, suggest enabling harmonic notch."""
    if _pi(p, "SERVO_BLH_TRATE", 0) == 0:
        return []
    if _pi(p, "INS_HNTCH_ENABLE", 0) == 0:
        return [{
            "severity": SUGGESTION,
            "category": "filters",
            "title": "ESC RPM telemetry available but harmonic notch filter not enabled",
            "detail": "SERVO_BLH_TRATE > 0 means ESC RPM data is available. "
                      "INS_HNTCH_ENABLE=0 leaves this data unused. "
                      "Enabling the dynamic harmonic notch significantly reduces "
                      "vibration noise in the EKF and improves flight performance.",
            "params_involved": ["INS_HNTCH_ENABLE", "SERVO_BLH_TRATE"],
            "fix": {"INS_HNTCH_ENABLE": 1, "INS_HNTCH_MODE": 3, "INS_HNTCH_REF": 0.25},
            "action": None,
        }]
    return []


def rule_pid_sanity(p):
    """Flag zero PIDs (unconfigured) or unusually high rate P gains."""
    rll_p = _p(p, "ATC_RAT_RLL_P", None)
    pit_p = _p(p, "ATC_RAT_PIT_P", None)
    if rll_p is None or pit_p is None:
        return []

    issues = []
    if rll_p == 0.0 and pit_p == 0.0:
        issues.append({
            "severity": WARNING,
            "category": "pid",
            "title": "Rate controller P gains are zero — PIDs not configured",
            "detail": "ATC_RAT_RLL_P=0 and ATC_RAT_PIT_P=0. These gains are essential "
                      "for stable flight. Either AutoTune was never run or params were reset.",
            "params_involved": ["ATC_RAT_RLL_P", "ATC_RAT_PIT_P"],
            "fix": None,
            "action": "Run AutoTune or set starting gains (e.g. 0.135)",
        })
    elif rll_p > 0.5 or pit_p > 0.5:
        issues.append({
            "severity": WARNING,
            "category": "pid",
            "title": f"Rate P gains are very high (Roll={rll_p:.3f}, Pitch={pit_p:.3f})",
            "detail": "Values above 0.4–0.5 are unusually high for most craft and may "
                      "cause oscillation or instability.",
            "params_involved": ["ATC_RAT_RLL_P", "ATC_RAT_PIT_P"],
            "fix": None,
            "action": None,
        })
    return issues


def rule_geofence(p):
    """Geofence enabled with action=Report Only gives false sense of security."""
    fence_en = _pi(p, "FENCE_ENABLE", -1)
    if fence_en == -1 or fence_en == 0:
        return []
    fence_action = _pi(p, "FENCE_ACTION", -1)
    if fence_action == 0:
        return [{
            "severity": WARNING,
            "category": "safety",
            "title": "Geofence enabled but action is 'Report Only' (FENCE_ACTION=0)",
            "detail": "The drone will breach the fence without any corrective action — "
                      "it will only log the event. Set FENCE_ACTION=1 for RTL on breach.",
            "params_involved": ["FENCE_ENABLE", "FENCE_ACTION"],
            "fix": {"FENCE_ACTION": 1},
            "action": None,
        }]
    return []


def rule_log_bitmask(p):
    """LOG_BITMASK=0 means no onboard flight data is recorded."""
    val = _pi(p, "LOG_BITMASK", -1)
    if val == -1:
        return []
    if val == 0:
        return [{
            "severity": WARNING,
            "category": "comms",
            "title": "Onboard logging disabled (LOG_BITMASK=0)",
            "detail": "No flight data is being written to the SD card. "
                      "Without logs, crash diagnosis is impossible.",
            "params_involved": ["LOG_BITMASK"],
            "fix": {"LOG_BITMASK": 65535},
            "action": None,
        }]
    return []


def rule_telemetry_baud(p):
    """
    Check that telemetry stream rates on SERIAL0 are within the baud capacity.
    SR0_* rates (in Hz) × ~100 bytes/msg × 8 bits should not exceed baud rate.
    """
    issues = []
    # Look for the MAVLink port with highest stream rates (usually SERIAL0 or SERIAL1)
    for uart in range(8):
        proto = _pi(p, f"SERIAL{uart}_PROTOCOL", -1)
        if proto not in (1, 2):  # MAVLink1 or MAVLink2
            continue
        baud = _pi(p, f"SERIAL{uart}_BAUD", 57)
        # Convert SERIAL_BAUD value to actual bps
        baud_map = {
            1: 1200, 2: 2400, 4: 4800, 9: 9600, 19: 19200,
            38: 38400, 57: 57600, 111: 111100, 115: 115200,
            230: 230400, 460: 460800, 500: 500000, 921: 921600,
        }
        actual_baud = baud_map.get(baud, baud * 1000)

        # Sum up stream rates for this port
        sr_prefix = f"SR{uart}_"
        total_rate = 0
        for k, v in p.items():
            if k.startswith(sr_prefix):
                total_rate += max(0, _p(p, k, 0))

        # Rough estimate: ~100 bytes per MAVLink message, 10 bits per byte
        estimated_bps = total_rate * 100 * 10
        if actual_baud > 0 and estimated_bps > actual_baud * 0.8:
            issues.append({
                "severity": WARNING,
                "category": "comms",
                "title": f"Telemetry stream rate may exceed SERIAL{uart} baud capacity",
                "detail": f"SERIAL{uart}_BAUD={baud} ({actual_baud} bps) but combined "
                          f"SR{uart}_* stream rates sum to ~{total_rate:.0f} Hz "
                          f"(~{estimated_bps} bps estimated). "
                          "Telemetry may be delayed or dropped.",
                "params_involved": [f"SERIAL{uart}_BAUD"],
                "fix": {f"SERIAL{uart}_BAUD": 115},
                "action": f"Increase SERIAL{uart}_BAUD or reduce SR{uart}_* rates",
            })
    return issues


# ── Rule registry ─────────────────────────────────────────────────────────────
# Add a function here → it runs automatically on every audit.

ALL_RULES = [
    rule_arming_checks,
    rule_failsafe_rc,
    rule_failsafe_battery,
    rule_battery_capacity,
    rule_battery_monitor,
    rule_failsafe_gcs,
    rule_calibration_compass,
    rule_compass_use,
    rule_calibration_accel,
    rule_rc_calibration,
    rule_dshot_consistency,
    rule_mot_spin,
    rule_serial_gps,
    rule_elrs_crsf,
    rule_duplicate_serial_protocols,
    rule_imu_orientation,
    rule_ekf_config,
    rule_notch_filter,
    rule_pid_sanity,
    rule_geofence,
    rule_log_bitmask,
    rule_telemetry_baud,
]


# ── Engine class ──────────────────────────────────────────────────────────────

class ParamRuleEngine:
    """
    Run all rules against a flat parameter dict.

    Parameters
    ----------
    flat_params        : { "PARAM_NAME": value, ... }  — from validator.params_dict
    categorized_params : { "Category": { ... } }       — optional, not used by rules
                         but kept for future rule extensions
    """

    def __init__(self, flat_params: dict, categorized_params: dict = None):
        self.p   = {k: v for k, v in (flat_params or {}).items()}
        self.cat = categorized_params or {}

    def run(self) -> dict:
        """
        Run all rules and return a structured report.

        Returns
        -------
        {
            "summary":       { "critical", "warning", "suggestion", "passed", "total" },
            "issues":        [ issue_dict, ... ],   sorted critical → warning → suggestion
            "passed_checks": [ { "check": str }, ... ]
        }
        """
        issues  = []
        passed  = []

        for rule_fn in ALL_RULES:
            try:
                result = rule_fn(self.p)
                if result:
                    issues.extend(result)
                else:
                    passed.append({
                        "check": rule_fn.__name__
                                         .replace("rule_", "")
                                         .replace("_", " ")
                                         .title()
                    })
            except Exception as exc:
                # Never let one rule crash the whole audit
                issues.append({
                    "severity": "warning",
                    "category": "engine",
                    "title": f"Rule check failed: {rule_fn.__name__}",
                    "detail": str(exc),
                    "params_involved": [],
                    "fix": None,
                    "action": None,
                })

        _ORDER = {CRITICAL: 0, WARNING: 1, SUGGESTION: 2}
        issues.sort(key=lambda x: _ORDER.get(x["severity"], 9))

        return {
            "summary": {
                "critical":   sum(1 for i in issues if i["severity"] == CRITICAL),
                "warning":    sum(1 for i in issues if i["severity"] == WARNING),
                "suggestion": sum(1 for i in issues if i["severity"] == SUGGESTION),
                "passed":     len(passed),
                "total":      len(ALL_RULES),
            },
            "issues":        issues,
            "passed_checks": passed,
        }
