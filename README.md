# UAV-AI Assistant

A comprehensive drone management and AI assistant system that connects to UAVs via MAVLink, provides real-time monitoring, and offers AI-powered diagnostics and assistance.

## Features

- **Drone Connection**: Connect to drones using MAVLink protocol over serial connections
- **Hardware Validation**: Automatically validate drone hardware and parameters
- **Real-time Monitoring**: Track drone status, battery levels, GPS, and other critical systems
- **Web Interface**: User-friendly web dashboard for monitoring and control
- **AI Assistant**: Integrated JARVIS AI assistant powered by Google's Gemini API
- **Diagnostic Capabilities**: Identify and suggest fixes for common drone issues
- **Logging System**: Comprehensive logging of all activities and communications

## System Requirements

- Python 3.8 or higher
- Windows/Linux/macOS
- Internet connection (for AI features)
- Serial port access for drone connection

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/UAV-AI.git
   cd UAV-AI
   ```

2. Create and activate a virtual environment:
   ```
   # Windows
   python -m venv winenv
   winenv\Scripts\activate

   # Linux/macOS
   python -m venv myenv
   source myenv/bin/activate
   ```

3. Install the required dependencies:
   ```
   pip install -r requirements.txt
   ```

4. Set up environment variables:
   Create a `.env` file in the project root with the following:
   ```
   GEMINI_API_KEY=your_gemini_api_key_here
   ```

## Usage

1. Start the UAV-AI Assistant:
   ```
   python main.py
   ```

2. The web interface will be available at http://localhost:5000

3. Connect to your drone by selecting the appropriate COM port and baud rate in the web interface

4. Use the terminal interface for direct commands:
   - `query:your question here` - Ask the AI assistant a question
   - `exit` - Exit the application

## Project Structure

- `main.py` - Main entry point and application controller
- `drone_validator.py` - Handles drone connection and hardware validation
- `web_server.py` - Web interface and API endpoints
- `JARVIS.py` - AI assistant powered by Google's Gemini API
- `Mavlink_rx_handler.py` - MAVLink message handling
- `logging_config.py` - Logging configuration
- `logs/` - Directory containing all log files
- `static/` - Web interface static files

## Logging

The system maintains several log files in the `logs/` directory:
- `mavlink_log.txt` - MAVLink communication logs
- `Agent.log` - AI agent/assistant activities
- `webserver.log` - Web server activities

## Troubleshooting

- **Connection Issues**: Ensure the correct COM port and baud rate are selected
- **Missing Logs**: Check that the `logs` directory exists and has write permissions
- **AI Not Responding**: Verify your Gemini API key is correctly set in the `.env` file
- **Web Interface Not Loading**: Ensure port 5000 is not in use by another application

## License

[Your License Here]

## Acknowledgments

- [PyMAVLink](https://github.com/ArduPilot/pymavlink) for MAVLink protocol support
- [Flask](https://flask.palletsprojects.com/) for the web framework
- [Google Generative AI](https://ai.google.dev/) for the Gemini API