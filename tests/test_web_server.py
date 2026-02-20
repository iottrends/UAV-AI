import json
import os
from types import SimpleNamespace

import pytest

import web_server


@pytest.fixture(autouse=True)
def reset_globals(tmp_path, monkeypatch):
    """Reset web_server globals between tests and point CONFIGS_DIR to tmp."""
    # Fresh validator mock per test
    web_server.validator = SimpleNamespace(
        categorized_params={"Battery": {"BATT_LOW_VOLT": 10.5}},
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
        is_connected=True,
        log_directory=str(tmp_path / "logs"),
        log_list=[],
    )
    web_server.validator.update_parameter = lambda name, value: web_server.validator.params_dict.__setitem__(name, value) is None or True
    web_server.validator.load_from_json = lambda filename: json.load(open(filename, "r", encoding="utf-8")).get("params", {})
    (tmp_path / "logs").mkdir(exist_ok=True)

    # Patch CONFIGS_DIR
    # CONFIGS_DIR may or may not exist as an attribute depending on import timing
    try:
        monkeypatch.setattr(web_server, "CONFIGS_DIR", str(tmp_path / "configs"))
    except AttributeError:
        web_server.CONFIGS_DIR = str(tmp_path / "configs")
    (tmp_path / "configs").mkdir(exist_ok=True)

    yield

    web_server.validator = None


@pytest.fixture
def client():
    web_server.app.config.update({"TESTING": True})
    return web_server.app.test_client()


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
    def fake_send(cmd):
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
    web_server.validator.send_mavlink_command_from_json = lambda _cmd: False
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
