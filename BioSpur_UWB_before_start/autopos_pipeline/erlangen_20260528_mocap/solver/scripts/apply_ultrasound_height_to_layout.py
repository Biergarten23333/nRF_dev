#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any


REPO = Path(__file__).resolve().parents[4]
FIELD_ROOT = REPO / "autopos_pipeline" / "erlangen_20260528_mocap"
LOWER_ANCHORS = ("A", "B", "C", "D")
UPPER_ANCHORS = ("E", "F", "G", "H")


def newest(paths: list[Path]) -> Path | None:
    paths = [p for p in paths if p.exists()]
    if not paths:
        return None
    return max(paths, key=lambda p: p.stat().st_mtime)


def find_session_from_staged(staged: Path) -> Path | None:
    manifest = staged / "stage_manifest.json"
    if not manifest.exists():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception:
        return None
    session = data.get("session")
    if not session:
        return None
    p = Path(session)
    return p if p.exists() else None


def find_ultrasound_csv(session: Path, anchor: str) -> Path | None:
    anchor = anchor.upper()
    candidates = []
    candidates.extend(session.glob(f"us_*_{anchor}_*/ultrasound_{anchor}.csv"))
    candidates.extend(session.glob(f"**/ultrasound_{anchor}.csv"))
    return newest(sorted(set(candidates)))


def read_ultrasound_height(csv_path: Path) -> dict[str, Any]:
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(row)

    usable = []
    for row in rows:
        try:
            ant_center = float(row.get("median_ant_center_mm") or "")
            median = float(row.get("median_mm") or "")
            offset = float(row.get("ant_center_offset_mm") or 0.0)
        except ValueError:
            continue
        if ant_center > 0:
            usable.append((row, median, offset, ant_center))
    if not usable:
        raise SystemExit(f"no usable median_ant_center_mm in {csv_path}")

    done = [item for item in usable if (item[0].get("state") or "").upper() == "DONE"]
    row, median, offset, ant_center = (done[-1] if done else usable[-1])
    return {
        "source_csv": str(csv_path.resolve()),
        "timestamp": row.get("timestamp") or "",
        "state": row.get("state") or "",
        "median_mm": median,
        "ant_center_offset_mm": offset,
        "height_ant_center_mm": ant_center,
        "raw": row.get("raw") or "",
    }


def anchor_entries(layout: dict[str, Any]) -> list[dict[str, Any]]:
    anchors = layout.get("anchors")
    if not isinstance(anchors, list):
        raise SystemExit("layout anchors must be a list")
    return anchors


def choose_z_sign(anchors: list[dict[str, Any]]) -> tuple[float, dict[str, float]]:
    by_label = {(a.get("label") or "").upper(): float(a["z_mm"]) for a in anchors}
    missing = [label for label in LOWER_ANCHORS + UPPER_ANCHORS if label not in by_label]
    if missing:
        raise SystemExit(f"layout missing anchors for z convention check: {','.join(missing)}")

    lower_mean_raw = sum(by_label[label] for label in LOWER_ANCHORS) / len(LOWER_ANCHORS)
    upper_mean_raw = sum(by_label[label] for label in UPPER_ANCHORS) / len(UPPER_ANCHORS)
    # Final physical coordinates must obey lower plane below upper plane:
    # mean_z(ABCD) < mean_z(EFGH). Flip the solver gauge only if needed.
    z_sign = 1.0 if lower_mean_raw < upper_mean_raw else -1.0
    return z_sign, {
        "lower_mean_raw_z_mm": lower_mean_raw,
        "upper_mean_raw_z_mm": upper_mean_raw,
    }


def apply_height(layout_path: Path, ultrasound_csv: Path, out_path: Path, anchor: str) -> dict[str, Any]:
    layout = json.loads(layout_path.read_text(encoding="utf-8"))
    us = read_ultrasound_height(ultrasound_csv)
    anchors = anchor_entries(layout)
    z_sign, convention = choose_z_sign(anchors)
    target = next((a for a in anchors if (a.get("label") or "").upper() == anchor.upper()), None)
    if target is None:
        raise SystemExit(f"anchor {anchor} not found in {layout_path}")

    raw_target_z = float(target["z_mm"])
    target_height = float(us["height_ant_center_mm"])
    z_shift = target_height - z_sign * raw_target_z

    corrected = json.loads(json.dumps(layout))
    for a in anchor_entries(corrected):
        raw_z = float(a["z_mm"])
        a["z_mm_raw_autopos"] = raw_z
        a["z_mm"] = z_sign * raw_z + z_shift

    corrected.setdefault("extra", {})
    corrected["extra"]["ultrasound_height_alignment"] = {
        "anchor": anchor.upper(),
        "z_model": "z_corrected_mm = z_sign * z_raw_autopos_mm + z_shift_mm",
        "z_convention": "mean_z(ABCD) < mean_z(EFGH)",
        "z_sign": z_sign,
        "z_shift_mm": z_shift,
        **convention,
        "raw_anchor_z_mm": raw_target_z,
        "target_anchor_height_mm": target_height,
        "ultrasound": us,
        "note": "Post-process coordinate-frame alignment only. Inter-anchor solve residuals are unchanged.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(corrected, indent=2) + "\n", encoding="utf-8")
    return corrected["extra"]["ultrasound_height_alignment"]


def main() -> int:
    ap = argparse.ArgumentParser(description="Post-process a solver layout into z-up physical height using Anchor H ultrasound.")
    ap.add_argument("--layout", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--staged", default=str(FIELD_ROOT / "solver" / "work" / "field_dataset_staged"))
    ap.add_argument("--session", default=None)
    ap.add_argument("--ultrasound-csv", default=None)
    ap.add_argument("--anchor", default="H")
    args = ap.parse_args()

    layout = Path(args.layout)
    out = Path(args.out) if args.out else layout.with_name("layout_us_height.json")
    staged = Path(args.staged)
    if not staged.is_absolute():
        staged = FIELD_ROOT / "solver" / "work" / staged

    session = Path(args.session) if args.session else find_session_from_staged(staged)
    us_csv = Path(args.ultrasound_csv) if args.ultrasound_csv else None
    if us_csv is None:
        if session is None:
            raise SystemExit("cannot infer session; pass --session or --ultrasound-csv")
        us_csv = find_ultrasound_csv(session, args.anchor)
    if us_csv is None or not us_csv.exists():
        raise SystemExit(f"ultrasound csv not found for anchor {args.anchor}")

    meta = apply_height(layout, us_csv, out, args.anchor)
    print(f"[ok] wrote {out}")
    print(json.dumps(meta, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
