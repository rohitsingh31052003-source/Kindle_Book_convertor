"""Release-readiness validation tooling (M6.6).

A lightweight, deterministic release gate that reuses the existing M6.5
infrastructure instead of duplicating it:

* optional ``--package`` reuses :func:`build_tools.verify_windows_package.verify`
  (the one package-verification implementation) against the built artifact;
* ``--print-commands`` prints the authoritative test commands consumed by the
  release checklist;
* the repository checks (required files, single version source, changelog
  head, README roadmap state, README documentation links, no tracked release
  artifacts) are pure and offline -- no network, no timestamps, no machine
  paths in the report.

The tool is intentionally small: documentation drift checks live in
``tests/test_release_docs.py`` (``-m docs``) and the heavy package verification
is M6.5's ``verify_windows_package``, invoked here only when ``--package`` is
passed and the artifact exists. It never builds the package, never executes the
expensive suites itself, and never fabricates verification that did not run.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

try:  # allow ``python build_tools/release_check.py``
    from build_tools import common
except ImportError:  # pragma: no cover - bootstrap for direct execution
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from build_tools import common

#: Release-critical files that must exist at the repository root / in docs.
REQUIRED_FILES: tuple[str, ...] = (
    "README.md",
    "pyproject.toml",
    "CHANGELOG.md",
    "requirements.txt",
    "CONTRIBUTING.md",
    "SECURITY.md",
    "docs/user-guide.md",
    "docs/development.md",
    "docs/windows-packaging.md",
    "docs/release-checklist.md",
    "build_tools/build_windows.py",
    "build_tools/common.py",
    "build_tools/verify_windows_package.py",
    "build_tools/release_check.py",
    "tests/test_windows_packaging.py",
    "tests/test_release_docs.py",
)

#: Documentation the README must reference (prevents doc drift).
REFERENCED_BY_README: tuple[str, ...] = (
    "docs/user-guide.md",
    "docs/development.md",
    "docs/windows-packaging.md",
    "docs/release-checklist.md",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "SECURITY.md",
)

#: Development/release artifact directories that must never be tracked by git.
#: ``.gitignore`` covers them; this check refuses a release from a repository
#: where they were accidentally committed.
RELEASE_DIR_MARKERS: tuple[str, ...] = ("build/", "dist/", "venv/", "output/", "generated/")

#: (name, command) pairs -- the authoritative test commands of the release
#: checklist. The release tool prints them; the checklist executes them.
TEST_COMMANDS: tuple[tuple[str, str], ...] = (
    ("regression", "python -m pytest -m regression"),
    ("quality", "python -m pytest -m quality"),
    ("performance", "python -m pytest -m performance"),
    ("packaging", "python -m pytest -m packaging"),
    ("docs", "python -m pytest -m docs"),
    ("full", "python -m pytest"),
)

_VERSION_HEADING = re.compile(r"^##\s+\[([^\]]+)\]", re.MULTILINE)


# ---------------------------------------------------------------------------
# Repository-level checks (pure, offline, deterministic)
# ---------------------------------------------------------------------------


def missing_required_files(repo_root: Path) -> list[str]:
    """Release-critical files (repo-relative) that do not exist."""
    return [name for name in REQUIRED_FILES if not (repo_root / name).is_file()]


def readme_missing_references(repo_root: Path) -> list[str]:
    """Required documentation not referenced by the README."""
    readme = Path(repo_root / "README.md")
    if not readme.is_file():
        return list(REFERENCED_BY_README)
    text = readme.read_text(encoding="utf-8")
    return [name for name in REFERENCED_BY_README if name not in text]


def changelog_head_version(changelog_text: str) -> str | None:
    """The version of the first ``## [x.y.z]`` release heading, or ``None``."""
    match = _VERSION_HEADING.search(changelog_text)
    return match.group(1) if match else None


def changelog_head_mismatch(repo_root: Path, version: str) -> str | None:
    """A description of a changelog/pyproject version mismatch, or ``None``."""
    changelog = repo_root / "CHANGELOG.md"
    if not changelog.is_file():
        return "CHANGELOG.md is missing"
    head = changelog_head_version(changelog.read_text(encoding="utf-8"))
    if head is None:
        return "CHANGELOG.md has no `## [x.y.z]` release heading"
    if head != version:
        return f"CHANGELOG.md head version {head!r} != pyproject version {version!r}"
    return None


_MILESTONE_HEADING = re.compile(r"^### Milestone (\S+)", re.MULTILINE)


def _milestone_sections(readme_text: str) -> list[tuple[str, str]]:
    """Return ``(number, section-text)`` for each ``### Milestone N`` block.

    A section spans from its own heading to the next ``###`` or ``##`` heading,
    so a later milestone never bleeds its items into an earlier section's scan.
    """
    matches = list(_MILESTONE_HEADING.finditer(readme_text))
    sections: list[tuple[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        number = match.group(1)
        if index + 1 < len(matches):
            end = matches[index + 1].start()
        else:
            end = readme_text.find("\n## ", start)
            if end == -1:
                end = len(readme_text)
        sections.append((number, readme_text[start:end]))
    return sections


def _unchecked_items(section_text: str) -> list[str]:
    """Checklist lines in a milestone section that are still open."""
    return [
        line.strip()
        for line in section_text.splitlines()
        if line.strip().startswith(("* [ ]", "- [ ]"))
    ]


def readme_roadmap_issues(readme_text: str) -> list[str]:
    """README roadmap state checks: honest, monotonic completion claims.

    Milestone sections are ``### Milestone N`` blocks. The release gate is
    strict for the released milestone: Milestone 6 must be entirely checked
    and mark the Documentation (M6.6) and Release preparation entries complete
    and mention M6.6. Middle milestones must have no open items unless a line
    is explicitly tagged as future work (e.g. Milestone 5's drag-and-drop
    note). The highest numbered section is the current roadmap state and may
    legitimately mix completed items (``[x]`` M7.1) with future ones (``[ ]``
    M7.2 / M7.3) -- but no completed line may reference a milestone beyond the
    highest roadmap section, so the roadmap never overstates its own future.
    """
    issues: list[str] = []
    sections = _milestone_sections(readme_text)
    if not sections:
        issues.append("README roadmap has no `### Milestone` sections")
        return issues

    m6 = next(((number, section) for number, section in sections if number == "6"), None)
    if m6 is None:
        issues.append("README roadmap has no `### Milestone 6` section")
        return issues
    m6_section = m6[1]
    unchecked = _unchecked_items(m6_section)
    if unchecked:
        issues.append(
            "Milestone 6 roadmap still has unchecked items: " + "; ".join(unchecked)
        )
    if "[x] Documentation" not in m6_section:
        issues.append("Milestone 6 roadmap does not mark Documentation (M6.6) complete")
    if "[x] Release preparation" not in m6_section:
        issues.append("Milestone 6 roadmap does not mark Release preparation (M6.6) complete")
    if "M6.6" not in m6_section:
        issues.append("Milestone 6 roadmap does not mention M6.6")

    highest_major = max(_milestone_major(number) for number, _ in sections)
    last_section = sections[-1][1]

    for number, section_text in sections[:-1]:
        if number == m6[0]:
            continue
        open_items = [
            line
            for line in _unchecked_items(section_text)
            if "future work" not in line and "not implemented" not in line
        ]
        if open_items:
            issues.append(
                f"Milestone {number} roadmap has unchecked non-future items: "
                + "; ".join(open_items)
            )

    for line in last_section.splitlines():
        if "[x]" not in line:
            continue
        for number in re.findall(r"\bM(\d+(?:\.\d+)?)\b", line):
            if _milestone_major(number) > highest_major:
                issues.append(
                    f"roadmap marks a milestone beyond M{highest_major} complete: "
                    + line.strip()
                )
    return issues


def _milestone_major(number: str) -> int:
    """Major number of a milestone label like ``"1.0"`` or ``"7"``."""
    match = re.search(r"\d+", number)
    return int(match.group(0)) if match else 0


def tracked_release_dirs(repo_root: Path) -> tuple[str, list[str]]:
    """Return (status, repo-relative tracked release directories).

    ``status`` is ``"ok"`` when git is usable and nothing is tracked, ``"fail"``
    when git is usable and release directories are tracked, or ``"skip"`` when
    git is unavailable / ``git ls-files`` fails (the caller reports this as a
    soft check).
    """
    markers = sorted({marker.rstrip("/") for marker in RELEASE_DIR_MARKERS})
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "skip", []
    if result.returncode != 0:
        return "skip", []
    tracked = [
        line
        for line in result.stdout.splitlines()
        if line and any(line == marker or line.startswith(marker + "/") for marker in markers)
    ]
    return ("fail" if tracked else "ok"), tracked


# ---------------------------------------------------------------------------
# Release read across the repository
# ---------------------------------------------------------------------------


def run_release_checks(repo_root: Path | None = None) -> dict[str, Any]:
    """Run the offline repository checks and return a deterministic report.

    The report contains ``version``, per-check ``status`` (``ok``/``fail``/
    ``skip``) with ``detail``, an aggregated ``findings`` list, and ``passed``.
    No timestamps, machine paths, network access, or wall-clock values are
    included, so the output is deterministic for a given repository state.
    """
    root = Path(repo_root) if repo_root is not None else common.REPO_ROOT
    version = common.project_version()
    findings: list[str] = []

    checks: dict[str, dict[str, Any]] = {}

    missing = missing_required_files(root)
    checks["required_files"] = {
        "status": "ok" if not missing else "fail",
        "detail": "required release files present" if not missing else "missing: " + ", ".join(missing),
    }
    if missing:
        findings.append("required release files missing: " + ", ".join(missing))

    checks["version_available"] = {
        "status": "ok" if version else "fail",
        "detail": version or "empty",
    }
    if not version:
        findings.append("pyproject.toml [project] version is empty")

    readme = root / "README.md"
    readme_text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    references = readme_missing_references(root)
    checks["readme_references"] = {
        "status": "ok" if not references else "fail",
        "detail": "README references required documentation"
        if not references
        else "missing: " + ", ".join(references),
    }
    if references:
        findings.append("README does not reference: " + ", ".join(references))

    mismatch = changelog_head_mismatch(root, version)
    checks["changelog_consistency"] = {
        "status": "ok" if mismatch is None else "fail",
        "detail": mismatch or f"CHANGELOG head matches pyproject version {version}",
    }
    if mismatch:
        findings.append(mismatch)

    roadmap_issues = readme_roadmap_issues(readme_text) if readme_text else ["README is empty"]
    checks["readme_roadmap"] = {
        "status": "fail" if roadmap_issues else "ok",
        "detail": "M6 complete; M6.6 marked complete"
        if not roadmap_issues
        else "; ".join(roadmap_issues),
    }
    findings.extend(roadmap_issues)

    tracked_status, tracked = tracked_release_dirs(root)
    checks["tracked_release_dirs"] = {
        "status": tracked_status,
        "detail": "release directories untracked"
        if tracked_status == "ok"
        else ("tracked: " + ", ".join(tracked) if tracked else "git unavailable - skipped"),
    }
    if tracked_status == "fail":
        findings.append("release artifact directories are tracked by git: " + ", ".join(tracked))

    passed = version and not missing and not references and mismatch is None and not roadmap_issues and tracked_status == "ok"
    return {
        "version": version,
        "checks": checks,
        "findings": findings,
        "passed": bool(passed),
    }


def print_commands() -> None:
    """Print the authoritative test commands of the release checklist."""
    for name, command in TEST_COMMANDS:
        print(f"[release] {name:12s} {command}", flush=True)


# ---------------------------------------------------------------------------
# Packaging gate (reuses the M6.5 verifier)
# ---------------------------------------------------------------------------


def run_package_verification(
    bundle: Path,
    *,
    build_venv: Path,
    work_dir: Path,
    corpus_dir: Path,
) -> dict[str, Any]:
    """Run the M6.5 package verifier and summarize it for the release report."""
    from build_tools.verify_windows_package import verify  # deferred import

    report = verify(
        bundle,
        build_venv=build_venv,
        work_dir=work_dir,
        corpus_dir=corpus_dir,
        expected_version=common.project_version(),
    )
    return {
        "artifact": str(bundle),
        "passed": report.passed,
        "status": "ok" if report.passed else "fail",
        "checks": [
            {"name": check.name, "status": check.status, "detail": check.detail}
            for check in report.checks
        ],
        "summary": report.summary,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run release checks; ``--package`` additionally runs the M6.5 verifier."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    want_package = "--package" in arguments
    want_commands = "--print-commands" in arguments
    unknown = [arg for arg in arguments if arg not in ("--package", "--print-commands")]
    if unknown:
        print(f"[release] unknown argument: {unknown[0]}", flush=True)
        return 2

    report = run_release_checks()
    for name, check in report["checks"].items():
        status = check["status"]
        print(f"[release] {status.upper():4s} {name}: {check['detail']}", flush=True)

    if want_commands:
        print_commands()

    package_result: dict[str, Any] | None = None
    if want_package:
        bundle = common.BUNDLE_DIR
        if not bundle.is_dir():
            print("[release] fail  package_verification: artifact missing (build it first)", flush=True)
            return 1
        package_result = run_package_verification(
            bundle,
            build_venv=common.BUILD_VENV_DIR,
            work_dir=common.BUILD_DIR / "verification_work",
            corpus_dir=common.CORPUS_DIR,
        )
        for check in package_result["checks"]:
            print(
                f"[release] {check['status'].upper():4s} package/{check['name']}: {check['detail']}",
                flush=True,
            )
        if not package_result["passed"]:
            return 1

    if not report["passed"]:
        print("[release] FAIL: release findings present", flush=True)
        return 1
    print("[release] PASS: repository is release-ready", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())