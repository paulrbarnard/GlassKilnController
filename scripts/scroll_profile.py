"""Record how a held UP/DOWN scrolls a value: time -> value, frame by frame.

Runs inside the app container on the board:

    python /app/scripts/scroll_profile.py UP 20000
    python /app/scripts/scroll_profile.py DOWN 20000
    python /app/scripts/scroll_profile.py DOWN 20000 --here   (skip the checks)

Gets into a value (Pro > slot > SEG > segment 1 rate, using ENTER and UP) if
one isn't already being edited, then holds KEY for the given milliseconds on
a background thread while the main thread reads the display as fast as the
camera allows. Writes /app/reference/scroll_<key>.csv (seconds since the
press started, value read) and prints a summary. The edit is left open (the
changed value isn't saved until ENTER); a later run can carry on from it, and
a long ENTER exits without saving.

Used to build kiln/sequencer.py SCROLL_CURVE.
"""

import csv
import os
import sys
import threading
import time

sys.path.insert(0, "/app/python")

from arduino.app_utils import Bridge  # noqa: E402

from kiln.protocol import DisplayMessage as M  # noqa: E402
from kiln.vision import RealVision, build_controller_state, read_frame_text  # noqa: E402

KEYS = {"ENTER": 0, "UP": 1, "DOWN": 2}
MODES = (M.AUTO, M.PRO, M.LOAD, M.OPTIONS)

vision = RealVision(config_path="/app/python/config/camera_calibration.yaml")
vision.read()  # open the camera and let it settle


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def seen(pred, secs):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        s = vision.read()
        if pred(s):
            return s
    return None


def press(key, ms=200):
    Bridge.call("press_button", KEYS[key], ms, timeout=max(10, ms // 1000 + 5))


def editing_value():
    # Straight after a scroll the value shows steadily for a few seconds
    # before the label ("rA 1") alternates with it again.
    return seen(lambda s: s.message is None and s.numeric_value is not None, 2) is not None and seen(
        lambda s: s.message is M.RATE_PROMPT, 6
    ) is not None


def get_to_value():
    if editing_value():
        return True
    if not seen(lambda s: s.message in MODES, 1.5):
        if not seen(lambda s: s.message is M.IDLE, 2.5):
            return False
        press("ENTER")
        if not seen(lambda s: s.message in MODES, 3):
            return False
    for _ in range(8):
        if seen(lambda s: s.message is M.PRO, 1.5):
            break
        press("UP")
    for check in (
        lambda s: s.message is None and s.numeric_value is not None,
        lambda s: s.message is M.SEGMENT_COUNT,
        lambda s: s.message is M.RATE_PROMPT and s.segment_number == 1,
    ):
        press("ENTER")
        if not seen(check, 3):
            return False
    return True


def main():
    key = sys.argv[1].upper()
    hold_ms = int(sys.argv[2])
    here = "--here" in sys.argv  # already editing a value (e.g. one left at a limit)
    if not here and not get_to_value():
        log("ABORT: couldn't get into a value")
        return 1
    start_value = seen(lambda s: s.message is None and s.numeric_value is not None, 3)
    log(f"editing a rate, now {int(start_value.numeric_value)}; holding {key} for {hold_ms} ms")

    samples = []
    t0 = time.monotonic()
    presser = threading.Thread(target=press, args=(key, hold_ms))
    presser.start()
    while presser.is_alive() or time.monotonic() - t0 < hold_ms / 1000 + 1.5:
        frame = vision.read_frame()
        if frame is None:
            continue
        state = build_controller_state(read_frame_text(frame, vision.calibration))
        if state.message is None and state.numeric_value is not None:
            samples.append((round(time.monotonic() - t0, 3), int(state.numeric_value)))
    presser.join()

    path = f"/app/reference/scroll_{key.lower()}.csv"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["seconds", "value"])
        writer.writerows(samples)
    if samples:
        first = int(start_value.numeric_value)
        last = samples[-1][1]
        log(f"{len(samples)} readings over {samples[-1][0]:.1f} s: {first} -> {last} ({last - first:+d}); saved {path}")
        for t in (1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20):
            at = [v for s, v in samples if s <= t]
            if at:
                log(f"  by {t:>2} s: {at[-1] - first:+d}")
    vision.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
