"""PDF to Kindle - convert PDFs into reflowable ebooks for Kindle.

Public API
----------
The one-call end-to-end entry point converts *any* supported PDF -- text,
scanned, or mixed -- into a Kindle-oriented, reflowable EPUB through the
unified document-model pipeline ``PDF -> Book -> EPUB`` (Milestone 4.1):

    >>> from kindle_converter import convert_pdf_to_epub
    >>> convert_pdf_to_epub("book.pdf", "book.epub")
    >>> convert_pdf_to_epub("scan.pdf", "scan.epub", engine=TesseractEngine())

The OCR-aware document-model entry point (Milestone 3.6) produces the
:class:`~kindle_converter.document.Book` that the EPUB builder consumes;
scanned and mixed PDFs are processed through the OCR path:

    >>> from kindle_converter import convert_pdf_to_book
    >>> from kindle_converter.pdf import TesseractEngine
    >>> book = convert_pdf_to_book("scan.pdf", engine=TesseractEngine())

The lower-level stages remain available for callers that need them:

    >>> from kindle_converter.pdf import analyze_pdf, extract_book
    >>> from kindle_converter.epub import build_epub

Formal EPUB validation (M4.2) and AZW3 conversion (M4.3) are not implemented
yet.
"""

from .pipeline import convert_pdf_to_book, convert_pdf_to_epub

__all__ = ["convert_pdf_to_book", "convert_pdf_to_epub"]
__version__ = "0.1.0"