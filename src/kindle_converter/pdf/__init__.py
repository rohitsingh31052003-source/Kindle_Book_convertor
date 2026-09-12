"""PDF input handling: analysis and (planned) extraction.

The ``analyzer`` submodule provides deterministic first-pass PDF analysis:
given a PDF path or an open PyMuPDF document, :func:`analyze_pdf` decides
whether the document is text-based, scanned, or mixed and returns a
structured :class:`PDFAnalysis` summary. Extraction, OCR, and layout
analysis are planned for later milestones.
"""

from .analyzer import (
    EmptyPDFError,
    NoContentError,
    PDFAnalysisError,
    PDFReadError,
    analyze_pdf,
    build_analysis,
    classify,
)
from .models import PageAnalysis, PDFAnalysis, PDFType

__all__ = [
    "PDFAnalysis",
    "PDFAnalysisError",
    "PDFReadError",
    "EmptyPDFError",
    "NoContentError",
    "PDFType",
    "PageAnalysis",
    "analyze_pdf",
    "build_analysis",
    "classify",
]