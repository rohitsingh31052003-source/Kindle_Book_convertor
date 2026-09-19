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
(columns) are planned for later milestones. Header/footer detection is
implemented in the ``header_footer`` submodule (Milestone 2.5): a
conservative, deterministic, detection-only layer that classifies repeated
page-level headers and footers from the M2.3 paragraph representation
(``detect_headers_footers`` / ``classify_header_footer_paragraphs``).

The ``renderer`` submodule (Milestone 3.2) rasterizes PDF pages into
deterministic RGB pixmaps at an explicit DPI (``render_page`` /
``render_pages``), producing the typed :class:`RenderedPage` values that the
next OCR stage (M3.3) consumes. Rendering is strictly
``PDF page -> raster image``: no OCR and no image preprocessing.

The ``ocr`` submodule (Milestone 3.3) consumes those :class:`RenderedPage`
rasters and recognizes their text through a small, injectable
:class:`OCREngine` abstraction (``ocr_page`` / ``ocr_pages``), returning a
typed :class:`OCRResult` per page. The built-in :class:`TesseractEngine`
wraps the external Tesseract executable through the optional ``ocr``
dependencies; it performs **no** OCR cleanup and **no** structural
reconstruction, and nothing routes scanned pages through OCR yet.

The ``ocr_cleanup`` submodule (Milestone 3.4) consumes those raw
:class:`OCRResult` values and applies a conservative, deterministic text
cleanup pass (``clean_ocr_text`` / ``clean_ocr_result`` /
``clean_ocr_pages``), producing an immutable :class:`CleanedOCRResult` per
page. The cleanup layer is strictly post-OCR: it never renders PDFs, never
calls an OCR engine, and never performs structural reconstruction or
recognition correction.

The ``processing`` submodule (Milestone 3.5) is the document/page-level
**routing layer**: given a PDF and its M3.1 analysis, it decides how each
page is processed (``process_page`` / ``process_pages``), returning an
immutable :class:`PageProcessingResult` per page. TEXT pages use native
extraction only; SCANNED pages use ``render → OCR → cleanup``; MIXED pages
preserve native text **and** keep OCR-derived text separately -- the two
sources are never concatenated or merged. Routing is deterministic, runs
strictly in page order, and injects the OCR engine (and optionally the
renderer) so it is testable without Tesseract. It performs no structural
reconstruction.

The ``structural`` submodule (Milestone 3.6) is the OCR-aware structural
reconstruction adapter: it consumes the M3.5 per-page routing results
(``reconstruct_processed_pages`` / ``processed_pages_to_book``), reuses the
M2 stack byte-identically for native text, and appends OCR-derived body
paragraphs page by page (native first, then OCR) for SCANNED/MIXED pages.
OCR paragraphs carry ``TextSource.OCR`` and no fabricated geometry.

The ``cover_detection`` submodule (Milestone 7.2) is the automatic cover
selection service: given the M3.1 analysis, the M2.1 layout, and the M3.5
routing results of one document, it measures a bounded window of early pages
(``measure_cover_signals``), scores them with documented deterministic
weights (``score_cover_page``), and applies an explicit confidence threshold
plus an ambiguity margin (``decide_cover_page``) before materializing the
selected page as a PNG cover image through the M3.2 renderer
(``materialize_cover_page``). ``select_cover`` applies the M7.2 precedence --
explicit cover, then confidently detected cover, then no cover -- and no
detection ever runs when an explicit cover exists.

Since M4.1 the resulting :class:`~kindle_converter.document.models.Book`
(with M2.13 images attached) is the single input of the EPUB layer:
``kindle_converter.convert_pdf_to_epub`` composes ``convert_pdf_to_book``
with ``kindle_converter.epub.build_epub`` for TEXT, SCANNED, and MIXED PDFs,
so no PDF/OCR logic is duplicated on the output side.
"""
from .analyzer import (
    EmptyPDFError,
    NoContentError,
    PDFAnalysisError,
    PDFReadError,
    analyze_pdf,
    build_analysis,
    classify,
    classify_pages,
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
    TextSource,
    reconstruct_ocr_paragraphs,
    reconstruct_page_paragraphs,
    reconstruct_paragraphs,
)
from .reading_order import (
    COLUMN_FULL_WIDTH_FRACTION,
    COLUMN_FULL_WIDTH_MIN_PT,
    COLUMN_GAP_TOLERANCE_PT,
    COLUMN_MAX_COUNT,
    COLUMN_MAX_OVERLAP_RATIO,
    COLUMN_MIN_BLOCKS,
    COLUMN_MIN_COVERAGE_FRACTION,
    COLUMN_OVERLAP_RATIO,
    ROW_OVERLAP_TOLERANCE_PT,
    ROW_TOP_TOLERANCE_PT,
    OrderedBlock,
    OrderedLayout,
    OrderedPage,
    reconstruct_page_order,
    reconstruct_read_order,
)
# Import header/footer detection API (Milestone 2.5)
from .header_footer import (
    HEADER_FOOTER_CONFIDENCE_THRESHOLD,
    HEADER_POSITION_TOLERANCE_FRACTION,
    HEADER_REGION_FRACTION,
    FOOTER_POSITION_TOLERANCE_FRACTION,
    FOOTER_REGION_FRACTION,
    ISOLATION_SCORE_WEIGHT,
    MAX_HEADER_FOOTER_CHARS,
    MIN_REPEATED_PAGES,
    PAGE_NUMBER_SCORE_WEIGHT,
    POSITION_CONSISTENCY_SCORE_WEIGHT,
    REPETITION_SATURATION_PAGES,
    REPETITION_SCORE_WEIGHT,
    SHORT_HEADER_FOOTER_CHARS,
    ClassifiedHeaderFooterParagraph,
    DetectedHeaderFooter,
    HeaderFooterLayout,
    HeaderFooterPage,
    HeaderFooterType,
    classify_header_footer_paragraphs,
    detect_headers_footers,
)

# Integrated reconstruction API (Milestone 2.7)
from .reconstruction import (
    GENERIC_HEADING_LEVEL,
    ElementKind,
    ReconstructedBodyElement,
    ReconstructedDocument,
    ReconstructedElement,
    ReconstructedImage,
    ReconstructedPage,
    build_reconstructed_document,
    ordered_body_elements,
    reconstruct_layout,
    reconstructed_document_to_book,
)

# Chapter detection API (Milestone 2.9)
from .chapters import (
    ChapterDetectionResult,
    ChapterNumberType,
    DetectedChapter,
    detect_chapters,
    detect_chapters_simple,
)

# Page-number removal API (Milestone 2.10)
from .page_numbers import (
    MIN_BARE_SEQUENCE_PAGES,
    DetectedPageNumber,
    PageNumberLocation,
    PageNumberRemovalResult,
    remove_page_numbers,
)

# Table of contents generation API (Milestone 2.11)
from .toc import (
    TOC_CHAPTER_LEVEL,
    TableOfContents,
    TOCEntry,
    generate_toc,
)

# Metadata handling API (Milestone 2.12)
from .metadata import (
    extract_pdf_metadata,
    resolve_metadata,
)

# Image extraction and placement API (Milestone 2.13)
from .images import (
    ImageAsset,
    ImageExtractionError,
    ImageExtractionResult,
    ImagePlacement,
    extract_pdf_images,
)

# Page rendering API (Milestone 3.2)
from .renderer import (
    DEFAULT_RENDER_DPI,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    PDFRenderingError,
    RenderedPage,
    render_page,
    render_pages,
)

# OCR processing API (Milestone 3.3)
from .ocr import (
    DEFAULT_OCR_LANGUAGE,
    OCREngine,
    OCRError,
    OCREngineUnavailableError,
    OCRResult,
    TesseractEngine,
    ocr_page,
    ocr_pages,
)

# OCR cleanup API (Milestone 3.4)
from .ocr_cleanup import (
    MAX_CONSECUTIVE_BLANK_LINES,
    CleanedOCRResult,
    clean_ocr_pages,
    clean_ocr_result,
    clean_ocr_text,
)

# Mixed text/image page processing / routing API (Milestone 3.5)
from .processing import (
    PageProcessingResult,
    PageRenderer,
    process_page,
    process_pages,
)

# OCR-aware structural reconstruction API (Milestone 3.6)
from .structural import (
    processed_pages_to_book,
    reconstruct_processed_pages,
)

# Automatic cover selection API (Milestone 7.2)
from .cover_detection import (
    COVER_AMBIGUITY_MARGIN,
    COVER_CANDIDATE_WINDOW,
    COVER_CONFIDENCE_THRESHOLD,
    COVER_RENDER_DPI,
    CoverCandidate,
    CoverDecision,
    CoverPageSignals,
    CoverSelection,
    CoverSelectionSource,
    decide_cover_page,
    detect_cover,
    document_median_text_chars,
    materialize_cover_page,
    measure_cover_signals,
    score_cover_page,
    select_cover,
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
    "classify_pages",
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
    "COLUMN_MIN_BLOCKS",
    "COLUMN_GAP_TOLERANCE_PT",
    "COLUMN_MAX_OVERLAP_RATIO",
    "COLUMN_FULL_WIDTH_FRACTION",
    "COLUMN_FULL_WIDTH_MIN_PT",
    "COLUMN_MIN_COVERAGE_FRACTION",
    "COLUMN_MAX_COUNT",
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
    "TextSource",
    "reconstruct_ocr_paragraphs",
    "reconstruct_page_paragraphs",
    "reconstruct_paragraphs",
    "PARAGRAPH_GAP_FACTOR",
    "PARAGRAPH_INDENT_PT",
    "SHORT_LINE_THRESHOLD",
    "ClassifiedParagraph",
    "DetectedHeading",
    "HeadingPage",
    "HeadingLayout",
    "detect_headers_footers",
    "classify_header_footer_paragraphs",
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
    "DetectedHeaderFooter",
    "HeaderFooterLayout",
    "HeaderFooterPage",
    "HeaderFooterType",
    "ClassifiedHeaderFooterParagraph",
    "HEADER_REGION_FRACTION",
    "FOOTER_REGION_FRACTION",
    "MIN_REPEATED_PAGES",
    "HEADER_POSITION_TOLERANCE_FRACTION",
    "FOOTER_POSITION_TOLERANCE_FRACTION",
    "REPETITION_SATURATION_PAGES",
    "SHORT_HEADER_FOOTER_CHARS",
    "MAX_HEADER_FOOTER_CHARS",
    "HEADER_FOOTER_CONFIDENCE_THRESHOLD",
    "REPETITION_SCORE_WEIGHT",
    "POSITION_CONSISTENCY_SCORE_WEIGHT",
    "ISOLATION_SCORE_WEIGHT",
    "PAGE_NUMBER_SCORE_WEIGHT",
    "ElementKind",
    "ReconstructedBodyElement",
    "ReconstructedDocument",
    "ReconstructedElement",
    "ReconstructedImage",
    "ReconstructedPage",
    "build_reconstructed_document",
    "ordered_body_elements",
    "reconstruct_layout",
    "reconstructed_document_to_book",
    "GENERIC_HEADING_LEVEL",
    # Chapter detection (Milestone 2.9)
    "ChapterDetectionResult",
    "ChapterNumberType",
    "DetectedChapter",
    "detect_chapters",
    "detect_chapters_simple",
    # Page-number removal (Milestone 2.10)
    "MIN_BARE_SEQUENCE_PAGES",
    "DetectedPageNumber",
    "PageNumberLocation",
    "PageNumberRemovalResult",
    "remove_page_numbers",
    # Table of contents generation (Milestone 2.11)
    "TOC_CHAPTER_LEVEL",
    "TableOfContents",
    "TOCEntry",
    "generate_toc",
    # Metadata handling (Milestone 2.12)
    "extract_pdf_metadata",
    "resolve_metadata",
    # Image extraction and placement (Milestone 2.13)
    "ImageAsset",
    "ImageExtractionError",
    "ImageExtractionResult",
    "ImagePlacement",
    "extract_pdf_images",
    # Page rendering (Milestone 3.2)
    "DEFAULT_RENDER_DPI",
    "MIN_RENDER_DPI",
    "MAX_RENDER_DPI",
    "PDFRenderingError",
    "RenderedPage",
    "render_page",
    "render_pages",
    # OCR processing (Milestone 3.3)
    "DEFAULT_OCR_LANGUAGE",
    "OCRResult",
    "OCREngine",
    "TesseractEngine",
    "OCRError",
    "OCREngineUnavailableError",
    "ocr_page",
    "ocr_pages",
    # OCR cleanup (Milestone 3.4)
    "MAX_CONSECUTIVE_BLANK_LINES",
    "CleanedOCRResult",
    "clean_ocr_text",
    "clean_ocr_result",
    "clean_ocr_pages",
    # Mixed text/image processing / routing (Milestone 3.5)
    "PageProcessingResult",
    "PageRenderer",
    "process_page",
    "process_pages",
    # OCR-aware structural reconstruction (Milestone 3.6)
    "processed_pages_to_book",
    "reconstruct_processed_pages",
    # Automatic cover selection (Milestone 7.2)
    "COVER_AMBIGUITY_MARGIN",
    "COVER_CANDIDATE_WINDOW",
    "COVER_CONFIDENCE_THRESHOLD",
    "COVER_RENDER_DPI",
    "CoverCandidate",
    "CoverDecision",
    "CoverPageSignals",
    "CoverSelection",
    "CoverSelectionSource",
    "decide_cover_page",
    "detect_cover",
    "document_median_text_chars",
    "materialize_cover_page",
    "measure_cover_signals",
    "score_cover_page",
    "select_cover",
]