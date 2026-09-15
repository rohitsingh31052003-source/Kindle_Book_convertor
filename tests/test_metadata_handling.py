"""M2.12 Metadata handling tests.

These tests cover the conservative metadata layer:

* the shared :class:`~kindle_converter.document.models.BookMetadata` model
  (the seven supported fields, empty/default semantics, and backwards
  compatibility with existing ``Book`` construction),
* public PDF metadata extraction (:func:`extract_pdf_metadata`) from
  synthetic in-memory PDFs,
* deterministic resolution (:func:`resolve_metadata`) with the documented
  explicit-wins / PDF-fills-gaps rule,
* the non-mutating guarantee of the handling layer,
* public API exports, and
* end-to-end integration with the existing extractor and EPUB builder.

Nothing here depends on the network, external files, machine state, or the
current date/time.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pymupdf
import pytest
from ebooklib import epub

from kindle_converter.document import Book, BookMetadata, Chapter, Paragraph
from kindle_converter.pdf import (
    PDFReadError,
    extract_book,
    extract_pdf_metadata,
    resolve_metadata,
)

PAGE_WIDTH = 595
PAGE_HEIGHT = 842

# The seven M2.12 metadata fields in a stable order, with sample values that
# exercise punctuation/case so untested normalization would be caught.
ALL_FIELDS = {
    "title": "A Tale, of Two Cities!",
    "author": "Dr. Jane Q. Author",
    "language": "en",
    "publisher": "Example Press, Ltd.",
    "description": "Two cities, one revolution.",
    "subject": "fiction; classic",
    "identifier": "urn:isbn:9780000000000",
}


# --------------------------------------------------------------------------- #
# Synthetic in-memory PDF helpers
# --------------------------------------------------------------------------- #


def make_pdf_bytes(metadata: dict | None = None) -> bytes:
    """Build a single-page text PDF as bytes, optionally with metadata."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page.insert_text(
        (72, 200), "Reliable metadata test body text here.", fontsize=12
    )
    if metadata:
        doc.set_metadata(metadata)
    data = doc.tobytes()
    doc.close()
    return data


def write_pdf(path: Path, metadata: dict | None = None) -> Path:
    path.write_bytes(make_pdf_bytes(metadata))
    return path


def open_doc(metadata: dict | None = None) -> pymupdf.Document:
    """Open an in-memory PDF as a Document (caller owns it)."""
    return pymupdf.open(stream=make_pdf_bytes(metadata), filetype="pdf")


def read_opf(path: Path) -> str:
    """Read the ``content.opf`` of a written EPUB as text."""
    with zipfile.ZipFile(path) as archive:
        return archive.read("EPUB/content.opf").decode("utf-8")


# --------------------------------------------------------------------------- #
# 1. Metadata model construction
# --------------------------------------------------------------------------- #


class TestBookMetadataModel:
    def test_constructs_with_all_supported_fields(self) -> None:
        metadata = BookMetadata(**ALL_FIELDS)
        for field, value in ALL_FIELDS.items():
            assert getattr(metadata, field) == value

    def test_defaults_to_empty_string_for_every_field(self) -> None:
        metadata = BookMetadata()
        for field in ALL_FIELDS:
            assert getattr(metadata, field) == ""
        assert metadata.is_empty is True

    def test_is_empty_false_when_any_new_field_set(self) -> None:
        assert BookMetadata(description="desc").is_empty is False
        assert BookMetadata(subject="subj").is_empty is False

    def test_is_empty_uses_non_empty_string_truthiness(self) -> None:
        # ``is_empty`` is the model's pre-existing raw presence flag: any
        # non-empty string counts as present. Whitespace-only *resolution*
        # semantics belong to the handling layer, which treats whitespace-only
        # as missing (covered in TestResolution).
        assert BookMetadata(title="   ").is_empty is False
        assert BookMetadata().is_empty is True
        assert BookMetadata(subject="s").is_empty is False

    def test_values_preserved_exactly(self) -> None:
        # Caller-provided values must never be stripped or rewritten: an
        # author's suffix, an internal double space, and punctuation survive.
        metadata = BookMetadata(
            author="Jane C. Doe, PhD", title="Title  with  extra  spacing!"
        )
        assert metadata.author == "Jane C. Doe, PhD"
        assert metadata.title == "Title  with  extra  spacing!"


# --------------------------------------------------------------------------- #
# 22. Backwards compatibility with existing Book/document construction
# --------------------------------------------------------------------------- #


class TestBackwardsCompatibility:
    def test_book_metadata_defaults(self) -> None:
        book = Book()
        assert isinstance(book.metadata, BookMetadata)
        assert book.metadata.is_empty is True

    def test_book_explicit_metadata_without_new_fields(self) -> None:
        # Construction that omits description/subject still works.
        book = Book(
            metadata=BookMetadata(title="Old Style"),
            chapters=[Chapter(title="Chapter 1", blocks=[Paragraph(text="Hi.")])],
        )
        assert book.metadata.title == "Old Style"
        assert book.metadata.description == ""
        assert book.metadata.subject == ""
        assert len(book.chapters) == 1

    def test_keyword_and_default_construction_still_valid(self) -> None:
        metadata = BookMetadata("T", "A", "en", "P", "ID")
        assert (metadata.title, metadata.author, metadata.publisher) == (
            "T",
            "A",
            "P",
        )


# --------------------------------------------------------------------------- #
# 2. Handling layer never mutates inputs (immutability boundary)
# --------------------------------------------------------------------------- #


class TestHandlingLayerNonMutation:
    def test_resolve_returns_new_object_and_does_not_mutate_inputs(self) -> None:
        explicit = BookMetadata(title="Preferred", author="Caller")
        pdf = BookMetadata(title="PDF Title", description="PDF desc")
        snapshot_explicit = (
            explicit.title,
            explicit.author,
            explicit.description,
            explicit.subject,
        )
        snapshot_pdf = (pdf.title, pdf.description, pdf.subject)

        resolved = resolve_metadata(explicit, pdf)

        # A fresh object, never an alias of either input.
        assert resolved is not explicit
        assert resolved is not pdf
        # Inputs unchanged.
        assert (explicit.title, explicit.author, explicit.description,
                explicit.subject) == snapshot_explicit
        assert (pdf.title, pdf.description, pdf.subject) == snapshot_pdf

    def test_extract_does_not_close_caller_owned_document(self) -> None:
        doc = open_doc({"title": "Owned Doc"})
        try:
            metadata = extract_pdf_metadata(doc)
            assert metadata.title == "Owned Doc"
            assert not doc.is_closed
            assert doc.metadata.get("title") == "Owned Doc"
        finally:
            doc.close()


# --------------------------------------------------------------------------- #
# 11/12/15. PDF metadata extraction
# --------------------------------------------------------------------------- #


class TestPDFMetadataExtraction:
    PDF_META = {
        "title": "Nineteen Eighty-Four",
        "author": "George Orwell",
        "creator": "Penguin Books",
        "subject": "urn:isbn:9780141036144",
    }

    def test_maps_reliable_pdf_fields(self, tmp_path) -> None:
        path = write_pdf(tmp_path / "meta.pdf", self.PDF_META)
        metadata = extract_pdf_metadata(path)
        assert metadata.title == "Nineteen Eighty-Four"
        assert metadata.author == "George Orwell"
        # Established project mapping: creator -> publisher.
        assert metadata.publisher == "Penguin Books"
        # Established project mapping: subject (keyword) -> identifier.
        assert metadata.identifier == "urn:isbn:9780141036144"
        # description/subject have no reliable V1 PDF source and stay empty.
        assert metadata.description == ""
        assert metadata.subject == ""

    def test_extract_from_open_document(self) -> None:
        doc = open_doc({"title": "Open Title", "author": "Open Author"})
        try:
            metadata = extract_pdf_metadata(doc)
            assert metadata.title == "Open Title"
            assert metadata.author == "Open Author"
        finally:
            doc.close()

    def test_extract_agrees_with_extract_book(self, tmp_path) -> None:
        path = write_pdf(tmp_path / "agree.pdf", self.PDF_META)
        pdf_meta = extract_pdf_metadata(path)
        book = extract_book(path)
        for field in ("title", "author", "publisher", "language", "identifier"):
            assert getattr(pdf_meta, field) == getattr(book.metadata, field)

    def test_missing_metadata_is_empty(self, tmp_path) -> None:
        path = write_pdf(tmp_path / "plain.pdf")
        metadata = extract_pdf_metadata(path)
        assert metadata.is_empty is True
        for field in ALL_FIELDS:
            assert getattr(metadata, field) == ""

    def test_empty_and_whitespace_metadata_is_missing(self, tmp_path) -> None:
        path = write_pdf(
            tmp_path / "blank.pdf",
            {"title": "", "author": "   ", "creator": " \t ", "subject": ""},
        )
        metadata = extract_pdf_metadata(path)
        assert metadata.is_empty is True
        assert metadata.title == ""
        assert metadata.author == ""
        assert metadata.publisher == ""
        assert metadata.identifier == ""

    def test_extract_preserves_values_exactly(self, tmp_path) -> None:
        path = write_pdf(
            tmp_path / "faithful.pdf",
            {"title": "A  Strange, Title!", "author": "Dr. X. Y."},
        )
        metadata = extract_pdf_metadata(path)
        # Internal double space and punctuation must survive extraction.
        assert metadata.title == "A  Strange, Title!"
        assert metadata.author == "Dr. X. Y."

    def test_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(PDFReadError):
            extract_pdf_metadata(tmp_path / "missing.pdf")

    def test_unreadable_source_raises(self) -> None:
        with pytest.raises(PDFReadError):
            extract_pdf_metadata(12345)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 13/14/16/17. Metadata resolution
# --------------------------------------------------------------------------- #


class TestResolution:
    PDF_META = BookMetadata(
        title="PDF Title",
        author="Jane Doe",
        publisher="PDF Press",
        language="fr",
    )

    def test_explicit_metadata_overrides_pdf_metadata(self) -> None:
        explicit = BookMetadata(title="My Preferred Title")
        resolved = resolve_metadata(explicit, self.PDF_META)
        assert resolved.title == "My Preferred Title"
        # Other, non-conflicting PDF fields still flow through.
        assert resolved.author == "Jane Doe"

    def test_pdf_metadata_fills_missing_explicit_fields(self) -> None:
        explicit = BookMetadata(title="My Title")
        resolved = resolve_metadata(explicit, self.PDF_META)
        assert resolved.title == "My Title"
        assert resolved.author == "Jane Doe"
        assert resolved.publisher == "PDF Press"
        assert resolved.language == "fr"

    def test_whitespace_only_explicit_falls_back_to_pdf(self) -> None:
        explicit = BookMetadata(title="   ", author=" \t ")
        resolved = resolve_metadata(explicit, self.PDF_META)
        assert resolved.title == "PDF Title"
        assert resolved.author == "Jane Doe"

    def test_empty_explicit_falls_back_to_pdf(self) -> None:
        resolved = resolve_metadata(BookMetadata(), self.PDF_META)
        assert resolved.title == "PDF Title"
        assert resolved.author == "Jane Doe"

    def test_explicit_metadata_passes_through_all_fields(self) -> None:
        explicit = BookMetadata(**ALL_FIELDS)
        resolved = resolve_metadata(explicit, self.PDF_META)
        for field, value in ALL_FIELDS.items():
            assert getattr(resolved, field) == value

    def test_explicit_wins_per_field_independently(self) -> None:
        explicit = BookMetadata(title="Explicit T", author="Explicit A")
        resolved = resolve_metadata(explicit, self.PDF_META)
        assert (resolved.title, resolved.author) == ("Explicit T", "Explicit A")
        assert resolved.publisher == "PDF Press"
        assert resolved.language == "fr"

    def test_no_fabricated_metadata_when_nothing_present(self) -> None:
        resolved = resolve_metadata(BookMetadata(), BookMetadata())
        assert resolved.is_empty is True
        for field in ALL_FIELDS:
            assert getattr(resolved, field) == ""
        # No identifier or ISBN is ever manufactured.
        assert resolved.identifier == ""

    def test_rejects_non_metadata_explicit(self) -> None:
        with pytest.raises(TypeError):
            resolve_metadata("not metadata", self.PDF_META)  # type: ignore[arg-type]

    def test_rejects_non_metadata_pdf(self) -> None:
        with pytest.raises(TypeError):
            resolve_metadata(BookMetadata(), None)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# 18. Deterministic behavior
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_resolve_is_deterministic(self) -> None:
        explicit = BookMetadata(title="T", description="D")
        pdf = BookMetadata(
            title="PDF T", author="PDF A", description="PDF D", subject="PDF S"
        )
        first = resolve_metadata(explicit, pdf)
        second = resolve_metadata(explicit, pdf)
        assert first == second
        # Independent objects, identical values.
        assert first is not second

    def test_extract_is_deterministic(self, tmp_path) -> None:
        path = write_pdf(tmp_path / "d.pdf", {"title": "Det", "author": "A"})
        assert extract_pdf_metadata(path) == extract_pdf_metadata(path)


# --------------------------------------------------------------------------- #
# 19. Public API exports
# --------------------------------------------------------------------------- #


class TestPublicAPI:
    def test_exports_from_pdf_package(self) -> None:
        from kindle_converter import pdf as pdf_package

        assert hasattr(pdf_package, "extract_pdf_metadata")
        assert hasattr(pdf_package, "resolve_metadata")
        assert "extract_pdf_metadata" in pdf_package.__all__
        assert "resolve_metadata" in pdf_package.__all__

    def test_functions_callable(self) -> None:
        assert callable(extract_pdf_metadata)
        assert callable(resolve_metadata)

    def test_resolve_returns_book_metadata(self) -> None:
        resolved = resolve_metadata(BookMetadata(), BookMetadata())
        assert isinstance(resolved, BookMetadata)


# --------------------------------------------------------------------------- #
# 20/21. Integration with extractor and EPUB builder
# --------------------------------------------------------------------------- #


class TestIntegration:
    def test_extract_book_still_maps_pdf_metadata(self, tmp_path) -> None:
        path = write_pdf(
            tmp_path / "book.pdf",
            {
                "title": "Nineteen Eighty-Four",
                "author": "George Orwell",
                "creator": "Penguin Books",
                "subject": "urn:isbn:9780141036144",
            },
        )
        book = extract_book(path)
        assert book.metadata.title == "Nineteen Eighty-Four"
        assert book.metadata.author == "George Orwell"
        assert book.metadata.publisher == "Penguin Books"
        assert book.metadata.identifier == "urn:isbn:9780141036144"

    def test_extract_then_resolve_then_build_epub(self, tmp_path) -> None:
        """Explicit metadata wins over PDF, PDF fills the gaps, and the
        merged result (including description/subject) lands in the EPUB."""
        path = write_pdf(
            tmp_path / "src.pdf",
            {"title": "PDF Title", "author": "Jane Doe"},
        )
        pdf_meta = extract_pdf_metadata(path)
        explicit = BookMetadata(
            title="My Preferred Title",
            description="Recovered after the fire.",
            subject="fiction; history",
        )
        resolved = resolve_metadata(explicit, pdf_meta)

        book = extract_book(path)
        book.metadata = resolved  # Book.metadata remains the integration point
        out = tmp_path / "resolved.epub"
        from kindle_converter.epub import build_epub

        build_epub(book, out)

        opf = read_opf(out)
        assert out.exists()
        # Explicit title wins; PDF author fills the gap.
        re_read = epub.read_epub(str(out))
        assert re_read.title == "My Preferred Title"
        dc = re_read.get_metadata("DC", "creator")
        assert dc and dc[0][0] == "Jane Doe"
        # The two new fields are emitted by the EPUB builder.
        assert "<dc:description>Recovered after the fire.</dc:description>" in opf
        assert "<dc:subject>fiction; history</dc:subject>" in opf

    def test_full_pipeline_works_with_resolution(self, tmp_path) -> None:
        """The public convert pipeline still works and carries PDF metadata."""
        path = write_pdf(
            tmp_path / "p.pdf", {"title": "Pipeline Title", "author": "Road Runner"}
        )
        out = tmp_path / "p.epub"
        from kindle_converter import convert_pdf_to_epub

        convert_pdf_to_epub(path, out)
        assert out.exists()
        assert out.read_bytes()[:2] == b"PK"
        re_read = epub.read_epub(str(out))
        assert re_read.title == "Pipeline Title"
        dc = re_read.get_metadata("DC", "creator")
        assert dc and dc[0][0] == "Road Runner"