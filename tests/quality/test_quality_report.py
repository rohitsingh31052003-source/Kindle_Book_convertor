"""Tests for the machine-readable and human-readable quality reports.

Reports are pure functions of the evaluation results. These tests pin the
report contract: stable structure, deterministic output, per-fixture and
per-metric expected/observed/status/details visibility (no single hiding
number), and freedom from timestamps and machine paths.
"""

from __future__ import annotations

import json

import pytest

from .report import build_machine_report, format_human_report

pytestmark = pytest.mark.quality


def test_machine_report_structure(quality_results):
    report = build_machine_report(quality_results)
    assert report["schema_version"] == 1
    assert report["report"] == "m63-quality"
    assert report["corpus_size"] == 11
    assert report["fixtures_passed"] == 11
    assert report["fixtures_failed"] == 0

    for fixture_id in ("novel_basic", "scanned_book", "twocolumn_article"):
        entry = report["fixtures"][fixture_id]
        assert entry["status"] == "PASS"
        assert "classification" in entry["dimensions"]
        assert "structure" in entry["dimensions"]


def test_machine_report_exposes_every_metric_detail(quality_results, quality_baseline):
    report = build_machine_report(quality_results)
    declared = sum(
        sum(len(entry.get(dim, {})) for dim in ("structure", "ocr", "images", "epub", "reading_order"))
        for entry in quality_baseline["documents"].values()
    )
    reported = sum(
        len(dim["metrics"])
        for fixture in report["fixtures"].values()
        for dim in fixture["dimensions"].values()
    )
    # classification adds one metric per fixture on top of the declared ones.
    assert reported == declared + 11


def test_machine_report_metric_fields(quality_results):
    report = build_machine_report(quality_results)
    statuses = {metric["status"] for metric in _all_metrics(report)}
    assert statuses == {"PASS"}
    for metric in _all_metrics(report):
        assert "expected" in metric
        assert "observed" in metric
        assert "details" in metric


def _all_metrics(report):
    return [
        metric
        for fixture in report["fixtures"].values()
        for dimension in fixture["dimensions"].values()
        for metric in dimension["metrics"]
    ]


def test_machine_report_failure_visibility():
    from .expectations import ExpectationResult
    from .evaluation import FixtureQualityResult

    failed = ExpectationResult(
        dimension="structure",
        metric="headings",
        expectation_type="minimum",
        expected=10,
        observed=2,
        passed=False,
        details="expected >= 10, observed 2",
    )
    ok = ExpectationResult(
        dimension="classification",
        metric="classification",
        expectation_type="exact",
        expected="TEXT",
        observed="TEXT",
        passed=True,
        details="matches",
    )
    result = FixtureQualityResult("novel_basic", "TEXT", (ok, failed))
    report = build_machine_report({"novel_basic": result})
    assert report["fixtures_passed"] == 0
    assert report["fixtures_failed"] == 1
    assert report["fixtures"]["novel_basic"]["status"] == "FAIL"
    heading = report["fixtures"]["novel_basic"]["dimensions"]["structure"]["metrics"][0]
    assert heading["status"] == "FAIL"
    assert heading["expected"] == 10
    assert heading["observed"] == 2


def test_machine_report_is_deterministic(quality_results):
    first = json.dumps(build_machine_report(quality_results), indent=2)
    second = json.dumps(build_machine_report(quality_results), indent=2)
    assert first == second


def test_machine_report_has_no_timestamps_or_paths(quality_results):
    rendered = json.dumps(build_machine_report(quality_results))
    assert "timestamp" not in rendered.lower()
    assert "C:\\" not in rendered
    assert "Kindle Book Convertor" not in rendered


def test_human_report_shape(quality_results, quality_review_notes):
    text = format_human_report(quality_results, quality_review_notes)
    assert "M6.3 Conversion Quality Report" in text
    assert "Corpus documents: 11" in text
    assert "11/11 PASS" in text
    assert "novel_basic: PASS" in text
    for fixture_id in ("scanned_book", "chapters_long", "edge_short_report"):
        assert f"{fixture_id}: PASS" in text


def test_human_report_exposes_expected_observed_status(quality_results):
    text = format_human_report(quality_results)
    assert "expected" in text
    assert "observed" in text
    assert "PASS" in text


def test_human_report_includes_review_notes(quality_results, quality_review_notes):
    text = format_human_report(quality_results, quality_review_notes)
    assert "notes:" in text
    for fixture_id in ("scanned_book", "chapters_long", "twocolumn_article"):
        assert f"{fixture_id}: PASS" in text


def test_human_report_no_review_notes_when_absent(quality_results):
    text = format_human_report(quality_results)
    assert "notes:" not in text


def test_human_report_is_deterministic(quality_results, quality_review_notes):
    first = format_human_report(quality_results, quality_review_notes)
    second = format_human_report(quality_results, quality_review_notes)
    assert first == second


def test_human_report_has_no_machine_paths(quality_results, quality_review_notes):
    text = format_human_report(quality_results, quality_review_notes)
    assert "C:\\" not in text
    assert "Kindle Book Convertor" not in text
    assert "\\Work\\" not in text