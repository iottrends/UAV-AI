import pytest
import re
from unittest.mock import MagicMock
from copilot import try_fast_command, _normalize

@pytest.fixture
def mock_mavlink_buffer():
    """Fixture for a mock mavlink_buffer."""
    return {
        "HEARTBEAT": {"base_mode": 128, "custom_mode": 4}, # Armed, Guided
        "GPS_RAW_INT": {"fix_type": 3, "satellites_visible": 10},
        "SYS_STATUS": {"voltage_battery": 16000, "current_battery": 10000, "battery_remaining": 80},
        "VFR_HUD": {"alt": 50.0, "climb": 2.5}
    }

def test_normalize():
    assert _normalize(" Arm the drone! ") == "arm the drone"
    assert _normalize("What is my GPS status?") == "what is my gps status"
    assert _normalize("POSITION-HOLD") == "positionhold" # Punctuation removed

@pytest.mark.parametrize("query, expected_command, expected_response_part", [
    ("arm the drone", "MAV_CMD_COMPONENT_ARM_DISARM", "Arming motors."),
    ("disarm", "MAV_CMD_COMPONENT_ARM_DISARM", "Disarming motors."),
    ("land now", "MAV_CMD_NAV_LAND", "Landing now."),
    ("position hold", "MAV_CMD_DO_SET_MODE", "Switching to Position Hold."),
    ("RTL", "MAV_CMD_NAV_RETURN_TO_LAUNCH", "Returning to launch."),
    ("go home", "MAV_CMD_NAV_RETURN_TO_LAUNCH", "Returning to launch."),
    ("loiter", "MAV_CMD_DO_SET_MODE", "Switching to Loiter."),
    ("stabilize", "MAV_CMD_DO_SET_MODE", "Switching to Stabilize."),
    ("guided mode", "MAV_CMD_DO_SET_MODE", "Switching to Guided mode."),
    ("brake", "MAV_CMD_DO_SET_MODE", "Braking"),
    ("alt hold", "MAV_CMD_DO_SET_MODE", "Switching to Altitude Hold."),
])
def test_try_fast_command_commands(query, expected_command, expected_response_part, mock_mavlink_buffer):
    result = try_fast_command(query, mock_mavlink_buffer)
    assert result is not None
    assert result["fix_command"]["command"] == expected_command
    assert expected_response_part in result["response"]

@pytest.mark.parametrize("query, expected_response_part", [
    ("gps status", "GPS: 3D Fix, 10 satellites visible."),
    ("what is my battery", "Battery: 16.00V, 10.0A, 80% remaining."),
    ("how high am i", "Altitude: 50.0m, climb rate: 2.5m/s."),
    ("what mode am i in", "Current mode: GUIDED, state: ARMED."),
])
def test_try_fast_command_status_queries(query, expected_response_part, mock_mavlink_buffer):
    result = try_fast_command(query, mock_mavlink_buffer)
    assert result is not None
    assert "fix_command" not in result # Status queries should not have fix_command
    assert expected_response_part in result["response"]

def test_try_fast_command_no_match(mock_mavlink_buffer):
    query = "analyze my compass deviation"
    result = try_fast_command(query, mock_mavlink_buffer)
    assert result is None

def test_try_fast_command_empty_buffer():
    query = "gps status"
    result = try_fast_command(query, {})
    assert result is not None
    assert "No GPS data available yet." in result["response"]

    query = "battery status"
    result = try_fast_command(query, {})
    assert result is not None
    assert "No battery data available yet." in result["response"]
