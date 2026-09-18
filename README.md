# PDF to Kindle Converter

A Python-based document conversion tool for transforming PDFs into high-quality, reflowable ebook formats such as **EPUB** and **AZW3**, with a focus on books intended for Kindle.

The project is being developed as a local-first conversion engine that can handle different kinds of PDFs, including text-based documents, scanned books, and PDFs with more complex layouts.

## Project Status

**Development stage:** Milestone 5 — User Interface (M5.3 — PDF input selection and analysis UI complete)

The project has a working conversion engine with deterministic
validation. PDF analysis, layout-aware reconstruction (reading order,
paragraphs, headings, chapters, page-number and header/footer removal,
metadata, images), OCR-aware processing for scanned and mixed PDFs,
Kindle-oriented EPUB generation, structural EPUB validation, EPUB → AZW3
conversion (via Calibre's `ebook-convert`), explicit optional cover
handling, and Kindle-specific reflowable formatting improvements are
implemented and covered by a deterministic test suite. The UI-independent
application / pipeline API (M5.1, `kindle_converter.application`) wraps
the conversion use case behind a stable boundary for the graphical
interface. M5.2 added the optional PySide6 desktop application shell
(`kindle_converter.ui`, the `ui` extra): a launchable main window and
entry point (`python -m kindle_converter.ui`). M5.3 implements the first
real desktop workflow on top of that shell: the window can select a PDF
from a file picker, analyze it through the application API (never by
reading the PDF itself and never by calling the pipeline directly), and
display a useful summary (page count, document type, text/scanned/mixed
page counts, OCR requirement) with explicit UI states and clean
validation/error handling. Conversion execution (progress, output
selection, results) is deliberately deferred to later M5 milestones;
M5.3 ends analyzed-and-ready.

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
    │   ├── cover.py       # M4.4: explicit optional cover input handling
    │   ├── structure.py
    │   └── cleanup.py
    │
    ├── epub/
    │   ├── builder.py
    │   ├── formatting.py  # M4.5: Kindle formatting profile + CSS generation
    │   ├── toc.py
    │   ├── css.py
    │   ├── validation.py
    │   ├── azw3.py        # M4.3: EPUB → AZW3 conversion API
    │   └── calibre.py     # M4.3: Calibre ebook-convert backend
    │
    ├── application/       # M5.1: UI-independent conversion use case
    │   ├── converter.py   # ConversionApplication: analyze_pdf (M5.3) / convert
    │   ├── request.py     # ConversionRequest / OutputFormat
    │   ├── result.py      # ConversionResult
    │   ├── progress.py    # ConversionStage / ConversionProgress / callback
    │   └── errors.py      # ApplicationError boundary (subclasses PipelineError)
    │
    ├── ui/                # M5.2/M5.3: PySide6 desktop UI (ui extra)
    │   ├── app.py         #   application entry point (create_application / main)
    │   ├── main_window.py #   input selection + PDF analysis window (UiState model)
    │   └── __main__.py    #   python -m kindle_converter.ui
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
* **PySide6** for the desktop UI (optional `ui` extra, Milestone 5.2)
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

### Desktop UI (M5.2 shell, M5.3 input selection + analysis)

The desktop application is a PySide6 UI (`kindle_converter.ui`). PySide6 is an
**optional** dependency (the `ui` extra): the core library never imports it,
so `import kindle_converter` keeps working in a base installation.

Install the UI extra:

```bash
pip install -e ".[ui]"
```

Launch the desktop application:

```bash
python -m kindle_converter.ui
```

M5.3 implements the first desktop workflow: select a PDF with the file picker,
see the selected path, run **Analyze PDF**, and read a summary of the M3.1
analysis (page count, document type, text/scanned/mixed page counts, and
whether OCR is required). The window tracks an explicit small state model
(`UiState`: no input → input selected → analyzing → analysis complete /
analysis failed), invalidates stale analysis when the input changes, and
translates validation and analysis failures into user-readable status messages
while staying usable for a retry.

The UI is a thin presentation layer: analysis is always invoked through the
M5.1 application API (`ConversionApplication.analyze_pdf`), never by importing
or calling the PDF/pipeline implementation from the UI, and no PDF is ever
opened just to populate the path display. Analysis runs synchronously in M5.3
(no `QThread`, no background workers); a later milestone moves conversion work
off the event loop without moving PDF/business logic into the UI.

M5.3 performs **no conversion** and adds no conversion UI (no progress, no
output/format/cover selection, no results screen). After a successful analysis
the window is ready for that later conversion workflow; nothing else happens.
The M5.1 application / pipeline API remains the boundary the UI drives.

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
cover handling (M4.4), and the Kindle formatting refinements (M4.5) are
separate milestones layered on top of it.

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

### AZW3 conversion (M4.3, requires optional Calibre)

M4.3 adds EPUB → AZW3 conversion as an explicit, standalone step on top of
the finished EPUB artifact:

```text
Book → build_epub() → EPUB → convert_epub_to_azw3() → AZW3
```

`convert_epub_to_azw3()` consumes an **EPUB artifact** (a filesystem path) —
never a PDF and never a `Book`. It performs no EPUB validation and no PDF
processing internally; those remain separate steps. AZW3 generation is done
by an external conversion backend: the production backend wraps **Calibre's
`ebook-convert` command** through a small adapter (see
`kindle_converter.epub.calibre`), and the AZW3 format itself is never
implemented in Python.

```python
from kindle_converter.epub import convert_epub_to_azw3

# Discovered automatically on PATH (e.g. C:\Program Files\Calibre2 on Windows)
convert_epub_to_azw3("book.epub", "book.azw3")

# Or point at an explicit ebook-convert executable
convert_epub_to_azw3(
    "book.epub",
    "book.azw3",
    calibre_path=r"C:\Program Files\Calibre2\ebook-convert.exe",
)
```

Key facts about the M4.3 feature:

* **Calibre is optional and external.** It is a system dependency installed
  by Calibre's own installer, never by this project: nothing in
  `pyproject.toml` pulls it in, `import kindle_converter` keeps working
  without it, and EPUB generation/validation never require it. Only AZW3
  conversion touches Calibre.
* **It is a separate, explicit step.** AZW3 output is never generated
  implicitly by `convert_pdf_to_epub` or `convert_pdf_to_book`. Callers
  compose the full chain (`PDF → Book → EPUB → AZW3`) themselves when they
  need it. The existing PDF → EPUB behavior is unchanged.
* **Safe subprocess invocation.** `ebook-convert` is invoked with
  `subprocess.run([...], shell=False, capture_output=True, ...)` — no shell,
  no shell interpolation, paths stay separate arguments (spaces are safe),
  the return code is checked, and a missing/unrunnable executable or a
  non-zero exit surfaces as a dedicated project error (not a raw
  `FileNotFoundError`/`CalledProcessError`).
* **Error taxonomy.** `AZW3ConversionError` is the base class;
  `AZW3BackendUnavailableError` (Calibre not found), `AZW3ConversionFailedError`
  (non-zero exit, with a bounded diagnostic excerpt), `AZW3InvalidInputError`
  (missing/unreadable source EPUB), and `AZW3InvalidOutputError` (Calibre
  reported success but the output is missing/empty) are the specific cases.
* **Validation guarantee.** "Success" means Calibre exited 0 **and** the
  requested output exists, is a regular file, and is non-empty. It is
  **not** a guarantee of Kindle rendering correctness or marketplace
  acceptance — no AZW3 parser or Kindle compatibility validator is
  implemented.
* **Determinism.** Command construction is deterministic (same executable +
  input + output → same argument list, no shell quoting, no timestamps, no
  temporary files). Byte-for-byte deterministic AZW3 output is **not**
  promised: Calibre may stamp its own metadata/timestamps into the file.
* **Tests.** The core suite tests conversion through a stubbed subprocess and
  injected fake backends — Calibre is never required, and paths containing
  spaces/child directories are covered. An optional integration suite
  (`tests/test_epub_azw3_calibre.py`) runs the real `ebook-convert` when it
  is available and skips cleanly otherwise:

  ```bash
  # Windows example (adjust to your install location)
  CALIBRE_CONVERT="C:\Program Files\Calibre2\ebook-convert.exe" pytest
  ```

### Cover handling (M4.4)

M4.4 adds an explicit, optional cover to the conversion path. Both entry
points accept a keyword-only `cover` argument — a filesystem path to a
supported image (JPEG, PNG, GIF, or SVG) or an already-loaded
`kindle_converter.document.Image`:

```python
from kindle_converter import convert_pdf_to_epub
from kindle_converter.document import Image

# From a file — the format is sniffed from the image bytes
convert_pdf_to_epub("book.pdf", "book.epub", cover="cover.jpg")

# Or pass an in-memory image directly
cover = Image(data=open("cover.png", "rb").read(), content_type="image/png")
convert_pdf_to_book("book.pdf", engine, cover=cover)
```

Key facts about the M4.4 feature:

* **Explicit and optional.** The cover is never auto-detected — no PDF
  filename heuristics, no first-page analysis, no image classification.
  A `Book` (or EPUB) without a cover renders exactly as before.
* **Validated early.** `cover` is resolved through
  `kindle_converter.document.load_cover` before the PDF is even opened, so a
  missing file, unreadable path, empty data, or unsupported format fails
  fast with a dedicated error (`CoverNotFoundError`,
  `CoverUnreadableError`, `CoverInvalidDataError`,
  `CoverUnsupportedFormatError`, all under `CoverError`) before any output
  is written.
* **Correct EPUB cover semantics** using EbookLib's supported mechanisms
  (no post-hoc ZIP/XML patching): the image is packaged as the cover (the
  OPF manifest marks it `properties="cover-image"` and the `name="cover"`
  package metadata points at it), and a minimal XHTML cover page
  (`EPUB/cover.xhtml`) reuses the project stylesheet and the reflowable
  `img.image` profile, so the cover has no fixed dimensions.
* **Cover page as a reading step, not a navigation entry.** The cover page
  is the first content document in the spine, but it is deliberately *not*
  added to the book's TOC: it produces no chapter document, no
  `nav.xhtml` entry, and no NCX `navPoint`.
* **Deterministic.** A given book and cover always produce the same cover
  image name (`images/cover.<ext>`), cover page, manifest entry, metadata,
  and spine order (`nav`, `cover`, chapters).
* **Validated output.** A covered EPUB passes the M4.2 structural validator
  (`validate_epub`), and the optional Calibre integration suite also
  converts a covered EPUB to AZW3.

```text
Book → build_epub() → EPUB (cover: properties="cover-image" + name="cover" + cover.xhtml)
```

### Kindle-specific formatting (M4.5)

M4.5 refines the **output** of the existing `Book → EPUB` boundary so the
generated EPUB reads well on a Kindle, without changing the conversion
architecture:

```text
PDF/OCR/reconstruction → Book → Kindle-oriented EPUB builder → EPUB → (optional) AZW3
```

* **One formatting profile.** The stylesheet is no longer a set of CSS literals
  in the builder: `kindle_converter.epub.formatting` owns a small, frozen
  `KindleFormattingProfile` (body, paragraph, heading, chapter, image,
  blockquote, list, and page-break values) and a pure
  `build_stylesheet(profile)` function. `builder.STYLESHEET_CSS` is simply the
  stylesheet the default profile produces, so the CSS has one source of truth
  and is reproducible.
* **Reflowable typography.** Relative units only (`em`/`%`); no `px`, `pt`,
  `in`, `cm`, `mm`, `vh`, or `vw` anywhere. Nothing is fixed, so the reader's
  font family and font-size controls stay effective. The body keeps a
  conservative font stack ending in a generic family, and no font is embedded
  (no `@font-face`, no font files).
* **Paragraphs and headings.** Paragraphs stay semantic `<p>` with a modest
  `1em` bottom margin, no indentation, and `1.5` line-height. Headings keep
  their `<h1>`–`<h6>` hierarchy with relative, size-decreasing sizes, spacing
  before/after, and conservative break avoidance (`break-after` /
  `page-break-after: avoid`, `break-inside: avoid`).
* **Chapters.** Each chapter remains its own XHTML document with unchanged
  titles, order, spine, and navigation. A chapter's opening heading no longer
  adds leading blank space (`h1:first-child` … `h6:first-child` →
  `margin-top: 0`). Chapter splitting is untouched.
* **Page breaks.** The M4.1/M3.6 semantics are preserved exactly: a
  `PageBreak` stays an empty, `aria-hidden` `div.page-break` with
  `page-break-after: always` plus the modern `break-after: always`
  counterpart. No break is forced before every paragraph and no PDF pagination
  is imitated; `break-inside: avoid` is used only on headings, images, and
  list items.
* **Images.** Document images stay responsive — `max-width: 100%`,
  `height: auto` (aspect ratio preserved), `display: block`, centered when
  smaller than the screen — with no fixed geometry in the markup, no cropping,
  no upscaling, and no changes to the image bytes. The M4.4 cover reuses the
  same rule, so a covered book needs no cover-specific, device-specific CSS.
* **Semantic blockquotes and lists.** The stylesheet defines conservative
  `blockquote`, `ul`/`ol`, and `li` defaults (em-based margins, readable
  leading, no generated content, no manual numbering). The current document
  model has no blockquote or list blocks, so none are invented during
  conversion.
* **Simple, Kindle-friendly CSS.** One stylesheet, ordered in readable
  sections (base/body, headings, chapters, paragraphs, images, cover,
  blockquotes, lists, page breaks), with no generated per-element classes, no
  `@import`/`@media`, no `position: absolute|fixed`, no grid/flex/columns, no
  JavaScript, no animations/transitions, no `url()`/remote fonts/external
  resources, and no `vh`/`vw` device hacks. Internal links, metadata,
  navigation, and cover semantics are unchanged.
* **No new API, no new dependency.** The formatting profile is internal: there
  is no user-selectable theme, and `convert_pdf_to_book`,
  `convert_pdf_to_epub`, `build_epub`, `validate_epub`, and
  `convert_epub_to_azw3` keep working unchanged. M4.5 adds no dependency to
  `pyproject.toml`.
* **Validated and deterministic.** M4.5 output keeps passing the M4.2
  validator (`validate_epub`), stays convertible through M4.3, and produces
  byte-identical stylesheets and chapter XHTML across builds of the same
  `Book`.

What M4.5 deliberately does **not** provide:

* exact Kindle device rendering, screen simulation, or pixel matching;
* fixed-layout EPUB, KFX, or Kindle Previewer automation;
* device-specific CSS profiles, media queries, or per-model hacks;
* an advanced typography engine (hyphenation, font embedding, optical margin
  alignment), and any change to PDF reconstruction, OCR, or chapter splitting.

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
* [x] AZW3 conversion (M4.3 — explicit EPUB → AZW3 step through Calibre's
  optional external `ebook-convert` tool)
* [x] Cover handling (M4.4 — explicit, optional cover: validated input,
  `cover-image`/`name="cover"` EPUB semantics, reflowable cover page)
* [x] Kindle-specific formatting improvements (M4.5 — one internal formatting
  profile, reflowable `em` typography, semantic heading/paragraph/chapter
  formatting, conservative page-break behavior, responsive centered images,
  conservative blockquote/list defaults, and a prohibited-construct-free
  deterministic stylesheet)

### Milestone 5 — User Interface

* [x] Application / Pipeline API (M5.1 — UI-independent
  `kindle_converter.application`: `ConversionApplication.convert` composing
  analysis, EPUB generation, validation, and optional AZW3; typed
  `ConversionRequest`, `ConversionResult`, progress callback, and
  application error boundary)
* [x] PySide6 application shell (M5.2 — optional `ui` extra,
  `kindle_converter.ui` main window + entry point; no conversion workflow yet)
* [x] Input selection + PDF analysis UI (M5.3 — select a PDF, analyze it
  through `ConversionApplication.analyze_pdf`, display the M3.1 analysis
  summary; explicit UI states and clean validation/error handling; no
  conversion yet)
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
