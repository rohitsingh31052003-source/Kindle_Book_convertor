"""Shared helpers for the M6.2 regression harness.

This module holds the deterministic pieces the corpus regression tests
depend on:

* :class:`CountingOCR` -- an injected OCR engine that records how many times
  it was asked to recognize a page and returns deterministic text, so
  scanned/mixed fixtures run without Tesseract;
* baseline I/O for ``tests/fixtures/regression_baseline.json`` -- the
  small, human- and machine-readable expectations file that encodes each
  fixture's observable behavior (never a full EPUB snapshot);
* structural helpers that turn a converted ``Book`` and a generated EPUB
  into the compact observations the baseline records.

Everything here reads the corpus through the public
``tests.fixtures.corpus`` discovery API and stays independent of the
converter's internals.
"""

from __future__ import annotations

import html
import json
import re
import tempfile
import zipfile
from pathlib import Path

from kindle_converter import convert_pdf_to_book, convert_pdf_to_epub
from kindle_converter.document import Heading, PageBreak, Paragraph
from kindle_converter.epub import validate_epub
from kindle_converter.pdf import (
    analyze_pdf,
    detect_chapters,
    extract_page_layout,
    reconstruct_layout,
)

from tests.fixtures.corpus import (
    CORPUS_DIR,
    CorpusError,
    document_path,
    iter_documents,
)

#: Location of the version-controlled regression baseline.
BASELINE_PATH = CORPUS_DIR.parent / "regression_baseline.json"

#: Baseline schema version; bump when the expectation keys change.
BASELINE_SCHEMA_VERSION = 1

#: Image resource names inside an EPUB archive (``EPUB/image-001.png``).
_IMAGE_RESOURCE = re.compile(r"^EPUB/image-\d+\.(?:png|jpe?g|gif|svg)$")

#: XHTML heading tags counted in a chapter document.
_HEADING_TAG = re.compile(r"<h[1-6](?:[ >])", re.IGNORECASE)


class CountingOCR:
    """Deterministic OCR engine that records how many pages it recognizes.

    Each call returns a predictable ``OCR-TEXT-FOR-CALL-<n>`` paragraph
    (plain ASCII, no reliance on the real page raster), so scanned and mixed
    fixtures can be asserted without Tesseract while still proving *when* OCR
    runs: the ``calls`` counter is the observable routing signal.
    """

    def __init__(self) -> None:
        self.calls = 0

    def recognize(self, image) -> str:
        self.calls += 1
        return f"OCR-TEXT-FOR-CALL-{self.calls}"


# --------------------------------------------------------------------------- #
# Corpus helpers
# --------------------------------------------------------------------------- #


def documents() -> list[dict]:
    """Every corpus document dict, in manifest order."""
    return list(iter_documents())


def document_id(document: dict) -> str:
    """The manifest ``id`` of a corpus document."""
    return document["id"]


def manifest_document(fixture_id: str) -> dict:
    """The corpus-manifest entry for ``fixture_id``.

    The manifest is the single source of corpus metadata (classification,
    page-count expectations, features), so regression tests read expectations
    from it instead of duplicating them.

    Raises
    ------
    CorpusError
        If no manifest document carries that id.
    """
    for document in documents():
        if document["id"] == fixture_id:
            return document
    raise CorpusError(f"unknown corpus document id: {fixture_id!r}")


# --------------------------------------------------------------------------- #
# Analysis observations
# --------------------------------------------------------------------------- #


def observe_analysis(analysis) -> dict:
    """Compact analysis facts: classification and the page-count breakdown.

    ``document_type`` uses the :class:`~kindle_converter.pdf.PDFType` member
    name (``"TEXT"``/``"SCANNED"``/``"MIXED"``) so it compares directly with
    the corpus manifest's ``classification.expected``. The page counts mirror
    the manifest's ``expectations`` block. Keeping the same vocabulary lets
    the regression suite assert "manifest says X, converter observes X"
    without re-encoding the corpus vocabulary.
    """
    return {
        "document_type": analysis.document_type.name,
        "page_count": analysis.page_count,
        "text_page_count": analysis.text_page_count,
        "image_page_count": analysis.image_page_count,
    }


# --------------------------------------------------------------------------- #
# Book observations
# --------------------------------------------------------------------------- #

_BLOCK_KINDS = ("paragraphs", "headings", "images", "page_breaks")


def count_blocks(book) -> dict[str, int]:
    """Count chapter blocks by kind, across all chapters.

    The four kinds mirror the domain model (``Paragraph``, ``Heading``,
    ``Image``, ``PageBreak``); anything unexpected is ignored so this is a
    forward-compatible summary rather than a brittle enumeration.
    """
    counts = {kind: 0 for kind in _BLOCK_KINDS}
    for chapter in book.chapters:
        for block in chapter.blocks:
            if isinstance(block, Paragraph):
                counts["paragraphs"] += 1
            elif isinstance(block, Heading):
                counts["headings"] += 1
            elif isinstance(block, PageBreak):
                counts["page_breaks"] += 1
            else:
                counts["images"] += 1
    return counts


def heading_texts(book) -> list[str]:
    """All heading texts in reading order, across chapters."""
    result = []
    for chapter in book.chapters:
        for block in chapter.blocks:
            if isinstance(block, Heading):
                result.append(block.text)
    return result


def chapter_text(book) -> str:
    """Every paragraph/heading text concatenated in reading order."""
    parts = []
    for chapter in book.chapters:
        for block in chapter.blocks:
            if isinstance(block, (Paragraph, Heading)):
                parts.append(block.text)
    return "\n".join(parts)


# --------------------------------------------------------------------------- #
# EPUB observations
# --------------------------------------------------------------------------- #


def epub_chapter_bodies(epub_path: Path) -> list[str]:
    """The ``<body>`` content of every chapter document, in spine order."""
    bodies: list[str] = []
    with zipfile.ZipFile(epub_path) as archive:
        for name in sorted(archive.namelist()):
            if name.startswith("EPUB/chapter-") and name.endswith(".xhtml"):
                document = archive.read(name).decode("utf-8")
                start = document.index("<body>") + len("<body>")
                end = document.index("</body>")
                bodies.append(document[start:end])
    return bodies


def strip_xhtml(fragment: str) -> str:
    """The plain text of an XHTML body fragment (entities decoded)."""
    import html

    without_tags = re.sub(r"<[^>]+>", "", fragment)
    return html.unescape(without_tags).strip()


def observe_epub(epub_path: Path) -> dict:
    """Compact, observable EPUB facts: structure + body text.

    Structure counts are semantic (tags, page breaks, image resources), never
    byte or ZIP-order comparisons. ``text`` is the unescaped concatenated
    body text used for content-anchor assertions.
    """
    bodies = epub_chapter_bodies(epub_path)
    text = "\n".join(strip_xhtml(body) for body in bodies)

    image_resources = 0
    with zipfile.ZipFile(epub_path) as archive:
        for name in archive.namelist():
            if _IMAGE_RESOURCE.match(name):
                image_resources += 1

    page_breaks = sum(
        body.count('class="page-break"') for body in bodies
    )
    heading_tags = sum(
        len(_HEADING_TAG.findall(body)) for body in bodies
    )
    validation = validate_epub(epub_path)
    return {
        "chapter_files": len(bodies),
        "image_resources": image_resources,
        "page_breaks": page_breaks,
        "heading_tags": heading_tags,
        "nav_entries": None,  # filled below; kept for schema stability
        "valid": validation.valid,
        "errors": len(validation.errors),
        "text": text,
    }


def observe_epub_nav_entries(epub_path: Path) -> int:
    """Number of TOC entries in ``EPUB/nav.xhtml``."""
    with zipfile.ZipFile(epub_path) as archive:
        nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
    return nav.count("<a href=")


def epub_pages(epub_path: Path) -> list[list[str]]:
    """Page-by-page body text, honoring ``class="page-break"`` dividers.

    Returns one entry per printed page in reading order; each entry is the
    ordered list of that page's body paragraphs (entities decoded, whitespace
    stripped). Feeding the raw XHTML through ``ElementTree`` keeps this
    namespace-agnostic.
    """
    import xml.etree.ElementTree as ET

    pages: list[list[str]] = []
    with zipfile.ZipFile(epub_path) as archive:
        for name in sorted(archive.namelist()):
            if not name.startswith("EPUB/chapter-") or not name.endswith(".xhtml"):
                continue
            root = ET.fromstring(archive.read(name).decode("utf-8"))
            body = next(
                (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "body"),
                root,
            )
            current: list[str] = []
            for element in body:
                tag = element.tag.rsplit("}", 1)[-1]
                if tag == "div" and "page-break" in (element.get("class") or ""):
                    pages.append(current)
                    current = []
                    continue
                text = html.unescape("".join(element.itertext())).strip()
                if text:
                    current.append(text)
            pages.append(current)
    return pages


# --------------------------------------------------------------------------- #
# Full conversion observations
# --------------------------------------------------------------------------- #


def observe_conversion(
    fixture_id: str, *, keep_text: bool = True, workdir: Path | None = None
) -> dict:
    """Convert one fixture end-to-end and return its compact observations.

    Runs the real public pipeline with an injected :class:`CountingOCR` --
    never Tesseract -- and summarises the observable behavior: the M3.1
    analysis (classification + page counts), block counts, how many pages were
    routed to OCR, detected heading texts, the PDF-layer chapter count, and
    the generated EPUB's structure. The EPUB body text and on-disk path are
    included when ``keep_text`` is true (needed by content-anchor and artifact
    tests; the baseline keeps them null/absent). When ``workdir`` is given the
    EPUB is written (and kept) there instead of a throwaway temp directory.
    """
    pdf_path = document_path(fixture_id)
    analysis = analyze_pdf(pdf_path)
    book_engine = CountingOCR()
    book = convert_pdf_to_book(pdf_path, book_engine)
    ocr_calls = book_engine.calls

    layout = extract_page_layout(pdf_path)
    reconstructed = reconstruct_layout(layout)
    chapters = detect_chapters(reconstructed[0])

    if workdir is not None:
        epub_path = workdir / f"{fixture_id}.epub"
        temp_dir = None
    else:
        temp_dir = tempfile.TemporaryDirectory()
        epub_path = Path(temp_dir.name) / f"{fixture_id}.epub"
    convert_pdf_to_epub(pdf_path, epub_path, engine=CountingOCR())
    epub = observe_epub(epub_path)
    epub["nav_entries"] = observe_epub_nav_entries(epub_path)
    if keep_text:
        epub["path"] = str(epub_path)
    if temp_dir is not None:
        temp_dir.cleanup()

    observation = {
        "analysis": observe_analysis(analysis),
        "blocks": count_blocks(book),
        "ocr_calls": ocr_calls,
        "heading_texts": heading_texts(book),
        "chapter_count": chapters.chapter_count,
        "epub": epub,
    }
    if not keep_text:
        epub.pop("text", None)
    return observation


# --------------------------------------------------------------------------- #
# Baseline I/O
# --------------------------------------------------------------------------- #


def load_baseline() -> dict:
    """Load and validate the committed regression baseline.

    Raises
    ------
    RuntimeError
        If the baseline is missing, unreadable, or does not match
        :data:`BASELINE_SCHEMA_VERSION`.
    """
    if not BASELINE_PATH.is_file():
        raise RuntimeError(
            f"regression baseline not found at {BASELINE_PATH}; "
            "run python -m tests.regression.regenerate_baseline --write"
        )
    with BASELINE_PATH.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if payload.get("schema_version") != BASELINE_SCHEMA_VERSION:
        raise RuntimeError(
            f"regression baseline schema mismatch at {BASELINE_PATH}: "
            f"expected {BASELINE_SCHEMA_VERSION}, got "
            f"{payload.get('schema_version')}"
        )
    return payload