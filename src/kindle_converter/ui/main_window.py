"""Main application window (M5.2, M5.3 input selection + PDF analysis).

The :class:`MainWindow` implements the first real desktop workflow: select a
PDF, analyze it, and read a summary of the analysis before converting. It is
deliberately a thin presentation/orchestration layer:

* it never parses, opens, or reads the PDF itself -- the displayed path is
  simply the path the user picked;
* selection and analysis always go through the M5.1 application boundary
  (:class:`~kindle_converter.application.ConversionApplication`);
* analysis failures and unexpected errors are translated into user-readable
  status messages, never Python tracebacks.

The dependency direction stays UI -> Application API -> core, never the
reverse: PySide6 never appears outside ``kindle_converter.ui``.

M5.3 performs **no conversion**: after a successful analysis the window is
ready for a later conversion milestone and nothing else happens.

Visual polish (themes, icons, fonts, animations) is deliberately out of scope
for M5.3; this is a clean default Qt layout only. The window also does not
introduce any background execution (no ``QThread``, no thread pools): analysis
runs synchronously for now, structured so later milestones can move it off the
event loop without moving PDF/business logic into the UI.
"""

from __future__ import annotations

import logging
from enum import Enum
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from kindle_converter.application import (
    ApplicationError,
    ConversionApplication,
    ConversionFailedError,
    InvalidRequestError,
)
from kindle_converter.pdf import PDFType
from kindle_converter.pdf.models import PDFAnalysis

logger = logging.getLogger(__name__)

#: Application and window title.
APP_TITLE = "Kindle Book Converter"

#: Initial window dimensions in logical pixels.
DEFAULT_WIDTH = 800
DEFAULT_HEIGHT = 640

_SUBTITLE_TEXT = "PDF \u2192 Kindle-ready ebook converter"
_INPUT_SECTION_TEXT = "Input PDF"
_ANALYSIS_SECTION_TEXT = "PDF Analysis"

_STATUS_NO_INPUT = "Select a PDF file to begin"
_STATUS_INPUT_SELECTED = "Ready to analyze"
_STATUS_ANALYZING = "Analyzing..."
_STATUS_ANALYSIS_COMPLETE = "Analysis complete"
_STATUS_ANALYSIS_FAILED = "Analysis failed. Please fix the input and try again."

_ERROR_NO_INPUT = "Please select a PDF file."
_ERROR_NOT_FOUND = "The selected PDF file could not be found."
_ERROR_NOT_A_FILE = "The selected input is not a regular file."
_ERROR_CANNOT_READ = "The selected PDF file could not be read."
_ERROR_INVALID_INPUT = "The selected input is not a valid PDF."
_ERROR_ANALYSIS_FAILED = "The PDF could not be analyzed."

#: Placeholder shown for every analysis value until an analysis is displayed.
_NO_VALUE = "\u2014"

#: File-dialog filter for the PDF picker.
_PDF_FILTER = "PDF files (*.pdf);;All files (*)"


class UiState(Enum):
    """The explicit, small UI state model (M5.3).

    ``NO_INPUT -> INPUT_SELECTED -> ANALYZING -> ANALYSIS_COMPLETE`` is the
    happy path; ``ANALYZING -> ANALYSIS_FAILED`` is the error path. The state
    drives widget enablement and the status message.
    """

    #: No PDF has been selected yet.
    NO_INPUT = "no_input"
    #: A PDF is selected and ready to be analyzed.
    INPUT_SELECTED = "input_selected"
    #: A synchronous analysis is running (controls that would conflict are
    #: disabled, duplicate analysis is prevented).
    ANALYZING = "analyzing"
    #: The last analysis finished successfully and its summary is displayed.
    ANALYSIS_COMPLETE = "analysis_complete"
    #: The last analysis failed; the selected input is preserved for retry.
    ANALYSIS_FAILED = "analysis_failed"


class MainWindow(QMainWindow):
    """The main application window: PDF input selection + analysis summary.

    Widgets carry stable ``objectName`` values (``appTitle``, ``subtitle``,
    ``inputSectionTitle``, ``inputPath``, ``browseButton``, ``analyzeButton``,
    ``analysisSectionTitle``, ``pagesValue``, ``documentTypeValue``,
    ``textPagesValue``, ``scannedPagesValue``, ``mixedPagesValue``,
    ``ocrRequiredValue``, ``statusLabel``) so tests and later milestones can
    locate them without depending on layout order.

    ``application`` is the M5.1 boundary double-callers can inject for tests;
    when omitted a real :class:`ConversionApplication` is used.
    """

    def __init__(self, application: ConversionApplication | None = None) -> None:
        super().__init__()
        self._application = (
            application if application is not None else ConversionApplication()
        )
        self._selected_path: Path | None = None
        self._last_analysis: PDFAnalysis | None = None
        self._state: UiState = UiState.NO_INPUT
        self.setWindowTitle(APP_TITLE)
        self.resize(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self._build_central_area()
        self._apply_state(UiState.NO_INPUT)

    # ------------------------------------------------------------------
    # Public state inspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> UiState:
        """The current :class:`UiState` of the window."""
        return self._state

    @property
    def selected_path(self) -> Path | None:
        """The path currently selected, or ``None`` when nothing is selected."""
        return self._selected_path

    @property
    def last_analysis(self) -> PDFAnalysis | None:
        """The most recent successful analysis, or ``None`` when none exists."""
        return self._last_analysis

    # ------------------------------------------------------------------
    # Input selection
    # ------------------------------------------------------------------

    def browse_for_input(self) -> None:
        """Open the PDF picker and apply the chosen file.

        Uses :class:`QFileDialog` restricted to PDF files. When the dialog is
        cancelled the current selection (if any) is preserved and no error is
        shown.
        """
        selected, _ = QFileDialog.getOpenFileName(
            self, "Select a PDF file", "", _PDF_FILTER
        )
        if selected:
            self.select_input(selected)

    def select_input(self, path) -> None:
        """Select ``path`` as the input PDF (or ``None`` to clear the input).

        Only stores and displays the path -- the PDF is never opened or read
        here -- and invalidates any stale analysis from a previous file.
        """
        if path is None:
            self._selected_path = None
            self._path_edit.clear()
            self._clear_analysis()
            self._apply_state(UiState.NO_INPUT)
            return
        self._selected_path = Path(path)
        self._path_edit.setText(str(self._selected_path))
        self._clear_analysis()
        self._apply_state(UiState.INPUT_SELECTED)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyze_selected(self) -> None:
        """Analyze the selected PDF through the application layer.

        Synchronous in M5.3 (no background execution). A repeated call while
        an analysis is running is a no-op; the Analyze button is also disabled
        during :attr:`UiState.ANALYZING`. Failures are translated into
        user-readable status messages and leave the window usable for a retry.
        """
        if self._state is UiState.ANALYZING:
            return
        selected = self._selected_path
        if selected is None:
            self._show_error(_ERROR_NO_INPUT)
            return
        path = Path(selected)
        if not path.exists():
            self._show_error(_ERROR_NOT_FOUND)
            return
        if not path.is_file():
            self._show_error(_ERROR_NOT_A_FILE)
            return

        self._apply_state(UiState.ANALYZING)
        try:
            analysis = self._application.analyze_pdf(path)
        except InvalidRequestError as exc:
            self._show_error(self._translate_invalid_request(exc))
            return
        except ConversionFailedError:
            self._show_error(_ERROR_ANALYSIS_FAILED)
            return
        except ApplicationError:
            self._show_error(_ERROR_ANALYSIS_FAILED)
            return
        except Exception:
            logger.exception("Unexpected error while analyzing %s", path)
            self._show_error(_ERROR_ANALYSIS_FAILED)
            return

        self._last_analysis = analysis
        self._display_analysis(analysis)
        self._apply_state(UiState.ANALYSIS_COMPLETE)

    # ------------------------------------------------------------------
    # Widget construction
    # ------------------------------------------------------------------

    def _build_central_area(self) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)

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

        layout.addSpacing(16)

        # --- Input section ------------------------------------------------
        input_title = QLabel(_INPUT_SECTION_TEXT, central)
        input_title.setObjectName("inputSectionTitle")
        layout.addWidget(input_title)

        input_row = QWidget(central)
        input_row_layout = QHBoxLayout(input_row)
        input_row_layout.setContentsMargins(0, 0, 0, 0)
        self._path_edit = QLineEdit(input_row)
        self._path_edit.setObjectName("inputPath")
        self._path_edit.setReadOnly(True)
        self._path_edit.setPlaceholderText("No PDF selected")
        input_row_layout.addWidget(self._path_edit, 1)
        self._browse_button = QPushButton("Browse...", input_row)
        self._browse_button.setObjectName("browseButton")
        self._browse_button.clicked.connect(self.browse_for_input)
        input_row_layout.addWidget(self._browse_button)
        layout.addWidget(input_row)

        self._analyze_button = QPushButton("Analyze PDF", central)
        self._analyze_button.setObjectName("analyzeButton")
        self._analyze_button.clicked.connect(self.analyze_selected)
        layout.addWidget(self._analyze_button, alignment=Qt.AlignmentFlag.AlignRight)

        layout.addSpacing(8)

        # --- Analysis section ---------------------------------------------
        analysis_title = QLabel(_ANALYSIS_SECTION_TEXT, central)
        analysis_title.setObjectName("analysisSectionTitle")
        layout.addWidget(analysis_title)

        form_host = QWidget(central)
        form = QFormLayout(form_host)
        form.setContentsMargins(0, 0, 0, 0)
        self._pages_value = self._add_analysis_row(form, "Pages:", "pagesValue", central)
        self._document_type_value = self._add_analysis_row(
            form, "Document type:", "documentTypeValue", central
        )
        self._text_pages_value = self._add_analysis_row(
            form, "Text pages:", "textPagesValue", central
        )
        self._scanned_pages_value = self._add_analysis_row(
            form, "Scanned pages:", "scannedPagesValue", central
        )
        self._mixed_pages_value = self._add_analysis_row(
            form, "Mixed pages:", "mixedPagesValue", central
        )
        self._ocr_required_value = self._add_analysis_row(
            form, "OCR required:", "ocrRequiredValue", central
        )
        layout.addWidget(form_host)

        layout.addSpacing(12)

        self._status_label = QLabel(central)
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

        layout.addStretch(1)

        self.setCentralWidget(central)

    @staticmethod
    def _add_analysis_row(
        form: QFormLayout, label: str, object_name: str, parent: QWidget
    ) -> QLabel:
        """Add one ``label``/value row to the analysis form and return the value."""
        name = QLabel(label, parent)
        value = QLabel(_NO_VALUE, parent)
        value.setObjectName(object_name)
        form.addRow(name, value)
        return value

    # ------------------------------------------------------------------
    # State / display helpers
    # ------------------------------------------------------------------

    def _apply_state(self, state: UiState, status: str | None = None) -> None:
        """Apply ``state``: widget enablement, ``self._state``, status text.

        ``status`` overrides the state's default status message (used to show
        concrete error text for :attr:`UiState.ANALYSIS_FAILED`).
        """
        self._state = state
        analyzing = state is UiState.ANALYZING
        has_input = state is not UiState.NO_INPUT
        self._browse_button.setEnabled(not analyzing)
        self._analyze_button.setEnabled(has_input and not analyzing)
        if status is not None:
            self._status_label.setText(status)
        elif state is UiState.NO_INPUT:
            self._status_label.setText(_STATUS_NO_INPUT)
        elif state is UiState.INPUT_SELECTED:
            self._status_label.setText(_STATUS_INPUT_SELECTED)
        elif state is UiState.ANALYZING:
            self._status_label.setText(_STATUS_ANALYZING)
        elif state is UiState.ANALYSIS_COMPLETE:
            self._status_label.setText(_STATUS_ANALYSIS_COMPLETE)
        elif state is UiState.ANALYSIS_FAILED:
            self._status_label.setText(_STATUS_ANALYSIS_FAILED)

    def _show_error(self, message: str) -> None:
        """Enter the failed state, never presenting stale analysis as current."""
        self._clear_analysis()
        self._apply_state(UiState.ANALYSIS_FAILED, status=message)

    def _clear_analysis(self) -> None:
        """Reset the analysis summary to its empty placeholders."""
        self._last_analysis = None
        self._pages_value.setText(_NO_VALUE)
        self._document_type_value.setText(_NO_VALUE)
        self._text_pages_value.setText(_NO_VALUE)
        self._scanned_pages_value.setText(_NO_VALUE)
        self._mixed_pages_value.setText(_NO_VALUE)
        self._ocr_required_value.setText(_NO_VALUE)

    def _display_analysis(self, analysis: PDFAnalysis) -> None:
        """Render the existing ``PDFAnalysis`` fields in the summary form.

        Text/scanned/mixed counts are derived deterministically from the
        analysis' per-page ``PageAnalysis.classification`` values (mutually
        exclusive, summing to the page count). ``OCR required`` is ``Yes``
        exactly when any page is classified SCANNED or MIXED (those are the
        pages the M3.5 routing layer renders and OCR's).
        """
        text_pages, scanned_pages, mixed_pages = _page_breakdown(analysis)
        self._pages_value.setText(str(analysis.page_count))
        self._document_type_value.setText(_human_type(analysis.document_type))
        self._text_pages_value.setText(str(text_pages))
        self._scanned_pages_value.setText(str(scanned_pages))
        self._mixed_pages_value.setText(str(mixed_pages))
        self._ocr_required_value.setText(
            "Yes" if (scanned_pages + mixed_pages) > 0 else "No"
        )

    @staticmethod
    def _translate_invalid_request(exc: InvalidRequestError) -> str:
        """Map an application ``InvalidRequestError`` to a user-readable message."""
        message = str(exc)
        if "does not exist" in message:
            return _ERROR_NOT_FOUND
        if "not a regular file" in message:
            return _ERROR_NOT_A_FILE
        if "cannot be read" in message:
            return _ERROR_CANNOT_READ
        return _ERROR_INVALID_INPUT


def _page_breakdown(analysis: PDFAnalysis) -> tuple[int, int, int]:
    """Return ``(text_pages, scanned_pages, mixed_pages)`` for ``analysis``.

    Both real analyses (per-page classifications present) and minimal stub
    values (no per-page rows) are supported: with rows, the three counts are
    the per-page classifications and sum to the page count; without rows the
    ``text_page_count`` is used and the remaining pages are treated as
    scanned (the analyzer classifies every page without meaningful text as
    SCANNED).
    """
    if analysis.pages:
        text = scanned = mixed = 0
        for page in analysis.pages:
            if page.classification is PDFType.SCANNED:
                scanned += 1
            elif page.classification is PDFType.MIXED:
                mixed += 1
            else:
                text += 1
        return text, scanned, mixed
    text = analysis.text_page_count
    scanned = max(analysis.page_count - text, 0)
    return text, scanned, 0


def _human_type(document_type: PDFType | None) -> str:
    """Format a ``PDFType`` for display (``PDFType.TEXT`` -> ``"Text"``)."""
    if document_type is None:
        return "Unknown"
    return document_type.value.capitalize()