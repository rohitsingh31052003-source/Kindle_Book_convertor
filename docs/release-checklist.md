# Release checklist

This is the authoritative release procedure for the Kindle Book Converter. It
splits the process into **required release gates** (must pass before any
artifact is distributed) and **optional / environment-dependent gates** (must
be executed where the environment supports them, and clearly reported when
they are not).

Tooling summary:

* Version single-sourcing and package configuration tests — `pytest -m packaging`
* Regression suite — `pytest -m regression`
* Quality suite — `pytest -m quality`
* Performance suite — `pytest -m performance`
* Documentation/release checks — `pytest -m docs`
* Release-readiness validator — `python build_tools/release_check.py`
* Windows build — `C:\Python314\python.exe build_tools/build_windows.py`
* Windows package verification — `C:\Python314\python.exe build_tools/verify_windows_package.py`

All releases start from the current `master` tip. The current version is
`0.1.0`; bumping the version means editing the **single source**
(`pyproject.toml` `[project] version`) and adding a `CHANGELOG.md` entry — see
[Windows packaging → Version handling](windows-packaging.md#version-handling).

## Required release gates

Every gate below must pass. Any failure blocks the release.

| # | Gate | Command / check | Exit criterion |
| --- | --- | --- | --- |
| 1 | **Repository is clean** | `git status --porcelain` | No unintended working-tree changes beyond the release commit; release/engineering artifacts in `build/`, `dist/`, `venv/` are untracked and ignored |
| 2 | **Version is intentionally selected** | `pyproject.toml` `[project] version` | Non-empty, valid version; documented in `CHANGELOG.md` head entry; matches `kindle_converter.__version__` and the packaged version resource |
| 3 | **Documentation is current** | `pytest -m docs`; `python build_tools/release_check.py` | All documentation/release checks pass with zero findings |
| 4 | **Full test suite passes** | `python -m pytest` | Full suite passes (any skip is expected and reported; any failure is understood and either fixed or explicitly documented as a pre-existing known issue with its cause) |
| 5 | **Regression suite passes** | `python -m pytest -m regression` | 253 tests pass |
| 6 | **Quality suite passes** | `python -m pytest -m quality` | 148 tests pass |
| 7 | **Performance suite passes** | `python -m pytest -m performance` | 12 tests pass (the suite compares against the committed baseline; a timing regression is a review finding) |
| 8 | **Windows package is built** | `C:\Python314\python.exe build_tools/build_windows.py` | `dist/KindleBookConverter/KindleBookConverter.exe` exists; build log shows provenance stripping |
| 9 | **Package verifier passes** | `C:\Python314\python.exe build_tools/verify_windows_package.py` | All checks `ok` (no `fail`); `build/windows_package_verification.json` has `"passed": true` |
| 10 | **Release-readiness validator passes** | `python build_tools/release_check.py --package` | Exit 0; no release findings; package verifier results re-run cleanly through the release tool |
| 11 | **Release artifact is inspected** | See [Windows packaging → Expected artifact checks](windows-packaging.md#expected-artifact-checks-before-distribution) | Executable + `_internal` present together, version resource correct, no dev artifacts/paths |
| 12 | **Version consistency is confirmed** | `pytest -m packaging` and `pytest -m docs` | Package single-sourcing tests and changelog/docs consistency checks pass |
| 13 | **Release notes are prepared** | `CHANGELOG.md` | A head entry for the released version documents the implemented capabilities accurately (no overstated claims) |

## Optional / environment-dependent gates

These gates depend on tools or environments that are not available on every
machine. Run them wherever the environment supports them, and record in the
release notes which ones were actually exercised. Do **not** present a gate as
passed when it was skipped.

| # | Gate | Command / condition | Exit criterion |
| --- | --- | --- | --- |
| 14 | **Native PDF smoke test** | `KindleBookConverter.exe --smoke <corpus>/pdfs/novel_basic.pdf <outdir>` (or via the verifier) | Conversion succeeds; the EPUB validates (0 warnings / 0 errors) |
| 15 | **Scanned/mixed behavior verified** | Verifier `smoke_scanned_book` / `smoke_mixed_text_image`, or manual `--smoke` on the scanned/mixed fixtures | Without Tesseract: **graceful** OCR unavailability (`expected: true`, exit 0). With Tesseract installed: a real OCR conversion succeeds. Either outcome must be the designed one for the environment, never a crash |
| 16 | **OCR path exercised with real Tesseract** | Install Tesseract and re-run `verify_windows_package.py` (or `--smoke` on `scanned_book.pdf`/`mixed_text_image.pdf`) | The scanned/mixed smokes succeed as real conversions, not graceful failures |
| 17 | **Optional Calibre/AZW3 path verified** | `KindleBookConverter.exe --smoke <novel_basic.pdf> <outdir> --azw3`, or verifier `smoke_azw3` | Calibre present: EPUB + AZW3 produced (`smoke_azw3` `ok`). Calibre absent: reported as **skipped**, not success |
| 18 | **Isolated local verification** | `build_tools/verify_windows_package.py` (already part of gate 9) | Frozen executable runs from `build/verification_work/bundle` with `PYTHONPATH`/`PYTHONHOME` cleared |
| 19 | **Actual clean-machine verification** | Run the packaged bundle on a separate clean Windows machine/VM with no development checkout | Executable launches; `--sysinfo`, `--smoke-check`, and `--smoke` behave as in gate 9. **Not currently claimed for this repository's own environment** — the M6.5/M6.6 verification is the isolated local procedure (gate 18); if you run a true clean machine, record that it was done |

## Notes on wording

* **Required vs optional.** Gates 1–13 must pass on every release. Gates 14–19
  are exercised where the environment provides the tools; a skipped
  environment-dependent gate is reported honestly (for example "Calibre absent,
  AZW3 path skipped").
* **No fabricated verification.** This repository does not claim a physical
  clean-machine test. The strongest available verification is the isolated
  local run of the frozen artifact (gate 18), which is a real execution of the
  packaged executable but on the same machine that built it.
* **Expensive suites.** The regression/quality/performance suites are run once
  for a release (gates 5–7). The full suite (gate 4) already includes them;
  do not re-run the full set merely because individual gates are listed.
* **Package verifier reuse.** The release tool (`release_check.py --package`)
  reuses `build_tools.verify_windows_package.verify` — there is exactly one
  package-verification implementation (M6.5), not a second one.