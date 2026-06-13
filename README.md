# pysidstation

Read and write **Elektron SidStation** patch (`.syx`) SysEx files in pure
Python — no dependencies.

The SidStation is a synthesizer built around the MOS 6581/8580 "SID" chip from
the Commodore 64. Its patches are exchanged as MIDI System Exclusive dumps.
`pysidstation` parses those dumps into editable `Patch` objects and writes them
back **byte-for-byte**.

The complete, corrected file format is documented in
[`docs/FORMAT.md`](docs/FORMAT.md).

## Install

```bash
pip install pysidstation
```

Requires Python 3.10+.

## Quick start

```python
import sidstation

bank = sidstation.read("SidStation_Presets_r1.syx")

print(len(bank), "patches")          # 90 patches
for patch in bank:
    print(patch.name)                # Anpanman, Krutong, Floating-7, ...

# Edit a patch
lead = bank[0]
lead.name = "My Lead"
lead.poly = True
lead.filter_cutoff = 90
lead.oscillators[0].waveform = sidstation.Waveform.SAW
lead.oscillators[0].ring_mod = True
lead.lfos[0].lfo_type = sidstation.LfoType.RAMP

bank.write("edited.syx")
```

An unmodified file round-trips exactly:

```python
data = open("SidStation_Presets_r1.syx", "rb").read()
assert sidstation.loads(data).to_bytes() == data
```

## Building a bank from scratch

```python
from sidstation import Bank, Patch, Waveform

p = Patch(name="Init")
p.osc1_enabled = True
p.oscillators[0].waveform = Waveform.PULSE
p.oscillators[0].attack = 2
p.oscillators[0].sustain = 12

bank = Bank.from_patches([p])   # prepends the all-clear message by default
bank.write("init.syx")
```

## What you can read and write

Each `Patch` exposes the full parameter set as plain attributes that read and
write the underlying patch bytes directly:

- **Name** — `patch.name` (and `patch.name_bytes` for the raw 10 bytes)
- **Mode** — `osc1_enabled`, `osc2_enabled`, `osc3_enabled`, `poly`, `legato`,
  `filter_wrap`, `filter_env_invert`
- **Filter** — `filter_routing`, `filter_osc1/2/3`, `resonance`, `filter_type`,
  `filter_lfo`, `filter_cutoff`, `filter_env_attack/decay/sustain/release`,
  `filter_env_depth`, `filter_lfo_depth`, `filter_lfo_wheel_depth`
- **Per-patch sync** — `sync_speed`, `sync_hcut`
- **Oscillators** — `patch.oscillators[0..2]` with `waveform`, `ring_mod`,
  `sync`, `test`, `sync_pwm`, `gate`, `track`, `arp_speed`, `transpose`,
  `detune`, `pitchbend_range`, `attack`, `decay`, `sustain`, `release`, `delay`,
  `pwm_start`, `pwm_add`, `pwm_lfo`, `pwm_lfo_depth`, `portamento`,
  `vibrato_lfo`, `vibrato_depth`, `vibrato_wheel_depth`, `table_speed`, plus
  decode helpers `transpose_semitones`, `detune_value`, `detune_cents`
- **LFOs** — `patch.lfos[0..3]` with `lfo_type`, `ctrl_source`, `ctrl_dest`,
  `sync`, `invert`, `above_zero`, `sync_note_off`, `speed`, `sample_hold`,
  `depth`, `add_lfo`, `lace`, `lace_with`, `add_depth`, `ctrl_value`, `fade_in`
- **Direct controllers** — `patch.direct_controllers[0..3]` with `value`,
  `limit_down`, `limit_up`
- **Tables** — `patch.tables` (three `Table`s of `TableStep`s) and
  `patch.replace_tables(...)`

Need the raw bytes? `patch.data` is the mutable, decoded `bytearray` that backs
every attribute above, indexed exactly as in [`docs/FORMAT.md`](docs/FORMAT.md).

## Command line

```bash
sidstation info  SidStation_Presets_r1.syx   # summary table
sidstation names SidStation_Presets_r1.syx   # one name per line
sidstation show  SidStation_Presets_r1.syx 0 # detail for patch 0
```

(Equivalently `python -m sidstation ...`.)

## A couple of things the manual gets wrong

`pysidstation` is built from the owner's manual *and* verified against a real
bank. A few corrections matter in practice:

- **Every stored parameter byte is nibble-swapped** relative to the synth's
  working value. The library undoes this on load (and re-applies on save), so
  `patch.data` and every attribute hold the synth's *true* values while files
  still round-trip byte-for-byte. This single fact is why the manual's waveform,
  LFO-type and detune layouts looked off — once un-swapped they line up. (e.g.
  the waveform is really in the OSC_WAVE high nibble, SID-control style.)
- **LFO type 5 is "Flat", not 7**, and **detune spans ≈ ±1 semitone, not ±½** —
  both corrections to the manual's tables.
- **The size field in each patch header is unreliable** (54 of 90 patches in the
  reference bank disagree with it). The library parses by message boundary and
  preserves the stored value so files still round-trip exactly.

All are explained in [`docs/FORMAT.md`](docs/FORMAT.md).

## Development

```bash
pip install -e ".[dev,lint]"
pytest
ruff check .
ruff format --check .
```

The test suite uses Elektron's factory preset bank as a fixture. That file is
Elektron's copyrighted content and is **not** committed here — `tests/conftest.py`
downloads it on demand from Elektron's [official SidStation Sound Pack
archive](https://www.elektron.se/support-downloads/sidstation), verifies its
SHA-256, and caches it locally. To run offline, point `SIDSTATION_PRESETS` at a
local copy; if it can't be obtained, the dependent tests are skipped.

All tests run in CI (GitHub Actions) across Python 3.10–3.13, and the
distribution is built and checked there too. The lint job uses the pinned
`ruff` from the `lint` extra, which Dependabot keeps current.

## License

Apache-2.0. See [`LICENSE`](LICENSE).

> SidStation and Elektron are trademarks of Elektron Music Machines. This is an
> independent, unofficial library and is not affiliated with or endorsed by
> Elektron.
