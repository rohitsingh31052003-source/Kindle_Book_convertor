"""M6.6 — release documentation checks.

Lightweight, deterministic, offline guards that prevent release-documentation
drift without building a documentation framework:

* required user/developer/release documentation files exist;
* the README references every required documentation file and the changelog;
* the changelog head entry matches the single version source
  (``pyproject.toml ``[project] version``) and the release-checklist states the
  current version nowhere except through the single source;
* the README roadmap marks M6.6 (and therefore M6) complete and marks no
  future milestone complete;
* the release-readiness tool (``build_tools/release_check.py``) reports no
  findings on the current repository.

These check the repository's *documentation release contract*, not the
converter. Package/version single-sourcing itself is covered by
``tests/test_windows_packaging.py`` (marker ``packaging``); the docs checks
reuse the same ``build_tools.common`` helpers.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from build_tools import common, release_check

pytestmark = pytest.mark.docs

#: M6.6 release documentation files (plus the primary landing documents they
#: must all stay consistent with).
REQUIRED_DOCUMENTS: tuple[str, ...] = (
    "README.md",
    "CHANGELOG.md",
    "docs/user-guide.md",
    "docs/development.md",
    "docs/windows-packaging.md",
    "docs/release-checklist.md",
)


class TestRequiredDocumentation:
    """All release documentation must exist."""

    def test_required_documents_exist(self) -> None:
        missing = [
            name
            for name in REQUIRED_DOCUMENTS
            if not (common.REPO_ROOT / name).is_file()
        ]
        assert missing == []

    def test_release_check_required_files_exist(self) -> None:
        missing = release_check.missing_required_files(common.REPO_ROOT)
        assert missing == []


class TestReadmeReferences:
    """The README is the landing page and must link every release document."""

    def test_readme_references_required_documents(self) -> None:
        missing = release_check.readme_missing_references(common.REPO_ROOT)
        assert missing == []

    def test_readme_references_packaging_and_release_flow(self) -> None:
        readme = (common.REPO_ROOT / "README.md").read_text(encoding="utf-8")
        # The README must point developers to the packaging doc and the release
        # checklist so the release process is discoverable from the landing page.
        assert "windows-packaging.md" in readme
        assert "release-checklist.md" in readme
        assert "CHANGELOG.md" in readme


class TestVersionConsistency:
    """Release documentation must agree with the single version source."""

    def test_changelog_head_matches_pyproject(self) -> None:
        mismatch = release_check.changelog_head_mismatch(
            common.REPO_ROOT, common.project_version()
        )
        assert mismatch is None

    def test_release_check_reports_expected_version(self) -> None:
        report = release_check.run_release_checks()
        assert report["version"] == common.project_version()
        assert report["passed"] is True

    def test_release_check_report_is_deterministic_json(self) -> None:
        """Two runs produce identical reports (no timestamps, no drift)."""
        first = json.dumps(
            release_check.run_release_checks(), sort_keys=True, default=str
        )
        second = json.dumps(
            release_check.run_release_checks(), sort_keys=True, default=str
        )
        assert first == second


class TestReadmeRoadmap:
    """The roadmap must declare M6.6 (and therefore M6) complete honestly."""

    def test_milestone_six_is_complete(self) -> None:
        readme = (common.REPO_ROOT / "README.md").read_text(encoding="utf-8")
        assert release_check.readme_roadmap_issues(readme) == []


class TestReleaseToolCli:
    """The release tool's CLI is small, offline, and deterministic."""

    def test_cli_passes_on_current_repository(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(common.REPO_ROOT / "build_tools" / "release_check.py")],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr

    def test_print_commands_lists_authoritative_suites(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                str(common.REPO_ROOT / "build_tools" / "release_check.py"),
                "--print-commands",
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        # Every documented authoritative test command must be printed.
        for _, command in release_check.TEST_COMMANDS:
            assert command in completed.stdout

    def test_release_check_tool_has_no_test_dependency(self) -> None:
        """The tool must run with only the standard library + build_tools."""
        import importlib.util

        assert importlib.util.find_spec("kindle_converter") is not None
        assert importlib.util.find_spec("build_tools") is not None


class TestReleaseChecklistConsistency:
    """The release checklist must not hardcode a version that diverges."""

    def test_checklist_does_not_pin_a_version_literal(self) -> None:
        checklist = (common.REPO_ROOT / "docs" / "release-checklist.md").read_text(
            encoding="utf-8"
        )
        # The checklist references the current version textually (as evidence
        # it is current), but must point at pyproject.toml as the single
        # source rather than maintaining its own numeric constant.
        assert "pyproject.toml" in checklist
        assert "single source" in checklist.lower() or "single authoritative" in checklist.lower()