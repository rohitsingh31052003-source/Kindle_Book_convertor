"""Windows release distribution archive tooling (M8.2).

The PyInstaller bundle produced by :mod:`build_tools.build_windows` is a
*folder* (``dist/KindleBookConverter/``), but a GitHub Release upload must be
a single file. This module packages the verified bundle into a **reproducible
ZIP archive** plus its **SHA-256 checksum** and verifies the produced archive:

* :func:`create_distribution` writes
  ``dist/KindleBookConverter-Windows-x64-<version>.zip`` (and the matching
  ``<archive>.sha256`` file) from the bundle directory;
* the archive is **reproducible**: members are stored in sorted order with a
  fixed timestamp and Unix file mode, so only the bundle's file bytes differ
  between builds -- two archives from the same bundle are byte-identical;
  (this is the same offline, deterministic philosophy as the M6.5 build);
* :func:`verify_distribution` re-reads the archive and proves the layout
  (``KindleBookConverter/KindleBookConverter.exe`` + ``_internal/``), the
  GUI-subsystem property of the packaged executable (inspected from *inside*
  the ZIP via :func:`build_tools.common.pe_subsystem_bytes`), content parity
  with the source bundle, archive integrity, and the checksum file;

The distribution archive is **not** a replacement for the bundle-level
verification (:mod:`build_tools.verify_windows_package`, M6.5): a release
builds, verifies the bundle, packages the distribution, then verifies the
distribution (see ``docs/windows-packaging.md`` for the ordered flow and
``docs/release-checklist.md`` for the release gates).
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

try:  # allow ``python build_tools/package_distribution.py``
    from build_tools import common
    from build_tools.verify_windows_package import CheckResult
except ImportError:  # pragma: no cover - bootstrap for direct execution
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from build_tools import common
    from build_tools.verify_windows_package import CheckResult

__all__ = [
    "create_distribution",
    "checksum_hex",
    "verify_distribution",
    "main",
]

#: Fixed member timestamp stored on every archive entry, so the archive bytes
#: do not wander with file mtimes (kept constant for reproducibility).
_ZIP_FIXED_TIME = (2020, 1, 1, 0, 0, 0)

#: Unix regular-file mode (``rw-r--r--``) stored on every member so the
#: extracted tree does not depend on the build machine's umask/attributes.
_FILE_MODE = 0o100644

#: Compression level used for every member (deterministic across runs).
_COMPRESS_LEVEL = 6


@dataclass
class DistributionReport:
    """Aggregate of :func:`verify_distribution` results plus context."""

    archive: Path
    checks: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(check.status == "fail" for check in self.checks)

    def as_dict(self) -> dict[str, Any]:
        return {
            "archive": str(self.archive),
            "passed": self.passed,
            "checks": [
                {"name": c.name, "status": c.status, "detail": c.detail}
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Pure helpers (offline, deterministic -- exercised by the packaging tests)
# ---------------------------------------------------------------------------


def checksum_hex(path: Path) -> str:
    """Lowercase SHA-256 hex digest of ``path``."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksum(archive: Path) -> Path:
    """Write the SHA-256 checksum file next to ``archive``; return its path.

    The line format is the widely used ``sha256sum`` convention
    ``<hex>  <archive-name>`` so release tools can verify with
    ``sha256sum -c`` or equivalent.
    """
    checksum_path = common.distribution_checksum_path(archive)
    checksum_path.write_text(f"{checksum_hex(archive)}  {archive.name}\n", encoding="utf-8")
    return checksum_path


def layout_issues(member_names: list[str]) -> list[str]:
    """Problems with the archive member layout, or ``[]`` when it is correct.

    The archive must contain the bundle under its single root folder
    ``KindleBookConverter/`` with the executable at its top level and a
    non-empty ``_internal/`` runtime tree beside it.
    """
    issues: list[str] = []
    executable = f"{common.BUNDLE_NAME}/{common.BUNDLE_NAME}.exe"
    if not member_names:
        issues.append("archive has no members")
        return issues
    root = member_names[0].split("/", 1)[0]
    if root != common.BUNDLE_NAME:
        issues.append(f"archive root is {root!r}, expected {common.BUNDLE_NAME!r}")
        return issues
    if executable not in member_names:
        issues.append(f"archive does not contain {executable!r}")
    internal_prefix = f"{common.BUNDLE_NAME}/_internal/"
    internal_members = [name for name in member_names if name.startswith(internal_prefix)]
    if not internal_members:
        issues.append(f"archive does not contain the {internal_prefix!r} runtime tree")
    return issues


def content_parity_issues(
    member_names: list[str], bundle_files: list[Path]
) -> list[str]:
    """Problems comparing archive members against the source bundle, or ``[]``.

    Every bundle file must appear in the archive exactly once (under the
    ``KindleBookConverter/`` root), and the archive must contain nothing else.
    The executable is included in this comparison like any other bundle file.
    """
    if not bundle_files:
        return ["bundle has no files"]
    expected = {
        f"{common.BUNDLE_NAME}/{item.relative_to(bundle_files[0].parent).as_posix()}"
        for item in bundle_files
    }
    actual = set(member_names)
    missing = sorted(expected - actual)
    extra = sorted(actual - expected)
    issues: list[str] = []
    if missing:
        issues.append("members missing: " + ", ".join(missing[:8]) + ("..." if len(missing) > 8 else ""))
    if extra:
        issues.append("extra members: " + ", ".join(extra[:8]) + ("..." if len(extra) > 8 else ""))
    return issues


def _archive_order(member_names: list[str]) -> list[str]:
    """Members sorted by archive path (the reproducible archive ordering)."""
    return sorted(member_names)


# ---------------------------------------------------------------------------
# Archive creation / verification
# ---------------------------------------------------------------------------


def _read_member_bytes(path: str, archive: Path) -> bytes:
    """Return the uncompressed bytes of one archive member."""
    with ZipFile(archive) as zip_handle:
        return zip_handle.read(path)


def create_distribution(
    bundle: Path,
    *,
    version: str | None = None,
    archive: Path | None = None,
) -> Path:
    """Package ``bundle`` into the reproducible distribution archive.

    Writes ``KindleBookConverter-Windows-x64-<version>.zip`` (next to the
    bundle in ``dist/`` by default) with the bundle under its own root folder,
    then writes the matching ``.sha256`` checksum. Returns the archive path.
    """
    if version is None:
        version = common.project_version()
    if archive is None:
        archive = common.distribution_archive_path(version)
    if not bundle.is_dir():
        raise FileNotFoundError(f"bundle not found: {bundle}")
    files = common.iter_bundle_files(bundle)
    if not files:
        raise ValueError(f"bundle contains no files: {bundle}")

    dist_dir = bundle.parent
    names = [
        f"{common.BUNDLE_NAME}/{item.relative_to(bundle).as_posix()}"
        for item in files
    ]
    archive.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(archive, "w") as zip_handle:
        for name in _archive_order(names):
            info = ZipInfo(name, date_time=_ZIP_FIXED_TIME)
            info.compress_type = ZIP_DEFLATED
            info._compresslevel = _COMPRESS_LEVEL
            info.external_attr = _FILE_MODE << 16
            info.create_system = 3  # deterministic across platforms
            zip_handle.writestr(info, (bundle / name[len(common.BUNDLE_NAME) + 1 :]).read_bytes())
    write_checksum(archive)
    return archive


def verify_distribution(
    archive: Path,
    bundle: Path,
) -> DistributionReport:
    """Verification checks for a distribution archive against its bundle.

    Mirrors the structure of :func:`build_tools.verify_windows_package.verify`
    (a list of :class:`CheckResult` with ``ok``/``fail``/``skip``) but proves
    the *distribution* layer: layout, GUI subsystem (inspected inside the
    archive), content parity, integrity, and the checksum file. The bundle
    itself is verified separately by the M6.5 verifier; this does not re-run
    the frozen executable.
    """
    checks: list[CheckResult] = []

    if not archive.is_file():
        checks.append(CheckResult("archive_present", "fail", f"archive missing: {archive}"))
        return DistributionReport(archive, checks)
    checks.append(
        CheckResult("archive_present", "ok", f"{archive.parent.name}/{archive.name}")
    )

    try:
        with ZipFile(archive) as zip_handle:
            names = zip_handle.namelist()
            bad = zip_handle.testzip()
    except (OSError, zipfile.BadZipFile) as exc:
        checks.append(CheckResult("archive_integrity", "fail", f"cannot read archive: {type(exc).__name__}: {exc}"))
        return DistributionReport(archive, checks)
    if bad is not None:
        checks.append(CheckResult("archive_integrity", "fail", f"corrupt member: {bad}"))
        return DistributionReport(archive, checks)
    checks.append(CheckResult("archive_integrity", "ok", f"{len(names)} members readable"))

    issues = layout_issues(names)
    if issues:
        checks.append(CheckResult("archive_layout", "fail", "; ".join(issues)))
    else:
        checks.append(
            CheckResult(
                "archive_layout",
                "ok",
                f"{common.BUNDLE_NAME}/KindleBookConverter.exe + _internal/ present",
            )
        )

    executable = f"{common.BUNDLE_NAME}/{common.BUNDLE_NAME}.exe"
    subsystem = None
    if executable in names and bad is None:
        subsystem = common.pe_subsystem_bytes(_read_member_bytes(executable, archive))
    if subsystem == 2:
        checks.append(CheckResult("gui_subsystem", "ok", "Windows GUI subsystem (2) in archive"))
    else:
        checks.append(
            CheckResult("gui_subsystem", "fail", f"expected GUI subsystem 2, found {subsystem}")
        )

    bundle_files = common.iter_bundle_files(bundle)
    issues = content_parity_issues(names, bundle_files)
    if issues:
        checks.append(CheckResult("content_parity", "fail", "; ".join(issues)))
    else:
        checks.append(
            CheckResult("content_parity", "ok", f"archive matches bundle ({len(bundle_files)} files)")
        )

    checksum_path = common.distribution_checksum_path(archive)
    if not checksum_path.is_file():
        checks.append(CheckResult("checksum", "fail", f"checksum file missing: {checksum_path.name}"))
    else:
        expected_line = checksum_path.read_text(encoding="utf-8").strip()
        actual = checksum_hex(archive)
        if expected_line == f"{actual}  {archive.name}":
            checks.append(CheckResult("checksum", "ok", f"SHA-256 {actual}"))
        else:
            checks.append(CheckResult("checksum", "fail", "checksum file does not match archive"))

    return DistributionReport(archive, checks)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point; writes ``build/windows_distribution_verification.json``.

    ``--bundle PATH`` overrides the bundle directory, ``--zip PATH`` overrides
    the archive output/input path, ``--version V`` overrides the version used
    in the archive name, ``--check`` verifies an existing archive without
    creating it, and ``--no-verify`` skips post-creation verification.
    """
    arguments = list(argv) if argv is not None else sys.argv[1:]
    bundle = common.BUNDLE_DIR
    archive: Path | None = None
    version: str | None = None
    check_only = False
    verify_after = True
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--bundle":
            index += 1
            bundle = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--zip":
            index += 1
            archive = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--version":
            index += 1
            version = arguments[index] if index < len(arguments) else None
        elif argument == "--check":
            check_only = True
        elif argument == "--no-verify":
            verify_after = False
        else:
            print(f"[dist] unknown argument: {argument}", flush=True)
            return 2
        index += 1

    if check_only:
        if archive is None:
            archive = common.distribution_archive_path(version)
        report = verify_distribution(archive, bundle)
    else:
        version = version if version is not None else common.project_version()
        if archive is None:
            archive = common.distribution_archive_path(version)
        try:
            archive = create_distribution(bundle, version=version, archive=archive)
            print(f"[dist] archive written: {archive}", flush=True)
        except Exception as exc:
            print(f"[dist] packaging failed: {type(exc).__name__}: {exc}", flush=True)
            return 1
        if verify_after:
            report = verify_distribution(archive, bundle)
        else:
            print("[dist] verification skipped (--no-verify)", flush=True)
            return 0

    for check in report.checks:
        print(f"[dist] {check.status.upper():4s} {check.name}: {check.detail}", flush=True)
    output = common.BUILD_DIR / "windows_distribution_verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    print(f"[dist] report written to {output}", flush=True)
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())