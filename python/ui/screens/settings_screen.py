"""Settings: MGF / temperature scale toggles, factory reset, connection
status. Servo/camera calibration numbers live in python/config/*.yaml and
are edited there directly for now (see docs/hardware.md) rather than through
this screen — a calibration UI is a good follow-up once there's real
hardware to calibrate against.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kiln.sequencer import Sequencer
from ui.worker import run_in_background


class SettingsScreen(QWidget):
    def __init__(self, sequencer: Sequencer, on_back: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._sequencer = sequencer

        title = QLabel("Settings")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")

        self._status_label = QLabel("Bridge: unknown")

        mgf_button = QPushButton("Toggle Modify GlassFire Mode (MGF)")
        mgf_button.clicked.connect(self._toggle_mgf)

        scale_row = QHBoxLayout()
        f_button = QPushButton("Set °F")
        f_button.clicked.connect(lambda: self._set_scale(False))
        c_button = QPushButton("Set °C")
        c_button.clicked.connect(lambda: self._set_scale(True))
        scale_row.addWidget(f_button)
        scale_row.addWidget(c_button)

        reset_button = QPushButton("Factory Reset Controller…")
        reset_button.setStyleSheet("QPushButton { background: #b71c1c; color: white; padding: 12px; }")
        reset_button.clicked.connect(self._factory_reset)

        back_button = QPushButton("← Back")
        back_button.clicked.connect(on_back)

        for button in (mgf_button, f_button, c_button):
            button.setStyleSheet("QPushButton { padding: 14px; font-size: 14px; }")

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self._status_label)
        layout.addWidget(mgf_button)
        layout.addLayout(scale_row)
        layout.addStretch(1)
        layout.addWidget(reset_button)
        layout.addWidget(back_button)

    def set_bridge_status(self, connected: bool) -> None:
        self._status_label.setText(f"Bridge: {'connected' if connected else 'unreachable'}")

    def _toggle_mgf(self) -> None:
        self._thread, self._worker = run_in_background(
            task=self._sequencer.toggle_mgf, on_failed=self._show_error
        )

    def _set_scale(self, celsius: bool) -> None:
        self._thread, self._worker = run_in_background(
            task=lambda: self._sequencer.set_temperature_scale(celsius),
            on_failed=self._show_error,
        )

    def _factory_reset(self) -> None:
        confirm = QMessageBox.warning(
            self,
            "Factory reset?",
            "This wipes stored Ramp & Hold programs in slots 1-4 and "
            "resets MGF/temperature-scale options. This cannot be undone.",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if confirm != QMessageBox.Yes:
            return
        self._thread, self._worker = run_in_background(
            task=self._sequencer.factory_reset, on_failed=self._show_error
        )

    def _show_error(self, message: str) -> None:
        QMessageBox.critical(self, "Error", message)
