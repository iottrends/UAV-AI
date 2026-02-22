import json
import os
from types import SimpleNamespace

import pytest

import web_server


def test_parameters_get_returns_categorized_params(client):
    resp = client.get("/api/parameters")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["Battery"]["BATT_LOW_VOLT"] == 10.5


def test_parameters_post_success_updates_params_and_verifies(client):
    # Arrange: validator will treat update_parameter as success and params_dict is mutable
    def fake_update(name, value):  # noqa: D401
        web_server.validator.params_dict[name] = value
        return True

    web_server.validator.update_parameter = fake_update

    payload = {"BATT_LOW_VOLT": 10.2}
    resp = client.post(
        "/api/parameters",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "BATT_LOW_VOLT" in data["updated"]
    assert web_server.validator.params_dict["BATT_LOW_VOLT"] == 10.2


def test_parameters_post_timeout_on_mismatch(client, monkeypatch):
    # Force update_parameter to succeed but never update params_dict -> triggers TimeoutError
    def fake_update(name, value):
        return True

    web_server.validator.update_parameter = fake_update

    # Speed up the 0.1s sleeps
    monkeypatch.setattr("time.sleep", lambda _t: None)

    payload = {"BATT_LOW_VOLT": 9.9}
    resp = client.post(
        "/api/parameters",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 500
    data = resp.get_json()
    assert data["status"] == "error"
    assert "Timeout waiting for parameter updates" in data["message"]


def test_firmware_info_success_and_missing(client):
    # First, with no firmware_data
    resp = client.get("/api/firmware")
    assert resp.status_code == 404

    # Now populate firmware_data
    web_server.validator.firmware_data = {"fw": "1.2.3"}
    resp2 = client.get("/api/firmware")
    assert resp2.status_code == 200
    data = resp2.get_json()
    assert data["firmware"]["fw"] == "1.2.3"


def test_save_and_list_configs(client):
    # validator.params_dict already has some params from reset_globals
    payload = {"name": "MyConfig"}
    resp = client.post(
        "/api/configs",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    filename = data["filename"]

    # List configs
    resp2 = client.get("/api/configs")
    assert resp2.status_code == 200
    data2 = resp2.get_json()
    names = [c["name"] for c in data2["configs"]]
    assert "MyConfig" in names

    # Apply config: should detect no changes (since params_dict matches)
    resp3 = client.post(
        "/api/configs/apply",
        data=json.dumps({"filename": filename}),
        content_type="application/json",
    )
    assert resp3.status_code == 200
    data3 = resp3.get_json()
    assert data3["changed"] == 0


def test_delete_config(client):
    # Create a dummy config file
    cfg_path = f"{web_server.CONFIGS_DIR}/to_delete.json"
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump({"name": "to_delete"}, f)

    resp = client.delete("/api/configs/to_delete.json")
    assert resp.status_code == 200
    assert not os.path.exists(cfg_path)


def test_calibrate_endpoint_success_and_error(client):
    # Inject a simple send_mavlink_command_from_json behaviour
    def fake_send(cmd, timeout_seconds=5):
        # Accept only gyro
        return cmd.get("param1") == 1

    web_server.validator.send_mavlink_command_from_json = fake_send

    # Success case
    resp = client.post(
        "/api/calibrate",
        data=json.dumps({"type": "gyro"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"

    # Error: unknown type
    resp2 = client.post(
        "/api/calibrate",
        data=json.dumps({"type": "unknown"}),
        content_type="application/json",
    )
    assert resp2.status_code == 400

    # Error: FC/NACK
    web_server.validator.send_mavlink_command_from_json = lambda _cmd, **kwargs: False
    resp3 = client.post(
        "/api/calibrate",
        data=json.dumps({"type": "gyro"}),
        content_type="application/json",
    )
    assert resp3.status_code == 500


def test_get_config_domain_serial_ports(client):
    resp = client.get("/api/config/domains/serial_ports")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["domain"] == "serial_ports"
    assert "SERIAL1_PROTOCOL" in data["params"]


def test_preview_config_domain_rc_mapping_duplicate_invalid(client):
    payload = {"changes": {"RCMAP_ROLL": 2, "RCMAP_PITCH": 2}}
    resp = client.post(
        "/api/config/domains/rc_mapping/preview",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "error"
    assert any("unique" in item["reason"] for item in data["invalid"])


def test_apply_config_domain_serial_ports_success(client):
    payload = {"changes": {"SERIAL1_PROTOCOL": 23, "SERIAL1_BAUD": 115}}
    resp = client.post(
        "/api/config/domains/serial_ports/apply",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["verified"] == 2
    assert web_server.validator.params_dict["SERIAL1_PROTOCOL"] == 23
    assert web_server.validator.params_dict["SERIAL1_BAUD"] == 115


def test_apply_config_domain_partial_verify_timeout(client):
    def fake_update(name, value):
        # Simulate TX success but no read-back update
        return True

    web_server.validator.update_parameter = fake_update
    payload = {"changes": {"SERIAL1_PROTOCOL": 23}, "verify_timeout_ms": 10}
    resp = client.post(
        "/api/config/domains/serial_ports/apply",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 207
    data = resp.get_json()
    assert data["status"] == "partial"
    assert len(data["mismatched"]) == 1


def test_preview_config_domain_flight_modes_invalid_mode_id(client):
    payload = {"changes": {"FLTMODE1": 999}}
    resp = client.post(
        "/api/config/domains/flight_modes/preview",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "error"
    assert any(item["param"] == "FLTMODE1" for item in data["invalid"])


def test_apply_config_domain_flight_modes_success(client):
    payload = {"changes": {"FLTMODE_CH": 6, "FLTMODE6": 21}}
    resp = client.post(
        "/api/config/domains/flight_modes/apply",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["verified"] == 2
    assert web_server.validator.params_dict["FLTMODE_CH"] == 6
    assert web_server.validator.params_dict["FLTMODE6"] == 21


def test_preview_config_domain_failsafe_out_of_range(client):
    payload = {"changes": {"FS_THR_VALUE": 3000}}
    resp = client.post(
        "/api/config/domains/failsafe/preview",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "error"
    assert any(item["param"] == "FS_THR_VALUE" for item in data["invalid"])


def test_apply_config_domain_failsafe_success(client):
    payload = {"changes": {"FS_THR_ENABLE": 1, "FS_THR_VALUE": 960, "BATT_FS_LOW_ACT": 1}}
    resp = client.post(
        "/api/config/domains/failsafe/apply",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["verified"] == 2
    assert web_server.validator.params_dict["FS_THR_VALUE"] == 960


def test_get_config_domain_aux_functions(client):
    resp = client.get("/api/config/domains/aux_functions")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["domain"] == "aux_functions"
    assert "RC7_OPTION" in data["params"]
    assert "function_catalog" in data["metadata"]


def test_preview_config_domain_aux_functions_invalid_range(client):
    payload = {"changes": {"RC7_OPTION": 999}}
    resp = client.post(
        "/api/config/domains/aux_functions/preview",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["status"] == "error"
    assert any(item["param"] == "RC7_OPTION" for item in data["invalid"])


def test_apply_config_domain_aux_functions_success(client):
    payload = {"changes": {"RC7_OPTION": 41, "RC8_OPTION": 30}}
    resp = client.post(
        "/api/config/domains/aux_functions/apply",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert data["verified"] == 2
    assert web_server.validator.params_dict["RC7_OPTION"] == 41
    assert web_server.validator.params_dict["RC8_OPTION"] == 30


def test_motor_test_success(client):
    # Mock armed state to False
    web_server.mavlink_buffer["HEARTBEAT"] = {"base_mode": 0}
    web_server.validator.send_mavlink_command_from_json = lambda cmd: True

    payload = {"motor": 1, "throttle": 15, "duration": 2}
    resp = client.post(
        "/api/motor_test",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    assert "test started" in resp.get_json()["message"]


def test_motor_test_blocked_when_armed(client):
    # Mock armed state to True (bit 128)
    web_server.mavlink_buffer["HEARTBEAT"] = {"base_mode": 128}

    payload = {"motor": 1, "throttle": 15, "duration": 2}
    resp = client.post(
        "/api/motor_test",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "is ARMED" in resp.get_json()["message"]


from unittest.mock import patch

def test_get_firmware_manifest_cached(client, tmp_path, monkeypatch):
    # Setup cache file
    cache_dir = tmp_path / "firmware_cache"
    cache_dir.mkdir()
    manifest_path = cache_dir / "manifest.json"
    manifest_data = {"firmware": [{"format": "apj", "board_id": 123, "vehicletype": "Copter", "url": "http://foo.apj"}]}
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest_data, f)
    
    # In web_server.py, get_firmware_manifest uses _get_firmware_cache_dir() 
    # which uses _writable_path('firmware_cache')
    # We'll monkeypatch FIRMWARE_CACHE_DIR directly if it exists, or the function
    monkeypatch.setattr(web_server, "FIRMWARE_CACHE_DIR", str(cache_dir))
    
    # We also need to avoid real URL fetch
    with patch("urllib.request.urlopen") as mock_url:
        resp = client.get("/api/firmware/manifest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "Copter" in data["firmware"]
        assert mock_url.called is False # Should use file cache


def test_download_firmware_invalid_url(client):
    resp = client.post(
        "/api/firmware/download",
        data=json.dumps({"url": "http://malicious.com/virus.apj"}),
        content_type="application/json"
    )
    assert resp.status_code == 400
    assert "must be from firmware.ardupilot.org" in resp.get_json()["message"]


def test_update_system_health_logic():
    # Setup fake validator and mavlink_buffer
    web_server.validator.hardware_validated = True
    web_server.validator.categorized_params = {
        "Battery": {"BATT_LOW_VOLT": 10.5, "BATT_CRT_VOLT": 10.0},
        "GPS": {"GPS_TYPE": 1},
        "Compass": {"COMPASS_ENABLE": 1, "COMPASS_USE": 1},
        "IMU": {},
        "RC": {"RC_PROTOCOLS": 1},
        "Motors": {"MOT_PWM_TYPE": 6}, # DShot600
        "Serial": {"SERIAL1_PROTOCOL": 23}, # ELRS
        "Flight Modes": {"FLTMODE_CH": 5, "FLTMODE1": 0},
        "Barometer": {}
    }
    
    web_server.mavlink_buffer = {
        "SYS_STATUS": {"voltage_battery": 12000, "current_battery": 500, "battery_remaining": 80},
        "GPS_RAW_INT": {"fix_type": 3, "satellites_visible": 10, "lat": 12345678, "lon": 87654321},
        "HEARTBEAT": {"base_mode": 0, "custom_mode": 0},
        "VFR_HUD": {"alt": 10, "heading": 180, "climb": 0},
        "SERVO_OUTPUT_RAW": {"servo1_raw": 1100, "servo2_raw": 1100, "servo3_raw": 1100, "servo4_raw": 1100},
        "RC_CHANNELS": {"chan1_raw": 1500, "chan2_raw": 1500, "chan3_raw": 1500, "chan4_raw": 1500, "rssi": 100, "chancount": 16}
    }
    
    # Run update
    web_server.update_system_health()
    
    # Check global result
    health = web_server.last_system_health
    assert health["score"] == 100
    assert health["readiness"] == "READY"
    assert health["battery"]["voltage"] == 12.0
    assert health["gps"]["fix_type"] == 3
    assert health["esc_protocol"] == "DShot600"
    assert health["rc_uart"] == "SERIAL1"
    assert health["overall_readiness"] == "READY"
