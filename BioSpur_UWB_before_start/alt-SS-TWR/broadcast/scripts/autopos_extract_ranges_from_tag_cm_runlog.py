#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


CM_RE = re.compile(r"\bCM;[^\\s]+")


def iter_cm_entries(text: str) -> list[str]:
    """
    Extract CM entries from a log chunk. Each firmware notify line may contain
    multiple CM entries separated by '|'.
    """
    out: list[str] = []
    for line in text.splitlines():
        if " notify: CM;" not in line:
            continue
        payload = line.split("notify:", 1)[1].strip()
        # payload like: "CM;...|CM;...|..."
        for part in payload.split("|"):
            part = part.strip()
            if part.startswith("CM;"):
                out.append(part)
    return out


def parse_one(entry: str) -> dict[str, str] | None:
    # CM;1;seq;anchor;status;dist;raw;quality;...
    parts = entry.split(";")
    if len(parts) < 8:
        return None
    if parts[0] != "CM":
        return None
    try:
        anchor_id = int(parts[3])
    except Exception:
        return None
    status = parts[4].strip()
    try:
        dist_mm = int(float(parts[5]))
        raw_mm = int(float(parts[6]))
        q = int(float(parts[7]))
    except Exception:
        return None

    ok = 1 if status == "ok" else 0
    fail = 0 if status == "ok" else 1
    addr_hex = f"a{0x100 + anchor_id:03x}"  # matches existing logs (a100..a107)
    return {
        "anchor_id": str(anchor_id),
        "addr_hex": addr_hex,
        "raw_mm": str(raw_mm),
        "filt_mm": str(dist_mm),
        "ok": str(ok),
        "fail": str(fail),
        "quality_percent": str(q),
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Extract Tag CM samples from run.log and write a ranges.csv compatible with solve_anchor_layout.py"
    )
    ap.add_argument("--run-log", required=True, help="run.log containing 'notify: CM;' lines")
    ap.add_argument("--out-dir", required=True, help="Output session directory (will contain ranges.csv)")
    ap.add_argument("--min-cm-lines", type=int, default=100, help="Require at least this many aggregated CM lines")
    args = ap.parse_args()

    run_log = Path(args.run_log)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    text = run_log.read_text(encoding="utf-8", errors="ignore")
    entries = iter_cm_entries(text)
    # Aggregated CM lines count is number of notify lines, not per-entry.
    notify_lines = sum(1 for line in text.splitlines() if " notify: CM;" in line)
    if notify_lines < args.min_cm_lines:
        raise SystemExit(f"[error] only {notify_lines} CM notify lines, need >= {args.min_cm_lines}")

    rows: list[dict[str, str]] = []
    for e in entries:
        r = parse_one(e)
        if r and r["ok"] == "1" and int(r["filt_mm"]) > 0:
            rows.append(r)

    out_csv = out_dir / "ranges.csv"
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(
            f,
            fieldnames=["anchor_id", "addr_hex", "raw_mm", "filt_mm", "ok", "fail", "quality_percent"],
        )
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # Optional summary for floating-reference initial guess. We don't have XYZ here, so skip.
    print(f"[ok] wrote {out_csv} rows={len(rows)} cm_notify_lines={notify_lines}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
