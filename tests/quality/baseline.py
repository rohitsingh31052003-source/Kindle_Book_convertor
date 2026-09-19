"""Loading and validation of the committed quality baseline.

The baseline lives at ``tests/fixtures/quality_baseline.json`` and stores
authored *quality expectations* plus the metadata needed to scope them. It is
independent from M6.2's ``regression_baseline.json``: that file pins exact
converter behavior, this file declares what *good* output looks like and is
refreshed only via ``python -m tests.quality.regenerate_baseline --write``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from tests.fixtures.corpus import CORPUS_DIR, iter_documents

QUALITY_BASELINE_PATH = CORPUS_DIR.parent / "quality_baseline.json"
QUALITY_SCHEMA_VERSION = 1

from .expectations import (
    QualityBaselineError,
    DIMENSIONS,
    EXPECTATION_TYPES,
    KNOWN_METRICS,
)


def load_baseline() -> dict[str, Any]:
    """Load and validate the committed quality baseline.

    Raises
    ------
    QualityBaselineError
        If the baseline is missing, unreadable, or does not match the schema.
    """
    if not QUALITY_BASELINE_PATH.is_file():
        raise QualityBaselineError(
            f"quality baseline not found at {QUALITY_BASELINE_PATH}; "
            f"run `python -m tests.quality.regenerate_baseline --write`"
        )
    try:
        with open(QUALITY_BASELINE_PATH, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError) as exc:
        raise QualityBaselineError(
            f"cannot read quality baseline at {QUALITY_BASELINE_PATH}: {exc}"
        ) from exc

    validate_baseline(payload)
    return payload


def validate_baseline(payload: Mapping[str, Any]) -> None:
    """Structural + schema validation of a quality-baseline payload.

    Checks: schema version, corpus-document coverage on both sides, allowed
    dimensions, expectation types, metric names, and per-expectation payload
    shape. Raises ``QualityBaselineError`` on the first problem found.
    """
    if payload.get("schema_version") != QUALITY_SCHEMA_VERSION:
        raise QualityBaselineError(
            "quality baseline schema version mismatch: "
            f"expected {QUALITY_SCHEMA_VERSION}, got "
            f"{payload.get('schema_version')!r}"
        )

    documents = payload.get("documents")
    if not isinstance(documents, dict):
        raise QualityBaselineError("quality baseline must define a 'documents' map")

    corpus_ids = sorted(doc["id"] for doc in iter_documents())
    missing = [fid for fid in corpus_ids if fid not in documents]
    if missing:
        raise QualityBaselineError(
            "quality baseline missing documents: " + ", ".join(missing)
        )
    extra = [fid for fid in documents if fid not in corpus_ids]
    if extra:
        raise QualityBaselineError(
            "quality baseline has non-corpus documents: " + ", ".join(extra)
        )

    for doc in documents.values():
        for dimension in doc.keys():
            if dimension != "notes" and dimension not in DIMENSIONS:
                raise QualityBaselineError(
                    f"unknown dimension {dimension!r} (valid: {', '.join(DIMENSIONS)})"
                )
        for dimension in DIMENSIONS:
            metrics = doc.get(dimension)
            if not metrics:
                continue
            if not isinstance(metrics, dict):
                raise QualityBaselineError(
                    f"dimension {dimension!r} must be a metric map, "
                    f"got {type(metrics).__name__}"
                )
            for metric, raw in metrics.items():
                _validate_metric(dimension, metric, raw)


def _validate_metric(dimension: str, metric: str, raw: Any) -> None:
    if metric not in KNOWN_METRICS:
        raise QualityBaselineError(
            f"unknown metric {metric!r} in dimension {dimension!r}"
        )
    if not isinstance(raw, dict):
        raise QualityBaselineError(
            f"expectation for {dimension}.{metric} must be an object, "
            f"got {type(raw).__name__}"
        )
    expectation_type = raw.get("type")
    if expectation_type not in EXPECTATION_TYPES:
        raise QualityBaselineError(
            f"unknown expectation type {expectation_type!r} for "
            f"{dimension}.{metric}"
        )
    if "expected" not in raw:
        raise QualityBaselineError(
            f"expectation for {dimension}.{metric} has no 'expected' value"
        )


def corpus_ids() -> list[str]:
    """Corpus document ids in manifest order."""
    return [doc["id"] for doc in iter_documents()]