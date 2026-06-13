"""Tests for the Bank container and control messages."""

from __future__ import annotations

import sidstation
from sidstation import Bank, ControlMessage, Patch
from sidstation._codec import DEFAULT_PREFIX, ELEKTRON_HARDWARE_PREFIX


def test_bank_structure(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    assert len(bank) == 90  # len() counts patches
    assert len(bank.messages) == 91  # ... plus the leading all-clear
    assert bank.prefix == DEFAULT_PREFIX


def test_leading_message_is_all_clear(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    first = bank.messages[0]
    assert isinstance(first, ControlMessage)
    assert first.is_all_clear


def test_bank_iteration_and_indexing(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    assert bank[0].name == "Anpanman"
    names = [p.name for p in bank]
    assert names[0] == "Anpanman"
    assert len(names) == 90


def test_from_patches_adds_all_clear():
    patches = [Patch(name="A"), Patch(name="B")]
    bank = Bank.from_patches(patches)
    assert len(bank.messages) == 3
    assert isinstance(bank.messages[0], ControlMessage)
    assert bank.messages[0].is_all_clear
    assert [p.name for p in bank.patches] == ["A", "B"]


def test_from_patches_without_all_clear():
    bank = Bank.from_patches([Patch(name="Solo")], all_clear=False)
    assert len(bank.messages) == 1
    assert bank.patches[0].name == "Solo"


def test_add_patch():
    bank = Bank.from_patches([], all_clear=False)
    bank.add_patch(Patch(name="New"))
    assert len(bank) == 1
    assert bank[0].name == "New"


def test_build_write_read_roundtrip(tmp_path):
    bank = Bank.from_patches([Patch(name="One"), Patch(name="Two")])
    out = tmp_path / "built.syx"
    bank.write(out)
    reread = sidstation.read(out)
    assert [p.name for p in reread.patches] == ["One", "Two"]
    assert reread.messages[0].is_all_clear


def test_edit_name_then_write_read(tmp_path, presets_bytes):
    bank = sidstation.loads(presets_bytes)
    bank.patches[0].name = "Renamed"
    out = tmp_path / "edited.syx"
    bank.write(out)
    reread = sidstation.read(out)
    assert reread.patches[0].name == "Renamed"
    # Every other patch is still byte-identical to the source.
    original = sidstation.loads(presets_bytes)
    for i in range(1, len(original.patches)):
        assert reread.patches[i] == original.patches[i]


def test_elektron_hardware_prefix_roundtrip():
    # Synthesise a bank that uses the 5-byte hardware prefix and confirm it is
    # detected and preserved.
    patch = Patch(name="HW")
    raw = bytes([0xF0]) + ELEKTRON_HARDWARE_PREFIX + patch.to_sysex()[1 + len(DEFAULT_PREFIX) :]
    bank = sidstation.loads(raw)
    assert bank.prefix == ELEKTRON_HARDWARE_PREFIX
    assert bank.patches[0].name == "HW"
    assert bank.to_bytes() == raw


def test_control_message_roundtrip():
    msg = ControlMessage.all_clear()
    raw = msg.to_sysex(DEFAULT_PREFIX)
    rebuilt = ControlMessage.from_sysex(raw, DEFAULT_PREFIX)
    assert rebuilt == msg
    assert rebuilt.is_all_clear


def test_empty_input_is_empty_bank():
    bank = sidstation.loads(b"")
    assert len(bank) == 0
    assert bank.to_bytes() == b""
