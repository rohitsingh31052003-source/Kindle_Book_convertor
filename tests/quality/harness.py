"""Measurement of conversion quality against the M6.1 corpus.

``observe_quality`` drives the real public conversion pipeline with an
injected :class:`~tests.regression.harness.CountingOCR` (never Tesseract)
and records a compact, deterministic :class:`QualityObservation` per fixture:

- classification (derived, not re-authored)   PDF analysis document type
- structure          paragraph/heading/chapter/TOC/blank-line artifacts
- reading order      left/right column token sequence from the EPUB
- ocr                how many pages were routed to OCR, marker text present
- images             image blocks in the Book and image resources in the EPUB
- epub               validity, chapter files, heading tags, nav, blanks

Observations carry no timestamps, no machine paths, and no randomness, so two
runs over the same corpus produce byte-identical results. The EPUB body text
and page token extraction are computed while the file is on disk and only the
derived facts survive in the :class:`QualityObservation`, keeping the baseline
and reports free of absolute paths.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kindle_converter import convert_pdf_to_book, convert_pdf_to_epub
from kindle_converter import pdf as pdf_api
from kindle_converter.document.models import Paragraph
from tests.fixtures.corpus import document_path
from tests.regression.harness import (
    CountingOCR,
    count_blocks,
    documents,
    observe_epub,
    observe_epub_nav_entries,
    epub_chapter_bodies,
    epub_pages,
    strip_xhtml,
)

# A reading-order voice: LEFT_1/RIGHT_2 (page number suffix) is spelled by the
# EPUB page text the same way the M2.10 reconstruction observes it.
COLUMN_TOKEN = re.compile(r"\b[LR][1-4]\b")

# --------------------------------------------------------------------------- #
# Observation model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class QualityObservation:
    """Deterministic quality facts for one converted fixture."""

    fixture: str
    classification: str  # PDFType.name as observed by analyze_pdf
    paragraphs: int
    headings: int
    chapters: int  # PDF-layer chapter detection (M2.11)
    toc_entries: int  # PDF-layer TOC generation (M2.11)
    page_breaks: int
    empty_paragraphs: int
    near_empty_paragraphs: int
    body_text: str
    column_tokens: tuple[str, ...]
    ocr_calls: int
    ocr_text_present: bool
    image_blocks: int
    epub_image_resources: int
    epub_valid: bool
    epub_chapter_files: int
    epub_heading_tags: int
    epub_nav_entries: int
    epub_text: str
    epub_empty_chapters: int
    epub_page_breaks: int


def _paragraph_texts(book) -> list[str]:
    """Body paragraph strings only (headings are not body paragraphs)."""
    texts: list[str] = []
    for chapter in book.chapters:
        for block in chapter.blocks:
            if isinstance(block, Paragraph):
                texts.append(block.text)
    return texts


def observed_metrics(observation: QualityObservation) -> dict[str, Any]:
    """Flatten an observation into the metric dict expectations evaluate on.

    The keys match the ``KNOWN_FIELDS`` namespace of ``expectations`` so an
    authored expectation like ``{"type": "minimum", "expected": 3}`` under
    ``structure.headings`` reads ``observation.headings``.
    """
    return {
        "classification": observation.classification,
        "paragraphs": observation.paragraphs,
        "headings": observation.headings,
        "chapters": observation.chapters,
        "toc_entries": observation.toc_entries,
        "page_breaks": observation.page_breaks,
        "empty_paragraphs": observation.empty_paragraphs,
        "near_empty_paragraphs": observation.near_empty_paragraphs,
        "body_text": observation.body_text,
        "column_tokens": tuple(observation.column_tokens),
        "ocr_calls": observation.ocr_calls,
        "ocr_text_present": observation.ocr_text_present,
        "image_blocks": observation.image_blocks,
        "epub_image_resources": observation.epub_image_resources,
        "epub_valid": observation.epub_valid,
        "epub_chapter_files": observation.epub_chapter_files,
        "epub_heading_tags": observation.epub_heading_tags,
        "epub_nav_entries": observation.epub_nav_entries,
        "epub_text": observation.epub_text,
        "epub_empty_chapters": observation.epub_empty_chapters,
        "epub_page_breaks": observation.epub_page_breaks,
    }


# --------------------------------------------------------------------------- #
# Measurement
# --------------------------------------------------------------------------- #


def observe_quality(fixture_id: str, *, workdir: Path | None = None) -> QualityObservation:
    """Convert one fixture end-to-end and measure its quality facts.

    ``workdir`` (used by the harness and tests) keeps the generated EPUB on
    disk; otherwise a throwaway temp directory is used. The observation never
    records the path itself.
    """
    pdf_path = document_path(fixture_id)

    analysis = pdf_api.analyze_pdf(pdf_path)
    book_engine = CountingOCR()
    book = convert_pdf_to_book(pdf_path, book_engine)
    ocr_calls = book_engine.calls

    layout = pdf_api.extract_page_layout(pdf_path)
    reconstructed = pdf_api.reconstruct_layout(layout)
    chapters = pdf_api.detect_chapters(reconstructed[0])
    toc = pdf_api.generate_toc(chapters)

    if workdir is not None:
        epub_path = workdir / f"{fixture_id}.epub"
        temp_dir = None
    else:
        temp_dir = tempfile.TemporaryDirectory()
        epub_path = Path(temp_dir.name) / f"{fixture_id}.epub"

    try:
        convert_pdf_to_epub(pdf_path, epub_path, engine=CountingOCR())
        epub = observe_epub(epub_path)
        epub["nav_entries"] = observe_epub_nav_entries(epub_path)

        counts = count_blocks(book)
        body_text = "\n".join(paragraph for paragraph in _paragraph_texts(book))

        empty_paragraphs = sum(
            1 for text in _paragraph_texts(book) if text.strip() == ""
        )
        near_empty_paragraphs = sum(
            1 for text in _paragraph_texts(book) if 0 < len(text.strip()) < 3
        )

        column_tokens: list[str] = []
        for page in epub_pages(epub_path):
            for text in page:
                column_tokens.extend(COLUMN_TOKEN.findall(text))

        epub_bodies = epub_chapter_bodies(epub_path)
        epub_empty_chapters = sum(
            1 for body in epub_bodies if not strip_xhtml(body)
        )
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()

    return QualityObservation(
        fixture=fixture_id,
        classification=analysis.document_type.name,
        paragraphs=counts["paragraphs"],
        headings=counts["headings"],
        chapters=chapters.chapter_count,
        toc_entries=toc.entry_count,
        page_breaks=counts["page_breaks"],
        empty_paragraphs=empty_paragraphs,
        near_empty_paragraphs=near_empty_paragraphs,
        body_text=body_text,
        column_tokens=tuple(column_tokens),
        ocr_calls=ocr_calls,
        ocr_text_present="OCR-TEXT-FOR-CALL" in body_text,
        image_blocks=counts["images"],
        epub_image_resources=epub["image_resources"],
        epub_valid=bool(epub["valid"]),
        epub_chapter_files=epub["chapter_files"],
        epub_heading_tags=epub["heading_tags"],
        epub_nav_entries=epub["nav_entries"],
        epub_text=epub["text"],
        epub_empty_chapters=epub_empty_chapters,
        epub_page_breaks=epub["page_breaks"],
    )


def expected_classification(fixture_id: str) -> str:
    """The expected classification straight from the corpus manifest.

    The manifest is the single source of truth for ``classification.expected``;
    the quality baseline deliberately does *not* duplicate it.
    """
    for document in documents():
        if document["id"] == fixture_id:
            return document["classification"]["expected"]
    raise KeyError(f"unknown corpus document id: {fixture_id!r}")