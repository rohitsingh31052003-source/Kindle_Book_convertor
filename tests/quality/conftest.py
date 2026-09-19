"""Shared fixtures for the M6.3 quality-measurement tests.

All heavy work (model conversion over the 11-fixture corpus) happens once per
pytest session: ``quality_observations`` converts every fixture with the
deterministic counting OCR engine and caches the derived metric dicts; the
evaluation and report modules are pure functions of those dicts.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from tests.fixtures.corpus import iter_documents

from .baseline import load_baseline
from .evaluation import evaluate_corpus
from .harness import expected_classification, observed_metrics, observe_quality


@pytest.fixture(scope="session")
def quality_workdir() -> Path:
    """A session temp directory; generated EPUBs live here for inspection."""
    with tempfile.TemporaryDirectory(prefix="quality-") as td:
        yield Path(td)


@pytest.fixture(scope="session")
def quality_observations(quality_workdir) -> dict[str, dict]:
    """Metric dicts for every corpus fixture, measured once per session."""
    observations: dict[str, dict] = {}
    for document in iter_documents():
        fixture_id = document["id"]
        observations[fixture_id] = observed_metrics(
            observe_quality(fixture_id, workdir=quality_workdir)
        )
    return observations


@pytest.fixture(scope="session")
def quality_baseline() -> dict:
    """The committed quality baseline (loaded and validated)."""
    return load_baseline()


@pytest.fixture(scope="session")
def quality_review_notes(quality_baseline) -> dict[str, list[str]]:
    """Per-fixture review notes carried in the baseline."""
    return {
        document["id"]: quality_baseline["documents"][document["id"]].get("notes", [])
        for document in iter_documents()
    }


@pytest.fixture(scope="session")
def quality_results(quality_observations, quality_baseline) -> dict:
    """Evaluated quality results for every fixture."""
    expected = {
        document["id"]: expected_classification(document["id"])
        for document in iter_documents()
    }
    return evaluate_corpus(quality_observations, quality_baseline, expected)