"""Programmatic access to the M6.1 representative PDF corpus.

This package owns the repository-safe PDF corpus committed under
``tests/fixtures/corpus``. The corpus is the deterministic test material
that later Milestone 6 work (M6.2 regression tests, M6.3 conversion-quality
measurement, M6.4 performance measurement) consumes.

The package exposes discovery helpers anchored to this module's location, so
callers never hard-code paths:

* :data:`CORPUS_DIR` -- the corpus directory itself.
* :data:`PDFS_DIR` -- the directory holding the committed fixture PDFs.
* :data:`MANIFEST_PATH` -- the committed machine-readable corpus manifest.
* :data:`CORPUS_CATEGORIES` -- the ordered corpus categories.
* :func:`load_manifest` -- parse and validate the JSON manifest.
* :func:`iter_documents` -- iterate manifest documents.
* :func:`document_path` -- resolve a document ID to its PDF path.

The manifest is the single source of truth about the corpus. Its schema is
documented in the corpus ``README.md`` and validated by
``tests/test_corpus.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

CORPUS_DIR = Path(__file__).resolve().parent
PDFS_DIR = CORPUS_DIR / "pdfs"
MANIFEST_PATH = CORPUS_DIR / "manifest.json"

#: The ordered corpus categories (mirrors the M6.1 coverage checklist).
CORPUS_CATEGORIES: tuple[str, ...] = (
    "normal_novel",
    "textbook_dense",
    "multi_column",
    "scanned_book",
    "pdf_with_images",
    "headers_footers",
    "unusual_typography",
    "mixed_text_image",
    "chapter_heavy",
    "edge_cases",
)


class CorpusError(Exception):
    """Raised when the corpus layout or manifest is not usable."""


def load_manifest() -> dict:
    """Load and return the corpus manifest as a plain dictionary.

    Raises
    ------
    CorpusError
        If the manifest is missing or contains malformed JSON.
    """
    if not MANIFEST_PATH.is_file():
        raise CorpusError(f"corpus manifest not found: {MANIFEST_PATH}")
    try:
        with MANIFEST_PATH.open(encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as exc:
        raise CorpusError(
            f"corpus manifest is not valid JSON: {MANIFEST_PATH}"
        ) from exc


def iter_documents() -> list[dict]:
    """Return the manifest's ``documents`` list (empty when absent)."""
    return list(load_manifest().get("documents", []))


def document_path(document_id: str) -> Path:
    """Resolve a document ID to the committed fixture PDF path.

    The path is resolved from the manifest so the layout stays discoverable
    even if PDFs are later relocated within the corpus directory.
    """
    documents = iter_documents()
    for document in documents:
        if document.get("id") == document_id:
            return _resolve_manifest_path(document.get("path"))
    raise CorpusError(f"unknown corpus document id: {document_id!r}")


def document_exists(document_id: str) -> bool:
    """Return whether the committed PDF for ``document_id`` is present."""
    return document_path(document_id).is_file()


def _resolve_manifest_path(relative: object) -> Path:
    if not isinstance(relative, str):
        raise CorpusError(f"manifest entry has no path string: {relative!r}")
    path = CORPUS_DIR / Path(relative)
    return path.resolve()