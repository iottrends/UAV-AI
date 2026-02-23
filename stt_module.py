"""
stt_module.py — Dual-path Speech-to-Text using Gemini as the transcription engine.

Path A  (server mic)  : PyAudio records from the RPi's local mic (mic HAT / USB mic).
                        Raw PCM is wrapped in WAV and sent to Gemini for transcription.
Path B  (browser mic) : The browser captures audio via MediaRecorder and sends the
                        WebM/Opus blob to the server via the 'voice_audio_blob' socket
                        event.  transcribe_audio_bytes() handles it the same way.

Both paths use the same GEMINI_API_KEY already in .env — no extra credentials needed.
"""

import os
import io
import wave
import base64
import threading
import logging

import google.generativeai as genai

try:
    import pyaudio
    _PYAUDIO_AVAILABLE = True
except ImportError:
    _PYAUDIO_AVAILABLE = False

stt_logger = logging.getLogger('stt_module')

# Audio recording constants (Path A)
_RATE     = 16000
_CHUNK    = int(_RATE / 10)   # 100 ms chunks
_CHANNELS = 1


class SpeechToTextRecorder:
    """
    Dual-path STT recorder.

    Public API
    ----------
    has_local_mic()                       → bool
    transcribe_audio_bytes(bytes, mime)   → (transcript, error)   used by both paths
    start_recording(callback)             → bool                   Path A
    stop_recording_and_transcribe()                                Path A
    close()
    """

    def __init__(self):
        self._callback  = None
        self._frames    = []
        self._recording = False
        self._stream    = None
        self._audio     = None

        # Try to open the default input device (fails gracefully if no mic)
        if _PYAUDIO_AVAILABLE:
            try:
                pa = pyaudio.PyAudio()
                pa.get_default_input_device_info()   # raises if no device
                self._audio = pa
                stt_logger.info("Local mic detected — Path A available")
            except Exception:
                stt_logger.warning("No local mic detected — Path A unavailable")

    # ── Public ────────────────────────────────────────────────────────────

    def has_local_mic(self) -> bool:
        return self._audio is not None

    def transcribe_audio_bytes(self, audio_bytes: bytes, mime_type: str = 'audio/wav'):
        """
        Send audio bytes to Gemini for transcription.
        Returns (transcript_str, None) on success or (None, error_str) on failure.
        Called by both Path A (after local recording) and Path B (browser blob).
        """
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            return None, "GEMINI_API_KEY not configured"

        try:
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-2.5-flash")
            audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
            response = model.generate_content([
                "Transcribe this audio accurately. "
                "Return only the spoken words — no punctuation changes, no explanation.",
                {"mime_type": mime_type, "data": audio_b64},
            ])
            transcript = response.text.strip()
            stt_logger.info(f"Gemini transcript: '{transcript}'")
            return transcript, None
        except Exception as e:
            stt_logger.error(f"Gemini transcription error: {e}")
            return None, str(e)

    # ── Path A: local mic ─────────────────────────────────────────────────

    def start_recording(self, transcription_callback=None):
        """Start recording from the server's local mic (Path A)."""
        if not self._audio:
            stt_logger.error("No local mic — Path A unavailable")
            if transcription_callback:
                transcription_callback(None, error="No local microphone on server")
            return False

        if self._recording:
            return True

        self._callback  = transcription_callback
        self._frames    = []
        self._recording = True

        self._stream = self._audio.open(
            format=pyaudio.paInt16,
            channels=_CHANNELS,
            rate=_RATE,
            input=True,
            frames_per_buffer=_CHUNK,
            stream_callback=self._audio_cb,
        )
        self._stream.start_stream()
        stt_logger.info("Path A recording started")
        return True

    def stop_recording_and_transcribe(self):
        """Stop local recording and transcribe via Gemini in a background thread."""
        if not self._recording:
            return

        self._recording = False
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None

        pcm = b''.join(self._frames)
        self._frames = []

        if not pcm:
            if self._callback:
                self._callback(None, error="No audio captured")
            return

        wav_bytes = self._pcm_to_wav(pcm)
        cb = self._callback

        def _run():
            transcript, err = self.transcribe_audio_bytes(wav_bytes, 'audio/wav')
            if cb:
                cb(transcript, error=err)

        threading.Thread(target=_run, daemon=True).start()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _audio_cb(self, in_data, frame_count, time_info, status):
        self._frames.append(in_data)
        return in_data, pyaudio.paContinue

    def _pcm_to_wav(self, pcm_bytes: bytes) -> bytes:
        buf = io.BytesIO()
        with wave.open(buf, 'wb') as wf:
            wf.setnchannels(_CHANNELS)
            wf.setsampwidth(2)        # 16-bit PCM
            wf.setframerate(_RATE)
            wf.writeframes(pcm_bytes)
        return buf.getvalue()

    def close(self):
        if self._audio:
            self._audio.terminate()
            self._audio = None


# Module-level singleton — imported by web_server.py
stt_recorder = SpeechToTextRecorder()
