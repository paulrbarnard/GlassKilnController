"""Run the app's own value entry (Sequencer._set_value) by hand, logging
every press and every value it reads.

Runs inside the app container on the board, with the controller at IdLE:

    python /app/scripts/set_value_test.py 167        (target; default 167)

Gets into segment 1's rate (as tap_test.py does), sets it to the target with
the sequencer's code, then leaves the edit with a long ENTER (nothing is
saved). For when the app's value entry misbehaves but tap_test.py's own
presses don't.
"""

import logging
import sys
import time

sys.path.insert(0, "/app/python")
sys.path.insert(0, "/app/scripts")

import tap_test as tt  # noqa: E402  (opens the camera)
from kiln.bridge import RealBridge  # noqa: E402
from kiln.sequencer import Sequencer  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s", datefmt="%H:%M:%S")
T0 = time.monotonic()


def stamp():
    return f"{time.monotonic() - T0:7.2f}"


class LoggedBridge(RealBridge):
    def press(self, key, hold_ms=200):
        print(stamp(), "press", key.name, hold_ms, flush=True)
        super().press(key, hold_ms)

    def hold_start(self, key, max_ms):
        print(stamp(), "hold_start", key.name, max_ms, flush=True)
        super().hold_start(key, max_ms)

    def hold_release(self):
        print(stamp(), "hold_release", flush=True)
        super().hold_release()


def main():
    target = int(sys.argv[1]) if len(sys.argv) > 1 else 167
    if not tt.get_to_value():
        return 1
    seq = Sequencer(bridge=LoggedBridge(), vision=tt.vision)
    real_value = seq._value

    def logged_value(scale=1, timeout_s=3.0):
        v = real_value(scale, timeout_s)
        print(stamp(), "read", v, flush=True)
        return v

    seq._value = logged_value
    try:
        time.sleep(3)
        seq._set_value(target)
        print(stamp(), "DONE: value set to", target, flush=True)
    except Exception as exc:  # noqa: BLE001
        print(stamp(), "FAILED:", exc, flush=True)
    finally:
        tt.press("ENTER", 5000)
        tt.log("left the edit")
        tt.vision.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
