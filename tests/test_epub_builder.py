"""Tests for EPUB generation from the document model (Milestone 1.4).

All tests operate on synthetic :class:`~kindle_converter.document.Book`
objects -- no PDFs required -- and stay deterministic and offline. Most tests
inspect the written EPUB archive directly (or re-read it with EbookLib)
rather than only checking that a file exists.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from ebooklib import epub

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
    InvalidImageError,
    NoChaptersError,
    build_epub,
)

#: A tiny but recognizable PNG: the 8-byte signature plus filler.
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03"
#: A tiny but recognizable JPEG: the 3-byte SOI marker plus filler.
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00\x01\x02\x03"


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _single_chapter_book(**metadata_values) -> Book:
    """A minimalist single-chapter book with one paragraph."""
    metadata = BookMetadata(**metadata_values)
    chapter = Chapter(title="Chapter 1", blocks=[Paragraph(text="Hello.")])
    return Book(metadata=metadata, chapters=[chapter])


def read_archive(path: Path) -> zipfile.ZipFile:
    """Open a generated EPUB as a zip archive for inspection."""
    assert path.exists(), f"EPUB was not written to {path}"
    return zipfile.ZipFile(path)


def chapter_document(archive: zipfile.ZipFile, index: int) -> str:
    """The rendered XHTML body of chapter ``index`` as text."""
    return archive.read(f"EPUB/chapter-{index:02d}.xhtml").decode("utf-8")


def chapter_body(archive: zipfile.ZipFile, index: int) -> str:
    """Only the ``<body>...</body>`` portion of a chapter document."""
    document = chapter_document(archive, index)
    start = document.index("<body>") + len("<body>")
    end = document.index("</body>")
    return document[start:end]


def read_back(path: Path) -> epub.EpubBook:
    """Re-open the EPUB with EbookLib (validates package structure)."""
    return epub.read_epub(str(path))


def find_img_src(body: str) -> list[str]:
    """The ``src`` values of every ``<img>`` in a chapter body."""
    import re

    return re.findall(r'<img[^>]*src="([^"]+)"', body)


# --------------------------------------------------------------------------- #
# Chapter / block rendering
# --------------------------------------------------------------------------- #


class TestBasicChapter:
    def test_single_chapter_book(self, tmp_path) -> None:
        out = tmp_path / "out.epub"
        build_epub(_single_chapter_book(title="Solo"), out)
        with read_archive(out) as archive:
            assert "EPUB/chapter-00.xhtml" in archive.namelist()
            assert "<p>Hello.</p>" in chapter_body(archive, 0)

    def test_multiple_chapters_preserve_order(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(title="First", blocks=[Paragraph(text="One")]),
                Chapter(title="Second", blocks=[Paragraph(text="Two")]),
                Chapter(title="Third", blocks=[Paragraph(text="Three")]),
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            names = archive.namelist()
            assert "EPUB/chapter-00.xhtml" in names
            assert "EPUB/chapter-01.xhtml" in names
            assert "EPUB/chapter-02.xhtml" in names
            for index, text in enumerate(["One", "Two", "Three"]):
                assert f"<p>{text}</p>" in chapter_body(archive, index)

    def test_chapter_titles_become_document_titles(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(title="Prologue", blocks=[Paragraph(text="P")]),
                Chapter(title="Epilogue", blocks=[Paragraph(text="E")]),
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            for index, title in enumerate(["Prologue", "Epilogue"]):
                document = archive.read(
                    f"EPUB/chapter-{index:02d}.xhtml"
                ).decode("utf-8")
                assert f"<title>{title}</title>" in document

    def test_untitled_chapter_still_renders(self, tmp_path) -> None:
        book = Book(chapters=[Chapter(blocks=[Paragraph(text="Body")])])
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            assert "EPUB/chapter-00.xhtml" in archive.namelist()
            assert "<p>Body</p>" in chapter_body(archive, 0)
        # It also has a valid fallback title in the XHTML <head>.
        with read_archive(out) as archive:
            content = archive.read("EPUB/chapter-00.xhtml").decode("utf-8")
        assert "<title>Chapter 1</title>" in content

    def test_blocks_order_is_preserved(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    title="Ordered",
                    blocks=[
                        Paragraph(text="one"),
                        Heading(text="mid", level=2),
                        Paragraph(text="two"),
                        PageBreak(),
                        Paragraph(text="three"),
                    ],
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        positions = [
            body.index("<p>one</p>"),
            body.index("<h2>mid</h2>"),
            body.index("<p>two</p>"),
            body.index('class="page-break"'),
            body.index("<p>three</p>"),
        ]
        assert positions == sorted(positions)


class TestParagraphs:
    def test_paragraph_conversion(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(blocks=[Paragraph(text="Simple text.")])
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        assert "<p>Simple text.</p>" in chapter_body(read_archive(out), 0)

    def test_html_escaping(self, tmp_path) -> None:
        text = 'A & B < C > D "quotes" \'and\' it\'s'
        book = Book(chapters=[Chapter(blocks=[Paragraph(text=text)])])
        out = tmp_path / "out.epub"
        build_epub(book, out)
        document = chapter_document(read_archive(out), 0)
        assert "A &amp; B" in document
        assert "&lt; C &gt; D" in document
        assert '"quotes"' in document
        assert "&amp;" in document
        # The document parses as well-formed XML.
        import xml.etree.ElementTree as ET

        ET.fromstring(document)

    def test_empty_paragraph_renders(self, tmp_path) -> None:
        book = Book(chapters=[Chapter(blocks=[Paragraph(text="")])])
        out = tmp_path / "out.epub"
        build_epub(book, out)
        # lxml serializes an empty <p></p> as self-closing <p/>; both are
        # valid XHTML and both represent the empty paragraph.
        body = chapter_body(read_archive(out), 0)
        assert "<p/>" in body or "<p></p>" in body


class TestHeadings:
    def test_heading_levels(self, tmp_path) -> None:
        chapters = Chapter(
            blocks=[
                Heading(text="One", level=1),
                Heading(text="Two", level=2),
                Heading(text="Three", level=3),
            ]
        )
        book = Book(chapters=[chapters])
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        assert "<h1>One</h1>" in body
        assert "<h2>Two</h2>" in body
        assert "<h3>Three</h3>" in body

    def test_heading_level_above_6_clamps_to_h6(self, tmp_path) -> None:
        book = Book(
            chapters=[Chapter(blocks=[Heading(text="Deep", level=9)])]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        assert "<h6>Deep</h6>" in body
        assert "h9" not in body

    def test_heading_text_is_escaped(self, tmp_path) -> None:
        book = Book(
            chapters=[Chapter(blocks=[Heading(text="<Script> & More")])]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        assert "&lt;Script&gt;" in body
        assert "&amp;" in body


class TestPageBreak:
    def test_pagebreak_renders_a_break_element(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    blocks=[
                        Paragraph(text="before"),
                        PageBreak(),
                        Paragraph(text="after"),
                    ]
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        assert 'class="page-break"' in body
        assert body.index("before") < body.index("page-break") < body.index("after")


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


class TestImages:
    def test_image_is_embedded_and_referenced(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    blocks=[
                        Image(
                            data=PNG_BYTES,
                            content_type="image/png",
                            alt_text="A diagram",
                        )
                    ]
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            body = chapter_body(archive, 0)
            sources = find_img_src(body)
            assert len(sources) == 1
            resource = sources[0]
            # Resource is present in the archive with the same bytes.
            assert archive.read(f"EPUB/{resource}") == PNG_BYTES
            assert resource.startswith("a-") and resource.endswith(".png")

    def test_image_alt_text(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    blocks=[
                        Image(
                            data=PNG_BYTES,
                            content_type="image/png",
                            alt_text='An "illustration" & co',
                        )
                    ]
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        assert 'alt="An &quot;illustration&quot; &amp; co"' in body

    def test_image_without_alt_text_gets_empty_alt(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    blocks=[Image(data=PNG_BYTES, content_type="image/png")]
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        body = chapter_body(read_archive(out), 0)
        assert 'alt=""' in body

    def test_multiple_images_get_unique_deterministic_names(
        self, tmp_path
    ) -> None:
        book = Book(
            chapters=[
                Chapter(
                    blocks=[
                        Image(
                            data=JPEG_BYTES,
                            content_type="image/jpeg",
                            alt_text="First picture",
                        ),
                        Image(
                            data=PNG_BYTES,
                            content_type="image/png",
                            alt_text="Second diagram",
                        ),
                        # Same alt_text as the first: must still be unique.
                        Image(
                            data=JPEG_BYTES,
                            content_type="image/jpeg",
                            alt_text="First picture",
                        ),
                    ]
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            names = archive.namelist()
            # index 0 and 2 share alt_text "First picture" -> f-0-0 / f-0-2
            assert "EPUB/f-0-0.jpg" in names
            assert "EPUB/f-0-2.jpg" in names
            # index 1 has alt_text "Second diagram" -> s-0-1.png
            assert "EPUB/s-0-1.png" in names
            # All three names are distinct.
            images = [n for n in names if n.startswith("EPUB/") and n.endswith((".jpg", ".png"))]
            assert len(images) == len(set(images))

    def test_image_bytes_are_preserved(self, tmp_path) -> None:
        payload = PNG_BYTES + b"\xde\xad\xbe\xef"
        book = Book(
            chapters=[
                Chapter(
                    blocks=[Image(data=payload, content_type="image/png")]
                )
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        re_read = read_back(out)
        images = [i for i in re_read.get_items() if i.media_type == "image/png"]
        assert len(images) == 1
        assert images[0].content == payload

    def test_content_type_inferable_from_bytes_when_missing(
        self, tmp_path
    ) -> None:
        book = Book(
            chapters=[
                Chapter(blocks=[Image(data=JPEG_BYTES, alt_text="jpeg")])
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            bodies = [chapter_body(archive, 0)]
            resource = find_img_src(bodies[0])[0]
            assert resource.endswith(".jpg")

    def test_invalid_image_bytes_raise(self, tmp_path) -> None:
        book = Book(
            chapters=[Chapter(blocks=[Image(data=b"\x00\x01\x02")])]
        )
        out = tmp_path / "out.epub"
        with pytest.raises(InvalidImageError):
            build_epub(book, out)
        assert not out.exists()


# --------------------------------------------------------------------------- #
# Metadata
# --------------------------------------------------------------------------- #


class TestMetadata:
    def test_metadata_is_mapped(self, tmp_path) -> None:
        book = _single_chapter_book(
            title="The Title",
            author="The Author",
            language="fr",
            publisher="The Publisher",
            identifier="urn:isbn:9780000000000",
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        re_read = read_back(out)
        assert re_read.title == "The Title"
        assert re_read.uid == "urn:isbn:9780000000000"
        dc = re_read.get_metadata("DC", "creator")
        assert dc and dc[0][0] == "The Author"
        publisher = re_read.get_metadata("DC", "publisher")
        assert publisher and publisher[0][0] == "The Publisher"
        # EbookLib's reader normalizes dc:language to its own default of
        # "en", so the actual language is verified from the OPF package file.
        with read_archive(out) as archive:
            opf = archive.read("EPUB/content.opf").decode("utf-8")
        assert "<dc:language>fr</dc:language>" in opf

    def test_empty_metadata_falls_back(self, tmp_path) -> None:
        out = tmp_path / "out.epub"
        build_epub(_single_chapter_book(), out)
        re_read = read_back(out)
        # EbookLib requires these package fields; fallbacks are applied.
        assert re_read.title == "Untitled Book"
        assert re_read.language == "en"
        assert re_read.uid  # falls back to the title-based identifier

    def test_language_override(self, tmp_path) -> None:
        out = tmp_path / "out.epub"
        build_epub(
            _single_chapter_book(language="de"), out, language="ja"
        )
        with read_archive(out) as archive:
            opf = archive.read("EPUB/content.opf").decode("utf-8")
        assert "<dc:language>ja</dc:language>" in opf
        assert "<dc:language>de</dc:language>" not in opf


# --------------------------------------------------------------------------- #
# Navigation
# --------------------------------------------------------------------------- #


class TestNavigation:
    def test_toc_contains_chapters_in_order(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(title="Alpha", blocks=[Paragraph(text="a")]),
                Chapter(title="Beta", blocks=[Paragraph(text="b")]),
                Chapter(title="Gamma", blocks=[Paragraph(text="c")]),
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        re_read = read_back(out)
        assert [link.title for link in re_read.toc] == [
            "Alpha",
            "Beta",
            "Gamma",
        ]
        assert [link.href for link in re_read.toc] == [
            "chapter-00.xhtml",
            "chapter-01.xhtml",
            "chapter-02.xhtml",
        ]

    def test_nav_document_lists_chapters(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(title="One", blocks=[Paragraph(text="1")]),
                Chapter(title="Two", blocks=[Paragraph(text="2")]),
            ]
        )
        out = tmp_path / "out.epub"
        build_epub(book, out)
        with read_archive(out) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
            one = nav.index(">One</a>")
            two = nav.index(">Two</a>")
            assert one < two
            assert 'href="chapter-00.xhtml"' in nav
            assert 'href="chapter-01.xhtml"' in nav


# --------------------------------------------------------------------------- #
# File handling
# --------------------------------------------------------------------------- #


class TestFileHandling:
    def test_output_file_is_created(self, tmp_path) -> None:
        out = tmp_path / "sub" / "book.epub"
        out.parent.mkdir()
        build_epub(_single_chapter_book(), out)
        assert out.is_file()
        assert out.read_bytes()[: 4] == b"PK\x03\x04"

    def test_epub_opens_with_ebooklib(self, tmp_path) -> None:
        out = tmp_path / "book.epub"
        build_epub(_single_chapter_book(title="T"), out)
        re_read = read_back(out)
        # The spine exposes the nav plus exactly one chapter.
        assert len(re_read.spine) == 2
        with read_archive(out) as archive:
            assert b"application/epub+zip" == archive.read("mimetype")

    def test_existing_output_is_overwritten(self, tmp_path) -> None:
        out = tmp_path / "book.epub"
        out.write_bytes(b"not an epub")
        build_epub(_single_chapter_book(title="New"), out)
        assert out.read_bytes()[: 2] == b"PK"

    def test_missing_parent_directory_raises_oserror(self, tmp_path) -> None:
        with pytest.raises(OSError):
            build_epub(
                _single_chapter_book(), tmp_path / "does" / "not" / "exist.epub"
            )

    def test_accepts_str_and_pathlike_output(self, tmp_path) -> None:
        path = tmp_path / "book.epub"
        build_epub(_single_chapter_book(title="S"), str(path))
        assert path.exists()


# --------------------------------------------------------------------------- #
# Validation and edge cases
# --------------------------------------------------------------------------- #


class TestValidation:
    def test_non_book_input_raises(self, tmp_path) -> None:
        with pytest.raises(EPUBGenerationError):
            build_epub({"not": "a book"}, tmp_path / "x.epub")  # type: ignore[arg-type]

    def test_empty_book_raises_no_chapters(self, tmp_path) -> None:
        out = tmp_path / "x.epub"
        with pytest.raises(NoChaptersError):
            build_epub(Book(), out)
        assert not out.exists()

    def test_cannot_reference_pdf_as_input(self) -> None:
        import pymupdf

        # Even an open PDF document object is not a Book: only the Book
        # model is accepted by the EPUB layer.
        assert not isinstance(pymupdf.open(), Book)

    def test_unsupported_block_type_raises(self, tmp_path) -> None:
        # A block type cannot be invented by the domain model today, but
        # the builder must still fail loudly rather than emit garbage.
        book = Book(chapters=[Chapter(blocks=[object()])])  # type: ignore[list-item]
        out = tmp_path / "x.epub"
        with pytest.raises(EPUBGenerationError):
            build_epub(book, out)
        assert not out.exists()


# --------------------------------------------------------------------------- #
# Integration with Milestone 1.3 (extractor)
# --------------------------------------------------------------------------- #

#: A body-text line long enough to pass the analyzer's meaningful-text
#: threshold (mirrors tests/test_pdf_extractor.py).
TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)


def _make_text_pdf(path: Path, *, pages: int = 2) -> Path:
    """Create a small text-based PDF on the fly with PyMuPDF."""
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": "Integration Book"})
    second = f"Far away, {TEXT_LINE}"
    for _ in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            pymupdf.Rect(72, 100, 500, 700),
            f"{TEXT_LINE}\n\n{second}",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


class TestIntegrationWithExtractor:
    def test_extract_then_build_epub(self, tmp_path) -> None:
        """PDF -> ``extract_book`` -> Book -> ``build_epub`` -> EPUB."""
        from kindle_converter.pdf import extract_book

        pdf_path = _make_text_pdf(tmp_path / "synthetic.pdf")
        book = extract_book(pdf_path)
        assert isinstance(book, Book)

        out = tmp_path / "converted.epub"
        build_epub(book, out)
        assert out.is_file()

        re_read = read_back(out)
        assert re_read.title == "Integration Book"
        # The extractor emits a single chapter (M1.3 behavior).
        assert len(re_read.toc) == 1
        body = chapter_body(read_archive(out), 0)
        assert "Winston Smith" in body