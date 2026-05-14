#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def build_inter_anchor_matrix_from_pair_csv(pair_csv: Path, output_json: Path) -> dict[str, Any]:
    """
    Build the inter-anchor matrix json expected by solve_anchor_layout_iterative.py
    from a pair-distance CSV with columns: a,b,distance_mm (or dist_mm).
    """
    distances: dict[str, int] = {}
    with pair_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            if not a or not b or a == b:
                continue
            d_raw = row.get("distance_mm") or row.get("dist_mm")
            if d_raw is None or str(d_raw).strip() == "":
                continue
            d = int(round(float(d_raw)))
            if d <= 0:
                continue
            distances[f"{a}-{b}"] = d

    payload = {
        "units": "mm",
        "anchors": list("ABCDEFGH"),
        "distances": distances,
        "pair_stats": {},
        "source": {"pair_csv": str(pair_csv.resolve())},
        "notes": [
            "Auto-generated from pair distance CSV.",
            "Used for iterative layout solve with soft constraints.",
        ],
    }
    output_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Build inter_anchor_matrix json from a pairs distance CSV.")
    ap.add_argument("--pairs-csv", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args()

    pair_csv = Path(args.pairs_csv)
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    build_inter_anchor_matrix_from_pair_csv(pair_csv, out_json)
    print(f"[ok] wrote {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

