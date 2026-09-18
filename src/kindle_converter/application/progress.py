"""UI-independent progress reporting for the application pipeline (M5.1).

A :class:`ConversionProgress` value is handed to an optional
``progress_callback`` during a conversion. The application layer never imports
or names any UI framework: the contract is a plain callable over a plain,
frozen data object, so the same code serves a future desktop UI, a CLI, a test,
or any other Python caller.

Progress reporting is **optional** (a converter called without a callback
behaves identically -- progress never alters conversion behavior) and
**deterministic**: a given request always produces the same event sequence.

Stage model
-----------
The stages reflect the *observable* work this layer performs -- the public
stage functions it delegates to:

* :attr:`ConversionStage.ANALYSIS` -- first-pass PDF analysis
  (:func:`kindle_converter.pdf.analyze_pdf`).
* :attr:`ConversionStage.EPUB` -- the unified PDF -> Book -> EPUB conversion
  (:func:`kindle_converter.convert_pdf_to_epub`), which performs routing, OCR,
  reconstruction, and EPUB generation as a single black box to this layer.
* :attr:`ConversionStage.VALIDATION` -- structural EPUB validation
  (:func:`kindle_converter.epub.validate_epub`).
* :attr:`ConversionStage.AZW3` -- EPUB -> AZW3 conversion
  (:func:`kindle_converter.epub.convert_epub_to_azw3`).
* :attr:`ConversionStage.COMPLETE` -- a single terminal event.

The M1--M4 modules do not report per-page or sub-stage progress, and exposing
OCR/page/reconstruction progress would require re-implementing or modifying
those stages -- which M5.1 deliberately avoids. Finer-grained progress may be
added later without breaking this contract; a stage that does not measure
sub-steps simply reports ``total == 0`` ("not measurable").

Event protocol
--------------
Each stage that runs emits one *start* event followed by one *finish* event,
except :attr:`ConversionStage.COMPLETE`, which emits a single terminal event.
On a stage that fails, only its start event is emitted; COMPLETE is never
emitted on failure. A start event carries ``current == 0`` and ``total`` equal
to the stage's measurable sub-step count (``0`` when not measurable); a finish
event carries ``current == total``. A caller can therefore distinguish "stage
started", "stage finished", and "conversion complete" from the
(stage, current, total) triple.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Callable, TypeAlias

__all__ = [
    "ConversionProgress",
    "ConversionStage",
    "ProgressCallback",
]


class ConversionStage(StrEnum):
    """The coarse, observable stages of a conversion.

    Values are stable, lowercase identifiers suitable for serialization and for
    a future UI to map onto progress-bar steps. They describe *this layer's*
    orchestration, not the internal M1--M4 sub-stages.
    """

    #: First-pass analysis of the input PDF (PDF type, page count, ...).
    ANALYSIS = "analysis"

    #: Unified PDF -> Book -> EPUB conversion (routing, OCR, reconstruction,
    #: EPUB generation) -- a single black box to this layer.
    EPUB = "epub"

    #: Structural validation of the generated EPUB artifact (M4.2).
    VALIDATION = "validation"

    #: Optional EPUB -> AZW3 conversion (M4.3).
    AZW3 = "azw3"

    #: Terminal event emitted once every requested stage finished.
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class ConversionProgress:
    """A single, immutable progress event for a conversion.

    ``stage`` identifies which observable stage the event belongs to.
    ``message`` is a short, deterministic, human-readable, *display-only*
    description -- never parsed for behavior. ``current`` and ``total``
    describe sub-step progress within the stage: both ``0`` means the stage
    does not report a measurable sub-step count. See the module docstring for
    the start/finish/complete protocol a caller can rely on.
    """

    #: Which observable stage this event belongs to.
    stage: ConversionStage

    #: A short, display-only description of what is happening.
    message: str = ""

    #: Completed sub-steps within the stage, or ``0`` when not measurable.
    current: int = 0

    #: Total sub-steps within the stage, or ``0`` when not measurable.
    total: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.stage, ConversionStage):
            raise TypeError(
                f"stage must be a ConversionStage, got {type(self.stage).__name__}"
            )
        if not isinstance(self.message, str):
            raise TypeError(
                f"message must be str, got {type(self.message).__name__}"
            )
        # bool is a subclass of int but is never a valid progress count.
        if isinstance(self.current, bool) or not isinstance(self.current, int):
            raise TypeError(
                f"current must be int, got {type(self.current).__name__}"
            )
        if isinstance(self.total, bool) or not isinstance(self.total, int):
            raise TypeError(
                f"total must be int, got {type(self.total).__name__}"
            )
        if self.current < 0 or self.total < 0:
            raise ValueError(
                f"current and total must be non-negative, got "
                f"current={self.current}, total={self.total}"
            )
        if self.current > self.total:
            raise ValueError(
                f"current ({self.current}) cannot exceed total ({self.total})"
            )

    @property
    def fraction(self) -> float | None:
        """``current / total`` when measurable, else ``None``."""
        if self.total == 0:
            return None
        return self.current / self.total


#: A caller-supplied progress reporter. The application never invokes UI code:
#: it simply hands each :class:`ConversionProgress` to this callable, or does
#: nothing when it is ``None``.
ProgressCallback: TypeAlias = Callable[[ConversionProgress], None]
