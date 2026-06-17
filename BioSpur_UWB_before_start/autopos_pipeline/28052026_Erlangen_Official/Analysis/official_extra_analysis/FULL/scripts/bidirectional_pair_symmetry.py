#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

for _name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_name, "1")

import numpy as np

THIS = Path(__file__).resolve()
FULL_ROOT = THIS.parents[1]
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
RUN_CLEAN = (
    REPO_ROOT
    / "biospur_tag_positioning_offline_solver"
    / "reference_current_implementations"
    / "official_report_field_solver_13052026"
    / "run_clean_full_compare.py"
)
STAGED_ROOT = OFFICIAL_ROOT / "solver/work/field_dataset_staged"
ANCHORS = list("ABCDEFGH")


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def finite_summary(values: list[float]) -> dict[str, float]:
    arr = np.asarray([v for v in values if np.isfinite(v)], dtype=float)
    if arr.size == 0:
        return {"n": 0}
    return {
        "n": int(arr.size),
        "median_abs_mm": float(np.nanmedian(np.abs(arr))),
        "p95_abs_mm": float(np.nanpercentile(np.abs(arr), 95)),
        "max_abs_mm": float(np.nanmax(np.abs(arr))),
        "rms_mm": float(math.sqrt(np.nanmean(arr * arr))),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Bidirectional inter-anchor symmetric/antisymmetric decomposition.")
    parser.add_argument("--out-dir", type=Path, default=FULL_ROOT)
    args = parser.parse_args()
    out_dir = args.out_dir.resolve()
    tables = out_dir / "tables"

    fc = load_module(RUN_CLEAN, "bidirectional_full_compare_helpers")
    fc.DATA = STAGED_ROOT
    fc.SWEEP_CSV = STAGED_ROOT / "sweep1000/pairs_all.csv"
    raw = fc.load_sweep_grouped()

    rows: list[dict[str, Any]] = []
    for i in range(8):
        for j in range(i + 1, 8):
            ab = np.asarray(raw.get((i, j), []), dtype=float)
            ba = np.asarray(raw.get((j, i), []), dtype=float)
            med_ab = float(np.nanmedian(ab)) if ab.size else float("nan")
            med_ba = float(np.nanmedian(ba)) if ba.size else float("nan")
            sym = 0.5 * (med_ab + med_ba) if np.isfinite(med_ab) and np.isfinite(med_ba) else float("nan")
            anti = 0.5 * (med_ab - med_ba) if np.isfinite(med_ab) and np.isfinite(med_ba) else float("nan")
            rows.append(
                {
                    "pair": f"{ANCHORS[i]}-{ANCHORS[j]}",
                    "a": ANCHORS[i],
                    "b": ANCHORS[j],
                    "n_ab": int(ab.size),
                    "n_ba": int(ba.size),
                    "median_ab_mm": med_ab,
                    "median_ba_mm": med_ba,
                    "sym_median_mm": sym,
                    "anti_signed_mm": anti,
                    "anti_abs_mm": abs(anti) if np.isfinite(anti) else float("nan"),
                    "half_difference_definition": "0.5*(median_a_to_b - median_b_to_a)",
                    "half_sum_definition": "0.5*(median_a_to_b + median_b_to_a)",
                }
            )
    summary = finite_summary([float(r["anti_signed_mm"]) for r in rows])
    summary.update(
        {
            "script": str(THIS),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "source": str(fc.SWEEP_CSV),
            "interpretation": "Small antisymmetric half-differences are consistent with clean bidirectional/CFO handling.",
        }
    )
    write_csv(tables / "pair_bidirectional_symmetry.csv", rows)
    write_csv(tables / "pair_bidirectional_symmetry_summary.csv", [summary])
    print(json.dumps({"status": "ok", "summary": summary}, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
