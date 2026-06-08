#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
import re
import statistics
from pathlib import Path
from typing import Iterable


ANCHORS = tuple("ABCDEFGH")

ACRX_RE = re.compile(r"\bACRX;1;(?P<seq>\d+);(?P<rx>\d+);(?P<src_kind>[AUT]);(?P<src_id>\d+);")
CRX_RE = re.compile(r"\bCRX;1;(?P<sweep>\d+);(?P<anchor>\d+);")


def iter_lines(paths: Iterable[Path]) -> Iterable[str]:
    for path in paths:
        if path.is_dir():
            for child in sorted(path.rglob("*.log")):
                yield from iter_lines([child])
            continue
        try:
            with path.open("r", encoding="utf-8", errors="ignore") as f:
                yield from f
        except OSError:
            continue


def feature_score(cols: list[str], full: bool) -> float | None:
    # ACRX:
    # 0 ACRX, 1 ver, 2 seq, 3 rx, 4 src_kind, 5 src_id, 6 src_addr,
    # 7 raw_mm, 8 rx_ts, 9 carrier, 10 fp, 11 amp1, 12 amp2, 13 amp3,
    # 14 maxGrowth, 15 preamCount, 16 stdNoise, 17 maxNoise
    # CRX:
    # 0 CRX, 1 ver, 2 sweep, 3 anchor, 4 raw_mm, 5 rx_ts, 6 carrier,
    # 7 fp, 8 amp1, 9 amp2, 10 amp3, 11 maxGrowth, 12 preamCount,
    # 13 stdNoise, 14 maxNoise
    try:
        off = 3 if full else 0
        amp1 = float(cols[8 + off])
        amp2 = float(cols[9 + off])
        amp3 = float(cols[10 + off])
        max_growth = float(cols[11 + off])
        pream = float(cols[12 + off])
        std_noise = max(1.0, float(cols[13 + off]))
        max_noise = max(1.0, float(cols[14 + off]))
    except (ValueError, IndexError):
        return None

    amp_sum = max(0.0, amp1 + amp2 + amp3)
    noise = 0.5 * (std_noise + max_noise)
    snr_like = amp_sum / noise
    growth_like = max_growth / noise
    pream_factor = min(1.0, max(0.1, pream / 118.0))
    score = math.sqrt(max(0.0, snr_like) * max(0.0, growth_like)) * pream_factor
    if not math.isfinite(score) or score <= 0.0:
        return None
    return score


def pair_key(aid: int, bid: int) -> str | None:
    if not (0 <= aid < len(ANCHORS) and 0 <= bid < len(ANCHORS)) or aid == bid:
        return None
    a, b = ANCHORS[aid], ANCHORS[bid]
    return "-".join(sorted((a, b)))


def collect_scores(paths: list[Path]) -> tuple[dict[str, list[float]], dict[str, int]]:
    pair_scores: dict[str, list[float]] = {}
    counts = {"acrx": 0, "crx": 0, "ignored": 0}
    for line in iter_lines(paths):
        if "ACRX;1;" in line:
            token = line[line.find("ACRX;1;") :].strip().split()[0]
            cols = token.split(";")
            m = ACRX_RE.search(token)
            if m is None or len(cols) < 18:
                counts["ignored"] += 1
                continue
            if m.group("src_kind") != "A":
                counts["ignored"] += 1
                continue
            key = pair_key(int(m.group("rx")), int(m.group("src_id")))
            score = feature_score(cols, full=True)
            if key is None or score is None:
                counts["ignored"] += 1
                continue
            pair_scores.setdefault(key, []).append(score)
            counts["acrx"] += 1
        elif "CRX;1;" in line:
            token = line[line.find("CRX;1;") :].strip().split()[0]
            cols = token.split(";")
            m = CRX_RE.search(token)
            if m is None or len(cols) < 15:
                counts["ignored"] += 1
                continue
            # Tag CRX has no anchor-anchor pair. Keep it under T-<anchor> for diagnostics;
            # layout solvers will ignore these keys.
            aid = int(m.group("anchor"))
            if not (0 <= aid < len(ANCHORS)):
                counts["ignored"] += 1
                continue
            score = feature_score(cols, full=False)
            if score is None:
                counts["ignored"] += 1
                continue
            pair_scores.setdefault(f"T-{ANCHORS[aid]}", []).append(score)
            counts["crx"] += 1
    return pair_scores, counts


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Convert compact CIR features (ACRX/CRX logs) into solver pair weights."
    )
    ap.add_argument("paths", nargs="+", help="Log files or directories to scan")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--min-weight", type=float, default=0.2)
    ap.add_argument("--max-weight", type=float, default=1.0)
    ap.add_argument("--reference-percentile", type=float, default=75.0)
    args = ap.parse_args()

    pair_scores, counts = collect_scores([Path(p) for p in args.paths])
    med_scores = {
        key: float(statistics.median(vals))
        for key, vals in pair_scores.items()
        if vals
    }
    anchor_pair_scores = [v for k, v in med_scores.items() if not k.startswith("T-")]
    ref_pool = anchor_pair_scores or list(med_scores.values())
    if not ref_pool:
        raise SystemExit("[error] no ACRX/CRX feature rows found")
    ref_sorted = sorted(ref_pool)
    idx = min(len(ref_sorted) - 1, max(0, round((len(ref_sorted) - 1) * args.reference_percentile / 100.0)))
    ref = max(1.0e-9, ref_sorted[idx])

    weights = {}
    rows = []
    for key, score in sorted(med_scores.items()):
        w = max(args.min_weight, min(args.max_weight, score / ref))
        rows.append(
            {
                "key": key,
                "n": len(pair_scores[key]),
                "median_score": score,
                "weight": float(w),
            }
        )
        if not key.startswith("T-"):
            weights[key] = float(w)

    out = {
        "weights": weights,
        "diagnostic_rows": rows,
        "counts": counts,
        "reference_percentile": args.reference_percentile,
        "reference_score": ref,
        "min_weight": args.min_weight,
        "max_weight": args.max_weight,
        "interpretation": "weights are compact-CIR quality weights; anchor-pair ACRX keys feed layout solver, T-* CRX keys are tag-link diagnostics only",
    }
    out_path = Path(args.out_json)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"[ok] wrote {out_path} anchor_pair_weights={len(weights)} acrx={counts['acrx']} crx={counts['crx']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
