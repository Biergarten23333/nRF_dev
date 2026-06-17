#!/usr/bin/env python3
"""Phase 0 -- tag-positioning solver-headroom driver.

This harness is intentionally faithful at the solver boundary: every realization
is emitted as a synthetic ``tr_all.csv`` and solved through the production
``export_trajectory_t.py`` CLI.  The script does not reimplement the tag solver.

The synthetic layout uses the frozen OptiTrack anchor truth with zero anchor/tag
delay.  Therefore direct comparison of solved tag positions to the frozen tag
truth is equivalent to the production anchor-locked evaluation with the anchor
alignment equal to identity.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
PHASE_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPT_DIR.parents[5]
ANALYSIS_ROOT = REPO_ROOT / "autopos_pipeline/28052026_Erlangen_Official/Analysis"

TAG_EXPORT = REPO_ROOT / "biospur_tag_positioning_offline_solver/scripts/export_trajectory_t.py"
ANCHOR_TRUTH_JSON = PHASE_DIR / "data/erlangen_anchor_truth_all8_v4io.json"
ANCHOR_DELAY_JSON = PHASE_DIR / "data/erlangen_delay_distribution.json"
TAG_PARAMS_JSON = PHASE_DIR / "data/tag_phase0/erlangen_tag_empirical_injection_params.json"
ANCHOR_SIGMA_SOURCE = (
    REPO_ROOT
    / "autopos_pipeline/28052026_Erlangen_Official/solver/outputs/v1_to_v4_io_field_check/tables/anchor_sigma.json"
)

ANCHORS = tuple("ABCDEFGH")
ANCHOR_ID = {label: idx for idx, label in enumerate(ANCHORS)}
TAIL_RESIDUAL_THRESHOLD_MM = 150.0
LOW_RED_TAG = "ID19"
LOW_RED_ANCHOR = "G"
LOW_RED_BIAS_MM = 400.0

TR_ALL_COLUMNS = [
    "host_elapsed_s",
    "host_epoch_s",
    "sweep",
    "conn_id",
    "peer_name",
    "tag_id",
    "plan",
    "pmode",
    "anchor_id",
    "raw_mm",
    "range_mm",
    "quality_percent",
    "valid",
    "status",
    "quality_flag_percent",
    "first_to_last_us",
    "frame_us",
    "poll_count",
    "tr_version",
    "rx_mask",
    "air_us",
    "post_us",
    "cycle_us",
    "rx_seen",
    "imu_valid",
    "imu_n",
    "acc_norm_mean_mg",
    "acc_norm_std_mg",
    "acc_norm_min_mg",
    "acc_norm_max_mg",
    "imu_skip_count",
]


@dataclass(frozen=True)
class CaseSpec:
    key: str
    family: str
    deterministic: bool
    k: int | None = None


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    arr = sorted(float(x) for x in xs)
    k = (len(arr) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(arr[lo])
    return float(arr[lo] * (hi - k) + arr[hi] * (k - lo))


def rms(xs: list[float]) -> float:
    if not xs:
        return float("nan")
    return float(math.sqrt(sum(float(x) * float(x) for x in xs) / len(xs)))


def load_anchor_truth() -> dict[str, np.ndarray]:
    doc = json.loads(ANCHOR_TRUTH_JSON.read_text(encoding="utf-8"))
    return {
        label: np.array([v["x_mm"], v["y_mm"], v["z_mm"]], dtype=float)
        for label, v in doc["anchors"].items()
    }


def load_anchor_delays() -> dict[str, float]:
    doc = json.loads(ANCHOR_DELAY_JSON.read_text(encoding="utf-8"))
    return {
        label: float(v["delaycal_endpoint_delay_mm"])
        for label, v in doc["per_anchor"].items()
    }


def load_tag_params() -> dict[str, Any]:
    return json.loads(TAG_PARAMS_JSON.read_text(encoding="utf-8"))


def load_tag_truth(tag_params: dict[str, Any]) -> dict[str, np.ndarray]:
    per_position = tag_params["recon"]["tag_truth_source"]["per_position"]
    return {
        tag: np.array([v["x_mm"], v["y_mm"], v["z_mm"]], dtype=float)
        for tag, v in per_position.items()
    }


def load_residuals(tag_params: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for row in tag_params["delay_and_residual"]["primary_per_tag_anchor"]:
        tag = str(row["tag_position"]).upper()
        anchor = str(row["anchor"]).upper()
        out.setdefault(tag, {})[anchor] = float(row["residual_mm"])
    return out


def load_sigma_mm(tag_params: dict[str, Any]) -> float:
    return float(tag_params["sigma"]["summary"]["per_link_std_mm"]["median"])


def load_tag_delay_mm(tag_params: dict[str, Any]) -> float:
    return float(tag_params["delay_and_residual"]["fitted_tag_delay_mm"])


def load_real_headline(tag_params: dict[str, Any]) -> dict[str, float]:
    row = tag_params["recon"]["headline_v4io_t4_all8"]
    return {
        "median_3d_mm": float(row["err_3d_median_mm"]),
        "p95_3d_mm": float(row["err_3d_p95_mm"]),
        "rms_3d_mm": float(row["err_3d_rms_mm"]),
        "horizontal_median_mm": float(row["err_horizontal_median_mm"]),
        "vertical_median_mm": float(row["err_vertical_median_mm"]),
    }


def write_synthetic_layout(layout_path: Path, anchor_truth: dict[str, np.ndarray]) -> None:
    anchors = []
    for label in ANCHORS:
        xyz = anchor_truth[label]
        anchors.append(
            {
                "id": ANCHOR_ID[label],
                "label": label,
                "x_mm": float(xyz[0]),
                "y_mm": float(xyz[1]),
                "z_mm": float(xyz[2]),
                "d_anchor_mm": 0.0,
            }
        )
    payload = {
        "version": "phase0-tag-truth-zero-delay",
        "label": "Phase 0 tag positioning synthetic OptiTrack anchor truth",
        "tag_delay_mm": 0.0,
        "anchors": anchors,
        "extra": {
            "coordinate_convention": "Y is vertical; horizontal plane is X/Z.",
            "delay_convention": "All solver-side anchor/tag delays are zero; delay models are injected into range_mm.",
        },
    }
    layout_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_anchor_sigma(sigma_path: Path) -> dict[str, float]:
    if ANCHOR_SIGMA_SOURCE.exists():
        sigma = json.loads(ANCHOR_SIGMA_SOURCE.read_text(encoding="utf-8"))
    else:
        sigma = {label: 50.0 for label in ANCHORS}
    sigma_path.write_text(json.dumps(sigma, indent=2) + "\n", encoding="utf-8")
    return {str(k): float(v) for k, v in sigma.items()}


def production_invocation(layout_path: Path, tr_all_csv: Path, out_json: Path, sigma_path: Path) -> list[str]:
    return [
        sys.executable,
        str(TAG_EXPORT),
        "--layout",
        str(layout_path.resolve()),
        "--capture",
        str(tr_all_csv.resolve()),
        "--out",
        str(out_json.resolve()),
        "--method",
        "T4",
        "--anchor-sigma",
        str(sigma_path.resolve()),
    ]


def true_distance_mm(tag_xyz: np.ndarray, anchor_xyz: np.ndarray) -> float:
    return float(np.linalg.norm(tag_xyz - anchor_xyz))


def build_fixed_offsets(
    *,
    case: CaseSpec,
    rng: np.random.Generator,
    tag_truth: dict[str, np.ndarray],
    anchor_delays: dict[str, float],
    tag_delay_mm: float,
    residuals: dict[str, dict[str, float]],
) -> tuple[dict[str, dict[str, float]], dict[str, Any]]:
    offsets: dict[str, dict[str, float]] = {
        tag: {anchor: 0.0 for anchor in ANCHORS}
        for tag in tag_truth
    }
    meta: dict[str, Any] = {"case": case.key}

    if case.family == "E2":
        outliers: dict[str, dict[str, float]] = {}
        for tag in tag_truth:
            selected = rng.choice(list(ANCHORS), size=int(case.k or 0), replace=False)
            for anchor in selected:
                mag = float(rng.uniform(150.0, 400.0))
                sign = -1.0 if float(rng.random()) < 0.5 else 1.0
                delta = sign * mag
                offsets[tag][str(anchor)] += delta
                outliers.setdefault(tag, {})[str(anchor)] = delta
        meta["outlier_offsets_mm"] = outliers

    if case.family in {"E3", "COMBINED_REAL"}:
        for tag in tag_truth:
            for anchor in ANCHORS:
                offsets[tag][anchor] += anchor_delays[anchor] + tag_delay_mm

    if case.family in {"E4_REAL", "COMBINED_REAL"}:
        for tag in tag_truth:
            for anchor in ANCHORS:
                offsets[tag][anchor] += residuals[tag][anchor]

    if case.family == "E4_TAIL":
        severe: dict[str, dict[str, float]] = {}
        for tag in tag_truth:
            for anchor in ANCHORS:
                residual = residuals[tag][anchor]
                if abs(residual) > TAIL_RESIDUAL_THRESHOLD_MM:
                    offsets[tag][anchor] += residual
                    severe.setdefault(tag, {})[anchor] = residual
        meta["tail_threshold_mm"] = TAIL_RESIDUAL_THRESHOLD_MM
        meta["severe_tail_offsets_mm"] = severe
        meta["severe_tail_cell_count"] = sum(len(v) for v in severe.values())

    if case.family == "LOW_RED_SINGLE_BIAS":
        offsets[LOW_RED_TAG][LOW_RED_ANCHOR] += LOW_RED_BIAS_MM
        meta["single_bias"] = {
            "tag_position": LOW_RED_TAG,
            "anchor": LOW_RED_ANCHOR,
            "bias_mm": LOW_RED_BIAS_MM,
        }

    return offsets, meta


def write_synthetic_tr_all(
    out_csv: Path,
    *,
    case: CaseSpec,
    seed: int,
    samples_per_position: int,
    anchor_truth: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
    anchor_delays: dict[str, float],
    tag_delay_mm: float,
    residuals: dict[str, dict[str, float]],
    sigma_mm: float,
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    offsets, meta = build_fixed_offsets(
        case=case,
        rng=rng,
        tag_truth=tag_truth,
        anchor_delays=anchor_delays,
        tag_delay_mm=tag_delay_mm,
        residuals=residuals,
    )
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=TR_ALL_COLUMNS)
        writer.writeheader()
        row_index = 0
        for sweep in range(samples_per_position):
            for tag_index, tag in enumerate(sorted(tag_truth)):
                tag_xyz = tag_truth[tag]
                for anchor in ANCHORS:
                    anchor_xyz = anchor_truth[anchor]
                    d = true_distance_mm(tag_xyz, anchor_xyz) + offsets[tag][anchor]
                    if case.family in {"E1", "COMBINED_REAL"}:
                        d += float(rng.normal(0.0, sigma_mm))
                    d_int = int(round(max(1.0, d)))
                    elapsed = sweep * 0.05 + tag_index * 0.001 + ANCHOR_ID[anchor] * 0.00001
                    writer.writerow(
                        {
                            "host_elapsed_s": f"{elapsed:.6f}",
                            "host_epoch_s": f"{elapsed:.6f}",
                            "sweep": sweep,
                            "conn_id": 0,
                            "peer_name": tag,
                            "tag_id": tag,
                            "plan": "phase0",
                            "pmode": "synthetic",
                            "anchor_id": ANCHOR_ID[anchor],
                            "raw_mm": d_int,
                            "range_mm": d_int,
                            "quality_percent": 100,
                            "valid": 1,
                            "status": "O",
                            "quality_flag_percent": 100,
                            "first_to_last_us": 0,
                            "frame_us": 0,
                            "poll_count": row_index,
                            "tr_version": "phase0",
                            "rx_mask": 255,
                            "air_us": 0,
                            "post_us": 0,
                            "cycle_us": 0,
                            "rx_seen": 1,
                            "imu_valid": 0,
                            "imu_n": 0,
                            "acc_norm_mean_mg": "",
                            "acc_norm_std_mg": "",
                            "acc_norm_min_mg": "",
                            "acc_norm_max_mg": "",
                            "imu_skip_count": 0,
                        }
                    )
                    row_index += 1
    meta.update(
        {
            "seed": seed,
            "samples_per_position": samples_per_position,
            "rows": row_index,
            "fixed_offset_summary_mm": summarize_offsets(offsets),
        }
    )
    return meta


def summarize_offsets(offsets: dict[str, dict[str, float]]) -> dict[str, float]:
    values = [float(v) for by_anchor in offsets.values() for v in by_anchor.values()]
    abs_values = [abs(v) for v in values]
    nonzero = [abs(v) for v in values if abs(v) > 1e-9]
    return {
        "cell_count": len(values),
        "nonzero_cell_count": len(nonzero),
        "median_abs_all_cells": percentile(abs_values, 50),
        "p95_abs_all_cells": percentile(abs_values, 95),
        "max_abs_all_cells": max(abs_values) if abs_values else 0.0,
        "median_abs_nonzero_cells": percentile(nonzero, 50) if nonzero else 0.0,
        "p95_abs_nonzero_cells": percentile(nonzero, 95) if nonzero else 0.0,
    }


def parse_trajectory(out_json: Path) -> dict[str, list[np.ndarray]]:
    doc = json.loads(out_json.read_text(encoding="utf-8"))
    points: dict[str, list[np.ndarray]] = {}
    for row in doc.get("points", []):
        tag = str(row["tag"]).upper()
        points.setdefault(tag, []).append(
            np.array([float(row["x_mm"]), float(row["y_mm"]), float(row["z_mm"])], dtype=float)
        )
    return points


def mean_positions(points: dict[str, list[np.ndarray]]) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    for tag, rows in points.items():
        if rows:
            out[tag] = np.mean(np.vstack(rows), axis=0)
    return out


def evaluate_positions(
    solved: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
) -> dict[str, Any]:
    per_tag: dict[str, dict[str, float]] = {}
    total: list[float] = []
    horizontal: list[float] = []
    vertical: list[float] = []
    for tag in sorted(tag_truth):
        if tag not in solved:
            continue
        residual = solved[tag] - tag_truth[tag]
        e_total = float(np.linalg.norm(residual))
        e_horizontal = float(math.sqrt(residual[0] * residual[0] + residual[2] * residual[2]))
        e_vertical = float(abs(residual[1]))
        per_tag[tag] = {
            "dx_mm": float(residual[0]),
            "dy_vertical_mm": float(residual[1]),
            "dz_mm": float(residual[2]),
            "err_3d_mm": e_total,
            "err_horizontal_xz_mm": e_horizontal,
            "err_vertical_y_mm": e_vertical,
        }
        total.append(e_total)
        horizontal.append(e_horizontal)
        vertical.append(e_vertical)
    return {
        "n_positions": len(per_tag),
        "per_tag": per_tag,
        "metrics": {
            "total_median_mm": percentile(total, 50),
            "total_p95_mm": percentile(total, 95),
            "total_rms_mm": rms(total),
            "horizontal_median_mm": percentile(horizontal, 50),
            "horizontal_p95_mm": percentile(horizontal, 95),
            "horizontal_rms_mm": rms(horizontal),
            "vertical_median_mm": percentile(vertical, 50),
            "vertical_p95_mm": percentile(vertical, 95),
            "vertical_rms_mm": rms(vertical),
        },
    }


def run_one_realization(task: dict[str, Any]) -> dict[str, Any]:
    case = CaseSpec(**task["case"])
    work_dir = Path(task["work_dir"])
    keep_work = bool(task["keep_work"])
    layout_path = Path(task["layout_path"])
    sigma_path = Path(task["sigma_path"])
    anchor_truth = {k: np.array(v, dtype=float) for k, v in task["anchor_truth"].items()}
    tag_truth = {k: np.array(v, dtype=float) for k, v in task["tag_truth"].items()}
    anchor_delays = {str(k): float(v) for k, v in task["anchor_delays"].items()}
    residuals = {
        str(tag): {str(anchor): float(value) for anchor, value in by_anchor.items()}
        for tag, by_anchor in task["residuals"].items()
    }
    tag_delay_mm = float(task["tag_delay_mm"])
    sigma_mm = float(task["sigma_mm"])
    seed = int(task["seed"])
    samples_per_position = int(task["samples_per_position"])

    tr_all_csv = work_dir / "tr_all.csv"
    out_json = work_dir / "trajectory_t4.json"
    log_path = work_dir / "solver_stdout.log"
    work_dir.mkdir(parents=True, exist_ok=True)

    injection_meta = write_synthetic_tr_all(
        tr_all_csv,
        case=case,
        seed=seed,
        samples_per_position=samples_per_position,
        anchor_truth=anchor_truth,
        tag_truth=tag_truth,
        anchor_delays=anchor_delays,
        tag_delay_mm=tag_delay_mm,
        residuals=residuals,
        sigma_mm=sigma_mm,
    )
    cmd = production_invocation(layout_path, tr_all_csv, out_json, sigma_path)
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    elapsed_s = time.time() - t0
    log_path.write_text(proc.stdout or "", encoding="utf-8")
    if proc.returncode != 0:
        return {
            "case": case.key,
            "family": case.family,
            "rep_index": int(task["rep_index"]),
            "seed": seed,
            "success": False,
            "returncode": proc.returncode,
            "elapsed_s": elapsed_s,
            "work_dir": str(work_dir),
            "log_tail": (proc.stdout or "")[-4000:],
        }
    points = parse_trajectory(out_json)
    solved = mean_positions(points)
    eval_report = evaluate_positions(solved, tag_truth)
    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)
    return {
        "case": case.key,
        "family": case.family,
        "rep_index": int(task["rep_index"]),
        "seed": seed,
        "success": True,
        "elapsed_s": elapsed_s,
        "work_dir": str(work_dir),
        "injection": injection_meta,
        "frames_solved": sum(len(v) for v in points.values()),
        "metrics": eval_report["metrics"],
        "per_tag": eval_report["per_tag"],
    }


def aggregate(items: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [x for x in items if x.get("success")]
    failures = [x for x in items if not x.get("success")]
    metric_keys = sorted({k for x in successes for k in x["metrics"].keys()})
    metrics: dict[str, Any] = {}
    for key in metric_keys:
        xs = [float(x["metrics"][key]) for x in successes]
        metrics[key] = {
            "median": percentile(xs, 50),
            "p05": percentile(xs, 5),
            "p95": percentile(xs, 95),
        }
    return {
        "executed": len(items),
        "successes": len(successes),
        "failures": len(failures),
        "metrics": metrics,
        "elapsed_s_median": percentile([float(x.get("elapsed_s", 0.0)) for x in items], 50),
        "elapsed_s_total": float(sum(float(x.get("elapsed_s", 0.0)) for x in items)),
        "failure_samples": failures[:3],
    }


def random_case_specs() -> list[CaseSpec]:
    cases = [CaseSpec("E1", "E1", False)]
    for k in (1, 2, 3):
        cases.append(CaseSpec(f"E2_K{k}", "E2", False, k=k))
    cases.append(CaseSpec("COMBINED_REAL", "COMBINED_REAL", False))
    return cases


def deterministic_case_specs() -> list[CaseSpec]:
    return [
        CaseSpec("BASELINE", "BASELINE", True),
        CaseSpec("E3", "E3", True),
        CaseSpec("E4_REAL", "E4_REAL", True),
        CaseSpec("E4_TAIL", "E4_TAIL", True),
        CaseSpec("LOW_RED_SINGLE_BIAS", "LOW_RED_SINGLE_BIAS", True),
    ]


def make_task(
    *,
    case: CaseSpec,
    rep_index: int,
    seed: int,
    run_dir: Path,
    layout_path: Path,
    sigma_path: Path,
    anchor_truth: dict[str, np.ndarray],
    tag_truth: dict[str, np.ndarray],
    anchor_delays: dict[str, float],
    residuals: dict[str, dict[str, float]],
    tag_delay_mm: float,
    sigma_mm: float,
    samples_per_position: int,
    keep_work: bool,
) -> dict[str, Any]:
    return {
        "case": {"key": case.key, "family": case.family, "deterministic": case.deterministic, "k": case.k},
        "rep_index": rep_index,
        "seed": seed,
        "work_dir": str(run_dir / "work" / case.key / f"rep_{rep_index:04d}"),
        "layout_path": str(layout_path),
        "sigma_path": str(sigma_path),
        "anchor_truth": {k: v.tolist() for k, v in anchor_truth.items()},
        "tag_truth": {k: v.tolist() for k, v in tag_truth.items()},
        "anchor_delays": anchor_delays,
        "residuals": residuals,
        "tag_delay_mm": tag_delay_mm,
        "sigma_mm": sigma_mm,
        "samples_per_position": samples_per_position,
        "keep_work": keep_work,
    }


def metric(aggregates: dict[str, Any], case: str, key: str) -> float:
    return float(aggregates[case]["metrics"][key]["median"])


def build_readouts(
    *,
    aggregates: dict[str, Any],
    baseline_result: dict[str, Any],
    low_red_result: dict[str, Any],
    tag_params: dict[str, Any],
    residuals: dict[str, dict[str, float]],
    real_headline: dict[str, float],
) -> dict[str, Any]:
    baseline = baseline_result["metrics"]
    e3_total = metric(aggregates, "E3", "total_median_mm")
    e4_total = metric(aggregates, "E4_REAL", "total_median_mm")
    ratio = e3_total / e4_total if e4_total else float("inf")

    severe_values = [
        abs(value)
        for by_anchor in residuals.values()
        for value in by_anchor.values()
        if abs(value) > TAIL_RESIDUAL_THRESHOLD_MM
    ]
    e4_tail_total = metric(aggregates, "E4_TAIL", "total_median_mm")
    e4_tail_p95 = metric(aggregates, "E4_TAIL", "total_p95_mm")
    tail_injected_median = percentile(severe_values, 50)
    tail_injected_p95 = percentile(severe_values, 95)
    tail_verdict = (
        "bounded_by_solver"
        if e4_tail_p95 < 0.5 * tail_injected_p95
        else "position_pulled_by_tail_links"
    )

    low_base = baseline_result["per_tag"][LOW_RED_TAG]
    low_bias = low_red_result["per_tag"][LOW_RED_TAG]
    low_shift = math.sqrt(
        (low_bias["dx_mm"] - low_base["dx_mm"]) ** 2
        + (low_bias["dy_vertical_mm"] - low_base["dy_vertical_mm"]) ** 2
        + (low_bias["dz_mm"] - low_base["dz_mm"]) ** 2
    )
    combined = aggregates["COMBINED_REAL"]["metrics"]

    return {
        "baseline_sanity": {
            "median_3d_mm": baseline["total_median_mm"],
            "p95_3d_mm": baseline["total_p95_mm"],
            "passes_lt_5mm": baseline["total_median_mm"] < 5.0,
            "note": "Clean synthetic distances solved in the frozen anchor-truth frame; integer mm rounding can leave sub-mm to mm residuals.",
        },
        "e3_delay_cir_blind": {
            "median_3d_mm": e3_total,
            "p95_3d_mm": metric(aggregates, "E3", "total_p95_mm"),
            "horizontal_median_mm": metric(aggregates, "E3", "horizontal_median_mm"),
            "vertical_median_mm": metric(aggregates, "E3", "vertical_median_mm"),
            "anchor_delay_source": str(ANCHOR_DELAY_JSON),
            "tag_delay_mm": float(tag_params["delay_and_residual"]["fitted_tag_delay_mm"]),
        },
        "e4_real_cir_addressable": {
            "median_3d_mm": e4_total,
            "p95_3d_mm": metric(aggregates, "E4_REAL", "total_p95_mm"),
            "horizontal_median_mm": metric(aggregates, "E4_REAL", "horizontal_median_mm"),
            "vertical_median_mm": metric(aggregates, "E4_REAL", "vertical_median_mm"),
            "e3_over_e4_median_ratio": ratio,
        },
        "e4_tail_rejection": {
            "threshold_abs_residual_mm": TAIL_RESIDUAL_THRESHOLD_MM,
            "severe_tail_cell_count": len(severe_values),
            "injected_tail_median_abs_mm": tail_injected_median,
            "injected_tail_p95_abs_mm": tail_injected_p95,
            "position_median_3d_mm": e4_tail_total,
            "position_p95_3d_mm": e4_tail_p95,
            "verdict": tail_verdict,
        },
        "low_redundancy_single_bias": {
            "tag_position": LOW_RED_TAG,
            "anchor": LOW_RED_ANCHOR,
            "injected_bias_mm": LOW_RED_BIAS_MM,
            "position_shift_3d_mm": float(low_shift),
            "biased_position_error_3d_mm": low_bias["err_3d_mm"],
        },
        "combined_real_vs_headline": {
            "combined_real_median_3d_mm": combined["total_median_mm"]["median"],
            "combined_real_p95_3d_mm": combined["total_p95_mm"]["median"],
            "real_headline_median_3d_mm": real_headline["median_3d_mm"],
            "real_headline_p95_3d_mm": real_headline["p95_3d_mm"],
            "ratio_combined_median_to_real": combined["total_median_mm"]["median"] / real_headline["median_3d_mm"],
        },
        "solver_recon": {
            "production_cli": " ".join(
                production_invocation(
                    Path("<layout.json>"),
                    Path("<tr_all.csv>"),
                    Path("<trajectory_t4.json>"),
                    Path("<anchor_sigma.json>"),
                )
            ),
            "solver_delay_behavior": tag_params["recon"]["tag_delay_solver_behavior"],
            "evaluation_note": "The synthetic layout is the frozen OptiTrack anchor truth with zero delay, so direct solved-vs-truth position error is the identity anchor-locked evaluation.",
        },
    }


def write_markdown(
    out_path: Path,
    *,
    run_dir: Path,
    config: dict[str, Any],
    timing: dict[str, Any],
    readouts: dict[str, Any],
    aggregates: dict[str, Any],
) -> None:
    lines = [
        "# Phase 0 Tag Positioning Results",
        "",
        f"Run directory: `{run_dir}`",
        "",
        "## Configuration",
        "",
        f"- Random MC realizations per random case: `{config['n_random']}`",
        f"- Synthetic sweeps per static position: `{config['samples_per_position']}`",
        f"- Workers: `{config['workers']}`",
        f"- One production solver invocation timing: `{timing['baseline_elapsed_s']:.3f} s`",
        f"- Estimated MC wall time before run: `{timing['estimated_wall_clock_s']:.1f} s`",
        "",
        "## Required Readouts",
        "",
    ]
    r = readouts
    lines += [
        f"1. BASELINE sanity: median 3D `{r['baseline_sanity']['median_3d_mm']:.3f} mm`, P95 `{r['baseline_sanity']['p95_3d_mm']:.3f} mm`, pass_lt_5mm `{r['baseline_sanity']['passes_lt_5mm']}`.",
        f"2. E3 delay/CIR-blind contribution: median 3D `{r['e3_delay_cir_blind']['median_3d_mm']:.3f} mm`, P95 `{r['e3_delay_cir_blind']['p95_3d_mm']:.3f} mm`.",
        f"3. E4_REAL residual/CIR-addressable contribution: median 3D `{r['e4_real_cir_addressable']['median_3d_mm']:.3f} mm`, P95 `{r['e4_real_cir_addressable']['p95_3d_mm']:.3f} mm`; E3/E4 median ratio `{r['e4_real_cir_addressable']['e3_over_e4_median_ratio']:.3f}`.",
        f"4. E4_TAIL rejection: injected severe-tail median `{r['e4_tail_rejection']['injected_tail_median_abs_mm']:.3f} mm`, P95 `{r['e4_tail_rejection']['injected_tail_p95_abs_mm']:.3f} mm`; position median `{r['e4_tail_rejection']['position_median_3d_mm']:.3f} mm`, P95 `{r['e4_tail_rejection']['position_p95_3d_mm']:.3f} mm`; verdict `{r['e4_tail_rejection']['verdict']}`.",
        f"5. Low-redundancy single +400 mm bias on `{r['low_redundancy_single_bias']['tag_position']}-{r['low_redundancy_single_bias']['anchor']}`: position shift `{r['low_redundancy_single_bias']['position_shift_3d_mm']:.3f} mm`.",
        f"6. COMBINED_REAL vs real 72.7 mm headline: combined median `{r['combined_real_vs_headline']['combined_real_median_3d_mm']:.3f} mm`, real median `{r['combined_real_vs_headline']['real_headline_median_3d_mm']:.3f} mm`, ratio `{r['combined_real_vs_headline']['ratio_combined_median_to_real']:.3f}`.",
        "",
        "## Aggregate Case Summary",
        "",
        "| Case | n | median 3D mm | P95 3D mm | horizontal median mm | vertical median mm |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in sorted(aggregates):
        m = aggregates[case]["metrics"]
        lines.append(
            f"| {case} | {aggregates[case]['successes']} | "
            f"{m['total_median_mm']['median']:.3f} | "
            f"{m['total_p95_mm']['median']:.3f} | "
            f"{m['horizontal_median_mm']['median']:.3f} | "
            f"{m['vertical_median_mm']['median']:.3f} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run tag-positioning Phase 0 MC driver.")
    ap.add_argument("--n", type=int, default=200, help="Random MC realizations per random case")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--seed", type=int, default=20260615)
    ap.add_argument("--samples-per-position", type=int, default=200)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--keep-work", action="store_true", help="Keep synthetic tr_all.csv/trajectory files")
    args = ap.parse_args()

    tag_params = load_tag_params()
    anchor_truth = load_anchor_truth()
    anchor_delays = load_anchor_delays()
    tag_truth = load_tag_truth(tag_params)
    residuals = load_residuals(tag_params)
    sigma_mm = load_sigma_mm(tag_params)
    tag_delay_mm = load_tag_delay_mm(tag_params)
    real_headline = load_real_headline(tag_params)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.run_dir) if args.run_dir else PHASE_DIR / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    layout_path = run_dir / "phase0_tag_truth_zero_delay_layout.json"
    sigma_path = run_dir / "anchor_sigma.json"
    write_synthetic_layout(layout_path, anchor_truth)
    anchor_sigma = write_anchor_sigma(sigma_path)

    print("[tag-phase0] production invocation:")
    print(
        " ",
        " ".join(
            production_invocation(
                layout_path,
                run_dir / "work/<case>/rep_0000/tr_all.csv",
                run_dir / "work/<case>/rep_0000/trajectory_t4.json",
                sigma_path,
            )
        ),
    )
    print(f"[tag-phase0] positions={len(tag_truth)} anchors={len(anchor_truth)} sigma={sigma_mm:.3f}mm tag_delay={tag_delay_mm:.3f}mm")
    print(f"[tag-phase0] samples_per_position={args.samples_per_position} workers={args.workers} n_random={args.n}")
    print(f"[tag-phase0] anchor sigma source={ANCHOR_SIGMA_SOURCE if ANCHOR_SIGMA_SOURCE.exists() else 'default 50mm'} values={anchor_sigma}")

    rng = np.random.default_rng(int(args.seed))
    baseline_case = CaseSpec("BASELINE", "BASELINE", True)
    baseline_task = make_task(
        case=baseline_case,
        rep_index=0,
        seed=int(rng.integers(0, 2**31 - 1)),
        run_dir=run_dir,
        layout_path=layout_path,
        sigma_path=sigma_path,
        anchor_truth=anchor_truth,
        tag_truth=tag_truth,
        anchor_delays=anchor_delays,
        residuals=residuals,
        tag_delay_mm=tag_delay_mm,
        sigma_mm=sigma_mm,
        samples_per_position=int(args.samples_per_position),
        keep_work=bool(args.keep_work),
    )
    print("[tag-phase0] timing one production solver invocation with BASELINE...")
    baseline_result = run_one_realization(baseline_task)
    if not baseline_result.get("success"):
        fail_path = run_dir / "baseline_failure.json"
        fail_path.write_text(json.dumps(baseline_result, indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"baseline solver failed; see {fail_path}")
    baseline_elapsed = float(baseline_result["elapsed_s"])

    remaining_tasks: list[dict[str, Any]] = []
    for case in deterministic_case_specs():
        if case.key == "BASELINE":
            continue
        remaining_tasks.append(
            make_task(
                case=case,
                rep_index=0,
                seed=int(rng.integers(0, 2**31 - 1)),
                run_dir=run_dir,
                layout_path=layout_path,
                sigma_path=sigma_path,
                anchor_truth=anchor_truth,
                tag_truth=tag_truth,
                anchor_delays=anchor_delays,
                residuals=residuals,
                tag_delay_mm=tag_delay_mm,
                sigma_mm=sigma_mm,
                samples_per_position=int(args.samples_per_position),
                keep_work=bool(args.keep_work),
            )
        )
    for case in random_case_specs():
        for rep in range(int(args.n)):
            remaining_tasks.append(
                make_task(
                    case=case,
                    rep_index=rep,
                    seed=int(rng.integers(0, 2**31 - 1)),
                    run_dir=run_dir,
                    layout_path=layout_path,
                    sigma_path=sigma_path,
                    anchor_truth=anchor_truth,
                    tag_truth=tag_truth,
                    anchor_delays=anchor_delays,
                    residuals=residuals,
                    tag_delay_mm=tag_delay_mm,
                    sigma_mm=sigma_mm,
                    samples_per_position=int(args.samples_per_position),
                    keep_work=bool(args.keep_work),
                )
            )

    total_invocations = 1 + len(remaining_tasks)
    estimated_wall = baseline_elapsed * total_invocations / max(1, int(args.workers))
    timing = {
        "baseline_elapsed_s": baseline_elapsed,
        "total_solver_invocations": total_invocations,
        "estimated_wall_clock_s": estimated_wall,
    }
    print(f"[tag-phase0] one invocation elapsed={baseline_elapsed:.3f}s")
    print(f"[tag-phase0] total invocations={total_invocations}; estimated wall with {args.workers} workers={estimated_wall:.1f}s")

    t0 = time.time()
    results = [baseline_result]
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
        futures = [ex.submit(run_one_realization, task) for task in remaining_tasks]
        for done, fut in enumerate(as_completed(futures), start=1):
            item = fut.result()
            results.append(item)
            if done == 1 or done % 25 == 0 or done == len(futures):
                ok = sum(1 for x in results if x.get("success"))
                print(f"[progress] {done}/{len(futures)} remaining done; ok={ok}/{len(results)} elapsed={time.time()-t0:.1f}s", flush=True)

    failures = [x for x in results if not x.get("success")]
    if failures:
        fail_path = run_dir / "failures.json"
        fail_path.write_text(json.dumps(failures[:20], indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"{len(failures)} solver realizations failed; see {fail_path}")

    by_case: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        by_case.setdefault(result["case"], []).append(result)
    aggregates = {case: aggregate(items) for case, items in sorted(by_case.items())}
    low_red_result = by_case["LOW_RED_SINGLE_BIAS"][0]
    readouts = build_readouts(
        aggregates=aggregates,
        baseline_result=baseline_result,
        low_red_result=low_red_result,
        tag_params=tag_params,
        residuals=residuals,
        real_headline=real_headline,
    )

    payload = {
        "run_dir": str(run_dir),
        "config": {
            "n_random": int(args.n),
            "workers": int(args.workers),
            "seed": int(args.seed),
            "samples_per_position": int(args.samples_per_position),
            "keep_work": bool(args.keep_work),
            "coordinate_convention": "Y is vertical; horizontal plane is X/Z.",
            "solver_method": "T4",
            "production_cli": readouts["solver_recon"]["production_cli"],
        },
        "sources": {
            "anchor_truth": str(ANCHOR_TRUTH_JSON),
            "anchor_delay": str(ANCHOR_DELAY_JSON),
            "tag_empirical_params": str(TAG_PARAMS_JSON),
            "anchor_sigma": str(ANCHOR_SIGMA_SOURCE if ANCHOR_SIGMA_SOURCE.exists() else sigma_path),
            "tag_solver_cli": str(TAG_EXPORT),
        },
        "timing": timing | {"actual_wall_clock_s": time.time() - t0},
        "real_headline": real_headline,
        "readouts": readouts,
        "aggregates": aggregates,
    }

    results_json = run_dir / "tag_phase0_results.json"
    results_md = run_dir / "tag_phase0_results.md"
    results_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    write_markdown(
        results_md,
        run_dir=run_dir,
        config=payload["config"],
        timing=payload["timing"],
        readouts=readouts,
        aggregates=aggregates,
    )

    print("\n[readouts]")
    print(
        "1. BASELINE sanity: median={:.3f}mm p95={:.3f}mm pass_lt_5mm={}".format(
            readouts["baseline_sanity"]["median_3d_mm"],
            readouts["baseline_sanity"]["p95_3d_mm"],
            readouts["baseline_sanity"]["passes_lt_5mm"],
        )
    )
    print(
        "2. E3 delay/CIR-blind: median={:.3f}mm p95={:.3f}mm".format(
            readouts["e3_delay_cir_blind"]["median_3d_mm"],
            readouts["e3_delay_cir_blind"]["p95_3d_mm"],
        )
    )
    print(
        "3. E4_REAL residual/CIR-addressable: median={:.3f}mm p95={:.3f}mm E3/E4={:.3f}".format(
            readouts["e4_real_cir_addressable"]["median_3d_mm"],
            readouts["e4_real_cir_addressable"]["p95_3d_mm"],
            readouts["e4_real_cir_addressable"]["e3_over_e4_median_ratio"],
        )
    )
    print(
        "4. E4_TAIL rejection: injected_tail_p95={:.3f}mm position_p95={:.3f}mm verdict={}".format(
            readouts["e4_tail_rejection"]["injected_tail_p95_abs_mm"],
            readouts["e4_tail_rejection"]["position_p95_3d_mm"],
            readouts["e4_tail_rejection"]["verdict"],
        )
    )
    print(
        "5. Single +400mm bias {}-{}: shift={:.3f}mm".format(
            readouts["low_redundancy_single_bias"]["tag_position"],
            readouts["low_redundancy_single_bias"]["anchor"],
            readouts["low_redundancy_single_bias"]["position_shift_3d_mm"],
        )
    )
    print(
        "6. COMBINED_REAL vs real headline: combined_median={:.3f}mm real_median={:.3f}mm ratio={:.3f}".format(
            readouts["combined_real_vs_headline"]["combined_real_median_3d_mm"],
            readouts["combined_real_vs_headline"]["real_headline_median_3d_mm"],
            readouts["combined_real_vs_headline"]["ratio_combined_median_to_real"],
        )
    )
    print(f"[output] {results_json}")
    print(f"[output] {results_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
