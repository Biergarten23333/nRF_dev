#!/usr/bin/env python3
"""Compute component-based Layout Score v2.

This score remains deterministic and interpretable. It does not use OptiTrack
labels in the production score; OptiTrack is reported as a validation overlay.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LAYOUT_FEATURES = Path("DATASETS/features/layout_features.csv")
DOP_SUMMARY = Path("DATASETS/features/dop_summary_by_layout.csv")
OPTI_VALIDATION = Path("DATASETS/features/optitrack_layout_validation.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


EVAL_METRICS = [
    ("eval_autopos_rms_mm", 0.16, "lower"),
    ("eval_autopos_p95_mm", 0.12, "lower"),
    ("eval_static_p95_mm", 0.16, "lower"),
    ("eval_static_max_mm", 0.06, "lower"),
    ("eval_roto_deltaR_rms_mm", 0.16, "lower"),
    ("eval_roto_abs_deltaR_p95_mm", 0.22, "lower"),
    ("eval_roto_turn_center_p95_mm", 0.12, "lower"),
]


GEOMETRY_METRICS = [
    ("xy_hull_coverage_ratio", 0.18, "higher"),
    ("nearest_neighbor_min_mm", 0.12, "higher"),
    ("paired_xy_offset_mean_mm", 0.20, "lower"),
    ("paired_xy_offset_max_mm", 0.12, "lower"),
    ("paired_vertical_gap_std_mm", 0.14, "lower"),
    ("anchor_delay_span_mm", 0.08, "lower"),
    ("risk_flag_count", 0.16, "lower"),
]


OPTI_METRICS = [
    ("opti_err_3d_median_mm", 0.25, "lower"),
    ("opti_err_3d_p95_mm", 0.25, "lower"),
    ("opti_err_3d_rms_mm", 0.30, "lower"),
    ("opti_err_vertical_median_mm", 0.20, "lower"),
]


@dataclass
class ScoreV2Row:
    layout_id: str
    group_rank_v2: int
    validation_rank: str
    score_group: str
    capture_id: str
    source_group: str
    solver_version: str
    solver_family: str
    layout_variant: str
    production_score_v2: float
    evaluation_score: str
    geometry_score: str
    dop_score: str
    optitrack_validation_score: str
    confidence: str
    component_weights: str
    score_basis: str
    dop_binding: str
    optitrack_eval_set: str
    opti_err_3d_median_mm: str
    opti_err_3d_p95_mm: str
    opti_err_3d_rms_mm: str
    opti_err_vertical_median_mm: str
    eval_autopos_rms_mm: str
    eval_static_p95_mm: str
    eval_roto_abs_deltaR_p95_mm: str
    dop_gdop_mean: str
    dop_gdop_p95: str
    dop_vdop_mean: str
    dop_vdop_p95: str
    dop_cond_p95: str
    dop_bad_gdop_ratio: str
    xy_hull_coverage_ratio: str
    paired_xy_offset_mean_mm: str
    paired_vertical_gap_std_mm: str
    risk_flags: str
    source_path: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-features", type=Path, default=LAYOUT_FEATURES)
    parser.add_argument("--dop-summary", type=Path, default=DOP_SUMMARY)
    parser.add_argument("--opti-validation", type=Path, default=OPTI_VALIDATION)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
        return list(csv.DictReader(handle))


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def norm_group(rows: list[dict[str, str]], metrics: list[tuple[str, float, str]]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for field, _weight, direction in metrics:
        values: list[tuple[str, float]] = []
        for row in rows:
            value = as_float(row.get(field))
            if value is not None:
                values.append((row["layout_id"], value))
        if not values:
            out[field] = {}
            continue
        nums = [value for _layout_id, value in values]
        lo = min(nums)
        hi = max(nums)
        field_scores: dict[str, float] = {}
        if abs(hi - lo) <= 1e-12:
            for layout_id, _value in values:
                field_scores[layout_id] = 0.0
        else:
            for layout_id, value in values:
                if direction == "lower":
                    field_scores[layout_id] = 100.0 * (value - lo) / (hi - lo)
                else:
                    field_scores[layout_id] = 100.0 * (hi - value) / (hi - lo)
        out[field] = field_scores
    return out


def weighted_component(row: dict[str, str], normalized: dict[str, dict[str, float]], metrics: list[tuple[str, float, str]]) -> tuple[str, list[str]]:
    weighted_sum = 0.0
    weight_sum = 0.0
    basis: list[str] = []
    for field, weight, _direction in metrics:
        score = normalized.get(field, {}).get(row["layout_id"])
        if score is None:
            continue
        weighted_sum += weight * score
        weight_sum += weight
        basis.append(field)
    if weight_sum <= 0.0:
        return "", basis
    return f"{weighted_sum / weight_sum:.6f}", basis


def clamp_score(value: float, target: float, worst: float) -> float:
    if worst <= target:
        return 100.0
    return max(0.0, min(100.0, 100.0 * (value - target) / (worst - target)))


def choose_dop_by_layout(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    # Prefer baseline all8 grid25 by-session summaries. Fall back to baseline
    # all8 by-session at other grids.
    candidates: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        if row.get("stress_variant") == "baseline" and row.get("mask") == "all8" and row.get("table_family") == "by_session":
            candidates[row["layout_id"]].append(row)
    chosen: dict[str, dict[str, str]] = {}
    for layout_id, items in candidates.items():
        items.sort(key=lambda row: (0 if row.get("grid_mm") == "25.0" else 1, row.get("grid_mm", "")))
        chosen[layout_id] = items[0]
    return chosen


def dop_component(row: dict[str, str] | None) -> str:
    if not row:
        return ""
    parts: list[float] = []
    for field, target, worst in [
        ("gdop_mean", 1.0, 1.8),
        ("gdop_p95", 1.2, 2.5),
        ("vdop_mean", 0.8, 1.8),
        ("vdop_p95", 1.0, 2.5),
        ("cond_p95", 4.0, 15.0),
        ("bad_gdop_gt_1p2_ratio", 0.0, 0.8),
        ("bad_vdop_gt_1p0_ratio", 0.0, 0.8),
    ]:
        value = as_float(row.get(field))
        if value is not None:
            parts.append(clamp_score(value, target, worst))
    return "" if not parts else f"{sum(parts) / len(parts):.6f}"


def opti_by_layout(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    out: dict[str, dict[str, str]] = {}
    for row in rows:
        if row.get("eval_set") == "all8" and row.get("layout_id"):
            out[row["layout_id"]] = row
    return out


def score_validation(rows: list[dict[str, str]]) -> dict[str, tuple[str, str]]:
    if not rows:
        return {}
    normalized = norm_group(rows, OPTI_METRICS)
    out: dict[str, tuple[str, str]] = {}
    scored: list[tuple[str, float]] = []
    for row in rows:
        score, _basis = weighted_component(row, normalized, OPTI_METRICS)
        if score:
            out[row["layout_id"]] = (score, "")
            scored.append((row["layout_id"], float(score)))
    scored.sort(key=lambda item: item[1])
    for rank, (layout_id, score) in enumerate(scored, start=1):
        out[layout_id] = (f"{score:.6f}", str(rank))
    return out


def build_scores(feature_rows: list[dict[str, str]], dop_rows: list[dict[str, str]], opti_rows: list[dict[str, str]]) -> list[ScoreV2Row]:
    dop = choose_dop_by_layout(dop_rows)
    opti = opti_by_layout(opti_rows)
    opti_scores = score_validation(list(opti.values()))

    by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in feature_rows:
        by_group[row["source_group"]].append(row)

    score_rows: list[ScoreV2Row] = []
    for group, rows in sorted(by_group.items()):
        eval_norm = norm_group(rows, EVAL_METRICS)
        geom_norm = norm_group(rows, GEOMETRY_METRICS)
        group_scored: list[tuple[dict[str, str], dict[str, Any]]] = []

        for row in rows:
            eval_score, eval_basis = weighted_component(row, eval_norm, EVAL_METRICS)
            geom_score, geom_basis = weighted_component(row, geom_norm, GEOMETRY_METRICS)
            dop_row = dop.get(row["layout_id"])
            dop_score = dop_component(dop_row)

            components: list[tuple[str, float, float]] = []
            if eval_score:
                components.append(("evaluation", 0.65, float(eval_score)))
            if geom_score:
                components.append(("geometry", 0.25, float(geom_score)))
            if dop_score:
                components.append(("dop", 0.10, float(dop_score)))

            if not components:
                production = 100.0
            else:
                total_weight = sum(weight for _name, weight, _score in components)
                production = sum(weight * score for _name, weight, score in components) / total_weight

            opti_row = opti.get(row["layout_id"], {})
            opti_score, opti_rank = opti_scores.get(row["layout_id"], ("", ""))

            if opti_score:
                confidence = "optitrack_validated"
            elif dop_score and eval_score:
                confidence = "evaluation_plus_dop"
            elif eval_score:
                confidence = "evaluation_matched"
            else:
                confidence = "geometry_only_low"

            group_scored.append(
                (
                    row,
                    {
                        "production": production,
                        "eval_score": eval_score,
                        "geom_score": geom_score,
                        "dop_score": dop_score,
                        "dop_row": dop_row or {},
                        "opti_row": opti_row,
                        "opti_score": opti_score,
                        "opti_rank": opti_rank,
                        "confidence": confidence,
                        "components": components,
                        "basis": eval_basis + geom_basis + (["dop_summary_by_layout"] if dop_score else []),
                    },
                )
            )

        group_scored.sort(key=lambda item: (item[1]["production"], item[0]["solver_version"], item[0]["layout_variant"]))
        for rank, (row, score) in enumerate(group_scored, start=1):
            dop_row = score["dop_row"]
            opti_row = score["opti_row"]
            score_rows.append(
                ScoreV2Row(
                    layout_id=row["layout_id"],
                    group_rank_v2=rank,
                    validation_rank=score["opti_rank"],
                    score_group=group,
                    capture_id=row["capture_id"],
                    source_group=row["source_group"],
                    solver_version=row["solver_version"],
                    solver_family=row["solver_family"],
                    layout_variant=row["layout_variant"],
                    production_score_v2=score["production"],
                    evaluation_score=score["eval_score"],
                    geometry_score=score["geom_score"],
                    dop_score=score["dop_score"],
                    optitrack_validation_score=score["opti_score"],
                    confidence=score["confidence"],
                    component_weights="|".join(f"{name}:{weight}" for name, weight, _val in score["components"]),
                    score_basis="|".join(score["basis"]),
                    dop_binding=dop_row.get("binding_confidence", ""),
                    optitrack_eval_set=opti_row.get("eval_set", ""),
                    opti_err_3d_median_mm=opti_row.get("opti_err_3d_median_mm", ""),
                    opti_err_3d_p95_mm=opti_row.get("opti_err_3d_p95_mm", ""),
                    opti_err_3d_rms_mm=opti_row.get("opti_err_3d_rms_mm", ""),
                    opti_err_vertical_median_mm=opti_row.get("opti_err_vertical_median_mm", ""),
                    eval_autopos_rms_mm=row.get("eval_autopos_rms_mm", ""),
                    eval_static_p95_mm=row.get("eval_static_p95_mm", ""),
                    eval_roto_abs_deltaR_p95_mm=row.get("eval_roto_abs_deltaR_p95_mm", ""),
                    dop_gdop_mean=dop_row.get("gdop_mean", ""),
                    dop_gdop_p95=dop_row.get("gdop_p95", ""),
                    dop_vdop_mean=dop_row.get("vdop_mean", ""),
                    dop_vdop_p95=dop_row.get("vdop_p95", ""),
                    dop_cond_p95=dop_row.get("cond_p95", ""),
                    dop_bad_gdop_ratio=dop_row.get("bad_gdop_gt_1p2_ratio", ""),
                    xy_hull_coverage_ratio=row.get("xy_hull_coverage_ratio", ""),
                    paired_xy_offset_mean_mm=row.get("paired_xy_offset_mean_mm", ""),
                    paired_vertical_gap_std_mm=row.get("paired_vertical_gap_std_mm", ""),
                    risk_flags=row.get("risk_flags", ""),
                    source_path=row["source_path"],
                )
            )

    return sorted(score_rows, key=lambda row: (row.score_group, row.group_rank_v2))


def write_scores(path: Path, rows: list[ScoreV2Row]) -> None:
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(ScoreV2Row.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            payload = asdict(row)
            payload["production_score_v2"] = f"{row.production_score_v2:.6f}"
            writer.writerow(payload)


def write_report(path: Path, rows: list[ScoreV2Row]) -> None:
    by_group: dict[str, list[ScoreV2Row]] = defaultdict(list)
    confidence = defaultdict(int)
    for row in rows:
        by_group[row.score_group].append(row)
        confidence[row.confidence] += 1

    lines = [
        "# Layout Score v2",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Scored layouts: `{len(rows)}`",
        f"- Score groups: `{len(by_group)}`",
        "- Production score excludes OptiTrack labels.",
        "- OptiTrack validation score is shown only where ground truth exists.",
        "- Lower score is better.",
        "- No GPU is used by this script.",
        "",
        "## Confidence",
        "",
    ]
    for key, count in sorted(confidence.items()):
        lines.append(f"- `{key}`: {count}")

    lines.extend(["", "## Top By Group", ""])
    lines.extend(["| Group | Rank | Version | Variant | Layout | Score v2 | Eval | Geo | DOP | Confidence |", "|---|---:|---|---|---|---:|---:|---:|---:|---|"])
    for group, group_rows in sorted(by_group.items()):
        top = sorted(group_rows, key=lambda row: row.group_rank_v2)[0]
        lines.append(
            f"| `{group}` | {top.group_rank_v2} | `{top.solver_version}` | `{top.layout_variant}` | "
            f"`{top.layout_id}` | {top.production_score_v2:.3f} | {top.evaluation_score} | "
            f"{top.geometry_score} | {top.dop_score} | `{top.confidence}` |"
        )

    erlangen = [
        row for row in rows
        if row.capture_id == "28052026_Erlangen_Official"
        and row.source_group.endswith("v1_to_v4_io_field_check")
    ]
    lines.extend(["", "## Erlangen Official: Production vs OptiTrack", ""])
    lines.extend(["| Prod rank | Val rank | Version | Score v2 | Opti score | 3D RMS | 3D p95 | DOP |", "|---:|---:|---|---:|---:|---:|---:|---:|"])
    for row in sorted(erlangen, key=lambda item: item.group_rank_v2):
        lines.append(
            f"| {row.group_rank_v2} | {row.validation_rank} | `{row.solver_version}` | "
            f"{row.production_score_v2:.3f} | {row.optitrack_validation_score} | "
            f"{row.opti_err_3d_rms_mm} | {row.opti_err_3d_p95_mm} | {row.dop_score} |"
        )

    lines.extend(["", "## Bewertung", ""])
    lines.append("- `v2` remains the best Erlangen official production-score candidate in this scoring setup.")
    lines.append("- `v3-lite` is effectively tied with `v2` on many field metrics and remains a strong candidate.")
    lines.append("- `v4-io` has the only currently bound DOP summary and the best median/vertical OptiTrack behavior, but its p95/RMS validation is weaker than `v2`/`v3-lite`.")
    lines.append("- `v4-io-roto` is the strongest repeated winner in the outdoor 20260513 evaluated groups.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    feature_rows = read_csv(args.layout_features)
    dop_rows = read_csv(args.dop_summary)
    opti_rows = read_csv(args.opti_validation)
    scores = build_scores(feature_rows, dop_rows, opti_rows)
    write_scores(args.feature_dir / "layout_scores_v2.csv", scores)
    write_report(args.report_dir / "layout_score_v2.md", scores)

    print(f"score_v2_rows={len(scores)} groups={len(set(row.score_group for row in scores))}")
    print(f"wrote {args.feature_dir / 'layout_scores_v2.csv'}")
    print(f"wrote {args.report_dir / 'layout_score_v2.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
