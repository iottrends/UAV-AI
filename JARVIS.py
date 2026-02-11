import os
import logging
import json
import time
from dotenv import load_dotenv
import google.generativeai as genai

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

# Model state
_model = None
_cached_params = None       # full param dict from initial model creation
_last_seen_params = None     # last param dict seen (for delta computation)

# Token & rate tracking
_request_timestamps = []     # list of timestamps for rate calculation
_total_input_tokens = 0
_total_output_tokens = 0
_total_requests = 0


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


def _append_to_history(query, response):
    """Append a query-response pair to chat history."""
    history = _load_chat_history()
    history.append({
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "query": query,
        "response": response
    })
    _save_chat_history(history)


def _ensure_model(parameter_context):
    """Create model on first call with full params in system_instruction."""
    global _model, _cached_params, _last_seen_params

    if _model is None:
        # First call — build system_instruction with full param list
        sys_instruction = SYSTEM_INSTRUCTION
        if parameter_context:
            sys_instruction += f"\n\n### Available Parameters:\n{json.dumps(parameter_context, indent=2)}"

        _model = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=sys_instruction
        )
        _cached_params = dict(parameter_context) if parameter_context else {}
        _last_seen_params = dict(parameter_context) if parameter_context else {}
        agent_logger.info("Created JARVIS model with initial params")
        print(f">>> JARVIS: Model created with {len(_cached_params)} parameters in system_instruction")

    return _model


def ask_gemini(query, parameter_context=None, mavlink_ctx=None):
    """Process the user query using Gemini AI with MAVLink context.

    Args:
        query: User query string.
        parameter_context: Categorized params dict (for delta tracking).
        mavlink_ctx: dict keyed by msg type → latest msg dict (from snapshot).
                     Falls back to global jarvis_mav_data if not provided.
    """
    global _last_seen_params

    ctx_data = mavlink_ctx if mavlink_ctx is not None else jarvis_mav_data
    mavlink_context = json.dumps(list(ctx_data.values()), indent=2)

    # Ensure model exists (created once with full params in system_instruction)
    model = _ensure_model(parameter_context)

    # Check for param changes since last query
    param_delta_text = ""
    if parameter_context and _last_seen_params is not None:
        delta = _compute_param_delta(_last_seen_params, parameter_context)
        if delta:
            param_delta_text = PARAM_UPDATE_TEMPLATE.format(
                delta_params=json.dumps(delta, indent=2)
            )
            agent_logger.info(f"Parameter delta detected: {len(delta)} params changed")
            print(f">>> JARVIS: {len(delta)} parameters updated, sending delta with query")
            _last_seen_params = dict(parameter_context)

    # Build prompt: [param delta if any] + MAVLink data + query
    prompt = param_delta_text + QUERY_TEMPLATE.format(
        mavlink_context=mavlink_context, query=query
    )

    try:
        global _total_input_tokens, _total_output_tokens, _total_requests, _request_timestamps

        # Log breakdown of what we're sending
        sys_instr_len = len(model._system_instruction.parts[0].text) if model._system_instruction else 0
        mavlink_ctx_len = len(mavlink_context)
        param_delta_len = len(param_delta_text)
        prompt_len = len(prompt)
        print(f">>> JARVIS prompt breakdown: sys_instruction={sys_instr_len} chars, "
              f"mavlink_ctx={mavlink_ctx_len} chars, param_delta={param_delta_len} chars, "
              f"prompt_total={prompt_len} chars")
        agent_logger.info("Sending query to Gemini API")

        # Stateless call — system_instruction (cached) + this prompt
        response = model.generate_content(prompt)
        response_text = response.text.strip()

        # Track tokens and request rate
        _total_requests += 1
        now = time.time()
        _request_timestamps.append(now)
        # Keep only last 60 seconds of timestamps
        _request_timestamps = [t for t in _request_timestamps if now - t <= 60]
        rpm = len(_request_timestamps)

        input_tok = 0
        output_tok = 0
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            input_tok = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
            output_tok = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
            _total_input_tokens += input_tok
            _total_output_tokens += output_tok

        agent_logger.info(
            f"Tokens: in={input_tok} out={output_tok} | "
            f"Totals: in={_total_input_tokens} out={_total_output_tokens} | "
            f"Requests: {_total_requests} ({rpm}/min)"
        )
        print(
            f">>> JARVIS tokens: in={input_tok} out={output_tok} | "
            f"totals: in={_total_input_tokens} out={_total_output_tokens} | "
            f"req={_total_requests} ({rpm}/min)"
        )

        # Extract JSON part only (ignores extra AI text)
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        json_data = response_text[json_start:json_end]

        result = json.loads(json_data)
        agent_logger.info(f"JARVIS response intent: {result.get('intent', 'unknown')}")

        # Save to chat history file
        _append_to_history(query, result)

        return result

    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON response from AI: {str(e)}"
        agent_logger.error(error_msg)
        return {"error": error_msg, "raw_response": response_text}

    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}


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

_log_model = None


def _ensure_log_model():
    """Create the log analysis model on first use."""
    global _log_model
    if _log_model is None:
        _log_model = genai.GenerativeModel(
            "gemini-2.5-flash",
            system_instruction=LOG_ANALYSIS_SYSTEM_PROMPT
        )
        agent_logger.info("Created JARVIS log analysis model")
    return _log_model


def ask_gemini_log_analysis(query, log_summary, message_data=None):
    """Analyze a flight log using Gemini AI.

    Args:
        query: User's analysis question.
        log_summary: Dict from LogParser.get_summary().
        message_data: Optional dict of {msg_type: [list of dicts]} with actual data.

    Returns:
        Dict with 'analysis', 'charts', and 'need_data' keys.
    """
    global _total_input_tokens, _total_output_tokens, _total_requests, _request_timestamps

    model = _ensure_log_model()

    # Build prompt
    prompt_parts = [f'### Log Summary:\n```json\n{json.dumps(log_summary, indent=1, default=str)}\n```\n']

    if message_data:
        prompt_parts.append("### Message Data:\n")
        for msg_type, data in message_data.items():
            prompt_parts.append(f"**{msg_type}** ({len(data)} points):\n```json\n{json.dumps(data[:5], indent=1, default=str)}\n... ({len(data)} total)\n```\n")
            # Include statistical summary for large datasets
            if len(data) > 10:
                fields = [k for k in data[0].keys() if isinstance(data[0].get(k), (int, float))]
                if fields:
                    stats = {}
                    for f in fields[:6]:  # limit to 6 fields for brevity
                        vals = [d[f] for d in data if isinstance(d.get(f), (int, float))]
                        if vals:
                            stats[f] = {"min": round(min(vals), 2), "max": round(max(vals), 2),
                                        "avg": round(sum(vals)/len(vals), 2)}
                    prompt_parts.append(f"Stats: {json.dumps(stats, default=str)}\n")

    prompt_parts.append(f'\n### User Query:\n"{query}"')
    prompt = "\n".join(prompt_parts)

    try:
        agent_logger.info(f"Log analysis query: {query}")
        print(f">>> JARVIS log analysis query: \"{query}\" (prompt {len(prompt)} chars)")

        response = model.generate_content(prompt)
        response_text = response.text.strip()

        # Track tokens
        _total_requests += 1
        now = time.time()
        _request_timestamps.append(now)
        _request_timestamps = [t for t in _request_timestamps if now - t <= 60]

        input_tok = 0
        output_tok = 0
        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            input_tok = getattr(response.usage_metadata, 'prompt_token_count', 0) or 0
            output_tok = getattr(response.usage_metadata, 'candidates_token_count', 0) or 0
            _total_input_tokens += input_tok
            _total_output_tokens += output_tok

        print(f"<<< JARVIS log analysis response: in={input_tok} out={output_tok}")

        # Extract JSON
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        if json_start == -1 or json_end == 0:
            return {"analysis": response_text, "charts": [], "need_data": []}

        result = json.loads(response_text[json_start:json_end])

        # Ensure required keys exist
        result.setdefault("analysis", "No analysis provided.")
        result.setdefault("charts", [])
        result.setdefault("need_data", [])

        return result

    except json.JSONDecodeError as e:
        agent_logger.error(f"Log analysis JSON error: {e}")
        return {"analysis": response_text, "charts": [], "need_data": []}
    except Exception as e:
        agent_logger.error(f"Log analysis error: {e}")
        return {"analysis": f"Error analyzing log: {str(e)}", "charts": [], "need_data": []}
