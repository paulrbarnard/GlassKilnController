# Hardware notes

This is a planning doc, not a finished BOM — fill in real values as the
physical build happens and keep `python/config/*.yaml` in sync.

## Overlay panel

The GM LT control panel (see manual p.1) is a small rectangle: a red 7-seg
display and 3 round buttons (ENTER, UP, DOWN) in a row underneath it. The
overlay needs to:

- Mount rigidly relative to the kiln's existing panel (button positions
  must not drift relative to the solenoid actuators once calibrated).
- Leave the existing panel's labels/display visible to a human, since a
  person should always be able to see what the kiln itself thinks is
  happening, not just what our UI says.
- Not block ventilation or the kiln's own peephole/safety features.

## Solenoid button actuators (x3)

One per key: `ENTER`, `UP`, `DOWN`. Each is a 12V mini push-pull solenoid
(the build uses Amazon B0CRKS9M19) whose plunger sits over its button. They
replaced the original SG90 micro servos in October 2026.

- At rest the plunger is retracted and clear of the button (no contact,
  no accidental holds). The solenoids are mounted on the front of the
  controller box, which stays cool, so a PLA bracket is fine.
- On command, the plunger presses the button for a controlled dwell, then
  releases. Short press ≈ a quick tap; long press (the 4s "hold" actions)
  holds the button for the full duration.

**Driver:** a MonkMakes **Mosfetti** 4-channel MOSFET board (3.3V logic,
flyback diodes on board, 2A per channel, 2.5A total, 16V max) with a
separate 12V supply. Wiring:

| Key | UNO Q pin | Mosfetti channel |
|---|---|---|
| ENTER | D9 | 1 |
| UP | D10 | 2 |
| DOWN | D11 | 3 |
| (spare) | D12 | 4 |

The fourth channel is wired to D12 so the harness is reusable, but nothing
uses it: the sketch holds D12 LOW. UNO Q GND, Mosfetti GND and the 12V supply's
negative must all be common, or the gates have no reference. Each solenoid
goes between its channel output and +12V.

**Drive scheme** (`sketch/sketch.ino`): every press starts with a short
full-power kick to pull the plunger in, then drops to a PWM hold duty that
keeps it there without overheating the coil. This board's PWM pins are
hardware-fixed at 500Hz, which suits a solenoid fine. Only one solenoid is
ever energised: all outputs are switched off before and after each press,
and a press longer than 8s is rejected. All gates are driven LOW in
`setup()` so nothing fires while the Bridge comes up. Before the sketch is
running (power-up, reflash) the pins aren't driven, so check that the
Mosfetti holds its gates off when its inputs float, or add a pull-down per
channel.

Drive settings (per-key `kick_ms` and `hold_duty`) live in
`python/config/solenoid_calibration.yaml` and are pushed to the sketch at
startup. Tune them with the controller **unpowered** first (the plunger
pulls in reliably on the kick, and the hold duty is the lowest that still
keeps the button down), then with the controller powered but the kiln not
firing (verify the right button is actually being pressed, using View
Segment/Review to sanity check against a known key sequence).

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

# 2. Autostart Chromium in kiosk mode once that session starts, via
# scripts/kiosk.sh (deployed with the app). The app can take minutes to
# come up after boot, so the browser opens scripts/kiosk-starting.html,
# which polls /api/status and switches to the app once it answers;
# pointing Chromium straight at localhost:7000 leaves it stuck on a
# "can't reach this site" page. The 7" panel's native mode is 1024x600,
# but its EDID prefers 1920x1080, which renders the UI at about half
# size, so kiosk.sh forces the mode; the CSS is sized for 1024x600. The
# panel is mounted landscape; the rotation is still set explicitly
# (xrandr is a runtime-only setting, so a one-off change by hand doesn't
# survive a reconnect/reboot). For a portrait mount use `--rotate left`
# in kiosk.sh instead — assets/style.css switches to a 2-column grid
# automatically.
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/glasskiln-kiosk.desktop <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Glass Kiln Kiosk
Exec=sh /home/arduino/ArduinoApps/glass-kiln-controller/scripts/kiosk.sh
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
reboot: `pkill -x chromium`, then re-run `scripts/kiosk.sh` from a shell
with `DISPLAY=:0` and `XAUTHORITY=/home/arduino/.Xauthority`
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
a USB mouse works as the pointer, and `assets/osk.js` provides an on-screen
QWERTY keyboard (text fields) and numeric keypad (number fields) that pop up
when a field is focused — needed with touch too, since the kiosk has no
OS-level virtual keyboard.

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
