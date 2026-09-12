"""Format-independent document model.

This package holds the intermediate representation produced by the PDF
layer and consumed by the EPUB layer. The models describe the logical
structure of a book and are independent of any concrete input or output
format.
"""

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
    "DocumentBlock",
    "Heading",
    "Image",
    "PageBreak",
    "Paragraph",
]