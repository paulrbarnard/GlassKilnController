#!/usr/bin/env bash
# Copy the app to the board and (re)start it.
#
#   scripts/deploy.sh              copy + restart the App Lab app
#   scripts/deploy.sh --copy-only  copy files, restart nothing
#   scripts/deploy.sh --homekit    ...and rebuild/restart the HomeKit companion
#
# HOST defaults to the ssh alias "steffi" (arduino@192.168.0.74).
# The board has no rsync, so this streams a tar over ssh. User-created
# profiles (python/profiles/user/) and app settings (python/state/) live
# only on the board and are never overwritten, nor are the HomeKit pairing
# keys (homekit/state/). The first deploy also makes this the default app, so it
# starts on boot (see docs/hardware.md).
set -euo pipefail

HOST="${HOST:-steffi}"
REMOTE_DIR="ArduinoApps/glass-kiln-controller"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

restart=1
homekit=0
for arg in "$@"; do
  case "$arg" in
    --copy-only) restart=0 ;;
    --homekit) homekit=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> copying to $HOST:~/$REMOTE_DIR"
COPYFILE_DISABLE=1 tar -C "$ROOT" -czf - \
  --exclude .git --exclude .venv --exclude venv --exclude .cache \
  --exclude __pycache__ --exclude .pytest_cache --exclude .claude \
  --exclude .DS_Store --exclude sketch/build \
  --exclude python/profiles/user --exclude python/state --exclude homekit/state \
  . | ssh "$HOST" "mkdir -p ~/$REMOTE_DIR && tar -xzf - -C ~/$REMOTE_DIR --no-same-owner 2>/dev/null"

if [[ $restart == 1 ]]; then
  echo "==> restarting app (compiles and flashes the sketch too)"
  # Not `app restart`: on Steffi it stops the app, then fails with "Is
  # Running" and leaves it stopped. Stop and start separately instead.
  ssh "$HOST" "arduino-app-cli app stop ~/$REMOTE_DIR || true; arduino-app-cli app start ~/$REMOTE_DIR"
  ssh "$HOST" "arduino-app-cli properties set default ~/$REMOTE_DIR"
  # The kiosk keeps the page it loaded at boot, so it would go on running
  # the old JavaScript. Relaunch it (if it's running) to load the new one.
  echo "==> reloading the kiosk browser"
  ssh "$HOST" "pgrep -x chromium >/dev/null && { pkill -x chromium; sleep 2; \
    DISPLAY=:0 XAUTHORITY=/home/arduino/.Xauthority setsid nohup sh ~/$REMOTE_DIR/scripts/kiosk.sh \
    >/tmp/kiosk.log 2>&1 </dev/null & } ; true"
fi

echo "==> done: http://192.168.0.74:7000"

if [[ $homekit == 1 ]]; then
  # A separate docker compose project next to the App Lab app (it needs host
  # networking for HomeKit's mDNS); see homekit/hap_kiln.py.
  echo "==> rebuilding the HomeKit companion"
  ssh "$HOST" "cd ~/$REMOTE_DIR/homekit && docker compose up -d --build"
fi
