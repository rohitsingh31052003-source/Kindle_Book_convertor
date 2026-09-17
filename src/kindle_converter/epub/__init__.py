"""EPUB output generation.

This package converts the format-independent
:class:`~kindle_converter.document.models.Book` model into a reflowable,
Kindle-oriented EPUB. :func:`build_epub` is the public entry point; the
low-level rendering lives in :mod:`kindle_converter.epub.builder`.

The M4.1 boundary is strict: this layer receives a ``Book`` and renders it.
It never inspects PDF pages, runs OCR, detects scanned pages, reconstructs
paragraphs, derives reading order, or makes PDF-specific structural
decisions -- all of that belongs to the PDF/reconstruction pipeline, which
produces the ``Book``. M4.2 adds the read-only structural validator in
:mod:`kindle_converter.epub.validation` (:func:`validate_epub`), which
inspects a generated EPUB artifact -- never a PDF or ``Book`` -- and reports
structured findings without modifying the artifact.
"""

from .builder import (
    EPUBGenerationError,
    InvalidImageError,
    NoChaptersError,
    build_epub,
)
from .validation import (
    EPUBValidationCode,
    EPUBValidationError,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
    validate_epub,
)

__all__ = [
    "EPUBGenerationError",
    "EPUBValidationCode",
    "EPUBValidationError",
    "EPUBValidationIssue",
    "EPUBValidationResult",
    "EPUBValidationSeverity",
    "InvalidImageError",
    "NoChaptersError",
    "build_epub",
    "validate_epub",
]
