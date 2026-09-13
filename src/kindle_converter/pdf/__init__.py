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

The ``headings`` submodule (Milestone 2.4) consumes the M2.3 paragraph
representation and classifies each paragraph as a likely heading or body
text using conservative, deterministic typography and geometry evidence
(``classify_paragraphs`` / ``detect_headings``). It is a classification
layer, not a text-rewriting layer: paragraphs are never reordered or
modified, and provenance is preserved.

The ``extractor`` submodule converts a text-based PDF into the
format-independent :class:`~kindle_converter.document.models.Book` model
with :func:`extract_book`. OCR and the remaining layout reconstruction
(columns, header/footer detection) are planned for later milestones.
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
from .headings import (
    BODY_LIKE_PENALTY,
    BOLD_TYPOGRAPHY_SCORE,
    CENTERED_ALIGNMENT_SCORE,
    CENTERED_ALIGNMENT_TOLERANCE_PT,
    CENTERED_WIDTH_FRACTION,
    ClassifiedParagraph,
    DetectedHeading,
    FONT_FAMILY_CHANGE_SCORE,
    FONT_SIZE_ABOVE_BASELINE_SCORE,
    FONT_SIZE_RATIO_THRESHOLD,
    HeadingLayout,
    HeadingPage,
    HEADING_SCORE_THRESHOLD,
    ISOLATED_PARAGRAPH_SCORE,
    ITALIC_TYPOGRAPHY_SCORE,
    LARGE_SPACING_AFTER_SCORE,
    LARGE_SPACING_BEFORE_SCORE,
    LARGE_SPACING_GAP_FACTOR,
    LARGE_SPACING_LINE_FACTOR,
    LARGE_SPACING_MIN_PT,
    LONG_PARAGRAPH_CHARS,
    LONG_PARAGRAPH_PENALTY,
    MIN_HEADING_SIGNALS,
    MIXED_FONT_SIZE_CONTRAST_SCORE,
    MULTI_LINE_HEADING_SHAPE_SCORE,
    PAGE_EDGE_MARGIN_PT,
    PAGE_EDGE_PROXIMITY_SCORE,
    SHORT_PARAGRAPH_CHARS,
    SHORT_PARAGRAPH_SCORE,
    STRONG_FONT_SIZE_ABOVE_BASELINE_SCORE,
    STRONG_FONT_SIZE_RATIO_THRESHOLD,
    UNCONFIRMED_BOLD_TYPOGRAPHY_SCORE,
    VERY_SHORT_PARAGRAPH_CHARS,
    classify_paragraphs,
    detect_headings,
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
    "ClassifiedParagraph",
    "DetectedHeading",
    "HeadingPage",
    "HeadingLayout",
    "classify_paragraphs",
    "detect_headings",
    "FONT_SIZE_RATIO_THRESHOLD",
    "STRONG_FONT_SIZE_RATIO_THRESHOLD",
    "MIN_HEADING_SIGNALS",
    "HEADING_SCORE_THRESHOLD",
    "SHORT_PARAGRAPH_CHARS",
    "VERY_SHORT_PARAGRAPH_CHARS",
    "LONG_PARAGRAPH_CHARS",
    "LARGE_SPACING_MIN_PT",
    "LARGE_SPACING_LINE_FACTOR",
    "LARGE_SPACING_GAP_FACTOR",
    "CENTERED_ALIGNMENT_TOLERANCE_PT",
    "CENTERED_WIDTH_FRACTION",
    "PAGE_EDGE_MARGIN_PT",
    "FONT_SIZE_ABOVE_BASELINE_SCORE",
    "STRONG_FONT_SIZE_ABOVE_BASELINE_SCORE",
    "MIXED_FONT_SIZE_CONTRAST_SCORE",
    "BOLD_TYPOGRAPHY_SCORE",
    "UNCONFIRMED_BOLD_TYPOGRAPHY_SCORE",
    "ITALIC_TYPOGRAPHY_SCORE",
    "FONT_FAMILY_CHANGE_SCORE",
    "CENTERED_ALIGNMENT_SCORE",
    "LARGE_SPACING_BEFORE_SCORE",
    "LARGE_SPACING_AFTER_SCORE",
    "ISOLATED_PARAGRAPH_SCORE",
    "SHORT_PARAGRAPH_SCORE",
    "MULTI_LINE_HEADING_SHAPE_SCORE",
    "PAGE_EDGE_PROXIMITY_SCORE",
    "LONG_PARAGRAPH_PENALTY",
    "BODY_LIKE_PENALTY",
]