#!/usr/bin/env python3
"""summary.json (AutoPos SW- sweep) -> pairs_all.csv (v4-io solver input).

Uses the identical parse as the production stage_field_dataset.py:
  SW-<master>,<peer>,<dist_mm>,<quality>,<peer>,<dist_mm>,<quality>,...
Rows with dist<=0 or quality<=0 are dropped (ok=1/0, fail flags).
"""
import csv
import json
import sys
from pathlib import Path


def parse_sw_line(line: str):
    s = line.strip()
    if "SW-" not in s:
        return None, []
    s = s.split("SW-", 1)[1]
    parts = s.split(",")
    master = parts[0].strip()
    triples = []
    i = 1
    while i + 2 < len(parts) + 1 and i + 1 < len(parts):
        other = parts[i].strip()
        try:
            dist_mm = int(float(parts[i + 1]))
            quality = int(float(parts[i + 2])) if i + 2 < len(parts) else 0
        except (ValueError, IndexError):
            break
        if other:
            triples.append((other, dist_mm, quality))
        i += 3
    return master, triples


def extract(summary_json: Path, out_csv: Path) -> int:
    summary = json.loads(summary_json.read_text(encoding="utf-8"))
    fieldnames = ["a", "b", "master", "dist_mm", "quality_percent", "raw_mm", "ok", "fail"]
    n = 0
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for _rk, r in sorted(summary.get("rounds", {}).items()):
            for line in r.get("sw_lines") or []:
                master, triples = parse_sw_line(line)
                if master is None:
                    continue
                for other, dist_mm, quality in triples:
                    ok = 1 if (dist_mm > 0 and quality > 0) else 0
                    if not ok:
                        continue
                    w.writerow({
                        "a": master, "b": other, "master": master,
                        "dist_mm": dist_mm, "quality_percent": quality,
                        "raw_mm": dist_mm, "ok": ok, "fail": 0,
                    })
                    n += 1
    return n


if __name__ == "__main__":
    summ = Path(sys.argv[1])
    out = Path(sys.argv[2])
    n = extract(summ, out)
    print(f"[build_pairs] {n} rows -> {out}")
