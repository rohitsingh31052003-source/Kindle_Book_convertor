"""Tests for M6.4 measurement, baseline, comparison, and reporting logic."""

from __future__ import annotations

import json

import pytest

from tests.fixtures.corpus import iter_documents

from .harness import (
    BenchmarkConfig,
    PerformanceMeasurement,
    TimingStats,
    build_baseline,
    compare_measurement,
    corpus_ids,
    environment_metadata,
    load_baseline,
    measure_fixture,
    validate_baseline,
)
from .report import build_machine_report, format_human_report

pytestmark = pytest.mark.performance


def _measurement(fixture: str = "novel_basic", elapsed: float = 2.0) -> PerformanceMeasurement:
    timing = TimingStats.from_samples([elapsed, elapsed + 0.2, elapsed - 0.2])
    return PerformanceMeasurement(
        fixture=fixture,
        page_count=6,
        classification="TEXT",
        operation="pdf_to_epub",
        elapsed=timing,
        phases={
            "pdf_analysis": timing,
            "book_conversion": timing,
            "epub_generation": timing,
            "epub_validation": timing,
        },
        peak_python_allocated_bytes=123,
    )


def test_corpus_discovery_uses_all_manifest_documents() -> None:
    assert corpus_ids() == [document["id"] for document in iter_documents()]
    assert len(corpus_ids()) == 11


def test_benchmark_configuration_rejects_invalid_values() -> None:
    with pytest.raises(ValueError):
        BenchmarkConfig(warmups=-1)
    with pytest.raises(ValueError):
        BenchmarkConfig(runs=0)
    with pytest.raises(ValueError):
        BenchmarkConfig(relative_tolerance=-0.1)


def test_timing_stats_aggregate_repeated_samples() -> None:
    stats = TimingStats.from_samples([3.0, 1.0, 2.0])
    assert stats.median == 2.0
    assert stats.minimum == 1.0
    assert stats.maximum == 3.0
    assert stats.as_dict()["samples_seconds"] == [3.0, 1.0, 2.0]


def test_measurement_uses_real_pipeline_and_records_memory() -> None:
    measurement = measure_fixture("edge_short_report", BenchmarkConfig(0, 1))
    assert measurement.operation == "pdf_to_epub"
    assert measurement.page_count == 2
    assert measurement.classification == "TEXT"
    assert measurement.elapsed.median > 0
    assert measurement.peak_python_allocated_bytes > 0
    assert set(measurement.phases) == {
        "pdf_analysis",
        "book_conversion",
        "epub_generation",
        "epub_validation",
    }


def test_deterministic_ocr_double_is_used_for_scanned_fixture() -> None:
    measurement = measure_fixture("scanned_book", BenchmarkConfig(0, 1))
    assert measurement.classification == "SCANNED"
    assert measurement.elapsed.median > 0


def test_baseline_contains_protocol_and_corpus_documents() -> None:
    config = BenchmarkConfig(1, 3)
    baseline = build_baseline({"novel_basic": _measurement()}, config)
    validate_baseline({
        **baseline,
        "documents": {
            fixture: _measurement(fixture).as_dict() for fixture in corpus_ids()
        },
    })
    assert baseline["protocol"]["aggregation"].startswith("median")
    assert baseline["protocol"]["memory"].startswith("tracemalloc")


def test_baseline_validation_rejects_extra_or_missing_fixture() -> None:
    baseline = build_baseline(
        {fixture: _measurement(fixture) for fixture in corpus_ids()},
        BenchmarkConfig(),
    )
    baseline["documents"].pop("novel_basic")
    with pytest.raises(ValueError, match="do not match"):
        validate_baseline(baseline)


def test_committed_baseline_loads_and_covers_the_corpus() -> None:
    baseline = load_baseline()
    assert set(baseline["documents"]) == set(corpus_ids())


def test_comparison_reports_delta_threshold_and_regression() -> None:
    baseline = build_baseline({"novel_basic": _measurement( elapsed=2.0)}, BenchmarkConfig())
    faster = _measurement(elapsed=2.1)
    slower = _measurement(elapsed=3.0)
    assert compare_measurement(faster, baseline)["status"] == "PASS"
    result = compare_measurement(slower, baseline)
    assert result["status"] == "FAIL"
    assert result["elapsed"]["baseline"] == 2.0
    assert result["elapsed"]["observed"] == 3.0
    assert result["elapsed"]["threshold"] == 2.5


def test_report_is_machine_readable_and_human_focused() -> None:
    measurements = {"novel_basic": _measurement()}
    machine = build_machine_report(measurements)
    json.dumps(machine)
    assert machine["report"] == "m64-performance"
    human = format_human_report(measurements)
    assert "novel_basic" in human
    assert "book_conversion" in human


def test_environment_metadata_is_interpretable() -> None:
    environment = environment_metadata()
    assert environment["python"]
    assert environment["platform"]
    assert environment["pymupdf"]


def test_baseline_regeneration_requires_explicit_write(monkeypatch, tmp_path, capsys) -> None:
    from . import regenerate_baseline as regen

    target = tmp_path / "performance_baseline.json"
    monkeypatch.setattr(regen, "PERFORMANCE_BASELINE_PATH", target)
    monkeypatch.setattr(regen, "measure_corpus", lambda config: {
        "novel_basic": _measurement()
    })
    monkeypatch.setattr(
        "sys.argv",
        ["regenerate_baseline"],
    )
    regen.main()
    assert not target.exists()
    assert "preview only" in capsys.readouterr().out

    monkeypatch.setattr("sys.argv", ["regenerate_baseline", "--write"])
    regen.main()
    assert target.exists()
