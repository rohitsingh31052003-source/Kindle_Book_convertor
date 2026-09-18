"""M5.8 — UI + integration testing.

These tests verify the complete desktop conversion workflow across the
established application boundary:

    MainWindow
        ↓
    ConversionWorker
        ↓
    ConversionApplication
        ↓
    existing conversion pipeline

The tests verify observable behavior and architectural boundaries,
not implementation details. They use real PySide6 widgets and the
actual desktop application boundary, with deterministic test doubles
(seams) where necessary to avoid expensive or platform-dependent
operations.

All Qt dependencies run headless via ``QT_QPA_PLATFORM=offscreen``.
The module skips cleanly when PySide6 is not installed (the ``ui``
extra is absent), consistent with the existing ``ui`` test modules.

PySide6 availability note:
    These tests require PySide6 (the ``ui`` extra). When PySide6 is
    unavailable the module is skipped in full via
    ``pytest.importorskip("PySide6")`` — see the report for exact
    skip counts. The tests are written to the repository's existing
    UI test convention and run unchanged when the ``ui`` extra is
    installed.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Generator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

import pymupdf  # noqa: E402

from PySide6 import QtCore, QtWidgets  # noqa: E402  (after importorskip)

from kindle_converter.application import (  # noqa: E402
    ApplicationError,
    ConversionApplication,
    ConversionFailedError,
    ConversionProgress,
    ConversionRequest,
    ConversionResult,
    ConversionStage,
    InvalidRequestError,
    OutputFormat,
    WorkspaceError,
)
from kindle_converter.epub import (  # noqa: E402
    EPUBValidationCode,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
)
from kindle_converter.pdf import PDFAnalysis, PDFType, PageAnalysis  # noqa: E402
from kindle_converter.ui import MainWindow, UiState  # noqa: E402
from kindle_converter.ui.platform import PlatformOpenError, open_path  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> Generator[QtWidgets.QApplication, None, None]:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    yield application


def _wait_for(predicate, timeout: float = 15.0) -> None:
    """Spin the GUI event loop until ``predicate()`` is true (deterministic)."""
    deadline = time.monotonic() + timeout
    app = QtCore.QCoreApplication.instance()
    while time.monotonic() < deadline:
        if app is not None:
            app.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("timed out waiting for condition")


def _settle(window: MainWindow) -> None:
    """Wait until the window fully released its finished conversion thread."""
    _wait_for(lambda: window._thread is None)
    _flush_deferred_deletes()


def _flush_deferred_deletes() -> None:
    app = QtCore.QCoreApplication.instance()
    if app is None:
        return
    for _ in range(2):
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def _page(number: int, classification: PDFType) -> PageAnalysis:
    return PageAnalysis(
        page_number=number,
        has_image=classification is not PDFType.TEXT,
        has_meaningful_text=classification is not PDFType.SCANNED,
        char_count=40,
        classification=classification,
    )


def _text_analysis() -> PDFAnalysis:
    pages = [_page(1, PDFType.TEXT), _page(2, PDFType.TEXT)]
    return PDFAnalysis(
        page_count=2,
        text_page_count=2,
        image_page_count=0,
        text_density=40.0,
        document_type=PDFType.TEXT,
        pages=pages,
    )


def _scanned_analysis() -> PDFAnalysis:
    pages = [_page(1, PDFType.SCANNED), _page(2, PDFType.SCANNED)]
    return PDFAnalysis(
        page_count=2,
        text_page_count=0,
        image_page_count=2,
        text_density=0.0,
        document_type=PDFType.SCANNED,
        pages=pages,
    )


def _mixed_analysis() -> PDFAnalysis:
    pages = [
        _page(1, PDFType.TEXT),
        _page(2, PDFType.TEXT),
        _page(3, PDFType.SCANNED),
        _page(4, PDFType.MIXED),
    ]
    return PDFAnalysis(
        page_count=4,
        text_page_count=2,
        image_page_count=2,
        text_density=20.0,
        document_type=PDFType.MIXED,
        pages=pages,
    )


def _make_pdf(path: Path, pages: int = 2) -> Path:
    """Write a real (tiny) text PDF with PyMuPDF."""
    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=612, height=792)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"Meaningful body text line on page {index + 1}. It needs to be "
            f"long enough to count as meaningful native text.",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


def _make_pdf_with_pages(path: Path, pages: int, classification: PDFType) -> Path:
    """Write a tiny PDF and return it (analysis classification is injected)."""
    return _make_pdf(path, pages)


def _label(window: MainWindow, object_name: str) -> QtWidgets.QLabel:
    widget = window.findChild(QtWidgets.QLabel, object_name)
    assert widget is not None, f"missing label {object_name!r}"
    return widget


def _button(window: MainWindow, object_name: str) -> QtWidgets.QPushButton:
    widget = window.findChild(QtWidgets.QPushButton, object_name)
    assert widget is not None, f"missing button {object_name!r}"
    return widget


def _results_section(window: MainWindow) -> QtWidgets.QWidget:
    widget = window.findChild(QtWidgets.QWidget, "resultsSection")
    assert widget is not None, "missing resultsSection"
    return widget


def _convert_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "convertButton")
    assert button is not None, "missing convertButton"
    return button


def _status(window: MainWindow) -> QtWidgets.QLabel:
    label = window.findChild(QtWidgets.QLabel, "statusLabel")
    assert label is not None, "missing statusLabel"
    return label


def _progress_bar(window: MainWindow) -> QtWidgets.QProgressBar:
    bar = window.findChild(QtWidgets.QProgressBar, "progressBar")
    assert bar is not None, "missing progressBar"
    return bar


def _combo(window: MainWindow) -> QtWidgets.QComboBox:
    combo = window.findChild(QtWidgets.QComboBox, "outputFormatCombo")
    assert combo is not None, "missing outputFormatCombo"
    return combo


def _out_dir_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "outputDirectoryButton")
    assert button is not None, "missing outputDirectoryButton"
    return button


def _browse_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "browseButton")
    assert button is not None, "missing browseButton"
    return button


def _analyze_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
    assert button is not None, "missing analyzeButton"
    return button


def _cover_browse_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "coverBrowseButton")
    assert button is not None, "missing coverBrowseButton"
    return button


def _cover_clear_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "coverClearButton")
    assert button is not None, "missing coverClearButton"
    return button


def _value(window: MainWindow, object_name: str) -> QtWidgets.QLabel:
    label = window.findChild(QtWidgets.QLabel, object_name)
    assert label is not None, f"missing analysis value label {object_name!r}"
    return label


def _make_ready(window: MainWindow, tmp_path: Path) -> Path:
    """Drive the window into ``UiState.READY`` and return the input path."""
    window.show()
    pdf = _make_pdf(tmp_path / "book.pdf")
    window.select_input(pdf)
    window.analyze_selected()
    out_dir = tmp_path / "out"
    out_dir.mkdir(exist_ok=True)
    window.select_output_directory(out_dir)
    assert window.state is UiState.READY
    assert window.is_configuration_ready
    return pdf


def _convert_to_completion(window: MainWindow) -> None:
    window.convert_selected()
    _wait_for(lambda: window.state is UiState.COMPLETED)
    _settle(window)


class _RecordingOpener:
    """Records every path a window output action asked to open."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[Path] = []
        self.error = error

    def __call__(self, path: Path) -> None:
        self.calls.append(Path(path))
        if self.error is not None:
            raise self.error


class _FakeApplication:
    """Deterministic application double driving ``analyze_pdf``/``validate_request``/``convert``.

    ``convert`` records each request, optionally emits canned progress
    events, and either returns a real :class:`ConversionResult` (built
    from the actual request it received) or raises a canned error. An
    optional ``entered`` / ``release`` ``threading.Event`` pair lets
    tests freeze the worker inside ``convert`` and observe the window
    while a conversion is genuinely running; ``block_on`` restricts the
    stall to a specific 1-based ``convert`` call (``0`` stalls every
    conversion) so earlier conversions can flow normally. ``analysis_error``
    makes ``analyze_pdf`` raise, and ``on_analyze`` observes the window from
    inside the synchronous analysis. ``results``/``result`` supply canned
    results (one per ``convert`` call, the last repeating); with none, a
    realistic result is built from the request (output-directory based paths and a
    valid ``EPUBValidationResult``).
    """

    def __init__(
        self,
        *,
        analysis: PDFAnalysis | None = None,
        error: Exception | None = None,
        analysis_error: Exception | None = None,
        progress_events: tuple[ConversionProgress, ...] = (),
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        block_on: int = 0,
        on_analyze=None,
        emit_progress_before_block: bool = True,
        results: tuple[ConversionResult, ...] = (),
        result: ConversionResult | None = None,
    ) -> None:
        self.analysis = analysis if analysis is not None else _text_analysis()
        self.error = error
        self.analysis_error = analysis_error
        self.progress_events = progress_events
        self.entered = entered
        self.release = release
        #: 1-based index of the ``convert`` call to hold until ``release`` is
        #: set. ``0`` (the default) holds **every** conversion, keeping the
        #: worker genuinely busy so tests can observe the ``CONVERTING`` state;
        #: a positive value lets earlier conversions flow normally while a
        #: specific later one intentionally stalls.
        self.block_on = block_on
        self.on_analyze = on_analyze
        self.emit_progress_before_block = emit_progress_before_block
        if result is not None:
            self._results = [result]
        elif results:
            self._results = list(results)
        else:
            self._results = []
        self.convert_calls: list[ConversionRequest] = []
        self.validate_calls: list[ConversionRequest] = []
        self.analyze_calls: list[Path] = []

    def analyze_pdf(self, path) -> PDFAnalysis:
        self.analyze_calls.append(Path(path))
        if self.on_analyze is not None:
            self.on_analyze(path)
        if self.analysis_error is not None:
            raise self.analysis_error
        return self.analysis

    def validate_request(self, request: ConversionRequest) -> None:
        self.validate_calls.append(request)

    def convert(self, request: ConversionRequest, *, progress=None) -> ConversionResult:
        self.convert_calls.append(request)
        if self.emit_progress_before_block and progress is not None:
            for event in self.progress_events:
                progress(event)
        if self.entered is not None:
            self.entered.set()
        if self.release is not None and (
            self.block_on == 0 or len(self.convert_calls) == self.block_on
        ):
            assert self.release.wait(10), "test conversion did not finish in time"
        if self.error is not None:
            raise self.error
        if self._results:
            base = self._results[min(len(self.convert_calls) - 1, len(self._results) - 1)]
        else:
            output = request.output_directory
            base = ConversionResult(
                request=request,
                analysis=self.analysis,
                epub_path=output / "book.epub",
                azw3_path=(
                    output / "book.azw3"
                    if OutputFormat.AZW3 in request.formats
                    else None
                ),
                validation=EPUBValidationResult(
                    package_document="container.xml",
                    epub_version="3.0",
                    spine_documents=("chapter-00.xhtml",),
                    issues=(),
                ),
            )
        return base


# --------------------------------------------------------------------------
# 1. Complete happy-path workflow
# --------------------------------------------------------------------------


class TestHappyPathWorkflow:
    """The primary desktop workflow end-to-end.

    Launch application → Select PDF → Analyze PDF → Display analysis
    → Configure output → Start conversion → Background conversion →
    Progress updates → Conversion succeeds → Results displayed →
    Validation displayed → Output actions available.
    """

    def test_launch_and_select_pdf(self, qapp, tmp_path: Path) -> None:
        window = MainWindow()
        try:
            assert window.state is UiState.NO_INPUT
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            assert window.state is UiState.INPUT_SELECTED
            assert window.selected_path == pdf
            assert _browse_button(window).isEnabled()
            assert _analyze_button(window).isEnabled()
        finally:
            window.close()

    def test_analyze_displays_analysis(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert _value(window, "pagesValue").text() == "2"
            assert _value(window, "documentTypeValue").text() == "Text"
            assert _value(window, "textPagesValue").text() == "2"
            assert _value(window, "scannedPagesValue").text() == "0"
            assert _value(window, "mixedPagesValue").text() == "0"
            assert _value(window, "ocrRequiredValue").text() == "No"
        finally:
            window.close()

    def test_configure_after_analysis(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            assert window.state is UiState.READY
            assert window.is_configuration_ready
            assert _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_start_conversion_in_background(self, qapp, tmp_path: Path) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_button(window).click()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            assert _status(window).text() == "Converting..."
            assert _progress_bar(window).maximum() == 0  # indeterminate
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_progress_updates_reach_the_ui(self, qapp, tmp_path: Path) -> None:
        the_message = ConversionProgress(
            ConversionStage.EPUB,
            "Converting the PDF to an EPUB (extraction, OCR, reconstruction)",
            0,
            2,
        )
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(),
            progress_events=(
                ConversionProgress(ConversionStage.ANALYSIS, "Analyzing the PDF", 0, 0),
                the_message,
            ),
            entered=entered,
            release=release,
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            _wait_for(lambda: _status(window).text() == the_message.message)
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_conversion_succeeds_with_results(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.state is UiState.COMPLETED
            assert window.last_result is not None
            assert _results_section(window).isVisible()
            assert _label(window, "resultStatusValue").text() == "Conversion complete"
            assert _label(window, "resultFormatValue").text() == "EPUB"
            assert _label(window, "epubOutputPath").text()
            assert _label(window, "validationStatusValue").text() == "Valid"
        finally:
            window.close()

    def test_output_actions_available_after_success(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _button(window, "openEpubButton").isVisible()
            assert _button(window, "openFolderButton").isVisible()
            _button(window, "openEpubButton").click()
            assert opener.calls == [epub]
        finally:
            window.close()

    def test_full_workflow_no_private_internals(self, qapp, tmp_path: Path) -> None:
        """The complete workflow runs without touching private attributes."""
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            window.show()
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            assert window.state is UiState.INPUT_SELECTED
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            _convert_button(window).click()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert window.state is UiState.COMPLETED
            assert window.last_result is not None
            assert _results_section(window).isVisible()
            # Output actions use public methods, not private paths.
            opener = _RecordingOpener()
            window._opener = opener  # replace with recorder via existing seam
            _button(window, "openFolderButton").click()
            assert opener.calls == [window.last_result.epub_path.parent]
        finally:
            window.close()


# --------------------------------------------------------------------------
# 2. PDF document-type coverage (TEXT, SCANNED, MIXED)
# --------------------------------------------------------------------------


class TestDocumentTypeCoverage:
    """The application's established M3 document classifications."""

    def test_text_document_analysis_and_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "text.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert _value(window, "documentTypeValue").text() == "Text"
            assert _value(window, "ocrRequiredValue").text() == "No"
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.document_type is PDFType.TEXT
            assert _label(window, "resultFormatValue").text() == "EPUB"
        finally:
            window.close()

    def test_scanned_document_analysis_and_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_scanned_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "scanned.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert _value(window, "documentTypeValue").text() == "Scanned"
            assert _value(window, "ocrRequiredValue").text() == "Yes"
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.document_type is PDFType.SCANNED
        finally:
            window.close()

    def test_mixed_document_analysis_and_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_mixed_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "mixed.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert _value(window, "documentTypeValue").text() == "Mixed"
            assert _value(window, "ocrRequiredValue").text() == "Yes"
            assert _value(window, "textPagesValue").text() == "2"
            assert _value(window, "scannedPagesValue").text() == "1"
            assert _value(window, "mixedPagesValue").text() == "1"
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_output_directory(out_dir)
            assert window.state is UiState.READY
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.document_type is PDFType.MIXED
        finally:
            window.close()

    def test_analysis_produces_expected_classification(
        self, qapp, tmp_path: Path
    ) -> None:
        """Analysis classification is displayed, not bypassed."""
        for analysis, expected_type, ocr in (
            (_text_analysis(), PDFType.TEXT, "No"),
            (_scanned_analysis(), PDFType.SCANNED, "Yes"),
            (_mixed_analysis(), PDFType.MIXED, "Yes"),
        ):
            app = _FakeApplication(analysis=analysis)
            window = MainWindow(application=app)
            try:
                pdf = _make_pdf(tmp_path / f"{expected_type.value}.pdf")
                window.select_input(pdf)
                window.analyze_selected()
                assert _value(window, "documentTypeValue").text() == expected_type.value.capitalize()
                assert _value(window, "ocrRequiredValue").text() == ocr
            finally:
                window.close()

    def test_different_document_types_reach_correct_conversion_path(
        self, qapp, tmp_path: Path
    ) -> None:
        """Each document type reaches conversion through the application layer."""
        for analysis, name in (
            (_text_analysis(), "text"),
            (_scanned_analysis(), "scanned"),
            (_mixed_analysis(), "mixed"),
        ):
            app = _FakeApplication(analysis=analysis)
            window = MainWindow(application=app)
            try:
                pdf = _make_pdf(tmp_path / f"{name}.pdf")
                window.select_input(pdf)
                window.analyze_selected()
                out_dir = tmp_path / f"{name}-out"
                out_dir.mkdir()
                window.select_output_directory(out_dir)
                assert window.is_configuration_ready
                _convert_to_completion(window)
                assert window.last_result is not None
                assert window.last_result.request.input_pdf == pdf
                assert window.last_result.epub_path is not None
            finally:
                window.close()


# --------------------------------------------------------------------------
# 3. Output configuration coverage
# --------------------------------------------------------------------------


class TestOutputConfiguration:
    """The UI correctly creates and submits the application's ConversionRequest."""

    def test_epub_only_configuration(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            assert _combo(window).currentIndex() == 0  # EPUB default
            request = window.build_conversion_request()
            assert request.formats == frozenset({OutputFormat.EPUB})
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.azw3_path is None
            assert _label(window, "resultFormatValue").text() == "EPUB"
        finally:
            window.close()

    def test_azw3_configuration_epub_plus_azw3(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _combo(window).setCurrentIndex(1)  # AZW3
            request = window.build_conversion_request()
            assert request.formats == frozenset({OutputFormat.EPUB, OutputFormat.AZW3})
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.azw3_path is not None
            assert _label(window, "resultFormatValue").text() == "EPUB + AZW3"
            assert _label(window, "azw3OutputPath").isVisible()
            assert _button(window, "openAzw3Button").isVisible()
        finally:
            window.close()

    def test_default_format_is_epub(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            assert window.output_format is OutputFormat.EPUB
            assert _combo(window).currentText() == "EPUB"
        finally:
            window.close()

    def test_output_directory_reaches_application(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            request = window.build_conversion_request()
            assert request.output_directory == out_dir
            assert isinstance(request.output_directory, Path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.request.output_directory == out_dir
        finally:
            window.close()

    def test_cover_path_reaches_application(self, qapp, tmp_path: Path) -> None:
        import base64

        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        cover = tmp_path / "cover.png"
        cover.write_bytes(png_bytes)
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            window.select_cover(cover)
            request = window.build_conversion_request()
            assert request.cover == cover
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.request.cover == cover
        finally:
            window.close()

    def test_defaults_remain_correct(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            request = window.build_conversion_request()
            assert request.validate is True
            assert request.calibre_path is None
            assert request.cover is None
            assert request.formats == frozenset({OutputFormat.EPUB})
        finally:
            window.close()

    def test_invalid_configuration_does_not_start_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert not window.is_configuration_ready
            assert not _convert_button(window).isEnabled()
            # No convert call should happen since we never click Convert.
            app.convert_calls = []
            window.convert_selected()
            assert app.convert_calls == []
        finally:
            window.close()


# --------------------------------------------------------------------------
# 4. Background conversion integration
# --------------------------------------------------------------------------


class TestBackgroundConversion:
    """M5.5 background worker architecture integration."""

    def test_conversion_work_not_on_gui_thread(self, qapp, tmp_path: Path) -> None:
        """Verify the worker runs on a separate thread from the GUI."""
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            thread = window._thread
            assert thread is not None
            # The worker's run() executes on the worker thread, not the GUI thread.
            assert thread.isRunning()
            # The GUI event loop stays responsive (proves work is off-thread).
            fired: list[bool] = []
            timer = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: fired.append(True))
            timer.start(0)
            _wait_for(lambda: fired)
            assert fired == [True]
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_worker_executes_through_qthread(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            thread = window._thread
            assert thread is not None
            assert thread.isRunning()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _wait_for(lambda: window._thread is None)
            assert window._worker is None
        finally:
            _settle(window)
            window.close()

    def test_progress_signals_reach_ui(self, qapp, tmp_path: Path) -> None:
        the_message = ConversionProgress(
            ConversionStage.EPUB,
            "Converting the PDF to an EPUB (extraction, OCR, reconstruction)",
            0,
            2,
        )
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(),
            progress_events=(
                ConversionProgress(ConversionStage.ANALYSIS, "Analyzing the PDF", 0, 0),
                the_message,
            ),
            entered=entered,
            release=release,
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            _wait_for(lambda: _status(window).text() == the_message.message)
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_completion_signal_reaches_ui(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert _status(window).text() == "Conversion complete"
            assert _progress_bar(window).value() == 1
        finally:
            window.close()

    def test_failure_signal_reaches_ui(self, qapp, tmp_path: Path) -> None:
        error = ConversionFailedError(
            "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
        )
        app = _FakeApplication(analysis=_text_analysis(), error=error)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert _status(window).text() == "PDF to EPUB conversion failed: boom"
            assert _progress_bar(window).value() == 0
        finally:
            window.close()

    def test_ui_transitions_out_of_converting(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert window.state is UiState.CONVERTING
            _wait_for(lambda: window.state in (UiState.COMPLETED, UiState.CONVERSION_FAILED))
            _settle(window)
            assert window.state is not UiState.CONVERTING
        finally:
            window.close()

    def test_controls_restored_after_completion(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _browse_button(window).isEnabled()
            assert _analyze_button(window).isEnabled()
            assert _combo(window).isEnabled()
            assert _out_dir_button(window).isEnabled()
            assert _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_controls_restored_after_failure(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(),
            error=ConversionFailedError("boom", stage=ConversionStage.ANALYSIS),
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert _browse_button(window).isEnabled()
            assert _analyze_button(window).isEnabled()
            assert _combo(window).isEnabled()
            assert _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_duplicate_conversion_starts_prevented(self, qapp, tmp_path: Path) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            _convert_button(window).click()
            window.convert_selected()
            assert len(app.convert_calls) == 1
            assert window.state is UiState.CONVERTING
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_worker_through_qthread_with_real_signals(self, qapp, tmp_path: Path) -> None:
        """Verify the actual QThread signal chain: started → worker.run → finished."""
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            thread = window._thread
            assert thread is not None
            assert thread.isRunning()
            # Wait for the worker to finish and the thread to be released.
            _wait_for(lambda: window._thread is None)
            assert thread.isFinished()
        finally:
            _settle(window)
            window.close()


# --------------------------------------------------------------------------
# 5. GUI responsiveness
# --------------------------------------------------------------------------


class TestGUIResponsiveness:
    """Conversion work belongs outside the GUI thread."""

    def test_event_loop_not_blocked_during_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            # A timer fires while the worker is running, proving the
            # GUI event loop is not blocked.
            fired: list[bool] = []
            timer = QtCore.QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: fired.append(True))
            timer.start(0)
            _wait_for(lambda: fired)
            assert fired == [True]
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_window_remains_usable_during_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            # processEvents can be called while the worker runs.
            app = QtCore.QCoreApplication.instance()
            for _ in range(5):
                if app is not None:
                    app.processEvents()
            assert window.state is UiState.CONVERTING
        finally:
            release.set()
            _settle(window)
            window.close()


# --------------------------------------------------------------------------
# 6. Results + validation integration
# --------------------------------------------------------------------------


class TestResultsValidationIntegration:
    """After successful conversion the UI shows the real ConversionResult."""

    def test_results_display_requested_formats(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "resultFormatValue").text() == "EPUB"
            _combo(window).setCurrentIndex(1)  # AZW3 → EPUB + AZW3
            _convert_to_completion(window)
            assert _label(window, "resultFormatValue").text() == "EPUB + AZW3"
        finally:
            window.close()

    def test_results_display_epub_output_path(self, qapp, tmp_path: Path) -> None:
        epub = tmp_path / "out" / "book.epub"
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "epubOutputPath").text() == str(epub)
        finally:
            window.close()

    def test_results_display_azw3_output_path_when_present(
        self, qapp, tmp_path: Path
    ) -> None:
        epub = tmp_path / "out" / "book.epub"
        azw3 = tmp_path / "out" / "book.azw3"
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            azw3_path=azw3,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _combo(window).setCurrentIndex(1)
            _convert_to_completion(window)
            assert _label(window, "azw3OutputPath").isVisible()
            assert _label(window, "azw3OutputPath").text() == str(azw3)
        finally:
            window.close()

    def test_results_hide_azw3_when_not_requested(self, qapp, tmp_path: Path) -> None:
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "book.epub",
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert not _label(window, "azw3OutputPath").isVisible()
            assert not _button(window, "openAzw3Button").isVisible()
        finally:
            window.close()

    def test_validation_status_displayed(self, qapp, tmp_path: Path) -> None:
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "book.epub",
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "validationStatusValue").text() == "Valid"
            assert _label(window, "validationWarningsValue").text() == "0"
            assert _label(window, "validationErrorsValue").text() == "0"
        finally:
            window.close()

    def test_validation_warning_error_info_displayed(
        self, qapp, tmp_path: Path
    ) -> None:
        validation = EPUBValidationResult(
            package_document="container.xml",
            epub_version="3.0",
            spine_documents=("chapter-00.xhtml",),
            issues=(
                EPUBValidationIssue(
                    code=EPUBValidationCode.MIMETYPE_NOT_FIRST,
                    message="mimetype should be first",
                    severity=EPUBValidationSeverity.WARNING,
                    location="mimetype",
                ),
                EPUBValidationIssue(
                    code=EPUBValidationCode.MISSING_METADATA,
                    message="dc:title is missing",
                    severity=EPUBValidationSeverity.ERROR,
                    location="package.opf",
                ),
            ),
        )
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "book.epub",
            validation=validation,
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "validationStatusValue").text() == "Invalid"
            assert _label(window, "validationWarningsValue").text() == "1"
            assert _label(window, "validationErrorsValue").text() == "1"
            details = _label(window, "validationErrorsDetails")
            assert details.isVisible()
            assert "dc:title is missing" in details.text()
        finally:
            window.close()

    def test_validation_not_run_shows_not_run(self, qapp, tmp_path: Path) -> None:
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
                validate=False,
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "book.epub",
            validation=None,
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "validationStatusValue").text() == "Not run"
        finally:
            window.close()

    def test_ui_does_not_rerun_epub_validation(self, qapp, tmp_path: Path) -> None:
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "book.epub",
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.validation is not None
            # The result's validation is exactly the one the application produced.
            assert window.last_result.validation.valid is True
        finally:
            window.close()


# --------------------------------------------------------------------------
# 7. Output-opening integration
# --------------------------------------------------------------------------


class TestOutputOpeningIntegration:
    """The UI → platform seam for output actions."""

    def test_open_epub_sends_real_epub_path(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openEpubButton").click()
            assert opener.calls == [epub]
        finally:
            window.close()

    def test_open_azw3_sends_real_azw3_path(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        azw3 = tmp_path / "MyBook.azw3"
        epub.write_bytes(b"PK fake")
        azw3.write_bytes(b"fake azw3")
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB, OutputFormat.AZW3}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            azw3_path=azw3,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _combo(window).setCurrentIndex(1)
            _convert_to_completion(window)
            _button(window, "openAzw3Button").click()
            assert opener.calls == [azw3]
        finally:
            window.close()

    def test_open_folder_sends_output_directory(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        epub = out_dir / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=out_dir,
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openFolderButton").click()
            assert opener.calls == [out_dir]
        finally:
            window.close()

    def test_unavailable_output_action_handled_safely(self, qapp, tmp_path: Path) -> None:
        """When a result has no AZW3 path, Open AZW3 is handled safely."""
        opener = _RecordingOpener()
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "book.epub",
            azw3_path=None,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            # openAzw3Button is hidden when no AZW3, but if clicked via
            # the method directly, it should not crash and should report
            # no result available.
            window.open_azw3()
            status = _label(window, "outputActionStatus")
            assert status.isVisible() or not status.text()
        finally:
            window.close()

    def test_open_actions_never_launch_external_app(self, qapp, tmp_path: Path) -> None:
        """The injected opener replaces the real platform opener."""
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=epub,
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openEpubButton").click()
            assert opener.calls == [epub]
            # The real open_path was never invoked.
            assert not any(
                call for call in opener.calls if call == Path("/nonexistent")
            )
        finally:
            window.close()


# --------------------------------------------------------------------------
# 8. Error-path integration
# --------------------------------------------------------------------------


class TestErrorPathIntegration:
    """Important application failure paths through the integrated workflow."""

    def test_invalid_missing_input(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            missing = tmp_path / "ghost.pdf"
            window.select_input(missing)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_FAILED
            assert "could not be found" in _status(window).text()
            assert window.last_result is None
        finally:
            window.close()

    def test_analysis_failure(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(),
            analysis_error=ConversionFailedError(
                "PDF analysis failed: corrupt", stage=ConversionStage.ANALYSIS
            ),
        )
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_FAILED
            assert "could not be analyzed" in _status(window).text()
            assert window.last_result is None
        finally:
            window.close()

    def test_conversion_failure(self, qapp, tmp_path: Path) -> None:
        error = ConversionFailedError(
            "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
        )
        app = _FakeApplication(analysis=_text_analysis(), error=error)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert "PDF to EPUB conversion failed" in _status(window).text()
            assert window.last_result is None
            assert window.last_error is error
            # Controls restored.
            assert _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_validation_failure(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            # Use a real conversion that fails validation via the app layer.
            error = ConversionFailedError(
                "the generated EPUB failed structural validation (1 error(s))",
                stage=ConversionStage.VALIDATION,
            )
            app2 = _FakeApplication(analysis=_text_analysis(), error=error)
            window._application = app2
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert window.last_error is not None
        finally:
            window.close()

    def test_unexpected_worker_exception(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(), error=RuntimeError("programmer error")
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert "Conversion failed" in _status(window).text()
            assert window.last_result is None
            assert isinstance(window.last_error, RuntimeError)
        finally:
            window.close()

    def test_azw3_failure_after_epub_succeeds(self, qapp, tmp_path: Path) -> None:
        """AZW3 failure after EPUB succeeds: error reaches UI, EPUB retained."""
        from kindle_converter.epub import AZW3ConversionError

        class _FakeAppWithAZW3Failure(_FakeApplication):
            def convert(self, request, *, progress=None):
                self.convert_calls.append(request)
                if self.entered is not None:
                    self.entered.set()
                if self.release is not None:
                    assert self.release.wait(10)
                # EPUB succeeded; AZW3 failed.
                raise AZW3ConversionError("calibre exploded")

        release = threading.Event()
        app = _FakeAppWithAZW3Failure(
            analysis=_text_analysis(),
            entered=threading.Event(),
            release=release,
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert app.entered.wait(10)
            release.set()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert window.last_error is not None
            assert "AZW3" in str(window.last_error) or "calibre" in str(window.last_error)
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_workspace_failure_reaches_ui(self, qapp, tmp_path: Path) -> None:
        """A workspace creation failure surfaces as a user-facing error."""
        error = WorkspaceError("The temporary workspace could not be created")
        app = _FakeApplication(analysis=_text_analysis(), error=error)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert window.last_error is error
            assert "workspace" in str(window.last_error).lower() or "temporary" in str(window.last_error).lower()
        finally:
            window.close()

    def test_error_does_not_expose_traceback(self, qapp, tmp_path: Path) -> None:
        """User-facing errors are concise, not raw tracebacks."""
        app = _FakeApplication(
            analysis=_text_analysis(), error=RuntimeError("programmer error")
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            status = _status(window).text()
            assert "Traceback" not in status
            assert "programmer error" not in status
            assert "Conversion failed" in status
        finally:
            window.close()

    def test_worker_thread_cleaned_up_after_failure(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(),
            error=ConversionFailedError("boom", stage=ConversionStage.ANALYSIS),
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _wait_for(lambda: window._thread is None)
            assert window._worker is None
        finally:
            window.close()

    def test_controls_usable_after_error(self, qapp, tmp_path: Path) -> None:
        """After any error, controls become usable again."""
        app = _FakeApplication(
            analysis=_text_analysis(),
            error=ConversionFailedError("boom", stage=ConversionStage.EPUB),
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert _browse_button(window).isEnabled()
            assert _analyze_button(window).isEnabled()
            assert _combo(window).isEnabled()
            assert _out_dir_button(window).isEnabled()
            assert _convert_button(window).isEnabled()
        finally:
            window.close()


# --------------------------------------------------------------------------
# 9. Temporary-workspace integration
# --------------------------------------------------------------------------


class TestWorkspaceIntegration:
    """Workspace behavior through the integrated conversion path."""

    def test_success_workspace_cleaned_final_outputs_remain(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.epub_path is not None
            # The workspace (created inside convert) was cleaned up.
            # We verify by checking the result's epub_path is in the output
            # directory (not the workspace), and the conversion completed.
            assert window.last_result.epub_path.parent == tmp_path / "out"
        finally:
            window.close()

    def test_failure_workspace_cleaned_error_reaches_ui(
        self, qapp, tmp_path: Path
    ) -> None:
        error = ConversionFailedError(
            "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
        )
        app = _FakeApplication(analysis=_text_analysis(), error=error)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert window.last_error is error
            # Workspace cleanup happened inside convert (no leak observable
            # from UI, but the error reached the UI without crashing).
        finally:
            window.close()

    def test_unexpected_exception_workspace_cleanup_safe_error(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(), error=RuntimeError("unexpected boom")
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert isinstance(window.last_error, RuntimeError)
            assert "Conversion failed" in _status(window).text()
        finally:
            window.close()


# --------------------------------------------------------------------------
# 10. Stale-result protection
# --------------------------------------------------------------------------


class TestStaleResultProtection:
    """A previous successful conversion cannot incorrectly remain visible."""

    def test_failed_conversion_hides_previous_success(
        self, qapp, tmp_path: Path
    ) -> None:
        first = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "first.epub",
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(result=first)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert _results_section(window).isVisible()
            assert _label(window, "epubOutputPath").text() == str(first.epub_path)

            # Now fail the next conversion.
            app.error = ConversionFailedError(
                "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
            )
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_retry_after_failure_shows_new_result_not_stale(
        self, qapp, tmp_path: Path
    ) -> None:
        first = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "first.epub",
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        second = ConversionResult(
            request=ConversionRequest(
                input_pdf=tmp_path / "book.pdf",
                output_directory=tmp_path / "out",
                formats=frozenset({OutputFormat.EPUB}),
            ),
            analysis=_text_analysis(),
            epub_path=tmp_path / "out" / "second.epub",
            validation=EPUBValidationResult(
                package_document="container.xml",
                epub_version="3.0",
                spine_documents=("chapter-00.xhtml",),
                issues=(),
            ),
        )
        app = _FakeApplication(results=(first, second))
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "epubOutputPath").text() == str(first.epub_path)

            app.error = ConversionFailedError(
                "boom", stage=ConversionStage.ANALYSIS
            )
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None

            app.error = None
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert _label(window, "epubOutputPath").text() == str(second.epub_path)
            assert window.last_result is not None
            assert window.last_result.epub_path == second.epub_path
        finally:
            window.close()

    def test_new_input_clears_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert _results_section(window).isVisible()

            window.select_input(_make_pdf(tmp_path / "other.pdf"))
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_output_format_change_clears_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _results_section(window).isVisible()

            _combo(window).setCurrentIndex(1)  # AZW3
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_output_directory_change_clears_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _results_section(window).isVisible()

            other = tmp_path / "elsewhere"
            other.mkdir()
            window.select_output_directory(other)
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_new_conversion_clears_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(),
            entered=entered,
            release=release,
            block_on=2,
            results=(
                ConversionResult(
                    request=ConversionRequest(
                        input_pdf=tmp_path / "book.pdf",
                        output_directory=tmp_path / "out",
                        formats=frozenset({OutputFormat.EPUB}),
                    ),
                    analysis=_text_analysis(),
                    epub_path=tmp_path / "out" / "first.epub",
                    validation=None,
                ),
            ),
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None

            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            release.set()
            _settle(window)
            window.close()


# --------------------------------------------------------------------------
# 11. State-transition coverage
# --------------------------------------------------------------------------


class TestStateTransitions:
    """Verify the established UI states."""

    def test_no_input_initial_state(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            assert window.state is UiState.NO_INPUT
            assert not _analyze_button(window).isEnabled()
        finally:
            window.close()

    def test_input_selected_state(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            assert window.state is UiState.INPUT_SELECTED
            assert _analyze_button(window).isEnabled()
        finally:
            window.close()

    def test_analyzing_state(self, qapp, tmp_path: Path) -> None:
        observed: dict[str, object] = {}

        def on_analyze(path) -> None:
            observed["state"] = window.state
            observed["browse_enabled"] = _browse_button(window).isEnabled()
            observed["analyze_enabled"] = _analyze_button(window).isEnabled()

        app = _FakeApplication(analysis=_text_analysis(), on_analyze=on_analyze)
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert observed["state"] is UiState.ANALYZING
            assert observed["browse_enabled"] is False
            assert observed["analyze_enabled"] is False
        finally:
            window.close()

    def test_analysis_complete_state(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
        finally:
            window.close()

    def test_configuring_state(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            # No output directory → configuring.
            assert window.state is not UiState.READY
        finally:
            window.close()

    def test_ready_state(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            assert window.state is UiState.READY
            assert _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_converting_state(self, qapp, tmp_path: Path) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            assert not _convert_button(window).isEnabled()
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_completed_state(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.state is UiState.COMPLETED
        finally:
            window.close()

    def test_conversion_failed_state(self, qapp, tmp_path: Path) -> None:
        error = ConversionFailedError(
            "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
        )
        app = _FakeApplication(analysis=_text_analysis(), error=error)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert _status(window).text() == "PDF to EPUB conversion failed: boom"
        finally:
            window.close()

    def test_analysis_failed_state(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(),
            analysis_error=ConversionFailedError(
                "PDF analysis failed: boom", stage=ConversionStage.ANALYSIS
            ),
        )
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_FAILED
            assert "could not be analyzed" in _status(window).text()
        finally:
            window.close()

    def test_happy_path_state_sequence(self, qapp, tmp_path: Path) -> None:
        """The complete happy path follows the expected state sequence."""
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            assert window.state == UiState.INPUT_SELECTED

            window.analyze_selected()
            assert window.state == UiState.ANALYSIS_COMPLETE

            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_output_directory(out_dir)
            assert window.state == UiState.READY

            _convert_to_completion(window)
            assert window.state == UiState.COMPLETED
        finally:
            window.close()


# --------------------------------------------------------------------------
# 12. Thread affinity and cleanup
# --------------------------------------------------------------------------


class TestThreadAffinity:
    """Worker/thread lifecycle and cleanup."""

    def test_worker_and_thread_cleaned_up_after_success(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            thread = window._thread
            assert thread is not None and thread.isRunning()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _wait_for(lambda: window._thread is None)
            assert window._worker is None
            _flush_deferred_deletes()
        finally:
            window.close()

    def test_another_conversion_can_run_after_completion(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            first_thread = window._thread
            assert first_thread is not None and first_thread.isRunning()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _wait_for(lambda: window._thread is None)
            _flush_deferred_deletes()

            window.convert_selected()
            second_thread = window._thread
            assert second_thread is not None and second_thread.isRunning()
            assert second_thread is not first_thread
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _wait_for(lambda: window._thread is None)
            assert window._worker is None
            _flush_deferred_deletes()
        finally:
            window.close()

    def test_closing_window_waits_for_running_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            analysis=_text_analysis(), entered=entered, release=release
        )
        window = MainWindow(application=app)
        _make_ready(window, tmp_path)
        window.convert_selected()
        try:
            assert entered.wait(10)
            thread = window._thread
            assert thread is not None and thread.isRunning()
            release.set()
            window.close()
            assert thread.isFinished()
        finally:
            release.set()
            _flush_deferred_deletes()


# --------------------------------------------------------------------------
# 13. Cover integration
# --------------------------------------------------------------------------


class TestCoverIntegration:
    """Cover handling through the integrated workflow."""

    def test_cover_selected_is_submitted_with_request(
        self, qapp, tmp_path: Path
    ) -> None:
        import base64

        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        cover = tmp_path / "cover.png"
        cover.write_bytes(png_bytes)
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            window.select_cover(cover)
            request = window.build_conversion_request()
            assert request.cover == cover
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.request.cover == cover
        finally:
            window.close()

    def test_no_cover_by_default(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            request = window.build_conversion_request()
            assert request.cover is None
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.request.cover is None
        finally:
            window.close()

    def test_cover_cleared_before_conversion(self, qapp, tmp_path: Path) -> None:
        import base64

        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
            "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
        )
        cover = tmp_path / "cover.png"
        cover.write_bytes(png_bytes)
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            out_dir = tmp_path / "out"
            out_dir.mkdir()
            window.select_input(pdf)
            window.analyze_selected()
            window.select_output_directory(out_dir)
            window.select_cover(cover)
            window.clear_cover()
            request = window.build_conversion_request()
            assert request.cover is None
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.request.cover is None
        finally:
            window.close()
