import pytest
import json
from unittest.mock import MagicMock, patch
from JARVIS import ask_gemini, SYSTEM_INSTRUCTION, QUERY_TEMPLATE, PARAM_UPDATE_TEMPLATE, _compute_param_delta, _ensure_model, _load_chat_history, _save_chat_history, _append_to_history

# A helper to create mock response objects consistently, now ensuring all parts are MagicMocks with explicit primitive attributes
def create_mock_response_object(response_text, prompt_token_count=10, candidates_token_count=5):
    mock_response = MagicMock()
    mock_response.text = str(response_text) # Explicitly ensure it's a string
    # Explicitly create MagicMocks for usage_metadata with primitive attributes
    mock_response.usage_metadata = MagicMock(
        prompt_token_count=int(prompt_token_count),
        candidates_token_count=int(candidates_token_count)
    )
    return mock_response

@pytest.fixture(autouse=True)
def mock_generate_content_method(): # Renamed fixture to indicate it yields the mocked method
    """
    Sets up mocks and resets JARVIS module's global state for each test.
    Patches genai.GenerativeModel to return a mock instance, and then patches
    the `generate_content` method of that mock instance.
    Yields the mocked `generate_content` method itself for direct configuration by tests.
    """
    # Reset JARVIS module's internal state globals (must set on module, not test globals)
    import JARVIS as _jarvis_mod
    _jarvis_mod.jarvis_mav_data.clear()
    _jarvis_mod._model = None
    _jarvis_mod._cached_params = None
    _jarvis_mod._last_seen_params = None
    _jarvis_mod._request_timestamps = []
    _jarvis_mod._total_input_tokens = 0
    _jarvis_mod._total_output_tokens = 0
    _jarvis_mod._total_requests = 0

    # Create the specific mock instance that JARVIS._model will become
    _mock_model_instance = MagicMock()
    _mock_model_instance._system_instruction = MagicMock(parts=[MagicMock(text=SYSTEM_INSTRUCTION)])

    # Patch genai.GenerativeModel *class* within JARVIS module to return our specific instance
    with patch('JARVIS.genai.GenerativeModel', return_value=_mock_model_instance) as MockGenerativeModelClass, \
         patch('os.getenv', return_value="fake_api_key") as mock_getenv, \
         patch('dotenv.load_dotenv') as mock_load_dotenv, \
         patch('google.generativeai.configure') as mock_genai_configure:
        
        # Now, patch the 'generate_content' method *on our specific mock instance*
        # This ensures we're mocking the exact method that `_model.generate_content` will call in JARVIS.py
        with patch.object(_mock_model_instance, 'generate_content') as _mock_generate_content_method:
            # Yield the mocked generate_content method itself.
            # Tests will receive this method to configure its return_value.
            yield _mock_generate_content_method


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

def test_ask_gemini_action_command(mock_generate_content_method): # Receive the mocked method
    mock_generate_content_method.return_value = create_mock_response_object(json.dumps({
        "intent": "action",
        "message": "Arming motors.",
        "fix_command": {"command": "MAV_CMD_COMPONENT_ARM_DISARM", "param1": 1}
    }))
    
    result = ask_gemini("arm the drone", {}, {})
    assert result["intent"] == "action"
    assert "Arming motors." in result["message"]
    assert result["fix_command"]["command"] == "MAV_CMD_COMPONENT_ARM_DISARM"
    mock_generate_content_method.assert_called_once()

def test_ask_gemini_status_query(mock_generate_content_method): # Receive the mocked method
    mock_generate_content_method.return_value = create_mock_response_object(json.dumps({
        "intent": "status",
        "message": "Battery is 16.0V, 80% remaining.",
        "fix_command": None
    }))

    result = ask_gemini("what is my battery", {}, {"SYS_STATUS": {"voltage_battery": 16000, "battery_remaining": 80}})
    assert result["intent"] == "status"
    assert "Battery is 16.0V" in result["message"]
    assert result["fix_command"] is None
    mock_generate_content_method.assert_called_once()

def test_ask_gemini_diagnostic_query(mock_generate_content_method): # Receive the mocked method
    mock_generate_content_method.return_value = create_mock_response_object(json.dumps({
        "intent": "diagnostic",
        "message": "Compass seems off, consider recalibrating.",
        "fix_command": None,
        "recommended_param": ["COMPASS_CAL_FIT", "VERY_TIGHT"]
    }))

    result = ask_gemini("why is my compass off", {"COMPASS_USE": 1}, {})
    assert result["intent"] == "diagnostic"
    assert "Compass seems off" in result["message"]
    assert result["fix_command"] is None
    mock_generate_content_method.assert_called_once()

def test_ask_gemini_with_param_delta(mock_generate_content_method): # Receive the mocked method
    # Setup mock for the first call to ask_gemini
    mock_generate_content_method.return_value = create_mock_response_object(json.dumps({
        "intent": "info",
        "message": "Parameters initialized.",
        "fix_command": None
    }), prompt_token_count=10, candidates_token_count=5)

    # First call to initialize _last_seen_params and _model
    result1 = ask_gemini("initial query", {"BATT_VOLT_MIN": 10.0}, {})
    assert result1["intent"] == "info"
    mock_generate_content_method.assert_called_once()

    # Reset the mock's call count BEFORE configuring for the next call
    mock_generate_content_method.reset_mock()

    # Setup mock for the second call to ask_gemini
    mock_generate_content_method.return_value = create_mock_response_object(json.dumps({
        "intent": "status",
        "message": "Parameters updated and battery is fine.",
        "fix_command": None
    }), prompt_token_count=10, candidates_token_count=5)

    # Second call with updated parameters
    updated_params = {"BATT_VOLT_MIN": 9.5, "GPS_TYPE": 1}
    result2 = ask_gemini("check params", updated_params, {})
    assert result2["intent"] == "status"
    
    mock_generate_content_method.assert_called_once()
    # Check that the prompt contains PARAM_UPDATE_TEMPLATE text
    called_prompt = mock_generate_content_method.call_args[0][0]
    assert "Parameter Update" in called_prompt
    assert "BATT_VOLT_MIN" in called_prompt
    assert "GPS_TYPE" in called_prompt

def test_ask_gemini_invalid_json_response(mock_generate_content_method): # Receive the mocked method
    mock_generate_content_method.return_value = create_mock_response_object("This is not JSON {invalid}")

    result = ask_gemini("invalid query", {}, {})
    assert result is not None
    assert "error" in result
    assert "Invalid JSON response" in result["error"]
    assert "raw_response" in result
    mock_generate_content_method.assert_called_once()

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
    assert saved_history[0]["response"] == response
    assert "timestamp" in saved_history[0]