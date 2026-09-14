"""Home screen: browse built-in + user profiles, touch one to view it."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kiln.profiles import Profile, ProfileLibrary

TOUCH_BUTTON_STYLE = "QPushButton { padding: 18px; font-size: 16px; }"


class HomeScreen(QWidget):
    def __init__(
        self,
        library: ProfileLibrary,
        on_select_profile: Callable[[Profile], None],
        on_create_profile: Callable[[], None],
        on_settings: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._library = library
        self._on_select_profile = on_select_profile

        title = QLabel("Glass Kiln Controller")
        title.setStyleSheet("font-size: 22px; font-weight: 600;")

        self._list = QListWidget()
        self._list.setStyleSheet("QListWidget::item { padding: 14px; font-size: 15px; }")
        self._list.itemActivated.connect(self._handle_activated)

        new_button = QPushButton("+ New Custom Profile")
        new_button.setStyleSheet(TOUCH_BUTTON_STYLE)
        new_button.clicked.connect(on_create_profile)

        settings_button = QPushButton("Settings")
        settings_button.setStyleSheet(TOUCH_BUTTON_STYLE)
        settings_button.clicked.connect(on_settings)

        button_row = QHBoxLayout()
        button_row.addWidget(new_button)
        button_row.addWidget(settings_button)

        layout = QVBoxLayout(self)
        layout.addWidget(title)
        layout.addWidget(self._list, stretch=1)
        layout.addLayout(button_row)

        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        profiles = sorted(
            self._library.all(), key=lambda p: (p.source != "user", p.name)
        )
        for profile in profiles:
            label = profile.name
            if profile.source == "user":
                label = f"★ {label}"  # mark user-created profiles
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, profile.id)
            self._list.addItem(item)

    def _handle_activated(self, item: QListWidgetItem) -> None:
        profile_id = item.data(Qt.UserRole)
        self._on_select_profile(self._library.get(profile_id))
