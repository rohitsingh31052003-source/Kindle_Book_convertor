"""UI application entry point (M5.2, extended M5.3).

``main`` launches the desktop application and returns the Qt event-loop exit
code. It performs no conversion, PDF analysis, OCR initialization, or
filesystem access at startup. :func:`create_application` returns the
process-wide ``QApplication``, creating it on first use, so later milestones
can reuse the same instance from worker-driven UI code.

M6.5 adds a single explicit verification seam: when the first command-line
argument is ``--smoke`` the bundled subcommands of
:mod:`kindle_converter.ui.smoke` run headlessly instead of entering the GUI
event loop. This is the only argument dispatch in the entry point; every
other invocation launches the window exactly as before.
"""

from __future__ import annotations

import sys
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

    The packaged-application verification subcommands (M6.5) are dispatched
    off the real command line before any windows or event loop are created:
    ``--smoke``, ``--smoke-check``, and ``--sysinfo`` (each accepting the
    optional ``--report <path>``). The normal GUI launch path is unchanged:
    without one of those exact leading arguments the event loop runs as
    before.

    Returns the event-loop exit code (or the verification subcommand's).
    """
    arguments = list(argv) if argv is not None else list(sys.argv[1:])
    if arguments and arguments[0] in ("--smoke", "--smoke-check", "--sysinfo"):
        from .smoke import dispatch

        return dispatch(arguments)
    application = create_application(argv)
    window = MainWindow()
    window.show()
    return int(application.exec())