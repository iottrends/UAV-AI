import json
import logging
from copy import deepcopy  # Optional, if you want to reset categorized_params later

drone_logger = logging.getLogger('drone_validator')

from pymavlink import DFReader

# from pymavlink.mavwp import DFReader

# Assuming MavlinkHandler is defined elsewhere
from Mavlink_rx_handler import MavlinkHandler


class DroneValidator(MavlinkHandler):
    def __init__(self):
        super().__init__()  # Initializes params_dict = {} from MavlinkHandler
        self.hardware_validated = False
        # Fixed typo: categorized_param -> categorized_params
        self.categorized_params = {
            "System": {},
            "GPS": {},
            "Battery": {},
            "Serial": {},
            "Compass": {},
            "IMU": {},
            "Navigation": {},
            "Motors": {},
            "Servos": {},
            "RC": {},
            "Flight Modes": {},
            "Safety": {},
            "EKF": {},
            "AHRS": {},
            "Control": {},
            "Pilot": {},
            "Landing": {},
            "Barometer": {},
            "RangeFinder": {},
            "RPM": {},
            "Notifications": {},
            "OSD": {},
            "Logging": {},
            "Scheduler": {},
            "Streaming": {},
            "Miscellaneous": {}
        }
        self.blackbox_logs = {}  # Declared here
        drone_logger.info("DroneValidator initialized, awaiting parameters")

    def categorize_params(self, params):
        """Categorize parameters into subsystems."""
        for param, value in params.items():
            param_upper = param.upper()  # Case-insensitive matching

            # System
            if any(kw in param_upper for kw in ["SYSID", "FORMAT", "FRAME", "BRD_", "STAT_", "DEV_"]):
                self.categorized_params["System"][param] = value

            # GPS
            elif any(kw in param_upper for kw in ["GPS", "AHRS_GPS"]):
                self.categorized_params["GPS"][param] = value

            # Battery
            elif any(kw in param_upper for kw in ["BATT", "MOT_BAT"]):
                self.categorized_params["Battery"][param] = value

            # Serial
            elif any(kw in param_upper for kw in ["SERIAL", "TELEM"]):
                self.categorized_params["Serial"][param] = value

            # Compass
            elif any(kw in param_upper for kw in ["COMPASS", "ARMING_MAGTHRESH"]):
                self.categorized_params["Compass"][param] = value

            # IMU
            elif any(kw in param_upper for kw in ["INS_", "EK3_IMU"]):
                self.categorized_params["IMU"][param] = value

            # Navigation
            elif any(kw in param_upper for kw in
                     ["WPNAV", "RTL_", "LOIT", "CIRCLE", "AVOID", "RALLY", "FENCE", "SRTL", "GUID", "SURFTRAK",
                      "WP_YAW"]):
                self.categorized_params["Navigation"][param] = value

            # Motors
            elif any(kw in param_upper for kw in ["MOT_", "ESC_"]):
                self.categorized_params["Motors"][param] = value

            # Servos
            elif "SERVO" in param_upper:
                self.categorized_params["Servos"][param] = value

            # RC
            elif any(kw in param_upper for kw in ["RC", "RCMAP"]):
                self.categorized_params["RC"][param] = value

            # Flight Modes
            elif any(kw in param_upper for kw in ["FLTMODE", "INITIAL_MODE", "SIMPLE", "THROW_"]):
                self.categorized_params["Flight Modes"][param] = value

            # Safety
            elif any(kw in param_upper for kw in ["FS_", "ARMING", "DISARM", "BRD_SAFETY"]):
                self.categorized_params["Safety"][param] = value

            # EKF
            elif "EK3_" in param_upper or param_upper == "AHRS_EKF_TYPE":
                self.categorized_params["EKF"][param] = value

            # AHRS
            elif "AHRS_" in param_upper and param_upper != "AHRS_EKF_TYPE" and "GPS" not in param_upper:
                self.categorized_params["AHRS"][param] = value

            # Control
            elif any(kw in param_upper for kw in ["ATC_", "PSC_", "ACRO_", "ANGLE_", "PHLD_"]):
                self.categorized_params["Control"][param] = value

            # Pilot
            elif any(kw in param_upper for kw in ["PILOT_", "TKOFF_", "THR_DZ"]):
                self.categorized_params["Pilot"][param] = value

            # Landing
            elif any(kw in param_upper for kw in ["LAND_", "PLDP_"]):
                self.categorized_params["Landing"][param] = value

            # Barometer
            elif "BARO" in param_upper:
                self.categorized_params["Barometer"][param] = value

            # RangeFinder
            elif "RNGFND" in param_upper:
                self.categorized_params["RangeFinder"][param] = value

            # RPM
            elif "RPM" in param_upper:
                self.categorized_params["RPM"][param] = value

            # Notifications
            elif "NTF_" in param_upper:
                self.categorized_params["Notifications"][param] = value

            # OSD
            elif "OSD" in param_upper:
                self.categorized_params["OSD"][param] = value

            # Logging
            elif "LOG_" in param_upper:
                self.categorized_params["Logging"][param] = value

            # Scheduler
            elif "SCHED" in param_upper:
                self.categorized_params["Scheduler"][param] = value

            # Streaming
            elif "SR" in param_upper:
                self.categorized_params["Streaming"][param] = value

            # Miscellaneous (catch-all)
            else:
                self.categorized_params["Miscellaneous"][param] = value

        drone_logger.info(f"Categorized Parameters: {self.categorized_params}")

    def on_params_received(self):
        """Override to perform validation when parameters are received."""
        drone_logger.info("📥 All parameters received, categorizing and validating...")
        self.param_done = 1
        self.categorize_params(self.params_dict)
        self.validate_hardware()

    def validate_hardware(self):
        """Validate hardware components based on parameters."""
        drone_logger.info("🔍 Starting hardware validation...")
        # Use categorized_params instead of calling get_parameters()
        self.validate_frame_type()
        self.validate_compass()
        self.validate_gps()
        self.validate_serial_ports()
        self.hardware_validated = True
        drone_logger.info("✅ Hardware validation complete")

    def parse_blackbox_log(self, log_filename, log_id):
        """Parse ArduPilot .BIN log."""
        drone_logger.info(f"📜 Parsing ArduPilot log: {log_filename}")
        log = DFReader.DFReader_binary(log_filename)
        data = {
            "IMU": {"gyro_x": [], "gyro_y": [], "gyro_z": [], "acc_x": [], "acc_y": [], "acc_z": []},
            "Motors": {"motor1": [], "motor2": [], "motor3": [], "motor4": []},
            "GPS": {"lat": [], "lon": [], "alt": [], "satellites": []},
            "Attitude": {"roll": [], "pitch": [], "yaw": []}
        }
        while True:
            msg = log.recv_msg()
            if msg is None:
                break
            if msg.get_type() == "GYR":
                data["IMU"]["gyro_x"].append(msg.GyrX)
                data["IMU"]["gyro_y"].append(msg.GyrY)
                data["IMU"]["gyro_z"].append(msg.GyrZ)
            elif msg.get_type() == "ACC":
                data["IMU"]["acc_x"].append(msg.AccX)
                data["IMU"]["acc_y"].append(msg.AccY)
                data["IMU"]["acc_z"].append(msg.AccZ)
            elif msg.get_type() == "MOT":
                data["Motors"]["motor1"].append(msg.Mot1)
                data["Motors"]["motor2"].append(msg.Mot2)
                data["Motors"]["motor3"].append(msg.Mot3)
                data["Motors"]["motor4"].append(msg.Mot4)
            elif msg.get_type() == "GPS":
                data["GPS"]["lat"].append(msg.Lat)
                data["GPS"]["lon"].append(msg.Lng)
                data["GPS"]["alt"].append(msg.Alt)
                data["GPS"]["satellites"].append(msg.NSats)
            elif msg.get_type() == "ATT":
                data["Attitude"]["roll"].append(msg.Roll)
                data["Attitude"]["pitch"].append(msg.Pitch)
                data["Attitude"]["yaw"].append(msg.Yaw)
        self.blackbox_logs[log_id] = data
        drone_logger.info(f"Parsed log {log_id}")

    def validate_frame_type(self):
        """Validate frame type and motor configuration."""
        params = self.categorized_params["System"]
        frame_type = params.get("FRAME_CLASS", 0)
        frame_types = {
            0: "Undefined",
            1: "Quad",
            2: "Hexa",
            3: "Octa",
            4: "OctaQuad",
            5: "Y6",
            6: "Tri",
            7: "Single/Dual",
            10: "Copter for helicopter",
            12: "DodecaHexa",
            13: "HeliQuad"
        }
        frame_name = frame_types.get(frame_type, f"Unknown ({frame_type})")
        drone_logger.info(f"🛠️ Frame Type: {frame_name}")
        #print(f"Frame name/type: {frame_name}, {frame_type}")

    def validate_compass(self):
        """Validate compass configuration."""
        drone_logger.info("🧭 Validating compass configuration...")
        params = self.categorized_params["Compass"]

        # Check if compass is enabled
        compass_enabled = params.get("COMPASS_ENABLE", 1) == 1
        if not compass_enabled:
            drone_logger.warning("⚠️ Compass is disabled in parameters!")
            return

        # Check primary compass
        primary_compass = params.get("COMPASS_PRIMARY", 0)
        drone_logger.info(f"🧭 Primary compass: #{primary_compass}")

        # Check external compass setting
        external_compass = params.get("COMPASS_EXTERNAL", 0) == 1
        drone_logger.info(f"🧭 External compass: {'Yes' if external_compass else 'No'}")

        # Check compass devices
        compass_count = 0
        for i in range(3):  # ArduPilot supports up to 3 compasses
            compass_use = params.get(f"COMPASS_USE{i + 1}", 0) == 1
            if compass_use:
                compass_count += 1
                dev_id = params.get(f"COMPASS_DEV_ID{i + 1}", 0)
                orient = params.get(f"COMPASS_ORIENT{i + 1}", 0)
                drone_logger.info(f"🧭 Compass #{i + 1}: Device ID: {dev_id}, Orientation: {orient}")

    def validate_gps(self):
        """Validate GPS configuration."""
        drone_logger.info("📍 Validating GPS configuration...")
        params = self.categorized_params["GPS"]
        gps_type = params.get("GPS_TYPE", 1)  # Note: Might need GPS1_TYPE instead
        gps_enabled = gps_type > 0
        drone_logger.info(f"📍 GPS enabled: {gps_enabled} (Type: {gps_type})")

    def validate_serial_ports(self):
        """Validate serial port configuration."""
        drone_logger.info("🔌 Checking serial port configuration...")
        params = self.categorized_params["Serial"]
        for i in range(1, 5):  # Check SERIAL1 to SERIAL4
            protocol = params.get(f"SERIAL{i}_PROTOCOL", 0)
            baud = params.get(f"SERIAL{i}_BAUD", 57600)
            if protocol > 0:
                drone_logger.info(f"🔌 SERIAL{i}: Protocol {protocol} @ {baud} baud")

    def get_param_value(self, params, param_name, default=None):
        """Safely get a parameter value with a default if not found."""
        return params.get(param_name, default)

    def save_to_json(self, filename):
        """Save categorized parameters to a JSON file."""
        with open(filename, 'w') as f:
            json.dump(self.categorized_params, f, indent=4)
        drone_logger.info(f"Parameters saved to {filename}")

    def load_from_json(self, filename):
        """Load flat parameters from a JSON config file into params_dict."""
        with open(filename, 'r') as f:
            data = json.load(f)
        params = data.get("params", {})
        drone_logger.info(f"Loaded {len(params)} parameters from {filename}")
        return params