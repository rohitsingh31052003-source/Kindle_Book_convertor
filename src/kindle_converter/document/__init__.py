"""Format-independent document model.

This package holds the intermediate representation produced by the PDF
layer and consumed by the EPUB layer. The models describe the logical
structure of a book and are independent of any concrete input or output
format.

M4.4 adds explicit, optional cover input handling: :func:`load_cover` turns
a cover source (a filesystem path or an existing
:class:`~kindle_converter.document.models.Image`) into a validated cover
``Image`` carried on ``Book.cover``. Covers are deliberately explicit --
nothing is auto-detected -- and the :class:`CoverError` subclasses describe
every input failure (missing, unreadable, unsupported, or empty).
"""

from .cover import (
    CoverError,
    CoverInvalidDataError,
    CoverNotFoundError,
    CoverUnreadableError,
    CoverUnsupportedFormatError,
    load_cover,
)
from .models import (
    Book,
    BookMetadata,
    Chapter,
    DocumentBlock,
    Heading,
    Image,
    PageBreak,
    Paragraph,
)

__all__ = [
    "Book",
    "BookMetadata",
    "Chapter",
    "CoverError",
    "CoverInvalidDataError",
    "CoverNotFoundError",
    "CoverUnreadableError",
    "CoverUnsupportedFormatError",
    "DocumentBlock",
    "Heading",
    "Image",
    "PageBreak",
    "Paragraph",
    "load_cover",
]