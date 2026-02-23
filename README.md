# UAV-AI — AI-Powered Ground Control Station for ArduPilot

> A web-based GCS that runs on a Raspberry Pi Zero 2W, accessible from any browser on any device. JARVIS — the built-in AI co-pilot — diagnoses flight issues, analyses logs, and guides tuning in plain English.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/python-3.8+-blue.svg)](https://www.python.org/)
[![ArduPilot](https://img.shields.io/badge/firmware-ArduPilot-orange.svg)](https://ardupilot.org/)
[![Flask](https://img.shields.io/badge/backend-Flask-black.svg)](https://flask.palletsprojects.com/)

---

## Demo Videos

| AI Diagnostics & Voice Commands | Drone Connection & Live Telemetry |
|---|---|
| [![Demo 1](https://img.youtube.com/vi/UgpRxgj8m-M/maxresdefault.jpg)](https://www.youtube.com/watch?v=UgpRxgj8m-M) | [![Demo 2](https://img.youtube.com/vi/JldcjQK7234/maxresdefault.jpg)](https://www.youtube.com/watch?v=JldcjQK7234) |

---

## Why UAV-AI

Every ArduPilot pilot knows the pain: something feels off mid-flight, or you just had a crash, and now you're staring at 800 parameters in Mission Planner on a laptop trying to figure out what went wrong.

**Today's workflow:**
1. Pull out laptop → connect USB → open Mission Planner
2. Stare at parameters → Google the error → read ArduPilot wiki for 20 minutes
3. Maybe fix it. Maybe pack up and go home.

**With UAV-AI:**
1. Open phone browser → *"JARVIS, why did it flip on takeoff?"*
2. *"MOT_SPIN_ARM is too low for your motor/prop combo and SERVO3 output is reversed. Apply fix?"*
3. *"Yes"* → Fixed → Fly again.

UAV-AI is built for pilots who don't want to carry a laptop to the field, ArduPilot beginners who want plain-English diagnostics, and system integrators doing rapid assembly and validation.

---

## Feature Overview

### JARVIS AI Co-Pilot
- Natural language diagnosis — ask anything about your drone's health
- Reads your full parameter set + live MAVLink telemetry as context
- Returns structured responses: diagnosis, fix commands, recommended params
- Executes MAVLink commands directly from AI response (arm, disarm, RTL, motor test)
- **AI Analyst** — query flight logs in natural language after a flight
- **Proactive context** — JARVIS knows your firmware version, sensor suite, and recent flight data

### Dashboard & Hardware Inventory
- Real-time battery, GPS, compass, IMU, barometer telemetry at 2Hz
- Hardware Inventory card: firmware version, board type, sensor suite, flash/SD storage
- System health score + preflight readiness (READY / CAUTION / NOT READY)
- MAVLink latency monitor with live chart (TIMESYNC-based)
- Preflight checks: GPS fix, battery threshold, RC input, EKF status, pre-arm errors

### Log Analysis (`.bin` / `.tlog`)
Three sub-tabs inside the Logs tab:

| Sub-tab | What it does |
|---|---|
| **Timeline** | Upload log → charts for attitude, altitude, battery, vibration, modes |
| **Spectrum (FFT)** | Cooley-Tukey FFT on gyro/rate data with Hann window. LPF + HNTCH overlay lines from live params. Peak detection. |
| **AI Analyst** | Chat with JARVIS about the loaded log. Auto-summary: duration, max altitude, GPS quality, vibration alerts, battery stats, mode sequence, errors. |

### Tuning Tab (Parameters + Analysis)
Three sub-tabs inside the Tuning tab:

| Sub-tab | What it does |
|---|---|
| **Parameters** | Full parameter table, categorised, searchable, inline edit and apply |
| **Filter Visualizer** | Bode plot of the full ArduPilot filter stack — Gyro LPF, Static Notch, Harmonic Notch (with harmonics bitmask), D-term LPF, Total response. Series toggles. |
| **Step Predictor** | Numerical 400Hz PID step response simulation. Flight Feel sliders (Aggressiveness, Smoothness, Position Hold, Stick Feel) map to actual ArduPilot params. Expert P/I/D mode. Apply changes directly to FC. |

### Motor Interference Wizard (MAGFit)
- Fits `COMPASS_MOT_X/Y/Z` coefficients from a flight log
- Rotates MAG data to Earth frame using ATT roll/pitch — removes tilt-induced variation
- Auto-selects Battery Current (A) as independent variable when available; falls back to throttle
- `numpy.linalg.lstsq` fit per axis — handles edge cases gracefully
- **Correction preview chart**: red = raw ΔMag, green = corrected ΔMag — visual confidence before applying
- R² quality score (Good / Fair / Poor) with sample count
- Apply button disabled when vehicle is armed — safety guard

### Calibration
- Gyro, Accelerometer (6-orientation), Level, Compass, Barometer calibration routines
- Live 3D quad model showing real-time attitude during calibration
- Calibration status feedback per routine

### Motor Testing
- Per-motor and all-motors-at-once test
- Safety interlock: arming detection disables test controls
- Slide-to-confirm safety gesture before motor spin

### Firmware Flashing
- Flash from local `.apj` file (ArduCopter, ArduPlane, ArduRover, etc.)
- Online firmware server: browse and flash by vehicle type and version
- DFU mode flashing for STM32 boards (`.bin` files)
- Auto-enter DFU mode via MAVLink command

### RC Modes & Configuration
- Dual gimbal RC input visualisation (175×175px) with deadband ring and PWM readouts
- Per-channel bars with 1500µs centre tick
- Flight mode range bands on dedicated channel
- Aux functions (RC7–RC12) with live PWM bars
- Failsafe auto-set from live throttle PWM
- Read → Preview diff → Apply → Verify workflow

### Serial Ports
- View and configure all serial ports on the flight controller
- Protocol assignment per port (MAVLink, GPS, RCIN, ESC telemetry, etc.)

### Configuration Snapshots
- Save named parameter snapshots ("good tune 2026-02-20")
- Restore snapshots to recover from bad tuning experiments
- Clone configuration to another airframe

---

## Comparison with Other GCS Tools

| Feature | Mission Planner | Betaflight | Cockpit/BlueOS | **UAV-AI** |
|---|---|---|---|---|
| Firmware | ArduPilot | Betaflight | Any MAVLink | **ArduPilot** |
| Platform | Desktop (Windows) | Desktop (Electron) | Browser (needs companion) | **Browser (any device)** |
| AI diagnosis | ✗ | ✗ | ✗ | **✓ JARVIS** |
| Log AI analysis | ✗ | ✗ | ✗ | **✓** |
| Spectrum FFT | ✗ | External app | ✗ | **✓ Inline** |
| Filter Bode plot | ✗ | ✗ | ✗ | **✓** |
| PID step simulation | ✗ | ✗ | ✗ | **✓** |
| MAGFit wizard | Manual/CLI | N/A | ✗ | **✓ Guided** |
| Runs on RPi Zero 2W | ✗ | ✗ | ✗ | **✓** |
| Works with ELRS backpack | ✓ | ✓ | ✗ | **✓** |
| Requires companion SBC | ✗ | ✗ | ✓ (mandatory) | **✗** |
| Map / mission planning | ✓ | ✗ | ✓ | ✗ (roadmap) |

UAV-AI is not a replacement for Mission Planner for complex mission planning. It is the tool you use **at the field, before and after a flight**, and the only GCS with an AI layer that understands ArduPilot.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                  Browser (any device)                 │
│  Dashboard · Logs · Tuning · Calibration · Motors    │
│  Firmware · RC Modes · Serial · Configs · JARVIS     │
└──────────────────────┬───────────────────────────────┘
                       │ WebSocket + REST (Flask-SocketIO)
┌──────────────────────▼───────────────────────────────┐
│                   web_server.py                       │
│  API endpoints · SocketIO telemetry broadcast (2Hz)  │
│  Log parser · MAGFit · Firmware flasher · Configs    │
└──────────────────────┬───────────────────────────────┘
                       │
┌──────────────────────▼───────────────────────────────┐
│              Mavlink_rx_handler.py                    │
│  Serial / UDP MAVLink · Heartbeat · TIMESYNC         │
│  Param download · Command ACK/NACK · Storage info    │
└──────────────────────┬───────────────────────────────┘
                       │ pymavlink
┌──────────────────────▼───────────────────────────────┐
│          Flight Controller (ArduPilot)                │
│  USB Serial · UDP:14550 (ELRS backpack)              │
└──────────────────────────────────────────────────────┘
```

**Single Python process. No Docker. No companion computer required.**

---

## Installation

### Requirements
- Python 3.8+
- Linux / macOS / Windows
- A Gemini API key for JARVIS ([get one free](https://aistudio.google.com/))

### Quick Start

```bash
git clone https://github.com/iottrends/UAV-AI.git
cd UAV-AI

python -m venv myenv
source myenv/bin/activate        # Windows: myenv\Scripts\activate

pip install -r requirements.txt

# Create .env with your Gemini API key
echo "GEMINI_API_KEY=your_key_here" > .env

python main.py
```

Open `http://localhost:5000` in your browser.

---

## Field Deployment

### Option A — Phone Hotspot (Minimal, ~$15)

The RPi Zero 2W is a headless box powered from the radio or a USB power bank. Your phone is the hotspot, display, and microphone.

```
Flight Controller ──ELRS RF──► ELRS Backpack (TX16S)
                                      │
                              Phone Hotspot (192.168.x.x)
                                 /              \
                   ELRS Backpack WiFi        RPi Zero 2W WiFi
                   → MAVLink UDP:14550    → UAV-AI :5000
                                                │
                                        Browser on phone
```

**Setup:**
1. Pre-configure RPi to join your phone's hotspot on boot
2. Set UAV-AI to start as a systemd service on boot
3. Power RPi from USB bank or radio USB port
4. Open `http://<rpi-ip>:5000` on phone → Select UDP → port 14550 → Connect

**Cost:** RPi Zero 2W ~$15. Everything else you already own.

---

### Option B — RPi4 Ground Unit with Video (Recommended for Pro Use)

Run UAV-AI + wfb-ng (wifibroadcast) together on an RPi4/CM4 with two WiFi adapters. The RPi4 creates its own hotspot — connect a tablet and fly.

```
┌──────────────────────────────────────────────────────┐
│              RPi4 / CM4  (Ground Unit)               │
│                                                      │
│  wlan0 (built-in)          wlan1 (USB RTL8812AU)    │
│  ├ STA: ELRS hotspot        Monitor mode             │
│  │  → MAVLink UDP:14550     wfb-ng RX                │
│  └ AP: "UAV-AI-Ground"      → H.264 UDP:5600         │
│         192.168.10.1                │                │
│                │                   │                 │
│                └────────┬──────────┘                 │
│                         │                            │
│                     UAV-AI                           │
│                     ├ Telemetry: UDP:14550           │
│                     ├ Video proxy: UDP:5600          │
│                     └ Serves http://192.168.10.1:5000│
└──────────────────────────────────────────────────────┘
                          │
                    WiFi (tablet/phone)
                          │
              Browser → Drone View tab
              (live video + JARVIS HUD)
```

**Bill of Materials:**

| Component | Cost |
|---|---|
| RPi4 4GB or CM4 | ~$55 |
| RTL8812AU USB WiFi adapter (monitor mode) | ~$15 |
| 64GB SD card | ~$10 |
| USB power bank | ~$20 |
| **Total** | **~$100** |

Compare to Herelink ground unit: $500+.

---

### Connection Modes

| Mode | When to use | Config |
|---|---|---|
| **Serial (USB)** | Bench work, direct USB to FC | Select port (e.g. `/dev/ttyACM0`), baud 115200 |
| **UDP** | ELRS WiFi backpack, wireless telemetry | IP `0.0.0.0`, port `14550` |

---

## Configuration

Create a `.env` file in the project root:

```env
GEMINI_API_KEY=your_gemini_api_key_here
```

JARVIS works without an API key in a limited co-pilot mode (local MAVLink commands only). Full AI diagnosis and log analysis requires the Gemini API key.

---

## Project Structure

```
UAV-AI/
├── main.py                    # Entry point
├── web_server.py              # Flask app, all API endpoints, SocketIO
├── Mavlink_rx_handler.py      # MAVLink receive loop, command sending
├── JARVIS.py                  # AI co-pilot (Gemini API integration)
├── log_parser.py              # .bin / .tlog parser for Logs tab
├── copilot.py                 # Fast local command path (no LLM)
├── firmware_flasher.py        # OTA firmware flash
├── dfu_flasher.py             # DFU / STM32 flash
├── drone_validator.py         # Pre-flight hardware validation
├── logging_config.py          # Structured logging setup
├── requirements.txt
├── static/
│   ├── index.html             # Single-page app shell
│   ├── css/style.css          # Full UI stylesheet
│   └── js/
│       ├── core.js            # Socket setup, shared state, JARVIS renderer
│       └── tabs/
│           ├── calibration.js # Calibration routines + 3D model + MAGFit
│           ├── drone-view.js  # Attitude visualisation
│           ├── logs.js        # Timeline + Spectrum FFT + AI Analyst
│           ├── motors.js      # Motor test
│           ├── parameters.js  # Param table + Filter Viz + Step Predictor
│           ├── rc-modes.js    # RC input display + mode config
│           └── serial-ports.js
├── tests/
│   ├── test_jarvis.py
│   ├── test_mavlink_handler.py
│   ├── test_web_server.py
│   ├── integration/
│   └── stress/
└── logs/                      # Runtime logs (mavlink, webserver, AI agent)
```

---

## Roadmap

| Phase | Feature | Status |
|---|---|---|
| A.1 | Dashboard: Hardware Inventory card | ✅ Done |
| A.2 | Logs: sub-tab bar + AI Analyst | ✅ Done |
| B.1 | Logs: Spectrum (FFT) sub-tab | ✅ Done |
| B.2 | Tuning: Filter Visualizer (Bode plots) | ✅ Done |
| B.3 | Tuning: Step Predictor + Flight Feel | ✅ Done |
| C.1 | Calibration: MAGFit Motor Interference Wizard | ✅ Done |
| D.1 | Proactive JARVIS alerts (vibration, battery, GPS) | 🔜 Next |
| D.2 | JARVIS voice input (Web Speech API) | 🔜 |
| D.3 | Preflight AI checklist | 🔜 |
| E.1 | Live telemetry plotter (user-configurable channels) | 🔜 |
| E.2 | Flight report export (HTML/PDF) | 🔜 |
| F.1 | Video stream + HUD (MJPEG / wfb-ng / RTSP) | 🔜 |
| F.2 | Joystick / Gamepad support | 🔜 |
| G.1 | Basic mission planner (Leaflet map + waypoints) | 🔜 |

---

## Contributing

Pull requests are welcome. For major changes, open an issue first to discuss what you'd like to change.

**Development setup:**

```bash
git clone https://github.com/iottrends/UAV-AI.git
cd UAV-AI
python -m venv myenv && source myenv/bin/activate
pip install -r requirements.txt
python main.py
```

Run tests:
```bash
pytest tests/
```

---

## Acknowledgements

- [PyMAVLink](https://github.com/ArduPilot/pymavlink) — MAVLink protocol
- [ArduPilot](https://ardupilot.org/) — the open source autopilot this is built for
- [Flask](https://flask.palletsprojects.com/) + [Flask-SocketIO](https://flask-socketio.readthedocs.io/) — web backend
- [Google Gemini](https://ai.google.dev/) — JARVIS AI engine
- [Chart.js](https://www.chartjs.org/) — all charts and visualisations
- [Three.js](https://threejs.org/) — 3D attitude model

---

## License

MIT License — see [LICENSE](LICENSE) for details.
