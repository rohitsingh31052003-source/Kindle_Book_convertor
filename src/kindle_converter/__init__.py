"""PDF to Kindle - convert PDFs into reflowable ebooks for Kindle.

Public API
----------
The one-call end-to-end entry point converts a PDF into an EPUB:

    >>> from kindle_converter import convert_pdf_to_epub
    >>> convert_pdf_to_epub("book.pdf", "book.epub")

The lower-level stages remain available for callers that need them:

    >>> from kindle_converter.pdf import analyze_pdf, extract_book
    >>> from kindle_converter.epub import build_epub
"""

from .pipeline import convert_pdf_to_epub

__all__ = ["convert_pdf_to_epub"]
__version__ = "0.1.0"