"""Kindle-specific formatting tests (Milestone 4.5).

Deterministic and offline. Every test inspects the **generated EPUB
artifact** (or the deterministic stylesheet contract) rather than builder
internals, and PDFs used by the pipeline tests are generated on the fly with
PyMuPDF with an injected fake OCR engine, so the suite never needs Tesseract.

Coverage:

* the centralized formatting profile and its deterministic stylesheet;
* reflowable typography (relative units only, reader-controlled fonts);
* paragraph, heading, chapter, image, blockquote, list, and page-break
  formatting;
* the prohibited-construct contract, checked with a small CSS tokenizer so a
  legitimate value can never cause a false positive;
* regressions: coverless EPUBs, M4.4 covered EPUBs, ordinary images,
  OCR-generated content, mixed native/OCR books, M2/M3 heading decisions,
  M4.2 validation, and determinism.
"""

from __future__ import annotations

import dataclasses
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pymupdf
import pytest

from kindle_converter import convert_pdf_to_epub
from kindle_converter.document import (
    Book,
    BookMetadata,
    Chapter,
    DocumentBlock,
    Heading,
    Image,
    PageBreak,
    Paragraph,
)
from kindle_converter.epub import build_epub, validate_epub
from kindle_converter.epub.builder import (
    COVER_IMAGE_ID,
    COVER_PAGE_FILE,
    COVER_PAGE_ID,
    STYLESHEET_CSS,
    STYLESHEET_RESOURCE,
)
from kindle_converter.epub.formatting import (
    DEFAULT_FORMATTING,
    KindleFormattingProfile,
    build_stylesheet,
)

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

#: Tiny but recognizable image payloads (mirrors the M4.4 cover tests).
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00\x01\x02\x03"

XHTML_NAMESPACE = "{http://www.w3.org/1999/xhtml}"


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


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


def make_text_pdf(path: Path, *, pages: int = 3) -> Path:
    """A text-only PDF whose pages each carry unique body text."""
    doc = pymupdf.open()
    doc.set_metadata({"title": "Formatting Fixture"})
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


def make_text_pdf_with_image(path: Path) -> Path:
    """A text PDF with one embedded image, for the responsive-image tests."""
    doc = pymupdf.open()
    doc.set_metadata({"title": "Illustrated Formatting Fixture"})
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


def _structured_book() -> Book:
    """A three-chapter book exercising every document block type."""
    return Book(
        metadata=BookMetadata(title="Formatted Book", language="en"),
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


def _covered_book() -> Book:
    """The structured book with an explicit M4.4 cover."""
    book = _structured_book()
    book.cover = Image(data=JPEG_BYTES, content_type="image/jpeg")
    return book


# --------------------------------------------------------------------------- #
# EPUB / CSS inspection helpers
# --------------------------------------------------------------------------- #

_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_COMMENT = re.compile(r"/\*.*?\*/", re.S)

#: Absolute (``px``/``pt``/...) and viewport (``vh``/``vw``/...) units. The
#: boundaries keep the check off legitimate text like ``1.5`` or a font name.
_ABSOLUTE_OR_VIEWPORT_UNIT = re.compile(
    r"(?<![\w.%-])(?:\d+|\d*\.\d+)(?:px|pt|pc|cm|mm|in|vh|vw|vmin|vmax)\b"
)

#: CSS properties that would make the book fixed-layout or web-page-like.
FORBIDDEN_PROPERTIES = frozenset(
    {
        "position",
        "top",
        "right",
        "bottom",
        "left",
        "z-index",
        "float",
        "clear",
        "animation",
        "transition",
        "transform",
        "columns",
        "column-count",
        "column-width",
        "column-gap",
        "column-rule",
        "gap",
        "flex",
        "flex-basis",
        "flex-direction",
        "flex-flow",
        "flex-grow",
        "flex-shrink",
        "flex-wrap",
        "justify-content",
        "align-items",
        "align-content",
        "align-self",
        "grid",
        "grid-area",
        "grid-gap",
        "grid-template-columns",
        "grid-template-rows",
    }
)

#: ``display`` values that introduce a web layout engine.
FORBIDDEN_DISPLAY_VALUES = frozenset(
    {"grid", "inline-grid", "flex", "inline-flex"}
)

#: Style-rule section markers, in the M4.5 CSS architecture order.
SECTION_MARKERS = (
    "Base / body",
    "Headings",
    "Chapters",
    "Paragraphs",
    "Images",
    "Cover",
    "Blockquotes",
    "Lists",
    "Page breaks",
)

_REFERENCE_ATTRIBUTE = re.compile(r'\b(?:src|href)="([^"]+)"')


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


def chapter_documents(archive: zipfile.ZipFile) -> list[str]:
    """The full XHTML of every chapter document, in order."""
    return [archive.read(name).decode("utf-8") for name in chapter_names(archive)]


def chapter_bodies(archive: zipfile.ZipFile) -> list[str]:
    """The ``<body>`` content of every chapter document, in order."""
    bodies: list[str] = []
    for document in chapter_documents(archive):
        start = document.index("<body>") + len("<body>")
        end = document.index("</body>")
        bodies.append(document[start:end])
    return bodies


def full_bodies(path: Path) -> str:
    """All chapter bodies of an EPUB concatenated (reading order)."""
    with read_archive(path) as archive:
        return "".join(chapter_bodies(archive))


def stylesheet(path: Path) -> str:
    """The stylesheet packaged inside the generated EPUB."""
    with read_archive(path) as archive:
        return archive.read(f"EPUB/{STYLESHEET_RESOURCE}").decode("utf-8")


def package_opf(path: Path) -> str:
    """The raw ``content.opf`` package document."""
    with read_archive(path) as archive:
        return archive.read("EPUB/content.opf").decode("utf-8")


def strip_comments(css: str) -> str:
    """Remove CSS comments so comment text never trips a compliance check."""
    return _COMMENT.sub("", css)


def parse_rules(css: str) -> list[tuple[list[str], list[tuple[str, str]]]]:
    """Parse ``css`` into ``(selectors, declarations)`` pairs.

    Comments are removed and declaration values are whitespace-normalized, so
    checks run against the actual CSS semantics instead of raw substrings.
    """
    rules: list[tuple[list[str], list[tuple[str, str]]]] = []
    for prelude, block in _RULE.findall(strip_comments(css)):
        selectors = [part.strip() for part in prelude.split(",") if part.strip()]
        declarations: list[tuple[str, str]] = []
        for declaration in block.split(";"):
            if ":" not in declaration:
                continue
            name, value = declaration.split(":", 1)
            declarations.append(
                (name.strip().lower(), " ".join(value.split()))
            )
        rules.append((selectors, declarations))
    return rules


def declarations_for(css: str, selector: str) -> dict[str, str]:
    """The merged declarations of every rule whose selector list has ``selector``.

    Merging mirrors the cascade: a selector may be styled by more than one
    rule (the shared heading rule plus a size rule, for example), and a later
    declaration wins.
    """
    merged: dict[str, str] = {}
    for selectors, declarations in parse_rules(css):
        if selector in selectors:
            merged.update(declarations)
    if not merged:
        raise AssertionError(f"the stylesheet defines no rule for {selector!r}")
    return merged


# --------------------------------------------------------------------------- #
# Formatting profile and stylesheet contract
# --------------------------------------------------------------------------- #


class TestFormattingProfile:
    def test_stylesheet_is_deterministic(self) -> None:
        assert build_stylesheet() == build_stylesheet()
        assert build_stylesheet() == build_stylesheet(DEFAULT_FORMATTING)
        assert build_stylesheet() == STYLESHEET_CSS

    def test_profile_is_an_immutable_constant(self) -> None:
        assert isinstance(DEFAULT_FORMATTING, KindleFormattingProfile)
        with pytest.raises(dataclasses.FrozenInstanceError):
            DEFAULT_FORMATTING.body_line_height = "2"  # type: ignore[misc]

    def test_stylesheet_has_no_generated_identifiers(self) -> None:
        css = build_stylesheet()
        assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}", css)
        assert "uuid" not in css.lower()

    def test_stylesheet_keeps_the_documented_section_order(self) -> None:
        css = build_stylesheet()
        positions = [css.index(marker) for marker in SECTION_MARKERS]
        assert positions == sorted(positions)

    def test_stylesheet_stays_small_and_well_formed(self) -> None:
        css = build_stylesheet()
        assert css.count("{") == css.count("}")
        assert 8 <= len(parse_rules(css)) <= 20
        assert len(css) < 2500

    def test_stylesheet_uses_no_generated_classes(self) -> None:
        class_selectors = {
            selector for selector in all_selectors(build_stylesheet())
            if "." in selector
        }
        # Only the pre-existing M4.1 class hooks may be styled.
        assert class_selectors == {"img.image", "div.page-break"}

    def test_profile_values_use_relative_units(self) -> None:
        for value in dataclasses.asdict(DEFAULT_FORMATTING).values():
            items = value if isinstance(value, tuple) else (value,)
            for item in items:
                assert not _ABSOLUTE_OR_VIEWPORT_UNIT.search(item), item

    def test_formatting_adds_no_public_configuration_api(self) -> None:
        import kindle_converter.epub as epub_package

        assert not any(
            "format" in name.lower() for name in epub_package.__all__
        )
        assert not hasattr(epub_package, "KindleFormattingProfile")
        assert not hasattr(epub_package, "build_stylesheet")

    def test_domain_model_gained_no_block_types(self) -> None:
        assert set(DocumentBlock.__args__) == {  # type: ignore[attr-defined]
            Heading,
            Paragraph,
            Image,
            PageBreak,
        }
        assert set(Book.__dataclass_fields__) == {
            "metadata",
            "chapters",
            "cover",
        }


# --------------------------------------------------------------------------- #
# Typography
# --------------------------------------------------------------------------- #


class TestReflowableTypography:
    @pytest.fixture()
    def css(self, tmp_path) -> str:
        """The stylesheet of a freshly built EPUB."""
        out = tmp_path / "typography.epub"
        build_epub(_structured_book(), out)
        return stylesheet(out)

    def test_body_keeps_reader_controlled_typography(self, css: str) -> None:
        body = declarations_for(css, "body")
        assert body["font-size"] == "1em"
        assert body["line-height"] == "1.5"
        assert body["margin"] == "0"
        assert body["padding"] == "0"
        assert body["text-align"] == "left"
        # No embedded or remote font: the reading system picks the font, and
        # a generic family terminates the conservative stack.
        assert "@font-face" not in strip_comments(css)
        assert body["font-family"].split(",")[-1].strip() in {
            "serif",
            "sans-serif",
            "monospace",
        }

    def test_no_absolute_or_viewport_units_anywhere(self, css: str) -> None:
        for value in all_values(css):
            assert not _ABSOLUTE_OR_VIEWPORT_UNIT.search(value), value

    def test_single_stylesheet_is_linked_by_every_document(
        self, tmp_path
    ) -> None:
        out = tmp_path / "linked.epub"
        build_epub(_covered_book(), out)
        with read_archive(out) as archive:
            documents = chapter_documents(archive)
            documents.append(
                archive.read(f"EPUB/{COVER_PAGE_FILE}").decode("utf-8")
            )
            stylesheets = [
                name for name in archive.namelist() if name.endswith(".css")
            ]
        assert stylesheets == [f"EPUB/{STYLESHEET_RESOURCE}"]
        for document in documents:
            assert f'href="{STYLESHEET_RESOURCE}"' in document
        assert 'media-type="text/css"' in package_opf(out)


def all_properties(css: str) -> set[str]:
    """Every CSS property name declared in ``css``."""
    return {
        name
        for _selectors, declarations in parse_rules(css)
        for name, _value in declarations
    }


def all_values(css: str) -> list[str]:
    """Every declared CSS value in ``css`` (comments removed)."""
    return [
        value
        for _selectors, declarations in parse_rules(css)
        for _name, value in declarations
    ]


def all_selectors(css: str) -> list[str]:
    """Every selector in ``css``."""
    return [
        selector
        for selectors, _declarations in parse_rules(css)
        for selector in selectors
    ]


# --------------------------------------------------------------------------- #
# Paragraphs
# --------------------------------------------------------------------------- #


class TestParagraphFormatting:
    def test_paragraphs_are_semantic_and_unclassed(self, tmp_path) -> None:
        out = tmp_path / "paragraphs.epub"
        build_epub(_structured_book(), out)
        with read_archive(out) as archive:
            body = chapter_bodies(archive)[0]
        assert body.count("<p>") == 3  # one per authored paragraph
        assert "<p class=" not in body
        assert "<p style=" not in body
        # No layout substitutes for semantic paragraphs.
        assert "<br" not in body
        assert "<p>&nbsp;</p>" not in body

    def test_paragraph_rule_is_a_modest_bottom_margin(self, tmp_path) -> None:
        out = tmp_path / "paragraphs.epub"
        build_epub(_structured_book(), out)
        paragraph = declarations_for(stylesheet(out), "p")
        assert paragraph["margin"] == "0 0 1em 0"
        assert paragraph["text-indent"] == "0"
        assert paragraph["line-height"] == "1.5"

    def test_builder_invents_no_extra_paragraphs_or_breaks(
        self, tmp_path
    ) -> None:
        book = Book(
            chapters=[
                Chapter(
                    title="Clean",
                    blocks=[
                        Paragraph(text="One."),
                        Heading(text="Section", level=2),
                        Paragraph(text="Two."),
                    ],
                )
            ]
        )
        out = tmp_path / "clean.epub"
        build_epub(book, out)
        body = full_bodies(out)
        assert body.count("<p") == 2
        assert "<br" not in body
        assert "&nbsp;" not in body


# --------------------------------------------------------------------------- #
# Headings
# --------------------------------------------------------------------------- #


class TestHeadingFormatting:
    def test_semantic_heading_hierarchy_is_preserved(self, tmp_path) -> None:
        out = tmp_path / "headings.epub"
        build_epub(_structured_book(), out)
        body = full_bodies(out)
        for expected in (
            "<h1>Opening</h1>",
            "<h2>Section A</h2>",
            "<h3>Deep Section</h3>",
        ):
            assert expected in body
        # No flattening into styled paragraphs.
        assert "<p><b>Opening</b>" not in body

    def test_heading_rule_avoids_awkward_breaks(self, tmp_path) -> None:
        out = tmp_path / "headings.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        for selector in ("h1", "h2", "h3", "h6"):
            headings = declarations_for(css, selector)
            assert headings["page-break-after"] == "avoid"
            assert headings["break-after"] == "avoid"
            assert headings["page-break-inside"] == "avoid"
            assert headings["break-inside"] == "avoid"
            assert headings["font-weight"] == "bold"
            assert headings["text-align"] == "left"

    def test_heading_sizes_decrease_with_level(self, tmp_path) -> None:
        out = tmp_path / "headings.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        values = [
            declarations_for(css, f"h{level}")["font-size"]
            for level in range(1, 7)
        ]
        assert all(value.endswith("em") for value in values)
        sizes = [float(value[:-2]) for value in values]
        assert sizes == sorted(sizes, reverse=True)
        assert sizes[0] > sizes[1] > sizes[2] >= sizes[3]

    def test_chapter_opening_heading_adds_no_leading_blank_space(
        self, tmp_path
    ) -> None:
        out = tmp_path / "headings.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        first_child_selectors = [
            selector
            for selector in all_selectors(css)
            if selector.endswith(":first-child")
        ]
        assert "h1:first-child" in first_child_selectors
        assert all(
            selector.startswith("h") for selector in first_child_selectors
        )
        for selector in first_child_selectors:
            assert declarations_for(css, selector)["margin-top"] == "0"

    def test_structural_heading_decisions_are_not_changed(
        self, tmp_path
    ) -> None:
        """M4.5 formats headings; it never re-derives them."""
        book = Book(
            chapters=[
                Chapter(
                    title="Ch",
                    blocks=[
                        Heading(text="One", level=1),
                        Paragraph(text="Body."),
                        Heading(text="Two", level=2),
                        Heading(text="Deep", level=9),
                    ],
                )
            ]
        )
        out = tmp_path / "structural.epub"
        build_epub(book, out)
        body = full_bodies(out)
        assert "<h1>One</h1>" in body
        assert "<h2>Two</h2>" in body
        assert "<h6>Deep</h6>" in body  # deep levels clamp, as before
        assert "<h9" not in body


# --------------------------------------------------------------------------- #
# Chapters
# --------------------------------------------------------------------------- #


class TestChapterFormatting:
    def test_chapters_stay_separate_documents_with_unchanged_navigation(
        self, tmp_path
    ) -> None:
        out = tmp_path / "chapters.epub"
        build_epub(_structured_book(), out)
        with read_archive(out) as archive:
            names = chapter_names(archive)
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert names == [
            "EPUB/chapter-00.xhtml",
            "EPUB/chapter-01.xhtml",
            "EPUB/chapter-02.xhtml",
        ]
        for href, title in (
            ("chapter-00.xhtml", "Opening"),
            ("chapter-01.xhtml", "Middle"),
            ("chapter-02.xhtml", "Closing"),
        ):
            assert f'href="{href}"' in nav
            assert f">{title}</a>" in nav

    def test_chapter_documents_are_valid_xhtml_with_headings(
        self, tmp_path
    ) -> None:
        out = tmp_path / "chapters.epub"
        build_epub(_structured_book(), out)
        with read_archive(out) as archive:
            documents = chapter_documents(archive)
        assert len(documents) == 3
        for document in documents:
            root = ET.fromstring(document)
            assert root.tag == f"{XHTML_NAMESPACE}html"
            body = root.find(f"{XHTML_NAMESPACE}body")
            assert body is not None
            assert len(list(body)) > 0

    def test_first_chapter_opens_with_its_heading(self, tmp_path) -> None:
        out = tmp_path / "chapters.epub"
        build_epub(_structured_book(), out)
        with read_archive(out) as archive:
            document = archive.read("EPUB/chapter-00.xhtml").decode("utf-8")
        body = document[document.index("<body>") + len("<body>"):]
        assert body.lstrip().startswith("<h1>")


# --------------------------------------------------------------------------- #
# Page breaks
# --------------------------------------------------------------------------- #


class TestPageBreakFormatting:
    def test_page_break_rule_keeps_both_properties(self, tmp_path) -> None:
        out = tmp_path / "breaks.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        rule = declarations_for(css, "div.page-break")
        assert rule["page-break-after"] == "always"
        assert rule["break-after"] == "always"
        assert rule["height"] == "0"
        assert rule["margin"] == "0"

    def test_page_breaks_mark_source_page_boundaries_only(
        self, tmp_path
    ) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=3)
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(pdf, out)

        body = full_bodies(out)
        assert body.count('class="page-break"') == 2  # one per page boundary
        assert [body.index(marker) for marker in MARKERS] == sorted(
            body.index(marker) for marker in MARKERS
        )
        # Pages do not become fixed EPUB pages, and breaks never appear
        # between every paragraph.
        assert body.count("<p") > body.count('class="page-break"')

    def test_page_break_markup_is_an_empty_marker_element(
        self, tmp_path
    ) -> None:
        out = tmp_path / "breaks.epub"
        build_epub(_structured_book(), out)
        with read_archive(out) as archive:
            documents = chapter_documents(archive)
        seen = 0
        for document in documents:
            root = ET.fromstring(document)
            for element in root.iter():
                classes = (element.get("class") or "").split()
                if "page-break" not in classes:
                    continue
                seen += 1
                assert element.tag == f"{XHTML_NAMESPACE}div"
                assert element.get("aria-hidden") == "true"
                assert not list(element)
                assert not (element.text or "").strip()
        assert seen == 1

    def test_page_breaks_do_not_follow_every_paragraph(self, tmp_path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    title="Prose",
                    blocks=[
                        Paragraph(text=f"Paragraph {index}.")
                        for index in range(6)
                    ],
                )
            ]
        )
        out = tmp_path / "prose.epub"
        build_epub(book, out)
        body = full_bodies(out)
        assert body.count("<p") == 6
        assert "page-break" not in body


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


class TestImageFormatting:
    def test_image_rule_scales_without_distorting(self, tmp_path) -> None:
        out = tmp_path / "images.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        image = declarations_for(css, "img.image")
        assert image["max-width"] == "100%"
        assert image["height"] == "auto"  # aspect ratio preserved
        assert image["display"] == "block"
        assert image["margin"].endswith("auto")  # centered when smaller
        assert image["page-break-inside"] == "avoid"
        assert image["break-inside"] == "avoid"

    def test_image_markup_keeps_no_fixed_geometry(self, tmp_path) -> None:
        out = tmp_path / "images.epub"
        build_epub(_structured_book(), out)
        with read_archive(out) as archive:
            body = "".join(chapter_bodies(archive))
            names = archive.namelist()
            tags = re.findall(r"<img[^>]*>", body)
            assert len(tags) == 1
            for tag in tags:
                assert 'class="image"' in tag
                assert 'alt="Plate one"' in tag
                assert "width=" not in tag
                assert "height=" not in tag
                assert "style=" not in tag
            source = re.search(r'<img[^>]*src="([^"]+)"', tags[0])
            assert source is not None
            # The image bytes are packaged unchanged.
            assert archive.read(f"EPUB/{source.group(1)}") == PNG_BYTES
            assert f"EPUB/{source.group(1)}" in names

    def test_cover_reuses_the_image_profile(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(), out)
        css = stylesheet(out)
        assert not [
            selector
            for selector in all_selectors(css)
            if "cover" in selector.lower()
        ]  # the cover needs no device-specific rule
        with read_archive(out) as archive:
            cover = archive.read(f"EPUB/{COVER_PAGE_FILE}").decode("utf-8")
            chapter = archive.read("EPUB/chapter-00.xhtml").decode("utf-8")
        assert 'class="image"' in cover
        assert "style=" not in cover and "width=" not in cover
        assert "page-break" not in cover
        assert "images/cover.jpg" in cover
        assert f'href="{STYLESHEET_RESOURCE}"' in cover
        assert 'src="images/cover.jpg"' not in chapter


# --------------------------------------------------------------------------- #
# Blockquotes and lists
# --------------------------------------------------------------------------- #


class TestBlockquoteAndListFormatting:
    def test_blockquote_rule_is_conservative(self, tmp_path) -> None:
        out = tmp_path / "quotes.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        quote = declarations_for(css, "blockquote")
        assert quote["margin"] == "1em 1.5em"
        assert quote["padding"] == "0"
        assert quote["line-height"] == "1.5"
        # No decorative content is generated and no quotation marks injected.
        assert "content" not in quote
        assert '"' not in quote["margin"]

    def test_list_rules_are_conservative_and_semantic(self, tmp_path) -> None:
        out = tmp_path / "lists.epub"
        build_epub(_structured_book(), out)
        css = stylesheet(out)
        for selector in ("ul", "ol"):
            declarations = declarations_for(css, selector)
            assert declarations["margin"] == "0 0 1em 1.5em"
            assert declarations["padding"] == "0"
            assert declarations["line-height"] == "1.5"
            # Numbering stays the reading system's, never manual text.
            assert "list-style" not in declarations
            assert "content" not in declarations
        item = declarations_for(css, "li")
        assert item["margin"] == "0 0 0.25em 0"
        assert item["line-height"] == "1.5"

    def test_builder_does_not_invent_blockquote_or_list_semantics(
        self, tmp_path
    ) -> None:
        """The domain model has no quote/list blocks, so none are emitted."""
        out = tmp_path / "plain.epub"
        build_epub(_structured_book(), out)
        body = full_bodies(out)
        for element in ("<blockquote", "<ul", "<ol", "<li", "<figure"):
            assert element not in body


# --------------------------------------------------------------------------- #
# CSS compliance
# --------------------------------------------------------------------------- #


class TestCssCompliance:
    @pytest.fixture()
    def css(self, tmp_path) -> str:
        out = tmp_path / "compliance.epub"
        build_epub(_covered_book(), out)
        return stylesheet(out)

    def test_uses_no_fixed_or_web_layout_properties(self, css: str) -> None:
        assert all_properties(css).isdisjoint(FORBIDDEN_PROPERTIES)

    def test_uses_no_grid_or_flex_display(self, css: str) -> None:
        for _selectors, declarations in parse_rules(css):
            for name, value in declarations:
                if name != "display":
                    continue
                assert value not in FORBIDDEN_DISPLAY_VALUES, value

    def test_uses_no_at_rules_or_remote_resources(self, css: str) -> None:
        text = strip_comments(css)
        assert not re.findall(r"@[\w-]+", text)  # no @media/@font-face/@import
        assert "url(" not in text
        assert "javascript:" not in text
        assert "expression(" not in text

    def test_uses_no_absolute_or_viewport_units(self, css: str) -> None:
        for value in all_values(css):
            assert not _ABSOLUTE_OR_VIEWPORT_UNIT.search(value), value

    def test_stylesheet_defines_every_class_the_markup_uses(
        self, tmp_path
    ) -> None:
        out = tmp_path / "compliance.epub"
        build_epub(_covered_book(), out)
        css = stylesheet(out)
        with read_archive(out) as archive:
            documents = chapter_documents(archive)
            documents.append(
                archive.read(f"EPUB/{COVER_PAGE_FILE}").decode("utf-8")
            )
        used_classes = set()
        for document in documents:
            for classes in re.findall(r'class="([^"]+)"', document):
                used_classes.update(classes.split())
        assert used_classes == {"image", "page-break"}
        selectors = all_selectors(css)
        # Every class hook the markup uses has a rule...
        assert "img.image" in selectors
        assert "div.page-break" in selectors
        # ... and the stylesheet defines no class the markup never uses.
        styled_classes = {
            selector.split(".")[1] for selector in selectors if "." in selector
        }
        assert styled_classes == used_classes

    def test_no_javascript_or_external_references_in_xhtml(
        self, tmp_path
    ) -> None:
        out = tmp_path / "compliance.epub"
        build_epub(_covered_book(), out)
        with read_archive(out) as archive:
            documents = [
                (name, archive.read(name).decode("utf-8"))
                for name in archive.namelist()
                if name.endswith(".xhtml")
            ]
        for name, document in documents:
            assert "<script" not in document, name
            assert "javascript:" not in document, name
            for event in ("onclick=", "onload=", "onerror=", "onmouseover="):
                assert event not in document, name
            for reference in _REFERENCE_ATTRIBUTE.findall(document):
                assert "://" not in reference, (name, reference)
                assert not reference.startswith("//"), (name, reference)


# --------------------------------------------------------------------------- #
# Regressions: cover, images, OCR, mixed content, validation
# --------------------------------------------------------------------------- #


class TestFormattingRegressions:
    def test_coverless_epub_stays_valid(self, tmp_path) -> None:
        out = tmp_path / "plain.epub"
        build_epub(_structured_book(), out)
        result = validate_epub(out)
        assert result.valid, result.format_report()
        assert result.error_count == 0
        with read_archive(out) as archive:
            names = archive.namelist()
        assert f"EPUB/{COVER_PAGE_FILE}" not in names
        assert "images/cover" not in " ".join(names)
        assert 'name="cover"' not in package_opf(out)

    def test_covered_epub_keeps_m4_4_cover_semantics(self, tmp_path) -> None:
        out = tmp_path / "covered.epub"
        build_epub(_covered_book(), out)
        assert validate_epub(out).valid
        opf = package_opf(out)
        assert f'id="{COVER_IMAGE_ID}"' in opf
        assert 'properties="cover-image"' in opf
        assert f'content="{COVER_IMAGE_ID}"' in opf
        spine = re.findall(r'<itemref[^>]*idref="([^"]+)"', opf)
        assert spine == [
            "nav",
            COVER_PAGE_ID,
            "chapter-00",
            "chapter-01",
            "chapter-02",
        ]
        with read_archive(out) as archive:
            nav = archive.read("EPUB/nav.xhtml").decode("utf-8")
        assert COVER_PAGE_FILE not in nav

    def test_ordinary_document_images_survive_formatting(self, tmp_path) -> None:
        pdf = make_text_pdf_with_image(tmp_path / "illustrated.pdf")
        out = tmp_path / "illustrated.epub"

        convert_pdf_to_epub(pdf, out)

        with read_archive(out) as archive:
            names = archive.namelist()
            body = "".join(chapter_bodies(archive))
        assert any(name.startswith("EPUB/image-") for name in names)
        assert re.search(r'<img class="image" src="image-\d+\.png"', body)
        assert stylesheet(out) == STYLESHEET_CSS

    def test_ocr_books_receive_the_same_formatting(self, tmp_path) -> None:
        pdf = make_scanned_pdf(tmp_path / "scanned.pdf", pages=2)
        engine = FakeOCREngine(["Scanned paragraph one.", "Scanned text two."])
        out = tmp_path / "scanned.epub"

        convert_pdf_to_epub(pdf, out, engine=engine)

        assert len(engine.calls) == 2  # OCR content was not touched by M4.5
        body = full_bodies(out)
        assert "Scanned paragraph one." in body
        assert "Scanned text two." in body
        assert "<p>" in body
        # OCR text is styled by exactly the same stylesheet as native text.
        assert stylesheet(out) == STYLESHEET_CSS
        assert validate_epub(out).valid

    def test_mixed_books_keep_their_source_semantics(self, tmp_path) -> None:
        pdf = make_mixed_pdf(tmp_path / "mixed.pdf")
        out = tmp_path / "mixed.epub"

        convert_pdf_to_epub(
            pdf, out, engine=FakeOCREngine(["Ocr text of the scanned page."])
        )

        body = full_bodies(out)
        assert body.index("Winston Smith") < body.index("Ocr text")
        assert stylesheet(out) == STYLESHEET_CSS
        assert validate_epub(out).valid

    def test_text_pdf_output_is_valid_and_unchanged_in_content(
        self, tmp_path
    ) -> None:
        pdf = make_text_pdf(tmp_path / "text.pdf", pages=3)
        out = tmp_path / "text.epub"

        convert_pdf_to_epub(pdf, out)

        body = full_bodies(out)
        for marker in MARKERS:
            assert marker in body
        assert validate_epub(out).valid


# --------------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------------- #


class TestFormattingDeterminism:
    def test_repeated_builds_share_identical_css_and_xhtml(self, tmp_path) -> None:
        first = tmp_path / "first.epub"
        second = tmp_path / "second.epub"

        build_epub(_covered_book(), first)
        build_epub(_covered_book(), second)

        with read_archive(first) as a, read_archive(second) as b:
            names_a = [
                name
                for name in sorted(a.namelist())
                if name.endswith((".xhtml", ".css"))
            ]
            names_b = [
                name
                for name in sorted(b.namelist())
                if name.endswith((".xhtml", ".css"))
            ]
            assert names_a == names_b
            for name in names_a:
                assert a.read(name) == b.read(name), name

    def test_packaged_stylesheet_equals_the_generated_stylesheet(
        self, tmp_path
    ) -> None:
        out = tmp_path / "book.epub"
        build_epub(_structured_book(), out)
        assert stylesheet(out) == build_stylesheet(DEFAULT_FORMATTING)
        assert stylesheet(out) == STYLESHEET_CSS

    def test_ocr_and_native_books_share_one_stylesheet(self, tmp_path) -> None:
        native = tmp_path / "native.epub"
        build_epub(_structured_book(), native)

        scanned = make_scanned_pdf(tmp_path / "scanned.pdf", pages=1)
        ocr = tmp_path / "ocr.epub"
        convert_pdf_to_epub(scanned, ocr, engine=FakeOCREngine(["Text."]))

        assert stylesheet(native) == stylesheet(ocr)

