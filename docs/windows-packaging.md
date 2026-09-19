# Windows packaging (M6.5)

This guide describes how the Kindle Book Converter produces its Windows
artifact and how that artifact is verified in an environment that never
imports the project's Python packages (the "clean-machine" check of M6.5).

The goal of the milestone was not just "make an `.exe`". It was to:

1. build a **reproducible** Windows bundle from a clean, project-dedicated
   virtual environment;
2. embed the real application version and runtime dependencies so the frozen
   application reports versions through `importlib.metadata` exactly like an
   installed package;
3. leave OCR (Tesseract) and AZW3 (Calibre) as **external** tools, exactly as
   they are in a source checkout;
4. verify the **real artifact** by launching it inside an isolated copy of the
   bundle and driving real conversions over the M6.1 corpus.

Everything below was executed on Windows with Python 3.14.2 and PyInstaller
6.22.3.

## Layout

* `build_tools/build_windows.py` -- builds the bundle from a fresh venv;
  the actual PyInstaller freeze graph lives in
  `build_tools/KindleBookConverter.spec`, and the windowed entry point is
  `build_tools/windows_entry.py`.
* `build_tools/verify_windows_package.py` -- verifies the built artifact in
  isolation (see "Verification" below).
* `build_tools/common.py` -- shared helpers (pyproject version, PE
  subsystem detection, dev-path scanning).
* `kindle_converter/ui/smoke.py` -- the headless smoke harness that ships
  *inside* the packaged application.
* `tests/test_windows_packaging.py` -- 24 tests (marker `packaging`) covering
  the pure layout/parsing/dispatch logic offline, with no PyInstaller run.
* `dist/KindleBookConverter/` -- the resulting onedir bundle
  (`KindleBookConverter.exe` + `_internal/`).

## Build

From the repository root:

```bash
C:\Python314\python.exe build_tools/build_windows.py
```

The build tool:

1. creates `build/windows_build_venv` (a venv dedicated to packaging) unless
   `--keep-venv` is passed, and installs the project (`.[ui,ocr]` extra) plus
   `pyinstaller` into it;
2. generates `build/version_info.txt` from `pyproject.toml` in the canonical
   PyInstaller version-resource format;
3. runs PyInstaller with `dist` and `build/pyinstaller` as the output/work
   directories so every artifact path stays inside the repo;
4. strips provenance from the bundled `*.dist-info` directories:
   `INSTALLER` and `direct_url.json` are removed so no development absolute
   path leaks into the artifact.

The spec makes the freeze deterministic and honest:

* the entry point is `build_tools/windows_entry.py` (a plain absolute-import
  script) -- not `ui/__main__.py` -- because the frozen runtime cannot
  reproduce the relative-import semantics of `python -m`;
* `copy_metadata` is used for `kindle-converter`, `PySide6`, `PyMuPDF`,
  `ebooklib`, `lxml`, `Pillow`, and `pytesseract` so both the frozen version
  lookup (`importlib.metadata`) and the verifier's filesystem-level component
  checks work;
* `console=False` produces a single GUI-subsystem executable: the windowed
  bootloader detaches stdout, so the verification subcommands write
  machine-readable reports to `--report <path>` and the process exit code is
  the primary signal.

## The smoke harness (`--smoke`, `--smoke-check`, `--sysinfo`)

`kindle_converter/ui/smoke.py` ships inside the artifact and exposes three
self-contained subcommands:

```text
KindleBookConverter.exe --smoke-check
KindleBookConverter.exe --smoke <input.pdf> <output-dir> [--azw3] [--expect-failure] [--report P]
KindleBookConverter.exe --sysinfo [--report P]
```

`--sysinfo` reports the frozen identity (frozen flag, version, `bundle_root`),
the runtime layout, dependency versions, and the external-tool availability
(Tesseract/Calibre) so a verifier can distinguish *application behavior* from
*tool availability*. `--smoke` drives the genuine `MainWindow` workflow
headlessly (input select → analyze → output select → background conversion)
and structurally validates the produced EPUB through the existing validator.

One outcome is deliberately *expected* (reported `expected: true`, exit 0):
OCR unavailability. With `tesseract` missing from `PATH`, a scanned or mixed
document fails with the TesseractEngine "not found on PATH" error. That is the
designed OCR-external behavior of M6.1, so it is not treated as a crash. The
`--expect-failure` flag forces expected-failure reporting for any error and
exists for one-off manual checks.

## Verification

```bash
C:\Python314\python.exe build_tools/verify_windows_package.py
```

The verifier copies the bundle into `build/verification_work/bundle`, runs the
isolated `KindleBookConverter.exe`, and checks:

| Check | What it proves |
| --- | --- |
| `artifact_present` | bundle and executable exist, size recorded |
| `gui_subsystem` | the PE subsystem is Windows GUI (2), not console |
| `version_resource` | `pyi-grab_version` output carries `0.1.0` (`filevers`) |
| `runtime_components` | the 12 required files/dist-infos are present |
| `no_dev_artifacts` | no dev markers (`tests`, `pytest`, `pyproject.toml`, `.gitignore`, `AGENTS.md`, `docs`, `__pycache__`, `site-packages`) |
| `no_dev_paths` | no `C:\Kindle Book Convertor\...` text inside any bundle file |
| `isolated_sysinfo` | frozen identity + version + all 5 dependency versions resolve |
| `isolated_launch` | `--smoke-check` creates the real window headless |
| `smoke_novel_basic` | text PDF → EPUB, structurally validated (0w/0e) |
| `smoke_scanned_book` | graceful OCR unavailability (expected, exit 0) |
| `smoke_mixed_text_image` | graceful OCR unavailability (expected, exit 0) |
| `smoke_azw3` | EPUB + AZW3 generated via the installed external Calibre |

The developer-oriented non-PyInstaller unit tests are:

```bash
PYTHONPATH=src venv\Scripts\python.exe -m pytest tests/test_windows_packaging.py -m packaging
```

## Notes and pitfalls

* The tooling directory is named `build_tools`, deliberately **not**
  `packaging`, to avoid shadowing the third-party `packaging` distribution
  when the repo root is on `sys.path`.
* A GUI (windowed) PyInstaller executable accepts no stdin and detaches
  stdout; always use `--report <path>` for machine-readable output.
* `pyi-grab_version` writes `file_version_info.txt` into its *current working
  directory* rather than stdout; the verifier runs it in a scratch directory.
* Pure-python distributions (`kindle_converter`, `ebooklib`, `pytesseract`)
  live inside the PyInstaller PYZ archive, not as loose directories; their
  frozen presence is proven by the bundled `*.dist-info` metadata.
* Executing a freshly copied bundle can transiently fail with
  `ERROR_ACCESS_DENIED` from antivirus real-time scanning; `run_app` retries
  process creation a few times before giving up.