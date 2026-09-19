"""Preview or explicitly write the M6.4 performance baseline."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = str(_REPO_ROOT / "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from .harness import (
    PERFORMANCE_BASELINE_PATH,
    BenchmarkConfig,
    build_baseline,
    load_baseline,
    measure_corpus,
)
from .report import build_machine_report, format_human_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="persist the fresh baseline")
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--tolerance", type=float, default=0.25)
    args = parser.parse_args()
    config = BenchmarkConfig(args.warmups, args.runs, args.tolerance)
    measurements = measure_corpus(config)
    fresh = build_baseline(measurements, config)

    if PERFORMANCE_BASELINE_PATH.is_file():
        previous = load_baseline()
        changed = [
            fixture_id
            for fixture_id in fresh["documents"]
            if json.dumps(previous["documents"][fixture_id], sort_keys=True)
            != json.dumps(fresh["documents"][fixture_id], sort_keys=True)
        ]
        print("fixtures with changed measurements: " + (", ".join(changed) or "(none)"))

    print(format_human_report(measurements))
    print(json.dumps(build_machine_report(measurements), indent=2))
    if not args.write:
        print("\npreview only; rerun with --write to update the baseline")
        return
    PERFORMANCE_BASELINE_PATH.write_text(
        json.dumps(fresh, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\nwrote {PERFORMANCE_BASELINE_PATH}")


if __name__ == "__main__":
    main()
