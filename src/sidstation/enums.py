"""Enumerations for SidStation patch parameters.

These mirror the value tables in the owner's manual.  They are convenience
aliases only: the underlying patch bytes are never validated against them, so a
patch can legitimately hold a value with no matching enum member.
"""

from __future__ import annotations

import enum


class Waveform(enum.IntEnum):
    """Oscillator / table waveform codes (the high nibble of a working OSC_WAVE
    byte -- the low nibble of the *stored* byte, before the parameter
    nibble-swap is undone)."""

    OFF = 0
    TRIANGLE = 1
    SAW = 2
    PULSE = 4
    MIXED = 5
    NOISE = 8


class LfoType(enum.IntEnum):
    """LFO waveform codes (low 3 bits of an LFO CTRL_TYPE byte).

    ``ctrl_type & 7`` selects the shape and ``5`` is the **Flat**
    (steady-maximum DC) source -- *not* ``7`` as the owner's manual's
    patch-data table claims.  The manual's Shape menu lists exactly six shapes
    (Tri..Flat = 0..5); codes ``6`` and ``7`` are no-ops (the LFO output is
    frozen).  Six factory presets use type ``5`` and none use ``6``/``7``,
    confirming this from real data.
    """

    TRIANGLE = 0
    SAW = 1
    RAMP = 2
    PULSE = 3  # the manual's LFO Shape menu calls this "Square"
    RANDOM = 4
    FLAT = 5


class LfoCtrlSource(enum.IntEnum):
    """What modulates an LFO (high nibble of an LFO CTRL_TYPE byte)."""

    MOD_WHEEL = 0
    PITCH_BEND = 1
    VELOCITY = 2
    AFTERTOUCH = 3
    CTRL1 = 4
    CTRL2 = 5
    CTRL3 = 6
    CTRL4 = 7
    LFO1 = 8
    LFO2 = 9
    LFO3 = 10
    LFO4 = 11


class LfoCtrlDest(enum.IntEnum):
    """Destination an LFO controller affects (LFO OPTIONS bits 4-6)."""

    NONE = 0
    DEPTH = 1
    SPEED = 2
    SAMPLE_HOLD = 3
    LACE = 4
