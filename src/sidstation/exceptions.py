"""Exception types for :mod:`sidstation`."""

from __future__ import annotations


class SidStationError(Exception):
    """Base class for all errors raised by :mod:`sidstation`."""


class SysExParseError(SidStationError):
    """Raised when a byte stream is not valid SidStation SysEx data."""
