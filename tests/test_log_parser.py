import os
from unittest.mock import MagicMock

import pytest

from log_parser import LogParser, KEY_MSG_TYPES


@pytest.fixture
def sample_bin_path(tmp_path):
    # We won't actually parse binary content; we'll patch DFReader_binary.
    p = tmp_path / "test.bin"
    p.write_bytes(b"FAKE")
    return str(p)


@pytest.fixture
def parser():
    return LogParser()


def _make_msg(msg_type, **fields):
    m = MagicMock()
    m.get_type.return_value = msg_type
    d = {"mavpackettype": msg_type}
    d.update(fields)
    m.to_dict.return_value = d
    return m


def test_parse_bin_uses_dfreader_binary(monkeypatch, parser, sample_bin_path):
    # Fake DFReader_binary instance yielding a couple of messages and then None
    msgs = [
        _make_msg("ATT", Roll=1.0, Pitch=2.0, Yaw=3.0),
        _make_msg("GPS", Lat=10, Lng=20, Alt=30),
        None,
    ]

    class FakeLog:
        def __init__(self, seq):
            self._seq = iter(seq)

        def recv_msg(self):
            return next(self._seq, None)

    fake_log = FakeLog(msgs)

    def fake_dfreader_binary(path):  # noqa: D401
        return fake_log

    monkeypatch.setattr("log_parser.DFReader_binary", fake_dfreader_binary)

    summary = parser.parse(sample_bin_path)

    assert summary["filename"] == os.path.basename(sample_bin_path)
    # We saw 2 messages
    assert summary["total_messages"] == 2
    # ATT and GPS should both be present
    assert "ATT" in summary["message_types"]
    assert "GPS" in summary["message_types"]

    att_entry = summary["message_types"]["ATT"]
    assert att_entry["count"] == 1
    assert set(["Roll", "Pitch", "Yaw"]).issubset(att_entry["fields"])


def test_get_message_data_downsampling(parser):
    parser._is_parsed = True
    # Create synthetic data for a type
    parser.parsed_data["ATT"] = [
        {"Roll": i, "Pitch": i * 2, "Yaw": i * 3} for i in range(100)
    ]
    parser.msg_counts["ATT"] = 100
    parser.msg_fields["ATT"] = ["Roll", "Pitch", "Yaw"]

    # Request small number of points
    data = parser.get_message_data("ATT", max_points=10)
    assert len(data) == 10
    # Check that it's monotonically increasing Roll
    rolls = [d["Roll"] for d in data]
    assert rolls == sorted(rolls)


def test_get_message_types_and_fields(parser):
    parser._is_parsed = True
    parser.msg_counts = {"ATT": 5, "GPS": 3}
    parser.msg_fields = {"ATT": ["Roll"], "GPS": ["Lat", "Lng"]}

    types = parser.get_message_types()
    assert types == ["ATT", "GPS"]

    assert parser.get_fields_for_type("ATT") == ["Roll"]
    assert parser.get_fields_for_type("GPS") == ["Lat", "Lng"]
    assert parser.get_fields_for_type("FOO") == []


def test_get_summary_without_parse(parser):
    # _is_parsed stays False
    assert parser.get_summary() == {"error": "No log parsed yet"}


def test_get_message_data_without_parse(parser):
    assert parser.get_message_data("ATT") == []
