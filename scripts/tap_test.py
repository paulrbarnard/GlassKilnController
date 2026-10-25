"""Measure how reliably taps step a value: missed taps and double counts.

Runs inside the app container on the board:

    python /app/scripts/tap_test.py            (single taps, then bursts)
    python /app/scripts/tap_test.py --here     (already editing a value)

Gets into a value the same way as scroll_profile.py (Pro > slot > SEG >
segment 1 rate), then:
  singles  6 UP and 6 DOWN taps, one at a time, reading the settled value
           after each (expect +-1 each)
  bursts   bursts of 5 UP / 5 DOWN taps at 0.25 s and 0.5 s gaps, reading
           the settled value after each burst (expect +-5)
Leaves the edit with a long ENTER (nothing is saved).
"""

import sys
import time

sys.path.insert(0, "/app/python")
sys.path.insert(0, "/app/scripts")

from arduino.app_utils import Bridge  # noqa: E402

import scroll_profile as sp  # noqa: E402  (opens the camera)
from kiln.protocol import DisplayMessage as M  # noqa: E402

KEYS = sp.KEYS
vision = sp.vision
log = sp.log


def value_now():
    s = sp.seen(lambda s: s.message is None and s.numeric_value is not None, 3)
    return None if s is None else int(s.numeric_value)


def settled(timeout=5.0):
    """Two readings 0.5 s apart that agree."""
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        v = value_now()
        if v is not None and v == last:
            return v
        last = v
        time.sleep(0.5)
    return last


def tap(key):
    Bridge.call("press_button", KEYS[key], 200, timeout=10)


def main():
    if "--here" not in sys.argv and not sp.get_to_value():
        log("ABORT: couldn't get into a value")
        return 1
    results = []
    try:
        start = settled()
        log(f"editing a rate, now {start}")
        for key, sign in (("UP", 1), ("DOWN", -1)):
            for i in range(6):
                before = settled()
                tap(key)
                after = settled()
                d = None if before is None or after is None else after - before
                results.append(("single", key, 1, d))
                log(f"single {key} tap {i + 1}: {before} -> {after} ({d:+d})" if d is not None else f"single {key}: unreadable")
        for gap in (0.25, 0.5):
            for key, sign in (("UP", 1), ("DOWN", -1)):
                for i in range(2):
                    before = settled()
                    for _ in range(5):
                        tap(key)
                        time.sleep(gap)
                    after = settled()
                    d = None if before is None or after is None else after - before
                    results.append((f"burst {gap}s", key, 5, d))
                    log(f"burst of 5 {key} at {gap} s gaps: {before} -> {after} ({d:+d})" if d is not None else "burst: unreadable")
    finally:
        Bridge.call("press_button", KEYS["ENTER"], 5000, timeout=12)
        log("left the edit: " + ("back to IdLE" if sp.seen(lambda s: s.message in (M.STOPPED, M.IDLE), 4) else "did not exit"))
        vision.close()
    log("SUMMARY (expected vs counted):")
    for kind, key, n, d in results:
        flag = "" if d is not None and abs(d) == n else "   <-- " + ("unreadable" if d is None else ("MISSED" if abs(d) < n else "EXTRA"))
        log(f"  {kind:12} {key:5} expected {n}, counted {'?' if d is None else abs(d)}{flag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
