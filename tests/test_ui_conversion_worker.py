"""Headless tests for the M5.5 background conversion worker.

The worker is the thin Qt threading boundary between the desktop UI and the
M5.1 application API: it receives a :class:`ConversionRequest` plus the
existing :class:`ConversionApplication`, executes
``ConversionApplication.convert(request, progress=...)``, and forwards the
application's progress / result / error over Qt signals. These tests run the
worker *synchronously* (direct ``run()`` calls, no real ``QThread``) against
deterministic application doubles, so there are no timing races and no real
PDF / OCR / Tesseract / Calibre machinery is involved.

The companion boundary check in ``test_ui_boundary.py`` proves the worker
stays QtCore-only (no widget manipulation).

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

from kindle_converter.application import (  # noqa: E402
    ApplicationError,
    ConversionApplication,
    ConversionFailedError,
    ConversionProgress,
    ConversionRequest,
    ConversionResult,
    ConversionStage,
    OutputFormat,
)
from kindle_converter.pdf import PDFAnalysis, PDFType, PageAnalysis  # noqa: E402
from kindle_converter.ui import ConversionWorker  # noqa: E402


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def qapp() -> QtWidgets.QApplication:
    application = QtWidgets.QApplication.instance()
    if application is None:
        application = QtWidgets.QApplication([])
    yield application


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


def _page(number: int) -> PageAnalysis:
    return PageAnalysis(
        page_number=number,
        has_image=False,
        has_meaningful_text=True,
        char_count=40,
        classification=PDFType.TEXT,
    )


def _request() -> ConversionRequest:
    return ConversionRequest(
        input_pdf="book.pdf",
        output_directory="out",
        formats=frozenset({OutputFormat.EPUB}),
    )


class _FakeApplication:
    """A deterministic stand-in for ``ConversionApplication.convert``.

    Records every call (so tests can assert the exact request that reached the
    application), invokes the progress callback with canned
    :class:`ConversionProgress` events, and either returns a canned
    ``ConversionResult`` or raises a canned error.
    """

    def __init__(
        self,
        *,
        result: ConversionResult | None = None,
        error: Exception | None = None,
        events: tuple[ConversionProgress, ...] = (),
    ) -> None:
        self.result = result
        self.error = error
        self.events = events
        self.convert_calls: list[ConversionRequest] = []
        self.progress_callbacks: list = []

    def convert(self, request: ConversionRequest, *, progress=None) -> ConversionResult:
        self.convert_calls.append(request)
        self.progress_callbacks.append(progress)
        if progress is not None:
            for event in self.events:
                progress(event)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _success_result(request: ConversionRequest) -> ConversionResult:
    return ConversionResult(
        request=request,
        analysis=_text_analysis(),
        epub_path=Path("out/book.epub"),
    )


def _capture(signal):
    """Connect ``signal`` to a plain slot and return the received values."""
    received: list = []

    def _handler(value=None) -> None:
        received.append(value)

    signal.connect(_handler)
    return received


def _analyse_events() -> tuple[ConversionProgress, ...]:
    return (
        ConversionProgress(ConversionStage.ANALYSIS, "Analyzing the PDF", 0, 0),
        ConversionProgress(
            ConversionStage.EPUB,
            "Converting the PDF to an EPUB (extraction, OCR, reconstruction)",
            0,
            2,
        ),
        ConversionProgress(ConversionStage.EPUB, "Wrote EPUB to book.epub", 2, 2),
    )


# --------------------------------------------------------------------------
# Delegation to the application layer
# --------------------------------------------------------------------------


class TestDelegation:
    def test_worker_calls_application_convert(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(result=_success_result(request))
        worker = ConversionWorker(app, request)
        worker.run()
        assert app.convert_calls == [request]

    def test_exact_request_reaches_the_application(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(result=_success_result(request))
        worker = ConversionWorker(app, request)
        worker.run()
        assert app.convert_calls[0] is request

    def test_convert_receives_the_progress_callback(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(result=_success_result(request))
        worker = ConversionWorker(app, request)
        worker.run()
        assert len(app.progress_callbacks) == 1
        assert callable(app.progress_callbacks[0])


# --------------------------------------------------------------------------
# Progress signals
# --------------------------------------------------------------------------


class TestProgressSignals:
    def test_callback_produces_progress_signals(self, qapp) -> None:
        request = _request()
        events = _analyse_events()
        app = _FakeApplication(result=_success_result(request), events=events)
        worker = ConversionWorker(app, request)
        received = _capture(worker.progress)
        worker.run()
        assert received == list(events)
        assert all(isinstance(event, ConversionProgress) for event in received)

    def test_no_progress_events_when_none_emitted(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(result=_success_result(request))
        worker = ConversionWorker(app, request)
        received = _capture(worker.progress)
        worker.run()
        assert received == []


# --------------------------------------------------------------------------
# Success / failure signals
# --------------------------------------------------------------------------


class TestOutcomeSignals:
    def test_success_emits_the_real_conversion_result(self, qapp) -> None:
        request = _request()
        result = _success_result(request)
        app = _FakeApplication(result=result)
        worker = ConversionWorker(app, request)
        succeeded = _capture(worker.succeeded)
        failed = _capture(worker.failed)
        worker.run()
        assert succeeded == [result]
        assert failed == []
        assert isinstance(succeeded[0], ConversionResult)

    def test_application_error_is_communicated_as_failure(self, qapp) -> None:
        request = _request()
        error = ConversionFailedError(
            "PDF to EPUB conversion failed: boom", stage=ConversionStage.EPUB
        )
        app = _FakeApplication(error=error)
        worker = ConversionWorker(app, request)
        succeeded = _capture(worker.succeeded)
        failed = _capture(worker.failed)
        worker.run()
        assert failed == [error]
        assert succeeded == []

    def test_unexpected_error_is_communicated_as_failure(self, qapp) -> None:
        request = _request()
        error = RuntimeError("programmer error")
        app = _FakeApplication(error=error)
        worker = ConversionWorker(app, request)
        succeeded = _capture(worker.succeeded)
        failed = _capture(worker.failed)
        worker.run()
        assert failed == [error]
        assert succeeded == []

    def test_finished_emitted_after_success(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(result=_success_result(request))
        worker = ConversionWorker(app, request)
        finished = _capture(worker.finished)
        worker.run()
        assert finished == [None]

    def test_finished_emitted_after_failure(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(error=ApplicationError("boom"))
        worker = ConversionWorker(app, request)
        succeeded = _capture(worker.succeeded)
        failed = _capture(worker.failed)
        finished = _capture(worker.finished)
        worker.run()
        assert failed and finished == [None]
        assert succeeded == []

    def test_results_are_not_emitted_together(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(error=ConversionFailedError("no", stage=ConversionStage.ANALYSIS))
        worker = ConversionWorker(app, request)
        succeeded = _capture(worker.succeeded)
        failed = _capture(worker.failed)
        worker.run()
        assert succeeded == []
        assert len(failed) == 1
        assert not (succeeded and failed)


# --------------------------------------------------------------------------
# No widget dependence (behavioral)
# --------------------------------------------------------------------------


class TestNoWidgets:
    def test_worker_run_creates_no_widgets(self, qapp) -> None:
        request = _request()
        app = _FakeApplication(result=_success_result(request))
        worker = ConversionWorker(app, request)
        before = len(QtWidgets.QApplication.topLevelWidgets())
        worker.run()
        after = len(QtWidgets.QApplication.topLevelWidgets())
        assert before == after