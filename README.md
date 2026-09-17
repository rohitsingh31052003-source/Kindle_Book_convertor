# PDF to Kindle Converter

A Python-based document conversion tool for transforming PDFs into high-quality, reflowable ebook formats such as **EPUB** and **AZW3**, with a focus on books intended for Kindle.

The project is being developed as a local-first conversion engine that can handle different kinds of PDFs, including text-based documents, scanned books, and PDFs with more complex layouts.

## Project Status

**Development stage:** Milestone 4 — Kindle Output (M4.1 and M4.2 complete)

The project now has a working conversion engine with deterministic
validation. PDF analysis, layout-aware reconstruction (reading order,
paragraphs, headings, chapters, page-number and header/footer removal,
metadata, images), OCR-aware processing for scanned and mixed PDFs,
Kindle-oriented EPUB generation, and structural EPUB validation are
implemented and covered by a deterministic test suite. AZW3
conversion (M4.3), cover handling (M4.4), Kindle-specific formatting
improvements (M4.5), and a graphical interface are upcoming milestones.

## Goals

The converter should eventually be able to:

* Convert text-based PDFs into reflowable EPUB files.
* Handle scanned/image-based PDFs using OCR.
* Detect different types of PDF structures automatically.
* Preserve chapters, headings, paragraphs, images, and other important book content.
* Remove common PDF artifacts such as page numbers and repeated headers/footers.
* Generate a useful table of contents.
* Preserve or generate book metadata and covers.
* Produce Kindle-friendly EPUB files.
* Convert EPUB output to AZW3.
* Provide a simple interface for users who do not want to work from the command line.
* Process books locally without requiring users to upload their documents to a remote service.

## Design Philosophy

PDF and EPUB represent documents in fundamentally different ways.

A PDF primarily describes how content is positioned on a page, while EPUB describes a reflowable document.

Therefore, the project will **not** treat conversion as a simple:

```text
PDF → EPUB
```

operation.

Instead, the application will reconstruct the logical structure of the book first.

The intended architecture is:

```text
                  ┌─────────────────┐
                  │      PDF        │
                  └────────┬────────┘
                           │
                           ▼
                  ┌─────────────────┐
                  │  PDF Analyzer   │
                  └────────┬────────┘
                           │
             ┌─────────────┼─────────────┐
             ▼             ▼             ▼
          Text PDF      Scanned PDF    Mixed PDF
             │             │             │
             ▼             ▼             ▼
        Text Extract      OCR       Layout Analysis
             │             │             │
             └─────────────┼─────────────┘
                           ▼
                  ┌─────────────────┐
                  │  Document Model │
                  └────────┬────────┘
                           │
                           ▼
                   ┌─────────────────┐
                   │  EPUB Builder   │
                   └────────┬────────┘
                            │
                            ▼
                          EPUB
                            │
                            ▼
                   ┌─────────────────┐
                   │  EPUB Validator │
                   └────────┬────────┘
                            │
                            ▼
                     valid EPUB
                            │
                            ▼
                   ┌─────────────────┐
                   │  AZW3 Converter  │
                   └────────┬────────┘
                           │
                           ▼
                         AZW3
```

The **Document Model** is the central abstraction of the application. PDF-specific extraction logic should not be tightly coupled to EPUB generation.

This allows additional input formats and output formats to be introduced later without rewriting the entire application.

## Planned Architecture

The project is expected to be organized approximately as follows:

```text
src/
└── kindle_converter/
    ├── pdf/
    │   ├── analyzer.py
    │   ├── extractor.py
    │   ├── ocr.py
    │   └── layout.py
    │
    ├── document/
    │   ├── models.py
    │   ├── structure.py
    │   └── cleanup.py
    │
    ├── epub/
    │   ├── builder.py
    │   ├── toc.py
    │   ├── css.py
    │   └── validation.py
    │
    ├── azw3/
    │   └── converter.py
    │
    └── pipeline.py
```

The exact structure may evolve as implementation progresses.

## Conversion Pipeline

The long-term pipeline is:

```text
Input PDF
   │
   ▼
Analyze document
   │
   ├── Text-based
   ├── Scanned
   └── Mixed
   │
   ▼
Extract content
   │
   ├── Text extraction
   ├── Image extraction
   └── OCR when required
   │
   ▼
Clean and normalize
   │
   ├── Paragraph reconstruction
   ├── Header/footer removal
   ├── Page-number removal
   └── Text normalization
   │
   ▼
Detect document structure
   │
   ├── Chapters
   ├── Headings
   ├── Paragraphs
   ├── Images
   └── Page breaks
   │
   ▼
Internal Book Model
   │
   ▼
EPUB generation
   │
   ▼
EPUB validation
   │
   ├───────────────┐
   ▼               ▼
  EPUB            AZW3
```

## Technology

The initial implementation will use Python.

Planned core technologies include:

* **Python 3.12+**
* **PyMuPDF** for PDF inspection and extraction
* **EbookLib** for EPUB generation
* **pytest** for automated testing

Additional dependencies, particularly OCR-related tools, will be introduced only when they are required by the corresponding milestone.

## Development

### Requirements

* Python 3.12 or newer

### Setup

Create a virtual environment and install the package in editable mode with
its development extra:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### Running the tests

pytest is configured (via `pythonpath = ["src"]` in `pyproject.toml`) to find the
`src/`-layout package, so tests can be run **without** installing the package:

```bash
pytest
```

For full development setup (including the runtime dependencies), use the
editable install described above.

### OCR (scanned PDFs)

OCR support (Milestone 3.3) is optional and has **two distinct
requirements**. The Tesseract executable is installed by Tesseract's own
installer, never by this project.

1. **Python packages (optional `ocr` extra):** the `pytesseract` and
   `Pillow` wrappers.

   ```bash
   pip install -e ".[ocr]"
   ```

   The OCR API stays importable without them; only the built-in
   `TesseractEngine` needs the extra.

2. **Tesseract executable (external runtime requirement):** the
   `tesseract` command must be discoverable on `PATH`, or its explicit
   path passed to `TesseractEngine(tesseract_cmd=...)`. The integration
   tests also honour the `TESSERACT_CMD` environment variable:

   ```bash
   # Windows example (adjust to your install location)
   TESSERACT_CMD="C:\Program Files\Tesseract-OCR\tesseract.exe" pytest

   # Verify availability
   tesseract --version
   tesseract --list-langs   # 'eng' must be listed
   ```

   When neither `TESSERACT_CMD` nor a `PATH`-discoverable `tesseract` is
   available, the optional OCR integration tests skip cleanly; the core
   test suite never requires Tesseract.

### OCR cleanup (scanned PDFs)

Cleanup (Milestone 3.4) runs **after** OCR as a separate, deterministic
layer (`clean_ocr_text` / `clean_ocr_result` / `clean_ocr_pages`). It is
deliberately conservative: it normalizes line endings, applies Unicode NFC
normalization, removes invalid control characters, trims trailing line
whitespace, collapses pathological blank-line runs, and strips document-edge
whitespace. It never attempts recognition correction — no spelling,
dictionary, grammar, character-substitution, or language-model correction —
and it never performs structural reconstruction. OCR recognition output
itself is consumed verbatim by cleanup.

### Mixed text/image routing (M3.5)

Milestone 3.5 adds per-page routing for mixed text/image documents
(`process_page` / `process_pages` in `kindle_converter.pdf.processing`).
Each page is routed deterministically from its `PageAnalysis`
classification:

* **Text pages** — native text extraction only (`deduplicate_layout` /
  `extract_page_layout`); OCR never runs.
* **Scanned pages** — rendered (`render_page`), OCR'd (`ocr_page`), and
  cleaned (`clean_ocr_result`) in sequence.
* **Mixed pages** — native text is preserved **and** a full-page OCR pass
  is run through cleanup. The two extractions are kept as separate,
  immutable fields (`native_text` / `ocr_text`) and are never merged;
  partial overlap between them is intentional and deduplication is deferred
  to a later structural-reconstruction milestone.

The module performs no structural reconstruction (no paragraphs, headings,
reading order, or deduplication of the two streams). Pages are processed in
document order with 1-based page numbers, and results are always returned in
that order. OCR engines and renderers are injectable through the public API.

### Unified PDF → Book → EPUB pipeline (M4.1)

Milestone 4.1 makes EPUB generation the production output path for every
supported PDF, through a single pipeline:

```text
PDF → convert_pdf_to_book() → Book → build_epub() → EPUB
```

* **Text PDFs** — native reconstruction produces the `Book`, which is then
  rendered as an EPUB.
* **Scanned PDFs** — pages are routed through the OCR-aware processing path
  (render → OCR → cleanup → structural reconstruction) to produce the `Book`,
  which is rendered by the same EPUB builder.
* **Mixed PDFs** — native text and OCR text are reconstructed into one `Book`
  (native paragraphs first, OCR paragraphs after, never merged), which is
  rendered by the same EPUB builder.

There is exactly **one** EPUB rendering path, and it consumes the
format-independent `Book` model only: the EPUB layer performs no PDF
inspection, OCR, scanned-page detection, paragraph reconstruction, reading
order, or heading detection. (The M1.5-era behavior in which
`convert_pdf_to_epub` refused scanned and mixed PDFs has been removed;
`extract_book` remains a text-only extractor, and its `ScannedPDFError` /
`MixedPDFError` exceptions are unchanged for callers that use it directly.)

`convert_pdf_to_epub(source, output, *, engine=None, renderer=None, dpi=...)`
accepts an optional OCR engine. When none is injected, Tesseract is used
*lazily*: the built-in engine is only constructed for the first scanned/mixed
page that actually needs recognition, so text-only conversion never requires
the optional `ocr` extra or a Tesseract installation. Existing error behavior
is preserved (`PDFReadError`, `EmptyPDFError`, `NoContentError`, and
`EPUBGenerationError` remain identifiable), and OCR failures surface as the
existing `OCRError` / `OCREngineUnavailableError` domain errors.

### Kindle-oriented EPUB output (M4.1)

The generated EPUB is a **reflowable ebook**, not a PDF replica:

* normal document flow only — no fixed page dimensions, no absolute
  positioning, no viewport units, no JavaScript, no external resources;
* semantic markup: headings stay `<h1>`–`<h6>`, paragraphs stay `<p>`, images
  stay `<img>`;
* a small, conservative, Kindle-oriented stylesheet (`style.css`) supplies the
  body text, paragraph, heading, image, and page-break defaults;
* images keep their original bytes and media types and are constrained to the
  reading width (`max-width: 100%; height: auto`);
* `PageBreak` semantics become a structural break element
  (`page-break-after` / `break-after`) rather than a fixed PDF-sized page;
* chapter order, titles, and navigation are preserved (EPUB 3 `nav.xhtml` plus
  an `toc.ncx` for older readers);
* the metadata carried by the `Book` (title, author, language, publisher,
  identifier, description, subject) is preserved.

Output is deterministic as far as the EPUB library allows: repeated builds of
the same `Book` produce identical chapters, resources, navigation, CSS, and
package metadata. EbookLib itself stamps a `dcterms:modified` timestamp (and
ZIP entry times) into the container; that library-side variation is documented
rather than worked around.

EPUB **validation** is M4.2 and is not part of M4.1; AZW3 conversion (M4.3),
cover handling (M4.4), and Kindle-specific formatting improvements (M4.5) are
likewise out of scope here.

EPUB **validation** is M4.2. It inspects the finished EPUB
artifact independently of PDF processing and reports structured
validation results:

```text
Book → build_epub() → EPUB → validate_epub() → EPUBValidationResult
```

`validate_epub()` accepts a filesystem path or the EPUB archive
bytes. It imports no PDF/OCR code and never modifies the archive.
It validates the ZIP container, `mimetype`, `META-INF/container.xml`,
the OPF package document, required metadata, manifest, spine, XHTML
documents, internal resource references, stylesheets, navigation,
images, and page-break markers. Failures are reported as
`EPUBValidationIssue` values with machine-readable
`EPUBValidationCode` codes; expected malformed input never leaks as
raw low-level exceptions.

### Building the package

```bash
pip install build
python -m build
```

## Development Principles

### 1. Build the conversion engine first

The core conversion pipeline will be developed and tested before building a GUI.

### 2. Keep PDF processing separate from ebook generation

PDF-specific code should produce a structured document representation rather than directly producing EPUB HTML.

### 3. Prefer deterministic processing

Where possible, the same input and configuration should produce the same output.

### 4. Test difficult cases

The project will eventually maintain a collection of representative PDFs covering different layouts and problems.

Examples include:

* Normal novels
* Textbooks
* Multi-column documents
* Scanned books
* PDFs containing images
* PDFs with headers and footers
* PDFs with unusual typography
* Mixed text/image PDFs

### 5. Small, reviewable commits

Development will use Git with small commits representing individual logical changes.

Example:

```text
chore: initialize python project
feat: add document domain models
feat: add pdf type analyzer
feat: add pdf text extraction
feat: add epub builder
test: add pdf extraction coverage
```

### 6. Avoid premature complexity

Features such as OCR, advanced layout reconstruction, GUI functionality, and Kindle-specific optimizations will be introduced only after the underlying conversion architecture is stable.

## Development Roadmap

### Milestone 1.0 — Project Foundation

* [x] Initialize Python project
* [x] Establish package structure
* [x] Add automated tests
* [x] Create document domain model
* [x] Implement PDF type analysis
* [x] Implement basic text extraction
* [x] Implement initial EPUB generation
* [x] Create an end-to-end PDF → EPUB pipeline
* [x] Create document domain model
* [x] Implement PDF type analysis
* [x] Implement basic text extraction
* [x] Implement initial EPUB generation
* [x] Create an end-to-end PDF → EPUB pipeline

### Milestone 2 — Book Reconstruction

* [x] Chapter detection (M2.9)
* [x] Heading detection (M2.4 — layout-based, conservative, deterministic)
* [x] Paragraph reconstruction (M2.3)
* [x] Header/footer removal (M2.5)
* [x] Page-number removal (M2.10)
* [x] Table of contents generation (M2.11)
* [x] Metadata handling (M2.12)
* [x] Image extraction and placement (M2.13)

### Milestone 3 — Scanned PDFs and OCR

* [x] Detect scanned PDFs
* [x] Render PDF pages
* [x] OCR processing
* [x] OCR cleanup (M3.4 — conservative, deterministic; no recognition correction)
* [x] Mixed text/image document handling (M3.5 — deterministic per-page OCR routing)
* [x] Improve structural reconstruction (M3.6 — OCR-aware reconstruction)

### Milestone 4 — Kindle Output

* [x] Kindle-friendly EPUB generation (M4.1 — one reflowable EPUB path from
  the unified `Book`, for TEXT, SCANNED, and MIXED PDFs)
* [x] EPUB validation (M4.2 — structural validation of generated EPUB
  artifacts through `kindle_converter.epub.validate_epub`)
* [ ] AZW3 conversion
* [ ] Cover handling
* [ ] Kindle-specific formatting improvements

### Milestone 5 — User Interface

* [ ] Simple desktop interface
* [ ] Drag-and-drop input
* [ ] Output format selection
* [ ] Conversion progress
* [ ] Error reporting
* [ ] Output directory management

### Milestone 6 — Quality and Distribution

* [ ] Build representative test corpus
* [ ] Add regression tests
* [ ] Measure conversion quality
* [ ] Improve performance
* [ ] Package for Windows
* [ ] Documentation
* [ ] Release preparation

## Project Philosophy

The goal is not simply to make a PDF file open on a Kindle.

The goal is to produce an ebook that behaves like a **properly structured digital book**:

* Text should reflow naturally.
* Chapters should be navigable.
* Headings should have meaningful hierarchy.
* Images should remain usable.
* Unnecessary PDF artifacts should disappear.
* The resulting ebook should be comfortable to read on different screen sizes.

Conversion quality is therefore more important than simply completing the file-format conversion.

## License

License to be determined during the initial project setup.
