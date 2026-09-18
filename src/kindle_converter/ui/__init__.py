"""PySide6 desktop UI (M5.2 shell, M5.3 input + analysis, M5.4 options, M5.5 conversion, M5.6 results).

This package is the outer (UI) layer of the application. It depends on the
optional PySide6 runtime (the ``ui`` extra) and on
:mod:`kindle_converter.application` -- never the other way around. Importing
the core package (``import kindle_converter``) never imports this package, so
a base installation works without PySide6.

Public API
----------
``main`` launches the desktop application and returns the Qt event-loop exit
code:

    >>> from kindle_converter.ui import main
    >>> raise SystemExit(main())

``MainWindow`` is the application's main window: it can select a PDF, analyze
it through the M5.1 application boundary, display the M3.1 analysis summary,
configure a conversion (output format, output directory, optional cover) into
a real application-layer ``ConversionRequest``, start the configured
conversion on a background ``QThread`` and show its progress, and -- since
M5.6 -- present the resulting ``ConversionResult`` (outputs, EPUB validation
status/warnings/errors) with local actions to open the generated files or
their containing folder. ``UiState`` is the small explicit state model it
exposes (analysis states, ``CONFIGURING``/``READY``, and the M5.5
``CONVERTING``/``COMPLETED``/``CONVERSION_FAILED`` conversion states).
``ConversionWorker`` is the M5.5 :class:`QObject` worker that the window
moves to a ``QThread``: it delegates the entire conversion to
``ConversionApplication.convert`` and forwards the application's
``ConversionProgress``/result/error over Qt signals without ever touching
widgets. ``open_path`` (:mod:`kindle_converter.ui.platform`) is the small,
injectable platform-opening seam the M5.6 output actions use.
"""

from __future__ import annotations

from .app import main
from .main_window import MainWindow, UiState
from .platform import PlatformOpenError, open_path
from .worker import ConversionWorker

__all__ = [
    "ConversionWorker",
    "MainWindow",
    "PlatformOpenError",
    "UiState",
    "main",
    "open_path",
]