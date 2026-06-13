"""The :class:`Bank` container and non-patch :class:`ControlMessage`."""

from __future__ import annotations

import os
from typing import Iterator, Sequence, Union

from ._codec import (
    ALL_CLEAR_MAGIC,
    ALL_CLEAR_PAD_LEN,
    DEFAULT_PREFIX,
    KNOWN_PREFIXES,
    MSG_ALL_CLEAR,
    MSG_PATCH_DUMP,
    PAD_BYTE,
    SYSEX_END,
    SYSEX_START,
    detect_prefix,
    split_sysex,
)
from .exceptions import SysExParseError
from .patch import Patch

Message = Union[Patch, "ControlMessage"]


class ControlMessage:
    """Any SidStation SysEx message that is not a patch dump.

    The all-clear, skip-patch and direct-program messages are stored opaquely
    as a type id plus the raw payload between it and the trailing ``0xF7`` so
    they survive a read/write cycle untouched.
    """

    __slots__ = ("type_id", "payload")

    def __init__(self, type_id: int, payload: bytes = b"") -> None:
        self.type_id = type_id & 0xFF
        self.payload = bytes(payload)

    @classmethod
    def all_clear(cls, pad_len: int = ALL_CLEAR_PAD_LEN) -> ControlMessage:
        """Build a *patch all clear* message (sent first to wipe the device)."""
        return cls(MSG_ALL_CLEAR, bytes([ALL_CLEAR_MAGIC]) + bytes([PAD_BYTE]) * pad_len)

    @property
    def is_all_clear(self) -> bool:
        return self.type_id == MSG_ALL_CLEAR

    @classmethod
    def from_sysex(cls, message: bytes, prefix: bytes) -> ControlMessage:
        o = 1 + len(prefix)
        return cls(message[o], message[o + 1 : -1])

    def to_sysex(self, prefix: bytes = DEFAULT_PREFIX) -> bytes:
        return (
            bytes([SYSEX_START])
            + bytes(prefix)
            + bytes([self.type_id])
            + self.payload
            + bytes([SYSEX_END])
        )

    def __repr__(self) -> str:
        kind = "all-clear" if self.is_all_clear else f"type 0x{self.type_id:02x}"
        return f"ControlMessage({kind}, {len(self.payload)} payload bytes)"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, ControlMessage)
            and other.type_id == self.type_id
            and other.payload == self.payload
        )


class Bank:
    """An ordered collection of SidStation SysEx messages.

    A bank read from disk preserves every message in order — including control
    messages such as the leading all-clear — so :meth:`to_bytes` reproduces the
    original file exactly.  Iterating a bank, ``len(bank)`` and indexing all
    operate on the *patches*; use :attr:`messages` for the full message list.
    """

    __slots__ = ("messages", "prefix")

    def __init__(
        self, messages: Sequence[Message] | None = None, prefix: bytes = DEFAULT_PREFIX
    ) -> None:
        self.messages: list[Message] = list(messages) if messages else []
        self.prefix = bytes(prefix)

    # -- Construction --------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes, prefixes: Sequence[bytes] = KNOWN_PREFIXES) -> Bank:
        """Parse a whole ``.syx`` byte string into a :class:`Bank`."""
        raw_messages = split_sysex(data)
        if not raw_messages:
            return cls([], DEFAULT_PREFIX)
        prefix = detect_prefix(raw_messages[0], prefixes)
        messages: list[Message] = []
        for raw in raw_messages:
            if raw[1 : 1 + len(prefix)] != prefix:
                raise SysExParseError("inconsistent manufacturer prefix within the bank")
            type_id = raw[1 + len(prefix)]
            if type_id == MSG_PATCH_DUMP:
                messages.append(Patch.from_sysex(raw, prefix))
            else:
                messages.append(ControlMessage.from_sysex(raw, prefix))
        return cls(messages, prefix)

    @classmethod
    def read(cls, path: str | os.PathLike[str]) -> Bank:
        """Read and parse a ``.syx`` file from *path*."""
        with open(path, "rb") as fh:
            return cls.from_bytes(fh.read())

    @classmethod
    def from_patches(
        cls,
        patches: Sequence[Patch],
        prefix: bytes = DEFAULT_PREFIX,
        all_clear: bool = True,
    ) -> Bank:
        """Build a bank from *patches*, optionally led by an all-clear message."""
        messages: list[Message] = []
        if all_clear:
            messages.append(ControlMessage.all_clear())
        messages.extend(patches)
        return cls(messages, prefix)

    # -- Serialisation -------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise every message back to a single ``.syx`` byte string."""
        return b"".join(m.to_sysex(self.prefix) for m in self.messages)

    def write(self, path: str | os.PathLike[str]) -> None:
        """Write the bank to *path*."""
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())

    # -- Patch access --------------------------------------------------------

    @property
    def patches(self) -> list[Patch]:
        """The patch messages, in order (live :class:`Patch` instances)."""
        return [m for m in self.messages if isinstance(m, Patch)]

    def add_patch(self, patch: Patch) -> Patch:
        """Append *patch* to the bank and return it."""
        self.messages.append(patch)
        return patch

    # -- Sequence-like over patches ------------------------------------------

    def __len__(self) -> int:
        return len(self.patches)

    def __iter__(self) -> Iterator[Patch]:
        return iter(self.patches)

    def __getitem__(self, index: int) -> Patch:
        return self.patches[index]

    def __repr__(self) -> str:
        return (
            f"Bank({len(self.patches)} patches, {len(self.messages)} messages, "
            f"prefix={self.prefix.hex(' ')})"
        )
