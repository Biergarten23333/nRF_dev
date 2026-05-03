#!/usr/bin/env python3
"""Replay broadcast TR rows with constrained 4-anchor subsets.

The broadcast Tag firmware emits TR rows with per-anchor ranges.  This script
re-solves positions offline using only 4-anchor subsets constrained to:

  * exactly 2 lower anchors (A-D) and 2 upper anchors (E-H)
  * non-coplanar geometry, using tetrahedron volume as the check

It also replays an all-valid solve for comparison and joins the original TS
rows from positions_all.csv.
"""

from __future__ import annotations

import argparse
import csv
import itertools
import json
import math
import pathlib
import statistics
from collections import Counter, defaultdict
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np


DEFAULT_LAYOUT_MM: Dict[int, Tuple[int, int, int]] = {
    0: (0, 0, 0),
    1: (4738, 0, 0),
    2: (3986, 3719, 34),
    3: (-455, 2738, 0),
    4: (66, -44, 1735),
    5: (4411, 71, 1552),
    6: (3851, 3760, 1640),
    7: (-553, 2722, 1561),
}

LOWER = set(range(4))
UPPER = set(range(4, 8))
ANCHOR_LABELS = "ABCDEFGH"


def label(ids: Iterable[int]) -> str:
    return "".join(ANCHOR_LABELS[i] for i in ids)


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return float("nan")
    ordered = sorted(values)
    idx = int((pct / 100.0) * (len(ordered) - 1))
    return ordered[idx]


def load_layout(path: Optional[pathlib.Path]) -> Dict[int, Tuple[float, float, float]]:
    raw: Dict[int, Tuple[int, int, int]]
    if path is None:
        raw = DEFAULT_LAYOUT_MM
    else:
        doc = json.loads(path.read_text())
        if "layout" in doc:
            src = doc["layout"]
        elif "anchors" in doc:
            src = {str(a["id"]): [a["x_mm"], a["y_mm"], a["z_mm"]] for a in doc["anchors"]}
        else:
            src = doc
        raw = {int(k): tuple(map(int, v[:3])) for k, v in src.items()}

    return {i: (xyz[0] / 1000.0, xyz[1] / 1000.0, xyz[2] / 1000.0)
            for i, xyz in raw.items()}


def tetra_volume_m3(layout: Dict[int, Tuple[float, float, float]],
                    ids: Sequence[int]) -> float:
    a, b, c, d = [np.array(layout[i], dtype=float) for i in ids]
    return abs(float(np.dot(b - a, np.cross(c - a, d - a)))) / 6.0


def valid_subsets(layout: Dict[int, Tuple[float, float, float]],
                  min_volume_m3: float) -> List[Tuple[int, int, int, int]]:
    subsets: List[Tuple[int, int, int, int]] = []
    for low in itertools.combinations(sorted(LOWER), 2):
        for high in itertools.combinations(sorted(UPPER), 2):
            ids = tuple(sorted(low + high))
            if tetra_volume_m3(layout, ids) >= min_volume_m3:
                subsets.append(ids)
    return subsets


def solve_3x3(a: np.ndarray, b: np.ndarray) -> Optional[np.ndarray]:
    try:
        return np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        return None


def linear_seed(layout: Dict[int, Tuple[float, float, float]],
                ranges_m: Dict[int, float],
                ids: Sequence[int]) -> Optional[np.ndarray]:
    ref = ids[0]
    pref = np.array(layout[ref], dtype=float)
    rref2 = ranges_m[ref] * ranges_m[ref]
    pref2 = float(np.dot(pref, pref))
    ata = np.zeros((3, 3), dtype=float)
    atb = np.zeros(3, dtype=float)

    for aid in ids[1:]:
        p = np.array(layout[aid], dtype=float)
        row = 2.0 * (p - pref)
        rhs = rref2 - ranges_m[aid] * ranges_m[aid] - pref2 + float(np.dot(p, p))
        ata += np.outer(row, row)
        atb += row * rhs

    return solve_3x3(ata, atb)


def refine(layout: Dict[int, Tuple[float, float, float]],
           ranges_m: Dict[int, float],
           qualities: Dict[int, int],
           ids: Sequence[int],
           estimate: np.ndarray,
           iterations: int = 8) -> Optional[np.ndarray]:
    x = estimate.astype(float).copy()
    for _ in range(iterations):
        h = np.zeros((3, 3), dtype=float)
        g = np.zeros(3, dtype=float)
        for aid in ids:
            p = np.array(layout[aid], dtype=float)
            diff = x - p
            pred = float(np.linalg.norm(diff))
            if pred < 1e-6:
                pred = 1e-6
            residual = pred - ranges_m[aid]
            jac = diff / pred
            weight = 0.25 + float(qualities.get(aid, 100)) / 100.0
            h += weight * np.outer(jac, jac)
            g += weight * jac * residual
        delta = solve_3x3(h, g)
        if delta is None:
            return None
        x -= delta
        if float(np.linalg.norm(delta)) < 1e-4:
            break
    return x


def solve_position(layout: Dict[int, Tuple[float, float, float]],
                   ranges_m: Dict[int, float],
                   qualities: Dict[int, int],
                   ids: Sequence[int]) -> Optional[Tuple[np.ndarray, float, float]]:
    seed = linear_seed(layout, ranges_m, ids)
    if seed is None:
        return None
    x = refine(layout, ranges_m, qualities, ids, seed)
    if x is None:
        return None

    residuals = []
    for aid in ids:
        p = np.array(layout[aid], dtype=float)
        residuals.append(float(np.linalg.norm(x - p)) - ranges_m[aid])
    rms = math.sqrt(sum(r * r for r in residuals) / len(residuals))
    max_abs = max(abs(r) for r in residuals)
    return x, rms * 1000.0, max_abs * 1000.0


def read_positions(path: pathlib.Path) -> Dict[Tuple[str, str], dict]:
    out: Dict[Tuple[str, str], dict] = {}
    if not path.exists():
        return out
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            out[(row["peer_name"], row["sweep"])] = row
    return out


def read_tr_sweeps(path: pathlib.Path) -> Dict[Tuple[str, str], List[dict]]:
    sweeps: Dict[Tuple[str, str], List[dict]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            sweeps[(row["peer_name"], row["sweep"])].append(row)
    return sweeps


def summarize(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "median": round(statistics.median(values), 3),
        "p75": round(percentile(values, 75), 3),
        "p90": round(percentile(values, 90), 3),
        "p95": round(percentile(values, 95), 3),
        "p99": round(percentile(values, 99), 3),
        "max": round(max(values), 3),
        "mean": round(statistics.mean(values), 3),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recv-dir", type=pathlib.Path, required=True,
                        help="Capture recv_* directory containing tr_all.csv and positions_all.csv")
    parser.add_argument("--layout-json", type=pathlib.Path,
                        help="APOS summary/layout JSON. Defaults to current free_loose APOS layout.")
    parser.add_argument("--out-dir", type=pathlib.Path,
                        help="Output directory. Defaults to <recv-dir>/replay_4anchor_2top2bottom")
    parser.add_argument("--min-quality", type=int, default=50)
    parser.add_argument("--min-volume-m3", type=float, default=0.005,
                        help="Reject nearly coplanar 4-anchor subsets.")
    args = parser.parse_args()

    recv = args.recv_dir
    out_dir = args.out_dir or (recv / "replay_4anchor_2top2bottom")
    out_dir.mkdir(parents=True, exist_ok=True)

    layout = load_layout(args.layout_json)
    subsets = valid_subsets(layout, args.min_volume_m3)
    positions = read_positions(recv / "positions_all.csv")
    sweeps = read_tr_sweeps(recv / "tr_all.csv")

    all_rows = []
    best_rows = []
    all_valid_rows = []
    subset_stats: Dict[str, List[float]] = defaultdict(list)
    subset_counts: Counter[str] = Counter()
    best_counts: Counter[str] = Counter()
    anchor_in_best: Counter[int] = Counter()
    anchor_in_bad_best: Counter[int] = Counter()
    compare_delta: Dict[str, List[float]] = defaultdict(list)

    for key, rows in sweeps.items():
        peer, sweep = key
        valid = {}
        qualities = {}
        host_elapsed_s = rows[0].get("host_elapsed_s", "")
        host_epoch_s = rows[0].get("host_epoch_s", "")
        for row in rows:
            aid = int(row["anchor_id"])
            if row.get("valid") == "1" and row.get("status") == "O" and int(row["quality_percent"]) >= args.min_quality:
                valid[aid] = float(row["range_mm"]) / 1000.0
                qualities[aid] = int(row["quality_percent"])

        pos8 = positions.get(key)
        ts_rms = int(pos8["rms_mm"]) if pos8 and pos8.get("rms_mm") else None
        ts_anchors = pos8.get("anchors", "") if pos8 else ""

        best = None
        for ids in subsets:
            if any(aid not in valid for aid in ids):
                continue
            solved = solve_position(layout, valid, qualities, ids)
            if solved is None:
                continue
            xyz, rms, max_abs = solved
            subset_name = label(ids)
            volume = tetra_volume_m3(layout, ids)
            row_out = {
                "host_elapsed_s": host_elapsed_s,
                "host_epoch_s": host_epoch_s,
                "peer_name": peer,
                "sweep": sweep,
                "subset": subset_name,
                "anchor_ids": ",".join(map(str, ids)),
                "x_mm": round(float(xyz[0]) * 1000.0),
                "y_mm": round(float(xyz[1]) * 1000.0),
                "z_mm": round(float(xyz[2]) * 1000.0),
                "rms_mm": round(rms),
                "max_mm": round(max_abs),
                "volume_m3": f"{volume:.6f}",
                "ts8_rms_mm": ts_rms if ts_rms is not None else "",
                "ts8_anchors": ts_anchors,
            }
            all_rows.append(row_out)
            subset_stats[subset_name].append(rms)
            subset_counts[subset_name] += 1
            if best is None or rms < best[0]:
                best = (rms, max_abs, ids, xyz, row_out)

        if len(valid) >= 4:
            ids_all = tuple(sorted(valid))
            solved_all = solve_position(layout, valid, qualities, ids_all)
            if solved_all is not None:
                xyz, rms, max_abs = solved_all
                all_valid_rows.append({
                    "host_elapsed_s": host_elapsed_s,
                    "host_epoch_s": host_epoch_s,
                    "peer_name": peer,
                    "sweep": sweep,
                    "valid_count": len(ids_all),
                    "anchors": label(ids_all),
                    "x_mm": round(float(xyz[0]) * 1000.0),
                    "y_mm": round(float(xyz[1]) * 1000.0),
                    "z_mm": round(float(xyz[2]) * 1000.0),
                    "rms_mm": round(rms),
                    "max_mm": round(max_abs),
                    "ts8_rms_mm": ts_rms if ts_rms is not None else "",
                    "ts8_anchors": ts_anchors,
                })

        if best is not None:
            rms, max_abs, ids, xyz, row_out = best
            best_row = dict(row_out)
            if ts_rms is not None:
                best_row["delta_vs_ts8_rms_mm"] = round(rms - ts_rms)
                compare_delta[peer].append(rms - ts_rms)
            else:
                best_row["delta_vs_ts8_rms_mm"] = ""
            best_rows.append(best_row)
            subset_name = label(ids)
            best_counts[subset_name] += 1
            for aid in ids:
                anchor_in_best[aid] += 1
                if rms >= 150.0:
                    anchor_in_bad_best[aid] += 1

    def write_csv(name: str, rows: List[dict]) -> None:
        if not rows:
            (out_dir / name).write_text("")
            return
        with (out_dir / name).open("w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    write_csv("replay_4anchor_all_subsets.csv", all_rows)
    write_csv("replay_4anchor_best_per_sweep.csv", best_rows)
    write_csv("replay_all_valid.csv", all_valid_rows)

    subset_summary_rows = []
    for subset_name, rms_values in sorted(subset_stats.items()):
        ids = tuple(ANCHOR_LABELS.index(ch) for ch in subset_name)
        s = summarize(rms_values)
        subset_summary_rows.append({
            "subset": subset_name,
            "anchor_ids": ",".join(map(str, ids)),
            "volume_m3": f"{tetra_volume_m3(layout, ids):.6f}",
            "rows": s["n"],
            "best_count": best_counts[subset_name],
            "rms_median": s.get("median", ""),
            "rms_p90": s.get("p90", ""),
            "rms_p95": s.get("p95", ""),
            "rms_p99": s.get("p99", ""),
            "rms_max": s.get("max", ""),
            "rms_mean": s.get("mean", ""),
        })
    write_csv("replay_4anchor_subset_summary.csv", subset_summary_rows)

    per_peer_summary = []
    for peer in sorted(set([r["peer_name"] for r in best_rows] + [r["peer_name"] for r in all_valid_rows])):
        best_rms = [float(r["rms_mm"]) for r in best_rows if r["peer_name"] == peer]
        all_rms = [float(r["rms_mm"]) for r in all_valid_rows if r["peer_name"] == peer]
        ts_rms = [float(r["rms_mm"]) for (p, _), r in positions.items() if p == peer]
        row = {"peer_name": peer}
        for prefix, values in [("best4", best_rms), ("replay8", all_rms), ("ts8", ts_rms)]:
            s = summarize(values)
            row[f"{prefix}_n"] = s["n"]
            row[f"{prefix}_median"] = s.get("median", "")
            row[f"{prefix}_p95"] = s.get("p95", "")
            row[f"{prefix}_max"] = s.get("max", "")
        d = summarize(compare_delta.get(peer, []))
        row["best4_minus_ts8_median"] = d.get("median", "")
        row["best4_minus_ts8_p95"] = d.get("p95", "")
        per_peer_summary.append(row)
    write_csv("replay_peer_summary.csv", per_peer_summary)

    anchor_badness_rows = []
    for aid in range(8):
        used = anchor_in_best[aid]
        bad = anchor_in_bad_best[aid]
        anchor_badness_rows.append({
            "anchor": ANCHOR_LABELS[aid],
            "anchor_id": aid,
            "best4_used_count": used,
            "best4_bad_rms_ge_150_count": bad,
            "bad_fraction_when_used": round((bad / used), 4) if used else "",
        })
    write_csv("replay_anchor_badness.csv", anchor_badness_rows)

    summary = {
        "recv_dir": str(recv),
        "layout_json": str(args.layout_json) if args.layout_json else "default_free_loose",
        "min_quality": args.min_quality,
        "min_volume_m3": args.min_volume_m3,
        "candidate_subsets": [label(s) for s in subsets],
        "candidate_subset_count": len(subsets),
        "sweeps_with_best4": len(best_rows),
        "all_subset_rows": len(all_rows),
        "all_valid_rows": len(all_valid_rows),
        "peer_summary": per_peer_summary,
        "top_best_subsets": best_counts.most_common(12),
        "outputs": {
            "all_subsets": str(out_dir / "replay_4anchor_all_subsets.csv"),
            "best_per_sweep": str(out_dir / "replay_4anchor_best_per_sweep.csv"),
            "all_valid": str(out_dir / "replay_all_valid.csv"),
            "subset_summary": str(out_dir / "replay_4anchor_subset_summary.csv"),
            "peer_summary": str(out_dir / "replay_peer_summary.csv"),
            "anchor_badness": str(out_dir / "replay_anchor_badness.csv"),
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
