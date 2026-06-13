"""Read and write Elektron SidStation patch (``.syx``) SysEx files.

Quick start::

    import sidstation

    bank = sidstation.read("SidStation_Presets_r1.syx")
    print(len(bank), "patches")
    print(bank[0].name)

    bank[0].name = "My Patch"
    bank[0].oscillators[0].waveform = sidstation.Waveform.SAW
    bank.write("edited.syx")

``bank.to_bytes()`` reproduces an unmodified input file byte-for-byte.
"""

from __future__ import annotations

import os

from ._codec import (
    ALL_CLEAR_MAGIC,
    ALL_CLEAR_PAD_LEN,
    DEFAULT_PREFIX,
    ELEKTRON_HARDWARE_PREFIX,
    KNOWN_PREFIXES,
    MSG_ALL_CLEAR,
    MSG_DIRECT_PROGRAM,
    MSG_PATCH_DUMP,
    MSG_SKIP,
    NAME_LEN,
    PAD_BYTE,
    PATCH_DATA_MARKER,
    SYSEX_END,
    SYSEX_START,
    decode_nibbles,
    detect_prefix,
    encode_nibbles,
    split_sysex,
)
from .bank import Bank, ControlMessage
from .enums import LfoCtrlDest, LfoCtrlSource, LfoType, Waveform
from .exceptions import SidStationError, SysExParseError
from .patch import (
    DirectController,
    Lfo,
    Oscillator,
    Patch,
    Table,
    TableStep,
    parse_tables,
    serialize_tables,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # Top-level helpers
    "read",
    "write",
    "loads",
    "dumps",
    # Core types
    "Bank",
    "Patch",
    "ControlMessage",
    "Oscillator",
    "Lfo",
    "DirectController",
    "Table",
    "TableStep",
    "parse_tables",
    "serialize_tables",
    # Enums
    "Waveform",
    "LfoType",
    "LfoCtrlSource",
    "LfoCtrlDest",
    # Exceptions
    "SidStationError",
    "SysExParseError",
    # Low-level codec
    "split_sysex",
    "detect_prefix",
    "encode_nibbles",
    "decode_nibbles",
    # Constants
    "SYSEX_START",
    "SYSEX_END",
    "PAD_BYTE",
    "PATCH_DATA_MARKER",
    "ALL_CLEAR_MAGIC",
    "ALL_CLEAR_PAD_LEN",
    "NAME_LEN",
    "DEFAULT_PREFIX",
    "ELEKTRON_HARDWARE_PREFIX",
    "KNOWN_PREFIXES",
    "MSG_ALL_CLEAR",
    "MSG_PATCH_DUMP",
    "MSG_SKIP",
    "MSG_DIRECT_PROGRAM",
]


def read(path: str | os.PathLike[str]) -> Bank:
    """Read and parse a SidStation ``.syx`` file into a :class:`Bank`."""
    return Bank.read(path)


def loads(data: bytes) -> Bank:
    """Parse a SidStation ``.syx`` byte string into a :class:`Bank`."""
    return Bank.from_bytes(data)


def write(bank: Bank, path: str | os.PathLike[str]) -> None:
    """Write *bank* to *path* as a ``.syx`` file."""
    bank.write(path)


def dumps(bank: Bank) -> bytes:
    """Serialise *bank* to a ``.syx`` byte string."""
    return bank.to_bytes()
