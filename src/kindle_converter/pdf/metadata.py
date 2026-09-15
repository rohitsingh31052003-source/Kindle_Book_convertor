"""Conservative, deterministic PDF metadata handling (Milestone 2.12).

This module establishes the explicit metadata-handling boundary for the
project. It reuses the existing format-independent
:class:`~kindle_converter.document.models.BookMetadata` model -- it does not
introduce a competing metadata representation -- and adds two small public
operations around it:

* :func:`extract_pdf_metadata` -- read only PyMuPDF's document metadata
  dictionary (``Document.metadata``) and map it onto a :class:`BookMetadata`.
  It never scans page text, never looks at OCR, and never infers anything.
* :func:`resolve_metadata` -- combine explicit caller-provided metadata with
  PDF-derived metadata deterministically: an explicit value always wins, and
  PDF metadata fills only the fields the caller left empty.

Design rules (M2.12)
--------------------
* **No guessing.** Nothing is invented. ``None``, empty, and whitespace-only
  values are all treated as *missing*. A metadata-less PDF yields an empty
  :class:`BookMetadata`; resolving two empty metadata objects yields an empty
  result.
* **No destructive normalization.** Present (non-empty) text values are
  preserved exactly -- capitalization, punctuation, spacing, and author names
  are never rewritten. Whitespace-only values are merely treated as missing.
* **Existing PDF field mapping is preserved.** To stay compatible with the
  established extractor convention (documented in
  :mod:`kindle_converter.pdf.extractor`), PyMuPDF ``creator`` maps to
  ``BookMetadata.publisher`` and PyMuPDF ``subject`` maps to
  ``BookMetadata.identifier``. ``title``/``author``/``language`` map
  directly. ``keywords`` are deliberately not mapped (the project does not
  need them yet); ``description`` and ``subject`` are only ever populated by
  explicit caller metadata in V1.
* **Deterministic.** Given the same inputs the same result is produced, with
  no randomization, timestamps, external services, or machine state. This
  layer never mutates its inputs.
"""

from __future__ import annotations

import os

import pymupdf

from ..document import BookMetadata
from .extractor import _close_if_owned, _open_document

PathLike = str | os.PathLike[str]

#: The public metadata fields, in a stable order used by resolution. Kept as a
#: module constant so the resolution rule is applied uniformly and readably.
_METADATA_FIELDS = (
    "title",
    "author",
    "language",
    "publisher",
    "description",
    "subject",
    "identifier",
)


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def extract_pdf_metadata(source: PathLike | pymupdf.Document) -> BookMetadata:
    """Extract reliable metadata from a PDF's document-info dictionary.

    Only PyMuPDF's ``Document.metadata`` is read. Arbitrary page text is never
    scanned, and no value is guessed or fabricated. Missing values
    (``None``/empty/whitespace-only) become the empty string.

    Parameters
    ----------
    source:
        Either a path to a PDF file (``str``/``pathlib.Path``/``os.PathLike``)
        or an already-open PyMuPDF ``Document``. With a path the file is opened
        and closed within this call. With a ``Document`` the caller keeps
        ownership: the document is never mutated and never closed here.

    Returns
    -------
    BookMetadata
        Metadata mapped onto the shared document model. ``description`` and
        ``subject`` are always empty from this source (V1 has no reliable PDF
        key for them); ``keywords`` are not mapped.

    Raises
    ------
    PDFReadError
        If ``source`` is a path that cannot be opened as a PDF.
    """
    doc = _open_document(source)
    try:
        return _map_pdf_metadata(doc.metadata or {})
    finally:
        _close_if_owned(source, doc)


def resolve_metadata(
    explicit: BookMetadata, pdf: BookMetadata
) -> BookMetadata:
    """Resolve explicit and PDF metadata into one deterministic result.

    Resolution rule (documented, deterministic):

    * explicit wins: for every field, a present explicit value overrides the
      PDF value completely and is preserved exactly;
    * PDF fills the gaps: a field the caller left missing falls back to the
      PDF value, if present;
    * otherwise the field is left empty.

    A value is *present* when it is a non-empty string after stripping;
    ``None``, ``''``, and whitespace-only strings are all treated as missing.
    Neither input is mutated; a fresh :class:`BookMetadata` is returned.

    Parameters
    ----------
    explicit:
        Caller-provided metadata (authoritative where present).
    pdf:
        PDF-derived metadata from :func:`extract_pdf_metadata` (fills gaps).

    Returns
    -------
    BookMetadata
        A fresh, resolved :class:`BookMetadata`.

    Raises
    ------
    TypeError
        If either argument is not a :class:`BookMetadata`.
    """
    if not isinstance(explicit, BookMetadata):
        raise TypeError(
            f"resolve_metadata expects explicit as BookMetadata, "
            f"got {type(explicit).__name__}"
        )
    if not isinstance(pdf, BookMetadata):
        raise TypeError(
            f"resolve_metadata expects pdf as BookMetadata, "
            f"got {type(pdf).__name__}"
        )
    resolved = {}
    for field in _METADATA_FIELDS:
        resolved[field] = _resolve_field(
            getattr(explicit, field), getattr(pdf, field)
        )
    return BookMetadata(**resolved)


# --------------------------------------------------------------------------- #
# Internals
# --------------------------------------------------------------------------- #


def _present(value: object) -> bool:
    """Return whether ``value`` carries meaningful metadata.

    ``None``, non-string values, the empty string, and whitespace-only strings
    are all treated as missing. Anything else is preserved exactly.
    """
    return isinstance(value, str) and bool(value.strip())


def _clean(value: object) -> str:
    """Return ``value`` when present, else the empty string.

    Present values are returned unchanged: no stripping, case or punctuation
    rewriting. This keeps extraction faithful to the source document.
    """
    return value if _present(value) else ""


def _resolve_field(explicit_value: object, pdf_value: object) -> str:
    """Resolve one field: explicit wins, PDF fills the gap, else empty."""
    if _present(explicit_value):
        return _clean(explicit_value)
    if _present(pdf_value):
        return _clean(pdf_value)
    return ""


def _map_pdf_metadata(metadata: dict) -> BookMetadata:
    """Map PyMuPDF's metadata dictionary onto :class:`BookMetadata`.

    Mirrors the extractor's pre-M2.12 convention so the refactored extractor
    and this API agree exactly: ``creator`` -> ``publisher``, ``subject`` ->
    ``identifier``, ``title``/``author``/``language`` direct.
    ``description`` and ``subject`` fields are left empty because V1 defines
    no reliable PDF key for them.
    """
    return BookMetadata(
        title=_clean(metadata.get("title")),
        author=_clean(metadata.get("author")),
        language=_clean(metadata.get("language")),
        publisher=_clean(metadata.get("creator")),
        identifier=_clean(metadata.get("subject")),
        description="",
        subject="",
    )