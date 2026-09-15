"""M2.14 final Milestone 2 regression and quality pass (part 1: helpers)."""

from __future__ import annotations

import dataclasses
import zipfile
from pathlib import Path

import pymupdf
import pytest

from kindle_converter import convert_pdf_to_epub
from kindle_converter.document import Book, BookMetadata, Chapter, Paragraph
from kindle_converter.epub import build_epub
from kindle_converter.pdf import (
    detect_chapters,
    extract_book,
    extract_page_layout,
    extract_pdf_images,
    extract_pdf_metadata,
    generate_toc,
    reconstruct_layout,
    remove_page_numbers,
    resolve_metadata,
)

W, H = 595.0, 842.0
BODY = (
    "It was a bright cold day in April and the clocks were striking thirteen "
    "and the weather was cold across the country side here in the valley."
)


def _pixmap(color=(200, 50, 60)):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 20, 15))
    pix.set_rect(pix.irect, color)
    return pix


def make_rich_pdf(path: Path, *, pages: int = 3) -> Path:
    doc = pymupdf.open()
    doc.set_metadata({"title": "M214 Rich Book", "author": "M214 Author"})
    pix = _pixmap()
    for i in range(pages):
        page = doc.new_page(width=W, height=H)
        page.insert_text((72, 50), "M214 HEADER", fontname="helv", fontsize=9)
        page.insert_text(
            (72, 120), f"Chapter {i + 1}", fontname="hebo", fontsize=20
        )
        page.insert_textbox(
            pymupdf.Rect(72, 200, 500, 620),
            f"{BODY}\n\n{BODY}",
            fontname="helv",
            fontsize=12,
        )
        if i == 1:
            page.insert_image(pymupdf.Rect(72, 660, 200, 760), pixmap=pix)
        page.insert_text((72, H - 42), "M214 FOOTER", fontname="helv", fontsize=9)
        page.insert_text(
            (450, 820), f"Page {i + 1} of {pages}",
            fontname="helv", fontsize=9,
        )
    doc.save(str(path))
    doc.close()
    return path


def make_gap_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Gap Book"})
    for i in range(2):
        page = doc.new_page(width=W, height=H)
        page.insert_textbox(
            pymupdf.Rect(72, 200, 500, 600), BODY,
            fontname="helv", fontsize=12,
        )
        page.insert_text((72, 800), str(i + 1), fontname="helv", fontsize=9)
    doc.save(str(path))
    doc.close()
    return path


def make_dup_image_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Dup Image Book"})
    pix = _pixmap(color=(10, 20, 30))
    for _ in range(2):
        page = doc.new_page(width=W, height=H)
        page.insert_textbox(
            pymupdf.Rect(72, 200, 500, 500), BODY,
            fontname="helv", fontsize=12,
        )
        page.insert_image(pymupdf.Rect(72, 550, 200, 650), pixmap=pix)
    doc.save(str(path))
    doc.close()
    return path


def make_text_pdf(path: Path, *, pages: int = 2) -> Path:
    doc = pymupdf.open()
    doc.set_metadata({"title": "Text Only"})
    for _ in range(pages):
        page = doc.new_page(width=W, height=H)
        page.insert_textbox(
            pymupdf.Rect(72, 200, 500, 700),
            f"{BODY}\n\n{BODY}",
            fontname="helv", fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


def _bodies(archive: zipfile.ZipFile) -> str:
    out: list[str] = []
    for name in sorted(archive.namelist()):
        if name.startswith("EPUB/chapter-") and name.endswith(".xhtml"):
            raw = archive.read(name).decode("utf-8")
            out.append(raw[raw.index("<body>"):raw.index("</body>")])
    return "\n".join(out)


class TestRichEndToEnd:
    def test_full_chain(self, tmp_path: Path) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")
        layout = extract_page_layout(pdf)
        images = extract_pdf_images(pdf)
        assert images.asset_count == 1 and images.placement_count == 1
        document, _, _, furniture = reconstruct_layout(layout, images=images)
        texts = {item.text for item in furniture.detected}
        assert "M214 HEADER" in texts and "M214 FOOTER" in texts
        assert document.chapters is not None
        assert document.chapters.chapter_count == 3
        cleaned = remove_page_numbers(document)
        assert cleaned.removed_count == 0
        assert [len(p.images) for p in cleaned.document.pages] == [0, 1, 0]
        chapters = detect_chapters(cleaned.document)
        assert chapters.chapter_count == 3
        toc = generate_toc(chapters)
        assert [e.title for e in toc.entries] == [
            "Chapter 1", "Chapter 2", "Chapter 3"]
        assert [e.page_number for e in toc.entries] == [1, 2, 3]
        assert all(
            entry.chapter is chapter
            for entry, chapter in zip(toc.entries, chapters.chapters))

    def test_rich_epub_packages_image(self, tmp_path: Path) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")
        out = tmp_path / "rich.epub"
        convert_pdf_to_epub(pdf, out)
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
            assert any(n.endswith(".xhtml") for n in names)
            assert "EPUB/image-001.png" in names
            text = _bodies(archive)
            assert "bright cold day" in text
            assert "M214 HEADER" not in text
            assert "M214 FOOTER" not in text


class TestExplicitApiBoundary:
    def test_pipeline_keeps_m25_gap_numbers(self, tmp_path: Path) -> None:
        pdf = make_gap_pdf(tmp_path / "gap.pdf")
        layout = extract_page_layout(pdf)
        document, _, _, furniture = reconstruct_layout(layout)
        assert furniture.detected_count == 0
        book = extract_book(pdf)
        flat = [getattr(b, "text", "") for b in book.chapters[0].blocks]
        assert "1" in flat and "2" in flat

    def test_explicit_removal_clears_gap(self, tmp_path: Path) -> None:
        pdf = make_gap_pdf(tmp_path / "gap.pdf")
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(layout)
        result = remove_page_numbers(document)
        assert result.removed_count == 2
        assert [d.value for d in result.removed] == [1, 2]
        remaining = [
            el.text for page in result.document.pages for el in page.elements]
        assert "1" not in remaining and "2" not in remaining
        assert any("bright cold day" in t for t in remaining)

class TestFeatureInteractions:
    def test_furniture_filtering_preserves_chapters(
        self, tmp_path: Path
    ) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(layout)
        before = [c.text for c in document.chapters.chapters]
        cleaned = remove_page_numbers(document)
        after = detect_chapters(cleaned.document)
        assert [c.text for c in after.chapters] == before

    def test_value_identical_body_text_survives(
        self, tmp_path: Path
    ) -> None:
        doc = pymupdf.open()
        doc.set_metadata({"title": "Dup Text"})
        for _ in range(3):
            page = doc.new_page(width=W, height=H)
            page.insert_text((72, 60), "Edge Title", fontname="helv", fontsize=9)
            page.insert_textbox(
                pymupdf.Rect(72, 300, 500, 500), "Edge Title",
                fontname="helv", fontsize=12)
            page.insert_textbox(
                pymupdf.Rect(72, 200, 500, 280), BODY,
                fontname="helv", fontsize=12)
            page.insert_text((72, 800), "Edge Footer",
                             fontname="helv", fontsize=9)
        pdf = tmp_path / "duptext.pdf"
        doc.save(str(pdf))
        doc.close()
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(layout)
        body = [el.text for p in document.pages for el in p.elements]
        assert "Edge Title" in body

    def test_images_survive_page_number_removal(
        self, tmp_path: Path
    ) -> None:
        pdf = make_dup_image_pdf(tmp_path / "dup.pdf")
        layout = extract_page_layout(pdf)
        images = extract_pdf_images(pdf)
        document, _, _, _ = reconstruct_layout(layout, images=images)
        result = remove_page_numbers(document)
        assert [len(p.images) for p in result.document.pages] == [1, 1]

    def test_repeated_image_deduped_but_placed_twice(
        self, tmp_path: Path
    ) -> None:
        pdf = make_dup_image_pdf(tmp_path / "dup.pdf")
        result = extract_pdf_images(pdf)
        assert result.asset_count == 1
        assert result.placement_count == 2
        assert {p.asset_id for p in result.placements} == {"image-001"}
        assert sorted(p.page_number for p in result.placements) == [1, 2]

    def test_metadata_epub_round_trip(self, tmp_path: Path) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")
        pdf_meta = extract_pdf_metadata(pdf)
        resolved = resolve_metadata(
            BookMetadata(title="", description="M214 desc."), pdf_meta)
        assert resolved.title == "M214 Rich Book"
        assert resolved.author == "M214 Author"
        assert resolved.description == "M214 desc."
        book = extract_book(pdf)
        book.metadata = resolved
        out = tmp_path / "meta.epub"
        build_epub(book, out)
        with zipfile.ZipFile(out) as archive:
            opf = archive.read("EPUB/content.opf").decode("utf-8")
            assert "M214 Rich Book" in opf
            assert "M214 desc." in opf

    def test_image_free_pdf_unchanged(self, tmp_path: Path) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf")
        layout = extract_page_layout(pdf)
        plain, _, _, _ = reconstruct_layout(layout)
        with_images, _, _, _ = reconstruct_layout(
            layout, images=extract_pdf_images(pdf))
        assert [(e.kind, e.text) for p in plain.pages for e in p.elements] == [
            (e.kind, e.text) for p in with_images.pages for e in p.elements]
        assert with_images.image_count == 0

    def test_mixed_page_sizes(self, tmp_path: Path) -> None:
        doc = pymupdf.open()
        doc.set_metadata({"title": "Mixed Sizes"})
        pix = _pixmap()
        for i, (w, h) in enumerate([(595.0, 842.0), (612.0, 792.0)]):
            page = doc.new_page(width=w, height=h)
            page.insert_text((72, 50), "Mixed Header",
                             fontname="helv", fontsize=9)
            page.insert_text(
                (72, 120), f"Chapter {i + 1}", fontname="hebo", fontsize=20)
            page.insert_textbox(
                pymupdf.Rect(72, 200, 500, 600), BODY,
                fontname="helv", fontsize=12)
            if i == 1:
                page.insert_image(pymupdf.Rect(72, 620, 200, 720), pixmap=pix)
            page.insert_text((72, h - 42), "Mixed Footer",
                             fontname="helv", fontsize=9)
        pdf = tmp_path / "mixed.pdf"
        doc.save(str(pdf))
        doc.close()
        layout = extract_page_layout(pdf)
        images = extract_pdf_images(pdf)
        document, _, _, _ = reconstruct_layout(layout, images=images)
        assert len(document.pages) == 2
        assert [len(p.images) for p in document.pages] == [0, 1]
        assert remove_page_numbers(document).removed_count == 0


class TestProvenanceDeterminismImmutability:
    def test_provenance_through_chain(self, tmp_path: Path) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")
        layout = extract_page_layout(pdf)
        images = extract_pdf_images(pdf)
        document, _, _, _ = reconstruct_layout(layout, images=images)
        cleaned = remove_page_numbers(document)
        chapters = detect_chapters(cleaned.document)
        toc = generate_toc(chapters)
        assert [e.page_number for e in toc.entries] == [1, 2, 3]
        assert all(
            entry.chapter is chapter
            for entry, chapter in zip(toc.entries, chapters.chapters))
        for page in cleaned.document.pages:
            for el in page.elements:
                assert el.paragraph.page_number == page.page_number
                assert el.paragraph.source_lines
        for placement in images.placements:
            assert placement.page_number in (1, 2, 3)
            x0, y0, x1, y1 = placement.bbox
            assert x1 >= x0 and y1 >= y0 and placement.xref > 0

    def test_chain_is_deterministic(self, tmp_path: Path) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")

        def snapshot():
            lay = extract_page_layout(pdf)
            imgs = extract_pdf_images(pdf)
            doc, _, _, _ = reconstruct_layout(lay, images=imgs)
            res = remove_page_numbers(doc)
            chaps = detect_chapters(res.document)
            toc = generate_toc(chaps)
            return (
                [(e.kind, e.text) for p in res.document.pages
                 for e in p.elements],
                [(p.asset_id, p.page_number, p.bbox)
                 for p in imgs.placements],
                [(e.title, e.order, e.page_number) for e in toc.entries])

        first = snapshot()
        for _ in range(3):
            assert snapshot() == first

    def test_explicit_ops_do_not_mutate(self, tmp_path: Path) -> None:
        pdf = make_rich_pdf(tmp_path / "rich.pdf")
        layout = extract_page_layout(pdf)
        document, _, _, _ = reconstruct_layout(
            layout, images=extract_pdf_images(pdf))
        before = tuple(tuple(e.text for e in p.elements)
                       for p in document.pages)
        before_images = tuple(
            tuple(i.placement.bbox for i in p.images)
            for p in document.pages)
        explicit = BookMetadata(title="Explicit")
        pdf_meta = extract_pdf_metadata(pdf)
        before_explicit = dataclasses.astuple(explicit)
        before_pdf = dataclasses.astuple(pdf_meta)
        cleaned = remove_page_numbers(document)
        assert cleaned.document is not document
        assert cleaned.document.pages is not document.pages
        detect_chapters(document)
        generate_toc(detect_chapters(document))
        resolved = resolve_metadata(explicit, pdf_meta)
        assert resolved is not explicit and resolved is not pdf_meta
        after = tuple(tuple(e.text for e in p.elements)
                      for p in document.pages)
        after_images = tuple(
            tuple(i.placement.bbox for i in p.images)
            for p in document.pages)
        assert after == before
        assert after_images == before_images
        assert dataclasses.astuple(explicit) == before_explicit
        assert dataclasses.astuple(pdf_meta) == before_pdf

        assert len(document.pages) == 3
        assert [len(p.images) for p in document.pages] == [0, 1, 0]
        assert remove_page_numbers(document).removed_count == 0
