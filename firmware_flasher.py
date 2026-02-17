"""
ArduPilot STM32 Bootloader Serial Protocol Implementation.

Handles .apj firmware parsing and flashing over the bootloader's serial protocol.
Protocol uses INSYNC (0x12) / EOC (0x20) framing with 252-byte program chunks.
"""

import json
import base64
import zlib
import struct
import time
import logging
import binascii

import serial

logger = logging.getLogger('firmware_flasher')

# Bootloader protocol bytes
INSYNC = 0x12
EOC = 0x20
OK = 0x10
FAILED = 0x11
INVALID = 0x13
BAD_SILICON_REV = 0x14

# Bootloader commands
NOP = 0x00
GET_SYNC = 0x21
GET_DEVICE = 0x22
CHIP_ERASE = 0x23
CHIP_VERIFY = 0x24  # not used — we do CRC
PROG_MULTI = 0x27
READ_MULTI = 0x28   # not used
GET_CRC = 0x29
REBOOT = 0x30
GET_OTP = 0x2A       # not used
GET_SN = 0x2B        # not used
GET_CHIP = 0x2C       # not used
SET_BOOT_DELAY = 0x2D # not used

# Device info fields
INFO_BL_REV = 1
INFO_BOARD_ID = 2
INFO_BOARD_REV = 3
INFO_FLASH_SIZE = 4

PROG_MULTI_MAX = 252  # max bytes per PROG_MULTI command


class FlashError(Exception):
    """Raised when a flash operation fails."""
    pass


class FirmwareFlasher:
    """Handles ArduPilot .apj firmware parsing and STM32 bootloader flashing."""

    def __init__(self):
        self._port = None

    # ── .apj Parsing ──────────────────────────────────────────────────────

    @staticmethod
    def parse_apj(filepath):
        """
        Read an .apj file and extract the firmware image.

        Returns:
            tuple: (board_id: int, image_bytes: bytes)
        """
        with open(filepath, 'r') as f:
            apj = json.load(f)

        board_id = apj.get('board_id', 0)
        image_b64 = apj.get('image', '')

        # Decode base64, then zlib-decompress
        raw = base64.b64decode(image_b64)
        try:
            image = zlib.decompress(raw)
        except zlib.error:
            # Some .apj files store uncompressed images
            image = raw

        logger.info(f"Parsed .apj: board_id={board_id}, image_size={len(image)} bytes")
        return board_id, image

    # ── Serial Helpers ────────────────────────────────────────────────────

    def _open_port(self, port_name, baudrate=115200):
        """Open serial port with bootloader-appropriate settings."""
        self._port = serial.Serial(
            port=port_name,
            baudrate=baudrate,
            timeout=0.5,
            write_timeout=5,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
        )

    def _close_port(self):
        if self._port and self._port.is_open:
            self._port.close()
            self._port = None

    def _send(self, data):
        """Send bytes to bootloader."""
        if isinstance(data, int):
            data = bytes([data])
        self._port.write(data)

    def _recv(self, count=1, timeout=2.0):
        """Receive exactly `count` bytes, with overall timeout."""
        old_timeout = self._port.timeout
        self._port.timeout = timeout
        data = self._port.read(count)
        self._port.timeout = old_timeout
        if len(data) != count:
            raise FlashError(f"Read timeout: expected {count} bytes, got {len(data)}")
        return data

    def _recv_insync(self, timeout=2.0):
        """Expect INSYNC byte."""
        b = self._recv(1, timeout)
        if b[0] != INSYNC:
            raise FlashError(f"Expected INSYNC (0x12), got 0x{b[0]:02x}")

    def _recv_ok(self, timeout=2.0):
        """Expect INSYNC + OK response."""
        self._recv_insync(timeout)
        b = self._recv(1, timeout)
        if b[0] != OK:
            if b[0] == FAILED:
                raise FlashError("Bootloader returned FAILED")
            elif b[0] == INVALID:
                raise FlashError("Bootloader returned INVALID")
            elif b[0] == BAD_SILICON_REV:
                raise FlashError("Bootloader returned BAD_SILICON_REV")
            else:
                raise FlashError(f"Expected OK (0x10), got 0x{b[0]:02x}")

    # ── Bootloader Commands ───────────────────────────────────────────────

    def _sync(self):
        """Send GET_SYNC command and expect OK."""
        self._port.flushInput()
        self._send(bytes([GET_SYNC, EOC]))
        self._recv_ok()

    def _get_device_info(self, info_type):
        """Request a device info value."""
        self._send(bytes([GET_DEVICE, info_type, EOC]))
        self._recv_insync()
        raw = self._recv(4)
        value = struct.unpack('<I', raw)[0]
        self._recv(1)  # OK byte
        return value

    def _erase(self):
        """Erase the flash. Can take ~20 seconds on some boards."""
        self._send(bytes([CHIP_ERASE, EOC]))
        self._recv_ok(timeout=30.0)

    def _program_chunk(self, data):
        """Program one chunk (up to PROG_MULTI_MAX bytes)."""
        length = len(data)
        self._send(bytes([PROG_MULTI, length]))
        self._send(data)
        self._send(bytes([EOC]))
        self._recv_ok(timeout=5.0)

    def _get_crc(self):
        """Request CRC32 of programmed flash."""
        self._send(bytes([GET_CRC, EOC]))
        self._recv_insync(timeout=10.0)
        raw = self._recv(4, timeout=10.0)
        crc = struct.unpack('<I', raw)[0]
        self._recv(1)  # OK byte
        return crc

    def _reboot(self):
        """Send reboot command."""
        self._send(bytes([REBOOT, EOC]))
        # Don't wait for response — board reboots immediately
        time.sleep(0.1)

    # ── Main Flash Routine ────────────────────────────────────────────────

    def flash(self, port_name, apj_path, progress_callback=None, force=False):
        """
        Flash an .apj firmware file over the bootloader protocol.

        Args:
            port_name: Serial port (e.g. 'COM3' or '/dev/ttyACM0')
            apj_path: Path to .apj firmware file
            progress_callback: fn(stage: str, percent: int, message: str)
            force: Skip board_id validation if True

        Returns:
            dict with 'success' bool and 'message' string

        Raises:
            FlashError on protocol-level failures
        """
        def progress(stage, percent, message):
            logger.info(f"[{stage}] {percent}% — {message}")
            if progress_callback:
                progress_callback(stage, percent, message)

        try:
            # 1. Parse .apj
            progress('parse', 0, f'Parsing {apj_path}...')
            board_id, image = self.parse_apj(apj_path)
            progress('parse', 100, f'Firmware image: {len(image)} bytes, board_id={board_id}')

            # 2. Open serial port (with retry for Windows serial quirks)
            progress('connect', 0, f'Opening {port_name} at 115200...')
            last_err = None
            for attempt in range(5):
                try:
                    self._open_port(port_name)
                    break
                except serial.SerialException as e:
                    last_err = e
                    time.sleep(1)
            else:
                raise FlashError(f"Cannot open {port_name}: {last_err}")
            progress('connect', 50, 'Port opened, syncing with bootloader...')

            # 3. Sync with bootloader
            synced = False
            for attempt in range(10):
                try:
                    self._sync()
                    synced = True
                    break
                except FlashError:
                    # Send a few NOPs and retry
                    self._port.flushInput()
                    self._send(bytes([NOP] * 4))
                    time.sleep(0.2)
            if not synced:
                raise FlashError("Could not sync with bootloader after 10 attempts")
            progress('connect', 100, 'Synced with bootloader')

            # 4. Get device info
            progress('info', 0, 'Reading device info...')
            dev_board_id = self._get_device_info(INFO_BOARD_ID)
            dev_bl_rev = self._get_device_info(INFO_BL_REV)
            dev_flash_size = self._get_device_info(INFO_FLASH_SIZE)
            progress('info', 100,
                     f'Board ID: {dev_board_id}, BL rev: {dev_bl_rev}, '
                     f'Flash: {dev_flash_size // 1024}KB')

            # 5. Validate board ID
            if not force and board_id != dev_board_id:
                raise FlashError(
                    f"Board ID mismatch: firmware expects {board_id}, "
                    f"device reports {dev_board_id}. Use force=True to override."
                )

            # Check image fits in flash
            if len(image) > dev_flash_size:
                raise FlashError(
                    f"Firmware image ({len(image)} bytes) exceeds flash size "
                    f"({dev_flash_size} bytes)"
                )

            # 6. Erase
            progress('erase', 0, 'Erasing flash (this may take ~20s)...')
            self._erase()
            progress('erase', 100, 'Flash erased')

            # 7. Program
            progress('program', 0, 'Programming...')
            total = len(image)
            offset = 0
            while offset < total:
                chunk_size = min(PROG_MULTI_MAX, total - offset)
                self._program_chunk(image[offset:offset + chunk_size])
                offset += chunk_size
                pct = int(offset * 100 / total)
                progress('program', pct, f'{offset}/{total} bytes')
            progress('program', 100, 'Programming complete')

            # 8. CRC verify
            progress('verify', 0, 'Verifying CRC...')
            # Pad image to full flash for CRC comparison
            padded = image + b'\xff' * (dev_flash_size - len(image))
            expected_crc = binascii.crc32(padded) & 0xFFFFFFFF
            device_crc = self._get_crc()
            if expected_crc != device_crc:
                raise FlashError(
                    f"CRC mismatch: expected 0x{expected_crc:08x}, "
                    f"device reports 0x{device_crc:08x}"
                )
            progress('verify', 100, 'CRC verified OK')

            # 9. Reboot
            progress('reboot', 0, 'Rebooting into firmware...')
            self._reboot()
            progress('reboot', 100, 'Reboot command sent — flash complete!')

            return {'success': True, 'message': 'Firmware flashed and verified successfully'}

        except FlashError as e:
            logger.error(f"Flash error: {e}")
            return {'success': False, 'message': str(e)}
        except Exception as e:
            logger.error(f"Unexpected error during flash: {e}")
            return {'success': False, 'message': f'Unexpected error: {e}'}
        finally:
            self._close_port()

    # ── MAVLink Reboot-to-Bootloader ──────────────────────────────────────

    @staticmethod
    def reboot_to_bootloader(mav_conn, target_sys=1, target_comp=1):
        """
        Send MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN with param1=3 to stay in bootloader.

        Args:
            mav_conn: pymavlink connection object
            target_sys: target system ID
            target_comp: target component ID
        """
        from pymavlink import mavutil
        mav_conn.mav.command_long_send(
            target_sys,
            target_comp,
            mavutil.mavlink.MAV_CMD_PREFLIGHT_REBOOT_SHUTDOWN,
            0,   # confirmation
            3.0, # param1 = 3 → stay in bootloader
            0, 0, 0, 0, 0, 0
        )
        logger.info("Sent reboot-to-bootloader command (param1=3)")
