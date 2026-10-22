"""Read the GM LT display once (or repeatedly) through the app's RealVision.

Runs inside the app container on the board, where the camera pipeline and
OpenCV are; scripts/read_display.sh is the wrapper to run from a Mac.

    python /app/scripts/read_display.py                 one reading
    python /app/scripts/read_display.py --loop 10       ten readings, 1 s apart
    python /app/scripts/read_display.py --save SLo      also keep this frame as
                                                        a reference, labelled SLo

Each reading prints the decoded text, what it classified as, and the green
level sampled at every segment (a-g, then the decimal point), with the
lit/unlit cut-off, so a misread can be traced to the segment that caused it.
It also writes /tmp/read_display.jpg: the display area with the digit boxes
and every sample point, green where it read as lit and red where unlit.

--save writes the roi crop to /app/reference/<label>_roi.png (ready to copy
into python/tests/fixtures/) and the full frame to /app/reference/<label>.jpg.
The camera must not be in use by anything else (a running sequence, focus.sh).
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "python"))

import cv2  # noqa: E402

from kiln.vision import (  # noqa: E402
    SEGMENT_NAMES,
    RealVision,
    build_controller_state,
    decode_roi,
    sample_segments,
    segment_cut,
)

APP = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
CONFIG = os.path.join(APP, "python", "config", "camera_calibration.yaml")
REFERENCE_DIR = os.path.join(APP, "reference")


def annotate(roi, cal, levels, cut):
    out = roi.copy()
    h = cal.sample_half
    for (bx, by, bw, bh), digit in zip(cal.digit_boxes, levels):
        cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (255, 255, 0), 1)
        for (px, py), level in zip((*cal.segment_points, cal.dp_point), digit):
            colour = (0, 255, 0) if cut is not None and level > cut else (0, 0, 255)
            cv2.rectangle(out, (bx + px - h, by + py - h), (bx + px + h, by + py + h), colour, 2)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", type=int, default=1, help="number of readings (1 s apart)")
    parser.add_argument("--save", metavar="LABEL", help="keep the frame as a reference, labelled LABEL")
    args = parser.parse_args()

    vision = RealVision(config_path=CONFIG)
    cal = vision.calibration
    x, y, w, h = cal.roi
    try:
        for i in range(args.loop):
            frame = vision.read_frame()
            if frame is None:
                print("camera read failed (camera busy, or not detected at boot?)")
                return 1
            roi = frame[y : y + h, x : x + w]
            levels = sample_segments(roi, cal)
            hi = max(v for d in levels for v in d)
            cut = segment_cut(levels, cal)
            text = decode_roi(roi, cal)
            state = build_controller_state(text)
            if state.message:
                what = state.message.name
            elif state.numeric_value is not None:
                what = f"number {state.numeric_value:g}"
            else:
                what = "display off" if cut is None else "unrecognised"
            if state.segment_number is not None:
                what += f" segment {state.segment_number}"
            print(f"{time.strftime('%H:%M:%S')}  {text!r:10}  {what}   (cut {'-' if cut is None else f'{cut:.0f}'}, brightest {hi:.0f})")
            for n, digit in enumerate(levels, 1):
                print(f"    digit {n}: " + " ".join(f"{s}{v:4.0f}" for s, v in zip(SEGMENT_NAMES + "P", digit)))
            cv2.imwrite("/tmp/read_display.jpg", annotate(roi, cal, levels, cut))
            if args.save:
                os.makedirs(REFERENCE_DIR, exist_ok=True)
                cv2.imwrite(os.path.join(REFERENCE_DIR, f"{args.save}_roi.png"), roi)
                cv2.imwrite(os.path.join(REFERENCE_DIR, f"{args.save}.jpg"), frame)
                print(f"    saved reference/{args.save}_roi.png and {args.save}.jpg")
            if i + 1 < args.loop:
                time.sleep(1)
    finally:
        vision.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
