#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def parse_sw_line(line: str) -> tuple[str, list[tuple[str, int, int]]]:
    """
    Parse:
      [AUTOPOS] SW-A,B,3685,95,C,5941,100,...
    Returns:
      master, [(other, dist_mm, quality_percent), ...]
    """
    s = line.strip()
    if "SW-" not in s:
        raise ValueError("not a SW line")
    # Keep only after SW-
    s = s.split("SW-", 1)[1]
    parts = [p.strip() for p in s.split(",") if p.strip() != ""]
    if not parts:
        raise ValueError("empty SW payload")
    master = parts[0]
    if len(master) != 1 or not master.isalpha():
        raise ValueError(f"bad master: {master!r}")

    out: list[tuple[str, int, int]] = []
    i = 1
    while i + 2 < len(parts):
        other = parts[i]
        dist_mm = int(float(parts[i + 1]))
        q = int(float(parts[i + 2]))
        if len(other) == 1 and other.isalpha():
            out.append((other, dist_mm, q))
        i += 3
    return master.upper(), out


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert sweep summary.json SW lines into pairs_all.csv")
    ap.add_argument("--summary-json", required=True)
    ap.add_argument("--out-csv", required=True)
    args = ap.parse_args()

    summary = json.loads(Path(args.summary_json).read_text(encoding="utf-8"))
    rounds = summary.get("rounds") or {}

    out_csv = Path(args.out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Keep compatible with existing fuse scripts: a,b,master,dist_mm is required.
    # Additional columns help debugging but are ignored by fusers.
    fieldnames = [
        "a",
        "b",
        "master",
        "dist_mm",
        "quality_percent",
        "raw_mm",
        "ok",
        "fail",
    ]

    rows = 0
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for label, r in rounds.items():
            sw_lines = r.get("sw_lines") or []
            for line in sw_lines:
                master, triples = parse_sw_line(line)
                for other, dist_mm, q in triples:
                    a = min(master, other)
                    b = max(master, other)
                    # Drop clearly invalid SW tuples. In practice, firmware emits
                    # peer,0,0 when a pair has no valid ranging in that set.
                    if dist_mm <= 0 or q <= 0:
                        continue
                    w.writerow(
                        {
                            "a": a,
                            "b": b,
                            "master": master,
                            "dist_mm": dist_mm,
                            "quality_percent": q,
                            "raw_mm": dist_mm,
                            "ok": 1,
                            "fail": 0,
                        }
                    )
                    rows += 1

    print(f"[ok] wrote {out_csv} rows={rows}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
