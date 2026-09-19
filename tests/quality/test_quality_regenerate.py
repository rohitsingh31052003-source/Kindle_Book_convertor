"""Tests for the quality-baseline regeneration tool.

Regeneration must be explicit and never fabricate or weaken authored quality
expectations. These tests pin: authored expectations are preserved verbatim,
``exact`` pins follow the fresh measurement, failing authored expectations
block ``--write``, and the diff preview reports changes before anything is
written. The corpus iteration is stubbed in the build/write tests so each test
converts at most one fixture.
"""

from __future__ import annotations

import json

import pytest

from . import regenerate_baseline as regen

pytestmark = pytest.mark.quality


def _committed_entry(fixture_id: str) -> dict:
    from .baseline import load_baseline

    return load_baseline()["documents"][fixture_id]


def _previous_with(fixture_id: str, entry: dict) -> dict:
    return {
        "schema_version": 1,
        "description": "test previous",
        "dimension_order": ["classification", "structure", "reading_order", "ocr", "images", "epub"],
        "documents": {fixture_id: entry},
    }


def _small_corpus(fixture_id: str):
    return [{"id": fixture_id}]


def _observe_override(metrics: dict):
    from unittest.mock import patch

    return patch.object(regen, "_observe", return_value=metrics)


def test_recompute_preserves_authored_quality_expectations():
    entry = _committed_entry("novel_basic")
    fresh = regen._recompute_document("novel_basic", _previous_with("novel_basic", entry))

    asserts = {
        "structure.paragraphs": entry["structure"]["paragraphs"],
        "structure.contains": entry["structure"]["contains"],
        "epub.nav_entries": entry["epub"]["nav_entries"],
        "ocr.ocr_calls": entry["ocr"]["ocr_calls"],
    }
    for path, expected in asserts.items():
        dimension, metric = path.split(".")
        assert fresh[dimension][metric] == expected, path


def test_recompute_updates_exact_pins_from_fresh_measurement():
    entry = _committed_entry("novel_basic")
    entry["structure"]["page_breaks"]["expected"] = 99
    fresh = regen._recompute_document("novel_basic", _previous_with("novel_basic", entry))
    assert fresh["structure"]["page_breaks"]["expected"] == 5  # pages(6) - 1


def test_recompute_preserves_notes():
    entry = _committed_entry("scanned_book")
    fresh = regen._recompute_document("scanned_book", _previous_with("scanned_book", entry))
    assert fresh["notes"] == entry["notes"]


def test_recompute_skips_fixture_without_previous_entry(capsys):
    entry = _committed_entry("novel_basic")
    result = regen._recompute_document("scanned_book", _previous_with("novel_basic", entry))
    assert result is None
    assert "refusing to fabricate authored expectations" in capsys.readouterr().out


def test_failing_authored_expectations_are_detected():
    entry = _committed_entry("edge_short_report")
    entry["structure"]["paragraphs"]["expected"] = 9999
    failures = regen._failing_authored("edge_short_report", entry)
    assert failures
    assert any("paragraphs" in line for line in failures)


def test_committed_expectations_are_not_failing(capsys):
    entry = _committed_entry("edge_short_report")
    assert regen._failing_authored("edge_short_report", entry) == []


def test_build_baseline_preserves_schema_and_notes(monkeypatch):
    fixture_id = "novel_basic"
    entry = _committed_entry(fixture_id)
    previous = _previous_with(fixture_id, entry)
    monkeypatch.setattr(regen, "iter_documents", lambda: _small_corpus(fixture_id))

    fresh = regen.build_baseline(previous)
    assert fresh["schema_version"] == 1
    assert fresh["dimension_order"] == list((
        "classification", "structure", "reading_order", "ocr", "images", "epub"
    ))
    assert set(fresh["documents"]) == {fixture_id}
    assert fresh["documents"][fixture_id]["notes"] == entry["notes"]


def test_diff_lines_reports_change():
    previous = _previous_with("novel_basic", _committed_entry("novel_basic"))
    fresh = json.loads(json.dumps(previous))
    fresh["documents"]["novel_basic"]["structure"]["page_breaks"]["expected"] = 6
    assert any("novel_basic: CHANGED" in line for line in regen._diff_lines(previous, fresh))


def test_diff_lines_reports_no_change():
    previous = _previous_with("novel_basic", _committed_entry("novel_basic"))
    fresh = json.loads(json.dumps(previous))
    lines = regen._diff_lines(previous, fresh)
    assert lines == ["  (no expectation value would change)"]


def test_write_refuses_when_authored_expectation_fails(monkeypatch, tmp_path, capsys):
    fixture_id = "edge_short_report"
    entry = _committed_entry(fixture_id)
    entry["structure"] = dict(entry["structure"])
    entry["structure"]["paragraphs"] = dict(entry["structure"]["paragraphs"])
    entry["structure"]["paragraphs"]["expected"] = 9999
    previous = _previous_with(fixture_id, entry)

    target = tmp_path / "quality_baseline.json"
    target.write_text(json.dumps(previous, indent=2), encoding="utf-8")

    monkeypatch.setattr(regen, "QUALITY_BASELINE_PATH", target)
    monkeypatch.setattr(regen, "load_baseline", lambda: previous)
    monkeypatch.setattr(regen, "iter_documents", lambda: _small_corpus(fixture_id))
    monkeypatch.setattr(
        "tests.quality.baseline.iter_documents", lambda: _small_corpus(fixture_id)
    )
    metrics = {
        "classification": "TEXT",
        "paragraphs": 5,
        "page_breaks": 1,
        "empty_paragraphs": 0,
        "near_empty_paragraphs": 0,
        "body_text": "Minimum Viable Document\nClosing note\nThis short report exists to give the corpus",
        "ocr_calls": 0,
        "ocr_text_present": False,
        "epub_valid": True,
        "epub_chapter_files": 1,
        "epub_heading_tags": 0,
        "epub_nav_entries": 1,
        "epub_text": "Minimum Viable Document\nClosing note",
        "epub_empty_chapters": 0,
    }
    with _observe_override(metrics):
        monkeypatch.setattr("sys.argv", ["regenerate_baseline", "--write"])
        with pytest.raises(SystemExit) as exc_info:
            regen.main()
    assert exc_info.value.code == 2
    assert "refusing to write" in capsys.readouterr().out
    # The committed file must be untouched: the authored 9999 survives.
    unchanged = json.loads(target.read_text(encoding="utf-8"))
    assert unchanged["documents"][fixture_id]["structure"]["paragraphs"]["expected"] == 9999


def test_write_persists_fresh_baseline(monkeypatch, tmp_path):
    fixture_id = "novel_basic"
    previous = _previous_with(fixture_id, _committed_entry(fixture_id))

    target = tmp_path / "quality_baseline.json"
    target.write_text(json.dumps(previous, indent=2), encoding="utf-8")

    monkeypatch.setattr(regen, "QUALITY_BASELINE_PATH", target)
    monkeypatch.setattr(regen, "load_baseline", lambda: previous)
    monkeypatch.setattr(regen, "iter_documents", lambda: _small_corpus(fixture_id))
    monkeypatch.setattr(
        "tests.quality.baseline.iter_documents", lambda: _small_corpus(fixture_id)
    )
    with _observe_override(
        {
            "classification": "TEXT",
            "paragraphs": 14,
            "headings": 3,
            "chapters": 3,
            "toc_entries": 3,
            "page_breaks": 5,
            "empty_paragraphs": 0,
            "near_empty_paragraphs": 0,
            "body_text": "The Lantern Keeper\nby Ada Grant\nChapter 1: The Harbor Lights",
            "column_tokens": [],
            "ocr_calls": 0,
            "ocr_text_present": False,
            "image_blocks": 0,
            "epub_image_resources": 0,
            "epub_valid": True,
            "epub_chapter_files": 1,
            "epub_heading_tags": 3,
            "epub_nav_entries": 1,
            "epub_text": "The Lantern Keeper\nby Ada Grant\nChapter 1: The Harbor Lights",
            "epub_empty_chapters": 0,
            "epub_page_breaks": 5,
        }
    ):
        monkeypatch.setattr("sys.argv", ["regenerate_baseline", "--write"])
        regen.main()

    written = json.loads(target.read_text(encoding="utf-8"))
    assert written["schema_version"] == 1
    assert set(written["documents"]) == {fixture_id}