# Windows packaging (M6.5, release documentation M6.6)

This guide describes how the Kindle Book Converter produces its Windows
artifact and how that artifact is verified in an environment that never
imports the project's Python packages (the "clean-machine" check of M6.5).

It is the authoritative packaging reference for the release process: the
[release checklist](release-checklist.md) and the release-readiness tool
(`build_tools/release_check.py`) refer to this document and to the M6.5
build/verify commands below, not to any other recipe.

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
* `build_tools/package_distribution.py` -- packages the verified bundle into
  the reproducible Windows distribution archive (ZIP + SHA-256) and verifies
  it (see "Distribution archive" below).
* `build_tools/common.py` -- shared helpers (pyproject version, PE
  subsystem detection, dev-path scanning, distribution-archive naming).
* `kindle_converter/ui/smoke.py` -- the headless smoke harness that ships
  *inside* the packaged application.
* `tests/test_windows_packaging.py` + `tests/test_windows_distribution.py` -- 46 tests (marker `packaging`) covering
  the pure layout/parsing/dispatch logic offline, with no PyInstaller run.
* `dist/KindleBookConverter/` -- the resulting onedir bundle
  (`KindleBookConverter.exe` + `_internal/`).

## Prerequisites

* A Windows machine with **Python 3.12+** available and, for a reproducible
  reference build, Python 3.14 (the M6.5 build and verification runs used
  3.14.2 and PyInstaller 6.22.3).
* Network access during the **build only**, so a fresh `pip install` can fetch
  the declared dependencies into the project-dedicated build venv. Verification
  needs no network.
* Optional at build time, required for the corresponding smoke checks:
  Tesseract (scanned/mixed OCR) and Calibre (AZW3).

## Version handling

`pyproject.toml` (`[project] version`) is the **single authoritative version
source**. It flows into every versioned surface:

* the installed distribution metadata (and therefore
  `kindle_converter.__version__` / `_meta.application_version()`);
* the Windows version resource embedded in `KindleBookConverter.exe`
  (`build_tools/common.py` generates `build/version_info.txt` from the
  pyproject version; the file version is `major, minor, patch, 0`);
* the frozen `--sysinfo` output and the release documentation
  (`CHANGELOG.md` head entry).

`tests/test_windows_packaging.py` (class `TestVersionSingleSourcing`) and
`tests/test_release_docs.py` keep these consumers in sync with
`pyproject.toml`, so bumping the version without updating the package,
metadata, changelog, or documentation fails the suite. To release a new
version, edit `pyproject.toml`, add a `CHANGELOG.md` entry, and let the
checks confirm consistency.

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

## Packaged application structure

The produced artifact is a PyInstaller **onedir** bundle:

```text
dist/KindleBookConverter/
├── KindleBookConverter.exe   <-- the single GUI-subsystem executable
└── _internal/                <-- runtime tree (DLLs, PyInstaller PYZ, plugins,
                                  bundled *.dist-info metadata, resources)
```

Key structural facts:

* `KindleBookConverter.exe` is a **windowed (GUI subsystem)** executable; it
  has no console, accepts no stdin, and detaches stdout. Machine-readable
  diagnostics therefore go to `--report <path>` files and the process exit
  code is the primary signal.
* Pure-python distributions (`kindle_converter`, `ebooklib`, `pytesseract`)
  live inside the PyInstaller PYZ archive, not as loose directories. Their
  frozen presence is proven by the bundled `*.dist-info` metadata
  (`copy_metadata` ships all seven distributions).
* The `tesseract` executable and Calibre's `ebook-convert` are **not** in the
  bundle; they are discovered on `PATH` at runtime, exactly as in a source
  checkout (see below).
* Provenance metadata (`direct_url.json`, `INSTALLER`) is stripped from every
  bundled `*.dist-info`, so no development absolute path ships in the output.

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
| `smoke_scanned_book` | OCR conversion succeeds when Tesseract is present; graceful OCR unavailability (expected, exit 0) when Tesseract is absent |
| `smoke_mixed_text_image` | OCR conversion succeeds when Tesseract is present; graceful OCR unavailability (expected, exit 0) when Tesseract is absent |
| `smoke_azw3` | EPUB + AZW3 generated via the installed external Calibre |

The developer-oriented non-PyInstaller unit tests are:

```bash
PYTHONPATH=src venv\Scripts\python.exe -m pytest tests/test_windows_packaging.py -m packaging
```

## Distribution archive

The verified bundle (`dist/KindleBookConverter/`) is a *folder*. A GitHub
Release hosts single files, so the release artifact is a **ZIP archive** of the
bundle plus a **SHA-256 checksum** file, produced by
`build_tools/package_distribution.py`:

```bash
C:\Python314\python.exe build_tools/package_distribution.py
```

This writes, next to the bundle in `dist/`:

```text
KindleBookConverter-Windows-x64-0.1.0.zip
KindleBookConverter-Windows-x64-0.1.0.zip.sha256
```

The archive holds the bundle under its own root folder, so extracting it
reproduces exactly the documented layout:

```text
KindleBookConverter/
├── KindleBookConverter.exe
└── _internal/
```

The archive is **reproducible**: members are stored in sorted order with a
fixed timestamp and Unix file mode, so two archives produced from the same
bundle are byte-identical (the same offline-determinism philosophy as the rest
of the packaging flow). The name derives from the single version source
(`pyproject.toml`) and the `Windows-x64` platform tag (`build_tools/common.py`),
so bumping the version renames the artifact automatically.

By default the tool creates the archive and then verifies it
(`--no-verify` skips the verification, `--check` only verifies an existing
archive). The verification report is written to
`build/windows_distribution_verification.json` and checks:

| Check | What it proves |
| --- | --- |
| `archive_present` | the archive exists next to the bundle |
| `archive_integrity` | every member is readable and uncorrupted |
| `archive_layout` | `KindleBookConverter/KindleBookConverter.exe` + a non-empty `_internal/` are present |
| `gui_subsystem` | the executable *inside* the archive is a Windows GUI subsystem image (read from the ZIP via the shared PE parser) |
| `content_parity` | the archive contains exactly the bundle's files, no more, no less |
| `checksum` | the `.sha256` file matches the archive contents |

The distribution archive is **not** a second bundle verification — it proves
the *distribution* layer over the already-verified bundle. A release therefore
orders: **build → verify bundle → package distribution → verify distribution**
(see the [release checklist](release-checklist.md)).

## Verification levels

The release process distinguishes three levels of verification, and does
**not** claim a level that was not performed:

1. **Automated package verification** (always runnable in this repository):
   `build_tools/verify_windows_package.py` checks the artifact shape, GUI
   subsystem, version resource, runtime components, absence of dev artifacts
   and dev paths, and then drives the real executable through the
   `--sysinfo`/`--smoke`/`--smoke-check` subcommands. It is deterministic and
   offline for the static checks; the isolated smoke runs need the M6.1 corpus
   fixtures (committed) and optional Tesseract/Calibre for the OCR/AZW3 paths.
2. **Isolated local verification**: the verifier copies the bundle into
   `build/verification_work/bundle` and runs the artifact with `PYTHONPATH` /
   `PYTHONHOME` cleared and QPA forced offscreen. The frozen executable then
   runs with no access to the developer environment, the source tree, or the
   interpreter that built it. This is a strong isolation check, but it still
   runs on the **same machine** that built the artifact.
3. **Actual clean-machine verification**: running the artifact on a separate
   physical/virtual machine with a clean operating system and no development
   checkout. **This repository does not currently claim a clean-machine test
   on a separate physical machine.** The M6.5 "clean-machine" exercise is the
   isolated-bundle-copy procedure in (2). If an external clean environment is
   available, run the verifier's same recipe there (copy
   `dist/KindleBookConverter`, run `--sysinfo`/`--smoke-check`/`--smoke` on
   the M6.1 fixtures) before distribution.

## Optional external tools in verification

* **Tesseract (OCR).** Tesseract is **external** (never bundled). The expected
  behavior when it is absent is *graceful OCR unavailability*: a scanned/mixed
  conversion reports `expected: true` with exit 0, and the text-document smoke
  still converts and validates. When Tesseract **is** present, the same smoke
  should *succeed*; the verifier records that outcome honestly either way.
* **Calibre (AZW3).** Calibre is **external**. The `smoke_azw3` check produces
  EPUB + AZW3 when `ebook-convert` is present; when it is absent the check is
  reported as skipped, not as success, so a release machine without Calibre is
  clearly distinguished from one that exercised the AZW3 path.

## Expected artifact checks before distribution

Before distributing a release artifact, the release checklist
([release-checklist.md](release-checklist.md)) requires that the built bundle:

* exists at `dist/KindleBookConverter/` with `KindleBookConverter.exe`;
* is a GUI-subsystem image whose version resource matches the `pyproject.toml`
  version;
* ships all 12 required runtime components and **no** development artifacts or
  repository path text;
* reports the correct frozen identity (`--sysinfo` version, all five dependency
  versions resolved);
* launches headless (`--smoke-check`);
* converts and validates the TEXT `novel_basic` fixture (0 warnings / 0 errors);
* handles scanned/mixed fixtures gracefully without Tesseract and converts them
  where the environment provides OCR;
* produces AZW3 via the external Calibre when it is available.

`build_tools/release_check.py --package` runs the M6.5 verifier against the
existing artifact and reports pass/fail per check.

In addition to the bundle checks above, before distributing a release the
checklist requires the **distribution archive** gate:

* `dist/KindleBookConverter-Windows-x64-<version>.zip` exists next to the
  bundle, along with its `.sha256` checksum file;
* `build_tools/package_distribution.py --check` reports all checks `ok` (no
  `fail`); `build/windows_distribution_verification.json` has `"passed": true`;
* the archive name embeds the current `pyproject.toml` version.

The two release assets for a GitHub Release are therefore the ZIP and the
`.sha256` file together.

## Troubleshooting common packaging failures

| Symptom | Cause / fix |
| --- | --- |
| Build fails early on `pip install` | No network, or a dependency resolution conflict in the fresh build venv. The build venv is recreated by default (`--keep-venv` reuses it); a stale venv can hold incompatible versions — delete `build/windows_build_venv` and rebuild. |
| Frozen exe crashes with an import error at startup | The entry script must be plain and absolute-imported (`build_tools/windows_entry.py`). The frozen runtime cannot reproduce `python -m` relative-import semantics — do not point the spec at `ui/__main__.py`. |
| The exe cannot be launched by the verifier (`ERROR_ACCESS_DENIED`, Win32 5) | Antivirus real-time scanning of a freshly copied bundle. `run_app` retries process creation automatically; if it persists, exclude the work directory from real-time scanning. Also confirm the verifier is handed the `.exe` path, not the bundle folder path. |
| `--sysinfo`/`--smoke` produce no stdout | Expected: the windowed bootloader detaches stdout. Always pass `--report <path>` and read the report file / exit code. |
| `version_resource` check fails | `pyi-grab_version` writes `file_version_info.txt` into its *current working directory*, not stdout; the verifier runs it in a scratch directory. If the version is genuinely wrong, edit `pyproject.toml` (the single source) and rebuild. |
| `runtime_components` check fails | A bundled distribution is missing or its `*.dist-info` was not copied. The spec `copy_metadata` list must cover all seven distributions; pure-python packages are proven by `*.dist-info`, not loose directories. |
| `no_dev_paths` check fails | A development absolute path leaked into the bundle (e.g. build ran against a checkout whose path was embedded). Remove `build/dist`, rebuild, and confirm provenance stripping (`direct_url.json`, `INSTALLER`) reports the removed files. |
| `smoke_scanned_book` / `smoke_mixed_text_image` reported as fail | The scanned/mixed smoke must be **graceful** (exit 0, `expected: true`) when Tesseract is absent, and a real conversion when it is present. A non-zero exit or a report without `expected` indicates a genuine crash — inspect the smoke report JSON in `build/verification_work/`. |
| `smoke_azw3` reported as skip | Calibre's `ebook-convert` is not on `PATH`. Install Calibre and re-run the verifier to exercise the real AZW3 path; a skip is not a pass. |

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