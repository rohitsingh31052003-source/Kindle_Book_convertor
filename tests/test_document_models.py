"""Tests for the format-independent document domain model (Milestone 1.1)."""

from dataclasses import FrozenInstanceError

import pytest

from kindle_converter.document import (
    Book,
    BookMetadata,
    Chapter,
    Heading,
    Image,
    PageBreak,
    Paragraph,
)
from kindle_converter.document.models import DocumentBlock


class TestBookMetadata:
    def test_all_fields_default_to_empty_string(self) -> None:
        metadata = BookMetadata()
        assert metadata.title == ""
        assert metadata.author == ""
        assert metadata.language == ""
        assert metadata.publisher == ""
        assert metadata.identifier == ""

    def test_creates_with_values(self) -> None:
        metadata = BookMetadata(
            title="The Great Novel",
            author="Jane Doe",
            language="en",
            publisher="Example Press",
            identifier="urn:uuid:123e4567-e89b-12d3-a456-426614174000",
        )
        assert metadata.title == "The Great Novel"
        assert metadata.author == "Jane Doe"
        assert metadata.language == "en"
        assert metadata.publisher == "Example Press"
        assert (
            metadata.identifier
            == "urn:uuid:123e4567-e89b-12d3-a456-426614174000"
        )

    def test_values_are_updateable(self) -> None:
        metadata = BookMetadata()
        metadata.title = "Later Title"
        assert metadata.title == "Later Title"

    def test_repr_shows_values(self) -> None:
        metadata = BookMetadata(title="T")
        assert "T" in repr(metadata)

    def test_is_empty_true_when_no_fields_set(self) -> None:
        assert BookMetadata().is_empty is True

    def test_is_empty_false_when_any_field_set(self) -> None:
        assert BookMetadata(title="T").is_empty is False
        assert BookMetadata(author="A").is_empty is False
        assert BookMetadata(identifier="1").is_empty is False


class TestHeading:
    def test_defaults_to_level_1(self) -> None:
        heading = Heading(text="Chapter 1")
        assert heading.text == "Chapter 1"
        assert heading.level == 1

    def test_accepts_explicit_level(self) -> None:
        heading = Heading(text="Subsection", level=3)
        assert heading.level == 3

    def test_rejects_non_integer_level(self) -> None:
        with pytest.raises(TypeError):
            Heading(text="Bogus", level="1")  # type: ignore[arg-type]

    def test_rejects_zero_or_negative_level(self) -> None:
        with pytest.raises(ValueError):
            Heading(text="Bogus", level=-1)
        with pytest.raises(ValueError):
            Heading(text="Bogus", level=0)

    def test_rejects_fractional_level(self) -> None:
        with pytest.raises(TypeError):
            Heading(text="Bogus", level=2.5)  # type: ignore[arg-type]


class TestParagraph:
    def test_creates_with_text(self) -> None:
        paragraph = Paragraph(text="Some body text.")
        assert paragraph.text == "Some body text."

    def test_accepts_empty_text(self) -> None:
        assert Paragraph(text="").text == ""

    def test_text_is_mutable(self) -> None:
        paragraph = Paragraph(text="Old")
        paragraph.text = "New"
        assert paragraph.text == "New"


class TestImage:
    def test_creates_with_raw_bytes(self) -> None:
        image = Image(data=b"\x89PNG\r\n\x1a\n")
        assert image.data == b"\x89PNG\r\n\x1a\n"
        assert image.content_type is None
        assert image.alt_text is None

    def test_accepts_content_type_and_alt_text(self) -> None:
        image = Image(
            data=b"\xff\xd8\xff\xe0",
            content_type="image/jpeg",
            alt_text="A diagram",
        )
        assert image.content_type == "image/jpeg"
        assert image.alt_text == "A diagram"

    def test_requires_data(self) -> None:
        with pytest.raises(TypeError):
            Image()  # type: ignore[call-arg]

    def test_rejects_non_bytes_data(self) -> None:
        with pytest.raises(TypeError):
            Image(data="not bytes")  # type: ignore[arg-type]


class TestPageBreak:
    def test_creates_without_arguments(self) -> None:
        assert PageBreak() is not None


class TestChapter:
    def test_creates_minimal_chapter(self) -> None:
        chapter = Chapter()
        assert chapter.title == ""
        assert chapter.blocks == []

    def test_creates_with_title(self) -> None:
        chapter = Chapter(title="Chapter 1")
        assert chapter.title == "Chapter 1"

    def test_blocks_default_is_fresh_per_chapter(self) -> None:
        first = Chapter()
        second = Chapter()
        first.blocks.append(Paragraph(text="p"))
        assert second.blocks == []

    def test_holds_ordered_blocks(self) -> None:
        chapter = Chapter(
            title="Ch 1",
            blocks=[
                Paragraph(text="First paragraph"),
                Image(data=b"abc"),
                Paragraph(text="Second paragraph"),
            ],
        )
        assert [type(b) for b in chapter.blocks] == [
            Paragraph,
            Image,
            Paragraph,
        ]

    def test_blocks_are_mutable(self) -> None:
        chapter = Chapter()
        chapter.blocks.append(Heading(text="Section"))
        assert len(chapter.blocks) == 1
        assert isinstance(chapter.blocks[0], Heading)


class TestBlockTypeAlias:
    def test_all_block_classes_are_document_blocks(self) -> None:
        blocks = [
            Heading(text="H"),
            Paragraph(text="P"),
            Image(data=b"x"),
            PageBreak(),
        ]
        assert all(isinstance(block, DocumentBlock) for block in blocks)

    def test_document_block_can_be_used_in_annotations(self) -> None:
        blocks: list[DocumentBlock] = [Heading(text="H"), PageBreak()]
        assert len(blocks) == 2

    def test_unrelated_objects_are_not_document_blocks(self) -> None:
        assert not isinstance(object(), DocumentBlock)


class TestBook:
    def test_creates_minimal_book(self) -> None:
        book = Book()
        assert isinstance(book.metadata, BookMetadata)
        assert book.metadata.is_empty is True
        assert book.chapters == []
        assert book.cover is None

    def test_default_collections_are_fresh_per_book(self) -> None:
        first = Book()
        second = Book()
        first.chapters.append(Chapter())
        assert second.chapters == []

    def test_creates_with_values(self) -> None:
        metadata = BookMetadata(title="T")
        chapter = Chapter(title="Ch 1")
        cover = Image(data=b"cover")
        book = Book(metadata=metadata, chapters=[chapter], cover=cover)
        assert book.metadata.title == "T"
        assert book.chapters == [chapter]
        assert book.cover is cover

    def test_chapters_are_mutable(self) -> None:
        book = Book()
        book.chapters.append(Chapter(title="Appended"))
        assert len(book.chapters) == 1

    def test_add_chapter(self) -> None:
        book = Book()
        book.add_chapter(Chapter(title="First"))
        book.add_chapter(Chapter(title="Second"))
        assert [c.title for c in book.chapters] == ["First", "Second"]

    def test_add_block_creates_chapter_when_none_exist(self) -> None:
        book = Book()
        book.add_block(Paragraph(text="Hello"))
        assert len(book.chapters) == 1
        assert book.chapters[0].blocks == [Paragraph(text="Hello")]

    def test_add_block_appends_to_last_chapter_by_default(self) -> None:
        book = Book()
        book.add_chapter(Chapter(title="Ch"))
        book.add_block(Heading(text="Section"))
        book.add_block(Paragraph(text="Body"))
        assert [type(b) for b in book.chapters[0].blocks] == [
            Heading,
            Paragraph,
        ]

    def test_add_block_to_specific_chapter_index(self) -> None:
        book = Book()
        book.add_chapter(Chapter(title="First"))
        book.add_chapter(Chapter(title="Second"))
        book.add_block(Paragraph(text="Into second"), chapter_index=1)
        book.add_block(Paragraph(text="Into first"), chapter_index=0)
        assert len(book.chapters[0].blocks) == 1
        assert len(book.chapters[1].blocks) == 1


class TestStructuralIntegration:
    def test_nested_structure_of_a_full_book(self) -> None:
        metadata = BookMetadata(
            title="The Great Novel",
            author="Jane Doe",
            language="en",
            publisher="Example Press",
            identifier="urn:isbn:9780000000000",
        )
        cover = Image(
            data=b"cover-bytes",
            content_type="image/jpeg",
            alt_text="Book cover",
        )

        chapter_one = Chapter(
            title="Chapter 1",
            blocks=[
                Heading(text="Chapter 1", level=1),
                Paragraph(text="It was a dark and stormy night."),
                Image(data=b"map-bytes", alt_text="A map"),
                PageBreak(),
                Paragraph(text="The wind howled."),
            ],
        )
        book = Book(
            metadata=metadata,
            chapters=[chapter_one],
            cover=cover,
        )

        assert book.metadata.title == "The Great Novel"
        assert book.metadata.author == "Jane Doe"
        assert book.cover is cover
        assert len(book.chapters) == 1
        assert book.chapters[0] is chapter_one
        assert chapter_one.blocks[0] == Heading(text="Chapter 1", level=1)
        assert chapter_one.blocks[-1] == Paragraph(text="The wind howled.")
        assert isinstance(chapter_one.blocks[3], PageBreak)

    def test_chapter_blocks_flow_in_reading_order(self) -> None:
        chapter = Chapter(
            title="Mixed",
            blocks=[
                Heading(text="Section", level=2),
                Paragraph(text="Text"),
                Image(data=b"x"),
                PageBreak(),
            ],
        )
        expected_types = [Heading, Paragraph, Image, PageBreak]
        assert [type(b) for b in chapter.blocks] == expected_types
        assert [b.level for b in chapter.blocks if isinstance(b, Heading)] == [2]