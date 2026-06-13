"""Tests for the low-level codec helpers."""

from __future__ import annotations

import pytest

import sidstation
from sidstation import _codec
from sidstation.exceptions import SysExParseError


def test_nibble_roundtrip_all_bytes():
    payload = bytes(range(256))
    encoded = _codec.encode_nibbles(payload)
    assert len(encoded) == 512
    assert all(b <= 0x0F for b in encoded)
    assert _codec.decode_nibbles(encoded) == payload


def test_encode_nibbles_order_is_high_then_low():
    assert _codec.encode_nibbles(b"\xab") == b"\x0a\x0b"


def test_decode_nibbles_rejects_odd_length():
    with pytest.raises(SysExParseError):
        _codec.decode_nibbles(b"\x01\x02\x03")


def test_decode_nibbles_rejects_out_of_range_nibble():
    with pytest.raises(SysExParseError):
        _codec.decode_nibbles(b"\x10\x00")


def test_split_sysex_counts_messages(presets_bytes):
    messages = _codec.split_sysex(presets_bytes)
    assert len(messages) == 91
    assert all(m[0] == _codec.SYSEX_START and m[-1] == _codec.SYSEX_END for m in messages)
    # Reassembling the split is lossless.
    assert b"".join(messages) == presets_bytes


def test_split_sysex_unterminated():
    with pytest.raises(SysExParseError):
        _codec.split_sysex(b"\xf0\x00\x45\x01\x00\x02")


def test_split_sysex_unexpected_leading_byte():
    with pytest.raises(SysExParseError):
        _codec.split_sysex(b"\x00\xf0\xf7")


def test_detect_prefix_librarian():
    msg = bytes([0xF0]) + _codec.DEFAULT_PREFIX + bytes([0x02, 0xF7])
    assert _codec.detect_prefix(msg) == _codec.DEFAULT_PREFIX


def test_detect_prefix_elektron_hardware():
    msg = bytes([0xF0]) + _codec.ELEKTRON_HARDWARE_PREFIX + bytes([0x02, 0xF7])
    assert _codec.detect_prefix(msg) == _codec.ELEKTRON_HARDWARE_PREFIX


def test_detect_prefix_unknown():
    with pytest.raises(SysExParseError):
        _codec.detect_prefix(bytes([0xF0, 0x7E, 0x00, 0xF7]))


def test_public_codec_exports():
    # The helpers are re-exported at the top level.
    assert sidstation.encode_nibbles(b"\x12") == b"\x01\x02"
    assert sidstation.decode_nibbles(b"\x01\x02") == b"\x12"
