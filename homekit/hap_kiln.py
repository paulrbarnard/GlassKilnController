#!/usr/bin/env python3
"""The kiln in Apple Home, for the Glass Kiln Controller app.

Runs in its own container with host networking, because HomeKit needs
Bonjour/mDNS on the LAN and App Lab containers use bridge networking. It
reads the App Lab app on http://127.0.0.1:7000 every POLL_S:

  - /api/kiln_temp   the kiln temperature, hot/off, the cooling estimate
  - /api/firing      whether a firing is running, and which profile
  - /api/profiles/x  the profile's peak temperature (the heater's target)

and shows the kiln as two accessories' worth of services:

  - a Heater ("Kiln"): Heating while a firing runs, Cooling after one while
    the kiln is at or above 40 °C, Idle below that, Off when the kiln is
    switched off. It's read-only: HomeKit has no read-only heater, so any
    change made in the Home app is put straight back and nothing reaches
    the kiln.
  - an occupancy sensor ("Kiln hot"): detected at or above 40 °C. Turn on
    its notifications in the Home app to hear when the kiln is safe to open.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import socket
import threading
import time
import urllib.request
from typing import Optional

from pyhap.accessory import Accessory
from pyhap.accessory_driver import AccessoryDriver
from pyhap.const import CATEGORY_HEATER

log = logging.getLogger("hap-kiln")

APP_URL = os.environ.get("APP_URL", "http://127.0.0.1:7000")
NAME = os.environ.get("ACCESSORY_NAME", "Kiln")
STATE_FILE = os.environ.get("STATE_FILE", "/state/accessory.state")
ADDRESS = os.environ.get("HOST_IP", "192.168.0.74")
PORT = int(os.environ.get("HAP_PORT", "51826"))
POLL_S = 10.0

# HomeKit's heater temperature range is about 0-100 °C by default; a kiln
# needs its full range (the GM LT goes to 1288 °C / 2350 °F).
TEMP_PROPS = {"minValue": -20, "maxValue": 1300, "minStep": 1}

# HeaterCooler characteristic values (HAP spec)
INACTIVE, IDLE, HEATING, COOLING = 0, 1, 2, 3  # CurrentHeaterCoolerState
TARGET_HEAT = 1  # TargetHeaterCoolerState
SAFE_C = 40  # below this the app says "Safe to open" (python/kiln/kiln_temp.py HOT_C)
ACTIVE_PHASES = ("programming", "firing", "stopping")


def http_json(path: str, timeout: float = 5):
    with urllib.request.urlopen(APP_URL + path, timeout=timeout) as resp:
        return json.loads(resp.read())


def to_c(temp: float, unit: str) -> float:
    return (temp - 32.0) * 5.0 / 9.0 if unit == "F" else temp


class Kiln(Accessory):
    category = CATEGORY_HEATER

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.set_info_service(
            manufacturer="Glass Kiln Controller",
            model="Skutt GM LT + UNO Q",
            serial_number="steffi",
            firmware_revision="1.0",
        )

        heater = self.add_preload_service(
            "HeaterCooler", chars=["HeatingThresholdTemperature", "Name"]
        )
        heater.configure_char("Name", value=NAME)
        self.char_active = heater.configure_char("Active", value=0, setter_callback=self._undo)
        self.char_state = heater.configure_char("CurrentHeaterCoolerState", value=INACTIVE)
        # Heat is its only mode: set the value before narrowing the valid
        # values, or HAP-python rejects its default of 0 (Auto).
        self.char_target_state = heater.configure_char(
            "TargetHeaterCoolerState", value=TARGET_HEAT, setter_callback=self._undo
        )
        self.char_target_state.override_properties(valid_values={"Heat": TARGET_HEAT})
        self.char_temp = heater.configure_char("CurrentTemperature", value=20, properties=TEMP_PROPS)
        self.char_target = heater.configure_char(
            "HeatingThresholdTemperature", value=20, properties=TEMP_PROPS, setter_callback=self._undo
        )

        hot = self.add_preload_service("OccupancySensor", chars=["Name"])
        hot.configure_char("Name", value="Kiln hot")
        self.char_hot = hot.configure_char("OccupancyDetected", value=0)

        self._shown: dict = {}
        self._peaks: dict[str, float] = {}

    # -- read-only: put any change from the Home app straight back -----------

    def _undo(self, _value) -> None:
        threading.Timer(0.5, self._republish).start()

    def _republish(self) -> None:
        for char, key in (
            (self.char_active, "active"),
            (self.char_target_state, "target_state"),
            (self.char_target, "target"),
        ):
            if key in self._shown:
                char.set_value(self._shown[key])

    # -- following the app ------------------------------------------------------

    def _peak_c(self, profile_id: Optional[str]) -> Optional[float]:
        if not profile_id:
            return None
        if profile_id not in self._peaks:
            profile = http_json(f"/api/profiles/{profile_id}")
            segments = profile.get("segments") or []
            if not segments:
                return None
            self._peaks[profile_id] = to_c(max(s["target_temp_f"] for s in segments), "F")
        return self._peaks[profile_id]

    def update(self) -> None:
        reading = http_json("/api/kiln_temp")
        firing = http_json("/api/firing")
        firing_now = firing.get("phase") in ACTIVE_PHASES
        temp = reading.get("temp")
        if temp is None and firing_now:
            temp, unit = firing.get("temp"), firing.get("unit", "C")
        else:
            unit = reading.get("unit", "C")
        temp_c = None if temp is None else to_c(float(temp), unit)

        if firing_now:
            state, active = HEATING, 1
        elif reading.get("off"):
            state, active = INACTIVE, 0
        elif reading.get("hot"):
            state, active = COOLING, 1
        else:
            state, active = IDLE, 1
        shown_temp = round(temp_c) if temp_c is not None else self._shown.get("temp", 20)
        # The heater's "target": the profile's peak while firing, the safe
        # temperature while cooling, otherwise just the temperature.
        if state == HEATING:
            peak = self._peak_c(firing.get("profile_id"))
            target = round(peak) if peak is not None else shown_temp
        elif state == COOLING:
            target = SAFE_C
        else:
            target = shown_temp
        shown = {
            "active": active,
            "state": state,
            "target_state": TARGET_HEAT,
            "temp": shown_temp,
            "target": target,
            "hot": 1 if reading.get("hot") else 0,
        }
        for char, key in (
            (self.char_active, "active"),
            (self.char_state, "state"),
            (self.char_target_state, "target_state"),
            (self.char_temp, "temp"),
            (self.char_target, "target"),
            (self.char_hot, "hot"),
        ):
            if self._shown.get(key) != shown[key]:
                char.set_value(shown[key])
        if (self._shown.get("state"), self._shown.get("hot")) != (state, shown["hot"]):
            log.info(
                "kiln %s, %s°C%s",
                {INACTIVE: "off", IDLE: "idle", HEATING: "heating", COOLING: "cooling"}[state],
                shown["temp"],
                ", hot" if shown["hot"] else "",
            )
        self._shown = shown

    @Accessory.run_at_interval(POLL_S)
    def run(self) -> None:
        try:
            self.update()
            self._failures = 0
        except Exception as exc:  # noqa: BLE001 - the app may be restarting
            self._failures = getattr(self, "_failures", 0) + 1
            # Log the first failure, then about every 10 minutes.
            if self._failures == 1 or self._failures % 60 == 0:
                log.warning("app not reachable (%s); retrying every %.0f s", exc, POLL_S)


def setup_code() -> bytes:
    """A setup code kept next to the pairing state. HAP-python otherwise picks
    a new random one on every start, which is confusing before pairing."""
    path = os.path.join(os.path.dirname(STATE_FILE), "setup_code")
    if os.path.exists(path):
        with open(path, "rb") as fh:
            return fh.read().strip()
    from pyhap.util import generate_pincode

    code = generate_pincode()
    with open(path, "wb") as fh:
        fh.write(code)
    return code


def wait_for_address() -> None:
    """Block until ADDRESS is assigned to this host. After a power cut the
    container can start before the network is up, and HAP-python then fails
    to bind but keeps running, so Docker never restarts it."""
    logged = False
    while True:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind((ADDRESS, 0))
            return
        except OSError as exc:
            if not logged:
                log.warning("waiting for %s to come up (%s)", ADDRESS, exc)
                logged = True
            time.sleep(2)


def watchdog() -> None:
    """Exit if the HAP server stops accepting connections, so Docker's
    restart policy brings it back."""
    time.sleep(60)
    misses = 0
    while True:
        try:
            with socket.create_connection((ADDRESS, PORT), timeout=5):
                misses = 0
        except OSError as exc:
            misses += 1
            log.warning("HAP port %s:%s not answering (%s), miss %d/3", ADDRESS, PORT, exc, misses)
            if misses >= 3:
                log.error("HAP server is down; exiting so Docker restarts the container")
                os._exit(1)
        time.sleep(30)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    wait_for_address()
    driver = AccessoryDriver(port=PORT, persist_file=STATE_FILE, address=ADDRESS, pincode=setup_code())
    driver.add_accessory(accessory=Kiln(driver, NAME))
    if not driver.state.paired:
        log.info("HomeKit setup code: %s", driver.state.pincode.decode())
    threading.Thread(target=watchdog, name="watchdog", daemon=True).start()
    signal.signal(signal.SIGTERM, driver.signal_handler)
    driver.start()


if __name__ == "__main__":
    main()
