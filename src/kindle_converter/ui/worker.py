"""Background conversion worker for the desktop UI (M5.5).

:class:`ConversionWorker` executes one :class:`ConversionRequest` through the
existing M5.1 application boundary -- :meth:`ConversionApplication.convert` --
away from the Qt GUI thread. It is a plain ``QObject`` that the main window
moves to a ``QThread``. It contains **no** conversion logic of its own (no
analysis, PDF processing, OCR, reconstruction, EPUB generation, validation,
AZW3 conversion, cover loading, or output-path calculation) and it never
touches widgets or GUI state: the application layer keeps owning every
conversion behavior and the worker only calls it and forwards events.

All communication with the GUI thread happens through four signals:

* ``progress`` -- one :class:`~kindle_converter.application.ConversionProgress`
  event for every application progress-callback invocation;
* ``succeeded`` -- the real
  :class:`~kindle_converter.application.ConversionResult` when conversion
  completed;
* ``failed`` -- the originating exception when conversion failed (an
  :class:`~kindle_converter.application.ApplicationError` for expected,
  user-facing failures; any other ``Exception`` otherwise);
* ``finished`` -- always emitted after ``succeeded``/``failed`` so the owning
  thread can shut down deterministically.

``run`` is connected to its ``QThread``'s ``started`` signal by the window and
executes on the worker thread. Unexpected (non-application) exceptions are
logged here (where the traceback is intact) and still surfaced to the GUI as
a ``failed`` event, so a conversion failure never crashes the GUI thread.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

from kindle_converter.application import (
    ApplicationError,
    ConversionApplication,
    ConversionProgress,
    ConversionRequest,
    ConversionResult,
)

logger = logging.getLogger(__name__)


class ConversionWorker(QObject):
    """A QObject that runs one conversion through the application layer.

    Constructor parameters reuse the window's existing injection seam for
    deterministic tests: ``application`` is the
    :class:`~kindle_converter.application.ConversionApplication` (or a test
    double exposing the same ``convert(request, *, progress=...)`` signature)
    that owns the conversion; ``request`` is the exact
    :class:`~kindle_converter.application.ConversionRequest` to execute.
    """

    #: One application progress event (a ``ConversionProgress`` value).
    progress = Signal(object)

    #: The real ``ConversionResult`` when conversion completed.
    succeeded = Signal(object)

    #: The originating exception when conversion failed.
    failed = Signal(object)

    #: Always emitted after ``succeeded``/``failed``.
    finished = Signal()

    def __init__(
        self, application: ConversionApplication, request: ConversionRequest
    ) -> None:
        super().__init__()
        self._application = application
        self._request = request

    def run(self) -> None:
        """Execute the conversion and emit the outcome signals.

        Runs on the worker thread (connected to the thread's ``started``
        signal by the window). Emits ``succeeded`` with the real
        :class:`~kindle_converter.application.ConversionResult` on success, or
        ``failed`` with the exception on failure, then ``finished``. Never
        touches widgets or GUI state.
        """
        try:
            result = self._application.convert(
                self._request, progress=self._on_progress
            )
        except Exception as exc:
            self._report_failure(exc)
        else:
            self.succeeded.emit(result)
        finally:
            self.finished.emit()

    def _on_progress(self, progress: ConversionProgress) -> None:
        """Forward one application progress event to the GUI thread."""
        self.progress.emit(progress)

    def _report_failure(self, exc: Exception) -> None:
        """Log unexpected failures and forward the exception to the GUI."""
        if not isinstance(exc, ApplicationError):
            logger.exception("Unexpected error during background conversion")
        self.failed.emit(exc)