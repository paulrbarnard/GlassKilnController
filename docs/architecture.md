# Architecture

Everything in this document is confirmed by deploying to and running on
real hardware (an Uno Q named "Steffi"), not just read from documentation —
see the git history around the "web_ui brick" and "RoboServo" commits for
the trail of live tests (curl round trips, real compiles, a real RPC call
into the sketch) that pinned down each API surface below.

## Split across the Uno Q

The Arduino UNO Q pairs a real-time STM32 MCU ("sketch side") with a Linux
MPU ("Python side"), connected over an internal RPC bridge (`arduino-router`,
MessagePack-RPC over a Unix domain socket, exposed as `Bridge` on both
sides). App Lab projects follow a fixed layout: `python/main.py` and
`sketch/sketch.ino` are found by path convention, never declared in
`app.yaml`. `app.yaml` itself only carries `name`/`icon`/`ports`/`bricks` —
bricks are App Lab's pre-built service modules (this app uses `web_ui`, an
HTML/JS+WebSocket server); declaring one in `bricks:` is what makes
`arduino.app_bricks.<name>` importable, nothing else needs to reference it.
App Lab composes the actual container per-app from `app.yaml` plus whatever
each brick needs bind-mounted:

```
app.yaml            # name, icon, ports, bricks: [arduino:web_ui]
sketch/
  sketch.ino         # MCU: owns the 3 servos (RoboServo), nothing else
  sketch.yaml         # arduino-cli sketch project file (platform + libraries)
python/
  main.py             # MPU: brings up Bridge + WebUI, registers routes
  requirements.txt    # extra deps beyond the App Lab base image
  kiln/                # domain logic — no UI framework, no hardware I/O of its own
  web/                  # REST/WebSocket route wiring, talks to kiln/ only
assets/               # static frontend served by the web_ui brick
  index.html, style.css, app.js, diagrams.js
  libs/                 # socket.io + the brick's WebUI JS wrapper (vendored)
```

**Everything that decides what to do lives in Python.** The sketch is
deliberately dumb: it exposes `press_button(which, hold_ms)`,
`calibrate_servo(which, rest, press)`, and `ping()`, and does nothing else.
This keeps the real-time side trivial to verify (three servos, three
functions) and keeps all the interesting logic — protocol sequencing,
vision, profile authoring — in Python where it's easier to test, version,
and iterate without re-flashing the MCU.

```
┌─────────────────────────── Linux / MPU side ───────────────────────────┐
│                                                                          │
│  browser (touchscreen)         python/web/api.py        kiln/          │
│  assets/index.html   HTTP/WS   ┌───────────┐  Profile   ┌───────────┐  │
│  + app.js + diagrams.js ──────▶│  routes   │───────────▶│ Sequencer │  │
│  (fetch + socket.io)   ◀───────│ (FastAPI  │◀─progress──│  (state   │  │
│                         push   │  via the  │  /state    │  machine) │  │
│                                │ web_ui    │             └─────┬─────┘  │
│                                │  brick)   │                   │ reads  │
│                                └───────────┘                   ▼        │
│                                       │                 ┌────────────┐  button
│                                       │ reads/writes    │   Bridge   │──presses─▶ sketch/
│                                       ▼                 │   client   │  (RPC)     (STM32,
│                                ┌────────────┐           └────────────┘   RoboServo
│                                │  Profiles  │                  ▲          moves
│                                │(JSON, lib+ │           ┌────────────┐    servo)
│                                │  user)     │           │   Vision   │◀─frames─┐
│                                └────────────┘           │ (7-seg OCR)│         │
│                                                          └────────────┘      ┌────┐
│                                                                               │cam │
│                                                                               └────┘
└──────────────────────────────────────────────────────────────────────┘
```

## Module responsibilities

- **`kiln/protocol.py`** — static reference data and enums transcribed from
  `docs/controller_protocol.md`: `Key`, `DisplayMessage`, `Mode`,
  `ProgrammingLevel`, the GlassFire rate/process tables, and the factory
  casting program. No behavior, no I/O — just the vocabulary everything else
  is built from. This is the file to update if a future manual revision
  changes anything.

- **`kiln/bridge.py`** — thin RPC client wrapping calls to the sketch's
  `press_button`/`calibrate_servo`/`ping`. Confirmed on real hardware that
  `arduino.app_utils.Bridge.call(name, *args)` on the Python side is
  **synchronous and returns the value directly** — unlike the sketch-side
  C++ `Bridge.call("name").result(var)` pattern, there's no `.result()`.
  Also defines `MockBridge`, which logs presses instead of moving servos,
  for developing/testing off the kiln.

- **`kiln/vision.py`** — camera capture + 7-segment decoding. Produces a
  `ControllerState` (raw display text, best-guess `DisplayMessage`, numeric
  value if applicable) from a frame. Uses `opencv-python-headless`, not
  plain `opencv-python` — confirmed on real hardware that the App Lab
  container lacks `libGL.so.1`, which regular opencv's GUI support needs
  and this app never uses anyway (see "Container is a stripped image"
  below). Camera ROI/ segment-thresholding is still a `TODO(hardware)`
  pending the physical build — see calibration notes in `docs/hardware.md`.
  `MockVision` lets the sequencer be developed/tested against a scripted
  sequence of states instead of a real camera.

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
  sequence rather than guessing. Surfaces progress back to the UI via a
  callback, and immediately halts and surfaces `Err1`/`ErrF`/`ErrD`/`FAIL`
  if vision ever reads one.

- **`web/api.py`** — `register_routes(ui, sequencer, library)` wires REST
  endpoints (`GET/POST/DELETE /api/profiles...`, `POST /api/run/{id}`,
  `/api/stop`, `/api/skip`, `/api/add_time`, `/api/add_temp`,
  `/api/settings/...`) and a `progress`/`error` WebSocket push, built on
  `arduino.app_bricks.web_ui.WebUI`. Confirmed on real hardware:
  `expose_api(method, path, handler)` is **FastAPI** underneath — a
  `{param}` path segment binds to a same-named handler argument, and a POST
  body arrives as a plain `dict` argument. `register_routes` takes a
  duck-typed `ui` (only needs `expose_api`/`send_message`), so it's tested
  with a fake double and no App Lab runtime (`python/tests/test_web_api.py`).
  Every route that triggers real controller interaction runs it on its own
  background thread — a firing can run for hours and a request handler must
  never block for that duration.

- **`assets/`** — the static frontend the `web_ui` brick serves. Plain
  JS/fetch/socket.io, no framework (five simple views don't need a build
  step). `diagrams.js` is a direct port of the ramp/hold/cool diagram math
  that used to live in a PySide6 `QPainter` widget — see "What didn't work"
  below for why that widget doesn't exist anymore.

## Container is a stripped image — two things this ruled out

The App Lab Python runtime is a container (`ghcr.io/arduino/app-bricks/
python-apps-base`), not a bare process, and it's deliberately minimal.
Confirmed on real hardware, in order:

1. **A native GUI toolkit doesn't work.** The original design used PySide6
   for the touchscreen UI. It installed fine (real aarch64 wheels), but
   failed at import: `libxkbcommon.so.0: cannot open shared object file`.
   Manually probing further (`apt-get install libxkbcommon0` inside the
   container) surfaced a whole missing GL/EGL/mesa/codec stack, several
   packages already "not going to be installed" before asking for
   anything — this image is deliberately stripped of the graphics stack a
   GUI toolkit needs, not missing one stray library. App Lab ships the
   `web_ui` brick precisely for this: HTML/JS served over HTTP, rendered by
   whatever's actually driving the touchscreen (outside this app's
   container), not a window this container has to draw itself.

2. **Camera access needs the `-headless` OpenCV wheel.** Same root cause,
   smaller blast radius: plain `opencv-python` also wants `libGL.so.1` for
   GUI/`imshow` support this app never uses. `opencv-python-headless` drops
   that dependency and imports cleanly — see `python/requirements.txt`.

## Servo PWM — a board-level constraint, not a library gap

The sketch originally used the classic `Servo` library; it doesn't compile
for this board's FQBN (`arduino:zephyr:unoq`) at all. Checked directly in
the board's devicetree overlay: every PWM-capable pin (D2–D13, D20/D21) is
hardware-fixed at 500Hz (`PWM_HZ(500)`), and a hobby servo needs 50Hz — that
period simply isn't reachable through `analogWrite()` on this board.

The fix isn't extra hardware: the devicetree also provisions a *separate*
hardware counter timer for exactly this (`counter_servo`), and the
`RoboServo` library (`library.properties` explicitly lists both `zephyr`
and "UNO Q") drives the 3 servos off that counter instead of the PWM
peripheral, bit-banging a real 50Hz signal with the same `attach()`/
`write()` API as classic `Servo`. One non-obvious thing about getting it to
compile: it does **not** auto-resolve from the sketch's `#include` the way
`Arduino_RouterBridge` does — it must be listed explicitly in
`sketch/sketch.yaml`'s profile `libraries:` (`RoboServo (1.2.0)`), or the
build fails to find it even with the library separately installed.

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
"what buttons do we press for profile X" logic — and now `web/api.py`'s
route logic, via a fake `ui` double — can be verified without a kiln, a
camera, or an Uno Q at all. 17 tests, all hardware-free; see
`python/tests/` for the pattern to extend.
