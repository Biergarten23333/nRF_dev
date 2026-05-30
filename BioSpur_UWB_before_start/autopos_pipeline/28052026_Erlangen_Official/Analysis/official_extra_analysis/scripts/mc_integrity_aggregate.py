#!/usr/bin/env python3
"""Integrity checks and aggregate plots for the CUDA keep-k MC run."""

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
LAYOUTS = ["v1-old", "v2", "v3-lite", "v3-full", "v4-io"]
TAG_METHODS = ["T1", "T2", "T3", "T4"]
KINDS = ["static", "roto"]
KEEP_LIST = [8, 7, 6, 5, 4]


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


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("")
        return
    fieldnames = []
    seen = set()
    for row in rows:
        for key in row.keys():
            if key not in seen:
                fieldnames.append(key)
                seen.add(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def expected_path(mc_dir: Path, layout: str, method: str, kind: str) -> Path:
    return mc_dir / layout / method / kind / f"{kind}_keepk_summary.csv"


def check_block(path: Path, layout: str, method: str, kind: str) -> tuple[list[dict], list[dict]]:
    issues = []
    rows = []
    if not path.exists():
        issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": "missing_csv", "path": str(path)})
        return rows, issues
    if path.stat().st_size == 0:
        issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": "empty_csv", "path": str(path)})
        return rows, issues
    try:
        df = pd.read_csv(path)
    except Exception as exc:
        issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": f"read_failed:{exc}", "path": str(path)})
        return rows, issues
    if df.empty:
        issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": "empty_dataframe", "path": str(path)})
        return rows, issues
    if "keep_k" not in df.columns:
        issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": "missing_keep_k_column", "path": str(path)})
        return rows, issues
    keep_present = sorted((int(k) for k in df["keep_k"].dropna().unique()), reverse=True)
    if keep_present != KEEP_LIST:
        issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": f"bad_keep_set:{keep_present}", "path": str(path)})
    for col in df.columns:
        if df[col].dtype.kind in "fc" and df[col].isna().any():
            issues.append({"severity": "WARN", "layout": layout, "tag_method": method, "kind": kind, "issue": f"nan_in:{col}", "path": str(path)})
    for _, row in df.iterrows():
        rec = row.to_dict()
        rec["layout"] = layout
        rec["tag_method"] = method
        rec["kind"] = kind
        rec["source_csv"] = str(path)
        rows.append(rec)
        keep = int(row["keep_k"])
        repeats = int(row.get("repeats", -1))
        if keep == 8 and repeats != 1:
            issues.append({"severity": "WARN", "layout": layout, "tag_method": method, "kind": kind, "issue": f"keep8_repeats_not_1:{repeats}", "path": str(path)})
        if keep != 8 and repeats < 5000:
            issues.append({"severity": "ERROR", "layout": layout, "tag_method": method, "kind": kind, "issue": f"keep{keep}_repeats_lt_5000:{repeats}", "path": str(path)})
    return rows, issues


def plot_static(df: pd.DataFrame, out: Path) -> None:
    if df.empty or "d3_std_mm_median" not in df.columns:
        return
    fig, axs = plt.subplots(1, len(LAYOUTS), figsize=(18, 4), sharey=True)
    for ax, layout in zip(axs, LAYOUTS):
        sub = df[(df["layout"] == layout) & (df["kind"] == "static")]
        for method, g in sub.groupby("tag_method"):
            g = g.sort_values("keep_k")
            ax.plot(g["keep_k"], g["d3_std_mm_median"], marker="o", label=method)
        ax.set_title(layout)
        ax.set_xlabel("keep-k")
        ax.invert_xaxis()
        ax.grid(alpha=0.25)
    axs[0].set_ylabel("median static D3 std mm")
    axs[-1].legend(loc="upper left", bbox_to_anchor=(1.02, 1.0))
    fig.suptitle("MC keep-k static repeatability")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def plot_roto(df: pd.DataFrame, out: Path) -> None:
    if df.empty or "turn_center_rms_3d_mm_median" not in df.columns:
        return
    fig, axs = plt.subplots(1, len(LAYOUTS), figsize=(18, 4), sharey=True)
    for ax, layout in zip(axs, LAYOUTS):
        sub = df[(df["layout"] == layout) & (df["kind"] == "roto")]
        for method, g in sub.groupby("tag_method"):
            g = g.sort_values("keep_k")
            ax.plot(g["keep_k"], g["turn_center_rms_3d_mm_median"], marker="o", label=method)
        ax.set_title(layout)
        ax.set_xlabel("keep-k")
        ax.invert_xaxis()
        ax.grid(alpha=0.25)
    axs[0].set_ylabel("median roto turn-center RMS mm")
    axs[-1].legend(loc="upper left", bbox_to_anchor=(1.02, 1.0))
    fig.suptitle("MC keep-k roto consistency")
    fig.tight_layout()
    fig.savefig(out, dpi=150)
    plt.close(fig)


def fmt_mm(value) -> str:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "-"
    return "-" if not np.isfinite(v) else f"{v:.3f}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--official-root", default="autopos_pipeline/28052026_Erlangen_Official")
    parser.add_argument("--mc-dir", default=None)
    parser.add_argument("--out-dir", default=None)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    official_root = Path(args.official_root).resolve()
    out_dir = Path(args.out_dir).resolve() if args.out_dir else official_root / "Analysis/official_extra_analysis"
    mc_dir = Path(args.mc_dir).resolve() if args.mc_dir else official_root / "Analysis/Monte-Carlo-Simulation"
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    figs_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    issues = []
    for layout in LAYOUTS:
        for method in TAG_METHODS:
            for kind in KINDS:
                block_rows, block_issues = check_block(expected_path(mc_dir, layout, method, kind), layout, method, kind)
                rows.extend(block_rows)
                issues.extend(block_issues)

    write_csv(tables_dir / "mc_keepk_combined_summary.csv", rows)
    write_csv(tables_dir / "mc_integrity_issues.csv", issues)
    df = pd.DataFrame(rows)
    if not df.empty:
        plot_static(df, figs_dir / "mc_keepk_static_curves.png")
        plot_roto(df, figs_dir / "mc_keepk_roto_curves.png")

    complete_blocks = len({(r["layout"], r["tag_method"], r["kind"]) for r in rows})
    ok = complete_blocks == 40 and not any(i["severity"] == "ERROR" for i in issues)
    md = ["# MC Keep-k Integrity\n\n"]
    md.append(f"Complete blocks: {complete_blocks}/40\n\n")
    md.append(f"Status: {'PASS' if ok else 'INCOMPLETE_OR_FAIL'}\n\n")
    if issues:
        md.append("## Issues\n\n")
        md.append("| severity | layout | tag_method | kind | issue |\n")
        md.append("| --- | --- | --- | --- | --- |\n")
        for i in issues[:200]:
            md.append(f"| {i['severity']} | {i['layout']} | {i['tag_method']} | {i['kind']} | {i['issue']} |\n")
    else:
        md.append("No integrity issues detected.\n")
    if not df.empty:
        md.append("\n## V4-io / T4 Headline Snapshot\n\n")
        md.append("| kind | keep_k | repeats | static_d3_std_median_mm | roto_turn_center_rms_median_mm | roto_turn_center_p95_median_mm |\n")
        md.append("| --- | --- | --- | --- | --- | --- |\n")
        sub = df[(df["layout"] == "v4-io") & (df["tag_method"] == "T4")].sort_values(["kind", "keep_k"], ascending=[True, False])
        for _, r in sub.iterrows():
            md.append(
                f"| {r['kind']} | {int(r['keep_k'])} | {int(r['repeats'])} | "
                f"{fmt_mm(r.get('d3_std_mm_median', np.nan))} | "
                f"{fmt_mm(r.get('turn_center_rms_3d_mm_median', np.nan))} | "
                f"{fmt_mm(r.get('turn_center_p95_3d_mm_median', np.nan))} |\n"
            )
    (tables_dir / "mc_integrity_summary.md").write_text("".join(md))

    append_run_meta(
        out_dir,
        {
            "script": "mc_integrity_aggregate.py",
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "args": vars(args),
            "seed": args.seed,
            "axis_convention": {
                "layout_horizontal_axes": LAYOUT_HORIZONTAL_AXES,
                "layout_vertical_axis": LAYOUT_VERTICAL_AXIS,
                "layout_upper_layer_sign": LAYOUT_UPPER_LAYER_SIGN,
                "reported_height_mm": REPORTED_HEIGHT_EXPR,
            },
            "mc_dir": str(mc_dir),
            "mc_dir_manifest_sha256": sha256_file(mc_dir / "dual_gpu_run_manifest.json") if (mc_dir / "dual_gpu_run_manifest.json").exists() else "",
            "complete_blocks": complete_blocks,
            "issue_count": len(issues),
            "status": "PASS" if ok else "INCOMPLETE_OR_FAIL",
        },
    )
    print(f"[mc] complete_blocks={complete_blocks}/40 status={'PASS' if ok else 'INCOMPLETE_OR_FAIL'}")
    print(f"[mc] wrote {tables_dir / 'mc_integrity_summary.md'}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
