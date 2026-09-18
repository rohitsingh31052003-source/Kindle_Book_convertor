"""Cover handling tests (Milestone 4.4).

Deterministic and offline: cover images are synthetic magic-byte payloads,
EPUBs are built from :class:`~kindle_converter.document.Book` objects, and
PDFs used by the pipeline tests are generated on the fly with PyMuPDF with
an injected no-OCR engine. The suite covers:

* the cover input boundary (:func:`kindle_converter.document.load_cover`),
  including the dedicated ``CoverError`` taxonomy;
* covered EPUB generation -- packaged image, ``properties="cover-image"``,
  ``name="cover"`` metadata, the reflowable cover page, and spine order;
* that the cover is *not* a navigation entry (nav/NCX/TOC unchanged);
* no-cover regression (the M4.4 output for a coverless ``Book`` is
  identical in structure to before);
* coexistence of the cover with ordinary chapter images; and
* the optional ``cover`` wiring through the PDF pipeline.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf
import pytest
from ebooklib import epub

from kindle_converter import convert_pdf_to_book, convert_pdf_to_epub
from kindle_converter.document import (
    Book,
    BookMetadata,
    Chapter,
    CoverError,
    CoverInvalidDataError,
    CoverNotFoundError,
    CoverUnreadableError,
    CoverUnsupportedFormatError,
    Image,
    Paragraph,
    load_cover,
)
from kindle_converter.document.cover import SUPPORTED_COVER_MEDIA_TYPES
from kindle_converter.epub import (
    InvalidImageError,
    build_epub,
    validate_epub,
)
from kindle_converter.epub.builder import (
    COVER_IMAGE_ID,
    COVER_IMAGE_RESOURCE,
    COVER_PAGE_FILE,
    COVER_PAGE_ID,
    STYLESHEET_RESOURCE,
)

#: Tiny but recognizable synthetic image payloads for cover tests.
PNG_BYTES = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03"
JPEG_BYTES = b"\xff\xd8\xff\xe0\x00\x01\x02\x03"
GIF_BYTES = b"GIF89a" + b"\x01\x00\x01\x00\x00"
SVG_BYTES = b"<svg xmlns='http://www.w3.org/2000/svg' width='1' height='1'/>"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _read_archive(path: Path) -> zipfile.ZipFile:
    """Open a generated EPUB as a zip archive, asserting it exists."""
    assert Path(path).is_file(), f"EPUB was not written to {path}"
    return zipfile.ZipFile(path)


def _opf(path: Path) -> str:
    """The raw ``content.opf`` package document."""
    with _read_archive(path) as archive:
        return archive.read("EPUB/content.opf").decode("utf-8")


def _spine_idrefs(opf: str) -> list[str]:
    """The manifest ids referenced by the spine ``<itemref>`` entries."""
    return re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)


def _cover_image_name(media_type: str) -> str:
    """The deterministic packaged cover resource name for a media type."""
    extension = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/gif": "gif",
        "image/svg+xml": "svg",
    }[media_type]
    return f"{COVER_IMAGE_RESOURCE}.{extension}"


def _covered_book(*, payload=JPEG_BYTES, content_type="image/jpeg") -> Book:
    """A single-chapter book with the given cover."""
    book = Book(
        metadata=BookMetadata(title="Covered", language="en"),
        chapters=[
            Chapter(title="Chapter 1", blocks=[Paragraph(text="Hello.")])
        ],
    )
    book.cover = Image(data=payload, content_type=content_type)
    return book


def _scanned_cover(media_type: str) -> tuple[bytes, str]:
    """A (payload, media_type) pair for one of the supported cover formats."""
    return {
        "image/jpeg": (JPEG_BYTES, "jpg"),
        "image/png": (PNG_BYTES, "png"),
        "image/gif": (GIF_BYTES, "gif"),
        "image/svg+xml": (SVG_BYTES, "svg"),
    }[media_type]


class _NoOcr:
    """An ``OCREngine`` that must never be called for a text PDF."""

    def recognize(self, image) -> str:  # pragma: no cover - must not run
        raise AssertionError("OCR must not run for a text PDF")


#: Body text long enough to be classified as meaningful native text.
_TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)


def _make_text_pdf(path: Path) -> Path:
    """A one-page text-only PDF with no images."""
    doc = pymupdf.open()
    doc.set_metadata({"title": "Covered Pipeline Fixture"})
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        pymupdf.Rect(72, 100, 500, 700),
        f"{_TEXT_LINE}\n\nFar away, {_TEXT_LINE}",
        fontname="helv",
        fontsize=12,
    )
    doc.save(str(path))
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# load_cover: filesystem path input
# --------------------------------------------------------------------------- #


class TestLoadCoverFromPath:
    @pytest.mark.parametrize(
        "media_type",
        ["image/jpeg", "image/png", "image/gif", "image/svg+xml"],
    )
    def test_sniffs_supported_formats_from_bytes(
        self, tmp_path, media_type: str
    ) -> None:
        payload, extension = _scanned_cover(media_type)
        path = tmp_path / f"cover.{extension}"
        path.write_bytes(payload)
        cover = load_cover(path)
        assert isinstance(cover, Image)
        assert cover.data == payload
        assert cover.content_type == media_type
        # A path loads no bibliographic metadata; alt_text stays empty.
        assert cover.alt_text is None

    def test_svg_with_xml_declaration_is_recognized(self, tmp_path) -> None:
        payload = b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg"/>'
        path = tmp_path / "logo.svg"
        path.write_bytes(payload)
        assert load_cover(path).content_type == "image/svg+xml"

    def test_accepts_str_and_pathlike(self, tmp_path) -> None:
        path = tmp_path / "cover.png"
        path.write_bytes(PNG_BYTES)
        assert load_cover(str(path)).content_type == "image/png"
        assert load_cover(path).content_type == "image/png"

    def test_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(CoverNotFoundError):
            load_cover(tmp_path / "absent.png")

    def test_directory_raises(self, tmp_path) -> None:
        with pytest.raises(CoverUnreadableError):
            load_cover(tmp_path)

    def test_unsupported_bytes_raise(self, tmp_path) -> None:
        path = tmp_path / "cover.jpg"
        path.write_bytes(b"\x00\x01\x02\x03")
        with pytest.raises(CoverUnsupportedFormatError):
            load_cover(path)

    def test_empty_file_raises(self, tmp_path) -> None:
        path = tmp_path / "cover.png"
        path.write_bytes(b"")
        with pytest.raises(CoverInvalidDataError):
            load_cover(path)


class TestLoadCoverFromImage:
    def test_valid_image_is_returned_unchanged(self) -> None:
        cover = Image(data=PNG_BYTES, content_type="image/png", alt_text="A")
        assert load_cover(cover) is cover

    def test_image_without_media_type_is_sniffed_on_a_copy(self) -> None:
        cover = Image(data=PNG_BYTES, alt_text="A")
        resolved = load_cover(cover)
        assert resolved is not cover
        assert resolved.content_type == "image/png"
        assert resolved.data == cover.data
        assert resolved.alt_text == cover.alt_text
        # The caller's image is never mutated.
        assert cover.content_type is None

    def test_declared_media_type_wins_over_bytes(self) -> None:
        # Like the EPUB builder's content images, the declared type is
        # trusted; no deep decoding is performed at the input boundary.
        cover = Image(data=JPEG_BYTES, content_type="image/png")
        assert load_cover(cover) is cover

    def test_empty_data_raises(self) -> None:
        with pytest.raises(CoverInvalidDataError):
            load_cover(Image(data=b""))

    def test_unsupported_declared_type_raises(self) -> None:
        with pytest.raises(CoverUnsupportedFormatError):
            load_cover(Image(data=b"x", content_type="image/webp"))

    def test_unknown_bytes_without_type_raise(self) -> None:
        with pytest.raises(CoverUnsupportedFormatError):
            load_cover(Image(data=b"\x00\x01\x02"))

    def test_errors_share_a_common_base(self) -> None:
        assert issubclass(CoverNotFoundError, CoverError)
        assert issubclass(CoverUnreadableError, CoverError)
        assert issubclass(CoverUnsupportedFormatError, CoverError)
        assert issubclass(CoverInvalidDataError, CoverError)

    def test_supported_types_match_builder_formats(self) -> None:
        # Every accepted cover must be renderable by the EPUB builder without
        # any format conversion.
        assert SUPPORTED_COVER_MEDIA_TYPES == frozenset(
            {
                "image/jpeg",
                "image/jpg",
                "image/png",
                "image/gif",
                "image/svg+xml",
            }
        )


# --------------------------------------------------------------------------- #
# Covered EPUB generation
# --------------------------------------------------------------------------- #


class TestCoveredEpub:
    @pytest.mark.parametrize(
        "media_type",
        ["image/jpeg", "image/png", "image/gif", "image/svg+xml"],
    )
    def test_cover_image_is_packaged_with_its_bytes(
        self, tmp_path, media_type: str
    ) -> None:
        payload, _ = _scanned_cover(media_type)
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(payload=payload, content_type=media_type), out)
        resource = _cover_image_name(media_type)
        with _read_archive(out) as archive:
            assert f"EPUB/{resource}" in archive.namelist()
            assert archive.read(f"EPUB/{resource}") == payload

    @pytest.mark.parametrize(
        "media_type",
        ["image/jpeg", "image/png", "image/gif", "image/svg+xml"],
    )
    def test_cover_manifest_media_type_and_properties(
        self, tmp_path, media_type: str
    ) -> None:
        payload, _ = _scanned_cover(media_type)
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(payload=payload, content_type=media_type), out)
        resource = _cover_image_name(media_type)
        manifest = ET.fromstring(_opf(out)).find("{http://www.idpf.org/2007/opf}manifest")
        items = {
            item.get("id"): item
            for item in manifest
            if item.tag.endswith("item")
        }
        cover_item = items[COVER_IMAGE_ID]
        # The cover image is marked properties="cover-image" and its declared
        # media type matches the packaged format exactly.
        assert cover_item.get("href") == resource
        assert cover_item.get("media-type") == media_type
        assert cover_item.get("properties") == "cover-image"

    def test_cover_metadata_points_at_the_cover_image(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(), out)
        metadata = ET.fromstring(_opf(out)).find(
            "{http://www.idpf.org/2007/opf}metadata"
        )
        covers = [
            element
            for element in metadata
            if element.tag.endswith("meta")
            and element.get("name") == "cover"
        ]
        assert len(covers) == 1
        assert covers[0].get("content") == COVER_IMAGE_ID

    def test_cover_page_renders_the_image(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(payload=PNG_BYTES, content_type="image/png"), out)
        with _read_archive(out) as archive:
            assert f"EPUB/{COVER_PAGE_FILE}" in archive.namelist()
            document = archive.read(f"EPUB/{COVER_PAGE_FILE}").decode("utf-8")
        assert 'src="images/cover.png"' in document
        assert '<title>Cover</title>' in document
        # The page links the project stylesheet (existing EPUB CSS approach).
        assert f'href="{STYLESHEET_RESOURCE}"' in document

    def test_cover_page_is_reflowable(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(), out)
        with _read_archive(out) as archive:
            document = archive.read(f"EPUB/{COVER_PAGE_FILE}").decode("utf-8")
        # Semantic, class-based markup: no inline layout, no fixed dimensions.
        assert 'class="image"' in document
        assert "style=" not in document
        assert "width=" not in document
        assert "height=" not in document
        assert "page-break" not in document
        # The cover document is well-formed XHTML.
        ET.fromstring(document)

    def test_cover_page_precedes_content_in_spine(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        book = _covered_book()
        book.chapters.append(
            Chapter(title="Chapter 2", blocks=[Paragraph(text="Two.")])
        )
        build_epub(book, out)
        assert _spine_idrefs(_opf(out)) == [
            "nav",
            COVER_PAGE_ID,
            "chapter-00",
            "chapter-01",
        ]

    def test_cover_is_not_a_navigation_entry(self, tmp_path) -> None:
        book = _covered_book()
        book.chapters.append(
            Chapter(title="Chapter 2", blocks=[Paragraph(text="Two.")])
        )
        out = tmp_path / "covered.epub"
        build_epub(book, out)
        # EPUB 3 nav: no link to the cover page.
        with _read_archive(out) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert COVER_PAGE_FILE not in nav
        assert "cover.xhtml" not in nav
        # EPUB 2 NCX: no navPoint for the cover.
        with _read_archive(out) as archive:
            ncx = archive.read("EPUB/toc.ncx").decode("utf-8")
        assert COVER_PAGE_FILE not in ncx
        # EbookLib's TOC is exactly the chapters.
        re_read = epub.read_epub(str(out))
        assert [link.title for link in re_read.toc] == [
            "Chapter 1",
            "Chapter 2",
        ]
        # The spine references the cover page exactly once.
        assert _spine_idrefs(_opf(out)).count(COVER_PAGE_ID) == 1

    def test_cover_does_not_duplicate_the_packaged_image(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(), out)
        with _read_archive(out) as archive:
            images = [
                name
                for name in archive.namelist()
                if name.endswith((".jpg", ".png", ".gif", ".svg"))
            ]
        assert images == [f"EPUB/{_cover_image_name('image/jpeg')}"]

    def test_covered_epub_passes_structural_validation(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(payload=PNG_BYTES, content_type="image/png"), out)
        result = validate_epub(out)
        assert result.valid, result.format_report()
        assert result.error_count == 0

    def test_repeated_builds_are_logically_identical(self, tmp_path) -> None:
        first = tmp_path / "one.epub"
        second = tmp_path / "two.epub"
        build_epub(_covered_book(), first)
        build_epub(_covered_book(), second)
        with _read_archive(first) as a, _read_archive(second) as b:
            names_a, names_b = sorted(a.namelist()), sorted(b.namelist())
            assert names_a == names_b
            for name in names_a:
                if name.endswith((".xhtml", ".css", ".opf", ".jpg")):
                    assert a.read(name) == b.read(name), name

    def test_cover_without_content_type_is_inferred(self, tmp_path) -> None:
        book = _covered_book()
        book.cover = Image(data=PNG_BYTES)
        out = tmp_path / "covered.epub"
        build_epub(book, out)
        with _read_archive(out) as archive:
            assert f"EPUB/{_cover_image_name('image/png')}" in archive.namelist()

    def test_invalid_cover_bytes_raise_invalid_image(self, tmp_path) -> None:
        book = _covered_book()
        book.cover = Image(data=b"\x00\x01\x02")
        out = tmp_path / "x.epub"
        with pytest.raises(InvalidImageError):
            build_epub(book, out)
        assert not out.exists()


# --------------------------------------------------------------------------- #
# No-cover regression
# --------------------------------------------------------------------------- #


class TestNoCoverRegression:
    def test_no_cover_book_produces_no_cover_resources(self, tmp_path) -> None:
        out = tmp_path / "plain.epub"
        build_epub(
            Book(
                metadata=BookMetadata(title="Plain"),
                chapters=[Chapter(blocks=[Paragraph(text="Plain.")])],
            ),
            out,
        )
        with _read_archive(out) as archive:
            names = archive.namelist()
        assert "EPUB/images/cover.png" not in names
        assert f"EPUB/{COVER_PAGE_FILE}" not in names
        opf = _opf(out)
        assert 'name="cover"' not in opf
        assert "cover-image" not in opf
        # Reading order is unchanged: nav first, then the chapters only.
        assert _spine_idrefs(opf) == ["nav", "chapter-00"]

    def test_no_cover_navigation_is_unchanged(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(title="Alpha", blocks=[Paragraph(text="a")]),
                Chapter(title="Beta", blocks=[Paragraph(text="b")]),
            ]
        )
        out = tmp_path / "plain.epub"
        build_epub(book, out)
        re_read = epub.read_epub(str(out))
        assert [link.title for link in re_read.toc] == ["Alpha", "Beta"]
        assert len(re_read.spine) == 3  # nav + two chapters

    def test_cover_image_is_never_used_as_content(self, tmp_path) -> None:
        """The cover must not leak into chapter bodies."""
        book = Book(
            chapters=[
                Chapter(
                    title="Ch",
                    blocks=[
                        Paragraph(text="Text"),
                        Image(
                            data=PNG_BYTES,
                            content_type="image/png",
                            alt_text="An in-book diagram",
                        ),
                    ],
                )
            ]
        )
        book.cover = Image(data=JPEG_BYTES, content_type="image/jpeg")
        out = tmp_path / "both.epub"
        build_epub(book, out)
        with _read_archive(out) as archive:
            body = archive.read("EPUB/chapter-00.xhtml").decode("utf-8")
        assert "images/cover" not in body
        assert body.count("<img") == 1


# --------------------------------------------------------------------------- #
# Cover + ordinary images coexist
# --------------------------------------------------------------------------- #

class TestCoverWithOrdinaryImages:
    def test_cover_and_content_images_are_packaged_separately(
        self, tmp_path
    ) -> None:
        book = Book(
            metadata=BookMetadata(title="Illustrated"),
            chapters=[
                Chapter(
                    title="Ch",
                    blocks=[
                        Image(
                            data=PNG_BYTES,
                            content_type="image/png",
                            alt_text="First diagram",
                        ),
                        Paragraph(text="Text between."),
                        Image(
                            data=JPEG_BYTES,
                            content_type="image/jpeg",
                            alt_text="Second picture",
                        ),
                    ],
                )
            ],
        )
        book.cover = Image(data=GIF_BYTES, content_type="image/gif")
        out = tmp_path / "mixed.epub"
        build_epub(book, out)
        with _read_archive(out) as archive:
            names = archive.namelist()
            assert "EPUB/f-0-0.png" in names
            assert "EPUB/s-0-2.jpg" in names
            assert "EPUB/images/cover.gif" in names
        result = validate_epub(out)
        assert result.valid, result.format_report()


# --------------------------------------------------------------------------- #
# Pipeline wiring (cover kwarg)
# --------------------------------------------------------------------------- #


class TestPipelineCover:
    def test_convert_pdf_to_book_accepts_a_cover_path(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "book.pdf")
        cover_path = tmp_path / "cover.png"
        cover_path.write_bytes(PNG_BYTES)
        book = convert_pdf_to_book(pdf, _NoOcr(), cover=cover_path)
        assert isinstance(book.cover, Image)
        assert book.cover.data == PNG_BYTES
        assert book.cover.content_type == "image/png"

    def test_convert_pdf_to_book_preserves_an_injected_image(
        self, tmp_path
    ) -> None:
        pdf = _make_text_pdf(tmp_path / "book.pdf")
        cover = Image(data=JPEG_BYTES, content_type="image/jpeg")
        book = convert_pdf_to_book(pdf, _NoOcr(), cover=cover)
        assert book.cover is cover

    def test_convert_pdf_to_book_defaults_to_no_cover(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "book.pdf")
        book = convert_pdf_to_book(pdf, _NoOcr())
        assert book.cover is None

    def test_convert_pdf_to_epub_accepts_a_cover_path(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "book.pdf")
        cover_path = tmp_path / "cover.png"
        cover_path.write_bytes(PNG_BYTES)
        out = tmp_path / "book.epub"
        convert_pdf_to_epub(pdf, out, cover=cover_path)
        with _read_archive(out) as archive:
            assert "EPUB/images/cover.png" in archive.namelist()
        assert validate_epub(out).valid

    def test_invalid_cover_fails_fast_with_no_output(self, tmp_path) -> None:
        pdf = _make_text_pdf(tmp_path / "book.pdf")
        out = tmp_path / "book.epub"
        with pytest.raises(CoverNotFoundError):
            convert_pdf_to_epub(pdf, out, cover=tmp_path / "missing.jpg")
        assert not out.exists()

    def test_cover_reaches_the_built_epub_from_convert_pdf_to_epub(
        self, tmp_path
    ) -> None:
        pdf = _make_text_pdf(tmp_path / "book.pdf")
        out = tmp_path / "book.epub"
        convert_pdf_to_epub(pdf, out, cover=Image(data=JPEG_BYTES))
        with _read_archive(out) as archive:
            assert "EPUB/images/cover.jpg" in archive.namelist()