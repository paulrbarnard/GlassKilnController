"""App settings that persist on the board: currently the temperature unit.

Stored as JSON in python/state/ (not in git, and skipped by
scripts/deploy.sh like the user profiles), so a redeploy doesn't reset them.

The temperature unit is the user's choice for the whole app: the UI shows
and edits temperatures in it, and the GM LT is set to the same unit
(OPTIONS > CHG°), so the controller's own display agrees. Profiles are
still stored in °F; the sequencer converts when it dials values into the
controller (see Sequencer.controller_celsius).
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path

log = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "state" / "settings.json"


@dataclass
class AppSettings:
    # "C" or "F". Celsius by default: it's what this build's owner uses, and
    # what the controller was set to when the app was first run against it.
    temperature_scale: str = "C"

    @property
    def celsius(self) -> bool:
        return self.temperature_scale == "C"

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "AppSettings":
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return cls()
        except (OSError, ValueError) as exc:
            log.warning("unreadable settings at %s (%s); using defaults", path, exc)
            return cls()
        scale = str(data.get("temperature_scale", "C")).upper()
        return cls(temperature_scale=scale if scale in ("C", "F") else "C")

    def save(self, path: Path = DEFAULT_PATH) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


def f_to_c(temp_f: float) -> float:
    return (temp_f - 32.0) * 5.0 / 9.0


def rate_f_to_c(rate_f_per_hr: float) -> float:
    """A rate of change has no offset: 9 °F/hr is 5 °C/hr."""

    return rate_f_per_hr * 5.0 / 9.0
