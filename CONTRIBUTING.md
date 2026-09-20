# Contributing to Kindle Book Converter

Thanks for considering a contribution. This project is a local desktop
application that converts PDFs into reflowable, Kindle-friendly EPUB (and
optionally AZW3) ebooks. This guide is intentionally short; the
[development guide](docs/development.md) is the technical source of truth for
environment setup, project structure, architecture, and conventions.

## Code of conduct

There is no separate code of conduct. Please keep interactions constructive and
respectful, and keep the project's scope in mind (see "Scope" below).

## Reporting bugs and proposing features

- **Bugs** — open an issue with the [bug report template](.github/ISSUE_TEMPLATE/bug_report.yml).
- **Feature requests** — open an issue with the [feature request template](.github/ISSUE_TEMPLATE/feature_request.yml).

Do **not** attach private PDFs or personal documents to issues. If a
reproduction file is needed, use a small non-sensitive placeholder document.

## Development setup

See the [development guide](docs/development.md#environment-setup) for the
full setup. In short:

```bash
git clone https://github.com/rohitsingh31052003-source/Kindle_Book_convertor
cd "Kindle Book Convertor"
python -m venv .venv
.venv\Scripts\activate        # Windows; macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev,ui,ocr]"
```

`dev` provides pytest; `ui` provides the desktop application dependencies;
`ocr` provides the Tesseract Python wrappers (the Tesseract executable itself
is an external dependency).

## Running the tests

The test suite is the project's quality gate — there is no separate linter or
type-checker configured.

```bash
python -m pytest                 # full suite
python -m pytest -m regression   # corpus regression
python -m pytest -m quality      # conversion-quality measurement
python -m pytest -m performance  # performance framework
python -m pytest -m packaging    # Windows packaging configuration
python -m pytest -m docs         # documentation / release-readiness checks
```

The authoritative commands are also printed by the release tool:

```bash
python build_tools/release_check.py --print-commands
```

Tesseract-, Calibre-, and GUI-dependent suites **skip cleanly** when the
external tools or the `ui` extra are unavailable; making a suite pass by
weakening or skipping it is not a valid substitute.

## Project structure

```text
src/kindle_converter/   application source (pdf/, document/, epub/, application/, ui/)
tests/                  test suites incl. fixtures/corpus, regression/, quality/, performance/
docs/                   user guide, development guide, packaging, release checklist
build_tools/            packaging, distribution, and release-check tooling
.github/                issue + pull-request templates
CHANGELOG.md            release notes (single-source version is pyproject.toml)
```

The [development guide](docs/development.md#project-structure) describes each
area in more detail.

## Pull requests

Before opening a pull request:

- Keep the change focused. A PR should do one thing and should not contain
  unrelated modifications.
- Preserve existing behavior. Conversion-algorithm changes are treated as
  behavior changes: they must pass the regression, quality, and performance
  suites, and the baselines under `tests/fixtures/` are **never** overwritten
  silently (regeneration is explicit and deliberate). Prefer documenting a
  defect over silently changing conversion behavior.
- Run the relevant test suites and report the results in the PR description.
- Update documentation when the change affects user-visible or
  contributor-facing behavior.
- Do not commit generated artifacts: `build/`, `dist/`, virtual environments,
  and test reports must stay untracked.
- Do not add secrets, credentials, or private/personal information.

Use the template at
[.github/PULL_REQUEST_TEMPLATE.md](.github/PULL_REQUEST_TEMPLATE.md).

## Documentation

User-facing, developer-facing, packaging, and release documentation live under
[`docs/`](docs/). Update the relevant document alongside a behavior change and
link the change to the release checklist (`docs/release-checklist.md`) when it
affects the release process.

## Scope

- This is a **local desktop application**. The hosted/web version is deferred
  and is not current work.
- Keep the architecture: a thin UI over the application layer
  (`kindle_converter.application`) over the PDF/document/EPUB layers. The UI
  never reaches into conversion internals.
- Respect the packaging workflow in `build_tools/` and its documentation —
  do not introduce a second build recipe.