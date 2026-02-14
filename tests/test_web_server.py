import json
from types import SimpleNamespace

import pytest

import web_server


@pytest.fixture(autouse=True)
def reset_globals(tmp_path, monkeypatch):
    """Reset web_server globals between tests and point CONFIGS_DIR to tmp."""
    # Fresh validator mock per test
    web_server.validator = SimpleNamespace(
        categorized_params={"Battery": {"BATT_LOW_VOLT": 10.5}},
        params_dict={"BATT_LOW_VOLT": 10.5},
        firmware_data={},
        is_connected=True,
        log_directory=str(tmp_path / "logs"),
        log_list=[],
    )
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
