# The SidStation Patch `.syx` Format

This is a from-scratch, byte-level description of the Elektron SidStation patch
SysEx format, written to be easier to read than the tables in the owner's manual
(r22b, page 40-43) and corrected against a real bank file
(`SidStation_Presets_r1.syx`, 90 patches).

Where the manual and real-world data disagree, this document follows the data
and calls out the difference. The biggest correction is that every stored
parameter byte is **nibble-swapped** relative to the synth's working value;
the library undoes this on load so `Patch.data` holds the synth's true values
(see §5 and [Discrepancies](#discrepancies-with-the-manual)).

---

## 1. The big picture

A `.syx` file is just a tight concatenation of MIDI System Exclusive messages,
back to back, with no bytes in between:

```
┌─────────────┬─────────────┬─────────────┬─────┬─────────────┐
│  message 0  │  message 1  │  message 2  │ ... │  message N  │
│ F0 ...... F7│ F0 ...... F7│ F0 ...... F7│     │ F0 ...... F7│
└─────────────┴─────────────┴─────────────┴─────┴─────────────┘
```

A typical bank is one **All-clear** message followed by one **Patch-dump**
message per preset:

```
All-clear, Patch "Anpanman", Patch "Krutong", Patch "Floating-7", ...
```

Every message has the same shape: a one-byte start, a manufacturer prefix, a
one-byte message type, a type-specific body, and a one-byte end.

```
 F0  │ manufacturer prefix │ type │ ............ body ............ │ F7
0xF0 │   (4 or 5 bytes)    │ 1 by │                               │0xF7
```

---

## 2. Manufacturer prefix

The bytes between `F0` and the message-type byte identify the device. **Two
variants exist** and this library auto-detects and preserves whichever it finds:

```
Librarian / preset-file prefix (4 bytes) — used by SidStation_Presets_r1.syx
┌──────┬──────┬──────┬──────┐
│  00  │  45  │  01  │  00  │
└──────┴──────┴──────┴──────┘
  Eur/USA  ?      ?    base-channel/pad

Elektron hardware prefix (5 bytes) — as printed in the owner's manual
┌──────┬──────┬──────┬──────┬──────┐
│  00  │  20  │  3C  │  01  │  00  │
└──────┴──────┴──────┴──────┘
  Eur/USA Europe Elektron SidStn  base-channel
```

`00 20 3C` is Elektron's registered 3-byte MIDI manufacturer ID. The preset
file uses the shorter, non-standard `00 45 01 00` instead — see
[Discrepancies](#discrepancies-with-the-manual).

---

## 3. Message types

The byte directly after the prefix:

| Type | Name             | Meaning                                            |
|:----:|------------------|----------------------------------------------------|
| `01` | All clear        | Wipe all patch positions before loading a bank     |
| `02` | Patch dump       | One preset (the only type that carries patch data) |
| `03` | Skip patch       | Advance the current patch position                 |
| `04` | Direct program   | Live-edit a single parameter (not used in banks)   |

This library parses type `02` into rich `Patch` objects and keeps every other
type as an opaque `ControlMessage` so files round-trip exactly.

---

## 4. All-clear message (type `01`)

Total length 22 bytes (with the 4-byte prefix).

```
 F0 │ 00 45 01 00 │ 01 │ 45 │ 2D × 14 │ F7
─── ┼──────────── ┼─── ┼─── ┼──────── ┼───
 ▲       prefix     ▲    ▲      ▲        ▲
 │                  │    │      │        └ SysEx end
 │                  │    │      └ padding: fourteen 0x2D ('-') bytes
 │                  │    └ magic byte 0x45 ('E')
 │                  └ type = 01 (all clear)
 └ SysEx start
```

---

## 5. Patch-dump message (type `02`)

```
 F0 │ 00 45 01 00 │ 02 │ ver │ Sh Sl │ 2D × 24 │ 45 │ <patch payload> │ F7
─── ┼──────────── ┼─── ┼──── ┼────── ┼──────── ┼─── ┼──────────────── ┼───
 ▲      prefix      ▲    ▲      ▲        ▲        ▲          ▲           ▲
 │                  │    │      │        │        │          │           └ end
 │                  │    │      │        │        │          └ patch payload (below)
 │                  │    │      │        │        └ marker 0x45 ('E') = data starts
 │                  │    │      │        └ padding: twenty-four 0x2D ('-') bytes
 │                  │    │      └ size: 14-bit, Sh = high 7 bits, Sl = low 7 bits
 │                  │    └ version (0x00 in every known file)
 │                  └ type = 02 (patch dump)
 └ start
```

### The size field

`size = (Sh << 7) | Sl` is *supposed* to be the number of decoded patch bytes,
but **it is frequently wrong** — in the reference bank, 54 of 90 patches carry a
size that disagrees with the actual data length (e.g. "Body drive" stores 307
but decodes to 177). **Never trust it for parsing.** Derive the real length from
the message boundary instead, and preserve the stored value verbatim so the file
round-trips. (`Patch.declared_size` holds the raw value; set it to `None` to
recompute on write.)

### The patch payload and nibble packing

The payload after the `0x45` marker is:

```
┌────────────────────┬────────────────────────────────────────────┐
│  10 bytes: NAME     │  nibble-packed parameter data              │
│  (raw 7-bit ASCII)  │  (2 transmitted bytes per real byte)       │
└────────────────────┴────────────────────────────────────────────┘
```

The 10 name bytes are sent as-is. **Every byte after the name is split into two
bytes** so that no data byte ever reaches the MIDI-illegal 0x80:

```
 one real byte 0xAB                two transmitted bytes
 ┌───────────────┐                ┌───────┬───────┐
 │ A A A A B B B B │  ───────────▶ │0 0 0 0 A A A A│0 0 0 0 B B B B│
 └───────────────┘                └───────┴───────┘
                                    high nibble    low nibble
```

So the decoded patch length is `10 + (transmitted_payload_len − 10) / 2`.

### The parameter nibble-swap

There is a second transform on top of the nibble *packing*. Every **parameter**
byte (everything after the 10-byte name) is stored with its two nibbles
**swapped** relative to the value the synth actually works with. A stored
`0x21` is transpose `0x12` = +18 semitones; a stored `0xA0` is a release of
`0x0A` = 10; a pulse oscillator's wave byte is stored `0x04` but is really the
SID control value `0x40`; an LFO's *type* is stored in the high nibble. The
10-byte ASCII name is **not** nibble-packed and is exempt, which is why it alone
survives un-swapped.

This is confirmed by the whole factory bank only decoding to valid value ranges
once the swap is undone (e.g. LFO types land in 0..5, filter types in 1..7,
table loop terminators reappear as `0xFE`).

**This library undoes the swap on load and re-applies it on save** (see
`swap_nibbles`), so everything from byte 10 onward in `Patch.data` — and in the
byte map below — holds the synth's **true working value**, while files still
round-trip byte-for-byte. (A consequence: bitfields like the oscillator waveform
and the table note are described below in their *working* layout, which differs
from the manual's stored-data diagrams — see
[Discrepancies](#discrepancies-with-the-manual).)

---

## 6. Decoded patch byte map

After un-packing the nibbles you get the decoded patch, indexed exactly as
below. Total length is variable because the tables at the end are variable.

```
Index    Field              Notes
───────  ─────────────────  ────────────────────────────────────────────
0  – 9   PATCH_NAME         10 ASCII chars, space-padded
10       DCTRL1             Direct controller 1 assignment (0..96)
11       DCTRL1_LIMIT_DOWN  Lower limit (0..127)
12       DCTRL1_LIMIT_UP    Upper limit (0..127)
13–15    DCTRL2 / down / up
16–18    DCTRL3 / down / up
19–21    DCTRL4 / down / up
22       PATCH_MODE         bitfield ─ see below
23       PATCH_MODE2        unused (0)
24       FLT_ROUTING        bitfield ─ filter routing + resonance
25       FLT_TYPE_LFO       bitfield ─ filter type + filter-LFO selector
26       FLT_CUTOFF         Filter cutoff
27       FLT_ENV_DEPTH      Filter envelope depth
28       FLT_ENV_ATTACK     Filter envelope attack
29       FLT_ENV_DECAY      Filter envelope decay
30       FLT_ENV_SUSTAIN    Filter envelope sustain
31       FLT_ENV_RELEASE    Filter envelope release
32       FLT_LFO_DEPTH      LFO depth for filter
33       FLT_LFO_WHEEL_DEP  Mod-wheel amount added to filter LFO
34       SYNC_SPEED         Local update speed — see note below
35       SYNC_HCUT          Local hard-cut setting (0..15)
36 – 56  OSC1               Oscillator 1 block (21 bytes) ─ see §7
57 – 77  OSC2               Oscillator 2 block (21 bytes)
78 – 98  OSC3               Oscillator 3 block (21 bytes)
  ↑ Note — these byte values (like all parameter bytes) are the synth's
    working values: the library has already undone the stored nibble-swap (see
    §5). So SYNC_SPEED reads directly as the update rate in Hz (stored 0x46 →
    0x64 = 100 Hz) and OSC_ARP_SPEED / OSC_TABLE_SPEED read as their dividers,
    with no further swap needed.

99 –109  LFO1               LFO 1 block (11 bytes) ─ see §8
110–120  LFO2               LFO 2 block (11 bytes)
121–131  LFO3               LFO 3 block (11 bytes)
132–142  LFO4               LFO 4 block (11 bytes)
143–...  TABLE1, TABLE2, TABLE3   Three oscillator tables ─ see §9
```

### Byte 22 — PATCH_MODE

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
     │FEINV │FWRAP │LEGATO│  --  │ POLY │ OSC3 │ OSC2 │ OSC1 │
     └──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
  FEINV  = filter envelope invert      POLY = polyphonic mode
  FWRAP  = filter wrap                 OSCn = oscillator n active
  LEGATO = portamento legato
```

`OSCn` are genuine on/off switches for each oscillator's audibility (there are
matching "Osc1/2/3 ON/OFF" direct-program messages). `POLY` selects the voice
model:

* **Single mode** (`POLY = 0`): playing a note triggers **all active
  oscillators simultaneously**, layered, **monophonically** — up to a
  three-oscillator sound from one key.
* **Poly mode** (`POLY = 1`): **three-note polyphony from a single oscillator**
  — each held note takes one SID voice. (The arpeggiator is disabled here.)

### Byte 24 — FLT_ROUTING

```
 bit   7      6      5      4      3      2      1      0
     ┌──────────────────────────┬──────┬──────┬──────┬──────┐
     │      RESONANCE (0..15)    │  --  │ FLT3 │ FLT2 │ FLT1 │
     └──────────────────────────┴──────┴──────┴──────┴──────┘
  FLTn = oscillator n routed through the filter
```

### Byte 25 — FLT_TYPE_LFO

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬──────┬─────────────┬──────┬────────────────────┐
     │  --  │  --  │ FILTER_LFO  │  --  │   FILTER_TYPE (0..7)│
     └──────┴──────┴─────────────┴──────┴────────────────────┘
  FILTER_LFO  = which LFO controls cutoff (0..3)   [bits 5-4]
  FILTER_TYPE = filter mode (0..7)                 [bits 2-0]
```

---

## 7. Oscillator block (21 bytes)

`x` is the block's base index (36, 57 or 78).

```
Offset  Field               Notes
──────  ──────────────────  ─────────────────────────────────────────────
x+0     OSC_FLAGS           bitfield ─ see below
x+1     OSC_TRACK           0 = keyboard tracking, 1..99 = fixed note
x+2     OSC_ARP_SPEED       0 = off, 1..127 = speed; arpeggiates the HELD chord
                            (single mode only — no effect in poly), see below
x+3     OSC_TRANSPOSE       semitones, plain signed -24..+24 (see below)
x+4     OSC_DETUNE          fine tune, plain signed -64..+63; 1 step = 1/64
                            semitone (~1.56 cents), so full scale ≈ ±1 semitone
                            (NOT ±½ — measured empirically; see below)
x+5     OSC_PITCHBEND_RANGE 0..24 (semitones, ±)
x+6     OSC_ATTACK          SID envelope attack  (0..15)
x+7     OSC_DECAY           SID envelope decay   (0..15)
x+8     OSC_SUSTAIN         SID envelope sustain / gate volume (0..15)
x+9     OSC_RELEASE         SID envelope release (0..15)
x+10    OSC_DELAY           oscillator delay
x+11    OSC_PWM_START       pulse-width start; 12-bit SID PW = value × 32, so 64
                            = 0x800 = square (50%). On-screen UI shows 1..64.
x+12    OSC_PWM_ADD         pulse-width sweep amount
x+13    OSC_PWM_LFO         pulse-width LFO number (0..3)
x+14    OSC_PWM_LFO_DEPTH   pulse-width LFO depth
x+15    OSC_WAVE            bitfield ─ see below
x+16    OSC_PORTAMENTO      portamento speed (0..99)
x+17    OSC_VIBRATO_LFO     vibrato LFO number (0..3)
x+18    OSC_VIBRATO_DEPTH   vibrato depth
x+19    OSC_VIBRATO_WHEEL   vibrato mod-wheel depth
x+20    OSC_TABLE_SPEED     0 = table off, else speed
```

### OSC_TRANSPOSE (x+3) and OSC_DETUNE (x+4) — byte encoding

In the working buffer (the stored nibble-swap already undone, §5) both are plain
**signed 8-bit** values — measured empirically by playing a note and reading the
SID FREQ:

- **Transpose** is whole semitones: working `0x0C` → +12, `0x04` → +4,
  `0xF4` → -12. The factory bank's transposes are musical octaves/intervals.
  (The *stored* bytes are the swapped `0xC0`/`0x40`/`0x4F`.)
- **Detune** is in 1/64-semitone steps — the synth computes pitch as
  `(detune + 6) × 4` log-pitch units (256 = one semitone). Working `0x10` = +16
  steps ≈ +25 cents; the editable -64..+63 spans ≈ **±1 semitone** (the manual's
  "±½" is wrong).

`Oscillator.transpose_semitones`, `.detune_value` and `.detune_cents` decode
these.

### OSC_ARP_SPEED (x+2) — the arpeggiator

The SidStation arpeggiator is a **"broken chord" (C64-style) arpeggiator of the
held chord**: it replays the held notes one per `arp_speed` clock tick, in the
order they were pressed. It runs **only in single mode** — *"The arpeggiator has
no function in poly mode."* Each oscillator has its own `arp_speed`. When synced
to MIDI clock the speed maps as: 96 = ½-note, 48 = ¼, 24 = ⅛, 12 = 1/16, 6 =
1/32, 3 = 1/64.

### OSC_FLAGS (x+0)

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
     │  --  │  --  │  --  │  --  │  --  │  --  │ GATE │ SPWM │
     └──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
  SPWM = restart PWM from PWM_START on every note-on (set in ~62% of patches)
  GATE = use gate instead of the SID envelope

  (In *stored* data these land in the high nibble — which is why earlier notes
  reported a "frequently set, undocumented bit 4": that was SPWM seen through
  the nibble-swap, now resolved.)
```

### OSC_WAVE (x+15) — working layout

In the working buffer (nibble-swap undone, §5) OSC_WAVE is exactly a **SID
control register** — waveform in the high nibble, then test/ring/sync/gate. This
matches the manual's diagram; it is only the *stored* byte that has the nibbles
swapped (waveform in the low nibble), the discrepancy older notes flagged (see
[Discrepancies](#discrepancies-with-the-manual)).

```
 bit   7      6      5      4      3      2      1      0
     ┌─────────────────────────┬──────┬──────┬──────┬──────┐
     │   WAVEFORM (high nibble)│ TEST │ RING │ SYNC │ GATE │
     └─────────────────────────┴──────┴──────┴──────┴──────┘
  WAVEFORM: 1 = Triangle   2 = Saw   4 = Pulse   5 = Mixed   8 = Noise
  RING = ring modulation     SYNC = oscillator sync
```

---

## 8. LFO block (11 bytes)

`x` is the block's base index (99, 110, 121 or 132).

```
Offset  Field             Notes
──────  ────────────────  ───────────────────────────────────────────────
x+0     LFO_CTRL_TYPE     bitfield ─ LFO waveform + control source
x+1     LFO_OPTIONS       bitfield ─ sync/invert/etc + control destination
x+2     LFO_SPEED         0..127
x+3     LFO_SAMPLE_HOLD   0 = off, else sample & hold amount
x+4     LFO_DEPTH         0..127
x+5     LFO_ADD_LFO       which LFO's output to add in (0..2)
x+6     LFO_LACE          interlace speed (0 = off)
x+7     LFO_LACE_WITH     0 = zero, 1..4 = LFO1..4
x+8     LFO_ADD_DEPTH     amount of the added LFO
x+9     LFO_CTRL_VALUE    how much the control source affects depth
x+10    LFO_FADE_IN       0 = off, else fade-in speed
```

### LFO_CTRL_TYPE (x+0)

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬─────────────────────────┬──────┬────────────────────┐
     │  --  │     CTRL_SOURCE (0..11)  │  --  │     LFO_TYPE (0..7)│
     └──────┴─────────────────────────┴──────┴────────────────────┘
  CTRL_SOURCE [bits 7-4]: 0=ModWheel 1=PitchBend 2=Velocity 3=Aftertouch
                          4..7=CTRL1..4  8..11=LFO1..4
  LFO_TYPE    [bits 2-0]: 0=Triangle 1=Saw 2=Ramp 3=Pulse(Square) 4=Random
                          5=Flat (steady-max DC; 6/7 are no-ops) —
                          NOT 7 as the manual's data table claims
```

### LFO_OPTIONS (x+1)

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬─────────────────────┬──────┬──────┬──────┬──────┐
     │  --  │   CTRL_DEST (0..4)   │SNOFF │ ABZ  │ INV  │ SYNC │
     └──────┴─────────────────────┴──────┴──────┴──────┴──────┘
  SYNC  = sync to note-on            ABZ   = output strictly positive
  INV   = invert LFO                 SNOFF = sync to note-off
  CTRL_DEST [bits 6-4]: 0=None 1=Depth 2=Speed 3=Sample/Hold 4=Lace
```

---

## 9. Oscillator tables

Three tables follow the LFO blocks, concatenated. Each table is a list of
2-byte steps, ending with a single terminator byte. A table holds at most 32
steps.

```
Each step:
  ┌──────────┬──────────┐
  │  DATA1   │  DATA2   │
  └──────────┴──────────┘

DATA1 = waveform byte (same SID-control layout as OSC_WAVE in the working buffer:
        waveform in the HIGH nibble, ring/sync in bits 2/1), OR a terminator:
          0xFF              → TABLE END     (no DATA2 follows)
          0xFE  <looppoint> → TABLE LOOP    (DATA2 is the loop position)

DATA2 (for a normal step) = note. These are working-buffer bytes (the parameter
       nibble-swap is already undone, §5), so DATA2 is sign-decoded directly:
          0x00..0x7F → fixed/absolute note
          0x80..0xBF → subtract (DATA2 & 0x3F) from base   (bit 6 clear = down)
          0xC0..0xFF → add (DATA2 & 0x3F) to base          (bit 6 set = up)
       where base = played note + OSC_TRANSPOSE.
```

Measured empirically: on Tracers
DA (osc1 transpose +12 → base = played + 12), working DATA2 `0x8C` → base−12,
`0xC3` → base+3, `0x85` → base−5 — matching the played melody.
`TableStep.note_mode`/`note_value` decode this. (Older notes had the bit-6 sign
backwards *and* read the byte un-swapped — both are corrected here.)

Example — Anpanman's table 1 (working bytes
`40 1E 80 50 40 28 80 50 40 1E 40 1C FF`):

```
 40 1E   Pulse, fixed note 0x1E   ┐
 80 50   Noise, fixed note 0x50   │
 40 28   Pulse, fixed note 0x28   │ Table 1 (6 steps)
 80 50   Noise, fixed note 0x50   │
 40 1E   Pulse, fixed note 0x1E   │
 40 1C   Pulse, fixed note 0x1C   ┘
 FF      Table 1 end (Table 2, Table 3 each a lone FF = empty)
```

---

## Discrepancies with the manual

1. **Manufacturer prefix.** The manual documents a 5-byte prefix
   `00 20 3C 01 00`. The preset file in the wild uses a 4-byte `00 45 01 00`
   (one byte shorter). This library recognises both and preserves whichever a
   file uses.

2. **The parameter nibble-swap (the big one).** Every patch parameter byte is
   *stored* with its two nibbles swapped relative to the synth's working
   value (§5). This one fact dissolves a pile of older "the manual is wrong"
   notes: the oscillator waveform only *looks* like it lives in the low nibble
   (a pulse stores `0x04`) because its true high-nibble value `0x40` got
   swapped; OSC_WAVE is really the manual's SID-control layout. The same swap is
   why LFO type, detune, ADSR, filter type, table notes, etc. all looked off.
   The library now undoes it on load, so `Patch.data` matches the synth's
   behaviour and the manual's *intended* layouts. Verified by decoding the
   whole bank to valid ranges.

3. **LFO type 5 is Flat, not 7.** The manual's patch-data table lists `7=Flat`,
   but `5` is really Flat (steady-max DC) and `6/7` are no-ops; the bank uses
   types 0..5 only (six genuine Flat LFOs). The manual's
   own Shape *menu* agrees (six shapes, Tri..Flat = 0..5).

4. **Detune is ≈ ±1 semitone, not ±½.** The manual implies a ±½-semitone detune
   range; the synth moves pitch by `(detune+6)·4` log-pitch units = 1/64
   semitone per step, so the editable -64..+63 spans ≈ ±1 semitone.

5. **Size field.** The 14-bit size in the patch-dump header is unreliable (see
   §5). Parse by message boundary, preserve the stored value for fidelity.

6. **The old "undocumented OSC_FLAGS bit 4"** was an artefact of the swap: it is
   bit 0 (SPWM, restart-PWM-on-note-on, set in ~62% of patches) seen through the
   stored nibble-swap. Now decoded, not mysterious.

These conclusions come from round-tripping the reference bank byte-for-byte and
decoding it to sane value ranges. The test suite enforces the byte-exact round
trip.
