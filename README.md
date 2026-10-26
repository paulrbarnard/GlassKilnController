# Glass Kiln Controller

An Arduino UNO Q "overlay" controller for a Skutt HotStart Pro / FireBox 14 /
FireBox 8 kiln running the GlassMaster LT (GM LT) controller.

The GM LT is a capable controller, but its UI is three buttons and a 7-segment
LED display, driven by long menu sequences (see `docs/controller_protocol.md`).
This project sits physically on top of the kiln's existing control panel and:

- Shows firing profiles (Full Fuse / Tack Fuse / Slump / custom Ramp & Hold)
  as plain-language ramp/hold/cool diagrams on a touchscreen.
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
- **Touchscreen** — profile browser, diagrams, run/monitor screen, shown by
  a kiosk Chromium pointed at the app's own web UI (port 7000); a native
  GUI toolkit was tried first and confirmed unworkable in the App Lab
  container (see `docs/architecture.md`). Currently a 7" 1024x600 HDMI
  panel; a Waveshare 8" DSI panel (8-DSI-TOUCH-A) is on order to replace it.
- **Camera** — a Pi Camera V2 (IMX219) on the Media Carrier, 105 mm from the
  GM LT's red 7-segment display, refocused for that distance; read by a
  segment decoder (`python/kiln/vision.py`,
  `python/config/camera_calibration.yaml`).
- **3x 12V push-pull solenoids** — one per GM LT key (ENTER, UP, DOWN), each
  pressing its button through a 4:1 lever with an adjustable finger, driven
  from D9-D11 through a MonkMakes Mosfetti MOSFET board (`sketch/`). They
  are rated 5% duty at 12 V, so every press is a short full-power kick then
  a reduced PWM hold, and both the sketch and the app keep a heat budget per
  coil. The module covers the whole front panel: the kiln is operated only
  through the app.

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

Working end to end on the kiln (an Uno Q named "Steffi"), confirmed
October 2026:

- **Reading the controller**: the camera decodes the display, including
  the glyphs the GM LT uses for letters, the °C indicator (the last digit's
  decimal point) and the IdLE/temperature alternation at rest.
- **Units**: the app chooses °C or °F and sets the GM LT to match
  (default °C).
- **Firing**: built-in GlassFire profiles and custom Ramp & Hold profiles
  start from the touchscreen; the firing screen shows live progress and a
  temperature trace, and STOP is verified against the display. Only one
  controller action runs at a time.
- **Kiln programs**: the GM LT's five PRO slots can be read, shown as
  diagrams, imported as app profiles, and written from app profiles. Every
  write is read back and only counts if it matches.
- **Slot management**: the app keeps up to 12 custom profiles (one screen,
  the factory Casting program included; delete one to make room) and maps
  them onto the five PRO slots by itself. A profile that isn't on the kiln
  replaces the least-used one (unknown, then stale, then least fired, then
  least recently fired), so reprogramming is rare. The slot is checked
  before every start (`python/kiln/slots.py`).
- **Value entry**: values are scrolled with held UP/DOWN presses watched by
  the camera and released just short of the target, then finished with
  single checked taps. The GM LT sometimes steps 2-3 for one tap when
  presses come close together, so taps are 2 s apart and the first comes
  5 s after a hold; anything that still lands off target is corrected (see
  `python/kiln/sequencer.py`). A 4-segment program takes about 4 minutes.
- **Kiln temperature**: the module covers the kiln's display, so the home
  screen shows the temperature read from it whenever nothing else is
  running: red with "Warning: Kiln hot. Open with caution" at 40 °C /
  104 °F and above, green with "Safe to open" below, "Off" when the display
  is dark. After a firing, a hot kiln's cooling is fitted to Newton's law to
  estimate when it will read below 40 °C (shown on the home screen and in a
  pop-up on the finished firing screen, revised with every reading;
  `python/kiln/kiln_temp.py`).
- **Status page**: `http://<board>:7000/?view` shows the same status
  read-only on any device on the home network (Idle / Programming / Firing
  / Busy / Kiln off, the firing screen during a firing, the temperature and
  cooling estimate). It has no controls; the full app at the same address
  without `?view` does.
- **Apple Home**: a HomeKit companion (`homekit/`, its own docker compose
  project with host networking, since App Lab containers can't do mDNS)
  shows the kiln as a heater: Heating while firing, Cooling after a firing
  while at or above 40 °C, Idle, or Off. It's read-only (changes made in
  Home are put back). A "Kiln hot" occupancy sensor can notify when the
  kiln is safe to open (turn on its activity notifications in Home
  Settings). `scripts/deploy.sh --homekit` rebuilds it; the pairing keys
  stay in `homekit/state/` on the board.
- **Recovery**: a controller left in a menu is returned to IdLE before a
  sequence starts, and a failed write backs out to IdLE and says so (with
  Try again). If the controller can't be brought back, the app asks for the
  kiln to be power-cycled.

Not yet done: °F value entry on the kiln, the MGF override flow, and the
switch to the DSI display (enabling DSI in the device tree currently breaks
the camera's, so it needs a plan). See `docs/architecture.md` for what was
confirmed on hardware and the dead ends: PySide6 doesn't work in the App Lab
container, and this board's PWM pins are fixed at 500 Hz, so the `Servo`
library doesn't either (the original servo actuators were replaced by
solenoids).

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

Real automated coverage of both layers lives in `python/tests/` (146 tests,
run with `pytest` from `python/`) — that's what to extend, not manual runs
of `main.py`.

**On-board tools** in `scripts/`, run inside the app container (`docker exec
glass-kiln-controller-main-1 python /app/scripts/...`) with the controller
at IdLE:

- `read_display.sh` / `focus.sh` — check what the camera reads; focus the lens
- `solenoid_test.py` — find the lowest working tap and hold duties
- `scroll_profile.py` — record how a held UP/DOWN accelerates (the
  `SCROLL_CURVE` source)
- `tap_test.py` — measure steps per tap against tap spacing, after holds,
  and across a range of values
- `set_value_test.py` — run the app's own value entry by hand, every press
  and reading logged
- `step.py` — press one key and record what the display does

## Key references

- `docs/controller_protocol.md` — the GM LT's menu tree, key sequences, and
  display vocabulary, transcribed from the operating manual page-by-page.
- `docs/architecture.md` — how the pieces (sketch, bridge, vision, sequencer,
  UI) fit together and the state machine that ties reality (camera) to intent
  (profile).
- `docs/hardware.md` — solenoid/camera mounting notes, wiring and calibration procedure.
