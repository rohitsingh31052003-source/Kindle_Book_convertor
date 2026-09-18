"""Application / pipeline API (M5.1).

This package is the stable, UI-independent boundary between a future user
interface and the existing M1--M4 conversion pipeline. The future PySide6 UI
(or a CLI, a test, or any Python caller) drives a conversion through a single
orchestrator -- :class:`ConversionApplication` -- and inspects the structured
:class:`ConversionResult`. Neither the UI nor any caller of this package needs
to know how PDF analysis, OCR, reconstruction, EPUB generation, validation, or
AZW3 conversion work internally; those stay in their existing modules.

Typical usage::

    from kindle_converter.application import (
        ConversionApplication,
        ConversionRequest,
        OutputFormat,
    )

    request = ConversionRequest(
        input_pdf="book.pdf",
        output_directory="out",
        formats={OutputFormat.EPUB, OutputFormat.AZW3},
    )
    result = ConversionApplication().convert(
        request, progress=lambda p: print(p.stage, p.message)
    )
    print(result.epub_path, result.azw3_path)

Since M5.3 the application also exposes the analysis-only half of the use
case, so a UI can inspect a PDF before offering a conversion::

    analysis = ConversionApplication().analyze_pdf("book.pdf")
    print(analysis.page_count, analysis.document_type)

Since M5.4 the application also exposes :meth:`ConversionApplication.validate_request`,
which runs the same pre-flight path checks ``convert`` performs (input PDF,
output directory, cover, explicit Calibre path) without analyzing, writing,
or executing any conversion -- so a UI can decide whether its current
configuration is ready without risking a conversion::

    request = ConversionRequest(
        input_pdf="book.pdf",
        output_directory="out",
        formats={OutputFormat.EPUB},
    )
    ConversionApplication().validate_request(request)

Architecture
------------
The application layer owns the **use case**, not the document-processing
algorithms. ``ConversionApplication.convert`` composes the existing public
stage functions and adds the cross-cutting concerns the pipeline does not own:

* **request validation** (structural, in
  :class:`ConversionRequest`; environmental, at the start of ``convert``);
* **orchestration** (analyze -> convert_pdf_to_epub -> validate -> azw3);
* **output-path contract** (deterministic ``<output_dir>/<input-stem>.<ext>``
  paths exposed on :class:`ConversionResult`);
* **progress reporting** (optional, UI-independent :class:`ConversionProgress`
  events);
* **result/error boundary** (a structured result on success; otherwise one of
  the :class:`ApplicationError` subclasses, each chained to its underlying
  cause via ``__cause__``).

It never reimplements PDF parsing, layout, reading order, OCR, reconstruction,
EPUB serialization, EPUB validation rules, AZW3 conversion internals, or cover
image processing -- those are delegated to their existing public APIs. Because
M5.1 has no temporary-directory strategy yet, the EPUB artifact is always
written into the requested output directory (it is also the AZW3 input when
AZW3 is requested). Since M5.7 each conversion runs inside an
application-owned temporary workspace that is created on entry and
deterministically cleaned up on exit; final artifacts are always written
to the user's selected output directory, never the workspace.

Threading
---------
M5.1 is synchronous and defines no threading. The API is free of Qt/event-loop
dependencies and may be called from a worker thread by the future PySide6 UI.
"""

from __future__ import annotations

from .converter import ConversionApplication
from .errors import (
    AZW3OutputError,
    ApplicationError,
    ConversionFailedError,
    InvalidRequestError,
    OutputError,
    ValidationFailedError,
    WorkspaceError,
)
from .progress import ConversionProgress, ConversionStage, ProgressCallback
from .request import ConversionRequest, CoverSource, OutputFormat, PathLike
from .result import ConversionResult
from .workspace import ConversionWorkspace

__all__ = [
    "ApplicationError",
    "AZW3OutputError",
    "ConversionApplication",
    "ConversionFailedError",
    "ConversionProgress",
    "ConversionRequest",
    "ConversionStage",
    "ConversionWorkspace",
    "CoverSource",
    "InvalidRequestError",
    "OutputError",
    "OutputFormat",
    "PathLike",
    "ProgressCallback",
    "ValidationFailedError",
    "WorkspaceError",
]

# The two public top-level aliases the M1--M4 package already exports remain the
# primary entry points; the application layer is the M5.1 use-case boundary on
# top of them.
