#!/usr/bin/env python3
"""Task 2 partial: static tag absolute accuracy from existing solved capture means.

This uses anchor-derived transforms only. No transform is fitted to tag truth.
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
    fig, axs = plt.subplots(1, 2, figsize=(13, 5), sharey=True)
    for ax, eval_set in zip(axs, ["all8", "noG"]):
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else official_root / "Analysis/official_extra_analysis"
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    opti_dir = official_root / "opti_captures/static"
    layout_dir = official_root / "solver/outputs/v1_to_v4_io_field_check"
    static_csv = layout_dir / "tables/static_all_captures.csv"

    anchor_markers = [f"{a}antenna" for a in ANCHORS]
    truth_by_file = {}
    tag_truth = {}
    for path in sorted(opti_dir.glob("ID*.trc")):
        markers = parse_trc(path, anchor_markers + ["Iantenna"])
        truth_by_file[path.stem] = {a: markers[f"{a}antenna"] for a in ANCHORS}
        tag_truth[path.stem] = markers["Iantenna"]

    anchor_truth = {}
    for a in ANCHORS:
        anchor_truth[a] = np.nanmedian(np.array([truth_by_file[pid][a] for pid in PRIMARY_IDS]), axis=0)

    static = pd.read_csv(static_csv)
    rows = []
    for version, g in static.groupby("version"):
        layout_path = layout_dir / version / "layout.json"
        labels, coords = load_layout(layout_path)
        for eval_set, anchors in {"all8": ANCHORS, "noG": [a for a in ANCHORS if a != "G"]}.items():
            idx = [labels.index(a) for a in anchors]
            src = coords[idx]
            dst = np.array([anchor_truth[a] for a in anchors])
            r, t, scale, det = fit_similarity(src, dst, allow_reflection=True, allow_scale=False)
            for _, row in g.iterrows():
                sid = row["ID"]
                if sid not in tag_truth:
                    continue
                p = np.array([[row["mean_x"], row["mean_y"], row["mean_z"]]], dtype=float)
                aligned = apply_transform(p, r, t, scale)[0]
                truth = tag_truth[sid]
                diff = aligned - truth
                rows.append(
                    {
                        "version": version,
                        "eval_set": eval_set,
                        "ID": sid,
                        "location": row.get("location", ""),
                        "height": row.get("height", ""),
                        "facing": row.get("facing", ""),
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
                        "n_frames": row.get("N_frames", np.nan),
                        "pct_ge8": row.get("pct_ge8", np.nan),
                    }
                )

    write_csv(tables_dir / "tag_abs_errors_per_session.csv", rows)
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
            }
        )
    write_csv(tables_dir / "tag_accuracy_summary.csv", summary_rows)
    md = ["# Static Tag Absolute Accuracy\n\n"]
    md.append("Transform source: anchor alignment only, reflection allowed, no scale. No tag truth was used for fitting.\n\n")
    md.append("This is the currently available production tag-solver output, not yet the full 5 Vx x 4 Tx replay matrix.\n\n")
    md.append("| version | eval_set | n | median_3d_mm | p95_3d_mm | rms_3d_mm | median_horizontal_mm | median_vertical_mm |\n")
    md.append("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
    for r in summary_rows:
        md.append(f"| {r['version']} | {r['eval_set']} | {r['n']} | {r['err_3d_median_mm']:.1f} | {r['err_3d_p95_mm']:.1f} | {r['err_3d_rms_mm']:.1f} | {r['err_horizontal_median_mm']:.1f} | {r['err_vertical_median_mm']:.1f} |\n")
    (tables_dir / "tag_accuracy_summary.md").write_text("".join(md))
    plot_errors(figs_dir / "tag_error_by_position.png", rows)

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
            "tag_truth_marker": "Iantenna",
            "note": "production tag solver only; full T1-T4 matrix pending",
        },
    )
    print(f"[tag-abs] wrote {tables_dir / 'tag_accuracy_summary.md'} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
