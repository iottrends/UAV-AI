import logging
import threading
import time
import sys
import serial
import os
from pymavlink import mavutil
import JARVIS
from collections import deque

# Update: Get the mavlink logger from root when available
mavlink_logger = logging.getLogger('mavlink')
class MavlinkHandler:
    def __init__(self):
        self.mav_conn = None
        self.ws_uri = None
        self.params_dict = {}
        self.param_done = 0
        self.last_heartbeat = 0
        self.heartbeat_timeout_flag = False
        self.is_connected = False
        self.param_count = 0
        self.param_progress = 0  # Track parameter download progress percentage
        self.target_system = None
        self.target_component = None
        self.log_directory = "blackbox_logs"
        self.log_list = []  # store log Ids from Log_Entry messages
        os.makedirs(self.log_directory, exist_ok=True)
        self.rx_mav_msg = list(range (100)) # rx mavlink list size 100
        self.ai_mavlink_ctx = deque(maxlen=10)
        self.tx_mav_msg = [] # store all tx mavlink msg.
        self.last_dump_time = time.time()

    def connect(self, port_name, baudrate):
        """Establish connection to flight controller."""
        try:
            if port_name.startswith("ws://") or port_name.startswith("wss://"):
                mavlink_logger.info(f"Connecting via websocket:{port_name}")
                ws_url = "wsserver:" + port_name[5:]
                self.mav_conn = mavutil.mavlink_connection(ws_url, dialect="ardupilotmega")
                mavlink_logger.info(f"✅ Connected successfully!:{ws_url}")
                self.ws_uri = ws_url
            else:
            # Set the COM port
                mavlink_logger.info(f"Connecting via COM Port")
                device = f"COM{port_name}" if port_name.isdigit() else port_name
                mavlink_logger.info(f"🔌 Connecting to {device} at {baudrate} baud...")

                # Connect to MAVLink
                self.mav_conn = mavutil.mavlink_connection(device, baud=baudrate)
                mavlink_logger.info("✅ Connected successfully!")

            # Wait for Heartbeat
            mavlink_logger.info("⏳ Waiting for heartbeat...")
            self.mav_conn.wait_heartbeat()
            self.last_heartbeat = time.time()
            mavlink_logger.info("✅ Heartbeat received!")

            # Set target info
            self.target_system = self.mav_conn.target_system
            self.target_component = self.mav_conn.target_component
            self.is_connected = True

            # Start heartbeat monitoring
            threading.Thread(target=self._check_heartbeat_timeout, daemon=True).start()

            return True

        except Exception as e:
            mavlink_logger.error(f"❌ Connection error: {e}")
            return False
#############################################################################
    def start_message_loop(self):
        """Start the MAVLink message reception loop in a separate thread."""
        if not self.is_connected:
            mavlink_logger.error("❌ Not connected to MAVLink device")
            return False

        threading.Thread(target=self._message_loop, daemon=True).start()
        return True
###########################################################################################
    def _message_loop(self):
        """Main loop for receiving MAVLink messages."""
        try:
            while self.is_connected:
                msg = self.mav_conn.recv_match(blocking=True, timeout=0.5)
                if not msg:
                    continue

                # Process the message
                self._process_message(msg)

        except Exception as e:
            mavlink_logger.error(f"❌ Message loop error: {e}")
            import traceback
            mavlink_logger.error(traceback.format_exc())
        finally:
            self.is_connected = False
########################################################################################
    def _process_message(self, msg):
        """Process received MAVLink messages."""
        msg_dict = msg.to_dict()  # Create dict from message for processing
        #self.rx_mav_msg.append(msg) # rx mavlink msg in list.
        JARVIS.jarvis_mav_data.append(msg_dict)
        #llm_ai.mavlink_context.append(msg_dict)
        self.ai_mavlink_ctx.append(msg_dict)
        mavlink_logger.debug(f"Received msg: {msg.get_type()}")

        if time.time() - self.last_dump_time > 5:
            mavlink_logger.debug(f" mavlink rx {self.tx_mav_msg}")
            mavlink_logger.debug(f" mavlink tx {self.rx_mav_msg}")
            self.last_dump_time = time.time()  # Reset timer

        # Process message based on type
        if msg.get_type() == "HEARTBEAT":
            self.last_heartbeat = time.time()
            self.heartbeat_timeout_flag = False

        elif msg.get_type() == "AUTOPILOT_VERSION":
            # firmware_info.parse_firmware_info(msg)
            self.parse_firmware_info(msg)

        elif msg.get_type() == "SYS_STATUS":
            self.decode_sensor_bitmask(msg)

        elif msg.get_type() == "STATUSTEXT":
            mavlink_logger.info(f"📢 FC STATUS: {msg.text}")

        elif msg.get_type() == "PARAM_VALUE":
            self._process_parameter(msg)

        elif msg.get_type() == "LOG_ENTRY":
            if msg.num_logs == 0:
                mavlink_logger.warning(f"⚠️ No black-box logs available on the flight controller")
                return

            # process log data chunk
            log_id = msg.id
            if log_id not in self.log_list:
                self.log_list.append(log_id)
                mavlink_logger.info(f"📋 received black-box log ID {log_id} to list")
                mavlink_logger.info(f" received {log_id} of {msg.num_logs}")
                # Trigger log list processing when all enteries are likely received
                #if msg.num_logs == msg.id + 1:
                if len(self.log_list) >= msg.num_logs:
                    self.on_log_list_received(self.log_list)
                else:
                    mavlink_logger.info(f" awaiting more black-box chunks")

        elif msg.get_type() == "LOG_DATA":
            self.on_log_data_received(msg.id, msg.data)

    def _process_parameter(self, msg):
        """Process parameter messages."""
        param_id = msg.param_id
        param_value = msg.param_value
        self.params_dict[param_id] = param_value

        # Track parameter download progress
        self.param_count = msg.param_count
        param_index = msg.param_index

        # Show progress periodically
        if param_index % 50 == 0 or param_index == self.param_count - 1:
            self.param_progress = (param_index + 1) / self.param_count * 100
            mavlink_logger.info(f"⏳ Parameter download: {self.param_progress:.1f}% ({param_index + 1}/{self.param_count})")

        # Check if we've received all parameters
        if len(self.params_dict) >= self.param_count > 0:
            mavlink_logger.info(f"✅ All {self.param_count} parameters received!")
            mavlink_logger.info(f"Abhinav Params: {self.params_dict}")
            # Notify that all parameters are received
            self.on_params_received()

    def on_params_received(self):
        """Called when all parameters are received. Override this in subclass."""
        pass
############################################################################################
    def _check_heartbeat_timeout(self):
        """Monitor for heartbeat timeouts."""
        while self.is_connected:
            if time.time() - self.last_heartbeat > 5:
                if not self.heartbeat_timeout_flag:
                    mavlink_logger.warning("⚠️ Heartbeat timeout detected!")
                    self.heartbeat_timeout_flag = True
                    print(f" mavlink rx {self.tx_mav_msg}")
                    print(f" mavlink tx {self.rx_mav_msg}")
            time.sleep(1)

    # Command sending methods
    def request_data_stream(self):
        """Request data stream from FC."""
        if not self.is_connected:
            return False

        mavlink_logger.info("⏳ Requesting data stream...")
        for i in range(0, 6):
            # Fixed: Using mav_conn instead of mav
            self.mav_conn.mav.request_data_stream_send(
                self.target_system,
                self.target_component,
                i,
                4,  # 4 Hz
                1  # Start
            )
            # store tx mavlink msg
            self.tx_mav_msg.append("DATA_STREAM_REQUEST")
        return True
####################################################################################
    def request_autopilot_version(self):
        """Request autopilot version information."""
        if not self.is_connected:
            return False

        mavlink_logger.info("⏳ Requesting autopilot version...")
        # Fixed: Using mav_conn instead of mav
        self.mav_conn.mav.command_long_send(
            self.target_system, self.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
            0,  # Confirmation
            mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION,
            0, 0, 0, 0, 0, 0
        )
        self.tx_mav_msg.append("VERSION_REQUEST")
        return True
#############################################################################
    def request_parameter_list(self):
        """Request full parameter list."""
        if not self.is_connected:
            return False

        mavlink_logger.info("⏳ Requesting parameter list...")
        # Fixed: Using mav_conn instead of mav
        self.mav_conn.mav.param_request_list_send(
            self.target_system,
            self.target_component
        )
        self.tx_mav_msg.append("PARAM_REQUEST")
        return True
#########################################################################
    def get_parameters(self):
        """Return the current parameter dictionary."""
        return self.params_dict.copy()
########################################################################
    def update_parameter(self, param_name, value):
        """Update a parameter on the flight controller."""
        if not self.is_connected:
            return False

        try:
            # Convert parameter value to float
            param_value = float(value)
            
            # Send parameter set command
            self.mav_conn.mav.param_set_send(
                self.target_system,
                self.target_component,
                param_name.encode('utf-8'),
                param_value,
                mavutil.mavlink.MAV_PARAM_TYPE_REAL32
            )
            
            # Store the sent command
            self.tx_mav_msg.append(f"PARAM_SET:{param_name}")
            
            mavlink_logger.info(f"⏳ Sent parameter update: {param_name} = {param_value}")
            return True
            
        except Exception as e:
            mavlink_logger.error(f"❌ Failed to update parameter {param_name}: {e}")
            return False
################################################################################
    ##log file handler
    def request_blackbox_logs(self):
        if not self.is_connected:
            return False
        mavlink_logger.info("📡 Requesting black-box log list...")
        # Fixed: Using mav_conn instead of mav
        self.mav_conn.mav.command_long_send(
            self.target_system,
            self.target_component,
            mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
            0,
            mavutil.mavlink.MAVLINK_MSG_ID_LOG_ENTRY,
            0,  # Start at 0
            65535,  # Request all logs
            0, 0, 0, 0
        )
        self.tx_mav_msg.append("LOG_REQUEST")
        return True
#################################################################################
    def on_log_list_received(self, log_list):
        mavlink_logger.info(f"📜 Received log list: {len(log_list)} logs")
        if not log_list:
            mavlink_logger.warning(f"No black-box logs found on this device")
            return
        for log_id in log_list:
            log_filename = f"{self.log_directory}/log_{log_id}.bin"
            if not os.path.exists(log_filename):
                mavlink_logger.info(f"📡 Requesting log data for ID {log_id}...")
                # Fixed: Using mav_conn instead of mav
                self.mav_conn.mav.log_request_data_send(
                    self.target_system,
                    self.target_component,
                    log_id,
                    0,  # Offset
                    0xFFFFFFFF  # Request all data
                )
####################################################################################
    def on_log_data_received(self, log_id, data):
        """Handle received log data (override in subclass)."""
        if len(self.log_list) == 0:
            mavlink_logger.warning(f"no log list found")
            return

        if log_id not in self.log_list:
            mavlink_logger.warning(f"⚠️ Received log data for unknown log ID {log_id}")
            return

        log_filename = f"{self.log_directory}/log_{log_id}.bin"
        mavlink_logger.info(f"📥 Received log data for ID {log_id}, size: {len(data)} bytes")
        with open(log_filename, "ab") as f:
            f.write(data)
        self.parse_blackbox_log(log_filename, log_id)

    def parse_blackbox_log(self, log_filename, log_id):
        """Parse black-box log (override in subclass)."""
        pass
#####################################################################################
    ##log file handler
    def disconnect(self):
        """Disconnect from MAVLink."""
        self.is_connected = False
        if self.mav_conn:
            try:
                self.mav_conn.close()
            except:
                pass
        mavlink_logger.info("🔌 Disconnected from MAVLink")
######################################################################################
    def parse_firmware_info(self, msg):
        flight_sw_major = (msg.flight_sw_version >> 24) & 0xFF
        flight_sw_minor = (msg.flight_sw_version >> 16) & 0xFF
        flight_sw_patch = (msg.flight_sw_version >> 8) & 0xFF
        flight_sw_type = msg.flight_sw_version & 0xFF

        board_version_major = (msg.board_version >> 24) & 0xFF
        board_version_minor = (msg.board_version >> 16) & 0xFF

        flight_custom_str = ''.join(chr(c) for c in msg.flight_custom_version if c != 0)

        capability_flags = {
            0x00000001: "MAVLink 2.0 Supported",
            0x00000002: "Mission FTP Supported",
            0x00000004: "Param FTP Supported",
            0x00000008: "TCP Support",
            0x00000010: "Set Attitude Target Supported",
            0x00000020: "Set Position Target Supported",
            0x00000040: "Set Actuator Target Supported",
            0x00000080: "Flight Termination Supported",
            0x00000100: "Companion Computer Present",
            0x00000200: "Mission Interface Supported",
            0x00000400: "Parameter Interface Supported",
            0x00000800: "FTP for Files Supported",
            0x00001000: "High Latency Support",
            0x00002000: "Camera Capture Supported",
            0x00004000: "Video Streaming Supported",
            0x00008000: "Manual Control Supported",
            0x00010000: "Mission Rally Points Supported",
            0x00020000: "Mission Fence Supported",
            0x00040000: "Terrain Data Supported",
            0x00080000: "MAV_CMD_DO_INVERTED_FLIGHT Supported",
            0x00100000: "Collision Avoidance Supported",
            0x00200000: "ADS-B Supported",
            0x00400000: "Autonomous Flight Modes Supported",
            0x00800000: "Gimbal Control Supported",
            0x01000000: "Onboard Logging Supported",
            0x02000000: "RTK GPS Supported",
            0x04000000: "AHRS Subsystem Present",
            0x08000000: "Motor Interlock Supported",
            0x10000000: "GPS Mode Switching Supported",
            0x20000000: "Button Control Supported",
            0x40000000: "Camera Tracking Supported",
            0x80000000: "GPS Dynamic Model Supported"
        }

        detected_capabilities = [name for bit, name in capability_flags.items() if msg.capabilities & bit]

        mavlink_logger.info("\n🔥 FIRMWARE INFO 🔥")
        mavlink_logger.info(
            f"Firmware Version: {flight_sw_major}.{flight_sw_minor}.{flight_sw_patch} (Type: {flight_sw_type})")
        mavlink_logger.info(f"Board Version: {board_version_major}.{board_version_minor}")
        mavlink_logger.info(f"Flight Custom Version: {flight_custom_str}")
        mavlink_logger.info(f"Vendor ID: {msg.vendor_id}, Product ID: {msg.product_id}")
        mavlink_logger.info("\n✅ Capabilities:")
        for cap in detected_capabilities:
            mavlink_logger.info(f"  ✔ {cap}")

    def decode_sensor_bitmask(self, msg):
        bitmask = msg.onboard_control_sensors_present
        mavlink_logger.info(f"Bitmask: {bin(bitmask)}")
        enabled_sensors = [name for value, name in SENSOR_FLAGS.items() if bitmask & value]
        if enabled_sensors:
            for sensor in enabled_sensors:
                mavlink_logger.info(f"✅ {sensor} is enabled")
        else:
            mavlink_logger.info("❌ No sensors detected")


# MAVLink sensor bitmask mapping
SENSOR_FLAGS = {
    1: "3D Gyro",
    2: "3D Accel",
    4: "3D Magnetometer",
    8: "Absolute Pressure",
    16: "Differential Pressure",
    32: "GPS",
    64: "Optical Flow",
    128: "Vision Position",
    256: "Laser Position",
    512: "External Ground Truth",
    1024: "3D Angular Rate Control",
    2048: "Attitude Stabilization",
    4096: "Yaw Position",
    8192: "Z/Altitude Control",
    16384: "XY Position Control",
    32768: "Motor Outputs",
    65536: "RC Receiver",
    131072: "3D Gyro2",
    262144: "3D Accel2",
    524288: "3D Magnetometer2",
    1048576: "Geofence",
    2097152: "AHRS",
    4194304: "Terrain",
    8388608: "Reverse Motor",
    16777216: "Logging",
    33554432: "Battery",
    67108864: "Proximity",
    134217728: "Satellite Communication",
    268435456: "Pre-arm Check",
    536870912: "Obstacle Avoidance",
    1073741824: "Propulsion",
    2147483648: "Extended Bit-field",
}