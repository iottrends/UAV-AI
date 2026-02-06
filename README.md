# UAV-AI — AI-Powered Ground Station for ArduPilot

> Lightweight, web-based ground station with AI diagnostics and voice commands. Runs on a Raspberry Pi Zero 2W, accessible from any phone browser.

AI-powered, web-based ground station for ArduPilot. Voice commands, real-time diagnostics, MAVLink over serial/UDP. Runs on RPi Zero 2W, fits in your pocket.

## What's Inside (~3,700 lines of code)

**MAVLink Layer**
- Serial, WebSocket, and UDP connections
- Full parameter list download + progress tracking
- Heartbeat monitoring with timeout detection
- TIMESYNC latency measurement
- COMMAND_LONG send with ACK/NACK/timeout handling
- Blackbox log download
- Firmware version + capability parsing
- Sensor bitmask decoding

**AI Layer**
- JARVIS powered by Gemini API
- Sends full categorized param list as context
- Sends recent MAVLink messages as context
- Receives structured JSON back with diagnosis + fix commands
- Executes MAVLink commands directly from AI response (arm/disarm, motor test, takeoff, land)

**Web UI (single HTML file)**
- Real-time dashboard with battery, GPS, compass, IMU, barometer, motors
- Parameter viewer with categories
- Latency monitor with live chart
- Chat interface for AI
- Voice command support
- Serial/UDP connection modal
- WebSocket for live telemetry push

**Infrastructure**
- Multi-threaded (MAVLink loop, heartbeat monitor, TIMESYNC, telemetry broadcast)
- Structured logging (mavlink, web server, AI agent — separate files)
- SocketIO for real-time frontend updates

## Features

- **Drone Connection**: MAVLink over serial (USB), WebSocket, or UDP/IP (ELRS WiFi backpack)
- **AI Diagnostics**: Ask JARVIS "What's wrong with my drone?" — it analyzes your full param list + live telemetry and responds with a diagnosis and executable fix commands
- **Voice Commands**: Tap the mic button, speak, and JARVIS executes MAVLink commands
- **Real-time Monitoring**: Battery, GPS, compass, IMU, barometer, motors — live on your phone
- **Parameter Management**: Full parameter download, categorized viewer, direct editing
- **Latency Monitoring**: MAVLink TIMESYNC-based drone-to-GCS latency with live chart
- **Hardware Validation**: Automatic pre-flight health checks
- **Logging**: Separate log files for MAVLink, web server, and AI agent

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

## Connection Architecture

UAV-AI supports two ways to receive MAVLink telemetry from the flight controller:

### Option 1: Serial (USB) — Direct Connection

The RPi Zero 2W connects directly to the flight controller over a USB/serial link.

```
Flight Controller ──USB/Serial──> RPi Zero 2W (UAV-AI)
```

### Option 2: UDP/IP — ELRS WiFi Backpack (Wireless)

For wireless operation, the ELRS WiFi backpack on the RadioMaster TX16S forwards MAVLink over WiFi. A mobile phone hotspot acts as the shared network so both the RPi and the radio can reach each other without any extra router.

```
                        Phone Hotspot (e.g. 192.168.x.x)
                           /                    \
                          /                      \
   RadioMaster TX16S ────WiFi                WiFi──── RPi Zero 2W
   (ELRS WiFi Backpack)                              (UAV-AI)
         |
    ELRS RF Link
         |
   Flight Controller
```

**How it works:**

1. **Phone creates a WiFi hotspot** — this is the shared network for all devices.
2. **RadioMaster TX16S** (with ELRS WiFi backpack enabled) connects to the phone hotspot. The backpack is configured to forward MAVLink telemetry over UDP.
3. **RPi Zero 2W** also connects to the same phone hotspot.
4. In the UAV-AI web UI, select **IP (UDP)** connection mode, enter `0.0.0.0` (listen on all interfaces) and port `14550`, then click Connect.
5. The RPi listens on UDP port 14550 and receives MAVLink packets from the ELRS WiFi backpack.

**Network summary:**

| Device | Role | Connects To |
|---|---|---|
| Phone | WiFi hotspot (network hub) | — |
| RadioMaster TX16S (ELRS WiFi backpack) | Sends MAVLink over UDP | Phone hotspot |
| RPi Zero 2W | Receives MAVLink on UDP :14550 | Phone hotspot |

> **Tip:** You can also use any WiFi router instead of a phone hotspot — the key requirement is that the RPi and the RadioMaster TX are on the same network.

## Hardware Deployment Options

### Option A: Phone-Based Setup (Minimal Hardware)

Your phone does triple duty — hotspot, display, and microphone. The RPi Zero 2W is a headless box powered from the radio or a small battery.

```
┌──────────────┐      ┌──────────────────┐      ┌──────────────┐
│   Your Phone │      │  RPi Zero 2W     │      │ RadioMaster  │
│              │ WiFi │  (headless)      │ WiFi │ TX16S + ELRS │
│  - Hotspot   │◄────►│  - UAV-AI server │◄────►│  WiFi        │
│  - Browser   │      │  - MAVLink GW    │      │  Backpack    │
│  - Mic input │      │  - Gemini API    │      │              │
└──────────────┘      └──────────────────┘      └──────┬───────┘
                                                   ELRS RF
                                                       │
                                                ┌──────┴───────┐
                                                │   Flight     │
                                                │  Controller  │
                                                └──────────────┘
```

**Power:** USB cable from RadioMaster TX USB-C port, or a small USB power bank.

**Boot sequence:**
1. RPi powers on → auto-connects to phone hotspot (pre-configured `wpa_supplicant`)
2. `main.py` starts automatically via systemd service
3. Open browser on phone → `http://<rpi-ip>:5000`
4. Select IP (UDP) → Connect → done

**Cost: ~$15** (RPi Zero 2W only, you already have the phone and radio)

### Option B: Standalone Ground Station (Full Build)

A self-contained unit with its own screen, internet, and battery — no phone needed. Everything fits in a 3D-printed enclosure.

```
┌─────────────────────────────────────────┐
│       7" HDMI Touchscreen               │
│  ┌───────────────────────────────────┐  │
│  │                                   │  │
│  │     UAV-AI Web UI                 │  │
│  │     (Chromium kiosk mode)         │  │
│  │                                   │  │
│  └───────────────────────────────────┘  │
│                                         │
│  ┌───────────┐ ┌────────┐ ┌──────────┐ │
│  │RPi Zero 2W│ │4G USB  │ │ 3000mAh  │ │
│  │           │ │Dongle  │ │ 5V LiPo  │ │
│  └───────────┘ └────────┘ └──────────┘ │
│  ┌───────────┐                          │
│  │ USB Hub   │                          │
│  └───────────┘                          │
└─────────────────────────────────────────┘
```

**In this setup the RPi acts as a WiFi Access Point** — it creates its own network (e.g. `UAV-GS`). The ELRS backpack connects directly to it. The 4G dongle provides internet for Gemini API calls.

**Bill of Materials:**

| Component | Purpose | Approx Cost |
|---|---|---|
| RPi Zero 2W | Runs UAV-AI, MAVLink gateway | $15 |
| 7" HDMI touchscreen | Display (mini HDMI) + touch (USB) | $30-40 |
| 4G USB dongle (SIM-based) | Internet for Gemini AI API | $10-15 |
| 3000mAh 5V LiPo + TP4056 charger | Powers everything (~2-3 hrs) | $10 |
| USB hub (micro USB) | Splits single USB port for touch + 4G | $3-5 |
| 3D printed enclosure | Holds all components | ~$5 filament |
| **Total** | | **~$75-90** |

**Wiring:**

```
RPi Zero 2W
├── Mini HDMI ──────────► 7" Touchscreen (display)
├── Micro USB (data) ───► USB Hub
│                          ├── USB ► Touchscreen (touch input)
│                          └── USB ► 4G Dongle
├── Micro USB (power) ──► Battery / TP4056 board
└── WiFi (AP mode) ─────► ELRS Backpack connects here
```

**Boot sequence (fully automatic):**
1. Power on → RPi boots, starts WiFi AP (`UAV-GS`)
2. `main.py` starts via systemd
3. Chromium opens in kiosk mode → `http://localhost:5000`
4. 4G dongle connects for internet (Gemini API)
5. ELRS WiFi backpack auto-joins `UAV-GS` network
6. Select IP (UDP) → Connect → ready to fly

**Note on RPi Zero 2W ports:** The Zero 2W has only one micro USB data port (the other is power only), hence the USB hub is required to connect both the touchscreen's touch input and the 4G dongle.

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
