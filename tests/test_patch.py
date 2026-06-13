"""Tests for structured patch access."""

from __future__ import annotations

import sidstation
from sidstation import Patch, Waveform


def test_known_patch_name_and_version(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    first = bank.patches[0]
    assert first.name == "Anpanman"
    assert first.version == 0
    assert first.name_bytes == b"Anpanman  "


def test_all_patch_names(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    names = [p.name for p in bank.patches]
    assert "Krutong" in names
    assert "Snare Std" in names
    assert len(names) == 90


def test_name_setter_pads_and_truncates():
    patch = Patch()
    patch.name = "Bass"
    assert patch.name == "Bass"
    assert patch.name_bytes == b"Bass      "
    patch.name = "ThisNameIsWayTooLong"
    assert patch.name_bytes == b"ThisNameIs"
    assert patch.name == "ThisNameIs"


def test_oscillator_waveform_is_high_nibble(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    osc = bank.patches[0].oscillators[0]
    # Anpanman OSC1 is a pulse wave.  In the working buffer (parameter
    # nibble-swap undone) OSC_WAVE is a SID control register, so the waveform
    # code lives in the HIGH nibble.
    assert osc.waveform == Waveform.PULSE
    assert osc.wave >> 4 == osc.waveform


def test_oscillator_waveform_setter_preserves_flag_bits():
    patch = Patch()
    osc = patch.oscillators[0]
    osc.ring_mod = True
    osc.sync = True
    osc.waveform = Waveform.SAW
    assert osc.waveform == Waveform.SAW
    assert osc.ring_mod is True
    assert osc.sync is True
    # SID control-register layout: waveform 2 in the high nibble | ring (bit2)
    # | sync (bit1) == 0x20 | 0x04 | 0x02
    assert osc.wave == 0x26


def test_flag_setters_preserve_other_bits():
    patch = Patch()
    patch.data[22] = 0b1010_1010
    patch.osc1_enabled = True  # set bit 0
    assert patch.data[22] == 0b1010_1011
    patch.filter_env_invert = False  # clear bit 7
    assert patch.data[22] == 0b0010_1011


def test_oscillator_and_lfo_block_offsets(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    patch = bank.patches[0]
    # Editing through the view writes into the patch buffer at the right index.
    patch.oscillators[1].attack = 9
    assert patch.data[57 + 6] == 9
    patch.lfos[2].speed = 42
    assert patch.data[99 + 11 * 2 + 2] == 42


def test_direct_controllers(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    dctrls = bank.patches[0].direct_controllers
    assert len(dctrls) == 4
    dctrls[0].value = 5
    dctrls[0].limit_up = 100
    assert bank.patches[0].data[10] == 5
    assert bank.patches[0].data[12] == 100


def test_tables_parse_and_serialize_roundtrip(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    for patch in bank.patches:
        tables = patch.tables
        assert len(tables) == 3
        region = bytes(patch.data[143:])
        assert sidstation.serialize_tables(tables) == region


def test_table_steps_of_known_patch(presets_bytes):
    bank = sidstation.loads(presets_bytes)
    anpanman = bank.patches[0]
    table1 = anpanman.tables[0]
    assert len(table1.steps) == 6
    assert table1.terminator == "end"
    assert table1.steps[0].waveform == Waveform.PULSE
    # second and third tables are empty
    assert anpanman.tables[1].steps == []
    assert anpanman.tables[2].steps == []


def test_table_step_note_sign_convention():
    # bit7 set = relative, bit6 = sign (SET = +, clear = -).
    # DATA2 is the working byte (nibble-swap undone); measured empirically
    # (working 0x8C -> base-12, 0xC3 -> base+3).
    step = sidstation.TableStep
    assert step(0x40, 0x3C).note_mode == "fixed"  # 0x00..0x7F absolute note
    assert step(0x40, 0x3C).note_value == 0x3C
    assert step(0x40, 0x8C).note_mode == "subtract"  # 0x80..0xBF -> -(v & 0x3F)
    assert step(0x40, 0x8C).note_value == 12
    assert step(0x40, 0xCC).note_mode == "add"  # 0xC0..0xFF -> +(v & 0x3F)
    assert step(0x40, 0xCC).note_value == 12


def test_oscillator_transpose_semitones():
    # The working buffer is already un-swapped, so transpose is a plain signed
    # semitone count (measured empirically; see patch.py).
    osc = Patch().oscillators[0]
    for raw, want in [
        (0x00, 0),
        (0x04, 4),
        (0x07, 7),
        (0x08, 8),
        (0x0C, 12),
        (0xF4, -12),
        (0xE8, -24),
        (0x18, 24),
    ]:
        osc.transpose = raw
        assert osc.transpose_semitones == want, hex(raw)


def test_oscillator_detune_value_and_cents():
    # Detune is a plain signed value (working buffer un-swapped) in 1/64-semitone
    # units; measured empirically via SID frequency. The editable -64..+63 spans
    # about +-1 semitone, NOT the +-1/2 the manual implies.
    osc = Patch().oscillators[0]
    for raw, value in [(0x00, 0), (0x10, 16), (0x3F, 63), (0xFF, -1), (0xC0, -64)]:
        osc.detune = raw
        assert osc.detune_value == value, hex(raw)
    osc.detune = 0x40  # +64 steps
    assert abs(osc.detune_cents - 100.0) < 0.01  # ~= +1 semitone


def test_lfo_type_flat_is_five(presets_bytes):
    # Type 5 is Flat (steady-max DC); 6/7 are no-ops.
    # The factory bank uses types 0..5 only -- with several
    # genuine Flat (5) LFOs and none at 6/7.
    from sidstation import LfoType

    assert LfoType.FLAT == 5
    bank = sidstation.loads(presets_bytes)
    seen = {lfo.lfo_type for patch in bank.patches for lfo in patch.lfos}
    assert seen <= {0, 1, 2, 3, 4, 5}
    assert 5 in seen  # at least one real Flat LFO
    assert not (seen & {6, 7})


def test_factory_bank_loop_tables_parse(presets_bytes):
    # A table loop terminator is 0xFE. It is nibble-swapped to 0xEF on the wire;
    # before the swap was undone these tables were mis-parsed. Several factory
    # patches use loops, so they must now show up as "loop"-terminated tables.
    bank = sidstation.loads(presets_bytes)
    loops = sum(1 for patch in bank.patches for table in patch.tables if table.terminator == "loop")
    assert loops >= 5


def test_replace_tables_changes_data():
    patch = Patch()
    tables = patch.tables
    tables[0].steps.append(sidstation.TableStep(0x04, 0x10))
    patch.replace_tables(tables)
    assert patch.data[143] == 0x04
    assert patch.data[144] == 0x10
    assert patch.data[145] == 0xFF  # table 1 terminator
    # round-trips through the table codec
    assert patch.tables[0].steps[0].data1 == 0x04


def test_default_patch_is_valid():
    patch = Patch(name="Init")
    assert patch.name == "Init"
    assert len(patch.data) == 146
    raw = patch.to_sysex()
    rebuilt = Patch.from_sysex(raw)
    assert rebuilt.name == "Init"
    assert rebuilt == patch


def test_patch_copy_is_independent():
    patch = Patch(name="Orig")
    clone = patch.copy()
    clone.name = "Clone"
    assert patch.name == "Orig"
    assert clone.name == "Clone"
