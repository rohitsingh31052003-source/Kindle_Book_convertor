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

M4.3 adds :func:`convert_epub_to_azw3`, an explicit, standalone output step
that turns a finished EPUB artifact into an AZW3 (Kindle) ebook through an
external conversion backend. The production backend is Calibre's
``ebook-convert`` executable (an optional system dependency, isolated in
:mod:`kindle_converter.epub.calibre`): this layer never consumes a PDF or
``Book``, never implements the AZW3 format itself, and never chains
automatically after EPUB generation. Full detail -- the validation guarantee
(artifact existence plus non-empty, *not* Kindle rendering correctness) and
the determinism contract (command construction only, not AZW3 bytes) -- is
documented in :mod:`kindle_converter.epub.azw3`.

M4.4 adds explicit, optional cover output to
:func:`kindle_converter.epub.builder.build_epub`: a ``Book.cover``
(:class:`~kindle_converter.document.models.Image`) is rendered as the EPUB
cover image (``properties="cover-image"`` + ``name="cover"`` metadata) and a
minimal reflowable cover page ahead of the content in the spine. The cover is
never inferred from anything, never added to the navigation, and books
without a cover are unchanged.
"""

from .azw3 import (
    AZW3BackendUnavailableError,
    AZW3ConversionBackend,
    AZW3ConversionError,
    AZW3ConversionFailedError,
    AZW3ConversionResult,
    AZW3InvalidInputError,
    AZW3InvalidOutputError,
    convert_epub_to_azw3,
)
from .builder import (
    EPUBGenerationError,
    InvalidImageError,
    NoChaptersError,
    build_epub,
)
from .calibre import CalibreBackend
from .validation import (
    EPUBValidationCode,
    EPUBValidationError,
    EPUBValidationIssue,
    EPUBValidationResult,
    EPUBValidationSeverity,
    validate_epub,
)

__all__ = [
    "AZW3BackendUnavailableError",
    "AZW3ConversionBackend",
    "AZW3ConversionError",
    "AZW3ConversionFailedError",
    "AZW3ConversionResult",
    "AZW3InvalidInputError",
    "AZW3InvalidOutputError",
    "CalibreBackend",
    "EPUBGenerationError",
    "EPUBValidationCode",
    "EPUBValidationError",
    "EPUBValidationIssue",
    "EPUBValidationResult",
    "EPUBValidationSeverity",
    "InvalidImageError",
    "NoChaptersError",
    "build_epub",
    "convert_epub_to_azw3",
    "validate_epub",
]
