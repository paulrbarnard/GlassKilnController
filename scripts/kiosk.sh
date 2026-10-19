#!/bin/sh
# Kiosk launcher, run from the XFCE autostart entry (see docs/hardware.md).
#
# The app can take minutes to come up after boot, so Chromium opens a local
# "Starting..." page that polls the app and switches to it once it answers,
# instead of landing on a "can't reach this site" page that never retries.
DIR="$(cd "$(dirname "$0")" && pwd)"

# The 7" panel's EDID prefers 1920x1080; force its native 1024x600.
xrandr --output DP-1 --mode 1024x600 --rotate normal
xset s off
xset -dpms

exec chromium --kiosk --incognito --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble "file://$DIR/kiosk-starting.html"
