"""Headless tests for the M5.5 desktop conversion flow and progress UI.

The window under test drives the M5.1 application boundary through an injected
deterministic double (``analyze_pdf`` / ``validate_request`` / ``convert``),
so no real PDF / OCR / EPUB machinery is involved. The conversion itself runs
on a real background ``QThread`` (as in production), so these tests exercise
the actual thread lifecycle, signal delivery, widget locking, and result /
error handling -- while staying deterministic: conversions are synchronized
with ``threading.Event`` gates and the GUI event loop is spun until the
window reaches the expected state. Every test closes its window, which waits
for any in-flight conversion to finish (the M5.5 safe-shutdown contract).

All Qt dependencies run headless via ``QT_QPA_PLATFORM=offscreen``; the module
skips cleanly when PySide6 is not installed (the ``ui`` extra is absent).
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

import pymupdf  # noqa: E402

from PySide6 import QtCore, QtWidgets  # noqa: E402  (after importorskip)

from kindle_converter.application import (  # noqa: E402
    ConversionFailedError,
    ConversionProgress,
    ConversionRequest,
    ConversionResult,
    ConversionStage,
    OutputFormat,
)
from kindle_converter.pdf import PDFAnalysis, PDFType, PageAnalysis  # noqa: E402
from kindle_converter.ui import MainWindow, UiState  # noqa: E402


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
    """Wait until the window fully released its finished conversion thread.

    A conversion's terminal events are delivered back to the GUI thread (state
    flip, thread ``finished``, deferred ``deleteLater`` calls) only once the
    event loop keeps running. Waiting for ``window._thread is None`` confirms
    the teardown slot ran; then every pending ``DeferredDelete`` (the thread's
    and worker's ``deleteLater``) is force-dispatched *while the window is
    still alive*. Otherwise a queued delete of the thread's C++ object could
    outlive the window, and the next test's ``processEvents`` would dispatch it
    against an already-freed object (a hard abort on PySide6).
    """
    _wait_for(lambda: window._thread is None)
    _flush_deferred_deletes()


def _flush_deferred_deletes() -> None:
    """Dispatch pending ``DeferredDelete`` events immediately.

    ``processEvents`` only dispatches deferred deletes whose object is still
    referenced; ``sendPostedEvents`` forces *all* queued ones. Both passes
    ensure a window can be garbage-collected with no delete scheduled for one
    of its children still outstanding.
    """
    app = QtCore.QCoreApplication.instance()
    if app is None:
        return
    for _ in range(2):
        app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        app.processEvents()


def _text_analysis() -> PDFAnalysis:
    pages = [
        PageAnalysis(
            page_number=1,
            has_image=False,
            has_meaningful_text=True,
            char_count=40,
            classification=PDFType.TEXT,
        ),
        PageAnalysis(
            page_number=2,
            has_image=False,
            has_meaningful_text=True,
            char_count=40,
            classification=PDFType.TEXT,
        ),
    ]
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


class _FakeApplication:
    """Deterministic application double driving ``analyze_pdf``/``validate_request``/``convert``.

    ``convert`` records each request, optionally emits canned progress events,
    and either returns a real :class:`ConversionResult` (built from the actual
    request it received) or raises a canned error. An optional ``entered`` /
    ``release`` ``threading.Event`` pair lets tests freeze the worker inside
    ``convert`` and observe the window while a conversion is genuinely running.
    """

    def __init__(
        self,
        *,
        analysis: PDFAnalysis | None = None,
        error: Exception | None = None,
        progress_events: tuple[ConversionProgress, ...] = (),
        entered: threading.Event | None = None,
        release: threading.Event | None = None,
        emit_progress_before_block: bool = True,
    ) -> None:
        self.analysis = analysis
        self.error = error
        self.progress_events = progress_events
        self.entered = entered
        self.release = release
        self.emit_progress_before_block = emit_progress_before_block
        self.convert_calls: list[ConversionRequest] = []
        self.validate_calls: list[ConversionRequest] = []

    def analyze_pdf(self, path) -> PDFAnalysis:
        assert self.analysis is not None
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
        if self.release is not None:
            assert self.release.wait(10), "test conversion did not finish in time"
        if self.error is not None:
            raise self.error
        return ConversionResult(
            request=request,
            analysis=self.analysis,
            epub_path=Path("out/book.epub"),
        )


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


def _browse_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "browseButton")
    assert button is not None, "missing browseButton"
    return button


def _analyze_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "analyzeButton")
    assert button is not None, "missing analyzeButton"
    return button


def _combo(window: MainWindow) -> QtWidgets.QComboBox:
    combo = window.findChild(QtWidgets.QComboBox, "outputFormatCombo")
    assert combo is not None, "missing outputFormatCombo"
    return combo


def _out_dir_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "outputDirectoryButton")
    assert button is not None, "missing outputDirectoryButton"
    return button


def _cover_browse_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "coverBrowseButton")
    assert button is not None, "missing coverBrowseButton"
    return button


def _cover_clear_button(window: MainWindow) -> QtWidgets.QPushButton:
    button = window.findChild(QtWidgets.QPushButton, "coverClearButton")
    assert button is not None, "missing coverClearButton"
    return button


def _make_ready(window: MainWindow, tmp_path: Path, review_analysis: bool = True) -> Path:
    """Drive the window into ``UiState.READY`` and return the input path."""
    pdf = _make_pdf(tmp_path / "book.pdf")
    window.select_input(pdf)
    window.analyze_selected()
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    window.select_output_directory(out_dir)
    if review_analysis:
        assert window.state is UiState.READY
        assert window.is_configuration_ready
    return pdf


# --------------------------------------------------------------------------
# Convert availability
# --------------------------------------------------------------------------


class TestConvertAvailability:
    def test_convert_button_exists(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            button = _convert_button(window)
            assert "Convert" in button.text()
            assert _progress_bar(window) is not None
        finally:
            window.close()

    def test_convert_is_unavailable_without_configuration(self, qapp) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            assert not _convert_button(window).isEnabled()
            assert window.state is UiState.NO_INPUT
        finally:
            window.close()

    def test_convert_unavailable_after_analysis_without_output_directory(
        self, qapp, tmp_path: Path
    ) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert window.state is UiState.ANALYSIS_COMPLETE
            assert not _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_convert_becomes_available_when_ready(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(
            application=_FakeApplication(analysis=_text_analysis())
        )
        try:
            _make_ready(window, tmp_path)
            assert window.state is UiState.READY
            assert _convert_button(window).isEnabled()
        finally:
            window.close()

    def test_convert_disabled_again_when_input_changes(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(
            application=_FakeApplication(analysis=_text_analysis())
        )
        try:
            _make_ready(window, tmp_path)
            assert _convert_button(window).isEnabled()
            window.select_input(_make_pdf(tmp_path / "other.pdf"))
            assert window.state is UiState.INPUT_SELECTED
            assert not _convert_button(window).isEnabled()
        finally:
            window.close()


# --------------------------------------------------------------------------
# Starting a conversion
# --------------------------------------------------------------------------


class TestStartConversion:
    def test_clicking_convert_starts_the_worker(self, qapp, tmp_path: Path) -> None:
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
            assert len(app.convert_calls) == 1
            assert window.state is UiState.CONVERTING
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_request_is_validated_through_the_application(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            _convert_button(window).click()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert app.validate_calls  # pre-flight validation ran
            assert len(app.convert_calls) == 1
            request = app.convert_calls[0]
            assert isinstance(request, ConversionRequest)
            assert request.formats == frozenset({OutputFormat.EPUB})
            assert Path(request.input_pdf) == _make_pdf(tmp_path / "book.pdf").parent / "book.pdf"
        finally:
            window.close()

    def test_ui_enters_converting_state(self, qapp, tmp_path: Path) -> None:
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
            assert _status(window).text() == "Converting..."
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_convert_is_a_noop_when_not_ready(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            pdf = _make_pdf(tmp_path / "book.pdf")
            window.select_input(pdf)
            window.analyze_selected()
            assert not _convert_button(window).isEnabled()
            window.convert_selected()
            assert app.convert_calls == []
            assert window.state is UiState.ANALYSIS_COMPLETE
        finally:
            window.close()


# --------------------------------------------------------------------------
# Controls locked during conversion
# --------------------------------------------------------------------------


class TestControlsLocked:
    def _assert_locked(self, window: MainWindow) -> None:
        assert not _browse_button(window).isEnabled()
        assert not _analyze_button(window).isEnabled()
        assert not _combo(window).isEnabled()
        assert not _out_dir_button(window).isEnabled()
        assert not _cover_browse_button(window).isEnabled()
        assert not _cover_clear_button(window).isEnabled()
        assert not _convert_button(window).isEnabled()

    def test_all_conflicting_controls_disabled_while_converting(
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
            self._assert_locked(window)
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_configuration_mutations_are_ignored_while_converting(
        self, qapp, tmp_path: Path, monkeypatch
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

            other = _make_pdf(tmp_path / "other.pdf")
            window.select_input(other)
            window.select_output_directory(None)
            window.select_cover(tmp_path / "cover.png")
            window.clear_cover()
            _combo(window).setCurrentIndex(1)
            window.analyze_selected()

            # None of the mutations took effect on the running request.
            assert window.selected_path != other
            assert window.output_directory is not None
            assert window.state is UiState.CONVERTING
            assert len(app.convert_calls) == 1
            request = app.convert_calls[0]
            assert Path(request.input_pdf) == _make_pdf(tmp_path / "book.pdf").parent / "book.pdf"
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_second_conversion_cannot_start_while_running(
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
            _convert_button(window).click()
            window.convert_selected()
            assert len(app.convert_calls) == 1
            assert window.state is UiState.CONVERTING
        finally:
            release.set()
            _settle(window)
            window.close()


# --------------------------------------------------------------------------
# Progress presentation
# --------------------------------------------------------------------------


class TestProgressPresentation:
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
            emit_progress_before_block=True,
        )
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            assert entered.wait(10)
            assert window.state is UiState.CONVERTING
            # The application-reported stage message is shown as the status.
            _wait_for(lambda: _status(window).text() == the_message.message)
            assert _progress_bar(window).maximum() == 0  # indeterminate
        finally:
            release.set()
            _settle(window)
            window.close()

    def test_success_shows_completion_status_and_full_bar(
        self, qapp, tmp_path: Path
    ) -> None:
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


# --------------------------------------------------------------------------
# Success handling
# --------------------------------------------------------------------------


class TestSuccessHandling:
    def test_successful_completion_returns_the_ui_to_a_usable_state(
        self, qapp, tmp_path: Path
    ) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert _browse_button(window).isEnabled()
            assert _combo(window).isEnabled()
            assert _convert_button(window).isEnabled()  # can convert again
        finally:
            window.close()

    def test_real_conversion_result_is_retained(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(analysis=_text_analysis())
        window = MainWindow(application=app)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert isinstance(window.last_result, ConversionResult)
            # The retained result is the real application result produced from
            # the exact request that reached the application.
            assert window.last_result.request is app.convert_calls[0]
            assert window.last_result.request is not None
            assert window.last_result.epub_path == Path("out/book.epub")
            assert window.last_error is None
        finally:
            window.close()

    def test_no_result_before_any_conversion(self, qapp, tmp_path: Path) -> None:
        window = MainWindow(application=_FakeApplication(analysis=_text_analysis()))
        try:
            _make_ready(window, tmp_path)
            assert window.last_result is None
            assert window.last_error is None
        finally:
            window.close()


# --------------------------------------------------------------------------
# Error handling
# --------------------------------------------------------------------------


class TestFailureHandling:
    def test_application_error_is_shown_without_crashing(self, qapp, tmp_path: Path) -> None:
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
            assert window.last_error is error
            assert window.last_result is None
        finally:
            window.close()

    def test_controls_are_restored_after_failure(self, qapp, tmp_path: Path) -> None:
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
            assert _convert_button(window).isEnabled()  # retry is possible
            assert _progress_bar(window).value() == 0
        finally:
            window.close()

    def test_retry_after_failure_succeeds(self, qapp, tmp_path: Path) -> None:
        app = _FakeApplication(
            analysis=_text_analysis(),
            error=ConversionFailedError("boom", stage=ConversionStage.ANALYSIS),
        )

        class _Flaky(_FakeApplication):
            def convert(self, request, *, progress=None):
                self.convert_calls.append(request)
                if len(self.convert_calls) == 1:
                    raise ConversionFailedError(
                        "boom", stage=ConversionStage.ANALYSIS
                    )
                return ConversionResult(
                    request=request,
                    analysis=self.analysis,
                    epub_path=Path("out/book.epub"),
                )

        flaky = _Flaky(analysis=_text_analysis())
        window = MainWindow(application=flaky)
        try:
            _make_ready(window, tmp_path)
            window.convert_selected()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _settle(window)
            assert window.last_error is not None

            window.convert_selected()  # retry
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _settle(window)
            assert len(flaky.convert_calls) == 2
            assert isinstance(window.last_result, ConversionResult)
            assert window.last_error is None
        finally:
            window.close()


# --------------------------------------------------------------------------
# Thread lifecycle
# --------------------------------------------------------------------------


class TestThreadLifecycle:
    def test_worker_and_thread_are_cleaned_up_after_success(
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
            assert second_thread is not first_thread  # fresh worker state, no reuse
            _wait_for(lambda: len(app.convert_calls) == 2)
            _wait_for(lambda: window.state is UiState.COMPLETED)
            _wait_for(lambda: window._thread is None)
            assert window._worker is None
            assert len(app.convert_calls) == 2
            _flush_deferred_deletes()
        finally:
            window.close()

    def test_worker_thread_is_cleaned_up_after_failure(
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
            thread = window._thread
            assert thread is not None and thread.isRunning()
            _wait_for(lambda: window.state is UiState.CONVERSION_FAILED)
            _wait_for(lambda: window._thread is None)
            assert window._worker is None
            _flush_deferred_deletes()
        finally:
            window.close()

    def test_closing_the_window_waits_for_a_running_conversion(
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
            # Close blocks until the conversion thread has finished safely, so
            # a running QThread is never destroyed.
            window.close()
            assert thread.isFinished()
        finally:
            release.set()
            # Drain the pending thread-teardown events that `close()` could not
            # process while it was blocking, so nothing leaks into later tests.
            _flush_deferred_deletes()


# --------------------------------------------------------------------------
# GUI responsiveness
# --------------------------------------------------------------------------


class TestResponsiveness:
    def test_gui_event_loop_stays_responsive_while_converting(
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
            # While the conversion is blocked in the worker thread, the GUI
            # event loop can still be spun: timers/queued events keep flowing.
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