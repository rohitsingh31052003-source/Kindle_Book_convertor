"""Tests for the M5.2 PySide6 UI foundation.

All Qt-dependent tests run headless via ``QT_QPA_PLATFORM=offscreen`` so they
never require a display or a physical screen. The module skips cleanly when
PySide6 is not installed (the ``ui`` extra is absent). The companion file
``test_ui_boundary.py`` tests the architectural dependency guarantees without
importing PySide6 and always runs.

``QT_QPA_PLATFORM`` must be set before PySide6 loads its platform plugin, so
it is configured at module import time before any Qt symbol is touched.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

# Qt requires a platform plugin before any QWidget is created.  ``offscreen``
# is supported everywhere and never requires a real display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtCore, QtWidgets  # noqa: E402  (after importorskip)

from kindle_converter.ui import MainWindow, main  # noqa: E402
from kindle_converter.ui.app import create_application  # noqa: E402
from kindle_converter.ui.main_window import (  # noqa: E402
    APP_TITLE,
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
)

_REPO_SRC = Path(__file__).resolve().parents[1] / "src"


def _run(code: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(_REPO_SRC) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    yield application


# -------------------------------------------------------------------------
# Application startup
# -------------------------------------------------------------------------


class TestApplicationStartup:
    def test_qapplication_can_be_created_in_fresh_interpreter(self) -> None:
        code = """
            import os
            os.environ["QT_QPA_PLATFORM"] = "offscreen"
            from PySide6.QtWidgets import QApplication
            from kindle_converter.ui.app import create_application
            assert QApplication.instance() is None, "no pre-existing QApplication"
            app = create_application([])
            assert QApplication.instance() is app
            assert isinstance(app, QApplication)
            print("created-ok")
        """
        result = _run(code)
        assert result.returncode == 0, result.stderr
        assert "created-ok" in result.stdout

    def test_qapplication_instance_is_reused(self, qapp: QtWidgets.QApplication) -> None:
        assert create_application([]) is qapp
        assert QtWidgets.QApplication.instance() is qapp

    def test_main_window_can_be_instantiated(self, qapp: QtWidgets.QApplication) -> None:
        window = MainWindow()
        try:
            assert type(window).__name__ == "MainWindow"
        finally:
            window.close()

    def test_main_window_can_be_shown_offscreen(self, qapp: QtWidgets.QApplication) -> None:
        window = MainWindow()
        try:
            window.show()
            assert window.isVisible()
        finally:
            window.close()

    def test_main_returns_event_loop_exit_code(self, qapp: QtWidgets.QApplication) -> None:
        QtCore.QTimer.singleShot(0, qapp.quit)
        assert main([]) == 0


# -------------------------------------------------------------------------
# Main window
# -------------------------------------------------------------------------


class TestMainWindow:
    def test_window_title(self, qapp: QtWidgets.QApplication) -> None:
        window = MainWindow()
        try:
            assert window.windowTitle() == APP_TITLE
        finally:
            window.close()

    def test_central_widget_exists(self, qapp: QtWidgets.QApplication) -> None:
        window = MainWindow()
        try:
            central = window.centralWidget()
            assert central is not None
            assert isinstance(central.layout(), QtWidgets.QVBoxLayout)
        finally:
            window.close()

    def test_initial_size_is_reasonable(self, qapp: QtWidgets.QApplication) -> None:
        window = MainWindow()
        try:
            assert window.width() >= DEFAULT_WIDTH - 50
            assert window.height() >= DEFAULT_HEIGHT - 50
        finally:
            window.close()

    def test_placeholder_content_exists(self, qapp: QtWidgets.QApplication) -> None:
        window = MainWindow()
        try:
            title = window.findChild(QtWidgets.QLabel, "appTitle")
            subtitle = window.findChild(QtWidgets.QLabel, "subtitle")
            placeholder = window.findChild(
                QtWidgets.QLabel, "conversionPlaceholder"
            )
            assert title is not None and title.text() == APP_TITLE
            assert subtitle is not None and "Kindle" in subtitle.text()
            assert subtitle is not None and "PDF" in subtitle.text()
            assert (
                placeholder is not None
                and placeholder.text() == "Conversion controls coming next"
            )
        finally:
            window.close()