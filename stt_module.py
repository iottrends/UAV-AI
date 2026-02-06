import os
import io
import pyaudio
import threading
import collections
import time
import logging
from google.cloud import speech_v1p1beta1 as speech
from google.oauth2 import service_account

# Configure logging for this module
stt_logger = logging.getLogger('stt_module')

# Audio recording parameters
RATE = 16000  # Sample rate (Hz)
CHUNK = int(RATE / 10)  # 100ms chunks
FORMAT = pyaudio.paInt16
CHANNELS = 1

class SpeechToTextRecorder:
    """Manages audio recording and sends to Google Cloud Speech-to-Text API."""

    def __init__(self):
        self._buff = collections.deque()
        self._closing = False
        self._audio_interface = pyaudio.PyAudio()
        self._audio_stream = None
        self._closed = True # Indicates if the stream is truly closed
        self._listening_thread = None
        self._transcription_callback = None
        self._current_audio_data = [] # Buffer for current recording session

        # Google Cloud Speech-to-Text client setup
        self.speech_client = self._get_speech_client()
        if self.speech_client:
            stt_logger.info("Google Cloud Speech-to-Text client initialized.")
        else:
            stt_logger.error("Failed to initialize Google Cloud Speech-to-Text client. Check GOOGLE_APPLICATION_CREDENTIALS.")

    def _get_speech_client(self):
        """Initializes Google Cloud Speech-to-Text client using service account credentials."""
        credentials_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
        if credentials_path and os.path.exists(credentials_path):
            try:
                credentials = service_account.Credentials.from_service_account_file(credentials_path)
                return speech.SpeechClient(credentials=credentials)
            except Exception as e:
                stt_logger.error(f"Error loading Google Cloud credentials from {credentials_path}: {e}")
                return None
        else:
            stt_logger.warning("GOOGLE_APPLICATION_CREDENTIALS environment variable not set or file not found. "
                               "Google Cloud Speech-to-Text will not function.")
            return None

    def _listen_continuously(self):
        """Continuously listens to audio and puts chunks in a buffer."""
        stt_logger.debug("Starting continuous audio capture.")
        while not self._closing:
            try:
                chunk_data = self._audio_stream.read(CHUNK, exception_on_overflow=False)
                self._buff.append(chunk_data)
            except Exception as e:
                stt_logger.error(f"Error reading audio stream: {e}")
                self._closing = True # Force close on error
        stt_logger.debug("Continuous audio capture stopped.")

    def start_recording(self, transcription_callback=None):
        """Starts recording audio from the microphone."""
        if not self.speech_client:
            stt_logger.error("Speech-to-Text client not initialized. Cannot start recording.")
            return False

        if self._audio_stream and not self._closed:
            stt_logger.warning("Recording already in progress.")
            return True

        self._transcription_callback = transcription_callback
        self._buff.clear()
        self._current_audio_data = [] # Clear buffer for new recording
        self._closing = False

        self._audio_stream = self._audio_interface.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            frames_per_buffer=CHUNK,
            stream_callback=self._audio_callback
        )
        self._closed = False
        self._audio_stream.start_stream()
        stt_logger.info("Audio recording started.")
        return True

    def _audio_callback(self, in_data, frame_count, time_info, status):
        """Callback function for PyAudio stream."""
        self._current_audio_data.append(in_data)
        return in_data, pyaudio.paContinue

    def stop_recording_and_transcribe(self):
        """Stops recording, collects audio, and sends it for transcription."""
        if self._closed:
            stt_logger.warning("No recording in progress to stop.")
            return None

        self._audio_stream.stop_stream()
        self._audio_stream.close()
        self._closed = True
        stt_logger.info("Audio recording stopped.")

        audio_content = b''.join(self._current_audio_data)
        self._current_audio_data = [] # Clear for next recording

        if not audio_content:
            stt_logger.warning("No audio data recorded for transcription.")
            return None

        stt_logger.info(f"Transcribing {len(audio_content)} bytes of audio.")
        
        audio = {"content": audio_content}
        config = {
            "encoding": speech.RecognitionConfig.AudioEncoding.LINEAR16,
            "sample_rate_hertz": RATE,
            "language_code": "en-US",
            "model": "command_and_search", # Optimized for short commands
            "speech_contexts": [
                {"phrases": ["arm the drone", "disarm the drone", "spin motor one", "change motor direction of motor two", "take off", "land", "go home"]}
            ]
        }

        try:
            response = self.speech_client.recognize(config=config, audio=audio)
            transcript = ""
            for result in response.results:
                transcript += result.alternatives[0].transcript
            
            stt_logger.info(f"Transcription received: '{transcript}'")
            if self._transcription_callback:
                self._transcription_callback(transcript)
            return transcript
        except Exception as e:
            stt_logger.error(f"Error during Google Cloud Speech-to-Text transcription: {e}")
            if self._transcription_callback:
                self._transcription_callback(None, error=str(e))
            return None

    def close(self):
        """Closes the PyAudio interface."""
        if self._audio_interface:
            self._audio_interface.terminate()
            self._audio_interface = None
            stt_logger.info("PyAudio interface terminated.")

# Global instance for easy access, or instantiate as needed
stt_recorder = SpeechToTextRecorder()

