"""Integration tests for the M2 reconstruction pipeline (Milestone 2.7).

These tests exercise the *integration* of the completed M2 layers, not the
individual detectors. Each M2 component is already covered by its own
milestone test module; this module proves they compose into one coherent
reconstruction pipeline whose output reaches the EPUB layer.

All tests are deterministic and local. Synthetic PDFs are used for the
end-to-end cases.
"""

from __future__ import annotations

import zipfile

import pymupdf
import pytest

from kindle_converter import convert_pdf_to_epub
from kindle_converter.pdf import (
    ElementKind,
    FontFlags,
    LayoutBlock,
    LayoutPage,
    ParagraphLayout,
    ParagraphPage,
    ReconstructedParagraph,
    TextLine,
    TextSpan,
    build_reconstructed_document,
    classify_paragraphs,
    detect_headers_footers,
    extract_page_layout,
    reconstruct_layout,
)

PAGE_WIDTH = 595.0
PAGE_HEIGHT = 842.0

TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen and the weather was cold across the country side here."
)


def make_span(text, font_size=12.0, font_name="Helvetica", flags=FontFlags(0), bbox=None):
    if bbox is None:
        bbox = (0.0, 0.0, len(text) * font_size * 0.5, font_size)
    return TextSpan(text=text, bbox=bbox, font_name=font_name, font_size=font_size, font_flags=flags)


def make_line(text, bbox, order=0, spans=None):
    if spans is None:
        spans = (make_span(text),)
    return TextLine(text=text, bbox=bbox, order=order, spans=spans)


def make_paragraph(text, page_number=1, lines=None, blocks=None, reading_order_indices=None):
    if lines is None:
        lines = (make_line(text, (72, 100, 72 + len(text) * 5, 116)),)
    if blocks is None:
        blocks = tuple(LayoutBlock(text=line.text, bbox=line.bbox, order=0, lines=(line,)) for line in lines)
    if reading_order_indices is None:
        reading_order_indices = tuple(range(len(lines)))
    return ReconstructedParagraph(
        text=text, page_number=page_number, source_lines=lines, source_blocks=blocks,
        source_line_orders=tuple((0, i) for i in range(len(lines))),
        reading_order_indices=reading_order_indices,
    )


def clone_paragraph(para, *, page_number):
    return ReconstructedParagraph(
        text=para.text, page_number=page_number, source_lines=para.source_lines,
        source_blocks=para.source_blocks, source_line_orders=para.source_line_orders,
        reading_order_indices=para.reading_order_indices, ends_at_page_boundary=para.ends_at_page_boundary,
    )


def body_paragraph(text, y0=200.0, page_number=1):
    line = make_line(text, (72, y0, 72 + len(text) * 6.0, y0 + 14), 0)
    return make_paragraph(text, page_number=page_number, lines=(line,),
        blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))


def heading_paragraph(text, y0=120.0, page_number=1):
    line = make_line(text, (72, y0, 72 + len(text) * 12.0, y0 + 24), 0,
        (make_span(text, font_size=24.0, font_name="Helvetica-Bold", flags=FontFlags.BOLD),))
    return make_paragraph(text, page_number=page_number, lines=(line,),
        blocks=(LayoutBlock(text=text, bbox=line.bbox, order=0, lines=(line,)),))


def make_paragraph_page(paragraphs, page_number=1):
    synced = tuple(clone_paragraph(p, page_number=page_number) for p in paragraphs)
    page = LayoutPage(page_number=page_number, page_width=PAGE_WIDTH, page_height=PAGE_HEIGHT,
        blocks=tuple(block for para in synced for block in para.source_blocks))
    return ParagraphPage(page=page, paragraphs=synced)


def make_paragraph_layout(pages):
    return ParagraphLayout(pages=pages)


def _new_page(doc):
    return doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)


def make_body_pdf(path, *, pages=3):
    doc = pymupdf.open()
    for index in range(pages):
        page = _new_page(doc)
        page.insert_textbox(pymupdf.Rect(72, 200, 500, 700),
            f"{TEXT_LINE}\n\nBody paragraph {index + 1} continues here with enough "
            "text to be meaningful body content for the test to find.",
            fontname="helv", fontsize=12)
    doc.save(str(path))
    doc.close()
    return str(path)


def make_pdf_with_furniture(path, *, pages=4):
    doc = pymupdf.open()
    for index in range(pages):
        page = _new_page(doc)
        page.insert_text((72, 60), "THE GREAT NOVEL", fontsize=12, fontname="hebo")
        page.insert_textbox(pymupdf.Rect(72, 200, 500, 680),
            f"{TEXT_LINE}\n\nThis is body text for page number {index + 1} of the book.",
            fontname="helv", fontsize=12)
        page.insert_text((500, 790), str(index + 1), fontsize=11, fontname="helv")
    doc.save(str(path))
    doc.close()
    return str(path)


def all_chapter_bodies(path):
    with zipfile.ZipFile(path) as archive:
        names = sorted(n for n in archive.namelist() if n.endswith(".xhtml"))
        return [archive.read(n).decode("utf-8") for n in names]


# 1. Basic paragraph integration
class TestBasicParagraphIntegration:
    def test_wrapped_lines_form_paragraph_elements(self, tmp_path):
        pdf = make_body_pdf(tmp_path / "p.pdf", pages=1)
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(layout)
        page = document.pages[0]
        assert len(page.elements) >= 1
        assert all(el.kind is ElementKind.PARAGRAPH for el in page.elements)
        joined = " ".join(el.text for el in page.elements)
        assert "bright cold day" in joined


# 2. Reading-order integration
class TestReadingOrderIntegration:
    def test_two_column_pdf_reaches_integrated_representation(self, tmp_path):
        doc = pymupdf.open()
        pdf_page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        cells = [
            ("A1 left-row-one", (72, 100)), ("B1 right-row-one", (340, 120)),
            ("A2 left-row-two", (72, 140)), ("B2 right-row-two", (340, 160)),
            ("A3 left-row-three", (72, 180)), ("B3 right-row-three", (340, 200)),
        ]
        for text, (x, y) in cells:
            pdf_page.insert_text((x, y), text, fontsize=12)
        path = str(tmp_path / "col.pdf")
        doc.save(path)
        doc.close()
        layout = extract_page_layout(path)
        document, _, _, _ = reconstruct_layout(layout)
        texts = [el.text.split()[0] for el in document.pages[0].elements]
        assert texts == ["A1", "A2", "A3", "B1", "B2", "B3"]


# 3. Heading integration
class TestHeadingIntegration:
    def test_classified_heading_becomes_heading_element(self):
        heading = heading_paragraph("Chapter One", y0=120, page_number=1)
        bodies = tuple(
            body_paragraph(f"Body paragraph {i} with ordinary text.",
                           y0=200 + i * 80, page_number=1)
            for i in range(3)
        )
        ppage = make_paragraph_page((heading, *bodies), page_number=1)
        layout = make_paragraph_layout((ppage,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        assert document.pages[0].elements[0].kind is ElementKind.HEADING
        assert document.pages[0].elements[0].heading is not None
        assert document.pages[0].elements[0].heading.score > 0
        assert document.pages[0].elements[1].kind is ElementKind.PARAGRAPH


# 4. Ordinary paragraph integration
class TestOrdinaryParagraphIntegration:
    def test_non_heading_paragraph_stays_paragraph(self):
        body = body_paragraph("This is a normal paragraph with ordinary typography and is not a heading.",
            page_number=1)
        ppage = make_paragraph_page((body,), page_number=1)
        layout = make_paragraph_layout((ppage,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        el = document.pages[0].elements[0]
        assert el.kind is ElementKind.PARAGRAPH
        assert el.heading is None


# 5. Header filtering
class TestHeaderFiltering:
    def test_detected_header_excluded_from_body_elements(self):
        header = body_paragraph("THE GREAT NOVEL", y0=60, page_number=1)
        pages = []
        for n in range(1, 4):
            hp = clone_paragraph(header, page_number=n)
            bp = body_paragraph(f"Unique body text for page number {n} here in the document.",
                y0=250, page_number=n)
            pages.append(make_paragraph_page((hp, bp), page_number=n))
        layout = make_paragraph_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        body_texts = [el.text for page in document.pages for el in page.elements]
        assert "THE GREAT NOVEL" not in body_texts
        assert any("Unique body text" in t for t in body_texts)
        assert document.header_footer.detected_count >= 1


# 6. Footer filtering
class TestFooterFiltering:
    def test_detected_footer_excluded_from_body_elements(self):
        footer = body_paragraph("Copyright Acme Publishing", y0=790, page_number=1)
        pages = []
        for n in range(1, 4):
            fp = clone_paragraph(footer, page_number=n)
            bp = body_paragraph(f"Distinctive body text for page number {n} of the book.",
                y0=250, page_number=n)
            pages.append(make_paragraph_page((fp, bp), page_number=n))
        layout = make_paragraph_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        body_texts = [el.text for page in document.pages for el in page.elements]
        assert "Copyright Acme Publishing" not in body_texts
        assert any("Distinctive body text" in t for t in body_texts)
        assert document.header_footer.detected_count >= 1


# 7. Page-number filtering
class TestPageNumberFiltering:
    def test_detected_page_numbers_excluded_from_body(self):
        pages = []
        for n in range(1, 5):
            np_para = body_paragraph(str(n), y0=790, page_number=n)
            bp = body_paragraph(f"Body content for page number {n} of the document here.",
                y0=250, page_number=n)
            pages.append(make_paragraph_page((np_para, bp), page_number=n))
        layout = make_paragraph_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        body_texts = [el.text for page in document.pages for el in page.elements]
        for n in range(1, 5):
            assert str(n) not in body_texts
        assert sum(1 for t in body_texts if "Body content for page" in t) == 4


# 8. Matching-text body paragraph preserved
class TestMatchingTextPreserved:
    def test_body_paragraph_with_header_text_is_kept(self):
        header = body_paragraph("Introduction", y0=60, page_number=1)
        pages = []
        for n in range(1, 4):
            hp = clone_paragraph(header, page_number=n)
            bp = body_paragraph(f"Body text for page number {n} here.", y0=300, page_number=n)
            if n == 2:
                mid = body_paragraph("Introduction", y0=500, page_number=2)
                pages.append(make_paragraph_page((hp, bp, mid), page_number=n))
            else:
                pages.append(make_paragraph_page((hp, bp), page_number=n))
        layout = make_paragraph_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        page2_texts = [el.text for el in document.pages[1].elements]
        assert "Introduction" in page2_texts
        page1_texts = [el.text for el in document.pages[0].elements]
        assert "Introduction" not in page1_texts


# 9. Provenance
class TestProvenance:
    def test_elements_retain_original_paragraph_provenance(self):
        body = body_paragraph("Provenance body paragraph text here.", page_number=1)
        ppage = make_paragraph_page((body,), page_number=1)
        layout = make_paragraph_layout((ppage,))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        el = document.pages[0].elements[0]
        assert el.paragraph.text == body.text
        assert el.paragraph.source_lines == body.source_lines
        assert el.paragraph.source_blocks == body.source_blocks
        assert el.paragraph.page_number == body.page_number
        assert document.paragraphs is layout


# 10. Page boundaries
class TestPageBoundaries:
    def test_paragraphs_remain_associated_with_original_pages(self):
        pages_list = []
        for n in range(1, 3):
            bp = body_paragraph(f"Body text for page {n} here.", y0=250, page_number=n)
            pages_list.append(make_paragraph_page((bp,), page_number=n))
        layout = make_paragraph_layout(tuple(pages_list))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        assert [page.page_number for page in document.pages] == [1, 2]
        assert document.pages[0].elements[0].paragraph.page_number == 1
        assert document.pages[1].elements[0].paragraph.page_number == 2


# 11. Heading/header conflict
class TestHeadingHeaderConflict:
    def test_furniture_classification_wins_over_heading_for_body_filtering(self):
        top = heading_paragraph("RECURRING TITLE", y0=60, page_number=1)
        pages = []
        for n in range(1, 4):
            tp = clone_paragraph(top, page_number=n)
            bp = body_paragraph(f"Body text for page number {n} here in doc.", y0=300, page_number=n)
            pages.append(make_paragraph_page((tp, bp), page_number=n))
        layout = make_paragraph_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        body_texts = [el.text for page in document.pages for el in page.elements]
        assert "RECURRING TITLE" not in body_texts
        assert any(h.text == "RECURRING TITLE" for h in document.headings.headings)


# 12. Empty page after filtering
class TestEmptyPageAfterFiltering:
    def test_header_footer_only_page_does_not_crash(self):
        pages = []
        for n in range(1, 4):
            hp = body_paragraph("ONLY FURNITURE", y0=60, page_number=n)
            fp = body_paragraph("Page marker text", y0=790, page_number=n)
            if n < 3:
                bp = body_paragraph(f"Body text for page number {n} here.", y0=300, page_number=n)
                pages.append(make_paragraph_page((hp, bp, fp), page_number=n))
            else:
                pages.append(make_paragraph_page((hp, fp), page_number=n))
        layout = make_paragraph_layout(tuple(pages))
        headings = classify_paragraphs(layout)
        furniture = detect_headers_footers(layout)
        document = build_reconstructed_document(layout, headings, furniture)
        assert document.pages[2].elements == ()
        assert document.pages[0].elements
        assert document.header_footer.detected_count >= 1


# 13. Mixed page layouts
class TestMixedPageLayouts:
    def test_single_and_multicolumn_pages_preserve_order(self, tmp_path):
        doc = pymupdf.open()
        p1 = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        for i, y in enumerate((150, 300, 450)):
            p1.insert_textbox(pymupdf.Rect(72, y, 500, y + 60),
                f"Single column block {i + 1} text here for testing.",
                fontname="helv", fontsize=12)
        p2 = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        cells = [
            ("C1-left-one", (72, 100)), ("D1-right-one", (340, 120)),
            ("C2-left-two", (72, 160)), ("D2-right-two", (340, 180)),
            ("C3-left-three", (72, 220)), ("D3-right-three", (340, 240)),
        ]
        for text, (x, y) in cells:
            p2.insert_text((x, y), text, fontsize=12)
        path = str(tmp_path / "mixed.pdf")
        doc.save(path)
        doc.close()
        layout = extract_page_layout(path)
        document, _, _, _ = reconstruct_layout(layout)
        page1_heads = [el.text.split()[0] for el in document.pages[0].elements]
        assert page1_heads == ["Single", "Single", "Single"]
        page2_heads = [el.text.split()[0] for el in document.pages[1].elements]
        assert page2_heads == [
            "C1-left-one", "C2-left-two", "C3-left-three",
            "D1-right-one", "D2-right-two", "D3-right-three",
        ]


# 14. EPUB integration
class TestEPUBIntegration:
    def test_epub_contains_reconstruction_omits_furniture(self, tmp_path):
        pdf = make_pdf_with_furniture(tmp_path / "book.pdf", pages=4)
        out = tmp_path / "book.epub"
        convert_pdf_to_epub(pdf, out)
        bodies = all_chapter_bodies(out)
        all_text = "\n".join(bodies)
        assert "THE GREAT NOVEL" not in all_text
        assert "bright cold day" in all_text
        assert all(f">{n}<" not in all_text for n in range(1, 5))


# 15. Existing M1 regression
class TestM1Regression:
    def test_plain_text_pdf_still_converts_to_epub(self, tmp_path):
        pdf = make_body_pdf(tmp_path / "plain.pdf", pages=2)
        out = tmp_path / "plain.epub"
        convert_pdf_to_epub(pdf, out)
        assert out.exists()
        bodies = all_chapter_bodies(out)
        assert any("bright cold day" in b for b in bodies)

    def test_convert_pdf_to_epub_entry_point_unchanged(self):
        from kindle_converter import convert_pdf_to_epub as entry
        assert entry is convert_pdf_to_epub

