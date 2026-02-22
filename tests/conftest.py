import json
import os
import time
from types import SimpleNamespace
import pytest
import web_server

@pytest.fixture(autouse=True)
def reset_globals(tmp_path, monkeypatch):
    """Reset web_server globals between tests and point CONFIGS_DIR to tmp."""
    # Fresh validator mock per test
    web_server.validator = SimpleNamespace(
        categorized_params={"Battery": {"BATT_LOW_VOLT": 10.5}, "Serial": {}, "GPS": {}, "Compass": {}, "IMU": {}, "RC": {}, "Motors": {}, "Flight Modes": {}, "Barometer": {}},
        params_dict={
            "BATT_LOW_VOLT": 10.5,
            "SERIAL1_PROTOCOL": 2,
            "SERIAL1_BAUD": 57,
            "RCMAP_ROLL": 1,
            "RCMAP_PITCH": 2,
            "RCMAP_THROTTLE": 3,
            "RCMAP_YAW": 4,
            "FLTMODE_CH": 5,
            "FLTMODE1": 0,
            "FLTMODE2": 2,
            "FLTMODE3": 5,
            "FLTMODE4": 6,
            "FLTMODE5": 9,
            "FLTMODE6": 16,
            "FS_THR_ENABLE": 1,
            "FS_THR_VALUE": 975,
            "FS_GCS_ENABLE": 0,
            "FS_OPTIONS": 0,
            "BATT_FS_LOW_ACT": 2,
            "BATT_FS_CRT_ACT": 1,
        },
        firmware_data={},
        hardware_validated=False,
        is_connected=True,
        log_directory=str(tmp_path / "logs"),
        log_list=[],
        connect=lambda p, b: True,
        disconnect=lambda: None,
        start_message_loop=lambda: None,
        request_data_stream=lambda: None,
        request_autopilot_version=lambda: None,
        request_parameter_list=lambda: None,
        update_socketio=lambda s: None,
        snapshot_rx_queue=lambda: None,
        ai_mavlink_ctx={},
        send_mavlink_command_from_json=lambda cmd, **kw: True,
        param_progress=0,
        param_count=0
    )
    web_server.validator.update_parameter = lambda name, value: web_server.validator.params_dict.__setitem__(name, value) is None or True
    web_server.validator.load_from_json = lambda filename: json.load(open(filename, "r", encoding="utf-8")).get("params", {})
    (tmp_path / "logs").mkdir(exist_ok=True)

    # Patch CONFIGS_DIR
    try:
        monkeypatch.setattr(web_server, "CONFIGS_DIR", str(tmp_path / "configs"))
    except AttributeError:
        web_server.CONFIGS_DIR = str(tmp_path / "configs")
    (tmp_path / "configs").mkdir(exist_ok=True)

    # Reset mavlink_buffer and connected_clients
    web_server.mavlink_buffer = {}
    web_server.connected_clients = set()

    yield

    web_server.validator = None

@pytest.fixture
def client():
    web_server.app.config.update({"TESTING": True})
    return web_server.app.test_client()
