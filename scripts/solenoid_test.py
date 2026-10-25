"""Check the button solenoids on the kiln and find the lowest working duties.

Runs inside the app container on the board (Bridge + camera):

    python /app/scripts/solenoid_test.py taps
    python /app/scripts/solenoid_test.py enter  --duties 40,60,80,100
    python /app/scripts/solenoid_test.py scroll --key UP   --duties 40,60,90,120,160
    python /app/scripts/solenoid_test.py scroll --key DOWN --duties 40,60,90,120,160

taps    from IdLE: ENTER opens the mode menu, then 4 UP and 4 DOWN taps go
        round its loop; reports how many registered (ends in the menu).
enter   a 5 s ENTER hold at each long-hold duty; it only counts if StOP
        appears (a dropped hold acts as a tap and goes one menu deeper).
        Stops at the first duty that exits twice.
scroll  gets into a value (Pro > slot > SEG > segment 1 rate, using ENTER and
        UP only) unless one is already being edited, then holds KEY for 7 s
        at each duty. A solid hold scrolls ~+-40; +-1 means the contact
        dropped. Stops at the first duty that moves >= 30. Leaves the edit
        with a long ENTER (the changed value isn't saved).

Every long press is followed by a 60 s rest: the solenoids are rated 5% duty
in 60 s at 12 V (see kiln/bridge.py HeatBudget). Saved settings from
python/config/solenoid_calibration.yaml are restored afterwards. Refuses to
run with a dark display (kiln off).
"""

import argparse
import sys
import time

sys.path.insert(0, "/app/python")

import yaml  # noqa: E402
from arduino.app_utils import Bridge  # noqa: E402

from kiln.protocol import DisplayMessage as M  # noqa: E402
from kiln.vision import RealVision  # noqa: E402

CONFIG = "/app/python/config/"
KEYS = {"ENTER": 0, "UP": 1, "DOWN": 2}
MODES = (M.AUTO, M.PRO, M.LOAD, M.OPTIONS)
REST_S = 60

vision = RealVision(config_path=CONFIG + "camera_calibration.yaml")
vision.read()  # open the camera and let it settle before any timed check


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
    if not Bridge.call("press_button", KEYS[key], ms, timeout=max(10, ms // 1000 + 5)):
        raise RuntimeError(f"{key} {ms} ms refused by the sketch (heat limit?)")


def long_hold(key, duty):
    Bridge.call("configure_long_hold", KEYS[key], 200, duty)


def value():
    s = seen(lambda s: s.message is None and s.numeric_value is not None, 3)
    return None if s is None else int(s.numeric_value)


def saved_long_holds():
    with open(CONFIG + "solenoid_calibration.yaml") as fh:
        cal = yaml.safe_load(fh)
    return {name.upper(): int(cal[name].get("long_hold_duty", 255)) for name in ("enter", "up", "down")}


def open_menu():
    if seen(lambda s: s.message in MODES, 1.5):
        return True
    if not seen(lambda s: s.message is M.IDLE, 2.5):
        return False
    for i in range(1, 4):
        press("ENTER")
        if seen(lambda s: s.message in MODES, 3):
            log(f"ENTER tap {i} at IdLE opened the menu")
            return True
        log(f"ENTER tap {i} at IdLE: no menu")
    return False


def stage_taps():
    if not open_menu():
        log("ABORT: couldn't open the mode menu (not at IdLE, or ENTER taps not registering)")
        return
    for key in ("UP", "DOWN"):
        ok = 0
        current = seen(lambda s: s.message in MODES, 2).message
        for i in range(4):
            press(key)
            nxt = seen(lambda s: s.message in MODES and s.message is not current, 3)
            ok += nxt is not None
            if nxt:
                current = nxt.message
            time.sleep(1)
        log(f"{key} taps registered: {ok}/4")


def stage_enter(duties):
    good = {}
    for duty in duties:
        for attempt in (1, 2):
            if seen(lambda s: s.message is M.IDLE, 2.5):
                open_menu()  # so a long press has somewhere to exit from
            long_hold("ENTER", duty)
            press("ENTER", 5000)
            ok = seen(lambda s: s.message is M.STOPPED, 3) is not None
            log(f"ENTER duty {duty} try {attempt}: {'EXITED (StOP)' if ok else 'no StOP'} (now {vision.read().raw_text!r})")
            good[duty] = good.get(duty, 0) + ok
            log(f"resting {REST_S} s")
            time.sleep(REST_S)
            if not ok:
                break
        if good.get(duty) == 2:
            log(f"RESULT: lowest reliable ENTER long-hold duty = {duty}")
            return
    log("RESULT: no tested duty exited twice")


def get_to_value():
    if value() is not None and seen(lambda s: s.message is M.RATE_PROMPT, 2):
        return True
    if not open_menu():
        return False
    for _ in range(8):
        if seen(lambda s: s.message is M.PRO, 1.5):
            break
        press("UP")
    for want in ("slot", "SEG", "rA 1"):
        for _ in range(3):
            press("ENTER")
            if want == "slot" and seen(lambda s: s.message is None and s.numeric_value is not None, 3):
                break
            if want == "SEG" and seen(lambda s: s.message is M.SEGMENT_COUNT, 3):
                break
            if want == "rA 1" and seen(lambda s: s.message is M.RATE_PROMPT and s.segment_number == 1, 3):
                break
        else:
            return False
    return True


def stage_scroll(key, duties, exit_duty, hold_ms=7000):
    if not get_to_value():
        log("ABORT: couldn't get into a value to scroll")
        return
    log(f"editing segment 1 rate, now {value()}")
    sign = 1 if key == "UP" else -1
    try:
        for duty in duties:
            long_hold(key, duty)
            before = value()
            press(key, hold_ms)
            time.sleep(0.8)
            after = value()
            change = None if before is None or after is None else after - before
            log(f"{key} duty {duty}: {before} -> {after}  ({change:+d})" if change is not None else f"{key} duty {duty}: unreadable")
            log(f"resting {REST_S} s")
            time.sleep(REST_S)
            if change is not None and change * sign >= 30:
                log(f"RESULT: lowest {key} scroll duty = {duty}")
                return
        log(f"RESULT: no tested duty scrolled {key} properly")
    finally:
        long_hold("ENTER", exit_duty)
        press("ENTER", 5000)
        log("left the edit: " + ("back to IdLE" if seen(lambda s: s.message in (M.STOPPED, M.IDLE), 4) else "did not exit"))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=("taps", "enter", "scroll"))
    parser.add_argument("--key", choices=("UP", "DOWN"), default="UP")
    parser.add_argument("--duties", default="40,60,80,100")
    parser.add_argument("--hold-ms", type=int, default=7000, help="scroll hold length (default 7 s)")
    args = parser.parse_args()
    duties = [int(d) for d in args.duties.split(",")]
    saved = saved_long_holds()
    if not seen(lambda s: s.raw_text.replace(".", "").strip() not in ("", "?"), 10):
        log("ABORT: the display is dark (is the kiln switched on?); nothing pressed")
        return 1
    try:
        if args.stage == "taps":
            stage_taps()
        elif args.stage == "enter":
            stage_enter(duties)
        else:
            stage_scroll(args.key, duties, saved["ENTER"], args.hold_ms)
    finally:
        for key, duty in saved.items():
            long_hold(key, duty)
        vision.close()
        log(f"saved long-hold duties restored: {saved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
