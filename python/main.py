#!/usr/bin/env python3
"""App Lab entry point (Linux/MPU side) — brings up the Bridge connection to
sketch/sketch.ino and launches the PySide6 touchscreen UI.

Usage on the Uno Q (via App Lab / app.yaml):
    python main.py

Usage for UI/sequencer development away from the kiln:
    python main.py --mock-bridge --mock-vision
    python main.py --mock-bridge --mock-vision --mock-script IdLE,Auto,SLo,FULL,rEdI,ON

``--mock-script`` feeds MockVision a comma-separated sequence of raw display
strings (see kiln/vision.py) so a whole profile run can be exercised without
a camera or kiln attached.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent / "config"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mock-bridge",
        action="store_true",
        help="use kiln.bridge.MockBridge instead of the real Uno Q Bridge RPC",
    )
    parser.add_argument(
        "--mock-vision",
        action="store_true",
        help="use kiln.vision.MockVision instead of the real camera",
    )
    parser.add_argument(
        "--mock-script",
        type=str,
        default="IdLE",
        help="comma-separated raw display strings for MockVision to cycle through",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def build_bridge(use_mock: bool):
    from kiln.bridge import KilnBridge, MockBridge, RealBridge

    bridge: KilnBridge
    if use_mock:
        bridge = MockBridge()
    else:
        bridge = RealBridge()
        _push_servo_calibration(bridge)
    return bridge


def _push_servo_calibration(bridge) -> None:
    """Push python/config/servo_calibration.yaml to the sketch at startup so
    the mechanical calibration lives in one editable file, not hardcoded
    twice (see sketch/sketch.ino calibrate_servo)."""

    import yaml

    from kiln.protocol import Key

    path = CONFIG_DIR / "servo_calibration.yaml"
    if not path.exists():
        logging.warning("no servo_calibration.yaml found at %s; using sketch defaults", path)
        return
    with open(path, "r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    key_by_name = {"enter": Key.ENTER, "up": Key.UP, "down": Key.DOWN}
    for name, key in key_by_name.items():
        cal = data.get(name)
        if not cal:
            continue
        bridge.calibrate(key, cal["rest_deg"], cal["press_deg"])


def build_vision(use_mock: bool, mock_script: str):
    from kiln.vision import KilnVision, MockVision, RealVision

    vision: KilnVision
    if use_mock:
        vision = MockVision(script=mock_script.split(","))
    else:
        vision = RealVision(config_path=str(CONFIG_DIR / "camera_calibration.yaml"))
    return vision


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from kiln.profiles import ProfileLibrary
    from kiln.sequencer import Sequencer

    bridge = build_bridge(args.mock_bridge)
    vision = build_vision(args.mock_vision, args.mock_script)
    library = ProfileLibrary().load()
    sequencer = Sequencer(bridge=bridge, vision=vision, on_progress=logging.info)

    if not bridge.ping():
        logging.warning(
            "sketch bridge did not respond to ping() — button presses will "
            "fail until the MCU side is reachable"
        )

    # Import Qt lazily so `python main.py --help` etc. don't require a
    # display server to be available.
    from PySide6.QtWidgets import QApplication

    from ui.app import MainWindow

    app = QApplication(sys.argv)
    window = MainWindow(library=library, sequencer=sequencer)
    window.showFullScreen()  # kiosk-style on the 8" touchscreen
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
