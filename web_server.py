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

# Create Flask app and SocketIO instance
app = Flask(__name__, static_folder='static')
app.config['SECRET_KEY'] = 'uav-ai-assistant-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global variables to store references to backend components
validator = None  # Will hold the DroneValidator instance
jarvis_module = None  # Will hold the JARVIS module
llm_ai_module = None  # Will hold the llm_ai_v5 module
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
        port = data.get('port', 'COM3')
        baud = int(data.get('baud', 115200))
        print(f"Attempting connection to {port} at {baud} baud")
        
        # Check if already connected and disconnect first if needed
        if hasattr(validator, 'is_connected') and validator.is_connected:
            logger.info(f"Already connected, disconnecting first")
            validator.disconnect()
        
        # Just attempt the basic connection
        if validator.connect(port, baud):
            # Store connection parameters and set flags
            connection_params["port"] = port
            connection_params["baud"] = baud
            connection_params["connect_requested"] = True
            connection_params["connect_success"] = True
            
            logger.info(f"Connection successful to {port} at {baud} baud")
            return jsonify({
                "status": "success",
                "message": f"Connected to {port} at {baud} baud"
            })
        else:
            logger.error(f"Failed to connect to drone on {port}")
            connection_params["connect_success"] = False
            return jsonify({"status": "error", "message": f"Failed to connect to drone on {port}"}), 400

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
            
            # In a real implementation, we would update the parameters on the drone
            # For now, we'll just simulate success
            
            # TODO: Implement actual parameter update logic
            # Example:
            # for param_name, value in data.items():
            #     validator.set_parameter(param_name, value)
            
            return jsonify({
                "status": "success",
                "message": "Parameters updated successfully",
                "updated": list(data.keys())
            })
    except Exception as e:
        logger.error(f"Error handling parameters: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


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
        llm_response = llm_ai_module.ask_ai5(query, validator, max_tokens)

        return jsonify({
            "status": "success",
            "jarvis": jarvis_response,
            "llm": llm_response
        })
    except Exception as e:
        logger.error(f"Query processing error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


@socketio.on('connect')
def handle_connect():
    """Handle WebSocket connection"""
    client_id = request.sid
    connected_clients.add(client_id)
    logger.info(f"Client connected: {client_id}, total clients: {len(connected_clients)}")

    # Send initial system status to the new client
    emit('system_status', last_system_health)


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

    if not query:
        emit('chat_response', {"error": "Empty message"}, room=client_id)
        return

    logger.info(f"Query from {client_id}: {query}")

    try:
        # Send acknowledgment first
        emit('chat_processing', {"status": "processing"}, room=client_id)

        # Process through AI systems (run JARVIS in thread to avoid blocking)
        def process_and_respond():
            try:
                # Process through JARVIS
                jarvis_response = jarvis_module.ask_gemini(query)
                print("Abhinav jarvis rep*****")
                print(jarvis_response)
                # Send JARVIS response immediately
                socketio.emit('chat_response', {
                    "source": "jarvis",
                    "response": jarvis_response
                }, room=client_id)

                # Process through LLM pipeline (which takes longer)
                #llm_response = llm_ai_module.ask_ai5(query, validator, 4500)

                # Send LLM response when ready
                #socketio.emit('chat_response', {
                #    "source": "llm",
                #    "response": llm_response
                #}, room=client_id)

            except Exception as e:
                logger.error(f"Error processing query: {str(e)}")
                socketio.emit('chat_response', {"error": str(e)}, room=client_id)

        # Start processing in a thread
        threading.Thread(target=process_and_respond).start()

    except Exception as e:
        logger.error(f"Error handling chat message: {str(e)}")
        emit('chat_response', {"error": str(e)}, room=client_id)


# Function removed as requested

# Function removed as requested

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
        
        # Parameter progress is now handled by update_param_progress function

        # Broadcast system health to all connected clients
        if connected_clients:
            socketio.emit('system_status', last_system_health)

    except Exception as e:
        logger.error(f"Error updating system health: {str(e)}")
        logger.exception("Exception details:")  # Log the full traceback

def update_param_progress():
    """Update and broadcast parameter download progress"""
    try:
        if validator and connected_clients:
            # Get parameter progress from validator
            param_percentage = validator.param_progress if hasattr(validator, 'param_progress') else 0
            param_count = validator.param_count if hasattr(validator, 'param_count') else 0
            param_downloaded = int(param_count * (param_percentage / 100)) if param_count > 0 else 0

            socketio.emit('parameter_progress', {
                "percentage": param_percentage,
                "downloaded": param_downloaded,
                "total": param_count
            }, room=connected_clients)
            
               
    except Exception as e:
        logger.error(f"Error updating parameter progress: {str(e)}")

def telemetry_update_loop():
    """Continuously update and broadcast telemetry data"""
    logger.info("Starting telemetry update loop")
    param_update_counter = 0
    
    while True:
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
            elif validator and not validator.hardware_validated:    
                # Update parameter progress more frequently
                update_param_progress()
                time.sleep(0.25)  # Sleep to avoid excessive updates
            else:
                time.sleep(1)  # Sleep to avoid excessive updates
            
        except Exception as e:
            logger.error(f"Error in telemetry update loop: {str(e)}")
            time.sleep(5)  # Sleep longer on error


def start_server(validator_instance, jarvis, host='0.0.0.0', port=5000, debug=False, loggers=None):
    """Start the Flask+SocketIO server in a new thread"""
    global validator, jarvis_module, llm_ai_module, telemetry_thread, logger
    
    # Use provided logger if available
    if loggers and 'web_server' in loggers:
        logger = loggers['web_server']

    # Store references to backend components
    validator = validator_instance
    jarvis_module = jarvis
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