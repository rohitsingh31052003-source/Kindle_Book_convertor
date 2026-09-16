"""Kindle-ready EPUB foundation tests (Milestone 4.1).

Deterministic and offline: every PDF is generated on the fly with PyMuPDF and
OCR is injected as a fake engine, so the normal suite never needs Tesseract
(the M4.1 requirement). The tests cover the single production path

    PDF -> convert_pdf_to_book -> Book -> build_epub -> EPUB

for TEXT, SCANNED, and MIXED documents, and inspect the generated container
directly: chapters, ordering, navigation, spine, metadata, images, page-break
semantics, and the Kindle-oriented stylesheet.

Formal EPUB validation is M4.2 scope; the structural checks here only catch
obvious integration failures.
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
    Heading,
    Image,
    PageBreak,
    Paragraph,
)
from kindle_converter.epub import (
    EPUBGenerationError,
    NoChaptersError,
    build_epub,
)
from kindle_converter.epub.builder import STYLESHEET_RESOURCE

PAGE_WIDTH = 595
PAGE_HEIGHT = 842

#: Body text long enough to be classified as meaningful native text.
TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)
SECOND_LINE = (
    "Far away, beyond the last of the great towers, the city lay grey "
    "and silent under a sky that promised rain before the evening came."
)

#: Short, unique per-page markers that never wrap inside the test textbox.
MARKERS = ("MARKER ONE", "MARKER TWO", "MARKER THREE")

#: A tiny but recognizable PNG/JPEG payload (mirrors test_epub_builder.py).
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00\x01\x02\x03"


class FakeOCREngine:
    """Deterministic ``OCREngine`` returning one predefined text per call."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[pymupdf.Pixmap] = []

    def recognize(self, image: pymupdf.Pixmap) -> str:
        self.calls.append(image)
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return self.texts[index]


def _pixmap(color: tuple[int, int, int] = (180, 60, 40)) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 240, 180))
    pix.set_rect(pix.irect, color)
    return pix


def make_text_pdf(path: Path, *, pages: int = 2, title: str = "") -> Path:
    """A text-only PDF whose pages each carry unique body text."""
    doc = pymupdf.open()
    if title:
        doc.set_metadata({"title": title})
    for index in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_textbox(
            pymupdf.Rect(72, 120, 500, 700),
            f"{TEXT_LINE}\n\n{SECOND_LINE}\n\n{MARKERS[index]}",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


def make_text_pdf_with_image(path: Path) -> Path:
    """A text PDF with one embedded image (on page 1) for image tests."""
    doc = pymupdf.open()
    doc.set_metadata({"title": "Illustrated Book"})
    for _ in range(2):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_textbox(
            pymupdf.Rect(72, 120, 500, 620),
            f"{TEXT_LINE}\n\n{SECOND_LINE}",
            fontname="helv",
            fontsize=12,
        )
    doc[0].insert_image(pymupdf.Rect(72, 660, 240, 800), pixmap=_pixmap())
    doc.save(str(path))
    doc.close()
    return path


def make_scanned_pdf(path: Path, *, pages: int = 2) -> Path:
    """A PDF whose pages are full-page images with no text at all."""
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page.insert_image(pymupdf.Rect(40, 40, 555, 800), pixmap=_pixmap())
    doc.save(str(path))
    doc.close()
    return path


def make_mixed_pdf(path: Path) -> Path:
    """One text page followed by one image-only page (classified MIXED)."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_textbox(
        pymupdf.Rect(72, 120, 500, 400),
        f"{TEXT_LINE}\n\n{SECOND_LINE}",
        fontname="helv",
        fontsize=12,
    )
    scanned = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    scanned.insert_image(pymupdf.Rect(40, 40, 555, 800), pixmap=_pixmap())
    doc.save(str(path))
    doc.close()
    return path


# --------------------------------------------------------------------------- #
# EPUB inspection helpers
# --------------------------------------------------------------------------- #


def read_archive(path: Path) -> zipfile.ZipFile:
    """Open a generated EPUB as a zip archive (asserting it exists)."""
    assert Path(path).is_file(), f"EPUB was not written to {path}"
    return zipfile.ZipFile(path)


def chapter_names(archive: zipfile.ZipFile) -> list[str]:
    """Every chapter document name, in deterministic (sorted) order."""
    return sorted(
        name
        for name in archive.namelist()
        if re.fullmatch(r"EPUB/chapter-\d+\.xhtml", name)
    )


def chapter_bodies(archive: zipfile.ZipFile) -> list[str]:
    """The ``<body>`` content of every chapter document, in order."""
    bodies: list[str] = []
    for name in chapter_names(archive):
        document = archive.read(name).decode("utf-8")
        start = document.index("<body>") + len("<body>")
        end = document.index("</body>")
        bodies.append(document[start:end])
    return bodies


def full_bodies(path: Path) -> str:
    """All chapter bodies of an EPUB concatenated (reading order)."""
    with read_archive(path) as archive:
        return "".join(chapter_bodies(archive))


def package_opf(path: Path) -> str:
    """The raw ``content.opf`` package document."""
    with read_archive(path) as archive:
        return archive.read("EPUB/content.opf").decode("utf-8")


def navigation(path: Path) -> str:
    """The raw EPUB 3 navigation document."""
    with read_archive(path) as archive:
        return archive.read("EPUB/nav.xhtml").decode("utf-8")


def stylesheet(path: Path) -> str:
    """The stylesheet packaged inside the EPUB."""
    with read_archive(path) as archive:
        return archive.read(f"EPUB/{STYLESHEET_RESOURCE}").decode("utf-8")


_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def logical_snapshot(path: Path) -> dict[str, object]:
    """A time-independent snapshot of an EPUB's logical content.

    EbookLib always stamps its own container metadata (``dcterms:modified``
    and ZIP entry times), so byte equality across builds cannot be demanded
    of the library. Everything the *builder* controls -- resource names,
    chapter documents, package metadata, stylesheet -- is compared here, with
    that library timestamp normalized away.
    """
    with read_archive(path) as archive:
        names = sorted(archive.namelist())
        documents = {
            name: archive.read(name)
            for name in names
            if name.endswith((".xhtml", ".css", ".opf"))
        }
    return {
        "names": names,
        "documents": documents,
        "opf": _TIMESTAMP.sub("<timestamp>", package_opf(path)),
    }


# --------------------------------------------------------------------------- #
# A/B/C: TEXT, SCANNED, and MIXED -> Book -> EPUB through one path
# --------------------------------------------------------------------------- #


class TestTextPdfToEpub:
    def test_text_pdf_flows_pdf_book_epub(self, tmp_path) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=2, title="Text Book")
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(pdf, out)

        re_read = epub.read_epub(str(out))
        assert re_read.title == "Text Book"
        assert len(re_read.toc) == 1
        body = full_bodies(out)
        assert "Winston Smith" in body
        assert "MARKER ONE" in body and "MARKER TWO" in body

    def test_text_pdf_does_not_need_the_ocr_stack(
        self, tmp_path, monkeypatch
    ) -> None:
        """A text PDF never constructs an OCR engine (M4.1 laziness)."""
        from kindle_converter.pdf import ocr as ocr_module

        class _ExplodingEngine:
            def __init__(self) -> None:  # pragma: no cover - must not run
                raise AssertionError("Tesseract must not be constructed")

        monkeypatch.setattr(ocr_module, "TesseractEngine", _ExplodingEngine)
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=1)
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(pdf, out)

        assert out.is_file()

    def test_text_pdf_never_calls_an_injected_engine(self, tmp_path) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=3)
        engine = FakeOCREngine(["unused"])
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(pdf, out, engine=engine)

        assert engine.calls == []

    def test_page_breaks_survive_without_fixed_pagination(self, tmp_path) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=3)
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(pdf, out)

        body = full_bodies(out)
        assert body.count('class="page-break"') == 2  # one per page boundary
        positions = [
            body.index("MARKER ONE"),
            body.index('class="page-break"'),
            body.index("MARKER TWO"),
        ]
        assert positions == sorted(positions)


class TestScannedPdfToEpub:
    def test_scanned_pdf_flows_ocr_book_epub(self, tmp_path) -> None:
        pdf = make_scanned_pdf(tmp_path / "scanned.pdf", pages=2)
        engine = FakeOCREngine(
            [
                "First scanned paragraph.\n\nSecond scanned paragraph.",
                "Scanned page two text.",
            ]
        )
        out = tmp_path / "scanned.epub"

        convert_pdf_to_epub(pdf, out, engine=engine)

        assert len(engine.calls) == 2
        body = full_bodies(out)
        assert "First scanned paragraph." in body
        assert "Second scanned paragraph." in body
        assert "Scanned page two text." in body
        assert body.count('class="page-break"') == 1

    def test_scanned_book_matches_the_epub_pipeline(self, tmp_path) -> None:
        """One EPUB rendering path: pipeline == Book + build_epub."""
        pdf = make_scanned_pdf(tmp_path / "scanned.pdf", pages=2)
        piped = tmp_path / "piped.epub"
        manual = tmp_path / "manual.epub"

        convert_pdf_to_epub(
            pdf, piped, engine=FakeOCREngine(["Scanned text."])
        )
        book = convert_pdf_to_book(pdf, FakeOCREngine(["Scanned text."]))
        build_epub(book, manual)

        assert logical_snapshot(piped) == logical_snapshot(manual)

    def test_scanned_page_images_are_packaged(self, tmp_path) -> None:
        """M2.13 images extracted for scanned pages stay in the EPUB."""
        pdf = make_scanned_pdf(tmp_path / "scanned.pdf", pages=2)
        out = tmp_path / "scanned.epub"

        convert_pdf_to_epub(pdf, out, engine=FakeOCREngine(["Scanned text."]))

        with read_archive(out) as archive:
            images = [
                name
                for name in archive.namelist()
                if name.startswith("EPUB/image-")
            ]
        assert images == ["EPUB/image-001.png"]


class TestMixedPdfToEpub:
    def test_mixed_pdf_flows_pdf_book_epub(self, tmp_path) -> None:
        pdf = make_mixed_pdf(tmp_path / "mixed.pdf")
        engine = FakeOCREngine(["Ocr text of the scanned page."])
        out = tmp_path / "mixed.epub"

        convert_pdf_to_epub(pdf, out, engine=engine)

        assert len(engine.calls) == 1  # only the image-only page needs OCR
        body = full_bodies(out)
        assert "Winston Smith" in body  # native page preserved
        assert "Ocr text of the scanned page." in body
        # The native page's paragraph is not OCR'd, so it appears first.
        assert body.index("Winston Smith") < body.index("Ocr text")

    def test_mixed_pdf_output_is_deterministic(self, tmp_path) -> None:
        pdf = make_mixed_pdf(tmp_path / "mixed.pdf")
        first = tmp_path / "first.epub"
        second = tmp_path / "second.epub"

        convert_pdf_to_epub(pdf, first, engine=FakeOCREngine(["Ocr text."]))
        convert_pdf_to_epub(pdf, second, engine=FakeOCREngine(["Ocr text."]))

        assert logical_snapshot(first) == logical_snapshot(second)


# --------------------------------------------------------------------------- #
# D/E: Structure preservation and navigation (Book -> EPUB)
# --------------------------------------------------------------------------- #


def _structured_book() -> Book:
    """A three-chapter book exercising every document block type."""
    return Book(
        metadata=BookMetadata(
            title="Structured Book",
            author="Test Author",
            language="en",
            publisher="Test Publisher",
            identifier="urn:isbn:9780000000000",
            description="A deterministic fixture.",
            subject="testing",
        ),
        chapters=[
            Chapter(
                title="Opening",
                blocks=[
                    Heading(text="Opening", level=1),
                    Paragraph(text="First paragraph."),
                    PageBreak(),
                    Paragraph(text="Second paragraph."),
                    Heading(text="Section A", level=2),
                    Paragraph(text="Third paragraph."),
                    Heading(text="Deep Section", level=3),
                    Image(
                        data=PNG_BYTES,
                        content_type="image/png",
                        alt_text="Plate one",
                    ),
                ],
            ),
            Chapter(title="Middle", blocks=[Paragraph(text="Middle text.")]),
            Chapter(title="Closing", blocks=[Paragraph(text="Closing text.")]),
        ],
    )


class TestStructurePreservation:
    def test_chapters_keep_their_order(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        with read_archive(out) as archive:
            assert chapter_names(archive) == [
                "EPUB/chapter-00.xhtml",
                "EPUB/chapter-01.xhtml",
                "EPUB/chapter-02.xhtml",
            ]
            bodies = chapter_bodies(archive)
        assert "First paragraph." in bodies[0]
        assert "Middle text." in bodies[1]
        assert "Closing text." in bodies[2]

    def test_headings_and_paragraphs_stay_semantic(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        with read_archive(out) as archive:
            document = archive.read("EPUB/chapter-00.xhtml").decode("utf-8")
            body = chapter_bodies(archive)[0]
        assert "<h1>Opening</h1>" in body
        assert "<h2>Section A</h2>" in body
        assert "<h3>Deep Section</h3>" in body
        assert "<p>First paragraph.</p>" in body
        # Headings are not flattened into styled paragraphs.
        assert "<p><b>Opening</b>" not in body
        # The document is well-formed XHTML.
        ET.fromstring(document)

    def test_block_order_is_preserved(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        body = full_bodies(out)
        order = [
            body.index("First paragraph."),
            body.index('class="page-break"'),
            body.index("Second paragraph."),
            body.index("<h2>Section A</h2>"),
            body.index("Third paragraph."),
            body.index("<h3>Deep Section</h3>"),
            body.index("<img"),
        ]
        assert order == sorted(order)

    def test_metadata_is_preserved(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        opf = package_opf(out)
        assert "<dc:title>Structured Book</dc:title>" in opf
        assert "<dc:language>en</dc:language>" in opf
        assert "<dc:publisher>Test Publisher</dc:publisher>" in opf
        assert "<dc:description>A deterministic fixture.</dc:description>" in opf
        assert "<dc:subject>testing</dc:subject>" in opf
        re_read = epub.read_epub(str(out))
        assert re_read.title == "Structured Book"
        assert re_read.uid == "urn:isbn:9780000000000"
        creators = re_read.get_metadata("DC", "creator")
        assert creators and creators[0][0] == "Test Author"


class TestNavigation:
    def test_nav_lists_chapters_in_order(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        nav = navigation(out)
        positions = [
            nav.index(">Opening</a>"),
            nav.index(">Middle</a>"),
            nav.index(">Closing</a>"),
        ]
        assert positions == sorted(positions)
        assert 'href="chapter-00.xhtml"' in nav
        assert 'href="chapter-02.xhtml"' in nav

    def test_toc_and_spine_are_deterministic(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        opf = package_opf(out)
        spine = re.findall(r'<itemref idref="([^"]+)"', opf)
        assert spine == ["nav", "chapter-00", "chapter-01", "chapter-02"]
        # NCX navigation exists for older readers, alongside the EPUB 3 nav.
        assert "EPUB/toc.ncx" in read_archive(out).namelist()
        re_read = epub.read_epub(str(out))
        assert [link.title for link in re_read.toc] == [
            "Opening",
            "Middle",
            "Closing",
        ]
        assert [link.href for link in re_read.toc] == [
            "chapter-00.xhtml",
            "chapter-01.xhtml",
            "chapter-02.xhtml",
        ]


# --------------------------------------------------------------------------- #
# F: Images
# --------------------------------------------------------------------------- #


def _book_with_images() -> Book:
    return Book(
        chapters=[
            Chapter(
                title="Plates",
                blocks=[
                    Image(
                        data=PNG_BYTES,
                        content_type="image/png",
                        alt_text="First picture",
                    ),
                    Paragraph(text="Between the plates."),
                    Image(
                        data=JPEG_BYTES,
                        content_type="image/jpeg",
                        alt_text="Second picture",
                    ),
                ],
            )
        ]
    )


class TestImageHandling:
    def test_image_bytes_and_media_types_are_preserved(self, tmp_path) -> None:
        out = tmp_path / "images.epub"

        build_epub(_book_with_images(), out)

        with read_archive(out) as archive:
            names = archive.namelist()
            pngs = [n for n in names if n.endswith(".png")]
            jpegs = [n for n in names if n.endswith(".jpg")]
            assert len(pngs) == 1 and len(jpegs) == 1
            assert archive.read(pngs[0]) == PNG_BYTES
            assert archive.read(jpegs[0]) == JPEG_BYTES
            sources = re.findall(
                r'<img[^>]*src="([^"]+)"', chapter_bodies(archive)[0]
            )
        assert sources == [
            pngs[0].removeprefix("EPUB/"),
            jpegs[0].removeprefix("EPUB/"),
        ]
        opf = package_opf(out)
        assert 'media-type="image/png"' in opf
        assert 'media-type="image/jpeg"' in opf

    def test_image_order_is_deterministic(self, tmp_path) -> None:
        first = tmp_path / "first.epub"
        second = tmp_path / "second.epub"

        build_epub(_book_with_images(), first)
        build_epub(_book_with_images(), second)

        assert logical_snapshot(first) == logical_snapshot(second)

    def test_image_markup_is_reflowable(self, tmp_path) -> None:
        out = tmp_path / "images.epub"

        build_epub(_book_with_images(), out)

        with read_archive(out) as archive:
            body = chapter_bodies(archive)[0]
        tags = re.findall(r"<img[^>]*>", body)
        assert len(tags) == 2
        for tag in tags:
            assert 'class="image"' in tag
            # No fixed geometry: the image scales to the reading width.
            assert "width=" not in tag
            assert "height=" not in tag
            assert "style=" not in tag
        css = stylesheet(out)
        assert "max-width: 100%" in css
        assert "height: auto" in css

    def test_pdf_image_reaches_the_epub(self, tmp_path) -> None:
        pdf = make_text_pdf_with_image(tmp_path / "illustrated.pdf")
        out = tmp_path / "illustrated.epub"

        convert_pdf_to_epub(pdf, out)

        with read_archive(out) as archive:
            names = archive.namelist()
            body = "".join(chapter_bodies(archive))
        assert "EPUB/image-001.png" in names
        assert 'src="image-001.png"' in body


# --------------------------------------------------------------------------- #
# G: Kindle-oriented stylesheet
# --------------------------------------------------------------------------- #


class TestKindleStylesheet:
    def test_stylesheet_is_packaged_and_linked(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        with read_archive(out) as archive:
            assert f"EPUB/{STYLESHEET_RESOURCE}" in archive.namelist()
            for name in chapter_names(archive):
                document = archive.read(name).decode("utf-8")
                assert f'href="{STYLESHEET_RESOURCE}"' in document
        assert 'media-type="text/css"' in package_opf(out)

    def test_stylesheet_provides_reflowable_defaults(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)
        css = stylesheet(out)

        # Body text: relative size, readable line height.
        assert "font-size: 1em" in css
        assert "line-height: 1.5" in css
        # Paragraph spacing.
        assert "margin: 0 0 1em 0" in css
        # Headings keep a semantic, size-decreasing hierarchy.
        assert "h1 { font-size: 1.6em; }" in css
        assert "page-break-after: avoid" in css
        # Images are constrained to the reading width.
        assert "max-width: 100%" in css
        assert "height: auto" in css
        # Page breaks reflow instead of reproducing PDF pagination.
        assert "page-break-after: always" in css
        assert "break-after: always" in css

    def test_stylesheet_uses_no_fixed_or_web_layout_techniques(
        self, tmp_path
    ) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)
        css = stylesheet(out)

        for forbidden in (
            "position: absolute",
            "position: fixed",
            "display: grid",
            "display: flex",
            "vh",
            "vw",
            "px",
            "@media",
            "url(",
            "animation",
            "transition",
        ):
            assert forbidden not in css, f"stylesheet must not use {forbidden!r}"

    def test_no_javascript_anywhere_in_the_epub(self, tmp_path) -> None:
        out = tmp_path / "structured.epub"

        build_epub(_structured_book(), out)

        with read_archive(out) as archive:
            for name in archive.namelist():
                if not name.endswith(".xhtml"):
                    continue
                document = archive.read(name).decode("utf-8")
                assert "<script" not in document
                assert "javascript:" not in document


# --------------------------------------------------------------------------- #
# Determinism and container behavior
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_repeated_text_builds_are_logically_identical(self, tmp_path) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=2, title="Stability")
        first = tmp_path / "a.epub"
        second = tmp_path / "b.epub"

        convert_pdf_to_epub(pdf, first)
        convert_pdf_to_epub(pdf, second)

        assert logical_snapshot(first) == logical_snapshot(second)

    def test_only_the_library_container_metadata_varies(self, tmp_path) -> None:
        """EbookLib's dcterms:modified timestamp is the accepted variation."""
        book = _structured_book()
        first = tmp_path / "a.epub"
        second = tmp_path / "b.epub"

        build_epub(book, first)
        build_epub(book, second)

        opf_first = package_opf(first)
        opf_second = package_opf(second)
        assert '<meta property="dcterms:modified">' in opf_first
        assert _TIMESTAMP.sub("<timestamp>", opf_first) == _TIMESTAMP.sub(
            "<timestamp>", opf_second
        )


class TestContainerAndErrors:
    def test_required_container_resources_exist(self, tmp_path) -> None:
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(make_text_pdf(tmp_path / "text.pdf", pages=1), out)

        with read_archive(out) as archive:
            assert archive.read("mimetype") == b"application/epub+zip"
            names = archive.namelist()
        for required in (
            "META-INF/container.xml",
            "EPUB/content.opf",
            "EPUB/nav.xhtml",
            "EPUB/toc.ncx",
            f"EPUB/{STYLESHEET_RESOURCE}",
            "EPUB/chapter-00.xhtml",
        ):
            assert required in names

    def test_referenced_images_exist_in_the_archive(self, tmp_path) -> None:
        out = tmp_path / "images.epub"

        build_epub(_book_with_images(), out)

        with read_archive(out) as archive:
            body = chapter_bodies(archive)[0]
            names = archive.namelist()
        sources = re.findall(r'<img[^>]*src="([^"]+)"', body)
        assert sources
        for source in sources:
            assert f"EPUB/{source}" in names

    def test_failures_stay_identifiable_epub_errors(self, tmp_path) -> None:
        """EPUB failures remain output-layer domain errors (M4.1)."""
        out = tmp_path / "empty.epub"

        with pytest.raises(NoChaptersError):
            build_epub(Book(), out)
        assert not out.exists()

        with pytest.raises(EPUBGenerationError):
            build_epub({"not": "a book"}, out)  # type: ignore[arg-type]
        assert not out.exists()





