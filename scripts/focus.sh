#!/usr/bin/env bash
# Live focus aid for the display camera: captures a frame on the board
# every couple of seconds and prints a sharpness score for the lit digits.
# Turn the lens slowly and stop at the highest score. Ctrl-C to stop.
#
#   scripts/focus.sh            score only
#   scripts/focus.sh --save     also copy each frame to /tmp/focus.jpg
#
# HOST defaults to the ssh alias "steffi"; COUNT=n stops after n frames.
# Needs the app running (the score is computed in its container, which has
# OpenCV) and the camera detected at boot (`cam -l` on the board lists it).
set -euo pipefail

HOST="${HOST:-steffi}"
CONTAINER="glass-kiln-controller-main-1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COUNT="${COUNT:-0}"
save=0
[[ "${1:-}" == "--save" ]] && save=1

scp -q "$ROOT/scripts/focus_score.py" "$HOST:/tmp/focus_score.py"
ssh "$HOST" "docker cp /tmp/focus_score.py $CONTAINER:/tmp/focus_score.py"

best=0
n=0
echo "score    (higher is sharper; Ctrl-C to stop)"
while true; do
  # Grab a few frames so auto-exposure settles; keep the last one.
  line=$(ssh "$HOST" "rm -f /tmp/focus*.ppm
    timeout 20 cam -c1 --capture=6 --stream width=1632,height=1232,pixelformat=BGR888 \
      --file=/tmp/focus#.ppm >/dev/null 2>&1
    f=\$(ls -t /tmp/focus*.ppm 2>/dev/null | head -1)
    [ -n \"\$f\" ] || { echo 'capture failed (camera busy?)'; exit 0; }
    docker cp \"\$f\" $CONTAINER:/tmp/focus.ppm
    docker exec $CONTAINER python /tmp/focus_score.py /tmp/focus.ppm
    [ $save = 1 ] && docker exec $CONTAINER python -c \
      'import cv2; cv2.imwrite(\"/tmp/focus.jpg\", cv2.imread(\"/tmp/focus.ppm\"))' \
      && docker cp $CONTAINER:/tmp/focus.jpg /tmp/focus.jpg
    true")
  score=$(awk '{print $1}' <<<"$line")
  if [[ "$score" =~ ^[0-9.]+$ ]]; then
    if awk "BEGIN{exit !($score > $best)}"; then best=$score; mark="  << best"; else mark=""; fi
    echo "$line   best $best$mark"
  else
    echo "$line"
  fi
  [[ $save == 1 ]] && { scp -q "$HOST:/tmp/focus.jpg" /tmp/focus.jpg || true; }
  n=$((n + 1))
  [[ $COUNT -gt 0 && $n -ge $COUNT ]] && break
  sleep 1
done
