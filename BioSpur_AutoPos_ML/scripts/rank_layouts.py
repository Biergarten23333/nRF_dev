#!/usr/bin/env python3
"""Rank canonical layouts with an interpretable baseline score.

This is not ML training. Scores are normalized within each source group because
different capture rooms and experiments are not directly comparable yet.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


FEATURE_CSV = Path("DATASETS/features/layout_features.csv")
LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")
TOP_DIR = Path("outputs/top_layouts")


METRICS = [
    ("eval_autopos_rms_mm", 0.18, "lower"),
    ("eval_autopos_p95_mm", 0.12, "lower"),
    ("eval_static_p95_mm", 0.14, "lower"),
    ("eval_static_max_mm", 0.06, "lower"),
    ("eval_roto_deltaR_rms_mm", 0.14, "lower"),
    ("eval_roto_abs_deltaR_p95_mm", 0.14, "lower"),
    ("eval_roto_turn_center_p95_mm", 0.08, "lower"),
    ("extra_split_align_rms_mm", 0.08, "lower"),
    ("xy_hull_coverage_ratio", 0.03, "higher"),
    ("nearest_neighbor_min_mm", 0.02, "higher"),
    ("z_span_mm", 0.01, "higher"),
]


@dataclass
class ScoreRow:
    layout_id: str
    group_rank: int
    score_group: str
    capture_id: str
    source_group: str
    solver_version: str
    solver_family: str
    layout_variant: str
    final_score: float
    eval_score: str
    geometry_score: str
    split_stability_score: str
    score_confidence: str
    score_basis: str
    top_reason: str
    eval_autopos_rms_mm: str
    eval_autopos_p95_mm: str
    eval_static_p95_mm: str
    eval_roto_abs_deltaR_p95_mm: str
    eval_roto_turn_center_p95_mm: str
    extra_split_align_rms_mm: str
    xy_hull_coverage_ratio: str
    nearest_neighbor_min_mm: str
    z_span_mm: str
    risk_flags: str
    source_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=FEATURE_CSV)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    parser.add_argument("--top-dir", type=Path, default=TOP_DIR)
    parser.add_argument("--top-n-per-group", type=int, default=3)
    return parser.parse_args()


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def load_layout_db(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            out[item["layout_id"]] = item
    return out


def normalize_metric(rows: list[dict[str, str]], field: str, direction: str) -> dict[str, float]:
    values: list[tuple[str, float]] = []
    for row in rows:
        value = as_float(row.get(field))
        if value is not None:
            values.append((row["layout_id"], value))
    if not values:
        return {}
    vals = [value for _, value in values]
    lo = min(vals)
    hi = max(vals)
    if abs(hi - lo) <= 1e-12:
        return {layout_id: 0.0 for layout_id, _ in values}
    out: dict[str, float] = {}
    for layout_id, value in values:
        if direction == "lower":
            out[layout_id] = (value - lo) / (hi - lo)
        else:
            out[layout_id] = (hi - value) / (hi - lo)
    return out


def score_group(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    normalized = {
        field: normalize_metric(rows, field, direction)
        for field, _, direction in METRICS
    }
    scored: list[dict[str, Any]] = []

    for row in rows:
        weighted_sum = 0.0
        weight_sum = 0.0
        basis: list[str] = []
        eval_weight = 0.0
        eval_sum = 0.0
        geom_weight = 0.0
        geom_sum = 0.0
        split_weight = 0.0
        split_sum = 0.0

        for field, weight, _direction in METRICS:
            value = normalized[field].get(row["layout_id"])
            if value is None:
                continue
            weighted_sum += weight * value
            weight_sum += weight
            basis.append(field)
            if field.startswith("eval_"):
                eval_sum += weight * value
                eval_weight += weight
            elif field.startswith("extra_split"):
                split_sum += weight * value
                split_weight += weight
            else:
                geom_sum += weight * value
                geom_weight += weight

        risk_count = as_float(row.get("risk_flag_count")) or 0.0
        if risk_count > 0.0:
            risk_penalty = min(1.0, risk_count / 4.0)
            weighted_sum += 0.05 * risk_penalty
            weight_sum += 0.05
            geom_sum += 0.05 * risk_penalty
            geom_weight += 0.05
            basis.append("risk_flag_count")

        if weight_sum <= 0.0:
            final_score = 100.0
        else:
            final_score = 100.0 * weighted_sum / weight_sum

        eval_metric_count = sum(1 for field, _, _ in METRICS if field.startswith("eval_") and as_float(row.get(field)) is not None)
        if eval_metric_count >= 5:
            confidence = "evaluation_matched"
        elif eval_metric_count > 0:
            confidence = "partial_evaluation"
        else:
            confidence = "geometry_only_low"

        out = dict(row)
        out["_final_score"] = final_score
        out["_eval_score"] = "" if eval_weight <= 0.0 else 100.0 * eval_sum / eval_weight
        out["_geometry_score"] = "" if geom_weight <= 0.0 else 100.0 * geom_sum / geom_weight
        out["_split_stability_score"] = "" if split_weight <= 0.0 else 100.0 * split_sum / split_weight
        out["_score_confidence"] = confidence
        out["_score_basis"] = "|".join(basis)
        scored.append(out)

    scored.sort(key=lambda item: (item["_final_score"], item["solver_version"], item["layout_variant"], item["layout_id"]))
    return scored


def top_reason(row: dict[str, Any]) -> str:
    parts: list[str] = []
    for field, label in [
        ("eval_autopos_rms_mm", "autopos_rms"),
        ("eval_static_p95_mm", "static_p95"),
        ("eval_roto_abs_deltaR_p95_mm", "roto_p95"),
        ("extra_split_align_rms_mm", "split_align"),
    ]:
        value = as_float(row.get(field))
        if value is not None:
            parts.append(f"{label}={value:.1f}mm")
    if not parts:
        parts.append("geometry-only score")
    if row.get("risk_flags"):
        parts.append(f"risk={row['risk_flags']}")
    return "; ".join(parts)


def format_score(value: Any) -> str:
    if value == "":
        return ""
    number = as_float(value)
    return "" if number is None else f"{number:.3f}"


def build_scores(feature_rows: list[dict[str, str]]) -> list[ScoreRow]:
    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in feature_rows:
        by_group[row["source_group"]].append(row)

    score_rows: list[ScoreRow] = []
    for group, rows in sorted(by_group.items()):
        scored = score_group(rows)
        for rank, row in enumerate(scored, start=1):
            score_rows.append(
                ScoreRow(
                    layout_id=row["layout_id"],
                    group_rank=rank,
                    score_group=group,
                    capture_id=row["capture_id"],
                    source_group=row["source_group"],
                    solver_version=row["solver_version"],
                    solver_family=row["solver_family"],
                    layout_variant=row["layout_variant"],
                    final_score=float(row["_final_score"]),
                    eval_score=format_score(row["_eval_score"]),
                    geometry_score=format_score(row["_geometry_score"]),
                    split_stability_score=format_score(row["_split_stability_score"]),
                    score_confidence=row["_score_confidence"],
                    score_basis=row["_score_basis"],
                    top_reason=top_reason(row),
                    eval_autopos_rms_mm=row.get("eval_autopos_rms_mm", ""),
                    eval_autopos_p95_mm=row.get("eval_autopos_p95_mm", ""),
                    eval_static_p95_mm=row.get("eval_static_p95_mm", ""),
                    eval_roto_abs_deltaR_p95_mm=row.get("eval_roto_abs_deltaR_p95_mm", ""),
                    eval_roto_turn_center_p95_mm=row.get("eval_roto_turn_center_p95_mm", ""),
                    extra_split_align_rms_mm=row.get("extra_split_align_rms_mm", ""),
                    xy_hull_coverage_ratio=row.get("xy_hull_coverage_ratio", ""),
                    nearest_neighbor_min_mm=row.get("nearest_neighbor_min_mm", ""),
                    z_span_mm=row.get("z_span_mm", ""),
                    risk_flags=row.get("risk_flags", ""),
                    source_path=row["source_path"],
                )
            )
    return sorted(score_rows, key=lambda item: (item.score_group, item.group_rank))


def write_scores(path: Path, scores: list[ScoreRow]) -> None:
    fieldnames = list(asdict(scores[0]).keys()) if scores else list(ScoreRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in scores:
            payload = asdict(row)
            payload["final_score"] = f"{row.final_score:.6f}"
            writer.writerow(payload)


def write_top_outputs(top_dir: Path, scores: list[ScoreRow], layouts: dict[str, dict[str, Any]], top_n: int) -> None:
    top_dir.mkdir(parents=True, exist_ok=True)
    top_rows = [row for row in scores if row.group_rank <= top_n]
    write_scores(top_dir / "top_layouts.csv", top_rows)

    for old in top_dir.glob("rank*_*.json"):
        old.unlink()

    for row in top_rows:
        layout = layouts.get(row.layout_id)
        if not layout:
            continue
        safe_group = row.score_group.replace("/", "__").replace(" ", "_")
        safe_version = row.solver_version.replace("/", "_")
        out_path = top_dir / f"rank{row.group_rank:02d}_{safe_group}_{safe_version}_{row.layout_variant}_{row.layout_id}.json"
        payload = dict(layout)
        payload["baseline_score"] = asdict(row)
        out_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_report(path: Path, scores: list[ScoreRow]) -> None:
    by_group: dict[str, list[ScoreRow]] = defaultdict(list)
    for row in scores:
        by_group[row.score_group].append(row)

    confidence_counts = defaultdict(int)
    for row in scores:
        confidence_counts[row.score_confidence] += 1

    lines = [
        "# Baseline Layout Ranking",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Ranked layouts: `{len(scores)}`",
        f"- Score groups: `{len(by_group)}`",
        "- Score direction: lower is better",
        "- Scores are normalized within each source group, not globally across rooms.",
        "- No GPU is used by this script.",
        "",
        "## Confidence",
        "",
    ]
    for key, count in sorted(confidence_counts.items()):
        lines.append(f"- `{key}`: {count}")

    lines.extend(["", "## Top Layout Per Group", ""])
    lines.extend(
        [
            "| Group | Rank | Layout | Version | Variant | Score | Confidence | Reason |",
            "|---|---:|---|---|---|---:|---|---|",
        ]
    )
    for group, rows in sorted(by_group.items()):
        top = sorted(rows, key=lambda item: item.group_rank)[0]
        lines.append(
            f"| `{group}` | {top.group_rank} | `{top.layout_id}` | `{top.solver_version}` | "
            f"`{top.layout_variant}` | {top.final_score:.3f} | `{top.score_confidence}` | {top.top_reason} |"
        )

    lines.extend(["", "## Erlangen Official Ranking", ""])
    erlangen = [
        row for row in scores
        if row.capture_id == "28052026_Erlangen_Official"
        and row.source_group.endswith("v1_to_v4_io_field_check")
    ]
    if erlangen:
        lines.extend(["| Rank | Version | Score | Autopos RMS | Static p95 | Roto p95 |", "|---:|---|---:|---:|---:|---:|"])
        for row in sorted(erlangen, key=lambda item: item.group_rank):
            lines.append(
                f"| {row.group_rank} | `{row.solver_version}` | {row.final_score:.3f} | "
                f"{row.eval_autopos_rms_mm} | {row.eval_static_p95_mm} | {row.eval_roto_abs_deltaR_p95_mm} |"
            )
    else:
        lines.append("- no Erlangen ranking rows found")

    lines.extend(["", "## Method", ""])
    lines.append("The baseline score combines matched `version_summary.csv` metrics where available:")
    lines.append("AutoPos RMS/p95, static p95/max, roto radius/center metrics, split alignment, and light geometry terms.")
    lines.append("Rows without matched evaluation metrics are kept as geometry-only, low-confidence candidates.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    args.top_dir.mkdir(parents=True, exist_ok=True)

    feature_rows = load_csv(args.features)
    layouts = load_layout_db(args.layout_db)
    scores = build_scores(feature_rows)

    write_scores(args.feature_dir / "layout_scores.csv", scores)
    write_top_outputs(args.top_dir, scores, layouts, args.top_n_per_group)
    write_report(args.report_dir / "layout_ranking_report.md", scores)

    print(f"ranked={len(scores)} groups={len(set(row.score_group for row in scores))}")
    print(f"wrote {args.feature_dir / 'layout_scores.csv'}")
    print(f"wrote {args.top_dir / 'top_layouts.csv'}")
    print(f"wrote {args.report_dir / 'layout_ranking_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
