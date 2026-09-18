"""Explicit, optional cover input handling (Milestone 4.4).

This module is the input boundary for book covers. It turns a cover source --
a filesystem path or an existing
:class:`~kindle_converter.document.models.Image` -- into a validated,
self-contained :class:`~kindle_converter.document.models.Image` that the
document model carries on ``Book.cover``.

Cover handling (M4.4) is deliberately explicit and conservative:

* a cover is *optional* -- a book without one renders exactly as before;
* only an explicitly supplied source is accepted; no automatic cover
  detection, heuristics, or image classification ever runs;
* the supported image formats mirror the EPUB image pipeline (JPEG, PNG,
  GIF, and SVG), so an accepted cover always has a real media type and the
  EPUB layer never has to guess it;
* the source is never mutated: an already-valid ``Image`` is returned
  unchanged, and a media type is only ever *added* on a fresh copy.

Failures are raised as dedicated :class:`CoverError` subclasses -- never as
raw low-level exceptions -- so callers can react to each cause separately: a
missing file, an unreadable path, an unsupported format, or empty data.
"""

from __future__ import annotations

import os
from typing import TypeAlias

from .models import Image

PathLike: TypeAlias = str | os.PathLike[str]

#: Accepted cover input: a filesystem path or an in-memory domain image.
CoverSource: TypeAlias = PathLike | Image


class CoverError(Exception):
    """Base class for cover input failures (Milestone 4.4)."""


class CoverNotFoundError(CoverError):
    """The cover source path does not exist."""


class CoverUnreadableError(CoverError):
    """The cover source is a directory or cannot be read from disk."""


class CoverUnsupportedFormatError(CoverError):
    """The cover source is not a supported image format."""


class CoverInvalidDataError(CoverError):
    """The cover source contains no usable image data."""


#: Media types this project accepts for a cover. Mirrors the image formats
#: the EPUB builder already renders (JPEG, PNG, GIF, SVG), so every accepted
#: cover can be packaged without any format conversion or image-processing
#: library.
SUPPORTED_COVER_MEDIA_TYPES = frozenset(
    {
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/gif",
        "image/svg+xml",
    }
)

#: The deterministic, dependency-free magic-byte prefixes used to recognize a
#: cover's format when no media type is supplied. This is the same
#: conservative sniffing the EPUB builder applies to content images, kept at
#: the input boundary so an invalid cover is rejected before any EPUB is
#: produced.
_MAGIC_BYTES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"<svg", "image/svg+xml"),
    (b"<?xml", "image/svg+xml"),
)


def load_cover(source: CoverSource) -> Image:
    """Resolve and validate a cover source into a domain :class:`Image`.

    ``source`` is either a filesystem path to a supported image (JPEG, PNG,
    GIF, or SVG) or an existing
    :class:`~kindle_converter.document.models.Image`. The result is always a
    validated ``Image`` whose ``content_type`` is a supported media type.

    An ``Image`` whose ``content_type`` is already supported is returned
    unchanged (never mutated, never copied). An ``Image`` without a media
    type is sniffed from its bytes and returned as a fresh ``Image`` with the
    sniffed type. A path is read and sniffed with the same rules, and
    returned as a fresh ``Image`` (no metadata is ever inferred from the file
    name).

    Parameters
    ----------
    source:
        A filesystem path (``str``, ``pathlib.Path``, or ``os.PathLike``) or
        an :class:`~kindle_converter.document.models.Image`.

    Returns
    -------
    Image
        A validated, self-contained cover image with a supported media type.

    Raises
    ------
    CoverNotFoundError
        If the source path does not exist.
    CoverUnreadableError
        If the source path is a directory or cannot be read.
    CoverInvalidDataError
        If the cover data is empty.
    CoverUnsupportedFormatError
        If the cover data is not a supported image format.
    """
    if isinstance(source, Image):
        return _resolve_image(source)
    return _load_from_path(source)


def _resolve_image(image: Image) -> Image:
    """Validate an in-memory ``Image``, returning it unchanged when valid.

    A supported declared ``content_type`` wins without deep image parsing
    (matching how the EPUB builder treats content images). A missing media
    type is added from the bytes on a *fresh* ``Image``, so the caller's
    object is never mutated.
    """
    if not image.data:
        raise CoverInvalidDataError("the cover image data is empty")
    declared = (image.content_type or "").strip()
    if declared:
        if declared not in SUPPORTED_COVER_MEDIA_TYPES:
            raise CoverUnsupportedFormatError(
                f"unsupported cover image type {declared!r}; supported "
                f"types: {', '.join(sorted(SUPPORTED_COVER_MEDIA_TYPES))}"
            )
        return image
    return Image(
        data=image.data,
        content_type=_sniff(image.data),
        alt_text=image.alt_text,
    )


def _load_from_path(path: PathLike) -> Image:
    """Read and sniff a cover image from the filesystem."""
    resolved = os.fspath(path)
    if isinstance(resolved, bytes):
        resolved = os.fsdecode(resolved)
    if not os.path.exists(resolved):
        raise CoverNotFoundError(f"the cover image does not exist: {resolved!r}")
    if os.path.isdir(resolved):
        raise CoverUnreadableError(
            f"the cover image path is a directory, not a file: {resolved!r}"
        )
    try:
        with open(resolved, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise CoverUnreadableError(
            f"the cover image cannot be read: {resolved!r} ({exc})"
        ) from exc
    if not data:
        raise CoverInvalidDataError(f"the cover image file is empty: {resolved!r}")
    return Image(data=data, content_type=_sniff(data))


def _sniff(data: bytes) -> str:
    """The media type of ``data``, else a :class:`CoverUnsupportedFormatError`."""
    for magic, media_type in _MAGIC_BYTES:
        if data.startswith(magic):
            return media_type
    raise CoverUnsupportedFormatError(
        "cannot determine the cover image format from its bytes; supported "
        f"types: {', '.join(sorted(SUPPORTED_COVER_MEDIA_TYPES))}"
    )