import pytest
import threading
import time
import socket
from web_server import start_server
from drone_validator import DroneValidator
import JARVIS

def get_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

@pytest.fixture(scope="session")
def server():
    port = get_free_port()
    validator = DroneValidator()
    # Mock JARVIS to avoid real API calls
    jarvis_mock = JARVIS
    
    thread = start_server(validator, jarvis_mock, host='127.0.0.1', port=port, debug=False)
    # Wait for server to start
    time.sleep(2)
    yield f"http://127.0.0.1:{port}"
    # Server thread is daemon, will exit with process
