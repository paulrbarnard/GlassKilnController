#!/usr/bin/env bash
# Copy the app to the board and (re)start it.
#
#   scripts/deploy.sh              copy + restart the App Lab app
#   scripts/deploy.sh --copy-only  copy files, restart nothing
#
# HOST defaults to the ssh alias "steffi" (arduino@192.168.0.74).
# The board has no rsync, so this streams a tar over ssh. User-created
# profiles (python/profiles/user/) live only on the board and are never
# overwritten. The first deploy also makes this the default app, so it
# starts on boot (see docs/hardware.md).
set -euo pipefail

HOST="${HOST:-steffi}"
REMOTE_DIR="ArduinoApps/glass-kiln-controller"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

restart=1
for arg in "$@"; do
  case "$arg" in
    --copy-only) restart=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "==> copying to $HOST:~/$REMOTE_DIR"
COPYFILE_DISABLE=1 tar -C "$ROOT" -czf - \
  --exclude .git --exclude .venv --exclude venv --exclude .cache \
  --exclude __pycache__ --exclude .pytest_cache --exclude .claude \
  --exclude .DS_Store --exclude sketch/build \
  --exclude python/profiles/user \
  . | ssh "$HOST" "mkdir -p ~/$REMOTE_DIR && tar -xzf - -C ~/$REMOTE_DIR --no-same-owner 2>/dev/null"

if [[ $restart == 1 ]]; then
  echo "==> restarting app (compiles and flashes the sketch too)"
  ssh "$HOST" "arduino-app-cli app restart ~/$REMOTE_DIR"
  ssh "$HOST" "arduino-app-cli properties set default ~/$REMOTE_DIR"
fi

echo "==> done: http://192.168.0.74:7000"
