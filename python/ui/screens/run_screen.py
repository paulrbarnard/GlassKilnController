"""Run/monitor screen: live diagram + progress log + Skip/Add Time/Add Temp
(mapped to Sequencer Edit Mode calls, p.21-22) + Stop.

Firing runs on a background thread via ``ui.worker.run_in_background`` — see
that module's docstring for why. This screen only ever calls into
``Sequencer``, never ``bridge``/``vision`` directly (docs/architecture.md).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QThread
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kiln.profiles import Profile
from kiln.sequencer import Sequencer
from ui.widgets.ramp_diagram import RampDiagram
from ui.worker import SequencerWorker, run_in_background


class RunScreen(QWidget):
    def __init__(
        self,
        sequencer: Sequencer,
        on_done: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sequencer = sequencer
        self._on_done = on_done
        self._profile: Profile | None = None
        self._thread: QThread | None = None
        self._worker: SequencerWorker | None = None

        self._title = QLabel()
        self._title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._diagram = RampDiagram()
        self._log = QListWidget()
        self._log.setStyleSheet("font-family: monospace; font-size: 12px;")

        self._skip_button = QPushButton("Skip Segment")
        self._add_time_button = QPushButton("+5 min")
        self._add_temp_button = QPushButton("+5°F")
        self._stop_button = QPushButton("STOP")
        self._stop_button.setStyleSheet(
            "QPushButton { background: #b71c1c; color: white; font-size: 16px; padding: 14px; }"
        )

        for button in (self._skip_button, self._add_time_button, self._add_temp_button):
            button.setStyleSheet("QPushButton { padding: 14px; font-size: 14px; }")

        self._skip_button.clicked.connect(self._handle_skip)
        self._add_time_button.clicked.connect(self._handle_add_time)
        self._add_temp_button.clicked.connect(self._handle_add_temp)
        self._stop_button.clicked.connect(self._handle_stop)

        edit_row = QHBoxLayout()
        edit_row.addWidget(self._skip_button)
        edit_row.addWidget(self._add_time_button)
        edit_row.addWidget(self._add_temp_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._diagram, stretch=1)
        layout.addWidget(self._log, stretch=1)
        layout.addLayout(edit_row)
        layout.addWidget(self._stop_button)

        self._set_edit_controls_enabled(False)

    def _set_edit_controls_enabled(self, enabled: bool) -> None:
        for button in (self._skip_button, self._add_time_button, self._add_temp_button):
            button.setEnabled(enabled)

    def start(self, profile: Profile) -> None:
        self._profile = profile
        self._title.setText(f"Firing: {profile.name}")
        self._diagram.set_segments(profile.resolved_segments())
        self._log.clear()
        self._set_edit_controls_enabled(True)

        self._thread, self._worker = run_in_background(
            task=lambda: self._sequencer.run_profile(profile),
            on_progress=self._append_log,
            on_finished=self._handle_finished,
            on_failed=self._handle_failed,
        )
        self._sequencer.on_progress = self._worker.progress.emit

    def _append_log(self, message: str) -> None:
        self._log.addItem(message)
        self._log.scrollToBottom()

    def _handle_finished(self) -> None:
        self._append_log("Program started successfully — kiln is running.")
        self._set_edit_controls_enabled(True)

    def _handle_failed(self, message: str) -> None:
        self._append_log(f"ERROR: {message}")
        self._set_edit_controls_enabled(False)
        QMessageBox.critical(self, "Sequencer error", message)

    def _run_edit_action(self, action: Callable[[], None], label: str) -> None:
        # Edit-mode actions (Skip/Add Time/Add Temp) are short, so they run
        # on their own background thread the same way the main sequence
        # does, to avoid ever blocking the UI on a camera poll.
        self._append_log(f"requested: {label}")
        thread, worker = run_in_background(
            task=action,
            on_progress=self._append_log,
            on_failed=self._handle_failed,
        )
        # Keep references alive for the duration of this short-lived call.
        self._edit_thread, self._edit_worker = thread, worker

    def _handle_skip(self) -> None:
        self._run_edit_action(self._sequencer.skip_segment, "skip segment")

    def _handle_add_time(self) -> None:
        self._run_edit_action(self._sequencer.add_time, "add 5 min")

    def _handle_add_temp(self) -> None:
        self._run_edit_action(self._sequencer.add_temperature, "add 5°F")

    def _handle_stop(self) -> None:
        confirm = QMessageBox.question(
            self,
            "Stop firing?",
            "Stop the current program? This cannot be undone.",
        )
        if confirm != QMessageBox.Yes:
            return
        self._run_edit_action(self._sequencer.stop, "stop")
        self._set_edit_controls_enabled(False)
        self._on_done()
