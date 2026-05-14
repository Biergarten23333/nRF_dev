#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


def build(pairs_csv: Path, out_json: Path) -> dict[str, Any]:
    distances: dict[str, int] = {}
    with pairs_csv.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            a = (row.get("a") or "").strip().upper()
            b = (row.get("b") or "").strip().upper()
            if not a or not b:
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
        "source": {"pair_csv": str(pairs_csv.resolve())},
        "notes": [
            "Auto-generated from pair distance CSV.",
            "Used for iterative layout solve with soft constraints.",
        ],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    ap = argparse.ArgumentParser(description="Build inter-anchor matrix json from a pair-distance CSV.")
    ap.add_argument("--pairs-csv", required=True)
    ap.add_argument("--out-json", required=True)
    args = ap.parse_args()

    payload = build(Path(args.pairs_csv), Path(args.out_json))
    print(f"[ok] wrote {args.out_json} pairs={len(payload.get('distances', {}))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

