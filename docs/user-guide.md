# Kindle Book Converter — User Guide

This guide explains what the Kindle Book Converter does, what kinds of PDFs it
supports, how to use the Windows application, and how to resolve common
problems. It describes the **implemented** application as of release 0.1.0;
see the [README](../README.md) and the [readme roadmap](../README.md#development-roadmap)
for the milestone history.

Also see:

* [Development guide](development.md) — for developers working on the source.
* [Windows packaging](windows-packaging.md) — for building/verifying the Windows package.
* [Release checklist](release-checklist.md) — for the release process.
* [CHANGELOG](../CHANGELOG.md) — release notes.

## What Kindle Book Converter does

Kindle Book Converter is a **local-first** document conversion application: it
turns PDFs into reflowable, Kindle-friendly **EPUB** ebooks on your own
computer. Nothing you open is uploaded anywhere; all processing happens locally.

There are two output workflows:

1. **PDF → EPUB (primary).** Every supported PDF is converted into a
   reflowable EPUB. A reflowable ebook adjusts text to fit your reading screen
   instead of imitating the fixed page layout of the PDF.
2. **EPUB → AZW3 (optional).** AZW3 is the Kindle-specific format produced by
   Calibre's `ebook-convert` tool. When you select **AZW3** as the output
   format, the application first generates the EPUB and then converts that
   EPUB to AZW3 **on top of it**. AZW3 output always requires the external
   Calibre installation (see [AZW3 (optional)](#azw3-optional)).

The converter does not make a "photocopy" of your PDF. It reconstructs the
book's logical structure — paragraphs, headings, chapters, images, and major
PDF furniture such as repeated headers and footers — and renders that structure
as an ebook, so the result is intended to be read as a book, not a page image.

## Getting the application

The primary supported way to run the application is the packaged Windows
executable produced by the build described in
[Windows packaging](windows-packaging.md). The build produces a folder that is
distributed as a ZIP archive plus its SHA-256 checksum
(`KindleBookConverter-Windows-x64-<version>.zip` on a GitHub Release):

```text
KindleBookConverter/
├── KindleBookConverter.exe   ← run this
└── _internal/                ← runtime files (keep next to the .exe)
```

Extract the ZIP wherever you like, then keep the executable and its `_internal`
folder together — the application needs its runtime files and does not work from
the executable alone. When you launch the application, a single window titled
**Kindle Book Converter** opens.

Source users can instead install and launch from Python (see the
[README](../README.md#development)).

## Supported PDFs

The application analyzes each PDF and classifies it as one of three types:

| Type | What it means | How it is converted |
| --- | --- | --- |
| **TEXT** | Meaningful selectable text on essentially all pages | Native layout-aware text extraction and reconstruction; no OCR |
| **SCANNED** | Little or no selectable text; pages are primarily page images | Pages are rendered to images, OCR'd (requires Tesseract), and cleaned before reconstruction |
| **MIXED** | A substantial mixture of text pages and image-based pages | Text pages use native extraction; image pages are OCR'd; the two text streams stay separate in the result |

Behavior notes for specific kinds of inputs:

* **Native/text PDFs.** Converted through a deterministic layout-reconstruction
  pipeline: reading order, paragraphs, headings, chapters, page-number and
  repeated header/footer removal, metadata, and embedded images are handled.
* **Scanned PDFs.** Conversion depends on the external Tesseract OCR engine
  (see [OCR](#ocr)).
* **Mixed PDFs.** Native and OCR text are kept as separate streams in the
  internal model; they are never merged.
* **PDFs containing images.** Embedded document images are extracted and placed
  in the EPUB at their reconstructed positions.
* **Multi-column / layout-sensitive documents.** The two-column corpus fixture
  is analyzed with explicit left/right column reading order. Layout-sensitive
  results are **best-effort**: complex designs can still produce imperfect
  reading order, and the converter deliberately does not reproduce the PDF's
  visual layout exactly.

Not supported: **encrypted/damaged PDFs that cannot be opened**, PDFs with
zero pages, PDFs that contain neither text nor images, and fixed-layout output.
The application never fabricates page layout, never performs OCR recognition
*correction* (spell-checking-style cleanup), and does not emulate or verify
exact Kindle device rendering.

## Using the Windows application

The desktop workflow is:

```text
Select PDF → Analyze PDF → Configure output → Start conversion → Results
```

### 1. Select a PDF

* Click **Browse** next to *Input PDF* and choose a `.pdf` file (the picker
  filters to PDF files).
* Selecting a different PDF later invalidates the previous analysis: you must
  re-analyze the new input before converting.

### 2. Analyze the PDF

* Click **Analyze PDF**.
* The window shows a summary of the analysis: page count, document type
  (TEXT / SCANNED / MIXED), the numbers of text, scanned, and mixed pages, and
  whether **OCR is required**.
* If analysis fails (for example the file is not a valid PDF), a readable
  status message is shown and you can pick another file and retry.

### 3. Configure the conversion options

* **Output format.** Choose **EPUB** (default) or **AZW3**. Selecting AZW3
  produces **both** an EPUB and an AZW3 file: the EPUB is always generated, and
  AZW3 is converted from it.
* **Output directory.** Click the directory button and choose where the
  resulting files will be written. Nothing is created on disk just by selecting
  a directory.
* **Cover (optional).** Click the cover button and choose an image file
  (JPEG, PNG, GIF, or SVG). The image becomes the ebook's cover. You can clear
  the selection at any time. Covers are never guessed automatically.
* When an input has been analyzed and a valid output directory and format are
  selected, the status changes to **Ready for conversion**.

### 4. Start the conversion

* Click **Convert**. Conversion runs in the background (the window stays
  responsive). The progress bar is **indeterminate** while converting — the
  application reports discrete stages, not a fabricated percentage — and fills
  to completion on success.

### 5. Read the results

* On success, the results section shows the completion status, the output
  format (EPUB, or EPUB + AZW3), the exact **.epub** output path and (when AZW3
  was requested) the **.azw3** output path, and the **EPUB validation** result:
  *Valid* or *Invalid*, warning/error counts, and any validation issues.
* **Open EPUB**, **Open AZW3** (only when an AZW3 exists), and **Open Folder**
  open the produced files or their containing folder with the default program.
* A failed conversion shows the failure message as status text, returns the
  window to a usable state, and never presents a false success.

Output files are named after the input PDF (for example `book.pdf` produces
`book.epub` and, when requested, `book.azw3`) inside the directory you chose.

## OCR

**When it is used:** OCR (optical character recognition) is used only when a
page is classified as needing it — SCANNED and MIXED documents. Converting a
text-only PDF never touches OCR.

**Dependency:** OCR needs two parts:

1. The `ocr` Python extra (`pytesseract` + `Pillow`). This is bundled in the
   Windows package.
2. The **external Tesseract executable** (`tesseract.exe`), which this project
   **does not bundle or install**. You must install Tesseract separately
   (for example from the official Tesseract OCR installer at `UB-Mannheim`
   builds and others) and make sure its `tesseract` command is findable on
   your `PATH`. The English language data (`eng`) must be present.

**When OCR is unavailable:** if a SCANNED or MIXED document needs OCR but the
`tesseract` executable cannot be found, the conversion fails with a
*readable* message telling you Tesseract was not found on `PATH` (or pointing
at an explicit `tesseract_cmd`). The application never silently produces an
empty book and never crashes: it reports the failure. Text-only PDFs convert
normally even when Tesseract is absent.

**Limitations:** OCR output is consumed *as recognized* by a conservative
cleanup layer (normalization of line endings, whitespace, control characters).
There is **no recognition correction** — no spelling, dictionary, grammar, or
character-substitution correction. OCR quality therefore depends on the source
scan and on the installed Tesseract version/language data.

## AZW3 (optional)

**Dependency:** AZW3 conversion is performed by **Calibre's `ebook-convert`**
command. Calibre is an external system dependency installed by Calibre's own
installer — this project never bundles or installs it, and never implements
AZW3 itself.

**How to use it:** install Calibre (which places `ebook-convert` on the
`PATH`, typically under `C:\Program Files\Calibre2` on Windows), then select
**AZW3** as the output format in the application. The EPUB is always produced
first; the AZW3 is generated from that EPUB.

**When Calibre is unavailable:** if Calibre's `ebook-convert` cannot be found
or fails, the EPUB conversion is still attempted/generated where possible and
the AZW3 step reports a dedicated failure (`AZW3BackendUnavailableError` /
`AZW3ConversionFailedError` style messages in the application status). "Success"
for AZW3 means Calibre exited successfully and produced a non-empty file; it is
**not** a guarantee of Kindle marketplace acceptance or exact device rendering.

**Limitations:** AZW3 output is not byte-for-byte deterministic (Calibre stamps
its own metadata), and there is no AZW3 parser or Kindle-compatibility
validator.

## Troubleshooting

| Problem | Likely cause and what to do |
| --- | --- |
| "OCR ... Tesseract executable 'tesseract' was not found on PATH" (on a SCANNED/MIXED book) | Tesseract is not installed or not on `PATH`. Install Tesseract and make sure `tesseract --version` works, or pass an explicit `tesseract_cmd` when calling the library. Text-only PDFs do not need OCR. |
| AZW3 step fails / no AZW3 output | Calibre is not installed, or `ebook-convert` is not on the `PATH`. Install Calibre (Windows default `C:\Program Files\Calibre2`). The EPUB usually still exists. |
| "The selected input is not a valid PDF" / "could not be read" | The file is missing, not a PDF, or damaged/encrypted. Choose another file. |
| "output directory" errors / output cannot be written | The chosen output directory does not exist, is not writable, or cannot be created. Choose an existing writable directory. |
| Conversion fails with a specific message | The status line shows the application-level error verbatim (invalid request, validation failure, AZW3 failure, workspace failure). Fix the reported cause and retry; the window stays usable. |
| EPUB validation reports issues | The generated EPUB failed a structural validation check; the results section lists the specific issues. Note the converter's known limitations (see below). |
| Package/runtime issues (Windows) | Keep `KindleBookConverter.exe` together with its `_internal` folder. Run from the packaged folder — the packaged app resolves resources relative to its own runtime tree. Antivirus software can briefly delay first launch of a freshly copied package; retry if the first start is slow. |

## Known limitations

The following are documented, implemented limitations (also see the
[README](../README.md) and the quality/performance milestone sections):

* **EPUB output currently ships a single chapter file** with one navigation
  entry even when the PDF layer detects multiple chapters. The chapters are
  detected and indexed, but EPUB chapter *splitting* is not yet implemented.
* **Document titles are not detected as headings.** The heading detector is
  deliberately conservative: explicit `Chapter N:` headings are surfaced, but
  a book's opening title line is treated as body text.
* **No per-device or fixed-layout output.** The EPUB is a reflowable, generic
  ebook; there is no Kindle Previewer automation, KFX, fixed-layout EPUB, or
  per-model CSS.
* **Reading order on complex layouts is best-effort** and multi-column
  behavior is exercised for the two-column case; unusual typography can still
  produce imperfect results.
* **No drag-and-drop** input: files are chosen with the file picker.
* **No conversion cancellation**: a running conversion finishes rather than
  being stopped.

If you find a defect, prefer documenting it rather than silently changing
conversion behavior: the release process treats conversion-algorithm changes as
behavior changes that must pass the regression, quality, and performance
suites.