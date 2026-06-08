#!/usr/bin/env python3
"""Extract calibration-mode CM range rows from a receiver raw.log.

The static/calibration tag profile emits CM notifications rather than TR rows.
This helper converts those raw notifications into <tag>/cm.csv so the existing
TDMA session analyzer can solve positions from the calibration ranges.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path


TAG_NOTIFY_RE = re.compile(r"\[RECV\]\s+(?P<tag>BS[0-9A-F]{4})\s+notify:\s+(?P<body>.*)")
CM_RE = re.compile(
    r"CM;"
    r"(?P<version>\d+);"
    r"(?P<sweep>\d+);"
    r"(?P<anchor_id>\d+);"
    r"(?P<status>[^;|]+);"
    r"(?P<raw_mm>-?\d+);"
    r"(?P<filt_mm>-?\d+);"
    r"(?P<quality_percent>\d+)"
    r"(?:;(?P<trailer>[^|]*))?"
)


FIELDS = [
    "tag",
    "version",
    "sweep",
    "anchor_id",
    "status",
    "raw_mm",
    "filt_mm",
    "quality_percent",
    "valid",
    "source_line",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw-log", required=True, type=Path)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--stop-at-cleanup",
        action="store_true",
        help="Stop when host cleanup switches the tag back to AOTA.",
    )
    return parser.parse_args()


def iter_cm_rows(raw_log: Path, stop_at_cleanup: bool) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with raw_log.open(encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            if stop_at_cleanup and "MODE_OK MODE=AOTA" in line:
                break
            m = TAG_NOTIFY_RE.search(line)
            if not m:
                continue
            tag = m.group("tag")
            for fragment in m.group("body").strip().split("|"):
                fragment = fragment.strip()
                cm = CM_RE.fullmatch(fragment)
                if not cm:
                    continue
                status = cm.group("status").strip()
                filt_mm = int(cm.group("filt_mm"))
                quality = int(cm.group("quality_percent"))
                valid = int(status.lower() == "ok" and filt_mm > 0 and quality > 0)
                rows.append(
                    {
                        "tag": tag,
                        "version": int(cm.group("version")),
                        "sweep": int(cm.group("sweep")),
                        "anchor_id": int(cm.group("anchor_id")),
                        "status": status,
                        "raw_mm": int(cm.group("raw_mm")),
                        "filt_mm": filt_mm,
                        "quality_percent": quality,
                        "valid": valid,
                        "source_line": lineno,
                    }
                )
    return rows


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    args = parse_args()
    rows = iter_cm_rows(args.raw_log, args.stop_at_cleanup)
    by_tag: dict[str, list[dict[str, object]]] = {}
    for row in rows:
        by_tag.setdefault(str(row["tag"]), []).append(row)
    for tag, tag_rows in sorted(by_tag.items()):
        write_csv(args.out_dir / tag / "cm.csv", tag_rows)
    write_csv(args.out_dir / "cm_all.csv", rows)
    print(f"cm_rows={len(rows)} tags={','.join(sorted(by_tag)) if by_tag else '-'}")
    for tag, tag_rows in sorted(by_tag.items()):
        valid = sum(1 for row in tag_rows if row["valid"])
        anchors = sorted({int(row["anchor_id"]) for row in tag_rows if row["valid"]})
        sweeps = sorted({int(row["sweep"]) for row in tag_rows})
        print(
            f"{tag}: rows={len(tag_rows)} valid={valid} anchors={anchors} "
            f"sweeps={len(sweeps)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
