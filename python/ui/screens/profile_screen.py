"""Profile detail screen: diagram + plain-language description + Start."""

from __future__ import annotations

from typing import Callable

from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from kiln.profiles import Profile
from ui.widgets.ramp_diagram import RampDiagram


def describe_segments(profile: Profile) -> str:
    """Plain-language segment summary shown under the diagram — the
    "text descriptions" half of the "diagrams and text descriptions"
    requirement.
    """

    lines = []
    for i, seg in enumerate(profile.resolved_segments(), start=1):
        hold = f", hold {seg.hold_minutes:.0f} min" if seg.hold_minutes else ""
        lines.append(
            f"{i}. Ramp at {seg.rate_f_per_hr:.0f}°F/hr to "
            f"{seg.target_temp_f:.0f}°F{hold}"
        )
    return "\n".join(lines)


class ProfileScreen(QWidget):
    def __init__(
        self,
        on_start: Callable[[Profile], None],
        on_edit: Callable[[Profile], None],
        on_back: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_start = on_start
        self._on_edit = on_edit
        self._profile: Profile | None = None

        self._title = QLabel()
        self._title.setStyleSheet("font-size: 20px; font-weight: 600;")
        self._description = QLabel()
        self._description.setWordWrap(True)
        self._diagram = RampDiagram()
        self._segments_label = QLabel()
        self._segments_label.setStyleSheet("font-family: monospace; font-size: 13px;")

        back_button = QPushButton("← Back")
        back_button.clicked.connect(on_back)

        self._edit_button = QPushButton("Edit")
        self._edit_button.clicked.connect(self._handle_edit)

        start_button = QPushButton("Start Firing")
        start_button.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; font-size: 18px; padding: 16px; }"
        )
        start_button.clicked.connect(self._handle_start)

        top_row = QHBoxLayout()
        top_row.addWidget(back_button)
        top_row.addStretch(1)
        top_row.addWidget(self._edit_button)

        layout = QVBoxLayout(self)
        layout.addLayout(top_row)
        layout.addWidget(self._title)
        layout.addWidget(self._description)
        layout.addWidget(self._diagram, stretch=1)
        layout.addWidget(self._segments_label)
        layout.addWidget(start_button)

    def show_profile(self, profile: Profile) -> None:
        self._profile = profile
        self._title.setText(profile.name)
        self._description.setText(profile.description)
        self._diagram.set_segments(profile.resolved_segments())
        self._segments_label.setText(describe_segments(profile))
        self._edit_button.setEnabled(profile.source == "user")

    def _handle_start(self) -> None:
        if self._profile is not None:
            self._on_start(self._profile)

    def _handle_edit(self) -> None:
        if self._profile is not None:
            self._on_edit(self._profile)
