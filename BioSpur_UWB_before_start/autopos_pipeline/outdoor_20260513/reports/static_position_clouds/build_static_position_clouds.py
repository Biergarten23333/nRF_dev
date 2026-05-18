#!/usr/bin/env python3
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np


ROOT = Path(__file__).resolve().parents[4]
DATA = ROOT / "autopos_pipeline" / "outdoor_20260513"
OUT = DATA / "reports" / "static_position_clouds"
RUN_CLEAN = DATA / "run_clean_full_compare.py"
FULL = DATA / "FULL-COMPARE-1000"
LAYOUT_JSON = FULL / "v4-io" / "layout.json"
SIGMA_JSON = FULL / "tables" / "anchor_sigma.json"
STATIC_SUMMARY = FULL / "v4-io" / "static_all_captures.csv"

ANCHORS = "ABCDEFGH"
SESSIONS = ["ID06", "ID08", "ID07"]
SESSION_TITLES = {
    "ID06": "Compact example: ID06\nedge, high, BCGF",
    "ID08": "Worst example: ID08\nedge, mid, CDHG",
    "ID07": "Weak CDHG example: ID07\nedge, low, CDHG",
}


def load_run_clean():
    spec = importlib.util.spec_from_file_location("run_clean_full_compare", RUN_CLEAN)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {RUN_CLEAN}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_v4_layout(run_clean):
    obj = json.loads(LAYOUT_JSON.read_text(encoding="utf-8"))
    anchors = sorted(obj["anchors"], key=lambda r: int(r["id"]))
    x = np.asarray(
        [[float(a["x_mm"]), float(a["y_mm"]), float(a["z_mm"])] for a in anchors],
        dtype=float,
    )
    dly = np.asarray([float(a["d_anchor_mm"]) for a in anchors], dtype=float)
    return run_clean.Layout(
        version="v4-io",
        label="V4-io",
        x=x,
        dly=dly,
        extra={"source_layout": str(LAYOUT_JSON)},
        tag_delay_mm=float(obj.get("tag_delay_mm", 0.0) or 0.0),
    )


def read_static_summary() -> dict[str, dict[str, str]]:
    with STATIC_SUMMARY.open(newline="", encoding="utf-8") as f:
        return {r["ID"]: r for r in csv.DictReader(f) if r.get("status") == "ok"}


def latest_dirs(root: Path, prefix: str) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for p in root.iterdir():
        if not p.is_dir() or not p.name.startswith(prefix):
            continue
        key = p.name.split("_", 1)[0]
        if key not in out or p.name > out[key].name:
            out[key] = p
    return out


def solve_session(run_clean, eval_mod, layout, sid: str) -> tuple[np.ndarray, list[dict]]:
    cap_dirs = latest_dirs(DATA / "Static_Test", "ID")
    if sid not in cap_dirs:
        raise FileNotFoundError(f"missing static session {sid}")
    frames = []
    by_peer = run_clean.load_frames_by_peer(cap_dirs[sid] / "tr_all.csv")
    for peer_frames in by_peer.values():
        frames.extend(peer_frames)
    frames = sorted(frames, key=lambda r: (r["t"], r["sweep"]))

    global_xyz, global_delay = run_clean.layout_to_global(eval_mod, layout, list(range(8)))
    last = None
    positions = []
    rows = []
    for fr in frames:
        obs = [(int(a), float(r)) for a, r in fr["obs"] if 0 <= int(a) < 8 and float(r) > 0]
        if len(obs) < 4:
            continue
        pos = run_clean.solve_position_fast(
            obs,
            global_xyz,
            global_delay,
            eval_mod.ANCHOR_SIGMA,
            last,
            layout.tag_delay_mm,
        )
        if not np.all(np.isfinite(pos)):
            continue
        last = pos
        positions.append(pos)
        rows.append({
            "ID": sid,
            "peer": fr.get("peer", ""),
            "sweep": fr.get("sweep", ""),
            "t": fr.get("t", ""),
            "anchor_count": len(obs),
            "x_mm": float(pos[0]),
            "y_mm": float(pos[1]),
            "z_mm": float(pos[2]),
        })
    return np.asarray(positions, dtype=float), rows


def write_csv(path: Path, rows: list[dict]) -> None:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    run_clean = load_run_clean()
    eval_mod = run_clean.load_eval_module()
    sigma_obj = json.loads(SIGMA_JSON.read_text(encoding="utf-8"))
    eval_mod.ANCHOR_SIGMA = {i: float(sigma_obj[ANCHORS[i]]) for i in range(8)}
    layout = load_v4_layout(run_clean)
    summary = read_static_summary()

    all_rows: list[dict] = []
    solved: dict[str, np.ndarray] = {}
    solved_rows: dict[str, list[dict]] = {}
    for sid in SESSIONS:
        pts, rows = solve_session(run_clean, eval_mod, layout, sid)
        if pts.size == 0:
            raise RuntimeError(f"no solved points for {sid}")
        center = np.mean(pts, axis=0)
        solved[sid] = pts
        solved_rows[sid] = rows
        meta = summary.get(sid, {})
        for row, p in zip(rows, pts):
            d = p - center
            row.update({
                "mean_x_mm": float(center[0]),
                "mean_y_mm": float(center[1]),
                "mean_z_mm": float(center[2]),
                "dx_mm": float(d[0]),
                "dy_mm": float(d[1]),
                "dz_mm": float(d[2]),
                "location": meta.get("location", ""),
                "height": meta.get("height", ""),
                "facing": meta.get("facing", ""),
                "D3_std_summary_mm": meta.get("D3_std", ""),
                "Z_std_summary_mm": meta.get("Z_std", ""),
                "pct_ge8_summary": meta.get("pct_ge8", ""),
            })
            all_rows.append(row)

    write_csv(OUT / "static_position_cloud_examples_points.csv", all_rows)

    fig, axes = plt.subplots(3, 3, figsize=(10.8, 9.0), constrained_layout=True)
    axis_lim = 200.0
    row_defs = [
        ("XY", "dx_mm", "dy_mm", "X deviation (mm)", "Y deviation (mm)", 0, 1),
        ("XZ", "dx_mm", "dz_mm", "X deviation (mm)", "Z deviation (mm)", 0, 2),
        ("YZ", "dy_mm", "dz_mm", "Y deviation (mm)", "Z deviation (mm)", 1, 2),
    ]
    scatter_ref = None
    for col, sid in enumerate(SESSIONS):
        pts = solved[sid]
        center = np.mean(pts, axis=0)
        dev = pts - center
        counts = np.asarray([float(r["anchor_count"]) for r in solved_rows[sid]], dtype=float)
        meta = summary[sid]
        for row_idx, (_name, _xkey, _ykey, xlabel, ylabel, ix, iy) in enumerate(row_defs):
            ax = axes[row_idx, col]
            scatter_ref = ax.scatter(
                dev[:, ix],
                dev[:, iy],
                c=counts,
                s=8,
                cmap="viridis",
                vmin=4,
                vmax=8,
                alpha=0.55,
                linewidths=0,
            )
            ax.axhline(0, color="0.75", lw=0.8)
            ax.axvline(0, color="0.75", lw=0.8)
            ax.set_xlim(-axis_lim, axis_lim)
            ax.set_ylim(-axis_lim, axis_lim)
            ax.set_aspect("equal", adjustable="box")
            ax.grid(True, color="0.9", lw=0.5)
            ax.set_xlabel(xlabel, fontsize=8)
            ax.set_ylabel(ylabel, fontsize=8)
            if row_idx == 0:
                ax.set_title(
                    f"{SESSION_TITLES[sid]}\n"
                    f"3D std {float(meta['D3_std']):.1f} mm, Z std {float(meta['Z_std']):.1f} mm",
                    fontsize=10,
                )
    if scatter_ref is not None:
        cbar = fig.colorbar(scatter_ref, ax=axes, shrink=0.82, pad=0.012)
        cbar.set_label("anchors used per epoch")
        cbar.set_ticks([4, 5, 6, 7, 8])
    fig.suptitle(
        "Representative V4-io Static Position Clouds\n"
        "Per-frame solved positions after subtracting each session mean; common +/-200 mm axes",
        fontsize=13,
    )
    fig.savefig(OUT / "static_position_cloud_examples.png", dpi=300)
    fig.savefig(DATA / "reports" / "static_position_cloud_examples.png", dpi=300)
    write_csv(DATA / "reports" / "static_position_cloud_examples_points.csv", all_rows)
    print(OUT / "static_position_cloud_examples.png")
    print(OUT / "static_position_cloud_examples_points.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
