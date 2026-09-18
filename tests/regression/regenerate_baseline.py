"""Regenerate ``tests/fixtures/regression_baseline.json`` (M6.2).

This is the maintenance script behind the baseline. It re-runs the real
conversion pipeline over every M6.1 corpus fixture -- with the deterministic
:class:`CountingOCR` engine, never Tesseract -- and rewrites only the
structural observations in the baseline:

* the M3.1 analysis facts (classification + page-count breakdown);
* per-chapter block counts, heading texts, OCR call counts;
* chapter detection results (via the public
  ``extract_page_layout`` / ``reconstruct_layout`` / ``detect_chapters`` path);
* EPUB artifact facts (validation, chapter files, image resources, page
  breaks, heading tags, nav entries).

The semantic content anchors (``contains`` / ``excludes``) are *not*
recomputed from the pipeline: they are human-authored expectations. They are
preserved from the currently committed baseline when present, falling back to
:data:`SEED_ANCHORS` below when a fixture has none yet.

Deterministic and offline: no Tesseract, no Calibre, no network, no clocks.

Usage
-----
    python -m tests.regression.regenerate_baseline
        Preview which baseline values would change (writes nothing).

    python -m tests.regression.regenerate_baseline --write
        Regenerate the baseline file in place.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tests.regression.harness import (
    BASELINE_PATH,
    BASELINE_SCHEMA_VERSION,
    documents,
    load_baseline,
    observe_conversion,
)

#: Human-authored semantic anchors, used when a fixture has none in the
#: committed baseline yet. ``contains`` are substrings that MUST appear in the
#: converted body text; ``excludes`` substrings that MUST NOT.
SEED_ANCHORS: dict[str, dict[str, list[str]]] = {
    "novel_basic": {
        "contains": [
            "The Lantern Keeper",
            "Chapter 1: The Harbor Lights",
            "Chapter 2: The Keeper's Daughter",
            "by Ada Grant",
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
    "textbook_dense": {
        "contains": [
            "Introduction to Coastal Hydrography",
            "The value of this work lies in its being repeatable",
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
    "twocolumn_article": {
        "contains": [
            "The Harbor Gazette",
            "The Gazette welcomes letters on harbor matters before noon on Fridays",
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
    "scanned_book": {
        "contains": ["OCR-TEXT-FOR-CALL-1", "OCR-TEXT-FOR-CALL-5"],
        "excludes": ["Tesseract", "pytesseract"],
    },
    "text_with_images": {
        "contains": [
            "Figure 1: The survey boat at anchor off the outer bar",
            "Figure 3: The tide curve recorded over a single spring day",
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
    "headers_footers": {
        "contains": [
            "The salt wind off the harbor carried the smell of rope and paint",
        ],
        "excludes": ["The Lantern Keeper", "Chapter One", "OCR-TEXT-FOR-CALL"],
    },
    "unusual_typography": {
        "contains": [
            "On the Behavior of Light",
            "SET IN SMALL CAPS",
            '"Detail is the friend of the patient observer',
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
    "mixed_text_image": {
        "contains": [
            "Harbor Bulletin, First Quarter",
            "OCR-TEXT-FOR-CALL-1",
            "OCR-TEXT-FOR-CALL-3",
        ],
        "excludes": ["Tesseract", "pytesseract"],
    },
    "chapters_long": {
        "contains": [
            "Chapter 1: The Harbor",
            "Chapter 8: The Light",
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
    "edge_interleaved_blank": {
        "contains": [
            "This page intentionally carries nothing",
            "OCR-TEXT-FOR-CALL-1",
            "OCR-TEXT-FOR-CALL-2",
        ],
        "excludes": ["Tesseract", "pytesseract"],
    },
    "edge_short_report": {
        "contains": [
            "Minimum Viable Document",
            "Closing note",
            "This short report exists to give the corpus",
        ],
        "excludes": ["OCR-TEXT-FOR-CALL"],
    },
}

#: Authored (non-numeric) per-fixture expectations preserved verbatim across
#: regenerations. ``contains``/``excludes`` are seeded from :data:`SEED_ANCHORS`
#: when absent; other fields (e.g. ``column_tokens``) are authored once here or
#: in the baseline and then carried forward untouched.
SEED_COLUMN_TOKENS: dict[str, list[str]] = {
    "twocolumn_article": ["L1", "R1", "L2", "R2", "L3", "R3", "L4", "R4"],
}

_PRESERVED_AUTHORED = ("column_tokens",)


def _observe_document(document: dict) -> dict:
    """Convert one fixture and return its compact structural observations."""
    return observe_conversion(document["id"], keep_text=False)


def _anchors_for(fixture_id: str, previous: dict | None) -> dict[str, list[str]]:
    if previous is not None:
        old = previous["documents"].get(fixture_id, {})
        if "contains" in old or "excludes" in old:
            return {
                "contains": old.get("contains", []),
                "excludes": old.get("excludes", []),
            }
    seeds = SEED_ANCHORS.get(fixture_id)
    if seeds is not None:
        return seeds
    return {"contains": [], "excludes": []}


def _authored_fields(fixture_id: str, previous: dict | None) -> dict:
    """Authored fields (e.g. ``column_tokens``) to carry into the entry."""
    if previous is not None:
        old = previous["documents"].get(fixture_id, {})
        return {key: old[key] for key in _PRESERVED_AUTHORED if key in old}
    fields: dict = {}
    if fixture_id in SEED_COLUMN_TOKENS:
        fields["column_tokens"] = SEED_COLUMN_TOKENS[fixture_id]
    return fields


def build_baseline(previous: dict | None) -> dict:
    """Compute fresh structural observations for every fixture.

    Content anchors (``contains``/``excludes``) are carried over from
    ``previous`` when present, else seeded from :data:`SEED_ANCHORS`; other
    authored fields survive from :meth:`_authored_fields`.
    """
    entries: dict[str, dict] = {}
    for document in documents():
        fixture_id = document["id"]
        observation = _observe_document(document)
        anchors = _anchors_for(fixture_id, previous)
        if not anchors["contains"] and not anchors["excludes"]:
            print(
                f"warning: {fixture_id} has no semantic contains/excludes anchors; "
                "add one via SEED_ANCHORS or by editing the baseline."
            )
            continue
        observation.update(anchors)
        observation.update(_authored_fields(fixture_id, previous))
        entries[fixture_id] = observation

    return {
        "schema_version": BASELINE_SCHEMA_VERSION,
        "description": (
            "Deterministic regression expectations (M6.2) for the M6.1 corpus. "
            "Regenerated with python -m tests.regression.regenerate_baseline; "
            "contains/excludes are human-authored and preserved on regeneration. "
            "The analysis block records the M3.1 classification and page-count "
            "breakdown observed by the converter; the corpus manifest remains the "
            "declared source of truth for the same facts "
            "(tests/fixtures/corpus/manifest.json)."
        ),
        "documents": entries,
    }


def _diff(previous: dict, fresh: dict) -> list[str]:
    lines: list[str] = []
    for fixture_id in sorted(set(previous["documents"]) | set(fresh["documents"])):
        old = json.dumps(previous["documents"].get(fixture_id, {}), sort_keys=True)
        new = json.dumps(fresh["documents"].get(fixture_id, {}), sort_keys=True)
        if old != new:
            lines.append(f"  {fixture_id}: CHANGED")
    return lines or ["  (no structural change detected)"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="write the regenerated baseline to disk (default: preview only)",
    )
    args = parser.parse_args()

    previous = load_baseline() if BASELINE_PATH.is_file() else None
    fresh = build_baseline(previous)

    lines = _diff(previous, fresh) if previous is not None else ["  (no previous baseline)"]
    print("structural changes vs committed baseline:")
    print("\n".join(lines))

    if not args.write:
        print(
            "preview only (nothing written); re-run with --write to persist "
            f"to {BASELINE_PATH}"
        )
        return

    BASELINE_PATH.write_text(
        json.dumps(fresh, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {BASELINE_PATH}")


if __name__ == "__main__":
    main()