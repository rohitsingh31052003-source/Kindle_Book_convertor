"""Automatic cover selection tests (Milestone 7.2).

Deterministic and offline: every PDF is generated on the fly with PyMuPDF,
every OCR page uses an injected fake engine, and no network, Tesseract,
machine time, or environment state is involved. The suite covers:

* the scoring gates (``score_cover_page``) -- blank pages, body-text pages,
  table-of-contents pages, copyright/title pages, and weak candidates are
  all rejected;
* the selection rule (``decide_cover_page``) -- below-threshold and
  ambiguous evidence produce *no* automatic cover;
* the precedence rule (``select_cover``) -- an explicit cover always wins
  and short-circuits detection entirely;
* end-to-end detection through the pipeline (``convert_pdf_to_book`` /
  ``convert_pdf_to_epub``) for native, scanned, and mixed documents,
  including the "not just page 1" and bounded-window guarantees;
* the unchanged M4.4 EPUB cover contract for an automatically detected
  cover (``cover.xhtml``, ``properties="cover-image"``, ``name="cover"``
  metadata, linear cover, non-linear nav, and no navigation entry).

M7.1 first-page and nav-spine regressions live in their own suites and are
run unchanged alongside this one.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf
import pytest

from kindle_converter import convert_pdf_to_book, convert_pdf_to_epub
from kindle_converter.document import Book, Image
from kindle_converter.epub import validate_epub
from kindle_converter.epub.builder import (
    COVER_IMAGE_ID,
    COVER_IMAGE_RESOURCE,
    COVER_PAGE_FILE,
)
from kindle_converter.pdf import (
    COVER_AMBIGUITY_MARGIN,
    COVER_CANDIDATE_WINDOW,
    COVER_CONFIDENCE_THRESHOLD,
    CoverCandidate,
    CoverDecision,
    CoverPageSignals,
    CoverSelectionSource,
    PDFType,
    TextSource,
    analyze_pdf,
    decide_cover_page,
    document_median_text_chars,
    materialize_cover_page,
    measure_cover_signals,
    score_cover_page,
    select_cover,
)
from kindle_converter.pdf.layout import extract_page_layout
from kindle_converter.pdf.processing import process_pages
from kindle_converter.pdf.renderer import render_page

PAGE_WIDTH = 595
PAGE_HEIGHT = 842

BODY_LINE = (
    "It was a bright cold day in April, and the clocks were striking "
    "thirteen; Winston Smith kept walking anyway."
)

#: A tiny synthetic JPEG payload for explicit-cover precedence tests.
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x01\x02\x03"

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


class _FakeOCREngine:
    """Deterministic ``recognize(image) -> text`` stand-in (no Tesseract)."""

    def __init__(self, texts: list[str]) -> None:
        self._texts = texts
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        index = min(len(self.calls) - 1, len(self._texts) - 1)
        return self._texts[index]


def _new_doc() -> pymupdf.Document:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Cover Detection Fixture", "author": "M7.2"})
    return doc


def _add_text_page(
    doc: pymupdf.Document, *, text: str = BODY_LINE, repeats: int = 8
) -> None:
    """A portrait page of ordinary body text (no images)."""
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    full_text = "\n".join(f"{text} ({repeats}-{index})" for index in range(repeats))
    page.insert_textbox(
        pymupdf.Rect(72, 72, PAGE_WIDTH - 72, PAGE_HEIGHT - 72),
        full_text,
        fontsize=11,
    )


def _add_blank_page(doc: pymupdf.Document) -> None:
    """A completely empty page."""
    doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def _add_cover_image_page(
    doc: pymupdf.Document, *, text: str | None = None
) -> None:
    """A portrait page that is (almost) one full-page image."""
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1200, 1700))
    pixmap.set_rect(pixmap.irect, (40, 80, 160))
    page.insert_image(page.rect, pixmap=pixmap)
    if text is not None:
        page.insert_text((72, PAGE_HEIGHT - 100), text, fontsize=24)


def _add_mixed_page(doc: pymupdf.Document, *, text: str) -> None:
    """A MIXED page: an image over roughly the top 60% plus real text."""
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 1200, 1020))
    pixmap.set_rect(pixmap.irect, (40, 80, 160))
    page.insert_image(
        pymupdf.Rect(0, 0, PAGE_WIDTH, PAGE_HEIGHT * 0.6), pixmap=pixmap
    )
    page.insert_textbox(
        pymupdf.Rect(72, PAGE_HEIGHT * 0.62, PAGE_WIDTH - 72, PAGE_HEIGHT - 72),
        text,
        fontsize=24,
    )


def _add_toc_page(doc: pymupdf.Document) -> None:
    """A portrait page shaped like a table of contents (no images)."""
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    entries = "\n".join(
        f"Chapter {index} ............ {index * 10 + 3}" for index in range(1, 9)
    )
    page.insert_textbox(
        pymupdf.Rect(72, 72, PAGE_WIDTH - 72, PAGE_HEIGHT - 72),
        f"Contents\n\n{entries}",
        fontsize=12,
    )


def _cover_first_pdf(path: Path) -> Path:
    """A scanned-shape book: full-page cover image, then body text."""
    doc = _new_doc()
    _add_cover_image_page(doc)
    _add_text_page(doc)
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _title_page_then_cover_pdf(path: Path) -> Path:
    """A prose title page first, full-page cover image second, then body."""
    doc = _new_doc()
    _add_text_page(doc, text="The Long Expected Title", repeats=4)
    _add_cover_image_page(doc)
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _mixed_cover_pdf(path: Path) -> Path:
    """A MIXED cover page: dominant image plus meaningful title text."""
    doc = _new_doc()
    _add_mixed_page(doc, text="Mixed Cover Book By An Author")
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _body_text_first_pdf(path: Path) -> Path:
    """An ordinary text book: no cover-like page anywhere."""
    doc = _new_doc()
    _add_text_page(doc)
    _add_text_page(doc)
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _blank_first_pdf(path: Path) -> Path:
    """A blank first page followed by ordinary body text."""
    doc = _new_doc()
    _add_blank_page(doc)
    _add_text_page(doc)
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _blank_only_pdf(path: Path) -> Path:
    """A document with no text anywhere (blank scanned-style pages)."""
    doc = _new_doc()
    for _ in range(3):
        _add_blank_page(doc)
    doc.save(path)
    doc.close()
    return path


def _toc_first_pdf(path: Path) -> Path:
    """A table-of-contents first page followed by ordinary body text."""
    doc = _new_doc()
    _add_toc_page(doc)
    _add_text_page(doc)
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _copyright_cover_pdf(path: Path) -> Path:
    """A full-page image carrying copyright text (front matter, not cover)."""
    doc = _new_doc()
    _add_cover_image_page(
        doc,
        text="Copyright 2001 Example Press. All rights reserved. ISBN 1-2-3",
    )
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _cover_beyond_window_pdf(path: Path) -> Path:
    """Five body-text pages, then the only cover-like page (page 6)."""
    doc = _new_doc()
    for _ in range(5):
        _add_text_page(doc)
    _add_cover_image_page(doc)
    doc.save(path)
    doc.close()
    return path


def _cover_at_page_five_pdf(path: Path) -> Path:
    """Four body-text pages, then a cover-like page at the last bound slot."""
    doc = _new_doc()
    for _ in range(4):
        _add_text_page(doc)
    _add_cover_image_page(doc, text="Fifth Page Book Cover")
    doc.save(path)
    doc.close()
    return path


def _ambiguous_cover_pdf(path: Path) -> Path:
    """Two equally plausible cover-like pages (page 1 and page 2)."""
    doc = _new_doc()
    _add_cover_image_page(doc)
    _add_cover_image_page(doc, text="Cover Book By An Author")
    _add_text_page(doc)
    _add_text_page(doc)
    doc.save(path)
    doc.close()
    return path


def _process(path: Path, *, engine=None):
    """Analyze + route one synthetic PDF (fake engine by default)."""
    source = pymupdf.open(path)
    analysis = analyze_pdf(source)
    results = process_pages(
        source,
        analysis,
        engine=engine if engine is not None else _FakeOCREngine([""]),
    )
    return source, analysis, results


def _select(path: Path, *, explicit: Image | None = None, engine=None):
    """Run the full M7.2 cover-resolution precedence for one PDF."""
    source, analysis, results = _process(path, engine=engine)
    try:
        return select_cover(
            source,
            analysis,
            results,
            layout=extract_page_layout(source),
            explicit=explicit,
        )
    finally:
        source.close()


def _signals(path: Path, *, engine=None):
    source, analysis, results = _process(path, engine=engine)
    try:
        return measure_cover_signals(
            analysis, results, layout=extract_page_layout(source)
        )
    finally:
        source.close()


def _save_pixmap(path: Path, color: tuple[int, int, int]) -> Path:
    """Write a tiny deterministic PNG to ``path``."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4))
    pixmap.set_rect(pixmap.irect, color)
    pixmap.save(str(path))
    return path


def _epub_archive(path: Path) -> zipfile.ZipFile:
    assert Path(path).is_file(), f"EPUB was not written to {path}"
    return zipfile.ZipFile(path)


def _signals_for(
    *,
    page_number: int = 1,
    width: float = PAGE_WIDTH,
    height: float = PAGE_HEIGHT,
    classification: PDFType = PDFType.SCANNED,
    image_count: int = 1,
    image_area_ratio: float = 1.0,
    text_char_count: int = 0,
    text_line_count: int = 0,
    is_blank: bool = False,
    is_toc_like: bool = False,
    is_front_matter_like: bool = False,
) -> CoverPageSignals:
    """Build a synthetic cover signal for pure ``score_cover_page`` tests."""
    return CoverPageSignals(
        page_number=page_number,
        page_width=width,
        page_height=height,
        classification=classification,
        image_count=image_count,
        image_area_ratio=image_area_ratio,
        text_source=TextSource.NATIVE if text_char_count > 0 else None,
        text_char_count=text_char_count,
        text_line_count=text_line_count,
        is_blank=is_blank,
        is_toc_like=is_toc_like,
        is_front_matter_like=is_front_matter_like,
    )


# --------------------------------------------------------------------------- #
# Scoring gates (pure functions of synthetic signals)
# --------------------------------------------------------------------------- #


class TestScoringGates:
    def test_full_page_image_without_text_passes(self) -> None:
        candidate = score_cover_page(
            _signals_for(), document_median_chars=100.0
        )
        # image 40 + early position 30 + portrait 5.
        assert candidate.page_number == 1
        assert candidate.score == 75.0
        assert candidate.eligible is True
        assert candidate.reason == (
            "page 1 is a confident cover candidate (score 75.0)"
        )

    def test_title_like_text_adds_score(self) -> None:
        candidate = score_cover_page(
            _signals_for(text_char_count=24, text_line_count=1),
            document_median_chars=100.0,
        )
        assert candidate.score == 90.0
        assert candidate.eligible is True

    def test_image_coverage_scales_the_dominance_signal(self) -> None:
        half = score_cover_page(
            _signals_for(image_area_ratio=0.5, text_char_count=24, text_line_count=1),
            document_median_chars=100.0,
        )
        full = score_cover_page(
            _signals_for(image_area_ratio=1.0, text_char_count=24, text_line_count=1),
            document_median_chars=100.0,
        )
        # 40 * (0.5 / 0.9) = 22.2 vs a full 40 points.
        assert half.score == round(22.2 + 30.0 + 15.0 + 5.0, 1)
        assert full.score == 90.0
        assert full.score > half.score

    def test_blank_page_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(is_blank=True), document_median_chars=100.0
        )
        assert candidate.score == 0.0
        assert candidate.eligible is False
        assert "blank" in candidate.reason

    def test_page_without_image_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(image_count=0, image_area_ratio=0.0),
            document_median_chars=100.0,
        )
        assert candidate.eligible is False
        assert "not image-dominated" in candidate.reason

    def test_untexted_partial_image_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(image_area_ratio=0.6), document_median_chars=100.0
        )
        assert candidate.eligible is False
        assert "no text and no full-page image" in candidate.reason

    def test_untexted_full_image_needs_book_text_evidence(self) -> None:
        candidate = score_cover_page(
            _signals_for(image_area_ratio=1.0), document_median_chars=0.0
        )
        assert candidate.eligible is False
        assert "the book has no text evidence" in candidate.reason

    def test_table_of_contents_text_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(is_toc_like=True), document_median_chars=100.0
        )
        assert candidate.eligible is False
        assert "table-of-contents-like" in candidate.reason

    def test_copyright_front_matter_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(is_front_matter_like=True),
            document_median_chars=100.0,
        )
        assert candidate.eligible is False
        assert "copyright/title-page front matter" in candidate.reason

    def test_body_text_mass_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(text_char_count=450, text_line_count=16),
            document_median_chars=100.0,
        )
        assert candidate.eligible is False
        assert "body-text-like" in candidate.reason

    def test_too_much_text_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(text_char_count=200, text_line_count=3),
            document_median_chars=100.0,
        )
        assert candidate.eligible is False
        assert "too much text" in candidate.reason

    def test_text_not_sparse_for_the_book_is_rejected(self) -> None:
        candidate = score_cover_page(
            _signals_for(text_char_count=60, text_line_count=1),
            document_median_chars=100.0,
        )
        assert candidate.eligible is False
        assert "too long to be cover text" in candidate.reason

    def test_landscape_page_is_rejected_without_portrait_points(self) -> None:
        portrait = score_cover_page(
            _signals_for(), document_median_chars=100.0
        )
        landscape = score_cover_page(
            _signals_for(width=PAGE_HEIGHT, height=PAGE_WIDTH),
            document_median_chars=100.0,
        )
        assert landscape.score == portrait.score - 5.0

    def test_late_weak_candidate_stays_below_threshold(self) -> None:
        candidate = score_cover_page(
            _signals_for(
                page_number=3, image_area_ratio=0.6, text_char_count=24, text_line_count=1
            ),
            document_median_chars=100.0,
        )
        # 40 * (0.6 / 0.9) + position(3)=8 + title 15 + portrait 5 = 54.7 < 55.
        assert candidate.score == round(26.7 + 8.0 + 15.0 + 5.0, 1)
        assert candidate.score < COVER_CONFIDENCE_THRESHOLD
        assert candidate.eligible is False
        assert "below the confidence threshold" in candidate.reason

    def test_invalid_signals_raise_type_error(self) -> None:
        with pytest.raises(TypeError):
            score_cover_page(None, document_median_chars=100.0)  # type: ignore[arg-type]

    def test_negative_median_raises_value_error(self) -> None:
        with pytest.raises(ValueError):
            score_cover_page(_signals_for(), document_median_chars=-1.0)


# --------------------------------------------------------------------------- #
# Selection rule (pure function of scored candidates)
# --------------------------------------------------------------------------- #


def _candidate(
    page_number: int, score: float, *, eligible: bool = True
) -> CoverCandidate:
    return CoverCandidate(
        page_number=page_number, score=score, eligible=eligible, reason="fixture"
    )


class TestDecision:
    def test_single_eligible_candidate_wins(self) -> None:
        decision = decide_cover_page([_candidate(1, 75.0)])
        assert isinstance(decision, CoverDecision)
        assert decision.page_number == 1
        assert decision.score == 75.0
        assert decision.detected is True

    def test_highest_score_wins_when_lead_is_not_ambiguous(self) -> None:
        decision = decide_cover_page(
            [_candidate(1, 60.0), _candidate(2, 75.0)]
        )
        assert decision.page_number == 2

    def test_lead_of_exactly_the_margin_is_enough(self) -> None:
        decision = decide_cover_page(
            [_candidate(1, 75.0), _candidate(2, 75.0 - COVER_AMBIGUITY_MARGIN)]
        )
        assert decision.page_number == 1

    def test_close_scores_are_ambiguous_and_select_nothing(self) -> None:
        decision = decide_cover_page(
            [_candidate(1, 75.0), _candidate(2, 66.0)]
        )
        assert decision.page_number is None
        assert decision.detected is False
        assert "ambiguous" in decision.reason

    def test_tied_top_scores_are_ambiguous(self) -> None:
        decision = decide_cover_page(
            [_candidate(1, 75.0), _candidate(2, 75.0)]
        )
        assert decision.page_number is None
        assert "too close to choose" in decision.reason

    def test_no_eligible_candidate_produces_no_cover(self) -> None:
        decision = decide_cover_page([_candidate(1, 40.0, eligible=False)])
        assert decision.page_number is None
        assert decision.score == 0.0

    def test_empty_candidates_produce_no_cover(self) -> None:
        decision = decide_cover_page([])
        assert decision.page_number is None

    def test_non_candidate_value_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            decide_cover_page(["not a candidate"])  # type: ignore[list-item]


# --------------------------------------------------------------------------- #
# Precedence and end-to-end detection
# --------------------------------------------------------------------------- #


class TestDetectionPrecedence:
    def test_explicit_cover_wins_and_short_circuits_detection(
        self, tmp_path, monkeypatch
    ) -> None:
        explicit = Image(data=b"\x89PNG\r\n\x1a\n\x00\x01", content_type="image/png")
        import kindle_converter.pdf.cover_detection as cover_detection

        def _must_not_run(*args, **kwargs):  # pragma: no cover - must not run
            raise AssertionError("detection must not run with an explicit cover")

        monkeypatch.setattr(cover_detection, "detect_cover", _must_not_run)
        selection = _select(_cover_first_pdf(tmp_path / "cover.pdf"), explicit=explicit)
        assert selection.source is CoverSelectionSource.EXPLICIT
        assert selection.image is explicit
        assert selection.page_number is None
        assert selection.has_cover is True

    def test_obvious_first_page_cover_is_detected(self, tmp_path) -> None:
        selection = _select(_cover_first_pdf(tmp_path / "cover.pdf"))
        assert selection.source is CoverSelectionSource.AUTOMATIC
        assert selection.page_number == 1
        assert selection.score == 75.0
        assert selection.has_cover is True
        assert selection.image.content_type == "image/png"

    def test_first_page_alone_is_not_enough_when_it_is_body_text(
        self, tmp_path
    ) -> None:
        selection = _select(_title_page_then_cover_pdf(tmp_path / "title.pdf"))
        # Page 2 is the cover; page 1 is rejected as not image-dominated.
        assert selection.source is CoverSelectionSource.AUTOMATIC
        assert selection.page_number == 2
        assert selection.score == 57.0

    def test_mixed_cover_page_is_detected(self, tmp_path) -> None:
        selection = _select(_mixed_cover_pdf(tmp_path / "mixed.pdf"))
        assert selection.source is CoverSelectionSource.AUTOMATIC
        assert selection.page_number == 1

    def test_body_text_document_gets_no_cover(self, tmp_path) -> None:
        selection = _select(_body_text_first_pdf(tmp_path / "body.pdf"))
        assert selection.source is CoverSelectionSource.NONE
        assert selection.page_number is None
        assert selection.has_cover is False

    def test_blank_first_page_gets_no_cover(self, tmp_path) -> None:
        selection = _select(_blank_first_pdf(tmp_path / "blank.pdf"))
        assert selection.source is CoverSelectionSource.NONE
        assert selection.page_number is None

    def test_toc_first_page_gets_no_cover(self, tmp_path) -> None:
        selection = _select(_toc_first_pdf(tmp_path / "toc.pdf"))
        assert selection.source is CoverSelectionSource.NONE
        assert selection.page_number is None

    def test_copyright_front_matter_page_gets_no_cover(self, tmp_path) -> None:
        pdf = _copyright_cover_pdf(tmp_path / "copyright.pdf")
        selection = _select(
            pdf,
            engine=_FakeOCREngine(
                ["Copyright 2001 Example Press. All rights reserved. ISBN 1-2-3"]
            ),
        )
        assert selection.source is CoverSelectionSource.NONE
        assert selection.page_number is None
        # The scanned cover scan carries copyright front matter (via OCR):
        # the measured signal is front-matter-like, so the page is rejected.
        source, analysis, results = _process(
            pdf,
            engine=_FakeOCREngine(
                ["Copyright 2001 Example Press. All rights reserved. ISBN 1-2-3"]
            ),
        )
        try:
            signals = measure_cover_signals(
                analysis, results, layout=extract_page_layout(source)
            )
        finally:
            source.close()
        assert signals[0].is_front_matter_like is True

    def test_ambiguous_candidates_get_no_cover(self, tmp_path) -> None:
        pdf = _ambiguous_cover_pdf(tmp_path / "ambiguous.pdf")
        selection = _select(
            pdf, engine=_FakeOCREngine(["", "Cover Book By An Author"])
        )
        assert selection.source is CoverSelectionSource.NONE
        assert selection.page_number is None
        assert "ambiguous" in selection.reason

    def test_scanned_cover_with_ocr_title_text_is_detected(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "scanned.pdf")
        selection = _select(
            pdf, engine=_FakeOCREngine(["The Great Book By An Author"])
        )
        assert selection.source is CoverSelectionSource.AUTOMATIC
        assert selection.page_number == 1
        assert selection.score == 90.0

    def test_detection_is_deterministic(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "det.pdf")
        first = _select(pdf)
        second = _select(pdf)
        assert first.source is second.source
        assert first.page_number == second.page_number
        assert first.score == second.score
        assert first.reason == second.reason
        assert first.image.data == second.image.data


# --------------------------------------------------------------------------- #
# Cover materialization
# --------------------------------------------------------------------------- #


class TestCoverMaterialization:
    def test_materialize_renders_the_selected_page_as_png(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        cover = materialize_cover_page(pdf, 1)
        assert isinstance(cover, Image)
        assert cover.content_type == "image/png"
        assert cover.data.startswith(b"\x89PNG\r\n\x1a\n")
        decoded = pymupdf.open(stream=cover.data, filetype="png")
        assert decoded.page_count == 1
        decoded.close()

    def test_materialize_matches_the_selection(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        selection = _select(pdf)
        assert selection.page_number == 1
        cover = materialize_cover_page(pdf, selection.page_number)
        assert selection.image.data == cover.data

    def test_invalid_page_number_raises(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        with pytest.raises(ValueError):
            materialize_cover_page(pdf, 0)
        with pytest.raises(ValueError):
            materialize_cover_page(pdf, -1)

    def test_page_beyond_the_document_yields_no_cover(self, tmp_path) -> None:
        pdf = _body_text_first_pdf(tmp_path / "body.pdf")
        assert materialize_cover_page(pdf, 999) is None

    def test_out_of_range_render_dpi_raises(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        with pytest.raises(ValueError):
            materialize_cover_page(pdf, 1, render_dpi=10)
        with pytest.raises(ValueError):
            materialize_cover_page(pdf, 1, render_dpi=700)


# --------------------------------------------------------------------------- #
# Bounded candidate window
# --------------------------------------------------------------------------- #


class TestWindowBoundary:
    def test_detection_measures_at_most_the_bound(self, tmp_path) -> None:
        pdf = _cover_beyond_window_pdf(tmp_path / "six.pdf")
        source, analysis, results = _process(pdf)
        try:
            signals = measure_cover_signals(
                analysis, results, layout=extract_page_layout(source)
            )
            assert len(signals) == COVER_CANDIDATE_WINDOW
            assert [s.page_number for s in signals] == [1, 2, 3, 4, 5]
        finally:
            source.close()

    def test_cover_after_the_bound_is_never_measured(self, tmp_path) -> None:
        pdf = _cover_beyond_window_pdf(tmp_path / "six.pdf")
        source, analysis, results = _process(pdf)
        try:
            signals = measure_cover_signals(
                analysis, results, layout=extract_page_layout(source)
            )
            assert all(s.page_number <= 5 for s in signals)
            assert any(s.is_image_dominated for s in signals) is False
        finally:
            source.close()
        selection = _select(pdf)
        assert selection.source is CoverSelectionSource.NONE

    def test_cover_at_the_last_bound_position_is_detected(self, tmp_path) -> None:
        pdf = _cover_at_page_five_pdf(tmp_path / "five.pdf")
        selection = _select(
            pdf, engine=_FakeOCREngine(["Fifth Page Book Cover"])
        )
        assert selection.source is CoverSelectionSource.AUTOMATIC
        assert selection.page_number == 5

    def test_cover_at_the_bound_position_needs_the_wider_window(
        self, tmp_path
    ) -> None:
        pdf = _cover_at_page_five_pdf(tmp_path / "five.pdf")
        source, analysis, results = _process(
            pdf, engine=_FakeOCREngine(["Fifth Page Book Cover"])
        )
        try:
            layout = extract_page_layout(source)
            narrow = select_cover(source, analysis, results, layout=layout, window=4)
            wide = select_cover(source, analysis, results, layout=layout, window=5)
        finally:
            source.close()
        assert narrow.source is CoverSelectionSource.NONE
        assert wide.source is CoverSelectionSource.AUTOMATIC
        assert wide.page_number == 5

    def test_window_must_be_positive_int(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        source, analysis, results = _process(pdf)
        try:
            with pytest.raises(ValueError):
                measure_cover_signals(
                    analysis, results, layout=extract_page_layout(source), window=0
                )
            with pytest.raises(ValueError):
                measure_cover_signals(
                    analysis, results, layout=extract_page_layout(source), window=True
                )
        finally:
            source.close()


# --------------------------------------------------------------------------- #
# Document-level text reference
# --------------------------------------------------------------------------- #


class TestDocumentMedianTextChars:
    def test_text_free_document_returns_zero(self) -> None:
        from kindle_converter.pdf.processing import PageProcessingResult

        results = [
            PageProcessingResult(
                page_number=index + 1,
                classification=PDFType.SCANNED,
                native_text=None,
                ocr_text="",
            )
            for index in range(3)
        ]
        assert document_median_text_chars(results) == 0.0

    def test_median_over_text_page_lengths(self) -> None:
        from kindle_converter.pdf.processing import PageProcessingResult

        results = [
            PageProcessingResult(
                page_number=index + 1,
                classification=PDFType.TEXT,
                native_text="word" * count,
                ocr_text=None,
            )
            for index, count in enumerate([1, 2, 3])
        ]
        # 4, 8, 12 non-whitespace characters -> median 8.
        assert document_median_text_chars(results) == 8.0

    def test_scanned_pages_are_measured_by_ocr_text(self) -> None:
        from kindle_converter.pdf.processing import PageProcessingResult

        results = [
            PageProcessingResult(
                page_number=1,
                classification=PDFType.SCANNED,
                native_text="hidden native layer ignored",
                ocr_text="words",
            )
        ]
        assert document_median_text_chars(results) == 5.0


# --------------------------------------------------------------------------- #
# CoverSelection value object
# --------------------------------------------------------------------------- #


class TestCoverSelectionFactory:
    def test_explicit_factory(self) -> None:
        from kindle_converter.pdf import CoverSelection

        image = Image(data=b"x", content_type="image/png")
        selection = CoverSelection.explicit(image)
        assert selection.source is CoverSelectionSource.EXPLICIT
        assert selection.has_cover is True
        assert selection.page_number is None

    def test_explicit_factory_rejects_non_image(self) -> None:
        from kindle_converter.pdf import CoverSelection

        with pytest.raises(TypeError):
            CoverSelection.explicit(b"bytes")  # type: ignore[arg-type]

    def test_none_factory(self) -> None:
        from kindle_converter.pdf import CoverSelection

        selection = CoverSelection.none("no evidence")
        assert selection.source is CoverSelectionSource.NONE
        assert selection.has_cover is False
        assert selection.image is None
        assert selection.reason == "no evidence"


# --------------------------------------------------------------------------- #
# EPUB integration (the unchanged M4.4 contract for a detected cover)
# --------------------------------------------------------------------------- #


def _spine_itemrefs(opf: str) -> list[tuple[str, bool]]:
    """(idref, is_linear) pairs in spine order from a raw content.opf."""
    pairs: list[tuple[str, bool]] = []
    for itemref in re.findall(r"<itemref[^>]*?/>", opf):
        idref = re.search(r'idref="([^"]+)"', itemref).group(1)
        pairs.append((idref, 'linear="no"' not in itemref))
    return pairs


def _opf(path: Path) -> str:
    with _epub_archive(path) as archive:
        return archive.read("EPUB/content.opf").decode("utf-8")


class TestAutoCoverEpubContract:
    def test_cover_page_and_image_are_packaged(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        with _epub_archive(out) as archive:
            names = archive.namelist()
            assert f"EPUB/{COVER_PAGE_FILE}" in names
            assert f"EPUB/{COVER_IMAGE_RESOURCE}.png" in names

    def test_cover_image_is_marked_cover_image(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        manifest = ET.fromstring(_opf(out)).find(
            "{http://www.idpf.org/2007/opf}manifest"
        )
        items = {item.get("id"): item for item in manifest if item.tag.endswith("item")}
        cover_item = items[COVER_IMAGE_ID]
        assert cover_item.get("href") == f"{COVER_IMAGE_RESOURCE}.png"
        assert cover_item.get("media-type") == "image/png"
        assert cover_item.get("properties") == "cover-image"

    def test_cover_metadata_points_at_the_cover_image(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        metadata = ET.fromstring(_opf(out)).find(
            "{http://www.idpf.org/2007/opf}metadata"
        )
        covers = [
            element
            for element in metadata
            if element.tag.endswith("meta") and element.get("name") == "cover"
        ]
        assert len(covers) == 1
        assert covers[0].get("content") == COVER_IMAGE_ID

    def test_spine_is_nav_linear_no_cover_linear_chapter(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        assert _spine_itemrefs(_opf(out)) == [
            ("nav", False),
            ("cover", True),
            ("chapter-00", True),
        ]

    def test_cover_is_not_a_navigation_entry(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        with _epub_archive(out) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
            ncx = archive.read("EPUB/toc.ncx").decode("utf-8")
        assert COVER_PAGE_FILE not in nav
        assert "cover.xhtml" not in nav
        assert "cover.xhtml" not in ncx

    def test_cover_machinery_is_emitted_exactly_once(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        with _epub_archive(out) as archive:
            names = archive.namelist()
        assert names.count(f"EPUB/{COVER_PAGE_FILE}") == 1
        assert names.count(f"EPUB/{COVER_IMAGE_RESOURCE}.png") == 1
        assert _spine_itemrefs(_opf(out)).count(("cover", True)) == 1

    def test_detected_cover_epub_passes_structural_validation(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        result = validate_epub(out)
        assert result.valid, result.format_report()
        assert result.error_count == 0

    def test_no_cover_document_produces_coverless_epub(self, tmp_path) -> None:
        pdf = _body_text_first_pdf(tmp_path / "body.pdf")
        out = tmp_path / "body.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        opf = _opf(out)
        assert 'name="cover"' not in opf
        assert "cover-image" not in opf
        with _epub_archive(out) as archive:
            names = archive.namelist()
        assert f"EPUB/{COVER_PAGE_FILE}" not in names
        assert f"EPUB/{COVER_IMAGE_RESOURCE}.png" not in names


# --------------------------------------------------------------------------- #
# Pipeline integration
# --------------------------------------------------------------------------- #


class TestPipelineIntegration:
    def test_detected_cover_reaches_book_from_convert_pdf_to_book(
        self, tmp_path
    ) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        book = convert_pdf_to_book(pdf, engine=_FakeOCREngine([""]))
        assert book.cover is not None
        assert book.cover.content_type == "image/png"
        assert book.cover.data.startswith(b"\x89PNG\r\n\x1a\n")

    def test_body_text_book_keeps_no_cover(self, tmp_path) -> None:
        pdf = _body_text_first_pdf(tmp_path / "body.pdf")
        book = convert_pdf_to_book(pdf, engine=_FakeOCREngine([""]))
        assert book.cover is None

    def test_explicit_cover_is_never_overridden_by_the_pipeline(
        self, tmp_path
    ) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        explicit = Image(data=JPEG_BYTES, content_type="image/jpeg")
        book = convert_pdf_to_book(pdf, engine=_FakeOCREngine([""]), cover=explicit)
        assert book.cover is explicit

    def test_convert_pdf_to_epub_writes_the_detected_cover(self, tmp_path) -> None:
        pdf = _cover_first_pdf(tmp_path / "cover.pdf")
        out = tmp_path / "cover.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine([""]))
        with _epub_archive(out) as archive:
            cover_bytes = archive.read(f"EPUB/{COVER_IMAGE_RESOURCE}.png")
        assert cover_bytes.startswith(b"\x89PNG\r\n\x1a\n")


