"""Application-level conversion orchestration (M5.1, analysis added M5.3).

:class:`ConversionApplication` is the stable UI-independent boundary that
composes the existing M1--M4 stage functions into the user-facing use case:
analyze a PDF, produce an EPUB, validate it, and optionally convert it to
AZW3, while reporting progress. Since M5.3 it also exposes the analysis-only
half of that workflow, :meth:`ConversionApplication.analyze_pdf`, so a UI can
inspect a PDF - page count, document type, per-page classifications - before
any conversion starts. It holds no PDF, OCR, reconstruction, EPUB,
validation, cover, or AZW3 algorithm of its own -- those stay in their
existing modules. Stateless and safe to call from a worker thread (M5.1
defines no threading).

    ANALYSIS -> EPUB (= PDF -> Book -> EPUB) -> VALIDATION -> AZW3 -> COMPLETE

Failures raise :class:`ApplicationError` subclasses chained to their cause; a
returned :class:`ConversionResult` always means every requested artifact was
produced and (when validation was requested) validated successfully.
"""
from __future__ import annotations

import os
from pathlib import Path

from ..document import CoverError, Image, load_cover
from ..epub import (
    AZW3ConversionBackend,
    AZW3ConversionError,
    EPUBGenerationError,
    EPUBValidationError,
    EPUBValidationResult,
    convert_epub_to_azw3,
    validate_epub,
)
from ..pdf import (
    EmptyPDFError,
    NoContentError,
    OCREngine,
    OCRError,
    OCREngineUnavailableError,
    PDFAnalysis,
    PDFReadError,
    PDFRenderingError,
    PageRenderer,
    analyze_pdf,
)
from ..pipeline import convert_pdf_to_epub
from .errors import (
    AZW3OutputError,
    ConversionFailedError,
    InvalidRequestError,
    OutputError,
    ValidationFailedError,
)
from .progress import ConversionProgress, ConversionStage, ProgressCallback
from .request import ConversionRequest, OutputFormat, PathLike
from .result import ConversionResult

__all__ = ["ConversionApplication"]

# Stage errors raised by the unified PDF -> EPUB conversion meaning "a
# required stage could not run" (vs. an I/O write failure -> OutputError).
_PDF_TO_EPUB_ERRORS: tuple[type[Exception], ...] = (
    CoverError,
    PDFReadError,
    EmptyPDFError,
    NoContentError,
    OCRError,
    OCREngineUnavailableError,
    PDFRenderingError,
    EPUBGenerationError,
)


class _Progress:
    """Constructs progress events; no-ops when there is no callback."""
    __slots__ = ("_callback",)

    def __init__(self, callback: ProgressCallback | None) -> None:
        self._callback = callback

    def _emit(self, event: ConversionProgress) -> None:
        if self._callback is not None:
            self._callback(event)

    def start(self, stage, message="", *, current=0, total=0) -> None:
        self._emit(ConversionProgress(stage, message, current, total))

    def finish(self, stage, message="", *, current, total) -> None:
        self._emit(ConversionProgress(stage, message, current, total))

    def complete(self, message="Conversion complete") -> None:
        self._emit(ConversionProgress(ConversionStage.COMPLETE, message, 0, 0))


class ConversionApplication:
    """UI-independent application / pipeline API for converting a PDF.

    Constructor parameters are dependency-injection seams (injectable OCR
    engine, page renderer, AZW3 backend) for deterministic tests; they are not
    user-facing configuration.
    """
    __slots__ = ("_engine", "_renderer", "_azw3_backend")

    def __init__(self, *, engine=None, renderer=None, azw3_backend=None) -> None:
        self._engine = engine
        self._renderer = renderer
        self._azw3_backend = azw3_backend

    def analyze_pdf(self, input_pdf: PathLike) -> PDFAnalysis:
        """Analyze one PDF and return its analysis, without converting.

        This is the analysis-only half of :meth:`convert` (M5.3): it validates
        the input with the exact same path checks (and the same
        :class:`InvalidRequestError` boundary), runs the existing M3.1
        :func:`~kindle_converter.pdf.analyze_pdf` implementation, and returns
        its structured :class:`~kindle_converter.pdf.PDFAnalysis` unchanged.
        No EPUB/AZW3 stage runs, nothing is written, and the OCR/renderer
        injection seams are never involved. A UI can display
        ``analysis.page_count``, ``analysis.document_type``, and the
        per-page classifications before offering a conversion.

        Parameters
        ----------
        input_pdf:
            A filesystem path to a readable PDF file (``str`` or
            ``os.PathLike``).

        Returns
        -------
        PDFAnalysis
            The existing M3.1 analysis type: page count, text density,
            document type, and per-page ``PageAnalysis`` rows.

        Raises
        ------
        InvalidRequestError
            ``input_pdf`` is not a filesystem path, does not exist, is not a
            regular file, or cannot be read.
        ConversionFailedError
            The PDF could not be analyzed; the originating
            ``PDFReadError``/``EmptyPDFError``/``NoContentError`` is chained as
            ``__cause__`` and ``stage`` is ``ConversionStage.ANALYSIS``.
        """
        input_path = self._resolve_input_path(input_pdf)
        try:
            return analyze_pdf(input_path)
        except (PDFReadError, EmptyPDFError, NoContentError) as exc:
            raise ConversionFailedError(
                f"PDF analysis failed: {exc}", stage=ConversionStage.ANALYSIS
            ) from exc

    @staticmethod
    def _resolve_input_path(input_pdf: PathLike) -> Path:
        """Validate a bare input path the same way ``convert`` validates one.

        Shared by :meth:`convert` (via :meth:`_resolve_input`) and the
        analysis-only API :meth:`analyze_pdf`, so both boundary operations
        reject bad input identically (:class:`InvalidRequestError`).
        """
        try:
            path = Path(os.fspath(input_pdf))
        except TypeError as exc:
            raise InvalidRequestError(
                f"input_pdf must be a filesystem path, got {type(input_pdf).__name__}"
            ) from exc
        if not path.exists():
            raise InvalidRequestError(f"the input PDF {str(path)!r} does not exist")
        if not path.is_file():
            raise InvalidRequestError(f"the input PDF {str(path)!r} is not a regular file")
        try:
            path.open("rb").close()
        except OSError as exc:
            raise InvalidRequestError(f"the input PDF {str(path)!r} cannot be read: {exc}") from exc
        return path

    @staticmethod
    def _resolve_input(request: ConversionRequest) -> Path:
        return ConversionApplication._resolve_input_path(request.input_pdf)

    @staticmethod
    def _resolve_output_directory(request: ConversionRequest) -> Path:
        try:
            path = Path(os.fspath(request.output_directory))
        except TypeError as exc:
            raise InvalidRequestError(
                f"output_directory must be a filesystem path, got "
                f"{type(request.output_directory).__name__}"
            ) from exc
        if not path.exists():
            raise InvalidRequestError(f"the output directory {str(path)!r} does not exist")
        if not path.is_dir():
            raise InvalidRequestError(f"the output path {str(path)!r} is not a directory")
        return path

    @staticmethod
    def _resolve_cover(request: ConversionRequest) -> Image | None:
        if request.cover is None:
            return None
        try:  # M4.4 reuse: path validated once here; Image returned unchanged.
            return load_cover(request.cover)
        except CoverError as exc:
            raise InvalidRequestError(
                f"the cover configuration is not supported: {exc}"
            ) from exc

    @staticmethod
    def _resolve_azw3(request: ConversionRequest) -> None:
        # Pre-check an explicit Calibre path; Calibre discovery/launch is M4.3.
        if OutputFormat.AZW3 not in request.formats or request.calibre_path is None:
            return
        try:
            path = Path(os.fspath(request.calibre_path))
        except TypeError as exc:
            raise InvalidRequestError(
                f"calibre_path must be a filesystem path, got {type(request.calibre_path).__name__}"
            ) from exc
        if not path.is_file():
            raise InvalidRequestError(
                f"calibre_path {str(path)!r} is not an executable file (AZW3 requested)"
            )

    def convert(
        self, request: ConversionRequest, *, progress: ProgressCallback | None = None
        ) -> ConversionResult:
        """Run one conversion and return its structured result.

        Parameters
        ----------
        request:
            The :class:`ConversionRequest` describing the conversion.
        progress:
            Optional, UI-independent progress callback. Each stage that runs
            emits a start and a finish event (plus a terminal ``COMPLETE``),
            via :class:`ConversionProgress` values. Safe to omit; progress
            never alters conversion behavior.

        Returns
        -------
        ConversionResult
            ``epub_path`` always points at the produced (and, when requested,
            validated) EPUB; ``azw3_path`` is set when AZW3 was requested.

        Raises
        ------
        InvalidRequestError
            The request is structurally or environmentally invalid (bad paths,
            unsupported combination, unusable cover, non-existent Calibre path).
        ConversionFailedError
            A required stage (analysis, EPUB) could not run; the underlying
            error is chained as ``__cause__`` and the failing stage is on
            ``stage``.
        OutputError
            An output artifact could not be written; ``path`` names it.
        ValidationFailedError
            Validation was requested and the EPUB is unreadable or reports
            structural errors; the EPUB (and any ``validation`` result) survive
            on ``epub_path``.
        AZW3OutputError
            The optional AZW3 conversion failed; the EPUB is still produced
            (``epub_path``) and the M4.3 cause is chained as ``__cause__``.
        """
        input_path = self._resolve_input(request)
        output_dir = self._resolve_output_directory(request)
        cover_image = self._resolve_cover(request)
        self._resolve_azw3(request)
        progress_reporter = _Progress(progress)

        # --- ANALYSIS -----------------------------------------------------
        progress_reporter.start(ConversionStage.ANALYSIS, "Analyzing the PDF")
        try:
            analysis = analyze_pdf(input_path)
        except (PDFReadError, EmptyPDFError, NoContentError) as exc:
            raise ConversionFailedError(
                f"PDF analysis failed: {exc}", stage=ConversionStage.ANALYSIS
            ) from exc
        progress_reporter.finish(
            ConversionStage.ANALYSIS,
            f"Analyzed {analysis.page_count} page(s) as {analysis.document_type.value}",
            current=analysis.page_count,
            total=analysis.page_count,
        )
                # --- EPUB (PDF -> Book -> EPUB) -----------------------------------
        epub_path = output_dir / f"{input_path.stem}.epub"
        progress_reporter.start(
            ConversionStage.EPUB,
            "Converting the PDF to an EPUB (extraction, OCR, reconstruction)",
            current=0,
            total=analysis.page_count,
        )
        try:
            convert_pdf_to_epub(
                input_path,
                epub_path,
                engine=self._engine,
                renderer=self._renderer,
                cover=cover_image,
            )
        except OSError as exc:
            raise OutputError(
                f"could not write the EPUB to {epub_path}: {exc}", path=epub_path
            ) from exc
        except _PDF_TO_EPUB_ERRORS as exc:
            raise ConversionFailedError(
                f"PDF to EPUB conversion failed: {exc}", stage=ConversionStage.EPUB
            ) from exc
        progress_reporter.finish(
            ConversionStage.EPUB,
            f"Wrote EPUB to {epub_path.name}",
            current=analysis.page_count,
            total=analysis.page_count,
        )

        # --- VALIDATION (optional) ---------------------------------------
        validation: EPUBValidationResult | None = None
        warnings: tuple[str, ...] = ()
        if request.validate:
            progress_reporter.start(ConversionStage.VALIDATION, "Validating the EPUB")
            try:
                validation = validate_epub(epub_path)
            except EPUBValidationError as exc:
                raise ValidationFailedError(
                    f"the generated EPUB could not be inspected: {exc}",
                    epub_path=epub_path,
                    validation=None,
                ) from exc
            if not validation.valid:
                raise ValidationFailedError(
                    f"the generated EPUB failed structural validation "
                    f"({len(validation.errors)} error(s))",
                    epub_path=epub_path,
                    validation=validation,
                )
            warnings = tuple(issue.message for issue in validation.warnings)
            progress_reporter.finish(
                ConversionStage.VALIDATION,
                "EPUB validation passed"
                + (f" ({len(warnings)} warning(s))" if warnings else ""),
                current=1,
                total=1,
            )

        # --- AZW3 (optional) ---------------------------------------------
        azw3_path: Path | None = None
        if OutputFormat.AZW3 in request.formats:
            azw3_path = output_dir / f"{input_path.stem}.azw3"
            progress_reporter.start(
                ConversionStage.AZW3, "Converting the EPUB to AZW3 (Calibre)"
            )
            try:
                convert_epub_to_azw3(
                    epub_path,
                    azw3_path,
                    calibre_path=request.calibre_path,
                    backend=self._azw3_backend,
                )
            except AZW3ConversionError as exc:
                raise AZW3OutputError(
                    f"AZW3 conversion failed: {exc}",
                    epub_path=epub_path,
                    cause=exc,
                ) from exc
            progress_reporter.finish(
                ConversionStage.AZW3,
                f"Wrote AZW3 to {azw3_path.name}",
                current=1,
                total=1,
            )

        progress_reporter.complete()
        return ConversionResult(
            request=request,
            analysis=analysis,
            epub_path=epub_path,
            azw3_path=azw3_path,
            validation=validation,
            warnings=warnings,
        )
