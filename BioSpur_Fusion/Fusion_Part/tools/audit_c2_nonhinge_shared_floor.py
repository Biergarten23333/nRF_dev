#!/usr/bin/env python3
"""Read-only ablation of the nonhinge cumulative shared-floor replay output."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

sys.dont_write_bytecode = True

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
SPRINT = (
    WORKSPACE
    / "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT"
)
REPLAY_DIR = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001"
NPZ = REPLAY_DIR / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
OUT = SPRINT / "C2_NONHINGE_SHARED_FLOOR_ABLATION_001"
EDGES = (
    "pelvis_torso",
    "shoulder_left",
    "shoulder_right",
    "hip_left",
    "hip_right",
)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normalize_log(values: np.ndarray) -> np.ndarray:
    shifted = np.asarray(values, dtype=float) - float(np.max(values))
    weights = np.exp(shifted)
    return weights / float(np.sum(weights))


def _convolve(weights: np.ndarray, variance_rad2: float) -> np.ndarray:
    values = np.asarray(weights, dtype=float)
    if variance_rad2 <= 0.0:
        return values.copy()
    count = len(values)
    offsets = np.arange(count, dtype=float) * 2.0 * np.pi / float(count)
    offsets = np.arctan2(np.sin(offsets), np.cos(offsets))
    kernel = np.exp(-0.5 * offsets**2 / float(variance_rad2))
    kernel /= float(np.sum(kernel))
    output = np.fft.ifft(np.fft.fft(values) * np.fft.fft(kernel)).real
    output = np.maximum(output, 0.0)
    return output / float(np.sum(output))


def _metrics(weights: np.ndarray, grid: np.ndarray) -> dict[str, float]:
    values = np.asarray(weights, dtype=float)
    entropy = float(-np.sum(values * np.log(np.maximum(values, 1e-300))))
    resultant = np.sum(values * np.exp(1j * grid))
    return {
        "entropy_nats": entropy,
        "information_gain_from_uniform_nats": float(np.log(len(values)) - entropy),
        "circular_resultant_magnitude": float(abs(resultant)),
    }


def _write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("shared-floor ablation requires canonical Fusion_Part")
    reports = [
        json.loads((REPLAY_DIR / f"REPLAY_{index:02d}.json").read_text(encoding="utf-8"))
        for index in range(19)
    ]
    branch_ids = tuple(reports[0]["branch_ids"])
    conditional = {
        (branch, edge): np.full(360, 1.0 / 360.0, dtype=float)
        for branch in branch_ids for edge in EDGES
    }
    cumulative_floor = {key: 0.0 for key in conditional}
    rows: list[dict[str, Any]] = []
    exact_stored_match_count = 0
    with np.load(NPZ, allow_pickle=False) as arrays:
        for index, replay in enumerate(reports):
            by_key = {
                (str(row["branch_id"]), str(row["edge"])): row["report"]
                for row in replay["nonhinge_full_s1_likelihood_reports"]
            }
            for branch in branch_ids:
                for edge in EDGES:
                    key = (branch, edge)
                    report = by_key[key]
                    prefix = f"nonhinge_heading/{index:02d}/{branch}/{edge}"
                    grid = np.asarray(arrays[f"{prefix}/delta_grid_rad"], dtype=float)
                    action_log = np.asarray(
                        arrays[f"{prefix}/action_log_likelihood"], dtype=float,
                    )
                    stored = np.asarray(arrays[f"{prefix}/posterior_weights"], dtype=float)
                    diffusion = float(report["diffusion_variance_rad2"])
                    prior = _convolve(conditional[key], diffusion)
                    conditional_now = _normalize_log(
                        np.log(np.maximum(prior, 1e-300)) + action_log
                    )
                    current_floor = float(
                        report["likelihood_report"]["shared_heading_variance_floor_rad2"]
                    )
                    cumulative_floor[key] = max(cumulative_floor[key], current_floor)
                    current_only = _convolve(conditional_now, current_floor)
                    cumulative = _convolve(conditional_now, cumulative_floor[key])
                    stored_matches = bool(np.allclose(
                        stored, cumulative, atol=2e-15, rtol=2e-13,
                    ))
                    exact_stored_match_count += int(stored_matches)
                    rows.append({
                        "chronological_index": index,
                        "action": replay["action"],
                        "branch_id": branch,
                        "edge": edge,
                        "diffusion_variance_rad2": diffusion,
                        "current_action_shared_floor_rad2": current_floor,
                        "historical_cumulative_max_floor_rad2": cumulative_floor[key],
                        "conditional_information": _metrics(conditional_now, grid),
                        "current_action_floor_only_diagnostic": _metrics(current_only, grid),
                        "historical_cumulative_max_output": _metrics(cumulative, grid),
                        "stored_output_matches_historical_cumulative_max": stored_matches,
                        "current_action_likelihood": {
                            "information_gain_from_uniform_nats": float(
                                report["likelihood_report"][
                                    "information_gain_from_uniform_nats"
                                ]
                            ),
                            "circular_resultant_magnitude": float(
                                report["likelihood_report"][
                                    "circular_resultant_magnitude"
                                ]
                            ),
                        },
                    })
                    conditional[key] = conditional_now

    counterexample = [
        row for row in rows
        if row["chronological_index"] == 9
        and row["edge"] in {"hip_left", "hip_right"}
    ]
    conditional_more_informative = [
        row for row in rows
        if row["conditional_information"]["information_gain_from_uniform_nats"]
        > row["historical_cumulative_max_output"][
            "information_gain_from_uniform_nats"
        ] + 1e-12
    ]
    audit = {
        "schema": "biospur-c2-nonhinge-shared-floor-ablation-v1",
        "source_replay_npz": {"path": str(NPZ), "sha256": _sha(NPZ)},
        "source_replay_audit": {
            "path": str(REPLAY_DIR / "REPLAY_AUDIT.json"),
            "sha256": _sha(REPLAY_DIR / "REPLAY_AUDIT.json"),
        },
        "branch_count": len(branch_ids),
        "edge_count": len(EDGES),
        "prefix_count": 19,
        "row_count": len(rows),
        "stored_output_exact_reconstruction_count": exact_stored_match_count,
        "conditional_more_informative_than_cumulative_max_count": len(
            conditional_more_informative
        ),
        "replay09_hip_counterexample_rows": counterexample,
        "finding": (
            "Historical max-over-actions shared-floor convolution suppresses later "
            "conditional motion evidence; a low-sensitivity action is being retained "
            "as a permanent output blur rather than a weak action likelihood."
        ),
        "conditional_state_is_scientific_posterior": False,
        "current_action_floor_only_is_scientific_replacement": False,
        "joint_shared_nuisance_recomputation_possible_from_serialized_output_alone": False,
        "missing_sufficient_statistics_for_valid_joint_replacement": [
            "per-candidate accumulated heading-sensitivity normal term",
            "per-candidate signed shared-nuisance score/Jacobian term",
            "capture-wide shared nuisance covariance binding",
        ],
        "hard_argmax_or_threshold_relaxation_used": False,
        "heldout_access": False,
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=False)
    path = OUT / "AUDIT.json"
    _write_new(path, audit)
    print(json.dumps({
        "audit": str(path),
        "sha256": _sha(path),
        "stored_output_exact_reconstruction_count": exact_stored_match_count,
        "row_count": len(rows),
        "conditional_more_informative_than_cumulative_max_count": len(
            conditional_more_informative
        ),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
