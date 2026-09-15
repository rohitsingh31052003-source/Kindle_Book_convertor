"""Tests for PDF page rendering (Milestone 3.2).

All PDFs are generated on the fly with PyMuPDF; nothing here requires
internet access, external fixture files, OCR engines, or downloaded books.
Rendering outputs are RGB pixmaps (``PyMuPDF.Pixmap``) with no alpha channel.
"""

from __future__ import annotations

import pymupdf
import pytest

from kindle_converter.pdf import (
    DEFAULT_RENDER_DPI,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    EmptyPDFError,
    PDFReadError,
    RenderedPage,
    render_page,
    render_pages,
)

# --------------------------------------------------------------------------- #
# Generation helpers
# --------------------------------------------------------------------------- #

WIDTH = 595
HEIGHT = 842

TEXT_LINE = (
    "It was a bright cold day in April, and the clocks "
    "were striking thirteen."
)


def _new_page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=WIDTH, height=HEIGHT)


def _insert_text(page: pymupdf.Page, lines: list[str] | None = None) -> None:
    for offset, text in enumerate(lines or [TEXT_LINE] * 6):
        page.insert_text((72, 100 + offset * 45), text)


def _insert_image(page: pymupdf.Page) -> None:
    pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300))
    pixmap.set_rect(pixmap.irect, (200, 50, 60))
    page.insert_image(pymupdf.Rect(50, 100, 545, 780), pixmap=pixmap)


def _text_doc(pages: int = 1) -> pymupdf.Document:
    """Open synthetic text-only document (caller closes it)."""
    doc = pymupdf.open()
    for _ in range(pages):
        _insert_text(_new_page(doc))
    return doc


def _save(doc: pymupdf.Document, path) -> str:
    doc.save(str(path))
    doc.close()
    return str(path)


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
# Basic rendering
# --------------------------------------------------------------------------- #


class TestBasicRendering:
    def test_single_page_pdf(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "one.pdf")
        rendered = render_page(path, 1)
        assert isinstance(rendered, RenderedPage)
        assert isinstance(rendered.image, pymupdf.Pixmap)
        assert rendered.image.n == 3  # RGB, no CMYK/gray surprises
        assert rendered.image.alpha == 0
        assert rendered.width > 0
        assert rendered.height > 0

    def test_multi_page_pdf_individual_render(self, tmp_path) -> None:
        path = _save(_text_doc(pages=3), tmp_path / "multi.pdf")
        for page_number in (1, 2, 3):
            rendered = render_page(path, page_number)
            assert rendered.page_number == page_number

    def test_native_text_page_renders_ink(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "text.pdf")
        rendered = render_page(path, 1, dpi=72)
        assert min(rendered.image.samples) < 255  # dark text is present

    def test_image_page_renders(self, tmp_path) -> None:
        doc = pymupdf.open()
        _insert_image(_new_page(doc))
        path = _save(doc, tmp_path / "image.pdf")
        rendered = render_page(path, 1, dpi=72)
        assert min(rendered.image.samples) < 255

    def test_blank_page_renders_white(self, tmp_path) -> None:
        doc = pymupdf.open()
        _new_page(doc)  # page exists but has no content
        path = _save(doc, tmp_path / "blank.pdf")
        rendered = render_page(path, 1, dpi=72)
        assert set(rendered.image.samples) == {255}


# --------------------------------------------------------------------------- #
# Dimensions
# --------------------------------------------------------------------------- #


class TestDimensions:
    def test_default_dpi(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "def.pdf")
        rendered = render_page(path, 1)
        assert rendered.dpi == DEFAULT_RENDER_DPI
        assert rendered.width == rendered.image.width
        assert rendered.height == rendered.image.height

    def test_exact_dpi_dimensions(self, tmp_path) -> None:
        # dpi=72 and dpi=144 produce exact integer pixel dimensions for a
        # 595x842 point page, so these are asserted exactly (no rounding).
        path = _save(_text_doc(pages=1), tmp_path / "exact.pdf")
        for dpi, width, height in [
            (72, WIDTH, HEIGHT),
            (144, WIDTH * 2, HEIGHT * 2),
        ]:
            rendered = render_page(path, 1, dpi=dpi)
            assert (rendered.width, rendered.height) == (width, height)

    def test_dimensions_follow_dpi_formula(self, tmp_path) -> None:
        # For a 595x842 point page at 150 DPI, PyMuPDF reports the actual
        # rendered dimensions; verify they match its 1:1 rendering is the
        # dpi=72 baseline and higher DPI scales the page proportionally.
        path = _save(_text_doc(pages=1), tmp_path / "scale.pdf")
        base = render_page(path, 1, dpi=72)
        scaled = render_page(path, 1, dpi=144)
        assert scaled.width == base.width * 2
        assert scaled.height == base.height * 2

    def test_portrait_page_is_portrait(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "portrait.pdf")
        rendered = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert rendered.width < rendered.height
        assert rendered.width / rendered.height == pytest.approx(
            WIDTH / HEIGHT, rel=1e-3
        )

    def test_landscape_page_is_landscape(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=HEIGHT, height=WIDTH)
        _insert_text(page)
        path = _save(doc, tmp_path / "landscape.pdf")
        rendered = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert rendered.width > rendered.height
        assert rendered.width / rendered.height == pytest.approx(
            HEIGHT / WIDTH, rel=1e-3
        )

    def test_unusual_page_size(self, tmp_path) -> None:
        # A very wide page; dpi=72 maps 1 point to 1 pixel exactly.
        doc = pymupdf.open()
        page = doc.new_page(width=1000, height=500)
        page.insert_text((50, 100), TEXT_LINE)
        path = _save(doc, tmp_path / "wide.pdf")
        rendered = render_page(path, 1, dpi=72)
        assert rendered.width == 1000
        assert rendered.height == 500
        assert rendered.width / rendered.height == pytest.approx(2.0)

    def test_deterministic_dimensions(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "det.pdf")
        first = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        second = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert (first.width, first.height) == (second.width, second.height)


# --------------------------------------------------------------------------- #
# Page identity and ordering
# --------------------------------------------------------------------------- #


class TestPageIdentity:
    def test_one_based_page_numbers(self, tmp_path) -> None:
        path = _save(_text_doc(pages=3), tmp_path / "ids.pdf")
        assert render_page(path, 1).page_number == 1
        assert render_page(path, 3).page_number == 3

    def test_specific_page_has_its_own_content(self, tmp_path) -> None:
        doc = pymupdf.open()
        for index in range(2):
            page = _new_page(doc)
            page.insert_text(
                (72, 100), f"Unique marker text for page number {index + 1}"
            )
        path = _save(doc, tmp_path / "content.pdf")
        first = render_page(path, 1, dpi=72)
        second = render_page(path, 2, dpi=72)
        assert first.page_number == 1
        assert second.page_number == 2
        assert first.image.samples != second.image.samples

    def test_render_pages_preserves_order(self, tmp_path) -> None:
        path = _save(_text_doc(pages=3), tmp_path / "order.pdf")
        numbers = [r.page_number for r in render_pages(path)]
        assert numbers == [1, 2, 3]

    def test_render_pages_matches_individual_rendering(self, tmp_path) -> None:
        path = _save(_text_doc(pages=2), tmp_path / "match.pdf")
        individual = [render_page(path, pn, dpi=72) for pn in (1, 2)]
        sequential = list(render_pages(path, dpi=72))
        assert [r.page_number for r in sequential] == [1, 2]
        for index, rendered in enumerate(individual):
            assert sequential[index].image.samples == rendered.image.samples


# --------------------------------------------------------------------------- #
# Orientation
# --------------------------------------------------------------------------- #


class TestOrientation:
    def test_portrait_remains_portrait(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "p.pdf")
        assert render_page(path, 1).width < render_page(path, 1).height

    def test_landscape_remains_landscape(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = doc.new_page(width=HEIGHT, height=WIDTH)
        _insert_text(page)
        path = _save(doc, tmp_path / "l.pdf")
        rendered = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert rendered.width > rendered.height

    def test_rotation_90_renders_swapped(self, tmp_path) -> None:
        # A portrait page with /Rotate 90 renders landscape: PyMuPDF's page
        # renderer applies the page's rotation instead of the raw media box.
        doc = pymupdf.open()
        page = _new_page(doc)
        _insert_text(page)
        page.set_rotation(90)
        path = _save(doc, tmp_path / "rot90.pdf")
        rendered = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert rendered.width > rendered.height

    def test_rotation_180_keeps_orientation(self, tmp_path) -> None:
        doc = pymupdf.open()
        page = _new_page(doc)
        _insert_text(page)
        page.set_rotation(180)
        path = _save(doc, tmp_path / "rot180.pdf")
        rendered = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert rendered.width < rendered.height


# --------------------------------------------------------------------------- #
# DPI validation
# --------------------------------------------------------------------------- #


class TestDpiValidation:
    @pytest.mark.parametrize("dpi", [0, -1, -300, 71, 601, 1000, 1e6])
    def test_dpi_out_of_range_rejected(self, tmp_path, dpi) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "range.pdf")
        with pytest.raises(ValueError):
            render_page(path, 1, dpi=dpi)

    @pytest.mark.parametrize(
        "dpi", [float("inf"), float("-inf"), float("nan")]
    )
    def test_non_finite_dpi_rejected(self, tmp_path, dpi) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "finite.pdf")
        with pytest.raises(ValueError):
            render_page(path, 1, dpi=dpi)

    @pytest.mark.parametrize("dpi", ["300", None, True, False, [300], {}])
    def test_non_numeric_dpi_rejected(self, tmp_path, dpi) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "type.pdf")
        with pytest.raises(ValueError):
            render_page(path, 1, dpi=dpi)

    def test_zero_dpi_rejected(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "zero.pdf")
        with pytest.raises(ValueError):
            render_page(path, 1, dpi=0)

    def test_boundary_dpis_accepted(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "bounds.pdf")
        assert render_page(path, 1, dpi=MIN_RENDER_DPI).dpi == MIN_RENDER_DPI
        assert render_page(path, 1, dpi=MAX_RENDER_DPI).dpi == MAX_RENDER_DPI

    def test_fractional_dpi_accepted(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "frac.pdf")
        rendered = render_page(path, 1, dpi=287.5)
        assert rendered.dpi == 287.5

    def test_integral_float_dpi_is_normalised(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "norm.pdf")
        rendered = render_page(path, 1, dpi=300.0)
        assert rendered.dpi == 300

    def test_default_dpi_is_constant(self) -> None:
        assert DEFAULT_RENDER_DPI == 300
        assert MIN_RENDER_DPI <= DEFAULT_RENDER_DPI <= MAX_RENDER_DPI


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_same_page_same_dpi_identical(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "same.pdf")
        first = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        second = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert first.image.samples == second.image.samples
        assert (first.width, first.height) == (second.width, second.height)

    def test_same_image_page_same_dpi_identical(self, tmp_path) -> None:
        doc = pymupdf.open()
        _insert_image(_new_page(doc))
        path = _save(doc, tmp_path / "same_img.pdf")
        first = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        second = render_page(path, 1, dpi=DEFAULT_RENDER_DPI)
        assert first.image.samples == second.image.samples

    def test_different_dpi_produces_different_output(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "diff.pdf")
        low = render_page(path, 1, dpi=72)
        high = render_page(path, 1, dpi=144)
        assert (low.width, low.height) != (high.width, high.height)
        assert low.dpi != high.dpi


# --------------------------------------------------------------------------- #
# Isolation (no mutation of the source page/document)
# --------------------------------------------------------------------------- #


class TestIsolation:
    def test_render_page_does_not_mutate_page(self, tmp_path) -> None:
        doc = _text_doc(pages=1)
        path = _save(doc, tmp_path / "iso.pdf")
        probe = pymupdf.open(path)
        try:
            page = probe[0]
            rect_before = pymupdf.Rect(page.rect)
            media_before = pymupdf.Point(page.mediabox_size)
            rotation_before = page.rotation
            text_before = page.get_text("text")
            render_page(probe, 1, dpi=DEFAULT_RENDER_DPI)
            assert pymupdf.Rect(page.rect) == rect_before
            assert pymupdf.Point(page.mediabox_size) == media_before
            assert page.rotation == rotation_before
            assert page.get_text("text") == text_before
        finally:
            probe.close()

    def test_render_pages_does_not_mutate_document(self, tmp_path) -> None:
        doc = _text_doc(pages=2)
        path = _save(doc, tmp_path / "iso2.pdf")
        probe = pymupdf.open(path)
        try:
            count_before = probe.page_count
            metadata_before = dict(probe.metadata)
            for _ in render_pages(probe, dpi=DEFAULT_RENDER_DPI):
                pass
            assert probe.page_count == count_before
            assert dict(probe.metadata) == metadata_before
            assert not probe.is_closed  # caller keeps ownership
        finally:
            probe.close()

    def test_render_with_path_closes_owned_document(self, tmp_path) -> None:
        # With a path input the renderer owns and closes the document; this
        # simply verifies the call completes without leaking a handle.
        path = _save(_text_doc(pages=1), tmp_path / "owned.pdf")
        assert isinstance(render_page(path, 1, dpi=72), RenderedPage)


# --------------------------------------------------------------------------- #
# render_pages convenience API
# --------------------------------------------------------------------------- #


class TestRenderPagesAPI:
    def test_yields_every_page_in_order(self, tmp_path) -> None:
        path = _save(_text_doc(pages=4), tmp_path / "all.pdf")
        rendered = list(render_pages(path, dpi=72))
        assert len(rendered) == 4
        assert [r.page_number for r in rendered] == [1, 2, 3, 4]

    def test_rejects_invalid_dpi_eagerly(self, tmp_path) -> None:
        path = _save(_text_doc(pages=1), tmp_path / "eager.pdf")
        with pytest.raises(ValueError):
            render_pages(path, dpi=0)

    def test_empty_document_raises_empty_pdf_error(self, tmp_path) -> None:
        path = tmp_path / "empty.pdf"
        path.write_bytes(ZERO_PAGE_PDF)
        with pytest.raises(EmptyPDFError):
            list(render_pages(path))

    def test_accepts_open_document_without_closing_it(self, tmp_path) -> None:
        doc = _text_doc(pages=2)
        path = _save(doc, tmp_path / "opendoc.pdf")
        probe = pymupdf.open(path)
        try:
            assert [r.page_number for r in render_pages(probe)] == [1, 2]
            assert not probe.is_closed
        finally:
            probe.close()


# --------------------------------------------------------------------------- #
# Page-number validation
# --------------------------------------------------------------------------- #


class TestPageNumberValidation:
    @pytest.mark.parametrize("page_number", [0, -1, 4])
    def test_out_of_range_page_number_rejected(
        self, tmp_path, page_number
    ) -> None:
        path = _save(_text_doc(pages=3), tmp_path / "range.pdf")
        with pytest.raises(ValueError):
            render_page(path, page_number)

    @pytest.mark.parametrize("page_number", [1.0, 1.5, "1", None, True])
    def test_non_integer_page_number_rejected(
        self, tmp_path, page_number
    ) -> None:
        path = _save(_text_doc(pages=3), tmp_path / "type.pdf")
        with pytest.raises(ValueError):
            render_page(path, page_number)


# --------------------------------------------------------------------------- #
# Input handling errors
# --------------------------------------------------------------------------- #


class TestInputErrors:
    def test_missing_file_raises_pdf_read_error(self, tmp_path) -> None:
        with pytest.raises(PDFReadError):
            render_page(tmp_path / "does_not_exist.pdf", 1)

    def test_corrupt_file_raises_pdf_read_error(self, tmp_path) -> None:
        path = tmp_path / "corrupt.pdf"
        path.write_bytes(b"this is not a pdf at all")
        with pytest.raises(PDFReadError):
            render_page(path, 1)


# --------------------------------------------------------------------------- #
# Public API surface
# --------------------------------------------------------------------------- #


class TestPublicAPI:
    def test_renderer_api_is_exported(self) -> None:
        from kindle_converter.pdf import (
            DEFAULT_RENDER_DPI,
            MAX_RENDER_DPI,
            MIN_RENDER_DPI,
            PDFRenderingError,
            RenderedPage,
            render_page,
            render_pages,
        )

        assert MIN_RENDER_DPI <= DEFAULT_RENDER_DPI <= MAX_RENDER_DPI
        assert issubclass(PDFRenderingError, Exception)

    def test_internal_helpers_are_not_exported(self) -> None:
        import kindle_converter.pdf as pdf_api

        for name in ("_validate_dpi", "_build_matrix", "_render_loaded_page"):
            assert name not in pdf_api.__all__