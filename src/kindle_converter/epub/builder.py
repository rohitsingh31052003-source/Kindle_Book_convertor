"""EPUB generation from the format-independent :class:`Book` model.

Milestone 1.4, extended for the Kindle-ready rendering profile in M4.1 and
refined by the Kindle-specific formatting milestone M4.5. This module converts
an existing
:class:`~kindle_converter.document.models.Book` -- the output of the PDF
layer -- into a reflowable EPUB. It consumes the document model only and
knows nothing about PDFs: no page inspection, OCR, scanned-page detection,
paragraph reconstruction, reading order, or heading detection happens here.

Rendering profile (M4.1, refined by M4.5)
----------------------------------------
The output is a **reflowable ebook**, never a PDF replica:

* normal document flow only -- no fixed page dimensions, no absolute
  positioning, no viewport units, no JavaScript, no external resources;
* semantic markup: ``<h1>``..``<h6>`` stay headings, ``<p>`` stays a
  paragraph, ``<img>`` is constrained to the available reading width;
* ``PageBreak`` blocks become an empty, class-marked ``div`` whose
  ``page-break-after``/``break-after`` CSS forces a break in the reading
  system without turning a PDF page into a fixed-size EPUB page;
* a small, deterministic stylesheet (:data:`STYLESHEET_CSS`) provides
  conservative defaults for body text, paragraphs, headings, images, and
  page breaks.

M4.5 keeps that profile and refines it for Kindle-oriented *reading*: the CSS
values are centralized in one formatting profile
(:class:`kindle_converter.epub.formatting.KindleFormattingProfile`), typography
stays relative (``em``) so the reader's font controls keep working, headings
keep their hierarchy with conservative break-avoidance, the first heading of a
chapter no longer adds leading blank space, images are centered and scale down
to the reading width with their aspect ratio preserved, and semantic
blockquote/list defaults exist for content that carries them. No structure,
chapter splitting, heading decision, page-break semantic, or navigation entry
changes: M4.5 only styles the semantic content M1-M3 already produced.

The mapping is deterministic: a given ``Book`` always produces the same
chapter order, block order, resource names, TOC, and CSS. The only
non-logical variation is EbookLib's own container metadata (a
``dcterms:modified`` timestamp and ZIP entry times); that library detail is
accepted and documented rather than fought.

Cover handling (M4.4) is explicit and optional
----------------------------------------------
An optional ``Book.cover`` (:class:`~kindle_converter.document.models.Image`)
becomes an EPUB cover using EbookLib's dedicated cover mechanism: the image
is packaged as an ``EpubCover`` (so the OPF manifest marks it
``properties="cover-image"`` and the ``name="cover"`` package metadata points
at it), and a minimal XHTML cover page (:file:`cover.xhtml`) reuses the
project stylesheet and precedes the content in the spine. The cover never
joins the book's ``toc``, so it becomes no chapter, no table-of-contents
entry, and no NCX ``navPoint``; the spine is the only place that references
it. A ``Book`` without a cover produces byte-for-byte the same output as
before.
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
from .formatting import DEFAULT_FORMATTING, build_stylesheet

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

#: Deterministic base name of the packaged cover image (M4.4); the format
#: extension is appended from the cover's resolved media type
#: (``images/cover.jpg`` for a JPEG cover, and so on).
COVER_IMAGE_RESOURCE = "images/cover"

#: Manifest id of the cover image, referenced by the ``name="cover"``
#: package metadata.
COVER_IMAGE_ID = "cover-img"

#: Deterministic id and file name of the cover XHTML document.
COVER_PAGE_ID = "cover"
COVER_PAGE_FILE = "cover.xhtml"

#: The neutral, deterministic title and alt text of the cover page. Not
#: bibliographic metadata -- just the literal word used to describe the
#: front-cover image in the package document.
COVER_TITLE = "Cover"


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

    The output is a Kindle-oriented, reflowable ebook: semantic chapter
    documents, headings, paragraphs, images, and page breaks in normal
    document flow (see the module docstring for the M4.1 rendering profile).
    No PDF-derived page geometry is reproduced anywhere.

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
    spine (deterministic order: document order, with the navigation document
    first). Resource names come from the rendering pass, so the XHTML
    ``src`` attributes and the registered EPUB resources can never drift
    apart.

    The navigation document leads the spine but is marked ``linear="no"``
    (EbookLib ``("nav", "no")``): it remains a discoverable spine entry for
    EPUB 2 readers while never being paged as a reading-order document, so
    the book's first *linear* page is always the first chapter (or the
    cover page, when one is set).

    Chapter titles are represented semantically as the chapter document's
    title and its navigation entry; the chapter body is exactly the
    author's block sequence, which already contains the book's own headings.
    """
    epub_book = epub.EpubBook()
    _add_metadata(epub_book, book.metadata, language)
    epub_book.set_identifier(
        book.metadata.identifier or book.metadata.title or "book"
    )
    if book.cover is not None:
        _add_cover(epub_book, book.cover)

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

    seen_resources: set[str] = set()
    for block, resource_name in images:
        if resource_name in seen_resources:
            continue
        seen_resources.add(resource_name)
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
    if book.cover is not None:
        epub_book.spine = [("nav", "no"), COVER_PAGE_ID, *chapters]
    else:
        epub_book.spine = [("nav", "no"), *chapters]
    return epub_book


def _add_cover(epub_book: epub.EpubBook, cover: Image) -> None:
    """Register the book cover with EbookLib's dedicated cover mechanism.

    The image is packaged as an :class:`ebooklib.epub.EpubCover`, so the OPF
    manifest marks it ``properties="cover-image"`` and the ``name="cover"``
    package metadata points at its manifest id -- the EPUB cover semantics
    readers recognize. A minimal XHTML cover page (:file:`cover.xhtml`)
    reuses the project stylesheet and ``img.image`` rendering profile, so the
    cover stays reflowable like the content it precedes.

    The cover is deliberately *not* added to ``book.toc``, so it never
    becomes a chapter, a table-of-contents entry, or an NCX ``navPoint``: the
    spine is the only place that references it, exactly as an EPUB cover page
    is supposed to appear. The packaged image keeps the exact bytes and media
    type of ``cover``.
    """
    media_type = _resolve_content_type(cover)
    extension = _CONTENT_TYPE_EXTENSIONS.get(media_type, "img")
    resource_name = f"{COVER_IMAGE_RESOURCE}.{extension}"

    image_item = epub.EpubCover(uid=COVER_IMAGE_ID, file_name=resource_name)
    image_item.media_type = media_type
    image_item.content = cover.data
    epub_book.add_item(image_item)

    page_item = epub.EpubHtml(
        uid=COVER_PAGE_ID, file_name=COVER_PAGE_FILE, title=COVER_TITLE
    )
    page_item.content = (
        f'<img class="image" src="{resource_name}" alt="{_escape(COVER_TITLE)}" />'
    )
    page_item.add_link(
        href=STYLESHEET_RESOURCE, rel="stylesheet", type="text/css"
    )
    epub_book.add_item(page_item)

    epub_book.add_metadata(
        None, "meta", "", {"name": "cover", "content": COVER_IMAGE_ID}
    )


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

    Images that carry a deterministic ``image-NNN`` asset token in their
    ``alt_text`` (emitted by the M2.13 reconstruction) reuse one packaged
    asset per token: repeated placements of the same extracted asset share a
    single EPUB resource, while images without such a token keep the
    historical per-block naming.
    """
    body_parts: list[str] = []
    images: list[tuple[Image, str]] = []
    seen_asset_names: dict[str, str] = {}
    for image_index, block in enumerate(chapter.blocks):
        if isinstance(block, Paragraph):
            body_parts.append(_paragraph_html(block))
        elif isinstance(block, Heading):
            body_parts.append(_heading_html(block))
        elif isinstance(block, PageBreak):
            body_parts.append(_pagebreak_html())
        elif isinstance(block, Image):
            resource_name = _deduped_image_resource_name(
                chapter_index, image_index, block, seen_asset_names
            )
            body_parts.append(_image_html(block, resource_name))
            if not any(
                existing is block and name == resource_name
                for existing, name in images
            ):
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
    """Deterministic, unique ``src`` name for an image resource."""
    prefix = _sanitize(block.alt_text or "")[:1].lower()
    if not prefix or prefix.isdigit():
        prefix = "image"
    extension = _CONTENT_TYPE_EXTENSIONS.get(
        _resolve_content_type(block), "img"
    )
    return f"{prefix}-{chapter_index}-{image_index}.{extension}"


def _deduped_image_resource_name(
    chapter_index: int,
    image_index: int,
    block: Image,
    seen_asset_names: dict[str, str],
) -> str:
    """Return the resource name for ``block``, reusing one per asset token."""
    token = _extract_asset_token(block.alt_text or "")
    if token is None:
        return _image_resource_name(chapter_index, image_index, block)
    existing = seen_asset_names.get(token)
    if existing is not None:
        return existing
    extension = _CONTENT_TYPE_EXTENSIONS.get(
        _resolve_content_type(block), "img"
    )
    name = f"{token}.{extension}"
    seen_asset_names[token] = name
    return name


def _extract_asset_token(alt_text: str) -> str | None:
    """Return the ``image-NNN`` asset token in ``alt_text``, if present."""
    for word in re.split(r"\s+", alt_text.strip()):
        core = word.strip().lower()
        if re.fullmatch(r"image-\d+", core):
            digits = core.split("-", 1)[1]
            return f"image-{int(digits):03d}"
    return None


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

#: Conservative, Kindle-oriented stylesheet for a reflowable book (M4.1,
#: refined by M4.5). The values live in the centralized formatting profile of
#: :mod:`kindle_converter.epub.formatting`; this constant is the deterministic
#: stylesheet one :data:`DEFAULT_FORMATTING` produces. It stays deliberately
#: plain and self-contained: relative units only, normal document flow, no
#: fixed page dimensions, no absolute positioning, no viewport units, no
#: JavaScript, no animations, no external resources, and no attempt to
#: reproduce PDF typography. Page margins are left to the reading system; the
#: styling rules only control block flow, so the text reflows naturally on any
#: screen size, and the reader's own font/size controls remain effective.
STYLESHEET_CSS = build_stylesheet(DEFAULT_FORMATTING)


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