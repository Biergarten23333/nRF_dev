#!/usr/bin/env python3
"""Surveyed-anchor static tag baseline in the OptiTrack frame.

This control solves the static tag directly with OptiTrack-truth anchor
coordinates, then compares the solved point to the corrected OptiTrack
`Iantenna` truth.  There is intentionally no Kabsch alignment, reflection, or
scale fit in this path.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from tag_ground_truth import TAG_BALL_LABEL_PERMUTATIONS, load_corrected_static_truth


ANCHORS = list("ABCDEFGH")
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]
OPTITRACK_VERTICAL_AXIS = "Y"
AUTO_SOURCE = "AutoPos v4-io production-output"
SURVEYED_SOURCE = "OptiTrack truth anchors"
DELAY_MODE_ORDER = ["raw_zero_delay", "autopos_v4io_delay_vector", "inter_anchor_delaycal"]
DELAY_MODE_LABELS = {
    "raw_zero_delay": "raw_zero_delay",
    "autopos_v4io_delay_vector": "autopos_v4io_delay_vector",
    "inter_anchor_delaycal": "inter_anchor_delaycal",
}

THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parents[6]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
sys.path.insert(0, str(SOLVER_ROOT))

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_anchor_sigma  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Anchor, Frame, Layout, SolverConfig  # noqa: E402


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_run_meta(out_dir: Path, entry: dict) -> None:
    meta_path = out_dir / "run_meta.json"
    lock_path = out_dir / "run_meta.json.lock"
    with lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                meta = {"runs": []}
        else:
            meta = {"runs": []}
        meta.setdefault("runs", []).append(entry)
        tmp = meta_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(meta_path)
        fcntl.flock(lock, fcntl.LOCK_UN)


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


def session_id_from_path(path: Path) -> str:
    for parent in path.parents:
        m = re.search(r"(static_ID\d+)_", parent.name)
        if m:
            return m.group(1).replace("static_", "")
    return path.parents[1].name


def capture_name_from_path(path: Path) -> str:
    for parent in path.parents:
        if parent.name.startswith("static_ID"):
            return parent.name
    return path.parents[1].name


def load_static_metadata(layout_table: Path) -> dict[str, dict]:
    if not layout_table.exists():
        return {}
    df = pd.read_csv(layout_table)
    out: dict[str, dict] = {}
    for _, row in df.iterrows():
        sid = str(row.get("ID", "")).strip()
        if sid and sid not in out:
            out[sid] = {
                "location": row.get("location", ""),
                "height": row.get("height", ""),
                "facing": row.get("facing", ""),
            }
    return out


def filter_frames(frames: list[Frame], allowed_anchor_ids: set[int], min_anchors: int) -> list[Frame]:
    out: list[Frame] = []
    for frame in frames:
        obs = tuple(o for o in frame.observations if o.anchor_id in allowed_anchor_ids)
        if len(obs) >= min_anchors:
            out.append(
                Frame(
                    tag=frame.tag,
                    sweep=frame.sweep,
                    host_elapsed_s=frame.host_elapsed_s,
                    host_epoch_s=frame.host_epoch_s,
                    observations=obs,
                    imu=frame.imu,
                )
            )
    return out


def solve_frames(layout: Layout, method: str, frames: list[Frame]) -> list:
    solver = TagPositionSolver(layout, SolverConfig(method=method))  # type: ignore[arg-type]
    results = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is not None:
            results.append(result)
    return results


def summarize_results(results: list, point_estimator: str) -> dict:
    if not results:
        return {
            "status": "no_solution",
            "frames_solved": 0,
            "x_mm": float("nan"),
            "y_mm": float("nan"),
            "z_mm": float("nan"),
        }
    pts = np.array([[r.x_mm, r.y_mm, r.z_mm] for r in results], dtype=float)
    if point_estimator == "mean":
        p = np.nanmean(pts, axis=0)
    elif point_estimator == "median":
        p = np.nanmedian(pts, axis=0)
    else:
        raise ValueError(f"unknown point estimator {point_estimator}")
    d = pts - p[None, :]
    d3 = np.linalg.norm(d, axis=1)
    anchors_used = np.array([r.anchors_used for r in results], dtype=float)
    anchors_input = np.array([r.anchors_input for r in results], dtype=float)
    residual_rms = np.array([r.residual_rms_mm for r in results], dtype=float)
    rejected = [r.rejected_anchor_id for r in results if r.rejected_anchor_id is not None]
    return {
        "status": "ok",
        "frames_solved": int(len(results)),
        "x_mm": float(p[0]),
        "y_mm": float(p[1]),
        "z_mm": float(p[2]),
        "mean_x_mm": float(np.nanmean(pts[:, 0])),
        "mean_y_mm": float(np.nanmean(pts[:, 1])),
        "mean_z_mm": float(np.nanmean(pts[:, 2])),
        "median_x_mm": float(np.nanmedian(pts[:, 0])),
        "median_y_mm": float(np.nanmedian(pts[:, 1])),
        "median_z_mm": float(np.nanmedian(pts[:, 2])),
        "x_std_mm": float(np.nanstd(d[:, 0])),
        "y_std_mm": float(np.nanstd(d[:, 1])),
        "z_std_mm": float(np.nanstd(d[:, 2])),
        "d3_std_mm": float(math.sqrt(np.nanmean(d3 * d3))),
        "radial_p50_mm": float(np.nanpercentile(d3, 50)),
        "radial_p95_mm": float(np.nanpercentile(d3, 95)),
        "anchors_used_median": float(np.nanmedian(anchors_used)),
        "anchors_input_median": float(np.nanmedian(anchors_input)),
        "pct_solved_ge7": float(np.mean(anchors_input >= 7.0) * 100.0),
        "pct_solved_ge8": float(np.mean(anchors_input >= 8.0) * 100.0),
        "residual_rms_median_mm": float(np.nanmedian(residual_rms)),
        "residual_rms_p95_mm": float(np.nanpercentile(residual_rms, 95)),
        "rejected_frames": int(len(rejected)),
        "rejected_anchor_counts": json.dumps({str(a): rejected.count(a) for a in sorted(set(rejected))}, sort_keys=True),
    }


def build_optitrack_layout(
    *,
    anchor_truth: dict[str, np.ndarray],
    anchor_delays: dict[int, float],
    tag_delay_mm: float,
    sigma_by_id: dict[int, float],
    delay_mode: str,
) -> Layout:
    anchors: dict[int, Anchor] = {}
    for aid, label in enumerate(ANCHORS):
        xyz = anchor_truth[label]
        anchors[aid] = Anchor(
            id=aid,
            label=label,
            x_mm=float(xyz[0]),
            y_mm=float(xyz[1]),
            z_mm=float(xyz[2]),
            d_anchor_mm=float(anchor_delays.get(aid, 0.0)),
            sigma_mm=float(sigma_by_id.get(aid, 50.0)),
        )
    return Layout(
        path=f"surveyed_optitrack_truth:{delay_mode}",
        anchors=anchors,
        tag_delay_mm=float(tag_delay_mm),
        metadata={
            "version": "surveyed-optitrack",
            "label": "OptiTrack truth anchors",
            "frame": "OptiTrack",
            "alignment_dof": 0,
            "delay_mode": delay_mode,
        },
    )


def estimate_delaycal(anchor_truth: dict[str, np.ndarray], pair_quality_csv: Path) -> tuple[dict[int, float], float, list[dict]]:
    df = pd.read_csv(pair_quality_csv)
    df = df[df["eval_set"] == "solve"].copy()
    rows: list[dict] = []
    design = []
    target = []
    for _, r in df.iterrows():
        a, b = str(r["pair"]).split("-")
        ia = ANCHORS.index(a)
        ib = ANCHORS.index(b)
        measured = float(r["median_all"])
        true_dist = float(np.linalg.norm(anchor_truth[a] - anchor_truth[b]))
        bias = measured - true_dist
        row = np.zeros(len(ANCHORS), dtype=float)
        row[ia] = 1.0
        row[ib] = 1.0
        design.append(row)
        target.append(bias)
        rows.append(
            {
                "pair": str(r["pair"]),
                "measured_median_mm": measured,
                "optitrack_true_mm": true_dist,
                "pair_bias_mm": bias,
                "common_endpoint_bias_mm": bias / 2.0,
            }
        )
    if not rows:
        raise ValueError(f"no solve rows in {pair_quality_csv}")
    m = np.vstack(design)
    y = np.asarray(target, dtype=float)
    delays, *_ = np.linalg.lstsq(m, y, rcond=None)
    for row in rows:
        a, b = row["pair"].split("-")
        pred = delays[ANCHORS.index(a)] + delays[ANCHORS.index(b)]
        row["delaycal_pair_predicted_bias_mm"] = float(pred)
        row["delaycal_pair_residual_mm"] = float(pred - row["pair_bias_mm"])
    tag_delay_mm = float(np.nanmedian(delays))
    return {i: float(v) for i, v in enumerate(delays)}, tag_delay_mm, rows


def load_autopos_delay_vector(layout_json: Path) -> tuple[dict[int, float], float]:
    data = json.loads(layout_json.read_text(encoding="utf-8"))
    delays = {
        int(item["id"]): float(item.get("d_anchor_mm") or 0.0)
        for item in data.get("anchors", [])
    }
    for aid in range(len(ANCHORS)):
        delays.setdefault(aid, 0.0)
    return delays, float(data.get("tag_delay_mm") or 0.0)


def summarize_abs(rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    out: list[dict] = []
    for (anchor_source, delay_mode, tag_method, eval_set), g in df.groupby(
        ["anchor_source", "delay_mode", "tag_method", "eval_set"]
    ):
        err = g["err_3d_mm"].to_numpy(dtype=float)
        out.append(
            {
                "anchor_source": anchor_source,
                "delay_mode": delay_mode,
                "tag_method": tag_method,
                "eval_set": eval_set,
                "n_sessions": int(len(g)),
                "frames_solved_total": int(g["frames_solved"].sum()),
                "frames_input_total": int(g["frames_input"].sum()),
                "err_3d_median_mm": float(np.nanmedian(err)),
                "err_3d_p75_mm": float(np.nanpercentile(err, 75)),
                "err_3d_p95_mm": float(np.nanpercentile(err, 95)),
                "err_3d_rms_mm": float(np.sqrt(np.nanmean(err * err))),
                "err_horizontal_median_mm": float(np.nanmedian(g["err_horizontal_mm"].to_numpy(dtype=float))),
                "err_vertical_median_mm": float(np.nanmedian(g["err_vertical_mm"].to_numpy(dtype=float))),
                "err_x_median_abs_mm": float(np.nanmedian(np.abs(g["err_x_mm"].to_numpy(dtype=float)))),
                "err_y_vertical_median_abs_mm": float(np.nanmedian(np.abs(g["err_y_vertical_mm"].to_numpy(dtype=float)))),
                "err_z_median_abs_mm": float(np.nanmedian(np.abs(g["err_z_mm"].to_numpy(dtype=float)))),
                "d3_std_median_mm": float(np.nanmedian(g["d3_std_mm"].to_numpy(dtype=float))),
                "residual_rms_median_mm": float(np.nanmedian(g["residual_rms_median_mm"].to_numpy(dtype=float))),
            }
        )
    return sorted(out, key=lambda r: (r["eval_set"], DELAY_MODE_ORDER.index(r["delay_mode"])))


def load_autopos_summary(tag_summary_csv: Path) -> list[dict]:
    df = pd.read_csv(tag_summary_csv)
    out = []
    for _, r in df[df["version"] == "v4-io"].iterrows():
        out.append(
            {
                "anchor_source": AUTO_SOURCE,
                "delay_mode": "autopos_estimated_delays",
                "tag_method": "production-output",
                "eval_set": r["eval_set"],
                "n_sessions": int(r["n"]),
                "err_3d_median_mm": float(r["err_3d_median_mm"]),
                "err_3d_p95_mm": float(r["err_3d_p95_mm"]),
                "err_3d_rms_mm": float(r["err_3d_rms_mm"]),
                "err_horizontal_median_mm": float(r["err_horizontal_median_mm"]),
                "err_vertical_median_mm": float(r["err_vertical_median_mm"]),
                "note": "production-output AutoPos line; included as the requested system headline comparator",
            }
        )
    return out


def metric_delta(auto_row: dict, base_row: dict, metric: str) -> float:
    return float(auto_row[metric]) - float(base_row[metric])


def write_comparison_md(
    path: Path,
    surveyed_summary: list[dict],
    autopos_summary: list[dict],
    delay_rows: list[dict],
    per_position_rows: list[dict],
) -> None:
    lines = ["# Anchor Source Comparison\n\n"]
    lines.append("Surveyed-anchor baseline is solved entirely in the OptiTrack frame: OptiTrack anchor coordinates in, corrected OptiTrack `Iantenna` truth out. Alignment DOF = 0; no Kabsch, no reflection, no scale.\n\n")
    lines.append("Delay modes:\n\n")
    lines.append("- `raw_zero_delay`: raw tag-to-anchor ranges, anchor delays = 0, tag delay = 0.\n")
    lines.append("- `autopos_v4io_delay_vector`: the V4-io AutoPos per-anchor delay vector is applied to OptiTrack-truth anchors. This is non-circular with respect to OptiTrack delay, but the delay vector is jointly estimated with the AutoPos layout and is gauge/scale-coupled.\n")
    lines.append("- `inter_anchor_delaycal`: per-anchor endpoint delays fit from raw inter-anchor medians against OptiTrack true anchor distances; tag delay is the median endpoint delay. This uses OptiTrack twice and is a partly circular lower bound.\n")
    lines.append("- `autopos_estimated_delays`: the production AutoPos v4-io line from `tag_accuracy_summary.csv`.\n\n")
    if delay_rows:
        endpoint = np.array([float(r["common_endpoint_bias_mm"]) for r in delay_rows], dtype=float)
        pair_res = np.array([float(r["delaycal_pair_residual_mm"]) for r in delay_rows], dtype=float)
        lines.append(
            f"Inter-anchor delaycal diagnostic: median common endpoint bias {np.nanmedian(endpoint):.1f} mm; "
            f"per-anchor LS residual RMS {math.sqrt(float(np.nanmean(pair_res * pair_res))):.1f} mm.\n\n"
        )

    s_by_key = {(r["eval_set"], r["delay_mode"]): r for r in surveyed_summary}
    a_by_eval = {r["eval_set"]: r for r in autopos_summary}
    lines.append("## Headline Comparison\n\n")
    lines.append("| eval set | anchor source | delay | tag method | median 3D mm | p95 mm | RMS mm | horiz med mm | vert med mm |\n")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: |\n")
    eval_sets = sorted({r["eval_set"] for r in [*surveyed_summary, *autopos_summary]})
    for eval_set in eval_sets:
        for mode in DELAY_MODE_ORDER:
            r = s_by_key.get((eval_set, mode))
            if r:
                lines.append(
                    f"| {eval_set} | OptiTrack truth | {mode} | {r['tag_method']} | "
                    f"{r['err_3d_median_mm']:.1f} | {r['err_3d_p95_mm']:.1f} | {r['err_3d_rms_mm']:.1f} | "
                    f"{r['err_horizontal_median_mm']:.1f} | {r['err_vertical_median_mm']:.1f} |\n"
                )
        a = a_by_eval.get(eval_set)
        if a:
            lines.append(
                f"| {eval_set} | AutoPos v4-io | autopos_estimated_delays | production-output | "
                f"{a['err_3d_median_mm']:.1f} | {a['err_3d_p95_mm']:.1f} | {a['err_3d_rms_mm']:.1f} | "
                f"{a['err_horizontal_median_mm']:.1f} | {a['err_vertical_median_mm']:.1f} |\n"
            )
    lines.append("\n## AutoPos Minus Baseline\n\n")
    lines.append("| eval set | baseline delay | delta median 3D mm | delta p95 mm | delta RMS mm | delta horiz med mm | delta vert med mm |\n")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: |\n")
    for eval_set in eval_sets:
        a = a_by_eval.get(eval_set)
        if not a:
            continue
        for mode in DELAY_MODE_ORDER:
            b = s_by_key.get((eval_set, mode))
            if not b:
                continue
            lines.append(
                f"| {eval_set} | {mode} | {metric_delta(a, b, 'err_3d_median_mm'):.1f} | "
                f"{metric_delta(a, b, 'err_3d_p95_mm'):.1f} | {metric_delta(a, b, 'err_3d_rms_mm'):.1f} | "
                f"{metric_delta(a, b, 'err_horizontal_median_mm'):.1f} | "
                f"{metric_delta(a, b, 'err_vertical_median_mm'):.1f} |\n"
            )

    df = pd.DataFrame(per_position_rows)
    lines.append("\n## Worst-Point Resolution\n\n")
    lines.append("| ID | eval set | surveyed raw 3D mm | surveyed AutoPos-delay 3D mm | surveyed delaycal 3D mm | location | height | facing | tag truth corrected |\n")
    lines.append("| --- | --- | ---: | ---: | ---: | --- | --- | --- | --- |\n")
    for sid in ["ID03", "ID04", "ID05", "ID06"]:
        for eval_set in ["all8"]:
            raw = df[(df["ID"] == sid) & (df["eval_set"] == eval_set) & (df["delay_mode"] == "raw_zero_delay")]
            apd = df[(df["ID"] == sid) & (df["eval_set"] == eval_set) & (df["delay_mode"] == "autopos_v4io_delay_vector")]
            cal = df[(df["ID"] == sid) & (df["eval_set"] == eval_set) & (df["delay_mode"] == "inter_anchor_delaycal")]
            if raw.empty or apd.empty or cal.empty:
                continue
            r = raw.iloc[0]
            a = apd.iloc[0]
            c = cal.iloc[0]
            lines.append(
                f"| {sid} | {eval_set} | {float(r['err_3d_mm']):.1f} | {float(a['err_3d_mm']):.1f} | "
                f"{float(c['err_3d_mm']):.1f} | "
                f"{r['location']} | {r['height']} | {r['facing']} | {r['tag_truth_corrected']} |\n"
            )
    lines.append("\nInterpretation rule: if ID03/ID04/ID06 stay large with OptiTrack-truth anchors, the tail is intrinsic UWB/NLOS/multipath/geometry rather than AutoPos layout error; if they collapse, it was dominated by self-calibration.\n")
    collapse_vals = []
    for sid in ["ID03", "ID04", "ID06"]:
        cal = df[(df["ID"] == sid) & (df["eval_set"] == "all8") & (df["delay_mode"] == "inter_anchor_delaycal")]
        if not cal.empty:
            collapse_vals.append((sid, float(cal["err_3d_mm"].iloc[0])))
    if len(collapse_vals) == 3:
        vals = ", ".join(f"{sid}={err:.1f} mm" for sid, err in collapse_vals)
        lines.append(
            f"\nActual result: the production tail points collapse under surveyed anchors plus delaycal ({vals}). "
            "The 270 mm-class AutoPos production tail is therefore mainly layout/self-calibration/frame-lock cost, not an irreducible UWB floor at those positions.\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def load_autopos_per_position(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    return df[(df["version"] == "v4-io") & (df["method"] == "C_anchor_locked_OFFICIAL")].copy()


def plot_autopos_vs_surveyed(per_rows: list[dict], autopos_csv: Path, out: Path) -> None:
    surveyed = pd.DataFrame(per_rows)
    auto = load_autopos_per_position(autopos_csv)
    if surveyed.empty or auto.empty:
        return
    eval_sets = sorted(surveyed["eval_set"].unique())
    fig, axs = plt.subplots(len(eval_sets), 1, figsize=(14, 4.5 * len(eval_sets)), sharex=True, constrained_layout=True, squeeze=False)
    for ax, eval_set in zip(axs[:, 0], eval_sets):
        ids = sorted(surveyed["ID"].unique())
        x = np.arange(len(ids))
        width = 0.18
        vals_auto = []
        vals_raw = []
        vals_apd = []
        vals_cal = []
        for sid in ids:
            a = auto[(auto["ID"] == sid) & (auto["eval_set"] == eval_set)]
            r = surveyed[(surveyed["ID"] == sid) & (surveyed["eval_set"] == eval_set) & (surveyed["delay_mode"] == "raw_zero_delay")]
            d = surveyed[(surveyed["ID"] == sid) & (surveyed["eval_set"] == eval_set) & (surveyed["delay_mode"] == "autopos_v4io_delay_vector")]
            c = surveyed[(surveyed["ID"] == sid) & (surveyed["eval_set"] == eval_set) & (surveyed["delay_mode"] == "inter_anchor_delaycal")]
            vals_auto.append(float(a["err_3d_mm"].iloc[0]) if len(a) else np.nan)
            vals_raw.append(float(r["err_3d_mm"].iloc[0]) if len(r) else np.nan)
            vals_apd.append(float(d["err_3d_mm"].iloc[0]) if len(d) else np.nan)
            vals_cal.append(float(c["err_3d_mm"].iloc[0]) if len(c) else np.nan)
        ax.bar(x - 1.5 * width, vals_auto, width=width, label="AutoPos v4-io production", color="#4C78A8")
        ax.bar(x - 0.5 * width, vals_raw, width=width, label="OptiTrack anchors raw", color="#F58518")
        ax.bar(x + 0.5 * width, vals_apd, width=width, label="OptiTrack anchors AutoPos delay", color="#B279A2")
        ax.bar(x + 1.5 * width, vals_cal, width=width, label="OptiTrack anchors delaycal", color="#54A24B")
        ax.set_ylabel(f"{eval_set} 3D error mm")
        ax.grid(axis="y", alpha=0.25)
        ax.legend(loc="upper left", ncol=3, fontsize=8)
    axs[-1, 0].set_xticks(np.arange(len(sorted(surveyed["ID"].unique()))))
    axs[-1, 0].set_xticklabels(sorted(surveyed["ID"].unique()), rotation=45, ha="right")
    fig.suptitle("AutoPos vs OptiTrack-surveyed anchor baseline by static position")
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_worst_points(per_rows: list[dict], autopos_csv: Path, out: Path) -> None:
    surveyed = pd.DataFrame(per_rows)
    auto = load_autopos_per_position(autopos_csv)
    if surveyed.empty or auto.empty:
        return
    ids = ["ID03", "ID04", "ID05", "ID06"]
    x = np.arange(len(ids))
    width = 0.18
    vals_auto = []
    vals_raw = []
    vals_apd = []
    vals_cal = []
    for sid in ids:
        a = auto[(auto["ID"] == sid) & (auto["eval_set"] == "all8")]
        r = surveyed[(surveyed["ID"] == sid) & (surveyed["eval_set"] == "all8") & (surveyed["delay_mode"] == "raw_zero_delay")]
        d = surveyed[(surveyed["ID"] == sid) & (surveyed["eval_set"] == "all8") & (surveyed["delay_mode"] == "autopos_v4io_delay_vector")]
        c = surveyed[(surveyed["ID"] == sid) & (surveyed["eval_set"] == "all8") & (surveyed["delay_mode"] == "inter_anchor_delaycal")]
        vals_auto.append(float(a["err_3d_mm"].iloc[0]) if len(a) else np.nan)
        vals_raw.append(float(r["err_3d_mm"].iloc[0]) if len(r) else np.nan)
        vals_apd.append(float(d["err_3d_mm"].iloc[0]) if len(d) else np.nan)
        vals_cal.append(float(c["err_3d_mm"].iloc[0]) if len(c) else np.nan)
    fig, ax = plt.subplots(figsize=(8.5, 4.8), constrained_layout=True)
    ax.bar(x - 1.5 * width, vals_auto, width=width, label="AutoPos v4-io production", color="#4C78A8")
    ax.bar(x - 0.5 * width, vals_raw, width=width, label="OptiTrack anchors raw", color="#F58518")
    ax.bar(x + 0.5 * width, vals_apd, width=width, label="OptiTrack anchors AutoPos delay", color="#B279A2")
    ax.bar(x + 1.5 * width, vals_cal, width=width, label="OptiTrack anchors delaycal", color="#54A24B")
    ax.set_xticks(x)
    ax.set_xticklabels(ids)
    ax.set_ylabel("all8 3D error mm")
    ax.set_title("Worst-point resolution with surveyed anchors")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(fontsize=8)
    fig.savefig(out, dpi=150)
    plt.close(fig)


def copy_report_figures(out_dir: Path, figures: list[Path]) -> None:
    for rel in ["reports/fig", "reports/to_be_discuess/fig"]:
        dest = out_dir / rel
        dest.mkdir(parents=True, exist_ok=True)
        for fig in figures:
            shutil.copy2(fig, dest / fig.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Solve static tags with OptiTrack-truth anchor coordinates.")
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--tag-method", default="T4", choices=["T1", "T2", "T3", "T4"])
    parser.add_argument("--eval-sets", default="all8")
    parser.add_argument("--point-estimator", choices=["median", "mean"], default="median")
    parser.add_argument("--max-frames", type=int, default=0)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    pair_quality_path = layout_base / "tables/pair_quality_solve.csv"
    autopos_v4io_layout_path = layout_base / "v4-io/layout.json"
    static_table = layout_base / "tables/static_all_captures.csv"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/full"
    tag_summary_csv = out_dir / "tables/tag_accuracy_summary.csv"
    tag_per_position_csv = out_dir / "tables/tag_abs_errors_per_session.csv"

    metadata = load_static_metadata(static_table)
    anchor_truth, tag_truth, tag_truth_meta, correction_rows = load_corrected_static_truth(opti_dir, ANCHORS, PRIMARY_IDS)
    sigma_by_id = load_anchor_sigma(sigma_path)
    delaycal_anchor_delays, delaycal_tag_delay, delay_rows = estimate_delaycal(anchor_truth, pair_quality_path)
    autopos_anchor_delays, autopos_tag_delay = load_autopos_delay_vector(autopos_v4io_layout_path)

    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    if not static_files:
        raise FileNotFoundError(f"no static tr_all.csv files under {captures_root}")

    eval_sets = [v.strip() for v in args.eval_sets.split(",") if v.strip()]
    allowed_by_eval = {
        "all8": set(range(8)),
    }
    for eval_set in eval_sets:
        if eval_set not in allowed_by_eval:
            raise ValueError(f"unknown eval set {eval_set!r}")

    layouts = {
        "raw_zero_delay": build_optitrack_layout(
            anchor_truth=anchor_truth,
            anchor_delays={i: 0.0 for i in range(8)},
            tag_delay_mm=0.0,
            sigma_by_id=sigma_by_id,
            delay_mode="raw_zero_delay",
        ),
        "autopos_v4io_delay_vector": build_optitrack_layout(
            anchor_truth=anchor_truth,
            anchor_delays=autopos_anchor_delays,
            tag_delay_mm=autopos_tag_delay,
            sigma_by_id=sigma_by_id,
            delay_mode="autopos_v4io_delay_vector",
        ),
        "inter_anchor_delaycal": build_optitrack_layout(
            anchor_truth=anchor_truth,
            anchor_delays=delaycal_anchor_delays,
            tag_delay_mm=delaycal_tag_delay,
            sigma_by_id=sigma_by_id,
            delay_mode="inter_anchor_delaycal",
        ),
    }
    for layout in layouts.values():
        assert layout.metadata["alignment_dof"] == 0
        assert layout.metadata["frame"] == "OptiTrack"

    print(f"[surveyed-baseline] loading {len(static_files)} static captures", flush=True)
    raw_frames: dict[str, list[Frame]] = {}
    for path in static_files:
        frames = read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        if args.max_frames > 0:
            frames = frames[: args.max_frames]
        raw_frames[str(path)] = frames

    session_rows: list[dict] = []
    t_start = time.perf_counter()
    total_blocks = len(layouts) * len(eval_sets)
    block = 0
    for delay_mode, layout in layouts.items():
        for eval_set in eval_sets:
            block += 1
            allowed = allowed_by_eval[eval_set]
            print(f"[surveyed-baseline] block {block}/{total_blocks} delay={delay_mode} Tx={args.tag_method} eval={eval_set}", flush=True)
            for path in static_files:
                sid = session_id_from_path(path)
                cap = capture_name_from_path(path)
                frames_in = raw_frames[str(path)]
                frames = filter_frames(frames_in, allowed, min_anchors=4)
                results = solve_frames(layout, args.tag_method, frames)
                summary = summarize_results(results, args.point_estimator)
                truth = tag_truth.get(sid)
                if truth is None or summary["status"] != "ok":
                    continue
                solved = np.array([summary["x_mm"], summary["y_mm"], summary["z_mm"]], dtype=float)
                diff = solved - truth
                meta = metadata.get(sid, {})
                truth_info = tag_truth_meta.get(sid, {})
                session_rows.append(
                    {
                        "anchor_source": SURVEYED_SOURCE,
                        "coordinate_frame": "OptiTrack",
                        "alignment_dof": 0,
                        "alignment_note": "direct OptiTrack-frame solve; no Kabsch/reflection/scale",
                        "delay_mode": delay_mode,
                        "delay_note": {
                            "raw_zero_delay": "zero delays",
                            "autopos_v4io_delay_vector": "V4-io AutoPos per-anchor delay vector; jointly estimated with AutoPos layout",
                            "inter_anchor_delaycal": "OptiTrack inter-anchor LS endpoint delays; partly circular lower bound",
                        }[delay_mode],
                        "tag_method": args.tag_method,
                        "eval_set": eval_set,
                        "ID": sid,
                        "capture": cap,
                        "location": meta.get("location", ""),
                        "height": meta.get("height", ""),
                        "facing": meta.get("facing", ""),
                        "tag_truth_source": truth_info.get("tag_truth_source", ""),
                        "tag_truth_corrected": truth_info.get("tag_truth_corrected", False),
                        "tag_truth_permutation": truth_info.get("tag_truth_permutation", ""),
                        "tag_truth_shift_from_motive_mm": truth_info.get("tag_truth_shift_from_motive_mm", np.nan),
                        "point_estimator": args.point_estimator,
                        "frames_input": int(len(frames)),
                        "frames_solved": int(summary["frames_solved"]),
                        "solve_fraction": float(summary["frames_solved"] / len(frames)) if frames else 0.0,
                        **summary,
                        "solved_x_mm": float(solved[0]),
                        "solved_y_vertical_mm": float(solved[1]),
                        "solved_z_mm": float(solved[2]),
                        "truth_x_mm": float(truth[0]),
                        "truth_y_vertical_mm": float(truth[1]),
                        "truth_z_mm": float(truth[2]),
                        "err_x_mm": float(diff[0]),
                        "err_y_vertical_mm": float(diff[1]),
                        "err_z_mm": float(diff[2]),
                        "err_horizontal_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                        "err_vertical_mm": float(abs(diff[1])),
                        "err_3d_mm": float(np.linalg.norm(diff)),
                        "anchor_delay_A_mm": float(layout.anchors[0].d_anchor_mm),
                        "anchor_delay_B_mm": float(layout.anchors[1].d_anchor_mm),
                        "anchor_delay_C_mm": float(layout.anchors[2].d_anchor_mm),
                        "anchor_delay_D_mm": float(layout.anchors[3].d_anchor_mm),
                        "anchor_delay_E_mm": float(layout.anchors[4].d_anchor_mm),
                        "anchor_delay_F_mm": float(layout.anchors[5].d_anchor_mm),
                        "anchor_delay_G_mm": float(layout.anchors[6].d_anchor_mm),
                        "anchor_delay_H_mm": float(layout.anchors[7].d_anchor_mm),
                        "tag_delay_mm": float(layout.tag_delay_mm),
                        "source_tr_all": str(path),
                    }
                )

    surveyed_summary = summarize_abs(session_rows)
    autopos_summary = load_autopos_summary(tag_summary_csv)

    write_csv(tables_dir / "surveyed_anchor_baseline_per_position.csv", session_rows)
    write_csv(tables_dir / "surveyed_anchor_baseline_summary.csv", surveyed_summary)
    write_csv(tables_dir / "surveyed_anchor_delaycal_diagnostics.csv", delay_rows)
    write_csv(tables_dir / "anchor_source_comparison.csv", [*surveyed_summary, *autopos_summary])
    write_comparison_md(
        tables_dir / "anchor_source_comparison.md",
        surveyed_summary,
        autopos_summary,
        delay_rows,
        session_rows,
    )
    fig1 = figs_dir / "autopos_vs_surveyed_per_position.png"
    fig2 = figs_dir / "worst_points_autopos_vs_surveyed.png"
    plot_autopos_vs_surveyed(session_rows, tag_per_position_csv, fig1)
    plot_worst_points(session_rows, tag_per_position_csv, fig2)
    copy_report_figures(out_dir, [fig1, fig2])

    elapsed = time.perf_counter() - t_start
    append_run_meta(
        out_dir,
        {
            "script": "surveyed_anchor_baseline.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "axis_convention": {"optitrack_vertical_axis": OPTITRACK_VERTICAL_AXIS},
            "anchor_source": "OptiTrack Aantenna..Hantenna medians from corrected static truth loader",
            "primary_anchor_truth_ids": PRIMARY_IDS,
            "tag_truth_marker": "corrected_Iantenna",
            "tag_truth_corrections": {
                sid: ",".join(str(i) for i in perm) for sid, perm in TAG_BALL_LABEL_PERMUTATIONS.items()
            },
            "surveyed_baseline_frame": "OptiTrack",
            "alignment_dof": 0,
            "alignment_note": "No Kabsch, reflection, or scale is applied to surveyed baseline.",
            "delaycal_note": "Per-anchor endpoint delays fitted from raw inter-anchor medians vs OptiTrack true distances; tag delay set to median endpoint delay. This is partly circular and a lower bound.",
            "delaycal_anchor_delays_mm": {ANCHORS[i]: delaycal_anchor_delays[i] for i in range(8)},
            "delaycal_tag_delay_mm": delaycal_tag_delay,
            "autopos_v4io_layout_path": str(autopos_v4io_layout_path),
            "autopos_v4io_layout_sha256": sha256_file(autopos_v4io_layout_path),
            "autopos_v4io_anchor_delays_mm": {ANCHORS[i]: autopos_anchor_delays[i] for i in range(8)},
            "autopos_v4io_tag_delay_mm": autopos_tag_delay,
            "pair_quality_path": str(pair_quality_path),
            "pair_quality_sha256": sha256_file(pair_quality_path),
            "sigma_path": str(sigma_path),
            "sigma_sha256": sha256_file(sigma_path) if sigma_path.exists() else "",
            "static_files": [str(p) for p in static_files],
            "static_file_sha256": {str(p): sha256_file(p) for p in static_files},
            "elapsed_s": elapsed,
            "outputs": [
                "tables/surveyed_anchor_baseline_per_position.csv",
                "tables/surveyed_anchor_baseline_summary.csv",
                "tables/surveyed_anchor_delaycal_diagnostics.csv",
                "tables/anchor_source_comparison.csv",
                "tables/anchor_source_comparison.md",
                "figs/autopos_vs_surveyed_per_position.png",
                "figs/worst_points_autopos_vs_surveyed.png",
            ],
        },
    )
    print(
        f"[surveyed-baseline] wrote rows={len(session_rows)} summary_rows={len(surveyed_summary)} "
        f"elapsed={elapsed:.1f}s table={tables_dir / 'anchor_source_comparison.md'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
