#!/usr/bin/env python3
"""All-combination ROTO time-offset consistency appendix.

Main ROTO absolute validation uses one capture-level time offset estimated from
the production v4-io/T4 trajectory.  This appendix lets every layout/tag-solver
combination independently estimate the same capture offset, then checks whether
the discovered OptiTrack segment is consistent.

Only timing is varied here.  Spatial alignment remains anchor-locked and
reflection-allowed with no scale, exactly as in the main analysis.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

import run_roto_absolute_analysis as base


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
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def percentile(values: list[float] | np.ndarray, pct: float) -> float:
    arr = np.asarray(values, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.percentile(arr, pct))


def solve_all_tracks(args: argparse.Namespace):
    official_root = Path(args.official_root).resolve()
    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"

    anchor_truth, _static_truth, _static_meta, _corr_rows = base.load_corrected_static_truth(
        opti_dir, base.ANCHORS, base.PRIMARY_ANCHOR_TRUTH_IDS
    )
    transforms = base.load_layout_transforms(layout_base, anchor_truth)
    tr_all_by_capture = base.discover_roto_capture_files(captures_root)
    capture_ids = sorted(tr_all_by_capture)
    opti_by_capture = {
        cid: base.parse_trc_trajectories(opti_dir / f"{cid}.trc", base.OPTITRACK_MARKERS)
        for cid in capture_ids
    }

    jobs = []
    for layout in base.LAYOUT_VERSIONS:
        layout_path = layout_base / layout / "layout.json"
        for tag_method in base.TAG_METHODS:
            for capture_id, tr_all in tr_all_by_capture.items():
                for tag in base.UWB_TAGS:
                    jobs.append(
                        {
                            "layout": layout,
                            "layout_path": str(layout_path),
                            "sigma_path": str(sigma_path),
                            "tag_method": tag_method,
                            "capture_id": capture_id,
                            "tag": tag,
                            "tr_all_path": str(tr_all),
                        }
                    )

    solved: dict[tuple[str, str, str, str], base.SolvedTrack] = {}
    print(f"[offset-consistency] solving {len(jobs)} tracks with {args.workers} workers", flush=True)
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(base.solve_track_worker, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            raw = fut.result()
            layout = raw["layout"]
            xyz_autopos = np.asarray(raw["xyz_autopos_mm"], dtype=float)
            xyz_opti = base.apply_transform(xyz_autopos, transforms[layout]) if xyz_autopos.size else np.empty((0, 3))
            track = base.SolvedTrack(
                layout=layout,
                tag_method=raw["tag_method"],
                capture_id=raw["capture_id"],
                tag=raw["tag"],
                time_s=np.asarray(raw["time_s"], dtype=float),
                xyz_autopos_mm=xyz_autopos,
                xyz_opti_frame_mm=xyz_opti,
                residual_rms_mm=np.asarray(raw["residual_rms_mm"], dtype=float),
                anchors_input=np.asarray(raw["anchors_input"], dtype=float),
                anchors_used=np.asarray(raw["anchors_used"], dtype=float),
                source_tr_all=raw["source_tr_all"],
            )
            solved[(track.layout, track.tag_method, track.capture_id, track.tag)] = track
            done += 1
            if done == 1 or done % 50 == 0 or done == len(jobs):
                print(f"[offset-consistency] solved {done}/{len(jobs)} tracks", flush=True)
    return capture_ids, solved, opti_by_capture, transforms


def search_offset_job(job: dict) -> tuple[dict, list[dict]]:
    layout = job["layout"]
    tag_method = job["tag_method"]
    capture_id = job["capture_id"]
    tracks = job["tracks"]
    opti = job["opti"]
    mapping = job["mapping"]
    offset, candidates = base.estimate_capture_offset(
        capture_id,
        tracks,
        opti,
        mapping,
        coarse_step_s=float(job["coarse_step_s"]),
        refine_step_s=float(job["refine_step_s"]),
        min_points=int(job["min_points"]),
    )
    u_start = min(float(np.nanmin(t.time_s)) for t in tracks.values() if t.time_s.size)
    u_end = max(float(np.nanmax(t.time_s)) for t in tracks.values() if t.time_s.size)
    beta = float(offset.get("beta_s", float("nan")))
    row = {
        "layout": layout,
        "tag_method": tag_method,
        "capture_id": capture_id,
        "status": offset.get("status", ""),
        "beta_s": beta,
        "uwb_start_s": u_start,
        "uwb_end_s": u_end,
        "opti_match_start_s": beta + u_start if math.isfinite(beta) else float("nan"),
        "opti_match_end_s": beta + u_end if math.isfinite(beta) else float("nan"),
        "score_median_3d_mm": offset.get("score_median_3d_mm", float("nan")),
        "n_overlap": offset.get("n_overlap", 0),
        "second_candidate_score_median_3d_mm": offset.get("second_candidate_score_median_3d_mm", float("nan")),
        "second_to_best_score_ratio": offset.get("second_to_best_score_ratio", float("nan")),
    }
    cand_rows = []
    for cand in candidates:
        c = dict(cand)
        c["layout"] = layout
        c["tag_method"] = tag_method
        cand_rows.append(c)
    return row, cand_rows


def summarize(rows: list[dict]) -> list[dict]:
    ref = {
        r["capture_id"]: float(r["beta_s"])
        for r in rows
        if r["layout"] == base.PRIMARY_LAYOUT
        and r["tag_method"] == base.PRIMARY_TAG_METHOD
        and r.get("status") == "ok"
        and math.isfinite(float(r["beta_s"]))
    }
    for r in rows:
        beta = float(r["beta_s"]) if r.get("beta_s") not in ("", None) else float("nan")
        ref_beta = ref.get(r["capture_id"], float("nan"))
        delta = beta - ref_beta if math.isfinite(beta) and math.isfinite(ref_beta) else float("nan")
        r["reference_layout"] = base.PRIMARY_LAYOUT
        r["reference_tag_method"] = base.PRIMARY_TAG_METHOD
        r["reference_beta_s"] = ref_beta
        r["delta_vs_reference_s"] = delta
        r["abs_delta_vs_reference_s"] = abs(delta) if math.isfinite(delta) else float("nan")

    summary_rows: list[dict] = []
    for layout in base.LAYOUT_VERSIONS:
        for tag_method in base.TAG_METHODS:
            sub = [
                r
                for r in rows
                if r["layout"] == layout
                and r["tag_method"] == tag_method
                and r.get("status") == "ok"
                and math.isfinite(float(r["abs_delta_vs_reference_s"]))
            ]
            if not sub:
                continue
            abs_delta = np.array([float(r["abs_delta_vs_reference_s"]) for r in sub], dtype=float)
            signed_delta = np.array([float(r["delta_vs_reference_s"]) for r in sub], dtype=float)
            scores = np.array([float(r["score_median_3d_mm"]) for r in sub], dtype=float)
            starts = np.array([float(r["opti_match_start_s"]) for r in sub], dtype=float)
            outliers = [
                f"{r['capture_id']}:{float(r['delta_vs_reference_s']):+.3f}s"
                for r in sub
                if abs(float(r["delta_vs_reference_s"])) > 0.5
            ]
            summary_rows.append(
                {
                    "layout": layout,
                    "tag_method": tag_method,
                    "captures_ok": len(sub),
                    "median_abs_delta_s": percentile(abs_delta, 50),
                    "p95_abs_delta_s": percentile(abs_delta, 95),
                    "max_abs_delta_s": percentile(abs_delta, 100),
                    "median_signed_delta_s": percentile(signed_delta, 50),
                    "within_0p05s_pct": float(np.mean(abs_delta <= 0.05) * 100.0),
                    "within_0p10s_pct": float(np.mean(abs_delta <= 0.10) * 100.0),
                    "within_0p25s_pct": float(np.mean(abs_delta <= 0.25) * 100.0),
                    "within_0p50s_pct": float(np.mean(abs_delta <= 0.50) * 100.0),
                    "within_1s_pct": float(np.mean(abs_delta <= 1.00) * 100.0),
                    "score_median_3d_mm": percentile(scores, 50),
                    "opti_start_median_s": percentile(starts, 50),
                    "outliers_gt_0p5s": ";".join(outliers),
                }
            )
    return summary_rows


def plot_heatmap(rows: list[dict], out_png: Path) -> None:
    combo_labels = [f"{layout}/{method}" for layout in base.LAYOUT_VERSIONS for method in base.TAG_METHODS]
    captures = sorted({r["capture_id"] for r in rows})
    mat = np.full((len(combo_labels), len(captures)), np.nan)
    lookup = {(f"{r['layout']}/{r['tag_method']}", r["capture_id"]): float(r["delta_vs_reference_s"]) for r in rows}
    for i, combo in enumerate(combo_labels):
        for j, capture in enumerate(captures):
            mat[i, j] = lookup.get((combo, capture), np.nan)
    fig, ax = plt.subplots(figsize=(11.5, 7.0), constrained_layout=True)
    im = ax.imshow(mat, cmap="coolwarm", vmin=-1.0, vmax=1.0, aspect="auto")
    ax.set_xticks(np.arange(len(captures)))
    ax.set_xticklabels(captures, rotation=45, ha="right")
    ax.set_yticks(np.arange(len(combo_labels)))
    ax.set_yticklabels(combo_labels)
    ax.set_title("Independent ROTO offset delta vs v4-io/T4 reference")
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("delta beta (s)")
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def generate_report(path: Path, summary_rows: list[dict], rows: list[dict]) -> None:
    primary = next(
        r for r in summary_rows if r["layout"] == base.PRIMARY_LAYOUT and r["tag_method"] == base.PRIMARY_TAG_METHOD
    )
    non_ref = [r for r in summary_rows if not (r["layout"] == base.PRIMARY_LAYOUT and r["tag_method"] == base.PRIMARY_TAG_METHOD)]
    med_abs = np.array([float(r["median_abs_delta_s"]) for r in non_ref], dtype=float)
    p95_abs = np.array([float(r["p95_abs_delta_s"]) for r in non_ref], dtype=float)
    max_abs = np.array([float(r["max_abs_delta_s"]) for r in non_ref], dtype=float)
    lines = ["# ROTO Offset Consistency Across Solver Combinations\n\n"]
    lines.append("Each layout/tag-solver combination independently searched the OptiTrack time offset for every ROTO capture. Spatial alignment remained anchor-locked; only the timing offset changed.\n\n")
    lines.append("Reference for deltas: `v4-io/T4`, because it is the production pipeline used by the main ROTO absolute report.\n\n")
    if non_ref:
        lines.append(
            f"Across the 19 non-reference combinations, median absolute offset delta has median "
            f"{np.median(med_abs):.3f} s; P95 absolute offset delta has median {np.median(p95_abs):.3f} s; "
            f"worst max absolute delta across combinations is {np.max(max_abs):.3f} s.\n\n"
        )
    lines.append("## Summary By Solver\n\n")
    cols = [
        "layout",
        "tag_method",
        "median_abs_delta_s",
        "p95_abs_delta_s",
        "max_abs_delta_s",
        "within_0p10s_pct",
        "within_0p50s_pct",
        "score_median_3d_mm",
        "outliers_gt_0p5s",
    ]
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    for r in summary_rows:
        vals = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                vals.append(f"{v:.3f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |\n")
    lines.append("\n## Interpretation\n\n")
    lines.append("Small deltas mean the timing segment is stable and does not depend on which solver is used. Large deltas indicate that a solver trajectory can align to a different turn phase, usually because the circular motion is periodic and the trajectory shape is less distinctive.\n\n")
    lines.append("This appendix is not used to choose a better timing offset for the main metric. It is a robustness check for the chosen unified timing reference.\n\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="All-combo independent ROTO offset consistency check.")
    parser.add_argument("--official-root", default=str(base.OFFICIAL_ROOT))
    parser.add_argument("--out", default=str(base.ROTO_ROOT))
    parser.add_argument("--workers", type=int, default=max(1, min(10, os.cpu_count() or 1)))
    parser.add_argument("--coarse-step-s", type=float, default=0.05)
    parser.add_argument("--refine-step-s", type=float, default=0.005)
    parser.add_argument("--min-points", type=int, default=500)
    args = parser.parse_args()

    t0 = time.time()
    out_root = Path(args.out).resolve()
    tables_dir = out_root / "tables"
    figs_dir = out_root / "figs"
    reports_dir = out_root / "reports"
    capture_ids, solved, opti_by_capture, _transforms = solve_all_tracks(args)
    mapping = base.DEFAULT_MAPPING

    jobs = []
    for layout in base.LAYOUT_VERSIONS:
        for tag_method in base.TAG_METHODS:
            for capture_id in capture_ids:
                tracks = {
                    tag: solved[(layout, tag_method, capture_id, tag)]
                    for tag in base.UWB_TAGS
                    if (layout, tag_method, capture_id, tag) in solved
                }
                jobs.append(
                    {
                        "layout": layout,
                        "tag_method": tag_method,
                        "capture_id": capture_id,
                        "tracks": tracks,
                        "opti": opti_by_capture[capture_id],
                        "mapping": mapping,
                        "coarse_step_s": float(args.coarse_step_s),
                        "refine_step_s": float(args.refine_step_s),
                        "min_points": int(args.min_points),
                    }
                )
    print(f"[offset-consistency] searching offsets for {len(jobs)} combo/capture jobs", flush=True)
    rows: list[dict] = []
    candidate_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(search_offset_job, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            row, candidates = fut.result()
            rows.append(row)
            candidate_rows.extend(candidates)
            done += 1
            if done == 1 or done % 25 == 0 or done == len(jobs):
                print(f"[offset-consistency] searched {done}/{len(jobs)}", flush=True)

    rows.sort(key=lambda r: (r["layout"], r["tag_method"], r["capture_id"]))
    candidate_rows.sort(key=lambda r: (r["layout"], r["tag_method"], r["capture_id"], int(r["rank"])))
    summary_rows = summarize(rows)
    write_csv(tables_dir / "roto_offset_consistency_all_combos.csv", rows)
    write_csv(tables_dir / "roto_offset_consistency_candidates_all_combos.csv", candidate_rows)
    write_csv(tables_dir / "roto_offset_consistency_summary_by_solver.csv", summary_rows)
    plot_heatmap(rows, figs_dir / "roto_offset_consistency_heatmap.png")
    generate_report(reports_dir / "ROTO_OFFSET_CONSISTENCY_ALL_COMBOS.md", summary_rows, rows)
    base.append_run_meta(
        out_root,
        {
            "script": str(Path(__file__).resolve()),
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "purpose": "all-combo independent beta-only ROTO offset consistency",
            "reference": f"{base.PRIMARY_LAYOUT}/{base.PRIMARY_TAG_METHOD}",
            "mapping": mapping,
            "coarse_step_s": float(args.coarse_step_s),
            "refine_step_s": float(args.refine_step_s),
            "elapsed_s": time.time() - t0,
        },
    )
    print(f"[offset-consistency] done in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
