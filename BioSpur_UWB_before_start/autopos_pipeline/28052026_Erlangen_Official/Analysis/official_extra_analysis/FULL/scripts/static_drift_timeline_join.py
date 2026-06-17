#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np
import pandas as pd

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
OFFICIAL_ROOT = THIS.parents[4]
CAPTURES_ROOT = OFFICIAL_ROOT / "captures/erlangen_20260528_optitrack"


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="Join static drift rows to session_notes timestamps.")
    parser.add_argument("--out-dir", type=Path, default=FULL_ROOT)
    args = parser.parse_args()
    out_dir = args.out_dir.resolve()
    tables = out_dir / "tables"

    drift_path = tables / "temporal_drift_per_anchor_session.csv"
    notes_path = CAPTURES_ROOT / "session_notes.csv"
    drift = pd.read_csv(drift_path)
    notes = pd.read_csv(notes_path)
    static_notes = notes[notes["type"].astype(str).str.strip('"') == "static"].copy()
    static_notes["session_id"] = static_notes["id"].astype(str).str.strip('"')
    static_notes["timestamp"] = pd.to_datetime(static_notes["timestamp"], errors="coerce")
    first_ts = static_notes["timestamp"].min()
    static_notes["elapsed_min_from_first_static"] = (static_notes["timestamp"] - first_ts).dt.total_seconds() / 60.0
    keep = static_notes[["session_id", "timestamp", "elapsed_min_from_first_static", "path", "notes"]].rename(
        columns={"path": "session_notes_path", "notes": "session_notes"}
    )
    joined = drift.merge(keep, on="session_id", how="left")
    joined = joined.sort_values(["timestamp", "anchor_id"]).reset_index(drop=True)

    summary_rows: list[dict[str, Any]] = []
    for anchor, g in joined.groupby("anchor", dropna=False):
        slopes = g["slope_mm_per_min"].to_numpy(dtype=float)
        drifts = g["drift_over_capture_mm"].to_numpy(dtype=float)
        summary_rows.append(
            {
                "anchor": anchor,
                "sessions": int(len(g)),
                "first_timestamp": str(g["timestamp"].min()),
                "last_timestamp": str(g["timestamp"].max()),
                "elapsed_h": float((g["timestamp"].max() - g["timestamp"].min()).total_seconds() / 3600.0),
                "median_slope_mm_per_min": float(np.nanmedian(slopes)),
                "median_abs_slope_mm_per_min": float(np.nanmedian(np.abs(slopes))),
                "p95_abs_slope_mm_per_min": float(np.nanpercentile(np.abs(slopes), 95)),
                "median_abs_drift_over_capture_mm": float(np.nanmedian(np.abs(drifts))),
                "p95_abs_drift_over_capture_mm": float(np.nanpercentile(np.abs(drifts), 95)),
                "worst_session": str(g.iloc[int(np.nanargmax(np.abs(slopes)))]["session_id"]),
                "worst_slope_mm_per_min": float(slopes[int(np.nanargmax(np.abs(slopes)))]),
            }
        )

    all_abs_drift = np.abs(joined["drift_over_capture_mm"].to_numpy(dtype=float))
    p95_drift = float(np.nanpercentile(all_abs_drift, 95))
    median_drift = float(np.nanmedian(all_abs_drift))
    session_span_h = float((static_notes["timestamp"].max() - static_notes["timestamp"].min()).total_seconds() / 3600.0)
    dtag_verdict = "mostly_justified_with_FG_caution" if p95_drift <= 50.0 else "not_justified_without_time_model"
    overall = {
        "script": str(THIS),
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "drift_source": str(drift_path),
        "session_notes": str(notes_path),
        "static_session_span_h": session_span_h,
        "joined_rows": int(len(joined)),
        "median_abs_drift_over_capture_mm": median_drift,
        "p95_abs_drift_over_capture_mm": p95_drift,
        "single_constant_dtag_verdict": dtag_verdict,
        "verdict_note": "Within-capture drift is generally smaller than the tag-delay scale, but high-p95 F/G links should be flagged when using one scalar D_tag over the whole static timeline.",
    }
    joined.to_csv(tables / "static_drift_timeline.csv", index=False)
    write_csv(tables / "static_drift_timeline_summary.csv", summary_rows)
    write_csv(tables / "static_drift_timeline_overall.csv", [overall])
    print(json.dumps({"status": "ok", "overall": overall}, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
