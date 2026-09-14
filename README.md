# Glass Kiln Controller

An Arduino UNO Q "overlay" controller for a Skutt HotStart Pro / FireBox 14 /
FireBox 8 kiln running the GlassMaster LT (GM LT) controller.

The GM LT is a capable controller, but its UI is three buttons and a 7-segment
LED display, driven by long menu sequences (see `docs/controller_protocol.md`).
This project sits physically on top of the kiln's existing control panel and:

- Shows firing profiles (Full Fuse / Tack Fuse / Slump / custom Ramp & Hold)
  as plain-language ramp/hold/cool diagrams on an 8" touchscreen.
- Reads the GM LT's 7-segment display via a camera pointed at it, so it always
  knows the controller's actual state (mode, segment, temperature, errors).
- Presses the GM LT's three physical buttons (ENTER/START/STOP, UP, DOWN)
  with three servo-driven actuators, replaying the exact key sequences from
  the GM LT manual to select or build the desired program.

In short: the human picks a profile on a touchscreen; the Uno Q "operates"
the stock kiln controller on their behalf, watching its own button presses
land correctly via the camera before advancing.

## Hardware

- **Arduino UNO Q** — STM32 MCU (real-time sketch side) + Linux MPU (Python
  "brain" side), connected internally over the Bridge RPC.
- **8" touchscreen** (HDMI/USB or DSI, capacitive touch) — profile browser,
  diagrams, run/monitor screen.
- **Camera** — framed on the GM LT's red 7-segment display, used to read
  controller state (`python/kiln/vision.py`).
- **3x servo button actuators** — one per GM LT key (ENTER, UP, DOWN),
  mounted so each can press-and-release its button on command (`sketch/`).

## Why not talk to the kiln electrically instead?

Possible in principle (see the GM LT wiring diagram in the manual), but it
would mean modifying/interposing on a mains-connected, high-temperature
appliance's safety-rated controller. Reading the display optically and
pressing the buttons mechanically keeps the kiln's own controller,
thermocouple, and safety relays completely unmodified and in the loop —
this project is an "extra pair of hands," not a replacement brain.

## Repo layout

```
docs/                       Reference material (protocol, hardware, architecture)
sketch/                     Arduino sketch (MCU side): drives the 3 servos
python/                     App Lab Python app (Linux/MPU side)
  kiln/                     Domain logic: protocol tables, bridge client,
                             vision (7-seg reader), profiles, sequencer
  ui/                       PySide6 touchscreen UI
  profiles/library/         Built-in profiles transcribed from the GM LT manual
  profiles/user/            User-created custom Ramp & Hold profiles
  config/                   Servo/camera calibration (machine-specific, not
                             generic defaults — see docs/hardware.md)
  tests/                    Unit tests for protocol/profile logic (no hardware needed)
hardware/                   Mechanical notes for the button actuators
app.yaml                    Arduino App Lab manifest pairing sketch/ + python/
```

## Status

This is the initial framework: real protocol/profile data transcribed from
the manual, a working (mock-able) sequencer and Bridge client, and a runnable
PySide6 UI skeleton with a live ramp/hold/cool diagram widget. Camera-based
7-segment reading and servo calibration are stubbed pending the physical
build — see `TODO` markers in `python/kiln/vision.py` and
`python/config/*.yaml`.

## Getting started (once deployed to the Uno Q)

```bash
cd python
pip install -r requirements.txt
python main.py
```

`main.py` starts the Bridge connection to the sketch and launches the Qt UI.
During development away from the kiln, run with `--mock-bridge` /
`--mock-vision` (see `main.py`) to exercise the UI and sequencer without
real hardware attached.

## Key references

- `docs/controller_protocol.md` — the GM LT's menu tree, key sequences, and
  display vocabulary, transcribed from the operating manual page-by-page.
- `docs/architecture.md` — how the pieces (sketch, bridge, vision, sequencer,
  UI) fit together and the state machine that ties reality (camera) to intent
  (profile).
- `docs/hardware.md` — servo/camera mounting notes and calibration procedure.
