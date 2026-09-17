# Hardware notes

This is a planning doc, not a finished BOM — fill in real values as the
physical build happens and keep `python/config/*.yaml` in sync.

## Overlay panel

The GM LT control panel (see manual p.1) is a small rectangle: a red 7-seg
display and 3 round buttons (ENTER, UP, DOWN) in a row underneath it. The
overlay needs to:

- Mount rigidly relative to the kiln's existing panel (button positions
  must not drift relative to the servo actuators once calibrated).
- Leave the existing panel's labels/display visible to a human, since a
  person should always be able to see what the kiln itself thinks is
  happening, not just what our UI says.
- Not block ventilation or the kiln's own peephole/safety features.

## Servo button actuators (x3)

One per key: `ENTER`, `UP`, `DOWN`. Each is a small lever/plunger driven by
a micro servo (e.g. SG90-class) that:

- At rest, sits clear of the button (no contact, no accidental holds).
- On command, presses the button for a controlled dwell time, then
  retracts. Short press ≈ a quick tap; long press (4s "hold" actions) needs
  the servo to hold the button down for the full duration.
- Needs enough repeatable travel/force to reliably actuate the button
  without over-driving it (these are the kiln's real controls — don't stall
  the servo against the panel).

**Driven directly off the Uno Q's MCU pins — no external servo driver
board needed.** This wasn't obvious going in: this board's Arduino-level
PWM pins are hardware-fixed at 500Hz in its devicetree (checked directly:
`arduino_uno_q_stm32u585xx.overlay`, every `pwms` entry is `PWM_HZ(500)`),
which is why the classic `Servo` library doesn't even compile for it — a
hobby servo needs 50Hz and that period isn't reachable through
`analogWrite()` here. The board's devicetree does provision a *separate*
hardware counter timer for exactly this purpose (`counter_servo`, aka
`TIM2`), and the `RoboServo` library (confirmed: compiles and links against
`arduino:zephyr:unoq`, `library.properties` explicitly lists both `zephyr`
and "UNO Q") drives the 3 servos off that counter with the same
`attach()`/`write()` API as classic `Servo`. See `sketch/sketch.ino` and
`sketch/sketch.yaml` (RoboServo has to be explicitly declared in the
sketch.yaml profile's `libraries:` list — it does not auto-resolve from the
`#include` alone, unlike `Arduino_RouterBridge`).

Calibration values (per-servo neutral/press angle, press dwell ms) live in
`python/config/servo_calibration.yaml`. Do this calibration with the kiln
**unpowered** first (verify mechanical actuation), then with the controller
powered but kiln not firing (verify the right button is actually being
pressed, using View Segment/Review to sanity check against a known key
sequence).

## Camera

Framed on the 7-segment display only (tight crop reduces false reads from
glare/reflection). Needs:

- Fixed mount, fixed focus, consistent framing — `vision.py`'s segment
  decoder assumes a stable ROI (`python/config/camera_calibration.yaml`).
- Lighting that doesn't wash out the red LEDs or create glare across them;
  the kiln's own heat/shimmer near the panel at high temperature may also
  be worth testing against, since the display should stay readable
  regardless of chamber temperature (it's on the control panel, not the
  chamber, but heat and vibration from the kiln body are still a factor).
- A view that doesn't require the overlay panel's own body to cast a
  shadow on the display.

Calibration procedure (see `python/kiln/vision.py` `TODO`s):
1. Capture a reference frame per known display string (use the manual's
   vocabulary in `docs/controller_protocol.md` as the checklist — every
   token that can appear should have at least one reference sample).
2. Determine per-digit ROI boxes (7-segment displays are fixed-pitch; once
   framing is fixed this is a one-time measurement).
   Persist ROI boxes + reference thresholds in `camera_calibration.yaml`.

## Touchscreen

8" touchscreen, driven by the Uno Q's Linux side. Confirm which interface
(HDMI+USB-touch vs. DSI/MIPI) before finalizing mounting — affects cabling
route through the overlay enclosure. PySide6 (`python/ui/`) runs full-screen
on this display; no separate kiosk browser needed.

## Safety

None of the above changes the kiln's own safety systems (relays,
thermocouple, mechanical lid switch if present) — see manual "Be Safe" (p.5)
and Warranty & Repairs (p.7). The overlay must not:

- Obstruct the kiln's ventilation or the operator's ability to see/reach
  the kiln directly.
- Prevent a human from opening the lid or power-cycling the kiln manually
  at any time (the overlay is not on the power path).
- Rely on the automation to prevent an overfire — that's the kiln
  controller's own job (`Err1`/`ErrD`/thermocouple failure handling,
  manual p.30); the sequencer's job is only to *navigate menus*, never to
  second-guess the kiln's own temperature control.
