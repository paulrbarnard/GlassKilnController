"""Measure how far one tap moves a value, against the gap since the last tap.

Runs inside the app container on the board:

    python /app/scripts/tap_test.py            (gets into a value first)
    python /app/scripts/tap_test.py --here     (already editing a value)
    python /app/scripts/tap_test.py --after-hold   (taps after a held press)
    python /app/scripts/tap_test.py --after-hold --hold 10 --waits 3,5,8 --gap 0.6
    python /app/scripts/tap_test.py --sweep    (single taps through ~160-180,
                                                logging what the camera reads)

Gets into a value (Pro > slot > SEG > segment 1 rate), rests 5 s, then for
each gap (longest first) taps UP 4 times and DOWN 4 times, one tap every
``gap`` seconds, reading the display between taps. The GM LT steps faster
when taps come close together (seen on the kiln 2026-10-25: taps about 1 s
apart moved 2-3 each), so this finds the gap at which every tap is one step.
With --after-hold, instead: for each wait W, hold UP (or DOWN) for 5 s,
wait W seconds, then tap the same key 3 times 1.5 s apart; on the kiln the
first taps after a hold counted 2-3 steps each.
Leaves the edit with a long ENTER (nothing is saved).
"""

import sys
import time

sys.path.insert(0, "/app/python")

from arduino.app_utils import Bridge  # noqa: E402

from kiln.protocol import DisplayMessage as M  # noqa: E402
from kiln.vision import RealVision  # noqa: E402

KEYS = {"ENTER": 0, "UP": 1, "DOWN": 2}
MODES = (M.AUTO, M.PRO, M.LOAD, M.OPTIONS)
GAPS = (3.0, 2.0, 1.5, 1.0, 0.6)

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


def is_value(s):
    return s.message is None and s.numeric_value is not None


def enter_until(pred, what):
    for _ in range(3):
        press("ENTER")
        if seen(pred, 3):
            return True
    log(f"ABORT: ENTER didn't reach {what} (showing {vision.read().raw_text!r})")
    return False


def get_to_value():
    if not seen(lambda s: s.message is M.IDLE, 8):
        log(f"ABORT: not at IdLE (showing {vision.read().raw_text!r})")
        return False
    if not enter_until(lambda s: s.message in MODES, "the mode menu"):
        return False
    for _ in range(8):
        if seen(lambda s: s.message is M.PRO, 1.5):
            break
        press("UP")
        time.sleep(1.5)
    else:
        log("ABORT: couldn't find Pro")
        return False
    return (
        enter_until(is_value, "the slot screen")
        and enter_until(lambda s: s.message is M.SEGMENT_COUNT, "SEG")
        and enter_until(lambda s: s.message is M.RATE_PROMPT and s.segment_number == 1, "rA 1")
    )


def value_until(deadline):
    """The last value read before ``deadline``."""
    last = None
    while time.monotonic() < deadline or last is None:
        s = vision.read()
        if is_value(s):
            last = int(s.numeric_value)
        if time.monotonic() > deadline + 3:
            break
    return last


def arg(name, default):
    if name in sys.argv:
        return sys.argv[sys.argv.index(name) + 1]
    return default


WAITS = tuple(float(w) for w in arg("--waits", "0.5,1,2,4,8").split(","))
HOLD_S = float(arg("--hold", "5"))
AFTER_GAP_S = float(arg("--gap", "1.5"))


def after_hold(results):
    value = value_until(time.monotonic() + 1.5)
    for wait in WAITS:
        for key in ("UP", "DOWN"):
            Bridge.call("press_hold", KEYS[key], int(HOLD_S * 1000) + 500, timeout=10)
            time.sleep(HOLD_S)
            Bridge.call("press_release", timeout=10)
            value = value_until(time.monotonic() + max(wait, 1.0))
            time.sleep(max(0.0, wait - 1.0))
            steps = []
            for _ in range(3):
                t = time.monotonic()
                press(key)
                after = value_until(t + AFTER_GAP_S)
                steps.append(None if after is None or value is None else abs(after - value))
                value = after
            results[(wait, key)] = steps
            log(f"{wait} s after a {HOLD_S:g} s {key} hold, taps {AFTER_GAP_S:g} s apart: steps per tap {steps}")
            time.sleep(8)


def sweep():
    """Hold DOWN 9 s (222 -> about 160), rest, then tap UP 20 times 1.5 s
    apart, logging every distinct text read after each tap: checks the
    decoder on the digits 0-9 in the last place."""
    Bridge.call("press_hold", KEYS["DOWN"], 9500, timeout=10)
    time.sleep(9)
    Bridge.call("press_release", timeout=10)
    time.sleep(3)
    value = value_until(time.monotonic() + 1.5)
    log(f"swept down to {value}")
    for _ in range(20):
        t = time.monotonic()
        press("UP")
        texts = []
        while time.monotonic() < t + 1.5:
            s = vision.read()
            if is_value(s) and (not texts or texts[-1] != s.raw_text):
                texts.append(s.raw_text)
        after = value_until(time.monotonic() + 0.5)
        d = "?" if after is None or value is None else f"{after - value:+d}"
        log(f"tap UP: {value} -> {after} ({d}) read {texts}")
        value = after


def main():
    if "--here" not in sys.argv and not get_to_value():
        return 1
    results = {}
    try:
        time.sleep(5)
        value = value_until(time.monotonic() + 1.5)
        log(f"editing a rate, now {value}")
        if "--sweep" in sys.argv:
            sweep()
            return 0
        if "--after-hold" in sys.argv:
            after_hold(results)
            return 0
        for gap in GAPS:
            for key in ("UP", "DOWN"):
                steps = []
                for _ in range(4):
                    t = time.monotonic()
                    press(key)
                    after = value_until(t + gap)
                    steps.append(None if after is None or value is None else abs(after - value))
                    value = after
                results[(gap, key)] = steps
                log(f"gap {gap} s, {key}: steps per tap {steps}")
            time.sleep(5)  # rest between gaps
    finally:
        press("ENTER", 5000)
        log("left the edit: " + ("back to IdLE" if seen(lambda s: s.message in (M.STOPPED, M.IDLE), 4) else "did not exit"))
        vision.close()
        log("SUMMARY: steps per tap (1 is right)")
        for (gap, key), steps in results.items():
            log(f"  {gap:>3} s {key:5} {steps}")


if __name__ == "__main__":
    sys.exit(main())
