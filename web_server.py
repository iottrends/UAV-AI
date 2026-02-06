import os
import json
import logging
import threading
import time
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from collections import deque

# Default logger that will be replaced if loggers are provided
logger = logging.getLogger('web_server')
stt_logger = logging.getLogger('stt_module')

# Create Flask app and SocketIO instance
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'uav-ai-assistant-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global variables to store references to backend components
validator = None  # Will hold the DroneValidator instance
jarvis_module = None  # Will hold the JARVIS module
llm_ai_module = None  # Will hold the llm_ai_v5 module
stt_module = None # Will hold the STT recorder instance
telemetry_thread = None  # Will hold the telemetry update thread
connected_clients = set()  # Track connected WebSocket clients
mavlink_buffer = deque(maxlen=10)  # Local buffer of recent MAVLink messages

# Connection parameters storage
connection_params = {
    "port": None,
    "baud": None,
    "connect_requested": False,
    "connect_success": False
}

# Global thread references
telemetry_thread = None
server_thread = None

# Status tracking variables
last_system_health = {
    "score": 0,
    "critical_issues": 0,
    "readiness": "UNKNOWN",
    "battery": {
        "voltage": 0,
        "threshold": 0,
        "status": "UNKNOWN"
    },
    "gps": {
        "fix_type": 0,
        "satellites": 0,
        "status": "UNKNOWN"
    },
    "motors": [
        {"id": 1, "output": 1000, "status": "OK"},
        {"id": 2, "output": 1000, "status": "OK"},
        {"id": 3, "output": 1000, "status": "OK"},
        {"id": 4, "output": 1000, "status": "OK"}
    ],
    "subsystems": [],
    "params": {
        "percentage": 0,
        "downloaded": 0,
        "total": 0
    },
    "latency": 0
}


@app.route('/')
def index():
    """Serve the main HTML page"""
    return send_from_directory('static', 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """Serve static files"""
    return send_from_directory('static', path)


@app.route('/api/connect', methods=['POST'])
def connect_drone():
    """API endpoint to connect to a drone"""
    global connection_params
    
    print("\n=== Connection Request Received ===")
    print(f"Headers: {request.headers}")
    print(f"Data: {request.data}")

    if not validator:
        print("Error: Validator not initialized")
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        # Parse the JSON data from the request
        data = request.get_json(force=True)  # force=True helps with content-type issues
        conn_type = data.get('type', 'serial')

        if conn_type == 'udp':
            ip = data.get('ip')
            udp_port = data.get('port')
            port = f"udpin:{ip}:{udp_port}"
            baud = 115200  # unused for UDP but required by connect() signature
            print(f"Attempting UDP connection on {port}")
        else:
            port = data.get('port')
            baud = int(data.get('baud'))
            print(f"Attempting connection to {port} at {baud} baud")
        
        # Check if already connected and disconnect first if needed
        if hasattr(validator, 'is_connected') and validator.is_connected:
            logger.info(f"Already connected, disconnecting first")
            validator.disconnect()
        
        # Attempt connection with retries
        MAX_RETRIES = 3
        RETRY_DELAY_SECONDS = 2
        for attempt in range(MAX_RETRIES):
            logger.info(f"Attempting connection to {port} at {baud} baud (Attempt {attempt + 1}/{MAX_RETRIES})")
            if validator.connect(port, baud):
                # Store connection parameters and set flags
                connection_params["port"] = port
                connection_params["baud"] = baud
                connection_params["connect_requested"] = True
                connection_params["connect_success"] = True
                #paass the socketio instance to the validator
                validator.update_socketio(socketio)
                print("passed socketio instance to validator!!!!")
                
                logger.info(f"Connection successful to {port} at {baud} baud")
                return jsonify({
                    "status": "success",
                    "message": f"Connected to {port} at {baud} baud"
                })
            else:
                logger.warning(f"Connection failed on attempt {attempt + 1}. Retrying in {RETRY_DELAY_SECONDS} seconds...")
                time.sleep(RETRY_DELAY_SECONDS)

        # If all retries fail
        logger.error(f"Failed to connect to drone on {port} after {MAX_RETRIES} attempts.")
        connection_params["connect_success"] = False
        return jsonify({"status": "error", "message": f"Failed to connect to drone on {port} after {MAX_RETRIES} attempts."}), 400

    except Exception as e:
        logger.error(f"Connection error: {str(e)}")
        connection_params["connect_success"] = False
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/disconnect', methods=['POST'])
def disconnect_drone():
    """API endpoint to disconnect from a drone"""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        validator.disconnect()
        return jsonify({"status": "success", "message": "Disconnected from drone"})
    except Exception as e:
        logger.error(f"Disconnection error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/test')
def test():
    return "Web server is working!"

############################################################################
@app.route('/api/fc_logs', methods=['GET'])
def get_fc_logs():
    """API endpoint to fetch logs from the flight controller"""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        if not validator.hardware_validated:
            return jsonify({"status": "error", "message": "Drone not connected or not validated"}), 400

         # Placeholder for actual logs. This should be replaced with a call to the validator's log fetching method.
        flight_controller_logs = """Mock log content from flight controller: This is a test log entry.
        Another log line with some important
        information.
Log entry with a timestamp: [2026-02-06 10:30:00] System ready."""

        if flight_controller_logs:
            return jsonify({"status": "success", "logs": flight_controller_logs})
        else:
            return jsonify({"status": "success", "logs": "No logs available from the flight controller."})
    except Exception as e:
        logger.error(f"Error fetching logs: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

#############################################################################
@app.route('/api/parameters', methods=['GET', 'POST'])
def handle_parameters():
    """API endpoint to get or update drone parameters"""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        if request.method == 'GET':
            # Return the categorized parameters
            return jsonify(validator.categorized_params)
        elif request.method == 'POST':
            # Update parameters
            data = request.json
            if not data:
                return jsonify({"status": "error", "message": "No parameters provided"}), 400
            
            # Log the parameters being updated
            logger.info(f"Updating parameters: {data}")
            
            try:
                # Send MAVLink messages to update parameters on the flight controller
                for param_name, value in data.items():
                    print(f"Updating parameter {param_name} to {value}")
                    if not validator.update_parameter(param_name, value):
                        raise ValueError(f"Failed to update parameter {param_name}")
                
                print("Wait for and verify parameter updates")
                start_time = time.time()
                timeout = 2  # timeout to 2 seconds
                verified_params = {}
                last_mismatch = None
                
                while time.time() - start_time < timeout:
                    # Check if all parameters have been updated in params_dict
                    verified_params = {
                        param: validator.params_dict.get(param)
                        for param in data.keys()
                    }
                    
                    # Compare values with tolerance for floating point numbers
                    mismatches = [
                        param for param, value in data.items()
                        if not (
                            verified_params.get(param) is not None and
                            abs(float(verified_params[param]) - float(value)) < 0.0001
                        )
                    ]
                    
                    if not mismatches:
                        return jsonify({
                            "status": "success",
                            "message": "Parameters updated successfully",
                            "updated": list(data.keys())
                        })
                    
                    # Track last mismatch for better error reporting
                    if mismatches != last_mismatch:
                        logger.info(f"Waiting for parameters to update: {mismatches}")
                        last_mismatch = mismatches
                    
                    time.sleep(0.1)
                
                # If we get here, timeout occurred
                failed_updates_details = []
                for param in mismatches:
                    expected = data[param]
                    actual = verified_params.get(param, "Not Received")
                    failed_updates_details.append(f"{param}: Expected {expected}, Actual {actual}")
                
                raise TimeoutError(
                    f"Timeout waiting for parameter updates. "
                    f"The following parameters did not update as expected: {'; '.join(failed_updates_details)}. "
                    f"Last verified values for all parameters: {verified_params}"
                )
            except Exception as e:
                logger.error(f"Error updating parameters: {str(e)}")
                return jsonify({
                    "status": "error",
                    "message": f"Failed to update parameters: {str(e)}"
                }), 500
    except Exception as e:
        logger.error(f"Error handling parameters: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/firmware', methods=['GET'])
def get_firmware_info():
    """API endpoint to get firmware information"""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        if validator.firmware_data:
            return jsonify({"status": "success", "firmware": validator.firmware_data})
        else:
            return jsonify({"status": "error", "message": "Firmware information not available"}), 404
    except Exception as e:
        logger.error(f"Error getting firmware info: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

####################################################################################
@app.route('/api/query', methods=['POST'])
def process_query():
    """API endpoint to process a query through both AI systems"""
    if not validator or not jarvis_module or not llm_ai_module:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    data = request.json
    query = data.get('query', '')
    max_tokens = data.get('max_tokens', 4500)

    if not query:
        return jsonify({"status": "error", "message": "Query cannot be empty"}), 400

    try:
        # Process through JARVIS
        jarvis_response = jarvis_module.ask_gemini(query)
        print(jarvis_response)
        # Process through LLM pipeline
        #llm_response = llm_ai_module.ask_ai5(query, validator, max_tokens)

        return jsonify({
            "status": "success",
            "jarvis": jarvis_response,
            "llm": llm_response
        })
    except Exception as e:
        logger.error(f"Query processing error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500
###########################################################################

@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    client_id = request.sid
    connected_clients.add(client_id)
    logger.info(f"Client connected: {client_id}, total clients: {len(connected_clients)}")

    # Send initial system status to the new client
    emit('system_status', last_system_health)

#############################################################################
@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection"""
    client_id = request.sid
    if client_id in connected_clients:
        connected_clients.remove(client_id)
    logger.info(f"Client disconnected: {client_id}, remaining clients: {len(connected_clients)}")

@socketio.on('ping')
def handle_ping():
    """Handle ping from client for latency measurement"""
    client_id = request.sid
    # Send pong back immediately
    emit('pong', {}, room=client_id)

@socketio.on('update_latency')
def handle_latency_update(data):
    """Handle latency updates from client"""
    global last_system_health
    
    if 'latency' in data:
        last_system_health['latency'] = data['latency']


@socketio.on('chat_message')
def handle_chat_message(data):
    """Handle chat messages from clients"""
    query = data.get('message', '')
    client_id = request.sid
    if not validator or not validator.hardware_validated:
        emit('chat_response', {"error": "Drone not connected or not validated"}, room=client_id)
        return
    
    if not query:
        emit('chat_response', {"error": "Empty message"}, room=client_id)
        return

    logger.info(f"Query from {client_id}: {query}")

    try:
        # Send acknowledgment first
        emit('chat_processing', {"status": "processing"}, room=client_id)  
        try:
                # Process through JARVIS
            #jarvis_response = jarvis_module.ask_gemini(query)
            jarvis_response = jarvis_module.ask_gemini(query, validator.categorized_params)
            print("Abhinav jarvis rep*****")
            print(jarvis_response)
                # Send JARVIS response immediately
            socketio.emit('chat_response', {
                    "source": "jarvis",
                    "response": jarvis_response
                }, room=client_id)

        except Exception as e:
            logger.error(f"Error processing query: {str(e)}")
            socketio.emit('chat_response', {"error": str(e)}, room=client_id)
       
    except Exception as e:
        logger.error(f"Error handling chat message: {str(e)}")
        emit('chat_response', {"error": str(e)}, room=client_id)

@socketio.on('start_voice_input')
def handle_start_voice_input():
    """Handle request from client to start voice input."""
    client_id = request.sid
    stt_logger.info(f"Client {client_id} requested to start voice input.")

    if not stt_module:
        stt_logger.error("STT module not initialized.")
        emit('voice_response', {'error': 'STT module not initialized.'}, room=client_id)
        return

    def transcription_callback(transcript, error=None):
        if error:
            stt_logger.error(f"Voice input error: {error}")
            socketio.emit('voice_response', {'error': error}, room=client_id)
        elif transcript:
            stt_logger.info(f"Transcribed text for {client_id}: '{transcript}'")
            # Now pass the transcribed text to JARVIS, similar to chat_message
            process_voice_command(client_id, transcript)
        else:
            stt_logger.warning(f"No transcription received for {client_id}.")
            socketio.emit('voice_response', {'message': 'No speech detected.'}, room=client_id)

    stt_module.start_recording(transcription_callback=transcription_callback)
    emit('voice_status', {'status': 'listening'}, room=client_id)

@socketio.on('stop_voice_input')
def handle_stop_voice_input():
    """Handle request from client to stop voice input and transcribe."""
    client_id = request.sid
    stt_logger.info(f"Client {client_id} requested to stop voice input and transcribe.")

    if not stt_module:
        stt_logger.error("STT module not initialized.")
        emit('voice_response', {'error': 'STT module not initialized.'}, room=client_id)
        return

    emit('voice_status', {'status': 'processing'}, room=client_id)
    stt_module.stop_recording_and_transcribe() # Transcription happens via callback

def process_voice_command(client_id, query):
    """Processes a transcribed voice command through JARVIS."""
    if not validator or not validator.hardware_validated:
        socketio.emit('voice_response', {"error": "Drone not connected or not validated"}, room=client_id)
        return
    
    if not query:
        socketio.emit('voice_response', {"error": "Empty command"}, room=client_id)
        return

    logger.info(f"Voice command from {client_id}: {query}")

    try:
        jarvis_response = jarvis_module.ask_gemini(query, validator.categorized_params)
        logger.info(f"JARVIS response to voice command: {jarvis_response}")
        
        socketio.emit('voice_response', {
            "source": "jarvis",
            "response": jarvis_response
        }, room=client_id)

        if jarvis_response and 'fix_command' in jarvis_response and jarvis_response['fix_command']:
            fix_command_json = jarvis_response['fix_command']
            logger.info(f"Attempting to execute fix command from JARVIS: {fix_command_json}")
            try:
                if isinstance(fix_command_json, dict): # Ensure it's a dict
                    command_name = fix_command_json.get('command', 'unknown')
                    if validator.send_mavlink_command_from_json(fix_command_json):
                        # The `send_mavlink_command_from_json` now handles logging the ACK/NACK/timeout details
                        # We can just confirm it was initiated and (eventually) ACKed/in_progress
                        socketio.emit('voice_response', {'message': f"Command '{command_name}' initiated and acknowledged by drone."}, room=client_id)
                    else:
                        # If send_mavlink_command_from_json returns False, it means NACK or timeout
                        socketio.emit('voice_response', {'error': f"Command '{command_name}' failed to be acknowledged by drone or timed out."}, room=client_id)
                else:
                    logger.error(f"Invalid fix_command format from JARVIS: {fix_command_json}")
                    socketio.emit('voice_response', {'error': f"Invalid command format: {fix_command_json}"}, room=client_id)
            except Exception as e:
                logger.error(f"Error sending MAVLink command: {e}")
                socketio.emit('voice_response', {'error': f"Error sending command: {e}"}, room=client_id)

    except Exception as e:
        logger.error(f"Error processing voice command with JARVIS: {str(e)}")
        socketio.emit('voice_response', {"error": str(e)}, room=client_id)

########################################################################

def update_system_health():
    """Update system health information from validator data"""
    global last_system_health

    if not validator or not validator.hardware_validated:
        return

    try:
        # Get categorized parameters
        params = validator.categorized_params

        # Process subsystem statuses
        subsystems = []
        critical_issues = 0

        # Check battery status from MAVLink messages
        battery_params = params.get("Battery", {})
        battery_voltage = 0
        battery_current = 0
        battery_remaining = 0
        
        # Get battery threshold parameters for status determination
        battery_low_threshold = round( battery_params.get("BATT_LOW_VOLT", 10.5), 2)
        battery_crit_threshold = battery_params.get("BATT_CRT_VOLT", 10.0)
        
        # Look for battery status in MAVLink messages
        for msg in mavlink_buffer:
            if msg.get("mavpackettype") == "SYS_STATUS":
                # SYS_STATUS contains basic battery info
                battery_voltage = msg.get("voltage_battery", 0) / 1000.0  # Convert from millivolts to volts
                battery_current = msg.get("current_battery", 0) / 1000.0   # Convert from centiamps to amps
                battery_remaining = msg.get("battery_remaining", -1)      # Percentage remaining, -1 if unknown
                #print(f"Battery data from SYS_STATUS: voltage={battery_voltage}V, current={battery_current}A")
                break
            elif msg.get("mavpackettype") == "BATTERY_STATUS":
                # BATTERY_STATUS has more detailed info
                battery_voltage = msg.get("voltages", [0])[0] / 1000.0  # First cell or total voltage
                battery_current = msg.get("current_battery", 0) / 1000.0
                battery_remaining = msg.get("battery_remaining", -1)
                #print(f"Battery data from BATTERY_STATUS: voltage={battery_voltage}V, current={battery_current}A")
                break
        
        # Determine battery status based on voltage thresholds
        if battery_voltage <= 0:
            battery_status = "UNKNOWN"
        elif battery_voltage < battery_crit_threshold:
            battery_status = "CRITICAL"
        elif battery_voltage < battery_low_threshold:
            battery_status = "WARNING"
        else:
            battery_status = "OK"
            
        # Add to critical issues if necessary
        if battery_status == "CRITICAL":
            critical_issues += 1
            subsystems.append({
                "component": "Battery",
                "status": "CRITICAL",
                "details": f"{battery_voltage:.2f}V (Below critical threshold)"
            })
        elif battery_status == "WARNING":
            subsystems.append({
                "component": "Battery",
                "status": "WARNING",
                "details": f"{battery_voltage:.2f}V (Below low threshold)"
            })
        elif battery_status == "UNKNOWN":
            subsystems.append({
                "component": "Battery",
                "status": "WARNING",
                "details": "No battery data available"
            })
        else:
            subsystems.append({
                "component": "Battery",
                "status": "OK",
                "details": f"{battery_voltage:.2f}V, {battery_current:.1f}A"
            })

        # Check GPS status
        gps_params = params.get("GPS", {})
        gps_type = gps_params.get("GPS_TYPE", 1)
        gps_fix = 0  # Default to no fix
        satellites = 0

        # Get GPS data from MAVLink messages
        for msg in mavlink_buffer:
            if msg.get("mavpackettype") == "GPS_RAW_INT":
                gps_fix = msg.get("fix_type", 0)
                satellites = msg.get("satellites_visible", 0)
                break

        gps_status = "CRITICAL" if gps_fix == 0 else "OK"

        if gps_status == "CRITICAL":
            critical_issues += 1
            subsystems.append({
                "component": "GPS",
                "status": "CRITICAL",
                "details": f"No GPS fix (Type {gps_fix})"
            })
        else:
            subsystems.append({
                "component": "GPS",
                "status": "OK",
                "details": f"Fix Type: {gps_fix}, Satellites: {satellites}"
            })

        # Check compass status
        compass_params = params.get("Compass", {})
        compass_enabled = compass_params.get("COMPASS_ENABLE", 1) == 1
        compass_use = compass_params.get("COMPASS_USE", 0) == 1

        if not compass_enabled or not compass_use:
            critical_issues += 1
            subsystems.append({
                "component": "Compass",
                "status": "CRITICAL",
                "details": "Disabled (COMPASS_USE=0)"
            })
        else:
            subsystems.append({
                "component": "Compass",
                "status": "OK",
                "details": "Enabled and calibrated"
            })

        # Check IMU status
        imu_params = params.get("IMU", {})
        imu_status = "OK"  # Assume OK
        imu_temp = 41.07  # Default value

        # Get IMU data from MAVLink messages
        for msg in mavlink_buffer:
            if msg.get("mavpackettype") == "SCALED_IMU":
                # Process IMU data if available
                imu_temp = msg.get("temperature", 41.07) / 100.0  # Typically in centidegrees
                print(f"IMU data found: temp={imu_temp}°C")
                break

        subsystems.append({
            "component": "IMU",
            "status": imu_status,
            "details": f"Calibrated, temp: {imu_temp}°C"
        })
        
        # Check barometer status
        baro_params = params.get("Barometer", {})
        baro_status = "OK"  # Default status
        baro_altitude = 0
        baro_pressure = 0
        baro_temperature = 0
        
        # Debug the mavlink buffer
        #print(f"Mavlink buffer contains {len(mavlink_buffer)} messages")
        message_types = [msg.get("mavpackettype", "unknown") for msg in mavlink_buffer]
        #print(f"Message types in buffer: {message_types}")
        
        if mavlink_buffer:
            sample_msg = mavlink_buffer[0]
           # print(f"Sample message keys: {sample_msg.keys()}")
           # print(f"Sample message content: {sample_msg}")
        
        # Get barometer data from MAVLink messages
        for msg in mavlink_buffer:
            if msg.get("mavpackettype") == "SCALED_PRESSURE":
                baro_pressure = msg.get("press_abs", 0)  # Absolute pressure in hectopascals/millibars
                baro_temperature = msg.get("temperature", 0) / 100.0  # Temperature in degrees C
                baro_status = "OK"
                #print(f"Barometer data: Pressure={baro_pressure}, Temperature={baro_temperature}")
                break
                
        # Check for altitude data which might come from different messages
        for msg in mavlink_buffer:
            if msg.get("mavpackettype") == "VFR_HUD":
                baro_altitude = msg.get("alt", 0)  # Altitude in meters
                #print(f"Altitude data from VFR_HUD: {baro_altitude}m")
                break
        
        # Check if barometer is enabled in parameters (some systems allow disabling)
        baro_enabled = True
        for param_name, value in baro_params.items():
            if param_name.upper().endswith("_ENABLE") and value == 0:
                baro_enabled = False
                baro_status = "WARNING"
                break
                
        if not baro_enabled:
            subsystems.append({
                "component": "Barometer",
                "status": "WARNING",
                "details": "Disabled in parameters"
            })
        elif baro_pressure <= 0:
            subsystems.append({
                "component": "Barometer",
                "status": "WARNING",
                "details": "No data received"
            })
        else:
            subsystems.append({
                "component": "Barometer",
                "status": "OK",
                "details": f"{baro_pressure:.1f} hPa, {baro_temperature:.1f}°C, Alt: {baro_altitude:.1f}m"
            })

        # Check RC status
        rc_params = params.get("RC", {})
        rc_status = "WARNING"  # Assume warning for demo

        subsystems.append({
            "component": "RC Channels",
            "status": rc_status,
            "details": "All channels reading 0"
        })

        # Check flight controller status
        control_params = params.get("Control", {})
        fc_status = "WARNING"  # Assume warning for demo

        subsystems.append({
            "component": "Flight Controllers",
            "status": fc_status,
            "details": "ATC_RATE params = 0"
        })

        # Calculate health score based on critical issues
        health_score = max(0, 100 - (critical_issues * 16))

        # Determine overall readiness
        readiness = "CAUTION" if critical_issues > 0 else "READY"

        # Get motor information from MAVLink messages
        motors = []
        motor_output_found = False
        
        # Process through SERVO_OUTPUT_RAW message which contains motor outputs
        for msg in mavlink_buffer:
            if msg.get("mavpackettype") == "SERVO_OUTPUT_RAW":
                motor_output_found = True
                #print(f"SERVO_OUTPUT_RAW message found: {msg}")
                # Map servo outputs to motor values (typically 1000-2000)
                for i in range(1, 5):  # Assuming quad copter with 4 motors
                    servo_key = f"servo{i}_raw"
                    output_raw = msg.get(servo_key, 1000)
                    # Convert to percentage (1000-2000 → 0-100%)
                    output_percent = max(0, min(100, (output_raw - 1000) / 10))
                    motors.append({
                        "id": i,
                        "output": int(output_percent),
                        "status": "OK" if output_raw > 1010 else "OFF"
                    })
                break
        
        # Also check for RC_CHANNELS message for stick inputs
        if not motor_output_found:
            for msg in mavlink_buffer:
                if msg.get("mavpackettype") == "RC_CHANNELS":
                 #   print(f"RC_CHANNELS message found: {msg}")
                    throttle_value = msg.get("chan3_raw", 1000)  # Typically throttle
                    # Map throttle to all motors for visualization
                    throttle_percent = max(0, min(100, (throttle_value - 1000) / 10))
                    for i in range(1, 5):
                        motors.append({
                            "id": i,
                            "output": int(throttle_percent),
                            "status": "OK" if throttle_value > 1010 else "OFF"
                        })
                    motor_output_found = True
                    break
        
        # If no motor data found, use default values but make it obvious they're not real
        if not motor_output_found or not motors:
            motors = [
                {"id": 1, "output": 0, "status": "NO DATA"},
                {"id": 2, "output": 0, "status": "NO DATA"},
                {"id": 3, "output": 0, "status": "NO DATA"},
                {"id": 4, "output": 0, "status": "NO DATA"}
            ]

        # Calculate battery percentage based on battery_low_threshold
        if battery_voltage > 0 and battery_low_threshold > 0:
            battery_percentage = int((battery_voltage / battery_low_threshold) * 100)
            # Clamp to reasonable range (0-100%)
            battery_percentage = max(0, min(100, battery_percentage))
        else:
            battery_percentage = 0
        
        # Update system health
        last_system_health = {
            "score": health_score,
            "critical_issues": critical_issues,
            "readiness": readiness,
            "battery": {
                "voltage": battery_voltage,
                "threshold": battery_low_threshold,
                "status": battery_status,
                "percentage": battery_percentage
            },
            "gps": {
                "fix_type": gps_fix,
                "satellites": satellites,
                "satellites_visible": satellites,
                "status": gps_status
            },
            "motors": motors,
            "subsystems": subsystems
        }

        # Add MAVLink link latency from TIMESYNC
        if hasattr(validator, 'get_latency_stats'):
            latency_stats = validator.get_latency_stats()
            last_system_health["latency"] = latency_stats["current"]
            last_system_health["latency_stats"] = {
                "avg": latency_stats["avg"],
                "min": latency_stats["min"],
                "max": latency_stats["max"],
            }
        
        # Parameter progress is now handled by update_param_progress function

        # Broadcast system health to all connected clients
        if connected_clients:
            socketio.emit('system_status', last_system_health)

    except Exception as e:
        logger.error(f"Error updating system health: {str(e)}")
        logger.exception("Exception details:")  # Log the full traceback

#####################################################################
######################################################################

def telemetry_update_loop():
    """Continuously update and broadcast telemetry data"""
    logger.info("Starting telemetry update loop")
    param_update_counter = 0
    
    while True:
        print("Total number of threads:", threading.active_count())
        try:
            # Update system health from validator data
            if validator and validator.hardware_validated and connected_clients:
                # Copy recent MAVLink messages to our buffer
                if hasattr(validator, 'ai_mavlink_ctx'):
                    global mavlink_buffer
                    mavlink_buffer = validator.ai_mavlink_ctx.copy()

                # Update and broadcast system health
                update_system_health()
                time.sleep(1)  # Sleep to avoid excessive updates
            
            else:
                time.sleep(2)  # Sleep to avoid excessive updates
            
        except Exception as e:
            logger.error(f"Error in telemetry update loop: {str(e)}")
            time.sleep(5)  # Sleep longer on error

#########################################################################
def start_server(validator_instance, jarvis, host='0.0.0.0', port=5000, debug=False, loggers=None, stt_recorder=None):
    """Start the Flask+SocketIO server in a new thread"""
    global validator, jarvis_module, llm_ai_module, telemetry_thread, logger
    
    # Use provided logger if available
    if loggers and 'web_server' in loggers:
        logger = loggers['web_server']

    # Store references to backend components
    validator = validator_instance
    jarvis_module = jarvis
    stt_module = stt_recorder
    #llm_ai_module = llm_ai

    # Make sure the static directory exists
    static_dir = os.path.join(os.path.dirname(__file__), 'static')
    os.makedirs(static_dir, exist_ok=True)

    # Copy index.html to static directory if not already there
    index_path = os.path.join(static_dir, 'index.html')
    if not os.path.exists(index_path):
        logger.info("Copying index.html to static directory")
        source_index = os.path.join(os.path.dirname(__file__), 'index.html')
        if os.path.exists(source_index):
            import shutil
            shutil.copy2(source_index, index_path)
        else:
            logger.warning(f"Source index.html not found at {source_index}")

    # Start telemetry update thread
    telemetry_thread = threading.Thread(target=telemetry_update_loop, daemon=True)
    telemetry_thread.start()

    # Start the Flask server in a new thread
    def run_server():
        logger.info(f"Starting web server on {host}:{port}")
        socketio.run(app, host=host, port=port, debug=debug, use_reloader=False)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    logger.info("Web server started")
    return server_thread


if __name__ == "__main__":
    # This allows running the web server standalone for testing
    print("Starting web server in standalone mode (no backend)")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True, use_reloader=False)
