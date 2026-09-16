"""Deterministic tests for mixed text/image page routing (Milestone 3.5).

The M3.5 routing layer decides, per page, how the existing capabilities are
used: TEXT pages use native extraction only; SCANNED pages route through
``render -> OCR -> cleanup``; MIXED pages preserve native text *and* keep
OCR text separately. Nothing here requires Tesseract: OCR is exercised
through a fake/injected engine and rendering through a fake/injected
renderer, exactly as the M3.5 dependency-injection design requires.

All PDFs are generated on the fly with PyMuPDF; no internet, external
binaries, fixture files, or downloaded books are involved.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    DEFAULT_RENDER_DPI,
    EmptyPDFError,
    OCRError,
    OCREngineUnavailableError,
    PDFReadError,
    PDFType,
    PageAnalysis,
    PageProcessingResult,
    PDFRenderingError,
    RenderedPage,
    analyze_pdf,
    clean_ocr_text,
    process_page,
    process_pages,
)
from kindle_converter.pdf.layout import PageLayout, extract_page_layout
from kindle_converter.pdf.reconstruction import deduplicate_layout

# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #

WIDTH = 595
HEIGHT = 842

TEXT_LINE = (
    "It was a bright cold day in April, and the clocks "
    "were striking thirteen."
)

#: Page-kind strings accepted by :func:`make_doc`.
KIND_TEXT = "text"
KIND_SCANNED = "scanned"
KIND_MIXED = "mixed"


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=WIDTH, height=HEIGHT)


def _insert_text(page: pymupdf.Page, lines: int = 6) -> None:
    for offset in range(lines):
        page.insert_text((72, 100 + offset * 45), TEXT_LINE)


def _insert_image(page: pymupdf.Page) -> None:
    """Full-page-size image (no text): SCANNED page content."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    pixmap.set_rect(pixmap.irect, (200, 50, 60))
    page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)


def _insert_mixed(page: pymupdf.Page) -> None:
    """Meaningful native text + image coverage >= 50 %: MIXED content."""
    _insert_text(page, lines=4)
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 500, 700))
    pixmap.set_rect(pixmap.irect, (200, 50, 60))
    page.insert_image(pymupdf.Rect(20, 50, 520, 750), pixmap=pixmap)


def make_doc(path, kinds: list[str]) -> str:
    """Create a PDF whose pages follow ``kinds`` in order."""
    doc = pymupdf.open()
    for kind in kinds:
        page = _new_page(doc)
        if kind == KIND_TEXT:
            _insert_text(page)
        elif kind == KIND_SCANNED:
            _insert_image(page)
        elif kind == KIND_MIXED:
            _insert_mixed(page)
        else:  # pragma: no cover - test bug guard
            raise ValueError(f"unknown page kind {kind!r}")
    doc.save(str(path))
    doc.close()
    return str(path)


def native_text(path, page_number: int) -> str:
    """Native text of ``page_number`` via the same M2.1 layout path."""
    layout = deduplicate_layout(extract_page_layout(path))
    return layout.pages[page_number - 1].text


ZERO_PAGE_PDF = b"""\
%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [] /Count 0 >>
endobj
trailer
<< /Root 1 0 R /Size 2 >>
%%EOF
"""

# --------------------------------------------------------------------------- #
# Fakes (deterministic dependency injection)
# --------------------------------------------------------------------------- #


def _pixmap() -> pymupdf.Pixmap:
    """A small valid solid-color RGB pixmap for fake rendered pages."""
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 64, 48))
    pixmap.set_rect(pixmap.irect, (10, 20, 30, 255))
    return pixmap


class _FakeEngine:
    """Fake OCREngine: returns a fixed string and records every image."""

    def __init__(self, text: str = "recognized page text") -> None:
        self.text = text
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        return self.text


class _PerCallEngine:
    """Fake engine returning a predetermined string per OCR call.

    Lets tests distinguish which pages were OCR'd and prove per-page
    plumbing with no cross-page contamination.
    """

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return self.texts[index]


class _RaisingEngine:
    """Fake engine that raises ``exc`` on its first OCR call."""

    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.calls = 0

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls += 1
        raise self.exc


class _FakeRenderer:
    """Fake PageRenderer: records (source, page_number, dpi) per call."""

    def __init__(self, exc: Exception | None = None) -> None:
        self.exc = exc
        self.calls: list[tuple[object, int, int | float]] = []

    def __call__(
        self,
        source: object,
        page_number: int,
        dpi: int | float = DEFAULT_RENDER_DPI,
    ) -> RenderedPage:
        if self.exc is not None:
            raise self.exc
        self.calls.append((source, page_number, dpi))
        return RenderedPage(page_number=page_number, dpi=dpi, image=_pixmap())

    def page_numbers(self) -> list[int]:
        """The 1-based page numbers rendered, in order."""
        return [entry[1] for entry in self.calls]


# --------------------------------------------------------------------------- #
# PageProcessingResult representation
# --------------------------------------------------------------------------- #


class TestPageProcessingResult:
    def test_fields_and_values(self) -> None:
        result = PageProcessingResult(
            page_number=3,
            classification=PDFType.MIXED,
            native_text="native",
            ocr_text="ocr",
        )
        assert result.page_number == 3
        assert result.classification is PDFType.MIXED
        assert result.native_text == "native"
        assert result.ocr_text == "ocr"

    def test_defaults_are_none(self) -> None:
        result = PageProcessingResult(1, PDFType.TEXT)
        assert result.native_text is None
        assert result.ocr_text is None

    def test_frozen_and_slots(self) -> None:
        result = PageProcessingResult(1, PDFType.TEXT)
        with pytest.raises(AttributeError):
            result.page_number = 2  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.ocr_text = "x"  # type: ignore[misc]
        assert PageProcessingResult.__slots__ == (
            "page_number",
            "classification",
            "native_text",
            "ocr_text",
        )

    def test_equality(self) -> None:
        assert PageProcessingResult(1, PDFType.TEXT) == PageProcessingResult(
            1, PDFType.TEXT
        )
        assert PageProcessingResult(1, PDFType.TEXT) != PageProcessingResult(
            2, PDFType.TEXT
        )
        mixed = PageProcessingResult(
            1, PDFType.MIXED, native_text="a", ocr_text="b"
        )
        assert mixed != PageProcessingResult(
            1, PDFType.MIXED, native_text="a", ocr_text="c"
        )

    def test_repr(self) -> None:
        assert "PageProcessingResult" in repr(
            PageProcessingResult(2, PDFType.TEXT)
        )

    def test_none_and_empty_string_are_distinct(self) -> None:
        # None = path not used; "" = path used but produced nothing.
        unused = PageProcessingResult(1, PDFType.SCANNED)
        empty = PageProcessingResult(
            1, PDFType.TEXT, native_text="", ocr_text=""
        )
        assert unused.native_text is None
        assert unused.ocr_text is None
        assert empty.native_text == ""
        assert empty.ocr_text == ""
        assert unused != empty


# --------------------------------------------------------------------------- #
# TEXT page routing
# --------------------------------------------------------------------------- #


class TestTEXTPageRouting:
    def test_native_extraction_occurs(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(),
            renderer=_FakeRenderer(),
        )
        assert result.native_text is not None
        assert result.native_text != ""

    def test_ocr_does_not_occur(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        engine = _FakeEngine()
        process_page(path, analysis.pages[0], engine=engine, renderer=_FakeRenderer())
        assert engine.calls == []

    def test_rendering_does_not_occur(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        renderer = _FakeRenderer()
        process_page(path, analysis.pages[0], engine=_FakeEngine(), renderer=renderer)
        assert renderer.calls == []

    def test_native_text_is_preserved(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(),
            renderer=_FakeRenderer(),
        )
        assert result.native_text == native_text(path, 1)
        assert TEXT_LINE in result.native_text

    def test_classification_is_text(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        assert analysis.pages[0].classification is PDFType.TEXT
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(),
            renderer=_FakeRenderer(),
        )
        assert result.classification is PDFType.TEXT

    def test_no_ocr_or_render_through_process_pages(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        engine = _FakeEngine()
        renderer = _FakeRenderer()
        (result,) = process_pages(path, analysis, engine=engine, renderer=renderer)
        assert result.classification is PDFType.TEXT
        assert result.native_text == native_text(path, 1)
        assert result.ocr_text is None
        assert engine.calls == []
        assert renderer.calls == []


# --------------------------------------------------------------------------- #
# SCANNED page routing
# --------------------------------------------------------------------------- #


class TestScannedPageRouting:
    def test_rendering_occurs(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        renderer = _FakeRenderer()
        process_page(path, analysis.pages[0], engine=_FakeEngine(), renderer=renderer)
        assert renderer.page_numbers() == [1]

    def test_renderer_receives_the_open_document(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        doc = pymupdf.open(path)
        try:
            analysis = analyze_pdf(doc)
            renderer = _FakeRenderer()
            process_page(doc, analysis.pages[0], engine=_FakeEngine(), renderer=renderer)
            assert len(renderer.calls) == 1
            assert isinstance(renderer.calls[0][0], pymupdf.Document)
            assert not doc.is_closed
        finally:
            doc.close()

    def test_ocr_occurs(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        engine = _FakeEngine()
        result = process_page(
            path, analysis.pages[0], engine=engine, renderer=_FakeRenderer()
        )
        assert len(engine.calls) == 1
        assert result.ocr_text == "recognized page text"

    def test_ocr_cleanup_occurs(self, tmp_path) -> None:
        dirty = "Hello\r\nworld\x00\n\n\nSecond paragraph"
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text=dirty),
            renderer=_FakeRenderer(),
        )
        assert result.ocr_text == "Hello\nworld\n\n\nSecond paragraph"
        assert result.ocr_text == clean_ocr_text(dirty)

    def test_native_text_is_not_required(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        result = process_page(
            path, analysis.pages[0], engine=_FakeEngine(), renderer=_FakeRenderer()
        )
        assert result.native_text is None

    def test_classification_is_scanned(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        assert analysis.pages[0].classification is PDFType.SCANNED
        result = process_page(
            path, analysis.pages[0], engine=_FakeEngine(), renderer=_FakeRenderer()
        )
        assert result.classification is PDFType.SCANNED

    def test_empty_ocr_text_is_not_reinterpreted_as_native(self, tmp_path) -> None:
        # OCR runs but yields empty text: the page stays SCANNED with an
        # empty ocr_text, never silently turned into a native-text page.
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text=""),
            renderer=_FakeRenderer(),
        )
        assert result.classification is PDFType.SCANNED
        assert result.native_text is None
        assert result.ocr_text == ""


# --------------------------------------------------------------------------- #
# MIXED page routing
# --------------------------------------------------------------------------- #


class TestMixedPageRouting:
    def test_native_text_is_preserved(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        assert analysis.pages[0].classification is PDFType.MIXED
        result = process_page(
            path, analysis.pages[0], engine=_FakeEngine(), renderer=_FakeRenderer()
        )
        assert result.native_text == native_text(path, 1)
        assert result.native_text != ""

    def test_mixed_page_is_ocrd_yields_ocr_text(self, tmp_path) -> None:
        # M3.5 policy: full-page OCR is performed on MIXED pages so
        # image-only content can be captured; native text is kept too.
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        engine = _FakeEngine(text="ocr-of-mixed")
        result = process_page(
            path, analysis.pages[0], engine=engine, renderer=_FakeRenderer()
        )
        assert len(engine.calls) == 1
        assert result.ocr_text == "ocr-of-mixed"

    def test_native_and_ocr_text_remain_distinguishable(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text="ocr-of-mixed"),
            renderer=_FakeRenderer(),
        )
        assert result.native_text != result.ocr_text
        assert result.native_text is not None
        assert result.ocr_text is not None

    def test_no_destructive_concatenation(self, tmp_path) -> None:
        # The result must NOT contain a merged single text blob: native and
        # OCR text stay separate fields on the immutable result.
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text="ocr-of-mixed"),
            renderer=_FakeRenderer(),
        )
        assert not hasattr(result, "text")
        assert result.native_text != result.ocr_text
        # Neither field contains both sources.
        assert "ocr-of-mixed" not in result.native_text
        assert result.native_text != result.native_text + result.ocr_text

    def test_classification_is_mixed(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        result = process_page(
            path, analysis.pages[0], engine=_FakeEngine(), renderer=_FakeRenderer()
        )
        assert result.classification is PDFType.MIXED

    def test_mixed_ocr_output_passes_through_cleanup(self, tmp_path) -> None:
        dirty = "image caption  \r\n\t\n\x00\n\nrest"
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text=dirty),
            renderer=_FakeRenderer(),
        )
        assert result.ocr_text == clean_ocr_text(dirty)


# --------------------------------------------------------------------------- #
# Multi-page routing (order, path, call counts, no contamination)
# --------------------------------------------------------------------------- #


SEQUENCE = [KIND_TEXT, KIND_SCANNED, KIND_MIXED, KIND_TEXT, KIND_SCANNED]
SEQUENCE_EXPECTED = [
    PDFType.TEXT,
    PDFType.SCANNED,
    PDFType.MIXED,
    PDFType.TEXT,
    PDFType.SCANNED,
]


class TestMultiPageRouting:
    def test_exact_page_count_and_numbers(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        results = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["o2", "o3", "o5"]),
            renderer=_FakeRenderer(),
        )
        assert len(results) == 5
        assert [r.page_number for r in results] == [1, 2, 3, 4, 5]

    def test_classifications_follow_each_page(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        assert [p.classification for p in analysis.pages] == SEQUENCE_EXPECTED
        results = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["o2", "o3", "o5"]),
            renderer=_FakeRenderer(),
        )
        assert [r.classification for r in results] == SEQUENCE_EXPECTED

    def test_native_text_present_on_text_and_mixed_only(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        results = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["o2", "o3", "o5"]),
            renderer=_FakeRenderer(),
        )
        assert [r.native_text is not None for r in results] == [
            True,  # page 1 TEXT
            False,  # page 2 SCANNED
            True,  # page 3 MIXED
            True,  # page 4 TEXT
            False,  # page 5 SCANNED
        ]
        assert [r.native_text for r in results][0] == native_text(path, 1)
        assert [r.native_text for r in results][2] == native_text(path, 3)

    def test_ocr_text_present_on_scanned_and_mixed_only(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        results = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["o2", "o3", "o5"]),
            renderer=_FakeRenderer(),
        )
        assert [r.ocr_text for r in results] == [
            None,  # page 1 TEXT
            "o2",  # page 2 SCANNED
            "o3",  # page 3 MIXED
            None,  # page 4 TEXT
            "o5",  # page 5 SCANNED
        ]

    def test_ocr_invocation_count(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        engine = _PerCallEngine(["o2", "o3", "o5"])
        process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert len(engine.calls) == 3  # pages 2, 3, 5

    def test_render_invocation_count(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        renderer = _FakeRenderer()
        process_pages(
            path, analysis, engine=_PerCallEngine(["a", "b", "c"]), renderer=renderer
        )
        assert renderer.page_numbers() == [2, 3, 5]

    def test_no_cross_page_contamination(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        engine = _PerCallEngine(["page2-ocr", "page3-ocr", "page5-ocr"])
        results = process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        # Every OCR result is bound to its own page; nothing bleeds out.
        assert results[1].ocr_text == "page2-ocr"
        assert results[2].ocr_text == "page3-ocr"
        assert results[4].ocr_text == "page5-ocr"
        assert results[0].ocr_text is None
        assert results[3].ocr_text is None
        # Per-page native text is distinct and stays with its own page.
        assert results[0].native_text != results[2].native_text
        assert results[2].native_text != results[3].native_text

    def test_page_order_never_reordered(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        results = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["a", "b", "c"]),
            renderer=_FakeRenderer(),
        )
        assert [r.page_number for r in results] == list(range(1, 6))


# --------------------------------------------------------------------------- #
# Call-count policies (classification-driven)
# --------------------------------------------------------------------------- #


class TestCallCountPolicies:
    def test_text_text_text_uses_no_ocr_and_no_render(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t3.pdf", [KIND_TEXT] * 3)
        analysis = analyze_pdf(path)
        engine = _FakeEngine()
        renderer = _FakeRenderer()
        results = process_pages(path, analysis, engine=engine, renderer=renderer)
        assert len(results) == 3
        assert all(r.ocr_text is None for r in results)
        assert all(r.native_text is not None for r in results)
        assert engine.calls == []
        assert renderer.calls == []

    def test_scanned_scanned_uses_ocr_and_render_per_page(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s2.pdf", [KIND_SCANNED] * 2)
        analysis = analyze_pdf(path)
        engine = _FakeEngine()
        renderer = _FakeRenderer()
        results = process_pages(path, analysis, engine=engine, renderer=renderer)
        assert len(engine.calls) == 2
        assert renderer.page_numbers() == [1, 2]
        assert all(r.native_text is None for r in results)
        assert all(r.ocr_text == "recognized page text" for r in results)

    def test_mixed_pages_use_ocr_per_page_policy(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m2.pdf", [KIND_MIXED] * 2)
        analysis = analyze_pdf(path)
        engine = _FakeEngine()
        renderer = _FakeRenderer()
        results = process_pages(path, analysis, engine=engine, renderer=renderer)
        assert len(engine.calls) == 2
        assert renderer.page_numbers() == [1, 2]
        for result in results:
            assert result.classification is PDFType.MIXED
            assert result.native_text is not None
            assert result.ocr_text == "recognized page text"


# --------------------------------------------------------------------------- #
# Cleanup integration (M3.4 reuse; routing just threads it through)
# --------------------------------------------------------------------------- #


DIRTY_TEXT = "Hello\r\nworld\x00\n\n\nSecond paragraph"
CLEAN_EXPECTED = "Hello\nworld\n\n\nSecond paragraph"


class TestCleanupIntegration:
    def test_scanned_page_ocr_output_is_cleaned(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text=DIRTY_TEXT),
            renderer=_FakeRenderer(),
        )
        assert result.ocr_text == CLEAN_EXPECTED
        assert result.ocr_text == clean_ocr_text(DIRTY_TEXT)

    def test_pages_cleaned_independently(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s2.pdf", [KIND_SCANNED] * 2)
        analysis = analyze_pdf(path)
        engine = _PerCallEngine([DIRTY_TEXT, "clean second"])
        results = process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert results[0].ocr_text == CLEAN_EXPECTED
        assert results[1].ocr_text == "clean second"

    def test_end_to_end_with_real_renderer(self, tmp_path) -> None:
        # The full M3.5 chain, real M3.2 renderer + fake engine:
        # render -> ocr -> cleanup, no fake renderer involved.
        path = make_doc(tmp_path / "real.pdf", [KIND_SCANNED, KIND_TEXT])
        analysis = analyze_pdf(path)
        results = process_pages(
            path, analysis, engine=_FakeEngine(text=DIRTY_TEXT)
        )
        assert results[0].classification is PDFType.SCANNED
        assert results[0].native_text is None
        assert results[0].ocr_text == CLEAN_EXPECTED
        assert results[1].classification is PDFType.TEXT
        assert results[1].ocr_text is None
        assert results[1].native_text == native_text(path, 2)

    def test_mixed_page_ocr_output_is_cleaned(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        result = process_page(
            path,
            analysis.pages[0],
            engine=_FakeEngine(text=DIRTY_TEXT),
            renderer=_FakeRenderer(),
        )
        assert result.ocr_text == CLEAN_EXPECTED


# --------------------------------------------------------------------------- #
# Error propagation (nothing is silently swallowed)
# --------------------------------------------------------------------------- #


class TestErrorPropagation:
    def test_ocr_engine_unavailable_propagates(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        engine = _RaisingEngine(OCREngineUnavailableError("no binary"))
        with pytest.raises(OCREngineUnavailableError) as excinfo:
            process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert "page 1" in str(excinfo.value)

    def test_ocr_failure_gets_page_context(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s2.pdf", [KIND_SCANNED] * 2)
        analysis = analyze_pdf(path)

        class _LateRaisingEngine:
            def __init__(self) -> None:
                self.calls = 0

            def recognize(self, image: pymupdf.Pixmap) -> str:
                self.calls += 1
                if self.calls == 2:
                    raise OCRError("bad page")
                return "ok"

        engine = _LateRaisingEngine()
        with pytest.raises(OCRError, match="page 2") as excinfo:
            process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert "bad page" in str(excinfo.value)

    def test_failure_stops_processing_not_skipped(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s3.pdf", [KIND_SCANNED] * 3)
        analysis = analyze_pdf(path)
        engine = _RaisingEngine(OCRError("fail"))
        with pytest.raises(OCRError):
            process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        # Page 1 failed; pages 2 and 3 were never OCR'd.
        assert engine.calls == 1

    def test_unknown_exception_propagates_unchanged(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        engine = _RaisingEngine(RuntimeError("raw crash"))
        with pytest.raises(RuntimeError) as excinfo:
            process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert type(excinfo.value) is RuntimeError

    def test_render_failure_propagates(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        analysis = analyze_pdf(path)
        renderer = _FakeRenderer(exc=PDFRenderingError("cannot rasterize"))
        with pytest.raises(PDFRenderingError, match="cannot rasterize"):
            process_pages(path, analysis, engine=_FakeEngine(), renderer=renderer)

    def test_mixed_page_ocr_failure_propagates(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        engine = _RaisingEngine(OCRError("mixed boom"))
        with pytest.raises(OCRError, match="page 1") as excinfo:
            process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert "mixed boom" in str(excinfo.value)

    def test_text_pages_never_trigger_ocr_errors(self, tmp_path) -> None:
        # A broken engine on a TEXT-only document is never invoked.
        path = make_doc(tmp_path / "t2.pdf", [KIND_TEXT] * 2)
        analysis = analyze_pdf(path)
        engine = _RaisingEngine(OCRError("would have failed"))
        results = process_pages(path, analysis, engine=engine, renderer=_FakeRenderer())
        assert engine.calls == 0
        assert all(r.classification is PDFType.TEXT for r in results)

    def test_invalid_page_number_rejected(self, tmp_path) -> None:
        path = make_doc(tmp_path / "one.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        for bad_number in (0, 2, 99):
            bad = PageAnalysis(
                page_number=bad_number,
                has_image=False,
                has_meaningful_text=True,
                char_count=50,
            )
            with pytest.raises(ValueError):
                process_page(path, bad, engine=_FakeEngine())

    def test_non_pageanalysis_rejected(self, tmp_path) -> None:
        path = make_doc(tmp_path / "one.pdf", [KIND_TEXT])
        with pytest.raises(TypeError):
            process_page(path, object(), engine=_FakeEngine())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            process_page(path, None, engine=_FakeEngine())  # type: ignore[arg-type]

    def test_non_pdfanalysis_rejected(self, tmp_path) -> None:
        path = make_doc(tmp_path / "one.pdf", [KIND_TEXT])
        with pytest.raises(TypeError):
            process_pages(path, object(), engine=_FakeEngine())  # type: ignore[arg-type]

    def test_invalid_engine_rejected_eagerly(self, tmp_path) -> None:
        # Even a TEXT-only document rejects a non-engine eagerly: the
        # routing API requires a valid injectable engine by contract.
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        with pytest.raises(TypeError):
            process_pages(path, analysis, engine=object())  # type: ignore[arg-type]
        with pytest.raises(TypeError):
            process_pages(path, analysis, engine=None)  # type: ignore[arg-type]

    def test_invalid_renderer_rejected(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        with pytest.raises(TypeError):
            process_pages(
                path, analysis, engine=_FakeEngine(), renderer="not callable"  # type: ignore[arg-type]
            )

    def test_invalid_dpi_rejected(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        for bad_dpi in (0, -100, 10000, "high", True):
            with pytest.raises(ValueError):
                process_pages(
                    path, analysis, engine=_FakeEngine(), dpi=bad_dpi  # type: ignore[arg-type]
                )

    def test_invalid_layout_rejected(self, tmp_path) -> None:
        path = make_doc(tmp_path / "t.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        with pytest.raises(TypeError):
            process_pages(
                path, analysis, engine=_FakeEngine(), layout=object()  # type: ignore[arg-type]
            )

    def test_layout_page_count_mismatch_rejected(self, tmp_path) -> None:
        path3 = make_doc(tmp_path / "three.pdf", [KIND_TEXT] * 3)
        path1 = make_doc(tmp_path / "one.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path3)
        small_layout = deduplicate_layout(extract_page_layout(path1))
        with pytest.raises(ValueError, match="layout has"):
            process_pages(
                path3,
                analysis,
                engine=_FakeEngine(),
                renderer=_FakeRenderer(),
                layout=small_layout,
            )

    def test_analysis_page_count_mismatch_rejected(self, tmp_path) -> None:
        from kindle_converter.pdf.models import PDFAnalysis

        path = make_doc(tmp_path / "d.pdf", [KIND_TEXT, KIND_SCANNED])
        inconsistent = PDFAnalysis(
            page_count=3,
            text_page_count=1,
            image_page_count=1,
            text_density=40.0,
            document_type=PDFType.MIXED,
            pages=[analyze_pdf(path).pages[0]],
        )
        with pytest.raises(ValueError):
            process_pages(path, inconsistent, engine=_FakeEngine())

    def test_zero_page_analysis_rejected(self, tmp_path) -> None:
        from kindle_converter.pdf.models import PDFAnalysis

        path = make_doc(tmp_path / "d.pdf", [KIND_TEXT])
        empty = PDFAnalysis(
            page_count=0,
            text_page_count=0,
            image_page_count=0,
            text_density=0.0,
            document_type=PDFType.SCANNED,
            pages=[],
        )
        with pytest.raises(ValueError, match="at least one page"):
            process_pages(path, empty, engine=_FakeEngine())

    def test_document_page_count_mismatch_rejected(self, tmp_path) -> None:
        path3 = make_doc(tmp_path / "three.pdf", [KIND_TEXT] * 3)
        path1 = make_doc(tmp_path / "one.pdf", [KIND_TEXT])
        analysis1 = analyze_pdf(path1)
        with pytest.raises(ValueError, match="analysis describes .+ pages but the document has"):
            process_pages(path3, analysis1, engine=_FakeEngine())

    def test_empty_pdf_rejected(self, tmp_path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(ZERO_PAGE_PDF)
        from kindle_converter.pdf.models import PDFAnalysis

        analysis = PDFAnalysis(
            page_count=1,
            text_page_count=0,
            image_page_count=0,
            text_density=0.0,
            document_type=PDFType.SCANNED,
            pages=[
                PageAnalysis(
                    page_number=1,
                    has_image=False,
                    has_meaningful_text=False,
                    char_count=0,
                    classification=PDFType.SCANNED,
                )
            ],
        )
        with pytest.raises(EmptyPDFError):
            process_pages(path, analysis, engine=_FakeEngine())

    def test_missing_file_raises_pdf_read_error(self, tmp_path) -> None:
        analysis = analyze_pdf(make_doc(tmp_path / "d.pdf", [KIND_TEXT]))
        with pytest.raises(PDFReadError):
            process_pages(tmp_path / "missing.pdf", analysis, engine=_FakeEngine())


# --------------------------------------------------------------------------- #
# Immutability
# --------------------------------------------------------------------------- #


class TestImmutability:
    def test_result_fields_are_frozen(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)
        result = process_page(
            path, analysis.pages[0], engine=_FakeEngine(), renderer=_FakeRenderer()
        )
        with pytest.raises(AttributeError):
            result.native_text = "changed"  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.ocr_text = "changed"  # type: ignore[misc]

    def test_analysis_is_not_modified(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        before = analyze_pdf(path)
        snapshot = list(before.pages)
        results = process_pages(
            path,
            before,
            engine=_PerCallEngine(["a", "b", "c"]),
            renderer=_FakeRenderer(),
        )
        assert [r.page_number for r in results] == [p.page_number for p in before.pages]
        assert before.pages == snapshot
        assert analyze_pdf(path) == before

    def test_page_analysis_is_not_modified(self, tmp_path) -> None:
        path = make_doc(tmp_path / "text.pdf", [KIND_TEXT])
        analysis = analyze_pdf(path)
        page_analysis = analysis.pages[0]
        before = (page_analysis.page_number, page_analysis.classification)
        process_page(path, page_analysis, engine=_FakeEngine(), renderer=_FakeRenderer())
        assert (page_analysis.page_number, page_analysis.classification) == before


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_two_multi_page_runs_are_identical(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)

        def run() -> list[PageProcessingResult]:
            return process_pages(
                path,
                analysis,
                engine=_PerCallEngine(["a", "b", "c"]),
                renderer=_FakeRenderer(),
            )

        first = run()
        second = run()
        assert first == second
        assert [repr(r) for r in first] == [repr(r) for r in second]
        assert [r.page_number for r in first] == [r.page_number for r in second]
        assert [r.classification for r in first] == [
            r.classification for r in second
        ]

    def test_two_single_page_runs_are_identical(self, tmp_path) -> None:
        path = make_doc(tmp_path / "m.pdf", [KIND_MIXED])
        analysis = analyze_pdf(path)

        def run() -> PageProcessingResult:
            return process_page(
                path,
                analysis.pages[0],
                engine=_FakeEngine(text="ocr"),
                renderer=_FakeRenderer(),
            )

        assert run() == run()

    def test_prebuilt_layout_gives_identical_results(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        analysis = analyze_pdf(path)
        layout = deduplicate_layout(extract_page_layout(path))
        expected = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["a", "b", "c"]),
            renderer=_FakeRenderer(),
        )
        with_layout = process_pages(
            path,
            analysis,
            engine=_PerCallEngine(["a", "b", "c"]),
            renderer=_FakeRenderer(),
            layout=layout,
        )
        assert with_layout == expected


# --------------------------------------------------------------------------- #
# Resource ownership
# --------------------------------------------------------------------------- #


class TestResourceOwnership:
    def test_open_document_stays_open_and_unmutated(self, tmp_path) -> None:
        path = make_doc(tmp_path / "seq.pdf", SEQUENCE)
        doc = pymupdf.open(path)
        try:
            analysis = analyze_pdf(doc)
            results = process_pages(
                doc,
                analysis,
                engine=_PerCallEngine(["a", "b", "c"]),
                renderer=_FakeRenderer(),
            )
            assert [r.page_number for r in results] == [1, 2, 3, 4, 5]
            assert doc.page_count == 5
            assert not doc.is_closed
        finally:
            doc.close()

    def test_open_document_stays_open_for_single_page(self, tmp_path) -> None:
        path = make_doc(tmp_path / "s.pdf", [KIND_SCANNED])
        doc = pymupdf.open(path)
        try:
            analysis = analyze_pdf(doc)
            result = process_page(
                doc, analysis.pages[0], engine=_FakeEngine(), renderer=_FakeRenderer()
            )
            assert result.page_number == 1
            assert not doc.is_closed
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# Public API surface
# --------------------------------------------------------------------------- #


class TestPublicAPI:
    def test_routing_api_is_exported(self) -> None:
        from kindle_converter.pdf import (
            PageProcessingResult as ExportedResult,
            PageRenderer as ExportedRenderer,
            process_page as exported_process_page,
            process_pages as exported_process_pages,
        )

        assert ExportedResult is PageProcessingResult
        assert ExportedRenderer is not None
        assert exported_process_page is process_page
        assert exported_process_pages is process_pages

    def test_internal_helpers_are_not_exported(self) -> None:
        import kindle_converter.pdf as pdf_api
        from kindle_converter.pdf import processing as processing_module

        for name in (
            "_route_page",
            "_extract_layout",
            "_page_native_text",
            "_validate_analysis",
            "_validate_page_analysis",
            "_validate_page_number",
            "_validate_engine",
            "_resolve_renderer",
            "_validate_layout",
            "_validate_dpi",
            "_open_document",
            "_close_if_owned",
        ):
            assert name not in pdf_api.__all__
            assert hasattr(processing_module, name)

    def test_prior_exports_still_present(self) -> None:
        import kindle_converter.pdf as pdf_api

        for name in (
            "PDFType",
            "PageAnalysis",
            "PDFAnalysis",
            "analyze_pdf",
            "classify_pages",
            "render_page",
            "RenderedPage",
            "ocr_page",
            "OCRResult",
            "OCREngine",
            "clean_ocr_result",
            "CleanedOCRResult",
            "extract_book",
        ):
            assert name in pdf_api.__all__
            assert hasattr(pdf_api, name)

    def test_routing_module_imports_without_ocr_extra(self) -> None:
        import kindle_converter.pdf.processing  # noqa: F401

        assert True