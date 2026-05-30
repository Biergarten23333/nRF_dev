#!/usr/bin/env python3
"""Create first-pass OptiTrack validation and correlation tables.

This is calibration/validation only. It does not train a model.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TABLE_DIR = Path("DATASETS/raw_captures/28052026_Erlangen_Official/Analysis/official_extra_analysis/tables")
LAYOUT_FEATURES = Path("DATASETS/features/layout_features.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


VERSION_ALIASES = {
    "v1": "v1",
    "v1-old": "v1-old",
    "v2": "v2",
    "v3full": "v3-full",
    "v3-full": "v3-full",
    "v3lite": "v3-lite",
    "v3-lite": "v3-lite",
    "v4": "v4",
    "v4-io": "v4-io",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-dir", type=Path, default=TABLE_DIR)
    parser.add_argument("--layout-features", type=Path, default=LAYOUT_FEATURES)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def normalize_version(value: str) -> str:
    return VERSION_ALIASES.get((value or "").strip().lower(), (value or "").strip())


def read_csv(path: Path) -> list[dict[str, str]]:
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


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str] | None = None) -> None:
    if fieldnames is None:
        fieldnames = []
        for row in rows:
            for key in row:
                if key not in fieldnames:
                    fieldnames.append(key)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 1e-12 or vy <= 1e-12:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return cov / math.sqrt(vx * vy)


def ranks(values: list[float]) -> list[float]:
    order = sorted((value, idx) for idx, value in enumerate(values))
    out = [0.0] * len(values)
    pos = 0
    while pos < len(order):
        end = pos + 1
        while end < len(order) and order[end][0] == order[pos][0]:
            end += 1
        avg_rank = (pos + 1 + end) / 2.0
        for _, idx in order[pos:end]:
            out[idx] = avg_rank
        pos = end
    return out


def spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 3:
        return None
    return pearson(ranks(xs), ranks(ys))


def correlation_rows(rows: list[dict[str, Any]], x_fields: list[str], y_fields: list[str], context: dict[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for x_field in x_fields:
        for y_field in y_fields:
            xs: list[float] = []
            ys: list[float] = []
            for row in rows:
                x = as_float(row.get(x_field))
                y = as_float(row.get(y_field))
                if x is None or y is None:
                    continue
                xs.append(x)
                ys.append(y)
            if len(xs) < 3:
                continue
            out.append(
                {
                    **context,
                    "x_feature": x_field,
                    "y_error": y_field,
                    "n": len(xs),
                    "pearson_r": "" if pearson(xs, ys) is None else f"{pearson(xs, ys):.6f}",
                    "spearman_r": "" if spearman(xs, ys) is None else f"{spearman(xs, ys):.6f}",
                    "x_min": f"{min(xs):.6g}",
                    "x_max": f"{max(xs):.6g}",
                    "y_min": f"{min(ys):.6g}",
                    "y_max": f"{max(ys):.6g}",
                }
            )
    return out


def build_layout_validation(table_dir: Path, layout_features_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tag_summary = read_csv(table_dir / "tag_accuracy_summary.csv")
    layout_features = read_csv(layout_features_path)
    feature_by_version = {
        normalize_version(row["solver_version"]): row
        for row in layout_features
        if row.get("capture_id") == "28052026_Erlangen_Official"
    }

    joined: list[dict[str, Any]] = []
    for row in tag_summary:
        version = normalize_version(row.get("version", ""))
        feature = feature_by_version.get(version, {})
        out = {
            "version": version,
            "eval_set": row.get("eval_set", ""),
            "layout_id": feature.get("layout_id", ""),
            "n": row.get("n", ""),
            "opti_err_3d_median_mm": row.get("err_3d_median_mm", ""),
            "opti_err_3d_p95_mm": row.get("err_3d_p95_mm", ""),
            "opti_err_3d_rms_mm": row.get("err_3d_rms_mm", ""),
            "opti_err_horizontal_median_mm": row.get("err_horizontal_median_mm", ""),
            "opti_err_vertical_median_mm": row.get("err_vertical_median_mm", ""),
            "layout_eval_autopos_rms_mm": feature.get("eval_autopos_rms_mm", ""),
            "layout_eval_autopos_p95_mm": feature.get("eval_autopos_p95_mm", ""),
            "layout_eval_static_p95_mm": feature.get("eval_static_p95_mm", ""),
            "layout_eval_roto_abs_deltaR_p95_mm": feature.get("eval_roto_abs_deltaR_p95_mm", ""),
            "xy_hull_coverage_ratio": feature.get("xy_hull_coverage_ratio", ""),
            "z_span_mm": feature.get("z_span_mm", ""),
        }
        joined.append(out)

    corr = correlation_rows(
        [row for row in joined if row["eval_set"] == "all8"],
        [
            "layout_eval_autopos_rms_mm",
            "layout_eval_autopos_p95_mm",
            "layout_eval_static_p95_mm",
            "layout_eval_roto_abs_deltaR_p95_mm",
            "xy_hull_coverage_ratio",
            "z_span_mm",
        ],
        [
            "opti_err_3d_median_mm",
            "opti_err_3d_p95_mm",
            "opti_err_3d_rms_mm",
            "opti_err_vertical_median_mm",
        ],
        {"level": "layout", "capture_id": "28052026_Erlangen_Official", "eval_set": "all8", "grid_mm": ""},
    )
    return joined, corr


def build_session_validation(table_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    errors = read_csv(table_dir / "tag_abs_errors_per_session.csv")
    dop_files = sorted(table_dir.glob("dop_by_facing_group_grid*.csv"))

    errors_by_key = {
        (normalize_version(row.get("version", "")), row.get("eval_set", ""), row.get("ID", "")): row
        for row in errors
    }

    joined: list[dict[str, Any]] = []
    for path in dop_files:
        grid = path.stem.rsplit("grid", 1)[-1]
        for dop in read_csv(path):
            key = (normalize_version(dop.get("version", "")), dop.get("mask", ""), dop.get("ID", ""))
            err = errors_by_key.get(key)
            if not err:
                continue
            joined.append(
                {
                    "version": key[0],
                    "eval_set": key[1],
                    "ID": key[2],
                    "grid_mm": dop.get("grid_mm", grid),
                    "location": dop.get("location", err.get("location", "")),
                    "height": dop.get("height", err.get("height", "")),
                    "facing": dop.get("facing", err.get("facing", "")),
                    "gdop": dop.get("gdop", ""),
                    "hdop": dop.get("hdop", ""),
                    "vdop": dop.get("vdop", ""),
                    "cond": dop.get("cond", ""),
                    "dop_radial_p95_mm": dop.get("radial_p95", ""),
                    "pct_ge8": dop.get("pct_ge8", ""),
                    "err_3d_mm": err.get("err_3d_mm", ""),
                    "err_horizontal_mm": err.get("err_horizontal_mm", ""),
                    "err_vertical_mm": err.get("err_vertical_mm", ""),
                    "distance_to_array_centroid_mm": err.get("distance_to_array_centroid_mm", ""),
                    "scale_bias_expected_mm": err.get("scale_bias_expected_mm", ""),
                }
            )

    corr: list[dict[str, Any]] = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in joined:
        grouped[(row["version"], row["eval_set"], row["grid_mm"])].append(row)
    for (version, eval_set, grid_mm), rows in sorted(grouped.items()):
        corr.extend(
            correlation_rows(
                rows,
                ["gdop", "hdop", "vdop", "cond", "dop_radial_p95_mm", "pct_ge8", "distance_to_array_centroid_mm"],
                ["err_3d_mm", "err_horizontal_mm", "err_vertical_mm"],
                {
                    "level": "session",
                    "capture_id": "28052026_Erlangen_Official",
                    "version": version,
                    "eval_set": eval_set,
                    "grid_mm": grid_mm,
                },
            )
        )
    return joined, corr


def write_report(path: Path, layout_rows: list[dict[str, Any]], layout_corr: list[dict[str, Any]], session_rows: list[dict[str, Any]], session_corr: list[dict[str, Any]]) -> None:
    def strongest(rows: list[dict[str, Any]], n: int = 10) -> list[dict[str, Any]]:
        def key(row: dict[str, Any]) -> float:
            val = as_float(row.get("spearman_r"))
            return abs(val) if val is not None else -1.0
        return sorted(rows, key=key, reverse=True)[:n]

    lines = [
        "# OptiTrack Validation Correlations",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Layout validation rows: `{len(layout_rows)}`",
        f"- Layout correlation rows: `{len(layout_corr)}`",
        f"- Session validation rows: `{len(session_rows)}`",
        f"- Session correlation rows: `{len(session_corr)}`",
        "- No GPU is used by this script.",
        "",
        "## Strongest Layout-Level Correlations",
        "",
        "| X feature | Y error | N | Pearson r | Spearman r |",
        "|---|---|---:|---:|---:|",
    ]
    for row in strongest(layout_corr):
        lines.append(
            f"| `{row['x_feature']}` | `{row['y_error']}` | {row['n']} | "
            f"{row['pearson_r']} | {row['spearman_r']} |"
        )

    lines.extend(["", "## Strongest Session-Level Correlations", ""])
    lines.extend(["| Version | Eval set | Grid | X feature | Y error | N | Pearson r | Spearman r |", "|---|---|---:|---|---|---:|---:|---:|"])
    for row in strongest(session_corr, 12):
        lines.append(
            f"| `{row.get('version', '')}` | `{row.get('eval_set', '')}` | {row.get('grid_mm', '')} | "
            f"`{row['x_feature']}` | `{row['y_error']}` | {row['n']} | {row['pearson_r']} | {row['spearman_r']} |"
        )

    lines.extend(["", "## Interpretation Guardrails", ""])
    lines.append("- Layout-level correlations use only 5 solver versions for `all8`; treat them as directional hints.")
    lines.append("- Session-level DOP correlations currently apply to Erlangen `v4-io` DOP grids.")
    lines.append("- These tables are for feature calibration, not supervised model training.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    layout_rows, layout_corr = build_layout_validation(args.table_dir, args.layout_features)
    session_rows, session_corr = build_session_validation(args.table_dir)

    write_csv(args.feature_dir / "optitrack_layout_validation.csv", layout_rows)
    write_csv(args.feature_dir / "optitrack_layout_correlations.csv", layout_corr)
    write_csv(args.feature_dir / "optitrack_session_validation.csv", session_rows)
    write_csv(args.feature_dir / "optitrack_session_correlations.csv", session_corr)
    write_report(args.report_dir / "optitrack_validation_correlations.md", layout_rows, layout_corr, session_rows, session_corr)

    print(f"layout_rows={len(layout_rows)} layout_corr={len(layout_corr)}")
    print(f"session_rows={len(session_rows)} session_corr={len(session_corr)}")
    print(f"wrote {args.report_dir / 'optitrack_validation_correlations.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
