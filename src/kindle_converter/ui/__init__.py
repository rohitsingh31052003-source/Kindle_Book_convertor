"""PySide6 desktop UI foundation (M5.2).

This package is the outer (UI) layer of the application. It depends on the
optional PySide6 runtime (the ``ui`` extra) and, in later milestones, on
:mod:`kindle_converter.application` -- never the other way around. Importing
the core package (``import kindle_converter``) never imports this package, so
a base installation works without PySide6.

Public API
----------
``main`` launches the desktop application shell and returns the Qt event-loop
exit code:

    >>> from kindle_converter.ui import main
    >>> raise SystemExit(main())

``MainWindow`` is the application's main window (the M5.2 shell). The shell is
only an application shell: no conversion workflow exists yet.
"""

from __future__ import annotations

from .app import main
from .main_window import MainWindow

__all__ = ["MainWindow", "main"]