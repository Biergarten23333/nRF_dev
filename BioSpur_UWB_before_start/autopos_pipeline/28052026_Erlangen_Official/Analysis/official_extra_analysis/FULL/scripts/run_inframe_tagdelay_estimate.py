#!/usr/bin/env python3
"""Profile-sweep tag delay using the unchanged production T4 replay.

This is an oracle-free diagnostic for whether the static UWB ranges themselves
prefer the same tag-side delay as the Vicon-derived stand-in.  It intentionally
does not add a new joint solver: each grid point is replayed through the
existing C-core T4 path, then scored post-hoc by a plain sigma-weighted range
SSR on the same static frames.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


THIS = Path(__file__).resolve()
SCRIPT_DIR = THIS.parent
FULL_ROOT = THIS.parents[1]
REPO_ROOT = THIS.parents[6]
AUTOPOS_ROOT = REPO_ROOT / "autopos_pipeline"
OFFICIAL_ROOT = AUTOPOS_ROOT / "28052026_Erlangen_Official"
COMMON_DRIVER = SCRIPT_DIR / "run_commonmode_tagdelay_candidate.py"
STATIC_RAW = SCRIPT_DIR / "static_tag_raw_replay_matrix.py"
STATIC_ABS = SCRIPT_DIR / "static_tag_absolute_accuracy.py"
LAYOUT_BASE = OFFICIAL_ROOT / "solver" / "outputs" / "v1_to_v4_io_field_check"
COMMON_VERSION = "v4-io-commonmode"
COMMON_LAYOUT = LAYOUT_BASE / COMMON_VERSION / "layout.json"
SIGMA_PATH = LAYOUT_BASE / "tables" / "anchor_sigma.json"
STATIC_TABLE = LAYOUT_BASE / "tables" / "static_all_captures.csv"
CAPTURES_ROOT = OFFICIAL_ROOT / "captures" / "erlangen_20260528_optitrack"
STATIC_TAG = "BSF66F"
ORACLE_STANDIN_TAG_DELAY_MM = 91.153
FROZEN_V4IO_MEDIAN_MM = 72.69
ORACLE_STANDIN_MEDIAN_REFERENCE_MM = 58.59


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def common_helpers():
    return load_module(COMMON_DRIVER, "commonmode_tagdelay_candidate_helpers_for_inframe")


def static_raw_helpers():
    return load_module(STATIC_RAW, "static_raw_replay_helpers_for_inframe")


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


def fmt_delay(delay: float) -> str:
    return f"{delay:07.3f}".replace(".", "p")


def run_cmd_logged(cmd: list[str], log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(cmd) + "\n")
        log.flush()
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            text=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    if proc.returncode != 0:
        tail = "\n".join(log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-80:])
        raise RuntimeError(f"command failed with code {proc.returncode}: {' '.join(cmd)}\n--- log tail ---\n{tail}")


def float_grid(start: float, stop: float, step: float) -> list[float]:
    vals: list[float] = []
    n = int(math.floor((stop - start) / step + 1e-9))
    for i in range(n + 1):
        vals.append(round(start + i * step, 6))
    if vals[-1] < stop - 1e-9:
        vals.append(round(stop, 6))
    return vals


def parse_delay_list(spec: str) -> list[float]:
    out = []
    for item in spec.split(","):
        item = item.strip()
        if item:
            out.append(round(float(item), 6))
    return out


def unique_delays(vals: list[float]) -> list[float]:
    return sorted({round(float(v), 6) for v in vals})


def compute_sigma_weighted_ssr(delay_mm: float, raw_session_csv: Path) -> dict[str, Any]:
    raw_mod = static_raw_helpers()
    layout = raw_mod.load_layout_json(COMMON_LAYOUT, SIGMA_PATH)
    static_files = [Path(row["source_tr_all"]) for row in csv.DictReader(raw_session_csv.open(newline="", encoding="utf-8"))]
    allowed = set(range(8))
    ssr = 0.0
    terms = 0
    frames_input = 0
    frames_solved = 0
    session_mean_deltas = []

    external_rows = {
        row["source_tr_all"]: row
        for row in csv.DictReader(raw_session_csv.open(newline="", encoding="utf-8"))
    }

    for path in static_files:
        frames_in = raw_mod.read_tr_all_frames(path, tags={STATIC_TAG}, min_anchors=4)
        frames = raw_mod.filter_frames(frames_in, allowed, min_anchors=4)
        frames_input += len(frames)
        solver = raw_mod.TagPositionSolver(
            layout,
            raw_mod.SolverConfig(method="T4"),
            tag_delay_by_tag={STATIC_TAG: delay_mm},
        )
        pts = []
        for frame in frames:
            result = solver.solve_frame(frame)
            if result is None or result.status != "ok":
                continue
            frames_solved += 1
            p = np.array([result.x_mm, result.y_mm, result.z_mm], dtype=float)
            pts.append(p)
            for obs in frame.observations:
                if obs.anchor_id not in layout.anchors or obs.range_mm <= 0.0:
                    continue
                if result.used_by_anchor and not result.used_by_anchor.get(obs.anchor_id, True):
                    continue
                anchor = layout.anchors[obs.anchor_id]
                a = np.array([anchor.x_mm, anchor.y_mm, anchor.z_mm], dtype=float)
                sigma = max(5.0, float(anchor.sigma_mm))
                pred = float(np.linalg.norm(p - a) + anchor.d_anchor_mm + delay_mm)
                rn = (pred - float(obs.range_mm)) / sigma
                ssr += rn * rn
                terms += 1
        if pts:
            mean_p = np.nanmean(np.vstack(pts), axis=0)
            row = external_rows.get(str(path))
            if row:
                ext_p = np.array([float(row["x_mm"]), float(row["y_mm"]), float(row["z_mm"])], dtype=float)
                session_mean_deltas.append(float(np.linalg.norm(mean_p - ext_p)))

    return {
        "range_ssr": float(ssr),
        "range_ssr_terms": int(terms),
        "range_ssr_per_term": float(ssr / terms) if terms else float("nan"),
        "range_sigma_rmse": float(math.sqrt(ssr / terms)) if terms else float("nan"),
        "ssr_frames_input": int(frames_input),
        "ssr_frames_solved": int(frames_solved),
        "replay_mean_check_max_delta_mm": float(np.nanmax(session_mean_deltas)) if session_mean_deltas else float("nan"),
        "replay_mean_check_median_delta_mm": float(np.nanmedian(session_mean_deltas)) if session_mean_deltas else float("nan"),
    }


def run_delay_case(delay_mm: float, out_root_str: str) -> dict[str, Any]:
    common = common_helpers()
    out_root = Path(out_root_str)
    case = f"tag_delay_{fmt_delay(delay_mm)}"
    case_dir = out_root / case
    raw_out = case_dir / "raw_replay"
    abs_in = case_dir / "static_session_mean_for_tag_abs.csv"
    abs_out = case_dir / "static_abs"
    log_path = case_dir / "case.log"
    case_dir.mkdir(parents=True, exist_ok=True)

    run_cmd_logged(
        [
            sys.executable,
            str(STATIC_RAW),
            "--official-root",
            str(OFFICIAL_ROOT),
            "--out-dir",
            str(raw_out),
            "--layout-dir",
            str(LAYOUT_BASE),
            "--static-csv",
            str(STATIC_TABLE),
            "--layout-versions",
            COMMON_VERSION,
            "--tag-methods",
            "T4",
            "--point-estimator",
            "mean",
            "--tag-delay-by-tag",
            f"{STATIC_TAG}={delay_mm:.6f}",
        ],
        log_path,
    )
    raw_session_csv = raw_out / "tables" / "tag_raw_replay_abs_errors_per_session.csv"
    common.make_static_abs_input(raw_session_csv, abs_in, COMMON_VERSION)
    run_cmd_logged(
        [
            sys.executable,
            str(STATIC_ABS),
            "--official-root",
            str(OFFICIAL_ROOT),
            "--out-dir",
            str(abs_out),
            "--layout-dir",
            str(LAYOUT_BASE),
            "--static-csv",
            str(abs_in),
            "--eval-sets",
            "all8",
        ],
        log_path,
    )
    summary = common.read_summary(abs_out)
    reg = common.vertical_regression(abs_out)
    ssr = compute_sigma_weighted_ssr(delay_mm, raw_session_csv)
    return {
        "case": case,
        "d_tag_mm": float(delay_mm),
        "version": COMMON_VERSION,
        "tag_method": "T4",
        "point_estimator": "mean",
        **summary,
        **reg,
        **ssr,
        "raw_replay_out": str(raw_out),
        "static_abs_out": str(abs_out),
        "case_log": str(log_path),
    }


def run_delays(delays: list[float], out_root: Path, existing: dict[float, dict[str, Any]], workers: int) -> dict[float, dict[str, Any]]:
    pending = [d for d in unique_delays(delays) if round(d, 6) not in existing]
    if not pending:
        return existing
    print(f"[profile] running {len(pending)} delays with {workers} workers: {pending}", flush=True)
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_delay_case, d, str(out_root)): d for d in pending}
        for fut in as_completed(futs):
            d = futs[fut]
            row = fut.result()
            existing[round(d, 6)] = row
            print(
                f"[profile] done d_tag={d:.3f} median={row['err_3d_median_mm']:.3f} "
                f"ssr={row['range_ssr']:.3f}",
                flush=True,
            )
            write_csv(out_root / "tables" / "inframe_tagdelay_profile_partial.csv", sorted(existing.values(), key=lambda r: r["d_tag_mm"]))
    return existing


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_delay = {round(float(r["d_tag_mm"]), 6): r for r in rows}
    range_row = min(rows, key=lambda r: (float(r["range_ssr"]), abs(float(r["d_tag_mm"]))))
    vicon_row = min(rows, key=lambda r: (float(r["err_3d_median_mm"]), abs(float(r["d_tag_mm"] - ORACLE_STANDIN_TAG_DELAY_MM))))
    oracle_row = by_delay.get(round(ORACLE_STANDIN_TAG_DELAY_MM, 6))
    if oracle_row is None:
        oracle_row = min(rows, key=lambda r: abs(float(r["d_tag_mm"]) - ORACLE_STANDIN_TAG_DELAY_MM))
    min_ssr = float(range_row["range_ssr"])
    within = [r for r in rows if float(r["range_ssr"]) <= min_ssr * 1.05]
    row0 = by_delay.get(0.0)
    row150 = by_delay.get(150.0)
    delta_delay = float(range_row["d_tag_mm"] - vicon_row["d_tag_mm"])
    deploy_vicon = float(range_row["err_3d_median_mm"])
    if abs(delta_delay) <= 5.0 and deploy_vicon <= 61.0:
        verdict = (
            "RECOVERS: the range-SSR minimum lands near the Vicon-optimal delay and recovers the oracle-level static median."
        )
    else:
        verdict = (
            "DOES NOT RECOVER: the range-SSR profile prefers a different/flat delay region, so in-frame profiling does not recover the oracle static accuracy."
        )
    return {
        "d_tag_range_star_mm": float(range_row["d_tag_mm"]),
        "range_star_ssr": min_ssr,
        "range_star_ssr_per_term": float(range_row["range_ssr_per_term"]),
        "range_star_vicon_median3d_mm": deploy_vicon,
        "d_tag_vicon_star_mm": float(vicon_row["d_tag_mm"]),
        "vicon_star_median3d_mm": float(vicon_row["err_3d_median_mm"]),
        "range_minus_vicon_star_mm": delta_delay,
        "oracle_standin_delay_mm": ORACLE_STANDIN_TAG_DELAY_MM,
        "oracle_standin_vicon_median3d_mm": float(oracle_row["err_3d_median_mm"]),
        "frozen_v4io_median3d_reference_mm": FROZEN_V4IO_MEDIAN_MM,
        "oracle_standin_median3d_reference_mm": ORACLE_STANDIN_MEDIAN_REFERENCE_MM,
        "deployable_vs_frozen_delta_mm": deploy_vicon - FROZEN_V4IO_MEDIAN_MM,
        "deployable_vs_oracle_standin_delta_mm": deploy_vicon - float(oracle_row["err_3d_median_mm"]),
        "ssr_min": min_ssr,
        "ssr_at_0": float(row0["range_ssr"]) if row0 else float("nan"),
        "ssr_at_150": float(row150["range_ssr"]) if row150 else float("nan"),
        "ssr_at_0_over_min": float(row0["range_ssr"] / min_ssr) if row0 else float("nan"),
        "ssr_at_150_over_min": float(row150["range_ssr"] / min_ssr) if row150 else float("nan"),
        "ssr_within_5pct_min_delay_mm": float(min(r["d_tag_mm"] for r in within)),
        "ssr_within_5pct_max_delay_mm": float(max(r["d_tag_mm"] for r in within)),
        "ssr_within_5pct_span_mm": float(max(r["d_tag_mm"] for r in within) - min(r["d_tag_mm"] for r in within)),
        "verdict": verdict,
    }


def write_report(out_root: Path, rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    lines = [
        "# In-Frame Tag-Delay Profile Sweep",
        "",
        f"- Common-mode layout: `{COMMON_LAYOUT}`",
        f"- Output directory: `{out_root}`",
        f"- Production C-core solver unchanged: replay uses `static_tag_raw_replay_matrix.py --tag-methods T4`.",
        "- Range score is post-hoc plain sigma-weighted SSR: `sum(((||x-A_i|| + d_anchor_i + d_tag - range_i) / sigma_i)^2)`.",
        "- The C-core can additionally apply quality/residual-history penalties in low-anchor frames; this profile uses plain sigma weighting to approximate the range objective.",
        "",
        "## Sweep Table",
        "",
        "| d_tag_mm | Vicon median 3D mm | range SSR | SSR/term | sigma RMSE | vertical slope mm/m | vertical R2 |",
        "| ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['d_tag_mm']:.3f} | {row['err_3d_median_mm']:.3f} | {row['range_ssr']:.3f} | "
            f"{row['range_ssr_per_term']:.6f} | {row['range_sigma_rmse']:.3f} | "
            f"{row['signed_vertical_slope_mm_per_m']:.3f} | {row['signed_vertical_r2']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Summary",
            "",
            f"- `d_tag_range*`: `{summary['d_tag_range_star_mm']:.3f}` mm, range SSR `{summary['range_star_ssr']:.3f}`.",
            f"- `d_tag_vicon*`: `{summary['d_tag_vicon_star_mm']:.3f}` mm, Vicon median `{summary['vicon_star_median3d_mm']:.3f}` mm.",
            f"- Minima separation: `{summary['range_minus_vicon_star_mm']:+.3f}` mm.",
            f"- Honest oracle-free deployable Vicon median at `d_tag_range*`: `{summary['range_star_vicon_median3d_mm']:.3f}` mm.",
            f"- Delta vs frozen v4-io `{FROZEN_V4IO_MEDIAN_MM:.2f}` mm: `{summary['deployable_vs_frozen_delta_mm']:+.3f}` mm.",
            f"- Delta vs sampled 91.153-mm stand-in `{summary['oracle_standin_vicon_median3d_mm']:.3f}` mm: `{summary['deployable_vs_oracle_standin_delta_mm']:+.3f}` mm.",
            f"- SSR at 0 / min / 150: `{summary['ssr_at_0']:.3f}` / `{summary['ssr_min']:.3f}` / `{summary['ssr_at_150']:.3f}`.",
            f"- SSR ratios at 0 and 150 vs min: `{summary['ssr_at_0_over_min']:.6f}` / `{summary['ssr_at_150_over_min']:.6f}`.",
            f"- +5% SSR delay span: `{summary['ssr_within_5pct_min_delay_mm']:.3f}` to `{summary['ssr_within_5pct_max_delay_mm']:.3f}` mm "
            f"(`{summary['ssr_within_5pct_span_mm']:.3f}` mm wide).",
            "",
            f"**Verdict:** {summary['verdict']}",
            "",
        ]
    )
    (out_root / "INFRAME_TAGDELAY_PROFILE.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    default_out = FULL_ROOT / f"inframe_tagdelay_estimate_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
    ap = argparse.ArgumentParser(description="Profile tag-delay from range SSR using unchanged production T4 replay.")
    ap.add_argument("--out-root", default=str(default_out))
    ap.add_argument("--coarse-start", type=float, default=0.0)
    ap.add_argument("--coarse-stop", type=float, default=150.0)
    ap.add_argument("--coarse-step", type=float, default=10.0)
    ap.add_argument("--refine-half-width", type=float, default=15.0)
    ap.add_argument("--refine-step", type=float, default=2.0)
    ap.add_argument("--extra-delays", default=f"80,{ORACLE_STANDIN_TAG_DELAY_MM},95")
    ap.add_argument("--workers", type=int, default=max(2, min(8, (os.cpu_count() or 4) - 2)))
    args = ap.parse_args()

    out_root = Path(args.out_root).resolve()
    if out_root.exists():
        raise SystemExit(f"refusing to overwrite existing output dir: {out_root}")
    if not COMMON_LAYOUT.exists():
        raise FileNotFoundError(f"missing committed common-mode layout: {COMMON_LAYOUT}")
    if not SIGMA_PATH.exists():
        raise FileNotFoundError(f"missing anchor sigma table: {SIGMA_PATH}")
    out_root.mkdir(parents=True)
    (out_root / "tables").mkdir(parents=True, exist_ok=True)

    start = time.perf_counter()
    coarse = float_grid(args.coarse_start, args.coarse_stop, args.coarse_step)
    extras = parse_delay_list(args.extra_delays)
    rows_by_delay: dict[float, dict[str, Any]] = {}
    rows_by_delay = run_delays(unique_delays(coarse + extras), out_root, rows_by_delay, args.workers)

    seed_rows = list(rows_by_delay.values())
    range_seed = min(seed_rows, key=lambda r: (float(r["range_ssr"]), abs(float(r["d_tag_mm"]))))
    refine_start = max(args.coarse_start, float(range_seed["d_tag_mm"]) - args.refine_half_width)
    refine_stop = min(args.coarse_stop, float(range_seed["d_tag_mm"]) + args.refine_half_width)
    refine = float_grid(refine_start, refine_stop, args.refine_step)
    rows_by_delay = run_delays(refine, out_root, rows_by_delay, args.workers)

    rows = sorted(rows_by_delay.values(), key=lambda r: float(r["d_tag_mm"]))
    summary = summarize(rows)
    summary.update(
        {
            "script": str(THIS),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "elapsed_s": time.perf_counter() - start,
            "coarse_grid": coarse,
            "extra_delays": extras,
            "refine_grid": refine,
            "common_layout": str(COMMON_LAYOUT),
            "sigma_path": str(SIGMA_PATH),
            "static_table": str(STATIC_TABLE),
            "note": "Host-side diagnostic only. No C-core, firmware, report .tex, or layout JSON was edited.",
        }
    )
    write_csv(out_root / "tables" / "inframe_tagdelay_profile.csv", rows)
    (out_root / "tables" / "inframe_tagdelay_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    write_report(out_root, rows, summary)
    created = [
        str(out_root),
        str(out_root / "tables" / "inframe_tagdelay_profile.csv"),
        str(out_root / "tables" / "inframe_tagdelay_summary.json"),
        str(out_root / "INFRAME_TAGDELAY_PROFILE.md"),
    ]
    (out_root / "CREATED_PATHS.txt").write_text("\n".join(created) + "\n", encoding="utf-8")
    print(f"[profile] wrote {out_root / 'INFRAME_TAGDELAY_PROFILE.md'}", flush=True)
    print(f"[profile] verdict: {summary['verdict']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
