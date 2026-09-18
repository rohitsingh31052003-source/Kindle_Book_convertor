"""Calibre ``ebook-convert`` backend for AZW3 conversion (Milestone 4.3).

This module isolates every subprocess/executable concern behind one small
adapter:

* **executable discovery** -- an explicit ``executable`` is used as-is and
  verified eagerly; otherwise ``ebook-convert`` is looked up on ``PATH``;
* **deterministic command construction** --
  :func:`build_calibre_command` returns the exact argument list
  ``[executable, source, destination]``;
* **a checked, shell-free invocation** -- ``subprocess.run(..., shell=False,
  capture_output=True)`` with a plain argument list, no shell interpolation,
  environment-free error messages, and a checked non-zero return code.

The public
:func:`~kindle_converter.epub.azw3.convert_epub_to_azw3` depends on the
:class:`~kindle_converter.epub.azw3.AZW3ConversionBackend` abstraction and
only constructs this adapter when no backend is injected, so nothing outside
this module touches a process.

Calibre itself is a system dependency -- never a Python package dependency.
This project does not install, download, or vendor Calibre. If the
executable cannot be found or launched, the dedicated
:class:`AZW3BackendUnavailableError` is raised instead of a raw
``FileNotFoundError``/``OSError``; a non-zero exit becomes
:class:`AZW3ConversionFailedError` instead of a raw ``CalledProcessError``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .azw3 import (
    AZW3BackendUnavailableError,
    AZW3ConversionFailedError,
)

PathLike = str | os.PathLike[str]

#: The Calibre executable invoked for EPUB -> AZW3 conversion.
CALIBRE_EXECUTABLE = "ebook-convert"

#: How many characters of subprocess output are quoted in an error message.
#: ``ebook-convert`` failures can be verbose; the message must stay bounded.
MAX_REPORTED_OUTPUT_CHARS = 1000


# --------------------------------------------------------------------------- #
# Deterministic command construction
# --------------------------------------------------------------------------- #


def build_calibre_command(
    executable: PathLike,
    source: PathLike,
    destination: PathLike,
) -> list[str]:
    """The argument list for one ``ebook-convert`` invocation.

    Returns ``[executable, source, destination]`` exactly: each path is a
    single, separate argument, so spaces and other characters survive
    untouched and ``subprocess.run`` can pass them straight to the OS with
    ``shell=False``. No shell quoting, no temporary names, no timestamps.

    The construction is **deterministic**: the same ``executable``,
    ``source``, and ``destination`` always produce the same list.
    Byte-for-byte determinism of the AZW3 file itself is a separate concern
    governed by Calibre and is explicitly out of scope for M4.3.
    """
    return [
        os.fspath(executable),
        os.fspath(source),
        os.fspath(destination),
    ]


# --------------------------------------------------------------------------- #
# The Calibre adapter
# --------------------------------------------------------------------------- #


class CalibreBackend:
    """Convert an EPUB to AZW3 through Calibre's ``ebook-convert``.

    Executable discovery (M4.3): an explicit ``executable`` is used as-is
    and its existence is verified eagerly at construction; with
    ``executable=None`` the Calibre tool is discovered on ``PATH`` at
    convert time. A missing or unusable executable raises
    :class:`AZW3BackendUnavailableError` -- never a raw
    ``FileNotFoundError``.

    Subprocess behavior: ``subprocess.run`` is called with ``shell=False``
    and a plain argument list (no shell interpolation), stdout/stderr are
    captured, and a non-zero return code raises
    :class:`AZW3ConversionFailedError` carrying a bounded diagnostic
    excerpt. No environment or sensitive information is included in error
    messages.
    """

    def __init__(self, executable: PathLike | None = None) -> None:
        """Configure the backend with an explicit executable, or discover it.

        Parameters
        ----------
        executable:
            Optional explicit path to Calibre's ``ebook-convert``
            executable. ``None`` (the default) defers discovery to ``PATH``
            until :meth:`convert` runs.

        Raises
        ------
        AZW3BackendUnavailableError
            If ``executable`` is given and does not exist.
        """
        if executable is None:
            self._executable: Path | None = None
        else:
            path = _as_path(executable)
            if not path.exists():
                raise AZW3BackendUnavailableError(
                    f"the configured Calibre executable {str(path)!r} does "
                    f"not exist; AZW3 conversion requires Calibre's "
                    f"{CALIBRE_EXECUTABLE!r} executable (install Calibre, "
                    f"or pass calibre_path=...)"
                )
            self._executable = path

    @property
    def executable(self) -> Path | None:
        """The explicit executable when one was configured, else ``None``."""
        return self._executable

    def convert(self, source: Path, destination: Path) -> None:
        """Convert ``source`` (an EPUB file) into ``destination``.

        Parameters
        ----------
        source:
            The existing EPUB file to convert.
        destination:
            The AZW3 file to write.

        Raises
        ------
        AZW3BackendUnavailableError
            If the executable cannot be found on ``PATH`` or cannot be
            launched.
        AZW3ConversionFailedError
            If the executable ran but returned a non-zero exit code.
        """
        executable = self._resolve_executable()
        command = build_calibre_command(executable, source, destination)
        try:
            process = subprocess.run(
                command,
                shell=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            raise AZW3BackendUnavailableError(
                f"could not run the Calibre executable "
                f"{str(executable)!r}: {exc}; AZW3 conversion requires a "
                f"working Calibre {CALIBRE_EXECUTABLE!r} executable"
            ) from exc
        if process.returncode != 0:
            raise AZW3ConversionFailedError(
                _conversion_failure_message(
                    source,
                    destination,
                    process.returncode,
                    process.stderr,
                    process.stdout,
                )
            )

    def _resolve_executable(self) -> Path:
        """Return the executable to run: explicit, or discovered on PATH."""
        if self._executable is not None:
            return self._executable
        discovered = shutil.which(CALIBRE_EXECUTABLE)
        if discovered is None:
            raise AZW3BackendUnavailableError(
                f"Calibre's {CALIBRE_EXECUTABLE!r} executable was not found "
                f"on PATH; AZW3 conversion requires Calibre -- install "
                f"Calibre, or pass an explicit calibre_path=..."
            )
        return Path(discovered)


# --------------------------------------------------------------------------- #
# Private helpers
# --------------------------------------------------------------------------- #


def _as_path(value: PathLike) -> Path:
    """Normalize a path-like value to a :class:`pathlib.Path`."""
    raw = os.fspath(value)
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    return Path(raw)


def _trim_output(text: str, limit: int = MAX_REPORTED_OUTPUT_CHARS) -> str:
    """A printable, bounded excerpt of subprocess output for diagnostics."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def _conversion_failure_message(
    source: Path,
    destination: Path,
    returncode: int,
    stderr: str | None,
    stdout: str | None,
) -> str:
    """A bounded, source-explicit diagnostic for a failed conversion.

    Prefers stderr (the natural place for ``ebook-convert`` diagnostics) and
    falls back to stdout; both are capped by :func:`_trim_output` so error
    messages never dump large subprocess output. No environment information
    is included.
    """
    detail = _trim_output(stderr or "")
    if not detail:
        detail = _trim_output(stdout or "")
    message = (
        f"AZW3 conversion failed: Calibre's {CALIBRE_EXECUTABLE!r} exited "
        f"with return code {returncode} while converting "
        f"'{source}' to '{destination}'"
    )
    if detail:
        message += f": {detail}"
    return message