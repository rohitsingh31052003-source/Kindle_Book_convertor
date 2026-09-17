"""Structural EPUB validation (Milestone 4.2).

This module is the output-quality boundary of the Kindle pipeline. It inspects
a finished EPUB *artifact* and reports whether it satisfies the structural
requirements the project's EPUB profile must meet::

    Book -> build_epub() -> EPUB -> validate_epub() -> EPUBValidationResult

The validator is deliberately not part of EPUB generation and never part of
PDF processing:

* It accepts an EPUB artifact -- a filesystem path or the archive bytes --
  never a PDF and never a :class:`~kindle_converter.document.models.Book`.
* It imports no PDF, OCR, routing, or reconstruction code (only the Python
  standard library), so it runs on any EPUB this project writes without a
  source PDF being available.
* It is strictly read-only: it never rewrites, repairs, normalizes, or
  otherwise modifies the artifact. Invalid output is *reported*, never fixed.
  A future repair workflow, if one is ever required, must be a separate
  component.

What is validated
-----------------
The layers run in a fixed order and are independent of PDF reconstruction
quality:

1. **Archive/container** -- the artifact opens as a ZIP archive, entry names
   are valid, and no name appears twice.
2. **mimetype** -- the ``mimetype`` entry exists, holds exactly
   ``application/epub+zip``, and is stored (not deflated), as OCF requires.
3. **``META-INF/container.xml``** -- present, well-formed, and declaring a
   package document that actually exists in the archive.
4. **OPF package document** -- well-formed, an ``opf:package`` root, declaring
   an EPUB 3.x version, with ``metadata``, ``manifest``, and ``spine``.
5. **Metadata** -- non-empty ``dc:title``, ``dc:language``, and
   ``dc:identifier``, plus a resolvable ``unique-identifier`` reference.
   Optional metadata (author, publisher, description, subject) is never
   required.
6. **Manifest** -- every item has an ``id``, ``href``, and ``media-type``,
   ids and hrefs are unique, hrefs are packaged relative references, and every
   referenced resource exists in the archive. XHTML content, a stylesheet, and
   the EPUB 3 navigation document must all be represented.
7. **Spine** -- declares at least one ``itemref``; every ``idref`` matches a
   manifest id that is a document; a ``toc`` attribute matches the NCX item.
8. **XHTML documents** -- parse as XML, have an ``<html>`` root and a
   ``<body>``, and every ``img``/``a``/``link`` reference resolves to a
   packaged resource declared in the manifest.
9. **Stylesheets** -- decodable, non-empty, and their ``url()``/``@import``
   references resolve to packaged resources.
10. **Page breaks** -- the ``page-break`` class is used only on an empty
    ``div`` and is defined by a stylesheet when it is used.
11. **Navigation** -- the ``properties="nav"`` document parses, contains a
    ``nav`` list marked as the table of contents, and every link targets a
    packaged XHTML document. A ``toc.ncx`` is validated when present.

Errors versus warnings
----------------------
An *error* means the EPUB is structurally invalid or unusable; ``valid`` is
``False`` whenever at least one error is reported. A *warning* never changes
``valid`` and is reserved for observations that do not prevent the EPUB from
being structurally sound (for example a ``mimetype`` entry that is not the
first archive entry). The validator deliberately emits no "quality" warnings:
whether a book *reads* well, whether the PDF reading order was perfect, and
whether a Kindle renders it attractively are outside this milestone.

Failures are never raw low-level exceptions
-------------------------------------------
Malformed EPUB input produces structured :class:`EPUBValidationIssue` values
with a machine-readable :class:`EPUBValidationCode`; ``BadZipFile``,
``XMLSyntaxError``, ``KeyError``, and their relatives are translated, never
propagated. :class:`EPUBValidationError` is reserved for the one case where no
result can be produced at all: the source is not an EPUB artifact (wrong type)
or the file cannot be read from disk. Only *expected* container/parsing
failures are translated -- genuine programming errors are not hidden behind a
blanket ``except Exception``.

Determinism
-----------
Given the same artifact, the same :class:`EPUBValidationResult` is always
produced: issues are reported in a fixed layer/document order, no sets or
timestamps are involved, and no network access, Calibre, Kindle Previewer, or
Tesseract is required.

Out of scope (M4.2)
-------------------
Structural validation only. This module does not validate PDF extraction or
OCR quality, paragraph/heading/chapter semantics, typography, visual fidelity,
Kindle device rendering, EPUB marketplace acceptance, AZW3 compatibility, or
covers -- and it implements no EPUB repair, no cover handling, and no
Kindle-specific formatting (M4.3-M4.5).
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import urllib.parse
import zipfile
from dataclasses import dataclass
from enum import StrEnum
from typing import TypeAlias
from xml.etree import ElementTree

PathLike: TypeAlias = str | os.PathLike[str]

#: Accepted validator input: a path to an ``.epub`` file, or the archive bytes.
Source: TypeAlias = "str | os.PathLike[str] | bytes | bytearray | memoryview"


# --------------------------------------------------------------------------- #
# Container facts (OCF / EPUB 3, fixed by the specification)
# --------------------------------------------------------------------------- #

#: The OCF mime-type entry that must be the first, stored entry of an EPUB.
MIMETYPE_PATH = "mimetype"

#: The exact bytes the ``mimetype`` entry must contain.
MIMETYPE_CONTENT = b"application/epub+zip"

#: The OCF container description that locates the package document.
CONTAINER_PATH = "META-INF/container.xml"

#: XML namespaces used by the container, package, content, and navigation.
CONTAINER_NAMESPACE = "urn:oasis:names:tc:opendocument:xmlns:container"
OPF_NAMESPACE = "http://www.idpf.org/2007/opf"
DC_NAMESPACE = "http://purl.org/dc/elements/1.1/"
XML_NAMESPACE = "http://www.w3.org/XML/1998/namespace"
XHTML_NAMESPACE = "http://www.w3.org/1999/xhtml"
OPS_NAMESPACE = "http://www.idpf.org/2007/ops"

MEDIA_TYPE_PACKAGE = "application/oebps-package+xml"
MEDIA_TYPE_XHTML = "application/xhtml+xml"
MEDIA_TYPE_NCX = "application/x-dtbncx+xml"
MEDIA_TYPE_CSS = "text/css"

#: A syntactically plausible media type token pair.
MEDIA_TYPE_PATTERN = re.compile(
    r"^[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+$"
)

#: Media types that may legitimately appear as a spine document.
DOCUMENT_MEDIA_TYPES = frozenset(
    {MEDIA_TYPE_XHTML, "text/html", "image/svg+xml"}
)

#: The ``properties`` token marking the EPUB 3 navigation document.
NAV_PROPERTY = "nav"

#: The ``class`` token the M4.1 renderer puts on its page-break elements.
PAGE_BREAK_CLASS = "page-break"

#: The EPUB major version this project produces and therefore validates.
#: ``3``, ``3.0``, ``3.1``, ``3.2``, ``3.3``, ... are accepted; EPUB 2 and
#: anything else is reported as unsupported instead of being migrated.
SUPPORTED_EPUB_VERSION_PATTERN = re.compile(r"^3(?:\.\d+)*$")

#: Image extensions the manifest media-type check understands.
IMAGE_EXTENSIONS = frozenset(
    {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "tif", "tiff"}
)

#: How many characters of an unexpected file payload are quoted in a message.
MAX_PREVIEW_CHARS = 60

#: Failures that mean "this artifact cannot be read as a ZIP archive". They are
#: the *expected* container failures (truncated data, bad central directory,
#: bad CRC, an encrypted entry, an unsupported compression method) and are
#: translated into an ``INVALID_ARCHIVE`` issue rather than propagated.
#: ``OSError`` is deliberately *not* included: a path that cannot be read at
#: all (missing file, directory, permissions) raises
#: :class:`EPUBValidationError` from :func:`_open_archive`, because nothing can
#: be validated in that case.
ARCHIVE_ERRORS = (
    zipfile.BadZipFile,
    zipfile.LargeZipFile,
    EOFError,
    RuntimeError,
    NotImplementedError,
)

#: Failures that mean "this XML document cannot be parsed". ``ParseError`` is
#: the syntax error; ``ValueError``/``LookupError`` cover a document whose
#: declared encoding is unknown or undecodable.
XML_ERRORS = (ElementTree.ParseError, ValueError, LookupError)


# --------------------------------------------------------------------------- #
# Public error taxonomy
# --------------------------------------------------------------------------- #


class EPUBValidationError(Exception):
    """The EPUB artifact could not be inspected at all.

    Raised only when no validation can run: ``source`` is neither a path nor
    archive bytes, or the file it names cannot be read (missing, a directory,
    permissions). *Invalid EPUB content* -- a broken ZIP, a missing
    ``mimetype``, malformed XML -- never raises; it is reported as a structured
    :class:`EPUBValidationIssue` inside an :class:`EPUBValidationResult`.
    """


class EPUBValidationSeverity(StrEnum):
    """How severe one validation finding is."""

    #: The EPUB is structurally invalid or unusable (``valid`` becomes False).
    ERROR = "error"

    #: A non-fatal structural observation; ``valid`` is unaffected.
    WARNING = "warning"


class EPUBValidationCode(StrEnum):
    """Machine-readable reason for one validation finding.

    The codes are stable identifiers so a caller (or a future repair workflow)
    can react to a specific failure without parsing messages.
    """

    #: The artifact is not a readable ZIP archive.
    INVALID_ARCHIVE = "invalid_archive"
    #: Two or more archive entries share the same name.
    DUPLICATE_ARCHIVE_ENTRY = "duplicate_archive_entry"
    #: An archive entry is not a valid EPUB path (absolute, ``..``, backslash).
    INVALID_ARCHIVE_ENTRY = "invalid_archive_entry"
    #: The ``mimetype`` entry is absent.
    MISSING_MIMETYPE = "missing_mimetype"
    #: The ``mimetype`` entry does not contain ``application/epub+zip``.
    INVALID_MIMETYPE = "invalid_mimetype"
    #: The ``mimetype`` entry is compressed; OCF requires it to be stored.
    COMPRESSED_MIMETYPE = "compressed_mimetype"
    #: The ``mimetype`` entry exists but is not the first archive entry.
    MIMETYPE_NOT_FIRST = "mimetype_not_first"
    #: ``META-INF/container.xml`` is absent.
    MISSING_CONTAINER = "missing_container"
    #: ``container.xml`` is malformed or declares no usable ``rootfile``.
    INVALID_CONTAINER = "invalid_container"
    #: A ``rootfile`` declares a media type other than the OPF media type.
    UNEXPECTED_ROOTFILE_MEDIA_TYPE = "unexpected_rootfile_media_type"
    #: The container does not identify a package document present in the ZIP.
    MISSING_PACKAGE_DOCUMENT = "missing_package_document"
    #: The package document is malformed or is not an ``opf:package``.
    INVALID_PACKAGE_DOCUMENT = "invalid_package_document"
    #: The package declares no version, or one this project does not produce.
    UNSUPPORTED_EPUB_VERSION = "unsupported_epub_version"
    #: Required package metadata (title, language, identifier) is absent.
    MISSING_METADATA = "missing_metadata"
    #: The package has no ``manifest``, or an empty one.
    MISSING_MANIFEST = "missing_manifest"
    #: A manifest entry is malformed, duplicated, or not a packaged resource.
    INVALID_MANIFEST = "invalid_manifest"
    #: The package has no ``spine``, or an empty one.
    MISSING_SPINE = "missing_spine"
    #: A spine reference is dangling or points at a non-document resource.
    INVALID_SPINE = "invalid_spine"
    #: An expected resource (XHTML, stylesheet, declared file) is missing.
    MISSING_RESOURCE = "missing_resource"
    #: A document references something that is not a packaged resource.
    BROKEN_RESOURCE_REFERENCE = "broken_resource_reference"
    #: An XHTML document is malformed or lacks its required structure.
    INVALID_XHTML = "invalid_xhtml"
    #: A stylesheet is unreadable, empty, or not declared as CSS.
    INVALID_STYLESHEET = "invalid_stylesheet"
    #: EPUB navigation is missing, malformed, or links to the wrong target.
    INVALID_NAVIGATION = "invalid_navigation"
    #: Navigation links are not in spine order (non-fatal).
    INCOHERENT_NAVIGATION_ORDER = "incoherent_navigation_order"
    #: An image resource has an incoherent media type for its file.
    INVALID_IMAGE = "invalid_image"
    #: The ``page-break`` class is used but no stylesheet defines it.
    UNDEFINED_PAGE_BREAK_STYLE = "undefined_page_break_style"


# --------------------------------------------------------------------------- #
# Public result model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EPUBValidationIssue:
    """One immutable validation finding.

    ``code`` is the machine-readable reason, ``message`` is a human-readable,
    actionable description (it names the offending resource), and ``location``
    is the archive path the finding belongs to, or the empty string when the
    finding is not tied to a single entry (for example an archive-wide
    duplicate-entry report).
    """

    #: Machine-readable reason (see :class:`EPUBValidationCode`).
    code: EPUBValidationCode

    #: Human-readable, actionable description of the problem.
    message: str

    #: Whether this finding invalidates the EPUB or is only an observation.
    severity: EPUBValidationSeverity = EPUBValidationSeverity.ERROR

    #: Archive path the finding belongs to (``""`` when not path-specific).
    location: str = ""

    def __str__(self) -> str:
        """``code: location: message`` -- readable in test failures and logs."""
        prefix = f"{self.location}: " if self.location else ""
        return f"{self.code}: {prefix}{self.message}"


@dataclass(frozen=True, slots=True)
class EPUBValidationResult:
    """The immutable outcome of :func:`validate_epub`.

    ``valid`` is derived from the issues: an EPUB is valid exactly when it has
    no errors. ``package_document``, ``epub_version``, and ``spine_documents``
    carry the structural facts the validator established before it stopped
    (they are ``None``/empty when the artifact failed earlier, for example
    because it is not a ZIP archive at all).
    """

    #: Archive path of the OPF package document, when one was located.
    package_document: str | None = None

    #: The ``version`` attribute of the package document, when available.
    epub_version: str | None = None

    #: Archive paths of the spine documents, in spine (reading) order.
    spine_documents: tuple[str, ...] = ()

    #: Every finding, in deterministic layer/document order.
    issues: tuple[EPUBValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        """Whether the EPUB is structurally valid (no errors reported)."""
        return not self.errors

    @property
    def errors(self) -> tuple[EPUBValidationIssue, ...]:
        """The findings that make the EPUB structurally invalid."""
        return tuple(
            issue
            for issue in self.issues
            if issue.severity is EPUBValidationSeverity.ERROR
        )

    @property
    def warnings(self) -> tuple[EPUBValidationIssue, ...]:
        """The non-fatal observations (they never affect ``valid``)."""
        return tuple(
            issue
            for issue in self.issues
            if issue.severity is EPUBValidationSeverity.WARNING
        )

    @property
    def error_count(self) -> int:
        """Number of reported errors."""
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        """Number of reported warnings."""
        return len(self.warnings)

    def format_report(self) -> str:
        """Render the result as a small, deterministic, human-readable report."""
        status = "valid" if self.valid else "INVALID"
        lines = [
            f"EPUB validation: {status} "
            f"({self.error_count} error(s), {self.warning_count} warning(s))"
        ]
        if self.package_document:
            lines.append(
                f"package document: {self.package_document} "
                f"(EPUB {self.epub_version or 'version unknown'})"
            )
        if self.spine_documents:
            lines.append("spine: " + ", ".join(self.spine_documents))
        if self.issues:
            lines.extend(
                f"  {issue.severity}: {issue}" for issue in self.issues
            )
        else:
            lines.append("no structural issues found")
        return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Private helpers (pure, deterministic, side-effect free)
# --------------------------------------------------------------------------- #


def _preview(data: bytes, limit: int = MAX_PREVIEW_CHARS) -> str:
    """A short, printable preview of a file payload for diagnostics."""
    text = data[:limit].decode("utf-8", "replace")
    if len(data) > limit:
        text += "..."
    return text


def _tokens(value: str | None) -> tuple[str, ...]:
    """The whitespace-separated tokens of an attribute (``""`` -> ``()``)."""
    return tuple(value.split()) if value else ()


def _local_name(tag: object) -> str:
    """The local name of an ElementTree tag (``{ns}name`` -> ``name``)."""
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1]


def _namespace(tag: object) -> str:
    """The namespace of an ElementTree tag (``""`` when unqualified)."""
    if not isinstance(tag, str) or not tag.startswith("{"):
        return ""
    return tag[1:].split("}", 1)[0]


def _find_child(
    parent: ElementTree.Element, namespace: str, local: str
) -> ElementTree.Element | None:
    """The first direct child of ``parent`` in ``namespace`` named ``local``."""
    for child in parent:
        if _local_name(child.tag) == local and _namespace(child.tag) == namespace:
            return child
    return None


def _element_text(element: ElementTree.Element) -> str:
    """All text inside ``element``, whitespace-trimmed."""
    return "".join(element.itertext()).strip()


def _describe_element(element: ElementTree.Element) -> str:
    """``<local>`` or ``<{namespace}local>`` for a diagnostic message."""
    namespace = _namespace(element.tag)
    local = _local_name(element.tag)
    return f"<{namespace}{local}>" if namespace else f"<{local}>"


def _metadata_elements(
    metadata: ElementTree.Element, local: str
) -> tuple[ElementTree.Element, ...]:
    """Every metadata child named ``local`` in the DC or no namespace.

    The DC namespace is matched by its URI, so a document that binds it to a
    prefix other than ``dc`` is still understood; an unqualified element is
    accepted as well, because real-world OPFs are not always namespace-clean.
    """
    return tuple(
        child
        for child in metadata
        if _local_name(child.tag) == local
        and _namespace(child.tag) in (DC_NAMESPACE, "")
    )


def _parse_xml(data: bytes) -> tuple[ElementTree.Element | None, str]:
    """Parse ``data`` as XML, returning ``(element, "")`` or ``(None, why)``.

    The failure text is the parser's own message, which already carries the
    line and column of the problem, so a validation message can point straight
    at it. Only parsing failures are translated; nothing else is caught.
    """
    try:
        return ElementTree.fromstring(data), ""
    except XML_ERRORS as exc:
        return None, str(exc)


def _extension(path: str) -> str:
    """The lowercase extension of an archive path (``""`` when there is none)."""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def _is_ncname(value: str) -> bool:
    """Whether a value is a usable XML name for this project's identifiers."""
    return re.fullmatch(r"[A-Za-z_][A-Za-z0-9._-]*", value) is not None


def _is_media_type(value: str) -> bool:
    """Whether a value has the basic shape of a registered media type."""
    return bool(MEDIA_TYPE_PATTERN.fullmatch(value.strip()))


def _has_content(element: ElementTree.Element) -> bool:
    """Whether an element contains non-whitespace text or child content."""
    return any(text.strip() for text in element.itertext()) or len(element) > 0


def _reference_target(href: str) -> str:
    """The path part of an ``href`` (fragment and query removed, trimmed)."""
    target = href.strip()
    for separator in ("#", "?"):
        index = target.find(separator)
        if index >= 0:
            target = target[:index]
    return target


def _is_packaged_target(target: str) -> bool:
    """Whether ``target`` is a relative reference into the EPUB container.

    Absolute paths, backslash paths, and references with a URL scheme
    (``http:``, ``data:``, ``mailto:``) are not packaged resources.
    """
    if not target or target.startswith("/") or "\\" in target:
        return False
    return re.match(r"^[A-Za-z][A-Za-z0-9+.\-]*:", target) is None


def _reference_path(base_dir: str, href: str) -> str | None:
    """The container-relative path an ``href`` names, or ``None``.

    ``None`` means the reference is not a packaged resource: it is empty,
    absolute, backslash-separated, scheme-bearing (``http:``, ``data:``), or
    it escapes the container root with ``..``.
    """
    target = _reference_target(href)
    if not _is_packaged_target(target):
        return None
    joined = posixpath.normpath(posixpath.join(base_dir, target))
    if joined in (".", "..") or joined.startswith("../"):
        return None
    return joined


def _resolve_reference(
    base_dir: str, href: str, files: frozenset[str]
) -> str | None:
    """Resolve an EPUB reference to an archive path, or ``None``.

    ``href`` is resolved against ``base_dir`` (the directory of the document
    that contains it) with POSIX semantics, so a reference works from any
    directory rather than only from the container root. A percent-encoded
    reference is tried literally first and decoded second, which matches how
    packaging tools write names that need escaping. ``None`` means the
    reference is not a packaged resource: it is absolute or scheme-bearing, it
    escapes the container root, or no such entry exists in the archive.
    """
    joined = _reference_path(base_dir, href)
    if joined is None:
        return None
    candidates = [joined]
    decoded = urllib.parse.unquote(joined)
    if decoded != joined:
        candidates.append(decoded)
    for candidate in candidates:
        if candidate in files:
            return candidate
    return None


def _is_invalid_entry_name(name: str) -> bool:
    """Whether a ZIP entry name is not a valid EPUB container path."""
    if (
        not name
        or "\\" in name
        or name.startswith("/")
        or any(ord(character) < 0x20 for character in name)
    ):
        return True
    path = name[:-1] if name.endswith("/") else name
    parts = path.split("/")
    return any(part in ("", ".", "..") for part in parts)


#: A CSS ``url(...)`` reference (quoted or bare).
_CSS_URL_PATTERN = re.compile(
    r"""url\(\s*(?P<quote>['"]?)(?P<value>[^'")]*)(?P=quote)\s*\)""",
    re.IGNORECASE,
)

#: A CSS ``@import`` that uses a plain string instead of ``url(...)``.
_CSS_IMPORT_PATTERN = re.compile(
    r"""@import\s+(?:url\(\s*)?(?P<quote>['"])(?P<value>[^'"]+)(?P=quote)""",
    re.IGNORECASE,
)

#: A ``.page-break`` class selector (``.page-break``, ``div.page-break``, but
#: not ``.page-break-inside``).
_PAGE_BREAK_SELECTOR_PATTERN = re.compile(r"\.page-break(?![\w-])")


def _css_references(text: str) -> tuple[str, ...]:
    """The packaged-resource references of a stylesheet, in source order.

    ``url(...)`` and ``@import`` targets are collected; ``data:`` URIs are
    dropped because they are inline payloads rather than packaged files.
    Duplicates are removed while preserving order, so one broken reference is
    reported once.
    """
    references = [
        match.group("value").strip()
        for match in _CSS_URL_PATTERN.finditer(text)
    ]
    references.extend(
        match.group("value").strip()
        for match in _CSS_IMPORT_PATTERN.finditer(text)
    )
    ordered = dict.fromkeys(
        reference
        for reference in references
        if reference and not reference.lower().startswith("data:")
    )
    return tuple(ordered)


@dataclass(frozen=True, slots=True)
class _ManifestItem:
    """One parsed OPF ``item`` (internal validation state)."""

    #: The item's ``id`` exactly as declared (``""`` when it declares none).
    id: str

    #: The item's ``href`` exactly as declared.
    href: str

    #: The item's ``media-type`` exactly as declared.
    media_type: str

    #: The resolved archive path, or ``None`` when the href is not packaged.
    path: str | None

    #: The ``properties`` tokens (``"nav"`` marks the navigation document).
    properties: tuple[str, ...]

    #: 0-based position of the item within the manifest.
    index: int

    @property
    def label(self) -> str:
        """A stable way to name the item in diagnostics."""
        return self.id or self.href or f"item #{self.index}"


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #


class _Validator:
    """Single-run validation state for one EPUB artifact.

    One instance is created per :func:`validate_epub` call, so repeated
    validation can never leak state between artifacts, and issues are appended
    in a fixed layer/document order (see :meth:`run`), which makes the result
    deterministic. The instance only ever *reads* the archive.
    """

    def __init__(self, archive: zipfile.ZipFile) -> None:
        self._archive = archive
        self._issues: list[EPUBValidationIssue] = []
        names = tuple(archive.namelist())
        self._names: tuple[str, ...] = names
        self._files: frozenset[str] = frozenset(
            name for name in names if not name.endswith("/")
        )
        self._package_document: str | None = None
        self._epub_version: str | None = None
        self._spine_documents: tuple[str, ...] = ()
        self._manifest_items: tuple[_ManifestItem, ...] = ()
        self._manifest: dict[str, _ManifestItem] = {}
        self._manifest_paths: dict[str, _ManifestItem] = {}
        self._nav_item: _ManifestItem | None = None
        self._documents: dict[str, ElementTree.Element] = {}
        self._valid_stylesheets: list[str] = []
        self._page_break_class_used = False

    # -- orchestration ------------------------------------------------------ #

    def run(self) -> EPUBValidationResult:
        """Run every validation layer in a fixed order and return the result."""
        self._check_archive_entries()
        self._check_mimetype()
        package_path = self._locate_package_document()
        if package_path is None:
            return self._result()
        package = self._read_package_document(package_path)
        if package is None:
            return self._result()
        self._check_epub_version(package)
        self._check_metadata(package)
        self._check_manifest(package)
        self._check_manifest_images()
        self._check_spine(package)
        self._check_documents()
        self._check_stylesheets()
        self._check_page_breaks()
        self._check_navigation()
        self._check_ncx()
        return self._result()

    # -- issue recording ---------------------------------------------------- #

    def _error(
        self, code: EPUBValidationCode, message: str, location: str = ""
    ) -> None:
        """Record a finding that makes the EPUB structurally invalid."""
        self._issues.append(
            EPUBValidationIssue(
                code=code,
                message=message,
                severity=EPUBValidationSeverity.ERROR,
                location=location,
            )
        )

    def _warning(
        self, code: EPUBValidationCode, message: str, location: str = ""
    ) -> None:
        """Record a non-fatal structural observation."""
        self._issues.append(
            EPUBValidationIssue(
                code=code,
                message=message,
                severity=EPUBValidationSeverity.WARNING,
                location=location,
            )
        )

    def _result(self) -> EPUBValidationResult:
        """Snapshot the state collected so far into an immutable result."""
        return EPUBValidationResult(
            package_document=self._package_document,
            epub_version=self._epub_version,
            spine_documents=self._spine_documents,
            issues=tuple(self._issues),
        )

    # -- archive access ----------------------------------------------------- #

    def _read_entry(self, name: str) -> bytes | None:
        """Read one archive entry, translating container failures.

        Returns ``None`` when the entry does not exist or cannot be decoded. A
        decoding failure is recorded as an ``INVALID_ARCHIVE`` issue so it
        stays actionable; it is never raised at the caller.
        """
        if name not in self._files:
            return None
        try:
            return self._archive.read(name)
        except ARCHIVE_ERRORS as exc:
            self._error(
                EPUBValidationCode.INVALID_ARCHIVE,
                f"the archive entry could not be read: {exc}",
                name,
            )
            return None

    def _resolve(self, document_path: str, href: str) -> str | None:
        """Resolve ``href`` relative to the document that contains it."""
        return _resolve_reference(
            posixpath.dirname(document_path), href, self._files
        )

    # -- layer A: ZIP container -------------------------------------------- #

    def _check_archive_entries(self) -> None:
        """Detect duplicate and invalid archive entry names."""
        counts: dict[str, int] = {}
        for name in self._names:
            counts[name] = counts.get(name, 0) + 1
        reported: set[str] = set()
        for name in self._names:
            if counts[name] > 1 and name not in reported:
                reported.add(name)
                self._error(
                    EPUBValidationCode.DUPLICATE_ARCHIVE_ENTRY,
                    f"the archive contains {counts[name]} entries named "
                    f"{name!r}",
                    name,
                )
        for name in self._names:
            if _is_invalid_entry_name(name):
                self._error(
                    EPUBValidationCode.INVALID_ARCHIVE_ENTRY,
                    f"the archive entry {name!r} is not a valid EPUB path",
                    name,
                )

    # -- layer B: mimetype -------------------------------------------------- #

    def _check_mimetype(self) -> None:
        """Validate the OCF ``mimetype`` entry."""
        if MIMETYPE_PATH not in self._files:
            self._error(
                EPUBValidationCode.MISSING_MIMETYPE,
                "the archive has no 'mimetype' entry; every EPUB must start "
                "with one",
                MIMETYPE_PATH,
            )
            return
        info = self._archive.getinfo(MIMETYPE_PATH)
        if info.compress_type != zipfile.ZIP_STORED:
            self._error(
                EPUBValidationCode.COMPRESSED_MIMETYPE,
                "the 'mimetype' entry is compressed; OCF requires it to be "
                "stored uncompressed",
                MIMETYPE_PATH,
            )
        if self._names and self._names[0] != MIMETYPE_PATH:
            self._error(
                EPUBValidationCode.MIMETYPE_NOT_FIRST,
                f"the 'mimetype' entry is not the first archive entry (the "
                f"first is {self._names[0]!r}); OCF requires it to lead the "
                "container",
                MIMETYPE_PATH,
            )
        data = self._read_entry(MIMETYPE_PATH)
        if data is not None and data != MIMETYPE_CONTENT:
            self._error(
                EPUBValidationCode.INVALID_MIMETYPE,
                f"the 'mimetype' entry must contain "
                f"{MIMETYPE_CONTENT.decode()!r}, found {_preview(data)!r}",
                MIMETYPE_PATH,
            )

    # -- layer C: META-INF/container.xml ----------------------------------- #

    def _locate_package_document(self) -> str | None:
        """Locate the OPF package document through ``container.xml``.

        Returns the archive path of the first declared ``rootfile`` that
        exists in the archive, or ``None`` after recording why the container
        is unusable: missing, malformed, declaring no ``rootfile``, or
        pointing at a file the archive does not contain.
        """
        if CONTAINER_PATH not in self._files:
            self._error(
                EPUBValidationCode.MISSING_CONTAINER,
                f"the archive has no {CONTAINER_PATH!r} entry, so the package "
                f"document cannot be located",
                CONTAINER_PATH,
            )
            return None
        data = self._read_entry(CONTAINER_PATH)
        if data is None:
            return None
        container, error = _parse_xml(data)
        if container is None:
            self._error(
                EPUBValidationCode.INVALID_CONTAINER,
                f"{CONTAINER_PATH!r} is not well-formed XML: {error}",
                CONTAINER_PATH,
            )
            return None
        if (
            _local_name(container.tag) != "container"
            or _namespace(container.tag) != CONTAINER_NAMESPACE
        ):
            self._error(
                EPUBValidationCode.INVALID_CONTAINER,
                f"the {CONTAINER_PATH!r} document element is "
                f"{_describe_element(container)}, not an OCF <container>",
                CONTAINER_PATH,
            )
            return None
        rootfiles = _find_child(container, CONTAINER_NAMESPACE, "rootfiles")
        if rootfiles is None:
            self._error(
                EPUBValidationCode.INVALID_CONTAINER,
                f"{CONTAINER_PATH!r} has no <rootfiles> element, so the "
                "package document cannot be located",
                CONTAINER_PATH,
            )
            return None
        candidates: list[str] = []
        missing_paths: list[str] = []
        for element in rootfiles:
            if (
                _local_name(element.tag) != "rootfile"
                or _namespace(element.tag) != CONTAINER_NAMESPACE
            ):
                continue
            full_path = (element.get("full-path") or "").strip()
            media_type = (element.get("media-type") or "").strip()
            if (
                not full_path
                or full_path.startswith("/")
                or "?" in full_path
                or "#" in full_path
                or not _is_packaged_target(full_path)
            ):
                self._error(
                    EPUBValidationCode.INVALID_CONTAINER,
                    "a <rootfile> declares an invalid or absolute full-path "
                    f"attribute: {full_path!r}",
                    CONTAINER_PATH,
                )
                continue
            if media_type != MEDIA_TYPE_PACKAGE:
                self._error(
                    EPUBValidationCode.INVALID_CONTAINER,
                    "a <rootfile> declares media-type "
                    f"{repr(media_type or '<missing>')}; an EPUB package document "
                    f"must be declared as {MEDIA_TYPE_PACKAGE!r}",
                    CONTAINER_PATH,
                )
                continue
            path = _resolve_reference("", full_path, self._files)
            if path is None:
                missing_paths.append(full_path)
                continue
            candidates.append(path)
        if not candidates:
            if missing_paths:
                self._error(
                    EPUBValidationCode.MISSING_PACKAGE_DOCUMENT,
                    f"the container identifies {missing_paths[0]!r} as the "
                    "package document, but that file is not present in the "
                    "archive",
                    CONTAINER_PATH,
                )
            else:
                self._error(
                    EPUBValidationCode.INVALID_CONTAINER,
                    f"{CONTAINER_PATH!r} declares no usable <rootfile>, "
                    "so the package document cannot be located",
                    CONTAINER_PATH,
                )
            return None
        self._package_document = candidates[0]
        return self._package_document

    # -- layer D: package document ----------------------------------------- #

    def _read_package_document(
        self, path: str
    ) -> ElementTree.Element | None:
        """Read and parse the OPF package document, or return ``None``."""
        data = self._read_entry(path)
        if data is None:
            return None
        package, error = _parse_xml(data)
        if package is None:
            self._error(
                EPUBValidationCode.INVALID_PACKAGE_DOCUMENT,
                f"the package document is not well-formed XML: {error}",
                path,
            )
            return None
        if (
            _local_name(package.tag) != "package"
            or _namespace(package.tag) != OPF_NAMESPACE
        ):
            self._error(
                EPUBValidationCode.INVALID_PACKAGE_DOCUMENT,
                f"the package document element is "
                f"{_describe_element(package)}, not an OPF <package>",
                path,
            )
            return None
        return package

    def _check_epub_version(self, package: ElementTree.Element) -> None:
        """Validate that the package declares a supported EPUB version."""
        location = self._package_document or ""
        version = (package.get("version") or "").strip()
        self._epub_version = version or None
        if not version:
            self._error(
                EPUBValidationCode.UNSUPPORTED_EPUB_VERSION,
                "the package document declares no version attribute; this "
                "project produces and validates EPUB 3.x",
                location,
            )
        elif not SUPPORTED_EPUB_VERSION_PATTERN.match(version):
            self._error(
                EPUBValidationCode.UNSUPPORTED_EPUB_VERSION,
                f"the package declares EPUB version {version!r}; this project "
                f"produces and validates EPUB 3.x",
                location,
            )

    def _check_metadata(self, package: ElementTree.Element) -> None:
        """Validate the required package metadata (the M4.1 fields).

        Only the fields the project's builder always emits are mandatory:
        title, language, and identifier. Optional metadata (author,
        publisher, description, subject) is never required.
        """
        location = self._package_document or ""
        metadata = _find_child(package, OPF_NAMESPACE, "metadata")
        if metadata is None:
            self._error(
                EPUBValidationCode.MISSING_METADATA,
                "the package has no <metadata> element",
                location,
            )
            return
        titles = tuple(
            element
            for element in _metadata_elements(metadata, "title")
            if _element_text(element)
        )
        languages = tuple(
            element
            for element in _metadata_elements(metadata, "language")
            if _element_text(element)
        )
        identifiers = tuple(
            element
            for element in _metadata_elements(metadata, "identifier")
            if _element_text(element)
        )
        if not titles:
            self._error(
                EPUBValidationCode.MISSING_METADATA,
                "the package metadata has no non-empty <dc:title>",
                location,
            )
        if not languages:
            self._error(
                EPUBValidationCode.MISSING_METADATA,
                "the package metadata has no non-empty <dc:language>",
                location,
            )
        if not identifiers:
            self._error(
                EPUBValidationCode.MISSING_METADATA,
                "the package metadata has no non-empty <dc:identifier>",
                location,
            )
        unique_identifier = (package.get("unique-identifier") or "").strip()
        if not unique_identifier:
            self._error(
                EPUBValidationCode.MISSING_METADATA,
                "the package has no non-empty unique-identifier attribute",
                location,
            )
        elif not any(
            element.get("id") == unique_identifier
            or element.get(f"{{{XML_NAMESPACE}}}id") == unique_identifier
            for element in identifiers
        ):
            self._error(
                EPUBValidationCode.INVALID_PACKAGE_DOCUMENT,
                f"the package unique-identifier {unique_identifier!r} does "
                f"not match the id of any <dc:identifier>",
                location,
            )

    # -- layer E: manifest -------------------------------------------------- #

    def _check_manifest(self, package: ElementTree.Element) -> None:
        """Validate the manifest and the resources the profile requires.

        Every declared item must carry an ``id``, ``href``, and
        ``media-type``; ids and hrefs must be unique; hrefs must be packaged
        relative references; and every referenced resource must exist in the
        archive. The profile itself must be represented by XHTML content, a
        stylesheet, and exactly one navigation document.
        """
        location = self._package_document or ""
        manifest = _find_child(package, OPF_NAMESPACE, "manifest")
        if manifest is None:
            self._error(
                EPUBValidationCode.MISSING_MANIFEST,
                "the package has no <manifest> element",
                location,
            )
            return
        items: list[_ManifestItem] = []
        for index, element in enumerate(manifest):
            if _local_name(element.tag) != "item":
                self._error(
                    EPUBValidationCode.INVALID_MANIFEST,
                    f"the manifest contains an unexpected "
                    f"{_describe_element(element)} element",
                    location,
                )
                continue
            items.append(self._read_manifest_item(element, index))
        self._manifest_items = tuple(items)
        if not items:
            self._error(
                EPUBValidationCode.INVALID_MANIFEST,
                "the manifest declares no <item> elements",
                location,
            )
            return
        self._check_manifest_identity(items)
        self._check_profile_resources(items)

    def _read_manifest_item(
        self, element: ElementTree.Element, index: int
    ) -> _ManifestItem:
        """Parse one ``<item>``, reporting malformed or dangling entries."""
        location = self._package_document or ""
        id_ = (element.get("id") or "").strip()
        href = (element.get("href") or "").strip()
        media_type = (element.get("media-type") or "").strip()
        label = id_ or href or f"item #{index}"
        if not id_ or not _is_ncname(id_):
            self._error(
                EPUBValidationCode.INVALID_MANIFEST,
                f"manifest item {label!r} declares an invalid id attribute",
                location,
            )
        if not href:
            self._error(
                EPUBValidationCode.INVALID_MANIFEST,
                f"manifest item {label!r} declares no href attribute",
                location,
            )
        elif "#" in href or "?" in href:
            self._error(
                EPUBValidationCode.INVALID_MANIFEST,
                f"manifest item {label!r} uses fragment/query syntax in href "
                f"{href!r}; manifest hrefs must name package resources",
                location,
            )
        if not media_type or not _is_media_type(media_type):
            self._error(
                EPUBValidationCode.INVALID_MANIFEST,
                f"manifest item {label!r} declares an invalid media-type "
                f"attribute: {media_type!r}",
                location,
            )
        resolved = self._resolve(location, href) if href else None
        if href and resolved is None:
            if _reference_path(location, href) is None:
                self._error(
                    EPUBValidationCode.INVALID_MANIFEST,
                    f"manifest item {label!r} references {href!r}, which is "
                    f"not a packaged resource in this EPUB",
                    location,
                )
            else:
                self._error(
                    EPUBValidationCode.MISSING_RESOURCE,
                    f"manifest item {label!r} references {href!r}, which is "
                    f"not present in the EPUB archive",
                    location,
                )
        return _ManifestItem(
            id=id_,
            href=href,
            media_type=media_type,
            path=resolved,
            properties=_tokens(element.get("properties")),
            index=index,
        )

    def _check_manifest_identity(self, items: list[_ManifestItem]) -> None:
        """Detect duplicate manifest ids and duplicate resource references.

        The first declaration of an id (and of a target file) wins, so later
        layers always resolve to one deterministic item.
        """
        location = self._package_document or ""
        by_id: dict[str, _ManifestItem] = {}
        by_path: dict[str, _ManifestItem] = {}
        for item in items:
            if item.id:
                existing = by_id.get(item.id)
                if existing is None:
                    by_id[item.id] = item
                else:
                    self._error(
                        EPUBValidationCode.INVALID_MANIFEST,
                        f"manifest id {item.id!r} is declared more than once "
                        f"(items #{existing.index} and #{item.index})",
                        location,
                    )
            if item.path:
                existing = by_path.get(item.path)
                if existing is None:
                    by_path[item.path] = item
                else:
                    self._error(
                        EPUBValidationCode.INVALID_MANIFEST,
                        f"manifest items {existing.label!r} and "
                        f"{item.label!r} reference the same resource "
                        f"{item.href!r}",
                        location,
                    )
        self._manifest = by_id
        self._manifest_paths = by_path

    def _check_profile_resources(self, items: list[_ManifestItem]) -> None:
        """Check that the resources this project's profile always emits exist.

        The M4.1 builder always packages XHTML content, a stylesheet, and
        exactly one EPUB 3 navigation document. Images are required only when
        the content references them, which the document layer verifies.
        """
        location = self._package_document or ""
        if not any(item.media_type == MEDIA_TYPE_XHTML for item in items):
            self._error(
                EPUBValidationCode.MISSING_RESOURCE,
                f"the manifest declares no {MEDIA_TYPE_XHTML!r} content "
                f"document",
                location,
            )
        if not any(item.media_type == MEDIA_TYPE_CSS for item in items):
            self._error(
                EPUBValidationCode.MISSING_RESOURCE,
                f"the manifest declares no {MEDIA_TYPE_CSS!r} stylesheet",
                location,
            )
        navigation = tuple(
            item for item in items if NAV_PROPERTY in item.properties
        )
        if not navigation:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                "no manifest item is marked as the navigation document "
                '(properties="nav")',
                location,
            )
        elif len(navigation) > 1:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"{len(navigation)} manifest items are marked as the "
                f"navigation document; exactly one is required",
                location,
            )
        else:
            self._nav_item = navigation[0]

    def _check_manifest_images(self) -> None:
        """Check that image resources carry a coherent image media type.

        A resource whose file name says ``.png``/``.jpg``/... but whose
        declared media type is not an image type is reported: readers pick
        decoding from the media type, so such a resource cannot be rendered.
        """
        location = self._package_document or ""
        for item in self._manifest_items:
            extension = _extension(item.path or item.href)
            is_image_media_type = item.media_type.startswith("image/")
            if extension in IMAGE_EXTENSIONS and not is_image_media_type:
                self._error(
                    EPUBValidationCode.INVALID_IMAGE,
                    f"the manifest declares the image resource {item.href!r} "
                    f"as {item.media_type!r}",
                    location,
                )
            elif is_image_media_type and extension not in IMAGE_EXTENSIONS:
                self._error(
                    EPUBValidationCode.INVALID_IMAGE,
                    f"the manifest declares {item.href!r} as "
                    f"{item.media_type!r}, but its file extension is not a "
                    "recognized image extension",
                    location,
                )

    # -- layer F: spine ----------------------------------------------------- #

    def _check_spine(self, package: ElementTree.Element) -> None:
        """Validate the spine's references and record its document order.

        Order is checked for self-consistency only: every ``itemref`` must
        resolve to a manifest document, and that order is recorded in the
        result. Whether it matches the source PDF's reading order is a
        reconstruction question, deliberately outside the validator.
        """
        location = self._package_document or ""
        spine = _find_child(package, OPF_NAMESPACE, "spine")
        if spine is None:
            self._error(
                EPUBValidationCode.MISSING_SPINE,
                "the package has no <spine> element, so the book has no "
                "reading order",
                location,
            )
            return
        self._check_spine_toc(spine, location)
        itemrefs: list[ElementTree.Element] = []
        for element in spine:
            if _local_name(element.tag) != "itemref":
                self._error(
                    EPUBValidationCode.INVALID_SPINE,
                    f"the spine contains an unexpected "
                    f"{_describe_element(element)} element",
                    location,
                )
                continue
            itemrefs.append(element)
        if not itemrefs:
            self._error(
                EPUBValidationCode.INVALID_SPINE,
                "the spine declares no <itemref> elements, so the book has no "
                "reading order",
                location,
            )
            return
        documents: list[str] = []
        seen_idrefs: set[str] = set()
        for element in itemrefs:
            idref = (element.get("idref") or "").strip()
            if not idref:
                self._error(
                    EPUBValidationCode.INVALID_SPINE,
                    "a spine <itemref> declares no idref attribute",
                    location,
                )
                continue
            if idref in seen_idrefs:
                self._error(
                    EPUBValidationCode.INVALID_SPINE,
                    f"spine idref {idref!r} is declared more than once",
                    location,
                )
            seen_idrefs.add(idref)
            if not self._manifest:
                # A manifest problem is already reported; do not turn it into
                # one dangling-reference finding per spine entry on top.
                continue
            item = self._manifest.get(idref)
            if item is None:
                self._error(
                    EPUBValidationCode.INVALID_SPINE,
                    f"spine idref {idref!r} does not match any manifest id",
                    location,
                )
                continue
            if item.media_type not in DOCUMENT_MEDIA_TYPES:
                self._error(
                    EPUBValidationCode.INVALID_SPINE,
                    f"spine idref {idref!r} references "
                    f"{item.media_type!r}, which is not a document",
                    location,
                )
                continue
            if item.path:
                documents.append(item.path)
        if (
            self._nav_item is not None
            and seen_idrefs
            and self._nav_item.id not in seen_idrefs
        ):
            self._error(
                EPUBValidationCode.INVALID_SPINE,
                f"the navigation document {self._nav_item.href!r} is not "
                "listed in the spine",
                location,
            )
        self._spine_documents = tuple(documents)

    def _check_spine_toc(
        self, spine: ElementTree.Element, location: str
    ) -> None:
        """Validate the EPUB 2 ``toc`` attribute when the spine declares one."""
        toc = (spine.get("toc") or "").strip()
        ncx_items = tuple(
            item for item in self._manifest_items if item.media_type == MEDIA_TYPE_NCX
        )
        if not toc and ncx_items:
            self._error(
                EPUBValidationCode.INVALID_SPINE,
                "the spine has no toc attribute for the packaged NCX document",
                location,
            )
            return
        if not toc:
            return
        item = self._manifest.get(toc) if self._manifest else None
        if item is None:
            self._error(
                EPUBValidationCode.INVALID_SPINE,
                f"the spine toc attribute {toc!r} does not match any manifest "
                f"id",
                location,
            )
        elif item.media_type != MEDIA_TYPE_NCX:
            self._error(
                EPUBValidationCode.INVALID_SPINE,
                f"the spine toc attribute {toc!r} references "
                f"{item.media_type!r}, which is not an NCX document",
                location,
            )

    # -- layer G: XHTML content documents ---------------------------------- #

    def _check_documents(self) -> None:
        """Parse every XHTML manifest item and inspect its references.

        Every XHTML document the manifest declares is parsed -- spine
        documents, the navigation document, and any other declared XHTML --
        because a document that cannot be parsed is a structural defect
        wherever it sits. Items whose resource is missing are skipped here;
        the manifest layer has already reported them.
        """
        for item in self._manifest_items:
            if item.media_type != MEDIA_TYPE_XHTML or item.path is None:
                continue
            tree = self._read_document(item.path)
            if tree is None:
                continue
            self._documents[item.path] = tree
            self._check_document_references(item.path, tree)

    def _read_document(self, path: str) -> ElementTree.Element | None:
        """Read, parse, and structurally check one XHTML document.

        Returns the parsed tree, or ``None`` after recording why the document
        cannot be used: unreadable, not well-formed XML, no ``<html>``
        document element, a foreign namespace, or no ``<body>``.
        """
        data = self._read_entry(path)
        if data is None:
            return None
        tree, error = _parse_xml(data)
        if tree is None:
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"{path!r} is not well-formed XML: {error}",
                path,
            )
            return None
        if _local_name(tree.tag) != "html":
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"the document element of {path!r} is "
                f"{_describe_element(tree)}, not <html>",
                path,
            )
            return None
        namespace = _namespace(tree.tag)
        if namespace not in (XHTML_NAMESPACE, ""):
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"the document element of {path!r} uses the unsupported "
                f"namespace {namespace!r}",
                path,
            )
            return None
        if _find_child(tree, namespace, "body") is None:
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"{path!r} has no <body> element",
                path,
            )
            return None
        return tree

    # -- layer H: internal resource references ------------------------------ #

    def _check_document_references(
        self, path: str, tree: ElementTree.Element
    ) -> None:
        """Check the resource references of one XHTML document.

        ``img``, stylesheet ``link``, and ``a`` targets must resolve to
        packaged resources declared in the manifest; inline ``style`` blocks
        are collected (and checked) like external stylesheets; and elements
        carrying the ``page-break`` class must be empty ``div`` markers.
        """
        for element in tree.iter():
            local = _local_name(element.tag)
            if local == "img":
                self._check_image_reference(path, element)
            elif local == "link" and "stylesheet" in _tokens(
                element.get("rel")
            ):
                self._check_stylesheet_reference(path, element)
            elif local == "a":
                self._check_anchor_reference(path, element)
            elif local == "style":
                inline = "".join(element.itertext())
                before = len(self._issues)
                self._check_css_references(path, inline)
                if inline.strip() and len(self._issues) == before:
                    self._valid_stylesheets.append(inline)
            if PAGE_BREAK_CLASS in _tokens(element.get("class")):
                self._check_page_break_element(path, element, local)

    def _check_page_break_element(
        self, path: str, element: ElementTree.Element, local: str
    ) -> None:
        """Validate one element that carries the ``page-break`` class.

        The class is only meaningful on an empty ``div`` (the M4.1 renderer's
        representation of a ``PageBreak`` block); using it on any other
        element, or nesting content inside it, is structurally invalid.
        Whether a break is *semantically* correct relative to the source PDF
        is not checked.
        """
        self._page_break_class_used = True
        if local != "div":
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"{path!r} uses the {PAGE_BREAK_CLASS!r} class on "
                f"{_describe_element(element)} instead of an empty <div>",
                path,
            )
        elif _has_content(element):
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"{path!r} contains a {PAGE_BREAK_CLASS!r} <div> with "
                "content; the page-break marker must be empty",
                path,
            )

    def _check_image_reference(
        self, path: str, element: ElementTree.Element
    ) -> None:
        """Validate one ``<img src>`` reference."""
        src = (element.get("src") or "").strip()
        if not src:
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"{path!r} contains an <img> without a src attribute",
                path,
            )
            return
        resolved = self._resolve(path, src)
        if resolved is None:
            self._error(
                EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                f"{path!r} references {src!r} from <img src>, but that "
                f"resource is not present in the EPUB archive",
                path,
            )
            return
        item = self._manifest_paths.get(resolved)
        if item is None:
            self._error(
                EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                f"{path!r} references {src!r} from <img src>, but that "
                f"resource is not declared in the OPF manifest",
                path,
            )
        elif not item.media_type.startswith("image/"):
            self._error(
                EPUBValidationCode.INVALID_IMAGE,
                f"{path!r} references {src!r} from <img src>, but the "
                f"manifest declares it as {item.media_type!r}, not an image "
                f"media type",
                path,
            )

    def _check_stylesheet_reference(
        self, path: str, element: ElementTree.Element
    ) -> None:
        """Validate one ``<link rel="stylesheet">`` reference."""
        href = (element.get("href") or "").strip()
        if not href:
            self._error(
                EPUBValidationCode.INVALID_XHTML,
                f"{path!r} contains a stylesheet <link> without an href "
                f"attribute",
                path,
            )
            return
        resolved = self._resolve(path, href)
        if resolved is None:
            self._error(
                EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                f"{path!r} references the stylesheet {href!r}, which is not "
                f"present in the EPUB archive",
                path,
            )
            return
        item = self._manifest_paths.get(resolved)
        if item is None:
            self._error(
                EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                f"{path!r} references the stylesheet {href!r}, which is not "
                f"declared in the OPF manifest",
                path,
            )
        elif item.media_type != MEDIA_TYPE_CSS:
            self._error(
                EPUBValidationCode.INVALID_STYLESHEET,
                f"{path!r} references {href!r} as a stylesheet, but the "
                f"manifest declares it as {item.media_type!r}",
                path,
            )

    def _check_anchor_reference(
        self, path: str, element: ElementTree.Element
    ) -> None:
        """Validate one ``<a href>`` reference.

        Fragment-only links stay inside the document and external hyperlinks
        (``http:``, ``mailto:``) are legitimate, so both are skipped; every
        other link must resolve to a packaged, declared resource.
        """
        href = (element.get("href") or "").strip()
        if not href or href.startswith("#"):
            return
        if not _is_packaged_target(_reference_target(href)):
            return
        resolved = self._resolve(path, href)
        if resolved is None:
            self._error(
                EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                f"{path!r} links to {href!r}, which is not present in the "
                "EPUB archive",
                path,
            )
        elif resolved not in self._manifest_paths:
            self._error(
                EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                f"{path!r} links to {href!r}, which is not declared in the "
                "OPF manifest",
                path,
            )

    # -- layer I: stylesheets ---------------------------------------------- #

    def _check_stylesheets(self) -> None:
        """Validate every stylesheet the manifest declares.

        A stylesheet must decode as UTF-8, must not be empty, and every
        ``url()``/``@import`` it contains must resolve to a packaged resource
        declared in the manifest. Typography is not judged: this is a
        structural check, not a CSS parser or a Kindle compatibility proof.
        """
        for item in self._manifest_items:
            if item.media_type != MEDIA_TYPE_CSS or item.path is None:
                continue
            data = self._read_entry(item.path)
            if data is None:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                self._error(
                    EPUBValidationCode.INVALID_STYLESHEET,
                    f"{item.path!r} is not valid UTF-8: {exc}",
                    item.path,
                )
                continue
            if not text.strip():
                self._error(
                    EPUBValidationCode.INVALID_STYLESHEET,
                    f"{item.path!r} is empty",
                    item.path,
                )
                continue
            before = len(self._issues)
            self._check_css_references(item.path, text)
            if len(self._issues) == before:
                self._valid_stylesheets.append(text)

    def _check_css_references(self, source_path: str, text: str) -> None:
        """Check the ``url()``/``@import`` references of a stylesheet."""
        for reference in _css_references(text):
            if reference.startswith("#"):
                continue
            resolved = self._resolve(source_path, reference)
            if resolved is None:
                self._error(
                    EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                    f"{source_path!r} references {reference!r}, which is not "
                    f"present in the EPUB archive",
                    source_path,
                )
            elif resolved not in self._manifest_paths:
                self._error(
                    EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                    f"{source_path!r} references {reference!r}, which is not "
                    f"declared in the OPF manifest",
                    source_path,
                )

    # -- layer J: page breaks ---------------------------------------------- #

    def _check_page_breaks(self) -> None:
        """Check that the ``page-break`` class is defined where it is used.

        The M4.1 stylesheet always defines ``div.page-break``; if a book's
        markup uses the class while no stylesheet (external or inline) defines
        it, the forced break silently disappears. That is a structural
        incompleteness worth reporting, but the EPUB stays usable, so it is a
        warning rather than an error.
        """
        if not self._page_break_class_used:
            return
        for text in self._valid_stylesheets:
            if _PAGE_BREAK_SELECTOR_PATTERN.search(text):
                return
        self._warning(
            EPUBValidationCode.UNDEFINED_PAGE_BREAK_STYLE,
            f"the content uses the {PAGE_BREAK_CLASS!r} class but no "
            f"stylesheet defines a .{PAGE_BREAK_CLASS} rule, so forced page "
            f"breaks will not be rendered",
        )

    # -- layer K: EPUB 3 navigation ---------------------------------------- #

    def _check_navigation(self) -> None:
        """Validate the EPUB 3 navigation document.

        The manifest layer has already established that exactly one item is
        marked ``properties="nav"``; here that document must be XHTML, must
        parse, must contain a ``nav`` element carrying ``epub:type="toc"``
        with an ordered list, and every link must target a packaged XHTML
        document. Whether the table of contents matches the source PDF's
        structure is a reconstruction question and is not judged; only its
        coherence with the spine order is reported, and only as a warning.
        """
        item = self._nav_item
        if item is None:
            return
        location = self._package_document or ""
        if item.media_type != MEDIA_TYPE_XHTML:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"the navigation document {item.href!r} is declared as "
                f"{item.media_type!r}; an XHTML document is required",
                location,
            )
            return
        if item.path is None:
            return
        tree = self._documents.get(item.path)
        if tree is None:
            # The document layer already reported why it cannot be used.
            return
        navigations = tuple(
            element
            for element in tree.iter()
            if _local_name(element.tag) == "nav"
        )
        if not navigations:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"{item.path!r} contains no <nav> element",
                item.path,
            )
            return
        toc = next(
            (
                element
                for element in navigations
                if "toc"
                in _tokens(element.get(f"{{{OPS_NAMESPACE}}}type"))
                or "doc-toc" in _tokens(element.get("role"))
            ),
            None,
        )
        if toc is None:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"{item.path!r} has no navigation list marked "
                f'epub:type="toc"',
                item.path,
            )
            return
        if not any(_local_name(child.tag) == "ol" for child in toc):
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"the table of contents in {item.path!r} has no <ol> list",
                item.path,
            )
        targets: list[str] = []
        for anchor in (
            element
            for element in toc.iter()
            if _local_name(element.tag) == "a"
        ):
            href = (anchor.get("href") or "").strip()
            if not href:
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"the table of contents in {item.path!r} has a link "
                    f"without an href attribute",
                    item.path,
                )
                continue
            if href.startswith("#"):
                continue
            resolved = self._resolve(item.path, href)
            if resolved is None:
                self._error(
                    EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                    f"the table of contents in {item.path!r} links to "
                    f"{href!r}, which is not present in the EPUB archive",
                    item.path,
                )
                continue
            entry = self._manifest_paths.get(resolved)
            if entry is None:
                self._error(
                    EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                    f"the table of contents in {item.path!r} links to "
                    f"{href!r}, which is not declared in the OPF manifest",
                    item.path,
                )
                continue
            if entry.media_type != MEDIA_TYPE_XHTML:
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"the table of contents in {item.path!r} links to "
                    f"{href!r}, which is declared as {entry.media_type!r}, "
                    "not an XHTML document",
                    item.path,
                )
                continue
            if (
                any(
                    document != self._nav_item.path
                    for document in self._spine_documents
                )
                and resolved not in self._spine_documents
            ):
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"the table of contents in {item.path!r} links to "
                    f"{href!r}, which is not listed in the spine",
                    item.path,
                )
                continue
            targets.append(resolved)
        self._check_navigation_order(targets, item.path)

    def _check_navigation_order(self, targets: list[str], path: str) -> None:
        """Warn when navigation links disagree with the spine order."""
        positions = [
            self._spine_documents.index(target)
            for target in targets
            if target in self._spine_documents
        ]
        if positions != sorted(positions):
            self._warning(
                EPUBValidationCode.INCOHERENT_NAVIGATION_ORDER,
                f"the table of contents in {path!r} links to spine documents "
                f"out of reading order",
                path,
            )

    # -- layer L: EPUB 2 NCX (only when packaged) -------------------------- #

    def _check_ncx(self) -> None:
        """Validate every ``toc.ncx`` the manifest declares.

        The M4.1 builder packages an NCX for EPUB 2 reading systems, so its
        basic structure is checked when it is present. An EPUB 3 book without
        an NCX is not an error: the navigation document is the required
        mechanism and obsolete formats are deliberately not demanded.
        """
        for item in self._manifest_items:
            if item.media_type == MEDIA_TYPE_NCX and item.path is not None:
                self._check_ncx_item(item)

    def _check_ncx_item(self, item: _ManifestItem) -> None:
        """Validate one NCX document: XML, ``navMap``, and every ``content``."""
        path = item.path or ""
        data = self._read_entry(path)
        if data is None:
            return
        tree, error = _parse_xml(data)
        if tree is None:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"{path!r} is not well-formed XML: {error}",
                path,
            )
            return
        if _local_name(tree.tag) != "ncx":
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"the document element of {path!r} is "
                f"{_describe_element(tree)}, not <ncx>",
                path,
            )
            return
        nav_map = next(
            (
                element
                for element in tree.iter()
                if _local_name(element.tag) == "navMap"
            ),
            None,
        )
        if nav_map is None:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"{path!r} has no <navMap> element",
                path,
            )
            return
        points = tuple(
            element
            for element in nav_map.iter()
            if _local_name(element.tag) == "navPoint"
        )
        if not points:
            self._error(
                EPUBValidationCode.INVALID_NAVIGATION,
                f"the <navMap> in {path!r} declares no <navPoint> elements",
                path,
            )
            return
        for point in points:
            content = _find_child(point, _namespace(point.tag), "content")
            if content is None:
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"a <navPoint> in {path!r} has no <content> element",
                    path,
                )
                continue
            src = (content.get("src") or "").strip()
            if not src:
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"a <content> in {path!r} has no src attribute",
                    path,
                )
                continue
            resolved = self._resolve(path, src)
            if resolved is None:
                self._error(
                    EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                    f"{path!r} lists {src!r}, which is not present in the "
                    f"EPUB archive",
                    path,
                )
                continue
            entry = self._manifest_paths.get(resolved)
            if entry is None:
                self._error(
                    EPUBValidationCode.BROKEN_RESOURCE_REFERENCE,
                    f"{path!r} lists {src!r}, which is not declared in the "
                    f"OPF manifest",
                    path,
                )
            elif entry.media_type != MEDIA_TYPE_XHTML:
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"{path!r} lists {src!r}, which is declared as "
                    f"{entry.media_type!r}, not an XHTML document",
                    path,
                )
            elif self._spine_documents and resolved not in self._spine_documents:
                self._error(
                    EPUBValidationCode.INVALID_NAVIGATION,
                    f"{path!r} lists {src!r}, which is not listed in the spine",
                    path,
                )


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def validate_epub(source: Source) -> EPUBValidationResult:
    """Validate an EPUB artifact and report its structural problems.

    The validator is read-only and independent of PDF processing: it accepts a
    finished EPUB -- a path or the archive bytes -- never a PDF and never a
    :class:`~kindle_converter.document.models.Book`, so it can check any EPUB
    this project (or another tool) produced. It never repairs, rewrites, or
    normalizes the artifact: if the file is invalid, the problem is reported.

    The layers described in the module docstring run in a fixed order, so the
    result is deterministic -- the same artifact always produces the same
    :class:`EPUBValidationResult`, with no network access, Calibre, Kindle
    Previewer, or Tesseract involved.

    Parameters
    ----------
    source:
        Either a path to a ``.epub`` file (``str``, ``pathlib.Path``, or
        ``os.PathLike``) or the bytes of an EPUB archive (``bytes``,
        ``bytearray``, or ``memoryview``); the caller's buffer is never
        modified.

    Returns
    -------
    EPUBValidationResult
        ``valid`` is True when no error was found. ``errors`` and ``warnings``
        (and ``format_report()``) carry the structured, actionable findings.

    Raises
    ------
    EPUBValidationError
        If ``source`` is neither a path nor archive bytes, or if the file it
        names cannot be read at all (missing, a directory, no permission).
        Invalid EPUB *content* never raises -- the problem is reported in the
        result instead.

    Examples
    --------
    >>> from kindle_converter.epub import validate_epub
    >>> result = validate_epub("book.epub")
    >>> result.valid
    True
    >>> validate_epub(b"not an epub").errors[0].code
    <EPUBValidationCode.INVALID_ARCHIVE: 'invalid_archive'>
    """
    try:
        archive = _open_archive(source)
    except ARCHIVE_ERRORS as exc:
        return _invalid_archive_result(exc)
    try:
        validator = _Validator(archive)
    except ARCHIVE_ERRORS as exc:
        archive.close()
        return _invalid_archive_result(exc)
    try:
        return validator.run()
    finally:
        archive.close()


def _open_archive(source: Source) -> zipfile.ZipFile:
    """Open ``source`` read-only as a ZIP archive.

    Bytes-like input is copied into an in-memory stream, so the caller's
    buffer is never touched; a path is opened for reading only. A
    ``BadZipFile`` is deliberately left to the caller, which turns it into a
    structured ``INVALID_ARCHIVE`` finding. A path that cannot be read at all
    raises :class:`EPUBValidationError`, because nothing can be validated in
    that case.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        return zipfile.ZipFile(io.BytesIO(bytes(source)))
    if isinstance(source, (str, os.PathLike)):
        path = os.fspath(source)
        if isinstance(path, bytes):
            path = os.fsdecode(path)
        try:
            return zipfile.ZipFile(path)
        except OSError as exc:
            raise EPUBValidationError(
                f"cannot read the EPUB artifact at {path!r}: {exc}"
            ) from exc
    raise EPUBValidationError(
        f"validate_epub expects a path or EPUB archive bytes, got "
        f"{type(source).__name__}"
    )


def _invalid_archive_result(exc: Exception) -> EPUBValidationResult:
    """The result for an artifact that cannot be opened as a ZIP archive."""
    return EPUBValidationResult(
        issues=(
            EPUBValidationIssue(
                code=EPUBValidationCode.INVALID_ARCHIVE,
                message=f"the artifact is not a readable ZIP archive: {exc}",
            ),
        )
    )
