"""Headless tests for the M5.3 input-selection + PDF-analysis UI workflow.

The window under test drives the M5.1 application boundary through an injected
test double (a ``ConversionApplication`` substitute with a duck-typed
``analyze_pdf``), so no real PDF analysis is required for the UI assertions
and the tests stay deterministic and fast. A couple of tests build a real PDF
with PyMuPDF only to prove the *application* side; none require Tesseract,
Calibre, a network, or a visible desktop.

All Qt dependencies run headless via ``QT_QPA_PLATFORM=offscreen``; the module
skips cleanly when PySide6 is not installed (the ``ui`` extra is absent).

``QT_QPA_PLATFORM`` must be set before PySide6 loads its platform plugin, so
it is configured at module import time before any Qt symbol is touched.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6 import QtWidgets  # noqa: E402  (after importorskip)

import pymupdf  # noqa: E402

from kindle_converter.application import (  # noqa: E402
    ConversionFailedError,
    ConversionStage,
    InvalidRequestError,
)
from kindle_converter.pdf import PDFAnalysis, PDFType, PageAnalysis  # noqa: E402
from kindle_converter.ui import MainWindow, UiState  # noqa: E402

_NO_VALUE = "\u2014"


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    yield application


class _FakeApplication:
    """Duck-typed stand-in for ``kindle_converter.application.ConversionApplication``.

    Records every ``analyze_pdf`` call and returns a canned analysis, raises a
    canned error, and/or runs a probe callback exactly like the real boundary
    would -- without touching any PDF content.
    """

    def __init__(
        self,
        *,
        analysis: PDFAnalysis | None = None,
        error: Exception | None = None,
        on_analyze=None,
    ) -> None:
        self.analysis = analysis
        self.error = error
        self.on_analyze = on_analyze
        self.analyze_calls: list[Path] = []

    def analyze_pdf(self, path) -> PDFAnalysis:
        self.analyze_calls.append(Path(path))
        if self.on_analyze is not None:
            self.on_analyze(path)
        if self.error is not None:
            raise self.error
        assert self.analysis is not None
        return self.analysis


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


def _page(number: int, classification: PDFType) -> PageAnalysis:
    return PageAnalysis(
        page_number=number,
        has_image=classification is not PDFType.TEXT,
        has_meaningful_text=classification is not PDFType.SCANNED,
        char_count=40,
        classification=classification,
    )


def _mixed_analysis() -> PDFAnalysis:
    """3 text + 1 scanned + 1 mixed page -> a MIXED, OCR-required document."""
    pages = [
        _page(1, PDFType.TEXT),
        _page(2, PDFType.TEXT),
        _page(3, PDFType.TEXT),
        _page(4, PDFType.SCANNED),
        _page(5, PDFType.MIXED),
    ]
    return PDFAnalysis(
        page_count=5,
        text_page_count=4,
        image_page_count=2,
        text_density=40.0,
        document_type=PDFType.MIXED,
        pages=pages,
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


def _value(window: MainWindow, object_name: str) -> QtWidgets.QLabel:
    label = window.findChild(QtWidgets.QLabel, object_name)
    assert label is not None, f"missing analysis value label {object_name!r}"
    return label


# --------------------------------------------------------------------------
# File selection
# --------------------------------------------------------------------------


class TestFileSelection:
    def test_initial_window_has_no_selected_input(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            assert window.state is UiState.NO_INPUT
            assert window.selected_path is None
            path_edit = window.findChild(QtWidgets.QLineEdit, "inputPath")
            assert path_edit is not None and path_edit.text() == ""
            analyze_button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            assert analyze_button is not None and not analyze_button.isEnabled()
        finally:
            window.close()

    def test_browse_action_updates_selected_path(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=_FakeApplication())
        try:
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                lambda *args, **kwargs: (str(pdf), "PDF files (*.pdf)"),
            )
            window.browse_for_input()
            assert window.state is UiState.INPUT_SELECTED
            assert window.selected_path == pdf
            path_edit = window.findChild(QtWidgets.QLineEdit, "inputPath")
            assert path_edit.text() == str(pdf)
            analyze_button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            assert analyze_button.isEnabled()
        finally:
            window.close()

    def test_cancelled_dialog_preserves_selection(
        self, qapp, tmp_path: Path, monkeypatch
    ) -> None:
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=_FakeApplication())
        try:
            window.select_input(pdf)
            assert window.state is UiState.INPUT_SELECTED
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                lambda *args, **kwargs: ("", ""),
            )
            window.browse_for_input()
            assert window.state is UiState.INPUT_SELECTED
            assert window.selected_path == pdf
            path_edit = window.findChild(QtWidgets.QLineEdit, "inputPath")
            assert path_edit.text() == str(pdf)
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Ready to analyze"
        finally:
            window.close()

    def test_selecting_new_file_invalidates_stale_analysis(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_mixed_analysis())
        first = _make_pdf(tmp_path / "one.pdf")
        second = _make_pdf(tmp_path / "two.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(first)
            window.analyze_selected()
            assert _value(window, "pagesValue").text() == "5"
            assert window.last_analysis is not None

            window.select_input(second)
            assert window.state is UiState.INPUT_SELECTED
            assert window.selected_path == second
            assert window.last_analysis is None
            assert _value(window, "pagesValue").text() == _NO_VALUE
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Ready to analyze"
        finally:
            window.close()


# --------------------------------------------------------------------------
# Analyze behavior
# --------------------------------------------------------------------------


class TestAnalyzeBehavior:
    def test_analyze_is_unavailable_without_input(self, qapp) -> None:
        app = _FakeApplication(analysis=_mixed_analysis())
        window = MainWindow(application=app)
        try:
            analyze_button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            assert not analyze_button.isEnabled()
            window.analyze_selected()
            assert app.analyze_calls == []
            assert window.state is UiState.ANALYSIS_FAILED
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Please select a PDF file."
        finally:
            window.close()

    def test_valid_input_triggers_application_analysis(
        self, qapp, tmp_path: Path
    ) -> None:
        analysis = _mixed_analysis()
        app = _FakeApplication(analysis=analysis)
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert app.analyze_calls == [pdf]
            assert window.last_analysis is analysis
            assert window.state is UiState.ANALYSIS_COMPLETE
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Analysis complete"
            analyze_button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            assert analyze_button.isEnabled()
        finally:
            window.close()

    def test_repeated_analyze_clicks_prevented_while_running(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(
            analysis=_mixed_analysis(),
            on_analyze=lambda path: window.analyze_selected(),
        )
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            # The nested click arrived while the (synchronous) run was active and
            # was ignored: only the original call reached the application.
            assert app.analyze_calls == [pdf]
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert _value(window, "pagesValue").text() == "5"
        finally:
            window.close()

    def test_analyzing_state_disables_conflicting_controls(
        self, qapp, tmp_path: Path
    ) -> None:
        observed: dict[str, object] = {}

        def on_analyze(path) -> None:
            browse = window.findChild(QtWidgets.QPushButton, "browseButton")
            analyze = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            observed["state"] = window.state
            observed["status"] = status.text()
            observed["browse_enabled"] = browse.isEnabled()
            observed["analyze_enabled"] = analyze.isEnabled()

        app = _FakeApplication(analysis=_mixed_analysis(), on_analyze=on_analyze)
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert observed["state"] is UiState.ANALYZING
            assert observed["status"] == "Analyzing..."
            assert observed["browse_enabled"] is False
            assert observed["analyze_enabled"] is False
            assert window.state is UiState.ANALYSIS_COMPLETE
        finally:
            window.close()


# --------------------------------------------------------------------------
# Analysis display
# --------------------------------------------------------------------------


class TestAnalysisDisplay:
    def test_all_analysis_values_are_displayed(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_mixed_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert _value(window, "pagesValue").text() == "5"
            assert _value(window, "documentTypeValue").text() == "Mixed"
            assert _value(window, "textPagesValue").text() == "3"
            assert _value(window, "scannedPagesValue").text() == "1"
            assert _value(window, "mixedPagesValue").text() == "1"
            assert _value(window, "ocrRequiredValue").text() == "Yes"
        finally:
            window.close()

    def test_text_document_shows_ocr_not_required(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert _value(window, "pagesValue").text() == "2"
            assert _value(window, "documentTypeValue").text() == "Text"
            assert _value(window, "textPagesValue").text() == "2"
            assert _value(window, "scannedPagesValue").text() == "0"
            assert _value(window, "mixedPagesValue").text() == "0"
            assert _value(window, "ocrRequiredValue").text() == "No"
        finally:
            window.close()

    def test_stub_analysis_without_per_page_rows_still_displays(
        self, qapp, tmp_path: Path
    ) -> None:
        analysis = PDFAnalysis(
            page_count=10,
            text_page_count=8,
            image_page_count=0,
            text_density=40.0,
            document_type=PDFType.TEXT,
            pages=[],
        )
        app = _FakeApplication(analysis=analysis)
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert _value(window, "pagesValue").text() == "10"
            assert _value(window, "documentTypeValue").text() == "Text"
            assert _value(window, "textPagesValue").text() == "8"
            assert _value(window, "scannedPagesValue").text() == "2"
            assert _value(window, "mixedPagesValue").text() == "0"
            assert _value(window, "ocrRequiredValue").text() == "Yes"
        finally:
            window.close()


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


class TestErrorHandling:
    def test_missing_input_message(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            window.analyze_selected()
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Please select a PDF file."
            assert window.state is UiState.ANALYSIS_FAILED
            assert window.last_analysis is None
        finally:
            window.close()

    def test_nonexistent_path_message(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_mixed_analysis())
        window = MainWindow(application=app)
        try:
            missing = tmp_path / "ghost.pdf"
            window.select_input(missing)
            window.analyze_selected()
            assert app.analyze_calls == []
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "The selected PDF file could not be found."
            assert window.state is UiState.ANALYSIS_FAILED
        finally:
            window.close()

    def test_non_file_input_message(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_mixed_analysis()))
        try:
            directory = tmp_path / "dir.pdf"
            directory.mkdir()
            window.select_input(directory)
            window.analyze_selected()
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "The selected input is not a regular file."
            assert window.state is UiState.ANALYSIS_FAILED
        finally:
            window.close()

    def test_application_analysis_failure_message(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            error=ConversionFailedError(
                "PDF analysis failed: boom", stage=ConversionStage.ANALYSIS
            )
        )
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "The PDF could not be analyzed."
            assert window.state is UiState.ANALYSIS_FAILED
            assert window.selected_path == pdf
            assert window.last_analysis is None
            assert _value(window, "pagesValue").text() == _NO_VALUE
            analyze_button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            assert analyze_button.isEnabled()
        finally:
            window.close()

    def test_invalid_request_error_is_translated(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            error=InvalidRequestError("the input PDF 'x' does not exist")
        )
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "The selected PDF file could not be found."
        finally:
            window.close()

    def test_unexpected_failure_shows_generic_message(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(error=RuntimeError("programmer error"))
        pdf = _make_pdf(tmp_path / "book.pdf")
        window = MainWindow(application=app)
        try:
            window.select_input(pdf)
            window.analyze_selected()
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "The PDF could not be analyzed."
            assert window.state is UiState.ANALYSIS_FAILED
            assert window.selected_path == pdf
            analyze_button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
            assert analyze_button.isEnabled()
        finally:
            window.close()

    def test_ui_remains_usable_after_error(self, qapp, tmp_path: Path) -> None:
        pdf = _make_pdf(tmp_path / "book.pdf")
        analysis = _mixed_analysis()

        class _Flaky:
            def __init__(self) -> None:
                self.calls = 0

            def analyze_pdf(self, path) -> PDFAnalysis:
                self.calls += 1
                if self.calls == 1:
                    raise ConversionFailedError(
                        "PDF analysis failed: boom", stage=ConversionStage.ANALYSIS
                    )
                return analysis

        window = MainWindow(application=_Flaky())
        try:
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_FAILED

            window.analyze_selected()  # retry with the same input
            assert window.state is UiState.ANALYSIS_COMPLETE
            status = window.findChild(QtWidgets.QLabel, "statusLabel")
            assert status.text() == "Analysis complete"
            assert _value(window, "pagesValue").text() == "5"
        finally:
            window.close()