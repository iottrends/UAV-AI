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
gemini_model = genai.GenerativeModel("gemini-1.5-pro")

# Define prompt template correctly
PROMPT_TEMPLATE = """You are a MAVLink drone assistant.
Analyze the MAVLink messages and the user query to determine intent.

### Intent Categories:
1️⃣ **Status Queries:**  
   - "What is my battery level?" → Reads MAVLink telemetry.  
   - "Is the GPS locked?" → Checks GPS fix type.  
2️⃣ **Diagnostic Queries:**  
   - "Why is the motor not spinning?" → Checks ESC status.  
   - "Why is the compass not working?" → Checks sensor health & calibration.  

### Instructions:
- If it is a **status query**, extract and parse the MAVLink data.
- If it is a **diagnostic query**, find possible issues and suggest a fix.
- If a fix is required, provide the **MAVLink command** needed.

### MAVLink Messages:
{mavlink_context}

### User Query:
"{query}"

### Expected JSON Response:
Respond in **strict JSON format only**, without extra text.
{{
    "intent": "status" or "diagnostic",
    "message": "your response here",
    "fix_command": "optional MAVLink command or null"
}}
"""


def ask_gemini(query):
    """Process the user query using Gemini AI with MAVLink context."""

    mavlink_context = json.dumps(list(jarvis_mav_data), indent=2)

    # Fill prompt template
    prompt = PROMPT_TEMPLATE.format(mavlink_context=mavlink_context, query=query)
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