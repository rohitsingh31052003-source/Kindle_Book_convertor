"""PDF input handling: analysis, layout extraction, and first-pass text.

The ``analyzer`` submodule provides deterministic first-pass PDF analysis:
given a PDF path or an open PyMuPDF document, :func:`analyze_pdf` decides
whether the document is text-based, scanned, or mixed and returns a
structured :class:`PDFAnalysis` summary.

The ``layout`` submodule (Milestone 2.1) preserves the layout-aware text
representation -- per-page blocks with geometry, font metadata, and
extraction ordering -- that the later reconstruction stages consume.

The ``reading_order`` submodule (Milestone 2.2) consumes that layout
representation and produces a deterministic, geometry-based reading order
for the text blocks of each page (``reconstruct_read_order``), leaving the
original extraction order untouched as provenance.

The ``paragraphs`` submodule (Milestone 2.3) consumes the M2.2 reading
order and groups physical text lines into logical paragraphs
(``reconstruct_paragraphs``), preserving source provenance without
introducing PDF geometry into the document model. The M2.2 reading order
is authoritative; M2.3 does not re-sort lines.

The ``extractor`` submodule converts a text-based PDF into the
format-independent :class:`~kindle_converter.document.models.Book` model
with :func:`extract_book`. OCR and the remaining layout reconstruction
(headings, columns, header/footer detection) are planned for later
milestones.
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
from .paragraphs import (
    PARAGRAPH_GAP_FACTOR,
    PARAGRAPH_INDENT_PT,
    SHORT_LINE_THRESHOLD,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    reconstruct_page_paragraphs,
    reconstruct_paragraphs,
)
from .reading_order import (
    COLUMN_OVERLAP_RATIO,
    ROW_OVERLAP_TOLERANCE_PT,
    ROW_TOP_TOLERANCE_PT,
    OrderedBlock,
    OrderedLayout,
    OrderedPage,
    reconstruct_page_order,
    reconstruct_read_order,
)

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
    "COLUMN_OVERLAP_RATIO",
    "ROW_OVERLAP_TOLERANCE_PT",
    "ROW_TOP_TOLERANCE_PT",
    "OrderedBlock",
    "OrderedPage",
    "OrderedLayout",
    "reconstruct_page_order",
    "reconstruct_read_order",
    "ParagraphLayout",
    "ParagraphPage",
    "ReconstructedParagraph",
    "reconstruct_page_paragraphs",
    "reconstruct_paragraphs",
    "PARAGRAPH_GAP_FACTOR",
    "PARAGRAPH_INDENT_PT",
    "SHORT_LINE_THRESHOLD",
]