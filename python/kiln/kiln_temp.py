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

While a hot kiln cools with nothing running (after a firing), it estimates
how long until it's below HOT_C: Newton's law of cooling, T = Ta + (T0 - Ta)
e^(-kt), fitted to the last COOLING_WINDOW_S of readings. The room
temperature Ta is the kiln's last cool reading (it starts a firing at room
temperature), else ASSUMED_ROOM_C. A real kiln slows down more than the law
says as it cools, so fitting only recent readings keeps the estimate honest
as it goes.
"""

from __future__ import annotations

import logging
import math
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
COOLING_WINDOW_S = 45 * 60.0  # readings the cooling estimate is fitted to
COOLING_MIN_SPAN_S = 10 * 60.0  # no estimate until it has cooled this long...
COOLING_MIN_DROP_C = 5.0  # ...and by this much
ASSUMED_ROOM_C = 20.0  # room temperature before the kiln has been read cool
COOLING_MAX_S = 48 * 3600.0  # beyond this the estimate isn't worth showing


def is_dark(raw_text: str) -> bool:
    return raw_text.replace(".", "").strip() in ("", "?")


def to_c(temp: float, celsius: bool) -> float:
    return temp if celsius else (temp - 32.0) * 5.0 / 9.0


def is_hot(temp: float, celsius: bool) -> bool:
    return to_c(temp, celsius) >= HOT_C


def cooling_eta_s(samples: list[tuple[float, float]], room_c: float) -> Optional[float]:
    """Seconds from the last sample until the kiln is below HOT_C, from
    (epoch s, °C) readings of it cooling; None until there's enough to go on.
    Fits ln(T - room) against time (a straight line under Newton's law)."""

    if len(samples) < 3 or room_c >= HOT_C - 1.0:
        return None
    (t0, first), (t_last, last) = samples[0], samples[-1]
    if t_last - t0 < COOLING_MIN_SPAN_S or first - last < COOLING_MIN_DROP_C:
        return None
    points = [(t - t0, math.log(temp - room_c)) for t, temp in samples if temp - room_c > 0.5]
    n = len(points)
    if n < 3:
        return None
    mean_t = sum(t for t, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    var_t = sum((t - mean_t) ** 2 for t, _ in points)
    if var_t <= 0:
        return None
    k = -sum((t - mean_t) * (y - mean_y) for t, y in points) / var_t
    if k <= 0:
        return None
    if last < HOT_C:
        return 0.0
    eta = math.log((last - room_c) / (HOT_C - room_c)) / k
    return eta if eta <= COOLING_MAX_S else None


@dataclass
class KilnTemp:
    temp: Optional[float] = None  # last reading, in the controller's unit
    unit: str = "C"
    hot: Optional[bool] = None
    at: Optional[float] = None  # epoch s of that reading
    off: bool = False  # the display is dark: the kiln is switched off
    # While a hot kiln cools with nothing running: estimated epoch s at
    # which it will be below HOT_C, and whether an estimate is being worked
    # out (cooling, but not enough readings yet).
    cool_by: Optional[float] = None
    cooling: bool = False


class KilnTemperature:
    def __init__(
        self,
        vision: KilnVision,
        publish: Callable[[dict], None],
        celsius: Callable[[], bool],
        busy: Callable[[], bool],
        firing_temp: Callable[[], Optional[float]],
        clock: Callable[[], float] = time.time,
    ):
        self._vision = vision
        self._publish = publish
        self._celsius = celsius
        self._busy = busy  # a sequence or a firing has the controller
        self._firing_temp = firing_temp
        self._clock = clock
        self._room_c = ASSUMED_ROOM_C
        self._cooling: list[tuple[float, float]] = []  # (epoch s, °C) since nothing ran
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

        busy = self._busy()
        if busy:
            temp, dark = self._firing_temp(), False
        else:
            temp, dark = self._read()
        if temp is not None:
            self._record(temp, cooling=not busy)
        elif dark:
            self._record_off()
        return temp

    def _read(self) -> tuple[Optional[float], bool]:
        """The temperature if one shows within READ_WINDOW_S, and whether
        the display was dark the whole time."""

        deadline = None  # from the first frame: opening the camera takes ~6 s
        dark = True
        while deadline is None or time.monotonic() < deadline:
            state = self._vision.read()
            if deadline is None:
                deadline = time.monotonic() + READ_WINDOW_S
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

    def _record(self, temp: float, cooling: bool = True) -> None:
        celsius = self._celsius()
        now = self._clock()
        temp_c = to_c(temp, celsius)
        hot = temp_c >= HOT_C
        cool_by = None
        with self._lock:
            if not hot:
                self._room_c = min(temp_c, HOT_C - 5.0)
                self._cooling = []
            elif not cooling or (self._cooling and temp_c > self._cooling[-1][1] + 2.0):
                self._cooling = []  # firing, or heating up again: start over
            if hot and cooling:
                self._cooling.append((now, temp_c))
                self._cooling = [(t, c) for t, c in self._cooling if now - t <= COOLING_WINDOW_S]
                eta = cooling_eta_s(self._cooling, self._room_c)
                cool_by = None if eta is None else now + eta
            self._latest = KilnTemp(
                temp=temp,
                unit="C" if celsius else "F",
                hot=hot,
                at=now,
                cool_by=cool_by,
                cooling=hot and cooling,
            )
        self._emit()

    def _emit(self) -> None:
        try:
            self._publish(self.status())
        except Exception:  # noqa: BLE001 - a UI hiccup mustn't stop the readings
            log.exception("kiln temperature: publish failed")
