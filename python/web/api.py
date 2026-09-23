"""Route wiring between the touchscreen web UI and ``kiln/``.

Built on ``arduino.app_bricks.web_ui.WebUI`` — confirmed on real hardware
(an Uno Q named "Steffi"): ``expose_api(method, path, handler)`` is FastAPI
underneath (a ``{param}`` path segment binds to a same-named handler
argument; a POST body arrives as a plain ``dict`` handler argument),
``send_message(event, data)`` pushes to connected browser clients over
socket.io.

``register_routes`` takes a duck-typed ``ui`` (only needs ``expose_api`` and
``send_message``) rather than importing ``WebUI`` directly, so this module
— and every handler it defines — is exercisable with a fake ``ui`` double
and ``MockBridge``/``MockVision``, no Uno Q or browser required (see
python/tests/test_web_api.py).

Long-running work (a firing can legitimately run for hours; Edit Mode
actions each involve several camera-polling round trips) always happens on
its own background thread — a request handler here must never block for
that duration, regardless of whatever concurrency model the brick's HTTP
server uses internally.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable, Protocol

from kiln.profiles import Profile, ProfileKind, ProfileLibrary
from kiln.sequencer import Sequencer, SequenceError

log = logging.getLogger(__name__)


class UILike(Protocol):
    def expose_api(self, method: str, path: str, handler: Callable) -> None: ...
    def send_message(self, event: str, data: dict) -> None: ...


@dataclass
class RunState:
    running: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)


def register_routes(ui: UILike, sequencer: Sequencer, library: ProfileLibrary) -> None:
    state = RunState()

    def push_progress(message: str) -> None:
        ui.send_message("progress", {"message": message})

    sequencer.on_progress = push_progress

    def run_in_background(task: Callable[[], None]) -> None:
        def guarded() -> None:
            try:
                task()
            except SequenceError as exc:
                log.warning("sequence error: %s", exc)
                ui.send_message("error", {"message": str(exc)})
            except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
                log.exception("unexpected error in background task")
                ui.send_message("error", {"message": f"unexpected error: {exc}"})

        threading.Thread(target=guarded, daemon=True).start()

    # -- profiles -------------------------------------------------------

    def list_profiles():
        # Full _detail (segments included), not just _summary: the home
        # screen shows a glanceable peak-temperature number per card, which
        # needs each profile's resolved segments up front rather than a
        # second round trip per card.
        return {"profiles": [_detail(p) for p in library.all()]}

    def get_profile(profile_id: str):
        try:
            profile = library.get(profile_id)
        except KeyError:
            return {"error": f"no such profile: {profile_id}"}
        return _detail(profile)

    def save_profile(body: dict):
        try:
            profile = Profile.from_dict(
                {**body, "kind": ProfileKind.RAMP_HOLD.value, "source": "user"}
            )
            library.save_user_profile(profile)
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        return _detail(profile)

    def delete_profile(profile_id: str):
        try:
            library.delete_user_profile(profile_id)
        except ValueError as exc:
            return {"error": str(exc)}
        return {"ok": True}

    def activate_profile(profile_id: str, body: dict):
        try:
            profile = library.set_active_slot(profile_id, body.get("slot"))
        except ValueError as exc:
            return {"error": str(exc)}
        return _detail(profile)

    def deactivate_profile(profile_id: str):
        try:
            profile = library.set_active_slot(profile_id, None)
        except ValueError as exc:
            return {"error": str(exc)}
        return _detail(profile)

    def set_mgf_override(profile_id: str, body: dict):
        # Only the process (segment 2) temp/hold are ever overridable — see
        # ProfileLibrary.set_mgf_override / docs/controller_protocol.md.
        # Values here are °F, matching every other segment-bearing
        # endpoint; the frontend converts from its °C inputs before POSTing.
        try:
            profile = library.set_mgf_override(
                profile_id,
                temp_override_f=body.get("temp_override_f"),
                hold_override_minutes=body.get("hold_override_minutes"),
            )
        except ValueError as exc:
            return {"error": str(exc)}
        return _detail(profile)

    # -- run lifecycle ----------------------------------------------------

    def run_profile(profile_id: str):
        try:
            profile = library.get(profile_id)
        except KeyError:
            return {"error": f"no such profile: {profile_id}"}

        with state.lock:
            if state.running:
                return {"error": "a firing is already in progress"}
            state.running = True

        def task() -> None:
            try:
                sequencer.run_profile(profile)
                push_progress("Program started successfully — kiln is running.")
            finally:
                with state.lock:
                    state.running = False

        run_in_background(task)
        return {"ok": True, "profile": _summary(profile)}

    def stop():
        run_in_background(sequencer.stop)
        return {"ok": True}

    def skip_segment():
        run_in_background(sequencer.skip_segment)
        return {"ok": True}

    def add_time():
        run_in_background(sequencer.add_time)
        return {"ok": True}

    def add_temperature():
        run_in_background(sequencer.add_temperature)
        return {"ok": True}

    def status():
        return {"bridge_ok": sequencer.bridge.ping(), "running": state.running}

    # -- settings (OPTIONS mode, p.22) -------------------------------------

    def toggle_mgf():
        run_in_background(sequencer.toggle_mgf)
        return {"ok": True}

    def set_temperature_scale(body: dict):
        celsius = bool(body.get("celsius", False))
        run_in_background(lambda: sequencer.set_temperature_scale(celsius))
        return {"ok": True}

    def factory_reset():
        # UI must get explicit human confirmation before calling this — see
        # Sequencer.factory_reset docstring. Wipes stored PRO programs 1-4.
        run_in_background(sequencer.factory_reset)
        return {"ok": True}

    ui.expose_api("GET", "/api/profiles", list_profiles)
    ui.expose_api("GET", "/api/profiles/{profile_id}", get_profile)
    ui.expose_api("POST", "/api/profiles", save_profile)
    ui.expose_api("DELETE", "/api/profiles/{profile_id}", delete_profile)
    ui.expose_api("POST", "/api/profiles/{profile_id}/activate", activate_profile)
    ui.expose_api("POST", "/api/profiles/{profile_id}/deactivate", deactivate_profile)
    ui.expose_api("POST", "/api/profiles/{profile_id}/mgf_override", set_mgf_override)
    ui.expose_api("POST", "/api/run/{profile_id}", run_profile)
    ui.expose_api("POST", "/api/stop", stop)
    ui.expose_api("POST", "/api/skip", skip_segment)
    ui.expose_api("POST", "/api/add_time", add_time)
    ui.expose_api("POST", "/api/add_temp", add_temperature)
    ui.expose_api("GET", "/api/status", status)
    ui.expose_api("POST", "/api/settings/mgf", toggle_mgf)
    ui.expose_api("POST", "/api/settings/scale", set_temperature_scale)
    ui.expose_api("POST", "/api/settings/reset", factory_reset)


def _summary(profile: Profile) -> dict:
    d = {
        "id": profile.id,
        "name": profile.name,
        "description": profile.description,
        "kind": profile.kind.value,
        "source": profile.source,
    }
    # process/speed let the UI pick an icon without parsing the id string.
    if profile.glassfire is not None:
        d["process"] = profile.glassfire.process.value
        d["speed"] = profile.glassfire.speed.value
        d["temp_override_f"] = profile.glassfire.temp_override_f
        d["hold_override_minutes"] = profile.glassfire.hold_override_minutes
    if profile.kind is ProfileKind.RAMP_HOLD:
        d["pro_slot"] = profile.pro_slot
    return d


def _detail(profile: Profile) -> dict:
    d = _summary(profile)
    d["segments"] = [
        {
            "rate_f_per_hr": s.rate_f_per_hr,
            "target_temp_f": s.target_temp_f,
            "hold_minutes": s.hold_minutes,
        }
        for s in profile.resolved_segments()
    ]
    return d
