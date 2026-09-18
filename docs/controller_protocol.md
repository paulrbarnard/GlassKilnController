# GlassMaster LT Controller Protocol Reference

Transcribed from *GlassMaster LT Operating Manual (HotStart Pro / FireBox 14 /
FireBox 8 w/ GM LT)*, revised 7/25/17. Page numbers below refer to that PDF.
This is the source of truth for `python/kiln/protocol.py`, `profiles.py`, and
`sequencer.py` — if the physical controller ever disagrees with this doc,
**trust the physical controller** and fix this file and the code together.

## Physical controls (p.10)

| Key (our name) | Silkscreen | Short press | Long press (4s) |
|---|---|---|---|
| `ENTER` | START / STOP / ENTER | start/select/confirm | from anywhere → back to `IdLE` ("Start Over") |
| `UP` | UP / VIEW SEGMENT | scroll value/option up; view active segment while firing | from `IdLE`, beginner level → unlock Advanced modes |
| `DOWN` | DOWN / REVIEW | scroll value/option down; review active/last program | from `IdLE`, advanced level → relock to Beginner |

Rapid scroll: holding `UP`/`DOWN` accelerates value changes; the manual
recommends releasing and tapping near the target value (p.10). Our sequencer
should therefore prefer many short taps over one long hold when precision
matters, and only use held scrolling for large deltas it can afford to
overshoot-and-correct via vision feedback.

## Display vocabulary (7-segment, pp.11-12)

The display is 7-segment LED, so some letters are approximated (`5` doubles
as `S`, etc). This is the exact string set the vision module
(`python/kiln/vision.py`) must be able to classify, mirrored in
`protocol.DisplayMessage`:

| Token | Meaning |
|---|---|
| `°F` / `°c` | temperature scale indicator, shown while entering/selecting a temperature |
| `Auto` | AUTO / GlassFire mode |
| `Pro` | PRO / Ramp & Hold mode |
| `LoAd` | LOAD mode |
| `OPt` | OPTIONS mode |
| `CHG°` | OPTIONS: "change temperature scale?" prompt |
| `CPLt` | program COMPLETE (kiln may still be hot) |
| `EdIt` | "enter Edit Mode?" prompt (live, firing only) |
| `HLd` | prompts for a Hold time (Hours.Minutes) |
| `IdLE` | idle state, alternates with current kiln temp — the only safe programming start point |
| `MGF` | Modify GlassFire mode indicator/toggle |
| `ON` | program running |
| `PF` | power failure occurred; press `ENTER` to clear |
| `rA` | Ramp/Rate prompt (Ramp & Hold segment) |
| `rEdI` | Ready — program loaded, press `ENTER` to start |
| `rSEt` | OPTIONS: factory reset |
| `SLo` / `MEd` / `FASt` | GlassFire firing speed selection |
| `SLP` / `tAC` / `FULL` | GlassFire process: Slump / Tack Fuse / Full Fuse |
| `SSEG` | Skip Segment prompt (Edit Mode) |
| `StOP` | program stopped (brief) |
| `tME` | Add Time prompt (Edit Mode) |
| `tMP` | Add Temperature prompt (Edit Mode) |
| `Err1` | heating segment: elements/relay failed to maintain +12°F/hr. Kiln shuts off. |
| `ErrF` | cooling segment: relay failed to maintain -12°F/hr. Kiln shuts off. |
| `ErrD` | heating segment: temp ≥100°F over setpoint for 15s (relay stuck on). Kiln shuts off. |
| `FAIL` | thermocouple failed. Kiln shuts off. |
| `tc--` | thermocouple wired backwards. |

`rA2`, `HLd2`, etc. — segment display messages are suffixed with the active
segment number while firing (View Segment, p.20).

## Programming levels (p.8)

1. **Beginner** (factory default) — only `AUTO` and (while firing) `EDIT`
   are reachable.
2. **Beginner Plus MGF** — adds "Modify GlassFire" (`MGF`): AUTO mode asks
   to confirm/change the process temperature and hold time each run.
3. **Advanced** — adds `PRO`, `LOAD`, `OPt` modes: custom Ramp & Hold
   programs, loading stored programs, factory options/reset.

Level is controller-global state, not per-run — the sequencer must check/set
it before assuming a mode is reachable (`protocol.ProgrammingLevel`).

```
IdLE --(ENTER)--> mode shown (Auto/Pro/LoAd/OPt)
IdLE --(hold UP 4s)--> unlock Advanced (Beginner -> Advanced)
IdLE --(hold DOWN 4s)--> relock Beginner (Advanced -> Beginner)
anywhere --(hold ENTER 4s)--> IdLE
```

## Five modes (p.9)

```
            (hold UP, 4s)
   AUTO  ───────────────►  PRO ── LOAD ── OPt      (Advanced only)
    ▲  ◄───────────────
    │      (hold DOWN, 4s)
    │
    └── EDIT (reachable only from any mode WHILE FIRING, via UP)
```

- **AUTO (GlassFire)** — run one of 9 factory profiles: process
  (Slump/Tack/Full) × speed (Slow/Med/Fast).
- **PRO (Ramp & Hold)** — author/run up to 5 stored programs, up to 8
  segments each (rate °F/hr, target temp °F, hold H.MM per segment).
- **LOAD** — recall a stored PRO program by slot number and run/review it.
- **OPt (Options)** — toggle MGF, toggle °F/°C, factory RESET (also wipes
  all stored PRO programs except slot 5, which is restored to the factory
  casting program).
- **EDIT** — live-only. Reached with `UP` while firing: Skip Segment, Add
  Time, Add Temperature (segment 2 hold only).

## Key sequences

Timings noted are from the manual; treat them as nominal — press windows are
short (~2-6s) so the sequencer must poll the camera fast enough to catch the
right prompt, and should defensively re-`ENTER`-hold back to `IdLE` and retry
the whole sequence if a poll misses a window rather than pressing blind.

### Return to Idle (universal escape hatch, p.8, p.10)
```
hold ENTER 4s  → IdLE
```

### Unlock / relock Advanced level (p.8, p.18)
```
from IdLE:
  hold UP 4s    → beep, Advanced unlocked (Auto/Pro/LoAd/OPt reachable)
  hold DOWN 4s  → beep, back to Beginner (Auto/Edit only)
```

### Run a GlassFire (AUTO) program (p.14-15)
```
1. from IdLE:                 ENTER
2. mode shows Auto/Pro:       scroll (UP/DOWN) to "Auto" if needed, ENTER
3. speed shows SLo/MEd/FASt:  scroll to desired speed, ENTER
4. process shows SLP/tAC/FULL:scroll to desired process, ENTER
5. [only if MGF is ON]
     process temp flashes:    UP or DOWN once to stop flashing, adjust value,
                               ENTER to lock in
     hold time flashes:       UP or DOWN once to stop flashing, adjust value
                               (H.MM), ENTER to lock in
   [only if MGF is OFF]       controller goes straight to rEdI
6. display shows rEdI:        ENTER to start (display -> ON, then temperature)
```
Segment 3 (process/soak) → segment 4 is the Pre-Annealing cool (fast, to
960°F/516°C) → final segment is Annealing (slow, 960→800°F/516→427°C, kiln
shuts off and free-cools after). **Never command a lid-open action once
Pre-Annealing has started** — thermal/stress risk (p.15), and the sequencer
has no business opening the lid anyway; this is a note for the profile
descriptions shown to the human.

### Modify a GlassFire program's process values without MGF (pp.16-17)

Only possible via `PRO` reprogramming or via live `EDIT` mode (below); MGF
(step 5 above) is the sanctioned way to do it per-run.

### Entry limits (pp.16-17) — `protocol.MAX_PROCESS_TEMP_F` / `MAX_HOLD_HOURS`/`MAX_HOLD_MINUTES_PART`

- **Temperature**: "The maximum temperature that can be entered is 1650°F
  (899°C)" (p.16). Applies to any temperature entry (GlassFire process
  temp, PRO segment temp).
- **Hold time**: "The maximum time that can be entered is 99 Hours and 99
  Minutes" (p.17) — entered as H.MM, not normalized (i.e. genuinely up to
  99h *and* 99 more minutes, not capped at 100h). No documented minimum
  for either; 0 is the sensible floor for hold, and room temperature is
  the sensible floor for a target temperature the UI would ever suggest,
  though the controller itself doesn't state one.

### Author + run a Ramp & Hold (PRO) program (pp.18-19)
```
1. Advanced level required (see unlock sequence)
2. from IdLE:                 ENTER
3. mode shows Auto/Pro:       scroll to "Pro", ENTER
4. shows Pro + slot 1-5:      scroll to target slot, ENTER
   (CAUTION: slot 5 ships with the factory casting program — see below)
5. shows SEG + count:         scroll to segment count (1-8), ENTER
6. for each segment i in 1..count:
     "rA i" (rate):           scroll to °F(or °C)/hr, ENTER
     "°F i" (temp):           scroll to target temp, ENTER
     "HLd i" (hold):          scroll to hold time H.MM, ENTER
7. after final segment:       display shows rEdI
8. ENTER to start (-> ON).  Optionally DOWN to Review first (p.19) — steps
   through Rate/Process/Temp/Hold for verification before starting.
```

### Load a stored Ramp & Hold program (p.22)
```
1. Advanced level required
2. from IdLE:                 ENTER
3. scroll to "LoAd",          ENTER
4. scroll to slot 1-5,        ENTER  → loads program
5. shows rEdI:                ENTER to start, or DOWN to Review first
```

### Options: toggle MGF / °F-°C / factory reset (p.22)
```
1. Advanced level required
2. from IdLE:                 ENTER
3. scroll to "OPt",            ENTER
4. scroll to feature (MGF / CHG° / rSEt), ENTER
   - MGF, CHG°:  scroll to toggle value, ENTER to select
   - rSEt:       ENTER to select (confirms reset — this WIPES stored PRO
                  programs 1-4 and restores the factory casting program to
                  slot 5; never issue this from the sequencer without
                  explicit human confirmation in the UI)
```

### Live Edit Mode — Skip Segment (p.21)
```
while firing:
  UP                       → shows current segment, then "EdIt" (~2s window)
  ENTER (while EdIt shown) → shows "SSEG" (~6s window)
  ENTER (while SSEG shown) → advances to next segment immediately
verify with View Segment (UP, read "rA n"/"HLd n") afterwards.
```
Uses: cut a process hold short once the camera/vision confirms the glass has
reached the desired look, or fast-forward through Annealing if a faster
custom cool is acceptable (p.21, p.31 "How Do I Make The Kiln Cool Faster?").

### Live Edit Mode — Add Time (p.21)
Only valid while the display reads `HLd2` (segment 2 hold — the process
soak). Adds 5 minutes per invocation; repeatable.
```
while firing, in segment 2 hold (confirm via View Segment first):
  UP                       → current segment, then "EdIt" (~6s window)
  ENTER (while EdIt shown) → "SSEG" (~6s window)
  UP   (while SSEG shown)  → advances prompt to "tME"
  ENTER (while tME shown)  → +5 min to segment-2 hold time
```

### Live Edit Mode — Add Temperature (p.22)
Only valid in segment 2 hold. Adds 5°F (3°C) per invocation; does **not**
reset/extend hold time, so pair with Add Time if the piece needs time to
reach the new target.
```
while firing, in segment 2 hold:
  UP                       → current segment, then "EdIt" (~6s window)
  ENTER (while EdIt shown) → "SSEG" (~6s window)
  UP, UP (while SSEG shown)→ advances prompt SSEG -> tME -> tMP
  ENTER (while tMP shown)  → +5°F (3°C) to segment-2 process temperature
```

### View Segment / Review (read-only, pp.20)
```
UP while firing     → "rA n" (heating) or "HLd n" (holding at segment n),
                       then briefly "EdIt", then back to temperature display
DOWN (firing or idle)→ steps through Rate / Process / Process Temp /
                       Process Hold of the running or last-run program
```
The sequencer should use `UP`/View-Segment as its primary "where are we"
poll — it's non-destructive and always available while firing.

## Factory GlassFire tables (p.32) — `protocol.GLASSFIRE_RATE_TABLE`

Each GlassFire program is 4 segments: Initial ramp → process ramp (rate
depends on speed, target is "by process") → fast Pre-Annealing cool to
950°F → slow Annealing cool to 800°F.

| Speed | Seg | Rate (°F/hr) | Soak Temp | Hold |
|---|---|---|---|---|
| Slow | 1 | 250 | 300°F | 10 min |
| Slow | 2 | 400 | by process | by process |
| Slow | 3 | 9999 (AFAP) | 950°F | 60 min |
| Slow | 4 | 100 | 800°F | 15 min |
| Medium | 1 | 350 | 300°F | 10 min |
| Medium | 2 | 600 | by process | by process |
| Medium | 3 | 9999 (AFAP) | 950°F | 30 min |
| Medium | 4 | 150 | 800°F | 10 min |
| Fast | 1 | 500 | 300°F | 5 min |
| Fast | 2 | 800 | by process | by process |
| Fast | 3 | 9999 (AFAP) | 950°F | 30 min |
| Fast | 4 | 175 | 800°F | 10 min |

### Process targets (p.32) — `protocol.PROCESS_TABLE`

| Process | Soak Temp | Hold |
|---|---|---|
| Full Fuse (`FULL`) | 1465°F | 10 min |
| Tack Fuse (`tAC`) | 1325°F | 10 min |
| Slump (`SLP`) | 1225°F | 15 min |

### Factory casting program — PRO slot 5 (p.33)

Overwritten by anything the user stores in slot 5; restored by factory
RESET.

| Seg | Rate (°F/hr) | Temp | Hold |
|---|---|---|---|
| 1 | 150 | 250°F | 00:15 |
| 2 | 250 | 1500°F | 00:05 |
| 3 | 1000 | 950°F | 02:30 |
| 4 | 30 | 750°F | 00:20 |

## Size/speed guidance shown to the user (p.14)

Used by the UI to suggest a starting speed for a new custom project; not a
controller input.

| Speed | 1/8" thick | 1/4" thick | 3/8" thick | 1/2" thick |
|---|---|---|---|---|
| Fast | ≤18" dia | ≤4" dia | not recommended | — |
| Medium | ≤20" dia | ≤8" dia | ≤5" dia | — |
| Slow | ≤24" dia | ≤18" dia | ≤12" dia | ≤8" dia |

## Firing/error notes the UI should surface, not just log

- Wait for internal temp < 100°F (38°C) before ever suggesting the lid be
  opened (pp.15, 27) — thermal shock cracks glass.
- `Err1` / `ErrF` / `ErrD` / `FAIL` all mean the kiln has already shut itself
  off — the sequencer should stop, surface the error prominently, and never
  attempt to "press through" an error state.
- `PF` on connect means a power interruption happened — before assuming
  IdLE, the sequencer must press `ENTER` to clear it and confirm no firing
  was silently abandoned.
