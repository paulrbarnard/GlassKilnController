"""Thin client over the Uno Q Bridge RPC connecting to sketch/sketch.ino.

The sketch exposes exactly three RPC methods (see sketch/sketch.ino):
``press_button(which, hold_ms)``, ``calibrate_servo(which, rest, press)``,
and ``ping()``. Everything else in this codebase should go through
``KilnBridge`` rather than touching ``arduino.app_utils`` directly, so the
rest of the app doesn't care whether it's talking to real hardware or
``MockBridge``.

API note: ``arduino.app_utils.Bridge`` on the Python side is confirmed (via
public UNO Q examples) to support ``Bridge.provide(name, fn)`` for exposing
Python functions to the sketch. Calling *into* the sketch from Python is
documented to work symmetrically over the same RPC router, but this
repo hasn't yet been run against real App Lab hardware to confirm the exact
call spelling — ``Bridge.call(name, *args).result()`` below is the best
grounded guess (mirrors the confirmed sketch-side
``Bridge.call("name").result(var)`` pattern) and is the first thing to
verify against `arduino.app_utils` docs/examples once hardware is in hand.
"""

from __future__ import annotations

import abc
import logging
import time
from dataclasses import dataclass, field

from kiln.protocol import Key, LONG_PRESS_SECONDS, SHORT_PRESS_MS

log = logging.getLogger(__name__)


class BridgeError(RuntimeError):
    """Raised when the sketch side reports failure or is unreachable."""


class KilnBridge(abc.ABC):
    """Abstract interface used by ``sequencer.py``."""

    @abc.abstractmethod
    def press(self, key: Key, hold_ms: int = SHORT_PRESS_MS) -> None:
        """Press-and-release ``key`` for ``hold_ms`` milliseconds."""

    def hold_long(self, key: Key) -> None:
        """Press-and-release for the manual's 4-second 'hold' actions
        (Start Over, unlock/relock Advanced level)."""

        self.press(key, hold_ms=int(LONG_PRESS_SECONDS * 1000))

    @abc.abstractmethod
    def ping(self) -> bool:
        """Return True if the sketch side is reachable."""


class RealBridge(KilnBridge):
    """Talks to the actual sketch over the Uno Q Bridge RPC.

    Import of ``arduino.app_utils`` is deferred to ``__init__`` so this
    module can be imported (and ``MockBridge`` used) on a dev machine that
    doesn't have the App Lab runtime installed.
    """

    def __init__(self) -> None:
        try:
            from arduino.app_utils import Bridge  # type: ignore
        except ImportError as exc:  # pragma: no cover - only off-target
            raise BridgeError(
                "arduino.app_utils not available — RealBridge only runs "
                "under Arduino App Lab on the Uno Q. Use MockBridge for "
                "development."
            ) from exc

        self._bridge = Bridge

    def press(self, key: Key, hold_ms: int = SHORT_PRESS_MS) -> None:
        ok = self._bridge.call("press_button", key.value, hold_ms).result()
        if not ok:
            raise BridgeError(f"press_button({key!r}, {hold_ms}) rejected by sketch")

    def calibrate(self, key: Key, rest_deg: int, press_deg: int) -> None:
        ok = self._bridge.call(
            "calibrate_servo", key.value, rest_deg, press_deg
        ).result()
        if not ok:
            raise BridgeError(f"calibrate_servo({key!r}) rejected by sketch")

    def ping(self) -> bool:
        try:
            return bool(self._bridge.call("ping").result())
        except Exception:  # noqa: BLE001 - any RPC failure means unreachable
            return False


@dataclass
class MockBridge(KilnBridge):
    """Records presses instead of moving servos — for UI/sequencer dev off
    the kiln, and for unit tests that assert on the exact press sequence a
    profile produces (see python/tests/test_protocol.py).
    """

    presses: list[tuple[Key, int]] = field(default_factory=list)
    press_delay_s: float = 0.0  # set >0 to simulate real actuation time

    def press(self, key: Key, hold_ms: int = SHORT_PRESS_MS) -> None:
        log.debug("MockBridge: press %s for %dms", key, hold_ms)
        self.presses.append((key, hold_ms))
        if self.press_delay_s:
            time.sleep(self.press_delay_s)

    def ping(self) -> bool:
        return True
