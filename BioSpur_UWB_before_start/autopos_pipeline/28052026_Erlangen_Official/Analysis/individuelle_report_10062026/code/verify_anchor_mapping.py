#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from scripts.audit_helpers import ANCHOR_LABELS, markdown_table
from scripts.phase1_common import (
    assert_sweep_direction_columns,
    compute_anchor_assignment_cost,
    load_phase1_data,
    quality_distribution,
    rank_anchor_assignments,
    save_markdown_fragment,
    write_data_config,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify tag anchor_id to anchor-label mapping before Phase 1.")
    parser.add_argument("--data-dir", type=Path, default=Path("."))
    parser.add_argument("--out-dir", type=Path, default=Path("reports"))
    parser.add_argument("--min-second-over-best", type=float, default=1.20)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    data = load_phase1_data(args.data_dir, args.out_dir)

    direction_summary = assert_sweep_direction_columns(data.sweep_df)

    cost = compute_anchor_assignment_cost(data.static_df, data.anchor_truth, data.tag_truth)
    cost_df = pd.DataFrame(cost, index=[f"anchor_id_{i}" for i in range(8)], columns=ANCHOR_LABELS)
    cost_df.to_csv(data.tables_dir / "00_anchor_id_assignment_cost_matrix.csv")

    rankings = rank_anchor_assignments(cost, top_n=10)
    rank_df = pd.DataFrame(
        [
            {
                "rank": r["rank"],
                "cost_sse_mm2": r["rank_cost"],
                "rms_mm": r["rms_mm"],
                "mapping": r["mapping_str"],
            }
            for r in rankings
        ]
    )
    rank_df.to_csv(data.tables_dir / "00_anchor_id_assignment_rankings.csv", index=False)
    best, second = rankings[0], rankings[1]
    ratio = second["rank_cost"] / best["rank_cost"]
    if ratio < args.min_second_over_best:
        raise SystemExit(
            f"anchor_id mapping not decisive enough: second/best cost ratio {ratio:.3f} < {args.min_second_over_best:.3f}"
        )

    config_path = write_data_config(data.data_dir, best["mapping"], best, second)

    quality_rows = []
    quality_summary = []
    for name, df, columns in [
        ("sweep", data.sweep_df, ["quality_percent", "quality_flag_percent"]),
        ("static", data.static_df, ["quality_percent", "quality_flag_percent"]),
        ("roto", data.roto_df, ["quality_percent", "quality_flag_percent"]),
    ]:
        rows, summary = quality_distribution(name, df, columns)
        quality_rows.extend(rows)
        quality_summary.extend(summary)
    pd.DataFrame(quality_rows).to_csv(data.tables_dir / "00_quality_distribution.csv", index=False)
    pd.DataFrame(quality_summary).to_csv(data.tables_dir / "00_quality_summary.csv", index=False)

    body = []
    body.append("Ground-truth system terminology: **Vicon**. The local `opti_captures` name is a storage convention, not the report terminology.")
    body.append("")
    body.append("Anchor ID mapping was verified from static tag ranges against Vicon link distances before any tag-link bias calculation.")
    body.append("")
    body.append(
        markdown_table(
            [
                {
                    "best_mapping": best["mapping_str"],
                    "best_rms_mm": best["rms_mm"],
                    "second_best_mapping": second["mapping_str"],
                    "second_best_rms_mm": second["rms_mm"],
                    "second_over_best_cost_ratio": ratio,
                    "data_config": config_path.name,
                }
            ],
            [
                "best_mapping",
                "best_rms_mm",
                "second_best_mapping",
                "second_best_rms_mm",
                "second_over_best_cost_ratio",
                "data_config",
            ],
        )
    )
    body.append("Direction columns were asserted before use:")
    body.append("")
    body.append(markdown_table([direction_summary], ["pair_columns_consistent", "master_equals_initiator", "self_links", "direction_definition"]))
    body.append("Quality fields were audited for saturation; fields marked `no` are excluded from weighting decisions.")
    body.append("")
    body.append(markdown_table(quality_summary, ["dataset", "field", "rows", "non_null", "top_value", "top_percent", "informative"]))
    body.append("Full quality distributions:")
    body.append("")
    body.append(markdown_table(quality_rows, ["dataset", "field", "value", "count", "percent"]))
    body.append("Sweep rows do not have per-sample timestamps, so time-drift analysis is out of Phase 1 scope.")
    save_markdown_fragment(data.fragments_dir / "00_prerequisites.md", "Phase 1 Prerequisites", "\n".join(body))
    print(f"verified mapping: {best['mapping_str']} (second/best cost ratio {ratio:.3f})")
    print(f"wrote {config_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
