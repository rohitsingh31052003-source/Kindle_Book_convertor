"""PDF input handling: analysis, layout extraction, and first-pass text.

The ``analyzer`` submodule provides deterministic first-pass PDF analysis:
given a PDF path or an open PyMuPDF document, :func:`analyze_pdf` decides
whether the document is text-based, scanned, or mixed and returns a
structured :class:`PDFAnalysis` summary.

The ``layout`` submodule (Milestone 2.1) preserves the layout-aware text
representation -- per-page blocks with geometry, font metadata, and
extraction ordering -- that the later reconstruction stages consume.

The ``extractor`` submodule converts a text-based PDF into the
format-independent :class:`~kindle_converter.document.models.Book` model
with :func:`extract_book`. OCR and layout reconstruction are planned for
later milestones.
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
from .extractor import (
    PDFExtractionError,
    MixedPDFError,
    ScannedPDFError,
    extract_book,
)
from .layout import (
    FontFlags,
    LayoutBlock,
    LayoutPage,
    PageLayout,
    TextLine,
    TextSpan,
    decode_font_flags,
    extract_page_layout,
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
    "PDFExtractionError",
    "ScannedPDFError",
    "MixedPDFError",
    "extract_book",
    "PageLayout",
    "LayoutPage",
    "LayoutBlock",
    "TextLine",
    "TextSpan",
    "FontFlags",
    "decode_font_flags",
    "extract_page_layout",
]