"""Package identity, version, and resource-path helpers (M6.5).

Single source of runtime identity and layout metadata shared by the desktop
application, the Windows packaging build (:mod:`build_tools`), the packaged
verification tooling, and the tests.

* ``DISTRIBUTION_NAME`` / ``EXECUTABLE_NAME`` / ``APP_NAME`` are the project's
  canonical identity strings (distribution on PyPI-style metadata, the frozen
  Windows executable base name, and the user-facing application/window name).
  ``tests/test_windows_packaging.py`` keeps them aligned with the installed
  distribution so a drift is caught immediately.
* :func:`application_version` resolves the version from the *installed*
  distribution metadata, whose source of truth is ``pyproject.toml``
  (``[project] version``). It never raises and never blocks; when the
  distribution is not installed (for example a bare source checkout without a
  pip install) it falls back to :data:`_VERSION_FALLBACK`, which a guard test
  keeps in sync with the ``pyproject.toml`` version.
* :func:`is_frozen` / :func:`bundle_root` / :func:`resource_path` describe the
  packaged runtime layout. When the application runs from a PyInstaller bundle
  (onedir), the runtime tree is extracted under ``sys._MEIPASS`` and every
  packaged resource lives beneath it; the current working directory must never
  be assumed. In a source checkout the bundle root is the package directory
  itself, so :func:`resource_path` stays deterministic for tests.
"""

from __future__ import annotations

import sys
from importlib import metadata
from pathlib import Path

__all__ = [
    "APP_NAME",
    "DISTRIBUTION_NAME",
    "EXECUTABLE_NAME",
    "application_version",
    "bundle_root",
    "is_frozen",
    "resource_path",
]

#: Distribution name in ``pyproject.toml`` ([project] name).
DISTRIBUTION_NAME = "kindle-converter"

#: User-facing application name (window title, bundle display name).
APP_NAME = "Kindle Book Converter"

#: Base name of the frozen Windows executable/bundle directory.
EXECUTABLE_NAME = "KindleBookConverter"

#: Version used only when the distribution metadata cannot be resolved
#: (bare source checkout). Kept in sync by ``tests/test_windows_packaging.py``.
_VERSION_FALLBACK = "0.1.0"


def is_frozen() -> bool:
    """Whether the process runs from a PyInstaller bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """The runtime tree of the running application.

    PyInstaller onedir extracts to ``sys._MEIPASS`` (the bundle directory at
    runtime); for a source checkout this is the ``kindle_converter`` package
    directory. The result is always an existing directory.
    """
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", sys.executable)).resolve()
    return Path(__file__).resolve().parent


def resource_path(name: str) -> Path:
    """Resolve ``name`` (a ``/``-delimited relative bundle path) under the
    runtime tree regardless of the current working directory."""
    return bundle_root() / name


def application_version() -> str:
    """The version of the installed distribution, or the fallback constant.

    Resolution reads the installed metadata (``importlib.metadata``), which in
    the frozen bundle is present because packaging ships the ``dist-info``;
    in case the metadata is missing or malformed only the non-empty fallback
    is used -- never an exception.
    """
    try:
        version = metadata.version(DISTRIBUTION_NAME)
    except metadata.PackageNotFoundError:
        return _VERSION_FALLBACK
    if not isinstance(version, str) or not version:
        return _VERSION_FALLBACK
    return version