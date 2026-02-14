import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from drone_validator import DroneValidator


@pytest.fixture
def validator():
    # Avoid touching real MavlinkHandler internals; its __init__ is simple, so we allow it.
    return DroneValidator()


def test_categorize_params_basic_buckets(validator):
    params = {
        "SYSID_THISMAV": 1,
        "GPS_TYPE": 1,
        "BATT_LOW_VOLT": 10.5,
        "SERIAL1_PROTOCOL": 2,
        "COMPASS_USE": 1,
        "INS_GYRO_FILTER": 20,
        "WPNAV_SPEED": 500,
        "MOT_SPIN_MIN": 0.15,
        "SERVO1_FUNCTION": 33,
        "RC1_MIN": 1000,
        "FLTMODE1": 3,
        "FS_BATT_ENABLE": 1,
        "EK3_IMU1_ZOFF": 0.1,
        "AHRS_EKF_TYPE": 3,
        "ATC_RAT_PIT_P": 0.15,
        "PILOT_ACCEL_Z": 250,
        "LAND_SPEED": 30,
        "BARO2_TYPE": 1,
        "RNGFND1_TYPE": 1,
        "RPM_MAX": 10000,
        "NTF_LED_BRIGHTNESS": 3,
        "OSD_TYPE": 1,
        "LOG_BITMASK": 65535,
        "SCHED_LOOP_RATE": 400,
        "SR0_POSITION": 2,
        "FOO_CUSTOM": 123,
    }

    validator.categorize_params(params)

    cats = validator.categorized_params

    assert "SYSID_THISMAV" in cats["System"]
    assert "GPS_TYPE" in cats["GPS"]
    assert "BATT_LOW_VOLT" in cats["Battery"]
    assert "SERIAL1_PROTOCOL" in cats["Serial"]
    assert "COMPASS_USE" in cats["Compass"]
    assert "INS_GYRO_FILTER" in cats["IMU"]
    assert "WPNAV_SPEED" in cats["Navigation"]
    assert "MOT_SPIN_MIN" in cats["Motors"]
    assert "SERVO1_FUNCTION" in cats["Servos"]
    assert "RC1_MIN" in cats["RC"]
    assert "FLTMODE1" in cats["Flight Modes"]
    # FS_BATT_ENABLE contains 'BATT', so it is categorized under Battery, not Safety
    assert "FS_BATT_ENABLE" in cats["Battery"]
    assert "EK3_IMU1_ZOFF" in cats["IMU"] or "EK3_IMU1_ZOFF" in cats["EKF"]
    assert "AHRS_EKF_TYPE" in cats["EKF"]
    assert "ATC_RAT_PIT_P" in cats["Control"]
    assert "PILOT_ACCEL_Z" in cats["Pilot"]
    assert "LAND_SPEED" in cats["Landing"]
    assert "BARO2_TYPE" in cats["Barometer"]
    assert "RNGFND1_TYPE" in cats["RangeFinder"]
    assert "RPM_MAX" in cats["RPM"]
    assert "NTF_LED_BRIGHTNESS" in cats["Notifications"]
    assert "OSD_TYPE" in cats["OSD"]
    assert "LOG_BITMASK" in cats["Logging"]
    assert "SCHED_LOOP_RATE" in cats["Scheduler"]
    # SR prefix is used for Streaming bucket
    assert "SR0_POSITION" in cats["Streaming"]
    # Unknown key should fall into Miscellaneous
    assert "FOO_CUSTOM" in cats["Miscellaneous"]


def test_get_param_value_with_and_without_default(validator):
    params = {"FOO": 1}

    assert validator.get_param_value(params, "FOO") == 1
    assert validator.get_param_value(params, "BAR") is None
    assert validator.get_param_value(params, "BAR", default=42) == 42


@patch("drone_validator.DFReader.DFReader_binary")
def test_parse_blackbox_log_happy_path(mock_dfreader_binary, validator):
    # Create a fake log reader that yields a few messages then None
    messages = [
        SimpleNamespace(get_type=lambda: "GYR", GyrX=1.0, GyrY=2.0, GyrZ=3.0),
        SimpleNamespace(get_type=lambda: "ACC", AccX=4.0, AccY=5.0, AccZ=6.0),
        SimpleNamespace(get_type=lambda: "MOT", Mot1=100, Mot2=110, Mot3=120, Mot4=130),
        SimpleNamespace(get_type=lambda: "GPS", Lat=10, Lng=20, Alt=30, NSats=7),
        SimpleNamespace(get_type=lambda: "ATT", Roll=0.1, Pitch=0.2, Yaw=0.3),
        None,
    ]

    class FakeLog:
        def __init__(self, msgs):
            self._msgs = iter(msgs)

        def recv_msg(self):
            return next(self._msgs, None)

    mock_dfreader_binary.return_value = FakeLog(messages)

    validator.parse_blackbox_log("dummy.bin", log_id="log1")

    assert "log1" in validator.blackbox_logs
    data = validator.blackbox_logs["log1"]

    assert data["IMU"]["gyro_x"] == [1.0]
    assert data["IMU"]["acc_y"] == [5.0]
    assert data["Motors"]["motor4"] == [130]
    assert data["GPS"]["satellites"] == [7]
    assert data["Attitude"]["yaw"] == [0.3]


def test_save_and_load_json_roundtrip(tmp_path, validator):
    # Pre-populate categorized_params with a small sample
    validator.categorized_params["System"] = {"FRAME_CLASS": 1}

    out_file = tmp_path / "params.json"
    validator.save_to_json(str(out_file))

    # File format: {"System": { ... }, ... }. load_from_json expects {"params": {...}}
    # So simulate that layout for load_from_json.
    with open(out_file, "r", encoding="utf-8") as f:
        raw = json.load(f)

    wrapped = {"params": raw.get("System", {})}
    wrapped_file = tmp_path / "wrapped.json"
    with open(wrapped_file, "w", encoding="utf-8") as f:
        json.dump(wrapped, f)

    loaded = validator.load_from_json(str(wrapped_file))
    assert loaded == {"FRAME_CLASS": 1}
