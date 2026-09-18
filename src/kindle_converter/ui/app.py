"""UI application entry point (M5.2, extended M5.3).

``main`` launches the desktop application and returns the Qt event-loop exit
code. It performs no conversion, PDF analysis, OCR initialization, or
filesystem access at startup. :func:`create_application` returns the
process-wide ``QApplication``, creating it on first use, so later milestones
can reuse the same instance from worker-driven UI code.
"""

from __future__ import annotations

from typing import Sequence

from PySide6.QtWidgets import QApplication

from .main_window import MainWindow

__all__ = ["create_application", "main"]


def create_application(argv: Sequence[str] | None = None) -> QApplication:
    """Return the process-wide ``QApplication``, creating it when none exists.

    ``argv`` is only used when a new instance has to be created; an existing
    (reused) instance keeps its own arguments.
    """
    application = QApplication.instance()
    if application is None:
        application = QApplication(list(argv) if argv is not None else [])
    return application


def main(argv: Sequence[str] | None = None) -> int:
    """Launch the desktop application shell and run its event loop.

    Returns the event-loop exit code.
    """
    application = create_application(argv)
    window = MainWindow()
    window.show()
    return int(application.exec())