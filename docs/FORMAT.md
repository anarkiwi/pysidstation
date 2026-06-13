# The SidStation Patch `.syx` Format

This is a from-scratch, byte-level description of the Elektron SidStation patch
SysEx format, written to be easier to read than the tables in the owner's manual
(r22b, page 40-43) and corrected against a real bank file
(`SidStation_Presets_r1.syx`, 90 patches).

Where the manual and real-world data disagree, this document follows the data
and calls out the difference. The two notable corrections are the **manufacturer
prefix length** and the **oscillator waveform nibble** (see
[Discrepancies](#discrepancies-with-the-manual)).

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
34       SYNC_SPEED         Local update speed (50..200)
35       SYNC_HCUT          Local hard-cut setting (0..15)
36 – 56  OSC1               Oscillator 1 block (21 bytes) ─ see §7
57 – 77  OSC2               Oscillator 2 block (21 bytes)
78 – 98  OSC3               Oscillator 3 block (21 bytes)
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
x+2     OSC_ARP_SPEED       0 = no arpeggiator, else speed
x+3     OSC_TRANSPOSE       transpose (manual: -24..+24)
x+4     OSC_DETUNE          detune (manual: -64..+63)
x+5     OSC_PITCHBEND_RANGE 0..24
x+6     OSC_ATTACK          SID envelope attack  (0..15)
x+7     OSC_DECAY           SID envelope decay   (0..15)
x+8     OSC_SUSTAIN         SID envelope sustain (0..15)
x+9     OSC_RELEASE         SID envelope release (0..15)
x+10    OSC_DELAY           oscillator delay
x+11    OSC_PWM_START       pulse-width start
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

### OSC_FLAGS (x+0)

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┬──────┐
     │  --  │  --  │  --  │  (*) │  --  │  --  │ GATE │ SPWM │
     └──────┴──────┴──────┴──────┴──────┴──────┴──────┴──────┘
  SPWM = restart PWM from PWM_START on every note-on
  GATE = use gate instead of the SID envelope
  (*)  = bit 4 is frequently set in real patches but undocumented;
         it is preserved on write but not interpreted.
```

### OSC_WAVE (x+15) — corrected layout

The manual draws the waveform in the high nibble, but in stored patch data the
waveform code occupies the **low** nibble (see
[Discrepancies](#discrepancies-with-the-manual)):

```
 bit   7      6      5      4      3      2      1      0
     ┌──────┬──────┬──────┬──────┬─────────────────────────┐
     │  --  │RINGM │ SYNC │  --  │    WAVEFORM (low nibble) │
     └──────┴──────┴──────┴──────┴─────────────────────────┘
  WAVEFORM: 1 = Triangle   2 = Saw   4 = Pulse   5 = Mixed   8 = Noise
  RINGM = ring modulation        SYNC = oscillator sync
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
x+5     LFO_ADD_LFO       add the output of another LFO
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
  LFO_TYPE    [bits 2-0]: 0=Triangle 1=Saw 2=Ramp 3=Pulse 4=Random 7=Flat
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

DATA1 = waveform byte (same layout as OSC_WAVE: waveform in the low nibble,
        ring/sync in bits 6/5), OR a terminator:
          0xFF              → TABLE END     (no DATA2 follows)
          0xFE  <looppoint> → TABLE LOOP    (DATA2 is the loop position)

DATA2 (for a normal step) = note:
          0x00..0x7F → fixed note number
          0x80..0xBF → add (value − 0x80) to the played note
          0xC0..0xFF → subtract (value − 0xC0) from the played note
```

Example — table region `04 E1 08 05 04 82 08 05 04 E1 04 C1 FF FF FF`:

```
 04 E1   Pulse, note +0x21        ┐
 08 05   Noise, fixed note 5      │
 04 82   Pulse, note +2           │ Table 1 (6 steps)
 08 05   Noise, fixed note 5      │
 04 E1   Pulse, note +0x21        │
 04 C1   Pulse, note +1           ┘
 FF      Table 1 end
 FF      Table 2 end (empty)
 FF      Table 3 end (empty)
```

---

## Discrepancies with the manual

1. **Manufacturer prefix.** The manual documents a 5-byte prefix
   `00 20 3C 01 00`. The preset file in the wild uses a 4-byte `00 45 01 00`
   (one byte shorter). This library recognises both and preserves whichever a
   file uses.

2. **Oscillator waveform nibble.** The manual's `OSC_WAVE` diagram places the
   waveform in the high nibble (bits 7-4). In stored patch data the waveform
   code (1/2/4/5/8) is plainly in the **low** nibble — e.g. a pulse oscillator
   stores `0x04`, not `0x40`. Ring-mod and sync sit in bits 6 and 5. The low
   nibble reading is what this library implements; it is confirmed across every
   patch in the reference bank.

3. **Size field.** The 14-bit size in the patch-dump header is unreliable (see
   §5). Parse by message boundary, preserve the stored value for fidelity.

4. **Undocumented OSC_FLAGS bit 4** is commonly set in real patches; it is
   preserved but not interpreted.

These conclusions come from round-tripping the reference bank byte-for-byte and
checking that names, tables and value ranges decode sensibly. The library's
test suite enforces the byte-exact round trip.
