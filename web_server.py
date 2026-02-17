import sys
import os
import json
import logging
import math
import signal
import threading
import time
import glob
import tempfile
from datetime import datetime
from flask import Flask, render_template, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit
from log_parser import LogParser
import copilot


def _resource_path(relative_path):
    """Get path to resource, works for dev and PyInstaller bundle."""
    if getattr(sys, '_MEIPASS', None):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)


# Default logger that will be replaced if loggers are provided
logger = logging.getLogger('web_server')
stt_logger = logging.getLogger('stt_module')

# Create Flask app and SocketIO instance
app = Flask(__name__, static_folder=_resource_path('static'))
app.config['SECRET_KEY'] = 'uav-ai-assistant-secret-key'
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Global variables to store references to backend components
validator = None  # Will hold the DroneValidator instance
jarvis_module = None  # Will hold the JARVIS module
llm_ai_module = None  # Will hold the llm_ai_v5 module
stt_module = None # Will hold the STT recorder instance
telemetry_thread = None  # Will hold the telemetry update thread
connected_clients = set()  # Track connected WebSocket clients
mavlink_buffer = {}  # dict keyed by message type → latest msg of each type
log_parser_instance = None  # Will hold the current LogParser instance
LOG_UPLOAD_DIR = os.path.join(tempfile.gettempdir(), 'uav-ai-logs')
os.makedirs(LOG_UPLOAD_DIR, exist_ok=True)

# Connection parameters storage
connection_params = {
    "port": None,
    "baud": None,
    "connect_requested": False,
    "connect_success": False
}

# Co-pilot mode state
copilot_active = False         # Current co-pilot mode state
copilot_user_override = None   # None=auto, True=forced on, False=forced off

# Current LLM provider (used by voice commands and as default)
current_provider = "gemini"

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
    return send_from_directory(_resource_path('static'), 'index.html')


@app.route('/<path:path>')
def static_files(path):
    """Serve static files"""
    return send_from_directory(_resource_path('static'), path)


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

                # Start message loop and request data every time we connect
                validator.start_message_loop()
                validator.request_data_stream()
                validator.request_autopilot_version()
                validator.request_parameter_list()
                logger.info(f"Connection successful to {port} at {baud} baud. Message loop started.")

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
    """API endpoint to request blackbox log list from FC and return status."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        if not validator.is_connected:
            return jsonify({"status": "error", "message": "Drone not connected"}), 400

        # Trigger a log list request from the FC
        validator.request_blackbox_logs()

        # Return current log status
        log_ids = list(validator.log_list)
        downloaded_files = []
        for log_id in log_ids:
            log_path = os.path.join(validator.log_directory, f"log_{log_id}.bin")
            if os.path.exists(log_path):
                size = os.path.getsize(log_path)
                downloaded_files.append({"id": log_id, "filename": f"log_{log_id}.bin", "size_bytes": size})

        if not log_ids:
            return jsonify({
                "status": "success",
                "message": "Log list requested from FC. No logs found yet — the FC may have no stored logs.",
                "log_count": 0,
                "logs": []
            })

        return jsonify({
            "status": "success",
            "message": f"Found {len(log_ids)} log(s) on FC.",
            "log_count": len(log_ids),
            "logs": downloaded_files
        })
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
# Firmware Flashing
####################################################################################
import firmware_flasher
import urllib.request

FIRMWARE_CACHE_DIR = None  # initialized lazily after _writable_path is defined

# Flash state shared across threads
_flash_state = {
    "status": "idle",       # idle | flashing | complete | error
    "stage": "",
    "percent": 0,
    "message": "",
    "result": None,
}
_flash_lock = threading.Lock()
_manifest_cache = {"data": None, "fetched_at": 0}


def _get_firmware_cache_dir():
    global FIRMWARE_CACHE_DIR
    if FIRMWARE_CACHE_DIR is None:
        FIRMWARE_CACHE_DIR = _writable_path('firmware_cache')
        os.makedirs(FIRMWARE_CACHE_DIR, exist_ok=True)
    return FIRMWARE_CACHE_DIR


@app.route('/api/firmware/manifest', methods=['GET'])
def get_firmware_manifest():
    """Fetch ArduPilot firmware manifest, filter by detected board_id."""
    try:
        now = time.time()
        # Cache for 1 hour
        if _manifest_cache["data"] and (now - _manifest_cache["fetched_at"]) < 3600:
            manifest = _manifest_cache["data"]
        else:
            cache_dir = _get_firmware_cache_dir()
            manifest_path = os.path.join(cache_dir, 'manifest.json')

            # Check local file cache
            if os.path.exists(manifest_path) and (now - os.path.getmtime(manifest_path)) < 3600:
                with open(manifest_path, 'r') as f:
                    manifest = json.load(f)
            else:
                url = 'https://firmware.ardupilot.org/manifest.json'
                logger.info(f"Fetching ArduPilot manifest from {url}...")
                req = urllib.request.Request(url, headers={'User-Agent': 'UAV-AI/1.0'})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    raw = resp.read()
                manifest = json.loads(raw)
                # Save to disk cache
                with open(manifest_path, 'w') as f:
                    json.dump(manifest, f)
                logger.info("Manifest fetched and cached")

            _manifest_cache["data"] = manifest
            _manifest_cache["fetched_at"] = now

        # Filter by detected board_id if connected
        detected_board_id = None
        if validator and validator.firmware_data:
            detected_board_id = validator.firmware_data.get('product_id')

        firmware_list = manifest.get('firmware', [])

        # Filter to .apj files only
        filtered = [
            fw for fw in firmware_list
            if fw.get('format') == 'apj'
        ]

        # If we know the board, filter to matching board_id
        if detected_board_id:
            board_filtered = [
                fw for fw in filtered
                if fw.get('board_id') == detected_board_id
            ]
            # Fall back to full list if no matches (board_id might differ in manifest)
            if board_filtered:
                filtered = board_filtered

        # Group by vehicle type and version for frontend
        grouped = {}
        for fw in filtered:
            vehicle = fw.get('vehicletype', 'Unknown')
            if vehicle not in grouped:
                grouped[vehicle] = []
            grouped[vehicle].append({
                'url': fw.get('url', ''),
                'version': fw.get('mav-firmware-version-str', fw.get('mav-firmware-version', '')),
                'vehicletype': vehicle,
                'board_id': fw.get('board_id', 0),
                'platform': fw.get('platform', ''),
                'latest': fw.get('latest', 0),
                'mav_type': fw.get('mav-type', ''),
                'git_sha': fw.get('git-sha', ''),
            })

        # Sort each group by version descending
        for vehicle in grouped:
            grouped[vehicle].sort(key=lambda x: x['version'], reverse=True)

        return jsonify({
            "status": "success",
            "firmware": grouped,
            "detected_board_id": detected_board_id,
        })

    except urllib.error.URLError as e:
        logger.error(f"Failed to fetch manifest: {e}")
        return jsonify({"status": "error", "message": f"Failed to fetch manifest: {e}"}), 502
    except Exception as e:
        logger.error(f"Error getting firmware manifest: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/firmware/download', methods=['POST'])
def download_firmware():
    """Download .apj firmware from ArduPilot server to local cache."""
    try:
        data = request.get_json(force=True)
        url = data.get('url')
        if not url:
            return jsonify({"status": "error", "message": "No URL provided"}), 400

        # Validate URL is from ArduPilot firmware server
        if not url.startswith('https://firmware.ardupilot.org/'):
            return jsonify({"status": "error", "message": "URL must be from firmware.ardupilot.org"}), 400

        cache_dir = _get_firmware_cache_dir()
        filename = url.split('/')[-1]
        if not filename.endswith('.apj'):
            return jsonify({"status": "error", "message": "URL must point to an .apj file"}), 400

        local_path = os.path.join(cache_dir, filename)

        # Download with progress
        logger.info(f"Downloading firmware from {url}...")
        req = urllib.request.Request(url, headers={'User-Agent': 'UAV-AI/1.0'})
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get('Content-Length', 0))
            downloaded = 0
            chunks = []
            while True:
                chunk = resp.read(8192)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = int(downloaded * 100 / total)
                    socketio.emit('firmware_download_progress', {
                        'percent': pct,
                        'downloaded': downloaded,
                        'total': total,
                    })

        with open(local_path, 'wb') as f:
            for chunk in chunks:
                f.write(chunk)

        logger.info(f"Downloaded firmware to {local_path}")
        return jsonify({
            "status": "success",
            "path": local_path,
            "filename": filename,
            "size": downloaded,
        })

    except Exception as e:
        logger.error(f"Error downloading firmware: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/firmware/flash', methods=['POST'])
def flash_firmware():
    """Start firmware flash. Accepts uploaded .apj file or cached path."""
    global _flash_state

    with _flash_lock:
        if _flash_state["status"] == "flashing":
            return jsonify({"status": "error", "message": "Flash already in progress"}), 409

    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    try:
        force = False
        apj_path = None

        # Check for uploaded file
        if 'file' in request.files:
            uploaded = request.files['file']
            if not uploaded.filename.endswith('.apj'):
                return jsonify({"status": "error", "message": "File must be .apj"}), 400
            cache_dir = _get_firmware_cache_dir()
            apj_path = os.path.join(cache_dir, uploaded.filename)
            uploaded.save(apj_path)
        else:
            data = request.get_json(force=True)
            apj_path = data.get('path')
            force = data.get('force', False)

        if not apj_path or not os.path.exists(apj_path):
            return jsonify({"status": "error", "message": "No firmware file provided or file not found"}), 400

        # Get current connection port
        port = connection_params.get("port")
        if not port or port.startswith("udp"):
            return jsonify({"status": "error",
                            "message": "Firmware flashing requires a serial connection (not UDP)"}), 400

        # Start flash in background thread
        def do_flash():
            global _flash_state
            with _flash_lock:
                _flash_state = {
                    "status": "flashing", "stage": "init",
                    "percent": 0, "message": "Starting...", "result": None
                }
            socketio.emit('firmware_flash_progress', {
                'stage': 'init', 'percent': 0, 'message': 'Starting flash...'
            })

            try:
                # 1. Reboot FC into bootloader
                socketio.emit('firmware_flash_progress', {
                    'stage': 'reboot', 'percent': 0,
                    'message': 'Rebooting flight controller into bootloader...'
                })
                validator.reboot_to_bootloader()
                time.sleep(1)

                # 2. Disconnect MAVLink to release serial port
                socketio.emit('firmware_flash_progress', {
                    'stage': 'reboot', 'percent': 50,
                    'message': 'Disconnecting MAVLink...'
                })
                validator.disconnect()
                time.sleep(2)  # Wait for bootloader to be ready

                # 3. Flash firmware
                flasher = firmware_flasher.FirmwareFlasher()

                def progress_cb(stage, percent, message):
                    with _flash_lock:
                        _flash_state["stage"] = stage
                        _flash_state["percent"] = percent
                        _flash_state["message"] = message
                    socketio.emit('firmware_flash_progress', {
                        'stage': stage, 'percent': percent, 'message': message
                    })

                result = flasher.flash(port, apj_path, progress_callback=progress_cb, force=force)

                with _flash_lock:
                    if result['success']:
                        _flash_state["status"] = "complete"
                        _flash_state["message"] = result['message']
                    else:
                        _flash_state["status"] = "error"
                        _flash_state["message"] = result['message']
                    _flash_state["result"] = result

                socketio.emit('firmware_flash_complete', result)

            except Exception as e:
                logger.error(f"Flash thread error: {e}")
                with _flash_lock:
                    _flash_state["status"] = "error"
                    _flash_state["message"] = str(e)
                    _flash_state["result"] = {'success': False, 'message': str(e)}
                socketio.emit('firmware_flash_complete', {
                    'success': False, 'message': str(e)
                })

        flash_thread = threading.Thread(target=do_flash, daemon=True)
        flash_thread.start()

        return jsonify({"status": "success", "message": "Flash started"})

    except Exception as e:
        logger.error(f"Error starting flash: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@app.route('/api/firmware/status', methods=['GET'])
def get_flash_status():
    """Return current flash state."""
    with _flash_lock:
        return jsonify({
            "status": "success",
            "flash": dict(_flash_state),
        })


####################################################################################
# Golden Config Snapshots
####################################################################################
def _writable_path(relative_path):
    """Get a writable path next to the executable (bundled) or project root (dev)."""
    if getattr(sys, '_MEIPASS', None):
        return os.path.join(os.path.dirname(sys.executable), relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)


CONFIGS_DIR = _writable_path('configs')
os.makedirs(CONFIGS_DIR, exist_ok=True)
MAX_CONFIGS = 5


@app.route('/api/configs', methods=['GET'])
def list_configs():
    """List saved config snapshots."""
    configs = []
    for filepath in sorted(glob.glob(os.path.join(CONFIGS_DIR, '*.json'))):
        try:
            with open(filepath, 'r') as f:
                data = json.load(f)
            configs.append({
                "name": data.get("name", os.path.basename(filepath)),
                "saved_at": data.get("saved_at", ""),
                "param_count": data.get("param_count", 0),
                "filename": os.path.basename(filepath)
            })
        except Exception:
            continue
    return jsonify({"status": "success", "configs": configs})


@app.route('/api/configs', methods=['POST'])
def save_config():
    """Save current params_dict as a named config snapshot."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    if not validator.params_dict:
        return jsonify({"status": "error", "message": "No parameters loaded from drone"}), 400

    data = request.get_json(force=True)
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"status": "error", "message": "Config name is required"}), 400

    # Check max configs limit
    existing = glob.glob(os.path.join(CONFIGS_DIR, '*.json'))
    if len(existing) >= MAX_CONFIGS:
        return jsonify({"status": "error", "message": f"Maximum {MAX_CONFIGS} configs allowed. Delete one first."}), 400

    # Sanitize filename
    safe_name = "".join(c if c.isalnum() or c in '-_' else '_' for c in name)
    filepath = os.path.join(CONFIGS_DIR, f"{safe_name}.json")

    config_data = {
        "name": name,
        "saved_at": datetime.now().isoformat(timespec='seconds'),
        "param_count": len(validator.params_dict),
        "params": dict(validator.params_dict)
    }

    with open(filepath, 'w') as f:
        json.dump(config_data, f, indent=4)

    logger.info(f"Config saved: {name} ({len(validator.params_dict)} params)")
    return jsonify({"status": "success", "message": f"Config '{name}' saved", "filename": f"{safe_name}.json"})


@app.route('/api/configs/apply', methods=['POST'])
def apply_config():
    """Load a config and write only changed params to the FC."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    if not validator.is_connected:
        return jsonify({"status": "error", "message": "Drone not connected"}), 400

    data = request.get_json(force=True)
    filename = data.get('filename', '')
    filepath = os.path.join(CONFIGS_DIR, filename)

    if not os.path.exists(filepath):
        return jsonify({"status": "error", "message": "Config file not found"}), 404

    try:
        saved_params = validator.load_from_json(filepath)
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to load config: {str(e)}"}), 500

    # Diff against current params and write only changed ones
    changed = {}
    for param, value in saved_params.items():
        current = validator.params_dict.get(param)
        if current is None or abs(float(current) - float(value)) > 0.0001:
            changed[param] = value

    if not changed:
        return jsonify({"status": "success", "message": "All parameters already match", "changed": 0})

    errors = []
    for param, value in changed.items():
        if not validator.update_parameter(param, value):
            errors.append(param)

    if errors:
        return jsonify({
            "status": "partial",
            "message": f"Applied {len(changed) - len(errors)} params, {len(errors)} failed",
            "changed": len(changed) - len(errors),
            "failed": errors
        }), 207

    return jsonify({"status": "success", "message": f"Applied {len(changed)} changed parameters", "changed": len(changed)})


@app.route('/api/configs/<name>', methods=['DELETE'])
def delete_config(name):
    """Delete a saved config snapshot."""
    safe_name = "".join(c if c.isalnum() or c in '-_.' else '_' for c in name)
    filepath = os.path.join(CONFIGS_DIR, safe_name)

    if not os.path.exists(filepath):
        return jsonify({"status": "error", "message": "Config not found"}), 404

    os.remove(filepath)
    logger.info(f"Config deleted: {safe_name}")
    return jsonify({"status": "success", "message": f"Config deleted"})


####################################################################################
# Field Calibration
####################################################################################
CALIBRATION_PARAMS = {
    "gyro":    {"param1": 1, "param2": 0, "param3": 0, "param5": 0},
    "compass": {"param1": 0, "param2": 1, "param3": 0, "param5": 0},
    "accel":   {"param1": 0, "param2": 0, "param3": 0, "param5": 1},
    "baro":    {"param1": 0, "param2": 0, "param3": 1, "param5": 0},
    "level":   {"param1": 0, "param2": 0, "param3": 0, "param5": 2},
}


@app.route('/api/calibrate', methods=['POST'])
def calibrate():
    """Trigger an on-board calibration routine via MAV_CMD_PREFLIGHT_CALIBRATION."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    if not validator.is_connected:
        return jsonify({"status": "error", "message": "Drone not connected"}), 400

    data = request.get_json(force=True)
    cal_type = data.get('type', '').lower()

    if cal_type not in CALIBRATION_PARAMS:
        return jsonify({"status": "error", "message": f"Unknown calibration type: {cal_type}. Valid: {list(CALIBRATION_PARAMS.keys())}"}), 400

    cal = CALIBRATION_PARAMS[cal_type]
    command_json = {
        "command": "MAV_CMD_PREFLIGHT_CALIBRATION",
        "param1": cal["param1"],
        "param2": cal["param2"],
        "param3": cal["param3"],
        "param4": 0,
        "param5": cal["param5"],
        "param6": 0,
        "param7": 0,
    }

    success = validator.send_mavlink_command_from_json(command_json, timeout_seconds=30)
    if success:
        return jsonify({"status": "success", "message": f"{cal_type.capitalize()} calibration command accepted"})
    else:
        return jsonify({"status": "error", "message": f"{cal_type.capitalize()} calibration command rejected or timed out"}), 500


####################################################################################
@app.route('/api/motor_test', methods=['POST'])
def motor_test():
    """API endpoint to send a motor test command (disarmed only)."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500

    if not validator.is_connected:
        return jsonify({"status": "error", "message": "Drone not connected"}), 400

    # Check armed state from HEARTBEAT
    hb = mavlink_buffer.get("HEARTBEAT")
    if hb and (hb.get("base_mode", 0) & 128):
        return jsonify({"status": "error", "message": "Drone is ARMED — motor test blocked. Disarm first."}), 400

    data = request.get_json(force=True)
    motor = data.get("motor")      # 1-4
    throttle = data.get("throttle") # 0-100
    duration = data.get("duration") # 1-10 seconds

    # Validate inputs
    if motor not in (1, 2, 3, 4):
        return jsonify({"status": "error", "message": "motor must be 1-4"}), 400
    if not isinstance(throttle, (int, float)) or throttle < 0 or throttle > 100:
        return jsonify({"status": "error", "message": "throttle must be 0-100"}), 400
    if not isinstance(duration, (int, float)) or duration < 1 or duration > 10:
        return jsonify({"status": "error", "message": "duration must be 1-10 seconds"}), 400

    command_json = {
        "command": "MAV_CMD_DO_MOTOR_TEST",
        "param1": motor,           # motor instance (1-based)
        "param2": 1,               # throttle type = percentage
        "param3": throttle,        # throttle 0-100
        "param4": duration,        # duration in seconds
        "param5": 0,               # motor count (0 = single motor)
        "param6": 0,
        "param7": 0,
    }

    success = validator.send_mavlink_command_from_json(command_json)
    if success:
        return jsonify({"status": "success", "message": f"Motor {motor} test started at {throttle}% for {duration}s"})
    else:
        return jsonify({"status": "error", "message": f"Motor {motor} test command rejected or timed out"}), 500


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
_shutdown_timer = None

@socketio.on('disconnect')
def handle_disconnect():
    """Handle WebSocket disconnection. In bundled mode, auto-shutdown when all clients leave."""
    global _shutdown_timer
    client_id = request.sid
    if client_id in connected_clients:
        connected_clients.remove(client_id)
    logger.info(f"Client disconnected: {client_id}, remaining clients: {len(connected_clients)}")

    # Auto-shutdown in bundled (desktop) mode when no clients remain
    if getattr(sys, '_MEIPASS', None) and len(connected_clients) == 0:
        def _delayed_shutdown():
            if len(connected_clients) == 0:
                logger.info("No clients connected — shutting down desktop app")
                os.kill(os.getpid(), signal.SIGTERM)
        # Cancel any previous timer (e.g. page refresh)
        if _shutdown_timer is not None:
            _shutdown_timer.cancel()
        _shutdown_timer = threading.Timer(5.0, _delayed_shutdown)
        _shutdown_timer.daemon = True
        _shutdown_timer.start()

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


@socketio.on('copilot_toggle')
def handle_copilot_toggle(data):
    """Handle co-pilot mode toggle from the frontend."""
    global copilot_user_override, copilot_active
    enabled = data.get('enabled')
    if enabled is None:
        # Reset to auto mode
        copilot_user_override = None
    else:
        copilot_user_override = bool(enabled)
        copilot_active = copilot_user_override
    logger.info(f"Co-pilot toggle: override={copilot_user_override}, active={copilot_active}")


@socketio.on('get_providers')
def handle_get_providers():
    """Return list of available AI providers (those with API keys configured)."""
    client_id = request.sid
    providers = jarvis_module.get_available_providers() if jarvis_module else []
    emit('available_providers', {'providers': providers, 'current': current_provider}, room=client_id)


@socketio.on('set_api_key')
def handle_set_api_key(data):
    """Save an API key for a provider and update .env file."""
    global current_provider
    client_id = request.sid
    provider = data.get('provider', '')
    key = data.get('key', '').strip()

    if not provider or not key:
        emit('api_key_result', {'error': 'Missing provider or key'}, room=client_id)
        return

    # Map provider to env var name
    env_map = {
        'openai': 'OPENAI_API_KEY',
        'claude': 'ANTHROPIC_API_KEY',
        'gemini': 'GEMINI_API_KEY',
    }
    env_var = env_map.get(provider)
    if not env_var:
        emit('api_key_result', {'error': f'Unknown provider: {provider}'}, room=client_id)
        return

    # Set in current process
    os.environ[env_var] = key

    # Update .env file
    env_path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, '_MEIPASS', None) else os.path.dirname(__file__), '.env')
    try:
        lines = []
        found = False
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            for i, line in enumerate(lines):
                if line.startswith(f"{env_var}="):
                    lines[i] = f"{env_var}={key}\n"
                    found = True
                    break
        if not found:
            lines.append(f"{env_var}={key}\n")
        with open(env_path, 'w') as f:
            f.writelines(lines)
    except IOError as e:
        logger.error(f"Failed to update .env: {e}")

    # Auto-switch to this provider
    current_provider = provider
    providers = jarvis_module.get_available_providers() if jarvis_module else []
    emit('api_key_result', {'success': True, 'provider': provider}, room=client_id)
    emit('available_providers', {'providers': providers, 'current': current_provider}, room=client_id)
    logger.info(f"API key set for {provider}, switched to {provider}")


####################################################################################
# Settings — API Key Management
####################################################################################

@app.route('/api/settings/keys', methods=['GET'])
def get_api_key_status():
    """Return which providers have API keys configured, with masked previews."""
    env_map = {
        'gemini': 'GEMINI_API_KEY',
        'openai': 'OPENAI_API_KEY',
        'claude': 'ANTHROPIC_API_KEY',
    }
    keys = {}
    for provider, env_var in env_map.items():
        val = os.environ.get(env_var, '')
        if val:
            # Mask: show first 4 and last 4 chars
            if len(val) > 8:
                masked = val[:4] + '...' + val[-4:]
            else:
                masked = '****'
            keys[provider] = {'configured': True, 'masked': masked}
        else:
            keys[provider] = {'configured': False, 'masked': ''}
    return jsonify({'status': 'success', 'keys': keys})


@app.route('/api/settings/keys', methods=['DELETE'])
def delete_api_key():
    """Remove an API key from env and .env file."""
    data = request.get_json(force=True)
    provider = data.get('provider', '')
    env_map = {
        'gemini': 'GEMINI_API_KEY',
        'openai': 'OPENAI_API_KEY',
        'claude': 'ANTHROPIC_API_KEY',
    }
    env_var = env_map.get(provider)
    if not env_var:
        return jsonify({'status': 'error', 'message': f'Unknown provider: {provider}'}), 400

    # Remove from current process
    if env_var in os.environ:
        del os.environ[env_var]

    # Remove from .env file
    env_path = os.path.join(os.path.dirname(sys.executable) if getattr(sys, '_MEIPASS', None) else os.path.dirname(__file__), '.env')
    try:
        if os.path.exists(env_path):
            with open(env_path, 'r') as f:
                lines = f.readlines()
            lines = [l for l in lines if not l.startswith(f"{env_var}=")]
            with open(env_path, 'w') as f:
                f.writelines(lines)
    except IOError as e:
        logger.error(f"Failed to update .env: {e}")

    logger.info(f"API key deleted for {provider}")
    return jsonify({'status': 'success', 'message': f'{provider} key removed'})


@socketio.on('chat_message')
def handle_chat_message(data):
    """Handle chat messages from clients.

    Routes to drone assistant (JARVIS) when connected, or to log analysis
    when a log file is loaded. Drone queries take priority if both are available.
    """
    global current_provider
    query = data.get('message', '')
    provider = data.get('provider', current_provider)
    current_provider = provider  # remember for voice commands
    client_id = request.sid

    if not query:
        emit('chat_response', {"error": "Empty message"}, room=client_id)
        return

    drone_available = validator and validator.hardware_validated
    log_available = log_parser_instance and log_parser_instance._is_parsed

    if not drone_available and not log_available:
        emit('chat_response', {"error": "No drone connected and no log file loaded."}, room=client_id)
        return

    logger.info(f"Query from {client_id}: {query}")

    # --- Co-pilot fast-path: instant command matching, no AI round-trip ---
    if copilot_active and drone_available:
        result = copilot.try_fast_command(query, mavlink_buffer)
        if result:
            if result.get('fix_command'):
                fix_cmd = result['fix_command']
                command_name = fix_cmd.get('command', 'unknown')
                logger.info(f"Co-pilot executing: {command_name}")
                if validator.send_mavlink_command_from_json(fix_cmd):
                    socketio.emit('chat_response', {
                        'source': 'copilot',
                        'response': result['response'],
                        'message': f"Command '{command_name}' acknowledged."
                    }, room=client_id)
                else:
                    socketio.emit('chat_response', {
                        'source': 'copilot',
                        'response': result['response'],
                        'error': f"Command '{command_name}' failed or timed out."
                    }, room=client_id)
            else:
                socketio.emit('chat_response', {
                    'source': 'copilot',
                    'response': result['response']
                }, room=client_id)
            return

    # --- Route to log analysis if no drone but log is loaded ---
    if not drone_available and log_available:
        emit('chat_processing', {"status": "processing"}, room=client_id)
        try:
            summary = log_parser_instance.get_summary()
            result = jarvis_module.ask_gemini_log_analysis(query, summary, provider=provider)

            # Phase 2: fetch data if AI requested it
            need_data = result.get('need_data', [])
            if need_data:
                message_data = {}
                for msg_type in need_data:
                    md = log_parser_instance.get_message_data(msg_type)
                    if md:
                        message_data[msg_type] = md
                if message_data:
                    result = jarvis_module.ask_gemini_log_analysis(query, summary, message_data, provider=provider)

            socketio.emit('chat_response', {
                "source": "log_analysis",
                "analysis": result.get("analysis", ""),
                "charts": result.get("charts", []),
            }, room=client_id)
        except Exception as e:
            logger.error(f"Log analysis error: {e}")
            socketio.emit('chat_response', {"error": str(e)}, room=client_id)
        return

    # --- Route to drone assistant (JARVIS) ---
    try:
        # Send acknowledgment first
        emit('chat_processing', {"status": "processing"}, room=client_id)
        try:
            import time as _time
            _jarvis_start = _time.time()
            print(f">>> JARVIS [{provider}] query sent: \"{query}\"")
            jarvis_response = jarvis_module.ask_jarvis(query, validator.categorized_params, validator.ai_mavlink_ctx, provider=provider)
            _jarvis_elapsed = _time.time() - _jarvis_start
            print(f"<<< JARVIS [{provider}] response received in {_jarvis_elapsed:.2f}s")
            print(jarvis_response)

            # Check for quota exhaustion
            response_data = {
                "source": "jarvis",
                "response": jarvis_response
            }
            if jarvis_response and jarvis_response.get('quota_exhausted'):
                response_data['quota_exhausted'] = True
            socketio.emit('chat_response', response_data, room=client_id)

            # Execute fix_command if JARVIS included one (supports single dict or list of dicts)
            if jarvis_response and 'fix_command' in jarvis_response and jarvis_response['fix_command']:
                fix_command_raw = jarvis_response['fix_command']
                # Normalize to a list
                commands = fix_command_raw if isinstance(fix_command_raw, list) else [fix_command_raw]
                for fix_command_json in commands:
                    logger.info(f"Attempting to execute fix command from JARVIS: {fix_command_json}")
                    try:
                        if isinstance(fix_command_json, dict):
                            command_name = fix_command_json.get('command', 'unknown')
                            if validator.send_mavlink_command_from_json(fix_command_json):
                                socketio.emit('chat_response', {'message': f"Command '{command_name}' initiated and acknowledged by drone."}, room=client_id)
                            else:
                                socketio.emit('chat_response', {'error': f"Command '{command_name}' failed to be acknowledged by drone or timed out."}, room=client_id)
                                break  # Stop executing remaining commands if one fails
                        else:
                            logger.error(f"Invalid fix_command format from JARVIS: {fix_command_json}")
                            socketio.emit('chat_response', {'error': f"Invalid command format: {fix_command_json}"}, room=client_id)
                    except Exception as e:
                        logger.error(f"Error sending MAVLink command: {e}")
                        socketio.emit('chat_response', {'error': f"Error sending command: {e}"}, room=client_id)

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
    """Processes a transcribed voice command through JARVIS or co-pilot fast-path."""
    if not validator or not validator.hardware_validated:
        socketio.emit('voice_response', {"error": "Drone not connected or not validated"}, room=client_id)
        return
    
    if not query:
        socketio.emit('voice_response', {"error": "Empty command"}, room=client_id)
        return

    logger.info(f"Voice command from {client_id}: {query}")

    # --- Co-pilot fast-path for voice commands ---
    if copilot_active:
        result = copilot.try_fast_command(query, mavlink_buffer)
        if result:
            if result.get('fix_command'):
                fix_cmd = result['fix_command']
                command_name = fix_cmd.get('command', 'unknown')
                logger.info(f"Co-pilot (voice) executing: {command_name}")
                if validator.send_mavlink_command_from_json(fix_cmd):
                    socketio.emit('voice_response', {
                        'source': 'copilot',
                        'response': result['response'],
                        'message': f"Command '{command_name}' acknowledged."
                    }, room=client_id)
                else:
                    socketio.emit('voice_response', {
                        'source': 'copilot',
                        'response': result['response'],
                        'error': f"Command '{command_name}' failed or timed out."
                    }, room=client_id)
            else:
                socketio.emit('voice_response', {
                    'source': 'copilot',
                    'response': result['response']
                }, room=client_id)
            return

    # --- Fall through to JARVIS for complex voice commands ---
    try:
        import time as _time
        _jarvis_start = _time.time()
        print(f">>> JARVIS [{current_provider}] voice query sent: \"{query}\"")
        jarvis_response = jarvis_module.ask_jarvis(query, validator.categorized_params, validator.ai_mavlink_ctx, provider=current_provider)
        _jarvis_elapsed = _time.time() - _jarvis_start
        print(f"<<< JARVIS [{current_provider}] voice response received in {_jarvis_elapsed:.2f}s")
        logger.info(f"JARVIS response to voice command: {jarvis_response}")
        
        socketio.emit('voice_response', {
            "source": "jarvis",
            "response": jarvis_response
        }, room=client_id)

        if jarvis_response and 'fix_command' in jarvis_response and jarvis_response['fix_command']:
            fix_command_raw = jarvis_response['fix_command']
            # Normalize to a list
            commands = fix_command_raw if isinstance(fix_command_raw, list) else [fix_command_raw]
            for fix_command_json in commands:
                logger.info(f"Attempting to execute fix command from JARVIS: {fix_command_json}")
                try:
                    if isinstance(fix_command_json, dict):
                        command_name = fix_command_json.get('command', 'unknown')
                        if validator.send_mavlink_command_from_json(fix_command_json):
                            socketio.emit('voice_response', {'message': f"Command '{command_name}' initiated and acknowledged by drone."}, room=client_id)
                        else:
                            socketio.emit('voice_response', {'error': f"Command '{command_name}' failed to be acknowledged by drone or timed out."}, room=client_id)
                            break  # Stop executing remaining commands if one fails
                    else:
                        logger.error(f"Invalid fix_command format from JARVIS: {fix_command_json}")
                        socketio.emit('voice_response', {'error': f"Invalid command format: {fix_command_json}"}, room=client_id)
                except Exception as e:
                    logger.error(f"Error sending MAVLink command: {e}")
                    socketio.emit('voice_response', {'error': f"Error sending command: {e}"}, room=client_id)

    except Exception as e:
        logger.error(f"Error processing voice command with JARVIS: {str(e)}")
        socketio.emit('voice_response', {"error": str(e)}, room=client_id)


####################################################################################
# Log Analysis
####################################################################################

ALLOWED_LOG_EXTENSIONS = {'.bin', '.tlog'}
MAX_LOG_SIZE = 500 * 1024 * 1024  # 500 MB


@app.route('/api/upload_log', methods=['POST'])
def upload_log():
    """Upload and parse a .bin or .tlog log file."""
    global log_parser_instance

    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file provided"}), 400

    file = request.files['file']
    if not file.filename:
        return jsonify({"status": "error", "message": "No file selected"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED_LOG_EXTENSIONS:
        return jsonify({"status": "error", "message": f"Unsupported file type: {ext}. Use .bin or .tlog"}), 400

    try:
        # Save to temp directory
        safe_name = os.path.basename(file.filename)
        filepath = os.path.join(LOG_UPLOAD_DIR, safe_name)
        file.save(filepath)

        file_size = os.path.getsize(filepath)
        if file_size > MAX_LOG_SIZE:
            os.remove(filepath)
            return jsonify({"status": "error", "message": f"File too large ({file_size // (1024*1024)} MB). Max is {MAX_LOG_SIZE // (1024*1024)} MB"}), 400

        # Parse the log
        parser = LogParser()
        summary = parser.parse(filepath)
        log_parser_instance = parser

        logger.info(f"Log uploaded and parsed: {safe_name} ({file_size} bytes, {summary['total_messages']} messages)")

        return jsonify({
            "status": "success",
            "message": f"Parsed {safe_name}: {summary['total_messages']} messages across {len(summary['message_types'])} types",
            "summary": summary,
        })

    except Exception as e:
        logger.error(f"Error parsing log: {e}")
        return jsonify({"status": "error", "message": f"Failed to parse log: {str(e)}"}), 500


@app.route('/api/log_status', methods=['GET'])
def get_log_status():
    """Check if a log file is currently loaded."""
    if log_parser_instance and log_parser_instance._is_parsed:
        return jsonify({"loaded": True, "filename": log_parser_instance.filename})
    return jsonify({"loaded": False})


@app.route('/api/log_message/<msg_type>', methods=['GET'])
def get_log_message_data(msg_type):
    """Return parsed data for a specific message type."""
    if not log_parser_instance or not log_parser_instance._is_parsed:
        return jsonify({"status": "error", "message": "No log file loaded"}), 400

    max_points = request.args.get('max_points', 500, type=int)
    data = log_parser_instance.get_message_data(msg_type, max_points=max_points)

    if not data:
        return jsonify({"status": "error", "message": f"No data for message type: {msg_type}"}), 404

    return jsonify({
        "status": "success",
        "msg_type": msg_type,
        "count": len(data),
        "fields": log_parser_instance.get_fields_for_type(msg_type),
        "data": data,
    })


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
        msg = mavlink_buffer.get("SYS_STATUS")
        if msg:
            battery_voltage = msg.get("voltage_battery", 0) / 1000.0
            battery_current = msg.get("current_battery", 0) / 1000.0
            battery_remaining = msg.get("battery_remaining", -1)
        else:
            msg = mavlink_buffer.get("BATTERY_STATUS")
            if msg:
                battery_voltage = msg.get("voltages", [0])[0] / 1000.0
                battery_current = msg.get("current_battery", 0) / 1000.0
                battery_remaining = msg.get("battery_remaining", -1)
        
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
        msg = mavlink_buffer.get("GPS_RAW_INT")
        gps_lat = 0.0
        gps_lon = 0.0
        if msg:
            gps_fix = msg.get("fix_type", 0)
            satellites = msg.get("satellites_visible", 0)
            gps_lat = msg.get("lat", 0) / 1e7
            gps_lon = msg.get("lon", 0) / 1e7

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
        msg = mavlink_buffer.get("SCALED_IMU")
        if msg:
            imu_temp = msg.get("temperature", 41.07) / 100.0

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
        
        # Get barometer data from MAVLink messages
        msg = mavlink_buffer.get("SCALED_PRESSURE")
        if msg:
            baro_pressure = msg.get("press_abs", 0)
            baro_temperature = msg.get("temperature", 0) / 100.0
            baro_status = "OK"

        # Check for altitude data
        msg = mavlink_buffer.get("VFR_HUD")
        if msg:
            baro_altitude = msg.get("alt", 0)
        
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

        # Check RC status from RC_CHANNELS message
        rc_params = params.get("RC", {})
        rc_msg = mavlink_buffer.get("RC_CHANNELS")
        rc_channels = []
        rc_rssi = 0
        rc_chancount = 0
        if rc_msg:
            for i in range(1, 17):
                rc_channels.append(rc_msg.get(f"chan{i}_raw", 0))
            rc_rssi = rc_msg.get("rssi", 0)
            rc_chancount = rc_msg.get("chancount", 0)
        else:
            rc_channels = [0] * 16

        # Determine RC status based on actual channel data
        rc_nonzero = sum(1 for v in rc_channels[:8] if v > 0)
        if rc_nonzero == 0:
            rc_status = "WARNING"
            rc_details = "No RC input detected"
        elif rc_nonzero < 4:
            rc_status = "WARNING"
            rc_details = f"{rc_nonzero}/8 channels active"
        else:
            rc_status = "OK"
            rc_details = f"{rc_chancount} channels, RSSI {rc_rssi}"

        subsystems.append({
            "component": "RC Channels",
            "status": rc_status,
            "details": rc_details
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
        msg = mavlink_buffer.get("SERVO_OUTPUT_RAW")
        if msg:
            motor_output_found = True
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

        # Also check for RC_CHANNELS message for stick inputs
        if not motor_output_found:
            msg = mavlink_buffer.get("RC_CHANNELS")
            if msg:
                throttle_value = msg.get("chan3_raw", 1000)
                throttle_percent = max(0, min(100, (throttle_value - 1000) / 10))
                for i in range(1, 5):
                    motors.append({
                        "id": i,
                        "output": int(throttle_percent),
                        "status": "OK" if throttle_value > 1010 else "OFF"
                    })
                motor_output_found = True
        
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
        
        # Determine armed state from HEARTBEAT
        is_armed = False
        hb_msg = mavlink_buffer.get("HEARTBEAT")
        if hb_msg:
            is_armed = bool(hb_msg.get("base_mode", 0) & 128)

        # Auto-toggle co-pilot mode based on armed state
        global copilot_active
        if copilot_user_override is not None:
            copilot_active = copilot_user_override
        else:
            copilot_active = is_armed

        # Extract ESC telemetry from ESC_TELEMETRY_1_TO_4 (real hardware)
        # or fall back to SERVO_OUTPUT_RAW (SITL / no ESC telemetry)
        esc_telemetry = []
        esc_msg = mavlink_buffer.get("ESC_TELEMETRY_1_TO_4")
        servo_msg = mavlink_buffer.get("SERVO_OUTPUT_RAW")
        if esc_msg:
            for i in range(4):
                esc_telemetry.append({
                    "motor": i + 1,
                    "rpm": esc_msg.get("rpm", [0]*4)[i] if isinstance(esc_msg.get("rpm"), list) else 0,
                    "temperature": esc_msg.get("temperature", [0]*4)[i] if isinstance(esc_msg.get("temperature"), list) else 0,
                    "voltage": (esc_msg.get("voltage", [0]*4)[i] if isinstance(esc_msg.get("voltage"), list) else 0) / 100.0,
                    "current": (esc_msg.get("current", [0]*4)[i] if isinstance(esc_msg.get("current"), list) else 0) / 100.0,
                })
        elif servo_msg:
            # Fallback: use SERVO_OUTPUT_RAW for motor output detection
            for i in range(4):
                servo_raw = servo_msg.get(f"servo{i+1}_raw", 1000)
                # Estimate "active" as RPM proxy: PWM > 1050 means motor is spinning
                esc_telemetry.append({
                    "motor": i + 1,
                    "rpm": servo_raw if servo_raw > 1050 else 0,
                    "temperature": 0,
                    "voltage": 0,
                    "current": 0,
                    "servo_raw": servo_raw,
                })
        else:
            for i in range(4):
                esc_telemetry.append({"motor": i + 1, "rpm": 0, "temperature": 0, "voltage": 0, "current": 0})

        # Determine ESC protocol from MOT_PWM_TYPE parameter
        mot_pwm_type_map = {
            0: "Normal PWM", 1: "OneShot", 2: "OneShot125", 3: "Brushed",
            4: "DShot150", 5: "DShot300", 6: "DShot600", 7: "DShot1200",
            8: "PWMRange", 9: "PWMAngle",
        }
        mot_pwm_type_val = int(params.get("Motors", {}).get("MOT_PWM_TYPE", 0))
        esc_protocol = mot_pwm_type_map.get(mot_pwm_type_val, f"Unknown ({mot_pwm_type_val})")

        # Decode RC protocol from RC_PROTOCOLS bitmask
        rc_protocol_map = {
            1: "All", 2: "PPM", 4: "IBUS", 8: "SBUS",
            32: "CRSF", 64: "FPORT", 256: "SRXL2", 512: "GHST",
        }
        rc_protocols_val = int(params.get("RC", {}).get("RC_PROTOCOLS", 1))
        rc_protocol_names = []
        for bit, name in rc_protocol_map.items():
            if rc_protocols_val & bit:
                rc_protocol_names.append(name)
        rc_protocol = ", ".join(rc_protocol_names) if rc_protocol_names else "Not configured"

        # Scan SERIAL0-7_PROTOCOL for RCIN (value 23) to identify the UART
        serial_params = params.get("Serial", {})
        rc_uart = "Not configured"
        for i in range(8):
            proto_val = int(serial_params.get(f"SERIAL{i}_PROTOCOL", -1))
            if proto_val == 23:
                rc_uart = f"SERIAL{i}"
                break

        # Extract ATTITUDE data (roll/pitch/yaw in degrees)
        attitude_msg = mavlink_buffer.get("ATTITUDE")
        if attitude_msg:
            attitude_roll = round(math.degrees(attitude_msg.get("roll", 0)), 1)
            attitude_pitch = round(math.degrees(attitude_msg.get("pitch", 0)), 1)
            attitude_yaw = round(math.degrees(attitude_msg.get("yaw", 0)), 1)
        else:
            attitude_roll = attitude_pitch = attitude_yaw = 0.0

        # Extract VFR_HUD data for altitude, heading, climb
        vfr_hud = mavlink_buffer.get("VFR_HUD")
        vfr_altitude = vfr_hud.get("alt", 0) if vfr_hud else 0
        vfr_heading = vfr_hud.get("heading", 0) if vfr_hud else 0
        vfr_climb = vfr_hud.get("climb", 0) if vfr_hud else 0
        vfr_groundspeed = vfr_hud.get("groundspeed", 0) if vfr_hud else 0
        vfr_airspeed = vfr_hud.get("airspeed", 0) if vfr_hud else 0

        # Extract servo outputs for motor visualization
        servo_raw = mavlink_buffer.get("SERVO_OUTPUT_RAW")
        servo_outputs = []
        if servo_raw:
            for i in range(1, 5):
                servo_outputs.append(servo_raw.get(f"servo{i}_raw", 1000))
        else:
            servo_outputs = [1000, 1000, 1000, 1000]

        # Update system health
        last_system_health = {
            "score": health_score,
            "critical_issues": critical_issues,
            "readiness": readiness,
            "armed": is_armed,
            "copilot_active": copilot_active,
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
                "status": gps_status,
                "lat": gps_lat,
                "lon": gps_lon
            },
            "motors": motors,
            "esc_telemetry": esc_telemetry,
            "esc_protocol": esc_protocol,
            "subsystems": subsystems,
            "rc_channels": rc_channels,
            "rc_rssi": rc_rssi,
            "rc_chancount": rc_chancount,
            "current_mode": hb_msg.get("custom_mode", 0) if hb_msg else 0,
            "fltmode_ch": int(params.get("Flight Modes", {}).get("FLTMODE_CH", 5)),
            "fltmodes": {i: int(params.get("Flight Modes", {}).get(f"FLTMODE{i}", 0)) for i in range(1, 7)},
            "rcmap": {
                "roll": int(rc_params.get("RCMAP_ROLL", 1)),
                "pitch": int(rc_params.get("RCMAP_PITCH", 2)),
                "throttle": int(rc_params.get("RCMAP_THROTTLE", 3)),
                "yaw": int(rc_params.get("RCMAP_YAW", 4)),
            },
            "rc_protocol": rc_protocol,
            "rc_uart": rc_uart,
            "attitude_roll": attitude_roll,
            "attitude_pitch": attitude_pitch,
            "attitude_yaw": attitude_yaw,
            "altitude": vfr_altitude,
            "heading": vfr_heading,
            "climb": vfr_climb,
            "groundspeed": vfr_groundspeed,
            "airspeed": vfr_airspeed,
            "servo_outputs": servo_outputs,
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

        if hasattr(validator, 'get_link_stats'):
            link_stats = validator.get_link_stats()
            last_system_health["link_stats"] = link_stats
        
        # Update parameter progress from validator
        if hasattr(validator, 'param_progress'):
            last_system_health["params"] = {
                "percentage": validator.param_progress,
                "downloaded": len(validator.params_dict),
                "total": validator.param_count
            }

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
        start = time.perf_counter()   # high-resolution timer
        try:
            # Update system health from validator data
            if validator and validator.hardware_validated and connected_clients:
                # Snapshot the 100-msg queue and build ai_mavlink_ctx
                validator.snapshot_rx_queue()

                # Copy the snapshot context to our local buffer
                global mavlink_buffer
                mavlink_buffer = validator.ai_mavlink_ctx.copy()

                # Update and broadcast system health
                update_system_health()

                # Flush the queue after we're done processing
                validator.flush_rx_queue()
                elapsed_ms = (time.perf_counter() - start) * 1000
#                print(f"[Telemetry Loop] {elapsed_ms:.2f} ms", flush=True)
                
                time.sleep(0.3)  # Sleep to avoid excessive updates

            else:
                time.sleep(1)  # Sleep to avoid excessive updates

        except Exception as e:
            logger.error(f"Error in telemetry update loop: {str(e)}")
            time.sleep(5)  # Sleep longer on error

#########################################################################
def start_server(validator_instance, jarvis, host='0.0.0.0', port=5000, debug=False, loggers=None, stt_recorder=None):
    """Start the Flask+SocketIO server in a new thread"""
    global validator, jarvis_module, llm_ai_module, telemetry_thread, logger, stt_module
    
    # Use provided logger if available
    if loggers and 'web_server' in loggers:
        logger = loggers['web_server']

    # Store references to backend components
    validator = validator_instance
    jarvis_module = jarvis
    stt_module = stt_recorder
    #llm_ai_module = llm_ai

    # Make sure the static directory exists
    static_dir = _resource_path('static')
    os.makedirs(static_dir, exist_ok=True)

    # Copy index.html to static directory if not already there
    index_path = os.path.join(static_dir, 'index.html')
    if not os.path.exists(index_path):
        logger.info("Copying index.html to static directory")
        source_index = os.path.join(_resource_path(''), 'index.html')
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
