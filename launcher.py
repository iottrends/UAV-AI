"""
UAV-AI Desktop Launcher
Headless entry point for PyInstaller-bundled desktop application.
Starts the Flask web server, opens the browser, and waits for shutdown.
"""
import sys
import os
import signal
import time
import threading
import webbrowser
import logging

# --- PyInstaller resource path helper ---
def resource_path(relative_path):
    """Get path to resource, works for dev and PyInstaller bundle."""
    if getattr(sys, '_MEIPASS', None):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath(os.path.dirname(__file__)), relative_path)


# Set the working directory so relative imports and paths work
os.chdir(os.path.dirname(os.path.abspath(__file__)) if not getattr(sys, '_MEIPASS', None) else sys._MEIPASS)

# Make resource_path available to other modules
import builtins
builtins.resource_path = resource_path

from logging_config import setup_logging

# Initialize logging
loggers = setup_logging()
web_logger = loggers['web_server']
mavlink_logger = loggers['mavlink']

# Now import the app modules
from drone_validator import DroneValidator
import JARVIS
import web_server
from stt_module import stt_recorder

HOST = '0.0.0.0'
PORT = 5000
BROWSER_URL = f'http://localhost:{PORT}'

_shutdown_event = threading.Event()


def open_browser_delayed(url, delay=2.0):
    """Open the default browser after a short delay to let the server start."""
    def _open():
        time.sleep(delay)
        if not _shutdown_event.is_set():
            web_logger.info(f"Opening browser at {url}")
            webbrowser.open(url)
    t = threading.Thread(target=_open, daemon=True)
    t.start()


def handle_signal(signum, frame):
    """Handle termination signals for clean shutdown."""
    web_logger.info(f"Received signal {signum}, shutting down...")
    _shutdown_event.set()


def main():
    # Register signal handlers
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    web_logger.info("Starting UAV-AI Desktop Application")

    # Create validator
    validator = DroneValidator()

    # Start the web server (runs in a daemon thread)
    web_server.start_server(
        validator_instance=validator,
        jarvis=JARVIS,
        host=HOST,
        port=PORT,
        debug=False,
        loggers=loggers,
        stt_recorder=stt_recorder,
    )

    web_logger.info(f"Web server started on {HOST}:{PORT}")

    # Open browser after server has had time to start
    open_browser_delayed(BROWSER_URL)

    # Keep the main thread alive until shutdown is requested.
    # Unlike main.py, there is no terminal input() loop — all interaction
    # happens through the web UI.
    try:
        while not _shutdown_event.is_set():
            _shutdown_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        pass

    # Cleanup
    web_logger.info("UAV-AI Desktop Application shutting down")
    try:
        validator.disconnect()
    except Exception:
        pass


if __name__ == "__main__":
    main()
