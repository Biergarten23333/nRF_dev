#!/usr/bin/env python3
"""Task 2: static tag absolute accuracy from existing solved capture means.

The official per-session error table uses the only non-circular frame lock:
anchor-derived reflection-allowed rigid alignment, with no scale fitted to tag truth.

For methods documentation, this script also writes an A/B/C alignment comparison:
  A. tag-cloud fit (circular, artificially optimistic)
  B. centroid-only translation (orientation is free; reported as a swept range)
  C. anchor-locked fit (official)
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
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
PRIMARY_IDS = ["ID01", "ID02", "ID03", "ID04", "ID05"]


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
        tmp.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
        tmp.replace(meta_path)
        fcntl.flock(lock, fcntl.LOCK_UN)


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


def load_layout(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text())
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
    """Deterministic rotation/reflection samples for the centroid-only sanity range."""
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


def tag_error_summary(aligned: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    diff = aligned - truth
    err3 = np.linalg.norm(diff, axis=1)
    vertical = np.abs(diff[:, 1])  # OptiTrack Y is vertical.
    horizontal = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
    return {
        "err_3d_median_mm": float(np.nanmedian(err3)),
        "err_3d_p95_mm": float(np.nanpercentile(err3, 95)),
        "err_3d_rms_mm": float(np.sqrt(np.nanmean(err3**2))),
        "err_horizontal_median_mm": float(np.nanmedian(horizontal)),
        "err_vertical_median_mm": float(np.nanmedian(vertical)),
    }


def add_alignment_comparison_rows(
    *,
    rows: list[dict],
    version: str,
    eval_set: str,
    solved: np.ndarray,
    truth: np.ndarray,
    official_summary: dict[str, float],
    orthogonal_samples: np.ndarray,
) -> None:
    r_tag, t_tag, scale_tag, det_tag = fit_similarity(solved, truth, allow_reflection=True, allow_scale=False)
    tag_fit = apply_transform(solved, r_tag, t_tag, scale_tag)
    a = tag_error_summary(tag_fit, truth)
    rows.append(
        {
            "version": version,
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
            "note": "WRONG for accuracy: transform fitted to tag truth; included only to show circular optimism.",
        }
    )

    src_c = solved.mean(axis=0)
    dst_c = truth.mean(axis=0)
    medians = []
    p95s = []
    for r in orthogonal_samples:
        t = dst_c - src_c @ r
        aligned = solved @ r + t
        s = tag_error_summary(aligned, truth)
        medians.append(s["err_3d_median_mm"])
        p95s.append(s["err_3d_p95_mm"])
    rows.append(
        {
            "version": version,
            "eval_set": eval_set,
            "method": "B_centroid_only_WRONG_underdetermined",
            "n_tags": int(solved.shape[0]),
            "err_3d_median_mm": "",
            "err_3d_p95_mm": "",
            "err_3d_rms_mm": "",
            "err_horizontal_median_mm": "",
            "err_vertical_median_mm": "",
            "range_median_min_mm": float(np.min(medians)),
            "range_median_max_mm": float(np.max(medians)),
            "range_p95_min_mm": float(np.min(p95s)),
            "range_p95_max_mm": float(np.max(p95s)),
            "det": "",
            "scale": 1.0,
            "note": "WRONG for accuracy: centroid fixes translation only; rotation/reflection remain free, so error is a range.",
        }
    )

    rows.append(
        {
            "version": version,
            "eval_set": eval_set,
            "method": "C_anchor_locked_OFFICIAL",
            "n_tags": int(solved.shape[0]),
            **official_summary,
            "range_median_min_mm": "",
            "range_median_max_mm": "",
            "range_p95_min_mm": "",
            "range_p95_max_mm": "",
            "det": "",
            "scale": 1.0,
            "note": "CORRECT: transform fitted from anchors only, reflection allowed, no scale.",
        }
    )


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def plot_errors(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    if df.empty:
        return
    eval_sets = list(dict.fromkeys(df["eval_set"].astype(str).tolist()))
    fig, axs = plt.subplots(1, len(eval_sets), figsize=(6.5 * len(eval_sets), 5), sharey=True, squeeze=False)
    for ax, eval_set in zip(axs[0], eval_sets):
        sub = df[df["eval_set"] == eval_set]
        versions = sorted(sub["version"].unique())
        data = [sub[sub["version"] == v]["err_3d_mm"].to_numpy() for v in versions]
        ax.boxplot(data, tick_labels=versions, showfliers=True)
        ax.set_title(eval_set)
        ax.set_ylabel("static tag absolute 3D error mm")
        ax.grid(axis="y", alpha=0.25)
        ax.tick_params(axis="x", rotation=25)
    fig.suptitle("Static tag absolute accuracy, transform from anchors only")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def plot_error_vs_distance(path: Path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows)
    if df.empty or "distance_to_array_centroid_mm" not in df:
        return
    eval_sets = list(dict.fromkeys(df["eval_set"].astype(str).tolist()))
    versions = sorted(df["version"].unique())
    fig, axs = plt.subplots(1, len(eval_sets), figsize=(6.5 * len(eval_sets), 5), sharey=True, squeeze=False)
    for ax, eval_set in zip(axs[0], eval_sets):
        sub = df[df["eval_set"] == eval_set]
        for version in versions:
            g = sub[sub["version"] == version]
            if g.empty:
                continue
            ax.scatter(g["distance_to_array_centroid_mm"], g["err_3d_mm"], s=22, alpha=0.75, label=version)
        ax.set_title(eval_set)
        ax.set_xlabel("OptiTrack tag distance to anchor centroid mm")
        ax.grid(alpha=0.25)
    axs[0, 0].set_ylabel("anchor-locked static tag 3D error mm")
    axs[0, -1].legend(fontsize=8, loc="best")
    fig.suptitle("Static tag error vs array distance, transform locked by anchors")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--layout-dir", default=None)
    parser.add_argument("--static-csv", default=None)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--centroid-sweep-samples", type=int, default=720)
    parser.add_argument("--eval-sets", default="all8")
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    opti_dir = official_root / "opti_captures/full"
    layout_dir = Path(args.layout_dir).resolve() if args.layout_dir else official_root / "solver/outputs/v1_to_v4_io_field_check"
    static_csv = Path(args.static_csv).resolve() if args.static_csv else layout_dir / "tables/static_all_captures.csv"

    anchor_truth, tag_truth, tag_truth_meta, correction_rows = load_corrected_static_truth(
        opti_dir,
        ANCHORS,
        PRIMARY_IDS,
    )

    static = pd.read_csv(static_csv)
    rows = []
    comparison_rows = []
    scale_rows = []
    rng = np.random.default_rng(args.seed)
    orthogonal_samples = random_orthogonal_matrices(args.centroid_sweep_samples, rng)
    eval_defs = {
        "all8": ANCHORS,
    }
    eval_sets = [v.strip() for v in args.eval_sets.split(",") if v.strip()]
    for eval_set in eval_sets:
        if eval_set not in eval_defs:
            raise ValueError(f"unknown eval set {eval_set!r}; FULL canonical analysis supports {sorted(eval_defs)}")
    for version, g in static.groupby("version"):
        layout_path = layout_dir / version / "layout.json"
        labels, coords = load_layout(layout_path)
        for eval_set in eval_sets:
            anchors = eval_defs[eval_set]
            idx = [labels.index(a) for a in anchors]
            src = coords[idx]
            dst = np.array([anchor_truth[a] for a in anchors])
            r, t, scale, det = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
            _, _, scale_diag, _ = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
            anchor_centroid = dst.mean(axis=0)
            solved_points = []
            truth_points = []
            for _, row in g.iterrows():
                sid = row["ID"]
                if sid not in tag_truth:
                    continue
                p = np.array([[row["mean_x"], row["mean_y"], row["mean_z"]]], dtype=float)
                aligned = apply_transform(p, r, t, scale)[0]
                truth = tag_truth[sid]
                truth_info = tag_truth_meta.get(sid, {})
                solved_points.append(p[0])
                truth_points.append(truth)
                diff = aligned - truth
                distance_to_array = float(np.linalg.norm(truth - anchor_centroid))
                rows.append(
                    {
                        "version": version,
                        "eval_set": eval_set,
                        "method": "C_anchor_locked_OFFICIAL",
                        "ID": sid,
                        "location": row.get("location", ""),
                        "height": row.get("height", ""),
                        "facing": row.get("facing", ""),
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
                        "err_x_mm": diff[0],
                        "err_y_vertical_mm": diff[1],
                        "err_z_mm": diff[2],
                        "err_horizontal_mm": float(np.sqrt(diff[0] ** 2 + diff[2] ** 2)),
                        "err_vertical_mm": abs(diff[1]),
                        "err_3d_mm": float(np.linalg.norm(diff)),
                        "aligned_x_mm": aligned[0],
                        "aligned_y_vertical_mm": aligned[1],
                        "aligned_z_mm": aligned[2],
                        "truth_x_mm": truth[0],
                        "truth_y_vertical_mm": truth[1],
                        "truth_z_mm": truth[2],
                        "anchor_fit_det": det,
                        "anchor_fit_scale": scale,
                        "anchor_similarity_scale_diagnostic": scale_diag,
                        "distance_to_array_centroid_mm": distance_to_array,
                        "scale_bias_expected_mm": abs(1.0 - scale_diag) * distance_to_array,
                        "n_frames": row.get("N_frames", np.nan),
                        "pct_ge8": row.get("pct_ge8", np.nan),
                    }
                )
            if solved_points:
                solved_arr = np.vstack(solved_points)
                truth_arr = np.vstack(truth_points)
                aligned_arr = apply_transform(solved_arr, r, t, scale)
                official_summary = tag_error_summary(aligned_arr, truth_arr)
                add_alignment_comparison_rows(
                    rows=comparison_rows,
                    version=version,
                    eval_set=eval_set,
                    solved=solved_arr,
                    truth=truth_arr,
                    official_summary=official_summary,
                    orthogonal_samples=orthogonal_samples,
                )

                g_rows = pd.DataFrame([r0 for r0 in rows if r0["version"] == version and r0["eval_set"] == eval_set])
                if len(g_rows) >= 3:
                    x = g_rows["distance_to_array_centroid_mm"].to_numpy(dtype=float)
                    y = g_rows["err_3d_mm"].to_numpy(dtype=float)
                    corr = float(np.corrcoef(x, y)[0, 1]) if np.nanstd(x) > 0 and np.nanstd(y) > 0 else np.nan
                    slope, intercept = np.polyfit(x, y, 1)
                    scale_rows.append(
                        {
                            "version": version,
                            "eval_set": eval_set,
                            "anchor_similarity_scale_diagnostic": scale_diag,
                            "one_minus_scale_abs": abs(1.0 - scale_diag),
                            "median_distance_to_array_centroid_mm": float(np.nanmedian(x)),
                            "median_scale_bias_expected_mm": float(np.nanmedian(g_rows["scale_bias_expected_mm"])),
                            "median_err_3d_mm": float(np.nanmedian(y)),
                            "corr_err_vs_distance": corr,
                            "ols_err_per_distance_slope": float(slope),
                            "ols_intercept_mm": float(intercept),
                        }
                    )

    write_csv(tables_dir / "tag_abs_errors_per_session.csv", rows)
    write_csv(tables_dir / "tag_alignment_method_comparison.csv", comparison_rows)
    write_csv(tables_dir / "tag_scale_propagation_summary.csv", scale_rows)
    write_csv(tables_dir / "tag_ground_truth_correction_summary.csv", correction_rows)
    df = pd.DataFrame(rows)
    summary_rows = []
    for (version, eval_set), g in df.groupby(["version", "eval_set"]):
        summary_rows.append(
            {
                "version": version,
                "eval_set": eval_set,
                "n": int(len(g)),
                "err_3d_median_mm": float(g["err_3d_mm"].median()),
                "err_3d_p95_mm": float(np.percentile(g["err_3d_mm"], 95)),
                "err_3d_rms_mm": float(np.sqrt(np.mean(g["err_3d_mm"] ** 2))),
                "err_horizontal_median_mm": float(g["err_horizontal_mm"].median()),
                "err_vertical_median_mm": float(g["err_vertical_mm"].median()),
                "median_distance_to_array_centroid_mm": float(g["distance_to_array_centroid_mm"].median()),
                "median_scale_bias_expected_mm": float(g["scale_bias_expected_mm"].median()),
            }
        )
    write_csv(tables_dir / "tag_accuracy_summary.csv", summary_rows)
    md = ["# Static Tag Absolute Accuracy\n\n"]
    md.append("Transform source: anchor alignment only, reflection allowed, no scale. No tag truth was used for fitting.\n\n")
    md.append("Tag truth source: corrected static OptiTrack `Iantenna`. ID01 and ID05 use deterministic I1..I5 relabeling plus a rebuilt ball-local consensus antenna; all other captures use Motive `Iantenna` as exported.\n\n")
    md.append("This is the production tag-solver output. The full 5 Vx x 4 Tx raw replay matrix is reported separately.\n\n")
    md.append("Frame-locking rule: the official values below use method C only, where the UWB->OptiTrack transform is fixed by anchors. Methods A/B are written separately in `tag_alignment_method_comparison.csv` as failure-mode demonstrations.\n\n")
    md.append("| version | eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm | median_dist_to_array_mm | median_scale_bias_expected_mm |\n")
    md.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |\n")
    for r in summary_rows:
        md.append(f"| {r['version']} | {r['eval_set']} | {r['n']} | {r['err_3d_median_mm']:.1f} | {r['err_3d_p95_mm']:.1f} | {r['err_3d_rms_mm']:.1f} | {r['err_horizontal_median_mm']:.1f} | {r['err_vertical_median_mm']:.1f} | {r['median_distance_to_array_centroid_mm']:.1f} | {r['median_scale_bias_expected_mm']:.1f} |\n")
    md.append("\n## A/B/C Frame-Locking Sanity\n\n")
    md.append("- A fits the transform to tag truth and is circular; it should not be used as an accuracy claim.\n")
    md.append("- B aligns only centroids; orientation and handedness remain free, so it is reported as an error range over swept rotations/reflections.\n")
    md.append("- C locks the transform from anchors only and is the official value.\n\n")
    md.append("## Iantenna Ground-Truth Correction\n\n")
    md.append("| ID | corrected | permutation | shift_from_motive_mm | fingerprint_as_is_max_mm | fingerprint_corrected_max_mm |\n")
    md.append("| --- | --- | --- | ---: | ---: | ---: |\n")
    for r in correction_rows:
        if not r["tag_truth_corrected"]:
            continue
        md.append(
            f"| {r['ID']} | {r['tag_truth_corrected']} | {r['tag_truth_permutation']} | "
            f"{r['tag_truth_shift_from_motive_mm']:.1f} | {r['fingerprint_as_is_max_abs_dev_mm']:.1f} | "
            f"{r['fingerprint_corrected_max_abs_dev_mm']:.1f} |\n"
        )
    (tables_dir / "tag_accuracy_summary.md").write_text("".join(md))
    plot_errors(figs_dir / "tag_error_by_position.png", rows)
    plot_error_vs_distance(figs_dir / "tag_error_vs_distance.png", rows)

    append_run_meta(
        out_dir,
        {
            "script": "static_tag_absolute_accuracy.py",
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
            "static_csv": str(static_csv),
            "static_sha256": sha256_file(static_csv),
            "primary_anchor_truth_ids": PRIMARY_IDS,
            "tag_truth_marker": "corrected_Iantenna",
            "tag_truth_corrections": {
                sid: ",".join(str(i) for i in perm) for sid, perm in TAG_BALL_LABEL_PERMUTATIONS.items()
            },
            "tag_truth_note": "ID01/ID05 marker-ball relabeling and consensus ball-local Iantenna rebuild applied; other captures use Motive Iantenna.",
            "centroid_sweep_samples": args.centroid_sweep_samples,
            "note": "production tag solver; official tag errors use corrected ground truth and anchor-locked method C",
        },
    )
    print(f"[tag-abs] wrote {tables_dir / 'tag_accuracy_summary.md'} rows={len(rows)} comparison_rows={len(comparison_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
