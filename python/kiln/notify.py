"""iPhone push notifications through Pushover (https://pushover.net).

Apple Home can show the kiln, but its notifications for a sensor can't say
what they mean (it wrote "Workshop occupancy detected" for "the kiln is safe
to open"), so the app sends its own: a firing finishing, programming
failing, and the kiln being safe to open. Two keys, kept only in
state/notify.json on the board (never in git):

  {"pushover_token": "<the Kiln application's API token>",
   "pushover_user": "<your user key>"}

With no keys, nothing is sent. Sends happen on a background thread so a slow
network never holds up the controller.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

API_URL = "https://api.pushover.net/1/messages.json"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "state" / "notify.json"


class Notifier:
    def __init__(self, path: Path | None = CONFIG_PATH, post: Callable[[dict], None] | None = None):
        self._path = path
        self._post = post or self._post_to_pushover
        self.last = {"time": None, "ok": None, "detail": "not sent yet"}

    def _config(self) -> dict:
        try:
            return json.loads(self._path.read_text())
        except (AttributeError, OSError, ValueError):
            return {}

    def configured(self) -> bool:
        cfg = self._config()
        return bool(cfg.get("pushover_token")) and bool(cfg.get("pushover_user"))

    def send(self, title: str, message: str, priority: int = 0, wait: bool = False) -> None:
        """Push ``title``/``message`` (priority: -1 quiet, 0 normal, 1 high)."""

        cfg = self._config()
        if not (cfg.get("pushover_token") and cfg.get("pushover_user")):
            return
        fields = {
            "token": cfg["pushover_token"],
            "user": cfg["pushover_user"],
            "title": title,
            "message": message,
            "priority": str(priority),
        }
        if cfg.get("sound"):
            fields["sound"] = cfg["sound"]
        log.info("push: %s: %s", title, message)
        thread = threading.Thread(target=self._send, args=(fields,), daemon=True)
        thread.start()
        if wait:
            thread.join()

    def _send(self, fields: dict) -> None:
        try:
            self._post(fields)
            self.last = {"time": time.time(), "ok": True, "detail": "sent"}
        except Exception as exc:  # noqa: BLE001 - a failed push mustn't touch the kiln
            self.last = {"time": time.time(), "ok": False, "detail": str(exc)}
            log.warning("push failed: %s", exc)

    @staticmethod
    def _post_to_pushover(fields: dict) -> None:
        req = urllib.request.Request(API_URL, data=urllib.parse.urlencode(fields).encode(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=15):
                pass
        except urllib.error.HTTPError as exc:
            # Pushover explains rejections (bad token, unknown sound...) in JSON.
            try:
                detail = "; ".join(json.loads(exc.read()).get("errors", [])) or str(exc)
            except ValueError:
                detail = str(exc)
            raise RuntimeError(detail) from None
