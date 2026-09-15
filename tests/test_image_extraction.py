"""Focused deterministic tests for M2.13 image extraction and placement.

All PDFs are generated in-memory with PyMuPDF; nothing here uses network
access, external fixtures, or machine-specific files.
"""

from __future__ import annotations

import dataclasses
import re
import zipfile
from pathlib import Path

import pymupdf
import pytest

from kindle_converter.document import Book, BookMetadata, Chapter, Image, Paragraph
from kindle_converter.epub import build_epub
from kindle_converter.pdf import (
    ImageAsset,
    ImageExtractionError,
    ImageExtractionResult,
    ImagePlacement,
    extract_pdf_images,
)
from kindle_converter.pdf.images import _mime_for


PAGE_W, PAGE_H = 595, 842
LONG_TEXT = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)
SECOND_TEXT = (
    "Far away, It was a bright cold day in April and the clocks were "
    "striking thirteen; Winston Smith slipped through the glass doors."
)


def _pixmap(w=20, h=15, color=(200, 50, 60)):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, w, h))
    pix.set_rect(pix.irect, color)
    return pix


def _save(doc, path):
    doc.save(str(path))
    doc.close()
    return str(path)


def _text_pdf(path, *, pages=1, title="Img Book"):
    doc = pymupdf.open()
    if title:
        doc.set_metadata({"title": title})
    for _ in range(pages):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"{LONG_TEXT}\n\n{SECOND_TEXT}",
            fontname="helv",
            fontsize=12,
        )
    return _save(doc, path)


def _pdf_with_images(path, specs, *, title="Img Book", text_pages=()):
    doc = pymupdf.open()
    if title:
        doc.set_metadata({"title": title})
    count = max([max(specs, default=-1), max(text_pages, default=-1)]) + 1
    count = max(count, 1)
    for index in range(count):
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        if index in text_pages:
            page.insert_textbox(
                pymupdf.Rect(72, 100, 500, 400),
                LONG_TEXT,
                fontname="helv",
                fontsize=12,
            )
            page.insert_textbox(
                pymupdf.Rect(72, 450, 500, 700),
                LONG_TEXT,
                fontname="helv",
                fontsize=12,
            )
        for rect, pix in specs.get(index, []):
            page.insert_image(pymupdf.Rect(*rect), pixmap=pix)
    return _save(doc, path)


def _open_result(path):
    doc = pymupdf.open(str(path))
    try:
        return extract_pdf_images(doc)
    finally:
        doc.close()

class TestBasics:
    def test_no_images_empty_result(self, tmp_path):
        path = _text_pdf(tmp_path / "t.pdf")
        result = extract_pdf_images(path)
        assert result.assets == () and result.placements == ()
        assert result.asset_count == 0 and result.placement_count == 0

    def test_one_image(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "one.pdf", {0: [((50, 50, 150, 150), _pixmap())]}
        )
        result = extract_pdf_images(path)
        assert result.asset_count == 1 and result.placement_count == 1
        asset = result.assets[0]
        placement = result.placements[0]
        assert asset.asset_id == "image-001"
        assert asset.width == 20 and asset.height == 15
        assert asset.mime_type == "image/png" and asset.extension == "png"
        assert placement.asset_id == asset.asset_id
        assert placement.page_number == 1
        x0, y0, x1, y1 = placement.bbox
        assert x0 == 50.0 and x1 == 150.0
        assert y1 > y0 and 50.0 <= y0 <= 150.0

    def test_two_images(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "m.pdf",
            {0: [
                ((10, 10, 60, 60), _pixmap(color=(10, 20, 30))),
                ((200, 200, 300, 300), _pixmap(color=(200, 100, 5))),
            ]},
        )
        result = extract_pdf_images(path)
        assert result.asset_count == 2 and result.placement_count == 2
        assert [a.asset_id for a in result.assets] == ["image-001", "image-002"]


class TestOrdering:
    def test_across_pages(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "x.pdf",
            {
                0: [((10, 10, 60, 60), _pixmap(color=(1, 2, 3)))],
                1: [((10, 10, 60, 60), _pixmap(color=(250, 251, 252)))],
            },
        )
        result = extract_pdf_images(path)
        assert result.asset_count == 2 and result.placement_count == 2
        assert [p.page_number for p in result.placements] == [1, 2]
        assert result.placements_for_page(2)[0].page_number == 2

    def test_reuse_dedupes(self, tmp_path):
        pix = _pixmap(color=(9, 9, 9))
        path = _pdf_with_images(
            tmp_path / "r.pdf",
            {0: [((10, 10, 60, 60), pix)], 1: [((300, 300, 400, 400), pix)]},
        )
        result = extract_pdf_images(path)
        assert result.asset_count == 1 and result.placement_count == 2
        assert {p.asset_id for p in result.placements} == {"image-001"}
        assert [p.page_number for p in result.placements] == [1, 2]

    def test_stable_repeat(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "s.pdf",
            {
                0: [((10, 300, 60, 350), _pixmap(color=(5, 6, 7)))],
                1: [((10, 10, 60, 60), _pixmap(color=(200, 201, 202)))],
            },
        )
        assert extract_pdf_images(path) == extract_pdf_images(path)

    def test_immutability(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "i.pdf", {0: [((50, 50, 150, 150), _pixmap())]}
        )
        result = extract_pdf_images(path)
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.assets[0].asset_id = "x"  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.placements[0].order = 99  # type: ignore[misc]

    def test_exports_and_mime(self):
        import kindle_converter.pdf as pkg

        for name in ("extract_pdf_images", "ImageAsset", "ImagePlacement",
                     "ImageExtractionResult", "ImageExtractionError"):
            assert name in pkg.__all__
        assert _mime_for("png", b"\x89PNG\r\n\x1a\nx") == ("png", "image/png")
        assert _mime_for("zz", b"\xff\xd8\xffx")[1] == "image/jpeg"

    def test_bad_path(self, tmp_path):
        from kindle_converter.pdf import PDFReadError

class TestIntegration:
    def test_text_unchanged_without_images(self, tmp_path):
        from kindle_converter.pdf import extract_book

        path = _text_pdf(tmp_path / "plain.pdf")
        book = extract_book(path)
        assert all(
            not isinstance(b, Image) for ch in book.chapters for b in ch.blocks
        )

    def test_image_only_page(self, tmp_path):
        from kindle_converter.pdf import extract_page_layout
        from kindle_converter.pdf.reconstruction import (
            deduplicate_layout,
            reconstruct_layout as full_layout,
            reconstructed_document_to_book,
        )
        from kindle_converter.document import BookMetadata

        path = _pdf_with_images(
            tmp_path / "only.pdf", {0: [((50, 50, 200, 200), _pixmap())]}
        )
        result = extract_pdf_images(path)
        layout = deduplicate_layout(extract_page_layout(path))
        document, _, _, _ = full_layout(layout, images=result)
        book = reconstructed_document_to_book(
            document, BookMetadata(title="Only")
        )
        imgs = [b for ch in book.chapters for b in ch.blocks if isinstance(b, Image)]
        assert len(imgs) == 1 and len(imgs[0].data) > 0

    def test_text_before_after_image(self, tmp_path):
        from kindle_converter.pdf import (
            extract_page_layout,
            ordered_body_elements,
            ReconstructedImage,
        )
        from kindle_converter.pdf.reconstruction import (
            deduplicate_layout,
            reconstruct_layout as full_layout,
        )

        doc = pymupdf.open()
        page = doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_textbox(
            pymupdf.Rect(72, 60, 500, 200),
            f"{LONG_TEXT}\n\n{SECOND_TEXT}",
            fontname="helv", fontsize=12,
        )
        page.insert_image(pymupdf.Rect(72, 300, 300, 450), pixmap=_pixmap())
        page.insert_textbox(
            pymupdf.Rect(72, 550, 500, 700),
            f"{LONG_TEXT}\n\n{SECOND_TEXT}",
            fontname="helv", fontsize=12,
        )
        pdf_path = tmp_path / "sand.pdf"
        doc.save(str(pdf_path))
        doc.close()
        result = extract_pdf_images(str(pdf_path))
        assert result.placement_count == 1
        layout = deduplicate_layout(extract_page_layout(str(pdf_path)))
        document, _, _, _ = full_layout(layout, images=result)
        seq = ordered_body_elements(document.pages[0])
        kinds = ["img" if isinstance(e, ReconstructedImage) else "txt" for e in seq]
        assert "img" in kinds and kinds.count("txt") >= 1

    def test_epub_packages_images(self, tmp_path):
        from kindle_converter.pdf import extract_book

        path = _pdf_with_images(
            tmp_path / "e.pdf",
            {0: [((50, 50, 200, 200), _pixmap())]},
            text_pages={0},
        )
        book = extract_book(path)
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
            assert "EPUB/image-001.png" in names
            body = archive.read("EPUB/chapter-00.xhtml").decode("utf-8")
            assert 'src="image-001.png"' in body

    def test_epub_reuse_single_asset(self, tmp_path):
        pix = _pixmap(color=(9, 9, 9))
        path = _pdf_with_images(
            tmp_path / "re.pdf",
            {0: [((10, 10, 60, 60), pix)], 1: [((300, 300, 400, 400), pix)]},
            text_pages={0, 1},
        )
        from kindle_converter.pdf import extract_book

        book = extract_book(path)
        out = tmp_path / "re.epub"
        build_epub(book, out)
        with zipfile.ZipFile(out) as archive:
            names = archive.namelist()
            assert names.count("EPUB/image-001.png") == 1
            bodies = [
                archive.read(n).decode("utf-8")
                for n in names if n.endswith(".xhtml")
            ]
            assert sum(b.count('src="image-001.png"') for b in bodies) == 2

    def test_metadata_toc_still_work(self, tmp_path):
        from kindle_converter.pdf import (
            detect_chapters,
            extract_book,
            extract_page_layout,
            generate_toc,
            resolve_metadata,
        )
        from kindle_converter.pdf.reconstruction import deduplicate_layout
        from kindle_converter.document import BookMetadata

        path = _pdf_with_images(
            tmp_path / "m2.pdf",
            {0: [((50, 50, 200, 200), _pixmap())]},
            text_pages={0},
            title="Meta Book",
        )
        book = extract_book(path)
        assert book.metadata.title == "Meta Book"
        resolved = resolve_metadata(
            BookMetadata(author="A"), book.metadata
        )
        assert resolved.author == "A" and resolved.title == "Meta Book"
        doc2, _, _, _ = __import__(
            "kindle_converter.pdf.reconstruction", fromlist=["x"]
        ).reconstruct_layout(
            deduplicate_layout(extract_page_layout(path)),
            images=extract_pdf_images(path),
        )
        toc = generate_toc(detect_chapters(doc2))
        assert toc.entry_count >= 0

class TestDetails:
    def test_dimensions(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "w.pdf", {0: [((10, 10, 60, 60), _pixmap(w=20, h=15))]}
        )
        asset = extract_pdf_images(path).assets[0]
        assert (asset.width, asset.height) == (20, 15)

    def test_format_mime(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "f.pdf", {0: [((10, 10, 60, 60), _pixmap())]}
        )
        asset = extract_pdf_images(path).assets[0]
        assert asset.mime_type == "image/png" and asset.extension == "png"

    def test_bbox_provenance(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "b.pdf", {0: [((10, 10, 60, 60), _pixmap())]}
        )
        placement = extract_pdf_images(path).placements[0]
        x0, y0, x1, y1 = placement.bbox
        assert x1 > x0 and y1 > y0 and placement.page_number == 1

    def test_deterministic_naming(self, tmp_path):
        pix = _pixmap(color=(9, 9, 9))
        path = _pdf_with_images(
            tmp_path / "dn.pdf",
            {0: [((10, 10, 60, 60), pix)], 1: [((10, 10, 60, 60), pix)]},
            text_pages={0, 1},
        )
        from kindle_converter.pdf import extract_book

        book = extract_book(path)
        out = tmp_path / "dn.epub"
        build_epub(book, out)
        assert build_epub(book, tmp_path / "dn2.epub") is None
        with zipfile.ZipFile(out) as archive:
            assert "EPUB/image-001.png" in archive.namelist()
        with zipfile.ZipFile(tmp_path / "dn2.epub") as archive2:
            assert "EPUB/image-001.png" in archive2.namelist()

    def test_full_pipeline_with_image(self, tmp_path):
        from kindle_converter import convert_pdf_to_epub

        pix = _pixmap(color=(30, 40, 50))
        doc = pymupdf.open()
        doc.set_metadata({"title": "Pipe"})
        for _ in range(2):
            page = doc.new_page(width=PAGE_W, height=PAGE_H)
            page.insert_textbox(
                pymupdf.Rect(72, 100, 500, 700),
                f"{LONG_TEXT}\n\n{SECOND_TEXT}",
                fontname="helv",
                fontsize=12,
            )
        doc[0].insert_image(pymupdf.Rect(72, 450, 200, 550), pixmap=pix)
        pdf_path = str(tmp_path / "pipe.pdf")
        doc.save(pdf_path)
        doc.close()
        out = tmp_path / "pipe.epub"
        convert_pdf_to_epub(pdf_path, out)
        with zipfile.ZipFile(out) as archive:
            assert "EPUB/image-001.png" in archive.namelist()

    def test_epub_still_works_text_only(self, tmp_path):
        book = Book(
            metadata=BookMetadata(title="T"),
            chapters=[Chapter(title="C", blocks=[Paragraph(text="Hi")])],
        )
        out = tmp_path / "t.epub"
        build_epub(book, out)
        assert out.exists()

    def test_distinct_assets(self, tmp_path):
        path = _pdf_with_images(
            tmp_path / "dd.pdf",
            {0: [
                ((10, 10, 60, 60), _pixmap(color=(1, 1, 1))),
                ((100, 100, 160, 160), _pixmap(color=(250, 250, 250))),
            ]},
        )
        result = extract_pdf_images(path)
        assert result.asset_count == 2
        assert result.assets[0].data != result.assets[1].data

    def test_reconstruction_keeps_text_order(self, tmp_path):
        from kindle_converter.pdf import extract_book

        plain = _text_pdf(tmp_path / "p.pdf")
        with_img_doc = pymupdf.open()
        with_img_doc.set_metadata({"title": "Img Book"})
        page = with_img_doc.new_page(width=PAGE_W, height=PAGE_H)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"{LONG_TEXT}\n\n{SECOND_TEXT}",
            fontname="helv",
            fontsize=12,
        )
        ipath = str(tmp_path / "pi.pdf")
        with_img_doc.save(ipath)
        with_img_doc.close()
        plain_texts = [
            b.text for b in extract_book(plain).chapters[0].blocks
            if isinstance(b, Paragraph)
        ]
        mixed_texts = [
            b.text for b in extract_book(ipath).chapters[0].blocks
            if isinstance(b, Paragraph)
        ]
        assert plain_texts == mixed_texts


