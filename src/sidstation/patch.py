"""The :class:`Patch` object and its structured parameter views.

A :class:`Patch` owns a single ``bytearray`` of *decoded* patch data, indexed
exactly as the owner's manual documents it (byte 0 is the first character of the
name, byte 10 is direct controller 1, and so on).  Every named parameter is a
thin descriptor that reads and writes that one buffer, so the buffer is always
the single source of truth and structured edits can never drift out of sync
with what gets written back to disk.

The parameter bytes (everything after the 10-byte name) hold the synth's
**true working values**: :meth:`Patch.from_sysex` undoes the nibble-swap the
SidStation applies to stored parameters (see
:func:`~sidstation._codec.swap_nibbles`) and :meth:`Patch.to_sysex` re-applies
it, so files still round-trip byte-for-byte while in-memory values match what
the synth actually uses (waveform in the high nibble, ADSR as 0..15, a signed
detune, and so on).
"""

from __future__ import annotations

from ._codec import (
    MSG_PATCH_DUMP,
    NAME_LEN,
    PAD_BYTE,
    PATCH_DATA_MARKER,
    PATCH_PAD_LEN,
    SYSEX_END,
    SYSEX_START,
    decode_nibbles,
    detect_prefix,
    encode_nibbles,
    swap_nibbles,
)
from .exceptions import SidStationError, SysExParseError

# Layout constants (decoded-byte indices, straight from the manual) ----------

OSC1_OFFSET = 36
OSC2_OFFSET = 57
OSC3_OFFSET = 78
OSC_BLOCK_SIZE = 21

LFO1_OFFSET = 99
LFO_BLOCK_SIZE = 11

DCTRL_OFFSET = 10
DCTRL_BLOCK_SIZE = 3

TABLE_OFFSET = 143
TABLE_COUNT = 3
TABLE_MAX_STEPS = 32
TABLE_END = 0xFF
TABLE_LOOP = 0xFE


# -- Field descriptors -------------------------------------------------------
#
# Each field-bearing object (Patch and the Oscillator/Lfo/DirectController
# views) exposes ``_buf()`` returning ``(bytearray, base)``.  The descriptors
# below read/write a single byte (or sub-field) relative to that base, which
# lets the very same descriptor type serve both absolute patch offsets and
# block-relative oscillator/LFO offsets.


class Byte:
    """A whole byte at ``base + offset``."""

    __slots__ = ("offset",)

    def __init__(self, offset: int) -> None:
        self.offset = offset

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        buf, base = obj._buf()
        return buf[base + self.offset]

    def __set__(self, obj, value: int) -> None:
        buf, base = obj._buf()
        buf[base + self.offset] = int(value) & 0xFF


class Bits:
    """An unsigned sub-field of ``width`` bits at ``shift`` within a byte."""

    __slots__ = ("offset", "shift", "width", "_mask")

    def __init__(self, offset: int, shift: int, width: int) -> None:
        self.offset = offset
        self.shift = shift
        self.width = width
        self._mask = (1 << width) - 1

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        buf, base = obj._buf()
        return (buf[base + self.offset] >> self.shift) & self._mask

    def __set__(self, obj, value: int) -> None:
        buf, base = obj._buf()
        i = base + self.offset
        clear = ~(self._mask << self.shift) & 0xFF
        buf[i] = (buf[i] & clear) | ((int(value) & self._mask) << self.shift)


class Flag:
    """A single bit exposed as a ``bool``."""

    __slots__ = ("offset", "bit")

    def __init__(self, offset: int, bit: int) -> None:
        self.offset = offset
        self.bit = bit

    def __get__(self, obj, owner=None):
        if obj is None:
            return self
        buf, base = obj._buf()
        return bool((buf[base + self.offset] >> self.bit) & 1)

    def __set__(self, obj, value: bool) -> None:
        buf, base = obj._buf()
        i = base + self.offset
        if value:
            buf[i] |= 1 << self.bit
        else:
            buf[i] &= ~(1 << self.bit) & 0xFF


# -- Block views -------------------------------------------------------------


class Oscillator:
    """A view over one 21-byte oscillator block within a :class:`Patch`.

    Byte values are exposed directly and are the synth's true working values
    -- :class:`Patch` has already undone the parameter nibble-swap (see
    :func:`~sidstation._codec.swap_nibbles`), so ``attack`` is 0..15, ``detune``
    is a signed fine-tune, the OSC_WAVE byte is laid out exactly like a SID
    control register, and so on.  ``waveform``/``ring_mod``/``sync`` decode that
    OSC_WAVE byte and ``sync_pwm``/``gate`` decode OSC_FLAGS.
    """

    __slots__ = ("_patch", "_base")

    flags = Byte(0)
    sync_pwm = Flag(0, 0)
    gate = Flag(0, 1)
    track = Byte(1)
    arp_speed = Byte(2)
    transpose = Byte(3)
    detune = Byte(4)
    pitchbend_range = Byte(5)
    attack = Byte(6)
    decay = Byte(7)
    sustain = Byte(8)
    release = Byte(9)
    delay = Byte(10)
    pwm_start = Byte(11)
    pwm_add = Byte(12)
    pwm_lfo = Byte(13)
    pwm_lfo_depth = Byte(14)
    wave = Byte(15)
    # OSC_WAVE is a SID control register: waveform in the HIGH nibble, then
    # test/ring/sync/gate.  (In the on-wire/stored patch the nibbles are swapped,
    # which is why the manual shows waveform in the low nibble.)
    waveform = Bits(15, 4, 4)  # high nibble: 1=Tri 2=Saw 4=Pulse 5=Mixed 8=Noise
    test = Flag(15, 3)
    ring_mod = Flag(15, 2)
    sync = Flag(15, 1)
    portamento = Byte(16)
    vibrato_lfo = Byte(17)
    vibrato_depth = Byte(18)
    vibrato_wheel_depth = Byte(19)
    table_speed = Byte(20)

    def __init__(self, patch: Patch, base: int) -> None:
        self._patch = patch
        self._base = base

    def _buf(self) -> tuple[bytearray, int]:
        return self._patch._data, self._base

    @property
    def transpose_semitones(self) -> int:
        """``OSC_TRANSPOSE`` (x+3) as a signed semitone offset.

        :class:`Patch` has already undone the parameter nibble-swap, so the
        working byte is a plain signed 8-bit semitone count -- **measured
        empirically** (play a note, read the SID frequency)::

            0x00 -> 0   0x04 -> +4   0x07 -> +7   0x0C -> +12   0xF4 -> -12

        i.e. the factory bank's transposes are musical octaves/intervals.
        """
        t = self.transpose
        return t - 256 if t >= 0x80 else t

    @property
    def detune_value(self) -> int:
        """``OSC_DETUNE`` (x+4) as a signed fine-tune amount, -128..127.

        After the nibble-swap the working byte is a plain signed value in units
        of **1/64 semitone** (~1.56 cents): the synth computes the pitch as
        ``(detune + 6) * 4`` log-pitch units (256 = one semitone), measured by
        playing notes and reading the SID frequency.  So the editable range
        -64..+63 spans about **+-1 semitone** (not the +-1/2 semitone the manual
        implies); ``detune_cents`` gives the offset in cents.
        """
        d = self.detune
        return d - 256 if d >= 0x80 else d

    @property
    def detune_cents(self) -> float:
        """:attr:`detune_value` expressed in cents (1 step = 1/64 semitone)."""
        return self.detune_value * (100.0 / 64.0)

    def __repr__(self) -> str:
        return (
            f"Oscillator(waveform={self.waveform}, attack={self.attack}, "
            f"decay={self.decay}, sustain={self.sustain}, release={self.release})"
        )


class Lfo:
    """A view over one 11-byte LFO block within a :class:`Patch`."""

    __slots__ = ("_patch", "_base")

    ctrl_type = Byte(0)
    lfo_type = Bits(0, 0, 3)
    ctrl_source = Bits(0, 4, 4)
    options = Byte(1)
    sync = Flag(1, 0)
    invert = Flag(1, 1)
    above_zero = Flag(1, 2)
    sync_note_off = Flag(1, 3)
    ctrl_dest = Bits(1, 4, 3)
    speed = Byte(2)
    sample_hold = Byte(3)
    depth = Byte(4)
    add_lfo = Byte(5)
    lace = Byte(6)
    lace_with = Byte(7)
    add_depth = Byte(8)
    ctrl_value = Byte(9)
    fade_in = Byte(10)

    def __init__(self, patch: Patch, base: int) -> None:
        self._patch = patch
        self._base = base

    def _buf(self) -> tuple[bytearray, int]:
        return self._patch._data, self._base

    def __repr__(self) -> str:
        return f"Lfo(type={self.lfo_type}, speed={self.speed}, depth={self.depth})"


class DirectController:
    """A view over a direct-controller triple (value, limit down, limit up)."""

    __slots__ = ("_patch", "_base")

    value = Byte(0)
    limit_down = Byte(1)
    limit_up = Byte(2)

    def __init__(self, patch: Patch, base: int) -> None:
        self._patch = patch
        self._base = base

    def _buf(self) -> tuple[bytearray, int]:
        return self._patch._data, self._base

    def __repr__(self) -> str:
        return (
            f"DirectController(value={self.value}, "
            f"limit_down={self.limit_down}, limit_up={self.limit_up})"
        )


# -- Tables ------------------------------------------------------------------


class TableStep:
    """A single waveform/note step in an oscillator table."""

    __slots__ = ("data1", "data2")

    def __init__(self, data1: int, data2: int) -> None:
        self.data1 = data1 & 0xFF
        self.data2 = data2 & 0xFF

    @property
    def waveform(self) -> int:
        """Waveform code (high nibble of DATA1).

        After :class:`Patch` undoes the parameter nibble-swap, DATA1 is laid out
        like a SID control register (waveform in the high nibble, then
        test/ring/sync), matching :class:`Oscillator`.
        """
        return self.data1 >> 4

    @property
    def ring_mod(self) -> bool:
        return bool(self.data1 & 0x04)

    @property
    def sync(self) -> bool:
        return bool(self.data1 & 0x02)

    @property
    def note_mode(self) -> str:
        """``"fixed"``, ``"add"`` or ``"subtract"`` (decoded from DATA2).

        DATA2 here is the working byte (:class:`Patch` has undone the
        parameter nibble-swap).  It reads as "bit 7 set = relative, bit 6 =
        sign, **set = +** / clear = -", confirmed by playing tables (working
        ``0x8C`` -> base-12, ``0xC3`` -> base+3)::

            0x00..0x7F -> fixed (absolute note)
            0x80..0xBF -> subtract (bit 6 clear = -) from the base note
            0xC0..0xFF -> add (bit 6 set = +) to the base note
        """
        if self.data2 < 0x80:
            return "fixed"
        if self.data2 < 0xC0:
            return "subtract"
        return "add"

    @property
    def note_value(self) -> int:
        """The note number (fixed) or the offset amount (add/subtract)."""
        if self.data2 < 0x80:
            return self.data2
        return self.data2 & 0x3F

    def __repr__(self) -> str:
        return (
            f"TableStep(waveform={self.waveform}, "
            f"note_mode={self.note_mode!r}, note_value={self.note_value})"
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, TableStep) and other.data1 == self.data1 and other.data2 == self.data2
        )


class Table:
    """One oscillator table: a list of steps plus how it terminates.

    ``terminator`` is ``"end"`` (FF), ``"loop"`` (FE + ``loop_point``) or
    ``"none"`` when the table fills all 32 steps without an explicit terminator.
    """

    __slots__ = ("steps", "terminator", "loop_point")

    def __init__(
        self,
        steps: list[TableStep] | None = None,
        terminator: str = "end",
        loop_point: int = 0,
    ) -> None:
        self.steps = list(steps) if steps else []
        self.terminator = terminator
        self.loop_point = loop_point & 0xFF

    def to_bytes(self) -> bytes:
        out = bytearray()
        for step in self.steps:
            out.append(step.data1)
            out.append(step.data2)
        if self.terminator == "end":
            out.append(TABLE_END)
        elif self.terminator == "loop":
            out.append(TABLE_LOOP)
            out.append(self.loop_point)
        return bytes(out)

    def __repr__(self) -> str:
        return f"Table({len(self.steps)} steps, terminator={self.terminator!r})"


def parse_tables(data: bytes, start: int = TABLE_OFFSET, count: int = TABLE_COUNT) -> list[Table]:
    """Parse *count* oscillator tables out of *data* starting at *start*."""
    pos = start
    tables: list[Table] = []
    n = len(data)
    for _ in range(count):
        steps: list[TableStep] = []
        terminator = "none"
        loop_point = 0
        while True:
            if len(steps) >= TABLE_MAX_STEPS or pos >= n:
                break
            d1 = data[pos]
            if d1 == TABLE_END:
                pos += 1
                terminator = "end"
                break
            if d1 == TABLE_LOOP:
                loop_point = data[pos + 1] if pos + 1 < n else 0
                pos += 2
                terminator = "loop"
                break
            d2 = data[pos + 1] if pos + 1 < n else 0
            steps.append(TableStep(d1, d2))
            pos += 2
        tables.append(Table(steps, terminator, loop_point))
    return tables


def serialize_tables(tables: list[Table]) -> bytes:
    """Concatenate the byte encodings of *tables*."""
    return b"".join(table.to_bytes() for table in tables)


# -- Patch -------------------------------------------------------------------


class Patch:
    """A single SidStation preset.

    The decoded patch bytes live in :attr:`data`; every named attribute is a
    view onto it.  ``version`` and ``declared_size`` reproduce the patch-dump
    header fields verbatim so a parsed patch round-trips byte-for-byte (the
    hardware's size field is frequently wrong, so it is preserved rather than
    recomputed; set it to ``None`` to derive the size from ``data`` on write).
    """

    # Mode byte (index 22)
    mode = Byte(22)
    osc1_enabled = Flag(22, 0)
    osc2_enabled = Flag(22, 1)
    osc3_enabled = Flag(22, 2)
    poly = Flag(22, 3)
    legato = Flag(22, 5)
    filter_wrap = Flag(22, 6)
    filter_env_invert = Flag(22, 7)
    mode2 = Byte(23)

    # Filter routing / resonance (index 24)
    filter_routing = Byte(24)
    filter_osc1 = Flag(24, 0)
    filter_osc2 = Flag(24, 1)
    filter_osc3 = Flag(24, 2)
    resonance = Bits(24, 4, 4)

    # Filter type / LFO selector (index 25)
    filter_type_lfo = Byte(25)
    filter_type = Bits(25, 0, 3)
    filter_lfo = Bits(25, 4, 2)

    # Filter envelope and modulation (indices 26-33)
    filter_cutoff = Byte(26)
    filter_env_depth = Byte(27)
    filter_env_attack = Byte(28)
    filter_env_decay = Byte(29)
    filter_env_sustain = Byte(30)
    filter_env_release = Byte(31)
    filter_lfo_depth = Byte(32)
    filter_lfo_wheel_depth = Byte(33)

    # Per-patch sync (indices 34-35)
    sync_speed = Byte(34)
    sync_hcut = Byte(35)

    __slots__ = (
        "_data",
        "version",
        "declared_size",
        "_oscillators",
        "_lfos",
        "_direct_controllers",
    )

    def __init__(
        self,
        data: bytes | None = None,
        *,
        version: int = 0,
        declared_size: int | None = None,
        name: str | None = None,
    ) -> None:
        if data is None:
            # Minimal valid patch: 10-byte name, 133 zero parameter bytes,
            # three empty tables (each a single TABLE_END byte).
            data = b" " * NAME_LEN + b"\x00" * 133 + bytes([TABLE_END]) * TABLE_COUNT
        self._data = bytearray(data)
        self.version = version
        self.declared_size = declared_size
        self._oscillators = (
            Oscillator(self, OSC1_OFFSET),
            Oscillator(self, OSC2_OFFSET),
            Oscillator(self, OSC3_OFFSET),
        )
        self._lfos = tuple(Lfo(self, LFO1_OFFSET + i * LFO_BLOCK_SIZE) for i in range(4))
        self._direct_controllers = tuple(
            DirectController(self, DCTRL_OFFSET + i * DCTRL_BLOCK_SIZE) for i in range(4)
        )
        if name is not None:
            self.name = name

    def _buf(self) -> tuple[bytearray, int]:
        return self._data, 0

    # -- Name ----------------------------------------------------------------

    @property
    def name(self) -> str:
        """The patch name with trailing space padding stripped."""
        return self._data[0:NAME_LEN].decode("latin-1").rstrip(" ")

    @name.setter
    def name(self, value: str) -> None:
        raw = value.encode("latin-1", "replace")[:NAME_LEN].ljust(NAME_LEN, b" ")
        self._data[0:NAME_LEN] = raw

    @property
    def name_bytes(self) -> bytes:
        """The raw 10 name bytes, padding and all."""
        return bytes(self._data[0:NAME_LEN])

    @name_bytes.setter
    def name_bytes(self, value: bytes) -> None:
        raw = bytes(value)[:NAME_LEN].ljust(NAME_LEN, b" ")
        self._data[0:NAME_LEN] = raw

    # -- Blocks --------------------------------------------------------------

    @property
    def data(self) -> bytearray:
        """The mutable, decoded patch bytes (the source of truth)."""
        return self._data

    @property
    def oscillators(self) -> tuple[Oscillator, Oscillator, Oscillator]:
        return self._oscillators

    @property
    def lfos(self) -> tuple[Lfo, Lfo, Lfo, Lfo]:
        return self._lfos

    @property
    def direct_controllers(self) -> tuple[DirectController, ...]:
        return self._direct_controllers

    @property
    def tables(self) -> list[Table]:
        """Parse and return the three oscillator tables (a snapshot copy)."""
        return parse_tables(self._data, TABLE_OFFSET, TABLE_COUNT)

    def replace_tables(self, tables: list[Table]) -> None:
        """Overwrite the table region with *tables* (a list of three)."""
        self._data[TABLE_OFFSET:] = serialize_tables(tables)

    # -- Serialisation -------------------------------------------------------

    @classmethod
    def from_sysex(cls, message: bytes, prefix: bytes | None = None) -> Patch:
        """Build a :class:`Patch` from one complete patch-dump SysEx message."""
        if not message or message[0] != SYSEX_START or message[-1] != SYSEX_END:
            raise SysExParseError("message is not framed by 0xF0 ... 0xF7")
        if prefix is None:
            prefix = detect_prefix(message)
        o = 1 + len(prefix)
        if message[o] != MSG_PATCH_DUMP:
            raise SysExParseError(f"not a patch-dump message (type id 0x{message[o]:02x})")
        o += 1
        version = message[o]
        o += 1
        size = (message[o] << 7) | message[o + 1]
        o += 2
        pad = message[o : o + PATCH_PAD_LEN]
        o += PATCH_PAD_LEN
        if any(b != PAD_BYTE for b in pad):
            raise SysExParseError("patch-dump padding is not all 0x2d")
        if message[o] != PATCH_DATA_MARKER:
            raise SysExParseError("missing patch-data marker (0x45)")
        o += 1
        body = message[o:-1]
        name = bytes(body[:NAME_LEN])
        # The parameter region is nibble-packed *and* stored nibble-swapped
        # relative to the synth's working values; undo both so ``data`` holds
        # the true values (see :func:`swap_nibbles`).
        decoded = swap_nibbles(decode_nibbles(body[NAME_LEN:]))
        data = bytearray(name) + decoded
        return cls(data, version=version, declared_size=size)

    def to_sysex(self, prefix: bytes = None) -> bytes:
        """Encode this patch as a complete patch-dump SysEx message."""
        from ._codec import DEFAULT_PREFIX

        if prefix is None:
            prefix = DEFAULT_PREFIX
        if len(self._data) < NAME_LEN:
            raise SidStationError("patch data is shorter than the 10-byte name")
        size = self.declared_size if self.declared_size is not None else len(self._data)
        if not 0 <= size < (1 << 14):
            raise SidStationError(f"declared_size {size} does not fit in 14 bits")
        out = bytearray([SYSEX_START])
        out += prefix
        out += bytes([MSG_PATCH_DUMP, self.version & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
        out += bytes([PAD_BYTE]) * PATCH_PAD_LEN
        out.append(PATCH_DATA_MARKER)
        out += self._data[:NAME_LEN]
        # Re-apply the parameter nibble-swap (its own inverse) before packing, so
        # the on-wire bytes match what the hardware stores.
        out += encode_nibbles(swap_nibbles(self._data[NAME_LEN:]))
        out.append(SYSEX_END)
        return bytes(out)

    # -- Dunder --------------------------------------------------------------

    def __repr__(self) -> str:
        return f"Patch(name={self.name!r}, version={self.version}, {len(self._data)} bytes)"

    def _effective_size(self) -> int:
        """The size value that will be written: the declared one or the length."""
        return self.declared_size if self.declared_size is not None else len(self._data)

    def __eq__(self, other: object) -> bool:
        # Two patches are equal when they serialise to the same bytes, so a
        # freshly built patch (declared_size=None) equals the patch you get back
        # after a write/read cycle (declared_size filled in from the header).
        return (
            isinstance(other, Patch)
            and other._data == self._data
            and other.version == self.version
            and other._effective_size() == self._effective_size()
        )

    def copy(self) -> Patch:
        """Return an independent copy of this patch."""
        return Patch(
            bytes(self._data),
            version=self.version,
            declared_size=self.declared_size,
        )
