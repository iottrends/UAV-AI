# JARVIS AI Drone Brain — Design Document

**Platform:** Raspberry Pi 4 companion computer + ArduPilot flight controller
**Scope:** Single-drone Phase 1 — advisory AI, no autonomous flight
**Stack:** Python 3, Flask + SocketIO, MAVLink (pymavlink), Gemini / OpenAI / Claude

---

## 1. Philosophy

> **Deterministic safety is authoritative. The LLM is advisory only.**

- The flight controller always has final authority over the aircraft.
- Every safety action (RTL, LAND, alert) executes from rule-based code, not from an LLM decision.
- The LLM reasons about *what is happening* and *what to do next*, but a human (or deterministic rule) pulls the trigger.
- Copilot fast-path commands are zero-latency regex matches — no AI round-trip for safety-critical voice commands.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Physical World                               │
│   ArduPilot FC  ←──MAVLink serial──→  Raspberry Pi 4 (companion)   │
└───────────────────────────┬─────────────────────────────────────────┘
                            │  MAVLink stream (USB / UART)
                            ▼
┌───────────────────────────────────────────────────────────────────┐
│  Layer 1 — MAVLink I/O  (Mavlink_rx_handler.py)                   │
│  • Circular RX buffer, param download, RC override                │
│  • ai_mavlink_ctx — dict[msg_type → latest_msg_dict]              │
│  • snapshot_rx_queue() — dequeues, classifies, updates ctx        │
└───────────────────────────┬───────────────────────────────────────┘
                            │  ai_mavlink_ctx  (updated every cycle)
                            ▼
┌───────────────────────────────────────────────────────────────────┐
│  Layer 2 — DroneState  (drone_state.py)                           │
│  • Thread-safe canonical snapshot of all telemetry fields         │
│  • update_from_ctx(ai_mavlink_ctx) — parses MAVLink dicts         │
│  • Computed properties: is_flying(), battery_critical(), etc.     │
│  • snapshot() → clean serialisable dict                           │
└───────────────────────────┬───────────────────────────────────────┘
                            │  DroneState  (immutable snapshot per tick)
                            ▼
┌───────────────────────────────────────────────────────────────────┐
│  Layer 3 — FlightPhaseDetector  (flight_phase.py)                 │
│  • Maps DroneState → named FlightPhase every tick                 │
│  • Hysteresis timers prevent phase flapping                       │
│  • Observer callbacks: add_phase_listener(fn(old, new, state))    │
│  • Phases: BOOT PREFLIGHT DISARMED ARMED_IDLE TAKEOFF CLIMB       │
│            CRUISE AGGRESSIVE LANDING EMERGENCY                    │
└─────────────────┬───────────────────┬─────────────────────────────┘
                  │                   │
        DroneState + Phase          DroneState + Phase
                  │                   │
                  ▼                   ▼
┌──────────────────────┐   ┌──────────────────────────────────────┐
│  Layer 4a            │   │  Layer 4b                            │
│  SafetyEngine        │   │  AnomalyDetector                     │
│  (safety_engine.py)  │   │  (anomaly_detector.py)               │
│                      │   │                                      │
│  Guardian authority  │   │  Trend analysis on sliding windows   │
│  6 checks:           │   │  7 detectors:                        │
│  • Battery ladder    │   │  • battery_sag                       │
│  • GPS               │   │  • battery_current_spike             │
│  • EKF               │   │  • vibration_escalation              │
│  • Vibration         │   │  • ekf_instability                   │
│  • RC loss           │   │  • gps_degradation                   │
│  • Link timeout      │   │  • uncontrolled_descent              │
│                      │   │  • altitude_hold_failure             │
│  Emits: alert_fn()   │   │  Emits: anomaly_fn()                 │
│  Commands: RTL/LAND  │   │  Lifecycle: INACTIVE→ACTIVE→RESOLVED │
└──────────┬───────────┘   └──────────────┬───────────────────────┘
           │                              │
           └─────────────┬────────────────┘
                         │  alerts + anomalies
                         ▼
┌───────────────────────────────────────────────────────────────────┐
│  Layer 5 — Orchestrator  (orchestrator.py)                        │
│                                                                   │
│  Central brain API — enriches every LLM query with structured     │
│  context (DroneState + FlightPhase + active Anomalies) before     │
│  forwarding to JARVIS.                                            │
│                                                                   │
│  • route_to_jarvis(query, provider) → JARVIS response             │
│  • proactive_tick() — monitors EMERGENCY entry + critical         │
│    anomalies, fires rate-limited async JARVIS advisories          │
│  • process_log(query, summary, data) → log analysis               │
└──────────┬──────────────────────────────────────────────────────-─┘
           │
           │  (enriched context + JARVIS JSON responses)
           ▼
┌───────────────────────────────────────────────────────────────────┐
│  Layer 6 — JARVIS LLM  (JARVIS.py)                                │
│                                                                   │
│  Multi-provider LLM reasoning (Gemini / OpenAI / Claude)          │
│  • Semantic MAVLink context filtering (60+ keyword→msg_type map)  │
│  • Conversation history with 5-turn sliding window                │
│  • Parameter delta tracking (full on first call, diff thereafter) │
│  • Tuning assistant mode (PID / AutoTune awareness)               │
│  • ask_jarvis(query, parameter_context, mavlink_ctx,              │
│               provider, drone_context) → JSON response dict       │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│  Layer 7 — VoiceCopilot  (voice_copilot.py)                       │
│                                                                   │
│  Voice I/O pipeline + proactive TTS announcements                 │
│  • process_audio_blob() → STT (Gemini) → copilot → JARVIS → TTS  │
│  • speak(text, priority) → browser speechSynthesis / pyttsx3      │
│  • Proactive speech: phase changes, safety alerts, advisories     │
│  • Phase listener: EMERGENCY, ARM, DISARM, TAKEOFF, LANDING       │
└───────────────────────────────────────────────────────────────────┘

┌───────────────────────────────────────────────────────────────────┐
│  Layer 8 — Web Server  (web_server.py)                            │
│                                                                   │
│  Flask + SocketIO — browser interface glue                        │
│  • Fast 20Hz loop: attitude, heading (UI smoothness)              │
│  • Slow 2Hz loop: health, alerts, orchestrator.proactive_tick()   │
│  • REST API: params, logs, firmware, calibration, video           │
│  • Copilot fast-path: regex → MAVLink, <200ms, no AI round-trip   │
│  • Chat handler → orchestrator.route_to_jarvis()                  │
│  • Voice handler → voice_copilot.process_audio_blob()             │
└───────────────────────────────────────────────────────────────────┘
                            │
                    WebSocket (SocketIO)
                            │
┌───────────────────────────▼───────────────────────────────────────┐
│  Browser SPA  (static/index.html + static/js/tabs/)               │
│  • Drone view (3D / MJPEG), Parameters, Logs, Chat, Voice         │
│  • Handles tts_speak → window.speechSynthesis                     │
│  • Sends voice_audio_blob (MediaRecorder WebM/Opus)               │
└───────────────────────────────────────────────────────────────────┘
```

---

## 3. Module Reference

### 3.1 DroneState — `drone_state.py` (469 lines)

Canonical real-time drone state. No MAVLink dependency — only parses plain dicts.

**Fields**

| Group | Fields |
|---|---|
| Control | `armed`, `flight_mode`, `flight_mode_id` |
| Position | `rel_altitude_m`, `altitude_m`, `lat`, `lon`, `home_lat`, `home_lon`, `home_distance_m` |
| Motion | `groundspeed_ms`, `airspeed_ms`, `climb_rate_ms`, `heading_deg` |
| Attitude | `roll_deg`, `pitch_deg`, `yaw_deg` |
| Battery | `battery_voltage`, `battery_pct`, `current_a` |
| GPS | `gps_fix`, `satellites`, `hdop` |
| Health | `ekf_ok`, `ekf_flags`, `rc_rssi`, `failsafe` |
| Vibration | `vib_x`, `vib_y`, `vib_z`, `vib_clipping` |
| System | `system_load_pct`, `update_count`, `last_update_ts` |

**Computed properties**

```python
is_flying()           # rel_alt > 0.3m AND speed > 0.5m/s, or FC armed + moving
battery_low()         # pct < 20 or voltage < 10.5V
battery_critical()    # pct < 10 or voltage < 10.0V
battery_force_land()  # pct < 7 or voltage < 9.5V
gps_ok()              # fix >= 3D AND sats >= 6
gps_weak()            # sats < 8 or hdop > 2.0
vibration_high()      # any axis > 30 m/s²
rc_lost()             # rssi == 0
is_stale()            # no update in last 3 seconds
```

**Integration point**

```python
# DroneValidator.snapshot_rx_queue() — called every telemetry cycle
self.drone_state.update_from_ctx(self.ai_mavlink_ctx)
```

---

### 3.2 FlightPhaseDetector — `flight_phase.py` (363 lines)

Maps DroneState to a named flight phase with hysteresis to prevent flapping.

**Phases and evaluation priority**

```
BOOT        →  no data or stale link (highest priority)
EMERGENCY   →  failsafe, battery force-land, RC lost while flying, EKF bad airborne
LANDING     →  LAND mode (immediate) or RTL family at low alt + descending
DISARMED    →  disarmed, GPS + EKF ready
PREFLIGHT   →  disarmed, GPS or EKF not ready
ARMED_IDLE  →  armed, on the ground
TAKEOFF     →  within 45s of arm + below 10m AGL + climbing
AGGRESSIVE  →  speed > 8m/s OR |climb| > 4m/s OR |roll/pitch| > 45°
CLIMB       →  sustained climb > 0.8 m/s
CRUISE      →  default airborne state (lowest priority)
```

**Hysteresis timers**

| Transition | Dwell |
|---|---|
| Enter AGGRESSIVE | 0.5 s |
| Exit AGGRESSIVE | 1.5 s |
| Enter CLIMB | 1.0 s |
| RTL descent → LANDING | 2.0 s |
| Exit EMERGENCY | 3.0 s |

**Observer API**

```python
detector.add_phase_listener(fn)   # fn(old: FlightPhase, new: FlightPhase, state: DroneState)
detector.is_airborne()            # True for TAKEOFF/CLIMB/CRUISE/AGGRESSIVE/LANDING/EMERGENCY
detector.is_safe_to_command()     # False only for BOOT and EMERGENCY
detector.snapshot()               # dict for JARVIS prompt / UI
```

---

### 3.3 SafetyEngine — `safety_engine.py` (501 lines)

Guardian authority layer. Executes protective actions autonomously; never blocks telemetry.

**Battery ladder**

| Threshold | Action | Cooldown |
|---|---|---|
| 20% / 10.5V | Warning alert | 120 s |
| 15% / 10.0V | Strong warning alert | 60 s |
| 10% / 9.8V | 5-second RTL countdown (cancellable) | — |
| 7% / 9.5V | Forced action: RTL (GPS OK) or LAND | — |

**Other checks** (all phase-gated)

| Check | Condition | Phase gate |
|---|---|---|
| GPS | fix < 3D while flying | airborne |
| EKF | ekf_ok = False | airborne |
| Vibration | any axis > 30 m/s² | airborne |
| RC loss | rssi = 0 | airborne |
| Link timeout | no telemetry for 5 s | any |

**RTL countdown**

```python
# Daemon thread counts down 5 seconds, fires tick alerts to UI
# Cancellable via:
safety_engine.cancel_rtl_countdown()   # programmatic
# SocketIO event 'cancel_rtl'          # from UI button
# Chat message "cancel" / "abort"      # zero-latency intercept
```

**Decoupled callbacks**

```python
SafetyEngine(
    command_fn=validator.send_mavlink_command_from_json,  # executes MAVLink commands
)
safety_engine.set_alert_fn(_alert_with_tts)  # wired in start_server()
```

---

### 3.4 AnomalyDetector — `anomaly_detector.py` (647 lines)

Trend analysis using sliding deque windows. Pure Python — no NumPy.

**Detectors**

| ID | Algorithm | Window | Threshold |
|---|---|---|---|
| `battery_sag` | Linear regression on voltage | 120 samples | warn: -0.025 V/s, crit: -0.06 V/s |
| `battery_current_spike` | 3× baseline for N consecutive | 60 samples | 3 consecutive spikes |
| `vibration_escalation` | Slope of peak vibration | 60 samples | warn: +0.08, crit: +0.20 m/s²/s |
| `ekf_instability` | Count True→False transitions | 20 samples | ≥4 flips |
| `gps_degradation` | Sat drop OR hdop rise | 30 samples | ≥3 sats lost OR +0.8 hdop |
| `uncontrolled_descent` | Sustained high descent rate | 10 samples | warn: < -2.5, crit: < -4.0 m/s |
| `altitude_hold_failure` | Altitude drift in hold modes | 10 samples | > 2.5m drift (modes 2,5,16) |

**Anomaly lifecycle**

```
INACTIVE  ──(threshold crossed)──→  ACTIVE    (fires anomaly_fn callback)
ACTIVE    ──(5s clear dwell)──────→  RESOLVED  (fires callback with active=False)
ACTIVE    ──(warn→crit escalation)→  ACTIVE    (re-fires at higher severity)
```

**Rate limiting:** self-throttled to 2.5 Hz max (`_MIN_TICK_INTERVAL_S = 0.4`) regardless of call frequency. The 20 Hz fast loop calls `snapshot_rx_queue()`, which calls `anomaly_detector.tick()`, but ticks are silently dropped within the window.

**Thread safety:** `_fire()` dispatches callbacks via daemon threads to prevent lock re-entry when callbacks call `snapshot()`.

---

### 3.5 Orchestrator — `orchestrator.py` (288 lines)

Central brain API. Replaces scattered routing in web_server.py with a single enriched entry point.

**Context enrichment**

Every LLM call is enriched with a `### Drone State Summary:` block injected between the MAVLink messages and the user query:

```
DroneState: armed=True, mode=LOITER, alt=12.3m AGL, groundspeed=0.1m/s,
            climb=0.0m/s, battery=85%/12.4V, gps_fix=3, sats=14,
            ekf_ok=True, failsafe=False, rc_rssi=215, roll=0.2°, pitch=-0.1°
FlightPhase: CRUISE (active for 45s, airborne=True, safe_to_command=True)
ActiveAnomalies: Battery Sag [critical]: Voltage dropping at -0.07 V/s
```

**Public API**

```python
orchestrator.route_to_jarvis(query, provider)      # chat + voice entry point
orchestrator.proactive_tick()                       # called every 2Hz telemetry cycle
orchestrator.process_log(query, summary, data)      # log analysis passthrough
```

**Proactive tick logic**

```python
# Fires a background async JARVIS advisory when:
# 1. Phase just entered EMERGENCY   (rate-limited: 60s minimum interval)
# 2. New critical anomaly detected  (rate-limited: 60s minimum interval)
# Emits 'proactive_advisory' SocketIO event → VoiceCopilot speaks it
```

---

### 3.6 VoiceCopilot — `voice_copilot.py` (408 lines)

Unified voice I/O pipeline and proactive speech layer.

**Full pipeline**

```
Browser MediaRecorder blob
    → base64 decode
    → STT: Gemini transcription (via stt_module.transcribe_audio_bytes)
    → Echo transcript to UI (voice_status event)
    → Copilot fast-path (try_fast_command regex match)
        hit  → execute MAVLink command → speak response
        miss → Orchestrator.route_to_jarvis()
                    → execute fix_command if present
                    → speak JARVIS message field (first 2 sentences, ≤300 chars)
```

**TTS delivery**

```python
voice_copilot.speak(text, priority)
# → emit('tts_speak', {'text': ..., 'priority': ..., 'interrupt': bool})
# → optional pyttsx3 local audio (daemon thread + queue)
```

Browser handler required:
```javascript
socket.on('tts_speak', (d) => {
    if (d.interrupt) speechSynthesis.cancel();
    const u = new SpeechSynthesisUtterance(d.text);
    u.rate = 1.1;
    speechSynthesis.speak(u);
});
```

**TTS priority levels**

| Constant | Value | Behaviour | Used for |
|---|---|---|---|
| `P_CRITICAL` | 0 | `interrupt=True` — cancels current speech | EMERGENCY, battery forced |
| `P_WARNING` | 1 | Queue, high priority | Battery low, GPS lost, EKF bad |
| `P_INFO` | 2 | Queue, normal | Mode change, arm/disarm |
| `P_RESPONSE` | 3 | Queue, low | Copilot / JARVIS replies |

**Deduplication:** Same text spoken within 8 seconds is silently dropped.

**Proactive speech hooks**

| Source | Method | Trigger |
|---|---|---|
| FlightPhaseDetector listener | `_on_phase_change()` | EMERGENCY, TAKEOFF, LANDING, ARMED_IDLE, DISARMED, PREFLIGHT |
| SafetyEngine alert chain | `announce_safety_alert()` | warning + critical severity alerts |
| Orchestrator emit hook | `announce_proactive_advisory()` | proactive JARVIS advisory (first 2 sentences) |

**Phase announcements**

```
EMERGENCY         → "WARNING. Emergency flight phase."          P_CRITICAL
TAKEOFF           → "Taking off."                               P_INFO
LANDING           → "Landing."                                  P_INFO
ARMED_IDLE        → "Drone armed."                              P_INFO
DISARMED          → "Drone disarmed."                           P_INFO
PREFLIGHT         → "Waiting for GPS and EKF lock."             P_INFO
EMERGENCY → any   → "Emergency cleared. [Resuming / Disarmed]"  P_INFO
```

---

### 3.7 JARVIS LLM — `JARVIS.py` (1100+ lines)

Multi-provider LLM reasoning engine.

**Providers:** Gemini (default), OpenAI GPT-4o, Anthropic Claude Sonnet

**Prompt construction** (per query)

```
[Session — sent once, lives in history]
  ### Drone Parameters: <full categorized_params JSON>

[Per-query — rebuilt each call]
  ### Parameter Update: <delta dict if params changed>
  ### MAVLink Messages: <filtered ctx JSON array>
  ### Drone State Summary: <DroneState + FlightPhase + Anomalies>   ← injected by Orchestrator
  ### User Query: "<query text>"
```

**Semantic MAVLink filtering**

Keyword stems in the query select only relevant MAVLink message types, reducing token count for focused queries (e.g. "battery" → only `SYS_STATUS`, `BATTERY_STATUS`, `POWER_STATUS`). 60+ keyword mappings. Falls back to full ctx when no keyword matches.

**Conversation history**

5-turn sliding window (10 messages). If the initial full-params message scrolls out, `_params_sent` is reset so the next query re-sends the full parameter list.

**Response format**

```json
{
  "intent": "status | diagnostic | tuning | action",
  "message": "human-readable response",
  "fix_command": { "command": "MAV_CMD_...", "param1": ..., ... },
  "recommended_param": [...],
  "clarification_needed": "..."
}
```

---

## 4. Data Flow

### 4.1 Telemetry cycle (2 Hz slow loop)

```
MAVLink serial RX
  → Mavlink_rx_handler.snapshot_rx_queue()
      → DroneValidator.snapshot_rx_queue()  [override]
          1. super().snapshot_rx_queue()        # populates ai_mavlink_ctx
          2. drone_state.update_from_ctx()      # canonical state
          3. phase_detector.update()            # → FlightPhase
          4. safety_engine.tick()               # → alerts / commands
          5. anomaly_detector.tick()            # → anomaly callbacks

  → mavlink_buffer = validator.ai_mavlink_ctx.copy()
  → update_system_health()                     # broadcast to UI
  → check_proactive_alerts()                   # rule-based alerts (legacy)
  → orchestrator.proactive_tick()              # async LLM advisory if needed
```

### 4.2 Chat query flow

```
Browser  →  'chat_message' SocketIO event
  web_server.handle_chat_message()
    1. Safety cancel intercept   ("cancel" / "abort" → SafetyEngine)
    2. Copilot fast-path         (regex → MAVLink, <200ms, room-targeted emit)
    3. Log analysis path         (if log loaded + no drone)
    4. orchestrator.route_to_jarvis(query, provider)
         _build_drone_context()
         ask_jarvis(query, params, mavlink_ctx, provider, drone_context)
    5. Emit chat_response to room
    6. Execute fix_command if present
```

### 4.3 Voice command flow

```
Browser MediaRecorder  →  'voice_audio_blob' SocketIO event
  web_server.handle_voice_audio_blob()
    base64 decode → audio bytes
    voice_copilot.process_audio_blob(audio_bytes, mime_type, client_id, room_emit_fn)
      stt_module.transcribe_audio_bytes()      # Gemini STT
      emit('voice_status', transcript)
      voice_copilot.process_text_command()
        copilot.try_fast_command()  → execute → speak
        orchestrator.route_to_jarvis() → execute fix_cmd → speak message
```

### 4.4 Proactive alert flow

```
SafetyEngine.tick()
  → _alert_with_tts(alert_id, severity, title, message)
      → _emit_alert()           # 'jarvis_alert' SocketIO event → UI toast
      → voice_copilot.announce_safety_alert()  # TTS speech if severity >= warning

AnomalyDetector._fire()  [daemon thread]
  → anomaly_fn callback
      → _emit_alert()           # 'jarvis_alert' SocketIO event → UI toast
      (no TTS — anomaly alerts are already spoken via SafetyEngine escalation)

Orchestrator.proactive_tick()  [every 2Hz, rate-limited 60s]
  → _proactive_advisory_worker()  [daemon thread]
      → ask_jarvis(advisory_query, drone_context=...)
      → emit('proactive_advisory', {message, intent})
          → voice_copilot.announce_proactive_advisory()   # TTS at P_CRITICAL
```

---

## 5. Thread Model

```
Main thread
  └─ Flask + SocketIO request handlers (chat, voice, REST)

Daemon threads (always running)
  ├─ _fast_telemetry_loop     20 Hz  — attitude / heading for UI smoothness
  ├─ _slow_telemetry_loop      2 Hz  — health, safety, anomaly, proactive tick
  ├─ MAVLink RX thread               — reads serial, enqueues messages
  ├─ SafetyEngine RTL countdown      — fires only during 5s countdown window
  ├─ AnomalyDetector _fire()         — one-shot per event, avoids lock re-entry
  ├─ Orchestrator proactive_tick()   — one-shot per advisory, 60s rate-limited
  └─ VoiceCopilot local TTS          — pyttsx3 queue worker (if pyttsx3 available)

STT / JARVIS calls
  └─ Per-request daemon threads in voice handlers and chat handler
     (blocking Gemini API calls must never block the telemetry loops)
```

**Lock discipline**

- `DroneState` uses `threading.RLock` (re-entrant; `update_from_ctx` and `snapshot` may be called from different threads)
- `FlightPhaseDetector` uses `threading.Lock`; listener callbacks fire **outside** the lock
- `SafetyEngine` uses `threading.Lock`; `_fire()` is inline (caller must not re-enter)
- `AnomalyDetector` uses `threading.Lock`; `_fire()` dispatches via daemon thread to prevent deadlock when callbacks call `snapshot()`
- `VoiceCopilot._local_tts_lock` serialises pyttsx3 `runAndWait()` calls

---

## 6. Module Dependency Graph

```
                    Mavlink_rx_handler
                          │
                    DroneValidator  ◄── owns all AI brain modules
                    ├── drone_state
                    ├── phase_detector  ◄── listens: VoiceCopilot
                    ├── safety_engine   ◄── alert_fn: web_server → VoiceCopilot
                    └── anomaly_detector ◄── anomaly_fn: web_server

                    Orchestrator
                    ├── validator (read-only access)
                    ├── jarvis_module (ask_jarvis)
                    └── emit_fn (SocketIO) ──→ VoiceCopilot.announce_proactive_advisory

                    VoiceCopilot
                    ├── validator (read-only + send_mavlink_command_from_json)
                    ├── orchestrator (route_to_jarvis)
                    ├── stt_module (transcribe_audio_bytes)
                    ├── copilot module (try_fast_command)
                    └── emit_fn (SocketIO tts_speak + voice_status)

                    JARVIS (jarvis_module)
                    └── (no runtime deps — pure LLM I/O)

                    web_server
                    ├── validator
                    ├── orchestrator
                    ├── voice_copilot
                    ├── jarvis_module
                    ├── stt_module
                    └── copilot module
```

No circular imports. All modules take their dependencies as constructor arguments or via `set_*` callbacks.

---

## 7. SocketIO Event Reference

### Server → Browser

| Event | Payload | Source |
|---|---|---|
| `telemetry` | Full telemetry snapshot (2 Hz) | slow loop |
| `attitude` | roll, pitch, yaw, heading (20 Hz) | fast loop |
| `jarvis_alert` | `{id, severity, title, message, action}` | SafetyEngine / AnomalyDetector |
| `proactive_advisory` | `{severity, title, message, intent}` | Orchestrator |
| `tts_speak` | `{text, priority, interrupt}` | VoiceCopilot |
| `voice_status` | `{status, transcript?}` | VoiceCopilot / voice handlers |
| `voice_response` | `{source, response, message?, error?}` | voice handlers |
| `chat_response` | `{source, response, quota_exhausted?}` | chat handler |
| `chat_processing` | `{status: 'processing'}` | chat handler |
| `command_ack` | `{command, status}` | Orchestrator |

### Browser → Server

| Event | Payload | Handler |
|---|---|---|
| `chat_message` | `{message, provider}` | `handle_chat_message` |
| `voice_audio_blob` | `{audio: base64, mime_type}` | `handle_voice_audio_blob` |
| `start_voice_input` | — | `handle_start_voice_input` (Path A) |
| `stop_voice_input` | — | `handle_stop_voice_input` (Path A) |
| `copilot_toggle` | `{enabled: bool\|null}` | `handle_copilot_toggle` |
| `cancel_rtl` | — | `handle_cancel_rtl` |

---

## 8. File Inventory

| File | Lines | Role |
|---|---|---|
| `main.py` | 125 | Entry point — creates validator, starts server |
| `Mavlink_rx_handler.py` | 850+ | MAVLink I/O, param download, RC override |
| `drone_validator.py` | 305 | Parameter categorization, hardware validation, owns AI brain modules |
| `drone_state.py` | 469 | Canonical drone state (no MAVLink dependency) |
| `flight_phase.py` | 363 | Flight phase state machine with hysteresis |
| `safety_engine.py` | 501 | Guardian authority — rule-based safety actions |
| `anomaly_detector.py` | 647 | Trend analysis — 7 sliding-window detectors |
| `orchestrator.py` | 288 | Central brain API — context enrichment + proactive tick |
| `voice_copilot.py` | 408 | Voice I/O pipeline + proactive TTS |
| `JARVIS.py` | 1100+ | Multi-provider LLM (Gemini / OpenAI / Claude) |
| `copilot.py` | 228 | Fast-path regex command matcher (<200ms) |
| `stt_module.py` | 174 | Dual-path STT — browser blob + local mic via PyAudio |
| `web_server.py` | 3400+ | Flask + SocketIO backend, REST API, telemetry loops |
| `log_parser.py` | 150 | .bin / .tlog parsing and summary for JARVIS |
| `video_streamer.py` | — | MJPEG proxy stream |
| `report_generator.py` | — | HTML flight report generation |
| `copilot.py` | 228 | Regex fast-path (no AI) |

---

## 9. Key Design Decisions

**Why DroneState is separate from DroneValidator**
DroneValidator has four responsibilities already (MAVLink I/O, param categorization, hardware validation, log parsing). DroneState is a lightweight, importable data class with no MAVLink baggage. Separation allows FlightPhase, SafetyEngine, and AnomalyDetector to depend only on DroneState — not on the full MAVLink stack.

**Why copilot fast-path stays in web_server.py**
The copilot success/failure response requires a room-targeted SocketIO emit with `client_id`. The Orchestrator and VoiceCopilot do not hold a `client_id`. Moving the copilot there would require passing `client_id` through multiple layers. The current split (copilot in web_server.py, JARVIS routing in Orchestrator) is the cleanest boundary.

**Why SafetyEngine uses callbacks, not imports**
`safety_engine.py` imports nothing from web_server or SocketIO. It receives `command_fn` and `alert_fn` at construction. This makes it independently testable and keeps the safety logic free of web framework coupling.

**Why AnomalyDetector fires callbacks via daemon threads**
The anomaly callback (wired to `_emit_alert` in web_server.py) may call back into `anomaly_detector.snapshot()`. If `_fire()` were called inline under the detector's lock, this would deadlock. Daemon thread dispatch breaks the lock re-entry cycle.

**Why the LLM never directly executes commands**
JARVIS returns a `fix_command` field in its JSON response. web_server.py (or VoiceCopilot) reads this field and calls `validator.send_mavlink_command_from_json()` only if the intent is `"action"` and the command is structurally valid. The LLM cannot directly reach the MAVLink serial port.

---

## 10. Out of Scope (Phase 1)

- Filter Bode plot visualizer
- PID step-response predictor
- MAGFit wizard (magnetometer calibration)
- Spectrum analyzer (FFT on gyro data)
- Automatic LLM provider fallback (currently manual switch)
- Multi-drone support
- Autonomous mission planning
