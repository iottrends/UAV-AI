import os
import logging
import json
import time
from dotenv import load_dotenv
import google.generativeai as genai

# Optional provider imports — only needed if API keys are configured
try:
    import openai as openai_module
except ImportError:
    openai_module = None

try:
    import anthropic as anthropic_module
except ImportError:
    anthropic_module = None

# Get the agent logger
agent_logger = logging.getLogger('agent')

jarvis_mav_data = {}  # dict keyed by message type → latest msg of each type

# Load environment variables from .env file
load_dotenv()

# Get API key from environment variable
api_key = os.getenv("GEMINI_API_KEY")

# Configure the API with the key from env file
genai.configure(api_key=api_key)
agent_logger.info("Initializing Gemini model")

# System instruction (static — includes prompt template but NOT params or MAVLink data)
SYSTEM_INSTRUCTION = """You are a MAVLink drone assistant.
Analyze the MAVLink messages, user query, and available parameter references to determine intent.

### Intent Categories:
1️⃣ **Status Queries:**
   - "What is my battery level?" → Reads MAVLink telemetry.
   - "Is the GPS locked?" → Checks GPS fix type.
2️⃣ **Diagnostic Queries:**
   - "Why is the motor not spinning?" → Checks ESC status.
   - "Why is the compass not working?" → Checks sensor health & calibration.
3️⃣ **Tuning Queries:**
   - "How can I tune my drone for better stability?" → Respond with relevant parameter list and recommended values.
4️⃣ **Action Commands:**
   - "Arm the drone" → Generates `fix_command` for arming.
   - "Disarm the drone" → Generates `fix_command` for disarming.
   - "Spin motor 1 at 50%" → Generates `fix_command` for motor testing.
   - "Take off" → Generates `fix_command` for taking off (from ground ONLY).
   - "Land" → Generates `fix_command` for landing.
   - "Go to 5m altitude" → If airborne, use MAV_CMD_CONDITION_CHANGE_ALT (NOT takeoff).
   - "Switch to loiter/poshold/RTL" → Generates `fix_command` for MAV_CMD_DO_SET_MODE.
   - "Go home" / "Return to launch" → MAV_CMD_NAV_RETURN_TO_LAUNCH.
   - "Change speed to 5 m/s" → MAV_CMD_DO_CHANGE_SPEED.

### Instructions:
- If it is a **status query**, extract and parse the MAVLink data.
- If it is a **diagnostic query**, find possible issues and suggest a fix.
- If it is a **tuning query**, respond first with relevant parameters and their recommended values.
- If it is an **action command**, generate the appropriate MAVLink command in the `fix_command` field.
- Use the available **parameter list** for referencing correct parameters and values.
- **Ask clarifying questions ONLY if essential information is missing** from the user query.
- If a fix is needed, suggest the correct **MAVLink command** or **parameter update**.

### Expected JSON Response:
Respond in **strict JSON format only**, without extra text.
{
    "intent": "status" or "diagnostic" or "tuning" or "action",
    "message": "your response here",
    "fix_command": "MAVLink command JSON object or null",
    "recommended_param": "list of recommended parameters and values, or null",
    "clarification_needed": "your clarification question if needed, or null"
}

### MAVLink Command JSON Object Format Examples for `fix_command`:
For MAV_CMD_COMPONENT_ARM_DISARM:
{ "command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 1, "param2": 21196 } // Arm
{ "command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 0, "param2": 29892 } // Disarm

For MAV_CMD_DO_MOTOR_TEST (e.g., spin motor 1 at 50% for 5 seconds):
{ "command": "MAV_CMD_DO_MOTOR_TEST", "param1": 1, "param2": 1, "param3": 500, "param4": 5, "param5": 0, "param6": 0 } // Motor 1, Thrust, 50% (500), 5 seconds
// param1: instance (motor ID, 1-based), param2: throttle type (1=Thrust), param3: throttle (0-1000 for 0-100%), param4: timeout (seconds)

For MAV_CMD_NAV_TAKEOFF:
{ "command": "MAV_CMD_NAV_TAKEOFF", "param1": 0, "param2": 0, "param3": 0, "param4": 0, "param5": 0, "param6": 0, "param7": 2.0 } // Take off to 2 meters altitude

For MAV_CMD_NAV_LAND:
{ "command": "MAV_CMD_NAV_LAND", "param1": 0, "param2": 0, "param3": 0, "param4": 0, "param5": 0, "param6": 0, "param7": 0 } // Land at current position

For MAV_CMD_CONDITION_CHANGE_ALT (change altitude while already in flight):
{ "command": "MAV_CMD_CONDITION_CHANGE_ALT", "param1": 1.0, "param7": 5.0 } // Change to 5m altitude at 1 m/s climb rate
// param1: descent/climb rate (m/s, positive=up), param7: target altitude (meters)
// IMPORTANT: Use this instead of MAV_CMD_NAV_TAKEOFF when the drone is already airborne.
// MAV_CMD_NAV_TAKEOFF ONLY works from the ground — it will FAIL if the drone is already flying.

For MAV_CMD_DO_SET_MODE (change flight mode):
{ "command": "MAV_CMD_DO_SET_MODE", "param1": 1, "param2": 4 } // Set GUIDED mode
// param1: mode flag (always 1 = MAV_MODE_FLAG_CUSTOM_MODE_ENABLED), param2: ArduCopter custom mode number
// ArduCopter mode numbers: 0=STABILIZE, 2=ALT_HOLD, 3=AUTO, 4=GUIDED, 5=LOITER, 6=RTL, 9=LAND, 16=POSHOLD
// Examples: GUIDED={ "command": "MAV_CMD_DO_SET_MODE", "param1": 1, "param2": 4 }
//           LOITER={ "command": "MAV_CMD_DO_SET_MODE", "param1": 1, "param2": 5 }
//           POSHOLD={ "command": "MAV_CMD_DO_SET_MODE", "param1": 1, "param2": 16 }
//           RTL={ "command": "MAV_CMD_DO_SET_MODE", "param1": 1, "param2": 6 }

For MAV_CMD_NAV_RETURN_TO_LAUNCH:
{ "command": "MAV_CMD_NAV_RETURN_TO_LAUNCH" } // Return to launch/home position

For MAV_CMD_DO_CHANGE_SPEED:
{ "command": "MAV_CMD_DO_CHANGE_SPEED", "param1": 0, "param2": 5.0, "param3": -1 } // Set ground speed to 5 m/s
// param1: speed type (0=ground, 1=air), param2: speed (m/s), param3: throttle (-1=no change)
"""

# Per-query template (only MAVLink data + query — lightweight)
QUERY_TEMPLATE = """### MAVLink Messages:
{mavlink_context}

### User Query:
"{query}"
"""

# Prepended to query prompt when parameters have been updated since last call
PARAM_UPDATE_TEMPLATE = """### Parameter Update:
The following drone parameters have been changed. Use these updated values going forward:
{delta_params}

"""

# Chat history file path
CHAT_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "chat_history.json")

# Conversation state
_conversation_history = []   # list of {"role": "user"|"assistant", "content": str}
_params_sent = False          # whether full params have been sent in current session
_last_seen_params = None      # last param dict seen (for delta computation)
MAX_HISTORY_TURNS = 5         # keep last 5 exchanges (10 messages) in context window

# Token & rate tracking
_request_timestamps = []     # list of timestamps for rate calculation
_total_input_tokens = 0
_total_output_tokens = 0
_total_requests = 0


# ── Semantic MAVLink context filtering ────────────────────────────────────────
# Maps keyword stems → MAVLink message types that are relevant to that topic.
# When the user's query matches a keyword, only those msg types are sent to the LLM,
# dramatically reducing token count for focused queries.
_MAVLINK_FILTER_MAP = {
    'battery':   ['SYS_STATUS', 'BATTERY_STATUS', 'POWER_STATUS'],
    'voltage':   ['SYS_STATUS', 'BATTERY_STATUS'],
    'current':   ['BATTERY_STATUS'],
    'gps':       ['GPS_RAW_INT', 'GPS_STATUS', 'GLOBAL_POSITION_INT', 'GPS2_RAW'],
    'satellite': ['GPS_RAW_INT', 'GPS_STATUS'],
    'position':  ['GLOBAL_POSITION_INT', 'GPS_RAW_INT', 'LOCAL_POSITION_NED'],
    'altitude':  ['GLOBAL_POSITION_INT', 'VFR_HUD', 'ALTITUDE'],
    'attitude':  ['ATTITUDE', 'AHRS', 'AHRS2', 'AHRS3'],
    'roll':      ['ATTITUDE', 'AHRS'],
    'pitch':     ['ATTITUDE', 'AHRS'],
    'yaw':       ['ATTITUDE', 'AHRS', 'VFR_HUD'],
    'heading':   ['VFR_HUD', 'GPS_RAW_INT'],
    'speed':     ['VFR_HUD', 'GLOBAL_POSITION_INT'],
    'airspeed':  ['VFR_HUD'],
    'compass':   ['RAW_IMU', 'SCALED_IMU2', 'SCALED_IMU3', 'MAG_CAL_PROGRESS', 'SYS_STATUS'],
    'magnetic':  ['RAW_IMU', 'SCALED_IMU2', 'SCALED_IMU3'],
    'motor':     ['SERVO_OUTPUT_RAW', 'ESC_TELEMETRY_1_TO_4', 'ESC_INFO'],
    'esc':       ['SERVO_OUTPUT_RAW', 'ESC_TELEMETRY_1_TO_4', 'ESC_INFO'],
    'servo':     ['SERVO_OUTPUT_RAW'],
    'rc':        ['RC_CHANNELS', 'RC_CHANNELS_RAW', 'RC_CHANNELS_SCALED'],
    'channel':   ['RC_CHANNELS', 'RC_CHANNELS_RAW'],
    'rssi':      ['RC_CHANNELS', 'RADIO_STATUS'],
    'signal':    ['RADIO_STATUS', 'RC_CHANNELS'],
    'link':      ['RADIO_STATUS', 'HEARTBEAT'],
    'arm':       ['HEARTBEAT', 'SYS_STATUS'],
    'disarm':    ['HEARTBEAT', 'SYS_STATUS'],
    'mode':      ['HEARTBEAT'],
    'flight':    ['HEARTBEAT', 'VFR_HUD', 'GLOBAL_POSITION_INT'],
    'health':    ['SYS_STATUS', 'HEARTBEAT', 'POWER_STATUS'],
    'sensor':    ['SYS_STATUS', 'RAW_IMU', 'SCALED_IMU2'],
    'imu':       ['RAW_IMU', 'SCALED_IMU2', 'SCALED_IMU3'],
    'accel':     ['RAW_IMU', 'SCALED_IMU2'],
    'gyro':      ['RAW_IMU', 'SCALED_IMU2'],
    'baro':      ['SCALED_PRESSURE', 'SCALED_PRESSURE2', 'RAW_PRESSURE'],
    'pressure':  ['SCALED_PRESSURE', 'SCALED_PRESSURE2'],
    'temperature': ['SCALED_PRESSURE', 'RAW_IMU', 'SYS_STATUS'],
    'vibration': ['VIBRATION'],
    'ekf':       ['EKF_STATUS_REPORT'],
    'ekf2':      ['EKF_STATUS_REPORT'],
    'land':      ['HEARTBEAT', 'GLOBAL_POSITION_INT', 'VFR_HUD'],
    'home':      ['HOME_POSITION', 'GLOBAL_POSITION_INT'],
    'mission':   ['MISSION_CURRENT', 'MISSION_COUNT', 'MISSION_ITEM_INT'],
    'waypoint':  ['MISSION_CURRENT', 'MISSION_ITEM_INT'],
    'fence':     ['FENCE_STATUS'],
    'geofence':  ['FENCE_STATUS'],
    'error':     ['SYS_STATUS', 'STATUSTEXT', 'HEARTBEAT'],
    'warn':      ['SYS_STATUS', 'STATUSTEXT'],
    'fail':      ['SYS_STATUS', 'STATUSTEXT'],
    'param':     ['PARAM_VALUE'],
    'pid':       ['PARAM_VALUE'],
    'tune':      ['PARAM_VALUE', 'ATTITUDE', 'VFR_HUD'],
    'stabil':    ['PARAM_VALUE', 'ATTITUDE'],
    'loiter':    ['PARAM_VALUE', 'GLOBAL_POSITION_INT'],
    'hover':     ['PARAM_VALUE', 'VFR_HUD'],
}

# Message types always included regardless of query (heartbeat, status text)
_DEFAULT_MAVLINK_TYPES = {'HEARTBEAT', 'SYS_STATUS', 'STATUSTEXT'}


def _filter_mavlink_ctx(query: str, ctx: dict) -> dict:
    """Return a filtered subset of ctx relevant to the query.

    If no keyword matches, returns the full ctx unchanged (defensive fallback).
    Always includes _DEFAULT_MAVLINK_TYPES if present in ctx.
    """
    query_lower = query.lower()
    wanted: set = set(_DEFAULT_MAVLINK_TYPES)

    for keyword, types in _MAVLINK_FILTER_MAP.items():
        if keyword in query_lower:
            wanted.update(types)

    if len(wanted) == len(_DEFAULT_MAVLINK_TYPES):
        # No keyword match — send everything (fallback for open-ended queries)
        return ctx

    return {k: v for k, v in ctx.items() if k in wanted}


# ── Tuning-assistant awareness ─────────────────────────────────────────────────
_TUNING_KEYWORDS = [
    'tune', 'tuning', 'pid', 'p gain', 'i gain', 'd gain', 'rate', 'stabilize',
    'oscillat', 'wobbl', 'overshoot', 'sluggish', 'twitchy', 'hover', 'loiter',
    'autotune', 'roll rate', 'pitch rate', 'yaw rate', 'acro', 'angle limit',
    'filter', 'notch', 'vibration', 'noise', 'harmonic',
]

_TUNING_CONTEXT = """### TUNING ASSISTANT MODE
You are also an expert ArduPilot/PX4 tuning assistant. When answering tuning queries:
1. Identify which PIDs or parameters are most relevant (e.g. ATC_RAT_RLL_P, ATC_RAT_PIT_P).
2. Suggest specific parameter names and conservative start values.
3. Explain the expected effect of each change in plain language.
4. Warn about safety: always disarm before changing params, test in a safe open area.
5. Recommend AutoTune if available and conditions allow.
Reference: ArduCopter attitude controller params use prefix ATC_, rate controller ATC_RAT_*.
PX4 uses MC_ROLL_P, MC_ROLLRATE_P, MC_ROLLRATE_I, MC_ROLLRATE_D, etc."""


def _is_tuning_query(query: str) -> bool:
    """Return True if the query appears to be about PID tuning or flight dynamics."""
    ql = query.lower()
    return any(kw in ql for kw in _TUNING_KEYWORDS)


def get_available_providers():
    """Return list of providers that have API keys configured."""
    providers = []
    if os.getenv("GEMINI_API_KEY"):
        providers.append("gemini")
    if os.getenv("OPENAI_API_KEY") and openai_module:
        providers.append("openai")
    if os.getenv("ANTHROPIC_API_KEY") and anthropic_module:
        providers.append("claude")
    return providers


def _call_gemini(prompt, system_instruction, history=None):
    """Call Gemini API. Returns (response_text, input_tokens, output_tokens)."""
    model = genai.GenerativeModel("gemini-2.5-flash", system_instruction=system_instruction)
    if history:
        gemini_history = [
            {"role": "model" if m["role"] == "assistant" else "user",
             "parts": [m["content"]]}
            for m in history
        ]
        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(prompt)
    else:
        response = model.generate_content(prompt)
    response_text = response.text.strip()

    input_tok = 0
    output_tok = 0
    if hasattr(response, 'usage_metadata') and response.usage_metadata:
        input_tok = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
        output_tok = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0

    return response_text, input_tok, output_tok


def _call_openai(prompt, system_instruction, history=None):
    """Call OpenAI API. Returns (response_text, input_tokens, output_tokens)."""
    if not openai_module:
        raise ImportError("openai package not installed. Run: pip install openai")
    client = openai_module.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    messages = [{"role": "system", "content": system_instruction}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    response = client.chat.completions.create(model="gpt-4o", messages=messages)
    response_text = response.choices[0].message.content.strip()
    input_tok = response.usage.prompt_tokens if response.usage else 0
    output_tok = response.usage.completion_tokens if response.usage else 0
    return response_text, input_tok, output_tok


def _call_claude(prompt, system_instruction, history=None):
    """Call Anthropic Claude API. Returns (response_text, input_tokens, output_tokens)."""
    if not anthropic_module:
        raise ImportError("anthropic package not installed. Run: pip install anthropic")
    client = anthropic_module.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    messages = []
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": prompt})
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        system=system_instruction,
        messages=messages,
    )
    response_text = response.content[0].text.strip()
    input_tok = response.usage.input_tokens if response.usage else 0
    output_tok = response.usage.output_tokens if response.usage else 0
    return response_text, input_tok, output_tok


def _dispatch(provider, prompt, system_instruction, history=None):
    """Route to the correct provider. Returns (response_text, input_tok, output_tok).

    Raises a dict with 'error' and 'quota_exhausted' on rate/quota errors.
    """
    try:
        if provider == "openai":
            return _call_openai(prompt, system_instruction, history)
        elif provider == "claude":
            return _call_claude(prompt, system_instruction, history)
        else:
            return _call_gemini(prompt, system_instruction, history)
    except Exception as e:
        # Check for quota / rate-limit errors from each provider
        err_type = type(e).__name__
        err_module = type(e).__module__ or ""
        is_quota = False

        # Gemini: google.api_core.exceptions.ResourceExhausted
        if "ResourceExhausted" in err_type:
            is_quota = True
        # OpenAI: openai.RateLimitError
        elif openai_module and isinstance(e, getattr(openai_module, 'RateLimitError', type(None))):
            is_quota = True
        # Anthropic: anthropic.RateLimitError
        elif anthropic_module and isinstance(e, getattr(anthropic_module, 'RateLimitError', type(None))):
            is_quota = True

        if is_quota:
            agent_logger.warning(f"Quota/rate limit hit for provider '{provider}': {e}")
            raise QuotaExhaustedError(str(e), provider)

        raise


class QuotaExhaustedError(Exception):
    """Raised when a provider's quota or rate limit is hit."""
    def __init__(self, message, provider):
        super().__init__(message)
        self.provider = provider


def _compute_param_delta(old_params, new_params):
    """Compute changed/added/removed parameters between two param dicts."""
    if not old_params:
        return None  # first call — no delta, full list goes in system_instruction
    if not new_params:
        return None

    delta = {}
    # Changed or added params
    for key, val in new_params.items():
        if key not in old_params or old_params[key] != val:
            delta[key] = {"old": old_params.get(key, "<new>"), "new": val}
    # Removed params
    for key in old_params:
        if key not in new_params:
            delta[key] = {"old": old_params[key], "new": "<removed>"}

    return delta if delta else None


def _load_chat_history():
    """Load chat history from JSON file."""
    if os.path.exists(CHAT_HISTORY_FILE):
        try:
            with open(CHAT_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


def _save_chat_history(history):
    """Save chat history to JSON file."""
    try:
        with open(CHAT_HISTORY_FILE, 'w') as f:
            json.dump(history, f, indent=2)
    except IOError as e:
        agent_logger.error(f"Failed to save chat history: {e}")


def _append_to_history(query, response, full_prompt=None, raw_response=None,
                        provider=None, tokens_in=0, tokens_out=0):
    """Append a full interaction record to chat history."""
    history = _load_chat_history()
    record = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "provider": provider,
        "query": query,
        "full_prompt": full_prompt,
        "raw_llm_response": raw_response,
        "parsed_response": response,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    history.append(record)
    _save_chat_history(history)


def reset_session():
    """Reset conversation state — call when drone disconnects or new session starts."""
    global _conversation_history, _params_sent, _last_seen_params
    _conversation_history = []
    _params_sent = False
    _last_seen_params = None
    agent_logger.info("JARVIS session reset")
    print(">>> JARVIS: session reset — full params will be sent on next query")


def _trim_history():
    """Trim history to MAX_HISTORY_TURNS. If the initial params message is dropped, mark for resend."""
    global _conversation_history, _params_sent
    max_messages = MAX_HISTORY_TURNS * 2
    if len(_conversation_history) > max_messages:
        dropped = _conversation_history[:-max_messages]
        _conversation_history = _conversation_history[-max_messages:]
        # If the initial full-params message was trimmed out, re-send on next call
        for msg in dropped:
            if msg["role"] == "user" and "### Drone Parameters:" in msg["content"]:
                _params_sent = False
                agent_logger.info("JARVIS: initial params dropped from history window — will resend on next query")
                break


def ask_jarvis(query, parameter_context=None, mavlink_ctx=None, provider="gemini"):
    """Process the user query using the selected AI provider with MAVLink context.

    Args:
        query: User query string.
        parameter_context: Categorized params dict (for delta tracking).
        mavlink_ctx: dict keyed by msg type → latest msg dict (from snapshot).
                     Falls back to global jarvis_mav_data if not provided.
        provider: AI provider to use — "gemini", "openai", or "claude".
    """
    global _last_seen_params, _params_sent, _conversation_history

    ctx_data = mavlink_ctx if mavlink_ctx is not None else jarvis_mav_data
    filtered_ctx = _filter_mavlink_ctx(query, ctx_data)
    mavlink_context = json.dumps(list(filtered_ctx.values()))  # compact — no indent
    if len(filtered_ctx) < len(ctx_data):
        agent_logger.info(f"JARVIS: MAVLink ctx filtered {len(ctx_data)}→{len(filtered_ctx)} msg types for query")

    # Build param section: full on first call, delta only on subsequent calls
    param_section = ""
    if not _params_sent and parameter_context:
        # First call — send full params once, LLM remembers via history after this
        param_section = f"### Drone Parameters:\n{json.dumps(parameter_context)}\n\n"
        _params_sent = True
        _last_seen_params = dict(parameter_context)
        agent_logger.info(f"JARVIS: sending full params ({len(parameter_context)} categories) on first call")
        print(f">>> JARVIS: first call — sending full params ({len(parameter_context)} categories)")
    elif parameter_context and _last_seen_params is not None:
        delta = _compute_param_delta(_last_seen_params, parameter_context)
        if delta:
            param_section = PARAM_UPDATE_TEMPLATE.format(delta_params=json.dumps(delta))
            _last_seen_params = dict(parameter_context)
            agent_logger.info(f"JARVIS: {len(delta)} param(s) changed, sending delta")
            print(f">>> JARVIS: {len(delta)} parameter(s) changed, sending delta")

    prompt = param_section + QUERY_TEMPLATE.format(
        mavlink_context=mavlink_context, query=query
    )

    # Snapshot history window before appending current turn
    history_window = list(_conversation_history)

    try:
        global _total_input_tokens, _total_output_tokens, _total_requests, _request_timestamps

        print(f">>> JARVIS [{provider}] prompt: {len(prompt)} chars | history: {len(history_window)//2} turns")
        agent_logger.info(f"Sending query to {provider} API (history={len(history_window)//2} turns)")

        system_instruction = SYSTEM_INSTRUCTION
        if _is_tuning_query(query):
            system_instruction += "\n\n" + _TUNING_CONTEXT
            agent_logger.info("JARVIS: tuning query detected — injecting tuning assistant context")

        response_text, input_tok, output_tok = _dispatch(
            provider, prompt, system_instruction, history_window or None
        )

        # Track tokens
        _total_requests += 1
        now = time.time()
        _request_timestamps.append(now)
        _request_timestamps = [t for t in _request_timestamps if now - t <= 60]
        rpm = len(_request_timestamps)
        _total_input_tokens += input_tok
        _total_output_tokens += output_tok

        agent_logger.info(
            f"[{provider}] Tokens: in={input_tok} out={output_tok} | "
            f"Totals: in={_total_input_tokens} out={_total_output_tokens} | "
            f"Requests: {_total_requests} ({rpm}/min)"
        )
        print(
            f">>> JARVIS [{provider}] tokens: in={input_tok} out={output_tok} | "
            f"totals: in={_total_input_tokens} out={_total_output_tokens} | "
            f"req={_total_requests} ({rpm}/min)"
        )

        # Update conversation history
        _conversation_history.append({"role": "user", "content": prompt})
        _conversation_history.append({"role": "assistant", "content": response_text})
        _trim_history()

        # Extract JSON
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        json_data = response_text[json_start:json_end]

        result = json.loads(json_data)
        agent_logger.info(f"JARVIS response intent: {result.get('intent', 'unknown')}")

        _append_to_history(query, result,
                           full_prompt=prompt,
                           raw_response=response_text,
                           provider=provider,
                           tokens_in=input_tok,
                           tokens_out=output_tok)

        return result

    except QuotaExhaustedError as e:
        agent_logger.warning(f"Quota exhausted for {e.provider}: {e}")
        return {"error": f"Token quota exhausted for {e.provider}. Please switch to another model.", "quota_exhausted": True}

    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON response from AI: {str(e)}"
        agent_logger.error(error_msg)
        return {"error": error_msg, "raw_response": response_text}

    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


# Backwards-compatible alias
def ask_gemini(query, parameter_context=None, mavlink_ctx=None):
    return ask_jarvis(query, parameter_context, mavlink_ctx, provider="gemini")


# ============================================================================
# Log Analysis
# ============================================================================

LOG_ANALYSIS_SYSTEM_PROMPT = """You are an expert ArduPilot flight log analyst.
You analyze .bin (dataflash) and .tlog (telemetry) log files to help pilots understand their flights.

You know all ArduPilot log message types and their fields:
- ATT: Attitude (Roll, Pitch, Yaw, DesRoll, DesPitch, DesYaw)
- GPS: GPS data (Lat, Lng, Alt, Spd, NSats, HDop)
- CTUN: Control tuning (ThI, ThO, DAlt, Alt, BAlt, DSAlt, SAlt)
- VIBE: Vibration levels (VibeX, VibeY, VibeZ, Clip0, Clip1, Clip2)
- MOT: Motor outputs (Mot1-Mot4 or more)
- BAT/CURR: Battery (Volt, Curr, CurrTot, EnrgTot)
- BARO: Barometer (Alt, Press, Temp)
- GYR: Gyroscope (GyrX, GyrY, GyrZ)
- ACC: Accelerometer (AccX, AccY, AccZ)
- MAG: Magnetometer (MagX, MagY, MagZ)
- MODE: Flight mode changes (Mode, ModeNum, Rsn)
- MSG: Text messages from autopilot
- ERR: Error events (Subsys, ECode)
- RCIN: RC input channels
- RCOU: RC output (servo/motor PWM)
- PARM: Parameter values
- NKF1/NKF2: EKF state estimates
- IMU: IMU data (GyrX-Z, AccX-Z)
- POWR: Power board voltage/flags
- EV: Events
- PM: Performance monitoring

### Response Format
You MUST respond in strict JSON format:
{
    "analysis": "Your analysis in markdown format. Use headers, bullet points, and bold for clarity.",
    "charts": [
        {
            "title": "Chart Title",
            "type": "line",
            "msg_type": "ATT",
            "x_field": "TimeUS",
            "y_fields": ["Roll", "Pitch"],
            "y_label": "Degrees"
        }
    ],
    "need_data": ["MSG_TYPE1", "MSG_TYPE2"]
}

### Rules:
- "analysis" is always required with markdown-formatted analysis text
- "charts" is a list of chart configs (can be empty [])
- Chart "type" can be: "line", "bar", "scatter"
- "need_data" is a list of message types you need to see actual data for (use ONLY on first call when you only have the summary)
- If you already have the data you need, set "need_data" to []
- When suggesting charts, use msg_type and field names that exist in the log summary
- Keep analysis concise but informative — focus on anomalies, safety issues, and actionable insights
- For vibration analysis: VibeX/Y/Z > 30 m/s/s is concerning, > 60 is problematic. Clip counts > 0 indicate clipping.
- For battery: voltage sag under load, capacity consumed, estimated flight time
- For attitude: compare desired vs actual (DesRoll vs Roll) — large errors indicate tuning issues
"""

def ask_gemini_log_analysis(query, log_summary, message_data=None, provider="gemini"):
    """Analyze a flight log using the selected AI provider.

    Args:
        query: User's analysis question.
        log_summary: Dict from LogParser.get_summary().
        message_data: Optional dict of {msg_type: [list of dicts]} with actual data.
        provider: AI provider to use — "gemini", "openai", or "claude".

    Returns:
        Dict with 'analysis', 'charts', and 'need_data' keys.
    """
    global _total_input_tokens, _total_output_tokens, _total_requests, _request_timestamps

    # Build prompt
    prompt_parts = [f'### Log Summary:\n```json\n{json.dumps(log_summary, indent=1, default=str)}\n```\n']

    if message_data:
        prompt_parts.append("### Message Data:\n")
        for msg_type, data in message_data.items():
            prompt_parts.append(f"**{msg_type}** ({len(data)} points):\n```json\n{json.dumps(data[:5], indent=1, default=str)}\n... ({len(data)} total)\n```\n")
            if len(data) > 10:
                fields = [k for k in data[0].keys() if isinstance(data[0].get(k), (int, float))]
                if fields:
                    stats = {}
                    for f in fields[:6]:
                        vals = [d[f] for d in data if isinstance(d.get(f), (int, float))]
                        if vals:
                            stats[f] = {"min": round(min(vals), 2), "max": round(max(vals), 2),
                                        "avg": round(sum(vals)/len(vals), 2)}
                    prompt_parts.append(f"Stats: {json.dumps(stats, default=str)}\n")

    prompt_parts.append(f'\n### User Query:\n"{query}"')
    prompt = "\n".join(prompt_parts)

    try:
        agent_logger.info(f"Log analysis query [{provider}]: {query}")
        print(f">>> JARVIS [{provider}] log analysis query: \"{query}\" (prompt {len(prompt)} chars)")

        response_text, input_tok, output_tok = _dispatch(provider, prompt, LOG_ANALYSIS_SYSTEM_PROMPT)

        # Track tokens
        _total_requests += 1
        now = time.time()
        _request_timestamps.append(now)
        _request_timestamps = [t for t in _request_timestamps if now - t <= 60]
        _total_input_tokens += input_tok
        _total_output_tokens += output_tok

        print(f"<<< JARVIS [{provider}] log analysis response: in={input_tok} out={output_tok}")

        # Extract JSON
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            return {"analysis": response_text, "charts": [], "need_data": []}

        result = json.loads(response_text[json_start:json_end])

        result.setdefault("analysis", "No analysis provided.")
        result.setdefault("charts", [])
        result.setdefault("need_data", [])

        return result

    except QuotaExhaustedError as e:
        agent_logger.warning(f"Quota exhausted for {e.provider} during log analysis: {e}")
        return {"analysis": f"Token quota exhausted for {e.provider}. Please switch to another model.", "charts": [], "need_data": [], "quota_exhausted": True}
    except json.JSONDecodeError as e:
        agent_logger.error(f"Log analysis JSON error: {e}")
        return {"analysis": response_text, "charts": [], "need_data": []}
    except Exception as e:
        agent_logger.error(f"Log analysis error: {e}")
        return {"analysis": f"Error analyzing log: {str(e)}", "charts": [], "need_data": []}
