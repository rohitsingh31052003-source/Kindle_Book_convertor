# PDF to Kindle Converter

A Python-based document conversion tool for transforming PDFs into high-quality, reflowable ebook formats such as **EPUB** and **AZW3**, with a focus on books intended for Kindle.

The project is being developed as a local-first conversion engine that can handle different kinds of PDFs, including text-based documents, scanned books, and PDFs with more complex layouts.

## Project Status

**Development stage:** Milestone 1.0 — Project Foundation

The project is currently being built from the ground up. Milestone 1.0 sets up a clean, testable project foundation (packaging, package structure, test harness). PDF processing, EPUB generation, OCR, Kindle-specific optimization, and a graphical interface will be added in later milestones.

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
    │   └── validator.py
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

### Milestone 1.0 — Project Foundation *(current)*

* [x] Initialize Python project
* [x] Establish package structure
* [x] Add automated tests
* [ ] Create document domain model
* [ ] Implement PDF type analysis
* [ ] Implement basic text extraction
* [ ] Implement initial EPUB generation
* [ ] Create an end-to-end PDF → EPUB pipeline

### Milestone 2 — Book Reconstruction

* [ ] Chapter detection
* [x] Heading detection (M2.4 — layout-based, conservative, deterministic)
* [x] Paragraph reconstruction (M2.3)
* [ ] Header/footer removal
* [ ] Page-number removal
* [ ] Table of contents generation
* [ ] Metadata handling
* [ ] Image extraction and placement

### Milestone 3 — Scanned PDFs and OCR

* [x] Detect scanned PDFs
* [x] Render PDF pages
* [x] OCR processing
* [ ] OCR cleanup
* [ ] Mixed text/image document handling
* [ ] Improve structural reconstruction

### Milestone 4 — Kindle Output

* [ ] Kindle-friendly EPUB generation
* [ ] EPUB validation
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
