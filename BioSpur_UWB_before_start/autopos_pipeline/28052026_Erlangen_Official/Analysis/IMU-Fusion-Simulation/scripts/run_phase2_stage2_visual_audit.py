#!/usr/bin/env python3
"""Run Phase 2 stage2 visual audit for an existing screening run."""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import multiprocessing as mp
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
STAGE1_SCRIPT = SIM_ROOT / "scripts" / "run_phase2_stage1_screening.py"


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


S1 = load_module(STAGE1_SCRIPT, "phase2_stage1_for_stage2")

_B0: pd.DataFrame | None = None
_P2: pd.DataFrame | None = None
_RAW_BY_TRACK: dict[tuple[str, str], pd.DataFrame] = {}
_ANCHOR_XYZ: np.ndarray | None = None
_ANCHOR_DELAY: np.ndarray | None = None
_TAG_DELAY: float = 0.0
_RANGE_BIAS: np.ndarray | None = None
_RANGE_SIGMA: np.ndarray | None = None
_TRACK_METRICS: pd.DataFrame | None = None
_RUN_ID = ""
_STAGE2_DIR: Path | None = None


def fmt(value: object, digits: int = 1) -> str:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(f):
        return "nan"
    return f"{f:.{digits}f}"


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    pd.DataFrame(rows, columns=fields).to_csv(path, index=False)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")


def markdown_table(rows: list[dict], columns: list[str]) -> str:
    if not rows:
        return ""
    out = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in rows:
        vals = []
        for col in columns:
            val = row.get(col, "")
            vals.append(fmt(val) if isinstance(val, float) else str(val))
        out.append("| " + " | ".join(vals) + " |")
    return "\n".join(out)


def select_experiments(summary: pd.DataFrame) -> list[dict]:
    selected: list[dict] = []

    def add(rows: pd.DataFrame, reason: str, limit: int = 1) -> None:
        for row in rows.head(limit).to_dict("records"):
            row["stage2_reason"] = reason
            selected.append(row)

    add(summary[summary["kind"].eq("baseline")], "control_baseline", 1)
    add(summary[summary["kind"].eq("imu_only") & summary["experiment_id"].str.contains("L0_I0", regex=False)], "perfect_imu_oracle", 1)
    add(summary[summary["kind"].eq("imu_only") & summary["experiment_id"].str.contains("L2_I3", regex=False)], "mpu6050_like_drift_control", 1)
    add(summary[summary["verdict"].eq("FUSION_HELPS_DEPLOYABLE")].sort_values("screening_score"), "deployable_help_candidate", 3)
    add(summary[summary["kind"].eq("position_fusion")].sort_values("screening_score"), "top_position_score", 5)
    add(summary[summary["verdict"].eq("FUSION_NEUTRAL")].sort_values("screening_score"), "neutral_control", 3)
    add(summary[summary["kind"].eq("range_fusion")].sort_values("screening_score"), "best_range_side_proto", 3)

    out: list[dict] = []
    seen: set[str] = set()
    for row in selected:
        exp = str(row["experiment_id"])
        if exp in seen:
            continue
        seen.add(exp)
        out.append(row)
    return out


def init_worker(
    b0: pd.DataFrame,
    p2: pd.DataFrame,
    raw_by_track: dict[tuple[str, str], pd.DataFrame],
    anchor_xyz: np.ndarray,
    anchor_delay: np.ndarray,
    tag_delay: float,
    range_bias: np.ndarray,
    range_sigma: np.ndarray,
    track_metrics: pd.DataFrame,
    run_id: str,
    stage2_dir: str,
) -> None:
    global _B0, _P2, _RAW_BY_TRACK, _ANCHOR_XYZ, _ANCHOR_DELAY, _TAG_DELAY, _RANGE_BIAS, _RANGE_SIGMA
    global _TRACK_METRICS, _RUN_ID, _STAGE2_DIR
    _B0 = b0
    _P2 = p2
    _RAW_BY_TRACK = raw_by_track
    _ANCHOR_XYZ = anchor_xyz
    _ANCHOR_DELAY = anchor_delay
    _TAG_DELAY = tag_delay
    _RANGE_BIAS = range_bias
    _RANGE_SIGMA = range_sigma
    _TRACK_METRICS = track_metrics
    _RUN_ID = run_id
    _STAGE2_DIR = Path(stage2_dir)


def parse_experiment_id(exp: str) -> dict[str, str]:
    parts = exp.split("_")
    if exp.startswith("B0_"):
        return {"kind": "baseline"}
    if len(parts) == 5 and parts[2].startswith("L"):
        return {"kind": "imu_only", "L": parts[2], "I": parts[3], "T": parts[4]}
    if len(parts) == 7 and parts[2].startswith("U"):
        return {"kind": "position_fusion", "U": parts[2], "P": parts[3], "L": parts[4], "I": parts[5], "T": parts[6]}
    if len(parts) == 6 and parts[2].startswith("R"):
        return {"kind": "range_fusion", "R": parts[2], "L": parts[3], "I": parts[4], "T": parts[5]}
    raise ValueError(f"cannot parse experiment_id: {exp}")


def imu_samples(l_id: str, i_id: str) -> pd.DataFrame:
    if _B0 is None:
        raise RuntimeError("worker missing B0")
    return S1.simulate_imu_for_li(_B0, _RUN_ID, l_id, i_id)


def reconstruct_samples(exp: str) -> pd.DataFrame:
    if _B0 is None or _P2 is None:
        raise RuntimeError("worker was not initialized")
    parsed = parse_experiment_id(exp)
    kind = parsed["kind"]
    if kind == "baseline":
        return _B0.copy()
    if kind == "imu_only":
        return imu_samples(parsed["L"], parsed["I"])
    if kind == "position_fusion":
        prior = imu_samples(parsed["L"], parsed["I"])
        stream = _B0 if parsed["P"] == "P0" else _P2
        t_id = parsed["T"]
        params = S1.T_PARAMS[t_id]
        process = S1.li_process_factor(parsed["L"], parsed["I"])
        prior_sigma = float(params["prior_sigma_base"]) * process
        if parsed["L"] == "L0":
            prior_sigma = min(prior_sigma, 8.0 if t_id == "T2" else 35.0)
        return S1.position_fusion_samples(
            stream,
            prior,
            exp,
            str(params["deployability"]),
            f"Phase 2 stage2 reconstruction {exp}.",
            prior_sigma,
            float(params["measurement_sigma"]),
        )
    if kind == "range_fusion":
        if _ANCHOR_XYZ is None or _ANCHOR_DELAY is None or _RANGE_BIAS is None or _RANGE_SIGMA is None:
            raise RuntimeError("worker missing range state")
        prior = imu_samples(parsed["L"], parsed["I"])
        t_id = parsed["T"]
        r_id = parsed["R"]
        params = S1.T_PARAMS[t_id]
        process = S1.li_process_factor(parsed["L"], parsed["I"])
        prior_sigma = float(params["prior_sigma_base"]) * process
        if parsed["L"] == "L0":
            prior_sigma = min(prior_sigma, 45.0)
        return S1.range_fusion_samples(
            _RAW_BY_TRACK,
            prior,
            exp,
            str(params["deployability"]),
            f"Phase 2 stage2 reconstruction {exp}.",
            prior_sigma,
            1.0 if r_id == "R2" else 1.35,
            t_id == "T8" or r_id == "R4",
            _RANGE_BIAS,
            _RANGE_SIGMA,
            _ANCHOR_XYZ,
            _ANCHOR_DELAY,
            _TAG_DELAY,
        )
    raise RuntimeError(f"unsupported kind: {kind}")


def color_for(kind: str, verdict: str) -> str:
    if kind == "baseline":
        return "#1f77b4"
    if kind == "imu_only":
        return "#d62728" if "DRIFTS" in verdict else "#17becf"
    if verdict == "FUSION_HELPS_DEPLOYABLE":
        return "#2ca02c"
    if kind == "range_fusion":
        return "#9467bd"
    if verdict == "FUSION_NEUTRAL":
        return "#ff7f0e"
    return "#bcbd22"


def pct(values: np.ndarray, q: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, q))


def finite_limits(*arrays: np.ndarray) -> tuple[float, float]:
    vals = np.concatenate([np.asarray(a, dtype=float).ravel() for a in arrays])
    vals = vals[np.isfinite(vals)]
    if vals.size == 0:
        return -1.0, 1.0
    lo = float(np.min(vals))
    hi = float(np.max(vals))
    pad = max(1.0, 0.05 * (hi - lo))
    return lo - pad, hi + pad


def plot_contact_sheet(samples: pd.DataFrame, record: dict) -> dict:
    if _STAGE2_DIR is None:
        raise RuntimeError("worker missing stage2 dir")
    exp = str(record["experiment_id"])
    kind = str(record.get("kind", ""))
    verdict = str(record.get("verdict", ""))
    color = color_for(kind, verdict)
    fig_dir = _STAGE2_DIR / "figs" / "contact_sheets"
    fig_dir.mkdir(parents=True, exist_ok=True)

    groups = list(samples.groupby(["capture_id", "tag"], sort=True))
    cols = 4
    rows = int(math.ceil(len(groups) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 3.1, rows * 2.75), squeeze=False)
    for ax in axes.ravel():
        ax.axis("off")
    for idx, ((capture_id, tag), g0) in enumerate(groups):
        ax = axes.ravel()[idx]
        ax.axis("on")
        g = g0.sort_values("time_s")
        ax.plot(g["opti_x_mm"], g["opti_z_mm"], color="black", lw=0.9)
        if {"uwb_x_mm", "uwb_z_mm"}.issubset(g.columns):
            ax.plot(g["uwb_x_mm"], g["uwb_z_mm"], color="0.72", lw=0.6)
        ax.plot(g["x_mm"], g["z_mm"], color=color, lw=0.8)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_title(f"{capture_id}/{tag} P95={fmt(pct(g['err3d_mm'].to_numpy(float), 95), 0)}", fontsize=7)
    fig.suptitle(f"{exp} | {verdict}", fontsize=11)
    path = fig_dir / f"{exp}__contact_sheet.png"
    fig.tight_layout(rect=[0, 0.02, 1, 0.98])
    fig.savefig(path, dpi=135)
    plt.close(fig)
    return {
        "figure_path": str(path.relative_to(SIM_ROOT)),
        "figure_kind": "contact_sheet_xz",
        "phase": "phase2_stage2",
        "experiment_id": exp,
        "capture_id": "R01-R17",
        "tag": "both",
        "metric_context": "all-track top-view contact sheet",
        "notes": str(record.get("stage2_reason", "")),
    }


def plot_curated_tracks(samples: pd.DataFrame, record: dict) -> list[dict]:
    if _STAGE2_DIR is None or _TRACK_METRICS is None:
        raise RuntimeError("worker missing stage2 state")
    exp = str(record["experiment_id"])
    kind = str(record.get("kind", ""))
    verdict = str(record.get("verdict", ""))
    color = color_for(kind, verdict)
    fig_dir = _STAGE2_DIR / "figs" / "curated" / exp
    fig_dir.mkdir(parents=True, exist_ok=True)
    tm = _TRACK_METRICS[_TRACK_METRICS["experiment_id"].eq(exp)].sort_values("err3d_p95_mm", ascending=False)
    chosen = tm[["capture_id", "tag", "err3d_p95_mm"]].head(4).to_dict("records")
    out_rows: list[dict] = []
    for row in chosen:
        capture_id = str(row["capture_id"])
        tag = str(row["tag"])
        g = samples[(samples["capture_id"].astype(str) == capture_id) & (samples["tag"].astype(str) == tag)].sort_values("time_s")
        if g.empty:
            continue
        fig, axes = plt.subplots(1, 2, figsize=(8.2, 3.6))
        ax = axes[0]
        ax.plot(g["opti_x_mm"], g["opti_z_mm"], color="black", lw=1.5, label="Opti")
        if {"uwb_x_mm", "uwb_z_mm"}.issubset(g.columns):
            ax.plot(g["uwb_x_mm"], g["uwb_z_mm"], color="0.70", lw=0.8, label="UWB")
        ax.plot(g["x_mm"], g["z_mm"], color=color, lw=1.1, label="Output")
        xlo, xhi = finite_limits(g["opti_x_mm"].to_numpy(float), g["uwb_x_mm"].to_numpy(float), g["x_mm"].to_numpy(float))
        zlo, zhi = finite_limits(g["opti_z_mm"].to_numpy(float), g["uwb_z_mm"].to_numpy(float), g["z_mm"].to_numpy(float))
        ax.set_xlim(xlo, xhi)
        ax.set_ylim(zlo, zhi)
        ax.set_aspect("equal", adjustable="box")
        ax.grid(True, alpha=0.25)
        ax.set_xlabel("x mm")
        ax.set_ylabel("z mm")
        ax.legend(fontsize=7)

        ax2 = axes[1]
        t = g["time_s"].to_numpy(float)
        t = t - t[0] if len(t) else t
        ax2.plot(t, g["err3d_mm"].to_numpy(float), color=color, lw=1.0)
        ax2.axhline(pct(g["err3d_mm"].to_numpy(float), 95), color="0.35", lw=0.8, ls="--")
        ax2.set_xlabel("time s")
        ax2.set_ylabel("3D error mm")
        ax2.grid(True, alpha=0.25)
        fig.suptitle(f"{exp} {capture_id}/{tag} | P95={fmt(row['err3d_p95_mm'], 0)} | {verdict}", fontsize=9)
        path = fig_dir / f"{exp}__{capture_id}__{tag}__curated.png"
        fig.tight_layout(rect=[0, 0.02, 1, 0.93])
        fig.savefig(path, dpi=145)
        plt.close(fig)
        out_rows.append(
            {
                "figure_path": str(path.relative_to(SIM_ROOT)),
                "figure_kind": "curated_worst_track_xz_error",
                "phase": "phase2_stage2",
                "experiment_id": exp,
                "capture_id": capture_id,
                "tag": tag,
                "metric_context": f"worst-track P95={fmt(row['err3d_p95_mm'], 1)} mm",
                "notes": str(record.get("stage2_reason", "")),
            }
        )
    return out_rows


def render_worker(job: tuple[int, dict]) -> dict:
    job_index, record = job
    t0 = time.perf_counter()
    exp = str(record["experiment_id"])
    samples = reconstruct_samples(exp)
    figures = [plot_contact_sheet(samples, record)]
    figures.extend(plot_curated_tracks(samples, record))
    return {
        "job_index": job_index,
        "experiment_id": exp,
        "figure_rows": figures,
        "timing": {"experiment_id": exp, "wall_time_s": time.perf_counter() - t0, "status": "ok"},
    }


def visual_gates(summary: pd.DataFrame, selected_rows: list[dict], figure_rows: list[dict], manifest: dict) -> list[dict]:
    row_count_ok = int(len(summary)) == 705
    fig_ok = len(figure_rows) >= len(selected_rows)
    help_count = int(summary["verdict"].eq("FUSION_HELPS_DEPLOYABLE").sum())
    workers = int(manifest.get("stage1_workers", 0) or 0)
    return [
        {
            "gate_id": "G7_stage1_row_count",
            "status": "PASS" if row_count_ok else "FAIL",
            "evidence": f"stage1 rows={len(summary)} expected=705",
            "blocking_next_phase": not row_count_ok,
        },
        {
            "gate_id": "G8_stage2_visual_assets",
            "status": "PASS" if fig_ok else "FAIL",
            "evidence": f"selected_experiments={len(selected_rows)} figures={len(figure_rows)}",
            "blocking_next_phase": not fig_ok,
        },
        {
            "gate_id": "G9_deployable_candidate_present",
            "status": "PASS" if help_count >= 1 else "FAIL",
            "evidence": f"FUSION_HELPS_DEPLOYABLE rows={help_count}",
            "blocking_next_phase": help_count < 1,
        },
        {
            "gate_id": "G10_cpu_parallel_execution",
            "status": "PASS" if workers >= 2 else "FAIL",
            "evidence": f"stage1_workers={workers}",
            "blocking_next_phase": workers < 2,
        },
    ]


def write_report(run_dir: Path, selected_rows: list[dict], gates: list[dict], figure_rows: list[dict], elapsed_s: float) -> None:
    summary_cols = [
        "stage2_reason",
        "experiment_id",
        "kind",
        "screening_score",
        "trackmedian_err3d_p50_mm",
        "trackmedian_err3d_p95_mm",
        "legacy_deltaR_error_rms_mm",
        "trackmedian_radius_error_abs_mm",
        "verdict",
    ]
    gate_cols = ["gate_id", "status", "evidence", "blocking_next_phase"]
    report = [
        "# Phase 2 Visual Audit",
        "",
        f"Generated: {datetime.now(UTC).isoformat()}",
        "Phase status: `stage2_visual_audit_complete`",
        f"Stage2 wall time: {fmt(elapsed_s, 2)} s",
        f"Selected experiments: {len(selected_rows)}",
        f"PNG figures: {len(figure_rows)}",
        "",
        "## Visual Gates",
        "",
        markdown_table(gates, gate_cols),
        "",
        "## Selected Experiments",
        "",
        markdown_table(selected_rows, summary_cols),
        "",
        "## Main Read",
        "",
        "- Only one row currently satisfies deployable-help verdict; visual confirmation is mandatory before promoting it.",
        "- Most high-score position rows improve central error but damage ROTO geometry, so P50/P95 alone is not sufficient.",
        "- Best range-side prototypes are still much worse than B0 after bias correction, so T6/T8 remain prototype-only in this run.",
        "- Pure IMU rows are retained as drift diagnostics, not candidates for deployable ROTO fusion.",
        "",
        "## Figure Index",
        "",
        markdown_table(figure_rows[:60], ["figure_kind", "experiment_id", "capture_id", "tag", "figure_path"]),
        "",
    ]
    (run_dir / "reports" / "PHASE2_VISUAL_AUDIT.md").write_text("\n".join(report), encoding="utf-8")


def run(args: argparse.Namespace) -> dict:
    run_id = args.run_id or S1.latest_phase2_ready_run()
    run_dir = SIM_ROOT / "runs" / "phase2_screening" / run_id
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("phase_status") not in {"stage1_screening_complete", "stage2_visual_audit_complete"}:
        raise RuntimeError(f"phase2 run is not ready for stage2: {manifest.get('phase_status')}")
    stage2 = run_dir / "stage2_ranking_and_visual_audit"
    for d in [stage2 / "figs" / "contact_sheets", stage2 / "figs" / "curated", stage2 / "tables", run_dir / "reports"]:
        d.mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    summary = pd.read_csv(run_dir / "tables" / "phase2_summary.csv")
    track_metrics = pd.read_csv(run_dir / "stage1_screening" / "tables" / "phase2_stage1_track_metrics.csv")
    selected_rows = select_experiments(summary)
    write_csv(stage2 / "tables" / "stage2_selected_experiments.csv", selected_rows)

    b0 = S1.load_b0_samples()
    p2 = S1.robust_p2_stream(b0)
    raw_by_track = S1.load_raw_frames(b0)
    anchor_xyz, anchor_delay, tag_delay = S1.load_a0_layout()
    range_bias, range_sigma = S1.load_range_policy(run_dir)

    cpu_count = os.cpu_count() or 2
    workers = int(args.workers or min(6, max(2, cpu_count - 4)))
    workers = max(1, min(workers, cpu_count))
    print(f"[stage2] run_id={run_id} selected={len(selected_rows)} workers={workers}", flush=True)

    jobs = list(enumerate(selected_rows))
    ctx = mp.get_context("fork")
    figure_rows: list[dict] = []
    timing_rows: list[dict] = []
    with ProcessPoolExecutor(
        max_workers=workers,
        mp_context=ctx,
        initializer=init_worker,
        initargs=(
            b0,
            p2,
            raw_by_track,
            anchor_xyz,
            anchor_delay,
            tag_delay,
            range_bias,
            range_sigma,
            track_metrics,
            run_id,
            str(stage2),
        ),
    ) as pool:
        futures = [pool.submit(render_worker, job) for job in jobs]
        for done, future in enumerate(as_completed(futures), start=1):
            result = future.result()
            figure_rows.extend(result["figure_rows"])
            timing_rows.append(result["timing"])
            print(f"[stage2] rendered {done}/{len(jobs)} {result['experiment_id']}", flush=True)

    figure_rows = sorted(figure_rows, key=lambda r: (str(r["experiment_id"]), str(r["figure_kind"]), str(r["capture_id"]), str(r["tag"])))
    timing_rows = sorted(timing_rows, key=lambda r: str(r["experiment_id"]))
    gates = visual_gates(summary, selected_rows, figure_rows, manifest)
    elapsed = time.perf_counter() - start

    write_csv(stage2 / "tables" / "stage2_figure_index.csv", figure_rows)
    write_csv(stage2 / "tables" / "stage2_timing.csv", timing_rows)
    write_csv(stage2 / "tables" / "stage2_visual_gates.csv", gates)
    write_report(run_dir, selected_rows, gates, figure_rows, elapsed)

    manifest.update(
        {
            "phase_status": "stage2_visual_audit_complete",
            "stage_completed": "stage2_ranking_and_visual_audit",
            "stage2_elapsed_s": elapsed,
            "stage2_workers": workers,
            "stage2_selected_experiment_count": len(selected_rows),
            "stage2_figure_count": len(figure_rows),
            "stage2_generated_utc": datetime.now(UTC).isoformat(),
            "outputs": {
                **manifest.get("outputs", {}),
                "stage2_selected_experiments": str((stage2 / "tables" / "stage2_selected_experiments.csv").relative_to(SIM_ROOT)),
                "stage2_figure_index": str((stage2 / "tables" / "stage2_figure_index.csv").relative_to(SIM_ROOT)),
                "stage2_visual_gates": str((stage2 / "tables" / "stage2_visual_gates.csv").relative_to(SIM_ROOT)),
                "stage2_visual_audit_report": str((run_dir / "reports" / "PHASE2_VISUAL_AUDIT.md").relative_to(SIM_ROOT)),
            },
        }
    )
    write_json(run_dir / "manifest.json", manifest)
    write_json(SIM_ROOT / "manifests" / f"phase2_{run_id}.json", manifest)
    return {
        "run_id": run_id,
        "run_dir": str(run_dir),
        "elapsed_s": elapsed,
        "selected": len(selected_rows),
        "figures": len(figure_rows),
        "gates": gates,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Phase 2 stage2 visual audit.")
    parser.add_argument("--run-id", default="", help="Existing phase2_screening run ID.")
    parser.add_argument("--workers", type=int, default=0, help="Render/reconstruction workers. Default: min(6, cpu_count - 4).")
    args = parser.parse_args()
    result = run(args)
    print(
        json.dumps(
            {
                "run_id": result["run_id"],
                "run_dir": result["run_dir"],
                "selected": result["selected"],
                "figures": result["figures"],
                "elapsed_s": result["elapsed_s"],
            },
            indent=2,
        )
    )
    print("\nGATES")
    for gate in result["gates"]:
        print(f"{gate['gate_id']} {gate['status']} blocking={gate['blocking_next_phase']} :: {gate['evidence']}")


if __name__ == "__main__":
    main()
