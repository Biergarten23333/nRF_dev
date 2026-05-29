#!/usr/bin/env python3
"""Task 4: bootstrap confidence intervals for official headline metrics."""

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


def bootstrap_stat(values: np.ndarray, fn, rng: np.random.Generator, n_boot: int) -> tuple[float, float, float]:
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return np.nan, np.nan, np.nan
    point = float(fn(values))
    if values.size == 1:
        return point, point, point
    idx = rng.integers(0, values.size, size=(n_boot, values.size))
    samples = values[idx]
    boot = np.apply_along_axis(fn, 1, samples)
    lo, hi = np.nanpercentile(boot, [2.5, 97.5])
    return point, float(lo), float(hi)


def rms(values: np.ndarray) -> float:
    return float(np.sqrt(np.nanmean(np.asarray(values, dtype=float) ** 2)))


def p95(values: np.ndarray) -> float:
    return float(np.nanpercentile(values, 95))


def add_metric(rows: list[dict], *, metric: str, version: str, eval_set: str, values: np.ndarray, fn_name: str, fn, rng, n_boot, source: str, unit: str = "mm") -> None:
    point, lo, hi = bootstrap_stat(values, fn, rng, n_boot)
    rows.append(
        {
            "metric": metric,
            "version": version,
            "eval_set": eval_set,
            "stat": fn_name,
            "point": point,
            "ci_low": lo,
            "ci_high": hi,
            "unit": unit,
            "n_values": int(np.isfinite(values).sum()),
            "n_boot": n_boot,
            "method": "nonparametric_bootstrap",
            "source": source,
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


def maybe_add_mc_metrics(rows: list[dict], mc_dir: Path, rng, n_boot: int) -> int:
    count = 0
    for p in sorted(mc_dir.glob("*/*/*/*_keepk_summary.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        rel = p.relative_to(mc_dir).parts
        if len(rel) < 4:
            continue
        version, method, kind = rel[0], rel[1], rel[2]
        for keep, g in df.groupby("keep_k"):
            # Prefer robust error columns if present; tolerate schema evolution.
            for col in ["rms_3d_mm", "median_3d_mm", "p95_3d_mm", "mean_3d_mm"]:
                if col in g.columns:
                    vals = g[col].to_numpy(dtype=float)
                    add_metric(
                        rows,
                        metric=f"mc_keep{int(keep)}_{kind}_{method}_{col}",
                        version=version,
                        eval_set=f"{kind}/keep{int(keep)}",
                        values=vals,
                        fn_name="median",
                        fn=np.nanmedian,
                        rng=rng,
                        n_boot=n_boot,
                        source=str(p),
                    )
                    count += 1
    return count


def plot_ci(path: Path, df: pd.DataFrame) -> None:
    pick = df[df["metric"].isin(["layout_rigid_3d_error", "static_radial_p95", "roto_abs_deltaR_error", "roto_turn_center_rms"])]
    if pick.empty:
        return
    labels = (pick["version"] + " " + pick["metric"] + " " + pick["eval_set"]).tolist()[:60]
    y = np.arange(len(labels))
    x = pick["point"].to_numpy(dtype=float)[: len(labels)]
    lo = pick["ci_low"].to_numpy(dtype=float)[: len(labels)]
    hi = pick["ci_high"].to_numpy(dtype=float)[: len(labels)]
    fig, ax = plt.subplots(figsize=(10, max(6, len(labels) * 0.25)))
    ax.errorbar(x, y, xerr=[x - lo, hi - x], fmt="o", ms=3)
    ax.set_yticks(y, labels, fontsize=7)
    ax.set_xlabel("metric value mm")
    ax.set_title("Selected bootstrap confidence intervals")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--n-boot", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--include-mc", action="store_true")
    args = parser.parse_args()

    rng = np.random.default_rng(args.seed)
    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else official_root / "Analysis/official_extra_analysis"
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)
    solver_tables = official_root / "solver/outputs/v1_to_v4_io_field_check/tables"

    rows: list[dict] = []

    for eval_set, rel in [("all8", "layout_abs_errors_all8.csv"), ("noG", "layout_abs_errors_noG.csv")]:
        path = tables_dir / rel
        if path.exists():
            df = pd.read_csv(path)
            for version, g in df.groupby("version"):
                add_metric(rows, metric="layout_rigid_3d_error", version=version, eval_set=eval_set, values=g["err_3d_mm"].to_numpy(), fn_name="rms", fn=rms, rng=rng, n_boot=args.n_boot, source=str(path))
                add_metric(rows, metric="layout_rigid_horizontal_error", version=version, eval_set=eval_set, values=g["err_horizontal_mm"].to_numpy(), fn_name="rms", fn=rms, rng=rng, n_boot=args.n_boot, source=str(path))
                add_metric(rows, metric="layout_rigid_vertical_error", version=version, eval_set=eval_set, values=g["err_vertical_mm"].to_numpy(), fn_name="rms", fn=rms, rng=rng, n_boot=args.n_boot, source=str(path))

    layout_resid = solver_tables / "layout_residuals_per_pair.csv"
    if layout_resid.exists():
        df = pd.read_csv(layout_resid)
        for (version, eval_set), g in df.groupby(["version", "eval_set"]):
            add_metric(rows, metric="layout_pair_residual", version=version, eval_set=eval_set, values=g["residual_mm"].to_numpy(), fn_name="rms", fn=rms, rng=rng, n_boot=args.n_boot, source=str(layout_resid))
            add_metric(rows, metric="layout_pair_abs_residual", version=version, eval_set=eval_set, values=g["abs_residual_mm"].to_numpy(), fn_name="p95", fn=p95, rng=rng, n_boot=args.n_boot, source=str(layout_resid))

    static_path = solver_tables / "static_all_captures.csv"
    if static_path.exists():
        df = pd.read_csv(static_path)
        for version, g in df.groupby("version"):
            add_metric(rows, metric="static_radial_p95", version=version, eval_set="all_static_sessions", values=g["radial_p95"].to_numpy(), fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(static_path))
            add_metric(rows, metric="static_D3_std", version=version, eval_set="all_static_sessions", values=g["D3_std"].to_numpy(), fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(static_path))

    tag_abs_path = tables_dir / "tag_abs_errors_per_session.csv"
    if tag_abs_path.exists():
        df = pd.read_csv(tag_abs_path)
        for (version, eval_set), g in df.groupby(["version", "eval_set"]):
            add_metric(rows, metric="tag_absolute_3d_error", version=version, eval_set=eval_set, values=g["err_3d_mm"].to_numpy(), fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(tag_abs_path))
            add_metric(rows, metric="tag_absolute_3d_error", version=version, eval_set=eval_set, values=g["err_3d_mm"].to_numpy(), fn_name="p95", fn=p95, rng=rng, n_boot=args.n_boot, source=str(tag_abs_path))
            add_metric(rows, metric="tag_absolute_vertical_error", version=version, eval_set=eval_set, values=g["err_vertical_mm"].to_numpy(), fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(tag_abs_path))

    roto_path = solver_tables / "roto_physical_consistency_all.csv"
    if roto_path.exists():
        df = pd.read_csv(roto_path)
        for version, g in df.groupby("version"):
            add_metric(rows, metric="roto_abs_deltaR_error", version=version, eval_set="roto_pairs", values=g["abs_deltaR_error_mm"].to_numpy(), fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(roto_path))
            add_metric(rows, metric="roto_abs_deltaR_error", version=version, eval_set="roto_pairs", values=g["abs_deltaR_error_mm"].to_numpy(), fn_name="p95", fn=p95, rng=rng, n_boot=args.n_boot, source=str(roto_path))
            add_metric(rows, metric="roto_inner_outer_center_sep", version=version, eval_set="roto_pairs", values=g["inner_outer_center_sep_mm"].to_numpy(), fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(roto_path))
            turn_vals = np.concatenate([g["inner_turn_center_rms_3d_mm"].to_numpy(), g["outer_turn_center_rms_3d_mm"].to_numpy()])
            add_metric(rows, metric="roto_turn_center_rms", version=version, eval_set="roto_tags", values=turn_vals, fn_name="median", fn=np.nanmedian, rng=rng, n_boot=args.n_boot, source=str(roto_path))

    mc_count = 0
    if args.include_mc:
        mc_count = maybe_add_mc_metrics(rows, official_root / "Analysis/Monte-Carlo-Simulation", rng, args.n_boot)

    out_csv = tables_dir / "metric_confidence_intervals.csv"
    write_csv(out_csv, rows)
    df = pd.DataFrame(rows)
    md = ["# Bootstrap Confidence Intervals\n\n"]
    md.append(f"n_boot={args.n_boot}, seed={args.seed}. MC metrics included: {bool(args.include_mc)} ({mc_count} metric groups).\n\n")
    if not df.empty:
        headline = df[
            (df["version"].isin(["v4-io"]))
            & (df["metric"].isin(["layout_rigid_3d_error", "static_radial_p95", "roto_abs_deltaR_error", "roto_turn_center_rms"]))
            | ((df["version"] == "v4-io") & (df["metric"].isin(["tag_absolute_3d_error", "tag_absolute_vertical_error"])))
        ].copy()
        md.append("## V4-io headline CIs\n\n")
        md.append("| metric | eval_set | stat | point | ci_low | ci_high | unit | n_values |\n")
        md.append("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for _, r in headline.iterrows():
            md.append(f"| {r['metric']} | {r['eval_set']} | {r['stat']} | {r['point']:.2f} | {r['ci_low']:.2f} | {r['ci_high']:.2f} | {r['unit']} | {int(r['n_values'])} |\n")
    (tables_dir / "metric_confidence_intervals.md").write_text("".join(md))
    if not df.empty:
        plot_ci(figs_dir / "bootstrap_confidence_intervals.png", df)

    sources = [solver_tables / "layout_residuals_per_pair.csv", solver_tables / "static_all_captures.csv", solver_tables / "roto_physical_consistency_all.csv", tables_dir / "tag_abs_errors_per_session.csv"]
    append_run_meta(
        out_dir,
        {
            "script": "bootstrap_ci.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
            },
            "sources": {str(p): sha256_file(p) for p in sources if p.exists()},
            "metric_rows": len(rows),
            "mc_metric_groups": mc_count,
        },
    )
    print(f"[bootstrap] wrote {out_csv} rows={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
