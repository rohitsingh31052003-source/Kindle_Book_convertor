# Kindle Book Converter — Development Guide

This guide is for developers working on the repository. It describes how to
set up a development environment, run the tests, understand the project
structure, and work with the packaging tooling.

| Topic | Reference |
| --- | --- |
| User-facing documentation | [User guide](user-guide.md) |
| Windows packaging / release artifact | [Windows packaging](windows-packaging.md) |
| Release process and gates | [Release checklist](release-checklist.md) |
| Release notes | [CHANGELOG](../CHANGELOG.md) |
| Contributing | [CONTRIBUTING](../CONTRIBUTING.md) |
| Security reporting | [SECURITY](../SECURITY.md) |

## Requirements

* **Python 3.12 or newer** (`requires-python = ">=3.12"` in `pyproject.toml`).
  The repository is currently developed and packaged on **Python 3.14**
  (Windows). The package is `src/`-layout `kindle_converter`.

No linter, type-checker, formatter, or coverage tool is **configured** in this
repository: `pyproject.toml` configures only `pytest` (see
["Running the tests"](#running-the-tests)). Do not assume such a tool exists;
the test suites are the project's quality gate.

## Environment setup

Create a virtual environment and install the package in editable mode with its
development extras:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,ui,ocr]"
```

`pip install -e ".[dev]"` gives the core + pytest only. Add the extras you
need:

| Extra | Provides | Purpose |
| --- | --- | --- |
| `dev` | `pytest` | Test runner |
| `ui` | `PySide6` | The desktop application (`python -m kindle_converter.ui`) and its tests |
| `ocr` | `pytesseract`, `Pillow` | Python wrappers for Tesseract OCR (the Tesseract executable is external) |

The environment variables `TESSERACT_CMD` and `CALIBRE_CONVERT` are honored by
the optional integration tests when the executables are not on `PATH`.

## Entry points

* **Library API:** `from kindle_converter import convert_pdf_to_book,
  convert_pdf_to_epub`. Lower-level stages live in `kindle_converter.pdf`
  (`analyze_pdf`, `extract_book`, OCR, layout, reconstruction),
  `kindle_converter.document` (the `Book` model, `cover`), and
  `kindle_converter.epub` (`build_epub`, `validate_epub`,
  `convert_epub_to_azw3`).
* **Application use case API (M5.1):** `kindle_converter.application`
  (`ConversionApplication.convert` / `.analyze_pdf` / `.validate_request`,
  `ConversionRequest`, `ConversionResult`, progress events, and the
  `ApplicationError` boundary). This is the stable boundary the UI talks to;
  neither the UI nor callers of this package reach into the PDF/EPUB internals.
* **Desktop UI:** `python -m kindle_converter.ui` (requires the `ui` extra).
  `python -m kindle_converter.ui` → `kindle_converter/ui/app.py` →
  `main_window.py`.
* **Packaged verification subcommands:** `KindleBookConverter.exe
  --smoke-check | --smoke <pdf> <outdir> [...] | --sysinfo` (see
  [Windows packaging](windows-packaging.md)).

## Project structure

```text
src/kindle_converter/
├── pipeline.py            # PDF → Book → EPUB orchestration (thin composition)
├── _meta.py               # identity + single-sourced version resolution
├── pdf/                   # analyzer, extractor, layout, reading_order,
│                          #   paragraphs, headings, chapters, toc, metadata,
│                          #   header_footer, page_numbers, images,
│                          #   renderer, ocr, ocr_cleanup, processing,
│                          #   structural, reconstruction, models
├── document/              # format-independent Book model, cover handling
├── epub/                  # builder, formatting, validation, azw3, calibre
├── application/           # M5.1 use-case boundary for the UI
└── ui/                    # PySide6 desktop UI (optional ui extra), worker,
                           #   platform seam, smoke harness

build_tools/               # Windows packaging + release + distribution tooling
                           #   (M6.5/M6.6/M8.2)
tests/
├── fixtures/corpus/       # M6.1 deterministic synthetic PDF corpus
├── regression/            # M6.2 deterministic corpus regression harness
├── quality/               # M6.3 conversion-quality measurement
├── performance/           # M6.4 performance measurement
└── test_*.py              # unit/integration suites (M1–M6.5)

docs/                      # user guide, development guide, packaging, checklist
.github/                   # issue and pull-request templates (M8.4)
CONTRIBUTING.md            # contribution guidance (links here for the details)
SECURITY.md                # security reporting policy
CHANGELOG.md               # release notes
```

## Architecture at a glance

```text
PDF
 ├─ analyze_pdf ─────────────► TEXT | SCANNED | MIXED
 ├─ extract_page_layout ─────► native layout (text pages)
 ├─ render_page + OCR ───────► scanned/mixed pages (Tesseract)
 └─ reconstruct → Document →  Book (kindle_converter.document)
                                 │
                                 ▼
                          build_epub → EPUB
                                 │
                                 ├─ validate_epub → EPUBValidationResult
                                 └─ convert_epub_to_azw3 → AZW3 (Calibre)
```

There is exactly one EPUB rendering path and it consumes the format-independent
`Book` model only. The PDF layer never produces EPUB HTML; the EPUB layer never
inspects PDFs, runs OCR, or reconstructs structure.

The **desktop UI is a thin presentation layer**. It never parses the PDF, never
calls the pipeline directly, and never runs conversion on the GUI thread.
Input analysis, request validation, and conversion all go through the M5.1
application boundary; conversions run on a dedicated `QThread` worker
(`kindle_converter/ui/worker.py`). Opening output files goes through an
injectable platform seam (`kindle_converter/ui/platform.py`).

## Running the tests

`pytest` is configured (`pythonpath = ["src"]`, `testpaths = ["tests"]`), so
tests run without installing the package:

```bash
venv\Scripts\python.exe -m pytest            # full suite
```

The suite is organized by markers so that you can run a single concern:

```bash
venv\Scripts\python.exe -m pytest -m regression     # M6.2 corpus regression (253 tests)
venv\Scripts\python.exe -m pytest -m quality        # M6.3 conversion quality (148 tests)
venv\Scripts\python.exe -m pytest -m performance    # M6.4 performance framework (12 tests)
venv\Scripts\python.exe -m pytest -m packaging      # M6.5/M8.2 packaging configuration (46 tests)
venv\Scripts\python.exe -m pytest -m docs           # M6.6 documentation/release checks
```

The authoritative commands are also printed by the release tool:

```bash
venv\Scripts\python.exe build_tools/release_check.py --print-commands
```

### Suite behavior and conventions

* **Regression, quality, packaging, and the docs checks are deterministic and
  offline.** They need no Tesseract, no Calibre, no network, no GUI, and use
  no absolute machine paths in observations.
* **Optional integrations skip cleanly.** PySide6 UI tests skip when the `ui`
  extra is absent; the real Tesseract (`tests/test_pdf_ocr_tesseract.py`) and
  real Calibre (`tests/test_epub_azw3_calibre.py`) integration suites skip when
  the executables are unavailable.
* **UI tests run headless** (`QT_QPA_PLATFORM=offscreen`).
* **Baselines are guarded.** The M6.2 regression, M6.3 quality, and M6.4
  performance baselines under `tests/fixtures/` are committed and are never
  silently overwritten by ordinary tests. Regeneration is explicit and each
  tool diffs first (see the README sections and each suite's module docstring).
* A pre-existing `UserWarning` about duplicate ZIP entries appears in
  `tests/test_epub_validation.py`; it is known and intentionally left in place.

## Packaging workflow

Building and verifying the Windows artifact is a self-contained M6.5 flow that
uses a **project-dedicated virtual environment** (`build/windows_build_venv`)
so the build never touches your developer environment:

```bash
C:\Python314\python.exe build_tools/build_windows.py
C:\Python314\python.exe build_tools/verify_windows_package.py
```

The M8.2 **distribution** step packages the verified bundle into the
reproducible release archive (ZIP + SHA-256) and verifies it:

```bash
C:\Python314\python.exe build_tools/package_distribution.py
```

The release-readiness tool can run the package verifier for you:

```bash
venv\Scripts\python.exe build_tools/release_check.py --package
```

See [Windows packaging](windows-packaging.md) and
[Release checklist](release-checklist.md). The packaging tooling directory is
named `build_tools` (not `packaging`) deliberately, so the repo root on
`sys.path` never shadows the third-party `packaging` distribution.

## Releasing

When you are ready to release, follow the
[release checklist](release-checklist.md). In short:

1. Confirm the repository is clean and versioned (`pyproject.toml` is the
   single version source; `CHANGELOG.md` gets a matching head entry).
2. Run the test suites (regression, quality, performance, packaging, docs,
   and the full suite).
3. Build and verify the Windows package.
4. Run `build_tools/release_check.py` to validate release-readiness and the
   artifact.

Do **not** make conversion-algorithm changes inside a release/engineering
milestone unless a tiny compatibility fix is genuinely required; product
issues should be documented first.