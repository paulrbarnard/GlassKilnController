"""Press one GM LT button, then watch and record what the display does.

Runs inside the app container on the board (Bridge + camera), for exploring
the controller's menus and collecting reference frames:

    python /app/scripts/step.py --press ENTER --watch 6
    python /app/scripts/step.py --press ENTER:5000 --watch 4    long press
    python /app/scripts/step.py --watch 10                      just watch

A reading is reported only once two consecutive frames agree, so frames
caught mid-change (e.g. "1dU6." between IdLE and a temperature) are
skipped. The first frame of each distinct reading is saved as a reference:
/app/reference/<n>_<text>_roi.png and .jpg (see read_display.py --pull).
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import cv2  # noqa: E402

from kiln.vision import RealVision, build_controller_state, decode_roi  # noqa: E402

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CONFIG = os.path.join(APP, "python", "config", "camera_calibration.yaml")
REFERENCE_DIR = os.path.join(APP, "reference")
KEYS = {"ENTER": 0, "UP": 1, "DOWN": 2}


def describe(text: str) -> str:
    state = build_controller_state(text)
    if state.message:
        return state.message.name + (f" {state.segment_number}" if state.segment_number is not None else "")
    if state.numeric_value is not None:
        return f"number {state.numeric_value:g}"
    return "blank" if not text.strip() else "unrecognised"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--press", help="KEY or KEY:ms (ENTER, UP, DOWN); default 150 ms")
    parser.add_argument("--watch", type=float, default=5.0, help="seconds to watch afterwards")
    parser.add_argument("--no-save", action="store_true", help="don't save reference frames")
    args = parser.parse_args()

    vision = RealVision(config_path=CONFIG)
    cal = vision.calibration
    x, y, w, h = cal.roi
    os.makedirs(REFERENCE_DIR, exist_ok=True)
    seq = len([f for f in os.listdir(REFERENCE_DIR) if f.endswith("_roi.png")])

    def frame_text():
        frame = vision.read_frame()
        if frame is None:
            return None, None
        return frame, decode_roi(frame[y : y + h, x : x + w], cal)

    try:
        # Make sure the camera is open and settled before pressing, so the
        # first frames after the press aren't lost to start-up.
        before = None
        for _ in range(2):
            _, before = frame_text()
        print(f"{time.strftime('%H:%M:%S')}  before: {before!r} ({describe(before or '')})", flush=True)

        if args.press:
            name, _, ms = args.press.upper().partition(":")
            if name not in KEYS:
                print(f"unknown key {name}")
                return 2
            from arduino.app_utils import Bridge  # type: ignore

            hold = int(ms) if ms else 150
            ok = Bridge.call("press_button", KEYS[name], hold)
            print(f"{time.strftime('%H:%M:%S')}  pressed {name} {hold} ms ok={ok}", flush=True)

        last_seen, last_reported = None, None
        saved = set()
        deadline = time.monotonic() + args.watch
        while time.monotonic() < deadline:
            frame, text = frame_text()
            if text is None:
                print("camera read failed")
                return 1
            if text == last_seen and text != last_reported:
                line = f"{time.strftime('%H:%M:%S')}  {text!r:10} {describe(text)}"
                if not args.no_save and text not in saved:
                    saved.add(text)
                    seq += 1
                    label = f"{seq:02d}_" + (re.sub(r"[^0-9A-Za-z]", "_", text.strip()) or "blank")
                    cv2.imwrite(os.path.join(REFERENCE_DIR, f"{label}_roi.png"), frame[y : y + h, x : x + w])
                    cv2.imwrite(os.path.join(REFERENCE_DIR, f"{label}.jpg"), frame)
                    line += f"   saved {label}"
                print(line, flush=True)
                last_reported = text
            last_seen = text
            time.sleep(0.1)
    finally:
        vision.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
