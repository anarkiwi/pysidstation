"""Render a SidStation patch to a WAV file through the reSIDfp SID emulation.

This plays one note of a patch through `pyresidfp` (the reSIDfp 6581/8580
emulator) and maps the patch onto the SID hardware:

* up to three oscillators -> SID voices 1-3 (waveform, ring-mod, sync, gate)
* the SID ADSR envelope, pulse width, and the multimode filter
* PAL/NTSC clock and 6581/8580 chip model

By default it uses a **dynamic** engine (:class:`PatchEngine`) that re-steps the
SidStation firmware's real-time modulation each tick (at the patch's local sync
speed): oscillator wavetables, the four LFOs (vibrato / PWM / filter cutoff), the
software filter envelope, and the PWM sweep. ``engine="static"`` instead writes a
single register snapshot (:func:`patch_to_sid_registers`) and holds it.

Not modelled: the arpeggiator and portamento (which need more than one note) and
the LFO lace / add-LFO / fade-in / MIDI-controller routings.

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


# -- Time-stepped modulation engine ------------------------------------------
# These constants are approximations where the manual does not pin down exact
# rates/depths; they are tuned to sound plausible and are easy to adjust.
DEFAULT_TICK_RATE = 50.0  # Hz, when a patch's sync speed is unset/out of range
LFO_MAX_HZ = 14.0  # LFO rate at speed 127
VIBRATO_MAX_SEMITONES = 2.0  # pitch swing at vibrato depth 127
DETUNE_MAX_SEMITONES = 0.5  # at detune extremes
PWM_LFO_MAX = 1400  # 12-bit PW swing at PWM-LFO depth 127
PWM_SWEEP_GAIN = 0.6  # 12-bit PW added per tick per unit of pwm_add
FILTER_LFO_MAX = 1024  # 11-bit cutoff swing at filter-LFO depth 127
FILTER_ENV_MAX = 2047  # 11-bit cutoff swing at filter-env depth 127
TABLE_STEP_THRESHOLD = 64.0  # table advances when an accumulator of table_speed crosses this


def _env_seconds(value: int) -> float:
    """Map a 0..127 filter-envelope rate to a segment time in seconds (approx)."""
    return 0.003 + (value / 127.0) ** 2 * 6.0


class PatchEngine:
    """A tick-based approximation of the SidStation firmware's voice engine.

    Each tick (run at the patch's local update / "sync" speed) recomputes the
    *time-varying* SID registers on top of the static snapshot from
    :func:`patch_to_sid_registers`:

    * **wavetables** -- per-oscillator tables step their waveform and note
      (fixed / +offset / -offset), honouring LOOP and END
    * **LFOs** -- triangle/saw/ramp/pulse/random/flat shapes routed to vibrato
      (pitch), pulse width and filter cutoff
    * **filter envelope** -- a software ADSR scaled by its depth, added to cutoff
    * **PWM sweep** -- ``pwm_add`` ramps the pulse width

    The SID's hardware handles the oscillator amplitude ADSR (gated once at
    note-on). Not modelled: arpeggiator and portamento (need multiple notes),
    and LFO lace / add-LFO / fade-in / controller routing.
    """

    def __init__(
        self,
        patch: Patch,
        note: int,
        clock_hz: float,
        knobs=(None, None, None, None),
        volume: int = DEFAULT_VOLUME,
        tick_rate=None,
    ):
        self.patch = patch
        self.note = note
        self.clock_hz = clock_hz
        sync = patch.sync_speed
        self.tick_rate = float(tick_rate or (sync if 10 <= sync <= 400 else DEFAULT_TICK_RATE))
        self.base = patch_to_sid_registers(patch, note, clock_hz, knobs=knobs, volume=volume)
        self._base_cutoff = (self.base[FILTER_FC_HI] << 3) | self.base[FILTER_FC_LO]
        self.gate_on = True
        self.lfo_phase = [0.0, 0.0, 0.0, 0.0]
        self._rng = (0x2545F491 ^ (note * 2654435761)) & 0xFFFFFFFF
        self._rand_val = [0.0, 0.0, 0.0, 0.0]
        self._rand_period = [-1, -1, -1, -1]
        self.table_pos = [0, 0, 0]
        self.table_acc = [0.0, 0.0, 0.0]
        self.pwm_offset = [0.0, 0.0, 0.0]
        self.fenv_level = 0.0
        self.fenv_stage = "attack"

    def note_off(self) -> None:
        self.gate_on = False
        self.fenv_stage = "release"

    def _lfo_value(self, i: int) -> float:
        lfo = self.patch.lfos[i & 3]
        phase = self.lfo_phase[i & 3]
        p = phase % 1.0
        typ = lfo.lfo_type
        if typ == 0:  # triangle
            v = 1.0 - 4.0 * abs(p - 0.5)
        elif typ == 1:  # saw up
            v = 2.0 * p - 1.0
        elif typ == 2:  # ramp down
            v = 1.0 - 2.0 * p
        elif typ == 3:  # pulse
            v = 1.0 if p < 0.5 else -1.0
        elif typ == 4:  # random sample & hold
            period = int(phase)
            if period != self._rand_period[i & 3]:
                self._rand_period[i & 3] = period
                self._rng = (1103515245 * self._rng + 12345) & 0x7FFFFFFF
                self._rand_val[i & 3] = (self._rng / 0x7FFFFFFF) * 2.0 - 1.0
            v = self._rand_val[i & 3]
        else:  # flat / unknown -> no modulation
            v = 0.0
        if lfo.invert:
            v = -v
        if lfo.above_zero:
            v = abs(v)
        return v

    def _table_step(self, i: int, osc, table):
        # Read the current step first, then advance the counter for next tick,
        # so the very first table entry actually plays.
        n = len(table.steps)
        pos = self.table_pos[i]
        if pos >= n:
            if table.terminator == "loop" and table.loop_point < n:
                span = max(1, n - table.loop_point)
                pos = table.loop_point + (pos - table.loop_point) % span
            else:  # END (or no terminator): hold the last step
                pos = n - 1
            self.table_pos[i] = pos
        step = table.steps[pos]
        self.table_acc[i] += osc.table_speed
        while self.table_acc[i] >= TABLE_STEP_THRESHOLD:
            self.table_acc[i] -= TABLE_STEP_THRESHOLD
            self.table_pos[i] += 1
        return step

    def _advance_fenv(self) -> float:
        dt = 1.0 / self.tick_rate
        p = self.patch
        sustain = p.filter_env_sustain / 127.0
        if self.fenv_stage == "attack":
            self.fenv_level += dt / _env_seconds(p.filter_env_attack)
            if self.fenv_level >= 1.0:
                self.fenv_level, self.fenv_stage = 1.0, "decay"
        elif self.fenv_stage == "decay":
            self.fenv_level -= dt / _env_seconds(p.filter_env_decay)
            if self.fenv_level <= sustain:
                self.fenv_level, self.fenv_stage = sustain, "sustain"
        elif self.fenv_stage == "sustain":
            self.fenv_level = sustain
        else:  # release
            self.fenv_level = max(0.0, self.fenv_level - dt / _env_seconds(p.filter_env_release))
        return self.fenv_level

    def tick(self) -> dict:
        """Advance one tick and return the SID register map for it."""
        regs = dict(self.base)
        for i, lfo in enumerate(self.patch.lfos):
            self.lfo_phase[i] += (lfo.speed / 127.0) * LFO_MAX_HZ / self.tick_rate
        fenv = self._advance_fenv()
        enabled = (self.patch.osc1_enabled, self.patch.osc2_enabled, self.patch.osc3_enabled)

        for i, osc in enumerate(self.patch.oscillators):
            addr = VOICE_BASE[i]
            table = self.patch.tables[i]
            if osc.table_speed > 0 and table.steps:
                step = self._table_step(i, osc, table)
                wave_bits = (step.waveform & 0x0F) << 4
                if step.note_mode == "fixed":
                    eff_note = step.note_value
                elif step.note_mode == "add":
                    eff_note = self.note + step.note_value
                else:
                    eff_note = self.note - step.note_value
                sync, ring = step.sync, step.ring_mod
            else:
                wave_bits = (osc.waveform & 0x0F) << 4
                transpose = _signed8(osc.transpose)
                eff_note = self.note + (transpose if -36 <= transpose <= 36 else 0)
                sync, ring = osc.sync, osc.ring_mod

            eff_note += (_signed8(osc.detune) / 128.0) * DETUNE_MAX_SEMITONES
            if osc.vibrato_depth:
                eff_note += (
                    self._lfo_value(osc.vibrato_lfo)
                    * (osc.vibrato_depth / 127.0)
                    * VIBRATO_MAX_SEMITONES
                )
            freg = _hz_to_freg(_midi_to_hz(eff_note), self.clock_hz)
            regs[addr + FREQ_LO] = freg & 0xFF
            regs[addr + FREQ_HI] = (freg >> 8) & 0xFF

            pw = osc.pwm_start * 32
            if osc.pwm_add:
                self.pwm_offset[i] = (self.pwm_offset[i] + osc.pwm_add * PWM_SWEEP_GAIN) % 4096
            pw += self.pwm_offset[i]
            if osc.pwm_lfo_depth:
                pw += self._lfo_value(osc.pwm_lfo) * (osc.pwm_lfo_depth / 127.0) * PWM_LFO_MAX
            pw = int(max(0, min(0xFFF, pw)))
            regs[addr + PW_LO] = pw & 0xFF
            regs[addr + PW_HI] = (pw >> 8) & 0x0F

            control = wave_bits | (SYNC if sync else 0) | (RING_MOD if ring else 0)
            if enabled[i] and wave_bits and self.gate_on:
                control |= GATE
            regs[addr + CONTROL] = control

        cutoff = float(self._base_cutoff)
        if self.patch.filter_env_depth:
            contrib = fenv * (self.patch.filter_env_depth / 127.0) * FILTER_ENV_MAX
            cutoff += -contrib if self.patch.filter_env_invert else contrib
        if self.patch.filter_lfo_depth:
            cutoff += (
                self._lfo_value(self.patch.filter_lfo)
                * (self.patch.filter_lfo_depth / 127.0)
                * FILTER_LFO_MAX
            )
        cutoff = int(max(0, min(2047, cutoff)))
        regs[FILTER_FC_LO] = cutoff & 0x07
        regs[FILTER_FC_HI] = (cutoff >> 3) & 0xFF
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
    engine: str = "dynamic",
    tick_rate=None,
):
    """Render *patch* to mono 16-bit samples. Returns ``(sample_rate, samples)``.

    Plays the note for *duration* seconds (gate on), then releases for *release*
    seconds (gate off). ``engine="dynamic"`` (default) steps the modulation
    engine (:class:`PatchEngine`) each tick; ``engine="static"`` writes a single
    register snapshot and holds it.
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

    def write(regs):
        for addr, value in regs.items():
            sid.write_register(addr_to_reg[addr], value & 0xFF)

    if engine == "static":
        regs = patch_to_sid_registers(patch, note, clock_hz, knobs=knobs, volume=volume)
        write(regs)
        samples = list(sid.clock(datetime.timedelta(seconds=max(0.0, duration))))
        for base in VOICE_BASE:
            ctrl = base + CONTROL
            sid.write_register(addr_to_reg[ctrl], regs[ctrl] & ~GATE & 0xFF)
        samples += list(sid.clock(datetime.timedelta(seconds=max(0.0, release))))
        return int(sample_rate), samples

    eng = PatchEngine(patch, note, clock_hz, knobs=knobs, volume=volume, tick_rate=tick_rate)
    step = datetime.timedelta(seconds=1.0 / eng.tick_rate)
    samples = []
    for _ in range(max(1, round(duration * eng.tick_rate))):
        write(eng.tick())
        samples += list(sid.clock(step))
    eng.note_off()
    for _ in range(max(0, round(release * eng.tick_rate))):
        write(eng.tick())
        samples += list(sid.clock(step))
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
    parser.add_argument(
        "--static",
        action="store_true",
        help="render a single register snapshot instead of stepping modulation",
    )
    parser.add_argument(
        "--tick-rate",
        type=float,
        default=None,
        help="modulation update rate in Hz (default: the patch's sync speed)",
    )
    parser.add_argument("--out", help="output .wav path (default: <patch name>.wav)")
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    bank = Bank.read(_resolve_syx(args.syx))
    patch = _select_patch(bank, args.patch)
    knobs = (args.knob1, args.knob2, args.knob3, args.knob4)
    out = args.out or "".join(c if c.isalnum() else "_" for c in patch.name.strip()) + ".wav"

    engine = "static" if args.static else "dynamic"
    sample_rate, samples = render_patch(
        patch,
        note=args.note,
        duration=args.duration,
        clock=args.clock,
        model=args.model,
        knobs=knobs,
        sample_rate=args.rate,
        release=args.release,
        engine=engine,
        tick_rate=args.tick_rate,
    )
    write_wav(out, sample_rate, samples)

    peak = max((abs(s) for s in samples), default=0)
    print(
        f"rendered {patch.name!r}  note={args.note} {args.clock.upper()}/{args.model} "
        f"{engine}  knobs={knobs}  {len(samples)} samples @ {sample_rate} Hz  peak={peak}\n"
        f"wrote {out}"
    )
    if peak == 0:
        print(
            "warning: output is silent (the voice may be gated off, or relies on "
            "modulation this renderer does not model)",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
