#!/usr/bin/env python3
import argparse
import csv
import json
import math
import re
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


ANCHORS = "ABCDEFGH"


def anchor_label(anchor_id: int) -> str:
    return ANCHORS[anchor_id] if 0 <= anchor_id < len(ANCHORS) else f"A{anchor_id}"


def parse_acrx(line: str):
    idx = line.find("ACRX;")
    if idx < 0:
        return None
    parts = line[idx:].strip().split(";")
    if len(parts) < 18 or parts[0] != "ACRX" or parts[1] != "1":
        return None
    try:
        sweep = int(parts[2])
        rx_id = int(parts[3])
        source_kind = parts[4]
        source_id = int(parts[5])
        source_addr = parts[6]
        raw_distance_mm = int(parts[7])
        rx_timestamp = int(parts[8])
        carrier_integrator = int(parts[9])
        first_path = int(parts[10])
        fp_amp1 = int(parts[11])
        fp_amp2 = int(parts[12])
        fp_amp3 = int(parts[13])
        max_growth_cir = int(parts[14])
        rx_pream_count = int(parts[15])
        std_noise = int(parts[16])
        max_noise = int(parts[17])
    except ValueError:
        return None
    fp_amp_sum = fp_amp1 + fp_amp2 + fp_amp3
    rx = anchor_label(rx_id)
    tx = anchor_label(source_id) if source_kind == "A" else f"{source_kind}{source_id}"
    return {
        "sweep": sweep,
        "rx_id": rx_id,
        "rx": rx,
        "source_kind": source_kind,
        "tx_id": source_id,
        "tx": tx,
        "source_addr": source_addr,
        "directed_link": f"{rx}<-{tx}",
        "raw_distance_mm": raw_distance_mm,
        "rx_timestamp": rx_timestamp,
        "carrier_integrator": carrier_integrator,
        "first_path": first_path,
        "fp_amp1": fp_amp1,
        "fp_amp2": fp_amp2,
        "fp_amp3": fp_amp3,
        "fp_amp_sum": fp_amp_sum,
        "max_growth_cir": max_growth_cir,
        "rx_pream_count": rx_pream_count,
        "std_noise": std_noise,
        "max_noise": max_noise,
        "snr_proxy": fp_amp_sum / max(std_noise, 1),
        "peak_over_fp": max_growth_cir / max(fp_amp_sum, 1),
        "noise_over_fp": std_noise / max(fp_amp_sum, 1),
        "raw_line": line.strip(),
    }


def collect_rows(sweep_dir: Path):
    rows = []
    for log_path in sorted(sweep_dir.glob("round_*/master.log")):
        m = re.search(r"round_([A-H])", str(log_path))
        staged_master = m.group(1) if m else ""
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                rec = parse_acrx(line)
                if rec:
                    rec["round_master"] = staged_master
                    rows.append(rec)
    return rows


def matrix_from_stats(stats: pd.DataFrame, column: str):
    mat = np.full((8, 8), np.nan)
    for _, row in stats.iterrows():
        rx_id = int(row["rx_id"])
        tx_id = int(row["tx_id"])
        if 0 <= rx_id < 8 and 0 <= tx_id < 8:
            mat[rx_id, tx_id] = row[column]
    return mat


def heatmap(stats: pd.DataFrame, column: str, title: str, path: Path, cmap="viridis", fmt=".1f"):
    mat = matrix_from_stats(stats, column)
    fig, ax = plt.subplots(figsize=(7.2, 6.0))
    im = ax.imshow(mat, cmap=cmap)
    ax.set_xticks(range(8), ANCHORS)
    ax.set_yticks(range(8), ANCHORS)
    ax.set_xlabel("TX / source anchor")
    ax.set_ylabel("RX / receiver anchor")
    ax.set_title(title)
    for i in range(8):
        for j in range(8):
            if i == j:
                ax.text(j, i, "-", ha="center", va="center", color="white", fontsize=8)
            elif not np.isnan(mat[i, j]):
                ax.text(j, i, format(mat[i, j], fmt), ha="center", va="center", color="white", fontsize=7)
    fig.colorbar(im, ax=ax, shrink=0.85)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def make_score(stats: pd.DataFrame):
    out = stats.copy()
    for col in ["peak_over_fp_median", "noise_over_fp_median", "valid_raw_distance_std"]:
        vals = out[col].to_numpy(dtype=float)
        finite = np.isfinite(vals)
        if finite.sum() <= 1:
            out[col + "_z"] = 0.0
            continue
        med = np.nanmedian(vals)
        mad = np.nanmedian(np.abs(vals - med))
        scale = 1.4826 * mad if mad > 0 else np.nanstd(vals)
        scale = scale if scale > 1e-9 else 1.0
        out[col + "_z"] = (vals - med) / scale
    out["snr_low_z"] = -out["snr_proxy_median"].rank(pct=True)
    out["compact_suspicion_score"] = (
        out["peak_over_fp_median_z"].clip(lower=0, upper=5)
        + out["noise_over_fp_median_z"].clip(lower=0, upper=5)
        + out["valid_raw_distance_std_z"].clip(lower=0, upper=5)
        + out["snr_low_z"].clip(lower=-1)
    )
    return out.sort_values("compact_suspicion_score", ascending=False)


def bar_top(stats: pd.DataFrame, path: Path):
    top = stats.head(16).iloc[::-1]
    fig, ax = plt.subplots(figsize=(9.5, 6.0))
    ax.barh(top["directed_link"], top["compact_suspicion_score"], color="#b8f37a")
    ax.set_xlabel("compact suspicion score")
    ax.set_title("COMPACT CIR suspicious directed links")
    ax.grid(axis="x", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=180)
    plt.close(fig)


def write_summary(out_dir: Path, rows: pd.DataFrame, stats: pd.DataFrame, sweep_dir: Path):
    top = stats.head(12)
    lines = [
        "# COMPACT CIR Sweep Analysis",
        "",
        f"- Sweep dir: `{sweep_dir}`",
        f"- ACRX compact samples: {len(rows)}",
        f"- Directed links observed: {stats['directed_link'].nunique()}",
        "",
        "Important limitation: COMPACT does not contain the full accumulator samples, so it cannot reproduce the full-CIR waveform/envelope plot. It can only draw feature maps from first-path amplitude, peak/growth, noise, and raw-distance stability.",
        "",
        "## Top Compact-Suspicious Directed Links",
        "",
        "| link | n | raw median mm | raw std mm | fp amp median | peak/fp median | snr proxy median | score |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for _, r in top.iterrows():
        lines.append(
            f"| {r['directed_link']} | {int(r['n'])} | {r['raw_distance_mm_median']:.1f} | "
            f"{r['valid_raw_distance_std']:.1f} | {r['fp_amp_sum_median']:.1f} | "
            f"{r['peak_over_fp_median']:.3f} | {r['snr_proxy_median']:.1f} | "
            f"{r['compact_suspicion_score']:.2f} |"
        )
    lines += [
        "",
        "## Generated Plots",
        "",
        "- `compact_count_heatmap.png`",
        "- `compact_fp_amp_sum_heatmap.png`",
        "- `compact_snr_proxy_heatmap.png`",
        "- `compact_peak_over_fp_heatmap.png`",
        "- `compact_raw_std_heatmap.png`",
        "- `compact_suspicion_score_heatmap.png`",
        "- `compact_suspicious_links.png`",
        "- `cir_pair_weights.csv`",
        "- `cir_pair_weights.json`",
    ]
    (out_dir / "analysis_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def pair_key(a: str, b: str) -> str:
    return "-".join(sorted([a, b]))


def export_pair_weights(stats: pd.DataFrame, out_dir: Path, min_weight: float, score_gain: float):
    rows = []
    for pair, g in stats.groupby(stats.apply(lambda r: pair_key(r["rx"], r["tx"]), axis=1)):
        directed = list(g["directed_link"])
        max_score = float(g["compact_suspicion_score"].max())
        mean_score = float(g["compact_suspicion_score"].mean())
        min_valid_rate = float(g["valid_raw_distance_rate"].min())
        max_peak_over_fp = float(g["peak_over_fp_median"].max())
        min_fp_amp = float(g["fp_amp_sum_median"].min())
        score = max_score
        if min_valid_rate < 0.6:
            score += (0.6 - min_valid_rate) * 3.0
        weight = math.exp(-score_gain * max(0.0, score))
        weight = max(min_weight, min(1.0, weight))
        rows.append(
            {
                "pair": pair,
                "weight": weight,
                "cir_badness": score,
                "max_directed_score": max_score,
                "mean_directed_score": mean_score,
                "min_valid_raw_distance_rate": min_valid_rate,
                "max_peak_over_fp": max_peak_over_fp,
                "min_fp_amp_sum": min_fp_amp,
                "directed_links": ",".join(directed),
            }
        )
    rows.sort(key=lambda r: (r["weight"], -r["cir_badness"]))
    csv_path = out_dir / "cir_pair_weights.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fields = [
            "pair",
            "weight",
            "cir_badness",
            "max_directed_score",
            "mean_directed_score",
            "min_valid_raw_distance_rate",
            "max_peak_over_fp",
            "min_fp_amp_sum",
            "directed_links",
        ]
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    payload = {
        "schema": "biospur_cir_pair_weights_v1",
        "units": "unitless",
        "policy": {
            "mode": "compact",
            "min_weight": min_weight,
            "score_gain": score_gain,
            "formula": "weight=max(min_weight, exp(-score_gain*(max_directed_score + valid_rate_penalty)))",
            "valid_rate_penalty": "if min_valid_rate < 0.6, add (0.6-min_valid_rate)*3",
        },
        "weights": {row["pair"]: row["weight"] for row in rows},
        "pairs": rows,
    }
    (out_dir / "cir_pair_weights.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sweep_dir", type=Path)
    ap.add_argument("--out-dir", type=Path)
    ap.add_argument("--min-weight", type=float, default=0.25)
    ap.add_argument("--score-gain", type=float, default=0.28)
    args = ap.parse_args()
    sweep_dir = args.sweep_dir
    out_dir = args.out_dir or sweep_dir / "compact_cir_analysis"
    out_dir.mkdir(parents=True, exist_ok=True)

    rows = collect_rows(sweep_dir)
    if not rows:
        raise SystemExit(f"No ACRX compact rows found in {sweep_dir}")
    df = pd.DataFrame(rows)
    df["raw_distance_valid"] = df["raw_distance_mm"] >= 0
    df["valid_raw_distance_mm"] = df["raw_distance_mm"].where(df["raw_distance_valid"], np.nan)
    df.to_csv(out_dir / "compact_acrx_samples.csv", index=False)

    grouped = df.groupby(["rx_id", "rx", "tx_id", "tx", "directed_link"], as_index=False)
    stats = grouped.agg(
        n=("directed_link", "size"),
        valid_raw_distance_rate=("raw_distance_valid", "mean"),
        raw_distance_mm_median=("raw_distance_mm", "median"),
        raw_distance_mm_mean=("raw_distance_mm", "mean"),
        raw_distance_mm_std=("raw_distance_mm", "std"),
        valid_raw_distance_median=("valid_raw_distance_mm", "median"),
        valid_raw_distance_std=("valid_raw_distance_mm", "std"),
        fp_amp_sum_median=("fp_amp_sum", "median"),
        max_growth_cir_median=("max_growth_cir", "median"),
        std_noise_median=("std_noise", "median"),
        max_noise_median=("max_noise", "median"),
        snr_proxy_median=("snr_proxy", "median"),
        peak_over_fp_median=("peak_over_fp", "median"),
        noise_over_fp_median=("noise_over_fp", "median"),
        rx_pream_count_median=("rx_pream_count", "median"),
    )
    stats["raw_distance_mm_std"] = stats["raw_distance_mm_std"].fillna(0.0)
    stats["valid_raw_distance_std"] = stats["valid_raw_distance_std"].fillna(0.0)
    stats = make_score(stats)
    stats.to_csv(out_dir / "compact_pair_stats.csv", index=False)

    heatmap(stats, "n", "COMPACT ACRX sample count", out_dir / "compact_count_heatmap.png", cmap="magma", fmt=".0f")
    heatmap(stats, "fp_amp_sum_median", "COMPACT first-path amplitude sum median", out_dir / "compact_fp_amp_sum_heatmap.png", cmap="viridis", fmt=".0f")
    heatmap(stats, "snr_proxy_median", "COMPACT SNR proxy median: fp_amp_sum / std_noise", out_dir / "compact_snr_proxy_heatmap.png", cmap="viridis", fmt=".0f")
    heatmap(stats, "peak_over_fp_median", "COMPACT peak/growth over first-path amplitude", out_dir / "compact_peak_over_fp_heatmap.png", cmap="plasma", fmt=".2f")
    heatmap(stats, "valid_raw_distance_std", "COMPACT valid raw-distance std per directed link", out_dir / "compact_raw_std_heatmap.png", cmap="plasma", fmt=".1f")
    heatmap(stats, "valid_raw_distance_rate", "COMPACT valid raw-distance rate", out_dir / "compact_valid_rate_heatmap.png", cmap="viridis", fmt=".2f")
    heatmap(stats, "compact_suspicion_score", "COMPACT feature suspicion score", out_dir / "compact_suspicion_score_heatmap.png", cmap="inferno", fmt=".1f")
    bar_top(stats, out_dir / "compact_suspicious_links.png")
    export_pair_weights(stats, out_dir, args.min_weight, args.score_gain)
    write_summary(out_dir, df, stats, sweep_dir)
    print(f"[ok] samples={len(df)} directed_links={stats['directed_link'].nunique()} out={out_dir}")


if __name__ == "__main__":
    main()
