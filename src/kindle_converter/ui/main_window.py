"""Main application window (M5.2).

The :class:`MainWindow` is the desktop application shell: it establishes the
application identity (title, initial size, central layout) and a minimal,
coherent placeholder area where the M5.3+ conversion workflow will live. It
performs no conversion, no PDF/OCR work, and no filesystem access, and it
never touches :mod:`kindle_converter.application` -- the dependency direction
stays UI -> Application API, never the reverse.

Visual polish (themes, icons, fonts, animations) is deliberately out of scope
for M5.2; this is a clean default Qt layout only.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLabel, QMainWindow, QVBoxLayout, QWidget

#: Application and window title.
APP_TITLE = "Kindle Book Converter"

#: Initial window dimensions in logical pixels.
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 520

_SUBTITLE_TEXT = "PDF \u2192 Kindle-ready ebook converter"
_PLACEHOLDER_TEXT = "Conversion controls coming next"


class MainWindow(QMainWindow):
    """The main application window (M5.2 application shell).

    The central widgets carry stable ``objectName`` values (``appTitle``,
    ``subtitle``, ``conversionPlaceholder``) so later milestones and tests can
    locate them without depending on layout order.
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(APP_TITLE)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._build_central_area()

    def _build_central_area(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

        layout.addStretch(2)

        title = QLabel(APP_TITLE, central)
        title.setObjectName("appTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = title.font()
        title_font.setPointSize(24)
        title_font.setBold(True)
        title.setFont(title_font)
        layout.addWidget(title)

        subtitle = QLabel(_SUBTITLE_TEXT, central)
        subtitle.setObjectName("subtitle")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle_font = subtitle.font()
        subtitle_font.setPointSize(12)
        subtitle.setFont(subtitle_font)
        layout.addWidget(subtitle)

        layout.addSpacing(24)

        placeholder = QLabel(_PLACEHOLDER_TEXT, central)
        placeholder.setObjectName("conversionPlaceholder")
        placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(placeholder)

        layout.addStretch(3)

        self.setCentralWidget(central)