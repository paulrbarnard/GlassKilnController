# Architecture

## Split across the Uno Q

The Arduino UNO Q pairs a real-time STM32 MCU ("sketch side") with a Linux
MPU ("Python side"), connected over an internal RPC bridge
(`arduino-router`, MessagePack-RPC over a Unix domain socket). App Lab
projects follow a fixed layout that ties the two together via `app.yaml`:

```
app.yaml            # App Lab manifest: name, python entry point, sketch dir
sketch/
  sketch.ino         # MCU: owns the 3 servos, nothing else
  sketch.yaml         # board FQBN / library deps
python/
  main.py             # MPU: brings up Bridge + Qt UI
  kiln/                # domain logic — no Qt, no hardware I/O of its own
  ui/                  # PySide6 screens, talks to kiln/ only
```

**Everything that decides what to do lives in Python.** The sketch is
deliberately dumb: it exposes one RPC method, `press_button(which, hold_ms)`,
and does nothing else. This keeps the real-time side trivial to verify (three
servos, one function) and keeps all the interesting logic — protocol
sequencing, vision, profile authoring — in Python where it's easier to test,
version, and iterate without re-flashing the MCU.

```
┌─────────────────────────── Linux / MPU side ───────────────────────────┐
│                                                                          │
│   ui/  (PySide6)              kiln/                                    │
│   ┌────────────┐   Profile    ┌───────────┐   button presses  ┌──────┐ │
│   │ HomeScreen │──────────────▶│ Sequencer │───────────────────▶│Bridge│─┼──▶ RPC ──▶ sketch/ (STM32)
│   │ProfileScrn │◀──progress────│  (state   │◀──observed state──│client│ │        press_button()
│   │ RunScreen  │   /state      │  machine) │                   └──────┘ │        → moves servo
│   │EditorScreen│               └─────┬─────┘                            │
│   └────────────┘                     │ reads                            │
│                                       ▼                                 │
│                                ┌────────────┐      frames        ┌────┐ │
│                                │   Vision   │◀────────────────────│cam │ │
│                                │ (7-seg OCR)│                     └────┘ │
│                                └────────────┘                           │
│                                       ▲                                 │
│                                       │ profiles/library, profiles/user │
│                                ┌────────────┐                           │
│                                │  Profiles  │ (data model + JSON I/O)   │
│                                └────────────┘                           │
└──────────────────────────────────────────────────────────────────────┘
```

## Module responsibilities

- **`kiln/protocol.py`** — static reference data and enums transcribed from
  `docs/controller_protocol.md`: `Key`, `DisplayMessage`, `Mode`,
  `ProgrammingLevel`, the GlassFire rate/process tables, and the factory
  casting program. No behavior, no I/O — just the vocabulary everything else
  is built from. This is the file to update if a future manual revision
  changes anything.

- **`kiln/bridge.py`** — thin RPC client wrapping the Bridge call to the
  sketch's `press_button`. Also defines `MockBridge`, which logs presses
  instead of moving servos, for developing the UI/sequencer off the kiln.

- **`kiln/vision.py`** — camera capture + 7-segment decoding. Produces a
  `ControllerState` (raw display text, best-guess `DisplayMessage`, numeric
  value if applicable) from a frame. This is the piece most dependent on the
  physical build (camera angle, ROI, lighting) — see calibration notes in
  `docs/hardware.md`. `MockVision` lets the sequencer be developed/tested
  against a scripted sequence of states instead of a real camera.

- **`kiln/profiles.py`** — the human-facing notion of a "firing profile":
  either a reference to a built-in GlassFire (process × speed, optionally
  with MGF overrides) or a custom Ramp & Hold program (list of segments).
  Independent of controller *mode mechanics* — a `Profile` doesn't know
  about button sequences, only what temperatures/times/rates it wants.
  Loads/saves JSON under `profiles/library` (built-in, read-only) and
  `profiles/user` (created in the UI).

- **`kiln/sequencer.py`** — the state machine that turns a `Profile` into
  the actual button-press sequence from `controller_protocol.md`, driving
  `bridge` and polling `vision` to confirm each transition before advancing
  ("press, then verify the display agrees, then press again" — never blind
  fire-and-forget). Owns retry/recovery: if a poll misses a timed prompt
  window (`EdIt`, `SSEG`, `tME` etc. are only shown a few seconds), it backs
  out to `IdLE` via the universal escape (`hold ENTER 4s`) and restarts the
  sequence rather than guessing. Surfaces progress + current segment back to
  the UI, and immediately halts and surfaces `Err1`/`ErrF`/`ErrD`/`FAIL` if
  vision ever reads one.

- **`ui/`** — PySide6. `HomeScreen` lists profiles (built-in + user);
  `ProfileScreen` shows the diagram (`widgets/ramp_diagram.py`) and
  description and starts the sequencer; `RunScreen` shows live camera
  preview, current segment/temperature, and Skip/Add-Time/Add-Temp controls
  (mapped to `sequencer` Edit Mode calls) plus a big Stop; `EditorScreen`
  builds/edits a custom Ramp & Hold `Profile`. UI code should only ever call
  into `kiln/`, never `bridge`/`vision` directly, so the domain logic stays
  testable without Qt.

## Why "press, then verify" instead of a fixed script

The manual's sequences have narrow timing windows (some prompts show for as
little as ~2 seconds) and the controller has no way to report success back
except by changing what's on its own display. Treating vision as the only
source of truth for "what state is the controller actually in" — rather than
assuming a press worked because we sent it — is what makes it safe to
automate a kiln we don't control electrically: worst case, the sequencer
gets confused, bails to `IdLE` via the 4-second `ENTER` hold, and a human
notices nothing started. That's a far better failure mode than a
mis-sequenced press silently landing on the wrong prompt.

## Testability

`kiln/protocol.py` and `kiln/profiles.py` have no I/O and are fully unit
tested (`python/tests/`). `kiln/sequencer.py` is tested against
`MockBridge` + `MockVision` fed a scripted state sequence, so the whole
"what buttons do we press for profile X" logic can be verified without a
kiln, a camera, or an Uno Q at all — see `python/tests/test_protocol.py` for
the pattern to extend.
