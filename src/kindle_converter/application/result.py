"""Application-level conversion result (M5.1).

A :class:`ConversionResult` is the single object the future UI inspects to
answer the questions users care about: did it work, where are the files, is
the EPUB structurally valid, what warnings were collected, and what did the
input look like. It references the *existing* M4 result/validation types
(:class:`~kindle_converter.pdf.models.PDFAnalysis` and
:class:`~kindle_converter.epub.EPUBValidationResult`) rather than copying their
fields into unrelated structures.

A returned result always means every requested artifact was produced and, when
validation was requested, passed structural validation (validation *errors*
raise :class:`~kindle_converter.application.ValidationFailedError` instead
of yielding a result; validation *warnings* are non-fatal and are surfaced in
``warnings``). Failures therefore never show up as a misleadingly-successful
result.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..epub import EPUBValidationResult
from ..pdf import PDFAnalysis, PDFType
from .request import ConversionRequest, OutputFormat

__all__ = [
    "ConversionResult",
]


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """The immutable outcome of a successful :meth:`ConversionApplication.convert`.

    Attributes
    ----------
    request:
        The exact request that produced this result (echoed for provenance /
        UI display).
    analysis:
        The M3.1 first-pass analysis of the input PDF: page count, per-page
        classifications, and the document type. Exposed as the structured M4
        type so a UI can summarize the input without learning internal models;
        convenience accessors ``page_count`` and ``document_type`` are provided.
    epub_path:
        The absolute path of the produced, validated EPUB artifact.
    azw3_path:
        The absolute path of the produced AZW3 artifact, or ``None`` when AZW3
        was not requested (or not produced -- an AZW3 failure raises rather than
        returning ``None``).
    validation:
        The M4.2 validation result for the EPUB, or ``None`` when
        ``request.validate`` is ``False``. Always non-``None`` for a returned
        result when validation was requested, and always ``valid`` (errors
        would have raised).
    warnings:
        A deterministic tuple of application-level warning messages. In M5.1
        these come from the EPUB validator's non-fatal findings; future
        milestones may append more. The structured validation issues remain
        available on ``validation`` for callers that want detail.
    """

    #: The request that produced this result.
    request: ConversionRequest

    #: First-pass analysis of the input PDF.
    analysis: PDFAnalysis

    #: The produced EPUB artifact (absolute path).
    epub_path: Path

    #: The produced AZW3 artifact (absolute path), or ``None``.
    azw3_path: Path | None = None

    #: Validation result for the EPUB, or ``None`` when validation was off.
    validation: EPUBValidationResult | None = None

    #: Flat, display-ready application warnings (non-fatal).
    warnings: tuple[str, ...] = ()

    @property
    def outputs(self) -> tuple[Path, ...]:
        """All produced artifacts, in deterministic EPUB-then-AZW3 order."""
        return (self.epub_path, *(p for p in (self.azw3_path,) if p is not None))

    @property
    def requested_formats(self) -> frozenset[OutputFormat]:
        """The output formats the request asked for."""
        return self.request.formats

    @property
    def page_count(self) -> int:
        """Number of pages in the source PDF (from the analysis summary)."""
        return self.analysis.page_count

    @property
    def document_type(self) -> PDFType:
        """The classified PDF type (text / scanned / mixed) of the source."""
        return self.analysis.document_type

    @property
    def valid(self) -> bool | None:
        """Validation outcome, or ``None`` when validation was disabled."""
        if self.validation is None:
            return None
        return self.validation.valid
