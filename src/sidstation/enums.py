"""Enumerations for SidStation patch parameters.

These mirror the value tables in the owner's manual.  They are convenience
aliases only: the underlying patch bytes are never validated against them, so a
patch can legitimately hold a value with no matching enum member.
"""

from __future__ import annotations

import enum


class Waveform(enum.IntEnum):
    """Oscillator / table waveform codes (the low nibble of an OSC_WAVE byte)."""

    OFF = 0
    TRIANGLE = 1
    SAW = 2
    PULSE = 4
    MIXED = 5
    NOISE = 8


class LfoType(enum.IntEnum):
    """LFO waveform codes (low 3 bits of an LFO CTRL_TYPE byte)."""

    TRIANGLE = 0
    SAW = 1
    RAMP = 2
    PULSE = 3
    RANDOM = 4
    FLAT = 7


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
