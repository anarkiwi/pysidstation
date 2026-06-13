"""Tests for the SID renderer.

The pure ``patch_to_sid_registers`` translation is tested directly (no
pyresidfp, no network). The actual reSIDfp render is exercised only when
pyresidfp is installed.
"""

from __future__ import annotations

import pytest

from sidstation import Patch, TableStep, Waveform
from sidstation.render import (
    NTSC_CLOCK_HZ,
    PAL_CLOCK_HZ,
    _hz_to_freg,
    _midi_to_hz,
    patch_to_sid_registers,
)


def _saw_patch():
    p = Patch(name="Test")
    p.osc1_enabled = True
    p.oscillators[0].waveform = Waveform.SAW
    p.oscillators[0].attack = 0x20  # 4-bit value lives in the high nibble -> 2
    p.oscillators[0].decay = 0x90  # -> 9
    p.oscillators[0].sustain = 0xF0  # -> 15
    p.oscillators[0].release = 0x80  # -> 8
    return p


def test_frequency_register_matches_note():
    p = _saw_patch()
    regs = patch_to_sid_registers(p, note=69, clock_hz=PAL_CLOCK_HZ)  # A4 = 440 Hz
    freg = (regs[0x01] << 8) | regs[0x00]
    assert freg == _hz_to_freg(440.0, PAL_CLOCK_HZ)
    # NTSC clock yields a different register value for the same pitch.
    regs_ntsc = patch_to_sid_registers(p, note=69, clock_hz=NTSC_CLOCK_HZ)
    assert ((regs_ntsc[0x01] << 8) | regs_ntsc[0x00]) != freg


def test_waveform_and_gate_bits():
    regs = patch_to_sid_registers(_saw_patch(), note=60, clock_hz=PAL_CLOCK_HZ)
    control = regs[0x04]
    assert control & 0x20  # sawtooth bit
    assert control & 0x01  # gated (oscillator enabled)


def test_adsr_packs_high_and_low_nibbles():
    regs = patch_to_sid_registers(_saw_patch(), note=60, clock_hz=PAL_CLOCK_HZ)
    assert regs[0x05] == 0x29  # attack 2, decay 9
    assert regs[0x06] == 0xF8  # sustain 15, release 8


def test_disabled_oscillators_are_not_gated():
    regs = patch_to_sid_registers(_saw_patch(), note=60, clock_hz=PAL_CLOCK_HZ)
    assert not regs[0x0B] & 0x01  # voice 2 control, no gate
    assert not regs[0x12] & 0x01  # voice 3 control, no gate


def test_transpose_shifts_pitch_up_an_octave():
    p = _saw_patch()
    base = patch_to_sid_registers(p, note=60, clock_hz=PAL_CLOCK_HZ)
    p.oscillators[0].transpose = 12  # +12 semitones
    up = patch_to_sid_registers(p, note=60, clock_hz=PAL_CLOCK_HZ)
    base_freg = (base[0x01] << 8) | base[0x00]
    up_freg = (up[0x01] << 8) | up[0x00]
    assert round(up_freg / base_freg) == 2  # one octave


def test_filter_bypassed_when_type_zero():
    p = _saw_patch()
    p.filter_type = 0
    p.filter_osc1 = True
    regs = patch_to_sid_registers(p, note=60, clock_hz=PAL_CLOCK_HZ)
    assert regs[0x17] & 0x0F == 0  # no routing bits
    assert regs[0x18] & 0xF0 == 0  # no filter mode bits


def test_filter_lowpass_routing_and_mode():
    p = _saw_patch()
    p.filter_type = 1  # bit0 -> low-pass
    p.filter_osc1 = True
    p.resonance = 10
    p.filter_cutoff = 100
    regs = patch_to_sid_registers(p, note=60, clock_hz=PAL_CLOCK_HZ)
    assert regs[0x17] & 0x01  # Filt1 routing
    assert regs[0x17] >> 4 == 10  # resonance in high nibble
    assert regs[0x18] & 0x10  # low-pass mode bit


def test_knobs_override_cutoff_resonance_pw_volume():
    p = _saw_patch()
    p.filter_type = 1
    regs = patch_to_sid_registers(p, note=60, clock_hz=PAL_CLOCK_HZ, knobs=(64, 64, 64, 64))
    fc11 = (regs[0x16] << 3) | regs[0x15]
    assert fc11 == 64 * 16  # knob1 -> cutoff
    assert regs[0x17] >> 4 == 8  # knob2 -> resonance (64>>3)
    pw = (regs[0x03] << 8) | regs[0x02]
    assert pw == 64 * 32  # knob3 -> pulse width
    assert regs[0x18] & 0x0F == 8  # knob4 -> volume (64>>3)


def test_table_driven_voice_uses_first_step():
    # Oscillator 2 has no static waveform but runs a table -> render step 0.
    p = Patch(name="Drum")
    p.osc2_enabled = True
    p.oscillators[1].waveform = 0
    p.oscillators[1].table_speed = 32
    tables = p.tables
    tables[1].steps.append(TableStep(0x04, 0x03))  # Pulse, fixed note 3
    p.replace_tables(tables)
    regs = patch_to_sid_registers(p, note=60, clock_hz=PAL_CLOCK_HZ)
    assert regs[0x07 + 4] & 0x40  # voice 2 pulse waveform
    assert regs[0x07 + 4] & 0x01  # gated
    # Pitched to the fixed note 3 (very low), not the played note 60.
    freg = (regs[0x08] << 8) | regs[0x07]
    assert freg == _hz_to_freg(_midi_to_hz(3), PAL_CLOCK_HZ)


def test_all_registers_present_and_in_range():
    regs = patch_to_sid_registers(_saw_patch(), note=60, clock_hz=PAL_CLOCK_HZ)
    assert set(regs) == set(range(0x19))  # 25 SID registers
    assert all(0 <= v <= 0xFF for v in regs.values())


# -- Dynamic modulation engine (pure, no pyresidfp) ---------------------------


def _engine(patch, note=60, **kw):
    from sidstation.render import PatchEngine

    return PatchEngine(patch, note, PAL_CLOCK_HZ, tick_rate=50, **kw)


def test_engine_steady_when_unmodulated():
    eng = _engine(_saw_patch(), note=69)
    fregs = {((r := eng.tick())[0x01] << 8) | r[0x00] for _ in range(12)}
    assert len(fregs) == 1  # no modulation -> constant pitch


def test_engine_table_steps_change_waveform():
    p = Patch(name="Tbl")
    p.osc1_enabled = True
    p.oscillators[0].waveform = 0
    p.oscillators[0].table_speed = 127  # advance quickly
    tables = p.tables
    tables[0].steps.append(TableStep(0x01, 0x00))  # Triangle
    tables[0].steps.append(TableStep(0x04, 0x00))  # Pulse
    tables[0].terminator = "loop"
    tables[0].loop_point = 0
    p.replace_tables(tables)
    eng = _engine(p)
    waveforms = {eng.tick()[0x04] & 0xF0 for _ in range(20)}
    assert 0x10 in waveforms and 0x40 in waveforms  # both triangle and pulse seen


def test_engine_filter_envelope_raises_cutoff():
    p = _saw_patch()
    p.filter_type = 1
    p.filter_osc1 = True
    p.filter_cutoff = 0
    p.filter_env_depth = 127
    p.filter_env_attack = 40
    p.filter_env_sustain = 127
    eng = _engine(p)
    cutoffs = [((r := eng.tick())[0x16] << 3) | r[0x15] for _ in range(30)]
    assert cutoffs[-1] > cutoffs[0]  # cutoff opens as the envelope attacks


def test_engine_vibrato_modulates_pitch():
    p = _saw_patch()
    p.oscillators[0].vibrato_depth = 127
    p.oscillators[0].vibrato_lfo = 0
    p.lfos[0].lfo_type = 0  # triangle
    p.lfos[0].speed = 127  # fast
    eng = _engine(p, note=69)
    fregs = {((r := eng.tick())[0x01] << 8) | r[0x00] for _ in range(40)}
    assert len(fregs) > 5  # pitch wobbles


def test_engine_note_off_clears_gate():
    eng = _engine(_saw_patch(), note=69)
    assert eng.tick()[0x04] & 0x01  # gated on
    eng.note_off()
    assert not eng.tick()[0x04] & 0x01  # gate released


# -- Actual reSIDfp render (only when pyresidfp is installed) -----------------


def test_render_produces_non_silent_wav(tmp_path):
    pytest.importorskip("pyresidfp")
    from sidstation.render import render_patch, write_wav

    sample_rate, samples = render_patch(
        _saw_patch(), note=69, duration=0.2, clock="pal", release=0.1
    )
    assert sample_rate == 44100
    assert len(samples) > 0
    assert any(s != 0 for s in samples)  # not silent

    out = tmp_path / "test.wav"
    write_wav(out, sample_rate, samples)
    import wave

    with wave.open(str(out)) as w:
        assert w.getnchannels() == 1
        assert w.getsampwidth() == 2
        assert w.getnframes() == len(samples)


def test_render_clock_choices_differ(tmp_path):
    pytest.importorskip("pyresidfp")
    from sidstation.render import render_patch

    _, pal = render_patch(_saw_patch(), note=69, duration=0.1, clock="pal", release=0.0)
    _, ntsc = render_patch(_saw_patch(), note=69, duration=0.1, clock="ntsc", release=0.0)
    assert pal != ntsc


def test_cli_end_to_end(tmp_path):
    pytest.importorskip("pyresidfp")
    import wave

    from sidstation import Bank
    from sidstation.render import main

    syx = tmp_path / "bank.syx"
    Bank.from_patches([_saw_patch()]).write(syx)
    out = tmp_path / "out.wav"
    rc = main(
        [
            "--syx",
            str(syx),
            "--patch",
            "0",
            "--clock",
            "ntsc",
            "--knob1",
            "90",
            "--note",
            "60",
            "--duration",
            "0.1",
            "--release",
            "0.05",
            "--out",
            str(out),
        ]
    )
    assert rc == 0
    assert out.is_file()
    with wave.open(str(out)) as w:
        assert w.getnframes() > 0
