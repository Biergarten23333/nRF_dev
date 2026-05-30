#!/usr/bin/env python3
"""Build a canonical layout database from raw layout JSON files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RAW_ROOT = Path("DATASETS/raw_captures")
INVENTORY_CSV = Path("DATASETS/processed/raw_inventory.csv")
PROCESSED_DIR = Path("DATASETS/processed")
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
class LayoutIndexRow:
    layout_id: str
    capture_id: str
    source_path: str
    source_group: str
    source_dir: str
    solver_version: str
    solver_family: str
    layout_variant: str
    label: str
    unit: str
    anchor_count: int
    anchor_ids: str
    has_anchor_delays: bool
    tag_delay_mm: str
    bbox_x_mm: float
    bbox_y_mm: float
    bbox_z_mm: float
    source_json_version: str
    source_json_solver: str
    source_json_label: str
    status: str
    notes: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--inventory-csv", type=Path, default=INVENTORY_CSV)
    parser.add_argument("--processed-dir", type=Path, default=PROCESSED_DIR)
    parser.add_argument("--report-dir", type=Path, default=REPORT_DIR)
    return parser.parse_args()


def stable_id(*parts: str) -> str:
    text = "\n".join(parts)
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def normalize_version(value: str) -> str:
    value = (value or "").strip()
    lower = value.lower()
    return VERSION_ALIASES.get(lower, value)


def solver_family(version: str) -> str:
    version = normalize_version(version)
    if version.startswith("v4-io"):
        return "v4-io"
    if version in {"v3full", "v3-full"}:
        return "v3-full"
    if version in {"v3lite", "v3-lite"}:
        return "v3-lite"
    return version


def infer_source_group(rel_path: str, solver_version: str) -> tuple[str, str]:
    parts = rel_path.split("/")
    source_dir_parts = parts[:-1]
    source_dir = "/".join(source_dir_parts)
    version = solver_version.lower()
    group_parts = source_dir_parts[:]
    if group_parts and group_parts[-1].lower() == version:
        group_parts = group_parts[:-1]
    return "/".join(group_parts), source_dir


def coerce_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def read_inventory_layouts(path: Path) -> list[dict[str, str]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if row.get("category") == "layout"]


def normalize_anchor(raw: dict[str, Any], index: int) -> dict[str, Any]:
    anchor_id = coerce_int(raw.get("id", raw.get("anchor_id", index)), index)
    label = str(raw.get("label", chr(ord("A") + anchor_id) if 0 <= anchor_id < 26 else anchor_id))
    x = coerce_float(raw.get("x_mm", raw.get("x", raw.get("x_m"))))
    y = coerce_float(raw.get("y_mm", raw.get("y", raw.get("y_m"))))
    z = coerce_float(raw.get("z_mm", raw.get("z", raw.get("z_m"))))

    unit_guess = "mm"
    if any(key in raw for key in ("x_m", "y_m", "z_m")):
        unit_guess = "m"
    if unit_guess == "m":
        x = None if x is None else x * 1000.0
        y = None if y is None else y * 1000.0
        z = None if z is None else z * 1000.0

    return {
        "anchor_id": anchor_id,
        "label": label,
        "x_mm": x,
        "y_mm": y,
        "z_mm": z,
        "d_anchor_mm": coerce_float(raw.get("d_anchor_mm", raw.get("delay_mm"))),
        "sigma_mm": coerce_float(raw.get("sigma_mm")),
        "source": raw,
    }


def bbox(values: list[float]) -> float:
    return max(values) - min(values) if values else 0.0


def build_layout(row: dict[str, str], raw_root: Path) -> tuple[dict[str, Any] | None, LayoutIndexRow]:
    rel_path = row["rel_path"]
    path = raw_root / rel_path
    notes: list[str] = []
    status = "ok"

    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:  # noqa: BLE001 - keep index row for debugging.
        data = {}
        status = "error"
        notes.append(f"json_error:{exc.__class__.__name__}")

    raw_anchors = data.get("anchors", []) if isinstance(data, dict) else []
    anchors = [normalize_anchor(item, idx) for idx, item in enumerate(raw_anchors) if isinstance(item, dict)]
    anchors.sort(key=lambda item: item["anchor_id"])

    solver_version = normalize_version(row.get("solver_version", ""))
    if not solver_version and isinstance(data, dict):
        solver_version = normalize_version(str(data.get("version", data.get("solver", ""))))
    family = solver_family(solver_version)
    layout_variant = row.get("layout_variant") or "default"
    source_group, source_dir = infer_source_group(rel_path, row.get("solver_version", solver_version))

    coords_ok = all(item["x_mm"] is not None and item["y_mm"] is not None and item["z_mm"] is not None for item in anchors)
    if not anchors:
        status = "error"
        notes.append("no_anchors")
    elif not coords_ok:
        status = "error"
        notes.append("missing_coordinates")

    xs = [float(item["x_mm"]) for item in anchors if item["x_mm"] is not None]
    ys = [float(item["y_mm"]) for item in anchors if item["y_mm"] is not None]
    zs = [float(item["z_mm"]) for item in anchors if item["z_mm"] is not None]
    anchor_ids = [str(item["anchor_id"]) for item in anchors]
    has_delays = any(item.get("d_anchor_mm") is not None for item in anchors)
    tag_delay = data.get("tag_delay_mm", "") if isinstance(data, dict) else ""

    layout_id = stable_id(row.get("capture_id", ""), rel_path)
    label = str(data.get("label", data.get("solver", solver_version)) if isinstance(data, dict) else solver_version)

    canonical = {
        "schema": 1,
        "layout_id": layout_id,
        "capture_id": row.get("capture_id", ""),
        "source_path": rel_path,
        "source_group": source_group,
        "source_dir": source_dir,
        "solver_version": solver_version,
        "solver_family": family,
        "layout_variant": layout_variant,
        "label": label,
        "unit": "mm",
        "anchor_count": len(anchors),
        "anchors": [
            {
                key: value
                for key, value in item.items()
                if key in {"anchor_id", "label", "x_mm", "y_mm", "z_mm", "d_anchor_mm", "sigma_mm"}
            }
            for item in anchors
        ],
        "tag_delay_mm": coerce_float(tag_delay),
        "stats": data.get("stats", {}) if isinstance(data, dict) else {},
        "extra": data.get("extra", {}) if isinstance(data, dict) else {},
        "source_json": {
            "version": str(data.get("version", "")) if isinstance(data, dict) else "",
            "solver": str(data.get("solver", "")) if isinstance(data, dict) else "",
            "label": str(data.get("label", "")) if isinstance(data, dict) else "",
            "config": str(data.get("config", "")) if isinstance(data, dict) else "",
        },
        "status": status,
        "notes": notes,
    }

    index_row = LayoutIndexRow(
        layout_id=layout_id,
        capture_id=row.get("capture_id", ""),
        source_path=rel_path,
        source_group=source_group,
        source_dir=source_dir,
        solver_version=solver_version,
        solver_family=family,
        layout_variant=layout_variant,
        label=label,
        unit="mm",
        anchor_count=len(anchors),
        anchor_ids="|".join(anchor_ids),
        has_anchor_delays=has_delays,
        tag_delay_mm="" if canonical["tag_delay_mm"] is None else str(canonical["tag_delay_mm"]),
        bbox_x_mm=bbox(xs),
        bbox_y_mm=bbox(ys),
        bbox_z_mm=bbox(zs),
        source_json_version=canonical["source_json"]["version"],
        source_json_solver=canonical["source_json"]["solver"],
        source_json_label=canonical["source_json"]["label"],
        status=status,
        notes=";".join(notes),
    )
    return canonical if status == "ok" else None, index_row


def write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for item in items:
            json.dump(item, handle, sort_keys=True)
            handle.write("\n")


def write_index(path: Path, rows: list[LayoutIndexRow]) -> None:
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(LayoutIndexRow.__dataclass_fields__)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def write_report(path: Path, canonical: list[dict[str, Any]], rows: list[LayoutIndexRow]) -> None:
    by_capture: dict[str, list[LayoutIndexRow]] = {}
    for row in rows:
        by_capture.setdefault(row.capture_id, []).append(row)

    lines = [
        "# Layout Database Report",
        "",
        f"Generated: `{datetime.now(timezone.utc).isoformat()}`",
        "",
        "## Summary",
        "",
        f"- Indexed layout files: `{len(rows)}`",
        f"- Valid canonical layouts: `{len(canonical)}`",
        "",
        "## By Capture",
        "",
        "| Capture | Layouts | Valid | Versions | Variants |",
        "|---|---:|---:|---|---|",
    ]
    valid_ids = {item["layout_id"] for item in canonical}
    for capture_id, capture_rows in sorted(by_capture.items()):
        versions = sorted({row.solver_version for row in capture_rows if row.solver_version})
        variants = sorted({row.layout_variant for row in capture_rows if row.layout_variant})
        valid_count = sum(1 for row in capture_rows if row.layout_id in valid_ids)
        lines.append(
            f"| {capture_id} | {len(capture_rows)} | {valid_count} | "
            f"{', '.join(versions)} | {', '.join(variants)} |"
        )

    lines.extend(["", "## Source Groups", ""])
    for row in sorted(rows, key=lambda item: (item.capture_id, item.source_group, item.solver_version, item.layout_variant)):
        lines.append(
            f"- `{row.capture_id}` / `{row.source_group}` / `{row.solver_version}` / "
            f"`{row.layout_variant}` -> `{row.layout_id}`"
        )

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    args.processed_dir.mkdir(parents=True, exist_ok=True)
    args.report_dir.mkdir(parents=True, exist_ok=True)

    inventory_rows = read_inventory_layouts(args.inventory_csv)
    canonical: list[dict[str, Any]] = []
    index_rows: list[LayoutIndexRow] = []
    for row in inventory_rows:
        layout, index_row = build_layout(row, args.raw_root)
        if layout is not None:
            canonical.append(layout)
        index_rows.append(index_row)

    write_jsonl(args.processed_dir / "layout_database.jsonl", canonical)
    write_index(args.processed_dir / "layout_index.csv", index_rows)
    write_report(args.report_dir / "layout_database.md", canonical, index_rows)

    print(f"indexed={len(index_rows)} valid={len(canonical)}")
    print(f"wrote {args.processed_dir / 'layout_database.jsonl'}")
    print(f"wrote {args.processed_dir / 'layout_index.csv'}")
    print(f"wrote {args.report_dir / 'layout_database.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
