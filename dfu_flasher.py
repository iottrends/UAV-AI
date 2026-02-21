"""
STM32 DfuSe Firmware Flasher via pyusb.

Implements the USB DFU 1.1 + STM32 DfuSe extension protocol (ST AN3156):
  - Automatic DFU device detection (VID:0x0483 / PID:0xDF11)
  - 1200-baud trigger to enter DFU on supported boards
  - Board info from USB descriptor strings + lookup tables
  - Mass erase, chunked download (2048-byte blocks), leave-DFU
  - Per-chunk progress callbacks compatible with the serial flash pipeline

Requires: pip install pyusb
  Linux:   sudo apt install libusb-1.0-0
  Windows: install libusb via Zadig (one-time, per board)
"""

import struct
import time
import logging

logger = logging.getLogger('dfu_flasher')

try:
    import usb.core
    import usb.util
    PYUSB_AVAILABLE = True
except ImportError:
    PYUSB_AVAILABLE = False
    logger.warning("pyusb not installed — DFU flashing unavailable. Run: pip install pyusb")

# ── STM32 DFU USB IDs ────────────────────────────────────────────────────────
STM32_VID     = 0x0483
STM32_DFU_PID = 0xDF11

# ── DFU Request Codes ────────────────────────────────────────────────────────
DFU_DETACH    = 0
DFU_DNLOAD    = 1
DFU_UPLOAD    = 2
DFU_GETSTATUS = 3
DFU_CLRSTATUS = 4
DFU_GETSTATE  = 5
DFU_ABORT     = 6

# ── DFU States ───────────────────────────────────────────────────────────────
APP_IDLE                = 0
APP_DETACH              = 1
DFU_IDLE                = 2
DFU_DNLOAD_SYNC         = 3
DFU_DNBUSY              = 4
DFU_DNLOAD_IDLE         = 5
DFU_MANIFEST_SYNC       = 6
DFU_MANIFEST            = 7
DFU_MANIFEST_WAIT_RESET = 8
DFU_UPLOAD_IDLE         = 9
DFU_ERROR               = 10

DFU_STATE_NAMES = {
    0: 'appIDLE', 1: 'appDETACH', 2: 'dfuIDLE',
    3: 'dfuDNLOAD-SYNC', 4: 'dfuDNBUSY', 5: 'dfuDNLOAD-IDLE',
    6: 'dfuMANIFEST-SYNC', 7: 'dfuMANIFEST', 8: 'dfuMANIFEST-WAIT-RESET',
    9: 'dfuUPLOAD-IDLE', 10: 'dfuERROR',
}

# ── DfuSe Command Bytes (wValue=0 DFU_DNLOAD payload) ────────────────────────
CMD_SET_ADDRESS = 0x21
CMD_ERASE       = 0x41

# ── Flash Constants ───────────────────────────────────────────────────────────
STM32_FLASH_BASE = 0x08000000
XFER_SIZE        = 2048          # bytes per USB transfer (matches STM32 page size)

# ── Board Identification Lookup Tables ───────────────────────────────────────
USB_VENDOR_NAMES = {
    0x0483: 'STMicroelectronics',
    0x26AC: 'Holybro',
    0x2DAE: 'mRobotics',
    0x27AC: 'Emlid',
    0x1209: 'Generic',
    0x04D8: 'Microchip',
}

# ArduPilot board_id → (manufacturer, model)
# Source: ArduPilot hwdef files and firmware manifest
BOARD_ID_NAMES = {
    5:   ('Holybro',    'Pixhawk 1 2MB'),
    9:   ('Holybro',    'PX4FMU v2'),
    11:  ('Holybro',    'PX4FMU v4 / PixRacer'),
    13:  ('mRobotics',  'mRo Pixhawk'),
    33:  ('Holybro',    'PixRacer R15'),
    42:  ('Holybro',    'Pixhawk Mini'),
    50:  ('Holybro',    'Pixhawk 4'),
    51:  ('Holybro',    'Pixhawk 4 Mini'),
    52:  ('Holybro',    'Durandal'),
    53:  ('Holybro',    'Pix32 v5'),
    100: ('CUAV',       'X7'),
    101: ('CUAV',       'X7 Pro'),
    108: ('CUAV',       'Nora'),
    110: ('CUAV',       'V5+'),
    112: ('CUAV',       'V5 Nano'),
    120: ('Matek',      'H743-Wing'),
    121: ('Matek',      'H743-Mini'),
    122: ('Matek',      'H743-Slim'),
    123: ('Matek',      'F405-Wing'),
    124: ('Matek',      'F405-SE'),
    125: ('Matek',      'F405-TE'),
    126: ('Matek',      'F765-Wing'),
    127: ('Matek',      'F765-SE'),
    128: ('Matek',      'G474-OSD'),
    129: ('Matek',      'F405-CTR'),
    130: ('SpeedyBee',  'F4 V3'),
    131: ('SpeedyBee',  'F7 V2'),
    132: ('Holybro',    'KakuteH7'),
    133: ('Holybro',    'KakuteF4'),
    134: ('Holybro',    'KakuteF7 Mini'),
    135: ('Holybro',    'Pixhawk 5X'),
    136: ('mRobotics',  'mRo X2.1'),
    137: ('mRobotics',  'mRo Ctrl Zero H7'),
    138: ('CUAV',       'V5+'),
    139: ('CUAV',       'V5 Nano'),
    140: ('Holybro',    'KakuteF7'),
    141: ('Holybro',    'KakuteH7 Mini'),
    142: ('Holybro',    'KakuteH7 v1.3'),
    143: ('Holybro',    'Pixhawk 6C'),
    144: ('Holybro',    'Pixhawk 6X'),
    145: ('SpeedyBee',  'F405 Wing'),
    146: ('SpeedyBee',  'F4 V4'),
    147: ('SpeedyBee',  'F7 V3'),
    148: ('SpeedyBee',  'F405 Mini'),
    149: ('JHEMCU',     'H743HD'),
    150: ('Matek',      'H743-HD'),
    151: ('Matek',      'F405-WTE'),
    152: ('Matek',      'H743-WLite'),
    153: ('Matek',      'F405-HDTE'),
    154: ('Matek',      'H743-Slim v2'),
    155: ('Holybro',    'KakuteH7 v2'),
    156: ('Holybro',    'KakuteH7 Mini v1.3'),
    157: ('Holybro',    'Pix32 v6'),
    158: ('Holybro',    'Pixhawk 6C Mini'),
    159: ('SpeedyBee',  'F405 AIO'),
    160: ('SpeedyBee',  'F7 V3'),
    161: ('Matek',      'H743-Wing v3'),
    162: ('Matek',      'F405-Wing v3'),
    163: ('JHEMCU',     'GHF405AIO'),
    164: ('JHEMCU',     'SPH7 Pro'),
    200: ('Emlid',      'Navio2'),
}


def get_manufacturer_from_vid(vid: int) -> str:
    """Map USB VID to human-readable manufacturer name."""
    return USB_VENDOR_NAMES.get(vid, f'Unknown (VID:0x{vid:04X})')


def get_board_name(board_id: int) -> tuple:
    """Return (manufacturer, model) for an ArduPilot board_id."""
    return BOARD_ID_NAMES.get(board_id, ('Unknown', f'Board ID {board_id}'))


def find_dfu_device():
    """
    Scan USB for an STM32 DFU device.
    Returns usb.core.Device or None.
    """
    if not PYUSB_AVAILABLE:
        return None
    try:
        return usb.core.find(idVendor=STM32_VID, idProduct=STM32_DFU_PID)
    except usb.core.NoBackendError:
        logger.error("libusb backend not found. Install libusb-1.0 (Linux) or run Zadig (Windows).")
        return None
    except Exception as e:
        logger.warning(f"USB scan error: {e}")
        return None


def read_usb_string(device, index) -> str:
    """Safely read a USB string descriptor by index."""
    try:
        if not index:
            return ''
        return usb.util.get_string(device, index) or ''
    except Exception:
        return ''


def enter_dfu_via_1200baud(port_name: str) -> bool:
    """
    Send a 1200-baud pulse on the serial port to trigger DFU entry.
    Supported on most STM32 boards running ArduPilot.
    Returns True on success, False on failure.
    """
    try:
        import serial as pyserial
        s = pyserial.Serial()
        s.port     = port_name
        s.baudrate = 1200
        s.open()
        s.setDTR(False)
        time.sleep(0.25)
        s.close()
        logger.info(f"1200-baud DFU trigger sent on {port_name}")
        return True
    except Exception as e:
        logger.warning(f"1200-baud trigger failed on {port_name}: {e}")
        return False


# ── Exception ────────────────────────────────────────────────────────────────

class DfuError(Exception):
    pass


# ── Main Flasher ─────────────────────────────────────────────────────────────

class DfuFlasher:
    """
    STM32 DfuSe firmware flasher via pyusb.

    Usage:
        flasher = DfuFlasher()
        result  = flasher.flash(bin_bytes, progress_cb=my_cb)
    """

    def __init__(self):
        self._dev  = None
        self._intf = 0          # DFU interface number (always 0 for STM32)

    # ── Low-level USB transfers ───────────────────────────────────────────

    def _ctrl_out(self, request, value, data=None, timeout=5000):
        """Host-to-device DFU control transfer."""
        return self._dev.ctrl_transfer(
            0x21,           # bmRequestType: host→device, class, interface
            request,
            value,
            self._intf,
            data,
            timeout=timeout,
        )

    def _ctrl_in(self, request, length, value=0, timeout=5000):
        """Device-to-host DFU control transfer."""
        return self._dev.ctrl_transfer(
            0xA1,           # bmRequestType: device→host, class, interface
            request,
            value,
            self._intf,
            length,
            timeout=timeout,
        )

    # ── DFU protocol commands ─────────────────────────────────────────────

    def _get_status(self):
        """Returns (bStatus, poll_timeout_ms, bState)."""
        data    = self._ctrl_in(DFU_GETSTATUS, 6)
        bStatus = data[0]
        poll_ms = data[1] | (data[2] << 8) | (data[3] << 16)
        bState  = data[4]
        return bStatus, poll_ms, bState

    def _clear_status(self):
        self._ctrl_out(DFU_CLRSTATUS, 0)

    def _abort(self):
        self._ctrl_out(DFU_ABORT, 0)

    def _dnload(self, block_num: int, data):
        """Send DFU_DNLOAD.  data=None → zero-length (triggers manifestation)."""
        self._dev.ctrl_transfer(
            0x21, DFU_DNLOAD,
            block_num, self._intf,
            data,
            timeout=30000,
        )

    def _wait_idle(self, timeout_s: float = 30.0):
        """Poll DFU_GETSTATUS until dfuDNLOAD-IDLE or dfuIDLE."""
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            bStatus, poll_ms, bState = self._get_status()
            if bState == DFU_ERROR:
                self._clear_status()
                raise DfuError(f"Device in DFU_ERROR (status=0x{bStatus:02x})")
            if bState in (DFU_DNLOAD_IDLE, DFU_IDLE):
                return bState
            time.sleep(max(poll_ms / 1000.0, 0.01))
        raise DfuError("Timeout waiting for dfuIDLE / dfuDNLOAD-IDLE")

    # ── DfuSe extension commands ──────────────────────────────────────────

    def _set_address(self, address: int):
        """DfuSe: Set flash address pointer (CMD 0x21)."""
        cmd = struct.pack('<BI', CMD_SET_ADDRESS, address)
        self._dnload(0, cmd)
        self._wait_idle()

    def _mass_erase(self, progress_cb=None):
        """DfuSe: Mass-erase entire flash (can take up to 30 s)."""
        cmd = bytes([CMD_ERASE])        # 1-byte = mass erase (no address = whole chip)
        self._dnload(0, cmd)
        if progress_cb:
            progress_cb('erase', 0, 'Mass erase started (may take up to 30 s)...')
        deadline = time.time() + 60
        while time.time() < deadline:
            bStatus, poll_ms, bState = self._get_status()
            if bState == DFU_ERROR:
                self._clear_status()
                raise DfuError(f"Mass erase failed (status=0x{bStatus:02x})")
            if bState in (DFU_DNLOAD_IDLE, DFU_IDLE):
                return
            if progress_cb:
                progress_cb('erase', -1, 'Erasing flash...')
            time.sleep(max(poll_ms / 1000.0, 0.2))
        raise DfuError("Mass erase timeout after 60 s")

    def _leave_dfu(self, start_address: int = STM32_FLASH_BASE):
        """
        DfuSe: Exit DFU and jump to application at start_address.
        Sets the address pointer then sends a zero-length DNLOAD,
        which triggers the device to reset and boot the new firmware.
        """
        try:
            self._abort()
        except Exception:
            pass
        try:
            bStatus, poll_ms, bState = self._get_status()
            if bState == DFU_ERROR:
                self._clear_status()
        except Exception:
            pass

        # Point to app start
        cmd = struct.pack('<BI', CMD_SET_ADDRESS, start_address)
        try:
            self._dnload(0, cmd)
            self._wait_idle(timeout_s=5.0)
        except Exception:
            pass

        # Zero-length DNLOAD at block 0 → triggers jump/reset
        try:
            self._dev.ctrl_transfer(
                0x21, DFU_DNLOAD, 0, self._intf, None, timeout=2000
            )
            time.sleep(0.1)
            self._get_status()      # may raise if device already disconnected
        except Exception:
            pass    # expected — device is rebooting

    # ── Board info ────────────────────────────────────────────────────────

    def read_board_info(self) -> dict:
        """
        Read manufacturer and product strings from the USB descriptor.
        Falls back to VID-based lookup when the device reports generic strings.
        """
        mfr_str  = read_usb_string(self._dev, self._dev.iManufacturer)
        prod_str = read_usb_string(self._dev, self._dev.iProduct)

        vid = self._dev.idVendor
        pid = self._dev.idProduct

        # If generic STM32 ROM DFU strings, replace manufacturer from VID table
        if 'STMicro' in mfr_str or not mfr_str:
            mfr_display = get_manufacturer_from_vid(vid)
        else:
            mfr_display = mfr_str

        return {
            'usb_manufacturer': mfr_str,
            'usb_product':      prod_str,
            'manufacturer':     mfr_display,
            'model':            prod_str if prod_str and 'BOOTLOADER' not in prod_str.upper() else '',
            'vid':              vid,
            'pid':              pid,
            'vid_str':          f'0x{vid:04X}',
            'pid_str':          f'0x{pid:04X}',
        }

    # ── Main flash routine ────────────────────────────────────────────────

    def flash(self, bin_data: bytes, progress_cb=None,
              start_address: int = STM32_FLASH_BASE) -> dict:
        """
        Flash raw binary firmware to an STM32 DFU device.

        Args:
            bin_data:       Raw .bin firmware bytes
            progress_cb:    fn(stage: str, percent: int, message: str)
            start_address:  Flash target address (default 0x08000000)

        Returns:
            dict { 'success': bool, 'message': str, 'board_info': dict }
        """
        def progress(stage, pct, msg):
            logger.info(f"[{stage}] {pct}% — {msg}")
            if progress_cb:
                progress_cb(stage, pct, msg)

        board_info = {}

        try:
            if not PYUSB_AVAILABLE:
                return {
                    'success': False,
                    'message': 'pyusb not installed. Run: pip install pyusb',
                }

            # ── 1. Locate DFU device ──────────────────────────────────────
            progress('dfu_detect', 0, 'Scanning USB for DFU device...')
            self._dev = find_dfu_device()
            if not self._dev:
                return {
                    'success': False,
                    'message': 'No STM32 DFU device found (0x0483:0xDF11). '
                               'Board did not enter DFU mode.',
                }
            progress('dfu_detect', 50,
                     f'Found DFU device {self._dev.idVendor:04X}:{self._dev.idProduct:04X}')

            # ── 2. Claim USB interface ────────────────────────────────────
            try:
                if self._dev.is_kernel_driver_active(self._intf):
                    self._dev.detach_kernel_driver(self._intf)
            except (AttributeError, NotImplementedError):
                pass    # not needed on Windows / macOS
            usb.util.claim_interface(self._dev, self._intf)

            # ── 3. Read board info ────────────────────────────────────────
            board_info = self.read_board_info()
            progress('dfu_detect', 100,
                     f"Board: {board_info['manufacturer']} "
                     f"{board_info['model'] or '(unknown model)'}")

            # ── 4. Ensure DFU is in a clean IDLE state ────────────────────
            progress('dfu_init', 0, 'Checking DFU state...')
            bStatus, poll_ms, bState = self._get_status()
            state_name = DFU_STATE_NAMES.get(bState, f'state_{bState}')
            progress('dfu_init', 25, f'DFU state: {state_name}')

            if bState == DFU_ERROR:
                self._clear_status()
                bStatus, poll_ms, bState = self._get_status()

            if bState == APP_IDLE:
                # Application is running — detach to enter DFU mode
                self._ctrl_out(DFU_DETACH, 1000, timeout=5000)
                time.sleep(1.0)
                bStatus, poll_ms, bState = self._get_status()

            if bState not in (DFU_IDLE, DFU_DNLOAD_IDLE):
                # Try abort + clear as a last resort
                try:
                    self._abort()
                    self._clear_status()
                except Exception:
                    pass

            progress('dfu_init', 100, 'DFU interface ready')

            # ── 5. Mass erase ─────────────────────────────────────────────
            progress('erase', 0, 'Starting mass erase...')
            self._mass_erase(
                progress_cb=lambda s, p, m: progress(s, p, m)
            )
            progress('erase', 100, 'Flash erased successfully')

            # ── 6. Set start address ──────────────────────────────────────
            progress('program', 0, f'Setting address 0x{start_address:08X}...')
            self._set_address(start_address)

            # ── 7. Program in 2 KB chunks ─────────────────────────────────
            total     = len(bin_data)
            block_num = 2       # DfuSe data blocks start at wValue=2
            offset    = 0

            progress('program', 0, f'Programming {total} bytes...')
            while offset < total:
                chunk = bin_data[offset:offset + XFER_SIZE]
                self._dnload(block_num, chunk)
                self._wait_idle()
                offset    += len(chunk)
                block_num += 1
                pct = int(offset * 100 / total)
                progress('program', pct, f'{offset}/{total} bytes')

            progress('program', 100, 'Programming complete')

            # ── 8. Leave DFU — board reboots into new firmware ───────────
            progress('reboot', 0, 'Leaving DFU mode...')
            self._leave_dfu(start_address)
            progress('reboot', 100, 'Board rebooting into new firmware')

            return {
                'success':    True,
                'message':    'DFU flash complete — board is rebooting.',
                'board_info': board_info,
            }

        except DfuError as e:
            logger.error(f"DFU error: {e}")
            return {'success': False, 'message': str(e), 'board_info': board_info}
        except Exception as e:
            logger.error(f"Unexpected DFU error: {e}", exc_info=True)
            return {'success': False, 'message': f'Unexpected error: {e}', 'board_info': board_info}
        finally:
            if self._dev:
                try:
                    usb.util.release_interface(self._dev, self._intf)
                    usb.util.dispose_resources(self._dev)
                except Exception:
                    pass
                self._dev = None
