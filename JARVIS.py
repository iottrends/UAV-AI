import os
import logging
from dotenv import load_dotenv
import google.generativeai as genai
import json
#from main import jarvis_mav_data  # Import MAVLink message buffer
from collections import deque

# Get the agent logger
agent_logger = logging.getLogger('agent')

jarvis_mav_data = deque(maxlen=10) # pack of last 10 mavlink messages.

# Load environment variables from .env file
load_dotenv()

# Get API key from environment variable
api_key = os.getenv("GEMINI_API_KEY")

# Configure the API with the key from env file
genai.configure(api_key=api_key)
agent_logger.info("Initializing Gemini model")

# Set up Gemini API
#genai.configure(api_key="AIzaSyCSmT6jaHLH60SdoPQmMPMZXxMbqKBftG4")

# Initialize the model once
#gemini_model = genai.GenerativeModel("gemini-1.5-pro")
gemini_model = genai.GenerativeModel("gemini-2.0-flash")

# Define prompt template correctly
PROMPT_TEMPLATE = """You are a MAVLink drone assistant.
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
   - "Take off" -> Generates `fix_command` for taking off.
   - "Land" -> Generates `fix_command` for landing.

### Instructions:
- If it is a **status query**, extract and parse the MAVLink data.
- If it is a **diagnostic query**, find possible issues and suggest a fix.
- If it is a **tuning query**, respond first with relevant parameters and their recommended values.
- If it is an **action command**, generate the appropriate MAVLink command in the `fix_command` field.
- Use the available **parameter list** for referencing correct parameters and values.
- **Ask clarifying questions ONLY if essential information is missing** from the user query.
- If a fix is needed, suggest the correct **MAVLink command** or **parameter update**.

### MAVLink Messages:
{mavlink_context}

### Available Parameters:
{parameter_context}

### User Query:
"{query}"

### Expected JSON Response:
Respond in **strict JSON format only**, without extra text.
{{
    "intent": "status" or "diagnostic" or "tuning" or "action",
    "message": "your response here",
    "fix_command": "MAVLink command JSON object or null",
    "recommended_param": "list of recommended parameters and values, or null",
    "clarification_needed": "your clarification question if needed, or null"
}}

### MAVLink Command JSON Object Format Examples for `fix_command`:
For MAV_CMD_COMPONENT_ARM_DISARM:
{{ "command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 1, "param2": 21196 }} // Arm
{{ "command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 0, "param2": 29892 }} // Disarm

For MAV_CMD_DO_MOTOR_TEST (e.g., spin motor 1 at 50% for 5 seconds):
{{ "command": "MAV_CMD_DO_MOTOR_TEST", "param1": 1, "param2": 1, "param3": 500, "param4": 5, "param5": 0, "param6": 0 }} // Motor 1, Thrust, 50% (500), 5 seconds
// param1: instance (motor ID, 1-based), param2: throttle type (1=Thrust), param3: throttle (0-1000 for 0-100%), param4: timeout (seconds)

For MAV_CMD_NAV_TAKEOFF:
{{ "command": "MAV_CMD_NAV_TAKEOFF", "param1": 0, "param2": 0, "param3": 0, "param4": 0, "param5": 0, "param6": 0, "param7": 2.0 }} // Take off to 2 meters altitude

For MAV_CMD_NAV_LAND:
{{ "command": "MAV_CMD_NAV_LAND", "param1": 0, "param2": 0, "param3": 0, "param4": 0, "param5": 0, "param6": 0, "param7": 0 }} // Land at current position
"""

def ask_gemini(query, parameter_context=None):
    """Process the user query using Gemini AI with MAVLink context."""

    mavlink_context = json.dumps(list(jarvis_mav_data), indent=2)

    # Fill prompt template
    #prompt = PROMPT_TEMPLATE.format(mavlink_context=mavlink_context, query=query)
    prompt = PROMPT_TEMPLATE.format(mavlink_context=mavlink_context, parameter_context=json.dumps(parameter_context, indent=2), query=query)
    print("Prompt:", prompt)  # Debugging output
    
    #print("mavlink_context:", mavlink_context)
    #print("jarvis_mav_data", jarvis_mav_data)
    try:
        agent_logger.info("Sending query to Gemini API")
        response_text = gemini_model.generate_content(prompt).text.strip()
        #print("Raw AI Response:", response_text)  # Debugging output

        # Extract JSON part only (ignores extra AI text)
        json_start = response_text.find("{")
        json_end = response_text.rfind("}") + 1
        json_data = response_text[json_start:json_end]

        result = json.loads(json_data) #parse json safely before returning
        agent_logger.info(f"JARVIS response intent: {result.get('intent', 'unknown')}")
        #return json.loads(json_data)
        return result

    except json.JSONDecodeError as e:
        error_msg = f"Invalid JSON response from AI: {str(e)}"
        agent_logger.error(error_msg)
        #return {"error": "Invalid JSON response from AI.", "raw_response": response_text}
        return {"error":error_msg, "raw_response":response_text}

    except Exception as e:
        return {"error": f"Unexpected error: {str(e)}"}