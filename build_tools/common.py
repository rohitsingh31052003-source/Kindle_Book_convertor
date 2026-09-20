"""Packaging build/verification tooling (M6.5).

This ``build_tools`` package is repository tooling: it is part of the
checkout but **not** part of the ``kindle_converter`` distribution. It is
deliberately importable both by ``build_tools/`` scripts (with the repository
root on ``sys.path``) and by the pytest suite, so the M6.5 packaging
configuration is covered by unit tests without a build artifact.

The helpers here are pure and deterministic: project-metadata reading,
PyInstaller version-resource generation, PE-subsystem inspection, and
development-path scanning. Everything that touches the network, a virtual
environment, or a build lives in the ``build_windows`` and
``verify_windows_package`` modules.
"""

from __future__ import annotations

import struct
import tomllib
from pathlib import Path
from typing import Any

#: Repository root (the directory containing ``pyproject.toml``).
REPO_ROOT = Path(__file__).resolve().parent.parent

#: Absolute path to the project's ``pyproject.toml``.
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

#: Build outputs live under ``build/``; ``.gitignore`` already ignores it.
BUILD_DIR = REPO_ROOT / "build"

#: The clean, project-dedicated virtual environment the build runs in.
BUILD_VENV_DIR = BUILD_DIR / "windows_build_venv"

#: PyInstaller working directory (``--workpath``).
PYINSTALLER_WORK_DIR = BUILD_DIR / "pyinstaller"

#: Version-resource text applied to the frozen executable.
VERSION_INFO_FILE = BUILD_DIR / "version_info.txt"

#: Distribution output directory (``--distpath``); ``.gitignore`` covers it.
DIST_DIR = REPO_ROOT / "dist"

#: Frozen bundle directory name and executable base name.
BUNDLE_NAME = "KindleBookConverter"

#: Relative layout of the produced artifact inside ``dist/``.
BUNDLE_DIR = DIST_DIR / BUNDLE_NAME
ARTIFACT_EXECUTABLE = BUNDLE_DIR / f"{BUNDLE_NAME}.exe"

#: The M6.1 repository corpus (fixture PDF documents).
CORPUS_DIR = REPO_ROOT / "tests" / "fixtures" / "corpus"

#: Identity constants mirrored from ``kindle_converter._meta`` (a drift-guard
#: test keeps both in sync).
APP_NAME = "Kindle Book Converter"
EXECUTABLE_NAME = BUNDLE_NAME
DISTRIBUTION_NAME = "kindle-converter"

#: Suffixes treated as text when scanning the bundle for development paths.
_TEXT_SUFFIXES = frozenset(
    {".py", ".pth", ".toml", ".cfg", ".ini", ".json", ".txt", ".md", ".xml"}
)

#: Platform tag embedded in the Windows distribution archive name (M8.2).
#: The release artifact is produced and verified on 64-bit Windows (the
#: PyInstaller build runs under ``win-amd64``).
PLATFORM_TAG = "Windows-x64"


def distribution_archive_name(version: str | None = None) -> str:
    """File name of the Windows distribution archive (M8.2).

    The artifact is ``KindleBookConverter-Windows-x64-<version>.zip``, derived
    from the bundle/executable base name, :data:`PLATFORM_TAG`, and the single
    authoritative version source (``pyproject.toml`` when not overridden).
    """
    if version is None:
        version = project_version()
    return f"{BUNDLE_NAME}-{PLATFORM_TAG}-{version}.zip"


def distribution_archive_path(version: str | None = None) -> Path:
    """Path of the Windows distribution archive next to the bundle in ``dist/``."""
    return DIST_DIR / distribution_archive_name(version)


def distribution_checksum_path(archive: Path) -> Path:
    """Path of the SHA-256 checksum file written beside ``archive``."""
    return archive.with_name(archive.name + ".sha256")


def load_pyproject() -> dict[str, Any]:
    """Parse ``pyproject.toml`` at the repository root."""
    with PYPROJECT_PATH.open("rb") as handle:
        return tomllib.load(handle)


def project_name(pyproject: dict[str, Any] | None = None) -> str:
    """The ``[project] name`` from ``pyproject.toml``."""
    return str((pyproject if pyproject is not None else load_pyproject())["project"]["name"])


def project_version(pyproject: dict[str, Any] | None = None) -> str:
    """The ``[project] version`` from ``pyproject.toml``."""
    return str((pyproject if pyproject is not None else load_pyproject())["project"]["version"])


def project_dependencies(
    pyproject: dict[str, Any] | None = None,
) -> dict[str, list[str]]:
    """Classify dependency declarations by category.

    Returns ``{"required": [...], "dev": [...], "ocr": [...], "ui": [...]}``
    from the ``[project] dependencies`` and ``[project.optional-dependencies]``
    tables -- used by the packaging tests to pin the exact dependency contract
    the frozen bundle must ship.
    """
    project = (pyproject if pyproject is not None else load_pyproject())["project"]
    required = [str(item) for item in project.get("dependencies", [])]
    optional = project.get("optional-dependencies", {})
    categories = {name: [str(item) for item in specs] for name, specs in optional.items()}
    return {"required": required, **categories}


def _version_tuple(version: str) -> tuple[int, int, int, int]:
    """Split ``major.minor.patch`` (any extra segment is treated as build).

    Non-numeric segments are coerced to ``0`` so the Windows version resource
    (which requires four integers) is always well-formed.
    """
    parts = version.split(".")
    numbers: list[int] = []
    for part in parts[:4]:
        digits = "".join(ch for ch in part if ch.isdigit())
        numbers.append(int(digits) if digits else 0)
    while len(numbers) < 4:
        numbers.append(0)
    return (numbers[0], numbers[1], numbers[2], numbers[3])


def version_info_text(version: str) -> str:
    """Generate the PyInstaller version-resource text for ``version``.

    The output matches the format ``pyi-grab_version`` produces and that
    ``VSVersionInfo.from_str`` / ``EXE(version=...)`` consume (verified by
    ``tests/test_windows_packaging.py`` and by the build-time embedding).
    """
    filevers = prodvers = _version_tuple(version)
    file_version = ", ".join(str(item) for item in filevers)
    return (
        "# UTF-8\n"
        "#\n"
        "# Generated by build_tools.common.version_info_text (M6.5); applies\n"
        f"# the pyproject.toml version {version} to the frozen executable.\n"
        "VSVersionInfo(\n"
        "  ffi=FixedFileInfo(\n"
        f"    filevers={filevers},\n"
        f"    prodvers={prodvers},\n"
        "    mask=0x3f,\n"
        "    flags=0x0,\n"
        "    OS=0x4,\n"
        "    fileType=0x1,\n"
        "    subtype=0x0,\n"
        "    date=(0, 0)\n"
        "    ),\n"
        "  kids=[\n"
        "    StringFileInfo(\n"
        "      [\n"
        "      StringTable(\n"
        "        '000004b0',\n"
        "        [StringStruct('CompanyName', ''),\n"
        f"        StringStruct('FileDescription', '{APP_NAME}'),\n"
        f"        StringStruct('FileVersion', '{file_version}'),\n"
        f"        StringStruct('InternalName', '{EXECUTABLE_NAME}'),\n"
        "        StringStruct('LegalCopyright', ''),\n"
        "        StringStruct('LegalTrademarks', ''),\n"
        f"        StringStruct('OriginalFilename', '{EXECUTABLE_NAME}.exe'),\n"
        "        StringStruct('PrivateBuild', ''),\n"
        f"        StringStruct('ProductName', '{APP_NAME}'),\n"
        f"        StringStruct('ProductVersion', '{version}'),\n"
        "        StringStruct('SpecialBuild', '')])\n"
        "      ]),\n"
        "    VarFileInfo([VarStruct('Translation', [0, 1200])])\n"
        "  ]\n"
        ")\n"
    )


def pe_subsystem_bytes(data: bytes) -> int | None:
    """Return the PE ``Subsystem`` field of ``data``, or ``None`` when ``data``
    is not a PE image (2 = Windows GUI subsystem, 3 = Windows console).

    The parser reads only the minimal header fields needed (``MZ`` magic, the
    ``e_lfanew`` offset, the ``PE\\0\\0`` signature, and the Optional Header
    ``Subsystem`` field), which is layout-stable for both PE32 and PE32+.
    Operating on raw bytes lets the distribution verifier inspect the
    executable *inside* a ZIP archive without extracting to disk.
    """
    if len(data) < 0x40 or data[:2] != b"MZ":
        return None
    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if e_lfanew + 24 + 96 > len(data):
        return None
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return None
    optional_offset = e_lfanew + 24
    magic = struct.unpack_from("<H", data, optional_offset)[0]
    if magic not in (0x10B, 0x20B):  # PE32 / PE32+
        return None
    # IMAGE_OPTIONAL_HEADER.Subsystem sits at offset 68 in both variants.
    return struct.unpack_from("<H", data, optional_offset + 68)[0]


def pe_subsystem(path: Path) -> int | None:
    """Return the PE ``Subsystem`` field of ``path``, or ``None`` when it is
    not a PE image. See :func:`pe_subsystem_bytes` for the layout contract.
    """
    try:
        data = path.read_bytes()
    except (OSError, ValueError):
        return None
    return pe_subsystem_bytes(data)


def is_gui_executable(path: Path) -> bool:
    """Whether ``path`` is a PE image built for the Windows GUI subsystem."""
    return pe_subsystem(path) == 2


def iter_bundle_files(bundle: Path) -> list[Path]:
    """All regular files under ``bundle`` (the PyInstaller onedir tree)."""
    if not bundle.is_dir():
        return []
    return [item for item in bundle.rglob("*") if item.is_file()]


def is_text_candidate(path: Path) -> bool:
    return path.suffix.lower() in _TEXT_SUFFIXES


def file_contains(data: bytes, needle: str) -> bool:
    """Whether ``data`` contains ``needle`` in any common encoding.

    ASCII (a superset of plain UTF-8 paths), UTF-16LE, both separator styles
    (``\\`` and ``/``), and case variants are checked, since Windows paths can
    be embedded with any mix in the bundle's text and metadata files.
    """
    if not needle:
        return False
    candidates = {
        needle,
        needle.replace("\\", "/"),
        needle.upper(),
        needle.replace("\\", "/").upper(),
    }
    data_upper = data.upper()
    utf16_data = data.decode("utf-16-le", errors="ignore").encode("utf-16-le")
    utf16_data_upper = utf16_data.upper()
    for candidate in candidates:
        if candidate.encode("ascii", errors="ignore") in data_upper:
            return True
        if candidate.encode("utf-16-le", errors="ignore") in utf16_data_upper:
            return True
    return False


def scan_for_strings(bundle: Path, needles: list[str]) -> dict[str, list[str]]:
    """Find files under ``bundle`` containing any of ``needles``.

    Returns ``{relative_path: [matched needles]}``. Only small text files and
    the executable itself are scanned (binary-heavy runtime DLLs never embed
    repository paths and would dominate the check).
    """
    matches: dict[str, list[str]] = {}
    needles = [needle for needle in needles if needle]
    for path in iter_bundle_files(bundle):
        is_exe = path.name == f"{EXECUTABLE_NAME}.exe"
        if not is_exe and not is_text_candidate(path):
            continue
        if path.stat().st_size > 8 * 1024 * 1024:
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        found = [needle for needle in needles if file_contains(data, needle)]
        if found:
            matches[str(path.relative_to(bundle))] = found
    return matches