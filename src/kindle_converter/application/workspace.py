"""Application-owned temporary workspace for one conversion (M5.7).

A :class:`ConversionWorkspace` is an isolated temporary directory that
belongs to a single conversion. It is created when the conversion
starts and deterministically cleaned up when the conversion ends --
successfully, or on any failure path (expected application errors,
validation failures, unexpected exceptions). The workspace is owned
by the application/conversion layer, never by the GUI or the worker.

    The workspace exists only for the lifetime of one conversion:

    with ConversionWorkspace() as workspace:
        result = application.convert(request)

    Its ``path`` attribute exposes the directory to application code that
needs intermediate artifacts. It is a standard-library temporary
directory (via :func:`tempfile.TemporaryDirectory`) -- no custom
random-directory mechanism is used.

The workspace is deliberately **not** the user's selected output
directory: final artifacts (EPUB, AZW3) live in the output directory
the user chose, and workspace cleanup never touches that directory.
"""

from __future__ import annotations

import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from .errors import WorkspaceError

logger = logging.getLogger(__name__)

__all__ = ["ConversionWorkspace"]


class ConversionWorkspace:
    """Isolated temporary workspace for one conversion.

    Used as a context manager: on entry a temporary directory is
    created and its path is available via :attr:`path`; on exit
    (normal or exceptional) the directory and all of its contents are
    removed deterministically.
    """

    __slots__ = ("_path", "_temp_dir")

    def __init__(self) -> None:
        self._path: Path | None = None
        self._temp_dir: TemporaryDirectory[str] | None = None

    @property
    def path(self) -> Path:
        """The workspace directory path.

        Available after context entry and until cleanup. Accessing it
        before entry or after cleanup raises :class:`RuntimeError`.
        """
        if self._path is None:
            raise RuntimeError("workspace not created (use as a context manager)")
        return self._path

    def __enter__(self) -> ConversionWorkspace:
        try:
            self._temp_dir = TemporaryDirectory()
        except OSError as exc:
            raise WorkspaceError(
                f"could not create a temporary workspace: {exc}"
            ) from exc
        self._path = Path(self._temp_dir.name)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Remove the workspace directory and all of its contents.

        Safe to call more than once: subsequent calls are a no-op.
        Cleanup failures are logged but never raised (see the
        cleanup-failure semantics in the milestone specification).
        """
        if self._temp_dir is None:
            return
        try:
            self._temp_dir.cleanup()
        except Exception as exc:
            logger.warning("workspace cleanup partially failed: %s", exc)
        finally:
            self._temp_dir = None
            self._path = None
