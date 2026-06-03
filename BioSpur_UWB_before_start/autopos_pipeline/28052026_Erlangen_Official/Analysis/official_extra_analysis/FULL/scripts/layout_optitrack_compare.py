#!/usr/bin/env python3
"""Task 1: OptiTrack vs AutoPos absolute anchor-layout comparison.

Official dataset convention:
  x_mm, y_mm = horizontal plane
  z_mm       = vertical axis, upper layer is negative z
  reported_height_mm = -z_mm

OptiTrack TRC convention:
  X/Z are horizontal, Y is vertical. Always align frames before comparing.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


LAYOUT_HORIZONTAL_AXES = ("x_mm", "y_mm")
LAYOUT_VERTICAL_AXIS = "z_mm"
LAYOUT_UPPER_LAYER_SIGN = "negative_z"
REPORTED_HEIGHT_EXPR = "-z_mm"
OPTITRACK_VERTICAL_AXIS = "Y"
ANCHOR_LABELS = list("ABCDEFGH")
LOWER_ANCHORS = list("ABCD")
UPPER_ANCHORS = list("EFGH")


@dataclass
class SimilarityFit:
    aligned: np.ndarray
    rotation: np.ndarray
    translation: np.ndarray
    scale: float
    det: float


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def append_run_meta(out_dir: Path, entry: dict) -> None:
    meta_path = out_dir / "run_meta.json"
    lock_path = out_dir / "run_meta.json.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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


def parse_trc_marker_medians(path: Path, markers: Iterable[str]) -> tuple[dict[str, dict], dict]:
    """Return marker medians/stds from a Motive TRC file.

    Marker names are on row 4 (0-index line 3), followed by three coordinate columns
    per marker in every data row.
    """
    markers = list(markers)
    with path.open("r", errors="replace", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if len(rows) < 6:
        raise ValueError(f"TRC too short: {path}")

    header_values = rows[2]
    units = header_values[4] if len(header_values) > 4 else ""
    raw_marker_fields = rows[3][2:]
    marker_names = [field.strip() for field in raw_marker_fields if field.strip()]
    marker_to_index = {name: i for i, name in enumerate(marker_names)}

    data = []
    for row in rows[5:]:
        if not row or not row[0].strip():
            continue
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
    max_cols = max(len(row) for row in data) if data else 0
    arr = np.full((len(data), max_cols), np.nan, dtype=float)
    for i, row in enumerate(data):
        arr[i, : len(row)] = row

    out: dict[str, dict] = {}
    for marker in markers:
        if marker not in marker_to_index:
            raise KeyError(f"{marker} not found in {path}")
        start = 2 + marker_to_index[marker] * 3
        xyz = arr[:, start : start + 3]
        valid = np.isfinite(xyz).all(axis=1)
        xyz = xyz[valid]
        if xyz.size == 0:
            med = np.array([np.nan, np.nan, np.nan])
            std = np.array([np.nan, np.nan, np.nan])
        else:
            med = np.nanmedian(xyz, axis=0)
            std = np.nanstd(xyz, axis=0, ddof=1) if xyz.shape[0] > 1 else np.zeros(3)
        out[marker] = {
            "median": med,
            "std": std,
            "n_valid": int(xyz.shape[0]),
            "std_3d_mm": float(np.linalg.norm(std)),
        }

    info = {
        "path": str(path),
        "units": units,
        "num_rows": int(arr.shape[0]),
        "num_markers": len(marker_names),
    }
    return out, info


def load_layout(path: Path) -> tuple[list[str], np.ndarray]:
    data = json.loads(path.read_text())
    anchors = data["anchors"]
    labels = [a.get("label", chr(ord("A") + int(a["id"]))) for a in anchors]
    coords = np.array([[a["x_mm"], a["y_mm"], a["z_mm"]] for a in anchors], dtype=float)
    return labels, coords


def pairwise_distances(points: np.ndarray) -> np.ndarray:
    vals = []
    for i in range(points.shape[0]):
        for j in range(i + 1, points.shape[0]):
            vals.append(float(np.linalg.norm(points[i] - points[j])))
    return np.array(vals)


def fit_similarity(
    src: np.ndarray,
    dst: np.ndarray,
    *,
    allow_reflection: bool,
    allow_scale: bool,
) -> SimilarityFit:
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad fit shapes: {src.shape} vs {dst.shape}")
    src_c = src.mean(axis=0)
    dst_c = dst.mean(axis=0)
    x = src - src_c
    y = dst - dst_c
    h = x.T @ y
    u, s, vt = np.linalg.svd(h)
    d = np.ones(3)
    if not allow_reflection and np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    det = float(np.linalg.det(r))
    scale = 1.0
    if allow_scale:
        denom = float(np.sum(x * x))
        scale = float(np.sum(s * d) / denom) if denom > 0 else 1.0
    t = dst_c - scale * src_c @ r
    aligned = scale * src @ r + t
    return SimilarityFit(aligned=aligned, rotation=r, translation=t, scale=scale, det=det)


def error_stats(aligned: np.ndarray, truth: np.ndarray) -> dict[str, float]:
    diff = aligned - truth
    e3 = np.linalg.norm(diff, axis=1)
    # OptiTrack frame: Y is vertical, X/Z horizontal.
    vertical = np.abs(diff[:, 1])
    horizontal = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
    return {
        "rms_3d_mm": float(np.sqrt(np.mean(e3**2))),
        "mean_3d_mm": float(np.mean(e3)),
        "max_3d_mm": float(np.max(e3)),
        "rms_horizontal_mm": float(np.sqrt(np.mean(horizontal**2))),
        "rms_vertical_mm": float(np.sqrt(np.mean(vertical**2))),
    }


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def format_markdown_table(rows: list[dict], cols: list[str]) -> str:
    if not rows:
        return ""
    lines = []
    lines.append("| " + " | ".join(cols) + " |")
    lines.append("| " + " | ".join(["---"] * len(cols)) + " |")
    for row in rows:
        vals = []
        for c in cols:
            v = row.get(c, "")
            if isinstance(v, float):
                vals.append(f"{v:.3f}")
            else:
                vals.append(str(v))
        lines.append("| " + " | ".join(vals) + " |")
    return "\n".join(lines) + "\n"


def plot_v4_3d(out_path: Path, truth: dict[str, np.ndarray], layout_dir: Path) -> None:
    layout_path = layout_dir / "v4-io" / "layout.json"
    labels, coords = load_layout(layout_path)
    idx = [labels.index(a) for a in ANCHOR_LABELS]
    src = coords[idx]
    dst = np.array([truth[a] for a in ANCHOR_LABELS])
    fit = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
    aligned = fit.aligned

    fig = plt.figure(figsize=(8, 7))
    ax = fig.add_subplot(111, projection="3d")
    # Display OptiTrack X/Z horizontal and Y vertical.
    opti_plot = np.column_stack([dst[:, 0], dst[:, 2], dst[:, 1]])
    fit_plot = np.column_stack([aligned[:, 0], aligned[:, 2], aligned[:, 1]])
    ax.scatter(opti_plot[:, 0], opti_plot[:, 1], opti_plot[:, 2], s=70, label="OptiTrack antenna", c="black")
    ax.scatter(fit_plot[:, 0], fit_plot[:, 1], fit_plot[:, 2], s=70, label="AutoPos v4-io aligned", c="#79b7ff")
    for i, anchor in enumerate(ANCHOR_LABELS):
        ax.plot(
            [opti_plot[i, 0], fit_plot[i, 0]],
            [opti_plot[i, 1], fit_plot[i, 1]],
            [opti_plot[i, 2], fit_plot[i, 2]],
            color="#888888",
            linewidth=1.2,
        )
        ax.text(opti_plot[i, 0], opti_plot[i, 1], opti_plot[i, 2] + 35, anchor, color="black")
    ax.set_xlabel("OptiTrack X mm")
    ax.set_ylabel("OptiTrack Z mm")
    ax.set_zlabel("OptiTrack Y vertical mm")
    ax.set_title("V4-io AutoPos layout aligned to OptiTrack (reflection allowed, no scale)")
    ax.legend(loc="upper left")
    ax.view_init(elev=24, azim=-52)
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--layout-dir", default=None)
    parser.add_argument("--opti-dir", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--primary-ids", default="ID01,ID02,ID03,ID04,ID05")
    parser.add_argument("--eval-sets", default="all8")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    np.random.default_rng(args.seed)
    official_root = Path(args.official_root).resolve()
    layout_dir = Path(args.layout_dir).resolve() if args.layout_dir else official_root / "solver/outputs/v1_to_v4_io_field_check"
    opti_dir = Path(args.opti_dir).resolve() if args.opti_dir else official_root / "opti_captures/full"
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    primary_ids = [x.strip() for x in args.primary_ids.split(",") if x.strip()]
    trc_paths = sorted(opti_dir.glob("ID*.trc"))
    if not trc_paths:
        raise FileNotFoundError(f"No TRC files found in {opti_dir}")

    antenna_marker_names = [f"{a}antenna" for a in ANCHOR_LABELS]
    jig_marker_names = []
    for anchor in ANCHOR_LABELS:
        jig_marker_names.extend([f"{anchor}short", f"{anchor}long", f"{anchor}top", f"{anchor}4"])
    marker_names = antenna_marker_names + jig_marker_names
    per_file_rows: list[dict] = []
    marker_by_file: dict[str, dict[str, np.ndarray]] = {}
    trc_infos = []
    for path in trc_paths:
        file_id = path.stem
        medians, info = parse_trc_marker_medians(path, marker_names)
        trc_infos.append(info)
        marker_by_file[file_id] = {}
        for anchor, marker in zip(ANCHOR_LABELS, antenna_marker_names):
            m = medians[marker]
            med = m["median"]
            std = m["std"]
            marker_by_file[file_id][anchor] = med
            per_file_rows.append(
                {
                    "file_id": file_id,
                    "anchor": anchor,
                    "marker": marker,
                    "x_mm": med[0],
                    "y_vertical_mm": med[1],
                    "z_mm": med[2],
                    "std_x_mm": std[0],
                    "std_y_vertical_mm": std[1],
                    "std_z_mm": std[2],
                    "std_3d_mm": m["std_3d_mm"],
                    "n_valid": m["n_valid"],
                }
            )
    write_csv(tables_dir / "opti_anchor_medians_by_file.csv", per_file_rows)

    missing_primary = [pid for pid in primary_ids if pid not in marker_by_file]
    if missing_primary:
        raise FileNotFoundError(f"Primary TRC IDs missing: {missing_primary}")

    truth: dict[str, np.ndarray] = {}
    for anchor in ANCHOR_LABELS:
        vals = np.array([marker_by_file[pid][anchor] for pid in primary_ids], dtype=float)
        truth[anchor] = np.nanmedian(vals, axis=0)

    jig_rows = []
    for file_id in primary_ids:
        path = opti_dir / f"{file_id}.trc"
        medians, _ = parse_trc_marker_medians(path, jig_marker_names)
        for anchor in ANCHOR_LABELS:
            pts = {
                "short": medians[f"{anchor}short"]["median"],
                "long": medians[f"{anchor}long"]["median"],
                "top": medians[f"{anchor}top"]["median"],
                "p4": medians[f"{anchor}4"]["median"],
            }
            pairs = {
                "short_long_mm": np.linalg.norm(pts["short"] - pts["long"]),
                "short_top_mm": np.linalg.norm(pts["short"] - pts["top"]),
                "short_4_mm": np.linalg.norm(pts["short"] - pts["p4"]),
                "long_top_mm": np.linalg.norm(pts["long"] - pts["top"]),
                "long_4_mm": np.linalg.norm(pts["long"] - pts["p4"]),
                "top_4_mm": np.linalg.norm(pts["top"] - pts["p4"]),
            }
            row = {"file_id": file_id, "anchor": anchor}
            row.update({k: float(v) for k, v in pairs.items()})
            jig_rows.append(row)
    write_csv(tables_dir / "opti_anchor_marker_fingerprint.csv", jig_rows)

    consistency_rows = []
    for file_id, by_anchor in marker_by_file.items():
        for anchor in ANCHOR_LABELS:
            dev = by_anchor[anchor] - truth[anchor]
            consistency_rows.append(
                {
                    "file_id": file_id,
                    "anchor": anchor,
                    "dev_x_mm": dev[0],
                    "dev_y_vertical_mm": dev[1],
                    "dev_z_mm": dev[2],
                    "dev_3d_mm": float(np.linalg.norm(dev)),
                    "is_primary_truth_file": file_id in primary_ids,
                }
            )
    write_csv(tables_dir / "opti_anchor_consistency.csv", consistency_rows)

    layout_versions = [p.parent.name for p in sorted(layout_dir.glob("*/layout.json"))]
    eval_defs = {
        "all8": ANCHOR_LABELS,
        "lower": LOWER_ANCHORS,
        "upper": UPPER_ANCHORS,
    }
    eval_sets = [v.strip() for v in args.eval_sets.split(",") if v.strip()]
    for eval_set in eval_sets:
        if eval_set not in eval_defs:
            raise ValueError(f"unknown eval set {eval_set!r}; FULL canonical analysis supports {sorted(eval_defs)}")
    all_summary = []
    all_error_rows: dict[str, list[dict]] = {"all8": []}
    for version in layout_versions:
        layout_path = layout_dir / version / "layout.json"
        labels, coords = load_layout(layout_path)
        layout_sha = sha256_file(layout_path)
        for eval_set in eval_sets:
            anchors = eval_defs[eval_set]
            idx = [labels.index(a) for a in anchors]
            src = coords[idx]
            dst = np.array([truth[a] for a in anchors], dtype=float)

            shape_diff = pairwise_distances(src) - pairwise_distances(dst)
            shape_rms = float(np.sqrt(np.mean(shape_diff**2))) if shape_diff.size else math.nan
            fit_ref = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
            fit_proper = fit_similarity(src, dst, allow_reflection=False, allow_scale=False)
            fit_sim = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
            ref_stats = error_stats(fit_ref.aligned, dst)
            proper_stats = error_stats(fit_proper.aligned, dst)
            sim_stats = error_stats(fit_sim.aligned, dst)

            row = {
                "version": version,
                "eval_set": eval_set,
                "n_anchors": len(anchors),
                "layout_sha256": layout_sha[:16],
                "shape_rms_mm": shape_rms,
                "proper_rotation_rms_3d_mm": proper_stats["rms_3d_mm"],
                "proper_rotation_det": fit_proper.det,
                "reflection_allowed_rms_3d_mm": ref_stats["rms_3d_mm"],
                "reflection_allowed_horizontal_rms_mm": ref_stats["rms_horizontal_mm"],
                "reflection_allowed_vertical_rms_mm": ref_stats["rms_vertical_mm"],
                "reflection_allowed_det": fit_ref.det,
                "similarity_scale": fit_sim.scale,
                "similarity_rms_3d_mm": sim_stats["rms_3d_mm"],
                "similarity_diagnostic_only": True,
            }
            all_summary.append(row)

            if eval_set == "all8":
                diff = fit_ref.aligned - dst
                err3 = np.linalg.norm(diff, axis=1)
                horiz = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
                vert = np.abs(diff[:, 1])
                for a, d, e3, eh, ev, aligned in zip(anchors, diff, err3, horiz, vert, fit_ref.aligned):
                    all_error_rows[eval_set].append(
                        {
                            "version": version,
                            "eval_set": eval_set,
                            "anchor": a,
                            "err_x_mm": d[0],
                            "err_y_vertical_mm": d[1],
                            "err_z_mm": d[2],
                            "err_3d_mm": e3,
                            "err_horizontal_mm": eh,
                            "err_vertical_mm": ev,
                            "aligned_x_mm": aligned[0],
                            "aligned_y_vertical_mm": aligned[1],
                            "aligned_z_mm": aligned[2],
                            "truth_x_mm": truth[a][0],
                            "truth_y_vertical_mm": truth[a][1],
                            "truth_z_mm": truth[a][2],
                        }
                    )

    write_csv(tables_dir / "layout_alignment_summary.csv", all_summary)
    write_csv(tables_dir / "layout_abs_errors_all8.csv", all_error_rows["all8"])

    summary_cols = [
        "version",
        "eval_set",
        "n_anchors",
        "shape_rms_mm",
        "proper_rotation_rms_3d_mm",
        "reflection_allowed_rms_3d_mm",
        "reflection_allowed_horizontal_rms_mm",
        "reflection_allowed_vertical_rms_mm",
        "similarity_scale",
        "similarity_rms_3d_mm",
    ]
    md = ["# Task 1 Layout Absolute Comparison\n"]
    md.append("Primary OptiTrack truth uses median antenna marker positions from: " + ", ".join(primary_ids) + "\n\n")
    md.append("All headline accuracy values use reflection-allowed rigid alignment with no scale.\n")
    md.append("Similarity scale/RMS are diagnostic only and must not be used as absolute-accuracy claims.\n\n")
    md.append(
        "Corrected FULL OptiTrack export is treated as authoritative; Anchor G is retained in the canonical all8 headline.\n\n"
    )
    v4_all8 = [r for r in all_summary if r["version"] == "v4-io" and r["eval_set"] == "all8"]
    if v4_all8:
        r = v4_all8[0]
        md.append(
            f"Headline sanity: v4-io all8 rigid RMS {r['reflection_allowed_rms_3d_mm']:.1f} mm "
            f"(horizontal {r['reflection_allowed_horizontal_rms_mm']:.1f} mm, "
            f"vertical {r['reflection_allowed_vertical_rms_mm']:.1f} mm); "
            f"similarity scale {r['similarity_scale']:.3f} diagnostic only.\n\n"
        )
    md.append("## Summary\n\n")
    md.append(format_markdown_table(all_summary, summary_cols))
    (tables_dir / "layout_alignment_summary.md").write_text("".join(md))

    plot_v4_3d(figs_dir / "layout_opti_vs_autopos_3d.png", truth, layout_dir)

    append_run_meta(
        out_dir,
        {
            "script": "layout_optitrack_compare.py",
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
            "layout_dir": str(layout_dir),
            "layout_files": {
                version: {
                    "path": str(layout_dir / version / "layout.json"),
                    "sha256": sha256_file(layout_dir / version / "layout.json"),
                }
                for version in layout_versions
            },
            "opti_dir": str(opti_dir),
            "trc_count": len(trc_paths),
            "primary_ids": primary_ids,
        },
    )

    print(f"[layout] wrote {tables_dir / 'layout_alignment_summary.md'}")
    print(f"[layout] wrote {figs_dir / 'layout_opti_vs_autopos_3d.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
