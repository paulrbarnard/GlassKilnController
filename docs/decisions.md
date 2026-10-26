# Design decisions

What was decided, and why. The commit history has the detail; this is the
short version for anyone (including future us) picking the project up.
Dates are when the decision was made, October 2026.

## The kiln and the module

- **Operate the GM LT optically and mechanically, never electrically.** The
  kiln's own controller, thermocouple and safety relays stay unmodified and
  in charge; the app only presses buttons and reads the display (see the
  README).
- **The module covers the whole front panel** (from 2026-10-25). Nobody can
  use the kiln's buttons or see its display any more, so the app has to show
  everything a person would have looked at: the temperature, whether it's
  safe to open, and what the controller is doing.
- **Solenoids, not servos**, for the buttons. The board's PWM is fixed at
  500 Hz, which the Servo library can't use. One solenoid burnt out on long
  scroll holds (rated 5% duty in 60 s at 12 V), so: a 4:1 lever per button,
  a full-power kick then a reduced hold for every press, and a heat budget
  per coil in both the sketch and the app. Settings live in
  `python/config/solenoid_calibration.yaml`.
- **A browser kiosk for the UI**, not a native toolkit (PySide6 can't run
  in the App Lab container). The same page serves the touchscreen and any
  browser on the network.
- **One controller action at a time.** Two sequences interleaving their
  presses (a double-tapped button) left the controller in the wrong menu.

## Units and display

- **The app chooses °C or °F and sets the controller to match**, default
  °C. The unit buttons are disabled for the unit already in use.
- **The last digit's decimal point is the °C indicator**; °F has none. A
  reading with a point between its digits is a time (H.MM), never a
  temperature: after a firing the display cycles CPLt, the firing time and
  the temperature, and "0.08" was once read as 0.08 °C.

## Profiles and kiln slots

- **Up to 12 custom profiles, on one screen**, so there's no scrolling or
  second page. The factory Casting program is just another PRO profile and
  counts towards the 12. When the app is full, New, Duplicate and Import ask
  which profile to replace (least used first) rather than refusing.
- **The app manages the kiln's five PRO slots itself, with as few decisions
  for the user as possible.** Starting a profile that isn't on the kiln
  loads it into the least-used slot (unknown, then stale, then least fired,
  then least recently fired). Usage is tracked per profile, so reprogramming
  is rare. The slot is checked against the profile before every start.
- **Profiles are named for what they are, not where they live.** Imports are
  "Imported from slot N" and each import is a new profile (a fixed id per
  slot let a later import overwrite an edited one). "Kiln slot N" was
  misleading, since a profile moves between slots as they're reused.

## Programming values

- **Watched holds, then single checked taps.** A held UP/DOWN accelerates
  (to hundreds of steps a second), so the app watches the display and
  releases just short of the target, then taps the rest.
- **Expect the controller to miss or double count, and correct rather than
  give up** (with a limit of 24 holds and taps per value). The GM LT steps
  2-3 for one tap (one beep) when presses come close together, also when
  programming by hand. Taps are 2 s apart, and the first is 5 s after a
  hold.
- **The timings are fine as they are.** A 4-segment program takes about 4
  minutes. That's acceptable latitude, because with slot management
  reprogramming is rare.

## Failures and stopping

- **Return the controller to IdLE before every sequence, and after a
  failure**, rather than relying on the user. If it can't get back to IdLE,
  the app says to power-cycle the kiln. A failed write says "Programming
  failed", with Try again.
- **STOP goes straight back to the home screen** once the controller
  confirms it. A firing that completes stays on the firing screen until
  Done.

## Temperature, cooling and safety

- **The home screen always shows the kiln temperature.** It's red with
  "Warning: Kiln hot. Open with caution" at 40 °C / 104 °F and above, green
  with "Safe to open" below, and "Off" when the display is dark.
- **The cooling estimate is informative, not exact.** It fits Newton's law
  of cooling to the last 45 minutes of readings. Room temperature is the
  lowest cool reading of the last day. The estimate counts down to when the
  display will read 39 (about 39.5 °C), and only a real reading ever says
  "Safe to open". On a 51 → 39 °C test it came out about 1.45x too short; it
  was left as it is, and each cooling is logged (`state/cooling/`) in case
  it's worth tuning against real firings.
- **The cooling pop-up appears on the firing screen** when a firing ends
  with the kiln at 40 °C or above, and clears itself once the kiln is below
  40 °C. The home screen shows the estimate on its own, for when the user
  has left the firing screen.

## Following the kiln from elsewhere

- **Status page** (`?view`): read-only status (Idle / Programming / Firing /
  Busy / Kiln off, the temperature, firing progress, the cooling estimate),
  not a mirror of the kiosk screen. What matters is the state, not the
  kiosk's screen. The full app is still reachable on the home network
  without `?view`; there's no login.
- **Apple Home shows the kiln as a heater**: "Heating to <peak>", "Cooling
  to 40°", otherwise Off. Home's heater tile has no idle state, so an idle
  kiln and a kiln that's switched off both show Off; that's fine.
  HomeKit has no read-only heater, so changes made in Home are put straight
  back. There's also a "Kiln safe to open" occupancy sensor for automations.
- **Notifications come from the app through Pushover, not Apple Home.**
  Home only notifies when an occupancy sensor is detected. It words the
  notification itself ("Workshop occupancy detected") and limits it to
  who-is-home conditions, and an automation workaround didn't notify
  either. Pushover sends Firing started (with an expected finish), Firing
  complete / stopped / ended, Kiln error, Kiln programming failed and Kiln
  safe to open. The titles don't carry profile names, which meant nothing in
  a notification. It uses its own "Kiln" application token; the keys are
  only on the board.

## Working on it

- **Don't deploy during a firing.** A restart loses the firing monitor and
  the cooling history. Changes to the page only (`assets/`) can be copied
  across without restarting the app.
- **Keys and pairing state never go in git**: `python/state/` (Pushover
  keys, settings, slot map) and `homekit/state/` stay on the board. Check
  for Wi-Fi details and keys before pushing.

## Waiting on

- **The Waveshare 8" DSI touchscreen (8-DSI-TOUCH-A).** It's ordered; the
  7" HDMI panel stays until it arrives. Enabling DSI in the device tree has
  broken the camera's, so it needs a plan before it goes in.
