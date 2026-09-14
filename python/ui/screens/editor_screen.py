"""Custom Ramp & Hold profile editor: up to 8 segments of rate/temp/hold.

Deliberately simple (a table, not a fancy drag-to-draw diagram) for this
first framework pass — the live ``RampDiagram`` preview at the bottom is
what makes it "intuitive": every edit immediately redraws the picture of
what the kiln will actually do.
"""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from kiln.profiles import Profile, ProfileKind, ProfileLibrary
from kiln.protocol import MAX_PRO_SEGMENTS, RampHoldSegment
from ui.widgets.ramp_diagram import RampDiagram

COLUMNS = ["Rate (°F/hr)", "Target Temp (°F)", "Hold (min)"]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", name.strip().lower()).strip("_")
    return slug or "custom_profile"


class EditorScreen(QWidget):
    def __init__(
        self,
        library: ProfileLibrary,
        on_saved: Callable[[Profile], None],
        on_back: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._on_saved = on_saved
        self._editing_id: str | None = None

        self._name_field = QLineEdit()
        self._name_field.setPlaceholderText("Profile name")
        self._description_field = QLineEdit()
        self._description_field.setPlaceholderText("Description (what is this for?)")

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels(COLUMNS)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self._table.itemChanged.connect(self._handle_table_changed)

        self._diagram = RampDiagram()

        add_row_button = QPushButton("+ Segment")
        add_row_button.clicked.connect(self._add_row)
        remove_row_button = QPushButton("- Segment")
        remove_row_button.clicked.connect(self._remove_selected_row)

        back_button = QPushButton("← Cancel")
        back_button.clicked.connect(on_back)
        save_button = QPushButton("Save Profile")
        save_button.setStyleSheet(
            "QPushButton { background: #2e7d32; color: white; padding: 14px; font-size: 16px; }"
        )
        save_button.clicked.connect(self._handle_save)

        row_buttons = QHBoxLayout()
        row_buttons.addWidget(add_row_button)
        row_buttons.addWidget(remove_row_button)

        bottom_buttons = QHBoxLayout()
        bottom_buttons.addWidget(back_button)
        bottom_buttons.addStretch(1)
        bottom_buttons.addWidget(save_button)

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Custom Ramp & Hold Profile"))
        layout.addWidget(self._name_field)
        layout.addWidget(self._description_field)
        layout.addWidget(self._table)
        layout.addLayout(row_buttons)
        layout.addWidget(self._diagram, stretch=1)
        layout.addLayout(bottom_buttons)

    def new_profile(self) -> None:
        self._editing_id = None
        self._name_field.setText("")
        self._description_field.setText("")
        self._table.setRowCount(0)
        self._add_row()
        self._refresh_diagram()

    def edit_profile(self, profile: Profile) -> None:
        if profile.kind is not ProfileKind.RAMP_HOLD:
            QMessageBox.information(
                self, "Not editable", "Built-in GlassFire profiles can't be edited directly."
            )
            return
        self._editing_id = profile.id
        self._name_field.setText(profile.name)
        self._description_field.setText(profile.description)
        self._table.setRowCount(0)
        for seg in profile.segments or []:
            self._add_row(seg)
        self._refresh_diagram()

    def _add_row(self, seg: RampHoldSegment | None = None) -> None:
        if self._table.rowCount() >= MAX_PRO_SEGMENTS:
            QMessageBox.information(
                self, "Segment limit", f"Controller supports at most {MAX_PRO_SEGMENTS} segments."
            )
            return
        row = self._table.rowCount()
        self._table.insertRow(row)
        values = (seg.rate_f_per_hr, seg.target_temp_f, seg.hold_minutes) if seg else (300, 1000, 10)
        for col, value in enumerate(values):
            self._table.setItem(row, col, QTableWidgetItem(str(value)))

    def _remove_selected_row(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self._refresh_diagram()

    def _handle_table_changed(self, _item: QTableWidgetItem) -> None:
        self._refresh_diagram()

    def _read_segments(self) -> list[RampHoldSegment] | None:
        segments = []
        for row in range(self._table.rowCount()):
            try:
                rate = float(self._table.item(row, 0).text())
                temp = float(self._table.item(row, 1).text())
                hold = float(self._table.item(row, 2).text())
            except (AttributeError, ValueError):
                return None
            segments.append(
                RampHoldSegment(rate_f_per_hr=rate, target_temp_f=temp, hold_minutes=hold)
            )
        return segments

    def _refresh_diagram(self) -> None:
        segments = self._read_segments()
        self._diagram.set_segments(segments or [])

    def _handle_save(self) -> None:
        name = self._name_field.text().strip()
        if not name:
            QMessageBox.warning(self, "Name required", "Give this profile a name.")
            return
        segments = self._read_segments()
        if not segments:
            QMessageBox.warning(self, "Invalid segments", "Check every rate/temp/hold value.")
            return

        profile_id = self._editing_id or slugify(name)
        profile = Profile(
            id=profile_id,
            name=name,
            description=self._description_field.text().strip(),
            kind=ProfileKind.RAMP_HOLD,
            source="user",
            segments=segments,
        )
        self._library.save_user_profile(profile)
        self._on_saved(profile)
