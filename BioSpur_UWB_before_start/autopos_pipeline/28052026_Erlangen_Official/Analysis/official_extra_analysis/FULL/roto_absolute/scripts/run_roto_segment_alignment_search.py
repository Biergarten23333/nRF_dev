#!/usr/bin/env python3
"""Brute-force ROTO segment alignment search.

This is a timing-only companion to `run_roto_absolute_analysis.py`.  It answers:
where does the shorter UWB ROTO capture lie inside the longer OptiTrack export?

The model is

    t_opti = alpha * t_uwb + beta

with spatial alignment still fixed only by anchors.  The output reports the
matched OptiTrack start/end time for every ROTO capture.
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
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


def percentile(values: np.ndarray, pct: float) -> float:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return float("nan")
    return float(np.percentile(values, pct))


def solve_primary_tracks(args: argparse.Namespace):
    official_root = Path(args.official_root).resolve()
    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"

    anchor_truth, _static_truth, _static_meta, _corr_rows = base.load_corrected_static_truth(
        opti_dir, base.ANCHORS, base.PRIMARY_ANCHOR_TRUTH_IDS
    )
    transforms = base.load_layout_transforms(layout_base, anchor_truth)
    transform = transforms[base.PRIMARY_LAYOUT]
    tr_all_by_capture = base.discover_roto_capture_files(captures_root)
    capture_ids = sorted(tr_all_by_capture)
    opti_by_capture = {
        cid: base.parse_trc_trajectories(opti_dir / f"{cid}.trc", base.OPTITRACK_MARKERS)
        for cid in capture_ids
    }
    jobs = []
    for capture_id, tr_path in tr_all_by_capture.items():
        for tag in base.UWB_TAGS:
            jobs.append(
                {
                    "layout": base.PRIMARY_LAYOUT,
                    "layout_path": str(layout_base / base.PRIMARY_LAYOUT / "layout.json"),
                    "sigma_path": str(sigma_path),
                    "tag_method": base.PRIMARY_TAG_METHOD,
                    "capture_id": capture_id,
                    "tag": tag,
                    "tr_all_path": str(tr_path),
                }
            )
    solved: dict[str, dict[str, base.SolvedTrack]] = {cid: {} for cid in capture_ids}
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [pool.submit(base.solve_track_worker, job) for job in jobs]
        done = 0
        for fut in as_completed(futures):
            raw = fut.result()
            xyz_autopos = np.asarray(raw["xyz_autopos_mm"], dtype=float)
            xyz_opti = base.apply_transform(xyz_autopos, transform) if xyz_autopos.size else np.empty((0, 3))
            track = base.SolvedTrack(
                layout=raw["layout"],
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
            solved[track.capture_id][track.tag] = track
            done += 1
            if done == 1 or done % 10 == 0 or done == len(jobs):
                print(f"[segment-search] solved primary tracks {done}/{len(jobs)}", flush=True)
    return capture_ids, solved, opti_by_capture


def affine_errors(
    tracks: dict[str, base.SolvedTrack],
    opti: dict[str, base.OptiTrackTrajectory],
    mapping: dict[str, str],
    alpha: float,
    beta: float,
) -> np.ndarray:
    chunks = []
    for tag, track in tracks.items():
        marker = mapping[tag]
        query = alpha * track.time_s + beta
        opti_xyz, good = base.interpolate_opti(opti[marker], query)
        finite = good & np.isfinite(track.xyz_opti_frame_mm).all(axis=1) & np.isfinite(opti_xyz).all(axis=1)
        if not np.any(finite):
            continue
        chunks.append(np.linalg.norm(track.xyz_opti_frame_mm[finite] - opti_xyz[finite], axis=1))
    if not chunks:
        return np.empty(0)
    return np.concatenate(chunks)


def score_affine(
    tracks: dict[str, base.SolvedTrack],
    opti: dict[str, base.OptiTrackTrajectory],
    mapping: dict[str, str],
    alpha: float,
    beta: float,
    min_points: int,
) -> tuple[float, float, float, int]:
    errors = affine_errors(tracks, opti, mapping, alpha, beta)
    if errors.size < min_points:
        return float("inf"), float("nan"), float("nan"), int(errors.size)
    med = percentile(errors, 50)
    p90 = percentile(errors, 90)
    p95 = percentile(errors, 95)
    # A light tail penalty keeps the optimum from using a sharp median-only
    # crossing during periodic motion.
    score = med + 0.15 * p90
    return float(score), med, p95, int(errors.size)


def bounds_for_alpha(
    tracks: dict[str, base.SolvedTrack],
    opti: dict[str, base.OptiTrackTrajectory],
    mapping: dict[str, str],
    alpha: float,
) -> tuple[float, float]:
    u_min = min(float(np.nanmin(t.time_s)) for t in tracks.values() if t.time_s.size)
    u_max = max(float(np.nanmax(t.time_s)) for t in tracks.values() if t.time_s.size)
    o_min = min(float(opti[mapping[tag]].time_s[0]) for tag in tracks)
    o_max = max(float(opti[mapping[tag]].time_s[-1]) for tag in tracks)
    return o_min - alpha * u_min, o_max - alpha * u_max


def top_candidates(rows: list[dict], n: int, min_beta_sep_s: float = 0.75, min_alpha_sep: float = 0.002) -> list[dict]:
    picked: list[dict] = []
    for row in sorted(rows, key=lambda r: float(r["score"])):
        beta = float(row["beta_s"])
        alpha = float(row["alpha"])
        if all(abs(beta - float(p["beta_s"])) >= min_beta_sep_s or abs(alpha - float(p["alpha"])) >= min_alpha_sep for p in picked):
            picked.append(row)
        if len(picked) >= n:
            break
    return picked


def search_one_capture(
    capture_id: str,
    tracks: dict[str, base.SolvedTrack],
    opti: dict[str, base.OptiTrackTrajectory],
    mapping: dict[str, str],
    args: argparse.Namespace,
) -> tuple[dict, list[dict]]:
    alpha_values = np.arange(float(args.alpha_min), float(args.alpha_max) + 0.5 * float(args.alpha_step), float(args.alpha_step))
    coarse_rows: list[dict] = []
    for alpha in alpha_values:
        b0, b1 = bounds_for_alpha(tracks, opti, mapping, float(alpha))
        betas = np.arange(b0, b1 + 0.5 * float(args.beta_step_s), float(args.beta_step_s))
        for beta in betas:
            score, med, p95, n = score_affine(tracks, opti, mapping, float(alpha), float(beta), int(args.min_points))
            if math.isfinite(score):
                coarse_rows.append(
                    {
                        "capture_id": capture_id,
                        "stage": "coarse",
                        "alpha": float(alpha),
                        "beta_s": float(beta),
                        "score": score,
                        "median_3d_mm": med,
                        "p95_3d_mm": p95,
                        "n_overlap": n,
                    }
                )
    if not coarse_rows:
        return {"capture_id": capture_id, "status": "no_valid_score"}, []

    coarse_best = min(coarse_rows, key=lambda r: float(r["score"]))
    a0 = max(float(args.alpha_min), float(coarse_best["alpha"]) - float(args.refine_alpha_span))
    a1 = min(float(args.alpha_max), float(coarse_best["alpha"]) + float(args.refine_alpha_span))
    b0 = float(coarse_best["beta_s"]) - float(args.refine_beta_span_s)
    b1 = float(coarse_best["beta_s"]) + float(args.refine_beta_span_s)
    refine_rows: list[dict] = []
    for alpha in np.arange(a0, a1 + 0.5 * float(args.refine_alpha_step), float(args.refine_alpha_step)):
        betas = np.arange(b0, b1 + 0.5 * float(args.refine_beta_step_s), float(args.refine_beta_step_s))
        for beta in betas:
            score, med, p95, n = score_affine(tracks, opti, mapping, float(alpha), float(beta), int(args.min_points))
            if math.isfinite(score):
                refine_rows.append(
                    {
                        "capture_id": capture_id,
                        "stage": "refine",
                        "alpha": float(alpha),
                        "beta_s": float(beta),
                        "score": score,
                        "median_3d_mm": med,
                        "p95_3d_mm": p95,
                        "n_overlap": n,
                    }
                )
    candidates = top_candidates(refine_rows or coarse_rows, n=8)
    best = candidates[0]
    alpha = float(best["alpha"])
    beta = float(best["beta_s"])
    u_start = min(float(np.nanmin(t.time_s)) for t in tracks.values() if t.time_s.size)
    u_end = max(float(np.nanmax(t.time_s)) for t in tracks.values() if t.time_s.size)
    o_start = alpha * u_start + beta
    o_end = alpha * u_end + beta
    row = {
        "capture_id": capture_id,
        "status": "ok",
        "alpha": alpha,
        "beta_s": beta,
        "uwb_start_s": u_start,
        "uwb_end_s": u_end,
        "uwb_duration_s": u_end - u_start,
        "opti_match_start_s": o_start,
        "opti_match_end_s": o_end,
        "opti_match_duration_s": o_end - o_start,
        "score": float(best["score"]),
        "median_3d_mm": float(best["median_3d_mm"]),
        "p95_3d_mm": float(best["p95_3d_mm"]),
        "n_overlap": int(best["n_overlap"]),
        "second_score": float(candidates[1]["score"]) if len(candidates) > 1 else float("nan"),
        "second_to_best_ratio": float(candidates[1]["score"]) / float(best["score"]) if len(candidates) > 1 and float(best["score"]) > 0 else float("nan"),
    }
    candidate_rows = []
    for rank, cand in enumerate(candidates, start=1):
        c = dict(cand)
        c["rank"] = rank
        candidate_rows.append(c)
    return row, candidate_rows


def plot_start_end(rows: list[dict], out_png: Path) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]
    if not ok:
        return
    x = np.arange(len(ok))
    labels = [r["capture_id"] for r in ok]
    starts = [float(r["opti_match_start_s"]) for r in ok]
    ends = [float(r["opti_match_end_s"]) for r in ok]
    fig, ax = plt.subplots(figsize=(10.5, 4.6), constrained_layout=True)
    ax.plot(x, starts, marker="o", label="Opti matched start")
    ax.plot(x, ends, marker="o", label="Opti matched end")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_ylabel("OptiTrack TRC time (s)")
    ax.set_title("ROTO UWB segment location inside OptiTrack export")
    ax.grid(True, alpha=0.3)
    ax.legend()
    out_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_png, dpi=220)
    plt.close(fig)


def generate_report(path: Path, rows: list[dict]) -> None:
    ok = [r for r in rows if r.get("status") == "ok"]
    starts = np.array([float(r["opti_match_start_s"]) for r in ok], dtype=float)
    ends = np.array([float(r["opti_match_end_s"]) for r in ok], dtype=float)
    alphas = np.array([float(r["alpha"]) for r in ok], dtype=float)
    scores = np.array([float(r["median_3d_mm"]) for r in ok], dtype=float)
    lines = ["# ROTO Segment Alignment Search\n\n"]
    lines.append("Model: `t_opti = alpha * t_uwb + beta`. Spatial coordinates are already anchor-locked to OptiTrack; tag truth is not used for spatial fitting.\n\n")
    if ok:
        lines.append(
            f"Solved {len(ok)} captures. OptiTrack matched start median/range: "
            f"{np.median(starts):.3f}s / {np.min(starts):.3f}--{np.max(starts):.3f}s. "
            f"Matched end median/range: {np.median(ends):.3f}s / {np.min(ends):.3f}--{np.max(ends):.3f}s.\n\n"
        )
        lines.append(
            f"Alpha median/range: {np.median(alphas):.6f} / {np.min(alphas):.6f}--{np.max(alphas):.6f}. "
            f"Median alignment 3D score: {np.median(scores):.1f} mm.\n\n"
        )
    cols = ["capture_id", "alpha", "beta_s", "uwb_start_s", "uwb_end_s", "opti_match_start_s", "opti_match_end_s", "median_3d_mm", "p95_3d_mm", "second_to_best_ratio"]
    lines.append("| " + " | ".join(cols) + " |\n")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |\n")
    for r in rows:
        vals = []
        for c in cols:
            v = r.get(c, "")
            if isinstance(v, float):
                vals.append(f"{v:.3f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Brute-force affine ROTO segment alignment search.")
    parser.add_argument("--official-root", default=str(base.OFFICIAL_ROOT))
    parser.add_argument("--out", default=str(base.ROTO_ROOT))
    parser.add_argument("--workers", type=int, default=max(1, min(10, __import__("os").cpu_count() or 1)))
    parser.add_argument("--alpha-min", type=float, default=0.98)
    parser.add_argument("--alpha-max", type=float, default=1.02)
    parser.add_argument("--alpha-step", type=float, default=0.001)
    parser.add_argument("--beta-step-s", type=float, default=0.10)
    parser.add_argument("--refine-alpha-span", type=float, default=0.003)
    parser.add_argument("--refine-alpha-step", type=float, default=0.0002)
    parser.add_argument("--refine-beta-span-s", type=float, default=0.60)
    parser.add_argument("--refine-beta-step-s", type=float, default=0.005)
    parser.add_argument("--min-points", type=int, default=500)
    args = parser.parse_args()

    t0 = time.time()
    out_root = Path(args.out).resolve()
    tables_dir = out_root / "tables"
    figs_dir = out_root / "figs"
    reports_dir = out_root / "reports"
    print("[segment-search] solving primary tracks", flush=True)
    capture_ids, solved, opti = solve_primary_tracks(args)

    print("[segment-search] deciding mapping using existing default/swapped test", flush=True)
    mapping, mapping_rows = base.build_mapping_decision(
        solved,
        opti,
        coarse_step_s=0.10,
        min_points=int(args.min_points),
    )
    write_csv(tables_dir / "roto_segment_mapping_decision.csv", mapping_rows)
    print(f"[segment-search] mapping {mapping}", flush=True)

    rows: list[dict] = []
    candidate_rows: list[dict] = []
    with ProcessPoolExecutor(max_workers=max(1, int(args.workers))) as pool:
        futures = [
            pool.submit(search_one_capture, cid, solved[cid], opti[cid], mapping, args)
            for cid in capture_ids
        ]
        done = 0
        for fut in as_completed(futures):
            row, cands = fut.result()
            rows.append(row)
            candidate_rows.extend(cands)
            done += 1
            print(f"[segment-search] {row.get('capture_id')} done {done}/{len(capture_ids)}", flush=True)
    rows.sort(key=lambda r: r["capture_id"])
    candidate_rows.sort(key=lambda r: (r["capture_id"], int(r["rank"])))

    write_csv(tables_dir / "roto_segment_alignment_affine_v4io_T4.csv", rows)
    write_csv(tables_dir / "roto_segment_alignment_candidates_affine_v4io_T4.csv", candidate_rows)
    plot_start_end(rows, figs_dir / "roto_segment_alignment_start_end_v4io_T4.png")
    generate_report(reports_dir / "ROTO_SEGMENT_ALIGNMENT_SEARCH.md", rows)
    base.append_run_meta(
        out_root,
        {
            "script": str(Path(__file__).resolve()),
            "timestamp_utc": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "purpose": "brute-force affine time segment search for ROTO UWB inside longer OptiTrack exports",
            "time_model": "t_opti = alpha * t_uwb + beta",
            "mapping": mapping,
            "alpha_min": args.alpha_min,
            "alpha_max": args.alpha_max,
            "alpha_step": args.alpha_step,
            "beta_step_s": args.beta_step_s,
            "elapsed_s": time.time() - t0,
        },
    )
    print(f"[segment-search] done in {time.time() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
