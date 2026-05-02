#!/usr/bin/env python3
import argparse
import csv
import json
import math
import statistics
from pathlib import Path


def percentile(sorted_values, p):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * p
    lower = math.floor(pos)
    upper = math.ceil(pos)
    if lower == upper:
        return sorted_values[lower]
    frac = pos - lower
    return sorted_values[lower] * (1.0 - frac) + sorted_values[upper] * frac


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze a Tag capture session against a known ground-truth point."
    )
    parser.add_argument("session_dir", help="Directory containing summary.json and positions.csv")
    parser.add_argument("--label", required=True, help="Ground-truth point label")
    parser.add_argument("--truth-x", type=float, required=True, help="True X in mm")
    parser.add_argument("--truth-y", type=float, required=True, help="True Y in mm")
    parser.add_argument("--truth-z", type=float, required=True, help="True Z in mm")
    parser.add_argument(
        "--notes",
        default="",
        help="Optional note stored in the output JSON",
    )
    args = parser.parse_args()

    session_dir = Path(args.session_dir)
    summary_path = session_dir / "summary.json"
    positions_path = session_dir / "positions.csv"
    output_path = session_dir / "ground_truth.json"

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = list(csv.DictReader(positions_path.open(encoding="utf-8")))
    skip_sweeps = int(summary.get("skip_sweeps", 0))
    rows = [row for row in rows if int(row["sweep"]) > skip_sweeps]

    truth = {
        "x_mm": float(args.truth_x),
        "y_mm": float(args.truth_y),
        "z_mm": float(args.truth_z),
    }

    mean_x = float(summary["position_mean_mm"]["x"])
    mean_y = float(summary["position_mean_mm"]["y"])
    mean_z = float(summary["position_mean_mm"]["z"])

    mean_error = {
        "x_mm": mean_x - truth["x_mm"],
        "y_mm": mean_y - truth["y_mm"],
        "z_mm": mean_z - truth["z_mm"],
    }
    mean_error["norm_mm"] = math.sqrt(
        mean_error["x_mm"] ** 2 + mean_error["y_mm"] ** 2 + mean_error["z_mm"] ** 2
    )

    sample_error_norms = []
    for row in rows:
        ex = int(row["x_mm"]) - truth["x_mm"]
        ey = int(row["y_mm"]) - truth["y_mm"]
        ez = int(row["z_mm"]) - truth["z_mm"]
        sample_error_norms.append(math.sqrt(ex * ex + ey * ey + ez * ez))

    sorted_norms = sorted(sample_error_norms)
    result = {
        "label": args.label,
        "notes": args.notes,
        "session_dir": str(session_dir),
        "truth_mm": truth,
        "summary_mean_mm": {
            "x": mean_x,
            "y": mean_y,
            "z": mean_z,
        },
        "summary_std_mm": summary["position_std_mm"],
        "mean_error_mm": mean_error,
        "residual_mean_mm": summary["residual_mean_mm"],
        "samples_used": len(rows),
        "sample_error_norm_mm": {
            "mean": statistics.mean(sample_error_norms) if sample_error_norms else None,
            "p95": percentile(sorted_norms, 0.95),
            "max": max(sample_error_norms) if sample_error_norms else None,
        },
        "anchor_subset_frequency_summary": summary.get("anchor_subset_frequency_summary", {}),
    }

    output_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
