#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import least_squares


ANCHORS = "ABCDEFGH"


def load_layout(path: Path) -> np.ndarray:
    raw = json.loads(path.read_text(encoding="utf-8"))
    xyz = np.zeros((8, 3), dtype=float)
    for ent in raw["anchors"]:
        idx = ANCHORS.index(str(ent["label"]).upper()) if "label" in ent else int(ent["id"])
        xyz[idx] = [float(ent["x_mm"]), float(ent["y_mm"]), float(ent["z_mm"])]
    return xyz


def read_tr(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                peer = row["peer_name"]
                sweep = row["sweep"]
                valid = str(row.get("valid", "")).strip() == "1"
                status = str(row.get("status", "")).strip()
                q = int(row.get("quality_percent") or 0)
                aid = int(row["anchor_id"])
                rng = float(row["range_mm"])
            except Exception:
                continue
            if valid and status == "O" and q >= 50 and aid < 8 and rng > 0:
                groups[(peer, sweep)].append({"anchor": aid, "range_mm": rng, "quality": q})
    return groups


TR_RE = re.compile(
    r"\[RECV\]\s+(?P<peer>\S+)\s+notify:\s+TR;2;(?P<sweep>[^;]+);(?P<plan>[^;]*);"
    r"(?P<pmode>[^;]*);(?P<mask>[^;]*);(?P<valid>[^;]*);"
    r"(?P<raw>[^;]*);(?P<filt>[^;]*);(?P<q>[^;]*);(?P<status>[OTXRE]{8})"
)


def read_tr_from_raw(path: Path) -> dict[tuple[str, str], list[dict[str, Any]]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    text = path.read_text(encoding="utf-8", errors="ignore")
    for m in TR_RE.finditer(text):
        peer = m.group("peer")
        sweep = m.group("sweep")
        try:
            ranges = [float(x) for x in m.group("filt").split(",")]
            qualities = [int(float(x)) for x in m.group("q").split(",")]
            statuses = m.group("status")
        except Exception:
            continue
        if len(ranges) != 8 or len(qualities) != 8 or len(statuses) != 8:
            continue
        for aid, (rng, q, status) in enumerate(zip(ranges, qualities, statuses)):
            if status == "O" and q >= 50 and rng > 0:
                groups[(peer, sweep)].append({"anchor": aid, "range_mm": rng, "quality": q})
    return groups


def solve_position(anchor_xyz: np.ndarray, rows: list[dict[str, Any]], dtag_mm: float) -> tuple[np.ndarray, float, float] | None:
    if len(rows) < 4:
        return None
    aids = np.asarray([int(r["anchor"]) for r in rows], dtype=int)
    ranges = np.asarray([float(r["range_mm"]) - dtag_mm for r in rows], dtype=float)
    pts = anchor_xyz[aids]
    centroid = np.mean(pts, axis=0)

    def residual(x: np.ndarray) -> np.ndarray:
        return np.linalg.norm(x - pts, axis=1) - ranges

    sol = least_squares(residual, centroid, loss="huber", f_scale=50.0, max_nfev=100)
    res = residual(sol.x)
    rms = float(np.sqrt(np.mean(res * res)))
    max_abs = float(np.max(np.abs(res)))
    return sol.x, rms, max_abs


def summarize(vals: list[float]) -> dict[str, float | int]:
    if not vals:
        return {"n": 0}
    arr = np.asarray(vals, dtype=float)
    return {
        "n": int(arr.size),
        "mean": float(np.mean(arr)),
        "median": float(np.median(arr)),
        "p75": float(np.percentile(arr, 75)),
        "p90": float(np.percentile(arr, 90)),
        "p95": float(np.percentile(arr, 95)),
        "max": float(np.max(arr)),
    }


def parse_dtag(items: list[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for item in items:
        if "=" not in item:
            raise ValueError(f"bad --dtag item {item!r}, expected TAG=MM")
        tag, val = item.split("=", 1)
        out[tag.strip()] = float(val)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay TR ranges with optional per-tag d_tag range correction.")
    ap.add_argument("--recv-dir", required=True)
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--dtag", action="append", default=[], help="TAG=mm, positive means subtract this from measured ranges")
    args = ap.parse_args()

    recv = Path(args.recv_dir)
    layout = load_layout(Path(args.layout))
    tr_csv = recv / "tr_all.csv"
    groups = read_tr(tr_csv) if tr_csv.exists() else read_tr_from_raw(recv / "raw.log")
    dtag = parse_dtag(args.dtag)

    per_tag_rms: dict[str, list[float]] = defaultdict(list)
    per_tag_max: dict[str, list[float]] = defaultdict(list)
    per_tag_count: dict[str, int] = defaultdict(int)
    per_tag_anchor_counts: dict[str, list[int]] = defaultdict(list)
    for (peer, _sweep), rows in groups.items():
        solved = solve_position(layout, rows, dtag.get(peer, 0.0))
        if solved is None:
            continue
        _pos, rms, max_abs = solved
        per_tag_rms[peer].append(rms)
        per_tag_max[peer].append(max_abs)
        per_tag_count[peer] += 1
        per_tag_anchor_counts[peer].append(len(rows))

    payload = {
        "schema": "tr_dtag_replay_v1",
        "recv_dir": str(recv),
        "layout": str(Path(args.layout)),
        "dtag_mm": dtag,
        "tags": {},
    }
    for tag in sorted(per_tag_rms):
        payload["tags"][tag] = {
            "positions": per_tag_count[tag],
            "rms_mm": summarize(per_tag_rms[tag]),
            "max_abs_mm": summarize(per_tag_max[tag]),
            "anchor_count": summarize([float(v) for v in per_tag_anchor_counts[tag]]),
        }
    Path(args.out_json).write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    print(f"[ok] wrote {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
