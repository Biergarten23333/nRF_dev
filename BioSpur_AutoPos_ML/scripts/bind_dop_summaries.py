#!/usr/bin/env python3
"""Bind reliable DOP summaries to canonical layouts.

The binding is deliberately conservative. A DOP row is attached to a layout only
when `(capture_id, version)` maps to exactly one canonical layout. This currently
binds the Erlangen official `v4-io` DOP analysis and leaves ambiguous capture-
level artifacts unbound.
"""

from __future__ import annotations

import argparse
import csv
import math
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


LAYOUT_FEATURES = Path("DATASETS/features/layout_features.csv")
DOP_FEATURES = Path("DATASETS/features/dop_grid_features.csv")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


NUMERIC_FIELDS = [
    "gdop",
    "hdop",
    "vdop",
    "cond",
    "radial_p95",
    "pct_ge8",
    "gdop_median",
    "hdop_median",
    "vdop_median",
    "vdop_p90",
    "vdop_p95",
    "cond_p95",
    "radial_p95_median",
    "pct_ge8_median",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout-features", type=Path, default=LAYOUT_FEATURES)
    parser.add_argument("--dop-features", type=Path, default=DOP_FEATURES)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


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


def percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def stress_variant(table_kind: str) -> str:
    if "pairdrop" in table_kind:
        return "pairdrop"
    if "rangebias" in table_kind:
        return "rangebias"
    return "baseline"


def table_family(table_kind: str) -> str:
    if table_kind.startswith("dop_by_facing_group"):
        return "by_session"
    if table_kind.startswith("dop_facing_height_summary"):
        return "facing_height_summary"
    if table_kind.startswith("dop_grid_summary"):
        return "grid_summary"
    return "other"


def bind_rows(layout_rows: list[dict[str, str]], dop_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    layouts_by_capture_version: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in layout_rows:
        if row.get("layout_variant") == "default":
            layouts_by_capture_version[(row["capture_id"], row["solver_version"])].append(row)

    bound: list[dict[str, Any]] = []
    unbound: list[dict[str, str]] = []
    for row in dop_rows:
        version = row.get("version", "")
        key = (row.get("capture_id", ""), version)
        candidates = layouts_by_capture_version.get(key, [])
        if version and len(candidates) == 1:
            layout = candidates[0]
            out = dict(row)
            out["layout_id"] = layout["layout_id"]
            out["source_group"] = layout["source_group"]
            out["solver_version"] = layout["solver_version"]
            out["stress_variant"] = stress_variant(row.get("table_kind", ""))
            out["table_family"] = table_family(row.get("table_kind", ""))
            out["binding_confidence"] = "unique_capture_version"
            bound.append(out)
        else:
            unbound.append(row)
    return bound, unbound


def summarize(bound_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in bound_rows:
        key = (
            row["layout_id"],
            row.get("grid_mm", ""),
            row.get("mask", ""),
            row.get("stress_variant", ""),
            row.get("table_family", ""),
        )
        groups[key].append(row)

    summaries: list[dict[str, Any]] = []
    for (layout_id, grid_mm, mask, stress, family), rows in sorted(groups.items()):
        first = rows[0]
        out: dict[str, Any] = {
            "layout_id": layout_id,
            "capture_id": first.get("capture_id", ""),
            "source_group": first.get("source_group", ""),
            "solver_version": first.get("solver_version", ""),
            "grid_mm": grid_mm,
            "mask": mask,
            "stress_variant": stress,
            "table_family": family,
            "n_rows": len(rows),
            "source_tables": "|".join(sorted({row.get("source_path", "") for row in rows})),
            "binding_confidence": first.get("binding_confidence", ""),
        }
        for field in NUMERIC_FIELDS:
            values = [value for value in (as_float(row.get(field)) for row in rows) if value is not None]
            if values:
                out[f"{field}_mean"] = f"{mean(values):.9g}"
                out[f"{field}_median"] = f"{percentile(values, 0.5):.9g}"
                out[f"{field}_p95"] = f"{percentile(values, 0.95):.9g}"
                out[f"{field}_max"] = f"{max(values):.9g}"
            else:
                out[f"{field}_mean"] = ""
                out[f"{field}_median"] = ""
                out[f"{field}_p95"] = ""
                out[f"{field}_max"] = ""

        gdop_values = [value for value in (as_float(row.get("gdop")) for row in rows) if value is not None]
        vdop_values = [value for value in (as_float(row.get("vdop")) for row in rows) if value is not None]
        cond_values = [value for value in (as_float(row.get("cond")) for row in rows) if value is not None]
        out["bad_gdop_gt_1p2_ratio"] = f"{sum(1 for v in gdop_values if v > 1.2) / len(gdop_values):.9g}" if gdop_values else ""
        out["bad_vdop_gt_1p0_ratio"] = f"{sum(1 for v in vdop_values if v > 1.0) / len(vdop_values):.9g}" if vdop_values else ""
        out["bad_cond_gt_5_ratio"] = f"{sum(1 for v in cond_values if v > 5.0) / len(cond_values):.9g}" if cond_values else ""
        summaries.append(out)
    return summaries


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


def write_report(path: Path, bound_rows: list[dict[str, Any]], unbound_rows: list[dict[str, str]], summaries: list[dict[str, Any]]) -> None:
    by_layout = defaultdict(int)
    for row in bound_rows:
        by_layout[row["layout_id"]] += 1
    lines = [
        "# DOP Layout Binding",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- DOP rows: `{len(bound_rows) + len(unbound_rows)}`",
        f"- Bound rows: `{len(bound_rows)}`",
        f"- Unbound rows: `{len(unbound_rows)}`",
        f"- Summary rows: `{len(summaries)}`",
        "- Binding rule: attach only when `(capture_id, version)` maps to one default layout.",
        "- No GPU is used by this script.",
        "",
        "## Bound Layouts",
        "",
    ]
    if by_layout:
        for layout_id, count in sorted(by_layout.items()):
            lines.append(f"- `{layout_id}`: {count} DOP rows")
    else:
        lines.append("- none")
    lines.extend(["", "## Baseline All8 Summaries", ""])
    for row in summaries:
        if row["stress_variant"] == "baseline" and row["mask"] == "all8" and row["table_family"] == "by_session":
            lines.append(
                f"- layout `{row['layout_id']}`, grid `{row['grid_mm']}`: "
                f"gdop_mean `{row.get('gdop_mean', '')}`, vdop_mean `{row.get('vdop_mean', '')}`, "
                f"cond_p95 `{row.get('cond_p95', '')}`"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    layout_rows = read_csv(args.layout_features)
    dop_rows = read_csv(args.dop_features)
    bound_rows, unbound_rows = bind_rows(layout_rows, dop_rows)
    summaries = summarize(bound_rows)

    write_csv(args.feature_dir / "dop_bound_rows.csv", bound_rows)
    write_csv(args.feature_dir / "dop_summary_by_layout.csv", summaries)
    write_report(args.report_dir / "dop_layout_binding.md", bound_rows, unbound_rows, summaries)

    print(f"dop_rows={len(dop_rows)} bound={len(bound_rows)} unbound={len(unbound_rows)} summaries={len(summaries)}")
    print(f"wrote {args.feature_dir / 'dop_summary_by_layout.csv'}")
    print(f"wrote {args.report_dir / 'dop_layout_binding.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
