"""Conversion request and output-format options (M5.1).

A :class:`ConversionRequest` is the immutable, self-describing description of
everything the application layer needs to perform one conversion: where the
PDF lives, where its outputs go, which formats to produce, and a cover. It
validates its own *structural* self-consistency at construction time
(path-like types, a non-empty set of output formats, and an AZW3-requires-EPUB
rule) and raises :class:`~kindle_converter.application.InvalidRequestError`.
Filesystem-level checks (does the PDF exist? is the output directory writable?)
are performed by the application when ``convert`` runs, also raising
``InvalidRequestError``.

The request contains only user-facing conversion configuration -- it never
exposes low-level knobs like OCR engine choice, render DPI, or internal stage
flags; those have safe defaults and, where a caller needs them (tests),
:class:`~kindle_converter.application.ConversionApplication` accepts injection
seams for them instead.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable, Union

from ..document import Image
from .errors import InvalidRequestError

__all__ = [
    "ConversionRequest",
    "CoverSource",
    "OutputFormat",
    "PathLike",
]

PathLike = str | os.PathLike[str]

#: Accepted cover input: a filesystem path or an in-memory domain image,
#: matching :func:`kindle_converter.document.load_cover`.
CoverSource = Union[PathLike, Image]


class OutputFormat(StrEnum):
    """The output artifacts a conversion may produce.

    A EPUB is always produced (it is both a Kindle-ready endpoint and, when
    AZW3 is requested, the input the AZW3 step consumes). ``azw3`` selects the
    optional EPUB -> AZW3 step (M4.3); it is only valid alongside ``epub``.
    """

    EPUB = "epub"
    AZW3 = "azw3"


@dataclass(slots=True)
class ConversionRequest:
    """An immutable conversion request.

    Parameters
    ----------
    input_pdf:
        A filesystem path to a readable PDF. An open document object is *not*
        accepted: the application works with files.
    output_directory:
        An existing directory the conversion may write into. Parent
        directories are not created; the directory must already exist. Output
        files are named ``<input-stem>.epub`` and ``<input-stem>.azw3`` and
        overwrite any existing file of the same name (matching M4 conventions).
    formats:
        The artifacts the caller is asking for. ``epub`` is always required;
        ``azw3`` is optional and is only valid alongside ``epub`` (an AZW3-only
        request is rejected as an invalid combination). Accepts any iterable of
        :class:`OutputFormat` and normalizes it to an immutable ``frozenset``.
    cover:
        Optional cover for the EPUB (M4.4): a filesystem path to a supported
        image (JPEG, PNG, GIF, or SVG) or an already-loaded
        :class:`~kindle_converter.document.models.Image`. Validated before any
        PDF work runs and fails fast with
        :class:`~kindle_converter.application.InvalidRequestError`.
    validate:
        Whether to run M4.2 structural validation on the generated EPUB. When
        ``False``, ``result.validation`` is ``None`` and validation errors do
        not raise.
    calibre_path:
        Optional explicit path to the Calibre ``ebook-convert`` executable for
        the optional AZW3 step. Only consulted when ``azw3`` is among
        ``formats``; ignored otherwise. When omitted, the executable is
        discovered on the platform ``PATH``.
    """

    input_pdf: PathLike
    output_directory: PathLike
    formats: frozenset[OutputFormat] = field(
        default_factory=lambda: frozenset({OutputFormat.EPUB})
    )
    cover: CoverSource | None = None
    validate: bool = True
    calibre_path: PathLike | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input_pdf, (str, os.PathLike)):
            raise InvalidRequestError(
                "input_pdf must be a filesystem path, got " + type(self.input_pdf).__name__
            )
        if not isinstance(self.output_directory, (str, os.PathLike)):
            raise InvalidRequestError(
                "output_directory must be a filesystem path, got "
                + type(self.output_directory).__name__
            )
        if not isinstance(self.validate, bool):
            raise InvalidRequestError("validate must be a bool")
        if self.calibre_path is not None and not isinstance(
            self.calibre_path, (str, os.PathLike)
        ):
            raise InvalidRequestError(
                "calibre_path must be a filesystem path or None, got "
                + type(self.calibre_path).__name__
            )
        if self.cover is not None and not isinstance(self.cover, Image) and not isinstance(
            self.cover, (str, os.PathLike)
        ):
            raise InvalidRequestError(
                "cover must be a filesystem path or a kindle_converter.document.Image, "
                + "got " + type(self.cover).__name__
            )
        self._normalize_formats()

    def _normalize_formats(self) -> None:
        raw = self.formats
        if isinstance(raw, str) or not isinstance(raw, Iterable):
            raise InvalidRequestError(
                "formats must be an iterable of OutputFormat values, got "
                + type(raw).__name__
            )
        try:
            coerced: frozenset[OutputFormat] = frozenset(raw)  # type: ignore[arg-type]
        except TypeError as exc:
            raise InvalidRequestError(
                "formats must contain only OutputFormat values: " + str(exc)
            ) from exc
        if not coerced:
            raise InvalidRequestError("at least one output format must be selected")
        for fmt in coerced:
            if not isinstance(fmt, OutputFormat):
                raise InvalidRequestError(
                    "formats contains a non-OutputFormat value: " + repr(fmt)
                )
        if OutputFormat.AZW3 in coerced and OutputFormat.EPUB not in coerced:
            raise InvalidRequestError(
                "AZW3 output requires EPUB output to be enabled: conversion "
                "produces the EPUB artifact first and then converts it to AZW3"
            )
        object.__setattr__(self, "formats", coerced)
