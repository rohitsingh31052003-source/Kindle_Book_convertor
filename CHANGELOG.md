# Changelog

All notable changes to the Kindle Book Converter are documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to the versioning established in `pyproject.toml`
(the single authoritative version source — see
[docs/windows-packaging.md](docs/windows-packaging.md#version-handling)).

## [0.1.0] - 2026-09-19

Initial release. This entry documents the capabilities actually implemented
across Milestones 1–6 of this repository. It describes the converter honestly:
see **Known limitations** below for what is deliberately not claimed.

### Added

* **PDF analysis and classification (M1/M3)** — deterministic analysis of a
  PDF into **TEXT**, **SCANNED**, or **MIXED** page types with per-page
  text/scanned/mixed counts and an explicit "OCR required" signal.
* **Native text PDF conversion (M2)** — layout-aware reconstruction: reading
  order, paragraph reconstruction, conservative heading detection, chapter
  detection, table-of-contents generation, metadata handling, extraction and
  placement of embedded images, and removal of repeated headers/footers and
  page numbers.
* **Scanned PDF OCR (M3.2–M3.4)** — page rendering, Tesseract-based OCR, and
  a conservative, deterministic OCR cleanup layer. OCR is an optional,
  external dependency: the Python wrappers are the `ocr` extra and the
  `tesseract` executable is never bundled.
* **Mixed PDF handling (M3.5)** — deterministic per-page routing of text and
  image pages; native and OCR text stay as separate, never-merged streams.
* **Unified PDF → Book → EPUB pipeline (M4.1)** — one EPUB rendering path for
  all three classifications through the format-independent `Book` model.
* **Kindle-oriented EPUB output (M4.1/M4.5)** — a reflowable EPUB: semantic
  markup, one deterministic Kindle-friendly stylesheet (relative `em`
  typography), responsive centered images, conservative page-break behavior,
  EPUB 3 navigation plus NCX.
* **Structural EPUB validation (M4.2)** — read-only validation of finished
  EPUB artifacts with structured, machine-readable issue reporting.
* **Cover handling (M4.4)** — explicit, optional cover images (JPEG/PNG/GIF/SVG)
  with correct EPUB cover semantics and a reflowable cover page.
* **EPUB → AZW3 conversion (M4.3)** — optional conversion through Calibre's
  external `ebook-convert` (never bundled), composing on the finished EPUB.
* **Desktop Windows application (M5)** — a PySide6 user interface
  (`python -m kindle_converter.ui`, optional `ui` extra) with the full
  workflow: select PDF, analyze, configure output (format, directory, optional
  cover), background conversion with progress, results/validation presentation,
  and Open EPUB / Open AZW3 / Open Folder actions.
* **Application layer (M5.1, M5.7)** — a UI-independent conversion use case
  (`kindle_converter.application`) with typed requests/results, progress
  events, an error boundary, and per-conversion temporary-workspace cleanup.
* **Deterministic, corpus-driven testing (M6.1–M6.4)** — an 11-fixture
  synthetic PDF corpus, a regression harness (253 tests), a conversion-quality
  measurement suite (148 tests), and a performance framework (12 tests), all
  offline and deterministic.
* **Windows packaging (M6.5)** — a reproducible PyInstaller onedir bundle with
  a single GUI-subsystem executable, Windows version resource, bundled runtime
  metadata, provenance stripping, and isolated verification of the packaged
  executable (see [docs/windows-packaging.md](docs/windows-packaging.md)).
* **Release engineering (M6.6)** — user/developer/release documentation,
  a release checklist, changelog, single-source version validation, and a
  release-readiness validator (`build_tools/release_check.py`).

### Known limitations (implemented behavior, not defects fixed in 0.1.0)

* EPUB output currently ships a **single chapter file** with one navigation
  entry even when the PDF layer detects multiple chapters; EPUB chapter
  splitting is not implemented.
* The heading detector is deliberately conservative: **document titles are
  not detected as headings** (explicit `Chapter N:` headings are).
* Reading order on complex/multi-column layouts is a **best-effort**
  reconstruction; unusual typography may convert imperfectly.
* OCR does **not** perform recognition correction (no spelling, dictionary,
  grammar, or character-substitution correction).
* AZW3 "success" means Calibre exited 0 and produced a non-empty file; it is
  **not** a guarantee of Kindle marketplace acceptance or exact device
  rendering, and AZW3 bytes are not deterministic.
* There is no per-device or fixed-layout output, no Kindle Previewer
  automation, no KFX, no drag-and-drop input, and no conversion cancellation.
* A true **clean-machine** verification on a separate physical machine has
  not been performed; the strongest performed check is the isolated local run
  of the frozen artifact.
* No telemetry, no auto-updates, and no cloud services: the application is
  local-first by design.

[0.1.0]: https://github.com/rohitsingh31052003-source/Kindle_Book_convertor