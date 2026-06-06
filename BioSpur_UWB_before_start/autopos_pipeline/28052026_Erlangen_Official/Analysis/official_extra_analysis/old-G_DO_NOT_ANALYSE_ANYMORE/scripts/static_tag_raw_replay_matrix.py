#!/usr/bin/env python3
"""Full static-tag raw replay matrix: 5 layout solvers x 4 tag solvers.

This is the report-grade replacement for the earlier production-output-only
static tag absolute analysis.  It replays each raw static `tr_all.csv` through
the C-core T-series solver, then maps the solved tag point into OptiTrack using
an anchor-locked, reflection-allowed, no-scale transform.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import re
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


LAYOUT_HORIZONTAL_AXES = ("x_mm", "y_mm")
LAYOUT_VERTICAL_AXIS = "z_mm"
LAYOUT_UPPER_LAYER_SIGN = "negative_z"
REPORTED_HEIGHT_EXPR = "-z_mm"
OPTITRACK_VERTICAL_AXIS = "Y"
ANCHORS = list("ABCDEFGH")
LAYOUT_VERSIONS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
TAG_METHODS = ["T1", "T2", "T3", "T4"]

THIS = Path(__file__).resolve()
REPO_ROOT = THIS.parents[5]
SOLVER_ROOT = REPO_ROOT / "biospur_tag_positioning_offline_solver"
sys.path.insert(0, str(SOLVER_ROOT))

from biospur_tag_positioning_offline_solver.capture_io import read_tr_all_frames  # noqa: E402
from biospur_tag_positioning_offline_solver.c_solver import TagPositionSolver  # noqa: E402
from biospur_tag_positioning_offline_solver.layout_io import load_layout_json  # noqa: E402
from biospur_tag_positioning_offline_solver.models import Frame, Layout, SolverConfig  # noqa: E402


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
                meta = json.loads(meta_path.read_text())
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


def parse_trc(path: Path, marker_names: list[str]) -> dict[str, np.ndarray]:
    with path.open("r", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    marker_row = [x.strip() for x in rows[3][2:] if x.strip()]
    marker_to_index = {name: i for i, name in enumerate(marker_row)}
    data = []
    for row in rows[5:]:
        vals = []
        for field in row:
            field = field.strip()
            if field == "":
                vals.append(np.nan)
            else:
                try:
                    vals.append(float(field))
                except ValueError:
                    vals.append(np.nan)
        data.append(vals)
    max_cols = max(len(r) for r in data)
    arr = np.full((len(data), max_cols), np.nan)
    for i, row in enumerate(data):
        arr[i, : len(row)] = row
    out = {}
    for marker in marker_names:
        if marker not in marker_to_index:
            raise KeyError(f"{marker} missing in {path}")
        start = 2 + marker_to_index[marker] * 3
        xyz = arr[:, start : start + 3]
        xyz = xyz[np.isfinite(xyz).all(axis=1)]
        out[marker] = np.nanmedian(xyz, axis=0)
    return out


def load_autopos_layout_coords(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text(encoding="utf-8"))
    anchors = data["anchors"]
    labels = [a.get("label", chr(ord("A") + int(a["id"]))) for a in anchors]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)
    return labels, coords


def fit_similarity(src: np.ndarray, dst: np.ndarray, *, allow_reflection=True, allow_scale=False):
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    u, s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1
    r = u @ np.diag(d) @ vt
    scale = 1.0
    if allow_scale:
        scale = float(np.sum(s * d) / np.sum(x * x))
    t = dst_c - scale * src_c @ r
    return r, t, scale, float(np.linalg.det(r))


def apply_transform(points: np.ndarray, r: np.ndarray, t: np.ndarray, scale: float) -> np.ndarray:
    return scale * points @ r + t


def random_orthogonal_matrices(n: int, rng: np.random.Generator) -> np.ndarray:
    mats = [np.eye(3), np.diag([-1.0, 1.0, 1.0])]
    for _ in range(max(0, n // 2 - 1)):
        q = rng.normal(size=4)
        q /= np.linalg.norm(q)
        w, x, y, z = q
        r = np.array(
            [
                [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
            ],
            dtype=float,
        )
        mats.append(r)
        mats.append(r @ np.diag([-1.0, 1.0, 1.0]))
    return np.stack(mats[:n])


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


def load_truth(opti_dir: Path) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, dict], list[dict]]:
    primary = ["ID01", "ID02", "ID03", "ID04", "ID05"]
    return load_corrected_static_truth(opti_dir, ANCHORS, primary)


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


def tag_error_summary(aligned: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    diff = aligned - truth
    err3 = np.linalg.norm(diff, axis=1)
    horizontal = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
    vertical = np.abs(diff[:, 1])
    return {
        "err_3d_median_mm": float(np.nanmedian(err3)),
        "err_3d_p95_mm": float(np.nanpercentile(err3, 95)),
        "err_3d_rms_mm": float(np.sqrt(np.nanmean(err3 * err3))),
        "err_horizontal_median_mm": float(np.nanmedian(horizontal)),
        "err_vertical_median_mm": float(np.nanmedian(vertical)),
    }


def add_alignment_rows(rows: list[dict], version: str, tag_method: str, eval_set: str, solved: np.ndarray, truth: np.ndarray, official: dict, samples: np.ndarray) -> None:
    r_tag, t_tag, scale_tag, det_tag = fit_similarity(solved, truth, allow_reflection=True, allow_scale=False)
    a = tag_error_summary(apply_transform(solved, r_tag, t_tag, scale_tag), truth)
    rows.append(
        {
            "version": version,
            "tag_method": tag_method,
            "eval_set": eval_set,
            "method": "A_tag_cloud_fit_WRONG_circular",
            "n_tags": int(solved.shape[0]),
            **a,
            "range_median_min_mm": "",
            "range_median_max_mm": "",
            "range_p95_min_mm": "",
            "range_p95_max_mm": "",
            "det": det_tag,
            "scale": scale_tag,
            "note": "WRONG: transform fitted to tag truth; included as a circularity diagnostic.",
        }
    )
    src_c = solved.mean(axis=0)
    dst_c = truth.mean(axis=0)
    meds = []
    p95s = []
    for r in samples:
        t = dst_c - src_c @ r
        s = tag_error_summary(solved @ r + t, truth)
        meds.append(s["err_3d_median_mm"])
        p95s.append(s["err_3d_p95_mm"])
    rows.append(
        {
            "version": version,
            "tag_method": tag_method,
            "eval_set": eval_set,
            "method": "B_centroid_only_WRONG_underdetermined",
            "n_tags": int(solved.shape[0]),
            "err_3d_median_mm": "",
            "err_3d_p95_mm": "",
            "err_3d_rms_mm": "",
            "err_horizontal_median_mm": "",
            "err_vertical_median_mm": "",
            "range_median_min_mm": float(np.min(meds)),
            "range_median_max_mm": float(np.max(meds)),
            "range_p95_min_mm": float(np.min(p95s)),
            "range_p95_max_mm": float(np.max(p95s)),
            "det": "",
            "scale": 1.0,
            "note": "WRONG: centroid fixes translation only; rotation/reflection remain free.",
        }
    )
    rows.append(
        {
            "version": version,
            "tag_method": tag_method,
            "eval_set": eval_set,
            "method": "C_anchor_locked_OFFICIAL",
            "n_tags": int(solved.shape[0]),
            **official,
            "range_median_min_mm": "",
            "range_median_max_mm": "",
            "range_p95_min_mm": "",
            "range_p95_max_mm": "",
            "det": "",
            "scale": 1.0,
            "note": "CORRECT: transform fitted from anchors only, reflection allowed, no scale.",
        }
    )


def summarize_abs(rows: list[dict]) -> list[dict]:
    df = pd.DataFrame(rows)
    if df.empty:
        return []
    out: list[dict] = []
    for (version, tag_method, eval_set), g in df.groupby(["version", "tag_method", "eval_set"]):
        err = g["err_3d_mm"].to_numpy(dtype=float)
        out.append(
            {
                "version": version,
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
                "d3_std_median_mm": float(np.nanmedian(g["d3_std_mm"].to_numpy(dtype=float))),
                "radial_p95_median_mm": float(np.nanmedian(g["radial_p95_mm"].to_numpy(dtype=float))),
                "residual_rms_median_mm": float(np.nanmedian(g["residual_rms_median_mm"].to_numpy(dtype=float))),
                "pct_solved_ge8_median": float(np.nanmedian(g["pct_solved_ge8"].to_numpy(dtype=float))),
                "median_scale_bias_expected_mm": float(np.nanmedian(g["scale_bias_expected_mm"].to_numpy(dtype=float))),
            }
        )
    return sorted(out, key=lambda r: (r["eval_set"], LAYOUT_VERSIONS.index(r["version"]), TAG_METHODS.index(r["tag_method"])))


def plot_matrix(summary_rows: list[dict], out: Path) -> None:
    df = pd.DataFrame(summary_rows)
    if df.empty:
        return
    fig, axs = plt.subplots(1, 2, figsize=(12, 4.5), constrained_layout=True)
    vmin = float(df["err_3d_median_mm"].min())
    vmax = float(df["err_3d_median_mm"].max())
    for ax, eval_set in zip(axs, ["all8", "noG"]):
        sub = df[df["eval_set"] == eval_set]
        mat = np.full((len(LAYOUT_VERSIONS), len(TAG_METHODS)), np.nan)
        for i, version in enumerate(LAYOUT_VERSIONS):
            for j, method in enumerate(TAG_METHODS):
                vals = sub[(sub["version"] == version) & (sub["tag_method"] == method)]["err_3d_median_mm"].to_numpy()
                if vals.size:
                    mat[i, j] = vals[0]
        im = ax.imshow(mat, cmap="viridis", vmin=vmin, vmax=vmax)
        ax.set_title(f"{eval_set}: median 3D absolute error")
        ax.set_xticks(np.arange(len(TAG_METHODS)))
        ax.set_xticklabels(TAG_METHODS)
        ax.set_yticks(np.arange(len(LAYOUT_VERSIONS)))
        ax.set_yticklabels(LAYOUT_VERSIONS)
        for i in range(mat.shape[0]):
            for j in range(mat.shape[1]):
                if np.isfinite(mat[i, j]):
                    ax.text(j, i, f"{mat[i, j]:.0f}", ha="center", va="center", color="white" if mat[i, j] > (vmin + vmax) / 2 else "black", fontsize=8)
    fig.colorbar(im, ax=axs, label="median 3D error mm")
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_v4io_by_position(rows: list[dict], out: Path) -> None:
    df = pd.DataFrame(rows)
    sub = df[(df["version"] == "v4-io") & (df["eval_set"] == "all8")]
    if sub.empty:
        return
    fig, ax = plt.subplots(figsize=(13, 4.8), constrained_layout=True)
    ids = sorted(sub["ID"].unique())
    x = np.arange(len(ids))
    width = 0.18
    for k, method in enumerate(TAG_METHODS):
        vals = []
        for sid in ids:
            g = sub[(sub["ID"] == sid) & (sub["tag_method"] == method)]
            vals.append(float(g["err_3d_mm"].iloc[0]) if len(g) else np.nan)
        ax.bar(x + (k - 1.5) * width, vals, width=width, label=method)
    ax.set_xticks(x)
    ax.set_xticklabels(ids, rotation=45, ha="right")
    ax.set_ylabel("anchor-locked 3D absolute error mm")
    ax.set_title("V4-io raw replay static tag absolute error by ID")
    ax.grid(axis="y", alpha=0.25)
    ax.legend()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def write_summary_md(path: Path, summary_rows: list[dict]) -> None:
    df = pd.DataFrame(summary_rows)
    lines = ["# Static Tag Raw Replay Absolute Matrix\n\n"]
    lines.append("Source: raw static `tr_all.csv` captures replayed through the C-core T-series solver.\n\n")
    lines.append("Official frame lock: anchor-derived reflection-allowed rigid transform, no scale. Tag truth is not used for fitting.\n\n")
    lines.append("Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.\n\n")
    lines.append("Eval sets:\n\n")
    lines.append("- `all8`: solve with all available anchors and align with all 8 anchors.\n")
    lines.append("- `noG`: drop G observations before solving and align using anchors A/B/C/D/E/F/H.\n\n")
    if df.empty:
        lines.append("No summary rows.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    v4 = df[(df["version"] == "v4-io") & (df["tag_method"] == "T4")]
    if not v4.empty:
        lines.append("## V4-io / T4 Headline\n\n")
        lines.append("| eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_repeatability_d3_mm |\n")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |\n")
        for _, r in v4.sort_values("eval_set").iterrows():
            lines.append(
                f"| {r['eval_set']} | {int(r['n_sessions'])} | {r['err_3d_median_mm']:.1f} | "
                f"{r['err_3d_p95_mm']:.1f} | {r['err_3d_rms_mm']:.1f} | "
                f"{r['err_horizontal_median_mm']:.1f} | {r['err_vertical_median_mm']:.1f} | "
                f"{r['d3_std_median_mm']:.1f} |\n"
            )
        lines.append("\n")
    best = df.sort_values("err_3d_median_mm").head(8)
    lines.append("## Best Median 3D Absolute Errors\n\n")
    lines.append("| rank | version | tag_method | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | vertical_median_mm |\n")
    lines.append("| ---: | --- | --- | --- | ---: | ---: | ---: | ---: |\n")
    for rank, (_, r) in enumerate(best.iterrows(), start=1):
        lines.append(
            f"| {rank} | {r['version']} | {r['tag_method']} | {r['eval_set']} | "
            f"{r['err_3d_median_mm']:.1f} | {r['err_3d_p95_mm']:.1f} | "
            f"{r['err_3d_rms_mm']:.1f} | {r['err_vertical_median_mm']:.1f} |\n"
        )
    lines.append("\n## Full Matrix\n\n")
    lines.append("| version | Tx | eval_set | median_3d_mm | p95_3d_mm | rms_3d_mm | horiz_med_mm | vert_med_mm | repeat_d3_med_mm |\n")
    lines.append("| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |\n")
    for _, r in df.iterrows():
        lines.append(
            f"| {r['version']} | {r['tag_method']} | {r['eval_set']} | "
            f"{r['err_3d_median_mm']:.1f} | {r['err_3d_p95_mm']:.1f} | "
            f"{r['err_3d_rms_mm']:.1f} | {r['err_horizontal_median_mm']:.1f} | "
            f"{r['err_vertical_median_mm']:.1f} | {r['d3_std_median_mm']:.1f} |\n"
        )
    path.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay static tag raw captures for every Vx x Tx combination.")
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--layout-versions", default="all", help="comma list or all")
    parser.add_argument("--tag-methods", default="all", help="comma list or all")
    parser.add_argument("--eval-sets", default="all8,noG")
    parser.add_argument("--point-estimator", choices=["median", "mean"], default="median")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--centroid-sweep-samples", type=int, default=720)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else official_root / "Analysis/official_extra_analysis"
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    layout_base = official_root / "solver/outputs/v1_to_v4_io_field_check"
    sigma_path = layout_base / "tables/anchor_sigma.json"
    captures_root = official_root / "captures/erlangen_20260528_optitrack"
    opti_dir = official_root / "opti_captures/static"
    static_table = layout_base / "tables/static_all_captures.csv"
    metadata = load_static_metadata(static_table)
    anchor_truth, tag_truth, tag_truth_meta, correction_rows = load_truth(opti_dir)
    static_files = sorted(captures_root.glob("static_ID*/tag_capture*/tr_all.csv"))
    if not static_files:
        raise FileNotFoundError(f"no static tr_all.csv files under {captures_root}")

    if args.layout_versions.lower().strip() == "all":
        layout_versions = LAYOUT_VERSIONS
    else:
        layout_versions = [v.strip() for v in args.layout_versions.split(",") if v.strip()]
    if args.tag_methods.lower().strip() == "all":
        tag_methods = TAG_METHODS
    else:
        tag_methods = [v.strip().upper() for v in args.tag_methods.split(",") if v.strip()]
    eval_sets = [v.strip() for v in args.eval_sets.split(",") if v.strip()]
    allowed_by_eval = {
        "all8": set(range(8)),
        "noG": {0, 1, 2, 3, 4, 5, 7},
    }
    for eval_set in eval_sets:
        if eval_set not in allowed_by_eval:
            raise ValueError(f"unknown eval set {eval_set!r}")

    print(f"[static-raw] loading {len(static_files)} static captures", flush=True)
    raw_frames: dict[str, list[Frame]] = {}
    for path in static_files:
        frames = read_tr_all_frames(path, tags={"BSF66F"}, min_anchors=4)
        if args.max_frames > 0:
            frames = frames[: args.max_frames]
        raw_frames[str(path)] = frames

    rng = np.random.default_rng(args.seed)
    orthogonal_samples = random_orthogonal_matrices(args.centroid_sweep_samples, rng)
    session_rows: list[dict] = []
    alignment_rows: list[dict] = []
    t_start = time.perf_counter()
    total_blocks = len(layout_versions) * len(tag_methods) * len(eval_sets)
    block = 0
    for version in layout_versions:
        layout_path = layout_base / version / "layout.json"
        layout = load_layout_json(layout_path, sigma_path)
        labels, coords = load_autopos_layout_coords(layout_path)
        for tag_method in tag_methods:
            for eval_set in eval_sets:
                block += 1
                allowed = allowed_by_eval[eval_set]
                anchor_labels = [ANCHORS[i] for i in sorted(allowed)]
                idx = [labels.index(a) for a in anchor_labels]
                src = coords[idx]
                dst = np.array([anchor_truth[a] for a in anchor_labels], dtype=float)
                r, t, scale, det = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
                _, _, scale_diag, _ = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
                anchor_centroid = dst.mean(axis=0)
                solved_for_alignment = []
                truth_for_alignment = []
                print(f"[static-raw] block {block}/{total_blocks} version={version} Tx={tag_method} eval={eval_set}", flush=True)
                for path in static_files:
                    sid = session_id_from_path(path)
                    cap = capture_name_from_path(path)
                    frames_in = raw_frames[str(path)]
                    frames = filter_frames(frames_in, allowed, min_anchors=4)
                    results = solve_frames(layout, tag_method, frames)
                    summary = summarize_results(results, args.point_estimator)
                    truth = tag_truth.get(sid)
                    meta = metadata.get(sid, {})
                    truth_info = tag_truth_meta.get(sid, {})
                    if truth is None or summary["status"] != "ok":
                        continue
                    point = np.array([[summary["x_mm"], summary["y_mm"], summary["z_mm"]]], dtype=float)
                    aligned = apply_transform(point, r, t, scale)[0]
                    diff = aligned - truth
                    solved_for_alignment.append(point[0])
                    truth_for_alignment.append(truth)
                    distance_to_array = float(np.linalg.norm(truth - anchor_centroid))
                    session_rows.append(
                        {
                            "version": version,
                            "tag_method": tag_method,
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
                            "tag_ball_fingerprint_as_is_max_abs_dev_mm": truth_info.get(
                                "tag_ball_fingerprint_as_is_max_abs_dev_mm", np.nan
                            ),
                            "tag_ball_fingerprint_corrected_max_abs_dev_mm": truth_info.get(
                                "tag_ball_fingerprint_corrected_max_abs_dev_mm", np.nan
                            ),
                            "point_estimator": args.point_estimator,
                            "frames_input": int(len(frames)),
                            "frames_solved": int(summary["frames_solved"]),
                            "solve_fraction": float(summary["frames_solved"] / len(frames)) if frames else 0.0,
                            **summary,
                            "aligned_x_mm": float(aligned[0]),
                            "aligned_y_vertical_mm": float(aligned[1]),
                            "aligned_z_mm": float(aligned[2]),
                            "truth_x_mm": float(truth[0]),
                            "truth_y_vertical_mm": float(truth[1]),
                            "truth_z_mm": float(truth[2]),
                            "err_x_mm": float(diff[0]),
                            "err_y_vertical_mm": float(diff[1]),
                            "err_z_mm": float(diff[2]),
                            "err_horizontal_mm": float(math.sqrt(diff[0] * diff[0] + diff[2] * diff[2])),
                            "err_vertical_mm": float(abs(diff[1])),
                            "err_3d_mm": float(np.linalg.norm(diff)),
                            "anchor_fit_det": det,
                            "anchor_fit_scale": scale,
                            "anchor_similarity_scale_diagnostic": scale_diag,
                            "distance_to_array_centroid_mm": distance_to_array,
                            "scale_bias_expected_mm": abs(1.0 - scale_diag) * distance_to_array,
                            "source_tr_all": str(path),
                            "layout_json": str(layout_path),
                        }
                    )
                if solved_for_alignment:
                    solved_arr = np.vstack(solved_for_alignment)
                    truth_arr = np.vstack(truth_for_alignment)
                    official = tag_error_summary(apply_transform(solved_arr, r, t, scale), truth_arr)
                    add_alignment_rows(alignment_rows, version, tag_method, eval_set, solved_arr, truth_arr, official, orthogonal_samples)

    summary_rows = summarize_abs(session_rows)
    write_csv(tables_dir / "tag_raw_replay_abs_errors_per_session.csv", session_rows)
    write_csv(tables_dir / "tag_raw_replay_accuracy_summary.csv", summary_rows)
    write_csv(tables_dir / "tag_raw_replay_alignment_method_comparison.csv", alignment_rows)
    write_summary_md(tables_dir / "tag_raw_replay_accuracy_summary.md", summary_rows)
    plot_matrix(summary_rows, figs_dir / "tag_raw_replay_accuracy_matrix.png")
    plot_v4io_by_position(session_rows, figs_dir / "tag_raw_replay_v4io_by_position.png")

    elapsed = time.perf_counter() - t_start
    append_run_meta(
        out_dir,
        {
            "script": "static_tag_raw_replay_matrix.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
                "optitrack_vertical_axis": OPTITRACK_VERTICAL_AXIS,
            },
            "layout_versions": layout_versions,
            "tag_methods": tag_methods,
            "eval_sets": eval_sets,
            "sigma_path": str(sigma_path),
            "sigma_sha256": sha256_file(sigma_path) if sigma_path.exists() else "",
            "static_files": [str(p) for p in static_files],
            "static_file_sha256": {str(p): sha256_file(p) for p in static_files},
            "point_estimator": args.point_estimator,
            "tag_truth_marker": "corrected_Iantenna",
            "tag_truth_corrections": {
                sid: ",".join(str(i) for i in perm) for sid, perm in TAG_BALL_LABEL_PERMUTATIONS.items()
            },
            "tag_truth_note": "ID01/ID05 marker-ball relabeling and consensus ball-local Iantenna rebuild applied; other captures use Motive Iantenna.",
            "tag_truth_correction_rows": correction_rows,
            "elapsed_s": elapsed,
            "outputs": [
                "tables/tag_raw_replay_abs_errors_per_session.csv",
                "tables/tag_raw_replay_accuracy_summary.csv",
                "tables/tag_raw_replay_accuracy_summary.md",
                "tables/tag_raw_replay_alignment_method_comparison.csv",
                "figs/tag_raw_replay_accuracy_matrix.png",
                "figs/tag_raw_replay_v4io_by_position.png",
            ],
        },
    )
    print(
        f"[static-raw] wrote rows={len(session_rows)} summary_rows={len(summary_rows)} "
        f"elapsed={elapsed:.1f}s summary={tables_dir / 'tag_raw_replay_accuracy_summary.md'}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
