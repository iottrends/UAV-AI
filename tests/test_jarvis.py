import pytest
import json
from unittest.mock import MagicMock, patch
from JARVIS import ask_gemini, _compute_param_delta, _load_chat_history, _save_chat_history, _append_to_history

@pytest.fixture(autouse=True)
def mock_dependencies():
    """
    Reset JARVIS module's global state for each test.
    """
    import JARVIS as _jarvis_mod
    _jarvis_mod.jarvis_mav_data.clear()
    _jarvis_mod._conversation_history = []
    _jarvis_mod._params_sent = False
    _jarvis_mod._last_seen_params = None
    _jarvis_mod._request_timestamps = []
    _jarvis_mod._total_input_tokens = 0
    _jarvis_mod._total_output_tokens = 0
    _jarvis_mod._total_requests = 0

    with patch('os.getenv', return_value="fake_api_key"), \
         patch('dotenv.load_dotenv'):
        yield

def test_compute_param_delta_no_changes():
    old = {"P1": 1, "P2": 2}
    new = {"P1": 1, "P2": 2}
    assert _compute_param_delta(old, new) is None

def test_compute_param_delta_added_param():
    old = {"P1": 1}
    new = {"P1": 1, "P2": 2}
    delta = _compute_param_delta(old, new)
    assert delta == {"P2": {"old": "<new>", "new": 2}}

def test_compute_param_delta_changed_param():
    old = {"P1": 1, "P2": 2}
    new = {"P1": 1, "P2": 3}
    delta = _compute_param_delta(old, new)
    assert delta == {"P2": {"old": 2, "new": 3}}

def test_compute_param_delta_removed_param():
    old = {"P1": 1, "P2": 2}
    new = {"P1": 1}
    delta = _compute_param_delta(old, new)
    assert delta == {"P2": {"old": 2, "new": "<removed>"}}

def test_compute_param_delta_mixed_changes():
    old = {"P1": 1, "P2": 2, "P3": 3}
    new = {"P1": 10, "P3": 3}
    delta = _compute_param_delta(old, new)
    assert delta == {
        "P1": {"old": 1, "new": 10},
        "P2": {"old": 2, "new": "<removed>"}
    }

@patch('JARVIS._dispatch')
def test_ask_gemini_action_command(mock_dispatch):
    # _dispatch returns (response_text, input_tokens, output_tokens)
    mock_response_json = json.dumps({
        "intent": "action",
        "message": "Arming motors.",
        "fix_command": {"command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 1}
    })
    mock_dispatch.return_value = (mock_response_json, 10, 5)
    
    result = ask_gemini("arm the drone", {}, {})
    assert result["intent"] == "action"
    assert "Arming motors." in result["message"]
    assert result["fix_command"]["command"] == "MAV_CMD_COMPONENT_ARM_DISARM"
    mock_dispatch.assert_called_once()

@patch('JARVIS._dispatch')
def test_ask_gemini_status_query(mock_dispatch):
    mock_response_json = json.dumps({
        "intent": "status",
        "message": "Battery is 16.0V, 80% remaining.",
        "fix_command": None
    })
    mock_dispatch.return_value = (mock_response_json, 10, 5)

    result = ask_gemini("what is my battery", {}, {"SYS_STATUS": {"voltage_battery": 16000, "battery_remaining": 80}})
    assert result["intent"] == "status"
    assert "Battery is 16.0V" in result["message"]
    assert result["fix_command"] is None
    mock_dispatch.assert_called_once()

@patch('JARVIS._dispatch')
def test_ask_gemini_diagnostic_query(mock_dispatch):
    mock_response_json = json.dumps({
        "intent": "diagnostic",
        "message": "Compass seems off, consider recalibrating.",
        "fix_command": None,
        "recommended_param": ["COMPASS_CAL_FIT", "VERY_TIGHT"]
    })
    mock_dispatch.return_value = (mock_response_json, 10, 5)

    result = ask_gemini("why is my compass off", {"COMPASS_USE": 1}, {})
    assert result["intent"] == "diagnostic"
    assert "Compass seems off" in result["message"]
    assert result["fix_command"] is None
    mock_dispatch.assert_called_once()

@patch('JARVIS._dispatch')
def test_ask_gemini_with_param_delta(mock_dispatch):
    # Setup mock for the first call
    mock_dispatch.return_value = (json.dumps({
        "intent": "info",
        "message": "Parameters initialized.",
        "fix_command": None
    }), 10, 5)

    # First call to initialize _last_seen_params
    result1 = ask_gemini("initial query", {"BATT_VOLT_MIN": 10.0}, {})
    assert result1["intent"] == "info"
    mock_dispatch.assert_called_once()

    # Reset mock for second call
    mock_dispatch.reset_mock()
    mock_dispatch.return_value = (json.dumps({
        "intent": "status",
        "message": "Parameters updated and battery is fine.",
        "fix_command": None
    }), 10, 5)

    # Second call with updated parameters
    updated_params = {"BATT_VOLT_MIN": 9.5, "GPS_TYPE": 1}
    result2 = ask_gemini("check params", updated_params, {})
    assert result2["intent"] == "status"
    
    mock_dispatch.assert_called_once()
    # Check that the prompt passed to _dispatch contains "Parameter Update"
    called_args = mock_dispatch.call_args
    prompt_arg = called_args[0][1] # arg 1 is prompt (arg 0 is provider)
    assert "Parameter Update" in prompt_arg
    assert "BATT_VOLT_MIN" in prompt_arg
    assert "GPS_TYPE" in prompt_arg

@patch('JARVIS._dispatch')
def test_ask_gemini_invalid_json_response(mock_dispatch):
    mock_dispatch.return_value = ("This is not JSON {invalid}", 10, 5)

    result = ask_gemini("invalid query", {}, {})
    assert result is not None
    assert "error" in result
    assert "Invalid JSON response" in result["error"]
    assert "raw_response" in result
    mock_dispatch.assert_called_once()

@patch('JARVIS._save_chat_history')
@patch('JARVIS._load_chat_history', return_value=[])
def test_append_to_history(mock_load, mock_save):
    query = "test query"
    response = {"message": "test response"}
    _append_to_history(query, response)
    
    mock_save.assert_called_once()
    saved_history = mock_save.call_args[0][0]
    assert len(saved_history) == 1
    assert saved_history[0]["query"] == query
    assert saved_history[0]["parsed_response"] == response
    assert "timestamp" in saved_history[0]