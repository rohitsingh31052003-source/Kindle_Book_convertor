"""OCR processing (Milestone 3.3).

This module consumes the M3.2 renderer output -- :class:`RenderedPage`
values -- and converts each page raster into recognized text through a small,
explicit OCR abstraction. It is the ``PDF -> RenderedPage -> OCRResult``
boundary described by M3.3:

    renderer.render_page(...)   or   renderer.render_pages(...)
              |
              v
         RenderedPage           (from the M3.2 renderer, never reopened here)
              |
              v
        ocr.ocr_page(...)       or   ocr.ocr_pages(...)
              |
              v
          OCREngine.recognize(image)  ->  str
              |
              v
            OCRResult(page_number, text)

Deliberate scope (M3.3 ends at ``image -> recognized text``):

* **No OCR cleanup**: no whitespace/Unicode normalization, hyphenation
  repair, ligature normalization, repeated header/footer removal, typo
  correction, or confidence filtering. The engine's output is returned
  exactly as the engine produces it.
* **No structural reconstruction**: no paragraphs, headings, lists,
  tables, chapters, or EPUB structure. OCR output remains a raw text
  result for later milestones (M3.4/M3.5) to consume.
* **No routing**: this module does not decide which pages should be OCR'd;
  it only provides the capability. ``extract_book`` still refuses scanned
  and mixed PDFs.
* **No cloud / no LLM**: OCR runs fully locally through the injected
  engine.

OCR engines are injected so deterministic application-level code never
depends on an external OCR executable:

* :func:`ocr_page` / :func:`ocr_pages` take an :class:`OCREngine` argument.
  Callers (including tests) supply a fake/custom engine; no hidden global
  engine is created.
* :class:`TesseractEngine` is the built-in engine backed by the **Tesseract
  OCR executable** through the small ``pytesseract`` wrapper. The
  ``pytesseract``/``Pillow`` Python packages are an *optional* dependency
  (the ``ocr`` extra); the Tesseract executable itself is an **external
  runtime requirement** that is never downloaded or installed by this
  project. It is discovered on ``PATH`` (pytesseract's default) or via an
  explicit ``tesseract_cmd`` path.

Configuring TesseractEngine:

    engine = TesseractEngine(language="eng",
                             tesseract_cmd="C:/path/to/tesseract.exe")
    result = ocr_page(rendered, engine)

Lifecycle: :class:`TesseractEngine` is a cheap, effectively stateless
configuration object (language + executable path). It can be constructed
once and reused across pages. There is no global mutable OCR state, no
caching, and no concurrency: pages are processed sequentially.

One pytesseract API limitation is handled inside the engine: pytesseract
exposes the Tesseract executable path only as a module global, so the
engine pins that global to a deterministic target (the explicit
``tesseract_cmd``, or the ``"tesseract"`` default) on every invocation.
This is the OCR layer's only global touch and is confined to
:class:`TesseractEngine`; the application-level :func:`ocr_page` /
:func:`ocr_pages` behave purely through dependency injection.
"""

from __future__ import annotations

import io
import os
import shutil
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol

import pymupdf

from .renderer import RenderedPage

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

#: Default OCR language. ``eng`` is Tesseract's universally available
#: traineddata; automatic language detection is deliberately not
#: implemented (make language explicit and deterministic).
DEFAULT_OCR_LANGUAGE = "eng"

#: Tesseract page segmentation mode passed to the engine. Mode 3 (fully
#: automatic page segmentation, no orientation/skew detection) is the
#: general-purpose Tesseract default and works well for rendered book
#: pages. It is an internal default, not part of the public configuration.
_OCR_PAGE_SEGMENTATION_MODE = 3

# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class OCRError(Exception):
    """Base class for OCR engine/runtime failures.

    Caller-argument problems (bad page object, bad image, bad language
    string) raise ``TypeError``/``ValueError`` instead, matching the
    package's argument-validation style. ``OCRError`` is reserved for
    failures *while running OCR*: missing executable, missing optional
    dependencies, engine invocation failures.
    """


class OCREngineUnavailableError(OCRError):
    """Raised when the OCR engine cannot be invoked at all.

    Either the optional OCR dependencies (``pytesseract``/``Pillow``) are
    not installed, or the Tesseract executable cannot be found/started.
    Nothing is downloaded or installed automatically.
    """


# --------------------------------------------------------------------------- #
# Result representation
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class OCRResult:
    """Recognized text for one rendered page (immutable value object).

    ``page_number`` is the 1-based physical PDF page index carried over
    unchanged from the :class:`~kindle_converter.pdf.renderer.RenderedPage`.
    ``text`` is the raw, unmodified text returned by the OCR engine; M3.3
    deliberately performs no cleanup or structural reconstruction on it.
    """

    page_number: int
    text: str


# --------------------------------------------------------------------------- #
# Engine abstraction
# --------------------------------------------------------------------------- #


class OCREngine(Protocol):
    """Protocol for any local OCR engine.

    An engine converts a raster image into recognized text. The image is the
    RGB pixmap payload of a :class:`~kindle_converter.pdf.renderer.RenderedPage`
    (``RenderedPage.image``) -- the exact raster produced by the M3.2
    renderer. Engines must return the recognized text as ``str`` without
    internal application-level cleanup.

    This abstraction is deliberately small so a future/alternative engine
    can replace the implementation without touching the PDF pipeline.
    """

    def recognize(self, image: pymupdf.Pixmap) -> str:
        """Recognize text in ``image`` and return it as a raw string."""
        ...


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #


def ocr_page(rendered_page: RenderedPage, engine: OCREngine) -> OCRResult:
    """Recognize the text of one rendered page.

    Parameters
    ----------
    rendered_page:
        A :class:`~kindle_converter.pdf.renderer.RenderedPage` produced by the
        M3.2 renderer. The page is consumed as-is: the PDF is never reopened
        and no pixel is re-rendered here.
    engine:
        An :class:`OCREngine` (e.g. :class:`TesseractEngine`). The engine is
        injected so callers can supply a fake/custom engine; no default
        engine is created implicitly.

    Returns
    -------
    OCRResult
        The recognized raw text plus the preserved 1-based page number.

    Raises
    ------
    TypeError
        If ``rendered_page`` is not a ``RenderedPage``, its ``image`` is not
        a PyMuPDF pixmap, or the engine returns a non-``str`` result.
    ValueError
        If the page image is missing (``None``) or has non-positive
        dimensions.
    OCRError (or a subclass such as ``OCREngineUnavailableError``)
        If the OCR engine fails, with the affected page number in the
        message.

    Examples
    --------
    >>> from kindle_converter.pdf import render_page, ocr_page, TesseractEngine
    >>> rendered = render_page("scan.pdf", page_number=3)
    >>> ocr_page(rendered, TesseractEngine())
    OCRResult(page_number=3, text='...')
    """
    rendered = _validate_rendered_page(rendered_page)
    _validate_engine(engine)
    try:
        text = engine.recognize(rendered.image)
    except OCRError as exc:
        raise type(exc)(
            f"OCR failed on page {rendered.page_number}: {exc}"
        ) from exc
    if not isinstance(text, str):
        raise TypeError(
            f"OCR engine returned {type(text).__name__}, expected str"
        )
    return OCRResult(page_number=rendered.page_number, text=text)


def ocr_pages(
    rendered_pages: Iterable[RenderedPage], engine: OCREngine
) -> Iterator[OCRResult]:
    """Recognize every rendered page, in order, one page at a time.

    This is the multi-page convenience layer over :func:`ocr_page`,
    designed to be fed directly from :func:`render_pages`:

        for result in ocr_pages(render_pages(source), engine):
            ...

    Pages are processed sequentially in the order given; each result keeps
    its own ``RenderedPage.page_number``. Results are produced lazily as a
    generator, so a large book is never fully resident in OCR memory at
    once. Failures are never silently skipped: an OCR failure propagates
    with the affected page number and stops iteration.

    Parameters
    ----------
    rendered_pages:
        An iterable of :class:`~kindle_converter.pdf.renderer.RenderedPage`
        (typically the output of :func:`render_pages`).
    engine:
        An :class:`OCREngine` to use for every page.

    Returns
    -------
    Iterator[OCRResult]
        One result per rendered page, in input order.

    Raises
    ------
    TypeError
        If ``rendered_pages`` is not an iterable of ``RenderedPage``, or the
        engine is not a valid :class:`OCREngine`.
    ValueError
        If a page image is missing or has non-positive dimensions.
    OCRError (or a subclass such as ``OCREngineUnavailableError``)
        If OCR fails on any page, with that page's number in the message.
    """
    _validate_engine(engine)
    if isinstance(rendered_pages, (str, bytes)) or not hasattr(
        rendered_pages, "__iter__"
    ):
        raise TypeError(
            "rendered_pages must be an iterable of RenderedPage, got "
            f"{type(rendered_pages).__name__}"
        )
    for rendered_page in rendered_pages:
        yield ocr_page(rendered_page, engine)


# --------------------------------------------------------------------------- #
# Tesseract engine
# --------------------------------------------------------------------------- #


class TesseractEngine:
    """Local OCR engine backed by the Tesseract OCR executable.

    This engine recognizes text with **Tesseract** through the small
    ``pytesseract`` wrapper. Two distinct requirements are involved and are
    deliberately kept explicit:

    * **Python packages (optional)**: ``pytesseract`` and ``Pillow`` are the
      project's optional ``ocr`` extra. Constructing this engine without
      them raises :class:`OCREngineUnavailableError`; install with
      ``pip install -e ".[ocr]"``.
    * **Tesseract executable (external runtime requirement)**: this project
      never downloads or installs the Tesseract binary. The engine discovers
      it on ``PATH`` (pytesseract's default ``tesseract`` command) or uses
      the explicit ``tesseract_cmd`` path, and raises
      :class:`OCREngineUnavailableError` when it cannot be found.

    Lifecycle: the engine is a cheap, effectively stateless configuration
    object (language + executable path) safe to build once and reuse across
    any number of pages. No caching, no global mutable OCR state, and no
    concurrency are introduced.

    Configuration is intentionally small: only the OCR language and the
    executable location are exposed. Page segmentation uses a fixed
    general-purpose internal default suitable for book pages; automatic
    language detection and document-specific tuning are not implemented.

    Parameters
    ----------
    language:
        Tesseract language code(s), e.g. ``"eng"`` or ``"eng+deu"``.
        Must be a non-empty string; automatic language detection is not
        supported.
    tesseract_cmd:
        Optional explicit path (``str`` / ``os.PathLike``) to the
        ``tesseract`` executable. When ``None``, discovery uses ``PATH`` via
        pytesseract's default command. No machine-specific default path is
        hard-coded.

    Raises
    ------
    ValueError
        If ``language`` is not a non-empty string or ``tesseract_cmd`` is
        not a path-like value.
    OCREngineUnavailableError
        If the optional ``pytesseract``/``Pillow`` packages are not
        installed, or the Tesseract executable cannot be located (checked
        eagerly at construction when a path is given, and when running for
        ``PATH`` discovery).
    """

    def __init__(
        self,
        *,
        language: str = DEFAULT_OCR_LANGUAGE,
        tesseract_cmd: str | os.PathLike[str] | None = None,
    ) -> None:
        self._language = _validate_language(language)
        self._tesseract_cmd = _validate_tesseract_cmd(tesseract_cmd)
        pytesseract, pil_image = _require_ocr_dependencies()
        if self._tesseract_cmd is not None:
            _require_tesseract_executable(self._tesseract_cmd)
        self._pytesseract = pytesseract
        self._pil_image = pil_image

    def recognize(self, image: pymupdf.Pixmap) -> str:
        """Run Tesseract on ``image`` and return the raw recognized text.

        The image is the RGB pixmap from a
        :class:`~kindle_converter.pdf.renderer.RenderedPage`. It is
        serialized to PNG and handed to the Tesseract executable via
        pytesseract; the returned text is **not** cleaned, normalized, or
        post-processed in any way (M3.3 deliberately stops at raw OCR
        output).

        Raises
        ------
        ValueError
            If ``image`` is ``None`` or has non-positive dimensions.
        TypeError
            If ``image`` is not a PyMuPDF pixmap.
        OCREngineUnavailableError
            If the Tesseract executable is not found on ``PATH``.
        OCRError
            If the image cannot be serialized/decoded for OCR, or Tesseract
            fails to run (for example an unavailable language).
        """
        image = _validate_image(image)
        pytesseract = self._pytesseract
        # pytesseract exposes the executable path only as its module global;
        # the engine pins it to a deterministic target on every call (the
        # explicit tesseract_cmd, or the "tesseract" default for PATH
        # discovery) so a previously configured path can never leak between
        # callers. This is the sole global mutation of the OCR layer and is
        # confined to this engine.
        if self._tesseract_cmd is not None:
            try:
                pytesseract.pytesseract.tesseract_cmd = self._tesseract_cmd
            except Exception:  # pragma: no cover - defensive assignment
                pass
        elif shutil.which("tesseract") is None:
            raise OCREngineUnavailableError(
                "The Tesseract executable 'tesseract' was not found on PATH; "
                "install Tesseract or pass an explicit tesseract_cmd to "
                "TesseractEngine."
            )
        try:
            pil_image = self._pixmap_to_pil(image)
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError("Failed to prepare the rendered page for OCR") from exc
        try:
            text = pytesseract.image_to_string(
                pil_image,
                lang=self._language,
                config=f"--psm {_OCR_PAGE_SEGMENTATION_MODE}",
            )
        except pytesseract.TesseractNotFoundError as exc:
            raise OCREngineUnavailableError(
                f"Tesseract executable was not found: {self._tesseract_cmd or 'tesseract'!r}"
            ) from exc
        except pytesseract.TesseractError as exc:
            raise OCRError(f"Tesseract failed: {exc}") from exc
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(
                "Failed to run Tesseract on the rendered page"
            ) from exc
        return text

    def _pixmap_to_pil(self, image: pymupdf.Pixmap) -> object:
        """Serialize the RGB pixmap to PNG and decode it as a PIL image."""
        try:
            png_bytes = image.tobytes("png")
        except Exception as exc:
            raise OCRError(
                "Failed to serialize the rendered page image as PNG"
            ) from exc
        try:
            pil_image = self._pil_image.open(io.BytesIO(png_bytes))
            pil_image.load()
        except Exception as exc:
            raise OCRError(
                "Failed to decode the rendered page image for OCR"
            ) from exc
        return pil_image


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #


def _validate_rendered_page(rendered_page: object) -> RenderedPage:
    """Return ``rendered_page`` as a validated :class:`RenderedPage`.

    Non-``RenderedPage`` inputs raise :class:`TypeError`; missing or invalid
    images raise :class:`ValueError`/``TypeError`` via :func:`_validate_image`.
    """
    if not isinstance(rendered_page, RenderedPage):
        raise TypeError(
            "rendered_page must be a RenderedPage (produced by "
            "render_page/render_pages), got "
            f"{type(rendered_page).__name__}"
        )
    _validate_image(rendered_page.image)
    return rendered_page


def _validate_image(image: object) -> pymupdf.Pixmap:
    """Return ``image`` as a validated PyMuPDF pixmap.

    * ``None`` (missing image) -> :class:`ValueError`
    * wrong type (unsupported representation) -> :class:`TypeError`
    * non-positive width/height (invalid dimensions) -> :class:`ValueError`
    """
    if image is None:
        raise ValueError("rendered page has no image (image is None)")
    if not isinstance(image, pymupdf.Pixmap):
        raise TypeError(
            f"rendered page image must be a pymupdf.Pixmap, got "
            f"{type(image).__name__}"
        )
    if image.width <= 0 or image.height <= 0:
        raise ValueError(
            f"rendered page image must have positive dimensions, got "
            f"{image.width}x{image.height}"
        )
    return image


def _validate_engine(engine: object) -> None:
    """Validate that ``engine`` provides a callable ``recognize(image)``."""
    recognize = getattr(engine, "recognize", None) if engine is not None else None
    if not callable(recognize):
        raise TypeError(
            "engine must be an OCREngine providing recognize(image) -> str, "
            f"got {type(engine).__name__ if engine is not None else 'None'}"
        )


def _validate_language(language: object) -> str:
    """Return ``language`` as a non-empty string (else :class:`ValueError`)."""
    if isinstance(language, bool) or not isinstance(language, str):
        raise ValueError(
            f"language must be a non-empty string, got "
            f"{type(language).__name__}"
        )
    if not language.strip():
        raise ValueError("language must not be empty")
    return language


def _validate_tesseract_cmd(tesseract_cmd: object) -> str | None:
    """Return ``tesseract_cmd`` as a path string or ``None``."""
    if tesseract_cmd is None:
        return None
    if isinstance(tesseract_cmd, bool) or not isinstance(
        tesseract_cmd, (str, os.PathLike)
    ):
        raise ValueError(
            "tesseract_cmd must be a path-like value or None, got "
            f"{type(tesseract_cmd).__name__}"
        )
    return str(tesseract_cmd)


# --------------------------------------------------------------------------- #
# Optional dependency / executable discovery
# --------------------------------------------------------------------------- #


def _require_ocr_dependencies() -> tuple[object, object]:
    """Import and return ``(pytesseract, PIL.Image)``.

    The ``pytesseract`` and ``Pillow`` Python packages are optional (the
    ``ocr`` extra). When either is missing, a clear
    :class:`OCREngineUnavailableError` explains the install command. This is
    the only place the optional packages are imported, so ``kindle_converter``
    imports cleanly without the OCR extra.
    """
    try:
        import pytesseract
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OCREngineUnavailableError(
            "pytesseract is not installed. TesseractEngine needs the 'ocr' "
            "optional dependencies: pip install -e \".[ocr]\""
        ) from exc
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise OCREngineUnavailableError(
            "Pillow is not installed. TesseractEngine needs the 'ocr' "
            "optional dependencies: pip install -e \".[ocr]\""
        ) from exc
    return pytesseract, Image


def _require_tesseract_executable(path: str) -> None:
    """Fail eagerly when an explicitly configured executable path is unusable.

    ``path`` is an explicit ``tesseract_cmd`` value: accept a path to an
    existing file, or an executable name resolvable on ``PATH``. Nothing is
    silently downloaded or installed; a missing executable raises
    :class:`OCREngineUnavailableError` with actionable guidance.
    """
    if os.path.isfile(path):
        return
    if os.path.dirname(path) == "" and shutil.which(path) is not None:
        return
    raise OCREngineUnavailableError(
        f"Tesseract executable {path!r} was not found. Install Tesseract or "
        "pass tesseract_cmd pointing to the tesseract executable."
    )