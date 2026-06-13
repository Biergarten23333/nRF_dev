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


@dataclass
class HeightPreservingFit:
    aligned: np.ndarray
    rotation_2d: np.ndarray
    translation_2d: np.ndarray
    vertical_shift: float
    det_2d: float


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


def fit_2d_rigid(src_xy: np.ndarray, dst_xy: np.ndarray) -> tuple[np.ndarray, np.ndarray, float]:
    if src_xy.shape != dst_xy.shape or src_xy.ndim != 2 or src_xy.shape[1] != 2:
        raise ValueError(f"bad 2D fit shapes: {src_xy.shape} vs {dst_xy.shape}")
    src_c = src_xy.mean(axis=0)
    dst_c = dst_xy.mean(axis=0)
    x = src_xy - src_c
    y = dst_xy - dst_c
    u, _s, vt = np.linalg.svd(x.T @ y)
    d = np.ones(2)
    if np.linalg.det(u @ vt) < 0:
        d[-1] = -1.0
    r = u @ np.diag(d) @ vt
    t = dst_c - src_c @ r
    return r, t, float(np.linalg.det(r))


def fit_height_preserving(src: np.ndarray, dst: np.ndarray, vertical_ref_idx: list[int] | None = None) -> HeightPreservingFit:
    """Align deployed AutoPos coordinates without rotating the US vertical gauge.

    AutoPos uses x/y as the horizontal plane and z as vertical. Vicon TRC uses
    X/Z as horizontal and Y as vertical. The fit therefore estimates only a
    2D rigid transform in the horizontal plane plus one global vertical shift.
    """
    if src.shape != dst.shape or src.ndim != 2 or src.shape[1] != 3:
        raise ValueError(f"bad height-preserving fit shapes: {src.shape} vs {dst.shape}")
    r2, t2, det_2d = fit_2d_rigid(src[:, :2], dst[:, [0, 2]])
    ref_idx = vertical_ref_idx if vertical_ref_idx is not None else list(range(src.shape[0]))
    vertical_shift = float(np.mean(dst[ref_idx, 1] - src[ref_idx, 2]))
    xy = src[:, :2] @ r2 + t2
    aligned = np.column_stack([xy[:, 0], src[:, 2] + vertical_shift, xy[:, 1]])
    return HeightPreservingFit(
        aligned=aligned,
        rotation_2d=r2,
        translation_2d=t2,
        vertical_shift=vertical_shift,
        det_2d=det_2d,
    )


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
    vertical_ref_idx = [ANCHOR_LABELS.index(a) for a in ("F", "G", "H")]
    fit = fit_height_preserving(src, dst, vertical_ref_idx=vertical_ref_idx)
    aligned = fit.aligned

    # Display in the AutoPos/BioSpur report convention: X/Y horizontal, Z vertical.
    # The raw Vicon TRC export uses X/Z horizontal and Y vertical, so we remap
    # raw (X, Y_vertical, Z) to report (X, Y_horizontal, Z_vertical).
    vicon_plot = np.column_stack([dst[:, 0], dst[:, 2], dst[:, 1]])
    fit_plot = np.column_stack([aligned[:, 0], aligned[:, 2], aligned[:, 1]])

    edge_pairs = [
        ("A", "B"), ("B", "C"), ("C", "D"), ("D", "A"),
        ("E", "F"), ("F", "G"), ("G", "H"), ("H", "E"),
        ("A", "E"), ("B", "F"), ("C", "G"), ("D", "H"),
    ]
    edge_idx = [(ANCHOR_LABELS.index(a), ANCHOR_LABELS.index(b)) for a, b in edge_pairs]
    lower_color = "#0072B2"
    upper_color = "#D55E00"
    layer_color = {a: lower_color for a in LOWER_ANCHORS} | {a: upper_color for a in UPPER_ANCHORS}

    fig = plt.figure(figsize=(11.5, 5.2), constrained_layout=True)
    gs = fig.add_gridspec(1, 2)
    ax3d = fig.add_subplot(gs[0, 0], projection="3d")
    ax_xy = fig.add_subplot(gs[0, 1])

    def draw_edges_3d(ax, pts: np.ndarray, *, color: str, linestyle: str, linewidth: float, alpha: float) -> None:
        for i, j in edge_idx:
            ax.plot(
                [pts[i, 0], pts[j, 0]],
                [pts[i, 1], pts[j, 1]],
                [pts[i, 2], pts[j, 2]],
                color=color,
                linestyle=linestyle,
                linewidth=linewidth,
                alpha=alpha,
            )

    def draw_edges_2d(ax, pts: np.ndarray, cols: tuple[int, int], *, color: str, linestyle: str, linewidth: float, alpha: float) -> None:
        c0, c1 = cols
        for i, j in edge_idx:
            ax.plot(
                [pts[i, c0], pts[j, c0]],
                [pts[i, c1], pts[j, c1]],
                color=color,
                linestyle=linestyle,
                linewidth=linewidth,
                alpha=alpha,
            )

    draw_edges_3d(ax3d, vicon_plot, color="#777777", linestyle="-", linewidth=1.1, alpha=0.42)
    label_offsets = {
        "A": (-95, -90),
        "B": (-80, 70),
        "C": (60, 55),
        "D": (55, -95),
        "E": (-90, 75),
        "F": (35, 80),
        "G": (70, 70),
        "H": (70, -80),
    }
    label_offsets_2d = {
        **label_offsets,
        "C": (-70, 85),
        "G": (88, 38),
        "H": (38, -120),
    }
    label_box = dict(boxstyle="round,pad=0.18", fc="white", ec="#666666", lw=0.6, alpha=0.88)

    point_colors = [layer_color[a] for a in ANCHOR_LABELS]
    ax3d.scatter(vicon_plot[:, 0], vicon_plot[:, 1], vicon_plot[:, 2], s=62, label="Vicon truth", c=point_colors, marker="o", edgecolors="black", linewidths=0.8)
    ax3d.scatter(fit_plot[:, 0], fit_plot[:, 1], fit_plot[:, 2], s=78, label="AutoPos aligned", c=point_colors, marker="x", linewidths=2.2)
    for i, anchor in enumerate(ANCHOR_LABELS):
        dx, dy = label_offsets[anchor]
        ax3d.text(
            vicon_plot[i, 0] + dx,
            vicon_plot[i, 1] + dy,
            vicon_plot[i, 2] + 85,
            anchor,
            color="black",
            fontsize=10,
            weight="bold",
            bbox=label_box,
        )
    ax3d.set_xlabel("X [mm]")
    ax3d.set_ylabel("Y horizontal [mm]")
    ax3d.set_zlabel("Z vertical [mm]")
    ax3d.set_title("3D overview")
    ax3d.view_init(elev=24, azim=-52)
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch
    legend_handles = [
        Line2D([0], [0], marker="o", color="none", markerfacecolor="#444444", markeredgecolor="black", markersize=7, label="Vicon truth"),
        Line2D([0], [0], marker="x", color="#444444", markersize=8, markeredgewidth=2, linestyle="None", label="AutoPos aligned"),
        Patch(facecolor=lower_color, edgecolor="black", label="Lower layer A-D"),
        Patch(facecolor=upper_color, edgecolor="black", label="Upper layer E-H"),
    ]
    ax3d.legend(handles=legend_handles, loc="upper left", fontsize=8)

    all_pts = np.vstack([vicon_plot, fit_plot])
    mins = all_pts.min(axis=0)
    maxs = all_pts.max(axis=0)
    span = maxs - mins
    pad = np.maximum(span * 0.18, 220.0)
    limits = [(mins[i] - pad[i], maxs[i] + pad[i]) for i in range(3)]
    ax3d.set_xlim(*limits[0])
    ax3d.set_ylim(*limits[1])
    ax3d.set_zlim(*limits[2])
    try:
        ax3d.set_box_aspect((span[0], span[1], span[2]))
    except Exception:
        pass

    def plot_projection(
        ax,
        cols: tuple[int, int],
        title: str,
        xlabel: str,
        ylabel: str,
        *,
        show_anchor_labels: bool,
    ) -> None:
        draw_edges_2d(ax, vicon_plot, cols, color="#777777", linestyle="-", linewidth=1.1, alpha=0.42)
        c0, c1 = cols
        for i, anchor in enumerate(ANCHOR_LABELS):
            if show_anchor_labels:
                dx, dy = label_offsets_2d[anchor]
                ax.annotate(
                    anchor,
                    xy=(vicon_plot[i, c0], vicon_plot[i, c1]),
                    xytext=(dx * 0.35, dy * 0.35),
                    textcoords="offset points",
                    color="black",
                    fontsize=9,
                    weight="bold",
                    bbox=label_box,
                    arrowprops=dict(arrowstyle="-", color="#555555", lw=0.5, shrinkA=0, shrinkB=4),
                )
        ax.scatter(vicon_plot[:, c0], vicon_plot[:, c1], s=48, c=point_colors, marker="o", edgecolors="black", linewidths=0.8, label="Vicon truth")
        ax.scatter(fit_plot[:, c0], fit_plot[:, c1], s=60, c=point_colors, marker="x", linewidths=2.2, label="AutoPos aligned")
        ax.set_title(title)
        ax.set_xlabel(xlabel)
        ax.set_ylabel(ylabel)
        ax.grid(True, alpha=0.25)
        ax.set_aspect("equal", adjustable="box")
        ax.set_xlim(*limits[c0])
        ax.set_ylim(*limits[c1])

    plot_projection(ax_xy, (0, 1), "Top view: anchor ID map", "X [mm]", "Y horizontal [mm]", show_anchor_labels=True)

    fig.suptitle("AutoPos v4-io UWB+US layout aligned to Vicon ground truth", fontsize=15)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def plot_v4_scale_diagnostic(out_path: Path, truth: dict[str, np.ndarray], layout_dir: Path) -> None:
    layout_path = layout_dir / "v4-io" / "layout.json"
    labels, coords = load_layout(layout_path)
    idx = [labels.index(a) for a in ANCHOR_LABELS]
    src = coords[idx]
    dst = np.array([truth[a] for a in ANCHOR_LABELS])
    fit_sim = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
    scale_ratio = 1.0 / fit_sim.scale if fit_sim.scale else math.nan

    pair_rows = []
    for i, a in enumerate(ANCHOR_LABELS):
        for j in range(i + 1, len(ANCHOR_LABELS)):
            b = ANCHOR_LABELS[j]
            autopos_dist = float(np.linalg.norm(src[i] - src[j]))
            vicon_dist = float(np.linalg.norm(dst[i] - dst[j]))
            ratio = autopos_dist / vicon_dist if vicon_dist > 0 else math.nan
            if a in LOWER_ANCHORS and b in LOWER_ANCHORS:
                color = "#0072B2"
                group = "lower-layer pair"
            elif a in UPPER_ANCHORS and b in UPPER_ANCHORS:
                color = "#D55E00"
                group = "upper-layer pair"
            else:
                color = "#666666"
                group = "cross-layer pair"
            pair_rows.append(
                {
                    "pair": f"{a}-{b}",
                    "autopos_dist": autopos_dist,
                    "vicon_dist": vicon_dist,
                    "ratio": ratio,
                    "color": color,
                    "group": group,
                }
            )

    ratios = np.array([row["ratio"] for row in pair_rows])
    vicon_dists = np.array([row["vicon_dist"] for row in pair_rows])
    mean_ratio = float(np.nanmean(ratios))
    median_ratio = float(np.nanmedian(ratios))

    fig, ax = plt.subplots(figsize=(10.8, 4.8), constrained_layout=True)
    for group, marker in [("lower-layer pair", "o"), ("upper-layer pair", "s"), ("cross-layer pair", "^")]:
        rows = [row for row in pair_rows if row["group"] == group]
        ax.scatter(
            [row["vicon_dist"] for row in rows],
            [row["ratio"] for row in rows],
            s=72,
            c=[row["color"] for row in rows],
            marker=marker,
            edgecolors="black",
            linewidths=0.6,
            alpha=0.9,
            label=group,
        )

    ax.axhline(1.0, color="#222222", linewidth=1.0, linestyle="-", label="no scale bias")
    ax.axhline(scale_ratio, color="#B00020", linewidth=1.6, linestyle="--", label=f"1 / Sim(3) scale = {scale_ratio:.3f}")
    ax.axhline(median_ratio, color="#555555", linewidth=1.1, linestyle=":", label=f"median pair ratio = {median_ratio:.3f}")

    label_candidates = sorted(pair_rows, key=lambda row: abs(row["ratio"] - 1.0), reverse=True)[:6]
    for row in label_candidates:
        ax.annotate(
            row["pair"],
            xy=(row["vicon_dist"], row["ratio"]),
            xytext=(5, 4),
            textcoords="offset points",
            fontsize=8,
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#888888", lw=0.5, alpha=0.85),
        )

    y_min = min(0.985, float(np.nanmin(ratios)) - 0.01)
    y_max = max(1.115, float(np.nanmax(ratios)) + 0.01)
    ax.set_ylim(y_min, y_max)
    ax.set_xlim(float(vicon_dists.min()) * 0.92, float(vicon_dists.max()) * 1.05)
    ax.set_xlabel("Vicon inter-anchor distance [mm]")
    ax.set_ylabel("Raw AutoPos distance / Vicon distance")
    ax.set_title("Scale diagnostic from all 28 inter-anchor distances")
    ax.grid(True, alpha=0.25)
    ax.legend(loc="lower right", fontsize=8)
    ax.text(
        0.02,
        0.06,
        f"mean pair ratio = {mean_ratio:.3f}\nraw AutoPos is about {(scale_ratio - 1.0) * 100:.1f}% larger before Sim(3) rescaling",
        transform=ax.transAxes,
        ha="left",
        va="bottom",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", ec="#888888", lw=0.6, alpha=0.9),
    )

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
            vertical_ref_idx = [anchors.index(a) for a in ("F", "G", "H") if a in anchors]
            fit_height = fit_height_preserving(src, dst, vertical_ref_idx=vertical_ref_idx or None)
            fit_ref = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
            fit_proper = fit_similarity(src, dst, allow_reflection=False, allow_scale=False)
            fit_sim = fit_similarity(src, dst, allow_reflection=True, allow_scale=True)
            height_stats = error_stats(fit_height.aligned, dst)
            ref_stats = error_stats(fit_ref.aligned, dst)
            proper_stats = error_stats(fit_proper.aligned, dst)
            sim_stats = error_stats(fit_sim.aligned, dst)

            row = {
                "version": version,
                "eval_set": eval_set,
                "n_anchors": len(anchors),
                "layout_sha256": layout_sha[:16],
                "shape_rms_mm": shape_rms,
                "height_preserving_rms_3d_mm": height_stats["rms_3d_mm"],
                "height_preserving_horizontal_rms_mm": height_stats["rms_horizontal_mm"],
                "height_preserving_vertical_rms_mm": height_stats["rms_vertical_mm"],
                "height_preserving_vertical_shift_mm": fit_height.vertical_shift,
                "height_preserving_det_2d": fit_height.det_2d,
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
                diff = fit_height.aligned - dst
                err3 = np.linalg.norm(diff, axis=1)
                horiz = np.sqrt(diff[:, 0] ** 2 + diff[:, 2] ** 2)
                vert = np.abs(diff[:, 1])
                for a, d, e3, eh, ev, aligned in zip(anchors, diff, err3, horiz, vert, fit_height.aligned):
                    all_error_rows[eval_set].append(
                        {
                            "version": version,
                            "eval_set": eval_set,
                            "anchor": a,
                            "alignment": "US height-preserving: 2D horizontal rigid + vertical shift, no pitch/roll or 3D scale",
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
                            "fit_det_2d": fit_height.det_2d,
                            "fit_vertical_shift_mm": fit_height.vertical_shift,
                        }
                    )

    write_csv(tables_dir / "layout_alignment_summary.csv", all_summary)
    write_csv(tables_dir / "layout_abs_errors_all8.csv", all_error_rows["all8"])

    summary_cols = [
        "version",
        "eval_set",
        "n_anchors",
        "shape_rms_mm",
        "height_preserving_rms_3d_mm",
        "height_preserving_horizontal_rms_mm",
        "height_preserving_vertical_rms_mm",
        "height_preserving_vertical_shift_mm",
        "proper_rotation_rms_3d_mm",
        "reflection_allowed_rms_3d_mm",
        "reflection_allowed_horizontal_rms_mm",
        "reflection_allowed_vertical_rms_mm",
        "similarity_scale",
        "similarity_rms_3d_mm",
    ]
    md = ["# Task 1 Layout Absolute Comparison\n"]
    md.append("Primary OptiTrack truth uses median antenna marker positions from: " + ", ".join(primary_ids) + "\n\n")
    md.append("Headline deployed UWB+US layout accuracy uses height-preserving alignment: 2D horizontal rigid transform plus one vertical shift, with no pitch/roll and no 3D scale.\n")
    md.append("Full 3D rigid and Sim(3) similarity alignments are diagnostic only and must not be used as deployed-system absolute-accuracy claims.\n\n")
    md.append(
        "Corrected FULL OptiTrack export is treated as authoritative; Anchor G is retained in the canonical all8 headline.\n\n"
    )
    v4_all8 = [r for r in all_summary if r["version"] == "v4-io" and r["eval_set"] == "all8"]
    if v4_all8:
        r = v4_all8[0]
        md.append(
            f"Headline sanity: v4-io all8 height-preserving RMS {r['height_preserving_rms_3d_mm']:.1f} mm "
            f"(horizontal {r['height_preserving_horizontal_rms_mm']:.1f} mm, "
            f"vertical {r['height_preserving_vertical_rms_mm']:.1f} mm); "
            f"similarity scale {r['similarity_scale']:.3f} diagnostic only.\n\n"
        )
    md.append("## Summary\n\n")
    md.append(format_markdown_table(all_summary, summary_cols))
    (tables_dir / "layout_alignment_summary.md").write_text("".join(md))

    plot_v4_3d(figs_dir / "layout_vicon_vs_autopos_3d.png", truth, layout_dir)
    plot_v4_scale_diagnostic(figs_dir / "layout_v4_scale_diagnostic.png", truth, layout_dir)

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
    print(f"[layout] wrote {figs_dir / 'layout_vicon_vs_autopos_3d.png'}")
    print(f"[layout] wrote {figs_dir / 'layout_v4_scale_diagnostic.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
