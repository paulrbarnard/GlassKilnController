"""Tracks a firing from the moment it's requested until it ends.

Phases: ``programming`` (the sequencer is pressing buttons), ``firing``
(the controller showed ON), then one of ``complete`` (CPLt), ``stopped``
(StOP / IdLE after a stop), ``error`` (an Err/FAIL code, or programming
failed) and ``ended`` (the controller went back to IdLE without us asking:
stopped by hand at the kiln, or a firing that finished while we weren't
looking).

While firing, a background thread reads the display every few seconds and
publishes the status. It only *reads*: it never presses a button, so it
can't interfere with the program. The status goes to the UI through the
``publish`` callback and is available from ``status()`` so a reloaded page
can pick it up again.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from kiln.protocol import DisplayMessage
from kiln.vision import ControllerState, KilnVision

log = logging.getLogger(__name__)

ACTIVE_PHASES = ("programming", "firing", "stopping")
TRACE_EVERY_S = 60.0  # one point per minute on the measured-temperature trace


def _c_to_f(temp_c: float) -> float:
    return temp_c * 9.0 / 5.0 + 32.0


@dataclass
class FiringStatus:
    phase: str = "idle"
    profile_id: Optional[str] = None
    profile_name: Optional[str] = None
    requested_at: Optional[float] = None  # epoch s: Start pressed in the app
    fired_at: Optional[float] = None  # epoch s: controller showed ON
    ended_at: Optional[float] = None
    display: str = ""  # last raw display text
    temp: Optional[float] = None  # last temperature, in the controller's unit
    unit: str = "C"
    message: str = ""
    # [minutes since fired_at, temperature °F] — °F like the profile data the
    # diagram draws, whatever unit the controller shows.
    trace: list = field(default_factory=list)

    @property
    def active(self) -> bool:
        return self.phase in ACTIVE_PHASES


class FiringMonitor:
    def __init__(
        self,
        vision: KilnVision,
        publish: Callable[[dict], None],
        poll_s: float = 5.0,
        celsius: Callable[[], bool] = lambda: True,
    ):
        self._vision = vision
        self._publish = publish
        self._poll_s = poll_s
        self._celsius = celsius
        self._lock = threading.Lock()
        self._status = FiringStatus()
        self._stop_watch = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_trace = 0.0

    # -- state changes (called by the API) ----------------------------------

    def status(self) -> dict:
        with self._lock:
            return asdict(self._status)

    @property
    def active(self) -> bool:
        with self._lock:
            return self._status.active

    def begin(self, profile_id: str, profile_name: str) -> None:
        self._stop_watch.set()
        with self._lock:
            self._status = FiringStatus(
                phase="programming",
                profile_id=profile_id,
                profile_name=profile_name,
                requested_at=time.time(),
                unit="C" if self._celsius() else "F",
                message="Programming the controller",
            )
        self._emit()

    def firing_started(self) -> None:
        with self._lock:
            self._status.phase = "firing"
            self._status.fired_at = time.time()
            self._status.message = "Firing"
        self._emit()
        self._stop_watch = threading.Event()
        self._last_trace = 0.0
        self._thread = threading.Thread(target=self._watch, name="firing-monitor", daemon=True)
        self._thread.start()

    def stopping(self) -> None:
        self._set_phase("stopping", "Stopping the program")

    def finish(self, phase: str, message: str) -> None:
        """End the firing in ``phase`` (stopped, error, ...)."""

        self._stop_watch.set()
        self._set_phase(phase, message, ended=True)

    # -- watching -------------------------------------------------------------

    def _watch(self) -> None:
        log.info("firing monitor started")
        while not self._stop_watch.wait(self._poll_s):
            try:
                state = self._vision.read()
            except Exception:  # noqa: BLE001 - keep watching through a bad read
                log.exception("firing monitor: read failed")
                continue
            if self._observe(state):
                break
        log.info("firing monitor stopped")

    def _observe(self, state: ControllerState) -> bool:
        """Update the status from one reading; True once the firing is over."""

        message = state.message
        with self._lock:
            if self._status.phase != "firing":
                return True  # a stop is being handled by the API
            self._status.display = state.raw_text
        if state.is_error:
            self.finish("error", f"Controller error {state.raw_text.strip()}: the kiln has shut itself off")
            return True
        if message is DisplayMessage.COMPLETE:
            self.finish("complete", "Program complete (the kiln may still be hot)")
            return True
        if message is DisplayMessage.IDLE:
            self.finish("ended", "The controller is back at IdLE: the program ended or was stopped at the kiln")
            return True
        if state.numeric_value is not None and message is None:
            self._record_temp(state.numeric_value)
        self._emit()
        return False

    def _record_temp(self, value: float) -> None:
        now = time.time()
        with self._lock:
            self._status.temp = value
            if self._status.fired_at and now - self._last_trace >= TRACE_EVERY_S:
                temp_f = _c_to_f(value) if self._status.unit == "C" else value
                minutes = (now - self._status.fired_at) / 60.0
                self._status.trace.append([round(minutes, 2), round(temp_f, 1)])
                self._last_trace = now

    # -- helpers ---------------------------------------------------------------

    def _set_phase(self, phase: str, message: str, ended: bool = False) -> None:
        with self._lock:
            self._status.phase = phase
            self._status.message = message
            if ended:
                self._status.ended_at = time.time()
        log.info("firing: %s: %s", phase, message)
        self._emit()

    def _emit(self) -> None:
        try:
            self._publish(self.status())
        except Exception:  # noqa: BLE001 - a UI hiccup mustn't stop the watch
            log.exception("firing monitor: publish failed")
