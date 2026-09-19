"""Windows package verification tooling (M6.5).

:func:`verify` checks a built ``dist/KindleBookConverter`` bundle from the
outside -- exactly the kind of check a machine without the development
environment would run -- and produces an ordered list of check results.

Static checks (no execution): artifact presence, GUI subsystem, embedded
version resource, required runtime components (Python runtime DLL, Qt core +
platform/imageformats plugins, PyMuPDF native library, Pillow imaging natives,
ebooklib/lxml, pytesseract, the packaged ``kindle_converter`` distribution and
its metadata), and the absence of development-time artifacts (tests, pytest,
``pyproject.toml``, repository/build-venv absolute paths anywhere in bundled
text/executable files).

Isolated run checks (execution): the bundle is copied to a scratch directory
and executed through its real ``--sysinfo``/``--smoke``/``--smoke-check``
subcommands with ``PYTHONPATH``/``PYTHONHOME`` cleared and QPA forced
offscreen, proving the artifact runs independently of the repository and the
developer environment. Smoke conversions use the M6.1 corpus fixtures; the
OCR-gated documents are verified for *graceful* unavailability behavior when
``tesseract`` is not on ``PATH`` (the exact dependency contract of §10), and
the AZW3 conversion is exercised when Calibre's ``ebook-convert`` is present.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

try:  # allow ``python build_tools/verify_windows_package.py``
    from build_tools import common
except ImportError:  # pragma: no cover - bootstrap for direct execution
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from build_tools import common


@dataclass(frozen=True)
class CheckResult:
    """One verification check: ``status`` is ``ok``, ``fail``, or ``skip``."""

    name: str
    status: str
    detail: str = ""


@dataclass
class VerificationReport:
    """Aggregate of :func:`verify` results plus environment context."""

    artifact: Path
    checks: list[CheckResult] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "artifact": str(self.artifact),
            "passed": self.passed,
            "summary": self.summary,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Pure layout helpers
# ---------------------------------------------------------------------------

#: (label, relative glob, scope) -- scope ``top`` searches the bundle root,
#: scope ``any`` searches the whole bundle tree. Pure-python distributions
#: (``kindle_converter``, ``ebooklib``, ``pytesseract``) are stored inside the
#: PyInstaller PYZ archive rather than as loose files, so their frozen
#: presence is proven by the copied ``*.dist-info`` metadata instead.
RUNTIME_COMPONENTS: tuple[tuple[str, str, str], ...] = (
    ("python runtime DLL", "python3*.dll", "any"),
    ("PySide6 Qt core library", "PySide6", "any"),
    ("PySide6 Qt6Core DLL", "PySide6/Qt6Core.dll", "any"),
    ("Qt windows platform plugin", "PySide6/plugins/platforms/qwindows.dll", "any"),
    ("Qt imageformats plugins", "PySide6/plugins/imageformats/*.dll", "any"),
    ("kindle_converter distribution metadata", "kindle_converter-*.dist-info", "any"),
    ("PyMuPDF package", "pymupdf", "any"),
    ("PyMuPDF native library", "pymupdf/*.pyd", "any"),
    ("ebooklib distribution metadata", "ebooklib-*.dist-info", "any"),
    ("lxml native binding", "lxml/etree*.pyd", "any"),
    ("Pillow native binding", "PIL/_imaging*.pyd", "any"),
    ("pytesseract distribution metadata", "pytesseract-*.dist-info", "any"),
)

#: Development artifacts that must not appear anywhere in the bundle. The
#: README.md / LICENSE files inside the shipped ``*.dist-info`` directories
#: are legitimate metadata, so repo-level markers are listed instead.
FORBIDDEN_ITEMS: tuple[str, ...] = (
    "tests",
    "pytest",
    "pyproject.toml",
    "site-packages",
    "__pycache__",
    ".gitignore",
    "AGENTS.md",
    "docs",
)

#: M6.1 corpus fixtures exercised by the isolated-smoke verification.
SMOKE_DOCUMENTS: tuple[tuple[str, str, bool], ...] = (
    ("novel_basic", "TEXT", False),
    ("scanned_book", "SCANNED", True),
    ("mixed_text_image", "MIXED", True),
)


def _has_item(bundle: Path, pattern: str, scope: str) -> bool:
    if scope == "top":
        return any(bundle.glob(pattern))
    return any(bundle.rglob(pattern))


def missing_runtime_components(bundle: Path) -> list[tuple[str, str]]:
    """Labels of the required runtime components missing from the bundle."""
    return [
        (label, pattern)
        for label, pattern, scope in RUNTIME_COMPONENTS
        if not _has_item(bundle, pattern, scope)
    ]


def forbidden_items_present(bundle: Path) -> list[str]:
    """Dev artifacts (by relative path) found inside the bundle."""
    found: list[str] = []
    for item in FORBIDDEN_ITEMS:
        for path in bundle.rglob(item):
            suffix = path.suffix
            if suffix in (".py", ".pth"):
                continue  # a python module legitimately named like a fixture module? unlikely; keep diagnostics clean
            found.append(str(path.relative_to(bundle)))
            break
    return found


def dev_path_leaks(bundle: Path, needles: list[str]) -> dict[str, list[str]]:
    """Bundle files (relative paths) containing any development path text."""
    return common.scan_for_strings(bundle, needles)


def parse_version_resource_text(text: str) -> dict[str, str]:
    """Parse ``pyi-grab_version`` output into a dictionary of string values.

    Returns the ``FileVersion`` / ``ProductVersion`` strings and the
    ``filevers`` / ``prodvers`` tuples when present (``""`` otherwise).
    """
    result: dict[str, str] = {}
    string_struct = re.compile(r"StringStruct\('(\w+)', '([^']*)'\)")
    for line in text.splitlines():
        stripped = line.strip()
        for key in ("filevers", "prodvers"):
            if stripped.startswith(key + "="):
                result[key] = stripped[len(key) + 1 :].strip(",")
        for name, value in string_struct.findall(stripped):
            if name in ("FileVersion", "ProductVersion"):
                result[name] = value
    return result


# ---------------------------------------------------------------------------
# Isolation helpers
# ---------------------------------------------------------------------------


def _isolated_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env.pop("PYTHONPATH", None)
    env["QT_QPA_PLATFORM"] = "offscreen"
    return env


def run_app(
    executable: Path,
    args: Sequence[str],
    *,
    cwd: Path,
    report_path: Path,
    timeout: float = 420.0,
) -> tuple[int, dict[str, Any] | None]:
    """Run the bundled executable; return ``(exit_code, parsed_report)``.

    The windowed executable detaches stdout, so the subcommand writes its
    report to ``report_path``; the exit code is the primary signal.

    ``CreateProcess`` can transiently fail with ``ERROR_ACCESS_DENIED``
    (Win32 5) when real-time antivirus is still scanning the just-copied
    bundle, so process creation is retried a few times before giving up.
    """
    if report_path.exists():
        report_path.unlink()

    def attempt() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [str(executable), *args, "--report", str(report_path)],
            cwd=str(cwd),
            env=_isolated_env(),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    last_error: OSError | None = None
    for retry in range(1, 7):
        try:
            completed: subprocess.CompletedProcess[str] = attempt()
            break
        except OSError as exc:
            last_error = exc
            if exc.winerror != 5:
                raise
            time.sleep(1.5 * retry)
    else:
        assert last_error is not None
        raise last_error

    report: dict[str, Any] | None = None
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = None
    return int(completed.returncode), report


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify(
    bundle: Path,
    *,
    build_venv: Path,
    work_dir: Path,
    corpus_dir: Path,
    expected_version: str,
    run_smoke: bool = True,
    keep_work: bool = True,
) -> VerificationReport:
    """Verify the frozen bundle and return the aggregate report."""
    checks: list[CheckResult] = []
    summary: dict[str, Any] = {
        "expected_version": expected_version,
        "source_corpus": str(corpus_dir),
    }

    artifact_ok = _check_artifact(bundle, checks, summary)
    if not artifact_ok:
        return VerificationReport(bundle, checks, summary)

    _check_version_resource(bundle, build_venv, expected_version, checks)

    missing = missing_runtime_components(bundle)
    if missing:
        checks.append(
            CheckResult(
                "runtime_components",
                "fail",
                "missing: " + ", ".join(f"{label} ({pattern})" for label, pattern in missing),
            )
        )
    else:
        checks.append(CheckResult("runtime_components", "ok", f"{len(RUNTIME_COMPONENTS)} components present"))

    forbidden = forbidden_items_present(bundle)
    if forbidden:
        checks.append(CheckResult("no_dev_artifacts", "fail", "found: " + ", ".join(forbidden)))
    else:
        checks.append(CheckResult("no_dev_artifacts", "ok", "no development artifacts"))

    leaks = dev_path_leaks(bundle, [str(common.REPO_ROOT), str(common.BUILD_VENV_DIR)])
    if leaks:
        rendered = "; ".join(f"{item}->{','.join(v)}" for item, v in sorted(leaks.items()))
        checks.append(CheckResult("no_dev_paths", "fail", rendered))
    else:
        checks.append(CheckResult("no_dev_paths", "ok", "no development paths embedded"))

    if run_smoke:
        _check_isolated_run(
            bundle, build_venv, work_dir, corpus_dir, expected_version, keep_work, checks, summary
        )
    else:
        checks.append(CheckResult("isolated_run", "skip", "disabled by --no-smoke"))

    return VerificationReport(bundle, checks, summary)


def _check_artifact(bundle: Path, checks: list[CheckResult], summary: dict[str, Any]) -> bool:
    executable = bundle / f"{common.BUNDLE_NAME}.exe"
    if not bundle.is_dir():
        checks.append(CheckResult("artifact_present", "fail", f"bundle missing: {bundle}"))
        return False
    if not executable.is_file():
        checks.append(CheckResult("artifact_present", "fail", f"executable missing: {executable}"))
        return False
    size_bytes = sum(p.stat().st_size for p in common.iter_bundle_files(bundle))
    summary["artifact_size_bytes"] = size_bytes
    summary["executable"] = str(executable)
    checks.append(CheckResult("artifact_present", "ok", f"{bundle} ({size_bytes} bytes)"))

    if common.is_gui_executable(executable):
        checks.append(CheckResult("gui_subsystem", "ok", "Windows GUI subsystem (2)"))
    else:
        subsystem = common.pe_subsystem(executable)
        checks.append(
            CheckResult("gui_subsystem", "fail", f"expected GUI subsystem 2, found {subsystem}")
        )
        return False
    return True


def _check_version_resource(
    bundle: Path, build_venv: Path, expected_version: str, checks: list[CheckResult]
) -> None:
    executable = bundle / f"{common.BUNDLE_NAME}.exe"
    grabber = build_venv / "Scripts" / "pyi-grab_version.exe"
    if os.name != "nt":
        grabber = build_venv / "bin" / "pyi-grab_version"
    if not grabber.is_file():
        checks.append(CheckResult("version_resource", "skip", f"pyi-grab_version not found: {grabber}"))
        return
    with tempfile.TemporaryDirectory() as scratch_text:
        # ``pyi-grab_version`` writes ``file_version_info.txt`` into its cwd.
        completed = subprocess.run(
            [str(grabber), str(executable)],
            cwd=scratch_text,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        grabbed = Path(scratch_text) / "file_version_info.txt"
        source = grabbed.read_text(encoding="utf-8") if grabbed.is_file() else completed.stdout + completed.stderr
    values = parse_version_resource_text(source)
    expected_tuple = "(" + ", ".join(str(n) for n in common._version_tuple(expected_version)) + ")"
    ok = (
        "filevers" in values
        and expected_tuple in values["filevers"]
        and values.get("FileVersion", "") == ", ".join(str(n) for n in common._version_tuple(expected_version))
    )
    if ok:
        checks.append(
            CheckResult("version_resource", "ok", f"FileVersion {values.get('FileVersion')} matches {expected_version}")
        )
    else:
        checks.append(
            CheckResult("version_resource", "fail", f"expected {expected_version}; parsed {values}")
        )


def _check_isolated_run(
    bundle: Path,
    build_venv: Path,
    work_dir: Path,
    corpus_dir: Path,
    expected_version: str,
    keep_work: bool,
    checks: list[CheckResult],
    summary: dict[str, Any],
) -> None:
    scratch = work_dir / "bundle"
    corpus_copy = work_dir / "corpus"
    if keep_work and scratch.is_dir():
        shutil.rmtree(scratch)
    corpus_copy.mkdir(parents=True, exist_ok=True)
    scratch = shutil.copytree(bundle, scratch, dirs_exist_ok=True)
    scratch_executable = scratch / f"{common.BUNDLE_NAME}.exe"
    for doc_id in ("novel_basic", "scanned_book", "mixed_text_image"):
        source = corpus_dir / "pdfs" / f"{doc_id}.pdf"
        if source.is_file():
            shutil.copy2(source, corpus_copy / source.name)
    summary["isolated_bundle"] = str(scratch)
    summary["isolated_corpus"] = str(corpus_copy)

    # --sysinfo
    code, sysinfo = run_app(scratch_executable, ["--sysinfo"], cwd=scratch, report_path=work_dir / "sysinfo.json")
    if code != 0 or sysinfo is None:
        checks.append(CheckResult("isolated_sysinfo", "fail", f"exit {code}, report {sysinfo is not None}"))
        return
    issues: list[str] = []
    if not sysinfo.get("frozen"):
        issues.append("frozen flag is false")
    if sysinfo.get("application", {}).get("version") != expected_version:
        issues.append(f"version {sysinfo.get('application', {}).get('version')!r} != {expected_version!r}")
    bundle_root = Path(sysinfo.get("application", {}).get("bundle_root", ""))
    if not str(bundle_root).lower().startswith(str(scratch).lower()):
        issues.append(f"bundle_root {bundle_root} outside isolated scratch {scratch}")
    scratch_low = str(scratch.resolve()).lower()
    repo_low = str(common.REPO_ROOT.resolve()).lower()
    venv_low = str(common.BUILD_VENV_DIR.resolve()).lower()
    for entry in sysinfo.get("runtime", {}).get("sys_path", []):
        low = str(entry).lower() if entry else ""
        if low.startswith(scratch_low):
            continue  # the frozen runtime is legitimately rooted under the scratch copy
        if low.startswith(repo_low) or low.startswith(venv_low):
            issues.append(f"dev path on sys.path: {entry}")
    for key in ("PySide6", "PyMuPDF", "ebooklib", "pytesseract", "Pillow"):
        if sysinfo.get("dependencies", {}).get(key) in (None, "unknown"):
            issues.append(f"dependency {key} unresolved")
    if issues:
        checks.append(CheckResult("isolated_sysinfo", "fail", "; ".join(issues)))
        return
    checks.append(CheckResult("isolated_sysinfo", "ok", f"identity + dependencies verified ({expected_version})"))
    summary["sysinfo"] = sysinfo

    # --smoke-check
    code, report = run_app(scratch_executable, ["--smoke-check"], cwd=scratch, report_path=work_dir / "smoke_check.json")
    if code == 0 and report is not None and report.get("ok") is True:
        checks.append(CheckResult("isolated_launch", "ok", "window created headless"))
    else:
        checks.append(CheckResult("isolated_launch", "fail", f"exit {code}, report={report}"))
        return

    # Smoke conversions
    out_root = work_dir / "outputs"
    out_root.mkdir(parents=True, exist_ok=True)
    done = 0
    for doc_id, classification, _ in SMOKE_DOCUMENTS:
        pdf = corpus_copy / f"{doc_id}.pdf"
        if not pdf.is_file():
            checks.append(CheckResult(f"smoke_{doc_id}", "skip", f"fixture missing: {pdf}"))
            continue
        out_dir = out_root / doc_id
        args = ["--smoke", str(pdf), str(out_dir)]
        code, report = run_app(
            scratch_executable, args, cwd=scratch, report_path=work_dir / f"smoke_{doc_id}.json"
        )
        status, detail = _classify_smoke(doc_id, classification, code, report)
        checks.append(CheckResult(f"smoke_{doc_id}", status, detail))
        done += 1
    if done == 0:
        checks.append(CheckResult("isolated_smoke", "fail", "no smoke fixtures available"))
        return

    # AZW3 (external Calibre)
    azw3_pdf = corpus_copy / "novel_basic.pdf"
    azw3_out = out_root / "novel_basic_azw3"
    if azw3_pdf.is_file():
        code, report = run_app(
            scratch_executable,
            ["--smoke", str(azw3_pdf), str(azw3_out), "--azw3"],
            cwd=scratch,
            report_path=work_dir / "smoke_novel_basic_azw3.json",
        )
        azw3_requested = report.get("azw3") if isinstance(report, dict) else None
        if code == 0 and isinstance(azw3_requested, dict) and azw3_requested.get("present"):
            checks.append(CheckResult("smoke_azw3", "ok", "EPUB + AZW3 generated via external Calibre"))
            summary["azw3_verified"] = True
        elif code == 0 or (report is not None and report.get("ok") is True):
            checks.append(CheckResult("smoke_azw3", "skip", f"AZW3 not produced (Calibre absent?) report={report}"))
        else:
            checks.append(CheckResult("smoke_azw3", "fail", f"exit {code}, report={report}"))
    else:
        checks.append(CheckResult("smoke_azw3", "skip", "novel_basic fixture missing"))


def _classify_smoke(doc_id: str, classification: str, code: int, report: dict[str, Any] | None) -> tuple[str, str]:
    """Translate one smoke result into (status, detail) against expectations.

    TEXT documents must convert successfully and validate. SCANNED/MIXED
    documents need OCR; without the external ``tesseract`` executable the
    genuine behavior is a *graceful* ``conversion_failed`` (exit 0 with the
    expected flag). A hard crash (non-zero exit / no report) is always a fail.
    """
    if classification == "TEXT":
        ok = (
            code == 0
            and isinstance(report, dict)
            and report.get("ok") is True
            and report.get("epub", {}).get("present") is True
            and report.get("epub", {}).get("validation_valid") is True
        )
        if ok:
            detail = f"EPUB validated ({report['epub']['validation_warnings']}w/{report['epub']['validation_errors']}e)"
            return "ok", detail
        return "fail", f"exit {code}, report={report}"
    graceful = (
        code == 0
        and isinstance(report, dict)
        and report.get("expected") is True
        and report.get("state") == "conversion_failed"
    )
    if graceful:
        return "ok", f"graceful unavailability (OCR '{report.get('error', {}).get('message', '')}')"
    if report is not None and report.get("ok") is True:
        return "skip", f"OCR unexpectedly succeeded (tesseract available?); exit {code}"
    return "fail", f"exit {code}, report={report}"


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; writes ``build/windows_package_verification.json``."""
    arguments = list(argv) if argv is not None else sys.argv[1:]
    bundle = common.BUNDLE_DIR
    venv = common.BUILD_VENV_DIR
    work = common.BUILD_DIR / "verification_work"
    corpus = common.CORPUS_DIR
    run_smoke = True
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--bundle":
            index += 1
            bundle = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--venv":
            index += 1
            venv = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--work":
            index += 1
            work = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--corpus":
            index += 1
            corpus = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--no-smoke":
            run_smoke = False
        elif argument == "--version":
            index += 1
            pass  # ignored; the authoritative version is read from pyproject
        else:
            print(f"[verify] unknown argument: {argument}", flush=True)
            return 2
        index += 1

    report = verify(
        bundle,
        build_venv=venv,
        work_dir=work,
        corpus_dir=corpus,
        expected_version=common.project_version(),
        run_smoke=run_smoke,
    )
    for check in report.checks:
        print(f"[verify] {check.status.upper():4s} {check.name}: {check.detail}", flush=True)

    output = common.BUILD_DIR / "windows_package_verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    print(f"[verify] report written to {output}", flush=True)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())