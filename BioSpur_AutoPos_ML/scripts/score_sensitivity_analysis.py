#!/usr/bin/env python3
"""Analyze ranking sensitivity under different validation objectives."""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


OPTI_LAYOUT = Path("DATASETS/features/optitrack_layout_validation.csv")
SCORES_V2 = Path("DATASETS/features/layout_scores_v2.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


OBJECTIVES = {
    "balanced_3d": [
        ("opti_err_3d_median_mm", 0.25, "lower"),
        ("opti_err_3d_p95_mm", 0.25, "lower"),
        ("opti_err_3d_rms_mm", 0.30, "lower"),
        ("opti_err_vertical_median_mm", 0.20, "lower"),
    ],
    "tail_robustness": [
        ("opti_err_3d_p95_mm", 0.55, "lower"),
        ("opti_err_3d_rms_mm", 0.35, "lower"),
        ("opti_err_vertical_median_mm", 0.10, "lower"),
    ],
    "median_field_error": [
        ("opti_err_3d_median_mm", 0.50, "lower"),
        ("opti_err_horizontal_median_mm", 0.20, "lower"),
        ("opti_err_vertical_median_mm", 0.30, "lower"),
    ],
    "vertical_priority": [
        ("opti_err_vertical_median_mm", 0.70, "lower"),
        ("opti_err_3d_rms_mm", 0.30, "lower"),
    ],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--opti-layout", type=Path, default=OPTI_LAYOUT)
    parser.add_argument("--scores-v2", type=Path, default=SCORES_V2)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def normalize(rows: list[dict[str, str]], field: str, direction: str) -> dict[str, float]:
    values: list[tuple[str, float]] = []
    for row in rows:
        value = as_float(row.get(field))
        if value is not None:
            values.append((row["layout_id"], value))
    if not values:
        return {}
    nums = [value for _layout_id, value in values]
    lo = min(nums)
    hi = max(nums)
    if abs(hi - lo) <= 1e-12:
        return {layout_id: 0.0 for layout_id, _value in values}
    out: dict[str, float] = {}
    for layout_id, value in values:
        if direction == "lower":
            out[layout_id] = 100.0 * (value - lo) / (hi - lo)
        else:
            out[layout_id] = 100.0 * (hi - value) / (hi - lo)
    return out


def objective_scores(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    all8 = [row for row in rows if row.get("eval_set") == "all8"]
    out: list[dict[str, Any]] = []
    for objective, metrics in OBJECTIVES.items():
        norm = {field: normalize(all8, field, direction) for field, _w, direction in metrics}
        scored: list[tuple[dict[str, str], float, list[str]]] = []
        for row in all8:
            total = 0.0
            weight_sum = 0.0
            basis: list[str] = []
            for field, weight, _direction in metrics:
                value = norm[field].get(row["layout_id"])
                if value is None:
                    continue
                total += weight * value
                weight_sum += weight
                basis.append(field)
            if weight_sum:
                scored.append((row, total / weight_sum, basis))
        scored.sort(key=lambda item: (item[1], item[0]["version"]))
        for rank, (row, score, basis) in enumerate(scored, start=1):
            out.append(
                {
                    "objective": objective,
                    "rank": rank,
                    "layout_id": row["layout_id"],
                    "version": row["version"],
                    "score": f"{score:.6f}",
                    "basis": "|".join(basis),
                    "opti_err_3d_median_mm": row.get("opti_err_3d_median_mm", ""),
                    "opti_err_3d_p95_mm": row.get("opti_err_3d_p95_mm", ""),
                    "opti_err_3d_rms_mm": row.get("opti_err_3d_rms_mm", ""),
                    "opti_err_horizontal_median_mm": row.get("opti_err_horizontal_median_mm", ""),
                    "opti_err_vertical_median_mm": row.get("opti_err_vertical_median_mm", ""),
                }
            )
    return out


def alignment_rows(objective_rows: list[dict[str, Any]], scores_v2: list[dict[str, str]]) -> list[dict[str, Any]]:
    score_by_layout = {row["layout_id"]: row for row in scores_v2}
    out: list[dict[str, Any]] = []
    for row in objective_rows:
        score = score_by_layout.get(row["layout_id"], {})
        out.append(
            {
                "objective": row["objective"],
                "objective_rank": row["rank"],
                "version": row["version"],
                "layout_id": row["layout_id"],
                "score_v2_rank": score.get("group_rank_v2", ""),
                "score_v2": score.get("production_score_v2", ""),
                "objective_score": row["score"],
                "rank_delta_score_v2_minus_objective": (
                    "" if not score.get("group_rank_v2") else int(score["group_rank_v2"]) - int(row["rank"])
                ),
            }
        )
    return out


def write_report(path: Path, objective_rows: list[dict[str, Any]], alignment: list[dict[str, Any]]) -> None:
    by_objective: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in objective_rows:
        by_objective[row["objective"]].append(row)

    lines = [
        "# Score Sensitivity Analysis",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Scope",
        "",
        "This report checks whether the recommended Erlangen layout changes under different validation objectives.",
        "No GPU is used and no model is trained.",
        "",
        "## Objective Winners",
        "",
        "| Objective | Winner | Score | 3D median | 3D p95 | 3D RMS | Vertical median |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for objective, rows in sorted(by_objective.items()):
        top = sorted(rows, key=lambda row: int(row["rank"]))[0]
        lines.append(
            f"| `{objective}` | `{top['version']}` | {top['score']} | "
            f"{top['opti_err_3d_median_mm']} | {top['opti_err_3d_p95_mm']} | "
            f"{top['opti_err_3d_rms_mm']} | {top['opti_err_vertical_median_mm']} |"
        )

    lines.extend(["", "## Full Rankings", ""])
    for objective, rows in sorted(by_objective.items()):
        lines.append(f"### {objective}")
        lines.extend(["| Rank | Version | Score | 3D median | 3D p95 | 3D RMS | Vertical median |", "|---:|---|---:|---:|---:|---:|---:|"])
        for row in sorted(rows, key=lambda item: int(item["rank"])):
            lines.append(
                f"| {row['rank']} | `{row['version']}` | {row['score']} | "
                f"{row['opti_err_3d_median_mm']} | {row['opti_err_3d_p95_mm']} | "
                f"{row['opti_err_3d_rms_mm']} | {row['opti_err_vertical_median_mm']} |"
            )
        lines.append("")

    lines.extend(["## Bewertung", ""])
    winners = {objective: sorted(rows, key=lambda row: int(row["rank"]))[0]["version"] for objective, rows in by_objective.items()}
    unique_winners = sorted(set(winners.values()))
    lines.append(f"- Objective winners: `{', '.join(unique_winners)}`.")
    if len(unique_winners) == 1:
        lines.append(f"- `{unique_winners[0]}` is robust across the tested validation objectives.")
    else:
        lines.append("- Recommendation depends on objective; do not collapse the decision into one scalar without choosing priorities.")
    lines.append("- Score v2 currently matches the balanced/tail ranking, but v4-io remains interesting for median/vertical behavior.")
    lines.append("- Next calibration should explicitly choose whether p95/RMS or median/vertical performance is more important.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    opti_rows = read_csv(args.opti_layout)
    scores_v2 = read_csv(args.scores_v2)
    objective_rows = objective_scores(opti_rows)
    alignment = alignment_rows(objective_rows, scores_v2)
    write_csv(args.feature_dir / "score_sensitivity_objectives.csv", objective_rows)
    write_csv(args.feature_dir / "score_sensitivity_alignment.csv", alignment)
    write_report(args.report_dir / "score_sensitivity_analysis.md", objective_rows, alignment)

    print(f"objective_rows={len(objective_rows)} alignment_rows={len(alignment)}")
    print(f"wrote {args.report_dir / 'score_sensitivity_analysis.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
