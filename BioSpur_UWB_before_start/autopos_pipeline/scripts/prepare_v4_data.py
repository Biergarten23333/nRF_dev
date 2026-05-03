#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


ANCHORS = "ABCDEFGH"


def robust_sigma_mm(vals: list[float], floor: float) -> float:
    if not vals:
        return floor
    m = median(vals)
    mad = median([abs(v - m) for v in vals])
    return max(floor, 1.4826 * mad)


def anchor_id(value: str | int) -> int | None:
    if isinstance(value, int):
        return value if 0 <= value < 8 else None
    s = str(value).strip().upper()
    if not s:
        return None
    if s in ANCHORS:
        return ANCHORS.index(s)
    try:
        i = int(s)
    except ValueError:
        return None
    return i if 0 <= i < 8 else None


def read_inter_anchor_pairs(path: Path, sigma_floor_mm: float, min_quality: float) -> list[dict[str, Any]]:
    groups: dict[tuple[int, int], list[dict[str, float]]] = defaultdict(list)
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ai = anchor_id(row.get("a", ""))
            aj = anchor_id(row.get("b", ""))
            if ai is None or aj is None or ai == aj:
                continue
            try:
                d = float(row.get("dist_mm") or row.get("distance_mm") or row.get("raw_mm") or "")
                q = float(row.get("quality_percent") or row.get("quality") or 100.0)
            except ValueError:
                continue
            if not math.isfinite(d) or d <= 0 or q < min_quality:
                continue
            i, j = sorted((ai, aj))
            groups[(i, j)].append({"range_mm": d, "quality": q})

    out: list[dict[str, Any]] = []
    for (i, j), rows in sorted(groups.items()):
        vals = [r["range_mm"] for r in rows]
        qs = [r["quality"] for r in rows]
        out.append(
            {
                "i": i,
                "j": j,
                "range_mm": float(median(vals)),
                "sigma_mm": robust_sigma_mm(vals, sigma_floor_mm),
                "quality": float(median(qs)),
                "n": len(vals),
                "source": str(path),
            }
        )
    return out


def find_recv_dirs(paths: list[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.is_file():
            if p.name in {"cm_all.csv", "positions_all.csv", "raw.log"}:
                out.append(p.parent)
            continue
        if not p.exists():
            continue
        if (p / "cm_all.csv").exists() or (p / "positions_all.csv").exists():
            out.append(p)
        out.extend(sorted(x.parent for x in p.rglob("cm_all.csv")))
    # Stable unique order.
    seen = set()
    uniq = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def read_tag_ranges_from_cm(recv_dir: Path, sigma_mm: float, min_quality: float) -> list[dict[str, Any]]:
    cm = recv_dir / "cm_all.csv"
    if not cm.exists():
        return []
    out: list[dict[str, Any]] = []
    with cm.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                status = (row.get("status") or "").strip().lower()
                ok = status in {"ok", "success", "valid", "1"} or status == ""
                rng = float(row.get("filt_mm") or row.get("raw_mm") or "")
                q = float(row.get("quality_percent") or row.get("quality") or 100.0)
                aid = anchor_id(row.get("anchor_id", ""))
            except ValueError:
                continue
            if aid is None or not ok or q < min_quality or not math.isfinite(rng) or rng <= 0:
                continue
            t_raw = row.get("host_epoch_s") or row.get("host_elapsed_s") or row.get("sweep") or "0"
            try:
                t = float(t_raw)
            except ValueError:
                t = 0.0
            out.append(
                {
                    "t": t,
                    "tag": row.get("peer_name") or row.get("tag_id") or "",
                    "sweep": row.get("sweep") or "",
                    "anchor": aid,
                    "range_mm": rng,
                    "sigma_mm": sigma_mm,
                    "quality": q,
                    "source": str(cm),
                }
            )
    return out


TS_RE = re.compile(r"(?:^|notify:\s*)TS;")


def read_tag_position_inits(recv_dir: Path, max_rows: int) -> list[dict[str, Any]]:
    pos = recv_dir / "positions_all.csv"
    if not pos.exists():
        return []
    out: list[dict[str, Any]] = []
    with pos.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                x = float(row["x_mm"])
                y = float(row["y_mm"])
                z = float(row["z_mm"])
                rms = float(row.get("rms_mm") or 0.0)
                t = float(row.get("host_epoch_s") or row.get("host_elapsed_s") or row.get("sweep") or 0.0)
            except (KeyError, ValueError):
                continue
            out.append(
                {
                    "t": t,
                    "tag": row.get("peer_name") or row.get("tag_id") or "",
                    "sweep": row.get("sweep") or "",
                    "x_mm": x,
                    "y_mm": y,
                    "z_mm": z,
                    "rms_mm": rms,
                    "anchors": row.get("anchors") or "",
                    "source": str(pos),
                }
            )
            if len(out) >= max_rows:
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Prepare AutoPos V4 fused range dataset.")
    ap.add_argument("--pairs-csv", required=True, help="Inter-anchor pairs_all.csv from AutoPos sweep.")
    ap.add_argument("--tag-capture", action="append", default=[], help="Broadcast recv dir/log root containing cm_all.csv.")
    ap.add_argument("--out", required=True, help="Output JSON path.")
    ap.add_argument("--inter-sigma-floor-mm", type=float, default=30.0)
    ap.add_argument("--inter-min-quality", type=float, default=80.0)
    ap.add_argument("--tag-sigma-mm", type=float, default=80.0)
    ap.add_argument("--tag-min-quality", type=float, default=0.0)
    ap.add_argument("--max-position-inits", type=int, default=5000)
    args = ap.parse_args()

    pairs_csv = Path(args.pairs_csv)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    inter = read_inter_anchor_pairs(pairs_csv, args.inter_sigma_floor_mm, args.inter_min_quality)
    recv_dirs = find_recv_dirs([Path(p) for p in args.tag_capture])

    tag_ranges: list[dict[str, Any]] = []
    tag_position_inits: list[dict[str, Any]] = []
    for d in recv_dirs:
        tag_ranges.extend(read_tag_ranges_from_cm(d, args.tag_sigma_mm, args.tag_min_quality))
        remaining = max(0, args.max_position_inits - len(tag_position_inits))
        if remaining:
            tag_position_inits.extend(read_tag_position_inits(d, remaining))

    payload = {
        "schema": "autopos_v4_data_v1",
        "units": "mm",
        "inter_anchor_ranges": inter,
        "tag_anchor_ranges": tag_ranges,
        "tag_position_initializers": tag_position_inits,
        "sources": {
            "pairs_csv": str(pairs_csv),
            "tag_captures": args.tag_capture,
            "recv_dirs": [str(d) for d in recv_dirs],
        },
        "stats": {
            "inter_anchor_ranges": len(inter),
            "tag_anchor_ranges": len(tag_ranges),
            "tag_position_initializers": len(tag_position_inits),
            "warning": (
                "No tag-anchor range observations found. Current broadcast motion logs often contain TS/RXG/CD only; "
                "V4 Phase A needs per-anchor range rows in cm_all.csv or an equivalent raw range export."
                if not tag_ranges
                else ""
            ),
        },
    }
    out_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload["stats"], indent=2))
    print(f"[ok] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
