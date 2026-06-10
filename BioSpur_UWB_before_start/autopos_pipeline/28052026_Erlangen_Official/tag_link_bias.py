#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from scripts.audit_helpers import ANCHOR_LABELS, markdown_table, valid_mask
from scripts.phase1_common import (
    DYNAMIC_EXCLUDE_CAPTURE_IDS,
    anchor_coord_map,
    load_data_config,
    load_phase1_data,
    mapping_as_anchor_label,
    save_markdown_fragment,
    tag_coord_map,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase 1.3 static tag link-level bias diagnostics.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = load_phase1_data(args.data_dir, args.out_dir)
    cfg = load_data_config(data.data_dir)
    mapping = {int(k): v for k, v in cfg.ANCHOR_ID_TO_LABEL.items()}
    anchors = anchor_coord_map(data.anchor_truth)
    tags = tag_coord_map(data.tag_truth)
    truth_meta = data.tag_truth.set_index("ID").to_dict("index")
    meta = data.static_meta.set_index("ID").to_dict("index") if not data.static_meta.empty else {}

    work = data.static_df[valid_mask(data.static_df)].copy()
    work["range_mm"] = pd.to_numeric(work["range_mm"], errors="coerce")
    grouped = work.groupby(["capture_id", "anchor_id"])
    rows = []
    for (capture_id, anchor_id), g in grouped:
        if capture_id not in tags:
            continue
        label = mapping_as_anchor_label(int(anchor_id), mapping)
        measured = float(g["range_mm"].median())
        true_d = float(np.linalg.norm(tags[capture_id] - anchors[label]))
        source = truth_meta[capture_id]["tag_truth_source"]
        rows.append(
            {
                "position": capture_id,
                "anchor_id": int(anchor_id),
                "anchor": label,
                "n_valid": int(len(g)),
                "median_range_mm": measured,
                "vicon_distance_mm": true_d,
                "bias_mm": measured - true_d,
                "range_std_mm": float(g["range_mm"].std(ddof=1)),
                "quality_median": float(pd.to_numeric(g["quality_percent"], errors="coerce").median()) if "quality_percent" in g else np.nan,
                "tag_truth_source": source,
                "truth_direct_motive": bool(source == "motive_iantenna"),
                "truth_reconstructed": bool(source != "motive_iantenna"),
                "location": meta.get(capture_id, {}).get("location", ""),
                "height": meta.get(capture_id, {}).get("height", ""),
                "facing": meta.get(capture_id, {}).get("facing", ""),
            }
        )
    link_df = pd.DataFrame(rows).sort_values(["position", "anchor_id"])
    link_df.to_csv(data.tables_dir / "03_tag_link_bias_links.csv", index=False)

    matrix = link_df.pivot(index="position", columns="anchor", values="bias_mm").reindex(columns=ANCHOR_LABELS)
    matrix.to_csv(data.tables_dir / "03_tag_link_bias_matrix.csv")
    noise = link_df[["position", "anchor", "range_std_mm", "n_valid"]].copy()
    noise.to_csv(data.tables_dir / "03_tag_link_noise.csv", index=False)

    anchor_summary = (
        link_df.groupby("anchor")
        .agg(
            links=("bias_mm", "size"),
            bias_mean_mm=("bias_mm", "mean"),
            bias_median_mm=("bias_mm", "median"),
            bias_p95_abs_mm=("bias_mm", lambda s: float(np.percentile(np.abs(s), 95))),
            noise_median_std_mm=("range_std_mm", "median"),
        )
        .reset_index()
    )
    anchor_summary.to_csv(data.tables_dir / "03_tag_anchor_bias_summary.csv", index=False)

    facing_summary = (
        link_df.groupby(["facing", "truth_reconstructed"], dropna=False)
        .agg(
            links=("bias_mm", "size"),
            bias_mean_mm=("bias_mm", "mean"),
            bias_median_mm=("bias_mm", "median"),
            bias_p95_abs_mm=("bias_mm", lambda s: float(np.percentile(np.abs(s), 95))),
        )
        .reset_index()
    )
    facing_summary.to_csv(data.tables_dir / "03_tag_facing_bias_summary.csv", index=False)

    x = link_df["vicon_distance_mm"].to_numpy(dtype=float)
    y = link_df["bias_mm"].to_numpy(dtype=float)
    linear_beta = np.linalg.lstsq(np.column_stack([np.ones_like(x), x]), y, rcond=None)[0]
    pred = linear_beta[0] + linear_beta[1] * x
    slope_percent = float(linear_beta[1] * 100.0)
    linear_rms = float(np.sqrt(np.mean((y - pred) ** 2)))

    fig, ax = plt.subplots(figsize=(7, 5))
    direct = link_df["truth_direct_motive"].to_numpy(dtype=bool)
    ax.scatter(x[direct], y[direct], s=28, color="#2f6f9f", alpha=0.75, label="direct motive truth")
    ax.scatter(x[~direct], y[~direct], s=55, color="#b4493a", marker="x", label="reconstructed truth")
    order = np.argsort(x)
    ax.plot(x[order], pred[order], color="black", linewidth=1.4, label=f"linear slope {slope_percent:.2f}%")
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xlabel("Vicon tag-anchor distance [mm]")
    ax.set_ylabel("Median range bias [mm]")
    ax.set_title("Static Tag Link Bias vs Distance")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig_path1 = data.figures_dir / "03_tag_bias_vs_distance.png"
    fig.savefig(fig_path1, dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 4.5))
    box_data = [link_df.loc[link_df["anchor"] == a, "bias_mm"].to_numpy() for a in ANCHOR_LABELS]
    ax.boxplot(box_data, tick_labels=ANCHOR_LABELS, showfliers=True)
    ax.axhline(0, color="gray", linewidth=0.8)
    ax.set_xlabel("Anchor")
    ax.set_ylabel("Bias [mm]")
    ax.set_title("Per-Anchor Static Link Bias")
    fig.tight_layout()
    fig_path2 = data.figures_dir / "03_tag_anchor_bias_boxplot.png"
    fig.savefig(fig_path2, dpi=180)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8, 7))
    vmax = float(np.nanmax(np.abs(matrix.to_numpy(dtype=float))))
    im = ax.imshow(matrix.to_numpy(dtype=float), aspect="auto", cmap="RdBu_r", vmin=-vmax, vmax=vmax)
    ax.set_xticks(range(len(ANCHOR_LABELS)), ANCHOR_LABELS)
    ax.set_yticks(range(len(matrix.index)), matrix.index)
    ax.set_xlabel("Anchor")
    ax.set_ylabel("Static position")
    ax.set_title("Static Tag Link Bias Matrix [mm]")
    fig.colorbar(im, ax=ax, label="bias [mm]")
    fig.tight_layout()
    fig_path3 = data.figures_dir / "03_tag_bias_matrix.png"
    fig.savefig(fig_path3, dpi=180)
    plt.close(fig)

    aux = data.roto_df[data.roto_df["capture_id"].isin(DYNAMIC_EXCLUDE_CAPTURE_IDS)].copy()
    aux_rows = []
    if not aux.empty:
        aux_valid = aux[valid_mask(aux)].copy()
        aux_valid["range_mm"] = pd.to_numeric(aux_valid["range_mm"], errors="coerce")
        for (capture_id, peer_name, anchor_id), g in aux_valid.groupby(["capture_id", "peer_name", "anchor_id"]):
            aux_rows.append(
                {
                    "capture_id": capture_id,
                    "peer_name": peer_name,
                    "anchor_id": int(anchor_id),
                    "anchor": mapping_as_anchor_label(int(anchor_id), mapping),
                    "n_valid": int(len(g)),
                    "median_range_mm": float(g["range_mm"].median()),
                    "note": "auxiliary static range check only; no dedicated Vicon tag truth assigned here",
                }
            )
    pd.DataFrame(aux_rows).to_csv(data.tables_dir / "03_auxiliary_static_middle_test.csv", index=False)

    outliers = link_df.assign(abs_bias_mm=link_df["bias_mm"].abs()).sort_values("abs_bias_mm", ascending=False).head(12)
    outlier_recon = int(outliers["truth_reconstructed"].sum())
    body = []
    body.append(f"Static tag link bias was computed over `{len(link_df)}` position-anchor links using the verified anchor_id mapping from `data_config.py`.")
    body.append(f"Linear pooled bias-vs-distance fit: intercept `{linear_beta[0]:.1f}` mm, slope `{slope_percent:.3f}%`, residual RMS `{linear_rms:.1f}` mm.")
    body.append(f"Reconstructed tag truth positions are flagged in all tables. Among the top 12 absolute-bias links, `{outlier_recon}` use reconstructed truth.")
    body.append("`R01-Static-middle-test` is excluded from dynamic RotoArm analysis and listed only as an auxiliary static range check.")
    body.append("")
    body.append(f"![Tag bias vs distance](figures/{fig_path1.name})")
    body.append("")
    body.append(f"![Per-anchor bias boxplot](figures/{fig_path2.name})")
    body.append("")
    body.append(f"![Tag bias matrix](figures/{fig_path3.name})")
    body.append("")
    body.append("Per-anchor summary:")
    body.append("")
    body.append(markdown_table(anchor_summary.to_dict("records"), ["anchor", "links", "bias_mean_mm", "bias_median_mm", "bias_p95_abs_mm", "noise_median_std_mm"]))
    body.append("")
    body.append("Facing/truth-source stratification:")
    body.append("")
    body.append(markdown_table(facing_summary.to_dict("records"), ["facing", "truth_reconstructed", "links", "bias_mean_mm", "bias_median_mm", "bias_p95_abs_mm"]))
    body.append("")
    body.append("Largest absolute link biases:")
    body.append("")
    body.append(markdown_table(outliers.to_dict("records"), ["position", "anchor", "bias_mm", "vicon_distance_mm", "median_range_mm", "tag_truth_source", "truth_reconstructed", "facing"]))
    save_markdown_fragment(data.fragments_dir / "03_tag_link_bias.md", "1.3 Tag Link Bias", "\n".join(body))
    print(f"tag link slope={slope_percent:.3f}% rms={linear_rms:.2f} mm")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
