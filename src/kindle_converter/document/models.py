"""Format-independent document domain model.

This module defines the intermediate representation produced by the PDF
extraction/OCR layer and consumed by the EPUB/AZW3 generation layer.

The models deliberately know nothing about PyMuPDF, EbookLib, or any
other concrete input/output implementation. They represent the logical
structure of a book, not PDF page geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias


@dataclass(slots=True)
class BookMetadata:
    """Bibliographic metadata for a book.

    Unknown fields are represented by an empty string so that callers can
    treat them as "not provided" without a third state.
    """

    title: str = ""
    author: str = ""
    language: str = ""
    publisher: str = ""
    identifier: str = ""

    @property
    def is_empty(self) -> bool:
        """Whether no metadata field has a value."""
        return not any((self.title, self.author, self.language,
                        self.publisher, self.identifier))


@dataclass(slots=True)
class Heading:
    """A section heading in the document hierarchy.

    ``level`` is a positive integer where 1 is the most prominent level.
    """

    text: str
    level: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.level, bool) or not isinstance(self.level, int):
            raise TypeError(f"level must be an int, got {type(self.level).__name__}")
        if self.level < 1:
            raise ValueError(f"level must be >= 1, got {self.level}")


@dataclass(slots=True)
class Paragraph:
    """A self-contained block of text."""

    text: str


@dataclass(slots=True)
class Image:
    """An image embedded in a chapter.

    ``data`` holds the raw encoded bytes (for example PNG or JPEG) so the
    document model is self-contained and does not depend on the filesystem
    or on the EPUB library. The EPUB layer is responsible for choosing the
    actual file name, MIME mapping, and XHTML serialization. A path-based
    reference would couple the model to where a file exists and would break
    for in-memory extraction pipelines; an opaque abstraction would just
    shuffle the decision onto every caller.
    """

    data: bytes
    content_type: str | None = None
    alt_text: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.data, (bytes, bytearray, memoryview)):
            raise TypeError(
                f"data must be bytes-like, got {type(self.data).__name__}"
            )
        if not isinstance(self.data, bytes):
            self.data = bytes(self.data)


@dataclass(slots=True)
class PageBreak:
    """A forced page break in reading order."""


DocumentBlock: TypeAlias = Heading | Paragraph | Image | PageBreak
"""Any block that can appear in a chapter's body."""


@dataclass(slots=True)
class Chapter:
    """A chapter consisting of a title and an ordered list of blocks."""

    title: str = ""
    blocks: list[DocumentBlock] = field(default_factory=list)


@dataclass(slots=True)
class Book:
    """The top-level representation of a book.

    ``metadata`` and ``chapters`` default to a fresh metadata object and an
    empty list so that a minimal ``Book()`` is always valid.
    """

    metadata: BookMetadata = field(default_factory=BookMetadata)
    chapters: list[Chapter] = field(default_factory=list)
    cover: Image | None = None

    def add_chapter(self, chapter: Chapter) -> None:
        """Append a chapter to the book."""
        self.chapters.append(chapter)

    def add_block(self, block: DocumentBlock, chapter_index: int = -1) -> None:
        """Append a block to a chapter.

        ``chapter_index`` defaults to the last chapter, creating one first if
        the book has no chapters.
        """
        if not self.chapters:
            self.add_chapter(Chapter())
        self.chapters[chapter_index].blocks.append(block)