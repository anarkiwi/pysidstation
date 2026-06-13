"""Render a SidStation patch to a WAV file through the reSIDfp SID emulation.

This translates a patch into a **static** snapshot of the SID's 25 registers and
plays one note through `pyresidfp` (the reSIDfp 6581/8580 emulator). It models
the parts of a patch that map directly onto SID hardware:

* up to three oscillators -> SID voices 1-3 (waveform, ring-mod, sync, gate)
* the SID ADSR envelope, pulse width, and the multimode filter
* PAL/NTSC clock and 6581/8580 chip model

It does **not** reproduce the SidStation firmware's real-time engine -- LFOs,
arpeggiator, wavetables, PWM sweeps and portamento are not stepped over time. As
a useful approximation, a voice whose static waveform is "off" but which has an
active table is rendered using its *first* table step (waveform + note), so
table-driven drums/leads still make a sound.

The four knobs map to four documented macro controls on the render (the
SidStation's per-patch knob routing is not modelled):

    knob1 -> filter cutoff      knob3 -> pulse width
    knob2 -> filter resonance   knob4 -> master volume

Each is 0..127; omit a knob to use the patch's own value.

Run it::

    sidstation-render --syx bank.syx --patch 0 --clock pal \\
        --knob1 90 --note 60 --duration 2.0 --out out.wav

`pyresidfp` is an optional dependency: ``pip install "pysidstation[render]"``.
"""

from __future__ import annotations

import argparse
import array
import os
import sys
import wave

from .bank import Bank
from .patch import Patch

# -- SID register addresses (0x00-0x18) --------------------------------------
# Per voice block (base 0, 7, 14): +0/+1 freq lo/hi, +2/+3 PW lo/hi,
# +4 control, +5 attack/decay, +6 sustain/release.
FREQ_LO, FREQ_HI, PW_LO, PW_HI, CONTROL, ATTACK_DECAY, SUSTAIN_RELEASE = range(7)
VOICE_BASE = (0x00, 0x07, 0x0E)
FILTER_FC_LO, FILTER_FC_HI, FILTER_RES_FILT, FILTER_MODE_VOL = 0x15, 0x16, 0x17, 0x18

# Control-register bits.
GATE, SYNC, RING_MOD = 0x01, 0x02, 0x04
# Filter mode bits in MODE_VOL.
FILTER_LP, FILTER_BP, FILTER_HP = 0x10, 0x20, 0x40

DEFAULT_VOLUME = 15
# Clock frequencies (Hz); mirror pyresidfp's PAL_/NTSC_CLOCK_FREQUENCY.
PAL_CLOCK_HZ = 985248.0
NTSC_CLOCK_HZ = 1022730.0
CLOCKS = {"pal": PAL_CLOCK_HZ, "ntsc": NTSC_CLOCK_HZ}


def _midi_to_hz(note: float) -> float:
    return 440.0 * 2.0 ** ((note - 69) / 12.0)


def _hz_to_freg(hz: float, clock_hz: float) -> int:
    """SID frequency register value for *hz* at the given clock."""
    return max(0, min(0xFFFF, round(hz * (1 << 24) / clock_hz)))


def _signed8(byte: int) -> int:
    return byte - 256 if byte >= 128 else byte


def patch_to_sid_registers(
    patch: Patch,
    note: int,
    clock_hz: float,
    knobs=(None, None, None, None),
    volume: int = DEFAULT_VOLUME,
) -> dict:
    """Translate *patch* playing *note* into a {register_address: value} map.

    This is the pure, side-effect-free core of the renderer (no pyresidfp), so
    it can be unit tested directly. Returns the note-*on* register state; the
    renderer clears the gate bits afterwards for the release phase.
    """
    regs = {addr: 0 for addr in range(0x19)}
    enabled = (patch.osc1_enabled, patch.osc2_enabled, patch.osc3_enabled)

    for i, osc in enumerate(patch.oscillators):
        base = VOICE_BASE[i]
        table = patch.tables[i]
        if osc.waveform == 0 and osc.table_speed > 0 and table.steps:
            # Table-driven voice: approximate with the first step.
            step = table.steps[0]
            wave_bits = (step.waveform & 0x0F) << 4
            if step.note_mode == "fixed":
                eff_note = step.note_value
            elif step.note_mode == "add":
                eff_note = note + step.note_value
            else:
                eff_note = note - step.note_value
            sync, ring = step.sync, step.ring_mod
        else:
            wave_bits = (osc.waveform & 0x0F) << 4
            transpose = _signed8(osc.transpose)
            eff_note = note + (transpose if -36 <= transpose <= 36 else 0)
            sync, ring = osc.sync, osc.ring_mod

        freg = _hz_to_freg(_midi_to_hz(eff_note), clock_hz)
        regs[base + FREQ_LO] = freg & 0xFF
        regs[base + FREQ_HI] = (freg >> 8) & 0xFF
        pw = min(0xFFF, osc.pwm_start * 32)
        regs[base + PW_LO] = pw & 0xFF
        regs[base + PW_HI] = (pw >> 8) & 0x0F
        # A/D/S/R: the patch stores each 4-bit value in the high nibble, so the
        # attack/sustain bytes already sit in the high nibble of the SID reg.
        regs[base + ATTACK_DECAY] = (osc.attack & 0xF0) | ((osc.decay >> 4) & 0x0F)
        regs[base + SUSTAIN_RELEASE] = (osc.sustain & 0xF0) | ((osc.release >> 4) & 0x0F)
        control = wave_bits | (SYNC if sync else 0) | (RING_MOD if ring else 0)
        if enabled[i] and wave_bits:
            control |= GATE
        regs[base + CONTROL] = control

    # -- Filter + master volume ---------------------------------------------
    cutoff, resonance = patch.filter_cutoff, patch.resonance
    if knobs[0] is not None:
        fc11 = min(2047, knobs[0] * 16)
    else:
        fc11 = min(2047, cutoff * 8)
    if knobs[1] is not None:
        resonance = min(15, knobs[1] >> 3)
    if knobs[3] is not None:
        volume = min(15, knobs[3] >> 3)

    regs[FILTER_FC_LO] = fc11 & 0x07
    regs[FILTER_FC_HI] = (fc11 >> 3) & 0xFF

    if patch.filter_type > 0:
        routing = (patch.filter_osc1, patch.filter_osc2, patch.filter_osc3)
        filt_bits = sum(bit for bit, on in zip((0x01, 0x02, 0x04), routing, strict=True) if on)
        mode = (patch.filter_type & 0x07) << 4  # bit0=LP, bit1=BP, bit2=HP
    else:
        filt_bits, mode = 0, 0  # filter bypassed
    regs[FILTER_RES_FILT] = ((resonance & 0x0F) << 4) | filt_bits
    regs[FILTER_MODE_VOL] = mode | (volume & 0x0F)

    if knobs[2] is not None:  # pulse-width macro: apply to every voice
        pw = min(0xFFF, knobs[2] * 32)
        for base in VOICE_BASE:
            regs[base + PW_LO] = pw & 0xFF
            regs[base + PW_HI] = (pw >> 8) & 0x0F

    return regs


def render_patch(
    patch: Patch,
    note: int = 60,
    duration: float = 2.0,
    clock: str = "pal",
    model: str = "6581",
    knobs=(None, None, None, None),
    sample_rate: int = 44100,
    release: float = 0.5,
    volume: int = DEFAULT_VOLUME,
):
    """Render *patch* to mono 16-bit samples. Returns ``(sample_rate, samples)``.

    Plays the note for *duration* seconds (gate on), then releases for *release*
    seconds (gate off) so the envelope tail is audible.
    """
    import datetime

    from pyresidfp import SoundInterfaceDevice
    from pyresidfp._pyresidfp import ChipModel, SamplingMethod
    from pyresidfp.registers import WritableRegister

    clock_hz = CLOCKS[clock] if isinstance(clock, str) else float(clock)
    chip = {"6581": ChipModel.MOS6581, "8580": ChipModel.MOS8580}[str(model)]
    sid = SoundInterfaceDevice(
        model=chip,
        sampling_method=SamplingMethod.RESAMPLE,
        clock_frequency=clock_hz,
        sampling_frequency=float(sample_rate),
    )
    addr_to_reg = {int(reg): reg for reg in WritableRegister}
    regs = patch_to_sid_registers(patch, note, clock_hz, knobs=knobs, volume=volume)
    for addr, value in regs.items():
        sid.write_register(addr_to_reg[addr], value & 0xFF)

    samples = list(sid.clock(datetime.timedelta(seconds=max(0.0, duration))))
    for base in VOICE_BASE:  # gate off -> release phase
        ctrl = base + CONTROL
        sid.write_register(addr_to_reg[ctrl], regs[ctrl] & ~GATE & 0xFF)
    samples += list(sid.clock(datetime.timedelta(seconds=max(0.0, release))))
    return int(sample_rate), samples


def write_wav(path, sample_rate: int, samples) -> None:
    """Write mono 16-bit PCM *samples* to *path*."""
    clamped = array.array("h", (max(-32768, min(32767, int(s))) for s in samples))
    if sys.byteorder == "big":
        clamped.byteswap()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(int(sample_rate))
        wav.writeframes(clamped.tobytes())


# -- CLI ---------------------------------------------------------------------


def _resolve_syx(path):
    if path:
        return path
    env = os.environ.get("SIDSTATION_PRESETS")
    if env and os.path.exists(env):
        return env
    fallback = os.path.join("tests", "data", "SidStation_Presets_r1.syx")
    if os.path.exists(fallback):
        return fallback
    raise SystemExit("no patch bank given: pass --syx PATH (or set SIDSTATION_PRESETS)")


def _select_patch(bank: Bank, selector: str) -> Patch:
    try:
        return bank[int(selector)]
    except ValueError:
        for patch in bank:
            if patch.name == selector:
                return patch
        raise SystemExit(f"no patch named {selector!r} in the bank") from None
    except IndexError:
        raise SystemExit(f"patch index {selector} out of range") from None


def _knob(value):
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"invalid knob value: {value!r}") from None
    if not 0 <= ivalue <= 127:
        raise argparse.ArgumentTypeError("knob value must be 0..127")
    return ivalue


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sidstation-render",
        description="Render a SidStation patch to a WAV via reSIDfp.",
    )
    parser.add_argument("--syx", help="patch bank .syx (default: $SIDSTATION_PRESETS)")
    parser.add_argument("--patch", default="0", help="patch index or name (default: 0)")
    parser.add_argument("--clock", choices=("pal", "ntsc"), default="pal", help="SID clock source")
    parser.add_argument("--model", choices=("6581", "8580"), default="6581", help="SID chip model")
    parser.add_argument("--note", type=int, default=60, help="MIDI note 0..127 (default 60)")
    parser.add_argument("--duration", type=float, default=2.0, help="note length, seconds")
    parser.add_argument("--release", type=float, default=0.5, help="release tail, seconds")
    parser.add_argument("--rate", type=int, default=44100, help="output sample rate")
    for n in (1, 2, 3, 4):
        parser.add_argument(f"--knob{n}", type=_knob, default=None, help=f"knob {n} (0..127)")
    parser.add_argument("--out", help="output .wav path (default: <patch name>.wav)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    bank = Bank.read(_resolve_syx(args.syx))
    patch = _select_patch(bank, args.patch)
    knobs = (args.knob1, args.knob2, args.knob3, args.knob4)
    out = args.out or "".join(c if c.isalnum() else "_" for c in patch.name.strip()) + ".wav"

    sample_rate, samples = render_patch(
        patch,
        note=args.note,
        duration=args.duration,
        clock=args.clock,
        model=args.model,
        knobs=knobs,
        sample_rate=args.rate,
        release=args.release,
    )
    write_wav(out, sample_rate, samples)

    peak = max((abs(s) for s in samples), default=0)
    print(
        f"rendered {patch.name!r}  note={args.note} {args.clock.upper()}/{args.model} "
        f"knobs={knobs}  {len(samples)} samples @ {sample_rate} Hz  peak={peak}\n"
        f"wrote {out}"
    )
    if peak == 0:
        print(
            "warning: output is silent (this patch may rely on modulation that "
            "the static renderer does not model, or the voice is gated off)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
