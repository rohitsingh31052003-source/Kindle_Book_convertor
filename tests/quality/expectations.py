"""Expectation schema, parsing, and evaluation for the M6.3 quality framework.

A quality expectation pairs an *authored* claim ("at least 3 chapter
headings survive", "no OCR marker text leaks into the body", "the left-to-right
column reading order of the two-column article is preserved") with an observed,
deterministic measurement of a converted fixture.

Supported expectation types (only what M6.3 genuinely needs):

- ``exact``            observed == expected            (numeric or scalar pin)
- ``minimum``          observed >= expected            (numeric floor)
- ``maximum``          observed <= expected            (numeric ceiling)
- ``contains``         every expected string is present in the body/epub text
- ``excludes``         no expected string is present in the body/epub text
- ``boolean``          bool(observed) == bool(expected)
- ``ordered_sequence`` list(observed) == list(expected)  (reading order)

The ``classification`` dimension is *not* stored in the baseline; it is derived
from the corpus manifest at evaluation time (single source of truth).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

DIMENSIONS: tuple[str, ...] = (
    "classification",
    "structure",
    "reading_order",
    "ocr",
    "images",
    "epub",
)

EXPECTATION_TYPES: frozenset[str] = frozenset(
    {
        "exact",
        "minimum",
        "maximum",
        "contains",
        "excludes",
        "boolean",
        "ordered_sequence",
    }
)

_SCALAR_TYPES: frozenset[str] = frozenset({"exact", "minimum", "maximum"})

# Metric -> observation field. The observation fields are produced by
# ``tests.quality.harness.QualityObservation`` (see ``observed_metrics``).
KNOWN_FIELDS: frozenset[str] = frozenset(
    {
        "classification",
        "paragraphs",
        "headings",
        "chapters",
        "toc_entries",
        "page_breaks",
        "empty_paragraphs",
        "near_empty_paragraphs",
        "body_text",
        "column_tokens",
        "ocr_calls",
        "ocr_text_present",
        "image_blocks",
        "epub_image_resources",
        "epub_valid",
        "epub_chapter_files",
        "epub_heading_tags",
        "epub_nav_entries",
        "epub_text",
        "epub_empty_chapters",
        "epub_page_breaks",
    }
)

# Free-text expectations (contains/excludes) apply to different text surfaces
# depending on the dimension they live in.
_FREE_TEXT_FIELD = {
    "structure": "body_text",
    "reading_order": "column_tokens",
    "ocr": "body_text",
    "images": "body_text",
    "epub": "epub_text",
    "classification": "body_text",
}

# Baseline metric names that differ from the observation field they read.
# The EPUB-dimension metrics read their ``epub_*`` observation counterparts.
_METRIC_ALIASES = {
    "valid": "epub_valid",
    "chapter_files": "epub_chapter_files",
    "heading_tags": "epub_heading_tags",
    "nav_entries": "epub_nav_entries",
    "empty_chapters": "epub_empty_chapters",
}

# Names accepted in the baseline (parse-time vocabulary), derived from the
# observation namespace plus the free-text and aliased metrics.
KNOWN_METRICS: frozenset[str] = frozenset(
    set(KNOWN_FIELDS) | set(_METRIC_ALIASES) | {"contains", "excludes"}
)


class QualityBaselineError(RuntimeError):
    """Raised when the quality baseline is missing, malformed, or unfittable."""


def resolve_observed_field(dimension: str, metric: str) -> str:
    """Return the observation field an expectation is evaluated against."""
    if metric in ("contains", "excludes"):
        return _FREE_TEXT_FIELD[dimension]
    return _METRIC_ALIASES.get(metric, metric)


@dataclass(frozen=True, slots=True)
class Expectation:
    """A parsed, validated quality expectation."""

    dimension: str
    metric: str
    expectation_type: str
    expected: Any
    observed_field: str = field(default="")
    source: str = field(default="baseline")

    def __post_init__(self) -> None:
        object.__setattr__(self, "observed_field", resolve_observed_field(self.dimension, self.metric))
        if not _is_valid_expected(self.expectation_type, self.expected):
            raise QualityBaselineError(
                f"invalid {self.expectation_type!r} expectation value for "
                f"{self.dimension}.{self.metric}: {self.expected!r}"
            )


@dataclass(frozen=True, slots=True)
class ExpectationResult:
    """Outcome of evaluating single expectation against a measurement."""

    dimension: str
    metric: str
    expectation_type: str
    expected: Any
    observed: Any
    passed: bool
    details: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric,
            "type": self.expectation_type,
            "status": "PASS" if self.passed else "FAIL",
            "expected": _json_safe(self.expected),
            "observed": _json_safe(self.observed),
            "details": self.details,
        }


def _is_valid_expected(expectation_type: str, expected: Any) -> bool:
    if expectation_type in _SCALAR_TYPES:
        return isinstance(expected, (int, float)) and not isinstance(expected, bool)
    if expectation_type == "boolean":
        return isinstance(expected, bool)
    if expectation_type in ("contains", "excludes"):
        return isinstance(expected, list) and all(isinstance(item, str) for item in expected)
    if expectation_type == "ordered_sequence":
        return isinstance(expected, list) and all(isinstance(item, (str, int, float)) for item in expected)
    return False


def _json_safe(value: Any) -> Any:
    if isinstance(value, tuple):
        return list(value)
    return value


def parse_expectation(dimension: str, metric: str, raw: Mapping[str, Any]) -> Expectation:
    """Validate and build an ``Expectation`` from a baseline dict entry.

    Raises ``QualityBaselineError`` on malformed or unknown expectations.
    """
    if not isinstance(raw, dict):
        raise QualityBaselineError(
            f"expectation for {dimension}.{metric} must be an object, got {type(raw).__name__}"
        )
    if "type" not in raw or "expected" not in raw:
        raise QualityBaselineError(
            f"expectation for {dimension}.{metric} must define 'type' and 'expected'"
        )
    expectation_type = raw["type"]
    if expectation_type not in EXPECTATION_TYPES:
        raise QualityBaselineError(
            f"unknown expectation type {expectation_type!r} for {dimension}.{metric}"
        )
    if metric not in KNOWN_METRICS:
        raise QualityBaselineError(
            f"unknown metric {metric!r} in dimension {dimension!r}"
        )
    if metric in KNOWN_FIELDS and not re.fullmatch(r"[a-z_]+", metric):
        raise QualityBaselineError(f"metric {metric!r} must be lower_snake_case")
    return Expectation(
        dimension=dimension,
        metric=metric,
        expectation_type=expectation_type,
        expected=raw["expected"],
    )


def evaluate_expectation(
    observation: Mapping[str, Any],
    expectation: Expectation,
) -> ExpectationResult:
    """Evaluate one expectation against an observation (metrics dict)."""
    field = expectation.observed_field
    if field not in observation:
        raise QualityBaselineError(
            f"observation has no value for field {field!r} required by "
            f"{expectation.dimension}.{expectation.metric}"
        )
    observed: Any = observation[field]
    expected: Any = expectation.expected

    passed = False
    details = ""
    if expectation.expectation_type == "exact":
        passed = observed == expected
        details = _scalar_details(expected, observed, "==", passed)
    elif expectation.expectation_type == "minimum":
        passed = _as_number(observed) >= _as_number(expected)
        details = _scalar_details(expected, observed, ">=", passed)
    elif expectation.expectation_type == "maximum":
        passed = _as_number(observed) <= _as_number(expected)
        details = _scalar_details(expected, observed, "<=", passed)
    elif expectation.expectation_type == "boolean":
        passed = bool(observed) == bool(expected)
        details = _bool_details(expected, observed, passed)
    elif expectation.expectation_type == "contains":
        missing = [item for item in expected if str(item) not in str(observed)]
        passed = not missing
        details = _contains_details(missing, passed)
    elif expectation.expectation_type == "excludes":
        leaked = [item for item in expected if str(item) in str(observed)]
        passed = not leaked
        details = _excludes_details(leaked)
    elif expectation.expectation_type == "ordered_sequence":
        observed_list = list(observed)
        passed = observed_list == list(expected)
        details = _sequence_details(expected, observed_list, passed)
    else:  # pragma: no cover - guarded by parse_expectation
        raise QualityBaselineError(
            f"unsupported type {expectation.expectation_type!r} for {expectation.metric}"
        )

    return ExpectationResult(
        dimension=expectation.dimension,
        metric=expectation.metric,
        expectation_type=expectation.expectation_type,
        expected=expected,
        observed=observed,
        passed=passed,
        details=details,
    )


def _as_number(value: Any) -> float:
    if isinstance(value, bool):
        raise QualityBaselineError("boolean is not a valid numeric observation")
    return float(value)


def _scalar_details(expected: Any, observed: Any, op: str, passed: bool) -> str:
    if passed:
        return f"expected {expected!r} ({op}) observed {observed!r}"
    return f"expected {op} {expected!r}, observed {observed!r}"


def _bool_details(expected: bool, observed: Any, passed: bool) -> str:
    if passed:
        return f"expected {expected!r}, observed {bool(observed)!r}"
    return f"expected {expected!r}, observed {bool(observed)!r}"


def _contains_details(missing: list[str], passed: bool) -> str:
    if passed:
        return "all required content present"
    return "missing required content: " + ", ".join(repr(item) for item in missing)


def _excludes_details(leaked: list[str]) -> str:
    if not leaked:
        return "no excluded content present"
    return "excluded content present: " + ", ".join(repr(item) for item in leaked)


def _sequence_details(expected: Any, observed: Any, passed: bool) -> str:
    if passed:
        return f"sequence matches ({len(list(observed))} items)"
    return f"sequence mismatch (expected {len(list(expected))} items, observed {len(observed)})"


def group_by_dimension(results) -> dict[str, tuple[ExpectationResult, ...]]:
    """Group flat evaluation results by dimension, in canonical order."""
    grouped: dict[str, list[ExpectationResult]] = {}
    for result in results:
        grouped.setdefault(result.dimension, []).append(result)
    return {dim: tuple(grouped[dim]) for dim in DIMENSIONS if dim in grouped}