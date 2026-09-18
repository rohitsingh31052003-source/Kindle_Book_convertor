"""M6.2 regression test harness for the Kindle Book Converter.

This package is the deterministic regression harness that runs the existing
conversion pipeline against the M6.1 representative corpus
(``tests/fixtures/corpus``) and asserts observable, semantic output:

* analysis drives conversion routing through the public pipeline
  (:func:`kindle_converter.convert_pdf_to_book` /
  :func:`kindle_converter.convert_pdf_to_epub`);
* block composition, heading hierarchy, OCR call counts, chapter detection,
  body content anchors, and EPUB artifact structure stay stable;
* every expectation lives in the machine- and human-readable baseline at
  ``tests/fixtures/regression_baseline.json``, kept small and incremental
  (never a byte-for-byte EPUB snapshot);
* the suite is deterministic: OCR is injected, never Tesseract, so the
  default run needs no optional dependencies and no external tools.

Marked ``regression`` so ``pytest -m regression`` selects it and ``pytest``
runs it as part of the full suite.
"""