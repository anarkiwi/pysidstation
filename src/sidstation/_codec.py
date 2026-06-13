"""Low-level encoding helpers and constants for the SidStation SysEx format.

This module deals only with raw bytes: splitting a file into individual MIDI
System Exclusive (SysEx) messages, detecting the manufacturer prefix, and the
high/low nibble packing used for patch payloads.  Everything here is free of
synth-specific semantics so it can be tested in isolation.
"""

from __future__ import annotations

from typing import Iterable, Sequence

from .exceptions import SysExParseError

# -- MIDI SysEx framing ------------------------------------------------------

#: Start-of-Exclusive status byte that opens every SysEx message.
SYSEX_START = 0xF0
#: End-of-Exclusive status byte that closes every SysEx message.
SYSEX_END = 0xF7

# -- SidStation message bytes ------------------------------------------------

#: Pad byte (ASCII ``-``) used to fill the fixed-width header padding regions.
PAD_BYTE = 0x2D
#: Marks the start of the patch data inside a patch-dump message (ASCII ``E``).
PATCH_DATA_MARKER = 0x45
#: "Magic" byte that follows the type id in a *patch all clear* message.
ALL_CLEAR_MAGIC = 0x45
#: Number of :data:`PAD_BYTE` filler bytes in a patch-dump header.
PATCH_PAD_LEN = 24
#: Number of :data:`PAD_BYTE` filler bytes in an all-clear message.
ALL_CLEAR_PAD_LEN = 14
#: Number of leading ASCII bytes that hold the patch name.
NAME_LEN = 10

# -- Message type ids (the byte directly after the manufacturer prefix) ------

MSG_ALL_CLEAR = 0x01
MSG_PATCH_DUMP = 0x02
MSG_SKIP = 0x03
MSG_DIRECT_PROGRAM = 0x04

# -- Manufacturer / device prefixes (the bytes between 0xF0 and the type id) -

#: Prefix used by ``SidStation_Presets_r1.syx`` and other librarian exports.
#: ``00`` (Europe/USA id) ``45`` ``01`` ``00`` (base channel / padding).
DEFAULT_PREFIX = bytes((0x00, 0x45, 0x01, 0x00))

#: Prefix documented in the SidStation owner's manual for hardware dumps:
#: ``00`` Europe/USA, ``20`` Europe, ``3C`` Elektron, ``01`` SidStation,
#: ``00`` base channel.
ELEKTRON_HARDWARE_PREFIX = bytes((0x00, 0x20, 0x3C, 0x01, 0x00))

#: Prefixes that :func:`detect_prefix` will recognise, longest first.
KNOWN_PREFIXES = (ELEKTRON_HARDWARE_PREFIX, DEFAULT_PREFIX)


def split_sysex(data: bytes) -> list[bytes]:
    """Split *data* into a list of complete SysEx messages.

    Each returned item starts with :data:`SYSEX_START` and ends with
    :data:`SYSEX_END`.  The input is expected to be a tight concatenation of
    messages with no bytes in between (which is how SidStation banks are
    stored); anything else raises :class:`SysExParseError`.
    """
    data = bytes(data)
    messages: list[bytes] = []
    i = 0
    n = len(data)
    while i < n:
        if data[i] != SYSEX_START:
            raise SysExParseError(
                f"expected SysEx start (0xF0) at offset {i}, found 0x{data[i]:02x}"
            )
        end = data.find(SYSEX_END, i + 1)
        if end == -1:
            raise SysExParseError(f"unterminated SysEx message starting at offset {i}")
        messages.append(data[i : end + 1])
        i = end + 1
    return messages


def detect_prefix(message: bytes, prefixes: Sequence[bytes] = KNOWN_PREFIXES) -> bytes:
    """Return the manufacturer prefix that *message* begins with.

    The longest matching prefix wins, so the 5-byte Elektron hardware prefix is
    preferred over the 4-byte librarian prefix when both could match.
    """
    if not message or message[0] != SYSEX_START:
        raise SysExParseError("message does not start with 0xF0")
    for prefix in sorted(prefixes, key=len, reverse=True):
        if message[1 : 1 + len(prefix)] == prefix:
            return prefix
    raise SysExParseError(f"unrecognised manufacturer prefix: {message[1:6].hex(' ')}")


def encode_nibbles(payload: Iterable[int]) -> bytes:
    """Pack each byte of *payload* into a high nibble then a low nibble.

    ``0xAB`` becomes the two bytes ``0x0A 0x0B``.  This is how every patch byte
    after the 10-byte name is transmitted, keeping all data bytes below 0x80 as
    MIDI requires.
    """
    out = bytearray()
    for byte in payload:
        byte &= 0xFF
        out.append((byte >> 4) & 0x0F)
        out.append(byte & 0x0F)
    return bytes(out)


def decode_nibbles(nibbles: bytes) -> bytes:
    """Inverse of :func:`encode_nibbles`.

    Consumes pairs of nibble bytes and recombines them into 8-bit values.
    Raises :class:`SysExParseError` if the count is odd or a nibble exceeds
    0x0F (which would mean the stream is not valid nibble-packed data).
    """
    if len(nibbles) % 2:
        raise SysExParseError("nibble payload has an odd length")
    out = bytearray(len(nibbles) // 2)
    for i in range(0, len(nibbles), 2):
        hi = nibbles[i]
        lo = nibbles[i + 1]
        if hi > 0x0F or lo > 0x0F:
            raise SysExParseError(f"nibble out of range at offset {i} (0x{hi:02x} 0x{lo:02x})")
        out[i // 2] = (hi << 4) | lo
    return bytes(out)
