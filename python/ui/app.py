"""Main window: wires the screens together behind a QStackedWidget.

Navigation is intentionally simple (a stack of full-screen pages, no
back-stack beyond "one level up") to fit an 8" touchscreen kiosk — see
docs/architecture.md for how this fits with kiln/.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow, QStackedWidget

from kiln.profiles import Profile, ProfileLibrary
from kiln.sequencer import Sequencer
from ui.screens.editor_screen import EditorScreen
from ui.screens.home_screen import HomeScreen
from ui.screens.profile_screen import ProfileScreen
from ui.screens.run_screen import RunScreen
from ui.screens.settings_screen import SettingsScreen


class MainWindow(QMainWindow):
    def __init__(self, library: ProfileLibrary, sequencer: Sequencer) -> None:
        super().__init__()
        self.setWindowTitle("Glass Kiln Controller")

        self._library = library
        self._sequencer = sequencer

        self._stack = QStackedWidget()
        self.setCentralWidget(self._stack)

        self._home = HomeScreen(
            library=library,
            on_select_profile=self._show_profile,
            on_create_profile=self._show_new_profile,
            on_settings=self._show_settings,
        )
        self._profile_screen = ProfileScreen(
            on_start=self._start_firing,
            on_edit=self._show_edit_profile,
            on_back=self._show_home,
        )
        self._run_screen = RunScreen(sequencer=sequencer, on_done=self._show_home)
        self._editor_screen = EditorScreen(
            library=library, on_saved=self._handle_profile_saved, on_back=self._show_home
        )
        self._settings_screen = SettingsScreen(sequencer=sequencer, on_back=self._show_home)

        for screen in (
            self._home,
            self._profile_screen,
            self._run_screen,
            self._editor_screen,
            self._settings_screen,
        ):
            self._stack.addWidget(screen)

        self._show_home()

    # -- navigation -----------------------------------------------------------

    def _show_home(self) -> None:
        self._home.refresh()
        self._stack.setCurrentWidget(self._home)

    def _show_profile(self, profile: Profile) -> None:
        self._profile_screen.show_profile(profile)
        self._stack.setCurrentWidget(self._profile_screen)

    def _show_new_profile(self) -> None:
        self._editor_screen.new_profile()
        self._stack.setCurrentWidget(self._editor_screen)

    def _show_edit_profile(self, profile: Profile) -> None:
        self._editor_screen.edit_profile(profile)
        self._stack.setCurrentWidget(self._editor_screen)

    def _show_settings(self) -> None:
        self._stack.setCurrentWidget(self._settings_screen)

    def _handle_profile_saved(self, profile: Profile) -> None:
        self._home.refresh()
        self._show_profile(profile)

    def _start_firing(self, profile: Profile) -> None:
        self._run_screen.start(profile)
        self._stack.setCurrentWidget(self._run_screen)
