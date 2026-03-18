#!/usr/bin/env python3
import argparse
import json
import statistics
from pathlib import Path


def mean_or_none(values):
    return statistics.mean(values) if values else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate all ground-truth session results under one directory."
    )
    parser.add_argument(
        "--root",
        default="logs/ground_truth",
        help="Root directory containing ground-truth session folders",
    )
    args = parser.parse_args()

    root = Path(args.root)
    result_paths = sorted(root.glob("gt_*/ground_truth.json"))
    results = [json.loads(path.read_text(encoding="utf-8")) for path in result_paths]

    mean_norms = [r["mean_error_mm"]["norm_mm"] for r in results]
    p95_norms = [r["sample_error_norm_mm"]["p95"] for r in results if r["sample_error_norm_mm"]["p95"] is not None]
    max_norms = [r["sample_error_norm_mm"]["max"] for r in results if r["sample_error_norm_mm"]["max"] is not None]

    batch_summary = {
        "root": str(root),
        "session_count": len(results),
        "overall_mean_error_norm_mm": mean_or_none(mean_norms),
        "overall_mean_p95_error_mm": mean_or_none(p95_norms),
        "overall_mean_max_error_mm": mean_or_none(max_norms),
        "points": [
            {
                "label": r["label"],
                "session_dir": r["session_dir"],
                "mean_error_norm_mm": r["mean_error_mm"]["norm_mm"],
                "p95_error_mm": r["sample_error_norm_mm"]["p95"],
                "max_error_mm": r["sample_error_norm_mm"]["max"],
            }
            for r in results
        ],
    }

    print(json.dumps(batch_summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
