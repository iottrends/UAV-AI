"""
voice_copilot.py — Voice I/O pipeline and proactive speech announcer.

Full pipeline
─────────────
  Browser mic blob / Server mic (Path A)
    → STT (Gemini, via stt_module)
    → Copilot fast-path  (zero-latency regex matching)
    → JARVIS via Orchestrator  (if no fast-path match)
    → TTS reply

Proactive speech (no user query needed)
─────────────────────────────────────────
  • Phase transitions  — EMERGENCY, ARM, DISARM, TAKEOFF, LANDING
  • Safety Engine alerts  — battery critical, GPS lost, EKF bad, RC lost
  • Orchestrator proactive advisories  (critical-anomaly / emergency)

TTS delivery
────────────
  Primary  : emit 'tts_speak' SocketIO event → browser speechSynthesis API
  Secondary: pyttsx3 local TTS on the server (optional; for RPi headless mode)

Browser must handle::

    socket.on('tts_speak', (d) => {
        if (d.interrupt) speechSynthesis.cancel();
        const u = new SpeechSynthesisUtterance(d.text);
        u.rate = 1.1;
        speechSynthesis.speak(u);
    });
"""

import logging
import threading
import queue
from typing import Callable, Optional

vc_logger = logging.getLogger("voice_copilot")

# ── TTS priority constants ───────────────────────────────────────────────────
P_CRITICAL = 0   # Interrupt any current speech (EMERGENCY, battery forced)
P_WARNING  = 1   # High-priority queue (battery low, GPS lost, EKF bad)
P_INFO     = 2   # Normal queue (mode change, arm/disarm confirmations)
P_RESPONSE = 3   # User-query responses from copilot / JARVIS

# Map SafetyEngine / AnomalyDetector severity strings → TTS priority
_SEVERITY_TO_PRIORITY = {
    'critical': P_CRITICAL,
    'warning':  P_WARNING,
    'info':     P_INFO,
}

# Phase transitions that warrant a proactive spoken announcement.
# Key: (from_phase_value, to_phase_value) — None means "match anything".
# Value: (spoken_text, priority)
_PHASE_ANNOUNCEMENTS: dict = {
    # Specific (from, to) pairs take precedence over wildcard (None, to) entries
    ('EMERGENCY', 'CRUISE'):    ('Emergency cleared. Resuming normal flight.', P_INFO),
    ('EMERGENCY', 'DISARMED'):  ('Emergency cleared. Drone disarmed.', P_INFO),
    # Wildcard "to" entries — fired when entering these phases from any state
    (None, 'EMERGENCY'):   ('WARNING. Emergency flight phase.', P_CRITICAL),
    (None, 'TAKEOFF'):     ('Taking off.', P_INFO),
    (None, 'LANDING'):     ('Landing.', P_INFO),
    (None, 'ARMED_IDLE'):  ('Drone armed.', P_INFO),
    (None, 'DISARMED'):    ('Drone disarmed.', P_INFO),
    (None, 'PREFLIGHT'):   ('Waiting for GPS and EKF lock.', P_INFO),
}


class VoiceCopilot:
    """
    Voice I/O pipeline and proactive speech announcer.

    Parameters
    ----------
    validator       DroneValidator — owns drone_state, phase_detector,
                    and send_mavlink_command_from_json.
    orchestrator    Orchestrator instance — for route_to_jarvis.
    stt_module      SpeechToTextRecorder — for audio transcription.
    emit_fn         Callable(event, data) — broadcasts a named SocketIO event
                    to all connected clients.
    copilot_mod     The copilot module (has try_fast_command).
    """

    def __init__(
        self,
        validator,
        orchestrator,
        stt_module,
        emit_fn: Optional[Callable] = None,
        copilot_mod=None,
    ):
        self._validator    = validator
        self._orchestrator = orchestrator
        self._stt          = stt_module
        self._emit         = emit_fn or (lambda event, data: None)
        self._copilot      = copilot_mod
        self._copilot_active = True   # mirrored from web_server global

        # Deduplication: don't repeat the same spoken text within N seconds
        self._last_spoken:  dict  = {}   # text → last spoken timestamp
        self._dedup_window: float = 8.0  # seconds

        # Optional pyttsx3 local TTS (RPi standalone mode)
        self._local_tts        = None
        self._local_tts_q:     queue.Queue = queue.Queue()
        self._local_tts_lock   = threading.Lock()
        self._init_local_tts()

        # Register as a FlightPhase change listener
        if validator and hasattr(validator, 'phase_detector'):
            validator.phase_detector.add_phase_listener(self._on_phase_change)
            vc_logger.info("VoiceCopilot: registered FlightPhase listener")

        vc_logger.info("VoiceCopilot initialized")

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def set_copilot_active(self, active: bool) -> None:
        """Sync the copilot fast-path flag (mirrors the web_server global)."""
        self._copilot_active = bool(active)

    def process_audio_blob(
        self,
        audio_bytes: bytes,
        mime_type: str,
        client_id: str,
        room_emit_fn: Callable,
        provider: str = "gemini",
    ) -> None:
        """
        Full voice pipeline: raw audio bytes → STT → route → respond → TTS.

        room_emit_fn(data) delivers a 'voice_response' payload to the
        requesting client only (room-targeted SocketIO emit).
        self._emit is used for broadcasts (proactive TTS, etc.).
        """
        if not self._stt:
            room_emit_fn({'error': 'STT module not available'})
            return

        transcript, err = self._stt.transcribe_audio_bytes(audio_bytes, mime_type)

        if err:
            room_emit_fn({'error': err})
            return
        if not transcript:
            room_emit_fn({'message': 'No speech detected'})
            return

        vc_logger.info(f"STT transcript: '{transcript}'")

        # Echo the transcript back to the UI
        self._emit('voice_status', {'status': 'idle', 'transcript': transcript})

        self.process_text_command(transcript, client_id, room_emit_fn, provider=provider)

    def process_text_command(
        self,
        text: str,
        client_id: str,
        room_emit_fn: Callable,
        provider: str = "gemini",
    ) -> None:
        """
        Route a text command: copilot fast-path first, JARVIS fallback.
        Emits voice_response payloads via room_emit_fn and speaks via TTS.
        """
        if not text or not text.strip():
            return

        vc_logger.info(f"Routing voice command: '{text}'")
        buf = self._mavlink_buffer()

        # ── Copilot fast-path ────────────────────────────────────────────
        if self._copilot and self._copilot_active:
            result = self._copilot.try_fast_command(text, buf)
            if result:
                response_text = result.get('response', '')
                fix_cmd       = result.get('fix_command')

                if fix_cmd:
                    ok      = self._execute_command(fix_cmd)
                    ack_msg = 'Command acknowledged.' if ok else 'Command failed.'
                    key     = 'message' if ok else 'error'
                    room_emit_fn({
                        'source':   'copilot',
                        'response': response_text,
                        key:        ack_msg,
                    })
                    self.speak(f"{response_text} {ack_msg}", priority=P_RESPONSE)
                else:
                    room_emit_fn({'source': 'copilot', 'response': response_text})
                    self.speak(response_text, priority=P_RESPONSE)
                return

        # ── JARVIS fallback ──────────────────────────────────────────────
        if not self._orchestrator:
            room_emit_fn({'error': 'No AI backend available'})
            return

        result = self._orchestrator.route_to_jarvis(text, provider=provider)

        # Emit full result to UI (it renders the JSON response)
        room_emit_fn({'source': 'jarvis', 'response': result})

        # Execute any fix_command included in the JARVIS response
        if result and result.get('fix_command'):
            cmds = result['fix_command']
            if not isinstance(cmds, list):
                cmds = [cmds]
            for cmd in cmds:
                if not isinstance(cmd, dict):
                    continue
                name = cmd.get('command', 'unknown')
                ok   = self._execute_command(cmd)
                key  = 'message' if ok else 'error'
                room_emit_fn({key: f"Command '{name}' {'acknowledged' if ok else 'failed'}."})
                if not ok:
                    break

        # Speak the JARVIS message field
        speakable = self._extract_speakable(result)
        if speakable:
            self.speak(speakable, priority=P_RESPONSE)

    def speak(
        self,
        text: str,
        priority: int = P_INFO,
    ) -> None:
        """
        Deliver TTS to all connected browser clients (and optionally pyttsx3).

        Priority P_CRITICAL sets interrupt=True in the payload, instructing
        the browser to cancel any ongoing speech before speaking this text.

        Duplicate suppression: the same text within _dedup_window seconds
        is silently dropped (prevents repeating the same alert on each tick).
        """
        if not text or not text.strip():
            return

        import time
        now  = time.time()
        text = text.strip()

        # Deduplication — skip if spoken recently at same or lower priority
        if now - self._last_spoken.get(text, 0) < self._dedup_window:
            return
        self._last_spoken[text] = now

        # Prune old dedup entries
        if len(self._last_spoken) > 200:
            cutoff = now - self._dedup_window * 2
            self._last_spoken = {
                k: v for k, v in self._last_spoken.items() if v > cutoff
            }

        payload = {
            'text':      text,
            'priority':  priority,
            'interrupt': priority <= P_CRITICAL,
        }
        self._emit('tts_speak', payload)
        vc_logger.info(
            f"TTS [p={priority}]{'[!]' if priority <= P_CRITICAL else ''}: {text[:80]}"
        )

        # Mirror to pyttsx3 local audio (RPi headless)
        if self._local_tts:
            self._local_tts_q.put((text, priority))

    def announce_safety_alert(
        self,
        alert_id: str,
        severity: str,
        title: str,
        message: str,
        action=None,
    ) -> None:
        """
        Called from the SafetyEngine alert chain (alongside _emit_alert).
        Speaks high-severity alerts; silently ignores 'info'-level notices.
        """
        if severity == 'info':
            return

        priority = _SEVERITY_TO_PRIORITY.get(severity, P_WARNING)
        # Brief spoken form: title + first sentence of the message
        brief = message.split('.')[0].strip()
        self.speak(f"{title}. {brief}.", priority=priority)

    def announce_proactive_advisory(self, message: str) -> None:
        """
        Called when the Orchestrator emits a proactive JARVIS advisory.
        Speaks a condensed version at critical priority.
        """
        if not message:
            return
        # Take the first two sentences to keep it brief
        sentences = [s.strip() for s in message.split('.') if s.strip()]
        brief = '. '.join(sentences[:2])
        if brief and not brief.endswith('.'):
            brief += '.'
        self.speak(brief[:250], priority=P_CRITICAL)

    # -----------------------------------------------------------------------
    # FlightPhase change listener
    # -----------------------------------------------------------------------

    def _on_phase_change(self, old_phase, new_phase, state) -> None:
        """Registered with FlightPhaseDetector — called on every phase transition."""
        old_val = old_phase.value if hasattr(old_phase, 'value') else str(old_phase)
        new_val = new_phase.value if hasattr(new_phase, 'value') else str(new_phase)

        # Lookup priority: specific pair first, then (None, to) wildcard, then (from, None)
        entry = (
            _PHASE_ANNOUNCEMENTS.get((old_val, new_val))
            or _PHASE_ANNOUNCEMENTS.get((None, new_val))
            or _PHASE_ANNOUNCEMENTS.get((old_val, None))
        )
        if entry:
            text, priority = entry
            vc_logger.info(f"Phase {old_val}→{new_val}: speaking '{text}'")
            self.speak(text, priority=priority)

    # -----------------------------------------------------------------------
    # Internal helpers
    # -----------------------------------------------------------------------

    def _mavlink_buffer(self) -> dict:
        if self._validator:
            return getattr(self._validator, 'ai_mavlink_ctx', {})
        return {}

    def _execute_command(self, cmd: dict) -> bool:
        if not self._validator:
            return False
        try:
            return bool(self._validator.send_mavlink_command_from_json(cmd))
        except Exception as e:
            vc_logger.error(f"Command execution error: {e}")
            return False

    def _extract_speakable(self, jarvis_result: dict) -> str:
        """
        Extract a concise, speakable text from a JARVIS JSON response dict.
        Strips markdown, takes the first 2 sentences, caps at 300 characters.
        Returns '' when there is nothing useful to speak.
        """
        if not jarvis_result:
            return ''
        if jarvis_result.get('error'):
            return f"Error: {jarvis_result['error'][:120]}"

        message = (jarvis_result.get('message') or '').strip()
        if not message:
            return ''

        # Strip markdown artifacts
        clean = (
            message
            .replace('**', '')
            .replace('*', '')
            .replace('`', '')
            .replace('#', '')
            .strip()
        )
        sentences = [s.strip() for s in clean.split('.') if s.strip()]
        spoken = '. '.join(sentences[:2])
        if spoken and not spoken.endswith('.'):
            spoken += '.'
        return spoken[:300]

    # -----------------------------------------------------------------------
    # Optional local TTS (pyttsx3)
    # -----------------------------------------------------------------------

    def _init_local_tts(self) -> None:
        """Try to initialise pyttsx3 for server-side audio output on RPi."""
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty('rate',   170)   # words/min — clear and natural
            engine.setProperty('volume', 1.0)
            self._local_tts = engine
            threading.Thread(
                target=self._local_tts_worker,
                daemon=True,
                name='vc-tts-local',
            ).start()
            vc_logger.info("pyttsx3 local TTS initialised (RPi mode)")
        except Exception as e:
            vc_logger.debug(f"pyttsx3 unavailable ({type(e).__name__}) — browser TTS only")

    def _local_tts_worker(self) -> None:
        """Dedicated thread that serialises pyttsx3 speak() calls."""
        while True:
            text, priority = self._local_tts_q.get()
            try:
                with self._local_tts_lock:
                    self._local_tts.say(text)
                    self._local_tts.runAndWait()
            except Exception as e:
                vc_logger.error(f"pyttsx3 error: {e}")
