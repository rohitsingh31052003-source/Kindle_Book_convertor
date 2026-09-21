# Kindle Book Converter

Kindle Book Converter is a **local desktop application** that converts PDFs
into reflowable, Kindle-friendly **EPUB** ebooks on your own computer — and,
optionally, **AZW3** when Calibre is installed.

Instead of copying a PDF's fixed page layout, the converter reconstructs the
book's logical structure — reading order, paragraphs, headings, chapters,
images, and metadata — removes common PDF furniture such as repeated headers,
footers, and page numbers, and renders that structure as a reflowable ebook
that reads like a book rather than a page image.

All processing happens **on your machine**: PDFs are never uploaded to a
remote service. Text-based PDFs convert directly. **Scanned** and **mixed**
PDFs can be converted using the optional **Tesseract OCR** engine (see
[OCR](#ocr)). EPUB output is validated structurally, and AZW3 is generated
from the EPUB through Calibre (see [AZW3 (optional)](#azw3-optional)).

The project has completed **Milestones 1–8**, including its first public
**GitHub release** (`v0.1.0`, with the Windows distribution ZIP and its
SHA-256 checksum attached). See the [Roadmap](#roadmap).

## Features

### PDF processing

* **PDF analysis** — deterministic classification of every PDF as `TEXT`,
  `SCANNED`, or `MIXED`, with per-page text/scanned/mixed counts and an
  explicit "OCR required" signal.
* **Text extraction and layout reconstruction** — layout-aware native text
  extraction, including reading-order reconstruction for multi-column pages.
* **Paragraph reconstruction** — lines reassembled into paragraphs instead of
  being emitted line-for-line.
* **Heading detection** — conservative, layout-based heading detection
  (explicit headings such as `Chapter N:` are surfaced reliably).
* **Chapter detection and table of contents** — chapters are detected and
  indexed from the PDF layer for navigation.
* **Metadata handling** — title, author, language, publisher, and identifiers
  are carried into the EPUB.
* **Image extraction and placement** — embedded document images are extracted
  and placed in the EPUB at their reconstructed positions.
* **Header/footer and page-number removal** — repeated furniture is kept out
  of the body text.

### OCR (scanned and mixed PDFs)

* **Scanned-page detection and page rendering** — pages that need OCR are
  detected during analysis and rendered to images.
* **Tesseract OCR** — an optional, external dependency (the Tesseract engine
  is installed separately, never bundled).
* **Conservative OCR cleanup** — a deterministic post-OCR layer that
  normalizes line endings, whitespace, and control characters. It performs
  *no* recognition correction.
* **Mixed text/image routing** — per-page routing so text pages use native
  extraction, image pages use OCR, and the two text streams stay separate.
* **OCR-aware structural reconstruction** — OCR output is reconstructed into
  the same document model as native text.

### Output

* **Reflowable, Kindle-oriented EPUB** — one EPUB rendering path for every
  PDF type: semantic markup, a single deterministic Kindle-friendly
  stylesheet, EPUB 3 navigation plus NCX.
* **Structural EPUB validation** — the finished EPUB is checked independently
  of conversion, with structured, machine-readable issue reports.
* **EPUB → AZW3** — optional conversion through Calibre's `ebook-convert`
  (an external, optional dependency).
* **Cover support** — an explicit cover image (JPEG, PNG, GIF, or SVG), plus
  conservative automatic detection of a confident cover page when no explicit
  cover is given. An explicit cover always wins.

### Desktop application

* **PDF selection and analysis** — pick a PDF with the file picker and see a
  summary of its analysis (page count, document type, OCR requirement).
* **Conversion options** — output format (EPUB, or EPUB + AZW3), output
  directory, and an optional cover.
* **Background conversion with progress** — conversion runs on a worker
  thread, reporting stage-level progress while the window stays responsive.
* **Results and validation** — output paths and the EPUB validation report,
  plus **Open EPUB / Open AZW3 / Open Folder** actions.
* **Error handling and temporary workspace** — expected failures surface as
  readable messages with a safe retry, and each conversion gets a clean
  temporary workspace.

## How It Works

PDF and EPUB represent documents in fundamentally different ways: a PDF
describes where content sits on a page, while an EPUB reflows text to fit the
reader's screen. The converter therefore **does not** treat conversion as a
simple `PDF → EPUB` byte mapping. It reconstructs the book first:

```text
PDF
  │
  ▼  analyze_pdf → TEXT | SCANNED | MIXED
  ▼  per-page extraction (native text and/or OCR)
  ▼  reconstruction
Book  (format-independent document model)
  │
  ▼  build_epub
EPUB
  │
  ├─ validate_epub        → EPUBValidationResult
  └─ convert_epub_to_azw3 → AZW3 (Calibre, optional)
```

There is exactly **one** EPUB rendering path, and it consumes only the
format-independent `Book` model. The PDF layer never produces EPUB HTML, and
the EPUB layer never inspects PDFs, runs OCR, or reconstructs structure.

## Supported Input and Output

| Input | What it is | How it is converted | Output |
| --- | --- | --- | --- |
| **TEXT PDF** | Meaningful selectable text on essentially all pages | Native layout-aware extraction and reconstruction; no OCR | EPUB (+ optional AZW3) |
| **SCANNED PDF** | Little or no selectable text; pages are primarily page images | Pages are rendered, OCR'd (requires Tesseract), and cleaned before reconstruction | EPUB (+ optional AZW3) |
| **MIXED PDF** | A substantial mixture of text pages and image-based pages | Text pages use native extraction; image pages are OCR'd; the two streams stay separate | EPUB (+ optional AZW3) |

An **EPUB** is always generated first; **AZW3** is an additional artifact
converted from that EPUB through Calibre.

Conversion is best-effort: the converter handles a variety of real-world
layouts, but unusual typography, dense designs, and poor-quality scans may
reconstruct imperfectly. See [Limitations](#limitations).

## Requirements

### Platform

* The supported user interface and the released artifact are **Windows**.
  The core library is platform-neutral Python and runs elsewhere from source,
  but the project is developed, tested, and packaged on Windows.
* **Python 3.12 or newer** is required for source installs (`requires-python
  = ">=3.12"`). Development and packaging currently use Python 3.14.

### Python packages

| Package | Installed | Purpose |
| --- | --- | --- |
| `PyMuPDF >=1.24,<2` | core | PDF inspection, extraction, rendering |
| `ebooklib >=0.19,<1` | core | EPUB generation |
| `PySide6 >=6.8,<7` | `ui` extra | the desktop application |
| `pytesseract` + `Pillow` | `ocr` extra | Python wrappers for Tesseract OCR |
| `pytest >=8.0,<9` | `dev` extra | test runner |

Install with `pip install -e ".[dev,ui,ocr]"` or add only the extras you need
(see [Development Setup](#development-setup)).

### External tools (installed separately, never bundled)

| Tool | Required for | How to get it |
| --- | --- | --- |
| **Tesseract OCR** | SCANNED and MIXED PDFs | Install the Tesseract OCR engine and make sure `tesseract` (with `eng` language data) is discoverable on `PATH`. See [OCR](#ocr). |
| **Calibre** | AZW3 output | Install Calibre so its `ebook-convert` tool is on `PATH` (Windows default: `C:\Program Files\Calibre2`). See [AZW3 (optional)](#azw3-optional). |

The packaged Windows application bundles the Python runtime libraries above,
but Tesseract and Calibre always remain external, exactly as they are in a
source installation.

## Installation

### Windows executable

Packaged Windows builds are distributed for this repository.
The release bundle is available as a **ZIP archive** (e.g.
`KindleBookConverter-Windows-x64-0.1.0.zip`) together with its SHA-256
checksum from the repository's [**Releases** page]
(https://github.com/rohitsingh31052003-source/Kindle_Book_convertor/releases)
— see [Windows packaging](docs/windows-packaging.md) for the build
and verification recipe. Extracting it reproduces the application folder:

```text
KindleBookConverter/
├── KindleBookConverter.exe   ← run this
└── _internal/                ← runtime files (keep next to the .exe)
```

Keep the executable and its `_internal` folder together — the application
needs its runtime files and does not work from the executable alone.

### From source (Python)

```bash
git clone https://github.com/rohitsingh31052003-source/Kindle_Book_convertor
cd Kindle Book Convertor
python -m venv .venv

# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

pip install -e ".[dev,ui,ocr]"
```

That installs the core engine plus the desktop UI, OCR support, and test
dependencies. For just the core conversion engine, `pip install -e .` is
enough.

## Running the Application

* **Packaged Windows app** — run `KindleBookConverter.exe`.
* **From source** — with the `ui` extra installed:

  ```bash
  python -m kindle_converter.ui
  ```

  This opens a window titled **Kindle Book Converter**.

## Using the Converter

The desktop workflow is fixed to a small, safe sequence — a PDF must be
analyzed before conversion options are enabled, and conversion always runs in
the background:

```text
Select PDF
    ↓
Analyze PDF
    ↓
Configure conversion (format, output directory, optional cover)
    ↓
Convert
    ↓
Validate (EPUB)
    ↓
Open generated output
```

1. **Select a PDF.** Click **Browse** next to *Input PDF* and choose a `.pdf`
   file.
2. **Analyze the PDF.** Click **Analyze PDF**. The window shows the page
   count, the document type (TEXT / SCANNED / MIXED), the text/scanned/mixed
   page counts, and whether OCR is required.
3. **Configure the conversion.** Choose the **output format** (EPUB, or AZW3
   — which also produces the EPUB), the **output directory**, and an optional
   **cover image** (JPEG, PNG, GIF, or SVG). The window becomes *ready for
   conversion* once a valid combination is in place.
4. **Convert.** Click **Convert**. The conversion runs on a background thread
   with an indeterminate progress bar; the window stays responsive.
5. **Read the results.** The results section shows the output paths, the
   EPUB validation summary (valid/invalid, warnings/errors, specific issues),
   and the **Open EPUB / Open AZW3 / Open Folder** actions.

Output files are named after the input PDF (for example `book.pdf` produces
`book.epub` and, when requested, `book.azw3`) in the directory you chose.

For the scanned/mixed OCR path, the pipeline is:

```text
Scanned (or mixed) PDF
    ↓
Page rendering
    ↓
OCR (Tesseract)
    ↓
OCR cleanup
    ↓
Structural reconstruction
    ↓
Output generation
```

## OCR

OCR is used **only** when a page is classified as needing it — SCANNED and
MIXED documents. Converting a text-only PDF never touches OCR.

**Dependency.** OCR needs two parts:

1. The `ocr` Python extra (`pytesseract` + `Pillow`), which is bundled in the
   Windows package.
2. The **external Tesseract executable** (`tesseract.exe`), which this project
   does **not** bundle or install. Install Tesseract separately (for example
   from the official Tesseract OCR builds for Windows) and make sure its
   `tesseract` command is findable on your `PATH`, with the English language
   data (`eng`) present:

   ```bash
   tesseract --version
   tesseract --list-langs   # 'eng' must be listed
   ```

**Configuration.** The conversion engine looks for `tesseract` on `PATH`, or
accepts an explicit `tesseract_cmd`. The optional integration tests
additionally honour the `TESSERACT_CMD` environment variable.

**When OCR is unavailable.** If a SCANNED or MIXED document needs OCR but the
`tesseract` executable cannot be found, the conversion fails with a readable
message telling you Tesseract was not found — it never silently produces an
empty book and never crashes. Text-only PDFs convert normally even when
Tesseract is absent.

**Limitations.** OCR output is consumed *as recognized* by a conservative
cleanup layer; there is **no recognition correction** (no spelling,
dictionary, grammar, or character-substitution correction). OCR quality
therefore depends on the quality of the source scan and on the installed
Tesseract version and language data.

## AZW3 (optional)

**Dependency.** AZW3 conversion is performed by **Calibre's `ebook-convert`**
command. Calibre is an external system dependency installed by Calibre's own
installer — this project never bundles or installs it and never implements
AZW3 itself.

**How to use it.** Install Calibre (it places `ebook-convert` on `PATH`,
typically under `C:\Program Files\Calibre2` on Windows), then select **AZW3**
as the output format. The EPUB is always produced first; the AZW3 is generated
from that EPUB. Explicit `calibre_path` / `CALIBRE_CONVERT` settings are
honoured by the conversion layer and the optional integration tests.

**When Calibre is unavailable.** If `ebook-convert` cannot be found or fails,
the EPUB conversion is still attempted/generated where possible and the AZW3
step reports a dedicated failure. "Success" for AZW3 means Calibre exited
successfully and produced a non-empty file — it is **not** a guarantee of
Kindle marketplace acceptance or exact device rendering.

**Limitations.** AZW3 output is not byte-for-byte deterministic (Calibre
stamps its own metadata), and there is no AZW3 parser or Kindle-compatibility
validator.

## Development Setup

For contributors and developers:

1. **Clone the repository** (see [From source](#from-source-python)).
2. **Install the package** with the development extras:

   ```bash
   python -m pip install -e ".[dev,ui,ocr]"
   ```

   `dev` provides pytest; `ui` provides PySide6 for the desktop application
   and its tests; `ocr` provides the Tesseract Python wrappers and the OCR
   tests.

3. **Run the test suite:** (see [Testing](#testing)).

`pytest` is configured via `pythonpath = ["src"]` in `pyproject.toml`, so tests
run against the `src/`-layout package even without installing it.

### Library API

The public Python API is small and stable:

```python
from kindle_converter import convert_pdf_to_book, convert_pdf_to_epub
from kindle_converter.pdf import analyze_pdf, TesseractEngine
from kindle_converter.epub import build_epub, validate_epub, convert_epub_to_azw3

convert_pdf_to_epub("book.pdf", "book.epub")                       # text PDF
convert_pdf_to_epub("scan.pdf", "scan.epub", engine=TesseractEngine())  # OCR
convert_epub_to_azw3("book.epub", "book.azw3")                     # needs Calibre
```

For the desktop application, the [user guide](docs/user-guide.md) documents
the full workflow, and the [application layer](docs/development.md) documents
the UI-independent `ConversionApplication` API that the UI talks to.

## Testing

The test suite is the project's quality gate (there is no separate linter or
type-checker configured). Run the full suite:

```bash
python -m pytest
```

The suites are organized by marker so you can run a single concern:

```bash
python -m pytest -m regression     # M6.2 corpus-driven regression harness
python -m pytest -m quality        # M6.3 conversion-quality measurement
python -m pytest -m performance    # M6.4 performance framework
python -m pytest -m packaging      # M6.5 Windows package configuration
python -m pytest -m docs           # M6.6 documentation/release-readiness checks
```

The authoritative commands are also printed by the release tool:

```bash
python build_tools/release_check.py --print-commands
```

**Conventions.** The regression, quality, performance, packaging, and docs
suites are deterministic and offline — they need no Tesseract, no Calibre, no
network, and no GUI. Optional integration suites run against the real external
tools and **skip cleanly** when those tools are unavailable:

* PySide6 UI tests skip when the `ui` extra is absent (they run headless via
  `QT_QPA_PLATFORM=offscreen`);
* the real-Tesseract suite (`tests/test_pdf_ocr_tesseract.py`) and the
  real-Calibre suite (`tests/test_epub_azw3_calibre.py`) skip when the
  executables are not present (honouring `TESSERACT_CMD` and
  `CALIBRE_CONVERT`).

## Architecture

The project is a layered pipeline with a thin desktop UI on top:

```text
Desktop UI (kindle_converter.ui, PySide6)
    ↓   talks only to the application layer
ConversionApplication (kindle_converter.application)
    ↓
PDF analysis / extraction / OCR / reconstruction (kindle_converter.pdf)
    ↓
Book model (kindle_converter.document)
    ↓
EPUB generation / validation / AZW3 (kindle_converter.epub)
```

The desktop UI is a thin presentation layer: it never parses the PDF, never
calls the conversion pipeline directly, and never runs conversion on the GUI
thread. Input analysis, request validation, and conversion all go through the
M5.1 **application boundary** (`ConversionApplication.analyze_pdf`,
`validate_request`, `convert`), and conversions run on a dedicated worker
thread. Opening output files goes through an injectable platform seam.

The project structure, module boundaries, and the packaging tooling are
described in the [development guide](docs/development.md).

## Documentation

* [User guide](docs/user-guide.md) — what the application does, supported
  PDFs, using the Windows app, OCR / AZW3 dependencies, troubleshooting.
* [Development guide](docs/development.md) — environment setup, project
  structure, architecture, running the tests.
* [Windows packaging](docs/windows-packaging.md) — building and verifying the
  Windows release artifact.
* [Release checklist](docs/release-checklist.md) — the release gates and the
  release process.
* [CHANGELOG](CHANGELOG.md) — release notes.
* [Contributing](CONTRIBUTING.md) — how to report bugs, propose features, and
  open pull requests.
* [Security policy](SECURITY.md) — how to report a security vulnerability.

## Limitations

These are the documented, *implemented* limitations of the current converter:

* **EPUB chapter splitting is absent.** The PDF layer detects and indexes
  chapters, but the EPUB currently ships a single chapter file with one
  navigation entry.
* **Document titles are not detected as headings.** The heading detector is
  deliberately conservative: explicit `Chapter N:` headings are surfaced, but
  a book's opening title line is treated as body text.
* **Reading order on complex layouts is best-effort.** Multi-column behavior
  is exercised for the two-column case; unusual typography and dense layouts
  can still reconstruct imperfectly.
* **OCR quality depends on the source scan**, and OCR performs *no*
  recognition correction (no spelling, dictionary, grammar, or
  character-substitution correction).
* **AZW3 is a best-effort external conversion.** "Success" means Calibre
  exited 0 and produced a non-empty file — not a guarantee of Kindle
  marketplace acceptance — and AZW3 output is not byte-for-byte deterministic.
* **No fixed-layout or per-device output.** The EPUB is deliberately
  reflowable and generic: no KFX, no fixed-layout EPUB, no Kindle Previewer
  automation, no per-model CSS.
* **No drag-and-drop input and no conversion cancellation.** Input is
  selected through the file picker; a running conversion finishes rather than
  being stopped.
* **No clean-machine verification on a separate machine has been performed.**
  The strongest performed check is the isolated local run of the frozen
  artifact; a true clean-VM check is a documented optional release gate.
* **No telemetry, auto-updates, or cloud services.** The application is
  local-first by design.

## Roadmap

**Milestones 1–8 are complete** (see the checklists below): the conversion
engine, OCR, Kindle-oriented output, the desktop application, quality,
performance, packaging, release engineering, and the first **public GitHub
release** (`v0.1.0`, published with the Windows distribution ZIP and its
SHA-256 checksum attached).

The previously discussed hosted/web version is **deferred**: this project is
a local desktop application, and no web hosting, server-side conversion, or
authentication is planned as current work.

### Milestone 1.0 — Project Foundation

* [x] Initialize Python project and package structure
* [x] Add automated tests
* [x] Create the document domain model
* [x] Implement PDF type analysis
* [x] Implement basic text extraction
* [x] Implement initial EPUB generation
* [x] Create an end-to-end PDF → EPUB pipeline

### Milestone 2 — Book Reconstruction

* [x] Layout analysis and reading-order reconstruction
* [x] Paragraph reconstruction (M2.3)
* [x] Heading detection (M2.4; layout-based, conservative, deterministic)
* [x] Header/footer removal (M2.5)
* [x] Chapter detection (M2.9)
* [x] Page-number removal (M2.10)
* [x] Table-of-contents generation (M2.11)
* [x] Metadata handling (M2.12)
* [x] Image extraction and placement (M2.13)

### Milestone 3 — Scanned PDFs and OCR

* [x] Detect scanned PDFs
* [x] Render PDF pages
* [x] OCR processing (Tesseract)
* [x] OCR cleanup (M3.4; conservative and deterministic, no recognition correction)
* [x] Mixed text/image document handling (M3.5; deterministic per-page OCR routing)
* [x] OCR-aware structural reconstruction (M3.6)

### Milestone 4 — Kindle Output

* [x] Kindle-friendly EPUB generation (M4.1; one reflowable EPUB path from the unified `Book`)
* [x] EPUB validation (M4.2; structural validation of generated EPUB artifacts)
* [x] AZW3 conversion (M4.3; explicit EPUB → AZW3 step through Calibre)
* [x] Cover handling (M4.4; explicit, optional cover with correct EPUB semantics)
* [x] Kindle-specific formatting improvements (M4.5; one internal Kindle formatting profile)

### Milestone 5 — User Interface

* [x] Application / pipeline API (M5.1; UI-independent `ConversionApplication`)
* [x] PySide6 desktop application (M5.2; optional `ui` extra)
* [x] Input selection + PDF analysis UI (M5.3)
* [x] Output options + cover UI with explicit readiness (M5.4)
* [x] Background conversion with progress (M5.5)
* [x] Results and validation presentation + output actions (M5.6)
* [x] Error handling + temporary workspace (M5.7)
* [x] UI / integration test coverage (M5.8)
* [x] Documentation and M5 closure (M5.9)
* [ ] Drag-and-drop input (future work — input is selected through the file picker)

### Milestone 6 — Quality and Distribution

* [x] Build a representative test corpus (M6.1; deterministic synthetic corpus)
* [x] Add a regression test harness (M6.2; corpus-driven, baseline-pinned)
* [x] Add conversion-quality measurement (M6.3)
* [x] Add performance measurement (M6.4)
* [x] Package for Windows (M6.5; reproducible PyInstaller bundle + isolated verification)
* [x] Documentation (M6.6; user, developer, packaging, and release documentation)
* [x] Release preparation (M6.6; single-source versioning, changelog, release checklist, release validator)

### Milestone 7 — Robustness on Real-World PDFs

* [x] First-page investigation and root-cause fix (M7.1; scanned-first-page text duplication and linear EPUB navigation fixed, with regression coverage)
* [x] Automatic cover selection (M7.2; deterministic, conservative, offline cover-page detection)
* [x] AZW3 process hardening (M7.3; hidden Calibre console on Windows)

### Milestone 8 — Public GitHub Release

* [x] Repository + README public readiness (M8.1; this README and the repository prepared for first-time visitors)
* [x] Windows distribution artifact + workflow (M8.2; a verified, reproducible Windows ZIP distribution with its build/verification/documentation flow)
* [x] User documentation + user guide (M8.3; complete, accurate, release-ready end-user documentation)
* [x] Repository quality (M8.4; contribution/security documentation, issue and pull-request templates, generated-artifact hygiene)
* [x] GitHub Release publication (M8.5; `v0.1.0` published to this public GitHub repository with the Windows distribution ZIP and its SHA-256 checksum attached as release assets)
* [x] Post-release readiness verification (M8.6; verification-only milestone â release immutability, integration/quality/performance/package/docs gates, and repository hygiene re-verified against the published `v0.1.0`; no new commits)
* [ ] Future improvements: conversion-quality refinements, additional PDF compatibility, and user-requested improvements
* [ ] Future hosted/web version (deferred; not current work — scope is undecided)

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for the
full guide. Before contributing, please:

* **Report bugs and request features through the issue tracker** of this
  repository, using the [bug report](.github/ISSUE_TEMPLATE/bug_report.yml) and
  [feature request](.github/ISSUE_TEMPLATE/feature_request.yml) templates.
  Include the PDF classification (TEXT / SCANNED / MIXED) when relevant, the
  application version, and whether Tesseract or Calibre was installed. Never
  attach private PDFs or personal documents to an issue.
* **Run the test suite** before submitting changes
  (`python -m pytest`, plus the marker suites in [Testing](#testing)).
* **Preserve existing behavior.** Conversion-algorithm changes are treated as
  behavior changes: they must pass the regression, quality, and performance
  suites, and the baselines under `tests/fixtures/` are never overwritten
  silently (regeneration is explicit and deliberate).
* **Prefer documenting defects** over silently changing conversion behavior;
  the release process treats conversion changes as behavior changes that must
  be justified.

See the [development guide](docs/development.md) for the environment setup,
project structure, and conventions.

## License

This project is **proprietary** — all rights reserved. The package metadata
declares `license = { text = "Proprietary" }` in `pyproject.toml`; there is no
separate `LICENSE` file in the repository at this time.