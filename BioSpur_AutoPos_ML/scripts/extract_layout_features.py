#!/usr/bin/env python3
"""Extract geometry and existing evaluation features for canonical layouts."""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


RAW_ROOT = Path("DATASETS/raw_captures")
LAYOUT_DB = Path("DATASETS/processed/layout_database.jsonl")
FEATURE_DIR = Path("DATASETS/features")
REPORT_DIR = Path("outputs/reports")


VERSION_ALIASES = {
    "v1": "v1",
    "v1-old": "v1-old",
    "v2": "v2",
    "v3": "v3",
    "v3full": "v3-full",
    "v3-full": "v3-full",
    "v3lite": "v3-lite",
    "v3-lite": "v3-lite",
    "v4": "v4",
    "v4-io": "v4-io",
    "v4-io-roto": "v4-io-roto",
    "v4-io-td": "v4-io-td",
    "v4-io-wand": "v4-io-wand",
    "v5": "v5",
}


@dataclass
class FeatureRow:
    layout_id: str
    capture_id: str
    source_group: str
    source_path: str
    solver_version: str
    solver_family: str
    layout_variant: str
    anchor_count: int
    x_span_mm: float
    y_span_mm: float
    z_span_mm: float
    bbox_area_m2: float
    bbox_volume_m3: float
    xy_hull_area_m2: float
    xy_hull_coverage_ratio: float
    centroid_x_mm: float
    centroid_y_mm: float
    centroid_z_mm: float
    z_mean_mm: float
    z_std_mm: float
    z_min_mm: float
    z_max_mm: float
    lower_anchor_count: int
    upper_anchor_count: int
    layer_gap_mm: float
    lower_layer_z_mean_mm: float
    upper_layer_z_mean_mm: float
    paired_vertical_gap_mean_mm: float
    paired_vertical_gap_std_mm: float
    paired_xy_offset_mean_mm: float
    paired_xy_offset_max_mm: float
    lower_ring_area_m2: float
    upper_ring_area_m2: float
    lower_ring_orientation: str
    upper_ring_orientation: str
    expected_layer_order_ok: bool
    expected_anchor_ids_ok: bool
    pair_count: int
    pair_distance_min_mm: float
    pair_distance_p05_mm: float
    pair_distance_median_mm: float
    pair_distance_mean_mm: float
    pair_distance_std_mm: float
    pair_distance_p95_mm: float
    pair_distance_max_mm: float
    nearest_neighbor_min_mm: float
    nearest_neighbor_mean_mm: float
    xy_pair_distance_min_mm: float
    xy_pair_distance_median_mm: float
    xy_pair_distance_mean_mm: float
    min_centroid_angle_3d_deg: float
    min_centroid_angle_xy_deg: float
    anchor_delay_mean_mm: float
    anchor_delay_std_mm: float
    anchor_delay_span_mm: float
    tag_delay_mm: str
    stats_inter_rms_mm: str
    stats_success: str
    extra_split_align_rms_mm: str
    risk_flag_count: int
    risk_flags: str
    eval_match: bool
    eval_source_path: str
    eval_label: str
    eval_meaning: str
    eval_autopos_rms_mm: str
    eval_autopos_p95_mm: str
    eval_static_n: str
    eval_static_median_mm: str
    eval_static_p95_mm: str
    eval_static_max_mm: str
    eval_roto_n: str
    eval_roto_deltaR_rms_mm: str
    eval_roto_abs_deltaR_median_mm: str
    eval_roto_abs_deltaR_p95_mm: str
    eval_roto_turn_center_median_mm: str
    eval_roto_turn_center_p95_mm: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--layout-db", type=Path, default=LAYOUT_DB)
    parser.add_argument("--feature-dir", type=Path, default=FEATURE_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def normalize_version(value: str) -> str:
    value = (value or "").strip()
    return VERSION_ALIASES.get(value.lower(), value)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def fmt_optional(value: Any) -> str:
    out = as_float(value)
    if out is None:
        return "" if value is None else str(value)
    return f"{out:.9g}"


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def std(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mu = mean(values)
    return math.sqrt(sum((value - mu) ** 2 for value in values) / len(values))


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


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    unique = sorted(set(points))
    if len(unique) <= 1:
        return unique

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for idx, point in enumerate(points):
        nxt = points[(idx + 1) % len(points)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return abs(area) * 0.5


def angle_between(v1: tuple[float, ...], v2: tuple[float, ...]) -> float | None:
    n1 = math.sqrt(sum(value * value for value in v1))
    n2 = math.sqrt(sum(value * value for value in v2))
    if n1 <= 1e-9 or n2 <= 1e-9:
        return None
    dot = sum(a * b for a, b in zip(v1, v2))
    cos_val = max(-1.0, min(1.0, dot / (n1 * n2)))
    return math.degrees(math.acos(cos_val))


def min_pair_angle(vectors: list[tuple[float, ...]]) -> float:
    angles: list[float] = []
    for idx in range(len(vectors)):
        for jdx in range(idx + 1, len(vectors)):
            angle = angle_between(vectors[idx], vectors[jdx])
            if angle is not None:
                acute = min(angle, 180.0 - angle)
                angles.append(acute)
    return min(angles) if angles else 0.0


def signed_polygon_area(points: list[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    area = 0.0
    for idx, point in enumerate(points):
        nxt = points[(idx + 1) % len(points)]
        area += point[0] * nxt[1] - nxt[0] * point[1]
    return 0.5 * area


def ring_orientation(points: list[tuple[float, float]]) -> str:
    area = signed_polygon_area(points)
    if area > 1e-9:
        return "ccw"
    if area < -1e-9:
        return "cw"
    return "degenerate"


def infer_source_group_for_table(path: Path, raw_root: Path) -> str:
    rel = path.relative_to(raw_root)
    parts = list(rel.parts)
    if len(parts) >= 3 and parts[-2].lower() == "tables":
        return "/".join(parts[:-2])
    return "/".join(parts[:-1])


def load_version_evaluations(raw_root: Path) -> dict[tuple[str, str], dict[str, str]]:
    out: dict[tuple[str, str], dict[str, str]] = {}
    for path in sorted(raw_root.rglob("version_summary.csv")):
        source_group = infer_source_group_for_table(path, raw_root)
        with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            for row in csv.DictReader(handle):
                version = normalize_version(row.get("version", ""))
                row["_source_path"] = path.relative_to(raw_root).as_posix()
                row["_source_group"] = source_group
                out[(source_group, version)] = row
    return out


def feature_row(layout: dict[str, Any], evals: dict[tuple[str, str], dict[str, str]]) -> FeatureRow:
    anchors = layout["anchors"]
    anchors_by_id = {int(item["anchor_id"]): item for item in anchors}
    xs = [float(item["x_mm"]) for item in anchors]
    ys = [float(item["y_mm"]) for item in anchors]
    zs = [float(item["z_mm"]) for item in anchors]
    x_span = max(xs) - min(xs) if xs else 0.0
    y_span = max(ys) - min(ys) if ys else 0.0
    z_span = max(zs) - min(zs) if zs else 0.0
    bbox_area = x_span * y_span / 1_000_000.0
    bbox_volume = x_span * y_span * z_span / 1_000_000_000.0

    xy_points = list(zip(xs, ys))
    hull_area_mm2 = polygon_area(convex_hull(xy_points))
    hull_area_m2 = hull_area_mm2 / 1_000_000.0
    hull_ratio = hull_area_m2 / bbox_area if bbox_area > 0.0 else 0.0

    cx = mean(xs)
    cy = mean(ys)
    cz = mean(zs)
    median_z = percentile(zs, 0.5)
    lower = [z for z in zs if z <= median_z]
    upper = [z for z in zs if z > median_z]
    layer_gap = abs(mean(upper) - mean(lower)) if lower and upper else 0.0

    expected_ids = set(range(8))
    expected_anchor_ids_ok = expected_ids.issubset(set(anchors_by_id))
    lower_ids = [0, 1, 2, 3]
    upper_ids = [4, 5, 6, 7]
    lower_zs = [float(anchors_by_id[idx]["z_mm"]) for idx in lower_ids if idx in anchors_by_id]
    upper_zs = [float(anchors_by_id[idx]["z_mm"]) for idx in upper_ids if idx in anchors_by_id]
    lower_layer_z_mean = mean(lower_zs)
    upper_layer_z_mean = mean(upper_zs)
    is_us_height_layout = layout.get("layout_variant") == "us_height"
    if is_us_height_layout:
        # us_height exports are already in a physical-height-like convention.
        expected_layer_order_ok = bool(lower_zs and upper_zs and max(lower_zs) < min(upper_zs))
    else:
        # Native AutoPos layouts use a z sign convention where the upper
        # physical layer commonly has more-negative z values. For physical
        # layer checks, treat height as approximately -z.
        expected_layer_order_ok = bool(lower_zs and upper_zs and min(lower_zs) > max(upper_zs))

    anchor_pairs = [(0, 4), (1, 5), (2, 6), (3, 7)]
    vertical_gaps: list[float] = []
    xy_offsets: list[float] = []
    for lower_id, upper_id in anchor_pairs:
        if lower_id not in anchors_by_id or upper_id not in anchors_by_id:
            continue
        lo = anchors_by_id[lower_id]
        hi = anchors_by_id[upper_id]
        if is_us_height_layout:
            vertical_gaps.append(float(hi["z_mm"]) - float(lo["z_mm"]))
        else:
            vertical_gaps.append(float(lo["z_mm"]) - float(hi["z_mm"]))
        dx = float(hi["x_mm"]) - float(lo["x_mm"])
        dy = float(hi["y_mm"]) - float(lo["y_mm"])
        xy_offsets.append(math.sqrt(dx * dx + dy * dy))

    lower_ring_points = [
        (float(anchors_by_id[idx]["x_mm"]), float(anchors_by_id[idx]["y_mm"]))
        for idx in lower_ids
        if idx in anchors_by_id
    ]
    upper_ring_points = [
        (float(anchors_by_id[idx]["x_mm"]), float(anchors_by_id[idx]["y_mm"]))
        for idx in upper_ids
        if idx in anchors_by_id
    ]
    lower_ring_area = abs(signed_polygon_area(lower_ring_points)) / 1_000_000.0
    upper_ring_area = abs(signed_polygon_area(upper_ring_points)) / 1_000_000.0
    lower_orientation = ring_orientation(lower_ring_points)
    upper_orientation = ring_orientation(upper_ring_points)

    pair_distances: list[float] = []
    xy_pair_distances: list[float] = []
    nearest_by_anchor = [float("inf")] * len(anchors)
    for idx in range(len(anchors)):
        for jdx in range(idx + 1, len(anchors)):
            dx = xs[idx] - xs[jdx]
            dy = ys[idx] - ys[jdx]
            dz = zs[idx] - zs[jdx]
            dist = math.sqrt(dx * dx + dy * dy + dz * dz)
            xy_dist = math.sqrt(dx * dx + dy * dy)
            pair_distances.append(dist)
            xy_pair_distances.append(xy_dist)
            nearest_by_anchor[idx] = min(nearest_by_anchor[idx], dist)
            nearest_by_anchor[jdx] = min(nearest_by_anchor[jdx], dist)
    nearest = [value for value in nearest_by_anchor if math.isfinite(value)]

    vectors_3d = [(x - cx, y - cy, z - cz) for x, y, z in zip(xs, ys, zs)]
    vectors_xy = [(x - cx, y - cy) for x, y in zip(xs, ys)]
    delays = [value for value in (as_float(item.get("d_anchor_mm")) for item in anchors) if value is not None]

    stats = layout.get("stats") or {}
    extra = layout.get("extra") or {}
    eval_row = evals.get((layout["source_group"], normalize_version(layout["solver_version"])), {})

    risk_flags: list[str] = []
    if len(anchors) < 4:
        risk_flags.append("anchor_count_lt4")
    if hull_ratio < 0.55:
        risk_flags.append("low_xy_hull_coverage")
    if z_span < 500.0:
        risk_flags.append("low_vertical_span")
    if pair_distances and min(pair_distances) < 400.0:
        risk_flags.append("close_anchor_pair")
    if x_span <= 0.0 or y_span <= 0.0:
        risk_flags.append("degenerate_xy_bbox")
    if expected_anchor_ids_ok and not expected_layer_order_ok:
        risk_flags.append("unexpected_layer_order")
    if expected_anchor_ids_ok and lower_orientation != "ccw" and not is_us_height_layout:
        risk_flags.append("lower_ring_not_ccw")
    if expected_anchor_ids_ok and upper_orientation != "ccw" and not is_us_height_layout:
        risk_flags.append("upper_ring_not_ccw")

    return FeatureRow(
        layout_id=layout["layout_id"],
        capture_id=layout["capture_id"],
        source_group=layout["source_group"],
        source_path=layout["source_path"],
        solver_version=layout["solver_version"],
        solver_family=layout["solver_family"],
        layout_variant=layout["layout_variant"],
        anchor_count=len(anchors),
        x_span_mm=x_span,
        y_span_mm=y_span,
        z_span_mm=z_span,
        bbox_area_m2=bbox_area,
        bbox_volume_m3=bbox_volume,
        xy_hull_area_m2=hull_area_m2,
        xy_hull_coverage_ratio=hull_ratio,
        centroid_x_mm=cx,
        centroid_y_mm=cy,
        centroid_z_mm=cz,
        z_mean_mm=mean(zs),
        z_std_mm=std(zs),
        z_min_mm=min(zs) if zs else 0.0,
        z_max_mm=max(zs) if zs else 0.0,
        lower_anchor_count=len(lower),
        upper_anchor_count=len(upper),
        layer_gap_mm=layer_gap,
        lower_layer_z_mean_mm=lower_layer_z_mean,
        upper_layer_z_mean_mm=upper_layer_z_mean,
        paired_vertical_gap_mean_mm=mean(vertical_gaps),
        paired_vertical_gap_std_mm=std(vertical_gaps),
        paired_xy_offset_mean_mm=mean(xy_offsets),
        paired_xy_offset_max_mm=max(xy_offsets) if xy_offsets else 0.0,
        lower_ring_area_m2=lower_ring_area,
        upper_ring_area_m2=upper_ring_area,
        lower_ring_orientation=lower_orientation,
        upper_ring_orientation=upper_orientation,
        expected_layer_order_ok=expected_layer_order_ok,
        expected_anchor_ids_ok=expected_anchor_ids_ok,
        pair_count=len(pair_distances),
        pair_distance_min_mm=min(pair_distances) if pair_distances else 0.0,
        pair_distance_p05_mm=percentile(pair_distances, 0.05),
        pair_distance_median_mm=percentile(pair_distances, 0.5),
        pair_distance_mean_mm=mean(pair_distances),
        pair_distance_std_mm=std(pair_distances),
        pair_distance_p95_mm=percentile(pair_distances, 0.95),
        pair_distance_max_mm=max(pair_distances) if pair_distances else 0.0,
        nearest_neighbor_min_mm=min(nearest) if nearest else 0.0,
        nearest_neighbor_mean_mm=mean(nearest),
        xy_pair_distance_min_mm=min(xy_pair_distances) if xy_pair_distances else 0.0,
        xy_pair_distance_median_mm=percentile(xy_pair_distances, 0.5),
        xy_pair_distance_mean_mm=mean(xy_pair_distances),
        min_centroid_angle_3d_deg=min_pair_angle(vectors_3d),
        min_centroid_angle_xy_deg=min_pair_angle(vectors_xy),
        anchor_delay_mean_mm=mean(delays),
        anchor_delay_std_mm=std(delays),
        anchor_delay_span_mm=(max(delays) - min(delays)) if delays else 0.0,
        tag_delay_mm=fmt_optional(layout.get("tag_delay_mm")),
        stats_inter_rms_mm=fmt_optional(stats.get("inter_rms")),
        stats_success=str(stats.get("success", "")),
        extra_split_align_rms_mm=fmt_optional(extra.get("split_align_rms")),
        risk_flag_count=len(risk_flags),
        risk_flags=";".join(risk_flags),
        eval_match=bool(eval_row),
        eval_source_path=eval_row.get("_source_path", ""),
        eval_label=eval_row.get("label", ""),
        eval_meaning=eval_row.get("meaning", ""),
        eval_autopos_rms_mm=fmt_optional(eval_row.get("autopos_rms")),
        eval_autopos_p95_mm=fmt_optional(eval_row.get("autopos_p95")),
        eval_static_n=fmt_optional(eval_row.get("static_n")),
        eval_static_median_mm=fmt_optional(eval_row.get("static_median")),
        eval_static_p95_mm=fmt_optional(eval_row.get("static_p95")),
        eval_static_max_mm=fmt_optional(eval_row.get("static_max")),
        eval_roto_n=fmt_optional(eval_row.get("roto_n")),
        eval_roto_deltaR_rms_mm=fmt_optional(eval_row.get("roto_deltaR_rms")),
        eval_roto_abs_deltaR_median_mm=fmt_optional(eval_row.get("roto_abs_deltaR_median")),
        eval_roto_abs_deltaR_p95_mm=fmt_optional(eval_row.get("roto_abs_deltaR_p95")),
        eval_roto_turn_center_median_mm=fmt_optional(eval_row.get("roto_turn_center_median")),
        eval_roto_turn_center_p95_mm=fmt_optional(eval_row.get("roto_turn_center_p95")),
    )


def write_csv(path: Path, rows: Iterable[dict[str, Any]], fieldnames: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def extract_version_evaluation_table(evals: dict[tuple[str, str], dict[str, str]], raw_root: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for (source_group, version), row in sorted(evals.items()):
        out = dict(row)
        out["source_group"] = source_group
        out["version"] = version
        out["capture_id"] = source_group.split("/", 1)[0]
        out["source_path"] = out.pop("_source_path", "")
        out.pop("_source_group", None)
        rows.append(out)
    return rows


def extract_dop_features(raw_root: Path) -> tuple[list[dict[str, str]], list[str]]:
    rows: list[dict[str, str]] = []
    fieldnames: list[str] = [
        "capture_id",
        "source_path",
        "table_kind",
    ]
    for path in sorted(raw_root.rglob("*dop*.csv")):
        rel = path.relative_to(raw_root).as_posix()
        table_kind = path.stem
        with path.open("r", newline="", encoding="utf-8", errors="replace") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                out = {
                    "capture_id": rel.split("/", 1)[0],
                    "source_path": rel,
                    "table_kind": table_kind,
                }
                out.update(row)
                rows.append(out)
                for key in out:
                    if key not in fieldnames:
                        fieldnames.append(key)
    return rows, fieldnames


def write_report(path: Path, features: list[FeatureRow], eval_rows: list[dict[str, str]], dop_rows: list[dict[str, str]]) -> None:
    by_capture = Counter(row.capture_id for row in features)
    eval_matched = sum(1 for row in features if row.eval_match)
    risk_counts = Counter()
    for row in features:
        for flag in row.risk_flags.split(";"):
            if flag:
                risk_counts[flag] += 1

    lines = [
        "# Layout Feature Extraction",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Layout feature rows: `{len(features)}`",
        f"- Rows with matched version evaluation: `{eval_matched}`",
        f"- Version evaluation rows: `{len(eval_rows)}`",
        f"- DOP rows: `{len(dop_rows)}`",
        "",
        "## Layout Rows By Capture",
        "",
    ]
    for capture_id, count in sorted(by_capture.items()):
        lines.append(f"- `{capture_id}`: {count}")

    lines.extend(["", "## Risk Flags", ""])
    if risk_counts:
        for flag, count in sorted(risk_counts.items()):
            lines.append(f"- `{flag}`: {count}")
    else:
        lines.append("- none")

    lines.extend(["", "## Notes", ""])
    lines.append("- Existing `version_summary.csv` metrics are merged only when source group and solver version match.")
    lines.append("- DOP tables are exported separately because current DOP grids are capture-level analysis artifacts.")
    lines.append("- No GPU is used by this script.")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.feature_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    layouts = load_jsonl(args.layout_db)
    evals = load_version_evaluations(args.raw_root)
    features = [feature_row(layout, evals) for layout in layouts]

    feature_dicts = [asdict(row) for row in features]
    feature_fieldnames = list(asdict(features[0]).keys()) if features else list(FeatureRow.__dataclass_fields__)
    write_csv(args.feature_dir / "layout_features.csv", feature_dicts, feature_fieldnames)

    eval_rows = extract_version_evaluation_table(evals, args.raw_root)
    eval_fieldnames: list[str] = []
    for row in eval_rows:
        for key in row:
            if key not in eval_fieldnames:
                eval_fieldnames.append(key)
    write_csv(args.feature_dir / "version_evaluation_features.csv", eval_rows, eval_fieldnames)

    dop_rows, dop_fieldnames = extract_dop_features(args.raw_root)
    write_csv(args.feature_dir / "dop_grid_features.csv", dop_rows, dop_fieldnames)

    write_report(args.report_dir / "layout_features.md", features, eval_rows, dop_rows)

    print(f"layout_features={len(features)} eval_rows={len(eval_rows)} dop_rows={len(dop_rows)}")
    print(f"wrote {args.feature_dir / 'layout_features.csv'}")
    print(f"wrote {args.feature_dir / 'version_evaluation_features.csv'}")
    print(f"wrote {args.feature_dir / 'dop_grid_features.csv'}")
    print(f"wrote {args.report_dir / 'layout_features.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
