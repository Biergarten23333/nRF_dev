#!/usr/bin/env python3
"""Phase 0 -- solver headroom simulation driver.

This harness is intentionally file- and subprocess-based: it generates synthetic
``pairs_all.csv`` files with the same schema as the production AutoPos sweep
input, then runs the existing production solver CLI.  It does not reimplement
the layout solver.
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

from layout_align_metrics import align_layout_metrics


SCRIPT_DIR = Path(__file__).resolve().parent
PHASE_DIR = SCRIPT_DIR.parent
REPO_ROOT = SCRIPT_DIR.parents[5]
ANALYSIS_ROOT = REPO_ROOT / "autopos_pipeline/28052026_Erlangen_Official/Analysis"
BROADCAST_PREPARE = REPO_ROOT / "SS-TWR/alt-SS-TWR/broadcast/scripts/prepare_autopos_v3_box.py"
REFERENCE_PAIRS = (
    REPO_ROOT
    / "autopos_pipeline/28052026_Erlangen_Official/solver/work/field_dataset_staged/sweep1000/pairs_all.csv"
)
TRUTH_JSON = PHASE_DIR / "data/erlangen_anchor_truth_all8_v4io.json"
DELAY_JSON = PHASE_DIR / "data/erlangen_delay_distribution.json"
PARAMS_JSON = PHASE_DIR / "data/erlangen_empirical_injection_params.json"
REAL_LAYOUT_ERRORS_CSV = (
    ANALYSIS_ROOT / "official_extra_analysis/FULL/tables/layout_abs_errors_all8.csv"
)
ANCHORS = tuple("ABCDEFGH")
SAMPLES_PER_DIRECTED_LINK = 1000


@dataclass(frozen=True)
class CaseSpec:
    key: str
    family: str
    deterministic: bool
    k: int | None = None
    factor: float | None = None


def percentile(xs: list[float], p: float) -> float:
    if not xs:
        return float("nan")
    arr = sorted(xs)
    k = (len(arr) - 1) * p / 100.0
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return float(arr[lo])
    return float(arr[lo] * (hi - k) + arr[hi] * (k - lo))


def load_truth() -> dict[str, dict[str, float]]:
    doc = json.loads(TRUTH_JSON.read_text())
    return {a: dict(v) for a, v in doc["anchors"].items()}


def truth_points(truth: dict[str, dict[str, float]]) -> dict[str, np.ndarray]:
    return {
        a: np.array([v["x_mm"], v["y_mm"], v["z_mm"]], dtype=float)
        for a, v in truth.items()
    }


def load_endpoint_delay() -> dict[str, float]:
    doc = json.loads(DELAY_JSON.read_text())
    return {
        a: float(v["delaycal_endpoint_delay_mm"])
        for a, v in doc["per_anchor"].items()
    }


def load_empirical_params() -> tuple[float, dict[str, float]]:
    doc = json.loads(PARAMS_JSON.read_text())
    sigma = float(doc["sigma"]["aggregates"]["pooled_demeaned_mad_sigma_mm"])
    residuals = {
        link: float(v["residual_after_frozen_endpoint_delay_mm"])
        for link, v in doc["consistent_per_link_residual"]["per_link"].items()
    }
    return sigma, residuals


def inspect_reference_pairs() -> dict[str, Any]:
    with REFERENCE_PAIRS.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        columns = list(reader.fieldnames or [])
        counts: dict[str, int] = {}
        rows = 0
        for row in reader:
            a = row["a"].strip().upper()
            b = row["b"].strip().upper()
            master = row["master"].strip().upper()
            if master == a:
                dst = b
            elif master == b:
                dst = a
            else:
                dst = "?"
            counts[f"{master}->{dst}"] = counts.get(f"{master}->{dst}", 0) + 1
            rows += 1
    return {
        "source": str(REFERENCE_PAIRS),
        "columns": columns,
        "rows": rows,
        "directed_link_count": len(counts),
        "min_samples_per_directed_link": min(counts.values()),
        "max_samples_per_directed_link": max(counts.values()),
        "directed_link_convention": "master -> other endpoint using columns a,b,master",
    }


def true_distance_mm(points: dict[str, np.ndarray], a: str, b: str) -> float:
    return float(np.linalg.norm(points[a] - points[b]))


def directed_links() -> list[tuple[str, str, str, str]]:
    links: list[tuple[str, str, str, str]] = []
    for i, a in enumerate(ANCHORS):
        for b in ANCHORS[i + 1 :]:
            links.append((a, b, a, f"{a}->{b}"))
            links.append((a, b, b, f"{b}->{a}"))
    return links


def generate_pairs_csv(
    out_csv: Path,
    *,
    case: CaseSpec,
    rep_index: int,
    seed: int,
    truth: dict[str, dict[str, float]],
    endpoint_delay: dict[str, float],
    sigma_mm: float,
    real_residual_by_link: dict[str, float],
) -> dict[str, Any]:
    rng = np.random.default_rng(seed)
    points = truth_points(truth)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    outlier_offsets: dict[str, float] = {}
    if case.family == "E2":
        all_links = [link for _, _, _, link in directed_links()]
        chosen = rng.choice(all_links, size=int(case.k or 0), replace=False)
        for link in chosen:
            mag = float(rng.uniform(100.0, 300.0))
            sign = -1.0 if float(rng.random()) < 0.5 else 1.0
            outlier_offsets[str(link)] = sign * mag

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["a", "b", "master", "dist_mm", "quality_percent", "raw_mm", "ok", "fail"],
        )
        writer.writeheader()
        for a, b, master, link in directed_links():
            dst = b if master == a else a
            base = true_distance_mm(points, master, dst)
            fixed_add = 0.0
            if case.family == "E2":
                fixed_add += outlier_offsets.get(link, 0.0)
            if case.family in {"E3", "COMBINED_REAL"}:
                fixed_add += endpoint_delay[master] + endpoint_delay[dst]
            if case.family in {"E4_REAL", "COMBINED_REAL"}:
                fixed_add += real_residual_by_link[link]
            if case.family == "E4_SWEEP":
                fixed_add += float(case.factor or 1.0) * real_residual_by_link[link]

            for _ in range(SAMPLES_PER_DIRECTED_LINK):
                d = base + fixed_add
                if case.family in {"E1", "COMBINED_REAL"}:
                    d += float(rng.normal(0.0, sigma_mm))
                d_int = int(round(d))
                writer.writerow(
                    {
                        "a": a,
                        "b": b,
                        "master": master,
                        "dist_mm": d_int,
                        "quality_percent": 100,
                        "raw_mm": d_int,
                        "ok": 1,
                        "fail": 0,
                    }
                )
    return {
        "seed": seed,
        "rep_index": rep_index,
        "outlier_offsets_mm": outlier_offsets,
    }


def solved_layout_to_mapping(layout_json: Path) -> dict[str, dict[str, float]]:
    doc = json.loads(layout_json.read_text())
    anchors = doc.get("anchors")
    if isinstance(anchors, list):
        return {
            item["label"]: {
                "x_mm": float(item["x_mm"]),
                "y_mm": float(item["y_mm"]),
                "z_mm": float(item["z_mm"]),
            }
            for item in anchors
        }
    if isinstance(anchors, dict):
        return {
            a: {"x_mm": float(v["x_mm"]), "y_mm": float(v["y_mm"]), "z_mm": float(v["z_mm"])}
            for a, v in anchors.items()
        }
    raise ValueError(f"unsupported layout format in {layout_json}")


def flatten_metrics(report: dict[str, Any]) -> dict[str, float]:
    out: dict[str, float] = {}
    for mode in ("rigid", "similarity"):
        out[f"{mode}_scale"] = float(report["modes"][mode]["scale"])
        for component in ("total", "horizontal_xz", "vertical_y"):
            comp = report["modes"][mode]["errors"][component]
            prefix = f"{mode}_{component}"
            out[f"{prefix}_rms_mm"] = float(comp["rms_mm"])
            out[f"{prefix}_median_mm"] = float(comp["median_mm"])
    for component in ("total", "horizontal_xz"):
        comp = report["scale_contribution"][component]
        out[f"scale_contribution_{component}_rms_mm"] = float(comp["rms_mm"])
        out[f"scale_contribution_{component}_median_mm"] = float(comp["median_mm"])
    return out


def production_invocation(pairs_csv: Path, out_dir: Path) -> list[str]:
    return [
        sys.executable,
        str(BROADCAST_PREPARE),
        "--pairs-csv",
        str(pairs_csv.resolve()),
        "--out-dir",
        str(out_dir.resolve()),
        "--verbose",
        "0",
    ]


def run_one_realization(task: dict[str, Any]) -> dict[str, Any]:
    case = CaseSpec(**task["case"])
    rep_dir = Path(task["rep_dir"])
    truth = task["truth"]
    endpoint_delay = task["endpoint_delay"]
    real_residual_by_link = task["real_residual_by_link"]
    sigma_mm = float(task["sigma_mm"])
    seed = int(task["seed"])
    rep_index = int(task["rep_index"])

    pairs_csv = rep_dir / "pairs_all.csv"
    solver_out_dir = rep_dir / "solver"
    log_path = rep_dir / "solver_stdout.log"
    rep_dir.mkdir(parents=True, exist_ok=True)

    injection_meta = generate_pairs_csv(
        pairs_csv,
        case=case,
        rep_index=rep_index,
        seed=seed,
        truth=truth,
        endpoint_delay=endpoint_delay,
        sigma_mm=sigma_mm,
        real_residual_by_link=real_residual_by_link,
    )
    cmd = production_invocation(pairs_csv, solver_out_dir)
    t0 = time.time()
    proc = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    elapsed = time.time() - t0
    log_path.write_text(proc.stdout or "", encoding="utf-8")
    if proc.returncode != 0:
        return {
            "case": case.key,
            "rep_index": rep_index,
            "seed": seed,
            "success": False,
            "returncode": proc.returncode,
            "elapsed_s": elapsed,
            "rep_dir": str(rep_dir),
            "log_tail": (proc.stdout or "")[-4000:],
        }
    layout_json = solver_out_dir / "anchor_layout_v3_box.json"
    solved = solved_layout_to_mapping(layout_json)
    report = align_layout_metrics(solved, truth, allow_reflection=True)
    metrics = flatten_metrics(report)
    return {
        "case": case.key,
        "family": case.family,
        "rep_index": rep_index,
        "seed": seed,
        "success": True,
        "elapsed_s": elapsed,
        "rep_dir": str(rep_dir),
        "layout_json": str(layout_json),
        "injection": injection_meta,
        "metrics": metrics,
    }


def case_specs() -> list[CaseSpec]:
    cases = [
        CaseSpec("BASELINE", "BASELINE", True),
        CaseSpec("E1", "E1", False),
    ]
    for k in (1, 2, 4, 8):
        cases.append(CaseSpec(f"E2_K{k}", "E2", False, k=k))
    cases.append(CaseSpec("E3", "E3", True))
    cases.append(CaseSpec("E4_REAL", "E4_REAL", True))
    for factor in (1.0, 2.0, 4.0, 8.0):
        cases.append(CaseSpec(f"E4_SWEEP_X{int(factor)}", "E4_SWEEP", True, factor=factor))
    cases.append(CaseSpec("COMBINED_REAL", "COMBINED_REAL", False))
    return cases


def aggregate_realizations(items: list[dict[str, Any]]) -> dict[str, Any]:
    successes = [x for x in items if x.get("success")]
    failures = [x for x in items if not x.get("success")]
    keys = sorted({k for x in successes for k in x["metrics"].keys()})
    metrics: dict[str, Any] = {}
    for key in keys:
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
        "elapsed_s_total": float(sum(x.get("elapsed_s", 0.0) for x in items)),
        "elapsed_s_median": percentile([float(x.get("elapsed_s", 0.0)) for x in items], 50),
        "failure_samples": failures[:3],
    }


def load_real_layout_target() -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    with REAL_LAYOUT_ERRORS_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["version"] == "v4-io" and row["eval_set"] == "all8":
                rows.append(row)
    if len(rows) != 8:
        raise RuntimeError(f"expected 8 real layout error rows, got {len(rows)}")
    e3d = [float(r["err_3d_mm"]) for r in rows]
    eh = [float(r["err_horizontal_mm"]) for r in rows]
    ev = [float(r["err_vertical_mm"]) for r in rows]
    return {
        "source": str(REAL_LAYOUT_ERRORS_CSV),
        "version": "v4-io",
        "eval_set": "all8",
        "total_rms_mm": float(math.sqrt(sum(x * x for x in e3d) / len(e3d))),
        "total_median_mm": percentile(e3d, 50),
        "horizontal_rms_mm": float(math.sqrt(sum(x * x for x in eh) / len(eh))),
        "horizontal_median_mm": percentile(eh, 50),
        "vertical_rms_mm": float(math.sqrt(sum(x * x for x in ev) / len(ev))),
        "vertical_median_mm": percentile(ev, 50),
    }


def metric_median(agg: dict[str, Any], case: str, key: str) -> float:
    return float(agg[case]["metrics"][key]["median"])


def build_readouts(
    agg: dict[str, Any],
    real_target: dict[str, Any],
    e4_residuals: dict[str, float],
) -> dict[str, Any]:
    baseline_total = metric_median(agg, "BASELINE", "rigid_total_rms_mm")
    baseline_ok = baseline_total < 10.0
    e3_solved_to_truth_scale = metric_median(agg, "E3", "similarity_scale")
    e3_layout_scale_vs_truth = 1.0 / e3_solved_to_truth_scale if e3_solved_to_truth_scale else float("inf")
    e3_scale_flag = abs(e3_layout_scale_vs_truth - 1.04) > 0.03
    combined_total = metric_median(agg, "COMBINED_REAL", "rigid_total_rms_mm")
    target_total = float(real_target["total_rms_mm"])
    combined_ratio = combined_total / target_total if target_total else float("nan")
    e3_total = metric_median(agg, "E3", "rigid_total_rms_mm")
    e4_total = metric_median(agg, "E4_REAL", "rigid_total_rms_mm")
    e3_scale_contrib = metric_median(agg, "E3", "scale_contribution_total_rms_mm")
    e4_scale_contrib = metric_median(agg, "E4_REAL", "scale_contribution_total_rms_mm")
    e4_addr_ratio = e3_total / e4_total if e4_total else float("inf")

    e2_curve = {
        k: metric_median(agg, f"E2_K{k}", "rigid_total_rms_mm")
        for k in (1, 2, 4, 8)
    }
    e1_total = metric_median(agg, "E1", "rigid_total_rms_mm")
    threshold = max(50.0, 2.0 * e1_total)
    breaking_k = None
    for k in (1, 2, 4, 8):
        if e2_curve[k] > threshold:
            breaking_k = k
            break
    e4_abs = [abs(v) for v in e4_residuals.values()]
    e4_sweep_magnitudes = {
        str(int(f)): {
            "median_abs_residual_mm": percentile([f * x for x in e4_abs], 50),
            "p95_abs_residual_mm": percentile([f * x for x in e4_abs], 95),
        }
        for f in (1.0, 2.0, 4.0, 8.0)
    }
    return {
        "baseline_sanity": {
            "median_rigid_total_rms_mm": baseline_total,
            "passes_lt_10mm": baseline_ok,
            "note": "Production fusion rounds fused distances to integer mm and the solver keeps soft geometry priors, so clean synthetic is expected to be near-zero, not bit-exact zero.",
        },
        "e3_validation": {
            "median_similarity_scale_solved_to_truth": e3_solved_to_truth_scale,
            "median_layout_scale_vs_truth": e3_layout_scale_vs_truth,
            "expected_layout_scale_vs_truth_near": 1.04,
            "flag_layout_scale_deviation_gt_0p03": e3_scale_flag,
            "median_rigid_total_rms_mm": e3_total,
            "median_scale_contribution_total_rms_mm": e3_scale_contrib,
        },
        "combined_real_vs_real_erlangen": {
            "combined_real_median_rigid_total_rms_mm": combined_total,
            "real_erlangen_total_rms_mm": target_total,
            "ratio_combined_to_real": combined_ratio,
            "real_target": real_target,
        },
        "verdict_number": {
            "e3_rigid_total_rms_mm": e3_total,
            "e4_real_rigid_total_rms_mm": e4_total,
            "e3_over_e4_ratio": e4_addr_ratio,
            "e3_scale_contribution_total_rms_mm": e3_scale_contrib,
            "e4_scale_contribution_total_rms_mm": e4_scale_contrib,
            "interpretation": "E3 is endpoint-delay/scale-like and CIR-blind; E4_REAL is the consistent per-link residual that CIR could address.",
        },
        "e2_readout": {
            "e1_noise_floor_rigid_total_rms_mm": e1_total,
            "breaking_threshold_mm": threshold,
            "median_rigid_total_rms_by_k": e2_curve,
            "breaking_k": breaking_k,
        },
        "e4_sweep_residual_magnitudes": e4_sweep_magnitudes,
    }


def write_markdown(
    out_path: Path,
    *,
    run_dir: Path,
    invocation: list[str],
    schema: dict[str, Any],
    readouts: dict[str, Any],
    aggregates: dict[str, Any],
) -> None:
    lines = [
        "# Phase 0 Solver Headroom Results",
        "",
        f"Run directory: `{run_dir}`",
        "",
        "## Production Invocation",
        "",
        "```bash",
        " ".join(invocation).replace(str(run_dir), "<run_dir>"),
        "```",
        "",
        "## Reference pairs_all.csv schema",
        "",
        f"- Source: `{schema['source']}`",
        f"- Columns: `{', '.join(schema['columns'])}`",
        f"- Directed convention: `{schema['directed_link_convention']}`",
        f"- Rows: `{schema['rows']}`, directed links: `{schema['directed_link_count']}`",
        "",
        "## Required Readouts",
        "",
    ]
    r = readouts
    lines += [
        f"1. BASELINE sanity: rigid total RMS median = `{r['baseline_sanity']['median_rigid_total_rms_mm']:.3f} mm`; pass_lt_10mm = `{r['baseline_sanity']['passes_lt_10mm']}`.",
        f"2. E3 validation: solved-to-truth similarity scale median = `{r['e3_validation']['median_similarity_scale_solved_to_truth']:.6f}`; layout-scale-vs-truth median = `{r['e3_validation']['median_layout_scale_vs_truth']:.6f}`; expected layout scale near `1.04`; flag = `{r['e3_validation']['flag_layout_scale_deviation_gt_0p03']}`.",
        f"3. COMBINED_REAL vs real Erlangen: combined median rigid total RMS = `{r['combined_real_vs_real_erlangen']['combined_real_median_rigid_total_rms_mm']:.3f} mm`; real target RMS = `{r['combined_real_vs_real_erlangen']['real_erlangen_total_rms_mm']:.3f} mm`; ratio = `{r['combined_real_vs_real_erlangen']['ratio_combined_to_real']:.3f}`.",
        f"4. Verdict: E3 rigid total RMS = `{r['verdict_number']['e3_rigid_total_rms_mm']:.3f} mm`; E4_REAL rigid total RMS = `{r['verdict_number']['e4_real_rigid_total_rms_mm']:.3f} mm`; E3/E4 ratio = `{r['verdict_number']['e3_over_e4_ratio']:.3f}`.",
        f"5. E2 readout: breaking_k = `{r['e2_readout']['breaking_k']}`; curve = `{r['e2_readout']['median_rigid_total_rms_by_k']}`.",
        "",
        "## Aggregate Case Summary",
        "",
        "| Case | n | Rigid total RMS median mm | Similarity total RMS median mm | Similarity scale median | Scale contrib total RMS median mm |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for case in sorted(aggregates):
        m = aggregates[case]["metrics"]
        lines.append(
            f"| {case} | {aggregates[case]['successes']} | "
            f"{m['rigid_total_rms_mm']['median']:.3f} | "
            f"{m['similarity_total_rms_mm']['median']:.3f} | "
            f"{m['similarity_scale']['median']:.6f} | "
            f"{m['scale_contribution_total_rms_mm']['median']:.3f} |"
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Phase 0 solver-headroom simulations.")
    ap.add_argument("--n", type=int, default=200, help="Monte Carlo realizations for random cases")
    ap.add_argument("--workers", type=int, default=min(8, os.cpu_count() or 1))
    ap.add_argument("--seed", type=int, default=20260615)
    ap.add_argument("--run-dir", default=None)
    ap.add_argument(
        "--repeat-deterministic",
        action="store_true",
        help="Run N identical repeats for deterministic cases instead of one representative run.",
    )
    ap.add_argument(
        "--keep-realization-files",
        action="store_true",
        help="Keep per-realization pairs/solver directories after extracting metrics.",
    )
    args = ap.parse_args()

    schema = inspect_reference_pairs()
    print("[schema] source:", schema["source"])
    print("[schema] columns:", ",".join(schema["columns"]))
    print("[schema] directed convention:", schema["directed_link_convention"])
    print(
        "[schema] rows={rows} links={directed_link_count} samples_per_link={min_samples_per_directed_link}..{max_samples_per_directed_link}".format(
            **schema
        )
    )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(args.run_dir) if args.run_dir else PHASE_DIR / "runs" / timestamp
    run_dir.mkdir(parents=True, exist_ok=True)
    invocation_example = production_invocation(run_dir / "<case>/rep_0000/pairs_all.csv", run_dir / "<case>/rep_0000/solver")
    print("[solver invocation]", " ".join(invocation_example))

    truth = load_truth()
    endpoint_delay = load_endpoint_delay()
    sigma_mm, real_residual_by_link = load_empirical_params()
    real_target = load_real_layout_target()

    tasks: list[dict[str, Any]] = []
    rng = np.random.default_rng(int(args.seed))
    for case in case_specs():
        n_case = int(args.n) if (not case.deterministic or args.repeat_deterministic) else 1
        for rep in range(n_case):
            tasks.append(
                {
                    "case": {
                        "key": case.key,
                        "family": case.family,
                        "deterministic": case.deterministic,
                        "k": case.k,
                        "factor": case.factor,
                    },
                    "rep_index": rep,
                    "seed": int(rng.integers(0, 2**31 - 1)),
                    "rep_dir": str(run_dir / "realizations" / case.key / f"rep_{rep:04d}"),
                    "truth": truth,
                    "endpoint_delay": endpoint_delay,
                    "sigma_mm": sigma_mm,
                    "real_residual_by_link": real_residual_by_link,
                }
            )

    print(f"[run] cases={len(case_specs())} tasks={len(tasks)} workers={args.workers} n_random={args.n}")
    t0 = time.time()
    realizations: list[dict[str, Any]] = []
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as ex:
        futures = [ex.submit(run_one_realization, task) for task in tasks]
        done = 0
        for fut in as_completed(futures):
            item = fut.result()
            realizations.append(item)
            done += 1
            if done == 1 or done % 25 == 0 or done == len(futures):
                ok = sum(1 for x in realizations if x.get("success"))
                print(f"[progress] {done}/{len(futures)} done ok={ok} elapsed={time.time()-t0:.1f}s", flush=True)
    wall_s = time.time() - t0

    failures = [x for x in realizations if not x.get("success")]
    if failures:
        fail_path = run_dir / "failures.json"
        fail_path.write_text(json.dumps(failures[:20], indent=2) + "\n", encoding="utf-8")
        raise SystemExit(f"{len(failures)} solver realizations failed; see {fail_path}")

    by_case: dict[str, list[dict[str, Any]]] = {}
    for item in realizations:
        by_case.setdefault(item["case"], []).append(item)
    aggregates = {case: aggregate_realizations(items) for case, items in sorted(by_case.items())}
    readouts = build_readouts(aggregates, real_target, real_residual_by_link)

    payload = {
        "run_dir": str(run_dir),
        "wall_clock_s": wall_s,
        "config": {
            "n_random": int(args.n),
            "workers": int(args.workers),
            "seed": int(args.seed),
            "samples_per_directed_link": SAMPLES_PER_DIRECTED_LINK,
            "repeat_deterministic": bool(args.repeat_deterministic),
            "keep_realization_files": bool(args.keep_realization_files),
            "deterministic_cases_executed_once_unless_repeat_deterministic": True,
        },
        "reference_pairs_schema": schema,
        "production_invocation": {
            "template": " ".join(invocation_example),
            "note": "Only --pairs-csv, --out-dir, and --verbose 0 are supplied; all solver physics/robustness parameters use production defaults from prepare_autopos_v3_box.py.",
        },
        "sources": {
            "truth": str(TRUTH_JSON),
            "delay": str(DELAY_JSON),
            "empirical_params": str(PARAMS_JSON),
            "real_layout_target": str(REAL_LAYOUT_ERRORS_CSV),
        },
        "aggregates": aggregates,
        "readouts": readouts,
        "realizations": realizations,
    }
    results_json = run_dir / "results.json"
    results_md = run_dir / "results.md"
    results_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_markdown(
        results_md,
        run_dir=run_dir,
        invocation=invocation_example,
        schema=schema,
        readouts=readouts,
        aggregates=aggregates,
    )

    if not args.keep_realization_files:
        # Keep logs/metrics in results.json; remove bulky generated pairs and solver dirs.
        shutil.rmtree(run_dir / "realizations", ignore_errors=True)

    print(f"[write] {results_json}")
    print(f"[write] {results_md}")
    print("[readout 1] BASELINE rigid total RMS median = "
          f"{readouts['baseline_sanity']['median_rigid_total_rms_mm']:.3f} mm; "
          f"pass_lt_10mm={readouts['baseline_sanity']['passes_lt_10mm']}")
    print("[readout 2] E3 solved-to-truth similarity scale median = "
          f"{readouts['e3_validation']['median_similarity_scale_solved_to_truth']:.6f}; "
          f"layout_scale_vs_truth={readouts['e3_validation']['median_layout_scale_vs_truth']:.6f}; "
          f"expected_layout_scale~1.04 flag={readouts['e3_validation']['flag_layout_scale_deviation_gt_0p03']}")
    print("[readout 3] COMBINED_REAL rigid total RMS median = "
          f"{readouts['combined_real_vs_real_erlangen']['combined_real_median_rigid_total_rms_mm']:.3f} mm; "
          f"real={readouts['combined_real_vs_real_erlangen']['real_erlangen_total_rms_mm']:.3f} mm; "
          f"ratio={readouts['combined_real_vs_real_erlangen']['ratio_combined_to_real']:.3f}")
    print("[readout 4] E3 vs E4_REAL rigid total RMS = "
          f"{readouts['verdict_number']['e3_rigid_total_rms_mm']:.3f} / "
          f"{readouts['verdict_number']['e4_real_rigid_total_rms_mm']:.3f} mm; "
          f"ratio={readouts['verdict_number']['e3_over_e4_ratio']:.3f}")
    print("[readout 5] E2 curve rigid total RMS by k = "
          f"{readouts['e2_readout']['median_rigid_total_rms_by_k']}; "
          f"breaking_k={readouts['e2_readout']['breaking_k']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
