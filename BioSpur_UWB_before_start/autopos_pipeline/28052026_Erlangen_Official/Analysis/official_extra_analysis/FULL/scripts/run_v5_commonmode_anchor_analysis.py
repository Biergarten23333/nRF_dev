#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import sys
import time
from dataclasses import dataclass
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
EVAL_SCRIPT = REPO_ROOT / "autopos_pipeline/outdoor_20260513/analysis_20260513_182053/run_full_evaluation_same_pipeline_20260513.py"
STAGED_ROOT = OFFICIAL_ROOT / "solver/work/field_dataset_staged"
LAYOUT_BASE = OFFICIAL_ROOT / "solver/outputs/v1_to_v4_io_field_check"
V5_LAYOUT = LAYOUT_BASE / "v5-commonmode/layout.json"
ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]

sys.path.insert(0, str(THIS.parent))
from tag_ground_truth import load_corrected_static_truth  # noqa: E402


@dataclass(frozen=True)
class Fit:
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float
    aligned: np.ndarray


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


def dump_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection: bool, allow_scale: bool) -> Fit:
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, svals, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    rotation = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(svals * d) / denom) if denom > 0.0 else 1.0
    translation = dst_c - scale * src_c @ rotation
    aligned = scale * src @ rotation + translation
    return Fit(rotation=rotation, translation=translation, scale=scale, det=float(np.linalg.det(rotation)), aligned=aligned)


def rmse(values: np.ndarray) -> float:
    arr = np.asarray(values, dtype=float)
    return float(math.sqrt(np.nanmean(arr * arr))) if arr.size else float("nan")


def layout_metrics(fc, mod, layout, pair_dists: dict, anchor_truth: np.ndarray, anchor_ids: list[int]) -> dict[str, Any]:
    src = np.asarray(layout.x, dtype=float)
    rigid = fit_similarity(src, anchor_truth, allow_reflection=True, allow_scale=False)
    sim3 = fit_similarity(src, anchor_truth, allow_reflection=True, allow_scale=True)
    rigid_err = np.linalg.norm(rigid.aligned - anchor_truth, axis=1)
    pair_rows = fc.inter_residual_rows(mod, layout, pair_dists, anchor_ids, "solve")
    pair_res = np.asarray([float(r["residual_mm"]) for r in pair_rows], dtype=float)
    return {
        "version": layout.version,
        "label": layout.label,
        "sim3_scale_autopos_to_vicon": float(sim3.scale),
        "rigid_anchor_rmse_mm": rmse(rigid_err),
        "rigid_anchor_median_mm": float(np.nanmedian(rigid_err)),
        "rigid_anchor_p95_mm": float(np.nanpercentile(rigid_err, 95)),
        "pair_residual_rms_mm": rmse(pair_res),
        "pair_residual_p95_abs_mm": float(np.nanpercentile(np.abs(pair_res), 95)),
    }


def solve_common(fc, mod, fused_v3: dict, anchor_ids: list[int], scale: float):
    init, _ = mod.solve_autopos_v1(fused_v3, anchor_ids)
    x, dly, res = mod.solve_v4_common_mode(fused_v3, anchor_ids, init, e_reg_scale_mm=float(scale))
    extra = {
        "success": bool(getattr(res, "success", False)),
        "based_on": "v4-io",
        "e_reg_scale_mm": float(scale),
        "delay_parameterization": "d_i = c + e_i for all anchors; no d_A=0 gauge",
        "common_mode_mm": float(getattr(res, "common_mode_mm", float("nan"))),
        "differential_delay_mm": [float(v) for v in np.asarray(getattr(res, "differential_delay_mm", []), dtype=float).tolist()],
        "mean_e_mm": float(getattr(res, "mean_e_mm", float("nan"))),
        "max_abs_e_mm": float(getattr(res, "max_abs_e_mm", float("nan"))),
        "pair_rmse_mm": float(getattr(res, "pair_rmse_mm", float("nan"))),
    }
    physical = getattr(res, "physical_diagnostics", None)
    if physical:
        extra.update(physical)
    version = f"v5-commonmode-e{int(scale)}"
    return fc.Layout(version, f"V5 common-mode e{int(scale)}", x, dly, extra, 0.0), res


def jackknife_common(mod, fused_v3: dict, anchor_ids: list[int], full_layout, full_res, scale: float) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    full_c = float(getattr(full_res, "common_mode_mm", float("nan")))
    full_e = np.asarray(getattr(full_res, "differential_delay_mm", []), dtype=float)
    for omit in sorted(fused_v3):
        reduced = {k: v for k, v in fused_v3.items() if k != omit}
        pair = f"{ANCHORS[int(omit[0])]}-{ANCHORS[int(omit[1])]}"
        t0 = time.perf_counter()
        try:
            _x, _d, res = mod.solve_v4_common_mode(
                reduced,
                anchor_ids,
                np.asarray(full_layout.x, dtype=float),
                c_init=full_c,
                e_init=full_e,
                e_reg_scale_mm=float(scale),
                max_nfev=4000,
            )
            e = np.asarray(getattr(res, "differential_delay_mm", []), dtype=float)
            delta = e - full_e if e.shape == full_e.shape else np.full_like(full_e, np.nan)
            rows.append(
                {
                    "e_reg_scale_mm": float(scale),
                    "omitted_pair": pair,
                    "status": "ok",
                    "wall_s": time.perf_counter() - t0,
                    "common_mode_mm": float(getattr(res, "common_mode_mm", float("nan"))),
                    "common_mode_delta_mm": float(getattr(res, "common_mode_mm", float("nan")) - full_c),
                    "e_delta_rmse_mm": rmse(delta),
                    "e_delta_max_abs_mm": float(np.nanmax(np.abs(delta))) if delta.size else float("nan"),
                    "pair_rmse_mm": float(getattr(res, "pair_rmse_mm", float("nan"))),
                }
            )
        except Exception as exc:
            rows.append(
                {
                    "e_reg_scale_mm": float(scale),
                    "omitted_pair": pair,
                    "status": "error",
                    "wall_s": time.perf_counter() - t0,
                    "error": repr(exc),
                }
            )
    ok = [r for r in rows if r.get("status") == "ok"]
    if not ok:
        return rows, {"e_reg_scale_mm": float(scale), "jackknife_ok": 0, "stable": False}
    c_abs = np.asarray([abs(float(r["common_mode_delta_mm"])) for r in ok], dtype=float)
    e_rmse = np.asarray([float(r["e_delta_rmse_mm"]) for r in ok], dtype=float)
    e_max = np.asarray([float(r["e_delta_max_abs_mm"]) for r in ok], dtype=float)
    summary = {
        "e_reg_scale_mm": float(scale),
        "jackknife_ok": int(len(ok)),
        "jackknife_errors": int(len(rows) - len(ok)),
        "c_delta_p95_abs_mm": float(np.nanpercentile(c_abs, 95)),
        "c_delta_max_abs_mm": float(np.nanmax(c_abs)),
        "e_delta_rmse_p95_mm": float(np.nanpercentile(e_rmse, 95)),
        "e_delta_max_abs_p95_mm": float(np.nanpercentile(e_max, 95)),
        "e_delta_max_abs_max_mm": float(np.nanmax(e_max)),
    }
    summary["stable"] = bool(
        summary["jackknife_ok"] == len(fused_v3)
        and summary["c_delta_p95_abs_mm"] <= 10.0
        and summary["e_delta_rmse_p95_mm"] <= 10.0
        and summary["e_delta_max_abs_p95_mm"] <= 20.0
    )
    return rows, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Solve and audit V5 common-mode e-regularizer candidates.")
    parser.add_argument("--out-dir", type=Path, default=FULL_ROOT / f"v5_commonmode_anchor_analysis_{datetime.now().strftime('%Y%m%dT%H%M%S')}")
    parser.add_argument("--replace-v5-layout", action="store_true")
    args = parser.parse_args()

    out_dir = args.out_dir.resolve()
    if out_dir.exists():
        raise SystemExit(f"refusing to overwrite existing output dir: {out_dir}")
    if V5_LAYOUT.exists() and not args.replace_v5_layout:
        raise SystemExit(f"refusing to overwrite existing V5 layout without --replace-v5-layout: {V5_LAYOUT}")
    out_dir.mkdir(parents=True)
    tables = out_dir / "tables"
    layouts_dir = out_dir / "layouts"

    fc = load_module(RUN_CLEAN, "v5_commonmode_full_compare_helpers")
    fc.DATA = STAGED_ROOT
    fc.SWEEP_CSV = STAGED_ROOT / "sweep1000/pairs_all.csv"
    fc.EVAL = EVAL_SCRIPT
    mod = fc.load_eval_module()

    raw = fc.load_sweep_grouped()
    raw_solve = fc.slice_raw(raw, "all")
    anchor_ids = list(range(8))
    fused = fc.fuse_all(mod, raw_solve, anchor_ids)
    fused_v3 = fused["v3"]

    anchor_truth_dict, _tag_truth, _tag_meta, _corr = load_corrected_static_truth(
        OFFICIAL_ROOT / "opti_captures/full",
        ANCHORS,
        PRIMARY_IDS,
    )
    anchor_truth = np.vstack([anchor_truth_dict[a] for a in ANCHORS])

    v4_layout = fc.solve_version(mod, "v4-io", fused, anchor_ids)
    common: dict[float, Any] = {}
    common_res: dict[float, Any] = {}
    for scale in (20.0, 50.0):
        layout, res = solve_common(fc, mod, fused_v3, anchor_ids, scale)
        common[scale] = layout
        common_res[scale] = res
        fc.save_layout(layouts_dir / layout.version / "layout.json", layout, anchor_ids)

    summary_rows: list[dict[str, Any]] = []
    v4_metrics = layout_metrics(fc, mod, v4_layout, fused_v3, anchor_truth, anchor_ids)
    v4_metrics.update({"e_reg_scale_mm": "", "common_mode_mm": "", "mean_e_mm": "", "max_abs_e_mm": ""})
    summary_rows.append(v4_metrics)
    anchor_rows: list[dict[str, Any]] = []
    for scale, layout in common.items():
        metrics = layout_metrics(fc, mod, layout, fused_v3, anchor_truth, anchor_ids)
        metrics.update(
            {
                "e_reg_scale_mm": scale,
                "common_mode_mm": layout.extra["common_mode_mm"],
                "mean_e_mm": layout.extra["mean_e_mm"],
                "max_abs_e_mm": layout.extra["max_abs_e_mm"],
            }
        )
        summary_rows.append(metrics)
        e = np.asarray(layout.extra["differential_delay_mm"], dtype=float)
        for idx, anchor in enumerate(ANCHORS):
            anchor_rows.append(
                {
                    **metrics,
                    "anchor": anchor,
                    "e_i_mm": float(e[idx]),
                    "d_anchor_mm": float(layout.dly[idx]),
                }
            )

    jk_rows: list[dict[str, Any]] = []
    jk_summary_rows: list[dict[str, Any]] = []
    for scale in (20.0, 50.0):
        rows, summary = jackknife_common(mod, fused_v3, anchor_ids, common[scale], common_res[scale], scale)
        jk_rows.extend(rows)
        jk_summary_rows.append(summary)

    e20 = next(r for r in summary_rows if r.get("e_reg_scale_mm") == 20.0)
    e50 = next(r for r in summary_rows if r.get("e_reg_scale_mm") == 50.0)
    jk50 = next(r for r in jk_summary_rows if r.get("e_reg_scale_mm") == 50.0)
    rmse_improves = float(e50["rigid_anchor_rmse_mm"]) < float(e20["rigid_anchor_rmse_mm"])
    residual_not_worse = float(e50["pair_residual_rms_mm"]) <= float(e20["pair_residual_rms_mm"]) + 1e-9
    keep_looser = bool(rmse_improves and residual_not_worse and jk50.get("stable"))
    selected_scale = 50.0 if keep_looser else 20.0
    selected = common[selected_scale]
    selected_layout = fc.Layout(
        "v5-commonmode",
        "V5 common-mode",
        np.asarray(selected.x, dtype=float),
        np.asarray(selected.dly, dtype=float),
        {
            **selected.extra,
            "selected_from_e_reg_scale_mm": selected_scale,
            "selection_verdict": "looser_50mm_kept" if keep_looser else "looser_50mm_not_supported",
        },
        0.0,
    )
    fc.save_layout(V5_LAYOUT, selected_layout, anchor_ids)
    fc.save_layout(layouts_dir / "v5-commonmode-selected" / "layout.json", selected_layout, anchor_ids)

    selection = {
        "selected_scale_mm": selected_scale,
        "keep_looser_50mm": keep_looser,
        "rmse_improves_50_vs_20": rmse_improves,
        "pair_residual_not_worse_50_vs_20": residual_not_worse,
        "jackknife_50_stable": bool(jk50.get("stable")),
        "verdict": "keep_looser_50mm" if keep_looser else "data_do_not_support_looser_50mm",
        "selected_layout": str(V5_LAYOUT),
        "output_dir": str(out_dir),
    }

    write_csv(tables / "v5_commonmode_anchor_summary.csv", summary_rows)
    write_csv(tables / "v4_vs_v5_anchor_table.csv", anchor_rows)
    write_csv(tables / "v5_commonmode_jackknife.csv", jk_rows)
    write_csv(tables / "v5_commonmode_jackknife_summary.csv", jk_summary_rows)
    dump_json(tables / "v5_commonmode_selection.json", selection)

    print(
        json.dumps(
            {
                "status": "ok",
                "selected": selection,
                "summary": summary_rows,
                "jackknife_summary": jk_summary_rows,
            },
            indent=2,
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
