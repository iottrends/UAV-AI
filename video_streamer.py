"""
video_streamer.py — MJPEG video source proxy for UAV-AI.

Captures frames from a video source (USB camera, RTSP, HTTP MJPEG, or
wfb-ng UDP H.264) using OpenCV and serves them as a
multipart/x-mixed-replace MJPEG stream via a Flask streaming response.

Supported source strings
------------------------
  "0"  /  "usb:0"              → first USB/V4L2 camera (/dev/video0)
  "1"  /  "usb:1"              → second camera
  "rtsp://user:pw@host/path"   → RTSP (IP cam, companion Pi, etc.)
  "http://host/video"          → HTTP MJPEG (many IP cams expose this)
  "udp://0.0.0.0:5600"         → UDP H.264 from wfb-ng (requires
                                  GStreamer support in OpenCV)

Notes
-----
- opencv-python-headless (or opencv-python) must be installed.
- For UDP H.264 (wfb-ng), OpenCV must be built with GStreamer support
  (standard on Raspberry Pi OS via 'sudo apt install python3-opencv').
- One VideoStreamer singleton is shared by all Flask clients; the MJPEG
  generator is re-entrant — each client gets its own generator pulling
  from the same latest-frame buffer.
"""

import time
import threading
import logging

try:
    import cv2
    import numpy as np
    _CV2 = True
except ImportError:
    _CV2 = False

logger = logging.getLogger('video_streamer')

_JPEG_QUALITY = 72       # trade-off: quality vs bandwidth
_MAX_FPS      = 30       # max frame rate pushed to clients
_RECONNECT_S  = 3.0      # seconds to wait before reconnecting on failure


# ---------------------------------------------------------------------------
# No-signal placeholder
# ---------------------------------------------------------------------------

def _make_no_signal_jpeg() -> bytes:
    """Generate a 640×360 'NO VIDEO SIGNAL' placeholder JPEG."""
    if _CV2:
        img = np.zeros((360, 640, 3), dtype=np.uint8)
        img[:] = (18, 18, 28)
        cv2.putText(img, 'NO VIDEO SIGNAL',
                    (125, 160), cv2.FONT_HERSHEY_SIMPLEX, 1.15,
                    (155, 155, 175), 2, cv2.LINE_AA)
        cv2.putText(img, 'Configure source in Drone View',
                    (145, 210), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (80, 80, 100), 1, cv2.LINE_AA)
        _, buf = cv2.imencode('.jpg', img, [cv2.IMWRITE_JPEG_QUALITY, 55])
        return buf.tobytes()

    # Hardcoded minimal 1×1 dark-grey JPEG fallback when cv2 is absent
    return (
        b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00'
        b'\xff\xdb\x00C\x00\x10\x0b\x0c\x0e\x0c\n\x10\x0e\r\x0e\x12\x11'
        b'\x10\x13\x18(\x1a\x18\x16\x16\x18\x310#%\x1e(9 ;,18H\x18E!(&'
        b'*I9n!g8N5m=4P+\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00'
        b'\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00'
        b'\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b'
        b'\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xf5\x00\xff\xd9'
    )


# ---------------------------------------------------------------------------
# VideoStreamer
# ---------------------------------------------------------------------------

class VideoStreamer:
    """
    Thread-safe, reconnecting video source proxy.

    Usage
    -----
    vs = VideoStreamer()
    vs.open("0")                          # open USB cam 0
    # in Flask route:
    return Response(vs.generate_mjpeg(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')
    vs.stop()
    """

    def __init__(self):
        self._lock       = threading.Lock()
        self._frame: bytes | None = None   # latest JPEG bytes
        self._source     = None
        self._thread     = None
        self._running    = False
        self.status      = 'idle'          # idle | connecting | streaming | error
        self.fps         = 0.0
        self.resolution  = None            # (width, height) or None
        self._no_signal  = _make_no_signal_jpeg()

    # ── Public API ────────────────────────────────────────────────────────

    def open(self, source: str) -> None:
        """Open or reopen a video source."""
        self.stop()
        self._source  = source
        self.status   = 'connecting'
        self._running = True
        self._thread  = threading.Thread(
            target=self._capture_loop, daemon=True, name='video-capture'
        )
        self._thread.start()
        logger.info(f"VideoStreamer: opening '{source}'")

    def stop(self) -> None:
        """Stop capture and release the device."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._thread = None
        with self._lock:
            self._frame = None
        self.status     = 'idle'
        self.fps        = 0.0
        self.resolution = None

    def generate_mjpeg(self):
        """
        Generator for Flask streaming response.
        Each call is independent — safe for multiple simultaneous clients.
        """
        interval = 1.0 / _MAX_FPS
        while True:
            with self._lock:
                frame = self._frame
            if frame is None:
                frame = self._no_signal
                time.sleep(0.5)        # slow rate for placeholder
            else:
                time.sleep(interval)   # ~30 fps
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n'
            )

    def info(self) -> dict:
        return {
            'source':        self._source,
            'status':        self.status,
            'fps':           self.fps,
            'resolution':    list(self.resolution) if self.resolution else None,
            'cv2_available': _CV2,
        }

    # ── Source parser ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_source(source: str):
        """
        Convert a user-supplied source string to a cv2.VideoCapture argument.
        Returns (cap_arg, backend_flag).
        """
        s = source.strip()

        # Strip "usb:" prefix
        if s.lower().startswith('usb:'):
            s = s[4:]

        # Plain integer → device index
        try:
            return int(s), cv2.CAP_ANY
        except ValueError:
            pass

        # UDP H.264 from wfb-ng → GStreamer pipeline
        if s.lower().startswith('udp://'):
            _, rest = s.split('://', 1)
            parts = rest.split(':')
            host = parts[0] if parts[0] else '0.0.0.0'
            port = parts[1] if len(parts) > 1 else '5600'
            pipeline = (
                f'udpsrc address={host} port={port} '
                f'! h264parse ! avdec_h264 '
                f'! videoconvert ! video/x-raw,format=BGR ! appsink'
            )
            return pipeline, cv2.CAP_GSTREAMER

        # RTSP / HTTP — pass directly, OpenCV handles both
        return s, cv2.CAP_ANY

    # ── Capture loop (runs in background thread) ──────────────────────────

    def _capture_loop(self) -> None:
        if not _CV2:
            self.status = 'error'
            logger.error("opencv-python not installed — video streaming unavailable")
            return

        cap_arg, backend = self._parse_source(self._source)

        while self._running:
            logger.info(f"VideoStreamer: connecting to {cap_arg!r} ...")
            cap = cv2.VideoCapture(cap_arg, backend)

            if not cap.isOpened():
                self.status = 'error'
                logger.warning(
                    f"VideoStreamer: cannot open '{cap_arg}', "
                    f"retrying in {_RECONNECT_S}s"
                )
                time.sleep(_RECONNECT_S)
                continue

            self.status     = 'streaming'
            self.resolution = (
                int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            )
            logger.info(
                f"VideoStreamer: streaming {self.resolution[0]}×{self.resolution[1]}"
            )

            t0, n = time.time(), 0

            while self._running:
                ok, frame = cap.read()
                if not ok:
                    logger.warning("VideoStreamer: frame grab failed — reconnecting")
                    break

                ok2, buf = cv2.imencode(
                    '.jpg', frame,
                    [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY],
                )
                if ok2:
                    with self._lock:
                        self._frame = buf.tobytes()

                n += 1
                elapsed = time.time() - t0
                if elapsed >= 1.0:
                    self.fps = round(n / elapsed, 1)
                    n, t0 = 0, time.time()

            cap.release()

        with self._lock:
            self._frame = None
        self.status = 'idle'
        logger.info("VideoStreamer: stopped")


# Module-level singleton used by web_server.py
video_streamer = VideoStreamer()
