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

Driven by the Uno Q's Linux side over HDMI, via a plain Chromium browser in
kiosk mode pointed at the app's own `web_ui` server (`http://localhost:7000`
— see docs/architecture.md for why it's a browser and not a native GUI).
This is genuinely a normal XFCE desktop (`lightdm` + `xfce4-session`)
sitting behind it, not something App Lab wires up for you automatically —
confirmed on real hardware: with nothing configured, the screen just sits
at the lightdm login greeter forever. Kiosk behavior is bog-standard Linux
kiosk setup, unrelated to App Lab itself:

```bash
# 1. Auto-login the arduino user into the XFCE session on boot
sudo mkdir -p /etc/lightdm/lightdm.conf.d
sudo tee /etc/lightdm/lightdm.conf.d/50-glasskiln-autologin.conf <<'CONF'
[Seat:*]
autologin-user=arduino
autologin-user-timeout=0
autologin-session=xfce
CONF

# 2. Autostart Chromium in kiosk mode once that session starts
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/glasskiln-kiosk.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Glass Kiln Kiosk
Exec=sh -c 'xset s off; xset -dpms; chromium --kiosk --incognito --noerrdialogs --disable-infobars --disable-session-crashed-bubble http://localhost:7000'
X-GNOME-Autostart-enabled=true
DESKTOP

# 3. Make the App Lab app itself start on boot — steps 1-2 alone only
# launch a browser; without this the container never starts, and the
# kiosk boots straight into a "can't reach this site" page. Confirmed
# the hard way: this app has no auto-start by default, at all, even
# though it survives a plain "arduino-app-cli app restart" fine — that
# only ever restarts an already-running container, it says nothing
# about what happens after power is cut and it starts fully cold.
arduino-app-cli properties set default ~/ArduinoApps/glass-kiln-controller

sudo systemctl restart lightdm
```

`--incognito` isn't optional polish: without it, Chromium's on-disk HTTP
cache survives a kiosk relaunch, so after redeploying updated
`assets/*.js` you can restart the browser and still see the *previous*
version — confirmed the hard way during development. `xset s off`/`-dpms`
stop the screen blanking, since a kiosk display going to sleep mid-firing
defeats the point. To pick up a fresh deploy manually without a full
reboot: `pkill -x chromium`, then re-run the `chromium --kiosk ...` line
from a shell with `DISPLAY=:0` and `XAUTHORITY=/home/arduino/.Xauthority`
exported (that's what a real login session sets up for you automatically;
an SSH shell doesn't have them by default).

### Touch input

Confirmed non-functional on the panel currently in hand: it enumerates on
USB (`lsusb` shows `4348:55e0 WinChipHead`) but creates no input device at
all. That VID:PID with a **vendor-specific USB class** (not HID) is the
signature of a WCH CH55x/CH57x-series microcontroller sitting in its
**ISP/bootloader mode** rather than running touch-controller firmware —
confirmed this is consistent (same result after a fresh unplug/replug, not
a one-off fluke). This is a board-firmware problem, not a Linux driver gap:
check for a BOOT/ISP jumper or button on the touch controller board, or
treat it as needing a replacement/reflash from the seller. Until resolved,
`assets/keyboard.js`'s number/letter key-hint system + arrow-key grid
navigation in the profile editor (`assets/app.js`) are the interim input
method (see
`docs/architecture.md`) — genuinely useful as a permanent accessibility
fallback too, not just a workaround, so there's no strong reason to rip it
out once touch works.

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
