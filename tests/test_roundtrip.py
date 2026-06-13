"""The headline guarantee: parsing then re-serialising changes nothing."""

from __future__ import annotations

import sidstation
from sidstation import _codec
from sidstation.patch import Patch


def test_bank_roundtrip_is_byte_exact(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    assert bank.to_bytes() == presets_bytes


def test_every_message_roundtrips_individually(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    raw_messages = _codec.split_sysex(presets_bytes)
    assert len(raw_messages) == len(bank.messages)
    for raw, message in zip(raw_messages, bank.messages):
        assert message.to_sysex(bank.prefix) == raw


def test_read_write_read_via_disk(tmp_path, presets_path, presets_bytes):
    bank = sidstation.read(presets_path)
    out = tmp_path / "out.syx"
    bank.write(out)
    assert out.read_bytes() == presets_bytes
    again = sidstation.read(out)
    assert again.to_bytes() == presets_bytes


def test_declared_size_is_preserved_when_unreliable(presets_bytes):
    # "Body drive" is one of the patches whose stored size field (307) does not
    # match its actual decoded length (177).  It must survive untouched.
    bank = sidstation.loads(presets_bytes)
    body_drive = next(p for p in bank.patches if p.name == "Body drive")
    assert body_drive.declared_size == 307
    assert len(body_drive.data) == 177
    assert body_drive.to_sysex(bank.prefix) in presets_bytes


def test_patch_to_from_sysex_roundtrip(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    for patch in bank.patches:
        raw = patch.to_sysex(bank.prefix)
        rebuilt = Patch.from_sysex(raw)
        assert rebuilt == patch
