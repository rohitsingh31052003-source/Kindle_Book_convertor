"""Windows packaging build tooling (M6.5).

Builds a reproducible, self-contained Windows artifact:

1. creates a **fresh, project-dedicated virtual environment** under
   ``build/windows_build_venv`` (recreated by default) so the frozen
   application is built from the rolling interpreters of the current
   dependency set and never from the developer's environment;
2. installs the project with its ``ui`` and ``ocr`` extras plus PyInstaller
   into that clean environment (the project is installed from the editable
   Python path first; ``build_tools`` is imported by this script, which runs
   with the repository root on ``sys.path``);
3. writes ``build/version_info.txt`` from ``pyproject.toml`` ([project]
   version) via :func:`build_tools.common.version_info_text`;
4. runs ``python -m PyInstaller`` with the checked-in spec
   ``build_tools/KindleBookConverter.spec`` into ``dist/KindleBookConverter``;
5. strips the packaged ``dist-info`` provenance artifacts (``direct_url.json``,
   ``INSTALLER``) so no development-time absolute path survives in the bundle
   (the version metadata itself remains, so the frozen application still
   resolves its version through importlib.metadata).

The produced artifact is verified separately by
:mod:`build_tools.verify_windows_package`.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

try:  # allow ``python build_tools/build_windows.py`` without installation
    from build_tools import common
except ImportError:  # pragma: no cover - bootstrap for direct execution
    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from build_tools import common

__all__ = [
    "build",
    "main",
    "ensure_build_python_path",
]


def ensure_build_python_path(build_venv: Path) -> Path:
    """Return the build venv's Python executable, creating the venv first."""
    python = (
        build_venv / "Scripts" / "python.exe"
        if os.name == "nt"
        else build_venv / "bin" / "python"
    )
    if not python.exists() or not build_venv.is_dir():
        _log(f"creating build virtual environment: {build_venv}")
        base = sys.executable
        subprocess.run(
            [base, "-m", "venv", str(build_venv)],
            check=True,
            capture_output=True,
            text=True,
        )
    return python


def _install(build_python: Path) -> None:
    _log("installing project (ui + ocr extras) and PyInstaller into build venv")
    subprocess.run(
        [
            str(build_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--upgrade",
            "pip",
        ],
        check=True,
    )
    subprocess.run(
        [
            str(build_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            ".[ui,ocr]",
            "pyinstaller",
        ],
        cwd=common.REPO_ROOT,
        check=True,
    )


def _write_version_resource() -> Path:
    text = common.version_info_text(common.project_version())
    common.VERSION_INFO_FILE.parent.mkdir(parents=True, exist_ok=True)
    common.VERSION_INFO_FILE.write_text(text, encoding="utf-8")
    _log(f"wrote version resource: {common.VERSION_INFO_FILE}")
    return common.VERSION_INFO_FILE


def _run_pyinstaller(build_python: Path) -> None:
    _log("running PyInstaller (see build/pyinstaller for work files)")
    subprocess.run(
        [
            str(build_python),
            "-m",
            "PyInstaller",
            "--noconfirm",
            "--clean",
            "--distpath",
            str(common.DIST_DIR),
            "--workpath",
            str(common.PYINSTALLER_WORK_DIR),
            str(common.REPO_ROOT / "build_tools" / "KindleBookConverter.spec"),
        ],
        cwd=common.REPO_ROOT,
        check=True,
    )


def _strip_provenance_metadata(bundle: Path) -> None:
    """Remove distribution-provenance files that record the build checkout.

    ``direct_url.json`` (editable/local installs) contains the repository's
    absolute path, which must not survive in the released bundle. The
    ``dist-info`` folder stays (the frozen app reads its version there).
    """
    removed: list[str] = []
    for path in bundle.rglob("*.dist-info"):
        for name in ("direct_url.json", "INSTALLER"):
            candidate = path / name
            if candidate.is_file():
                candidate.unlink()
                removed.append(str(candidate.relative_to(bundle)))
    if removed:
        _log("stripped provenance metadata: " + ", ".join(removed))
    else:  # pragma: no cover - informational
        _log("no provenance metadata to strip")


def build(
    *,
    python: str | None = None,
    build_venv: Path | None = None,
    keep_venv: bool = False,
    dist_dir: Path | None = None,
) -> Path:
    """Build the frozen Windows bundle and return its directory.

    ``python`` selects the interpreter used to create the build venv (default:
    the interpreter running this script). ``build_venv`` overrides the default
    ``build/windows_build_venv``; ``keep_venv`` reuses instead of recreating
    it; ``dist_dir`` overrides the default ``dist/`` output location.
    """
    venv_dir = build_venv if build_venv is not None else common.BUILD_VENV_DIR
    if not keep_venv and venv_dir.exists():
        _log(f"removing previous build venv: {venv_dir}")
        import shutil

        shutil.rmtree(venv_dir)

    os.environ.setdefault("PYTHONUTF8", "1")
    build_python = ensure_build_python_path(venv_dir)
    if not keep_venv:
        _install(build_python)

    _write_version_resource()
    _run_pyinstaller(build_python)

    artifact = dist_dir if dist_dir is not None else common.BUNDLE_DIR
    executable = artifact / f"{common.BUNDLE_NAME}.exe"
    if not executable.is_file():
        raise FileNotFoundError(f"expected artifact not produced: {executable}")
    _strip_provenance_metadata(artifact)
    _log(f"build complete: {artifact}")
    return artifact


def _log(message: str) -> None:
    print(f"[build_windows] {message}", flush=True)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point.

    ``--python PATH`` uses a specific interpreter for the build venv;
    ``--keep-venv`` reuses an existing build venv; ``--venv PATH`` and
    ``--dist PATH`` override the default build/venv and dist/ directories.
    """
    arguments = list(argv) if argv is not None else sys.argv[1:]
    python: str | None = None
    venv: Path | None = None
    dist: Path | None = None
    keep_venv = False
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--python":
            index += 1
            python = arguments[index] if index < len(arguments) else None
        elif argument == "--venv":
            index += 1
            venv = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--dist":
            index += 1
            dist = Path(arguments[index]) if index < len(arguments) else None
        elif argument == "--keep-venv":
            keep_venv = True
        else:
            _log(f"unknown argument: {argument}")
            return 2
        index += 1
    if python is None:
        python = sys.executable
    try:
        build(python=python, build_venv=venv, keep_venv=keep_venv, dist_dir=dist)
        return 0
    except Exception as exc:  # keep failures one line and non-zero
        _log(f"build failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())