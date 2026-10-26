"""The kiln's temperature while nothing else is using the controller.

The module covers the whole front panel, so the app is the only way to see
whether the kiln has cooled enough to open after a firing. At rest the GM
LT alternates IdLE with the kiln temperature; this reads it every so often
and publishes it for the home screen. It only *reads* the display (never
presses a button) and stands aside while a sequence or a firing is running:
the firing monitor's own temperature is used then.

The camera is released 30 s after its last read (vision.RealVision), so
reading every IDLE_COOL_S keeps it free most of the time for the on-board
tools; while the kiln is hot it reads more often.

A display that stays dark for the whole read means the kiln is switched
off (at rest it never goes blank: IdLE alternates with the temperature).
The last temperature read is kept, since a kiln switched off while hot
can't be read any more.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from typing import Callable, Optional

from kiln.vision import KilnVision

log = logging.getLogger(__name__)

HOT_C = 40.0  # at or above this the kiln isn't safe to open
IDLE_HOT_S = 15.0  # how often to read while hot (or not known yet)
IDLE_COOL_S = 60.0  # ...and once it has cooled
IDLE_OFF_S = 30.0  # ...and while the kiln is switched off
READ_WINDOW_S = 4.0  # long enough to catch the temperature between IdLEs


def is_dark(raw_text: str) -> bool:
    return raw_text.replace(".", "").strip() in ("", "?")


def is_hot(temp: float, celsius: bool) -> bool:
    temp_c = temp if celsius else (temp - 32.0) * 5.0 / 9.0
    return temp_c >= HOT_C


@dataclass
class KilnTemp:
    temp: Optional[float] = None  # last reading, in the controller's unit
    unit: str = "C"
    hot: Optional[bool] = None
    at: Optional[float] = None  # epoch s of that reading
    off: bool = False  # the display is dark: the kiln is switched off


class KilnTemperature:
    def __init__(
        self,
        vision: KilnVision,
        publish: Callable[[dict], None],
        celsius: Callable[[], bool],
        busy: Callable[[], bool],
        firing_temp: Callable[[], Optional[float]],
    ):
        self._vision = vision
        self._publish = publish
        self._celsius = celsius
        self._busy = busy  # a sequence or a firing has the controller
        self._firing_temp = firing_temp
        self._lock = threading.Lock()
        self._latest = KilnTemp()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def status(self) -> dict:
        with self._lock:
            return asdict(self._latest)

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="kiln-temp", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while True:
            try:
                self.poll()
            except Exception:  # noqa: BLE001 - keep going through a bad read
                log.exception("kiln temperature: read failed")
            latest = self._latest
            if latest.off:
                wait = IDLE_OFF_S
            elif latest.hot is False:
                wait = IDLE_COOL_S
            else:
                wait = IDLE_HOT_S
            if self._stop.wait(wait):
                return

    def poll(self) -> Optional[float]:
        """Take one reading (or the firing monitor's) and publish it."""

        if self._busy():
            temp, dark = self._firing_temp(), False
        else:
            temp, dark = self._read()
        if temp is not None:
            self._record(temp)
        elif dark:
            self._record_off()
        return temp

    def _read(self) -> tuple[Optional[float], bool]:
        """The temperature if one shows within READ_WINDOW_S, and whether
        the display was dark the whole time."""

        deadline = time.monotonic() + READ_WINDOW_S
        dark = True
        while time.monotonic() < deadline:
            state = self._vision.read()
            if state.message is None and state.numeric_value is not None:
                return state.numeric_value, False
            dark = dark and is_dark(state.raw_text)
        return None, dark

    def _record_off(self) -> None:
        with self._lock:
            if self._latest.off:
                return
            self._latest.off = True
        self._emit()

    def _record(self, temp: float) -> None:
        celsius = self._celsius()
        with self._lock:
            self._latest = KilnTemp(
                temp=temp, unit="C" if celsius else "F", hot=is_hot(temp, celsius), at=time.time()
            )
        self._emit()

    def _emit(self) -> None:
        try:
            self._publish(self.status())
        except Exception:  # noqa: BLE001 - a UI hiccup mustn't stop the readings
            log.exception("kiln temperature: publish failed")
