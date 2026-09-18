"""Headless tests for the M5.6 post-conversion results + validation UI.

The window under test drives the M5.1 application boundary through an injected
deterministic double (``analyze_pdf`` / ``validate_request`` / ``convert``) and
replaces the platform-opener with a recording double, so no real PDF / OCR /
EPUB machinery and no external application launch are involved. The conversion
itself runs on a real background ``QThread`` (as in production), exercising the
actual thread lifecycle; the results assertions verify that every displayed
value comes from the real application-layer :class:`ConversionResult` and its
existing :class:`EPUBValidationResult`.

These tests never rerun EPUB validation (the same guarantee is enforced in the
source of ``main_window.py``) and never launch an external program.

All Qt dependencies run headless via ``QT_QPA_PLATFORM=offscreen``; the module
skips cleanly when PySide6 is not installed (the ``ui`` extra is absent).
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

import pymupdf  # noqa: E402

from PySide6 import QtCore, QtWidgets  # noqa: E402  (after importorskip)

from kindle_converter.application import (  # noqa: E402
    ConversionFailedError,
    ConversionRequest,
    ConversionResult,
    ConversionStage,
    OutputFormat,
)
from kindle_converter.epub import (  # noqa: E402
    EPUBValidationCode,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
)
from kindle_converter.pdf import (  # noqa: E402
    PDFAnalysis,
    PDFType,
    PageAnalysis,
)
from kindle_converter.ui import MainWindow, PlatformOpenError, UiState  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
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


def _page(number: int, classification: PDFType = PDFType.TEXT) -> PageAnalysis:
    return PageAnalysis(
        page_number=number,
        has_image=classification is not PDFType.TEXT,
        has_meaningful_text=classification is not PDFType.SCANNED,
        char_count=40,
        classification=classification,
    )


def _text_analysis() -> PDFAnalysis:
    pages = [_page(1), _page(2)]
    return PDFAnalysis(
        page_count=2,
        text_page_count=2,
        image_page_count=0,
        text_density=40.0,
        document_type=PDFType.TEXT,
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


def _validation(*, warnings: tuple[str, ...] = (), errors: tuple[str, ...] = ()) -> EPUBValidationResult:
    """A real ``EPUBValidationResult`` carrying the requested findings."""
    issues = [
        EPUBValidationIssue(
            code=EPUBValidationCode.MIMETYPE_NOT_FIRST,
            message=message,
            severity=EPUBValidationSeverity.WARNING,
            location="mimetype",
        )
        for message in warnings
    ]
    issues.extend(
        EPUBValidationIssue(
            code=EPUBValidationCode.MISSING_METADATA,
            message=message,
            severity=EPUBValidationSeverity.ERROR,
            location="package.opf",
        )
        for message in errors
    )
    return EPUBValidationResult(
        package_document="package.opf",
        epub_version="3.0",
        spine_documents=("chapter-00.xhtml",),
        issues=tuple(issues),
    )


def _result(
    request: ConversionRequest,
    analysis: PDFAnalysis,
    *,
    epub_path: str = "out/book.epub",
    azw3_path: str | None = None,
    validation: EPUBValidationResult | None = None,
    warnings: tuple[str, ...] = (),
) -> ConversionResult:
    """Build a real ``ConversionResult`` from the app-model types."""
    return ConversionResult(
        request=request,
        analysis=analysis,
        epub_path=Path(epub_path),
        azw3_path=Path(azw3_path) if azw3_path is not None else None,
        validation=validation,
        warnings=warnings,
    )


def _epub_only_result(request: ConversionRequest, analysis: PDFAnalysis) -> ConversionResult:
    return _result(
        request,
        analysis,
        epub_path="out/book.epub",
        validation=_validation(),
    )


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

    ``results`` is a queue of canned ``ConversionResult`` values consumed one
    per ``convert`` call (the last one repeats), so a test can make a later
    conversion produce a different real result and verify it replaces the
    earlier one. ``convert`` always returns a result whose ``request`` is the
    exact request that reached the application (``dataclasses.replace``), so
    ``window.last_result.request`` reflects the real request object.
    """

    def __init__(
        self,
        *,
        analysis: PDFAnalysis | None = None,
        result: ConversionResult | None = None,
        results: tuple[ConversionResult, ...] = (),
        error: Exception | None = None,
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        block_on: int = 0,
    ) -> None:
        self.analysis = analysis if analysis is not None else _text_analysis()
        if results:
            self._results = list(results)
        elif result is not None:
            self._results = [result]
        else:
            self._results = []
        self.error = error
        self.entered = entered
        self.release = release
        #: 1-based index of the ``convert`` call to hold until ``release`` is
        #: set (``0`` blocks nothing). Keeps earlier conversions in a test
        #: flowing normally while a later one intentionally stalls.
        self.block_on = block_on
        self.analyze_calls: list[Path] = []
        self.validate_calls: list[ConversionRequest] = []
        self.convert_calls: list[ConversionRequest] = []

    def analyze_pdf(self, path) -> PDFAnalysis:
        self.analyze_calls.append(Path(path))
        return self.analysis

    def validate_request(self, request: ConversionRequest) -> None:
        self.validate_calls.append(request)

    def convert(self, request: ConversionRequest, *, progress=None) -> ConversionResult:
        self.convert_calls.append(request)
        if self.entered is not None:
            self.entered.set()
        if len(self.convert_calls) == self.block_on and self.release is not None:
            assert self.release.wait(10), "test conversion did not finish in time"
        if self.error is not None:
            raise self.error
        if self._results:
            base = self._results[min(len(self.convert_calls) - 1, len(self._results) - 1)]
        else:
            base = _epub_only_result(request, self.analysis)
        return replace(base, request=request)


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


def _select_format(window: MainWindow, index: int) -> None:
    """Select an output format: index 0 -> EPUB, index 1 -> EPUB + AZW3."""
    combo = window.findChild(QtWidgets.QComboBox, "outputFormatCombo")
    assert combo is not None
    combo.setCurrentIndex(index)


def _convert_to_completion(window: MainWindow) -> None:
    window.convert_selected()
    _wait_for(lambda: window.state is UiState.COMPLETED)
    _settle(window)


# --------------------------------------------------------------------------
# Results visibility
# --------------------------------------------------------------------------


class TestResultsVisibility:
    def test_results_not_presented_before_successful_conversion(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            _make_ready(window, tmp_path)
            assert window.state is UiState.READY
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_completion_makes_results_visible(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.state is UiState.COMPLETED
            assert window.last_result is not None
            assert _results_section(window).isVisible()
        finally:
            window.close()

    def test_conversion_failure_does_not_display_a_successful_result(
        self, qapp, tmp_path: Path
    ) -> None:
        error = ConversionFailedError(
            "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
        )
        app = _FakeApplication(error=error)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_window_usable_after_completion(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _button(window, "browseButton").isEnabled()
            assert _button(window, "analyzeButton").isEnabled()
            assert _button(window, "outputDirectoryButton").isEnabled()
            assert _button(window, "convertButton").isEnabled()
        finally:
            window.close()


# --------------------------------------------------------------------------
# Results presentation
# --------------------------------------------------------------------------


class TestResultsPresentation:
    def test_successful_epub_result_displayed(self, qapp, tmp_path: Path) -> None:
        result = _result(
            None,
            _text_analysis(),
            epub_path="C:/Books/MyBook.epub",
            validation=_validation(warnings=("styling note",)),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "resultStatusValue").text() == "Conversion complete"
            assert _label(window, "resultFormatValue").text() == "EPUB"
            assert Path(_label(window, "epubOutputPath").text()) == Path("C:/Books/MyBook.epub")
            # AZW3 was not produced: its row and button are hidden.
            assert not _label(window, "azw3OutputPath").isVisible()
            assert not _button(window, "openAzw3Button").isVisible()
        finally:
            window.close()

    def test_epub_plus_azw3_result_displayed(self, qapp, tmp_path: Path) -> None:
        result = _result(
            None,
            _text_analysis(),
            epub_path="C:/Books/MyBook.epub",
            azw3_path="C:/Books/MyBook.azw3",
            validation=_validation(),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _select_format(window, 1)  # EPUB + AZW3
            _convert_to_completion(window)
            assert _label(window, "resultFormatValue").text() == "EPUB + AZW3"
            assert Path(_label(window, "epubOutputPath").text()) == Path("C:/Books/MyBook.epub")
            assert _label(window, "azw3OutputPath").isVisible()
            assert Path(_label(window, "azw3OutputPath").text()) == Path("C:/Books/MyBook.azw3")
            assert _button(window, "openAzw3Button").isVisible()
        finally:
            window.close()

    def test_output_paths_come_from_the_real_conversion_result(
        self, qapp, tmp_path: Path
    ) -> None:
        epub = tmp_path / "out" / "book.epub"
        azw3 = tmp_path / "out" / "book.azw3"
        result = _result(
            None,
            _text_analysis(),
            epub_path=str(epub),
            azw3_path=str(azw3),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _select_format(window, 1)  # EPUB + AZW3
            _convert_to_completion(window)
            # The displayed paths equal the paths on the retained real result,
            # and that object is the exact one the application produced for the
            # request that reached it.
            assert window.last_result is not None
            assert _label(window, "epubOutputPath").text() == str(window.last_result.epub_path)
            assert _label(window, "azw3OutputPath").text() == str(window.last_result.azw3_path)
            assert window.last_result.epub_path == epub
            assert window.last_result.azw3_path == azw3
            assert window.last_result.request is app.convert_calls[0]
        finally:
            window.close()

    def test_subsequent_conversion_replaces_the_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        first = _result(None, _text_analysis(), epub_path="C:/Books/One.epub")
        second = _result(
            None,
            _text_analysis(),
            epub_path="C:/Books/Two.epub",
            azw3_path="C:/Books/Two.azw3",
        )
        app = _FakeApplication(results=(first, second))
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert Path(_label(window, "epubOutputPath").text()) == Path("C:/Books/One.epub")
            first_result = window.last_result

            # A second conversion replaces (not duplicates) the presentation.
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result is not first_result
            assert Path(_label(window, "epubOutputPath").text()) == Path("C:/Books/Two.epub")
            assert _label(window, "azw3OutputPath").isVisible()
            assert Path(_label(window, "azw3OutputPath").text()) == Path("C:/Books/Two.azw3")
        finally:
            window.close()

    def test_completion_status_and_validation_status_are_distinct(
        self, qapp, tmp_path: Path
    ) -> None:
        result = _result(None, _text_analysis(), validation=_validation())
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "resultStatusValue").text() == "Conversion complete"
            assert _label(window, "validationStatusValue").text() == "Valid"
        finally:
            window.close()


# --------------------------------------------------------------------------
# Validation presentation
# --------------------------------------------------------------------------


class TestValidationPresentation:
    def test_valid_validation_result_displayed_as_valid(
        self, qapp, tmp_path: Path
    ) -> None:
        result = _result(None, _text_analysis(), validation=_validation())
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "validationStatusValue").text() == "Valid"
            assert _label(window, "validationWarningsValue").text() == "0"
            assert _label(window, "validationErrorsValue").text() == "0"
            assert not _label(window, "validationWarningsDetails").isVisible()
            assert not _label(window, "validationErrorsDetails").isVisible()
        finally:
            window.close()

    def test_warnings_are_displayed_when_available(self, qapp, tmp_path: Path) -> None:
        validation = _validation(warnings=("mimetype is not first", "nav order hint"))
        result = _result(None, _text_analysis(), validation=validation)
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _label(window, "validationStatusValue").text() == "Valid"
            assert _label(window, "validationWarningsValue").text() == "2"
            assert _label(window, "validationErrorsValue").text() == "0"
            details = _label(window, "validationWarningsDetails")
            assert details.isVisible()
            assert details.text() == "\u2022 mimetype is not first\n\u2022 nav order hint"
            assert not _label(window, "validationErrorsDetails").isVisible()
        finally:
            window.close()

    def test_invalid_validation_result_is_represented(self, qapp, tmp_path: Path) -> None:
        validation = _validation(errors=("dc:title is missing",))
        result = _result(None, _text_analysis(), validation=validation)
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert validation.valid is False
            assert _label(window, "validationStatusValue").text() == "Invalid"
            assert _label(window, "validationWarningsValue").text() == "0"
            assert _label(window, "validationErrorsValue").text() == "1"
            details = _label(window, "validationErrorsDetails")
            assert details.isVisible()
            assert details.text() == "\u2022 dc:title is missing"
            assert not _label(window, "validationWarningsDetails").isVisible()
        finally:
            window.close()

    def test_validation_disabled_result_shows_not_run(self, qapp, tmp_path: Path) -> None:
        result = _result(None, _text_analysis(), validation=None)
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.valid is None
            assert _label(window, "validationStatusValue").text() == "Not run"
            assert not _label(window, "validationWarningsDetails").isVisible()
            assert not _label(window, "validationErrorsDetails").isVisible()
        finally:
            window.close()

    def test_validation_information_comes_from_the_existing_result(
        self, qapp, tmp_path: Path
    ) -> None:
        validation = _validation(warnings=("w1", "w2", "w3"), errors=())
        result = _result(None, _text_analysis(), validation=validation)
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert window.last_result.validation is validation
            assert _label(window, "validationWarningsValue").text() == str(
                validation.warning_count
            )
            assert _label(window, "validationErrorsValue").text() == str(
                validation.error_count
            )
            assert (
                _label(window, "validationWarningsDetails").text()
                == "\n".join(f"\u2022 {issue.message}" for issue in validation.warnings)
            )
        finally:
            window.close()

    def test_validation_is_not_rerun_by_the_ui(self, qapp, tmp_path: Path) -> None:
        result = _result(None, _text_analysis(), validation=_validation(warnings=("x",)))
        app = _FakeApplication(result=result)
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _select_format(window, 1)
            assert app.validate_calls  # setup ran pre-flight validation

            window.convert_selected()
            # ``convert_selected`` validated synchronously before starting the
            # worker; snapshot that count so we can prove the presentation of
            # the finished result never validates again.
            validate_calls_when_conversion_ran = len(app.validate_calls)
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            # The background conversion ran exactly once, and rendering the
            # result (including its validation section) added no further
            # validation calls.
            assert len(app.convert_calls) == 1
            assert len(app.validate_calls) == validate_calls_when_conversion_ran
        finally:
            window.close()


class TestNoValidationImplementation:
    def test_main_window_source_never_invokes_validation(self) -> None:
        from kindle_converter.ui import main_window as main_window_module

        source = Path(main_window_module.__file__).read_text(encoding="utf-8")
        assert "validate_epub" not in source


# --------------------------------------------------------------------------
# Stale-result protection
# --------------------------------------------------------------------------


class TestStaleResults:
    def test_changing_input_clears_the_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert window.last_result is not None
            assert _results_section(window).isVisible()

            window.select_input(_make_pdf(tmp_path / "other.pdf"))
            assert window.state is UiState.INPUT_SELECTED
            assert window.last_result is None
            assert not _results_section(window).isVisible()
        finally:
            window.close()

    def test_changing_output_format_clears_the_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication())
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _results_section(window).isVisible()

            _select_format(window, 1)  # EPUB + AZW3
            assert window.last_result is None
            assert not _results_section(window).isVisible()
            assert window.state is UiState.READY
        finally:
            window.close()

    def test_changing_output_directory_clears_the_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication())
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

    def test_starting_a_new_conversion_hides_the_previous_result(
        self, qapp, tmp_path: Path
    ) -> None:
        entered = threading.Event()
        release = threading.Event()
        app = _FakeApplication(
            entered=entered, release=release, block_on=2
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert _results_section(window).isVisible()
            first_result = window.last_result

            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            # The old result must not be presented as the new run's result.
            assert window.last_result is None
            assert not _results_section(window).isVisible()
            assert first_result is not None
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_retry_after_failure_shows_the_new_result_only(
        self, qapp, tmp_path: Path
    ) -> None:
        first = _result(None, _text_analysis(), epub_path="C:/Books/First.epub")
        second = _result(None, _text_analysis(), epub_path="C:/Books/Second.epub")
        app = _FakeApplication(results=(first, second))
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            assert Path(_label(window, "epubOutputPath").text()) == Path("C:/Books/First.epub")

            # Fail the next conversion.
            app.error = ConversionFailedError("boom", stage=ConversionStage.ANALYSIS)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_result is None
            assert not _results_section(window).isVisible()

            # Retry succeeds and shows the fresh result, not the stale one.
            app.error = None
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert Path(_label(window, "epubOutputPath").text()) == Path("C:/Books/Second.epub")
            assert window.last_result is not None
            assert window.last_result.epub_path == Path("C:/Books/Second.epub")
        finally:
            window.close()


# --------------------------------------------------------------------------
# Output actions
# --------------------------------------------------------------------------


class TestOutputActions:
    def test_open_epub_sends_the_correct_epub_path(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = _result(None, _text_analysis(), epub_path=str(epub))
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openEpubButton").click()
            assert opener.calls == [epub]
        finally:
            window.close()

    def test_open_azw3_sends_the_correct_azw3_path(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        azw3 = tmp_path / "MyBook.azw3"
        epub.write_bytes(b"PK fake")
        azw3.write_bytes(b"fake azw3")
        result = _result(
            None,
            _text_analysis(),
            epub_path=str(epub),
            azw3_path=str(azw3),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _select_format(window, 1)  # EPUB + AZW3
            _convert_to_completion(window)
            _button(window, "openAzw3Button").click()
            assert opener.calls == [azw3]
        finally:
            window.close()

    def test_open_folder_sends_the_containing_directory(
        self, qapp, tmp_path: Path
    ) -> None:
        opener = _RecordingOpener()
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        epub = out_dir / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = _result(None, _text_analysis(), epub_path=str(epub))
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openFolderButton").click()
            assert opener.calls == [out_dir]
        finally:
            window.close()

    def test_missing_output_path_is_handled_safely(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener()
        result = _result(
            None,
            _text_analysis(),
            epub_path=str(tmp_path / "out" / "ghost.epub"),
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openEpubButton").click()
            assert opener.calls == []
            status = _label(window, "outputActionStatus")
            assert status.isVisible()
            assert "no longer available" in status.text()
            assert window.last_result is not None  # nothing was deleted/modified
        finally:
            window.close()

    def test_platform_open_failure_is_handled_safely(self, qapp, tmp_path: Path) -> None:
        opener = _RecordingOpener(
            error=PlatformOpenError("no system handler is available")
        )
        epub = tmp_path / "MyBook.epub"
        epub.write_bytes(b"PK fake")
        result = _result(None, _text_analysis(), epub_path=str(epub))
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _convert_to_completion(window)
            _button(window, "openEpubButton").click()
            assert opener.calls == [epub]
            status = _label(window, "outputActionStatus")
            assert status.isVisible()
            assert "Could not open" in status.text()
            assert window.state is UiState.COMPLETED  # window is still usable
        finally:
            window.close()

    def test_opening_a_file_does_not_modify_the_conversion_result(
        self, qapp, tmp_path: Path
    ) -> None:
        opener = _RecordingOpener()
        epub = tmp_path / "MyBook.epub"
        azw3 = tmp_path / "MyBook.azw3"
        epub.write_bytes(b"PK fake")
        azw3.write_bytes(b"fake azw3")
        validation = _validation(warnings=("w",))
        result = _result(
            None,
            _text_analysis(),
            epub_path=str(epub),
            azw3_path=str(azw3),
            validation=validation,
        )
        app = _FakeApplication(result=result)
        window = MainWindow(application=app, opener=opener)
        try:
            _make_ready(window, tmp_path)
            _select_format(window, 1)  # EPUB + AZW3
            _convert_to_completion(window)
            kept = window.last_result
            assert kept is not None
            _button(window, "openEpubButton").click()
            _button(window, "openAzw3Button").click()
            _button(window, "openFolderButton").click()
            assert window.last_result is kept
            assert kept.epub_path == epub
            assert kept.azw3_path == azw3
            assert kept.validation is validation
            assert epub.exists() and azw3.exists()
        finally:
            window.close()