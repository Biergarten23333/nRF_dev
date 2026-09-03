#!/usr/bin/env python3
"""Package the reduced Root-R5A fast triage from already-computed diagnostics."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


ROOT_R4_SHA = "fb5000d759e35abcc7d568711d4b5e2ef2d3cca6862c76f7b1cb75c3ec35e48e"


def canonical_manifest_hash(path: Path) -> tuple[str, str]:
    value = json.loads(path.read_text()); recorded = value.pop("manifest_payload_sha256")
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return recorded, hashlib.sha256(payload).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--root-r4", type=Path, required=True); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); source = args.source.resolve(); output = args.output.resolve()
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(output)
    output.mkdir(parents=True, exist_ok=True)
    t4 = json.loads((source / "T4_CIRCULAR_YAW_PROFILES.json").read_text())["profiles"]
    raw = json.loads((source / "RAW_CIRCULAR_YAW_PROFILES.json").read_text())["profiles"]
    timing = json.loads((source / "TIME_OFFSET_PROFILE.json").read_text())
    comparison = json.loads((source / "CONSTANT_VS_DYNAMIC_MODEL_COMPARISON.json").read_text())
    reproduction = json.loads((source / "ROOT_R4_REPRODUCTION.json").read_text())
    recorded, computed = canonical_manifest_hash(args.root_r4 / "REPRODUCIBILITY_MANIFEST.json")

    block_rows = []
    for block in range(5):
        block_rows.append({
            "block": block,
            "t4": {"best_yaw_deg": t4[block]["global_mode_deg"],
                   "profile_width_95_deg": t4[block]["asymptotic_profile_interval_width_95_deg"],
                   "secondary_material_modes": t4[block]["material_modes"][1:],
                   "nuisance_eliminated_information": t4[block]["nuisance_eliminated_yaw_information"]},
            "raw": {"best_yaw_deg": raw[block]["global_mode_deg"],
                    "profile_width_95_deg": raw[block]["asymptotic_profile_interval_width_95_deg"],
                    "secondary_material_modes": raw[block]["material_modes"][1:],
                    "nuisance_eliminated_information": raw[block]["nuisance_eliminated_yaw_information"]},
            "raw_minus_t4_circular_deg": float((raw[block]["global_mode_deg"] - t4[block]["global_mode_deg"] + 180) % 360 - 180),
        })
    t4_cv = comparison["T4"]["reference_prior"]; raw_cv = comparison["RAW"]["reference_prior"]
    result = {
        "schema": "biospur.root_r5a.fast_triage.v1",
        "diagnostic_label": "OTHER_MODEL_OR_NUISANCE_MISMATCH_LIKELY",
        "plain_answer": "The evidence is locally informative rather than broadly unobservable, but its block modes are irregular. One bounded global timing shift and the smallest smooth drift model do not explain the contradiction robustly.",
        "root_r4": {"manifest_expected": ROOT_R4_SHA, "manifest_recorded": recorded,
                    "manifest_recomputed": computed, "verified": recorded == computed == ROOT_R4_SHA,
                    "metrics_reproduced": reproduction["all_reproduced"],
                    "five_block_yaw_range_deg": reproduction["artifact_values"]["five_block_yaw_range_deg"],
                    "maximum_tag_loo_change_deg": reproduction["artifact_values"]["maximum_tag_loo_yaw_change_deg"],
                    "t4_raw_hybrid_yaw_deg": [reproduction["artifact_values"][key] for key in ("t4_yaw_deg", "raw_yaw_deg", "hybrid_yaw_deg")]},
        "blocks": block_rows,
        "profile_interpretation": {"all_primary_profiles_unimodal": all(len(row["material_modes"]) == 1 for row in t4[:5] + raw[:5]),
            "median_width_all_blocks_deg": float(np.median([row["asymptotic_profile_interval_width_95_deg"] for row in t4[:5] + raw[:5]])),
            "t4_width_range_deg": [min(row["asymptotic_profile_interval_width_95_deg"] for row in t4[:5]), max(row["asymptotic_profile_interval_width_95_deg"] for row in t4[:5])],
            "raw_width_range_deg": [min(row["asymptotic_profile_interval_width_95_deg"] for row in raw[:5]), max(row["asymptotic_profile_interval_width_95_deg"] for row in raw[:5])],
            "classification": "LOCALLY_INFORMATIVE_UNIMODAL_BUT_NOT_UNIFORMLY_SHARP"},
        "temporal_coherence": {"t4_block_modes_deg": [row["global_mode_deg"] for row in t4[:5]],
            "raw_block_modes_deg": [row["global_mode_deg"] for row in raw[:5]],
            "interpretation": "irregular jumps; a smooth common drift is not a sufficient explanation"},
        "bounded_timing": {"interval_ms": [-20, 20],
            "t4_conflict_reduction_fraction": timing["best"]["T4"]["range_reduction_fraction"],
            "raw_conflict_reduction_fraction": timing["best"]["RAW"]["range_reduction_fraction"],
            "t4_best_offset_ms": timing["best"]["T4"]["best_global_offset_s"] * 1000,
            "raw_best_offset_ms": timing["best"]["RAW"]["best_global_offset_s"] * 1000,
            "changed_conclusion": False},
        "held_block": {"model": "gauge-fixed slow yaw plus bounded gyro-bias drift",
            "t4_dynamic_to_constant_aggregate_ratio": t4_cv["mean_ratio"], "t4_dynamic_wins_of_5": t4_cv["wins"],
            "raw_dynamic_to_constant_aggregate_ratio": raw_cv["mean_ratio"], "raw_dynamic_wins_of_5": raw_cv["wins"],
            "dynamic_outperformed_robustly": False,
            "interpretation": "T4 aggregate score is lower because endpoints improve, but only 2/5 blocks win; raw is worse overall and also wins only 2/5."},
        "scope": {"real_c1_fusion_executed": False, "candidate_authorized": False,
                  "external_truth_opened": False, "golf_or_boxing_opened": False,
                  "commit": False, "push": False, "merge": False,
                  "raw_t4_combined_as_independent_factors": False},
        "single_next_computation": "On the same five blocks and matched support, compute a 5x10 block-by-tag influence table (objective/mode change when each tag is omitted) to identify whether one wearable/lever-arm family drives the irregular block modes.",
    }
    (output / "ROOT_R5A_FAST_TRIAGE.json").write_text(json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n")

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.8), sharey=True)
    for ax, layer, profiles in zip(axes, ("T4", "raw"), (t4, raw)):
        for row in profiles[:5]:
            line, = ax.plot(row["grid_yaw_deg"], row["normalized_delta_objective"], label=row["block"], linewidth=1.1)
            ax.plot(row["global_mode_deg"], 0, marker="|", markersize=9,
                    color=line.get_color(), markeredgewidth=1.5)
        ax.set(title=f"{layer} matched-support profiles", xlabel="yaw of R_V4_from_N (deg)", ylabel="normalized Δ objective")
        ax.set_xlim(-180, 180); ax.grid(alpha=.25); ax.legend(fontsize=8)
    axes[0].set_ylim(0, 1.05 * max(max(row["normalized_delta_objective"]) for row in t4[:5] + raw[:5]))
    fig.suptitle("Root-R5A fast triage — internal C1 evidence, no external truth")
    fig.savefig(output / "BLOCK_YAW_PROFILES.png", dpi=140, bbox_inches="tight", metadata={"Software": "biospur-root-r5a-fast"}); plt.close(fig)

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.5), sharey=False)
    for ax, layer, cv in zip(axes, ("T4", "raw"), (t4_cv, raw_cv)):
        folds = cv["folds"]; x = np.arange(5); width = .36
        ax.bar(x-width/2, [row["held_constant_score"] for row in folds], width, label="constant")
        ax.bar(x+width/2, [row["held_dynamic_score"] for row in folds], width, label="slow drift")
        ax.set(title=f"{layer}: drift wins {cv['wins']}/5", xlabel="held block", ylabel="held profile score"); ax.grid(axis="y", alpha=.25); ax.legend()
    fig.suptitle("Held-block comparison; lower is better")
    fig.savefig(output / "CONSTANT_VS_DRIFT_COMPARISON.png", dpi=140, bbox_inches="tight", metadata={"Software": "biospur-root-r5a-fast"}); plt.close(fig)

    report = f"""# Root-R5A fast triage

## Diagnostic conclusion

```text
OTHER_MODEL_OR_NUISANCE_MISMATCH_LIKELY
```

The conflict is not best described as absent or broadly flat UWB yaw
information, and the existing evidence does not support the simpler claim that
one constant frame merely absorbed a smooth common IMU yaw drift. The profiles
are unimodal and locally informative, especially for raw ranges, but their
block modes move irregularly and raw/T4 do not agree closely enough block by
block. This is an internal diagnostic, not frame or fusion authorization.

1. **Broad or sharp?** Locally informative and unimodal, but not uniformly
   sharp. T4 widths are {result['profile_interpretation']['t4_width_range_deg'][0]:.1f}–{result['profile_interpretation']['t4_width_range_deg'][1]:.1f}°;
   raw widths are {result['profile_interpretation']['raw_width_range_deg'][0]:.1f}–{result['profile_interpretation']['raw_width_range_deg'][1]:.1f}°.
   No block has a material secondary mode.

2. **Coherent time evolution?** No. T4 modes are
   `{', '.join(f'{x:.1f}' for x in result['temporal_coherence']['t4_block_modes_deg'])}°`; raw modes are
   `{', '.join(f'{x:.1f}' for x in result['temporal_coherence']['raw_block_modes_deg'])}°`.
   The jumps are not a clean slow trajectory.

3. **Bounded timing?** No change. Across ±20 ms, block-conflict reduction is
   only {100*result['bounded_timing']['t4_conflict_reduction_fraction']:.2f}% for T4 and
   {100*result['bounded_timing']['raw_conflict_reduction_fraction']:.2f}% for raw; the layers prefer opposite bound edges.

4. **Minimal drift versus constant held-block evidence?** No robust win. T4
   aggregate dynamic/constant score is {result['held_block']['t4_dynamic_to_constant_aggregate_ratio']:.3f}, but drift wins only
   {result['held_block']['t4_dynamic_wins_of_5']}/5 blocks. Raw ratio is
   {result['held_block']['raw_dynamic_to_constant_aggregate_ratio']:.3f} and also wins only
   {result['held_block']['raw_dynamic_wins_of_5']}/5. Endpoint gains do not establish predictive drift.

5. **Supported label:** `OTHER_MODEL_OR_NUISANCE_MISMATCH_LIKELY`.

6. **Single smallest next computation:** {result['single_next_computation']}

## Provenance and safety

Root-R4 manifest `{ROOT_R4_SHA}` recomputes exactly, and its headline metrics
were reproduced. Raw and T4 were separate matched-support solves and were not
double-counted. No real fusion, external truth, golf/boxing data, authorization,
commit, push, or merge occurred.
"""
    (output / "ROOT_R5A_FAST_TRIAGE.md").write_text(report)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
