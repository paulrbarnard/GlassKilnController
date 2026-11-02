#!/bin/sh
# Kiosk launcher, run from the XFCE autostart entry (see docs/hardware.md).
#
# The app can take minutes to come up after boot, so Chromium opens a local
# "Starting..." page that polls the app and switches to it once it answers,
# instead of landing on a "can't reach this site" page that never retries.
DIR="$(cd "$(dirname "$0")" && pwd)"

# The Waveshare 8" DSI panel is portrait-native (800x1280) and mounted
# landscape: rotate it clockwise to 1280x800. Touch is rotated to match by
# scripts/99-glasskiln-touch-rotate.rules (in /etc/udev/rules.d/). Without the
# DSI panel, the old 7" USB-C panel: its EDID prefers 1920x1080, so force its
# native 1024x600.
if xrandr | grep -q "^DSI-1 connected"; then
  xrandr --output DSI-1 --rotate right
else
  xrandr --output DP-1 --mode 1024x600 --rotate normal
fi
xset s off
xset -dpms

exec chromium --kiosk --incognito --noerrdialogs --disable-infobars \
  --disable-session-crashed-bubble "file://$DIR/kiosk-starting.html"
