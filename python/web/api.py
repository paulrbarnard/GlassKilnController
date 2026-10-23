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
import time
from dataclasses import dataclass, field
from typing import Callable, Optional, Protocol

from kiln.firing import FiringMonitor
from kiln.profiles import Profile, ProfileKind, ProfileLibrary
from kiln.sequencer import Sequencer, SequenceAborted, SequenceError
from kiln.settings import DEFAULT_PATH as SETTINGS_PATH
from kiln.settings import AppSettings

log = logging.getLogger(__name__)


class UILike(Protocol):
    def expose_api(self, method: str, path: str, handler: Callable) -> None: ...
    def send_message(self, event: str, data: dict) -> None: ...


@dataclass
class RunState:
    """Which controller action (if any) is pressing buttons right now. Only
    one may run at a time: two sequences' presses interleaving left the
    controller in the MGF option when a double-tapped unit change ran
    twice at once (2026-10-22)."""

    busy: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def running(self) -> bool:
        return self.busy is not None


def register_routes(
    ui: UILike,
    sequencer: Sequencer,
    library: ProfileLibrary,
    settings: AppSettings | None = None,
    settings_path=SETTINGS_PATH,
) -> None:
    state = RunState()
    settings = settings or AppSettings()
    sequencer.controller_celsius = settings.celsius

    def push_progress(message: str) -> None:
        log.info("progress: %s", message)
        ui.send_message("progress", {"message": message})

    sequencer.on_progress = push_progress

    monitor = FiringMonitor(
        sequencer.vision,
        publish=lambda status: ui.send_message("firing", status),
        celsius=lambda: settings.celsius,
    )

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

    def run_exclusive(
        what: str, task: Callable[[], None], before_start: Optional[Callable[[], None]] = None
    ) -> dict:
        """Run a button-pressing ``task`` in the background unless another
        controller action is already running. ``before_start`` runs once
        the controller is claimed, before the task's thread starts."""

        with state.lock:
            if state.busy is not None:
                return {"error": f"the controller is busy ({state.busy}); try again when it has finished"}
            state.busy = what
        if before_start:
            before_start()

        def wrapped() -> None:
            try:
                task()
            finally:
                with state.lock:
                    state.busy = None

        run_in_background(wrapped)
        return {"ok": True}

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

        if monitor.active:
            return {"error": "a firing is already in progress"}

        def task() -> None:
            try:
                sequencer.run_profile(profile)
                monitor.firing_started()
                push_progress("Program started successfully — kiln is running.")
            except SequenceAborted:
                pass  # stop() is handling it and reports the outcome
            except SequenceError as exc:
                # Never leave the controller half-programmed: back out to
                # IdLE (which also stops it if it did start), then report.
                push_progress(f"Programming failed ({exc}); returning the controller to IdLE")
                try:
                    sequencer.stop()
                    monitor.finish("error", f"Programming failed and was backed out: {exc}")
                except SequenceError as stop_exc:
                    monitor.finish("error", f"Programming failed ({exc}) and backing out failed too: {stop_exc}")
                raise

        def before_start() -> None:
            monitor.begin(profile.id, profile.name)
            sequencer.abort.clear()

        result = run_exclusive("starting a firing", task, before_start)
        if "error" in result:
            return result
        return {"ok": True, "profile": _summary(profile)}

    def stop():
        """Stop whatever is happening: abort any programming in progress,
        then hold ENTER until the display confirms StOP/IdLE."""

        sequencer.abort.set()
        monitor.stopping()

        def task() -> None:
            # Let a programming sequence on another thread notice the abort
            # and finish its current press before we press anything.
            deadline = time.monotonic() + 15.0
            while time.monotonic() < deadline:
                with state.lock:
                    if state.busy is None:
                        state.busy = "stopping"
                        break
                time.sleep(0.2)
            else:
                with state.lock:  # a press is stuck; stop regardless
                    state.busy = "stopping"
            sequencer.abort.clear()
            try:
                result = sequencer.stop()
            except SequenceError as exc:
                monitor.finish("error", str(exc))
                raise
            finally:
                with state.lock:
                    state.busy = None
            monitor.finish("stopped", f"Stopped: the controller shows {result.raw_text.strip()!r}")

        run_in_background(task)
        return {"ok": True}

    def firing_status():
        return monitor.status()

    # -- the kiln's own Ramp & Hold programs (PRO slots 1-5) ----------------

    kiln_programs: dict[int, dict] = {}

    def _slot_entry(slot: int, program=None, error: Optional[str] = None) -> dict:
        entry: dict = {"slot": slot}
        if error:
            entry["error"] = error
        if program is not None:
            entry.update(program.to_dict())
        holder = library.active_slot_holder(slot)
        if holder is not None:
            entry["app_profile"] = {"id": holder.id, "name": holder.name}
            if program is not None:
                entry["app_profile"]["matches"] = _same_segments(holder.segments or [], program.segments)
        return entry

    def read_kiln_programs(body: dict | None = None):
        """Read the kiln's PRO slots (default all five) into the app. Each
        slot is read twice and must match; the controller is left at IdLE
        and nothing is changed or started."""

        slots = [int(s) for s in (body or {}).get("slots") or [1, 2, 3, 4, 5]]
        if any(not 1 <= s <= 5 for s in slots):
            return {"error": "slots must be 1-5"}

        def task() -> None:
            for slot in slots:
                try:
                    kiln_programs[slot] = _slot_entry(slot, program=sequencer.read_program(slot))
                except SequenceAborted:
                    raise
                except SequenceError as exc:
                    kiln_programs[slot] = _slot_entry(slot, error=str(exc))
                ui.send_message("kiln_programs", {"programs": list_kiln_programs()["programs"], "done": False})
            ui.send_message("kiln_programs", {"programs": list_kiln_programs()["programs"], "done": True})
            push_progress(f"Read {len(slots)} program slot(s) from the kiln.")

        return run_exclusive("reading the kiln's programs", task)

    def list_kiln_programs():
        with state.lock:
            busy = state.busy
        return {
            "reading": busy == "reading the kiln's programs",
            "programs": [kiln_programs[s] for s in sorted(kiln_programs)],
        }

    def write_kiln_program(slot: str, body: dict | None = None):
        """Enter an app custom profile into PRO ``slot`` on the kiln, then
        read the slot back and only report success if it matches. On
        success the profile becomes the one assigned to that slot."""

        slot_n = int(slot)
        profile_id = (body or {}).get("profile_id")
        try:
            profile = library.get(profile_id)
        except KeyError:
            return {"error": f"no such profile: {profile_id}"}
        if profile.kind is not ProfileKind.RAMP_HOLD or not profile.segments:
            return {"error": "only custom Ramp & Hold profiles can be sent to the kiln"}
        if not 1 <= slot_n <= 5:
            return {"error": "slot must be 1-5"}

        def task() -> None:
            sequencer.write_program(slot_n, profile.segments)
            push_progress(f"Checking slot {slot_n} by reading it back")
            program = sequencer.read_program(slot_n)
            if not _same_segments(profile.segments, program.segments):
                kiln_programs[slot_n] = _slot_entry(slot_n, program=program)
                ui.send_message("kiln_programs", {"programs": list_kiln_programs()["programs"], "done": True})
                raise SequenceError(f"slot {slot_n} read back differently from “{profile.name}”")
            library.set_active_slot(profile.id, slot_n)
            kiln_programs[slot_n] = _slot_entry(slot_n, program=program)
            ui.send_message("kiln_programs", {"programs": list_kiln_programs()["programs"], "done": True})
            push_progress(f"“{profile.name}” is now in kiln slot {slot_n} (checked).")

        return run_exclusive(f"writing kiln slot {slot_n}", task)

    def import_kiln_program(slot: str, body: dict | None = None):
        """Save a slot read from the kiln as an app custom profile, active
        in that slot (replacing whichever profile held it)."""

        entry = kiln_programs.get(int(slot))
        if not entry or "segments" not in entry:
            return {"error": f"slot {slot} hasn't been read from the kiln"}
        name = ((body or {}).get("name") or f"Kiln slot {slot}").strip()
        profile_id = f"kiln_slot_{slot}"
        try:
            profile = Profile.from_dict(
                {
                    "id": profile_id,
                    "name": name,
                    "description": f"Read from the kiln's program slot {slot}.",
                    "kind": ProfileKind.RAMP_HOLD.value,
                    "source": "user",
                    "segments": entry["segments"],
                }
            )
            library.save_user_profile(profile)
            profile = library.set_active_slot(profile_id, int(slot))
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}
        kiln_programs[int(slot)] = {**entry, "app_profile": {"id": profile.id, "name": profile.name, "matches": True}}
        return _detail(profile)

    def skip_segment():
        return run_exclusive("skipping a segment", sequencer.skip_segment)

    def add_time():
        return run_exclusive("adding time", sequencer.add_time)

    def add_temperature():
        return run_exclusive("adding temperature", sequencer.add_temperature)

    def status():
        return {"bridge_ok": sequencer.bridge.ping(), "running": state.running}

    # -- settings (OPTIONS mode, p.22) -------------------------------------

    def toggle_mgf():
        return run_exclusive("toggling MGF", sequencer.toggle_mgf)

    def get_settings():
        return {"temperature_scale": settings.temperature_scale}

    def controller_unit():
        """Which unit the GM LT itself is in, read from its °C indicator
        dot (takes a few seconds if the camera has to start). "unit" is
        null if the display is blank or the controller is busy."""

        with state.lock:
            busy = state.busy
        if busy is not None:
            return {"unit": None, "busy": busy}
        shown = sequencer.read_controller_celsius()
        return {"unit": None if shown is None else ("C" if shown else "F")}

    def set_temperature_scale(body: dict):
        """Set the app's temperature unit and the GM LT's to match. The app
        only switches once the controller has, so the two can't disagree
        (Sequencer.set_temperature_scale refuses unless it sees IdLE)."""

        celsius = bool(body.get("celsius", False))

        def task() -> None:
            sequencer.set_temperature_scale(celsius)
            settings.temperature_scale = "C" if celsius else "F"
            settings.save(settings_path)
            ui.send_message("settings", {"temperature_scale": settings.temperature_scale})
            push_progress(f"Temperature unit set to °{settings.temperature_scale} on the app and the controller.")

        return run_exclusive("changing the temperature unit", task)

    def factory_reset():
        # UI must get explicit human confirmation before calling this — see
        # Sequencer.factory_reset docstring. Wipes stored PRO programs 1-4.
        return run_exclusive("factory reset", sequencer.factory_reset)

    ui.expose_api("GET", "/api/profiles", list_profiles)
    ui.expose_api("GET", "/api/profiles/{profile_id}", get_profile)
    ui.expose_api("POST", "/api/profiles", save_profile)
    ui.expose_api("DELETE", "/api/profiles/{profile_id}", delete_profile)
    ui.expose_api("POST", "/api/profiles/{profile_id}/activate", activate_profile)
    ui.expose_api("POST", "/api/profiles/{profile_id}/deactivate", deactivate_profile)
    ui.expose_api("POST", "/api/profiles/{profile_id}/mgf_override", set_mgf_override)
    ui.expose_api("POST", "/api/run/{profile_id}", run_profile)
    ui.expose_api("POST", "/api/stop", stop)
    ui.expose_api("GET", "/api/firing", firing_status)
    ui.expose_api("POST", "/api/kiln_programs/read", read_kiln_programs)
    ui.expose_api("GET", "/api/kiln_programs", list_kiln_programs)
    ui.expose_api("POST", "/api/kiln_programs/{slot}/import", import_kiln_program)
    ui.expose_api("POST", "/api/kiln_programs/{slot}/write", write_kiln_program)
    ui.expose_api("POST", "/api/skip", skip_segment)
    ui.expose_api("POST", "/api/add_time", add_time)
    ui.expose_api("POST", "/api/add_temp", add_temperature)
    ui.expose_api("GET", "/api/status", status)
    ui.expose_api("POST", "/api/settings/mgf", toggle_mgf)
    ui.expose_api("GET", "/api/settings", get_settings)
    ui.expose_api("GET", "/api/settings/controller_unit", controller_unit)
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


def _same_segments(app_segments, kiln_segments, temp_tol_f: float = 2.0, rate_tol_f: float = 2.0) -> bool:
    """Whether an app profile's segments are what the kiln holds, allowing
    for whole-degree rounding when the controller is in °C."""

    if len(app_segments) != len(kiln_segments):
        return False
    return all(
        abs(a.target_temp_f - k.target_temp_f) <= temp_tol_f
        and abs(a.rate_f_per_hr - k.rate_f_per_hr) <= rate_tol_f
        and round(a.hold_minutes) == round(k.hold_minutes)
        for a, k in zip(app_segments, kiln_segments)
    )
