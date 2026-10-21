#!/usr/bin/env bash
# Read the kiln display from a Mac, through the app's own vision code on the
# board, and open the annotated picture. Arguments pass through to
# scripts/read_display.py (--loop N, --save LABEL).
#
#   scripts/read_display.sh
#   scripts/read_display.sh --save SLo
#   scripts/read_display.sh --pull      copy the board's reference frames into
#                                       python/tests/fixtures/
#
# HOST defaults to the ssh alias "steffi". Needs the app running and the
# current code deployed (scripts/deploy.sh).
set -euo pipefail

HOST="${HOST:-steffi}"
CONTAINER="glass-kiln-controller-main-1"
REMOTE_DIR="ArduinoApps/glass-kiln-controller"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

if [[ "${1:-}" == "--pull" ]]; then
  mkdir -p "$ROOT/python/tests/fixtures"
  scp -q "$HOST:$REMOTE_DIR/reference/*_roi.png" "$ROOT/python/tests/fixtures/"
  ls -l "$ROOT/python/tests/fixtures"
  exit 0
fi

ssh "$HOST" "docker exec $CONTAINER python /app/scripts/read_display.py $* && docker cp $CONTAINER:/tmp/read_display.jpg /tmp/read_display.jpg"
scp -q "$HOST:/tmp/read_display.jpg" /tmp/read_display.jpg
[[ "$(uname)" == "Darwin" ]] && open -a Preview /tmp/read_display.jpg
echo "annotated picture: /tmp/read_display.jpg"
