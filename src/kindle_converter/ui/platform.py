"""Small platform-opening seam for the M5.6 output actions.

:func:`open_path` launches the operating system's default handler for a file or
folder (``os.startfile`` on Windows, the ``open`` command on macOS, and
``xdg-open`` on Linux). It is deliberately isolated in the UI layer -- the
core conversion domain never launches external programs -- and it is small
enough to be replaced wholesale by tests: :class:`MainWindow` accepts an
injectable ``opener`` callable, so UI tests verify the exact path an action
would open without ever launching a real application.

All platform-specific failures are translated into a single
:class:`PlatformOpenError` instead of leaking raw OS exceptions to the UI.
Existence of the target is deliberately *not* asserted here: the caller (the
output action in the main window) performs that check so it can give a precise
user-facing message for a missing output.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

__all__ = ["PlatformOpenError", "open_path"]


class PlatformOpenError(Exception):
    """The platform "open" operation could not be performed.

    Raised when the current platform has no usable open handler (for example
    the ``xdg-open``/``open`` command is missing) or the underlying OS call
    fails. The window catches it to show a concise user-facing message; it is
    never a raw platform exception.
    """


def open_path(path: Path) -> None:
    """Open ``path`` (a file or directory) with the OS default application.

    Parameters
    ----------
    path:
        The file or directory to open. It is not asserted to exist here; the
        caller performs that check so it can choose the right message.

    Raises
    ------
    PlatformOpenError
        The platform has no usable handler, or the underlying launch call
        failed.
    """
    try:
        _open_with_default_handler(path)
    except FileNotFoundError as exc:
        raise PlatformOpenError(
            f"no system handler is available to open {str(path)!r}: {exc}"
        ) from exc
    except OSError as exc:
        raise PlatformOpenError(f"could not open {str(path)!r}: {exc}") from exc


def _open_with_default_handler(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(str(path))  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])