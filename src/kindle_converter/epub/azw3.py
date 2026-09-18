"""EPUB -> AZW3 conversion through an external conversion backend (M4.3).

This module is the output/conversion layer that turns a finished EPUB
artifact into an AZW3 (Kindle) ebook::

    build_epub() -> EPUB -> convert_epub_to_azw3() -> AZW3

Like the M4.2 validator, the AZW3 layer is deliberately not part of EPUB
generation and never part of PDF processing:

* It consumes an EPUB **artifact** (a filesystem path) -- never a PDF and
  never a :class:`~kindle_converter.document.models.Book`.
* It imports no PDF, OCR, routing, or reconstruction code, and it performs
  no EPUB validation of its own: validation stays a separate step
  (:func:`~kindle_converter.epub.validation.validate_epub`), exactly as it
  was in M4.2.
* It never revisits the document-processing boundary. The architecture
  remains ``PDF -> Book -> EPUB -> AZW3``; AZW3 conversion only ever sees
  the EPUB artifact.

Calibre is an **optional, external** system dependency (M4.3 policy): the
package stays importable and the whole EPUB stack keeps working without it.
AZW3 conversion uses Calibre's ``ebook-convert`` command through the small
:class:`~kindle_converter.epub.calibre.CalibreBackend` adapter -- the AZW3
format is never implemented in Python. If Calibre is missing or fails, a
dedicated project error is raised instead of a raw low-level exception.

Validation guarantee (M4.3)
---------------------------
A *successful* conversion means exactly that:

1. the conversion backend exited successfully (for Calibre: return code 0), and
2. the requested output artifact exists, is a regular file, and is non-empty.

That is **not** a guarantee of Kindle rendering correctness, Kindle
marketplace acceptance, or even that the bytes form a valid AZW3 file. No
AZW3 parser or Kindle compatibility validator is implemented in M4.3.
Callers that need byte-level guarantees must validate after conversion.

Determinism (M4.3)
------------------
Command construction is deterministic: the same executable, source, and
destination always produce the same argument list
(:func:`~kindle_converter.epub.calibre.build_calibre_command`), passed to
the backend with no shell and no shell quoting. Byte-for-byte deterministic
AZW3 output is **not** required in this milestone: Calibre itself may stamp
internal metadata and timestamps into the artifact. Only the conversion
invocation is deterministic here.

Out of scope (M4.3)
-------------------
No Kindle Previewer integration, Amazon publishing integration, MOBI or KFX
conversion, cover handling, advanced Kindle formatting, GUI, EPUB repair, or
automatic EPUB validation.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TypeAlias

PathLike: TypeAlias = str | os.PathLike[str]

#: Accepted conversion input: a filesystem path to an ``.epub`` file. Bytes
#: are deliberately not accepted; the conversion backend runs on disk paths.
Source: TypeAlias = str | os.PathLike[str]


# --------------------------------------------------------------------------- #
# Public error taxonomy
# --------------------------------------------------------------------------- #


class AZW3ConversionError(Exception):
    """Base class for all AZW3 conversion failures.

    Every M4.3 conversion failure is an instance of this class (or a
    subclass), so callers can catch one type and react to the specific
    subclass when they need detail. Raw ``FileNotFoundError`` /
    ``CalledProcessError`` are never surfaced as the public API.
    """


class AZW3BackendUnavailableError(AZW3ConversionError):
    """The conversion backend (Calibre's ``ebook-convert``) cannot be used.

    Raised when ``calibre_path`` names an executable that does not exist,
    when no explicit path is supplied and ``ebook-convert`` cannot be found
    on ``PATH``, or when the executable exists but cannot be launched.
    """


class AZW3ConversionFailedError(AZW3ConversionError):
    """The conversion backend ran but reported failure (non-zero exit).

    The message carries the source, destination, return code, and a bounded
    excerpt of the subprocess output -- never the full output, and never any
    environment or sensitive information.
    """


class AZW3InvalidInputError(AZW3ConversionError):
    """The supplied EPUB fails basic input requirements.

    Raised when the source is not a filesystem path, does not exist, is a
    directory, or cannot be read. Invalid *content* is not inspected here;
    structural EPUB validation is a separate step
    (:func:`~kindle_converter.epub.validation.validate_epub`).
    """


class AZW3InvalidOutputError(AZW3ConversionError):
    """The backend reported success but no usable AZW3 artifact was produced.

    Raised after conversion when the destination does not exist, is not a
    regular file, or is empty (zero bytes).
    """


# --------------------------------------------------------------------------- #
# Backend abstraction
# --------------------------------------------------------------------------- #


class AZW3ConversionBackend(Protocol):
    """A conversion engine that turns an EPUB artifact into an AZW3 file.

    The abstraction keeps :func:`convert_epub_to_azw3` free of subprocess
    logic: the production implementation is the Calibre adapter
    (:class:`~kindle_converter.epub.calibre.CalibreBackend`), and tests
    inject deterministic fake backends. The high-level API never invokes
    ``subprocess.run`` itself.
    """

    def convert(self, source: Path, destination: Path) -> None:
        """Convert ``source`` (an existing EPUB file) into ``destination``.

        Must raise :class:`AZW3BackendUnavailableError` when the backend
        cannot run and :class:`AZW3ConversionFailedError` when it runs but
        fails. Whether ``destination`` ends up populated is verified by the
        caller after this method returns.
        """


# --------------------------------------------------------------------------- #
# Public result model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class AZW3ConversionResult:
    """The immutable outcome of :func:`convert_epub_to_azw3`.

    The result makes a successful conversion explicit: the backend finished
    successfully and ``destination`` was verified to exist as a non-empty
    regular file. It carries no metadata beyond the two paths: everything
    Calibre learned about the book lives inside the artifact, not here.
    """

    #: The EPUB source artifact that was converted.
    source: Path

    #: The AZW3 output artifact that was produced and verified.
    destination: Path


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def convert_epub_to_azw3(
    source: Source,
    destination: PathLike,
    *,
    calibre_path: PathLike | None = None,
    backend: AZW3ConversionBackend | None = None,
) -> AZW3ConversionResult:
    """Convert an EPUB file into an AZW3 file and verify the output.

    This is an explicit, standalone step on top of the existing pipeline::

        build_epub() -> EPUB -> convert_epub_to_azw3() -> AZW3

    It consumes a finished EPUB artifact and never a PDF or ``Book``, and it
    does not chain automatically into :func:`convert_pdf_to_epub` -- callers
    compose ``PDF -> Book -> EPUB -> AZW3`` themselves when they need the
    full chain.

    Parameters
    ----------
    source:
        A filesystem path (``str``, ``pathlib.Path``, or ``os.PathLike``) to
        an existing, readable ``.epub`` file. Bytes are not accepted; the
        backend operates on filesystem paths.
    destination:
        A filesystem path (``str``, ``pathlib.Path``, or ``os.PathLike``)
        where the ``.azw3`` file is written. Parent directories must already
        exist; they are not created. An existing file is overwritten.
    calibre_path:
        Optional explicit path to the Calibre ``ebook-convert`` executable.
        When ``None`` (the default) the executable is discovered on the
        platform ``PATH``.
    backend:
        Optional :class:`AZW3ConversionBackend`. When given, it is used
        unchanged and ``calibre_path`` is ignored; when ``None`` (the
        default) a
        :class:`~kindle_converter.epub.calibre.CalibreBackend` is used.
        Injectable so conversion is testable without Calibre.

    Returns
    -------
    AZW3ConversionResult
        The verified source/destination pair. Returning (rather than
        ``None``) signals that the backend finished successfully **and** the
        output artifact exists, is a regular file, and is non-empty.

    Raises
    ------
    AZW3InvalidInputError
        If ``source`` is not a filesystem path, does not exist, is a
        directory, or cannot be read.
    AZW3BackendUnavailableError
        If the Calibre ``ebook-convert`` executable cannot be found or
        launched.
    AZW3ConversionFailedError
        If Calibre ran but returned a non-zero exit code.
    AZW3InvalidOutputError
        If Calibre reported success but the output is missing, is not a
        regular file, or is empty.

    Examples
    --------
    >>> from kindle_converter.epub import convert_epub_to_azw3
    >>> result = convert_epub_to_azw3("book.epub", "book.azw3")
    >>> result.destination
    WindowsPath('book.azw3')
    >>> convert_epub_to_azw3(
    ...     "book.epub",
    ...     "book.azw3",
    ...     calibre_path=r"C:\\Program Files\\Calibre2\\ebook-convert.exe",
    ... )
    """
    source_path = _resolve_source(source)
    destination_path = _resolve_destination(destination)
    if backend is None:
        from .calibre import CalibreBackend

        backend = CalibreBackend(executable=calibre_path)
    backend.convert(source_path, destination_path)
    _verify_output(destination_path)
    return AZW3ConversionResult(source=source_path, destination=destination_path)


# --------------------------------------------------------------------------- #
# Private validation helpers
# --------------------------------------------------------------------------- #


def _as_path(value: PathLike) -> Path:
    """Normalize a path-like value to a :class:`pathlib.Path`."""
    raw = os.fspath(value)
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    return Path(raw)


def _resolve_source(source: Source) -> Path:
    """Validate and normalize the EPUB source path.

    ``AZW3InvalidInputError`` is raised -- never a raw ``OSError`` or
    ``TypeError`` -- for the basic input failures this milestone defines:
    a non-path source, a missing file, a directory, or an unreadable file.
    """
    try:
        path = _as_path(source)
    except TypeError:
        raise AZW3InvalidInputError(
            f"convert_epub_to_azw3 expects a filesystem path for source, got "
            f"{type(source).__name__}"
        ) from None
    if not path.exists():
        raise AZW3InvalidInputError(
            f"the EPUB source {str(path)!r} does not exist; AZW3 conversion "
            "requires an existing, readable EPUB file"
        )
    if not path.is_file():
        raise AZW3InvalidInputError(
            f"the EPUB source {str(path)!r} is not a regular file; AZW3 "
            "conversion requires an existing, readable EPUB file"
        )
    try:
        with path.open("rb"):
            pass
    except OSError as exc:
        raise AZW3InvalidInputError(
            f"cannot read the EPUB source {str(path)!r}: {exc}; AZW3 "
            "conversion requires an existing, readable EPUB file"
        ) from exc
    return path


def _resolve_destination(destination: PathLike) -> Path:
    """Normalize the destination path, rejecting a non-path value cleanly."""
    try:
        return _as_path(destination)
    except TypeError:
        raise AZW3InvalidInputError(
            f"convert_epub_to_azw3 expects a filesystem path for "
            f"destination, got {type(destination).__name__}"
        ) from None


def _verify_output(destination: Path) -> None:
    """Verify that a usable AZW3 artifact exists at ``destination``.

    The M4.3 validation guarantee is deliberately shallow: a successful
    conversion plus a non-empty regular file at the destination. Verifying
    Kindle rendering correctness (or parsing the AZW3 structure) is out of
    scope and is never claimed here.
    """
    if not destination.exists():
        raise AZW3InvalidOutputError(
            f"the conversion backend reported success, but no output was "
            f"created at {str(destination)!r}"
        )
    if not destination.is_file():
        raise AZW3InvalidOutputError(
            f"the output at {str(destination)!r} is not a regular file; "
            "expected an AZW3 file"
        )
    if destination.stat().st_size == 0:
        raise AZW3InvalidOutputError(
            f"the output at {str(destination)!r} is empty (0 bytes); "
            "expected a non-empty AZW3 file"
        )