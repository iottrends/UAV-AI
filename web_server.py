import sys
import os
import json
import logging
import math
import numpy as np
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

# Maintenance mode state
maintenance_mode = False

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
        if jarvis_module:
            jarvis_module.reset_session()
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
import dfu_flasher
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
# DFU Flashing (pyusb / STM32 DfuSe)
####################################################################################

@app.route('/api/firmware/dfu/detect', methods=['GET'])
def dfu_detect():
    """Scan USB for an STM32 DFU device and return board info."""
    device = dfu_flasher.find_dfu_device()
    if device is None:
        return jsonify({'status': 'not_found', 'message': 'No DFU device detected (0x0483:0xDF11)'})

    try:
        try:
            if device.is_kernel_driver_active(0):
                device.detach_kernel_driver(0)
        except (AttributeError, NotImplementedError):
            pass
        import usb.util as _uutil
        _uutil.claim_interface(device, 0)
        flasher = dfu_flasher.DfuFlasher()
        flasher._dev = device
        info = flasher.read_board_info()
        _uutil.release_interface(device, 0)
        _uutil.dispose_resources(device)
    except Exception as e:
        logger.warning(f"DFU detect board info error: {e}")
        info = {
            'vid': device.idVendor,
            'pid': device.idProduct,
            'vid_str': f'0x{device.idVendor:04X}',
            'pid_str': f'0x{device.idProduct:04X}',
            'manufacturer': dfu_flasher.get_manufacturer_from_vid(device.idVendor),
            'model': '',
            'usb_manufacturer': '',
            'usb_product': '',
        }

    return jsonify({'status': 'found', 'device': info})


@app.route('/api/firmware/dfu/enter', methods=['POST'])
def dfu_enter():
    """
    Trigger DFU entry automatically.
    1. MAVLink reboot-to-bootloader if connected.
    2. 1200-baud pulse on the serial port as fallback.
    Returns the method used so the UI can guide the user.
    """
    # Method 1: MAVLink (cleanest — board is already connected)
    if validator and validator.is_connected:
        ok = validator.reboot_to_bootloader()
        if ok:
            validator.disconnect()
            return jsonify({
                'status': 'success',
                'method': 'mavlink',
                'message': 'Reboot-to-bootloader command sent via MAVLink. '
                           'Waiting for DFU device to enumerate...',
            })

    # Method 2: 1200-baud pulse (board present but MAVLink not running)
    port = connection_params.get('port', '')
    if port and not port.startswith('udp'):
        ok = dfu_flasher.enter_dfu_via_1200baud(port)
        if ok:
            return jsonify({
                'status': 'success',
                'method': '1200baud',
                'message': f'1200-baud trigger sent on {port}. '
                           'Waiting for DFU device to enumerate...',
            })

    # Nothing worked — instruct user to do it manually
    return jsonify({
        'status': 'manual',
        'message': 'Could not trigger DFU automatically. '
                   'Hold the BOOT button and replug the USB cable.',
    })


@app.route('/api/firmware/dfu/flash', methods=['POST'])
def flash_firmware_dfu():
    """
    Start a DFU flash.  Accepts:
      - Multipart file upload  (.bin)
      - JSON body { "path": "/absolute/path/to/firmware.bin" }
    Streams progress via Socket.IO 'firmware_flash_progress' / 'firmware_flash_complete'.
    """
    global _flash_state

    with _flash_lock:
        if _flash_state.get('status') == 'flashing':
            return jsonify({'status': 'error', 'message': 'Flash already in progress'}), 409

    try:
        bin_data = None

        if 'file' in request.files:
            uploaded = request.files['file']
            if not uploaded.filename.lower().endswith('.bin'):
                return jsonify({'status': 'error', 'message': 'DFU flash requires a .bin file'}), 400
            bin_data = uploaded.read()
        else:
            payload  = request.get_json(force=True) or {}
            bin_path = payload.get('path', '')
            if not bin_path or not os.path.exists(bin_path):
                return jsonify({'status': 'error', 'message': 'No .bin file provided or file not found'}), 400
            with open(bin_path, 'rb') as fh:
                bin_data = fh.read()

        if not bin_data:
            return jsonify({'status': 'error', 'message': 'Empty firmware file'}), 400

        def do_dfu_flash():
            global _flash_state
            with _flash_lock:
                _flash_state = {
                    'status': 'flashing', 'stage': 'dfu_detect',
                    'percent': 0, 'message': 'Starting DFU flash...', 'result': None,
                }
            socketio.emit('firmware_flash_progress', {
                'stage': 'dfu_detect', 'percent': 0, 'message': 'Starting DFU flash...',
            })

            def progress_cb(stage, percent, message):
                with _flash_lock:
                    _flash_state['stage']   = stage
                    _flash_state['percent'] = percent
                    _flash_state['message'] = message
                socketio.emit('firmware_flash_progress', {
                    'stage': stage, 'percent': percent, 'message': message,
                })

            flasher = dfu_flasher.DfuFlasher()
            result  = flasher.flash(bin_data, progress_cb=progress_cb)

            with _flash_lock:
                _flash_state['status']  = 'complete' if result['success'] else 'error'
                _flash_state['message'] = result['message']
                _flash_state['result']  = result

            socketio.emit('firmware_flash_complete', result)

        threading.Thread(target=do_dfu_flash, daemon=True).start()
        return jsonify({'status': 'success', 'message': 'DFU flash started'})

    except Exception as e:
        logger.error(f"DFU flash start error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/api/firmware/dfu/download', methods=['POST'])
def dfu_download_bin():
    """
    Download a .bin firmware file from firmware.ardupilot.org.
    Derives the .bin URL from the .apj URL (same path, different extension).
    Accepts JSON body { "url": "https://firmware.ardupilot.org/...apj" }
                   or { "bin_url": "https://firmware.ardupilot.org/...bin" }
    """
    payload = request.get_json(force=True) or {}
    url = payload.get('bin_url') or payload.get('url', '').replace('.apj', '.bin')

    if not url.startswith('https://firmware.ardupilot.org/'):
        return jsonify({'status': 'error', 'message': 'URL must be from firmware.ardupilot.org'}), 400

    try:
        cache_dir  = _get_firmware_cache_dir()
        filename   = os.path.basename(url)
        if not filename.endswith('.bin'):
            filename += '.bin'
        local_path = os.path.join(cache_dir, filename)

        with urllib.request.urlopen(url, timeout=30) as resp:
            data = resp.read()

        with open(local_path, 'wb') as fh:
            fh.write(data)

        logger.info(f"Downloaded .bin firmware to {local_path} ({len(data)} bytes)")
        return jsonify({
            'status':   'success',
            'path':     local_path,
            'filename': filename,
            'size':     len(data),
        })

    except Exception as e:
        logger.error(f"DFU firmware download error: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


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

# Domain config schemas (Copter-first baseline)
_COPTER_MODE_IDS = {
    0, 1, 2, 3, 4, 5, 6, 7, 9, 11, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27
}
_SERIAL_PROTOCOL_IDS = {
    -1, 0, 1, 2, 3, 4, 5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20,
    21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39,
    40, 41, 42, 43, 44
}
_SERIAL_BAUD_IDS = {1, 2, 4, 9, 19, 38, 57, 111, 115, 230, 460, 500, 921, 1500}
_AUX_FUNCTION_CATALOG = {
    # General
    0: "Disabled",
    2: "Flip",
    3: "Simple Mode",
    4: "RTL",
    5: "Save Trim",
    7: "Save Waypoint",
    9: "Camera Trigger",
    10: "RangeFinder Enable",
    11: "Fence Enable",
    13: "Super Simple Mode",
    14: "Acro Trainer",
    16: "Auto Mode",
    17: "AutoTune",
    18: "Land",
    19: "Gripper",
    21: "Parachute Enable",
    22: "Parachute Release",
    23: "Parachute 3-Position",
    26: "GPS Disable",
    27: "Relay 1",
    28: "Relay 2",
    29: "Landing Gear",
    30: "Lost Copter Sound / Beeper",
    31: "Motor Emergency Stop",
    32: "Motor Interlock",
    33: "Brake with Yaw",
    34: "Relay 3",
    35: "Relay 4",
    36: "Heli External RSC",
    38: "Wind Vane Home Heading",
    39: "Limit Max Speed",
    40: "Proximity Avoidance",
    41: "Arm/Disarm",
    42: "Smart RTL",
    44: "Winch Enable",
    45: "Winch Control",
    46: "RC Override Enable",
    47: "User Function 1",
    48: "User Function 2",
    49: "User Function 3",
    52: "Acro Balance",
    55: "Guided Mode",
    56: "Loiter Enable",
    58: "Clear Waypoints",
    60: "ZigZag Mode",
    61: "ZigZag Save Waypoint",
    62: "Compass Learn",
    65: "GPS Disable Yaw",
    66: "Brake Mode",
    72: "Mount Lock",
    73: "Retract Mount",
    74: "Washer Pump",
    80: "Disarm",
    82: "Smart RTL or Disarm",
    84: "Arm/Disarm (AirSpeed Check)",
    90: "Camera Auto Focus Lock",
    94: "VTX Channel",
    95: "VTX Power",
    100: "Kill IMU2 (Heli)",
    101: "Camera Mode Toggle",
    105: "Generator",
    110: "EKF Source Select",
    150: "Scripting Function 1",
    151: "Scripting Function 2",
    152: "Scripting Function 3",
    153: "Scripting Function 4",
    154: "Scripting Function 5",
    155: "Scripting Function 6",
    156: "Scripting Function 7",
    157: "Scripting Function 8",
    158: "Scripting Function 9",
    159: "Scripting Function 10",
}

CONFIG_DOMAIN_SCHEMAS = {
    "serial_ports": {
        "requires_connected": True,
        "params": {
            **{f"SERIAL{i}_PROTOCOL": {"type": "int", "enum": _SERIAL_PROTOCOL_IDS, "default": 0} for i in range(8)},
            **{f"SERIAL{i}_BAUD": {"type": "int", "enum": _SERIAL_BAUD_IDS, "default": 57} for i in range(8)},
            **{f"SERIAL{i}_OPTIONS": {"type": "int", "min": 0, "max": 65535, "default": 0} for i in range(8)},
        },
        "warnings": ["Serial port changes may require FC reboot to take effect."],
    },
    "rc_mapping": {
        "requires_connected": True,
        "params": {
            "RCMAP_ROLL": {"type": "int", "min": 1, "max": 16, "default": 1},
            "RCMAP_PITCH": {"type": "int", "min": 1, "max": 16, "default": 2},
            "RCMAP_THROTTLE": {"type": "int", "min": 1, "max": 16, "default": 3},
            "RCMAP_YAW": {"type": "int", "min": 1, "max": 16, "default": 4},
        },
        "warnings": [],
    },
    "flight_modes": {
        "requires_connected": True,
        "params": {
            "FLTMODE_CH": {"type": "int", "min": 1, "max": 16, "default": 5},
            **{f"FLTMODE{i}": {"type": "int", "enum": _COPTER_MODE_IDS, "default": 0} for i in range(1, 7)},
        },
        "warnings": [],
    },
    "failsafe": {
        "requires_connected": True,
        "params": {
            "FS_THR_ENABLE": {"type": "int", "enum": {0, 1, 2}, "default": 1},
            "FS_THR_VALUE": {"type": "int", "min": 800, "max": 2200, "default": 975},
            "FS_GCS_ENABLE": {"type": "int", "enum": {0, 1, 2}, "default": 0},
            "FS_OPTIONS": {"type": "int", "min": 0, "max": 65535, "default": 0},
            "BATT_FS_LOW_ACT": {"type": "int", "enum": {0, 1, 2, 3, 4}, "default": 2},
            "BATT_FS_CRT_ACT": {"type": "int", "enum": {0, 1, 2, 3, 4}, "default": 1},
        },
        "warnings": [],
    },
    "aux_functions": {
        "requires_connected": True,
        "params": {
            **{f"RC{i}_OPTION": {"type": "int", "min": 0, "max": 300, "default": 0} for i in range(7, 13)},
        },
        "warnings": ["Aux functions typically activate when channel PWM is above 1800."],
    },
}
_config_apply_lock = threading.Lock()


def _to_number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _values_equal(left, right, tolerance=0.0001):
    left_num = _to_number(left)
    right_num = _to_number(right)
    if left_num is None or right_num is None:
        return left == right
    return abs(left_num - right_num) <= tolerance


def _get_domain_params(domain):
    schema = CONFIG_DOMAIN_SCHEMAS[domain]
    params = {}
    for name, spec in schema["params"].items():
        if validator and hasattr(validator, "params_dict") and name in validator.params_dict:
            params[name] = validator.params_dict.get(name)
        else:
            params[name] = spec.get("default")
    return params


def _normalize_and_validate_domain_changes(domain, raw_changes):
    schema = CONFIG_DOMAIN_SCHEMAS[domain]
    normalized = {}
    invalid = []
    warnings = list(schema.get("warnings", []))

    for param, raw_value in raw_changes.items():
        spec = schema["params"].get(param)
        if not spec:
            invalid.append({"param": param, "reason": "unknown parameter for this domain"})
            continue

        num = _to_number(raw_value)
        if num is None:
            invalid.append({"param": param, "reason": "value must be numeric"})
            continue

        if spec.get("type") == "int":
            if abs(num - round(num)) > 1e-6:
                invalid.append({"param": param, "reason": "value must be an integer"})
                continue
            value = int(round(num))
        else:
            value = float(num)

        enum_values = spec.get("enum")
        if enum_values is not None and value not in enum_values:
            invalid.append({"param": param, "reason": f"value {value} not in allowed set"})
            continue

        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is not None and value < min_val:
            invalid.append({"param": param, "reason": f"value {value} below minimum {min_val}"})
            continue
        if max_val is not None and value > max_val:
            invalid.append({"param": param, "reason": f"value {value} above maximum {max_val}"})
            continue

        normalized[param] = value

    if invalid:
        return normalized, invalid, warnings

    merged = _get_domain_params(domain)
    merged.update(normalized)

    if domain == "rc_mapping":
        mapped = [merged["RCMAP_ROLL"], merged["RCMAP_PITCH"], merged["RCMAP_THROTTLE"], merged["RCMAP_YAW"]]
        if len(set(mapped)) != len(mapped):
            invalid.append({"param": "RCMAP_*", "reason": "roll/pitch/throttle/yaw channels must be unique"})

    if domain == "failsafe":
        if merged["FS_THR_ENABLE"] > 0 and not (800 <= merged["FS_THR_VALUE"] <= 2200):
            invalid.append({"param": "FS_THR_VALUE", "reason": "must be 800-2200 when FS_THR_ENABLE is enabled"})

    return normalized, invalid, warnings


@app.route('/api/config/domains/<domain>', methods=['GET'])
def get_config_domain(domain):
    """Return current FC values for a configuration domain."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500
    if domain not in CONFIG_DOMAIN_SCHEMAS:
        return jsonify({"status": "error", "message": f"Unknown domain: {domain}"}), 404
    if not validator.params_dict:
        return jsonify({"status": "error", "message": "No parameters loaded from drone"}), 400

    schema = CONFIG_DOMAIN_SCHEMAS[domain]
    if schema.get("requires_connected") and not validator.is_connected:
        return jsonify({"status": "error", "message": "Drone not connected"}), 400

    return jsonify({
        "status": "success",
        "domain": domain,
        "vehicle_type": "Copter",
        "params": _get_domain_params(domain),
        "metadata": {
            "editable": bool(validator.is_connected),
            "source": "fc",
            "warnings": schema.get("warnings", []),
            "function_catalog": _AUX_FUNCTION_CATALOG if domain == "aux_functions" else {},
            "activation_pwm": 1800 if domain == "aux_functions" else None,
        }
    })


@app.route('/api/config/domains/<domain>/preview', methods=['POST'])
def preview_config_domain_changes(domain):
    """Validate and compute diff for domain changes without writing to FC."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500
    if domain not in CONFIG_DOMAIN_SCHEMAS:
        return jsonify({"status": "error", "message": f"Unknown domain: {domain}"}), 404

    payload = request.get_json(force=True) or {}
    changes = payload.get("changes")
    if not isinstance(changes, dict):
        return jsonify({"status": "error", "message": "'changes' must be an object"}), 400

    normalized, invalid, warnings = _normalize_and_validate_domain_changes(domain, changes)
    if invalid:
        return jsonify({
            "status": "error",
            "domain": domain,
            "invalid": invalid,
            "warnings": warnings,
        }), 400

    current = _get_domain_params(domain)
    diff = []
    for param, new_val in normalized.items():
        old_val = current.get(param)
        changed = not _values_equal(old_val, new_val)
        diff.append({
            "param": param,
            "old": old_val,
            "new": new_val,
            "changed": changed,
        })

    diff.sort(key=lambda x: x["param"])
    has_changes = any(item["changed"] for item in diff)

    return jsonify({
        "status": "success",
        "domain": domain,
        "diff": diff,
        "has_changes": has_changes,
        "warnings": warnings,
        "invalid": [],
    })


@app.route('/api/config/domains/<domain>/apply', methods=['POST'])
def apply_config_domain_changes(domain):
    """Write domain changes to FC and verify read-back values."""
    if not validator:
        return jsonify({"status": "error", "message": "Backend not initialized"}), 500
    if domain not in CONFIG_DOMAIN_SCHEMAS:
        return jsonify({"status": "error", "message": f"Unknown domain: {domain}"}), 404
    if not validator.is_connected:
        return jsonify({"status": "error", "message": "Drone not connected"}), 400

    payload = request.get_json(force=True) or {}
    changes = payload.get("changes")
    if not isinstance(changes, dict):
        return jsonify({"status": "error", "message": "'changes' must be an object"}), 400
    tolerance = float(payload.get("tolerance", 0.0001))
    verify_timeout_ms = int(payload.get("verify_timeout_ms", 5000))

    normalized, invalid, warnings = _normalize_and_validate_domain_changes(domain, changes)
    if invalid:
        return jsonify({
            "status": "error",
            "domain": domain,
            "invalid": invalid,
            "warnings": warnings,
        }), 400

    current = _get_domain_params(domain)
    changed = {k: v for k, v in normalized.items() if not _values_equal(current.get(k), v, tolerance)}
    unchanged_count = len(normalized) - len(changed)
    if not changed:
        return jsonify({
            "status": "success",
            "domain": domain,
            "applied": 0,
            "verified": 0,
            "unchanged": unchanged_count,
            "failed": [],
            "mismatched": [],
            "warnings": warnings,
            "duration_ms": 0,
        })

    started = time.time()
    failed = []
    sent = {}
    with _config_apply_lock:
        for param, value in changed.items():
            try:
                ok = validator.update_parameter(param, value)
            except Exception as e:
                ok = False
                logger.error(f"Error updating parameter {param}: {e}")
            if ok:
                sent[param] = value
            else:
                failed.append({"param": param, "reason": "send_failed"})

    deadline = time.time() + (verify_timeout_ms / 1000.0)
    pending = dict(sent)
    verified = []
    while pending and time.time() < deadline:
        to_remove = []
        for param, expected in pending.items():
            actual = validator.params_dict.get(param)
            if _values_equal(actual, expected, tolerance):
                verified.append(param)
                to_remove.append(param)
        for param in to_remove:
            pending.pop(param, None)
        if pending:
            time.sleep(0.1)

    mismatched = []
    for param, expected in pending.items():
        mismatched.append({
            "param": param,
            "expected": expected,
            "actual": validator.params_dict.get(param),
            "reason": "verify_timeout_or_mismatch",
        })

    duration_ms = int((time.time() - started) * 1000)
    success = not failed and not mismatched
    result = {
        "status": "success" if success else "partial",
        "domain": domain,
        "applied": len(sent),
        "verified": len(verified),
        "unchanged": unchanged_count,
        "failed": failed,
        "mismatched": mismatched,
        "warnings": warnings,
        "duration_ms": duration_ms,
    }
    return jsonify(result), (200 if success else 207)


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


@app.route('/api/log_summary', methods=['GET'])
def get_log_flight_summary():
    """Return computed flight stats from parsed log — no LLM call."""
    if not log_parser_instance or not log_parser_instance._is_parsed:
        return jsonify({'status': 'error', 'message': 'No log loaded'}), 400

    pd = log_parser_instance.parsed_data
    stats = {}

    # Flight duration — first and last TimeUS from any high-rate message
    for mtype in ('ATT', 'GPS', 'BARO', 'RCOU'):
        msgs = pd.get(mtype, [])
        if len(msgs) >= 2:
            t0 = msgs[0].get('TimeUS') or 0
            t1 = msgs[-1].get('TimeUS') or 0
            if t1 > t0:
                stats['duration_s'] = round((t1 - t0) / 1e6)
                break

    # Max altitude from BARO
    baro_msgs = pd.get('BARO', [])
    if baro_msgs:
        alts = [m.get('Alt') for m in baro_msgs if m.get('Alt') is not None]
        if alts:
            stats['max_alt_m'] = round(max(alts), 1)

    # GPS fix quality — GPS.Status: 0=no GPS, 1=no fix, 2=2D, 3=3D, 4=DGPS, 5=RTK float, 6=RTK fixed
    gps_msgs = pd.get('GPS', [])
    if gps_msgs:
        statuses = [m.get('Status', 0) for m in gps_msgs if m.get('Status') is not None]
        if statuses:
            stats['gps_fix'] = '3D Fix' if max(statuses) >= 3 else ('2D Fix' if max(statuses) == 2 else 'No Fix')
            if min(statuses) < 3 and max(statuses) >= 3:
                stats['gps_dropout'] = True

    # Vibration peaks — flag if any axis exceeds 30 m/s²
    vibe_alerts = []
    for m in pd.get('VIBE', []):
        for ax in ('VibeX', 'VibeY', 'VibeZ'):
            v = m.get(ax)
            if v is not None and v > 30:
                t_s = round((m.get('TimeUS') or 0) / 1e6)
                vibe_alerts.append({'axis': ax, 'value': round(v, 1), 'time_s': t_s})
                break  # one alert per message
    if vibe_alerts:
        # Return up to 3 worst (highest value)
        vibe_alerts.sort(key=lambda x: x['value'], reverse=True)
        stats['vibe_alerts'] = vibe_alerts[:3]

    # Battery — min and start voltage
    bat_msgs = pd.get('BAT', pd.get('CURR', []))
    if bat_msgs:
        volts = [m.get('Volt') for m in bat_msgs if m.get('Volt') is not None]
        if volts:
            stats['min_volt'] = round(min(volts), 2)
            stats['start_volt'] = round(volts[0], 2)

    # Flight modes used
    mode_msgs = pd.get('MODE', [])
    if mode_msgs:
        modes = []
        for m in mode_msgs:
            mode = m.get('Mode') or m.get('ModeNum')
            if mode is not None and mode not in modes:
                modes.append(mode)
        if modes:
            stats['modes'] = modes

    # Errors / failsafes
    errors = []
    for m in pd.get('ERR', []):
        ecode = m.get('ECode', 0)
        if ecode:
            t_s = round((m.get('TimeUS') or 0) / 1e6)
            errors.append({'subsys': m.get('Subsys', '?'), 'ecode': ecode, 'time_s': t_s})
    if errors:
        stats['errors'] = errors[:5]

    return jsonify({'status': 'success', 'stats': stats})


@app.route('/api/magfit', methods=['GET'])
def get_magfit():
    """
    Compute COMPASS_MOT_X/Y/Z coefficients from the loaded flight log.

    Method:
      1. Rotate each MAG sample into Earth frame using ATT roll+pitch (removes
         tilt-induced variation so only motor interference remains).
      2. Independent variable: Battery current (A) when available; falls back
         to mean normalised RCOU throttle (0-1). Current is the physically
         correct variable because the magnetic field scales with Amps.
      3. Fit  earth_mag = k * ind + c  per axis via numpy.linalg.lstsq.
         The slope  k  is the COMPASS_MOT coefficient.
      4. Return raw and corrected scatter points so the UI can preview the
         "before / after" improvement before the pilot applies the fix.
    """
    if not log_parser_instance or not log_parser_instance._is_parsed:
        return jsonify({'status': 'error', 'message': 'No log loaded'}), 400

    pd = log_parser_instance.parsed_data

    mag_msgs  = pd.get('MAG', [])
    att_msgs  = pd.get('ATT', [])
    rcou_msgs = pd.get('RCOU', [])
    bat_msgs  = pd.get('BAT', pd.get('CURR', []))

    if not mag_msgs:
        return jsonify({'status': 'error', 'message': 'No MAG messages in log'}), 400
    if not att_msgs:
        return jsonify({'status': 'error', 'message': 'No ATT messages in log'}), 400
    if not rcou_msgs and not bat_msgs:
        return jsonify({'status': 'error', 'message': 'No RCOU or BAT/CURR messages in log'}), 400

    # ── Build fast time-sorted lookup arrays ──────────────────────────────
    def _sort(msgs):
        return sorted(msgs, key=lambda m: m.get('TimeUS', 0))

    att_sorted  = _sort(att_msgs)
    att_times   = [m.get('TimeUS', 0) for m in att_sorted]

    use_current = False
    ind_sorted  = []
    ind_times   = []
    if bat_msgs:
        # Check that current field exists and has useful range
        sample_curr = [m.get('Curr', m.get('I')) for m in bat_msgs[:50] if m.get('Curr', m.get('I')) is not None]
        if sample_curr and max(sample_curr) > 0.5:
            use_current = True
            ind_sorted = _sort(bat_msgs)
            ind_times  = [m.get('TimeUS', 0) for m in ind_sorted]

    if not use_current and rcou_msgs:
        ind_sorted = _sort(rcou_msgs)
        ind_times  = [m.get('TimeUS', 0) for m in ind_sorted]

    if not ind_sorted:
        return jsonify({'status': 'error', 'message': 'No usable current or throttle data in log'}), 400

    ind_label = 'Current (A)' if use_current else 'Throttle (0-1)'

    # ── Nearest-neighbour lookup via binary search ────────────────────────
    import bisect

    def _nearest(sorted_times, sorted_msgs, t, *fields):
        idx = bisect.bisect_left(sorted_times, t)
        idx = min(idx, len(sorted_msgs) - 1)
        if idx > 0 and abs(sorted_times[idx-1] - t) < abs(sorted_times[idx] - t):
            idx -= 1
        m = sorted_msgs[idx]
        return tuple(m.get(f) for f in fields)

    # ── Process each MAG sample ───────────────────────────────────────────
    samples = []
    for mag in mag_msgs:
        t  = mag.get('TimeUS', 0)
        mx = mag.get('MagX')
        my = mag.get('MagY')
        mz = mag.get('MagZ')
        if mx is None or my is None or mz is None:
            continue

        roll_deg, pitch_deg = _nearest(att_times, att_sorted, t, 'Roll', 'Pitch')
        if roll_deg is None or pitch_deg is None:
            continue

        roll  = math.radians(float(roll_deg))
        pitch = math.radians(float(pitch_deg))

        # Rotate body → Earth (remove roll + pitch tilt, ignore yaw since
        # Earth-field horizontal direction is constant during the flight)
        cp, sp = math.cos(pitch), math.sin(pitch)
        cr, sr = math.cos(roll),  math.sin(roll)
        ex =  cp*mx + sp*sr*my + sp*cr*mz
        ey =          cr*my    -    sr*mz
        ez = -sp*mx + cp*sr*my + cp*cr*mz

        # Independent variable
        if use_current:
            (raw_curr,) = _nearest(ind_times, ind_sorted, t, 'Curr')
            if raw_curr is None:
                (raw_curr,) = _nearest(ind_times, ind_sorted, t, 'I')
            ind_val = float(raw_curr) if raw_curr is not None else None
        else:
            c1, c2, c3, c4 = _nearest(ind_times, ind_sorted, t, 'C1', 'C2', 'C3', 'C4')
            vals = [v for v in (c1, c2, c3, c4) if v is not None]
            if not vals:
                continue
            ind_val = (sum(vals) / len(vals) - 1000.0) / 1000.0  # 0-1

        if ind_val is None or ind_val < 0:
            continue

        samples.append((float(ind_val), float(ex), float(ey), float(ez)))

    if len(samples) < 50:
        return jsonify({
            'status': 'error',
            'message': f'Insufficient aligned samples ({len(samples)}). '
                       'Log needs both MAG and throttle/current data across a range of throttle levels.'
        }), 400

    # ── Least-squares fit: earth_mag = k * ind + c ───────────────────────
    ind_arr = np.array([s[0] for s in samples])
    ex_arr  = np.array([s[1] for s in samples])
    ey_arr  = np.array([s[2] for s in samples])
    ez_arr  = np.array([s[3] for s in samples])

    A = np.column_stack([ind_arr, np.ones(len(samples))])  # [ind, 1]

    def _lstsq(Y):
        coefs, _, _, _ = np.linalg.lstsq(A, Y, rcond=None)
        pred   = A @ coefs
        ss_res = float(np.sum((Y - pred) ** 2))
        ss_tot = float(np.sum((Y - float(np.mean(Y))) ** 2))
        r2     = 1.0 - ss_res / ss_tot if ss_tot > 1e-9 else 0.0
        return float(coefs[0]), float(coefs[1]), round(r2, 3)

    k_x, c_x, r2_x = _lstsq(ex_arr)
    k_y, c_y, r2_y = _lstsq(ey_arr)
    k_z, c_z, r2_z = _lstsq(ez_arr)

    avg_r2  = (r2_x + r2_y + r2_z) / 3.0
    quality = 'good' if avg_r2 > 0.6 else ('fair' if avg_r2 > 0.3 else 'poor')

    # ── Downsample scatter to ≤ 600 points for the chart ─────────────────
    max_pts = 600
    step    = max(1, len(samples) // max_pts)
    scatter = []
    for i in range(0, len(samples), step):
        ind, ex, ey, ez = samples[i]
        scatter.append({
            'ind':    round(ind, 3),
            'raw_x':  round(ex, 2),
            'raw_y':  round(ey, 2),
            'raw_z':  round(ez, 2),
            'corr_x': round(ex - k_x * ind, 2),
            'corr_y': round(ey - k_y * ind, 2),
            'corr_z': round(ez - k_z * ind, 2),
        })

    return jsonify({
        'status':       'success',
        'k_x':          round(k_x, 4),
        'k_y':          round(k_y, 4),
        'k_z':          round(k_z, 4),
        'r2_x':         r2_x,
        'r2_y':         r2_y,
        'r2_z':         r2_z,
        'quality':      quality,
        'sample_count': len(samples),
        'use_current':  use_current,
        'ind_label':    ind_label,
        'ind_max':      round(float(np.max(ind_arr)), 2),
        'scatter':      scatter,
    })


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

        # Hardware inventory — detect sensor models from device IDs + system stats
        _GYRO_TYPES = {
            0x01: "ICM-42688", 0x02: "ICM-42605", 0x09: "BMI160",
            0x10: "L3GD20",    0x11: "ICM-20608", 0x16: "ICM-20689",
            0x17: "ICM-20602", 0x21: "MPU-6000",  0x24: "MPU-9250",
            0x28: "BMI270",    0x2B: "ICM-42670",  0x32: "ICM-45686",
        }
        _BARO_TYPES = {
            0x01: "BMP280",  0x02: "BMP388",   0x03: "DPS310",
            0x04: "MS5611",  0x05: "MS5607",   0x06: "TSYS01",
            0x07: "ICP-10111", 0x09: "SPL06",  0x0A: "BMP390",
        }
        _COMPASS_TYPES = {
            0x01: "HMC5843", 0x02: "HMC5883",  0x03: "LSM303D",
            0x04: "IST8310", 0x05: "LSM9DS1",  0x06: "AK8963",
            0x07: "RM3100",  0x08: "QMC5883",  0x09: "AK09916",
            0x0A: "MMC3416", 0x0C: "IST8308",  0x0D: "MMC5883",
        }
        _GPS_TYPES = {
            0: "None", 1: "Auto", 2: "uBlox", 3: "MTK", 4: "MTK19",
            5: "NMEA", 6: "SiRF", 7: "HIL", 8: "SwiftNav", 9: "DroneCAN",
            11: "NTRIP", 14: "HERE", 15: "BLH", 16: "uBlox-MB",
            17: "NOVA", 19: "Unicore", 21: "HERE3", 24: "Trimble",
        }

        def _devid_name(devid, lookup):
            try:
                devid = int(float(devid or 0))
            except (TypeError, ValueError):
                devid = 0
            if devid == 0:
                return "—"
            bus_type = devid & 0x3F          # bits 0-5: 1=I2C 2=SPI 3=UAVCAN 4=SITL
            dev_type = (devid >> 18) & 0x3F  # bits 18-23: chip model
            if bus_type == 4:
                return "Simulated"
            return lookup.get(dev_type, f"ID:{devid:#010x}")

        flat_params = getattr(validator, 'params_dict', {})
        try:
            gps_type_val = int(float(flat_params.get("GPS_TYPE", 0) or 0))
        except (TypeError, ValueError):
            gps_type_val = 0
        hardware_inventory = {
            "imu":           _devid_name(flat_params.get("INS_GYRO_ID", 0), _GYRO_TYPES),
            "baro":          _devid_name(flat_params.get("BARO1_DEVID", 0), _BARO_TYPES),
            "compass":       _devid_name(flat_params.get("COMPASS_DEV_ID", 0), _COMPASS_TYPES),
            "gps":           _GPS_TYPES.get(gps_type_val, f"Type {gps_type_val}"),
            "esc_protocol":  esc_protocol,
            "cpu_load":      None,
            "free_ram_kb":   None,
            "drop_rate":     None,
            "flash_total_mb": None,
            "flash_used_mb":  None,
        }
        sys_msg = mavlink_buffer.get("SYS_STATUS")
        if sys_msg:
            raw_load = sys_msg.get("load", 0)
            hardware_inventory["cpu_load"]  = round(raw_load / 10.0, 1)
            hardware_inventory["drop_rate"] = round(sys_msg.get("drop_rate_comm", 0) / 100.0, 1)
        mem_msg = mavlink_buffer.get("MEMINFO")
        if mem_msg:
            hardware_inventory["free_ram_kb"] = round(mem_msg.get("freemem", 0) / 1024)
        storage_msg = mavlink_buffer.get("STORAGE_INFORMATION")
        if storage_msg:
            hardware_inventory["flash_total_mb"] = round(storage_msg.get("total_capacity", 0))
            hardware_inventory["flash_used_mb"]  = round(storage_msg.get("used_capacity", 0))

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

        # ── Pre-flight readiness checks ───────────────────────────────────
        preflight_checks = []

        # GPS
        if gps_fix >= 3:
            preflight_checks.append({"id": "gps", "label": "GPS Lock", "status": "ok",
                "detail": f"{satellites} sats, Fix {gps_fix}"})
        elif gps_fix > 0:
            preflight_checks.append({"id": "gps", "label": "GPS Lock", "status": "warn",
                "detail": f"{satellites} sats, Fix {gps_fix} (need 3D fix)"})
        else:
            preflight_checks.append({"id": "gps", "label": "GPS Lock", "status": "fail",
                "detail": "No GPS fix"})

        # Battery
        if battery_voltage <= 0:
            preflight_checks.append({"id": "batt", "label": "Battery", "status": "warn",
                "detail": "No battery data"})
        elif battery_status == "CRITICAL":
            preflight_checks.append({"id": "batt", "label": "Battery", "status": "fail",
                "detail": f"{battery_voltage:.2f}V — CRITICAL"})
        elif battery_status == "WARNING":
            preflight_checks.append({"id": "batt", "label": "Battery", "status": "warn",
                "detail": f"{battery_voltage:.2f}V — LOW"})
        else:
            preflight_checks.append({"id": "batt", "label": "Battery", "status": "ok",
                "detail": f"{battery_voltage:.2f}V ({battery_percentage}%)"})

        # Compass
        if compass_enabled and compass_use:
            preflight_checks.append({"id": "compass", "label": "Compass", "status": "ok",
                "detail": "Enabled & calibrated"})
        else:
            preflight_checks.append({"id": "compass", "label": "Compass", "status": "fail",
                "detail": "Disabled (COMPASS_USE=0)"})

        # RC signal
        if rc_nonzero >= 4:
            preflight_checks.append({"id": "rc", "label": "RC Signal", "status": "ok",
                "detail": f"{rc_chancount} channels active"})
        elif rc_nonzero > 0:
            preflight_checks.append({"id": "rc", "label": "RC Signal", "status": "warn",
                "detail": f"Only {rc_nonzero} channels active"})
        else:
            preflight_checks.append({"id": "rc", "label": "RC Signal", "status": "fail",
                "detail": "No RC input detected"})

        # Armed state
        if is_armed:
            preflight_checks.append({"id": "arm", "label": "Armed State", "status": "warn",
                "detail": "ARMED"})
        else:
            preflight_checks.append({"id": "arm", "label": "Armed State", "status": "ok",
                "detail": "Disarmed (safe)"})

        # Pre-arm STATUSTEXT errors
        statustext_msg = mavlink_buffer.get("STATUSTEXT")
        arm_error = ""
        if statustext_msg:
            txt = statustext_msg.get("text", "")
            if "PreArm" in txt or "PreARM" in txt:
                arm_error = txt.strip()
        if arm_error:
            preflight_checks.append({"id": "prearm", "label": "Pre-arm Check", "status": "fail",
                "detail": arm_error})
        else:
            preflight_checks.append({"id": "prearm", "label": "Pre-arm Check", "status": "ok",
                "detail": "No errors"})

        pf_fails = sum(1 for c in preflight_checks if c["status"] == "fail")
        pf_warns = sum(1 for c in preflight_checks if c["status"] == "warn")
        overall_readiness = "NOT READY" if pf_fails > 0 else ("CAUTION" if pf_warns > 0 else "READY")

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
            "preflight": preflight_checks,
            "overall_readiness": overall_readiness,
            "hardware": hardware_inventory,
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
            downloaded = len(validator.params_dict)
            total = validator.param_count
            progress = validator.param_progress
            if total > 0 and downloaded >= total:
                param_status = "Complete"
            elif progress > 0:
                param_status = "Downloading..."
            else:
                param_status = "Not Started"
            last_system_health["params"] = {
                "percentage": progress,
                "downloaded": downloaded,
                "total": total,
                "status": param_status
            }

        # Broadcast system health to all connected clients
        if connected_clients:
            socketio.emit('system_status', last_system_health)

    except Exception as e:
        logger.error(f"Error updating system health: {str(e)}")
        logger.exception("Exception details:")  # Log the full traceback

#####################################################################
######################################################################

def _fast_telemetry_loop():
    """Emit attitude + RC data at 20 Hz for smooth HUD/RC-tab updates.

    Snapshots the MAVLink queue on every cycle (no flush — flush belongs to
    the slow loop) so the data is always fresh from the serial stream.
    Emits a lightweight 'attitude' SocketIO event consumed by drone-view and
    the RC tab without touching the heavy health-check path.
    """
    FAST_INTERVAL = 0.05   # 20 Hz = 50 ms
    logger.info("Starting fast telemetry loop (20 Hz)")
    while True:
        t0 = time.perf_counter()
        try:
            if validator and validator.hardware_validated and connected_clients:
                # Fresh snapshot — no flush; _process_message clears at 100
                validator.snapshot_rx_queue()
                ctx = validator.ai_mavlink_ctx

                attitude = ctx.get("ATTITUDE")
                vfr      = ctx.get("VFR_HUD")
                servo    = ctx.get("SERVO_OUTPUT_RAW")
                rc       = ctx.get("RC_CHANNELS")

                roll = pitch = yaw = 0.0
                if attitude:
                    roll  = round(math.degrees(attitude.get("roll",  0)), 1)
                    pitch = round(math.degrees(attitude.get("pitch", 0)), 1)
                    yaw   = round(math.degrees(attitude.get("yaw",   0)), 1)

                rc_channels  = [rc.get(f"chan{i}_raw", 0) for i in range(1, 17)] if rc else [0] * 16
                rc_rssi      = rc.get("rssi",     0) if rc else 0
                rc_chancount = rc.get("chancount", 0) if rc else 0

                servo_outputs = [servo.get(f"servo{i}_raw", 1000) for i in range(1, 5)] if servo else [1000] * 4

                socketio.emit('attitude', {
                    "attitude_roll":  roll,
                    "attitude_pitch": pitch,
                    "attitude_yaw":   yaw,
                    "altitude":    vfr.get("alt",         0) if vfr else 0,
                    "heading":     vfr.get("heading",     0) if vfr else 0,
                    "climb":       vfr.get("climb",       0) if vfr else 0,
                    "groundspeed": vfr.get("groundspeed", 0) if vfr else 0,
                    "airspeed":    vfr.get("airspeed",    0) if vfr else 0,
                    "rc_channels":   rc_channels,
                    "rc_rssi":       rc_rssi,
                    "rc_chancount":  rc_chancount,
                    "servo_outputs": servo_outputs,
                })
        except Exception as e:
            logger.error(f"Fast telemetry error: {e}")

        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, FAST_INTERVAL - elapsed))


def telemetry_update_loop():
    """Full system-health snapshot and broadcast at 2 Hz (slow path).

    Reads from ai_mavlink_ctx (persistent LKV cache updated by snapshot_rx_queue)
    and emits the heavyweight 'system_status' event: battery, GPS, params,
    subsystems, ESC telemetry, flight modes, RC map, etc.
    The deque is NOT flushed here — _process_message clears it after every
    100-message batch write to the traffic log.
    """
    SLOW_INTERVAL = 0.5   # 2 Hz = 500 ms
    logger.info("Starting slow telemetry loop (2 Hz)")
    while True:
        t0 = time.perf_counter()
        try:
            if validator and validator.hardware_validated and connected_clients:
                # Snapshot and copy to shared mavlink_buffer
                validator.snapshot_rx_queue()
                global mavlink_buffer
                mavlink_buffer = validator.ai_mavlink_ctx.copy()

                # Full health computation + broadcast
                update_system_health()

                # No flush here — _process_message owns the flush trigger.
                # It writes 100 messages as a batch then clears the deque.
                # Calling flush_rx_queue() every 500ms would prevent the
                # deque from ever reaching 100, breaking the traffic log.
            else:
                time.sleep(1)
                continue

        except Exception as e:
            logger.error(f"Error in telemetry update loop: {str(e)}")
            time.sleep(5)
            continue

        elapsed = time.perf_counter() - t0
        time.sleep(max(0.0, SLOW_INTERVAL - elapsed))

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

    # Start fast (20 Hz attitude) and slow (2 Hz health) telemetry threads
    fast_thread = threading.Thread(target=_fast_telemetry_loop, daemon=True)
    fast_thread.start()
    telemetry_thread = threading.Thread(target=telemetry_update_loop, daemon=True)
    telemetry_thread.start()

    # Start the Flask server in a new thread
    def run_server():
        logger.info(f"Starting web server on {host}:{port}")
        socketio.run(app, host=host, port=port, debug=debug, use_reloader=False, allow_unsafe_werkzeug=True)

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    logger.info("Web server started")
    return server_thread


if __name__ == "__main__":
    # This allows running the web server standalone for testing
    print("Starting web server in standalone mode (no backend)")
    socketio.run(app, host='127.0.0.1', port=5000, debug=True, use_reloader=False, allow_unsafe_werkzeug=True)
