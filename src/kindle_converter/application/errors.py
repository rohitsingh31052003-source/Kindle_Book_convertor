"""Application-layer error boundary (M5.1).

The application layer owns the *use case*, not a single stage, so its failures
fall into two groups:

* **Expected, user-facing failures** -- invalid input or configuration, a
  stage that could not run, a missing/unusable output artifact, an EPUB that
  failed structural validation, or the optional AZW3 conversion failing. Every
  one of these is a :class:`ApplicationError` (and therefore also a
  :class:`~kindle_converter.pipeline.PipelineError`, so existing callers that
  already scope pipeline failures keep working), so a future UI can catch *one*
  base type to react to anything the user can fix.
* **Unexpected programming errors** -- anything else. These are never turned
  into a generic application error: they propagate as-is so a developer sees
  the real bug.

Underlying diagnostic information is never discarded. Every expected failure
chains the originating stage error through ``__cause__`` (and, where it is
useful, a dedicated ``cause``/``epub_path``/``stage`` attribute), so the cause
remains inspectable even after wrapping.
"""

from __future__ import annotations

from pathlib import Path

from ..pipeline import PipelineError
from .progress import ConversionStage

__all__ = [
    "ApplicationError",
    "AZW3OutputError",
    "ConversionFailedError",
    "InvalidRequestError",
    "OutputError",
    "ValidationFailedError",
    "WorkspaceError",
]


class ApplicationError(PipelineError):
    """Base class for every conversion failure the application layer raises.

    Subclasses are expected, user-facing failures (invalid request, a stage or
    output that could not be produced, a failed validation, a missing optional
    AZW3). Catching this one type is enough to react to *any* conversion
    failure; the specific subclass (and the chained ``__cause__``) gives the
    detail. It subclasses :class:`~kindle_converter.pipeline.PipelineError`,
    the documented home for orchestration-level errors, so callers already
    scoping ``PipelineError`` are unaffected.
    """


class InvalidRequestError(ApplicationError):
    """The conversion request is invalid before any work starts.

    Raised when the request itself cannot drive a conversion: a missing or
    unreadable input PDF, a missing/non-directory output directory, no output
    format selected, an unsupported cover configuration, an AZW3-without-EPUB
    combination, or a non-existent Calibre executable given for AZW3. This is a
    request/configuration problem -- the right response is to ask the caller to
    correct their inputs, not to retry the conversion.
    """


class ConversionFailedError(ApplicationError):
    """A required conversion stage could not run.

    The failing :class:`ConversionStage` is available as ``stage``; the
    originating M1--M4 error (e.g. ``PDFReadError``, ``OCRError``,
    ``EPUBGenerationError``, ``NoContentError``) is chained as ``__cause__``
    rather than swallowed.
    """

    def __init__(self, message: str, *, stage: ConversionStage) -> None:
        super().__init__(message)
        self.stage = stage


class OutputError(ApplicationError):
    """An output artifact could not be written.

    The path that was being written is available as ``path``; the originating
    error (typically an ``OSError``) is chained as ``__cause__``.
    """

    def __init__(self, message: str, *, path: Path) -> None:
        super().__init__(message)
        self.path = path


class ValidationFailedError(ApplicationError):
    """EPUB validation was requested but did not pass cleanly.

    Two distinct causes:

    * the generated EPUB could not be inspected at all (for example it is not a
      readable file); then ``validation`` is ``None`` and the underlying
      ``EPUBValidationError`` is chained as ``__cause__``;
    * the validator ran and reported structural *errors*; then ``validation``
      holds the full :class:`~kindle_converter.epub.EPUBValidationResult`
      (findings, warnings, and the ``valid`` flag).

    In both cases the EPUB artifact was produced and remains on disk at
    ``epub_path``.
    """

    def __init__(
        self,
        message: str,
        *,
        epub_path: Path,
        validation=None,
    ) -> None:
        super().__init__(message)
        self.epub_path = epub_path
        self.validation = validation


class AZW3OutputError(ApplicationError):
    """The optional AZW3 conversion failed after the EPUB was produced.

    The EPUB output remains on disk; its path is available as ``epub_path``.
    The originating M4.3 error (a subclass of
    :class:`~kindle_converter.epub.AZW3ConversionError`) is both chained as
    ``__cause__`` and exposed as ``cause`` for callers that want to branch on
    Calibre being unavailable vs. a failing conversion.
    """

    cause: BaseException | None

    def __init__(self, message: str, *, epub_path: Path, cause: BaseException) -> None:
        super().__init__(message)
        self.epub_path = epub_path
        self.cause = cause


class WorkspaceError(ApplicationError):
    """The temporary workspace for a conversion could not be created.

    Raised before any conversion work starts; no artifacts are produced
    and no cleanup is needed. The originating ``OSError`` (if any) is
    chained as ``__cause__``.
    """
