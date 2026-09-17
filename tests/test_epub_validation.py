"""EPUB validation tests (Milestone 4.2).

The suite exercises :func:`kindle_converter.epub.validate_epub` against two
kinds of input:

* **real project-generated EPUBs** -- written by the M4.1 ``build_epub`` and
  the ``convert_pdf_to_epub`` pipeline for TEXT/SCANNED/MIXED PDFs; and
* **controlled malformed EPUBs** -- small in-memory ZIP fixtures with one
  defect at a time.

Both are deterministic and fully offline: no network, no Tesseract, no
Calibre, no Kindle Previewer. Every assertion is about the structured
:class:`EPUBValidationResult` (codes, severities, locations) rather than
about raw exceptions leaking for expected invalid input.
"""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

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
from kindle_converter.epub import (
    EPUBValidationCode,
    EPUBValidationError,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
    build_epub,
    validate_epub,
)

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\x03"
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00\x01\x02\x03"

MIMETYPE = b"application/epub+zip"

#: Canonical minimal *valid* EPUB content. Individual tests swap broken
#: pieces in so each test changes exactly one thing at a time.
OPF = """\
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:test:epub</dc:identifier>
<dc:title>A Test Book</dc:title>
<dc:language>en</dc:language>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="chapter-01" href="chapter-01.xhtml" media-type="application/xhtml+xml"/>
<item id="styles" href="style.css" media-type="text/css"/>
</manifest>
<spine>
<itemref idref="nav"/>
<itemref idref="chapter-01"/>
</spine>
</package>
"""

CONTAINER = """\
<?xml version="1.0" encoding="utf-8"?>
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles>
    <rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>
"""

NAV = """\
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head><title>A Test Book</title></head>
  <body>
    <nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">
      <ol>
        <li><a href="chapter-01.xhtml">Chapter 1</a></li>
      </ol>
    </nav>
  </body>
</html>
"""

CHAPTER = """\
<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
  <head>
    <title>Chapter 1</title>
    <link rel="stylesheet" href="style.css" type="text/css"/>
  </head>
  <body>
    <h1>Chapter 1</h1>
    <p>Hello from the first chapter.</p>
    <div class="page-break" aria-hidden="true"/>
    <p>Second paragraph.</p>
  </body>
</html>
"""

CSS = """\
body { font-family: serif; }
p { margin: 0 0 1em 0; }
h1 { font-size: 1.6em; }
div.page-break { page-break-after: always; break-after: always; }
"""

NCX = """\
<?xml version="1.0" encoding="utf-8"?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">
  <head>
    <meta content="urn:test:epub" name="dtb:uid"/>
    <meta content="1" name="dtb:depth"/>
  </head>
  <docTitle><text>A Test Book</text></docTitle>
  <navMap>
    <navPoint id="chapter-01">
      <navLabel><text>Chapter 1</text></navLabel>
      <content src="chapter-01.xhtml"/>
    </navPoint>
  </navMap>
</ncx>
"""

# --------------------------------------------------------------------------- #
# Fixture builders (in-memory, deterministic, offline)
# --------------------------------------------------------------------------- #


def _container(full_path: str = "EPUB/content.opf") -> str:
    return CONTAINER.replace(
        'full-path="EPUB/content.opf"', f'full-path="{full_path}"'
    )


def _entries() -> dict[str, bytes]:
    """The canonical minimal *valid* EPUB as ``{archive_path: data}``."""
    return {
        "mimetype": MIMETYPE,
        "META-INF/container.xml": CONTAINER.encode("utf-8"),
        "EPUB/content.opf": OPF.encode("utf-8"),
        "EPUB/nav.xhtml": NAV.encode("utf-8"),
        "EPUB/chapter-01.xhtml": CHAPTER.encode("utf-8"),
        "EPUB/style.css": CSS.encode("utf-8"),
    }


def _archive(entries: dict[str, bytes]) -> bytes:
    """Zip ``entries`` into an in-memory EPUB archive (byte-safe)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def _codes(result: EPUBValidationResult) -> list[EPUBValidationCode]:
    return [issue.code for issue in result.issues]


def _replace_manifest_item(opf: str, item_id: str, replacement: str) -> str:
    """Replace the manifest ``<item>`` with ``item_id`` (format-agnostic)."""
    return re.sub(
        rf'<item[^>]*id="{item_id}"[^>]*/>', replacement, opf, count=1
    )


def _remove_manifest_item(opf: str, item_id: str) -> str:
    return _replace_manifest_item(opf, item_id, "")


def _add_manifest_item(opf: str, item: str) -> str:
    return opf.replace("</manifest>", f"{item}</manifest>", 1)


def _replace_spine(opf: str, itemrefs: str) -> str:
    """Replace the ``<spine>...</spine>`` body (format-agnostic)."""
    return re.sub(
        r"<spine>.*?</spine>",
        f"<spine>{itemrefs}</spine>",
        opf,
        count=1,
        flags=re.DOTALL,
    )


# --------------------------------------------------------------------------- #
# Real project output (M4.1 builder / pipeline)
# --------------------------------------------------------------------------- #


def _structured_book() -> Book:
    """A book exercising headings, paragraphs, page breaks, and images."""
    return Book(
        metadata=BookMetadata(
            title="Validated Book",
            author="Test Author",
            language="en",
            identifier="urn:isbn:9780000000000",
        ),
        chapters=[
            Chapter(
                title="Opening",
                blocks=[
                    Heading(text="Opening", level=1),
                    Paragraph(text="First paragraph."),
                    PageBreak(),
                    Paragraph(text="Second paragraph."),
                    Image(
                        data=PNG_BYTES,
                        content_type="image/png",
                        alt_text="Plate one",
                    ),
                ],
            ),
            Chapter(title="Closing", blocks=[Paragraph(text="Closing.")]),
        ],
    )


class TestValidProjectEpub:
    """Cases A, O, P, Q: real M4.1 output validates successfully."""

    def test_structured_book_epub_is_valid(self, tmp_path: Path) -> None:
        out = tmp_path / "book.epub"
        build_epub(_structured_book(), out)

        result = validate_epub(out)
        assert result.valid
        assert result.errors == ()
        assert result.warnings == ()
        assert result.package_document == "EPUB/content.opf"
        assert result.epub_version == "3.0"
        assert result.spine_documents == (
            "EPUB/nav.xhtml",
            "EPUB/chapter-00.xhtml",
            "EPUB/chapter-01.xhtml",
        )

    def test_book_without_images_is_valid(self, tmp_path: Path) -> None:
        """Case P: no-image books are valid (images are never mandatory)."""
        book = Book(
            metadata=BookMetadata(title="Plain", language="en"),
            chapters=[Chapter(title="Ch", blocks=[Paragraph(text="Text.")])],
        )
        out = tmp_path / "plain.epub"
        build_epub(book, out)

        result = validate_epub(out)
        assert result.valid
        assert result.errors == ()

    def test_page_breaks_validate(self, tmp_path: Path) -> None:
        """Case Q: M4.1 page-break markup has a defined stylesheet rule."""
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
        out = tmp_path / "breaks.epub"
        build_epub(book, out)

        result = validate_epub(out)
        assert result.valid
        assert result.warnings == ()

    def test_pipeline_text_epub_is_valid(self, tmp_path: Path) -> None:
        from kindle_converter import convert_pdf_to_epub

        pdf = _make_text_pdf(tmp_path / "text.pdf", pages=2)
        out = tmp_path / "text.epub"
        convert_pdf_to_epub(pdf, out)

        assert validate_epub(out).valid

    def test_pipeline_scanned_epub_is_valid(self, tmp_path: Path) -> None:
        from kindle_converter import convert_pdf_to_epub

        pdf = _make_scanned_pdf(tmp_path / "scanned.pdf")
        out = tmp_path / "scanned.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine(["Scanned text."]))

        assert validate_epub(out).valid

    def test_pipeline_mixed_epub_is_valid(self, tmp_path: Path) -> None:
        from kindle_converter import convert_pdf_to_epub

        pdf = _make_mixed_pdf(tmp_path / "mixed.pdf")
        out = tmp_path / "mixed.epub"
        convert_pdf_to_epub(pdf, out, engine=_FakeOCREngine(["OCR text."]))

        assert validate_epub(out).valid

    def test_validation_accepts_an_artifact_not_a_book(self) -> None:
        """The validator consumes an EPUB artifact, never a PDF or a Book."""
        with pytest.raises(EPUBValidationError):
            validate_epub(_structured_book())  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Layer A: ZIP / container
# --------------------------------------------------------------------------- #


class TestArchiveValidation:
    def test_invalid_zip_is_structured_error(self) -> None:
        """Case B: non-ZIP data fails as INVALID_ARCHIVE, never a raise."""
        result = validate_epub(b"this is definitely not a zip archive")
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_ARCHIVE]
        issue = result.issues[0]
        assert isinstance(issue, EPUBValidationIssue)
        assert issue.severity is EPUBValidationSeverity.ERROR
        assert "zip" in issue.message.lower()

    def test_truncated_zip_is_structured_error(self) -> None:
        result = validate_epub(_archive(_entries())[:40])
        assert not result.valid
        assert EPUBValidationCode.INVALID_ARCHIVE in _codes(result)

    def test_empty_bytes_is_invalid_archive(self) -> None:
        result = validate_epub(b"")
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_ARCHIVE]

    def test_duplicate_archive_entries_are_detected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in _entries().items():
                archive.writestr(name, data)
            archive.writestr("EPUB/duplicate.txt", b"first copy")
            archive.writestr("EPUB/duplicate.txt", b"second copy")
        result = validate_epub(buffer.getvalue())
        assert not result.valid
        assert EPUBValidationCode.DUPLICATE_ARCHIVE_ENTRY in _codes(result)
        duplicate = next(
            i for i in result.issues if i.code is EPUBValidationCode.DUPLICATE_ARCHIVE_ENTRY
        )
        assert "EPUB/duplicate.txt" in duplicate.location

    def test_invalid_entry_names_are_detected(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in _entries().items():
                archive.writestr(name, data)
            archive.writestr("../escape.txt", b"bad path")
            archive.writestr("EPUB\\backslash.txt", b"bad separator")
        result = validate_epub(buffer.getvalue())
        assert not result.valid
        assert EPUBValidationCode.INVALID_ARCHIVE_ENTRY in _codes(result)
        assert result.issues[0].location in ("../escape.txt", "EPUB\\backslash.txt")


# --------------------------------------------------------------------------- #
# mimetype
# --------------------------------------------------------------------------- #


class TestMimetypeValidation:
    def test_missing_mimetype(self) -> None:
        """Case C."""
        entries = {k: v for k, v in _entries().items() if k != "mimetype"}
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.MISSING_MIMETYPE]
        assert result.issues[0].location == "mimetype"

    def test_invalid_mimetype_content(self) -> None:
        """Case D."""
        entries = _entries()
        entries["mimetype"] = b"application/zip"
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_MIMETYPE]
        assert "application/epub+zip" in result.issues[0].message

    def test_mimetype_with_trailing_newline_is_invalid(self) -> None:
        entries = _entries()
        entries["mimetype"] = MIMETYPE + b"\n"
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_MIMETYPE]

    def test_compressed_mimetype_is_an_error(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in _entries().items():
                archive.writestr(name, data, compress_type=zipfile.ZIP_DEFLATED)
        result = validate_epub(buffer.getvalue())
        assert not result.valid
        assert EPUBValidationCode.COMPRESSED_MIMETYPE in _codes(result)

    def test_mimetype_not_first_is_an_error(self) -> None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, data in _entries().items():
                if name == "mimetype":
                    continue
                archive.writestr(name, data)
            archive.writestr("mimetype", MIMETYPE)
        result = validate_epub(buffer.getvalue())
        assert not result.valid
        assert EPUBValidationCode.MIMETYPE_NOT_FIRST in _codes(result)
        assert any(
            i.severity is EPUBValidationSeverity.ERROR
            for i in result.issues
            if i.code is EPUBValidationCode.MIMETYPE_NOT_FIRST
        )


# --------------------------------------------------------------------------- #
# META-INF/container.xml
# --------------------------------------------------------------------------- #


class TestContainerValidation:
    def test_missing_container(self) -> None:
        """Case E."""
        entries = {k: v for k, v in _entries().items() if k != "META-INF/container.xml"}
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.MISSING_CONTAINER]
        assert result.issues[0].location == "META-INF/container.xml"

    def test_malformed_container_xml(self) -> None:
        """Case F: container is not well-formed XML."""
        entries = _entries()
        entries["META-INF/container.xml"] = b'<?xml version="1.0"?><container><unclosed>'
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_CONTAINER]

    def test_container_with_wrong_root_element(self) -> None:
        entries = _entries()
        entries["META-INF/container.xml"] = (
            b'<?xml version="1.0"?><rootfiles><rootfile full-path="EPUB/content.opf"/>'
            b"</rootfiles>"
        )
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_CONTAINER]

    def test_container_without_rootfile(self) -> None:
        entries = _entries()
        entries["META-INF/container.xml"] = (
            b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument'
            b':xmlns:container"><rootfiles/></container>'
        )
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_CONTAINER in _codes(result)

    def test_rootfile_without_full_path(self) -> None:
        entries = _entries()
        entries["META-INF/container.xml"] = (
            b'<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument'
            b':xmlns:container"><rootfiles>'
            b'<rootfile media-type="application/oebps-package+xml"/>'
            b"</rootfiles></container>"
        )
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_CONTAINER in _codes(result)

    def test_missing_package_document(self) -> None:
        """Case G: container points at a file the archive does not contain."""
        entries = _entries()
        entries["META-INF/container.xml"] = _container("EPUB/missing.opf").encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.MISSING_PACKAGE_DOCUMENT]
        assert "missing.opf" in result.issues[0].message

    def test_package_document_in_subdirectory_is_found(self) -> None:
        """The container's rootfile path is resolved, not assumed at root."""
        entries = {
            "mimetype": MIMETYPE,
            "META-INF/container.xml": _container("OEBPS/content.opf").encode("utf-8"),
            "OEBPS/content.opf": OPF.encode("utf-8"),
            "OEBPS/nav.xhtml": NAV.encode("utf-8"),
            "OEBPS/chapter-01.xhtml": CHAPTER.encode("utf-8"),
            "OEBPS/style.css": CSS.encode("utf-8"),
        }
        result = validate_epub(_archive(entries))
        assert result.valid
        assert result.package_document == "OEBPS/content.opf"

    def test_missing_rootfile_falls_back_to_present_rootfile(self) -> None:
        """The first rootfile that *exists* wins; missing ones are skipped."""
        container = (
            '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument'
            ':xmlns:container" version="1.0"><rootfiles>'
            '<rootfile full-path="EPUB/gone.opf" media-type="application/oebps-package+xml"/>'
            '<rootfile full-path="EPUB/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>"
        )
        entries = _entries()
        entries["META-INF/container.xml"] = container.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert result.valid
        assert result.package_document == "EPUB/content.opf"

    def test_unexpected_rootfile_media_type_is_an_error(self) -> None:
        container = CONTAINER.replace(
            "application/oebps-package+xml", "application/octet-stream"
        )
        entries = _entries()
        entries["META-INF/container.xml"] = container.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_CONTAINER in _codes(result)
        assert not any(
            i.code is EPUBValidationCode.UNEXPECTED_ROOTFILE_MEDIA_TYPE
            for i in result.warnings
        )


# --------------------------------------------------------------------------- #
# OPF package document
# --------------------------------------------------------------------------- #


class TestPackageValidation:
    def test_malformed_opf_xml(self) -> None:
        """Case H: package document is not well-formed XML."""
        entries = _entries()
        entries["EPUB/content.opf"] = b"<package><metadata>"
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_PACKAGE_DOCUMENT]

    def test_opf_wrong_root_element(self) -> None:
        entries = _entries()
        entries["EPUB/content.opf"] = (
            b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"/>'
        )
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_PACKAGE_DOCUMENT]

    @pytest.mark.parametrize("version", ["2.0", "1.2", "4.0", "abc"])
    def test_unsupported_epub_versions_are_rejected(self, version: str) -> None:
        entries = _entries()
        entries["EPUB/content.opf"] = OPF.replace(
            'version="3.0"', f'version="{version}"'
        ).encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.UNSUPPORTED_EPUB_VERSION in _codes(result)

    def test_missing_version_attribute_is_rejected(self) -> None:
        entries = _entries()
        entries["EPUB/content.opf"] = OPF.replace(
            ' version="3.0"', ""
        ).encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.UNSUPPORTED_EPUB_VERSION in _codes(result)
        assert result.epub_version is None

    @pytest.mark.parametrize("version", ["3", "3.0", "3.1", "3.2"])
    def test_supported_epub_versions_are_accepted(self, version: str) -> None:
        entries = _entries()
        entries["EPUB/content.opf"] = OPF.replace(
            'version="3.0"', f'version="{version}"'
        ).encode("utf-8")
        assert validate_epub(_archive(entries)).valid

    def test_epub_version_is_reported(self) -> None:
        result = validate_epub(_archive(_entries()))
        assert result.epub_version == "3.0"


class TestMetadataValidation:
    def test_missing_metadata_element(self) -> None:
        """Case I: no <metadata> at all."""
        opf = re.sub(
            r"<metadata[^>]*>.*?</metadata>",
            "",
            OPF,
            count=1,
            flags=re.DOTALL,
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.MISSING_METADATA]

    @pytest.mark.parametrize(
        "field, declared",
        [
            ("title", "<dc:title>A Test Book</dc:title>"),
            ("language", "<dc:language>en</dc:language>"),
            ("identifier", '<dc:identifier id="uid">urn:test:epub</dc:identifier>'),
        ],
    )
    def test_required_metadata_fields_are_mandatory(self, field, declared) -> None:
        opf = OPF.replace(declared, "")
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.MISSING_METADATA in _codes(result)
        assert f"dc:{field}" in result.issues[0].message

    def test_empty_metadata_values_are_rejected(self) -> None:
        opf = OPF.replace("<dc:title>A Test Book</dc:title>", "<dc:title>   </dc:title>")
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.MISSING_METADATA in _codes(result)

    def test_optional_metadata_is_not_required(self) -> None:
        """Author, publisher, description, subject must never be mandatory."""
        assert validate_epub(_archive(_entries())).valid

    def test_unique_identifier_must_reference_an_identifier(self) -> None:
        opf = OPF.replace('unique-identifier="uid"', 'unique-identifier="other"')
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_PACKAGE_DOCUMENT in _codes(result)

    def test_metadata_in_foreign_prefix_still_matches(self) -> None:
        opf = OPF.replace(
            'xmlns:dc="http://purl.org/dc/elements/1.1/"',
            'xmlns:d="http://purl.org/dc/elements/1.1/"',
        ).replace("<dc:", "<d:").replace("</dc:", "</d:")
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        assert validate_epub(_archive(entries)).valid


# --------------------------------------------------------------------------- #
# Manifest
# --------------------------------------------------------------------------- #


class TestManifestValidation:
    def test_missing_manifest_element(self) -> None:
        opf = re.sub(
            r"<manifest[^>]*>.*?</manifest>",
            "",
            OPF,
            count=1,
            flags=re.DOTALL,
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.MISSING_MANIFEST]

    def test_empty_manifest_is_invalid(self) -> None:
        opf = re.sub(r"<item[^>]*/>", "", OPF)
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)

    def test_broken_manifest_reference(self) -> None:
        """Case J: a manifest item names a file the archive lacks."""
        opf = OPF.replace('href="chapter-01.xhtml"', 'href="missing.xhtml"')
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.MISSING_RESOURCE in _codes(result)
        assert "missing.xhtml" in next(
            i.message for i in result.issues if i.code is EPUBValidationCode.MISSING_RESOURCE
        )

    def test_manifest_item_without_id(self) -> None:
        opf = _replace_manifest_item(
            OPF,
            "chapter-01",
            '<item href="chapter-01.xhtml" media-type="application/xhtml+xml"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)

    def test_manifest_item_without_href(self) -> None:
        opf = _replace_manifest_item(
            OPF,
            "chapter-01",
            '<item id="chapter-01" media-type="application/xhtml+xml"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)

    def test_manifest_item_without_media_type(self) -> None:
        opf = _replace_manifest_item(
            OPF,
            "chapter-01",
            '<item id="chapter-01" href="chapter-01.xhtml"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)

    def test_duplicate_manifest_ids_are_detected(self) -> None:
        opf = _replace_manifest_item(
            OPF,
            "nav",
            '<item id="chapter-01" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)
        assert "more than once" in next(
            i.message for i in result.issues if i.code is EPUBValidationCode.INVALID_MANIFEST
        )

    def test_duplicate_manifest_hrefs_are_detected(self) -> None:
        opf = _add_manifest_item(
            OPF,
            '<item id="nav-dup" href="nav.xhtml" '
            'media-type="application/xhtml+xml" properties="nav"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)
        assert "same resource" in next(
            i.message for i in result.issues if i.code is EPUBValidationCode.INVALID_MANIFEST
        )

    @pytest.mark.parametrize("bad", ["http://example.com/book.xhtml", "/absolute.xhtml"])
    def test_manifest_href_outside_the_container_is_invalid(self, bad: str) -> None:
        opf = OPF.replace('href="chapter-01.xhtml"', f'href="{bad}"')
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_MANIFEST in _codes(result)

    def test_profile_requires_css(self) -> None:
        """The M4.1 profile always packages a stylesheet."""
        opf = _remove_manifest_item(OPF, "styles")
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.MISSING_RESOURCE in _codes(result)


# --------------------------------------------------------------------------- #
# Spine
# --------------------------------------------------------------------------- #


class TestSpineValidation:
    def test_missing_spine_element(self) -> None:
        opf = re.sub(
            r"<spine[^>]*>.*?</spine>",
            "",
            OPF,
            count=1,
            flags=re.DOTALL,
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.MISSING_SPINE]

    def test_empty_spine_is_invalid(self) -> None:
        opf = re.sub(r"<itemref[^>]*/>", "", OPF)
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_SPINE]

    def test_dangling_spine_reference(self) -> None:
        """Case K: an itemref names an id the manifest does not have."""
        opf = OPF.replace('idref="chapter-01"', 'idref="ghost"')
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_SPINE]
        assert "ghost" in result.issues[0].message

    def test_spine_itemref_without_idref(self) -> None:
        opf = re.sub(
            r'<itemref idref="chapter-01"/>', "<itemref/>", OPF, count=1
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_SPINE in _codes(result)

    def test_spine_itemref_to_non_document_is_invalid(self) -> None:
        """A spine cannot point at a stylesheet, image, or NCX."""
        opf = re.sub(
            r'<itemref idref="chapter-01"/>', '<itemref idref="styles"/>', OPF, count=1
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_SPINE in _codes(result)

    def test_spine_order_is_recorded_in_result(self) -> None:
        result = validate_epub(_archive(_entries()))
        assert result.spine_documents == (
            "EPUB/nav.xhtml",
            "EPUB/chapter-01.xhtml",
        )


# --------------------------------------------------------------------------- #
# XHTML documents
# --------------------------------------------------------------------------- #


class TestXhtmlValidation:
    def test_malformed_xhtml(self) -> None:
        """Case L."""
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = b"<html><body><p unclosed>"
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_XHTML]

    def test_missing_html_root_element(self) -> None:
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = b'<?xml version="1.0"?><body><p>x</p></body>'
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_XHTML]

    def test_missing_body_element(self) -> None:
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = (
            b'<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml">'
            b"<head><title>x</title></head></html>"
        )
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.INVALID_XHTML]

    def test_foreign_namespace_is_rejected(self) -> None:
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = (
            b'<?xml version="1.0"?>'
            b'<html xmlns="http://www.w3.org/1999/xhtml/v2"><body><p>x</p></body></html>'
        )
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_XHTML in _codes(result)

    def test_img_without_src_is_structural_invalidity(self) -> None:
        entries = _entries()
        broken = CHAPTER.replace(
            "<p>Hello from the first chapter.</p>", "<img alt='x'/>"
        )
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_XHTML in _codes(result)

    def test_stylesheet_link_without_href_is_structural_invalidity(self) -> None:
        entries = _entries()
        broken = CHAPTER.replace(
            '<link rel="stylesheet" href="style.css" type="text/css"/>',
            '<link rel="stylesheet" type="text/css"/>',
        )
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_XHTML in _codes(result)


# --------------------------------------------------------------------------- #
# Internal resource references
# --------------------------------------------------------------------------- #


class TestResourceReferenceValidation:
    def test_broken_image_reference(self) -> None:
        """Case M (image): <img src> names a file the archive lacks."""
        entries = _entries()
        broken = CHAPTER.replace("<p>Second paragraph.</p>", '<img src="missing.png"/>')
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert _codes(result) == [EPUBValidationCode.BROKEN_RESOURCE_REFERENCE]
        assert "missing.png" in result.issues[0].message

    def test_broken_stylesheet_reference(self) -> None:
        """Case M (stylesheet): a linked stylesheet is not packaged."""
        entries = _entries()
        broken = CHAPTER.replace('href="style.css"', 'href="absent.css"')
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.BROKEN_RESOURCE_REFERENCE in _codes(result)

    def test_image_reference_to_undeclared_resource(self) -> None:
        """The file exists in the archive but the manifest omits it."""
        entries = _entries()
        entries["EPUB/orphan.png"] = PNG_BYTES
        broken = CHAPTER.replace("<p>Second paragraph.</p>", '<img src="orphan.png"/>')
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.BROKEN_RESOURCE_REFERENCE in _codes(result)

    def test_relative_references_resolve_from_subdirectories(self) -> None:
        """Section 10: resources are not assumed to live at the container root."""
        opf = """\
<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
<dc:identifier id="uid">urn:test:sub</dc:identifier>
<dc:title>Sub Book</dc:title>
<dc:language>en</dc:language>
</metadata>
<manifest>
<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
<item id="ch1" href="text/ch1.xhtml" media-type="application/xhtml+xml"/>
<item id="css" href="css/style.css" media-type="text/css"/>
<item id="img" href="images/pic.png" media-type="image/png"/>
</manifest>
<spine><itemref idref="nav"/><itemref idref="ch1"/></spine>
</package>
"""
        nav = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
            "<ol><li><a href=\"text/ch1.xhtml\">Ch1</a></li></ol>"
            "</nav></body></html>"
        )
        chapter = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><head>'
            '</head>'
            '<body><p>x</p><img src="../images/pic.png"/></body></html>'
        )
        css = 'p { background: url("../images/pic.png"); }\n'
        entries = {
            "mimetype": MIMETYPE,
            "META-INF/container.xml": _container("OEBPS/content.opf").encode("utf-8"),
            "OEBPS/content.opf": opf.encode("utf-8"),
            "OEBPS/nav.xhtml": nav.encode("utf-8"),
            "OEBPS/text/ch1.xhtml": chapter.encode("utf-8"),
            "OEBPS/css/style.css": css.encode("utf-8"),
            "OEBPS/images/pic.png": PNG_BYTES,
        }
        result = validate_epub(_archive(entries))
        assert result.valid
        assert result.spine_documents == (
            "OEBPS/nav.xhtml",
            "OEBPS/text/ch1.xhtml",
        )

    def test_external_hyperlinks_are_not_errors(self) -> None:
        chapter = CHAPTER.replace(
            "<p>Second paragraph.</p>",
            '<a href="https://example.com/reference">source</a>',
        )
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = chapter.encode("utf-8")
        assert validate_epub(_archive(entries)).valid

    def test_fragment_links_are_not_errors(self) -> None:
        chapter = CHAPTER.replace(
            "<p>Second paragraph.</p>", '<a href="#section-2">jump</a>'
        )
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = chapter.encode("utf-8")
        assert validate_epub(_archive(entries)).valid

    def test_broken_anchor_reference_is_reported(self) -> None:
        chapter = CHAPTER.replace(
            "<p>Second paragraph.</p>", '<a href="nope.xhtml">jump</a>'
        )
        entries = _entries()
        entries["EPUB/chapter-01.xhtml"] = chapter.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.BROKEN_RESOURCE_REFERENCE in _codes(result)


# --------------------------------------------------------------------------- #
# Stylesheets
# --------------------------------------------------------------------------- #


class TestStylesheetValidation:
    def test_empty_stylesheet_is_invalid(self) -> None:
        entries = _entries()
        entries["EPUB/style.css"] = b"   \n\t  "
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_STYLESHEET in _codes(result)
        assert result.issues[0].location == "EPUB/style.css"

    def test_non_utf8_stylesheet_is_invalid(self) -> None:
        entries = _entries()
        entries["EPUB/style.css"] = b"body { color: #000; }\xff\xfe\x00"
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_STYLESHEET in _codes(result)

    def test_broken_css_url_reference(self) -> None:
        entries = _entries()
        entries["EPUB/style.css"] = b'body { background: url("missing.png"); }\n'
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.BROKEN_RESOURCE_REFERENCE in _codes(result)

    def test_broken_css_import_reference(self) -> None:
        entries = _entries()
        entries["EPUB/style.css"] = b'@import "other.css";\n'
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.BROKEN_RESOURCE_REFERENCE in _codes(result)

    def test_stylesheet_declared_without_backing_file(self) -> None:
        entries = {k: v for k, v in _entries().items() if k != "EPUB/style.css"}
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.MISSING_RESOURCE in _codes(result)

    def test_link_marked_as_css_but_declared_differently(self) -> None:
        opf = _replace_manifest_item(
            OPF,
            "styles",
            '<item id="styles" href="style.css" media-type="application/octet-stream"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_STYLESHEET in _codes(result)


# --------------------------------------------------------------------------- #
# Navigation (EPUB 3 nav + EPUB 2 NCX)
# --------------------------------------------------------------------------- #


class TestNavigationValidation:
    def test_valid_navigation_passes(self) -> None:
        """Case N (valid)."""
        assert validate_epub(_archive(_entries())).valid

    def test_no_navigation_item(self) -> None:
        opf = _remove_manifest_item(OPF, "nav")
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_NAVIGATION in _codes(result)

    def test_navigation_declared_as_non_xhtml(self) -> None:
        opf = _replace_manifest_item(
            OPF,
            "nav",
            '<item id="nav" href="nav.xhtml" media-type="text/css" properties="nav"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_NAVIGATION in _codes(result)

    def test_navigation_without_toc_marker(self) -> None:
        broken = NAV.replace('epub:type="toc"', 'epub:type="landmarks"')
        entries = _entries()
        entries["EPUB/nav.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_NAVIGATION in _codes(result)

    def test_navigation_without_list(self) -> None:
        broken = NAV.replace("<ol>", "").replace("</ol>", "")
        entries = _entries()
        entries["EPUB/nav.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_NAVIGATION in _codes(result)

    def test_navigation_with_broken_link(self) -> None:
        broken = NAV.replace("chapter-01.xhtml", "bogus.xhtml")
        entries = _entries()
        entries["EPUB/nav.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.BROKEN_RESOURCE_REFERENCE in _codes(result)
        assert "bogus.xhtml" in next(
            i.message
            for i in result.issues
            if i.code is EPUBValidationCode.BROKEN_RESOURCE_REFERENCE
        )

    def test_navigation_link_to_non_xhtml_is_invalid(self) -> None:
        broken = NAV.replace("chapter-01.xhtml", "style.css")
        entries = _entries()
        entries["EPUB/nav.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_NAVIGATION in _codes(result)

    def test_out_of_order_navigation_is_only_a_warning(self) -> None:
        """Navigation links that cross spine order stay valid EPUB (warning)."""
        base_opf = _add_manifest_item(
            OPF,
            '<item id="chapter-00" href="chapter-00.xhtml" '
            'media-type="application/xhtml+xml"/>',
        )
        opf_matched = _replace_spine(
            base_opf,
            '<itemref idref="nav"/><itemref idref="chapter-00"/>'
            '<itemref idref="chapter-01"/>',
        )
        opf_crossed = _replace_spine(
            base_opf,
            '<itemref idref="nav"/><itemref idref="chapter-01"/>'
            '<itemref idref="chapter-00"/>',
        )
        nav = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            '<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc">'
            "<ol><li><a href=\"chapter-00.xhtml\">Early</a></li>"
            "<li><a href=\"chapter-01.xhtml\">Chapter 1</a></li></ol>"
            "</nav></body></html>"
        )
        chapter_zero = (
            '<html xmlns="http://www.w3.org/1999/xhtml"><body>'
            "<p>zero</p></body></html>"
        )

        entries = _entries()
        entries["EPUB/nav.xhtml"] = nav.encode("utf-8")
        entries["EPUB/chapter-00.xhtml"] = chapter_zero.encode("utf-8")

        entries["EPUB/content.opf"] = opf_matched.encode("utf-8")
        matched = validate_epub(_archive(entries))
        assert matched.valid
        assert not any(
            i.code is EPUBValidationCode.INCOHERENT_NAVIGATION_ORDER
            for i in matched.warnings
        )

        entries["EPUB/content.opf"] = opf_crossed.encode("utf-8")
        crossed = validate_epub(_archive(entries))
        assert crossed.valid  # coherence is not structural invalidity
        assert any(
            i.code is EPUBValidationCode.INCOHERENT_NAVIGATION_ORDER
            for i in crossed.warnings
        )

    def test_malformed_ncx_is_reported(self) -> None:
        opf = _add_manifest_item(
            OPF,
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        ).replace("<spine>", '<spine toc="ncx">')
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        entries["EPUB/toc.ncx"] = b"<ncx><navMap></navMap></ncx>"
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_NAVIGATION in _codes(result)

    def test_well_formed_ncx_passes(self) -> None:
        opf = _add_manifest_item(
            OPF,
            '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
        ).replace("<spine>", '<spine toc="ncx">')
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        entries["EPUB/toc.ncx"] = NCX.encode("utf-8")
        assert validate_epub(_archive(entries)).valid

    def test_ncx_absent_is_not_an_error(self) -> None:
        """EPUB 3 navigation is the mechanism; NCX is never required."""
        assert validate_epub(_archive(_entries())).valid


# --------------------------------------------------------------------------- #
# Images
# --------------------------------------------------------------------------- #


class TestImageValidation:
    def test_epub_with_images_is_valid(self) -> None:
        """Case O."""
        opf = _add_manifest_item(
            OPF, '<item id="img" href="plate.png" media-type="image/png"/>'
        )
        chapter = CHAPTER.replace(
            "<p>Second paragraph.</p>",
            '<img src="plate.png" alt="Plate" class="image"/>',
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        entries["EPUB/chapter-01.xhtml"] = chapter.encode("utf-8")
        entries["EPUB/plate.png"] = PNG_BYTES
        result = validate_epub(_archive(entries))
        assert result.valid

    def test_image_declared_with_wrong_media_type(self) -> None:
        """A .png declared as text/css is structurally unusable."""
        opf = _add_manifest_item(
            OPF, '<item id="img" href="plate.png" media-type="text/css"/>'
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        entries["EPUB/plate.png"] = PNG_BYTES
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_IMAGE in _codes(result)

    def test_img_referencing_non_image_media_type(self) -> None:
        opf = _add_manifest_item(
            OPF, '<item id="img" href="plate.png" media-type="text/plain"/>'
        )
        chapter = CHAPTER.replace(
            "<p>Second paragraph.</p>", '<img src="plate.png" alt="Plate"/>'
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        entries["EPUB/chapter-01.xhtml"] = chapter.encode("utf-8")
        entries["EPUB/plate.png"] = PNG_BYTES
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_IMAGE in _codes(result)

    def test_real_builder_image_epub_is_valid(self, tmp_path: Path) -> None:
        book = Book(
            chapters=[
                Chapter(
                    blocks=[
                        Image(
                            data=JPEG_BYTES,
                            content_type="image/jpeg",
                            alt_text="Second picture",
                        )
                    ]
                )
            ]
        )
        out = tmp_path / "images.epub"
        build_epub(book, out)
        assert validate_epub(out).valid

    def test_image_bytes_are_never_required_to_decode(self) -> None:
        """Structural validation must not demand a decodable image payload."""
        opf = _add_manifest_item(
            OPF, '<item id="img" href="plate.png" media-type="image/png"/>'
        )
        entries = _entries()
        entries["EPUB/content.opf"] = opf.encode("utf-8")
        entries["EPUB/plate.png"] = b"\x89PNG\r\n\x1a\n" + b"\x00\xff\x00\xff"
        assert validate_epub(_archive(entries)).valid


# --------------------------------------------------------------------------- #
# Page breaks
# --------------------------------------------------------------------------- #


class TestPageBreakValidation:
    def test_defined_page_break_style_is_valid(self) -> None:
        """Case Q: class used and stylesheet defines it -- no complaints."""
        result = validate_epub(_archive(_entries()))
        assert result.valid
        assert not any(
            i.code is EPUBValidationCode.UNDEFINED_PAGE_BREAK_STYLE
            for i in result.issues
        )

    def test_undefined_page_break_style_is_a_warning(self) -> None:
        """The class used without a stylesheet rule stays a *warning*."""
        entries = _entries()
        entries["EPUB/style.css"] = b"body { margin: 0; }"
        result = validate_epub(_archive(entries))
        assert result.valid  # warnings never invalidate
        assert any(
            i.code is EPUBValidationCode.UNDEFINED_PAGE_BREAK_STYLE
            and i.severity is EPUBValidationSeverity.WARNING
            for i in result.issues
        )

    def test_page_break_class_on_non_div_is_structural_invalidity(self) -> None:
        entries = _entries()
        broken = CHAPTER.replace(
            '<div class="page-break" aria-hidden="true"/>',
            '<p class="page-break">break</p>',
        )
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_XHTML in _codes(result)

    def test_page_break_div_with_children_is_structural_invalidity(self) -> None:
        entries = _entries()
        broken = CHAPTER.replace(
            '<div class="page-break" aria-hidden="true"/>',
            '<div class="page-break">content</div>',
        )
        entries["EPUB/chapter-01.xhtml"] = broken.encode("utf-8")
        result = validate_epub(_archive(entries))
        assert not result.valid
        assert EPUBValidationCode.INVALID_XHTML in _codes(result)


# --------------------------------------------------------------------------- #
# Determinism and read-only guarantees
# --------------------------------------------------------------------------- #


class TestDeterminism:
    def test_repeated_validation_is_identical(self) -> None:
        """Case R: the same artifact always yields the same result."""
        data = _archive(_entries())
        first = validate_epub(data)
        for _ in range(3):
            again = validate_epub(data)
            assert again.issues == first.issues
            assert again.spine_documents == first.spine_documents
            assert again.package_document == first.package_document
            assert again.epub_version == first.epub_version
            assert again.error_count == first.error_count
            assert again.warning_count == first.warning_count

    def test_repeated_failing_validation_is_identical(self) -> None:
        entries = _entries()
        entries["EPUB/content.opf"] = OPF.replace(
            "chapter-01.xhtml", "ghost.xhtml"
        ).encode("utf-8")
        data = _archive(entries)
        first = validate_epub(data)
        for _ in range(3):
            assert validate_epub(data).issues == first.issues

    def test_path_and_bytes_inputs_are_equivalent(self, tmp_path: Path) -> None:
        out = tmp_path / "book.epub"
        build_epub(_structured_book(), out)
        by_path = validate_epub(out)
        by_bytes = validate_epub(out.read_bytes())
        assert by_path.issues == by_bytes.issues
        assert by_path.valid == by_bytes.valid

    def test_stored_epub_validation_is_identical_to_bytes(self) -> None:
        """Compression of the intermediate archive does not change findings."""
        data = _archive(_entries())
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, entry in _entries().items():
                if name == "mimetype":
                    archive.writestr(name, entry, compress_type=zipfile.ZIP_STORED)
                else:
                    archive.writestr(name, entry, compress_type=zipfile.ZIP_DEFLATED)
        assert validate_epub(data).issues == validate_epub(buffer.getvalue()).issues


class TestReadOnly:
    def test_file_input_is_not_modified(self, tmp_path: Path) -> None:
        out = tmp_path / "book.epub"
        build_epub(_structured_book(), out)
        before = out.read_bytes()
        validate_epub(out)
        assert out.read_bytes() == before

    def test_bytes_input_is_not_modified(self) -> None:
        data = _archive(_entries())
        snapshot = bytes(data)
        validate_epub(data)
        assert data == snapshot

    def test_malformed_file_is_not_repaired(self, tmp_path: Path) -> None:
        entries = {k: v for k, v in _entries().items() if k != "mimetype"}
        out = tmp_path / "malformed.epub"
        out.write_bytes(_archive(entries))
        before = out.read_bytes()
        result = validate_epub(out)
        assert not result.valid
        assert out.read_bytes() == before


# --------------------------------------------------------------------------- #
# Public API details
# --------------------------------------------------------------------------- #


class TestPublicApi:
    def test_public_api_exports(self) -> None:
        from kindle_converter.epub import (
            EPUBValidationCode as Code,
            EPUBValidationError as Err,
            EPUBValidationIssue as Issue,
            EPUBValidationResult as Res,
            EPUBValidationSeverity as Sev,
            validate_epub as validate,
        )

        assert callable(validate)
        assert Code.INVALID_ARCHIVE == "invalid_archive"
        assert Sev.WARNING == "warning"
        result = validate(_archive(_entries()))
        assert isinstance(result, Res)
        assert all(isinstance(issue, Issue) for issue in result.issues)

    def test_result_is_immutable(self) -> None:
        result = validate_epub(_archive(_entries()))
        with pytest.raises(AttributeError):
            result.issues = ()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            result.package_document = "other"  # type: ignore[misc]

    def test_valid_derives_from_errors_only(self) -> None:
        """A warnings-only result is still valid; errors invalidate it."""
        good = _entries()
        good["EPUB/style.css"] = b"body { margin: 0; }"  # undefined page-break rule
        result = validate_epub(_archive(good))
        assert result.warnings
        assert result.errors == ()
        assert result.valid

        bad = _entries()
        del bad["mimetype"]
        result = validate_epub(_archive(bad))
        assert result.errors
        assert not result.valid

    def test_error_count_and_warning_count(self) -> None:
        result = validate_epub(_archive(_entries()))
        assert result.error_count == 0
        assert result.warning_count == 0

        bad = _entries()
        bad["EPUB/style.css"] = b"   "
        result = validate_epub(_archive(bad))
        assert result.error_count == len(result.errors)
        assert result.warning_count == len(result.warnings)

    def test_format_report_is_deterministic(self) -> None:
        result = validate_epub(_archive(_entries()))
        assert result.format_report() == result.format_report()
        assert "EPUB validation: valid" in result.format_report()
        assert "EPUB/content.opf" in result.format_report()

        bad = _entries()
        del bad["mimetype"]
        report = validate_epub(_archive(bad)).format_report()
        assert "EPUB validation: INVALID" in report
        assert "1 error" in report

    def test_issues_are_actionable(self) -> None:
        bad = _entries()
        del bad["META-INF/container.xml"]
        issue = validate_epub(_archive(bad)).issues[0]
        assert issue.location == "META-INF/container.xml"
        assert "container" in issue.message
        assert str(issue) == f"{issue.code}: {issue.location}: {issue.message}"

    def test_non_artifacts_raise_validation_error(self) -> None:
        with pytest.raises(EPUBValidationError):
            validate_epub(12345)  # type: ignore[arg-type]
        with pytest.raises(EPUBValidationError):
            validate_epub(None)  # type: ignore[arg-type]
        with pytest.raises(EPUBValidationError):
            validate_epub(["nope"])  # type: ignore[arg-type]

    def test_unreadable_path_raises_validation_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing.epub"
        with pytest.raises(EPUBValidationError):
            validate_epub(missing)
        with pytest.raises(EPUBValidationError):
            validate_epub(tmp_path)  # a directory is not an EPUB

    def test_accepts_str_and_pathlike(self, tmp_path: Path) -> None:
        out = tmp_path / "book.epub"
        build_epub(_structured_book(), out)
        assert validate_epub(str(out)).valid
        assert validate_epub(Path(str(out))).valid


# --------------------------------------------------------------------------- #
# Minimal PDF helpers for the pipeline end-to-end tests
# --------------------------------------------------------------------------- #


TEXT_LINE = (
    "It was a bright cold day in April and the clocks were striking "
    "thirteen; Winston Smith, his chin nuzzled into his breast in an "
    "effort to escape the vile wind, slipped quickly through the "
    "glass doors of Victory Mansions."
)


class _FakeOCREngine:
    """Deterministic ``recognize(image) -> text`` stand-in (no Tesseract)."""

    def __init__(self, texts: list[str]) -> None:
        self.texts = list(texts)
        self.calls: list[object] = []

    def recognize(self, image) -> str:
        self.calls.append(image)
        index = min(len(self.calls) - 1, len(self.texts) - 1)
        return self.texts[index]


def _make_text_pdf(path: Path, *, pages: int = 2) -> Path:
    import pymupdf

    doc = pymupdf.open()
    doc.set_metadata({"title": "Validation Book"})
    for index in range(pages):
        page = doc.new_page(width=595, height=842)
        page.insert_textbox(
            pymupdf.Rect(72, 120, 500, 700),
            f"{TEXT_LINE}\n\nMore body text for page {index}.",
            fontname="helv",
            fontsize=12,
        )
    doc.save(str(path))
    doc.close()
    return path


def _pixmap():
    import pymupdf

    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 240, 180))
    pix.set_rect(pix.irect, (180, 60, 40))
    return pix


def _make_scanned_pdf(path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for _ in range(2):
        page = doc.new_page(width=595, height=842)
        page.insert_image(pymupdf.Rect(40, 40, 555, 800), pixmap=_pixmap())
    doc.save(str(path))
    doc.close()
    return path


def _make_mixed_pdf(path: Path) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_textbox(
        pymupdf.Rect(72, 120, 500, 400),
        f"{TEXT_LINE}\n\nMore body text.",
        fontname="helv",
        fontsize=12,
    )
    scanned = doc.new_page(width=595, height=842)
    scanned.insert_image(pymupdf.Rect(40, 40, 555, 800), pixmap=_pixmap())
    doc.save(str(path))
    doc.close()
    return path