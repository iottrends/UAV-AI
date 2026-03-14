import logging
import threading
import time
import sys
import serial
import os
import json
import queue
from enum import Enum, auto
from pymavlink import mavutil
import JARVIS
from collections import deque
import socketio

# Update: Get the mavlink logger from root when available
mavlink_logger = logging.getLogger('mavlink')


class ConnectionState(Enum):
    DISCONNECTED     = auto()   # No connection attempt
    CONNECTING       = auto()   # Attempting to connect / waiting for heartbeat
    CONNECTED_SERIAL = auto()   # Connected via USB / COM port
    CONNECTED_UDP    = auto()   # Connected via UDP (WiFi / ELRS backpack / wfb-ng)
    CONNECTED_WS     = auto()   # Connected via WebSocket
    RECONNECTING     = auto()   # Lost link, attempting to recover
    ERROR            = auto()   # Unrecoverable error


# Heartbeat timeout per connection type (seconds)
_HEARTBEAT_TIMEOUT = {
    ConnectionState.CONNECTED_SERIAL: 3,
    ConnectionState.CONNECTED_UDP:    10,   # tolerant — UDP packet loss is normal
    ConnectionState.CONNECTED_WS:     5,
}

# States that count as "connected" for guards like is_connected
_CONNECTED_STATES = {
    ConnectionState.CONNECTED_SERIAL,
    ConnectionState.CONNECTED_UDP,
    ConnectionState.CONNECTED_WS,
}
class MavlinkHandler:
    def __init__(self):
        self.mav_conn = None
        self.ws_uri = None
        self.params_dict = {}
        self.socketio = None
        mavlink_logger.info("SocketIO instance updated successfully")
        self.param_done = 0
        self.last_heartbeat = 0
        self.heartbeat_timeout_flag = False

        # Connection state machine
        self._state = ConnectionState.DISCONNECTED
        self._state_lock = threading.Lock()

        self.param_count = 0
        self.param_progress = 0  # Track parameter download progress percentage
        self.target_system = None
        self.target_component = None
        # Write blackbox logs next to the executable (bundled) or project root (dev)
        if getattr(sys, '_MEIPASS', None):
            self.log_directory = os.path.join(os.path.dirname(sys.executable), "blackbox_logs")
        else:
            self.log_directory = os.path.join(os.path.abspath(os.path.dirname(__file__)), "blackbox_logs")
        self.log_list = []  # store log Ids from Log_Entry messages
        self.firmware_data = {}
        os.makedirs(self.log_directory, exist_ok=True)
        self.rx_mav_msg = deque(maxlen=100)  # circular buffer: last 100 rx MAVLink messages
        self._rx_mav_lock = threading.Lock()  # protects rx_mav_msg
        self.ai_mavlink_ctx = {}  # dict keyed by message type → latest msg (built from snapshot)
        self._telemetry_snapshot = []  # snapshot copy taken at telemetry loop start
        self.tx_mav_msg = [] # store all tx mavlink msg.
        self.last_dump_time = time.time()

        # Additions for command acknowledgment
        self.command_ack_status = {} # Stores {command_id: (result, completion_time)}
        self.command_ack_condition = threading.Condition() # For signaling ACK reception

        # For parameter update confirmation (PARAM_VALUE echo after PARAM_SET)
        self._param_update_condition = threading.Condition()
        self._pending_param_updates = {}  # {param_name: expected_value}

        # Latency measurement via TIMESYNC
        self.latency_ms = 0  # most recent RTT in ms
        self.latency_history = deque(maxlen=60)  # last 60 samples (~1 per second)

        # MAVLink packet/byte rate counters
        self._pkt_count = 0        # total packets since last rate calc
        self._byte_count = 0       # total bytes since last rate calc
        self._rate_timestamp = time.time()
        self._pkt_rate = 0.0       # packets/sec
        self._byte_rate = 0.0      # bytes/sec

        self._traffic_file = None  # mavlink_rxtx_log file handle
        self._tx_queue = queue.Queue()  # serialized outgoing MAVLink messages

    def connect(self, port_name, baudrate):
        """Establish connection to flight controller."""
        self._transition(ConnectionState.CONNECTING)
        try:
            if port_name.startswith("udpin:") or port_name.startswith("udpout:") or port_name.startswith("udp:"):
                mavlink_logger.info(f"Connecting via UDP: {port_name}")
                self.mav_conn = mavutil.mavlink_connection(port_name, dialect="ardupilotmega")
                connected_state = ConnectionState.CONNECTED_UDP
            elif port_name.startswith("ws://") or port_name.startswith("wss://"):
                mavlink_logger.info(f"Connecting via WebSocket: {port_name}")
                ws_url = "wsserver:" + port_name[5:]
                self.mav_conn = mavutil.mavlink_connection(ws_url, dialect="ardupilotmega")
                self.ws_uri = ws_url
                connected_state = ConnectionState.CONNECTED_WS
            else:
                # Serial / COM port
                device = f"COM{port_name}" if port_name.isdigit() else port_name
                mavlink_logger.info(f"🔌 Connecting to {device} at {baudrate} baud...")
                self.mav_conn = mavutil.mavlink_connection(device, baud=baudrate)
                connected_state = ConnectionState.CONNECTED_SERIAL

            # Wait for heartbeat
            mavlink_logger.info("⏳ Waiting for heartbeat...")
            self.mav_conn.wait_heartbeat()
            self.last_heartbeat = time.time()
            mavlink_logger.info("✅ Heartbeat received!")

            # Set target info
            self.target_system = self.mav_conn.target_system
            self.target_component = self.mav_conn.target_component

            # Transition to the correct connected sub-state
            self._transition(connected_state)
            self._open_traffic_log()

            # Start background threads
            threading.Thread(target=self._check_heartbeat_timeout, daemon=True).start()
            threading.Thread(target=self._timesync_loop, daemon=True).start()
            threading.Thread(target=self._tx_loop, daemon=True, name="mavlink-tx").start()

            return True

        except Exception as e:
            mavlink_logger.error(f"❌ Connection error: {e}")
            self._transition(ConnectionState.ERROR)
            return False

##############################################################################
    @property
    def is_connected(self):
        """True when in any CONNECTED_* state."""
        return self._state in _CONNECTED_STATES

    @is_connected.setter
    def is_connected(self, value):
        """Legacy setter — kept for backward compatibility with callers that
        still write  self.is_connected = False  (e.g. _message_loop finally).
        Maps True → keeps current connected state, False → DISCONNECTED."""
        if not value:
            self._transition(ConnectionState.DISCONNECTED)

    def _transition(self, new_state: ConnectionState):
        """Move to a new ConnectionState, log the transition, and notify UI."""
        with self._state_lock:
            old_state = self._state
            if old_state == new_state:
                return
            self._state = new_state

        mavlink_logger.info(f"🔄 Connection state: {old_state.name} → {new_state.name}")

        # Notify the browser so the UI can reflect the exact state
        if self.socketio:
            try:
                self.socketio.emit('connection_state', {
                    'state': new_state.name,
                    'connected': new_state in _CONNECTED_STATES,
                })
            except Exception:
                pass

##############################################################################
    def update_socketio(self, socketio_instance):
        """Update the SocketIO instance."""
        self.socketio = socketio_instance
        mavlink_logger.info("SocketIO instance updated successfully")


#############################################################################
    def start_message_loop(self):
        """Start the MAVLink message reception loop in a separate thread."""
        if not self.is_connected:
            mavlink_logger.error("❌ Not connected to MAVLink device")
            return False

        threading.Thread(target=self._message_loop, daemon=True).start()
        return True
###########################################################################################
    def xmit_mavlink(self, name, fn):
        """Enqueue an outgoing MAVLink message.
        Appends name to tx_mav_msg for traffic logging and puts the send
        callable onto _tx_queue for the dedicated TX thread to execute."""
        self.tx_mav_msg.append(name)
        if len(self.tx_mav_msg) >= 10:
            self._write_traffic_records(
                [{"dir": "tx", "ts": time.time(), "msg": m} for m in self.tx_mav_msg]
            )
            self.tx_mav_msg.clear()
        self._tx_queue.put(fn)

    def _tx_loop(self):
        """Dedicated TX thread — pops send callables from _tx_queue and executes
        them serially so MAVLink writes are never concurrent."""
        while self.is_connected:
            try:
                fn = self._tx_queue.get(timeout=0.5)
                fn()
            except queue.Empty:
                continue
            except Exception as e:
                mavlink_logger.error(f"TX error: {e}")

###########################################################################################
    def _message_loop(self):
        """Main loop for receiving MAVLink messages."""
        try:
            while self.is_connected:
                msg = self.mav_conn.recv_match(blocking=True, timeout=0.5)
                if not msg:
                    continue

                # Capture TIMESYNC timestamp immediately before any processing overhead
                if msg.get_type() == "TIMESYNC" and msg.tc1 != 0:
                    now_us = int(time.time() * 1e6)
                    rtt_ms = (now_us - msg.ts1) / 1000.0
                    if 0 < rtt_ms < 10000:
                        self.latency_ms = round(rtt_ms, 1)
                        self.latency_history.append(self.latency_ms)
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
        msg_dict["_rx_timestamp"] = time.time()  # arrival timestamp
        msg_type = msg_dict.get("mavpackettype", "UNKNOWN")

        # Count packets and bytes for rate calculation
        self._pkt_count += 1
        try:
            self._byte_count += len(msg.get_msgbuf())
        except Exception:
            self._byte_count += 32  # fallback estimate

        # Push into the 100-msg circular buffer (thread-safe)
        with self._rx_mav_lock:
            self.rx_mav_msg.append(msg_dict)
            if len(self.rx_mav_msg) >= 100:
                self._write_traffic_records([{"dir": "rx", "data": m} for m in self.rx_mav_msg])
                self.rx_mav_msg.clear()

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
        elif msg.get_type() == "STORAGE_INFORMATION":
            self.flash_info = {
                "total_mb":     round(msg.total_capacity),
                "used_mb":      round(msg.used_capacity),
                "available_mb": round(msg.available_capacity),
                "storage_type": msg.type,
            }
            mavlink_logger.info(
                f"💾 STORAGE_INFORMATION: total={msg.total_capacity:.1f} MiB "
                f"used={msg.used_capacity:.1f} MiB avail={msg.available_capacity:.1f} MiB"
            )

        elif msg.get_type() == "COMMAND_ACK":
            with self.command_ack_condition:
                command_id = msg.command
                result = msg.result
                result_names = {0: "ACCEPTED", 1: "TEMPORARILY_REJECTED", 2: "DENIED", 3: "UNSUPPORTED", 4: "FAILED", 5: "IN_PROGRESS", 6: "CANCELLED"}
                result_str = result_names.get(result, f"UNKNOWN({result})")
                mavlink_logger.info(f"Received COMMAND_ACK for command {command_id} with result: {result_str}")
                print(f"<<< COMMAND_ACK: cmd={command_id} result={result_str}")
                self.command_ack_status[command_id] = result
                self.command_ack_condition.notify_all() # Notify waiting threads
########################################################################################
    def _process_parameter(self, msg):
        """Process parameter messages."""
        param_id = msg.param_id
        param_value = msg.param_value
        self.params_dict[param_id] = param_value

        # Notify if this is an echo for a pending param update
        with self._param_update_condition:
            if param_id in self._pending_param_updates:
                del self._pending_param_updates[param_id]
                mavlink_logger.info(f"✅ Parameter {param_id} confirmed: {param_value}")
                self._param_update_condition.notify_all()

        # Track parameter download progress
        self.param_count = msg.param_count
        param_index = msg.param_index

        # Skip sentinel values (0xFFFF = unknown index in MAVLink spec)
        if param_index >= self.param_count:
            return

        # Always update progress so system_status broadcasts have current value
        self.param_progress = min(100.0, (param_index + 1) / self.param_count * 100)

        # Log and emit progress periodically
        if param_index % 50 == 0 or param_index == self.param_count - 1:
            mavlink_logger.info(f"⏳ Parameter download: {self.param_progress:.1f}% ({param_index + 1}/{self.param_count})")
            print(f"⏳ Parameter download: {self.param_progress:.1f}% ({param_index + 1}/{self.param_count})")

            ## emit param progress to frontend
            if self.socketio:
                self.socketio.emit('param_progress', {
                "params": {
                    "percentage": self.param_progress,
                    "downloaded": param_index + 1,
                    "total": self.param_count,
                    "status": "Downloading..."
                }
            })

        # Check if we've received all parameters
        if len(self.params_dict) >= self.param_count > 0:
            mavlink_logger.info(f"✅ All {self.param_count} parameters received!")
            mavlink_logger.info(f"Abhinav Params: {self.params_dict}")
            # Notify that all parameters are received
            #emit to frontend
            self.on_params_received()
            if self.socketio:
                self.socketio.emit('param_progress', {
                    "params": {
                        "percentage": self.param_progress,
                        "downloaded": param_index + 1,
                        "total": self.param_count,
                        "status": "Complete"
                    }
                })


#######################################################################
    def on_params_received(self):
        """Called when all parameters are received. Overridden by DroneValidator."""
        pass
############################################################################################
    def snapshot_rx_queue(self):
        """Update ai_mavlink_ctx with the latest message of each type from
        the rx deque.  ai_mavlink_ctx is a *persistent* last-known-value cache
        and is NEVER wiped — only keys present in the current snapshot are
        overwritten.  This prevents the fast loop from emitting zeroes when
        the slow loop has just flushed the deque but new messages haven't
        arrived yet (the "flicker" race condition)."""
        with self._rx_mav_lock:
            self._telemetry_snapshot = list(self.rx_mav_msg)

        # Update in-place: only overwrite keys we actually received this cycle
        for m in self._telemetry_snapshot:
            self.ai_mavlink_ctx[m.get("mavpackettype", "UNKNOWN")] = m

    def flush_rx_queue(self):
        """Clear the rx queue. Call at the END of the telemetry loop."""
        with self._rx_mav_lock:
            self.rx_mav_msg.clear()

    def _check_heartbeat_timeout(self):
        """Monitor for heartbeat timeouts using state-aware thresholds.

        CONNECTED_SERIAL: 3s  — cable is either there or not
        CONNECTED_UDP:   10s  — tolerant of WiFi/RF packet loss
        CONNECTED_WS:     5s
        """
        while self.is_connected:
            timeout = _HEARTBEAT_TIMEOUT.get(self._state, 5)
            if time.time() - self.last_heartbeat > timeout:
                if not self.heartbeat_timeout_flag:
                    mavlink_logger.warning(
                        f"⚠️ Heartbeat timeout ({timeout}s) on {self._state.name}"
                    )
                    self.heartbeat_timeout_flag = True
                    self._transition(ConnectionState.RECONNECTING)
            time.sleep(1)

############################################################################################
    def _timesync_loop(self):
        """Send TIMESYNC requests every 1 second to measure MAVLink link latency."""
        while self.is_connected:
            ts1 = int(time.time() * 1e6)
            self.xmit_mavlink(
                "TIMESYNC",
                lambda ts=ts1: self.mav_conn.mav.timesync_send(0, ts)
            )
            time.sleep(1)

    def get_latency_stats(self):
        """Return current latency and statistics (avg, min, max) in ms."""
        if not self.latency_history:
            return {"current": 0, "avg": 0, "min": 0, "max": 0}
        history = list(self.latency_history)
        return {
            "current": self.latency_ms,
            "avg": round(sum(history) / len(history), 1),
            "min": round(min(history), 1),
            "max": round(max(history), 1),
        }

    def get_link_stats(self):
        """Calculate and return MAVLink packet rate and link speed since last call."""
        now = time.time()
        elapsed = now - self._rate_timestamp
        if elapsed < 0.1:
            return {"pkt_rate": self._pkt_rate, "byte_rate": self._byte_rate}
        self._pkt_rate = round(self._pkt_count / elapsed, 1)
        self._byte_rate = round(self._byte_count / elapsed, 1)
        self._pkt_count = 0
        self._byte_count = 0
        self._rate_timestamp = now
        return {"pkt_rate": self._pkt_rate, "byte_rate": self._byte_rate}

############################################################################################
    # Command sending methods
    def request_data_stream(self):
        """Request data stream from FC."""
        if not self.is_connected:
            return False

        mavlink_logger.info("⏳ Requesting data stream...")
        for i in range(0, 6):
            stream_id = i
            self.xmit_mavlink(
                f"DATA_STREAM_REQUEST:{stream_id}",
                lambda sid=stream_id: self.mav_conn.mav.request_data_stream_send(
                    self.target_system, self.target_component, sid, 4, 1
                )
            )
        return True
####################################################################################
    def request_autopilot_version(self):
        """Request autopilot version information."""
        if not self.is_connected:
            return False

        mavlink_logger.info("⏳ Requesting autopilot version...")
        self.xmit_mavlink(
            "VERSION_REQUEST",
            lambda: self.mav_conn.mav.command_long_send(
                self.target_system, self.target_component,
                mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                0,
                mavutil.mavlink.MAVLINK_MSG_ID_AUTOPILOT_VERSION,
                0, 0, 0, 0, 0, 0
            )
        )
        return True
#############################################################################
    def request_parameter_list(self):
        """Request full parameter list."""
        if not self.is_connected:
            return False

        mavlink_logger.info("⏳ Requesting parameter list...")
        self.xmit_mavlink(
            "PARAM_REQUEST",
            lambda: self.mav_conn.mav.param_request_list_send(
                self.target_system, self.target_component
            )
        )
        return True
#########################################################################
    def get_parameters(self):
        """Return the current parameter dictionary."""
        return self.params_dict.copy()
########################################################################
    def update_parameter(self, param_name, value, timeout_seconds=5):
        """Update a parameter on the flight controller and wait for PARAM_VALUE echo."""
        if not self.is_connected:
            return False

        try:
            param_value = float(value)

            # Register as pending before sending
            with self._param_update_condition:
                self._pending_param_updates[param_name] = param_value

            # Send parameter set command
            self.xmit_mavlink(
                f"PARAM_SET:{param_name}",
                lambda pn=param_name, pv=param_value: self.mav_conn.mav.param_set_send(
                    self.target_system, self.target_component,
                    pn.encode('utf-8'), pv,
                    mavutil.mavlink.MAV_PARAM_TYPE_REAL32
                )
            )

            mavlink_logger.info(f"⏳ Sent parameter update: {param_name} = {param_value}")

            # Wait for FC to echo back PARAM_VALUE
            with self._param_update_condition:
                if param_name in self._pending_param_updates:
                    self._param_update_condition.wait(timeout=timeout_seconds)

                if param_name in self._pending_param_updates:
                    # Still pending — FC didn't confirm
                    del self._pending_param_updates[param_name]
                    mavlink_logger.error(f"❌ Parameter {param_name} update timed out — no PARAM_VALUE echo within {timeout_seconds}s")
                    return False

            return True

        except Exception as e:
            mavlink_logger.error(f"❌ Failed to update parameter {param_name}: {e}")
            return False
################################################################################

    _mav_command_map = {
        "MAV_CMD_COMPONENT_ARM_DISARM": mavutil.mavlink.MAV_CMD_COMPONENT_ARM_DISARM,
        "MAV_CMD_DO_MOTOR_TEST": mavutil.mavlink.MAV_CMD_DO_MOTOR_TEST,
        "MAV_CMD_NAV_TAKEOFF": mavutil.mavlink.MAV_CMD_NAV_TAKEOFF,
        "MAV_CMD_NAV_LAND": mavutil.mavlink.MAV_CMD_NAV_LAND,
        "MAV_CMD_PREFLIGHT_CALIBRATION": mavutil.mavlink.MAV_CMD_PREFLIGHT_CALIBRATION,
        "MAV_CMD_DO_SET_MODE": mavutil.mavlink.MAV_CMD_DO_SET_MODE,
        "MAV_CMD_CONDITION_CHANGE_ALT": mavutil.mavlink.MAV_CMD_CONDITION_CHANGE_ALT,
        "MAV_CMD_DO_CHANGE_SPEED": mavutil.mavlink.MAV_CMD_DO_CHANGE_SPEED,
        "MAV_CMD_NAV_RETURN_TO_LAUNCH": mavutil.mavlink.MAV_CMD_NAV_RETURN_TO_LAUNCH,
    }

    def send_mavlink_command_from_json(self, command_json, timeout_seconds=5):
        """
        Sends a MAVLink command (MAV_CMD_LONG) to the flight controller
        based on a JSON dictionary provided by JARVIS.

        Args:
            command_json (dict): A dictionary containing command and parameters.
                                 Example: {"command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 1, "param2": 21196}
            timeout_seconds (int): How long to wait for ACK (default 5s).
        Returns:
            bool: True if command was sent, False otherwise.
        """
        if not self.is_connected:
            mavlink_logger.error("❌ Not connected to MAVLink device. Cannot send command.")
            return False

        command_name = command_json.get("command")
        if command_name not in self._mav_command_map:
            mavlink_logger.error(f"❌ Unknown MAVLink command: {command_name}")
            return False
        
        mavlink_command_id = self._mav_command_map[command_name]

        # Extract params, defaulting to 0.0 for unused ones
        params = [
            float(command_json.get("param1", 0)),
            float(command_json.get("param2", 0)),
            float(command_json.get("param3", 0)),
            float(command_json.get("param4", 0)),
            float(command_json.get("param5", 0)),
            float(command_json.get("param6", 0)),
            float(command_json.get("param7", 0))
        ]

        try:
            # Clear previous ACK status for this command
            with self.command_ack_condition:
                self.command_ack_status.pop(mavlink_command_id, None)

            # Send the MAV_CMD_LONG command
            self.xmit_mavlink(
                f"COMMAND_SENT:{command_name}",
                lambda cid=mavlink_command_id, p=params: self.mav_conn.mav.command_long_send(
                    self.target_system, self.target_component, cid, 0, *p
                )
            )
            mavlink_logger.info(f"✅ Sent MAVLink command: {command_name} with params: {params}")
            print(f">>> SENT MAVLink command: {command_name} | target_sys={self.target_system} target_comp={self.target_component} | params={params}")

            # Wait for ACK with a timeout
            with self.command_ack_condition:
                acked_result = self.command_ack_status.get(mavlink_command_id)
                if acked_result is None: # Only wait if ACK not already received (unlikely but safe)
                    self.command_ack_condition.wait(timeout=timeout_seconds)
                
                acked_result = self.command_ack_status.get(mavlink_command_id)
                if acked_result is not None:
                    if acked_result == mavutil.mavlink.MAV_RESULT_ACCEPTED or \
                       acked_result == mavutil.mavlink.MAV_RESULT_TEMPORARILY_REJECTED or \
                       acked_result == mavutil.mavlink.MAV_RESULT_IN_PROGRESS:
                        mavlink_logger.info(f"✅ Command {command_name} ACKed with result: {acked_result}")
                        return True
                    else:
                        mavlink_logger.warning(f"⚠️ Command {command_name} NACKed with result: {acked_result}")
                        print(f"<<< NACK: {command_name} result={acked_result}")
                        return False
                else:
                    mavlink_logger.error(f"❌ Command {command_name} timed out waiting for ACK.")
                    print(f"<<< TIMEOUT: {command_name} — no ACK received within 5s")
                    return False
        except Exception as e:
            mavlink_logger.error(f"❌ Failed to send MAVLink command {command_name}: {e}")
            return False

################################################################################
    ##log file handler
    def request_blackbox_logs(self):
        if not self.is_connected:
            return False
        mavlink_logger.info("📡 Requesting black-box log list...")
        self.xmit_mavlink(
            "LOG_REQUEST",
            lambda: self.mav_conn.mav.command_long_send(
                self.target_system, self.target_component,
                mavutil.mavlink.MAV_CMD_REQUEST_MESSAGE,
                0,
                mavutil.mavlink.MAVLINK_MSG_ID_LOG_ENTRY,
                0, 65535, 0, 0, 0, 0
            )
        )
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
                self.xmit_mavlink(
                    f"LOG_DATA_REQUEST:{log_id}",
                    lambda lid=log_id: self.mav_conn.mav.log_request_data_send(
                        self.target_system, self.target_component,
                        lid, 0, 0xFFFFFFFF
                    )
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

    def send_rc_override(self, channels: list) -> bool:
        """
        Send RC_CHANNELS_OVERRIDE to the flight controller.

        channels : list of 8 PWM values (µs, typically 1000-2000).
                   Use 0 for channels you don't want to override.
        Returns True on success.
        """
        if not self.is_connected or not self.mav_conn:
            return False
        try:
            ch = (list(channels) + [0] * 8)[:8]
            self.xmit_mavlink(
                "RC_OVERRIDE",
                lambda c=ch: self.mav_conn.mav.rc_channels_override_send(
                    self.mav_conn.target_system, self.mav_conn.target_component,
                    int(c[0]), int(c[1]), int(c[2]), int(c[3]),
                    int(c[4]), int(c[5]), int(c[6]), int(c[7]),
                )
            )
            return True
        except Exception as e:
            mavlink_logger.error(f"RC override send error: {e}")
            return False
#####################################################################################
    def reboot_fc(self):
        """Send MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN with param1=1 to reboot the autopilot."""
        if not self.is_connected or not self.mav_conn:
            mavlink_logger.error("Cannot reboot FC: not connected")
            return False
        try:
            self.xmit_mavlink(
                "REBOOT_FC",
                lambda: self.mav_conn.mav.command_long_send(
                    self.target_system, self.target_component,
                    mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                    0, 1.0, 0, 0, 0, 0, 0, 0
                )
            )
            mavlink_logger.info("Sent FC reboot command (param1=1)")
            return True
        except Exception as e:
            mavlink_logger.error(f"Failed to send FC reboot: {e}")
            return False

#####################################################################################
    def reboot_to_bootloader(self):
        """Send MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN with param1=3 to stay in bootloader."""
        if not self.is_connected or not self.mav_conn:
            mavlink_logger.error("Cannot reboot to bootloader: not connected")
            return False
        try:
            self.xmit_mavlink(
                "REBOOT_TO_BOOTLOADER",
                lambda: self.mav_conn.mav.command_long_send(
                    self.target_system, self.target_component,
                    mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
                    0, 3.0, 0, 0, 0, 0, 0, 0
                )
            )
            mavlink_logger.info("Sent reboot-to-bootloader command (param1=3)")
            return True
        except Exception as e:
            mavlink_logger.error(f"Failed to send reboot-to-bootloader: {e}")
            return False

#####################################################################################
    def _open_traffic_log(self):
        """Open the mavlink_rxtx_log file for appending."""
        try:
            if getattr(sys, '_MEIPASS', None):
                log_dir = os.path.join(os.path.dirname(sys.executable), 'logs')
            else:
                log_dir = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'logs')
            os.makedirs(log_dir, exist_ok=True)
            path = os.path.join(log_dir, 'mavlink_rxtx_log.jsonl')
            self._traffic_file = open(path, 'a', encoding='utf-8')
            mavlink_logger.info(f"MAVLink traffic log opened: {path}")
        except Exception as e:
            mavlink_logger.error(f"Failed to open traffic log: {e}")

    def _write_traffic_records(self, records):
        """Write a list of records as JSONL lines to the traffic log."""
        if not self._traffic_file:
            return
        try:
            for record in records:
                self._traffic_file.write(json.dumps(record, default=str) + '\n')
            self._traffic_file.flush()
        except Exception as e:
            mavlink_logger.error(f"Traffic log write error: {e}")

    def disconnect(self):
        """Disconnect from MAVLink."""
        self._transition(ConnectionState.DISCONNECTED)
        # Clear the LKV cache so stale data doesn't survive into the next session
        self.ai_mavlink_ctx = {}
        # Flush remaining buffered messages before closing
        if self.rx_mav_msg:
            self._write_traffic_records([{"dir": "rx", "data": m} for m in self.rx_mav_msg])
            self.rx_mav_msg.clear()
        if self.tx_mav_msg:
            self._write_traffic_records([{"dir": "tx", "ts": time.time(), "msg": m} for m in self.tx_mav_msg])
            self.tx_mav_msg.clear()
        if self._traffic_file:
            self._traffic_file.close()
            self._traffic_file = None
        if self.mav_conn:
            try:
                self.mav_conn.close()
            except:
                pass
        # Drain TX queue so the tx_loop exits cleanly
        while not self._tx_queue.empty():
            try:
                self._tx_queue.get_nowait()
            except queue.Empty:
                break
        # Reset parameter state so reconnect starts fresh
        self.params_dict = {}
        self.param_count = 0
        self.param_progress = 0
        self.param_done = 0
        self.flash_info = {}
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

        self.firmware_data = {
            "firmware_version": f"{flight_sw_major}.{flight_sw_minor}.{flight_sw_patch} (Type: {flight_sw_type})",
            "board_version": f"{board_version_major}.{board_version_minor}",
            "flight_custom_version": flight_custom_str,
            "vendor_id": msg.vendor_id,
            "product_id": msg.product_id,
            "capabilities": detected_capabilities
        }

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
