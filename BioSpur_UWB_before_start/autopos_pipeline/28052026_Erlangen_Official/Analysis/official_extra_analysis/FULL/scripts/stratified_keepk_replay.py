#!/usr/bin/env python3
"""Stratified keep-k replay with dropped-set composition recorded.

Unlike the large random MC5000 run, this script exhaustively replays fixed
anchor subsets.  That makes it possible to answer questions such as "does
dropping upper anchors hurt Z more?" without relying on an unrecorded random
mask composition.
"""

from __future__ import annotations

import argparse
import csv
import fcntl
import hashlib
import importlib.util
import itertools
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


LAYOUT_HORIZONTAL_AXES = ("x_mm", "y_mm")
LAYOUT_VERTICAL_AXIS = "z_mm"
LAYOUT_UPPER_LAYER_SIGN = "negative_z"
REPORTED_HEIGHT_EXPR = "-z_mm"
ANCHOR_LABELS = list("ABCDEFGH")
LOWER_ANCHORS = set("ABCD")
UPPER_ANCHORS = set("EFGH")
LAYOUT_VERSIONS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
TAG_METHODS = ["T1", "T2", "T3", "T4"]

THIS = Path(__file__).resolve()
OFFICIAL_ROOT = THIS.parents[4]
REPO_ROOT = THIS.parents[6]
CUDA_REPLAY_PATH = OFFICIAL_ROOT / "Analysis/scripts/cuda_t4_keepk_replay.py"


def load_cuda_replay_module():
    spec = importlib.util.spec_from_file_location("official_cuda_keepk_replay", CUDA_REPLAY_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {CUDA_REPLAY_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def parse_list(value: str, all_values: list[str]) -> list[str]:
    if value.strip().lower() == "all":
        return all_values
    vals = [v.strip() for v in value.split(",") if v.strip()]
    bad = [v for v in vals if v not in all_values]
    if bad:
        raise ValueError(f"unknown values {bad}; available={all_values}")
    return vals


def generate_keep_sets(keep_k: int) -> list[tuple[int, ...]]:
    return list(itertools.combinations(range(8), keep_k))


def describe_drop_set(keep_set: tuple[int, ...]) -> dict:
    keep = set(keep_set)
    dropped = [i for i in range(8) if i not in keep]
    dropped_labels = "".join(ANCHOR_LABELS[i] for i in dropped) or "none"
    kept_labels = "".join(ANCHOR_LABELS[i] for i in keep_set)
    lower = sum(1 for i in dropped if ANCHOR_LABELS[i] in LOWER_ANCHORS)
    upper = sum(1 for i in dropped if ANCHOR_LABELS[i] in UPPER_ANCHORS)
    if upper > lower:
        category = "upper_heavy"
    elif lower > upper:
        category = "lower_heavy"
    else:
        category = "balanced"
    return {
        "keep_set": kept_labels,
        "dropped_set": dropped_labels,
        "dropped_lower_count": lower,
        "dropped_upper_count": upper,
        "drop_category": category,
        "drops_G": int("G" in dropped_labels),
        "drops_H": int("H" in dropped_labels),
        "drops_FGH_count": sum(1 for ch in dropped_labels if ch in "FGH"),
    }


def make_fixed_masks(available: np.ndarray, keep_sets: list[tuple[int, ...]]) -> np.ndarray:
    tracks, frames, anchors = available.shape
    masks = np.zeros((tracks, len(keep_sets), frames, anchors), dtype=bool)
    for ri, keep_set in enumerate(keep_sets):
        keep = np.zeros((anchors,), dtype=bool)
        keep[list(keep_set)] = True
        masks[:, ri, :, :] = available & keep[None, None, :]
    return masks


def median(vals) -> float:
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.nanmedian(arr))


def percentile(vals, pct: float) -> float:
    arr = np.asarray(vals, dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return float("nan")
    return float(np.nanpercentile(arr, pct))


def summarize_by_composition(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    import pandas as pd

    df = pd.DataFrame(rows)
    out: list[dict] = []
    keys = ["layout", "tag_method", "kind", "keep_k", "drop_category", "dropped_lower_count", "dropped_upper_count"]
    for key_vals, g in df.groupby(keys):
        row = dict(zip(keys, key_vals))
        row["drop_sets"] = int(len(g))
        if row["kind"] == "static":
            row["d3_std_mm_median"] = median(g["d3_std_mm_median"])
            row["d3_std_mm_p95_over_drop_sets"] = percentile(g["d3_std_mm_median"], 95)
            row["z_std_mm_median"] = median(g["z_std_mm_median"])
            row["residual_rms_median_mm"] = median(g["residual_rms_median_mm"])
        else:
            row["turn_center_rms_3d_mm_median"] = median(g["turn_center_rms_3d_mm_median"])
            row["turn_center_rms_3d_mm_p95_over_drop_sets"] = percentile(g["turn_center_rms_3d_mm_median"], 95)
            row["circle_thickness_rms_mm_median"] = median(g["circle_thickness_rms_mm_median"])
            row["radius_mm_median"] = median(g["radius_mm_median"])
        out.append(row)
    return sorted(out, key=lambda r: (r["layout"], r["tag_method"], r["kind"], -int(r["keep_k"]), str(r["drop_category"])))


def summarize_by_category(rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    import pandas as pd

    df = pd.DataFrame(rows)
    out: list[dict] = []
    keys = ["layout", "tag_method", "kind", "keep_k", "drop_category"]
    for key_vals, g in df.groupby(keys):
        row = dict(zip(keys, key_vals))
        row["drop_sets"] = int(len(g))
        row["dropped_lower_count_median"] = float(np.nanmedian(g["dropped_lower_count"].to_numpy(dtype=float)))
        row["dropped_upper_count_median"] = float(np.nanmedian(g["dropped_upper_count"].to_numpy(dtype=float)))
        if row["kind"] == "static":
            row["d3_std_mm_median"] = median(g["d3_std_mm_median"])
            row["d3_std_mm_p95_over_drop_sets"] = percentile(g["d3_std_mm_median"], 95)
            row["z_std_mm_median"] = median(g["z_std_mm_median"])
            row["residual_rms_median_mm"] = median(g["residual_rms_median_mm"])
        else:
            row["turn_center_rms_3d_mm_median"] = median(g["turn_center_rms_3d_mm_median"])
            row["turn_center_rms_3d_mm_p95_over_drop_sets"] = percentile(g["turn_center_rms_3d_mm_median"], 95)
            row["circle_thickness_rms_mm_median"] = median(g["circle_thickness_rms_mm_median"])
            row["radius_mm_median"] = median(g["radius_mm_median"])
        out.append(row)
    return sorted(out, key=lambda r: (r["layout"], r["tag_method"], r["kind"], -int(r["keep_k"]), str(r["drop_category"])))


def plot_upper_lower(category_rows: list[dict], out: Path) -> None:
    import pandas as pd

    df = pd.DataFrame(category_rows)
    sub = df[(df["layout"] == "v4-io") & (df["tag_method"] == "T4")]
    if sub.empty:
        return
    with plt.rc_context(
        {
            "font.size": 16,
            "axes.titlesize": 18,
            "axes.labelsize": 17,
            "xtick.labelsize": 15,
            "ytick.labelsize": 15,
            "legend.fontsize": 14,
        }
    ):
        fig, axs = plt.subplots(1, 2, figsize=(13.2, 5.4))
        fig.subplots_adjust(left=0.08, right=0.98, bottom=0.16, top=0.78, wspace=0.28)
        styles = [
            ("upper_heavy", "More upper E-H removed", "#0072B2", "o"),
            ("lower_heavy", "More lower A-D removed", "#D55E00", "s"),
            ("balanced", "Balanced drop", "#009E73", "^"),
        ]
        handles = []
        labels = []
        for ax, kind, metric, title, ylabel in [
            (
                axs[0],
                "static",
                "d3_std_mm_median",
                "Static repeatability",
                r"Median $\sigma_{3D}$ [mm]",
            ),
            (
                axs[1],
                "roto",
                "turn_center_rms_3d_mm_median",
                "RotoArm geometric consistency",
                "Median turn-centre RMS [mm]",
            ),
        ]:
            g = sub[sub["kind"] == kind]
            for cat, label, color, marker in styles:
                h = g[g["drop_category"] == cat].sort_values("keep_k", ascending=False)
                if h.empty or metric not in h:
                    continue
                (line,) = ax.plot(
                    h["keep_k"],
                    h[metric],
                    marker=marker,
                    color=color,
                    linewidth=2.8,
                    markersize=7,
                    label=label,
                )
                if label not in labels:
                    handles.append(line)
                    labels.append(label)
            ax.set_title(title)
            ax.set_xlabel("Number of anchors kept $k$")
            ax.set_ylabel(ylabel)
            ax.set_xticks([7, 6, 5, 4])
            ax.set_xlim(7.25, 3.75)
            ax.grid(alpha=0.25, linewidth=0.8)
        fig.legend(handles, labels, loc="upper center", ncol=3, frameon=True, bbox_to_anchor=(0.5, 0.98))
        fig.savefig(out, dpi=220, bbox_inches="tight")
        plt.close(fig)


def write_summary_md(path: Path, summary_rows: list[dict], detail_rows: list[dict], category_rows: list[dict]) -> None:
    import pandas as pd

    df = pd.DataFrame(category_rows)
    lines = ["# Stratified Keep-k Replay\n\n"]
    lines.append("Method: exhaustive fixed dropped-set replay. Each row records which anchors were dropped, then aggregates by upper/lower/balanced composition.\n\n")
    lines.append("This is separate from the random MC5000 keep-k run and is meant to explain which missing-anchor patterns hurt most.\n\n")
    if df.empty:
        lines.append("No data.\n")
        path.write_text("".join(lines), encoding="utf-8")
        return
    v4 = df[(df["layout"] == "v4-io") & (df["tag_method"] == "T4")]
    lines.append("## V4-io / T4 Composition Snapshot\n\n")
    lines.append("| kind | keep_k | category | drop_sets | dropped_lower_med | dropped_upper_med | metric_mm |\n")
    lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: |\n")
    for _, r in v4.sort_values(["kind", "keep_k", "drop_category"], ascending=[True, False, True]).iterrows():
        if r["kind"] == "static":
            metric = r.get("d3_std_mm_median", float("nan"))
        else:
            metric = r.get("turn_center_rms_3d_mm_median", float("nan"))
        lines.append(
            f"| {r['kind']} | {int(r['keep_k'])} | {r['drop_category']} | {int(r['drop_sets'])} | "
            f"{float(r['dropped_lower_count_median']):.1f} | {float(r['dropped_upper_count_median']):.1f} | {float(metric):.1f} |\n"
        )
    comp = pd.DataFrame(summary_rows)
    if not comp.empty:
        lines.append("\n## V4-io / T4 Count-Split Detail\n\n")
        lines.append("| kind | keep_k | category | dropped_lower | dropped_upper | drop_sets | metric_mm |\n")
        lines.append("| --- | ---: | --- | ---: | ---: | ---: | ---: |\n")
        v4c = comp[(comp["layout"] == "v4-io") & (comp["tag_method"] == "T4")]
        for _, r in v4c.sort_values(["kind", "keep_k", "drop_category", "dropped_lower_count"], ascending=[True, False, True, True]).iterrows():
            if r["kind"] == "static":
                metric = r.get("d3_std_mm_median", float("nan"))
            else:
                metric = r.get("turn_center_rms_3d_mm_median", float("nan"))
            lines.append(
                f"| {r['kind']} | {int(r['keep_k'])} | {r['drop_category']} | "
                f"{int(r['dropped_lower_count'])} | {int(r['dropped_upper_count'])} | "
                f"{int(r['drop_sets'])} | {float(metric):.1f} |\n"
            )
    detail = pd.DataFrame(detail_rows)
    if not detail.empty:
        lines.append("\n## Worst V4-io / T4 Drop Sets\n\n")
        lines.append("| kind | keep_k | dropped_set | category | metric_mm |\n")
        lines.append("| --- | ---: | --- | --- | ---: |\n")
        v4d = detail[(detail["layout"] == "v4-io") & (detail["tag_method"] == "T4")].copy()
        metric_col = np.where(v4d["kind"] == "static", v4d.get("d3_std_mm_median"), v4d.get("turn_center_rms_3d_mm_median"))
        v4d["metric"] = metric_col
        for _, r in v4d.sort_values("metric", ascending=False).head(16).iterrows():
            lines.append(f"| {r['kind']} | {int(r['keep_k'])} | {r['dropped_set']} | {r['drop_category']} | {float(r['metric']):.1f} |\n")
    path.write_text("".join(lines), encoding="utf-8")


def run_kind(kind: str, tracks, replay, ranges, quality, available, keep_values, subset_batch, layout_name, tag_method) -> list[dict]:
    mod = run_kind.mod
    rows: list[dict] = []
    for keep_k in keep_values:
        keep_sets = generate_keep_sets(int(keep_k))
        for start in range(0, len(keep_sets), subset_batch):
            batch_sets = keep_sets[start : start + subset_batch]
            masks = make_fixed_masks(available, batch_sets)
            t0 = time.perf_counter()
            pos, rms, valid = replay.replay(ranges, quality, masks, int(keep_k), tag_method)
            elapsed = time.perf_counter() - t0
            print(
                f"[strat] layout={layout_name} Tx={tag_method} kind={kind} keep={keep_k} "
                f"subsets={start + len(batch_sets)}/{len(keep_sets)} elapsed={elapsed:.2f}s",
                flush=True,
            )
            for ri, keep_set in enumerate(batch_sets):
                track_rows = []
                for ti, track in enumerate(tracks):
                    if kind == "static":
                        s = mod.summarize_positions(pos[ti, ri], rms[ti, ri], valid[ti, ri])
                    else:
                        s = mod.summarize_roto_points(pos[ti, ri], valid[ti, ri])
                    track_rows.append(
                        {
                            "layout": layout_name,
                            "tag_method": tag_method,
                            "kind": kind,
                            "keep_k": int(keep_k),
                            "capture": track.capture_id,
                            "tag": track.tag,
                            **s,
                        }
                    )
                if kind == "static":
                    agg = mod.summarize_capture_set(track_rows)
                else:
                    agg = mod.summarize_roto_set(track_rows)
                rows.append(
                    {
                        "layout": layout_name,
                        "tag_method": tag_method,
                        "kind": kind,
                        "keep_k": int(keep_k),
                        **describe_drop_set(keep_set),
                        **agg,
                    }
                )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Exhaustive stratified keep-k replay for official Erlangen captures.")
    parser.add_argument("--official-root", default=str(OFFICIAL_ROOT))
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--layout-versions", default="all")
    parser.add_argument("--tag-methods", default="all")
    parser.add_argument("--kinds", default="static,roto")
    parser.add_argument("--keep-list", default="7,6,5,4")
    parser.add_argument("--subset-batch", type=int, default=96)
    parser.add_argument("--device", default="cuda")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-frames-per-track", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--shard-id", type=int, default=0)
    parser.add_argument("--combine-shards-only", action="store_true", help="combine existing shard CSVs and write unsuffixed final outputs")
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else Path(__file__).resolve().parents[1]
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    if args.combine_shards_only:
        shard_paths = sorted(tables_dir.glob("stratified_keepk_by_drop_set_shard*_of_*.csv"))
        if not shard_paths:
            raise FileNotFoundError(f"no stratified shard detail CSVs under {tables_dir}")
        import pandas as pd

        frames = [pd.read_csv(path) for path in shard_paths if path.stat().st_size > 0]
        if not frames:
            raise RuntimeError("all shard CSVs are empty")
        detail_df = pd.concat(frames, ignore_index=True)
        detail_rows = detail_df.to_dict("records")
        summary_rows = summarize_by_composition(detail_rows)
        category_rows = summarize_by_category(detail_rows)
        detail_path = tables_dir / "stratified_keepk_by_drop_set.csv"
        summary_path = tables_dir / "stratified_keepk_composition_summary.csv"
        category_path = tables_dir / "stratified_keepk_category_summary.csv"
        md_path = tables_dir / "stratified_keepk_summary.md"
        write_csv(detail_path, detail_rows)
        write_csv(summary_path, summary_rows)
        write_csv(category_path, category_rows)
        write_summary_md(md_path, summary_rows, detail_rows, category_rows)
        plot_upper_lower(category_rows, figs_dir / "stratified_keepk_upper_vs_lower.png")
        append_run_meta(
            out_dir,
            {
                "script": "stratified_keepk_replay.py",
                "mode": "combine_shards_only",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "args": vars(args),
                "shards": [str(p) for p in shard_paths],
                "outputs": [str(detail_path), str(summary_path), str(category_path), str(md_path)],
            },
        )
        print(f"[strat] combined shards={len(shard_paths)} rows={len(detail_rows)} summary_rows={len(summary_rows)} category_rows={len(category_rows)}")
        return 0

    mod = load_cuda_replay_module()
    run_kind.mod = mod
    layout_map = mod.available_layouts()
    layout_versions = parse_list(args.layout_versions, LAYOUT_VERSIONS)
    tag_methods = parse_list(args.tag_methods, TAG_METHODS)
    kinds = parse_list(args.kinds, ["static", "roto"])
    keep_values = [int(v.strip()) for v in args.keep_list.split(",") if v.strip()]

    static_tracks = mod.load_static_tracks(args.max_frames_per_track) if "static" in kinds else []
    roto_tracks = mod.load_roto_tracks(args.max_frames_per_track) if "roto" in kinds else []
    packed = {}
    if static_tracks:
        packed["static"] = (static_tracks, *mod.pack_tracks(static_tracks))
    if roto_tracks:
        packed["roto"] = (roto_tracks, *mod.pack_tracks(roto_tracks))

    all_blocks = [(layout, method, kind) for layout in layout_versions for method in tag_methods for kind in kinds]
    shard_blocks = [(i, b) for i, b in enumerate(all_blocks) if i % args.num_shards == args.shard_id]
    if not shard_blocks:
        raise ValueError("this shard has no blocks")

    detail_rows: list[dict] = []
    replay_cache = {}
    t_start = time.perf_counter()
    for idx, (layout_name, tag_method, kind) in shard_blocks:
        print(f"[strat] block={idx + 1}/{len(all_blocks)} shard={args.shard_id}/{args.num_shards} {layout_name} {tag_method} {kind}", flush=True)
        if layout_name not in replay_cache:
            layout = mod.load_layout_json(layout_map[layout_name], mod.layout_paths()[1])
            anchor_xyz = np.asarray(
                [[layout.anchors[aid].x_mm, layout.anchors[aid].y_mm, layout.anchors[aid].z_mm] for aid in range(8)],
                dtype=np.float32,
            )
            delays = np.asarray([layout.anchors[aid].d_anchor_mm + layout.tag_delay_mm for aid in range(8)], dtype=np.float32)
            sigmas = np.asarray([layout.anchors[aid].sigma_mm for aid in range(8)], dtype=np.float32)
            replay_cache[layout_name] = mod.CudaT4Replay(anchor_xyz, delays, sigmas, device=args.device)
        tracks, ranges, quality, available, _lengths = packed[kind]
        detail_rows.extend(run_kind(kind, tracks, replay_cache[layout_name], ranges, quality, available, keep_values, args.subset_batch, layout_name, tag_method))

    suffix = "" if args.num_shards == 1 else f"_shard{args.shard_id:02d}_of_{args.num_shards:02d}"
    detail_path = tables_dir / f"stratified_keepk_by_drop_set{suffix}.csv"
    summary_path = tables_dir / f"stratified_keepk_composition_summary{suffix}.csv"
    category_path = tables_dir / f"stratified_keepk_category_summary{suffix}.csv"
    md_path = tables_dir / f"stratified_keepk_summary{suffix}.md"
    summary_rows = summarize_by_composition(detail_rows)
    category_rows = summarize_by_category(detail_rows)
    write_csv(detail_path, detail_rows)
    write_csv(summary_path, summary_rows)
    write_csv(category_path, category_rows)
    write_summary_md(md_path, summary_rows, detail_rows, category_rows)
    plot_upper_lower(category_rows, figs_dir / f"stratified_keepk_upper_vs_lower{suffix}.png")

    append_run_meta(
        out_dir,
        {
            "script": "stratified_keepk_replay.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
            },
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES", ""),
            "layout_versions": layout_versions,
            "tag_methods": tag_methods,
            "kinds": kinds,
            "keep_values": keep_values,
            "elapsed_s": time.perf_counter() - t_start,
            "outputs": [str(detail_path), str(summary_path), str(category_path), str(md_path)],
            "cuda_replay_path": str(CUDA_REPLAY_PATH),
            "cuda_replay_sha256": sha256_file(CUDA_REPLAY_PATH),
        },
    )
    print(f"[strat] wrote detail={detail_path} rows={len(detail_rows)} summary_rows={len(summary_rows)} category_rows={len(category_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
