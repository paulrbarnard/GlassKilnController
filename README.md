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
  with three solenoid actuators, replaying the exact key sequences from
  the GM LT manual to select or build the desired program.

In short: the human picks a profile on a touchscreen; the Uno Q "operates"
the stock kiln controller on their behalf, watching its own button presses
land correctly via the camera before advancing.

## Hardware

- **Arduino UNO Q** — STM32 MCU (real-time sketch side) + Linux MPU (Python
  "brain" side), connected internally over the Bridge RPC.
- **8" touchscreen** (HDMI/USB or DSI, capacitive touch) — profile browser,
  diagrams, run/monitor screen. Driven by a browser pointed at the app's
  own web UI (port 7000) — a native GUI toolkit was tried first and
  confirmed unworkable in the App Lab container (see
  `docs/architecture.md`).
- **Camera** — framed on the GM LT's red 7-segment display, used to read
  controller state (`python/kiln/vision.py`).
- **3x 12V push-pull solenoids** — one per GM LT key (ENTER, UP, DOWN),
  mounted so each can press-and-release its button on command, driven from
  D9-D11 through a MonkMakes Mosfetti MOSFET board (`sketch/`).

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
sketch/                     Arduino sketch (MCU side): drives the 3 solenoids
python/                     App Lab Python app (Linux/MPU side)
  kiln/                     Domain logic: protocol tables, bridge client,
                             vision (7-seg reader), profiles, sequencer
  web/                      REST/WebSocket route wiring (web_ui brick), talks to kiln/ only
  profiles/library/         Built-in profiles transcribed from the GM LT manual
  profiles/user/            User-created custom Ramp & Hold profiles
  config/                   Solenoid/camera calibration (machine-specific, not
                             generic defaults — see docs/hardware.md)
  tests/                    Unit tests for kiln/ and web/ logic (no hardware needed)
assets/                     Static frontend served by the web_ui brick
  index.html, style.css, app.js, diagrams.js
  libs/                     Vendored socket.io + the brick's WebUI JS wrapper
hardware/                   Mechanical notes for the button actuators
app.yaml                    Arduino App Lab manifest (name/icon/ports/bricks)
```

## Status

Deployed and running end-to-end on real hardware (an Uno Q named "Steffi"):
sketch compiles and flashes, the Python side reaches it over the Bridge RPC
(`ping`/`press_button` both confirmed live), and the web UI serves real
profile data over its REST API. What's still pending the physical build:
camera-based 7-segment reading (`RealVision` is a deliberate stub — see
`TODO(hardware)` in `python/kiln/vision.py`) and solenoid mounting/tuning
(`python/config/solenoid_calibration.yaml`). See `docs/architecture.md` for
the full trail of what was confirmed on real hardware vs. still assumed,
including two dead ends worth knowing about before you touch this again:
PySide6 doesn't work in the App Lab container (missing graphics libs), and
this board's PWM pins are fixed at 500Hz so the classic `Servo` library
doesn't either — both documented there with what to use instead.

## Getting started

**On the Uno Q**, via App Lab or `arduino-app-cli app start <path>` — no
arguments needed, `python/main.py` finds everything by convention. From a
dev machine, `scripts/deploy.sh` copies the repo to the board (ssh alias
`steffi`; override with `HOST=`), restarts the app and sets it as the
default app so it starts on boot, without touching on-board user profiles. The
touchscreen (or any browser on the same network) reaches the UI at
`http://<board-ip>:7000`.

**Away from the kiln**, for developing/testing `kiln/` and `web/api.py`'s
route logic (the App Lab-only pieces — `arduino.app_utils`,
`arduino.app_bricks.web_ui` — aren't pip-installable off-device, so this
only proves construction + route registration, not a real HTTP server):

```bash
cd python
pip install -r requirements.txt
python main.py --mock-bridge --mock-vision
```

Real automated coverage of both layers lives in `python/tests/` (17 tests,
run with `pytest` from `python/`) — that's what to extend, not manual runs
of `main.py`.

## Key references

- `docs/controller_protocol.md` — the GM LT's menu tree, key sequences, and
  display vocabulary, transcribed from the operating manual page-by-page.
- `docs/architecture.md` — how the pieces (sketch, bridge, vision, sequencer,
  UI) fit together and the state machine that ties reality (camera) to intent
  (profile).
- `docs/hardware.md` — solenoid/camera mounting notes, wiring and calibration procedure.
