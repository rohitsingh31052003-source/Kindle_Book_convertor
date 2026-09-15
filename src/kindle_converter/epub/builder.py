"""EPUB generation from the format-independent :class:`Book` model.

Milestone 1.4. This module converts an existing
:class:`~kindle_converter.document.models.Book` -- the output of the PDF
layer -- into a reflowable EPUB. It consumes the document model only and
knows nothing about PDFs.

The mapping is deterministic: a given ``Book`` always produces the same
chapter order, block order, resource names, TOC, and CSS. EbookLib may add
its own package metadata/timestamps to the container; that is accepted
rather than fought.
"""

from __future__ import annotations

import html
import os
import re
import warnings

from ebooklib import epub

from ..document import (
    Book,
    BookMetadata,
    Heading,
    Image,
    PageBreak,
    Paragraph,
)

PathLike = str | os.PathLike[str]

#: XHTML heading tags by level. ``Heading.level`` is 1-based and unbounded
#: (the domain model only enforces ``>= 1``); the XHTML spec only defines
#: ``<h1>``..``<h6>``, so any deeper level is rendered as ``<h6>`` without
#: touching the domain model.
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")

#: Characters that may not appear in an XML/URL-safe resource name. Image
#: resource names are derived from the sanitized ``alt_text``.
_ILLEGAL_XML_CHARS = re.compile(r"[^\w.-]")

#: Supported image MIME types and their EPUB resource extension.
_CONTENT_TYPE_EXTENSIONS = {
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/svg+xml": "svg",
}

#: Magic-byte prefixes used only when ``Image.content_type`` is missing or
#: unsupported. No image-processing dependency is introduced; unsupported
#: bytes raise :class:`InvalidImageError`.
_MAGIC_BYTES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"<svg", "image/svg+xml"),
    (b"<?xml", "image/svg+xml"),
)

#: EbookLib requires a language on the package; used when the book supplies
#: none. Not invented bibliographic data -- merely a package-level default.
DEFAULT_LANGUAGE = "en"

#: EbookLib requires some title on the package; used when the book's title
#: is empty. The chapter documents and TOC remain exactly as authored.
DEFAULT_TITLE = "Untitled Book"

#: Base name of the book-wide stylesheet resource.
STYLESHEET_RESOURCE = "style.css"


class EPUBGenerationError(Exception):
    """Base class for failures while building an EPUB."""


class NoChaptersError(EPUBGenerationError):
    """Raised when the ``Book`` contains no chapters."""


class InvalidImageError(EPUBGenerationError):
    """Raised when an Image block cannot be embedded as an EPUB resource."""


def build_epub(
    book: Book, output: PathLike, *, language: str | None = None
) -> None:
    """Build a reflowable EPUB from ``book`` and write it to ``output``.

    Parameters
    ----------
    book:
        The document-model book to convert. Must be a
        :class:`~kindle_converter.document.models.Book`.
    output:
        A filesystem path (``str``, ``Path`` or ``os.PathLike``) where the
        ``.epub`` file is written. Parent directories must already exist;
        they are not created. An existing file is overwritten.
    language:
        Optional override for ``dc:language``. When ``None`` (default) the
        book's own ``BookMetadata.language`` is used, falling back to
        ``"en"`` when empty.

    Returns
    -------
    None
        Callers already hold the output path; there is no separate result.

    Raises
    ------
    EPUBGenerationError
        If ``book`` is not a ``Book``, contains no chapters, or holds an
        image that cannot be embedded.
    OSError
        If the output file cannot be written (e.g. a missing parent
        directory). Filesystem errors are deliberately not swallowed.
    """
    if not isinstance(book, Book):
        raise EPUBGenerationError(
            f"build_epub expects a Book, got {type(book).__name__}"
        )
    if not book.chapters:
        raise NoChaptersError(
            "the Book contains no chapters; refusing to write an empty EPUB"
        )
    _write(_build_epub_book(book, language), output)


# --------------------------------------------------------------------------- #
# EbookLib book construction
# --------------------------------------------------------------------------- #


def _build_epub_book(book: Book, language: str | None) -> epub.EpubBook:
    """Assemble the EbookLib book from the :class:`Book` model.

    The ``Book``'s chapters become ``EpubHtml`` documents, its images become
    ``EpubImage`` resources, and its chapters form both the TOC and the
    spine. Resource names come from the rendering pass, so the XHTML
    ``src`` attributes and the registered EPUB resources can never drift
    apart.
    """
    epub_book = epub.EpubBook()
    _add_metadata(epub_book, book.metadata, language)
    epub_book.set_identifier(
        book.metadata.identifier or book.metadata.title or "book"
    )

    stylesheet = epub.EpubItem(
        uid="stylesheet",
        file_name=STYLESHEET_RESOURCE,
        media_type="text/css",
        content=STYLESHEET_CSS.encode("utf-8"),
    )
    epub_book.add_item(stylesheet)

    chapters: list[epub.EpubHtml] = []
    images: list[tuple[Image, str]] = []
    for chapter_index, chapter in enumerate(book.chapters):
        body, chapter_images = _render_chapter(chapter, chapter_index)
        images.extend(chapter_images)
        item = epub.EpubHtml(
            uid=_chapter_resource_name(chapter_index),
            file_name=_chapter_resource_name(chapter_index) + ".xhtml",
            title=chapter.title or f"Chapter {chapter_index + 1}",
        )
        item.content = body
        item.add_link(
            href=STYLESHEET_RESOURCE, rel="stylesheet", type="text/css"
        )
        epub_book.add_item(item)
        chapters.append(item)

    for block, resource_name in images:
        epub_book.add_item(
            epub.EpubImage(
                uid=resource_name,
                file_name=resource_name,
                media_type=_resolve_content_type(block),
                content=block.data,
            )
        )

    epub_book.toc = chapters
    epub_book.add_item(epub.EpubNcx())
    epub_book.add_item(epub.EpubNav())
    epub_book.spine = ["nav", *chapters]
    return epub_book


def _add_metadata(
    epub_book: epub.EpubBook, metadata: BookMetadata, language: str | None
) -> None:
    """Push the ``BookMetadata`` fields into the EbookLib book.

    Only metadata that actually exists on the book is emitted. ``title`` and
    ``language`` are always present because EbookLib's package requires them
    (empty values fall back to EPUB-safe defaults). ``author``, ``publisher``,
    ``description``, and ``subject`` are only added when non-empty (M2.12).
    """
    epub_book.set_title(metadata.title or DEFAULT_TITLE)
    epub_book.set_language(language or metadata.language or DEFAULT_LANGUAGE)
    if metadata.author:
        epub_book.add_author(metadata.author)
    if metadata.publisher:
        epub_book.add_metadata("DC", "publisher", metadata.publisher)
    if metadata.description:
        epub_book.add_metadata("DC", "description", metadata.description)
    if metadata.subject:
        epub_book.add_metadata("DC", "subject", metadata.subject)


# --------------------------------------------------------------------------- #
# Chapter / block rendering
# --------------------------------------------------------------------------- #


def _render_chapter(
    chapter, chapter_index: int
) -> tuple[str, list[tuple[Image, str]]]:
    """Render one chapter as an XHTML body fragment.

    Returns ``(xhtml_body, images)`` where ``images`` is the ordered list of
    ``(Image, resource_name)`` pairs referenced by the fragment. EbookLib
    wraps the fragment into a full XHTML document on write.
    """
    body_parts: list[str] = []
    images: list[tuple[Image, str]] = []
    for image_index, block in enumerate(chapter.blocks):
        if isinstance(block, Paragraph):
            body_parts.append(_paragraph_html(block))
        elif isinstance(block, Heading):
            body_parts.append(_heading_html(block))
        elif isinstance(block, PageBreak):
            body_parts.append(_pagebreak_html())
        elif isinstance(block, Image):
            resource_name = _image_resource_name(
                chapter_index, image_index, block
            )
            body_parts.append(_image_html(block, resource_name))
            images.append((block, resource_name))
        else:
            raise EPUBGenerationError(
                f"unsupported block type {type(block).__name__} in EPUB builder"
            )
    return "\n".join(body_parts), images


def _escape(text: str) -> str:
    """Escape a string for safe insertion into XHTML.

    Escapes ``& < > " '`` so the result is valid in both text and
    attribute contexts.
    """
    return html.escape(str(text), quote=True)


def _paragraph_html(block: Paragraph) -> str:
    return f"<p>{_escape(block.text)}</p>"


def _heading_html(block: Heading) -> str:
    level = min(len(_HEADING_TAGS), max(1, block.level))
    tag = _HEADING_TAGS[level - 1]
    return f"<{tag}>{_escape(block.text)}</{tag}>"


def _pagebreak_html() -> str:
    return '<div class="page-break" aria-hidden="true" />'


def _image_html(block: Image, resource_name: str) -> str:
    alt = _escape(block.alt_text or "")
    return f'<img class="image" src="{resource_name}" alt="{alt}" />'


# --------------------------------------------------------------------------- #
# Deterministic resource naming
# --------------------------------------------------------------------------- #


def _chapter_resource_name(chapter_index: int) -> str:
    """Deterministic, unique base name for a chapter document."""
    return f"chapter-{chapter_index:02d}"


def _image_resource_name(
    chapter_index: int, image_index: int, block: Image
) -> str:
    """Deterministic, unique ``src`` name for an image resource.

    Names are ``{prefix}-{chapter}-{index}.{ext}`` where ``prefix`` is the
    first sanitized character of ``alt_text`` when available (``image``
    otherwise). The chapter/image indices make the name unique even when two
    images share an ``alt_text``. Resource files live flat in ``EPUB/`` next
    to the chapter documents, which keeps the XHTML ``href`` values on the
    same path level.
    """
    prefix = _sanitize(block.alt_text or "")[:1].lower()
    if not prefix or prefix.isdigit():
        prefix = "image"
    extension = _CONTENT_TYPE_EXTENSIONS.get(
        _resolve_content_type(block), "img"
    )
    return f"{prefix}-{chapter_index}-{image_index}.{extension}"


def _sanitize(text: str) -> str:
    """Keep only URL/XML-safe characters from ``text``."""
    return _ILLEGAL_XML_CHARS.sub("", text)


def _resolve_content_type(block: Image) -> str:
    """Resolve the MIME type for an image.

    A supplied, supported ``content_type`` wins. Otherwise the bytes are
    sniffed conservatively; if nothing matches, an ``InvalidImageError`` is
    raised rather than guessing.
    """
    if block.content_type in _CONTENT_TYPE_EXTENSIONS:
        return block.content_type
    for magic, media_type in _MAGIC_BYTES:
        if block.data.startswith(magic):
            return media_type
    raise InvalidImageError(
        "cannot determine the content type of an Image from its bytes; "
        "provide Image.content_type"
    )


# --------------------------------------------------------------------------- #
# CSS
# --------------------------------------------------------------------------- #

#: Minimal stylesheet for a reflowable, Kindle-friendly book. Deliberately
#: plain: no themes, custom fonts, columns, fixed positioning, or attempt to
#: reproduce PDF typography.
STYLESHEET_CSS = """\
/* Minimal stylesheet for a reflowable book. */
body {
    font-family: serif, Georgia, "Times New Roman", sans-serif;
    font-size: 1em;
    line-height: 1.5;
    margin: 0;
    padding: 0;
    color: #000;
}
p {
    margin: 0 0 1em 0;
    text-indent: 0;
}
h1, h2, h3, h4, h5, h6 {
    font-weight: bold;
    line-height: 1.3;
    margin: 1.2em 0 0.6em 0;
    page-break-after: avoid;
}
h1 { font-size: 1.6em; }
h2 { font-size: 1.4em; }
h3, h4, h5, h6 { font-size: 1.2em; }
img.image {
    max-width: 100%;
    height: auto;
}
div.page-break {
    page-break-after: always;
}
"""


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


def _write(epub_book: epub.EpubBook, output: PathLike) -> None:
    """Write the EbookLib book to ``output``, overwriting an existing file.

    EbookLib only surfaces write failures when ``raise_exceptions`` is set,
    so that option is always passed; the resulting exception propagates.
    """
    path = os.fspath(output)
    with warnings.catch_warnings():
        # EbookLib warns that raising will become the default; we already
        # opt in explicitly via ``raise_exceptions``.
        warnings.simplefilter("ignore", UserWarning)
        wrote = epub.write_epub(
            path, epub_book, options={"raise_exceptions": True}
        )
    if wrote is not True:
        raise EPUBGenerationError(
            f"EbookLib failed to write the EPUB to {output!r}"
        )