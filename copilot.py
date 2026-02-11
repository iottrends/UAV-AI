"""
Co-Pilot Mode — fast-path command interceptor.

No AI, no network calls. Pure regex/keyword matching for safety-critical
and common status queries. Returns a dict compatible with the chat_response
event or None (fall through to Gemini).
"""

import re

# ArduCopter custom_mode values (mirrors drone-view.js)
COPTER_MODES = {
    0: 'STABILIZE', 1: 'ACRO', 2: 'ALT_HOLD', 3: 'AUTO',
    4: 'GUIDED', 5: 'LOITER', 6: 'RTL', 7: 'CIRCLE',
    9: 'LAND', 11: 'DRIFT', 13: 'SPORT', 14: 'FLIP',
    15: 'AUTOTUNE', 16: 'POSHOLD', 17: 'BRAKE', 18: 'THROW',
    19: 'AVOID_ADSB', 20: 'GUIDED_NOGPS', 21: 'SMART_RTL',
    22: 'FLOWHOLD', 23: 'FOLLOW', 24: 'ZIGZAG', 25: 'SYSTEMID',
    26: 'AUTOROTATE', 27: 'AUTO_RTL',
}


def _normalize(query):
    """Lowercase, strip punctuation, collapse whitespace."""
    q = query.lower().strip()
    q = re.sub(r'[^\w\s]', '', q)
    q = re.sub(r'\s+', ' ', q)
    return q


# ---------------------------------------------------------------------------
# Command handlers — each returns dict or None
# ---------------------------------------------------------------------------

def _handle_arm(query, buf):
    return {
        "response": "Arming motors.",
        "fix_command": {
            "command": "MAV_CMD_COMPONENT_ARM_DISARM",
            "param1": 1,
            "param2": 21196,
        },
    }


def _handle_disarm(query, buf):
    return {
        "response": "Disarming motors.",
        "fix_command": {
            "command": "MAV_CMD_COMPONENT_ARM_DISARM",
            "param1": 0,
            "param2": 21196,
        },
    }


def _handle_land(query, buf):
    return {
        "response": "Landing now.",
        "fix_command": {"command": "MAV_CMD_NAV_LAND"},
    }


def _handle_rtl(query, buf):
    return {
        "response": "Returning to launch.",
        "fix_command": {"command": "MAV_CMD_NAV_RETURN_TO_LAUNCH"},
    }


def _handle_poshold(query, buf):
    return {
        "response": "Switching to Position Hold.",
        "fix_command": {
            "command": "MAV_CMD_DO_SET_MODE",
            "param1": 1,
            "param2": 16,
        },
    }


def _handle_loiter(query, buf):
    return {
        "response": "Switching to Loiter.",
        "fix_command": {
            "command": "MAV_CMD_DO_SET_MODE",
            "param1": 1,
            "param2": 5,
        },
    }


def _handle_stabilize(query, buf):
    return {
        "response": "Switching to Stabilize.",
        "fix_command": {
            "command": "MAV_CMD_DO_SET_MODE",
            "param1": 1,
            "param2": 0,
        },
    }


def _handle_guided(query, buf):
    return {
        "response": "Switching to Guided mode.",
        "fix_command": {
            "command": "MAV_CMD_DO_SET_MODE",
            "param1": 1,
            "param2": 4,
        },
    }


def _handle_brake(query, buf):
    return {
        "response": "Braking — holding position.",
        "fix_command": {
            "command": "MAV_CMD_DO_SET_MODE",
            "param1": 1,
            "param2": 17,
        },
    }


def _handle_althold(query, buf):
    return {
        "response": "Switching to Altitude Hold.",
        "fix_command": {
            "command": "MAV_CMD_DO_SET_MODE",
            "param1": 1,
            "param2": 2,
        },
    }


# ---------------------------------------------------------------------------
# Status query handlers
# ---------------------------------------------------------------------------

def _handle_gps_status(query, buf):
    msg = buf.get("GPS_RAW_INT")
    if not msg:
        return {"response": "No GPS data available yet."}
    fix = msg.get("fix_type", 0)
    sats = msg.get("satellites_visible", 0)
    fix_names = {0: "No GPS", 1: "No Fix", 2: "2D Fix", 3: "3D Fix",
                 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}
    fix_str = fix_names.get(fix, f"Unknown ({fix})")
    return {"response": f"GPS: {fix_str}, {sats} satellites visible."}


def _handle_battery(query, buf):
    msg = buf.get("SYS_STATUS")
    if not msg:
        return {"response": "No battery data available yet."}
    voltage = msg.get("voltage_battery", 0) / 1000.0
    current = msg.get("current_battery", 0) / 1000.0
    remaining = msg.get("battery_remaining", -1)
    parts = [f"{voltage:.2f}V"]
    if current > 0:
        parts.append(f"{current:.1f}A")
    if remaining >= 0:
        parts.append(f"{remaining}% remaining")
    return {"response": f"Battery: {', '.join(parts)}."}


def _handle_altitude(query, buf):
    msg = buf.get("VFR_HUD")
    if not msg:
        return {"response": "No altitude data available yet."}
    alt = msg.get("alt", 0)
    climb = msg.get("climb", 0)
    return {"response": f"Altitude: {alt:.1f}m, climb rate: {climb:.1f}m/s."}


def _handle_mode_check(query, buf):
    hb = buf.get("HEARTBEAT")
    if not hb:
        return {"response": "No heartbeat data available yet."}
    custom_mode = hb.get("custom_mode", 0)
    mode_name = COPTER_MODES.get(custom_mode, f"Unknown ({custom_mode})")
    armed = "ARMED" if hb.get("base_mode", 0) & 128 else "DISARMED"
    return {"response": f"Current mode: {mode_name}, state: {armed}."}


# ---------------------------------------------------------------------------
# Pattern table — ordered list of (compiled_regex, handler)
# First match wins.
# ---------------------------------------------------------------------------

_PATTERNS = [
    # Commands (order matters: "disarm" before "arm" so "disarm" isn't caught by "arm")
    (re.compile(r'\bdisarm\b'), _handle_disarm),
    (re.compile(r'\barm\b'), _handle_arm),
    (re.compile(r'\bland\b'), _handle_land),
    (re.compile(r'\b(rtl|return to launch|return home|go home|come back)\b'), _handle_rtl),
    (re.compile(r'\b(position hold|poshold|hold position|hold|stop)\b'), _handle_poshold),
    (re.compile(r'\b(brake|stop now)\b'), _handle_brake),
    (re.compile(r'\bloiter\b'), _handle_loiter),
    (re.compile(r'\bstabili[sz]e\b'), _handle_stabilize),
    (re.compile(r'\bguided\b'), _handle_guided),
    (re.compile(r'\b(alt hold|altitude hold)\b'), _handle_althold),
    # Status queries
    (re.compile(r'\b(gps status|gps|how many satellites)\b'), _handle_gps_status),
    (re.compile(r'\b(battery|battery status|voltage)\b'), _handle_battery),
    (re.compile(r'\b(altitude|how high|what altitude)\b'), _handle_altitude),
    (re.compile(r'\b(what mode|current mode|which mode)\b'), _handle_mode_check),
]


def try_fast_command(query, mavlink_buffer):
    """Attempt to match a fast-path command or status query.

    Args:
        query: User's raw text query.
        mavlink_buffer: dict keyed by MAVLink message type -> latest msg dict.

    Returns:
        dict with "response" (and optionally "fix_command") on match,
        or None if no match (fall through to Gemini).
    """
    normalized = _normalize(query)
    for pattern, handler in _PATTERNS:
        if pattern.search(normalized):
            return handler(normalized, mavlink_buffer)
    return None
