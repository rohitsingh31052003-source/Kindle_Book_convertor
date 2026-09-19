"""Deterministic performance measurements over the M6.1 PDF corpus.

The benchmark uses only public conversion stages and the deterministic OCR
double from M6.2. Timing is intentionally kept out of normal regression and
quality observations.
"""

from __future__ import annotations

import json
import platform
import statistics
import sys
import time
import tracemalloc
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping

from kindle_converter import convert_pdf_to_book
from kindle_converter.epub import build_epub, validate_epub
from kindle_converter.pdf import analyze_pdf
from tests.fixtures.corpus import document_path, iter_documents
from tests.regression.harness import CountingOCR

PERFORMANCE_BASELINE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "performance_baseline.json"
PERFORMANCE_SCHEMA_VERSION = 1
DEFAULT_WARMUPS = 1
DEFAULT_RUNS = 3
DEFAULT_RELATIVE_TOLERANCE = 0.25


@dataclass(frozen=True, slots=True)
class BenchmarkConfig:
    warmups: int = DEFAULT_WARMUPS
    runs: int = DEFAULT_RUNS
    relative_tolerance: float = DEFAULT_RELATIVE_TOLERANCE

    def __post_init__(self) -> None:
        if self.warmups < 0:
            raise ValueError("warmups must be non-negative")
        if self.runs < 1:
            raise ValueError("runs must be positive")
        if self.relative_tolerance < 0:
            raise ValueError("relative_tolerance must be non-negative")


@dataclass(frozen=True, slots=True)
class TimingStats:
    samples: tuple[float, ...]
    median: float
    minimum: float
    maximum: float

    @classmethod
    def from_samples(cls, samples: list[float]) -> "TimingStats":
        if not samples:
            raise ValueError("at least one timing sample is required")
        return cls(
            samples=tuple(samples),
            median=statistics.median(samples),
            minimum=min(samples),
            maximum=max(samples),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "samples_seconds": [round(value, 6) for value in self.samples],
            "median_seconds": round(self.median, 6),
            "minimum_seconds": round(self.minimum, 6),
            "maximum_seconds": round(self.maximum, 6),
        }


@dataclass(frozen=True, slots=True)
class PerformanceMeasurement:
    fixture: str
    page_count: int
    classification: str
    operation: str
    elapsed: TimingStats
    phases: dict[str, TimingStats]
    peak_python_allocated_bytes: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "fixture": self.fixture,
            "page_count": self.page_count,
            "classification": self.classification,
            "operation": self.operation,
            "elapsed": self.elapsed.as_dict(),
            "phases": {
                name: timing.as_dict()
                for name, timing in self.phases.items()
            },
            "memory": {
                "metric": "python_tracemalloc_peak_bytes",
                "peak_bytes": self.peak_python_allocated_bytes,
            },
        }


def corpus_ids() -> list[str]:
    """Return fixture IDs in manifest order."""
    return [document["id"] for document in iter_documents()]


def environment_metadata() -> dict[str, str]:
    """Return stable context useful for interpreting a local benchmark."""
    versions: dict[str, str] = {}
    for package in ("PyMuPDF", "ebooklib"):
        try:
            versions[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    return {
        "python": platform.python_version(),
        "platform": platform.platform(aliased=True),
        "machine": platform.machine() or "unknown",
        "pymupdf": versions["PyMuPDF"],
        "ebooklib": versions["ebooklib"],
    }


def _timed(action: Callable[[], Any]) -> float:
    started = time.perf_counter()
    action()
    return time.perf_counter() - started


def measure_fixture(fixture_id: str, config: BenchmarkConfig | None = None) -> PerformanceMeasurement:
    """Measure one fixture through the real PDF -> Book -> EPUB path."""
    config = config or BenchmarkConfig()
    pdf_path = document_path(fixture_id)
    analysis = analyze_pdf(pdf_path)
    phases: dict[str, list[float]] = {
        "pdf_analysis": [],
        "book_conversion": [],
        "epub_generation": [],
        "epub_validation": [],
    }
    elapsed: list[float] = []
    peaks: list[int] = []

    def one_run() -> None:
        output_dir = TemporaryDirectory(prefix="kbc-performance-")
        try:
            output = Path(output_dir.name) / f"{fixture_id}.epub"
            engine = CountingOCR()
            phase_started = time.perf_counter()
            analyze_pdf(pdf_path)
            phases["pdf_analysis"].append(time.perf_counter() - phase_started)

            phase_started = time.perf_counter()
            book = convert_pdf_to_book(pdf_path, engine)
            phases["book_conversion"].append(time.perf_counter() - phase_started)

            phase_started = time.perf_counter()
            build_epub(book, output)
            phases["epub_generation"].append(time.perf_counter() - phase_started)

            phase_started = time.perf_counter()
            validation = validate_epub(output)
            phases["epub_validation"].append(time.perf_counter() - phase_started)
            if not validation.valid:
                raise RuntimeError(
                    f"{fixture_id}: benchmark generated an invalid EPUB: "
                    f"{len(validation.errors)} error(s)"
                )
        finally:
            output_dir.cleanup()

    for _ in range(config.warmups):
        one_run()
    for samples in phases.values():
        samples.clear()

    tracemalloc.start()
    try:
        for _ in range(config.runs):
            started = time.perf_counter()
            one_run()
            elapsed.append(time.perf_counter() - started)
            peaks.append(tracemalloc.get_traced_memory()[1])
    finally:
        tracemalloc.stop()

    return PerformanceMeasurement(
        fixture=fixture_id,
        page_count=analysis.page_count,
        classification=analysis.document_type.name,
        operation="pdf_to_epub",
        elapsed=TimingStats.from_samples(elapsed),
        phases={
            name: TimingStats.from_samples(samples)
            for name, samples in phases.items()
        },
        peak_python_allocated_bytes=max(peaks),
    )


def measure_corpus(config: BenchmarkConfig | None = None) -> dict[str, PerformanceMeasurement]:
    """Measure every fixture in manifest order."""
    return {
        fixture_id: measure_fixture(fixture_id, config)
        for fixture_id in corpus_ids()
    }


def build_baseline(measurements: Mapping[str, PerformanceMeasurement], config: BenchmarkConfig) -> dict[str, Any]:
    """Build a baseline payload without writing it."""
    return {
        "schema_version": PERFORMANCE_SCHEMA_VERSION,
        "benchmark": "m64-performance",
        "description": (
            "Reference measurements for the M6.1 corpus. Values are machine-"
            "dependent observations, not behavioral contracts."
        ),
        "protocol": {
            "warmups": config.warmups,
            "measured_runs": config.runs,
            "aggregation": "median, with minimum and maximum retained",
            "timer": "time.perf_counter",
            "fresh_state_per_run": True,
            "ocr": "tests.regression.harness.CountingOCR (deterministic double)",
            "memory": "tracemalloc peak Python allocations; not process RSS",
            "relative_tolerance": config.relative_tolerance,
        },
        "environment": environment_metadata(),
        "documents": {
            fixture_id: measurement.as_dict()
            for fixture_id, measurement in measurements.items()
        },
    }


def load_baseline(path: Path = PERFORMANCE_BASELINE_PATH) -> dict[str, Any]:
    if not path.is_file():
        raise RuntimeError(
            f"performance baseline not found at {path}; "
            "run python -m tests.performance.regenerate_baseline --write"
        )
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    validate_baseline(payload)
    return payload


def validate_baseline(payload: Mapping[str, Any]) -> None:
    if payload.get("schema_version") != PERFORMANCE_SCHEMA_VERSION:
        raise ValueError("performance baseline schema version mismatch")
    documents = payload.get("documents")
    if not isinstance(documents, dict):
        raise ValueError("performance baseline must contain a documents map")
    expected = set(corpus_ids())
    if set(documents) != expected:
        raise ValueError("performance baseline documents do not match the corpus")
    protocol = payload.get("protocol")
    if not isinstance(protocol, dict) or "relative_tolerance" not in protocol:
        raise ValueError("performance baseline protocol is incomplete")


def compare_measurement(
    measurement: PerformanceMeasurement,
    baseline: Mapping[str, Any],
    *,
    relative_tolerance: float | None = None,
) -> dict[str, Any]:
    """Compare elapsed median and phase medians against one baseline entry."""
    entry = baseline["documents"][measurement.fixture]
    tolerance = (
        baseline["protocol"]["relative_tolerance"]
        if relative_tolerance is None
        else relative_tolerance
    )

    def comparison(name: str, observed: float, expected: float) -> dict[str, Any]:
        threshold = expected * (1 + tolerance)
        delta = observed - expected
        return {
            "baseline": round(expected, 6),
            "observed": round(observed, 6),
            "delta": round(delta, 6),
            "threshold": round(threshold, 6),
            "status": "FAIL" if observed > threshold else "PASS",
        }

    phases = {
        name: comparison(
            name,
            timing.median,
            entry["phases"][name]["median_seconds"],
        )
        for name, timing in measurement.phases.items()
    }
    elapsed = comparison(
        "elapsed",
        measurement.elapsed.median,
        entry["elapsed"]["median_seconds"],
    )
    return {
        "fixture": measurement.fixture,
        "status": "FAIL" if elapsed["status"] == "FAIL" or any(
            item["status"] == "FAIL" for item in phases.values()
        ) else "PASS",
        "elapsed": elapsed,
        "phases": phases,
    }


def compare_corpus(
    measurements: Mapping[str, PerformanceMeasurement],
    baseline: Mapping[str, Any],
    *,
    relative_tolerance: float | None = None,
) -> dict[str, dict[str, Any]]:
    return {
        fixture_id: compare_measurement(
            measurement, baseline, relative_tolerance=relative_tolerance
        )
        for fixture_id, measurement in measurements.items()
    }
