from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from Mavlink_rx_handler import MavlinkHandler, SENSOR_FLAGS


@pytest.fixture
def handler():
    h = MavlinkHandler()
    # Avoid real connection
    h.is_connected = True
    # Fake mav_conn with nested mav attribute
    h.mav_conn = MagicMock()
    h.mav_conn.mav = MagicMock()
    h.target_system = 1
    h.target_component = 1
    return h


def test_snapshot_and_flush_rx_queue(handler):
    # preload some fake messages
    handler.rx_mav_msg.extend([
        {"mavpackettype": "HEARTBEAT", "foo": 1},
        {"mavpackettype": "GPS_RAW_INT", "bar": 2},
    ])

    handler.snapshot_rx_queue()
    assert handler.ai_mavlink_ctx["HEARTBEAT"]["foo"] == 1
    assert handler.ai_mavlink_ctx["GPS_RAW_INT"]["bar"] == 2

    handler.flush_rx_queue()
    assert list(handler.rx_mav_msg) == []


def test_get_latency_stats_empty(handler):
    handler.latency_history.clear()
    stats = handler.get_latency_stats()
    assert stats == {"current": 0, "avg": 0, "min": 0, "max": 0}


def test_get_latency_stats_populated(handler):
    handler.latency_history.extend([10.0, 20.0, 30.0])
    handler.latency_ms = 30.0
    stats = handler.get_latency_stats()
    assert stats["current"] == 30.0
    assert stats["avg"] == 20.0
    assert stats["min"] == 10.0
    assert stats["max"] == 30.0


def test_get_link_stats(handler, monkeypatch):
    # Setup counters and timestamp so that elapsed ~1s
    handler._pkt_count = 100
    handler._byte_count = 1000

    fake_now = handler._rate_timestamp + 1.0
    monkeypatch.setattr("time.time", lambda: fake_now)

    stats = handler.get_link_stats()
    # packets/sec and bytes/sec should both be ~1000/1, 100/1
    assert stats["pkt_rate"] == pytest.approx(100.0, rel=0.01)
    assert stats["byte_rate"] == pytest.approx(1000.0, rel=0.01)


def test_request_helpers_append_tx(handler):
    assert handler.request_data_stream() is True
    assert "DATA_STREAM_REQUEST" in handler.tx_mav_msg

    assert handler.request_autopilot_version() is True
    assert "VERSION_REQUEST" in handler.tx_mav_msg

    assert handler.request_parameter_list() is True
    assert "PARAM_REQUEST" in handler.tx_mav_msg


def test_update_parameter_success(handler):
    ok = handler.update_parameter("TEST_PARAM", 42)
    assert ok is True
    handler.mav_conn.mav.param_set_send.assert_called_once()
    assert any("PARAM_SET:TEST_PARAM" in s for s in handler.tx_mav_msg)


def test_update_parameter_failure(handler, monkeypatch):
    def boom(*args, **kwargs):  # noqa: D401
        raise RuntimeError("bad serial")

    handler.mav_conn.mav.param_set_send.side_effect = boom
    ok = handler.update_parameter("BAD_PARAM", 1)
    assert ok is False


def test_send_mavlink_command_unknown(handler):
    # Unknown command name should fail early
    ok = handler.send_mavlink_command_from_json({"command": "NOPE"})
    assert ok is False


def test_send_mavlink_command_success_ack(handler, monkeypatch):
    # Pretend ACK arrives with ACCEPTED
    cmd_name = "MAV_CMD_NAV_LAND"
    cmd_id = handler._mav_command_map[cmd_name]

    # Define a side effect for wait() that populates the ACK status
    def simulate_ack(*args, **kwargs):
        handler.command_ack_status[cmd_id] = 0  # MAV_RESULT_ACCEPTED
        return True

    # Mock the condition variable
    handler.command_ack_condition = MagicMock()
    handler.command_ack_condition.wait.side_effect = simulate_ack
    
    # We also need to mock __enter__/__exit__ because 'with self.command_ack_condition:' is used
    handler.command_ack_condition.__enter__.return_value = handler.command_ack_condition

    ok = handler.send_mavlink_command_from_json({"command": cmd_name, "param1": 1})
    assert ok is True
    handler.mav_conn.mav.command_long_send.assert_called_once()


def test_send_mavlink_command_timeout(handler, monkeypatch):
    cmd_name = "MAV_CMD_NAV_LAND"

    # Force no ACK by keeping command_ack_status empty and time.sleep fast
    monkeypatch.setattr("time.sleep", lambda _t: None)

    ok = handler.send_mavlink_command_from_json({"command": cmd_name})
    # In this case we may get False if it times out
    assert ok is False


def test_parse_firmware_info_and_decode_bitmask(handler, caplog):
    msg = SimpleNamespace(
        flight_sw_version=(1 << 24) | (2 << 16) | (3 << 8) | 4,
        board_version=(5 << 24) | (6 << 16),
        flight_custom_version=[ord(c) for c in "ABCD"] + [0] * 4,
        vendor_id=123,
        product_id=456,
        capabilities=sum(SENSOR_FLAGS.keys()),
    )

    handler.parse_firmware_info(msg)
    assert handler.firmware_data["firmware_version"].startswith("1.2.3")
    assert handler.firmware_data["board_version"] == "5.6"
    assert "ABCD" in handler.firmware_data["flight_custom_version"]
    assert handler.firmware_data["vendor_id"] == 123
    assert handler.firmware_data["product_id"] == 456

    # Now test decode_sensor_bitmask logs something for enabled sensors
    caplog.set_level("INFO")
    sys_status = SimpleNamespace(onboard_control_sensors_present=1 | 32)
    handler.decode_sensor_bitmask(sys_status)
    assert any("3D Gyro" in r.message for r in caplog.records)
    assert any("GPS" in r.message for r in caplog.records)
