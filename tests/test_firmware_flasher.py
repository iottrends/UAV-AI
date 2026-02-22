import json
import base64
import zlib
import struct
import pytest
from unittest.mock import MagicMock, patch
from firmware_flasher import FirmwareFlasher, FlashError, INSYNC, OK, GET_SYNC, EOC, INFO_BOARD_ID

def create_mock_apj(board_id=123, image_data=b"hello world"):
    compressed = zlib.compress(image_data)
    b64 = base64.b64encode(compressed).decode('utf-8')
    return {
        "board_id": board_id,
        "image": b64
    }

def test_parse_apj(tmp_path):
    apj_content = create_mock_apj(123, b"firmware_image_bytes")
    apj_path = tmp_path / "test.apj"
    with open(apj_path, "w") as f:
        json.dump(apj_content, f)
    
    flasher = FirmwareFlasher()
    board_id, image = flasher.parse_apj(str(apj_path))
    
    assert board_id == 123
    assert image == b"firmware_image_bytes"

@patch('serial.Serial')
def test_flash_success(mock_serial_class, tmp_path):
    # Setup mock serial
    mock_serial = MagicMock()
    mock_serial_class.return_value = mock_serial
    mock_serial.is_open = True
    
    # Define response sequence for bootloader protocol
    # 1. Sync: GET_SYNC(0x21), EOC(0x20) -> INSYNC(0x12), OK(0x10)
    # 2. Get Info (Board ID): GET_DEVICE(0x22), INFO_BOARD_ID(2), EOC(0x20) -> INSYNC(0x12), 4-byte ID, OK(0x10)
    # 3. Get Info (BL Rev): ...
    # 4. Get Info (Flash Size): ...
    # 5. Erase: CHIP_ERASE(0x23), EOC(0x20) -> INSYNC(0x12), OK(0x10)
    # 6. Program: PROG_MULTI(0x27), len, data, EOC(0x20) -> INSYNC(0x12), OK(0x10)
    # 7. Get CRC: GET_CRC(0x29), EOC(0x20) -> INSYNC(0x12), 4-byte CRC, OK(0x10)
    # 8. Reboot: REBOOT(0x30), EOC(0x20)
    
    board_id = 123
    image_data = b"fake_fw"
    
    # Helper to pack 4-byte little-endian
    def pack_i(val): return struct.pack('<I', val)

    responses = [
        bytes([INSYNC, OK]), # Sync response
        bytes([INSYNC]) + pack_i(board_id) + bytes([OK]), # Board ID response
        bytes([INSYNC]) + pack_i(5) + bytes([OK]),        # BL Rev response
        bytes([INSYNC]) + pack_i(1024*1024) + bytes([OK]), # Flash size (1MB)
        bytes([INSYNC, OK]), # Erase response
        bytes([INSYNC, OK]), # Program response
        bytes([INSYNC]) + pack_i(0) + bytes([OK]), # CRC response (mocked as 0 for simplicity)
    ]
    
    def side_effect(count):
        if not responses: return b""
        res = responses.pop(0)
        return res[:count] # This is simplified, real _recv might be called multiple times for one response

    # More robust side effect for _recv
    mock_serial.read_responses = [
        bytes([INSYNC, OK]), 
        bytes([INSYNC]), pack_i(board_id), bytes([OK]),
        bytes([INSYNC]), pack_i(5), bytes([OK]),
        bytes([INSYNC]), pack_i(1024*1024), bytes([OK]),
        bytes([INSYNC, OK]),
        bytes([INSYNC, OK]),
        bytes([INSYNC]), pack_i(0), bytes([OK]),
    ]
    
    # Flat list of bytes to return
    all_bytes = b"".join(mock_serial.read_responses)
    byte_list = [all_bytes[i:i+1] for i in range(len(all_bytes))]
    
    def mock_read(count=1):
        out = b""
        for _ in range(count):
            if byte_list:
                out += byte_list.pop(0)
        return out
    
    mock_serial.read.side_effect = mock_read

    # Mock parse_apj to return a matching CRC
    with patch.object(FirmwareFlasher, 'parse_apj', return_value=(board_id, image_data)):
        # Also need to mock crc32 to return 0 to match our response
        with patch('binascii.crc32', return_value=0):
            flasher = FirmwareFlasher()
            result = flasher.flash("COM_FAKE", "test.apj")
            
            assert result['success'] is True
            assert 'successfully' in result['message']

@patch('serial.Serial')
def test_flash_board_id_mismatch(mock_serial_class):
    mock_serial = MagicMock()
    mock_serial_class.return_value = mock_serial
    
    board_id_fw = 123
    board_id_dev = 456
    
    def pack_i(val): return struct.pack('<I', val)
    
    responses = [
        bytes([INSYNC, OK]), # Sync
        bytes([INSYNC]) + pack_i(board_id_dev) + bytes([OK]), # Board ID
        bytes([INSYNC]) + pack_i(5) + bytes([OK]), # BL Rev
        bytes([INSYNC]) + pack_i(1024*1024) + bytes([OK]), # Size
    ]
    all_bytes = b"".join(responses)
    byte_list = [all_bytes[i:i+1] for i in range(len(all_bytes))]

    def mock_read(count=1):
        out = b""
        for _ in range(count):
            if byte_list:
                out += byte_list.pop(0)
        return out
    mock_serial.read.side_effect = mock_read

    with patch.object(FirmwareFlasher, 'parse_apj', return_value=(board_id_fw, b"data")):
        flasher = FirmwareFlasher()
        result = flasher.flash("COM_FAKE", "test.apj")
        
        assert result['success'] is False
        assert "Board ID mismatch" in result['message']

def test_flash_error_propagation():
    flasher = FirmwareFlasher()
    # Test _recv_insync failure
    flasher._port = MagicMock()
    flasher._port.read.return_value = b"\x00" # Not INSYNC
    
    with pytest.raises(FlashError) as excinfo:
        flasher._recv_insync()
    assert "Expected INSYNC" in str(excinfo.value)
