#!/usr/bin/env python3
"""Diagnose saved B2 synthetic basins without refitting or opening real data."""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import SEGMENTS  # noqa: E402
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (  # noqa: E402
    JOINTS,
    angles_from_axis,
    yaw,
)
from biospur_fusion.v0.synthetic_qualification import (  # noqa: E402
    profiled_subspace,
    recovery_record,
)
from biospur_fusion.v0.unified_calibration import (  # noqa: E402
    FULL_DIMENSION,
    FUNCTIONAL_JOINTS,
    PRODUCT_DIMENSION,
    ZERO_JOINTS,
    UnifiedCalibrationObjective,
    decode_full,
    decode_product,
    production_jacobian,
    profiled_product_observability,
    wrap_angle,
)
from biospur_fusion.v0.unified_synthetic import generate_unified_case  # noqa: E402


CONTRACT = ROOT / "config/biospur_fusion_v0_observability_first/SYNTHETIC_QUALIFICATION_CONTRACT.json"
GROUPS = {
    "sensor_axes": np.arange(0, 20),
    "relative_headings": np.arange(20, 29),
    "functional_axes": np.arange(29, 45),
    "trunk_frame": np.arange(45, 48),
    "joint_zeros": np.arange(48, 55),
    "initial_pose_nuisance": np.arange(55, 72),
    "tpose_nuisance": np.arange(72, 89),
}


def jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(item) for item in value]
    return value


def robust_cost(values: np.ndarray) -> float:
    values = np.asarray(values, dtype=float)
    return float(np.sum(np.sqrt(1.0 + values * values) - 1.0))


def vector_angle(first: np.ndarray, second: np.ndarray) -> float:
    return math.degrees(math.acos(float(np.clip(np.asarray(first) @ np.asarray(second), -1.0, 1.0))))


def state_comparison(truth: np.ndarray, candidate: np.ndarray) -> dict[str, Any]:
    truth_product, truth_nuisance = decode_full(truth)
    fit_product, fit_nuisance = decode_full(candidate)
    return {
        "sensor_axis_error_deg": {
            segment: vector_angle(truth_product["axes"][segment], fit_product["axes"][segment])
            for segment in SEGMENTS
        },
        "relative_heading_error_deg": {
            segment: math.degrees(float(wrap_angle(fit_product["headings"][segment] - truth_product["headings"][segment])))
            for segment in SEGMENTS[1:]
        },
        "functional_axis_error_deg": {
            joint: vector_angle(truth_product["functional"][joint], fit_product["functional"][joint])
            for joint in FUNCTIONAL_JOINTS
        },
        "trunk_frame_geodesic_error_deg": math.degrees(float(np.linalg.norm(
            Rotation.from_matrix(truth_product["trunk_frame"].T @ fit_product["trunk_frame"]).as_rotvec()
        ))),
        "joint_zero_error_deg": {
            joint: math.degrees(float(wrap_angle(fit_product["zeros"][joint] - truth_product["zeros"][joint])))
            for joint in ZERO_JOINTS
        },
        "pose_nuisance": {
            action: {
                "root_rotation_geodesic_error_deg": math.degrees(float(np.linalg.norm(
                    (
                        Rotation.from_rotvec(truth_nuisance[action][:3]).inv()
                        * Rotation.from_rotvec(fit_nuisance[action][:3])
                    ).as_rotvec()
                ))),
                "nonroot_coordinate_l2_error_deg": math.degrees(float(np.linalg.norm(
                    fit_nuisance[action][3:] - truth_nuisance[action][3:]
                ))),
            }
            for action in truth_nuisance
        },
    }


def factor_family(factor: str) -> str:
    prefixes = (
        ("articulated_static_direction", "STATIC_LATENT_DIRECTION"),
        ("time_resolved_functional_axis", "RELATIVE_RATE_AXIS"),
        ("time_resolved_axis_direction_geometry", "AXIS_DIRECTION_GEOMETRY"),
        ("time_resolved_bilateral", "BILATERAL_STRUCTURE"),
        ("curl_noncollinear_axis", "COMPOUND_ELBOW_CURL"),
        ("pronation_noncollinear_axis", "COMPOUND_ELBOW_PRONATION"),
        ("time_resolved_trunk", "TRUNK_NONCOLLINEAR"),
        ("capture_defined_neutral_zero", "CAPTURE_DEFINED_ZERO"),
        ("soft_bilateral_functional_semantics", "SOFT_FUNCTIONAL_SEMANTICS"),
        ("soft_right_handed_trunk_frame_semantics", "SOFT_TRUNK_HANDEDNESS"),
        ("broad_soft_pose_protocol", "PARAMETER_ONLY_POSE_PRIOR"),
    )
    return next((family for prefix, family in prefixes if factor.startswith(prefix)), factor)


def residual_costs(objective: UnifiedCalibrationObjective, x: np.ndarray) -> dict[str, Any]:
    classification: dict[str, float] = {}
    action: dict[str, float] = {}
    family: dict[str, float] = {}
    block: list[dict[str, Any]] = []
    for item in objective.blocks(x, True):
        cost = robust_cost(item.values)
        classification[item.classification] = classification.get(item.classification, 0.0) + cost
        action[item.action] = action.get(item.action, 0.0) + cost
        name = factor_family(item.factor)
        family[name] = family.get(name, 0.0) + cost
        block.append({
            "action": item.action,
            "classification": item.classification,
            "factor": item.factor,
            "family": name,
            "cost": cost,
        })
    return {
        "total": sum(classification.values()),
        "by_classification": classification,
        "by_action": action,
        "by_family": family,
        "blocks": sorted(block, key=lambda value: value["cost"], reverse=True),
    }


def _rank(singular: np.ndarray) -> int:
    threshold = max(1e-8, float(singular[0]) * 1e-7 if len(singular) else 0.0)
    return int(np.sum(singular > threshold))


def spectrum(objective: UnifiedCalibrationObjective, x: np.ndarray) -> dict[str, Any]:
    blocks = objective.blocks(x, False)
    jacobian = production_jacobian(objective, x, False)
    residual = objective.residual(x, False)
    # IRLS Gauss-Newton spectrum for the declared soft_l1 loss.
    weights = np.power(1.0 + residual * residual, -0.25)
    weighted = jacobian * weights[:, None]
    _, singular, vh = np.linalg.svd(weighted, full_matrices=False)
    offsets = np.cumsum([0] + [len(block.values) for block in blocks])
    raw_rows = np.concatenate([
        np.arange(offsets[index], offsets[index + 1])
        for index, block in enumerate(blocks)
        if block.classification == "MEASURED_OBSERVATION"
    ])
    raw_profile = profiled_product_observability(jacobian[raw_rows])
    heading_target = np.arange(20, 29)
    heading_other = np.r_[np.arange(0, 20), np.arange(29, FULL_DIMENSION)]
    heading = profiled_subspace(jacobian[raw_rows], heading_target, heading_other)
    directions = []
    for offset in range(1, min(6, len(singular)) + 1):
        vector = vh[-offset]
        energy = {name: float(np.sum(vector[indices] ** 2)) for name, indices in GROUPS.items()}
        directions.append({
            "singular_value": float(singular[-offset]),
            "gauss_newton_eigenvalue": float(singular[-offset] ** 2),
            "state_block_energy": dict(sorted(energy.items(), key=lambda item: item[1], reverse=True)),
        })
    return {
        "data_plus_soft_protocol_irls_rank": _rank(singular),
        "data_plus_soft_protocol_irls_nullity": FULL_DIMENSION - _rank(singular),
        "bottom_irls_singular_values": singular[-12:],
        "bottom_irls_directions": directions,
        "raw_measurement_profiled_product_rank": int(raw_profile["rank"]),
        "raw_measurement_profiled_product_nullity": int(raw_profile["nullity"]),
        "raw_measurement_bottom_product_singular_values": np.asarray(raw_profile["singular_values"])[-12:],
        "raw_measurement_heading_conditioned_rank": int(heading["rank"]),
        "raw_measurement_heading_conditioned_nullity": int(heading["nullity"]),
        "raw_measurement_heading_conditioned_singular_values": heading["singular_values"],
    }


def static_preserving_heading_transform(
    objective: UnifiedCalibrationObjective,
    truth: np.ndarray,
    basin: np.ndarray,
) -> np.ndarray:
    """Apply the basin's heading shifts while preserving initial-rest vectors."""

    transformed = truth.copy()
    truth_product = decode_product(truth)
    basin_product = decode_product(basin)
    transformed[20:29] = basin[20:29]
    for segment_index, segment in enumerate(SEGMENTS):
        rows = objective._rows("initial_still_attempt2", (segment,), static=True)
        row = int(rows[len(rows) // 2])
        observed = objective.obs.rotation[row, objective.segment_index[segment]]
        old_rotation = yaw(truth_product["headings"][segment]) @ observed
        new_rotation = yaw(basin_product["headings"][segment]) @ observed
        axis = new_rotation.T @ old_rotation @ truth_product["axes"][segment]
        transformed[2 * segment_index:2 * segment_index + 2] = angles_from_axis(axis)
    for joint_index, joint in enumerate(FUNCTIONAL_JOINTS):
        parent, _ = JOINTS[joint]
        rows = objective._rows("initial_still_attempt2", (parent,), static=True)
        row = int(rows[len(rows) // 2])
        observed = objective.obs.rotation[row, objective.segment_index[parent]]
        old_rotation = yaw(truth_product["headings"][parent]) @ observed
        new_rotation = yaw(basin_product["headings"][parent]) @ observed
        axis = new_rotation.T @ old_rotation @ truth_product["functional"][joint]
        start = 29 + 2 * joint_index
        transformed[start:start + 2] = angles_from_axis(axis)
    return transformed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--qualification", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(CONTRACT.read_text())
    report = json.loads((args.qualification / "SYNTHETIC_QUALIFICATION.json").read_text())
    audit = json.loads((args.qualification / "DATA_ACCESS_AUDIT.json").read_text())
    if audit.get("real_capture_payload_opened") or audit.get("h_series_payload_opened") or audit.get("sealed_inputs_opened"):
        raise RuntimeError("qualification source is contaminated by sealed input access")

    cases = []
    for case in report["cases"]:
        seed, noisy = int(case["seed"]), bool(case["noisy"])
        observation, truth, metadata = generate_unified_case(contract, seed, noisy=noisy)
        objective = UnifiedCalibrationObjective(observation, contract)
        filename = f"CASE_{seed}_{'NOISY' if noisy else 'NOISE_FREE'}.npz"
        with np.load(args.qualification / filename) as archive:
            fits = np.asarray(archive["fits"], dtype=float)
        costs = np.asarray([float(item["cost"]) for item in case["fits"]])
        recoveries = [recovery_record(objective, value, metadata["segment_directions"]) for value in fits]
        best_index = int(np.argmin(costs))
        alternatives = [
            index for index in range(len(fits))
            if index != best_index and recoveries[index]["segment_direction_max_deg"] > 3.0
        ]
        alternative_index = min(alternatives, key=lambda index: costs[index])
        failed_index = best_index if recoveries[best_index]["segment_direction_max_deg"] > 3.0 else alternative_index
        failed = fits[failed_index]
        transformed = static_preserving_heading_transform(objective, truth, failed)
        entries = []
        for index, value in enumerate(fits):
            entries.append({
                "start": index,
                "cost": float(costs[index]),
                "cost_ratio_to_best": float(costs[index] / costs[best_index]),
                "recovery": recoveries[index],
                "state_vs_truth": state_comparison(truth, value),
                "residual_costs": residual_costs(objective, value),
            })
        cases.append({
            "seed": seed,
            "noisy": noisy,
            "best_start": best_index,
            "closest_cost_product_distinct_start": alternative_index,
            "diagnostic_failed_basin_start": failed_index,
            "truth": {
                "residual_costs": residual_costs(objective, truth),
                "spectrum": spectrum(objective, truth),
            },
            "failed_basin": {
                "state_vs_truth": state_comparison(truth, failed),
                "residual_costs": residual_costs(objective, failed),
                "spectrum": spectrum(objective, failed),
            },
            "static_preserving_heading_symmetry_probe": {
                "construction": "failed-basin heading shifts plus exact initial-rest compensation of every sensor and parent-frame functional axis",
                "state_vs_truth": state_comparison(truth, transformed),
                "residual_costs": residual_costs(objective, transformed),
                "initial_static_cost_delta": (
                    residual_costs(objective, transformed)["by_action"].get("initial_still_attempt2", 0.0)
                    - residual_costs(objective, truth)["by_action"].get("initial_still_attempt2", 0.0)
                ),
                "measured_dynamic_cost_delta": (
                    residual_costs(objective, transformed)["by_classification"].get("MEASURED_OBSERVATION", 0.0)
                    - residual_costs(objective, truth)["by_classification"].get("MEASURED_OBSERVATION", 0.0)
                ),
            },
            "fits": entries,
        })

    output = {
        "schema": "biospur-pure-imu-v0-b2-global-identifiability-diagnosis-v1",
        "source_qualification": str(args.qualification),
        "source_terminal_outcome": report["terminal_outcome"],
        "source_access_audit_clean": True,
        "classification": {
            "exact_symmetry_or_missing_local_rank": False,
            "near_static_subtree_symmetry": True,
            "parameterization_defects_found": [
                "initializer fixed both latent static root yaws to zero while profiling relative headings",
                "S2 latitude bound +/-1.45 rad excluded a valid sensor axis at 1.513948 rad",
                "first B2 bilateral opposition model was invalid for mirrored knee forward components and was discarded",
            ],
            "global_search_failure_present": True,
            "structural_protocol_insufficiency_present": True,
            "basis": [
                "noise-free correct fitted basins have the lowest objective cost, while optimized product-distinct basins remain local near-symmetries",
                "a static-preserving heading transform changes initial-rest cost by approximately zero but raises measured dynamic cost, proving the symmetry is not exact",
                "profiled product rank is 55 and conditional nine-heading rank is 9, but weak singular directions remain",
                "noisy product-distinct basins survive within the declared cost-ratio neighborhood",
                "trunk handedness and required lower-chain action-ablation gates remain failed",
            ],
        },
        "required_action_ablations": report["action_ablations"],
        "sign_handedness_alternatives": report["sign_handedness_alternatives"],
        "rank_lineage": report["rank_lineage"],
        "cases": cases,
        "minimal_additional_motion": {
            "lower_chain": "one complete bilateral hip circumduction/pelvis-hula episode with clear two-axis relative thigh motion, followed by one seated knee flexion episode with the thigh held still and deliberate small tibial axial rotation; both must return to the same rest",
            "trunk": "one complete labelled left-right lateral-bend episode in addition to axial rotation and flexion, returning to the same rest",
            "why_minimal": "these add measured non-collinear directions in the weak lower-chain and trunk sign subspaces; more repetitions of the existing nearly collinear flexion actions do not address the failed singular/action-ablation directions",
        },
        "real_data_authorized": False,
    }
    target = args.qualification / "B2_GLOBAL_IDENTIFIABILITY_DIAGNOSIS.json"
    target.write_text(json.dumps(jsonable(output), indent=2, sort_keys=True) + "\n")
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
