"""PySide6 desktop UI (M5.2 shell, M5.3 input selection + analysis, M5.4 options).

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
and configure a conversion (output format, output directory, optional cover)
into a real application-layer ``ConversionRequest`` via
``MainWindow.build_conversion_request``. ``UiState`` is the small explicit
state model it exposes (analysis states plus ``CONFIGURING``/``READY``).
"""

from __future__ import annotations

from .app import main
from .main_window import MainWindow, UiState

__all__ = ["MainWindow", "UiState", "main"]