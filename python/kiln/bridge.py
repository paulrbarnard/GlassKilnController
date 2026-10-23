"""Thin client over the Uno Q Bridge RPC connecting to sketch/sketch.ino.

The sketch exposes exactly three RPC methods (see sketch/sketch.ino):
``press_button(which, hold_ms)``, ``configure_solenoid(which, kick_ms, hold_duty)``,
and ``ping()``. Everything else in this codebase should go through
``KilnBridge`` rather than touching ``arduino.app_utils`` directly, so the
rest of the app doesn't care whether it's talking to real hardware or
``MockBridge``.

API note: confirmed by actually calling into a running sketch on real
hardware (an Uno Q named "Steffi"), not just documentation. Unlike the
sketch-side C++ pattern (``Bridge.call("name").result(var)``, async +
explicit result fetch), ``arduino.app_utils.Bridge.call(name, *args)`` on
the Python side is **synchronous and returns the value directly** — no
``.result()``. Confirmed via a real RPC round trip: ``Bridge.call("ping")``
returned ``True`` and ``Bridge.call("press_button", 1, 150)`` returned
``True`` and actually moved the actuator (a servo at the time; the RPC
is unchanged for the solenoids).
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
        # press_button blocks for the whole press; the Bridge's default 10 s
        # reply timeout is shorter than a scroll hold (up to 12 s), so it
        # gave up while the solenoid was still pressing (2026-10-23).
        timeout_s = max(10, int(hold_ms / 1000) + 5)
        ok = self._bridge.call("press_button", key.value, hold_ms, timeout=timeout_s)
        if not ok:
            raise BridgeError(f"press_button({key!r}, {hold_ms}) rejected by sketch")

    def configure(self, key: Key, kick_ms: int, hold_duty: int) -> None:
        ok = self._bridge.call("configure_solenoid", key.value, kick_ms, hold_duty)
        if not ok:
            raise BridgeError(f"configure_solenoid({key!r}, {kick_ms}, {hold_duty}) rejected by sketch")

    def configure_long_hold(self, key: Key, settle_ms: int, duty: int) -> None:
        ok = self._bridge.call("configure_long_hold", key.value, settle_ms, duty)
        if not ok:
            raise BridgeError(f"configure_long_hold({key!r}, {settle_ms}, {duty}) rejected by sketch")

    def ping(self) -> bool:
        try:
            return bool(self._bridge.call("ping"))
        except Exception:  # noqa: BLE001 - any RPC failure means unreachable
            return False


@dataclass
class MockBridge(KilnBridge):
    """Records presses instead of firing solenoids — for UI/sequencer dev off
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
