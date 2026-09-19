# PDF to Kindle Converter

A Python-based document conversion tool for transforming PDFs into high-quality, reflowable ebook formats such as **EPUB** and **AZW3**, with a focus on books intended for Kindle.

The project is being developed as a local-first conversion engine that can handle different kinds of PDFs, including text-based documents, scanned books, and PDFs with more complex layouts.

## Project Status

**Development stage:** complete. **Milestone 6 (Quality and Distribution)
is finished**: the M6.1 representative corpus, M6.2 regression harness, M6.3
conversion-quality measurement, M6.4 performance measurement, M6.5 Windows
packaging, and M6.6 documentation + release engineering milestones are all
implemented. **Milestone 7.1 (First-Page Investigation and Root-Cause Fix) is
also complete**: the two mechanisms by which a generated EPUB could open with
an "unexpected first page" that the source PDF does not contain — the native
text of a *scanned* page being re-emitted alongside its OCR text, and the EPUB
navigation document being a *linear* spine entry — were reproduced, root-caused,
and fixed at the architectural level (see the Milestone 7 checklist below).
**Milestone 7.2 (Automatic Cover Selection) is also complete**: when no explicit
cover is supplied, a bounded window of early pages is examined and a
confidently detected cover page is used as the EPUB cover — deterministically,
offline, and conservatively, with weak, ambiguous, or absent evidence
producing the pre-M7.2 coverless EPUB. **Milestone 7.3 (AZW3 process
hardening) is also complete**: the Calibre subprocess is launched with a
hidden Windows console so the app does not create a visible console while
preserving the existing conversion behavior, output validation, and error
handling. The repository is at an initial **0.1.0** release state; see
the [CHANGELOG](CHANGELOG.md), the [release checklist](docs/release-checklist.md),
and the "Known limitations" section below.

The repository is at the end of **Milestone 5**. Every M5 sub-milestone, M5.1
through M5.9, is implemented and complete:

* **M5.1** — Application / pipeline API (`kindle_converter.application`):
  the stable, UI-independent conversion use case (`ConversionApplication.convert`,
  `analyze_pdf`, `validate_request`) with typed `ConversionRequest`,
  `ConversionResult`, progress events, and an application-level error
  boundary.
* **M5.2** — PySide6 application foundation: the optional `ui` extra
  (`kindle_converter.ui`) with a launchable main window and the
  `python -m kindle_converter.ui` entry point.
* **M5.3** — Input + PDF analysis: select a PDF, analyze it through the
  application API, and view the M3.1 analysis summary.
* **M5.4** — Conversion options + cover: output format (EPUB/AZW3), output
  directory, optional cover, and construction of the real `ConversionRequest`
  with an explicit readiness model. No conversion execution yet.
* **M5.5** — Background conversion + progress: convert through
  `ConversionApplication.convert` on a dedicated `QThread` worker, reporting
  stage progress over Qt signals while the GUI stays responsive.
* **M5.6** — Results + validation: results rendered exclusively from the real
  `ConversionResult`, including the existing `EPUBValidationResult`, plus
  Open EPUB / Open AZW3 / Open Folder output actions.
* **M5.7** — Errors + temporary files: an application-owned temporary
  workspace per conversion with deterministic cleanup, and application-level
  error handling (`ApplicationError` subclasses including `WorkspaceError`).
* **M5.8** — UI / integration testing: headless PySide6 integration tests
  covering the complete desktop workflow, following the repository's
  PySide6-optional skip convention.
* **M5.9** — Documentation + M5 closure: this README reconciled with the
  implemented M5 state (desktop workflow, PDF classifications, conversion
  options, results/validation, background conversion, temporary workspace,
  error handling, installation, testing, license, and the M5 acceptance
  checklist below).

The desktop application is an optional layer on top of a complete engine
(Milestones 1–4): deterministic PDF analysis, layout-aware reconstruction
(reading order, paragraphs, headings, chapters, page-number and header/footer
removal, metadata, images), OCR-aware processing for scanned and mixed PDFs,
Kindle-oriented EPUB generation, structural EPUB validation, EPUB → AZW3
conversion (via Calibre's `ebook-convert`), explicit optional cover handling,
and Kindle-specific reflowable formatting improvements — all covered by a
deterministic test suite.

The core library never imports PySide6, and the UI talks to the core only
through the M5.1 application API. **Milestone 6** (quality and distribution)
is complete: the M6.1 representative test corpus, the M6.2 deterministic
regression harness, the M6.3 conversion-quality measurement suite, the M6.4
performance measurement framework, the M6.5 Windows packaging + clean-machine
verification, and the M6.6 documentation + release engineering work (this
README, the user/developer/release documentation, a changelog, a release
checklist, release validation, and single-source versioning) are all done (see
the sections below).

## Documentation

* [User guide](docs/user-guide.md) — what the application does, supported
  PDFs, using the Windows app, OCR / AZW3 dependencies, troubleshooting.
* [Development guide](docs/development.md) — environment setup, project
  structure, architecture, running the tests.
* [Windows packaging](docs/windows-packaging.md) — build and verify the
  Windows artifact (M6.5) and the verification levels.
* [Release checklist](docs/release-checklist.md) — the required and
  optional/environment-dependent release gates.
* [CHANGELOG](CHANGELOG.md) — release notes for **0.1.0**.

## Supported platforms

The library core is platform-neutral Python. The production **graphical
workflow and the release artifact are Windows**: the PySide6 desktop
application is the supported user interface, and the release artifact is the
packaged Windows bundle built by M6.5. All suites (unit/integration, M6.2
regression, M6.3 quality, M6.4 performance, M6.5 packaging, M6.6 docs) run on
Windows with Python 3.14; the converter tested against Python 3.12+.

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
    ├── application/           # M5.1: UI-independent conversion use case
    │   ├── converter.py   # ConversionApplication: analyze_pdf (M5.3) / validate_request (M5.4) / convert
    │   ├── request.py     # ConversionRequest / OutputFormat
    │   ├── result.py      # ConversionResult
    │   ├── progress.py    # ConversionStage / ConversionProgress / callback
    │   ├── errors.py      # ApplicationError boundary (subclasses PipelineError)
    │   └── workspace.py   # M5.7: application-owned temporary workspace per conversion
    │
    ├── ui/                # M5.2-M5.6: PySide6 desktop UI (ui extra)
    │   ├── app.py         #   application entry point (create_application / main)
    │   ├── main_window.py #   input selection + analysis + options + results + output actions
    │   ├── platform.py    #   M5.6: injectable platform-open seam (open_path)
    │   ├── worker.py      #   M5.5: QtCore-only ConversionWorker (off-GUI-thread convert)
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

The repository contains dedicated PySide6 UI tests, including the M5.8
integration suite (`tests/test_ui_integration.py`) that drives the complete
desktop workflow (input selection → analysis → conversion options → background
conversion → results/validation → output actions) across the MainWindow →
ConversionWorker → ConversionApplication → pipeline boundary. UI tests run
headless (`QT_QPA_PLATFORM=offscreen`) and, following the repository's
long-standing convention, **skip cleanly when PySide6 (the `ui` extra) is not
installed** — the core suite never requires it. The optional Calibre
(`tests/test_epub_azw3_calibre.py`) and Tesseract (`tests/test_pdf_ocr_tesseract.py`)
integration tests likewise skip when those external tools are unavailable.

### Regression test harness (M6.2)

`tests/regression` is a deterministic, corpus-driven regression suite over the
M6.1 fixture corpus (`tests/fixtures/corpus`). It runs every representative
fixture through the **real** public pipeline
(`convert_pdf_to_book` / `convert_pdf_to_epub`, plus the M5
`ConversionApplication`) and asserts observable, semantic properties — never
byte-for-byte EPUB snapshots, ZIP ordering, or HTML formatting:

* classification and conversion routing (native vs OCR vs mixed) via an
  injected, deterministic OCR engine (`tests.regression.harness.CountingOCR`);
* paragraph reconstruction, reading order, heading hierarchy, and chapter
  detection;
* repeated header/footer furniture staying out of the body;
* image extraction and placement in the resulting EPUB;
* EPUB generation, `validate_epub` results, chapter/image/heading structure.

Run it on its own or as part of the full suite:

```bash
pytest -m regression   # only the corpus regression suite
pytest                 # full suite, regression included
```

Expectations live in two places, each with a distinct role:

* **`tests/fixtures/corpus/manifest.json`** — corpus metadata only:
  classification, page counts, and per-fixture features (single source of
  truth, shared with M6.3/M6.4).
* **`tests/fixtures/regression_baseline.json`** — regression-specific,
  structural expectations (block/heading/paragraph counts, chapter counts,
  image and EPUB artifact structure). It is kept small, human-readable, and
  regenerable with:

  ```bash
  python -m tests.regression.regenerate_baseline --write
  ```

  (the script diffs against the committed baseline first; run without
  `--write` to preview). Review any baseline diff like a behavior change.

The suite is fully deterministic and needs **no** Tesseract, Calibre, network,
or GUI: scanned/mixed fixtures exercise the real OCR routing with the injected
engine, and the pre-existing optional Tesseract/Calibre integration tests
continue to skip when those tools are absent.

### Conversion quality measurement (M6.3)

`tests/quality` measures *conversion quality* over the same M6.1 corpus and is
deliberately separate from the M6.2 regression suite:

* the **regression baseline** (M6.2) pins exact, deterministic structural
  behavior (block counts, chapter counts, OCR call counts) so unintended
  converter changes fail loudly;
* the **quality baseline** (M6.3) declares authored, human-written *quality*
  expectations -- numeric floors and ceilings, content anchors, exclusion
  rules, booleans, and ordered reading-order sequences -- and reports how the
  current converter measures against them without a single hiding "quality
  score".

Both share the deterministic, offline discipline: no Tesseract, no Calibre, no
network, no clocks, no absolute paths in observations or reports.
Classification is the single exception to "authored": it is read from the
corpus manifest at evaluation time, not duplicated in the quality baseline.

**Dimensions.** Each fixture is measured across the applicable dimensions:

* `classification` -- manifest-driven PDF type check (never stored in the
  baseline);
* `structure` -- paragraphs, detected headings, PDF-layer chapters and TOC
  entries (`detect_chapters` / `generate_toc`), page breaks, empty /
  near-empty paragraph artifacts, body-text content anchors;
* `reading_order` -- the left/right `L1..R4` column-token sequence of the
  two-column fixture, asserted as an ordered sequence;
* `ocr` -- how many pages were routed to OCR (via the injected
  `CountingOCR`), whether OCR marker text entered the body, and that no
  real OCR-engine strings leak through;
* `images` -- image blocks in the `Book` and deduplicated image resources in
  the EPUB;
* `epub` -- `validate_epub` validity, chapter files, heading tags, nav
  entries, empty chapters, and EPUB body-content anchors.

Expectation types: `exact`, `minimum`, `maximum`, `contains`, `excludes`,
`boolean`, `ordered_sequence`.

**Run it** (marked `quality`, run as part of the full suite or alone):

```bash
pytest -m quality   # only the conversion-quality measurement suite
pytest              # full suite, both suites included
```

**Baseline and regeneration.** Authored expectations live in
`tests/fixtures/quality_baseline.json` (schema version 1). `exact` pins follow
the current measurement when the baseline is regenerated; every other
expectation type is preserved verbatim and is never fabricated from a
measurement. Regeneration is explicit and protects against silent weakening:

```bash
PYTHONPATH=src python -m tests.quality.regenerate_baseline        # preview only
PYTHONPATH=src python -m tests.quality.regenerate_baseline --write # persist after review
```

The tool diffs against the committed baseline first, reports every *authored*
expectation that now fails the fresh measurement as a **finding** (not a
baseline edit -- it refuses to `--write` over failing authored
expectations), and updates only the `exact` pins. Review any diff like a
behavior change, exactly as with M6.2.

**Reports.** `tests/quality/report.py` renders the measured quality two ways:
a JSON machine report and a human-readable report. Both expose per-fixture,
per-metric `expected` / `observed` / `status` / `details` so individual
measurements are never hidden behind one aggregate number, and no timestamps
or machine paths leak in.

**Known converter gaps surfaced by M6.3.** The measurement underlines two
honest limitations (recorded as per-fixture `notes`, not hidden):

* **EPUB chapter splitting is absent.** Even when the PDF layer detects and
  indexes chapters (`novel_basic` 3, `chapters_long` 8, `twocolumn_article`
  3 TOC entries), the EPUB always ships a single chapter file with one nav
  entry. The quality baseline records `nav_entries >= 1` and flags the
  divergence in the `chapters_long` review note as follow-up work.
* **Document titles are not detected as headings.** The M2.4 heading detector
  is deliberately conservative: titles such as "Minimum Viable Document" and
  "On the Behavior of Light" are treated as body text while explicit
  `Chapter N:` headings are surfaced. Expectations document this behavior
  rather than weakening the detection thresholds.

### Performance measurement (M6.4)

`tests/performance` measures the real PDF -> Book -> EPUB pipeline over all
11 M6.1 fixtures. It uses one warm-up and three measured runs by default,
`time.perf_counter`, median/minimum/maximum timing statistics, and a fresh
temporary output state for every run. Scanned and mixed fixtures use the
deterministic `tests.regression.harness.CountingOCR` double, so the default
benchmark never requires Tesseract; its timings are OCR-routing timings, not
real Tesseract timings.

The benchmark records end-to-end elapsed time, observable phase timings
(`pdf_analysis`, `book_conversion`, `epub_generation`, and
`epub_validation`), fixture metadata, and peak Python allocations from
`tracemalloc`. The memory value is not process RSS or total system memory.
No aggregate performance score is calculated.

Run a measurement preview and explicitly regenerate the separate baseline:

```bash
PYTHONPATH=src python -m tests.performance.regenerate_baseline
PYTHONPATH=src python -m tests.performance.regenerate_baseline --write
```

The baseline is `tests/fixtures/performance_baseline.json`; ordinary tests
never overwrite it. Baseline comparisons retain baseline, observed, delta,
threshold, and status for elapsed and phase medians. The default relative
tolerance is 25% to avoid treating normal local timing noise as a regression;
it can be changed with `--tolerance`. Environment metadata (Python, platform,
PyMuPDF, and ebooklib versions) is recorded for interpretation. Optional
Tesseract is not needed, and no targeted production optimization was justified
by this measurement because M6.4 does not make speculative changes.

### Windows packaging (M6.5)

M6.5 produces a reproducible Windows artifact and verifies it on a "clean
machine" (a process that never imports the project's Python packages). The
results are a single-file, console-free (GUI-subsystem) PyInstaller **onedir**
bundle in `dist/KindleBookConverter/` plus the build/verification tooling in
`build_tools/`. See `docs/windows-packaging.md` for the recipe; the
implementation report is `work report/M6.5_IMPLEMENTATION_REPORT.md`.

Build from the repo root (Python 3.14; creates a project-dedicated venv and
never touches the developer environment):

```bash
C:\Python314\python.exe build_tools/build_windows.py
```

The bundle embeds the real runtime distributions (`PySide6`, `PyMuPDF`,
`ebooklib`/`lxml`, `Pillow`, `pytesseract`, and `kindle-converter` metadata),
a Windows version resource carrying the `0.1.0` application version, and the
exact PyInstaller runtime files. Tesseract and Calibre remain external, as
they are in a source checkout.

Verification runs the *actual executable* in an isolated copy of the bundle
from a scratch directory, never the interpreter that built it:

```bash
C:\Python314\python.exe build_tools/verify_windows_package.py
```

The verifier checks the artifact shape, GUI subsystem, version resource, the
12 required runtime components, the absence of development artifacts and dev
paths, and then drives the windowed executable headlessly (`--sysinfo`,
`--smoke-check`, and real conversions over the M6.1 fixtures
`novel_basic.pdf`, `scanned_book.pdf`, and `mixed_text_image.pdf`, plus
`--azw3` through the installed Calibre). Scanned/mixed documents without a
`tesseract` executable are expected to fail *gracefully* (exit 0, reported
`expected`) -- exactly the designed OCR-unavailable behavior of M6.1 -- while
the text document must convert and validate its EPUB (0 warnings / 0 errors).

Pieces that make this reproducible:

* `build_tools/build_windows.py` -- clean venv, `pip install`, the version
  info file from `pyproject.toml`, PyInstaller, and provenance stripping
  (`direct_url.json` / `INSTALLER` removed so no dev absolute path leaks);
* `build_tools/KindleBookConverter.spec` -- the full freeze graph
  (windowed entry `windows_entry.py`, `copy_metadata` for every shipped
  distribution so frozen `importlib.metadata` versions resolve);
* `kindle_converter/ui/smoke.py` -- the headless subcommand harness that
  ships inside the artifact;
* `tests/test_windows_packaging.py` -- 24 tests marked `packaging` covering
  the pure layout/parsing/dispatch logic offline without PyInstaller.

### Release preparation (M6.6)

M6.6 turns the repository into a release-ready project: user documentation
([docs/user-guide.md](docs/user-guide.md)), developer documentation
([docs/development.md](docs/development.md)), packaging/release documentation
([docs/windows-packaging.md](docs/windows-packaging.md)), a release checklist
([docs/release-checklist.md](docs/release-checklist.md)), a changelog
([CHANGELOG.md](CHANGELOG.md)), single-source version validation, and a
lightweight release validator (`build_tools/release_check.py`) that reuses the
M6.5 package verifier rather than duplicating it:

```bash
venv\Scripts\python.exe build_tools/release_check.py          # repository gates
venv\Scripts\python.exe build_tools/release_check.py --package  # + M6.5 package verifier
```

The **single authoritative version source remains `pyproject.toml`**
(`[project] version`): the package metadata, the runtime
`kindle_converter.__version__`, the Windows executable version resource, and
the `CHANGELOG.md` head entry are all derived from or validated against it
(`pytest -m packaging` and `pytest -m docs` are the drift guards).

**Verification levels** (important for release honesty):

1. **automated package verification** — `verify_windows_package.py` (M6.5);
2. **isolated local verification** — the same verifier runs the frozen
   executable from an isolated bundle copy in `build/verification_work/` with
   `PYTHONPATH`/`PYTHONHOME` cleared;
3. **actual clean-machine verification** — running the artifact on a separate
   clean machine. **This repository does not claim a separate-machine clean
   test**; the performed check is (2), which runs the real packaged executable
   but on the machine that built it.

See the [release checklist](docs/release-checklist.md) for the required vs
optional/environment-dependent gates and the "Known limitations" subsection
below for the documented product limitations.

### Desktop UI (M5.2–M5.9, the complete desktop workflow)

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

**Desktop workflow.** The implemented desktop workflow is:

```text
Select PDF
    ↓
Analyze PDF
    ↓
Configure output and optional cover
    ↓
Start conversion
    ↓
Background conversion (application layer, off the GUI thread)
    ↓
EPUB validation
    ↓
Results and output actions
```

The order of steps is fixed: **a PDF is analyzed before any conversion
configuration is made available**, and conversion is always executed through
the M5.1 application layer (`ConversionApplication.convert`) on a background
`QThread` — the window never parses the PDF itself, never calls the pipeline
directly, and never runs conversion on the GUI thread.

**Supported document types.** PDF analysis classifies a document as one of
three types (M3.1), and the unified M4.1 pipeline converts **all three** to
EPUB (and optionally AZW3):

* **TEXT** — meaningful text on essentially all pages. Native layout-aware
  reconstruction; no OCR involved.
* **SCANNED** — little or no meaningful text; pages are primarily images.
  Pages are rendered, OCR'd, and cleaned before structural reconstruction.
* **MIXED** — a substantial mixture of text pages and image-based pages.
  Text pages use native extraction; image pages are OCR'd; the two text
  streams stay separate in the resulting `Book` (never merged).

OCR is only required for SCANNED and MIXED documents (the `ocr` extra plus an
external Tesseract executable); TEXT-only conversion never touches OCR. EPUB
and AZW3 generation behave identically for every classification: the same
`Book` is built, the EPUB is produced and (by default) structurally validated,
and when AZW3 is selected the emitted EPUB is the input to the M4.3
EPUB → AZW3 step.

M5.3 implements the first desktop workflow: select a PDF with the file picker,
see the selected path, run **Analyze PDF**, and read a summary of the M3.1
analysis (page count, document type, text/scanned/mixed page counts, and
whether OCR is required). The window tracks an explicit small state model
(`UiState`: no input → input selected → analyzing → analysis complete /
analysis failed), invalidates stale analysis when the input changes, and
translates validation and analysis failures into user-readable status messages
while staying usable for a retry.

M5.4 adds the conversion-options step (select → analyze → configure → ready):

* **Output format.** A combo offers **EPUB** and **AZW3**; both map to the
  existing application-layer `OutputFormat` enum (`epub` default). Per the
  application contract an EPUB is always produced and AZW3 is an additional
  artifact, so selecting AZW3 means `formats = {EPUB, AZW3}`.
* **Output directory.** A native directory picker stores and displays the
  selected directory; cancelling preserves the current selection and nothing
  is created on disk just by selecting it.
* **Optional cover.** A native image picker filters to the image types the
  existing M4.4 `load_cover` implementation accepts (JPEG, PNG, GIF, SVG).
  The selection can be cleared. No image processing happens in the UI — the
  application layer (M4.4 cover handling) owns loading and validation.
* **Request construction.** `MainWindow.build_conversion_request()` reads the
  current UI state and constructs the real
  `kindle_converter.application.ConversionRequest` (input PDF, output
  directory, output formats, optional cover, default validation settings). No
  UI-specific request object exists.
* **Readiness model.** After a successful analysis the window is
  *configuring*; a configuration becomes **ready for conversion** only when an
  analyzed input, an output directory, and a valid format are present and any
  supplied cover is acceptable. Readiness is decided by application-layer
  validation: `ConversionApplication.validate_request()` (added for M5.4) runs
  the same pre-flight path checks `convert` performs — readable input,
  existing output directory, resolvable cover, explicit Calibre path — without
  executing or analyzing anything.
* **Input change invalidation.** Selecting a different PDF clears the previous
  analysis and any readiness while retaining the independent configuration
  selections (output directory/format/cover), so a request can never refer to
  a stale, previously analyzed PDF.

M5.4 performs **no conversion**: changing the format, picking a directory or a
cover, or building the request never invokes `ConversionApplication.convert`
or any lower-level conversion function, and no `QThread`/worker, no progress
UI, and no result/validation presentation are introduced. The UI remains a thin
presentation layer: analysis and configuration validation always go through the
M5.1 application API, never by importing or calling the PDF/pipeline
implementation from the UI. Everything is synchronous for now; background
conversion execution is reserved for M5.5.

M5.5 adds background **conversion execution** to the window:

* **Convert button + progress bar.** A **Convert** row sits above the status
  label: a `convertButton` (enabled exactly when the window is
  configured-and-ready, including after a failed conversion for retry) and a
  `progressBar`. While converting the bar is **indeterminate** (M5.5 never
  fabricates a percentage — `ConversionApplication.convert` reports discrete
  `ConversionStage` progress, not completion fractions); it fills to 100% on
  success and resets to 0 on failure.
* **Background worker, thread-safe by construction.** Conversion runs on a
  dedicated `QThread` via a new QtCore-only `ConversionWorker`
  (`kindle_converter.ui.worker`). The worker only ever calls the M5.1
  application API; it never touches widgets, and the UI never runs
  `ConversionApplication.convert` on the GUI thread. Reusing the existing
  `ConversionStage` / `ConversionProgress` / progress-callback types, the
  worker relays `progress`, `succeeded`, and `failed` back to the window over
  Qt queued signal connections, then emits `finished` so the window can
  release and clean up the thread (`worker.finished → thread.quit`
  direct-connected; `thread.finished` triggers window teardown). One worker +
  one thread per conversion, retained on the window, never reused.
* **Explicit conversion states.** Three new `UiState` values —
  `CONVERTING`, `COMPLETED`, `CONVERSION_FAILED` — gate the controls while a
  conversion is in flight (everything disabled during `CONVERTING`, restored
  afterwards) and drive the status line: an indeterminate "converting" message
  during the run, the conversion's own completion message verbatim on success,
  and the failure message verbatim on failure. The window exposes
  `last_result` / `last_error` accessors for the completed run.
* **No cancellation in M5.5** (a real progress callback and cancellation are
  future milestones). `closeEvent` guards the window instead: closing while a
  conversion is running waits for it to finish safely, so a live `QThread` is
  never destroyed mid-run.
* **Testability / determinism.** The flow tests drive conversion through an
  injectable fake application object that can block on an event, so the worker
  thread, queued signal delivery, and teardown are exercised deterministically
  on an offscreen `QApplication` (no real PDFs, no Calibre). Thread teardown
  is fully flushed in the tests (`DeferredDelete` events are dispatched while
  the window is still alive) so no queued delete can outlive a test and crash
  a later one.

M5.6 adds **results + validation presentation** and **output actions** to the
completed conversion:

* **Results section.** After a conversion succeeds, a results section becomes
  visible under the progress bar. It is populated only from the real
  :class:`ConversionResult` retained on the window: a completion status, an output
  format label derived from the request's `requested_formats` (always
  ``"EPUB"`` or ``"EPUB + AZW3"``), and the exact `epub_path` / `azw3_path`
  (the AZW3 row and its "Open AZW3" button appear only when the result carries
  an `azw3_path`). No second, UI-specific result model exists and nothing is
  recomputed or reconstructed.
* **Validation section.** The results section renders the result's *existing*
  `EPUBValidationResult` (`result.validation`): "Valid" / "Invalid" from
  `validation.valid`, warning/error counts, and the structured
  `EPUBValidationIssue` messages as a bullet list (shown only when non-empty).
  A result whose validation was disabled (`validation is None`) reports
  "Not run". The UI never reruns validation — the worker still performs and
  returns it exactly once inside `ConversionApplication.convert`.
* **Output actions.** **Open EPUB**, **Open AZW3**, and **Open Folder** open
  the real output paths (`result.epub_path`, `result.azw3_path`, and
  `result.epub_path.parent`) with the OS default handler through a small
  injectable platform seam, `kindle_converter.ui.platform.open_path`
  (`os.startfile` on Windows, `open` on macOS, `xdg-open` elsewhere), which
  translates platform failures into a single `PlatformOpenError`. The window
  catches it (and `OSError`) and shows a concise status on the results section;
  a missing retained result or a no-longer-existing output file gets its own
  message. Opening never modifies the retained result or deletes output files.
* **Stale-result protection.** `_clear_result()` drops the retained result and
  hides its section whenever the current request is invalidated: a new input,
  an output-format change, an output-directory change, and the start of a new
  conversion all clear a previous result, so an old result is never presented
  as the current request's. A failed conversion never shows a successful
  result.

M5.7 adds application-level **error handling** and a **temporary workspace** for
every conversion:

* **Error model.** Failures are divided into two groups:
  * **Expected application-level conversion failures** — an invalid request,
    a required stage that could not run, an output that could not be written,
    an EPUB that failed structural validation, a failed optional AZW3 step, or
    a temporary workspace that could not be created. Each is a
    `kindle_converter.application.ApplicationError` subclass
    (`InvalidRequestError`, `ConversionFailedError`, `OutputError`,
    `ValidationFailedError`, `AZW3OutputError`, `WorkspaceError`) chained to its
    underlying cause, and the UI displays these messages verbatim as
    user-readable status text while returning the window to a usable state for
    a retry.
  * **Unexpected failures** — anything else (a programming error). The worker
    logs these with their traceback and the UI shows a concise generic message;
    they are never misreported as a successful conversion or as a recoverable
    application error.

  Not every possible failure is recovered automatically — the application
  reports what went wrong instead, without ever losing the underlying cause.
* **Temporary workspace.** Each call to `ConversionApplication.convert`
  creates an application-owned temporary workspace (a
  `tempfile.TemporaryDirectory`) that exists only for the lifetime of that
  conversion: it is created on entry and deterministically cleaned up on exit
  — successfully, **or on any failure path** (expected application errors,
  validation failures, unexpected exceptions). The workspace is owned by the
  application/conversion layer, never by the GUI or the worker, and it is
  deliberately **not** the user's selected output directory: final EPUB/AZW3
  outputs always remain in the directory the user chose, and workspace cleanup
  never touches it. A workspace-creation failure raises `WorkspaceError`
  before any conversion work starts; a cleanup failure is logged as a warning
  and never replaces the original conversion failure.

M5.8 adds **integration tests** verifying the complete desktop workflow across
the established application boundary (MainWindow → ConversionWorker →
ConversionApplication → pipeline). These tests cover the happy path, all three
document types (TEXT, SCANNED, MIXED), output configuration (EPUB, AZW3, cover),
background conversion and thread/UI boundary, results and validation
presentation, output-opening actions, error paths (invalid input, analysis
failure, conversion failure, validation failure, workspace failure, unexpected
exception, AZW3 failure after EPUB succeeds), workspace cleanup, stale-result
protection, and UI state transitions. All tests run headless via
``QT_QPA_PLATFORM=offscreen`` and skip cleanly when PySide6 is unavailable
(the ``ui`` extra is absent). See ``tests/test_ui_integration.py``.
* **Testability / determinism.** New headless tests drive the real QThread
  lifecycle with an injectable fake application (as in M5.5) and replace the
  platform opener with a recording double, asserting the exact path sent to it
  and that failures surface locally. The platform seam itself is unit-tested
  per platform branch with monkeypatched `sys.platform`, `os.startfile`, and
  `subprocess.Popen` — no external program is ever launched, and the UI tests
  never invoke EPUB validation (`"validate_epub"` does not appear in
  `main_window.py`).

M5.9 is the **documentation and M5 closure** milestone. This README describes
the implemented M5 state — project status, roadmap, desktop workflow, PDF
classifications, conversion options, results/validation, background
conversion, temporary workspace and error handling, installation, testing, and
license — and records the M5 acceptance checklist below. It was written before
Milestone 6 began; the Milestone 6 sections further below document M6.1–M6.3.

### M5 acceptance checklist

Milestone 5 is complete; it delivers:

* [x] application / pipeline API (M5.1 `kindle_converter.application`)
* [x] PySide6 desktop application (M5.2, optional `ui` extra)
* [x] PDF input and analysis (M5.3; the window analyzes through the
  application API, never by parsing the PDF itself)
* [x] TEXT / SCANNED / MIXED classification handling (M3.1 analysis in the
  UI; the unified M4.1 pipeline converts all three)
* [x] EPUB / AZW3 output selection (M5.4; an EPUB is always produced, AZW3 is
  an additional artifact converted from the EPUB)
* [x] optional cover (M5.4; loaded and validated by the application layer,
  M4.4)
* [x] configurable output directory (M5.4; outputs remain in the user-selected
  directory)
* [x] background conversion (M5.5, dedicated `QThread`; GUI stays responsive)
* [x] progress reporting (M5.5, stage-level via the application callback;
  indeterminate bar, no fabricated percentage)
* [x] EPUB validation / results (M5.6; rendered from the real
  `ConversionResult` / `EPUBValidationResult`, never rerun)
* [x] output-opening actions (M5.6; Open EPUB / Open AZW3 / Open Folder)
* [x] application-level error handling (M5.7; expected failures are
  user-facing, unexpected failures are logged)
* [x] temporary workspace cleanup (M5.7; per-conversion, deterministic on
  every path)
* [x] UI / integration test coverage (M5.8; headless, skipped when PySide6 is
  unavailable)
* [x] documentation (M5.9; this README)

Remaining/future capabilities (not implemented in M5): **drag-and-drop**
input (input is selected through the file picker), conversion **cancellation**
(a running conversion finishes rather than being stopped), and remaining
Milestone 6 work (performance measurement, Windows packaging, and release
preparation). The M6.1 representative corpus and the M6.2 regression + M6.3
conversion-quality measurement suites are implemented and part of the standard
test run.

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
  `FileNotFoundError`/`CalledProcessError`). On Windows, the child process is
  launched with a hidden console (`CREATE_NO_WINDOW` + `STARTUPINFO`) so the
  app does not open a visible console window while preserving the same
  behavior.
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

* **Explicit and optional.** An explicit cover is never auto-detected and
  always wins: no PDF filename heuristics, no image classification, and no
  detection runs at all. A `Book` (or EPUB) without a supplied cover renders
  exactly as before unless a cover is confidently auto-detected (M7.2 below).
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

### Automatic cover selection (M7.2)

M7.2 adds a single conservative step between the two M4.4 states: when no
explicit cover is supplied, the converter looks at a bounded window of early
pages and selects a page as the cover *only* when the evidence is clear. The
resolved precedence is therefore:

```text
explicit user cover  >  automatically detected cover  >  no cover
```

Detection is a small, deterministic service inside the PDF layer
(`kindle_converter.pdf.cover_detection` through the `select_cover` /
`detect_cover` API) that reuses the existing milestones and never re-reads
page text:

* `measure_cover_signals` measures only the first `COVER_CANDIDATE_WINDOW` (5)
  pages, so detection cost is independent of document length;
* `score_cover_page` applies fixed rejection gates in a fixed order — blank
  pages, pages that are not image-dominated, untexted pages that are not
  (almost) a full-page image, pages in a book with no text evidence at all,
  table-of-contents text, copyright/title-page front matter, body-text-mass,
  and text that is not short (in absolute and book-relative terms) — and then
  accumulates documented weights (image coverage, early position, title-like
  text, portrait orientation);
* `decide_cover_page` selects the top candidate only when it reaches
  `COVER_CONFIDENCE_THRESHOLD` (55) **and** leads the runner-up by at least
  `COVER_AMBIGUITY_MARGIN` (12); a tie, a close race, or no eligible page
  yields the pre-M7.2 coverless EPUB;
* `materialize_cover_page` renders the selected page through the existing M3.2
  renderer at `COVER_RENDER_DPI` (200) as PNG, so the cover shows the page as
  displayed (image, vector art, and overlaid text).

Design guarantees:

* **A cover is never guessed.** First page is one weighted signal among
  several, never an assumption: a first page that is body text, blank, a
  table of contents, copyright front matter, or not image-dominated is
  rejected, and page 2 (or later within the window) can win (e.g. a prose
  title page followed by a cover plate). Weak or ambiguous evidence means
  *no* cover, and the EPUB matches the pre-M7.2 coverless output byte for
  byte in structure.
* **No new machinery.** No OCR engine, image classifier, machine-learning
  model, network service, or filename heuristic is involved; detection is a
  pure function of the analysis (M3.1), routing (M3.5), and layout (M2.1)
  results and consumes the same per-page text the pipeline already produced
  (a SCANNED page's OCR text, a TEXT/MIXED page's native text — so the M7.1
  scanned-first-page fix is preserved by construction).
* **An explicit cover always wins and short-circuits detection entirely.**
  When `cover` is supplied no detection runs at all, and a detected cover is
  carried exactly like an explicit one: a PNG `Book.cover`, the unchanged
  M4.4 `cover.xhtml` / `properties="cover-image"` / `name="cover"` contract,
  a linear cover page, and a non-linear nav. Reconstructed content is never
  modified: like M4.4's explicit cover, the auto-detected cover is a reading
  step *in addition to* the reconstructed document, and the source page stays
  in content exactly as it does for a covered M4.4 book.

```text
PDF → analysis + routing + layout → measure → score → decide → render page → Book.cover
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

## Known limitations

The following are the documented, implemented limitations of the 0.1.0
converter (also recorded in the [CHANGELOG](CHANGELOG.md)). They are
*product* limitations surfaced by the M6.3 quality measurement and M6
development, not claims of missing documentation:

* **EPUB chapter splitting is absent.** The PDF layer detects and indexes
  chapters (`novel_basic` 3, `chapters_long` 8, `twocolumn_article` 3 TOC
  entries), but the EPUB always ships a single chapter file with one nav
  entry (`nav_entries >= 1`). Recorded in the M6.3 quality baseline as
  follow-up work.
* **Document titles are not detected as headings.** The M2.4 heading detector
  is deliberately conservative: explicit `Chapter N:` headings are surfaced,
  but book titles such as "Minimum Viable Document" are treated as body text.
* **Reading order on complex layouts is best-effort.** Multi-column behavior is
  exercised for the two-column case; unusual typography and dense layouts can
  still reconstruct imperfectly. OCR quality also depends on the source scan,
  and OCR performs **no recognition correction** (no spelling/dictionary/
  grammar/character-substitution correction).
* **AZW3 is a best-effort external conversion.** AZW3 "success" means Calibre's
  `ebook-convert` exited 0 and produced a non-empty file — not a guarantee of
  Kindle marketplace acceptance or exact device rendering — and AZW3 output is
  not byte-for-byte deterministic (Calibre stamps its own metadata).
* **No fixed-layout / per-device output.** The EPUB is deliberately reflowable
  and generic: no KFX, no fixed-layout EPUB, no Kindle Previewer automation, no
  per-model CSS.
* **No drag-and-drop input and no conversion cancellation.** Input is selected
  through the file picker; a running conversion finishes rather than being
  stopped.
* **No clean-machine verification on a separate machine has been performed.**
  The strongest performed check is the isolated local run of the frozen
  artifact (M6.5/M6.6); a true clean-VM check is a documented optional gate.
* **No telemetry, auto-updates, or cloud services.** The application is
  local-first by design.

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
* [x] Simple desktop interface (M5.2 — optional `ui` extra,
  `kindle_converter.ui` main window + entry point; no conversion workflow yet)
* [x] Input selection + PDF analysis UI (M5.3 — select a PDF, analyze it
  through `ConversionApplication.analyze_pdf`, display the M3.1 analysis
  summary; explicit UI states and clean validation/error handling; no
  conversion yet)
* [x] Output format selection (M5.4 — the EPUB/AZW3 output-format combo maps
  to the application-layer `OutputFormat` enum; per the application contract
  an EPUB is always produced and AZW3 is an additional artifact)
* [x] Output directory management (M5.4 — native directory picker; nothing is
  created on disk just by selecting it, and M5.6's **Open Folder** action
  opens the real containing directory of the produced outputs)
* [x] Conversion options + cover handling UI (M5.4 — output format EPUB/AZW3,
  output directory picker, optional cover selection, and construction of the
  real application-layer `ConversionRequest` via
  `MainWindow.build_conversion_request`; explicit `UiState` readiness
  (configuring → ready for conversion) gated by the new non-executing
  `ConversionApplication.validate_request`; no conversion execution yet)
* [x] Conversion execution (M5.5 — run the configured `ConversionRequest` in
  the background on a `QThread` worker via `ConversionApplication.convert`)
* [x] Conversion progress (M5.5 — indeterminate progress bar while converting,
  full on success, reset on failure)
* [x] Results presentation (M5.6 — after success, show the real
  `ConversionResult`: status, requested output formats, exact EPUB/AZW3
  output paths; stale results are cleared whenever the input, options, or a new
  conversion invalidate the request, and failures never show a result)
* [x] Validation presentation (M5.6 — render the existing
  `EPUBValidationResult` (valid/invalid, warning/error counts, issue
  messages) or "Not run"; the UI never reruns validation and no second result
  model exists)
* [x] Output actions (M5.6 — **Open EPUB / Open AZW3 / Open Folder** open the
  real output paths through the injectable `kindle_converter.ui.platform`
  seam; missing outputs and platform-open failures surface locally)
* [x] Error reporting (M5.7 — expected application-level conversion failures
  surface as user-readable status messages; unexpected errors are logged with
  their traceback and shown as a concise generic message; the window stays
  usable for retry)
* [x] Errors + Temporary Files (M5.7 — application-owned temporary workspace
  for every conversion: creation on entry, deterministic cleanup on exit,
  `WorkspaceError` for creation failures, cleanup failures logged but never
  replacing the original conversion failure)
* [x] UI/Integration Testing (M5.8 — integration tests verifying the complete
  desktop workflow across the MainWindow → ConversionWorker → ConversionApplication
  → pipeline boundary: happy path, TEXT/SCANNED/MIXED document types, output
  configuration, background conversion, thread/UI boundary, results/validation,
  output actions, error paths, workspace cleanup, stale-result protection, and
  state transitions)
* [x] Documentation + M5 Closure (M5.9 — this README reconciled with the
  implemented M5 state: project status, roadmap, desktop workflow, PDF
  classifications, conversion options, validation/results, background
  conversion, temporary workspace and cleanup, error handling, installation,
  testing, license, and the M5 acceptance checklist)
* [ ] Drag-and-drop input (future work — not implemented in M5; the desktop
  workflow selects input through the file picker)

### Milestone 6 — Quality and Distribution

* [x] Build representative test corpus (M6.1; deterministic synthetic PDF
  corpus under `tests/fixtures/corpus` — see that directory's `README.md` —
  with a byte-reproducible generator, a machine-readable `manifest.json`,
  and infrastructure coverage in `tests/test_corpus.py`)
* [x] Add regression tests (M6.2; deterministic corpus-driven regression
  harness under `tests/regression` — 253 tests marked `regression` — running
  the M6.1 corpus through the real conversion pipeline and asserting
  observable behavior against the version-controlled baseline
  `tests/fixtures/regression_baseline.json`; see "Regression test harness"
  below)
* [x] Measure conversion quality (M6.3; deterministic corpus-driven quality
  measurement under `tests/quality` — 148 tests marked `quality` — authoring
  per-fixture quality expectations (structure, reading order, OCR routing,
  images, EPUB) in `tests/fixtures/quality_baseline.json`, evaluating them
  against fresh measurements, and rendering human/machine reports; regeneration
  is explicit via `python -m tests.quality.regenerate_baseline`; see
  "Conversion quality measurement (M6.3)" below)
* [x] Measure performance (M6.4; reproducible corpus benchmarks under
  `tests/performance`, phase timing, Python allocation peaks, a separate
  `tests/fixtures/performance_baseline.json`, explicit regeneration, and
  tolerance-aware regression comparison; see "Performance measurement (M6.4)"
  above)
* [x] Package for Windows (M6.5; reproducible PyInstaller onedir bundle built
  from a clean project-dedicated venv into `dist/KindleBookConverter` with a
  single GUI-subsystem executable, Windows version resource, bundled runtime
  distribution metadata, and provenance stripping; verification runs the real
  executable in an isolated bundle copy and exercises identity, launch, and
  real conversions over the M6.1 fixtures, including graceful OCR
  unavailability and AZW3 through Calibre; see "Windows packaging (M6.5)" and
  `docs/windows-packaging.md`)
* [x] Documentation (M6.6; final user documentation in
  `docs/user-guide.md`, developer documentation in `docs/development.md`,
  release/packaging documentation in `docs/windows-packaging.md`, a release
  checklist in `docs/release-checklist.md`, and this README finalized as the
  primary landing page, with documented known limitations)
* [x] Release preparation (M6.6; single authoritative version source retained
  in `pyproject.toml` with changelog/docs consistency checks, a release
  checklist separating required from environment-dependent gates, the initial
  changelog in `CHANGELOG.md`, and a lightweight release validator
  `build_tools/release_check.py` reusing the M6.5 package verifier; M6 is
  complete)

### Milestone 7 — Robustness on Real-World PDFs

* [x] First-Page Investigation and Root-Cause Fix (M7.1; investigate and fix
  the "unexpected first page" reported on a real 468-page PDF (446 TEXT / 22
  SCANNED / 0 MIXED pages): the investigation reproduced both mechanisms that
  could open an EPUB with a page the source PDF does not contain, proved the
  routing contract was being violated (a page classified `SCANNED` had its
  native layout text re-emitted alongside its OCR text), and fixed both at the
  root, never by deleting first-page content:
  - the structural integration now honors the M3.5/M3.6 routing contract —
    `TEXT` pages stay native-only, `SCANNED` pages contribute their OCR
    paragraphs only, and `MIXED` pages keep native + OCR (regression tests in
    `tests/test_structural_reconstruction.py`);
  - the EPUB navigation document leads the spine but is now marked
    `linear="no"`, so no reading system can page the generated TOC as the
    book's first content page while the navigation stays discoverable
    (regression tests in `tests/test_kindle_epub.py`);
  - no metadata/title/front-matter page is ever fabricated and no cover page
    is invented from book filename or metadata; the first *linear* EPUB page
    is always real book content (M7.2 may add a real detected cover page
    ahead of it, never a fabricated one)
* [x] Automatic cover selection (M7.2; deterministic, offline, and
  conservative: a bounded window of early pages is measured from the existing
  M3.1/M3.5/M2.1 results, filtered by documented rejection gates, scored with
  weighted evidence, and selected only at a confidence threshold with an
  ambiguity margin; an explicit cover always wins and short-circuits
  detection, and a detected cover is carried through the unchanged M4.4 EPUB
  contract; regression tests in `tests/test_cover_detection.py`)
* [x] AZW3 process hardening (M7.3; the Calibre subprocess window is hidden on Windows without changing conversion behavior)

No web version is planned or implemented; the supported interface remains the
Windows desktop application (M5) and the programmatic pipeline API (M5.1).

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

The project is **proprietary** (all rights reserved). The package metadata in
`pyproject.toml` declares `license = { text = "Proprietary" }`; there is no
separate `LICENSE` file in the repository at this time.
