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
import queue
import struct
import subprocess

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


# ---------------------------------------------------------------------------
# LowLatencyStreamer — FFmpeg fMP4 → browser MSE  (<100 ms for UDP H.264)
# ---------------------------------------------------------------------------

class LowLatencyStreamer:
    """
    Low-latency H.264 video streamer for wfb-ng UDP sources.

    Pipeline
    --------
      wfb-ng UDP:5600  →  FFmpeg (remux H.264 → fMP4, no decode)
        →  chunked HTTP  →  browser MediaSource Extensions (MSE)
        →  hardware H.264 decoder  →  <video>

    Typical glass-to-glass latency: 50–150 ms on LAN.

    FFmpeg only remuxes (copy codec); no pixel processing.  CPU cost on
    CM4 is negligible.  The MSE init segment (ftyp + moov) is stored and
    replayed for every new client that connects mid-stream.

    Limitations
    -----------
    - Requires ffmpeg on PATH.
    - Browser must support MSE + H.264 (Chrome, Firefox, Safari 14+).
    - Falls back gracefully to MJPEG when unavailable.
    """

    _CHUNK       = 65536   # stdout read granularity (64 KB)
    _MAX_CLIENTS = 8       # hard cap on concurrent fMP4 consumers
    _QUEUE_MAX   = 60      # per-client frame queue depth before dropping

    def __init__(self):
        self._lock         = threading.Lock()
        self._proc         = None
        self._thread       = None
        self._running      = False
        self._init_segment = b''    # ftyp + moov bytes; sent to every new client
        self._clients      = []     # list of queue.Queue
        self.status        = 'idle'
        self.source        = None

    # ── Public API ────────────────────────────────────────────────────────

    def open(self, udp_url: str) -> None:
        """Start FFmpeg and begin distributing fMP4 to clients."""
        self.stop()
        self.source   = udp_url
        self._running = True
        self.status   = 'connecting'
        self._thread  = threading.Thread(
            target=self._run, daemon=True, name='ll-streamer'
        )
        self._thread.start()
        logger.info(f"LowLatencyStreamer: opening '{udp_url}'")

    def stop(self) -> None:
        """Stop FFmpeg and drain all client queues."""
        self._running = False
        with self._lock:
            proc    = self._proc
            clients = list(self._clients)
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
        self._proc = None
        self._thread = None
        with self._lock:
            self._init_segment = b''
            for q in clients:
                try:
                    q.put_nowait(None)
                except Exception:
                    pass
            self._clients.clear()
        self.status = 'idle'
        logger.info("LowLatencyStreamer: stopped")

    def generate_fmp4(self):
        """
        Flask generator — yields raw fMP4 bytes for one HTTP client.

        Sends the stored init segment (ftyp+moov) first so the browser
        can initialise MSE, then streams live moof+mdat atoms as they arrive.
        Removes itself from the client list on generator close / exception.
        """
        q = queue.Queue(maxsize=self._QUEUE_MAX)
        with self._lock:
            if len(self._clients) >= self._MAX_CLIENTS:
                logger.warning("LowLatencyStreamer: max clients reached, rejecting")
                return
            self._clients.append(q)
            init = self._init_segment   # snapshot under lock

        try:
            if init:
                yield init
            while self._running:
                try:
                    atom = q.get(timeout=5.0)
                    if atom is None:    # stop sentinel
                        break
                    yield atom
                except queue.Empty:
                    continue            # keep-alive (client still connected)
        finally:
            with self._lock:
                try:
                    self._clients.remove(q)
                except ValueError:
                    pass

    def info(self) -> dict:
        return {
            'source':     self.source,
            'status':     self.status,
            'mode':       'll',
            'init_bytes': len(self._init_segment),
            'clients':    len(self._clients),
        }

    # ── Background reader thread ──────────────────────────────────────────

    def _run(self) -> None:
        """Start FFmpeg subprocess and parse its fMP4 output atom by atom."""
        src = (self.source or '').strip()
        # Build FFmpeg-compatible UDP URL with wfb-ng-friendly options
        if src.lower().startswith('udp://'):
            _, rest = src.split('://', 1)
            ff_src = (
                f'udp://{rest}?overrun_nonfatal=1&fifo_size=50000000'
                if '?' not in rest else src
            )
        else:
            ff_src = src

        cmd = [
            'ffmpeg',
            '-fflags',         'nobuffer',
            '-flags',          'low_delay',
            '-avioflags',      'direct',
            '-probesize',      '32768',       # 32 KB — find SPS/PPS fast
            '-analyzeduration','500000',      # 0.5 s max probe
            '-i',              ff_src,
            '-c:v',            'copy',        # NO decode — remux only
            '-an',
            '-f',              'mp4',
            '-movflags',       'frag_keyframe+empty_moov+default_base_moof',
            '-frag_duration',  '200000',      # 200 ms fragments → ~100 ms latency
            'pipe:1',
        ]

        logger.info(f"LowLatencyStreamer FFmpeg: {' '.join(cmd)}")
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
        except FileNotFoundError:
            logger.error("LowLatencyStreamer: ffmpeg not found — install ffmpeg")
            self.status = 'error'
            return

        with self._lock:
            self._proc = proc
        self.status  = 'streaming'

        buf        = b''
        past_init  = False   # True after we've seen the first moof atom

        try:
            while self._running and proc.poll() is None:
                raw = proc.stdout.read(self._CHUNK)
                if not raw:
                    break
                buf += raw

                # Parse complete MP4 atoms out of the accumulation buffer
                while True:
                    if len(buf) < 8:
                        break

                    atom_size = struct.unpack('>I', buf[:4])[0]
                    atom_type = buf[4:8]

                    if atom_size == 1:          # 64-bit extended size field
                        if len(buf) < 16:
                            break
                        atom_size = struct.unpack('>Q', buf[8:16])[0]
                    elif atom_size == 0:        # extends to end of stream
                        atom_size = len(buf)

                    if len(buf) < atom_size:
                        break               # incomplete atom — accumulate more

                    atom = buf[:atom_size]
                    buf  = buf[atom_size:]

                    if not past_init:
                        if atom_type == b'moof':
                            # First media fragment — init segment complete
                            past_init = True
                            logger.info(
                                f"LowLatencyStreamer: init segment ready "
                                f"({len(self._init_segment)} B), streaming fragments"
                            )
                            # Fall through: broadcast this moof below
                        else:
                            # ftyp, moov → accumulate into stored init segment
                            with self._lock:
                                self._init_segment += atom
                            continue

                    # ── Broadcast media atom to all connected clients ──────
                    with self._lock:
                        dead = []
                        for q in self._clients:
                            try:
                                q.put_nowait(atom)
                            except queue.Full:
                                dead.append(q)
                        for q in dead:
                            try:
                                self._clients.remove(q)
                            except ValueError:
                                pass
                        if dead:
                            logger.warning(
                                f"LowLatencyStreamer: dropped {len(dead)} slow client(s)"
                            )

        except Exception as exc:
            logger.error(f"LowLatencyStreamer reader error: {exc}")
        finally:
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
            self.status = 'idle'
            with self._lock:
                for q in self._clients:
                    try:
                        q.put_nowait(None)
                    except Exception:
                        pass
                self._clients.clear()
            logger.info("LowLatencyStreamer: FFmpeg process exited")


# Module-level low-latency singleton
ll_streamer = LowLatencyStreamer()
