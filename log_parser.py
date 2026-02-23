import os
import math
import logging
from pymavlink import mavutil
from pymavlink.DFReader import DFReader_binary, DFReader_text

logger = logging.getLogger('log_parser')

# Key message types for ArduPilot log analysis
KEY_MSG_TYPES = {
    'ATT', 'GPS', 'CTUN', 'VIBE', 'MOT', 'BAT', 'BARO', 'GYR', 'ACC',
    'MAG', 'MODE', 'MSG', 'ERR', 'RCIN', 'RCOU', 'PARM', 'IMU', 'NKF1',
    'NKF2', 'POWR', 'CMD', 'EV', 'PM', 'CURR', 'RAD', 'TERRAIN',
    'RATE',  # PID actual vs desired rates — needed for FFT/PID tracking analysis
}

MAX_POINTS = 500


class LogParser:
    """Parses ArduPilot .bin (dataflash) and .tlog (telemetry) log files."""

    def __init__(self):
        self.filepath = None
        self.filename = None
        self.parsed_data = {}   # msg_type -> list of dicts
        self.msg_counts = {}    # msg_type -> int
        self.msg_fields = {}    # msg_type -> list of field names
        self._is_parsed = False

    def parse(self, filepath):
        """Parse a log file. Auto-detects .bin vs .tlog format."""
        self.filepath = filepath
        self.filename = os.path.basename(filepath)
        self.parsed_data = {}
        self.msg_counts = {}
        self.msg_fields = {}
        self._is_parsed = False

        ext = os.path.splitext(filepath)[1].lower()
        logger.info(f"Parsing log file: {filepath} (format: {ext})")

        if ext == '.bin':
            self._parse_bin(filepath)
        elif ext == '.tlog':
            self._parse_tlog(filepath)
        else:
            raise ValueError(f"Unsupported log format: {ext}. Use .bin or .tlog")

        self._is_parsed = True
        total_msgs = sum(self.msg_counts.values())
        logger.info(f"Parsed {total_msgs} messages across {len(self.msg_counts)} types")
        return self.get_summary()

    def _parse_bin(self, filepath):
        """Parse a .bin (dataflash) log using DFReader."""
        log = DFReader_binary(filepath)
        self._iterate_messages(log)

    def _parse_tlog(self, filepath):
        """Parse a .tlog (telemetry) log using mavutil."""
        log = mavutil.mavlink_connection(filepath)
        self._iterate_messages(log)

    def _iterate_messages(self, log):
        """Iterate through all messages in a log and store them."""
        while True:
            try:
                msg = log.recv_msg()
            except Exception:
                break
            if msg is None:
                break

            msg_type = msg.get_type()
            if msg_type in ('FMT', 'FMTU', 'MULT', 'ISBD', 'ISBH'):
                continue  # skip format/metadata messages

            # Track counts
            self.msg_counts[msg_type] = self.msg_counts.get(msg_type, 0) + 1

            # Store data for key types (keep all, downsample on retrieval)
            if msg_type in KEY_MSG_TYPES or self.msg_counts[msg_type] <= 10:
                if msg_type not in self.parsed_data:
                    self.parsed_data[msg_type] = []

                try:
                    d = msg.to_dict()
                    # Remove mavpackettype key added by pymavlink
                    d.pop('mavpackettype', None)
                    # Sanitize values for JSON serialization
                    for k, v in d.items():
                        if isinstance(v, bytes):
                            try:
                                d[k] = v.decode('utf-8', errors='replace')
                            except Exception:
                                d[k] = str(v)
                        elif isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                            d[k] = None
                    self.parsed_data[msg_type].append(d)

                    # Track fields from first message
                    if msg_type not in self.msg_fields:
                        self.msg_fields[msg_type] = list(d.keys())
                except Exception:
                    pass

    def get_summary(self):
        """Return a summary of the parsed log for Gemini context."""
        if not self._is_parsed:
            return {"error": "No log parsed yet"}

        summary = {
            "filename": self.filename,
            "total_messages": sum(self.msg_counts.values()),
            "message_types": {},
        }

        for msg_type, count in sorted(self.msg_counts.items()):
            entry = {"count": count}
            if msg_type in self.msg_fields:
                entry["fields"] = self.msg_fields[msg_type]
            # Include sample values from first message
            if msg_type in self.parsed_data and self.parsed_data[msg_type]:
                entry["sample"] = self.parsed_data[msg_type][0]
            summary["message_types"][msg_type] = entry

        return summary

    def get_message_data(self, msg_type, max_points=MAX_POINTS):
        """Return parsed data for a message type, downsampled if needed."""
        if not self._is_parsed:
            return []

        data = self.parsed_data.get(msg_type, [])
        if len(data) <= max_points:
            return data

        # Downsample with uniform stride
        stride = len(data) / max_points
        return [data[int(i * stride)] for i in range(max_points)]

    def get_message_types(self):
        """Return list of available message types."""
        return sorted(self.msg_counts.keys())

    def get_fields_for_type(self, msg_type):
        """Return field names for a message type."""
        return self.msg_fields.get(msg_type, [])
