"""Shared pytest fixtures."""

from __future__ import annotations

import pathlib

import pytest

DATA_DIR = pathlib.Path(__file__).parent / "data"
PRESETS_FILE = DATA_DIR / "SidStation_Presets_r1.syx"


@pytest.fixture(scope="session")
def presets_path() -> pathlib.Path:
    """Path to the bundled real-world preset bank."""
    return PRESETS_FILE


@pytest.fixture(scope="session")
def presets_bytes() -> bytes:
    """Raw bytes of the bundled preset bank."""
    return PRESETS_FILE.read_bytes()
