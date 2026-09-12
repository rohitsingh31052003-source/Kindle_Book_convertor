"""EPUB output generation.

This package converts the format-independent
:class:`~kindle_converter.document.models.Book` model into a valid,
reflowable EPUB. :func:`build_epub` is the public entry point; the low-level
rendering lives in :mod:`kindle_converter.epub.builder`.
"""

from .builder import (
    EPUBGenerationError,
    InvalidImageError,
    NoChaptersError,
    build_epub,
)

__all__ = [
    "EPUBGenerationError",
    "InvalidImageError",
    "NoChaptersError",
    "build_epub",
]
