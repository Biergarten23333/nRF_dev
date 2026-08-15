#!/usr/bin/env python3
"""Export and reload-verify the frozen D0-B synthetic nullspace evidence.

This tool is instrumentation only.  ``reconstruct`` calls the existing
``qualify_d0_synthetic`` evaluator exactly once and captures the two Jacobians
that evaluator already computes.  It does not run an optimizer or alter the
production model.  ``verify`` imports no BioSpur production module and works
only from the persisted evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any, Callable, Mapping

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
EXPECTED_HEAD = "bc4060909285a7d51fe9b464f0867aa004f4ef45"
ABS_TOL = 1e-12
REL_TOL = 1e-12
SENSITIVITY_REPORTING_FLOOR = 1e-14
ALLOWED_NONDATA_CLASSES = {
    "MANIFOLD_CONSTRAINT",
    "HARD_PHYSICAL_CONSTRAINT",
    "GAUGE_CONVENTION",
    "TEMPORAL_REGULARIZER",
    "MODEL_REGULARIZER",
    "PROTOCOL_PRIOR",
    "POPULATION_PRIOR",
    "ENGINEERING_PRIOR",
}


def canonical(value: Any) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n").encode()


def dump(path: Path, value: Any) -> None:
    path.write_bytes(canonical(value))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def semantic_hash(name: str, value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    digest = hashlib.sha256()
    digest.update(name.encode("utf-8") + b"\0")
    digest.update(array.dtype.str.encode("ascii") + b"\0")
    digest.update(np.asarray(array.shape, dtype="<i8").tobytes())
    digest.update(array.tobytes(order="C"))
    return digest.hexdigest()


def rank_from_spectrum(singular: np.ndarray, threshold: float) -> int:
    singular = np.asarray(singular, float)
    if not len(singular) or singular[0] <= 0.0:
        return 0
    return int(np.sum(singular > singular[0] * threshold))


def reload_rank_summary(arrays: Mapping[str, np.ndarray], threshold: float) -> dict[str, int]:
    """Reconstruct published ranks from persisted matrices only."""
    return {
        "data_only_full_rank": rank_from_spectrum(
            np.linalg.svd(arrays["J_data"], compute_uv=False), threshold
        ),
        "data_only_nuisance_rank": rank_from_spectrum(
            np.linalg.svd(arrays["J_nuisance"], compute_uv=False), threshold
        ),
        "data_only_profiled_product_rank": rank_from_spectrum(
            np.linalg.svd(arrays["J_eff"], compute_uv=False), threshold
        ),
        "data_plus_prior_rank": rank_from_spectrum(
            np.linalg.svd(arrays["J_full"], compute_uv=False), threshold
        ),
    }


def canonicalize_columns(vectors: np.ndarray) -> np.ndarray:
    output = np.asarray(vectors, float).copy()
    for column in range(output.shape[1]):
        pivot = int(np.argmax(np.abs(output[:, column])))
        if output[pivot, column] < 0.0:
            output[:, column] *= -1.0
    return output


def close(left: float, right: float) -> bool:
    return math.isclose(float(left), float(right), rel_tol=REL_TOL, abs_tol=ABS_TOL)


def verify_frozen_bindings(freeze: Mapping[str, Any]) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if head != EXPECTED_HEAD or head != freeze["head"]:
        raise RuntimeError(f"unexpected HEAD: {head}")
    for item in freeze["runtime_import_closure"] + freeze["frozen_inputs"]:
        path = ROOT / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise RuntimeError(f"frozen source/config changed: {path}")
    old = freeze["existing_d0b"]
    for role in ("result", "report"):
        path = ROOT / old[f"{role}_path"]
        if sha256(path) != old[f"{role}_sha256"]:
            raise RuntimeError(f"existing D0-B {role} changed")


def invoke_frozen_production_evaluator(
    module: Any,
    r3d_contract: Mapping[str, Any],
    chain_map: Mapping[str, Any],
    action_contract: Mapping[str, Any],
    evaluator: Callable[..., Mapping[str, Any]] | None = None,
) -> tuple[Mapping[str, Any], dict[str, Any]]:
    """Call the frozen evaluator once while capturing its existing intermediates."""
    evaluator = module.qualify_d0_synthetic if evaluator is None else evaluator
    captured: dict[str, Any] = {"jacobians": []}
    original_generate = module.generate_synthetic_dataset
    original_objective = module.D0SyntheticObjective
    original_fd = module.finite_difference_jacobian

    def generate(*args: Any, **kwargs: Any) -> Any:
        value = original_generate(*args, **kwargs)
        captured["dataset"] = value
        return value

    def objective_factory(*args: Any, **kwargs: Any) -> Any:
        value = original_objective(*args, **kwargs)
        captured["objective"] = value
        return value

    def fd(fun: Callable[[np.ndarray], np.ndarray], x: np.ndarray, step: float) -> np.ndarray:
        value = original_fd(fun, x, step)
        captured["jacobians"].append(np.asarray(value, float).copy())
        return value

    module.generate_synthetic_dataset = generate
    module.D0SyntheticObjective = objective_factory
    module.finite_difference_jacobian = fd
    try:
        result = evaluator(r3d_contract, chain_map, action_contract)
    finally:
        module.generate_synthetic_dataset = original_generate
        module.D0SyntheticObjective = original_objective
        module.finite_difference_jacobian = original_fd
    if evaluator is module.qualify_d0_synthetic and len(captured["jacobians"]) != 2:
        raise RuntimeError("frozen evaluator did not produce exactly data and full Jacobians")
    return result, captured


def expand_parameter_inventory(layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for entry in layout["entries"]:
        name = entry["name"]
        width = int(entry["stop"] - entry["start"])
        if name.startswith(("segment_axis:", "functional_axis:", "latent_pose:")):
            components = ["theta", "phi"]
            ambient, intrinsic, manifold = 3, 2, "S2_UNIT_AXIS"
        elif name == "trunk_functional_frame":
            components = ["rotvec_x", "rotvec_y", "rotvec_z"]
            ambient, intrinsic, manifold = 9, 3, "SO3_ROTATION_MATRIX"
        else:
            components = ["angle"]
            ambient, intrinsic, manifold = 1, 1, "EUCLIDEAN_ANGLE"
        if len(components) != width:
            raise ValueError(f"component width mismatch for {name}")
        lifecycle = {
            "PER_WEAR_MOUNTING": "PER_WEAR",
            "SUBJECT_FUNCTIONAL": "SUBJECT",
            "JOINT_ZERO": "SUBJECT",
            "CALIBRATION_SESSION_NUISANCE": "SESSION",
        }[entry["block"]]
        publishable = int(entry["stop"]) <= int(layout["publishable_dimension"])
        for offset, component in enumerate(components):
            gauge = "NONE_DECLARED"
            if name.startswith("segment_axis:"):
                gauge = "AXIAL_TWIST_EXCLUDED_FROM_STATE"
            elif name.startswith("relative_heading:"):
                gauge = "EFFECTIVE_HEADING; PELVIS_GLOBAL_YAW_COORDINATE_REMOVED"
            output.append(
                {
                    "column_index": int(entry["start"] + offset),
                    "coordinate_name": f"{name}:{component}",
                    "parameter_entry": name,
                    "block": entry["block"],
                    "unit": "rad",
                    "numerical_coordinate_scale": 1.0,
                    "ambient_dimension": ambient,
                    "intrinsic_dof": intrinsic,
                    "manifold": manifold,
                    "publishable": publishable,
                    "nuisance": not publishable,
                    "lifecycle": lifecycle,
                    "frame_convention": "ACTIVE R_Ni_Bi; left-multiplied effective heading correction; column-vector convention",
                    "replay_consumer": "CONTINUOUS_LABEL_BLIND_FORWARD_MODEL" if publishable else "CALIBRATION_ONLY_NOT_REPLAYED",
                    "gauge_or_convention_status": gauge,
                }
            )
    validate_parameter_inventory(output, int(layout["dimension"]))
    return output


def validate_parameter_inventory(inventory: list[Mapping[str, Any]], dimension: int) -> None:
    required = {
        "column_index", "coordinate_name", "unit", "numerical_coordinate_scale",
        "ambient_dimension", "intrinsic_dof", "publishable", "nuisance",
        "lifecycle", "frame_convention", "replay_consumer", "gauge_or_convention_status",
    }
    if len(inventory) != dimension:
        raise ValueError("parameter inventory dimension mismatch")
    if [item.get("column_index") for item in inventory] != list(range(dimension)):
        raise ValueError("parameter inventory order mismatch")
    for item in inventory:
        missing = required - set(item)
        if missing or not item["unit"] or not math.isfinite(float(item["numerical_coordinate_scale"])):
            raise ValueError(f"parameter metadata incomplete: {missing}")


def touched_parameters(action: str, factor: str, layout: Mapping[str, Any]) -> list[str]:
    names = [item["name"] for item in layout["entries"]]
    chosen: set[str] = set()

    def add_segment(segment: str) -> None:
        chosen.add(f"segment_axis:{segment}")
        heading = f"relative_heading:{segment}"
        if heading in names:
            chosen.add(heading)

    if factor.startswith("latent_pose_data:"):
        segment = factor.split(":", 1)[1]
        add_segment(segment)
        chosen.add(f"latent_pose:{action}:{segment}")
    elif factor.startswith("protocol_pose_prior:"):
        _, pose, segment = factor.split(":", 2)
        chosen.add(f"latent_pose:{pose}:{segment}")
    elif factor.startswith("neutral_zero_data:"):
        chosen.add(f"neutral_zero:{factor.split(':', 1)[1]}")
    elif factor.startswith(("broad_hinge:", "curl_pronation_subspace:")):
        joint = factor.split(":", 1)[1]
        joint_segments = {
            "shoulder_L": ("torso", "upper_arm_L"), "shoulder_R": ("torso", "upper_arm_R"),
            "elbow_L": ("upper_arm_L", "forearm_L"), "elbow_R": ("upper_arm_R", "forearm_R"),
            "hip_L": ("pelvis", "thigh_L"), "hip_R": ("pelvis", "thigh_R"),
            "knee_L": ("thigh_L", "shank_L"), "knee_R": ("thigh_R", "shank_R"),
        }
        for segment in joint_segments[joint]:
            add_segment(segment)
        chosen.add(f"functional_axis:{joint}")
    elif factor == "bilateral_phase_consistency":
        for segment in ("pelvis", "thigh_L", "thigh_R"):
            add_segment(segment)
    elif factor == "minimal_trunk_turn_flex_plane":
        add_segment("pelvis")
        add_segment("torso")
        chosen.add("trunk_functional_frame")
    else:
        raise ValueError(f"unknown factor: {factor}")
    return [name for name in names if name in chosen]


def whitening(action: str, factor: str, scalar_rows: int, contract: Mapping[str, Any]) -> str:
    covariance = contract["covariance_semantics"]
    if factor.startswith("latent_pose_data:"):
        return f"1/(deg2rad({covariance['static_model_mismatch_sigma_deg']})*sqrt({scalar_rows // 3}))"
    if factor.startswith("neutral_zero_data:"):
        return f"1/{covariance['neutral_zero_sigma_rad']} rad"
    if factor.startswith("protocol_pose_prior:"):
        return f"1/deg2rad({covariance['protocol_pose_prior_sigma_deg']})"
    return f"1/({covariance['dynamic_gyro_sigma_rad_s']} rad/s*sqrt(selected_sample_count))"


def build_row_manifest(objective: Any, truth: np.ndarray, layout: Mapping[str, Any], contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    row = 0
    for action, factors in objective.data_blocks(truth).items():
        for factor, values in factors:
            values = np.asarray(values)
            touched = touched_parameters(action, factor, layout)
            for local in range(len(values)):
                records.append({
                    "row_index": row,
                    "action": action,
                    "factor": factor,
                    "factor_scalar_index": local,
                    "classification": "DATA",
                    "nondata_class": None,
                    "units": "dimensionless_whitened_residual",
                    "whitening": whitening(action, factor, len(values), contract),
                    "robust_loss": "LINEAR_L2_NO_ROBUST_LOSS",
                    "parameter_blocks_touched": touched,
                })
                row += 1
    for factor, values in objective.prior_blocks(truth):
        _, action, _ = factor.split(":", 2)
        touched = touched_parameters(action, factor, layout)
        for local in range(len(values)):
            records.append({
                "row_index": row,
                "action": action,
                "factor": factor,
                "factor_scalar_index": local,
                "classification": "NONDATA",
                "nondata_class": "PROTOCOL_PRIOR",
                "units": "dimensionless_whitened_residual",
                "whitening": whitening(action, factor, len(values), contract),
                "robust_loss": "LINEAR_L2_NO_ROBUST_LOSS",
                "parameter_blocks_touched": touched,
            })
            row += 1
    validate_row_manifest(records)
    return records


def validate_row_manifest(rows: list[Mapping[str, Any]]) -> None:
    if [item.get("row_index") for item in rows] != list(range(len(rows))):
        raise ValueError("residual row order is not contiguous")
    for item in rows:
        if item["classification"] == "NONDATA":
            if item.get("nondata_class") not in ALLOWED_NONDATA_CLASSES:
                raise ValueError("non-data residual lacks a valid explicit classification")
            if item["nondata_class"] == "MANIFOLD_CONSTRAINT" and item["factor"].startswith("protocol_pose_prior:"):
                raise ValueError("soft protocol prior misclassified as manifold constraint")
        elif item["classification"] != "DATA" or item.get("nondata_class") is not None:
            raise ValueError("invalid residual classification")


def validate_order_bindings(
    rows: list[Mapping[str, Any]],
    inventory: list[Mapping[str, Any]],
    expected_row_sha256: str,
    expected_column_sha256: str,
) -> None:
    row_digest = hashlib.sha256(canonical(rows)).hexdigest()
    column_digest = hashlib.sha256(
        canonical([item["coordinate_name"] for item in inventory])
    ).hexdigest()
    if row_digest != expected_row_sha256:
        raise ValueError("residual row order binding mismatch")
    if column_digest != expected_column_sha256:
        raise ValueError("parameter column order binding mismatch")


def block_energy(vector: np.ndarray, layout: Mapping[str, Any]) -> list[dict[str, Any]]:
    values = []
    for entry in layout["entries"]:
        values.append({
            "parameter": entry["name"],
            "block": entry["block"],
            "l2_energy": float(np.linalg.norm(vector[entry["start"]:entry["stop"]])),
        })
    return sorted(values, key=lambda item: (-item["l2_energy"], item["parameter"]))


def compare_old_summary(
    old: Mapping[str, Any],
    current: Mapping[str, Any],
    data_jacobian: np.ndarray,
    full_jacobian: np.ndarray,
    objective: Any,
    truth: np.ndarray,
    layout: Mapping[str, Any],
    threshold: float,
) -> dict[str, Any]:
    data_s = np.linalg.svd(data_jacobian, compute_uv=False)
    _, full_s, full_vh = np.linalg.svd(full_jacobian, full_matrices=False)
    data_rank = rank_from_spectrum(data_s, threshold)
    full_rank = rank_from_spectrum(full_s, threshold)
    checks: dict[str, bool] = {
        "terminal_outcome": current["terminal_outcome"] == old["terminal_outcome"] == "FAIL_D0B_SYNTHETIC_NULLSPACE",
        "data_shape": [*data_jacobian.shape] == [old["data_only_observability"]["rows"], old["data_only_observability"]["columns"]],
        "full_shape": [*full_jacobian.shape] == [old["data_plus_protocol_prior_observability"]["rows"], old["data_plus_protocol_prior_observability"]["columns"]],
        "data_rank": data_rank == current["data_only_observability"]["rank"] == old["data_only_observability"]["rank"] == 72,
        "full_rank": full_rank == current["data_plus_protocol_prior_observability"]["rank"] == old["data_plus_protocol_prior_observability"]["rank"] == 92,
        "remaining_nullity": 95 - full_rank == 3,
        "data_sigma_max": close(data_s[0], old["data_only_observability"]["sigma_max"]),
        "data_sigma_min": close(data_s[-1], old["data_only_observability"]["sigma_min"]),
        "full_sigma_max": close(full_s[0], old["data_plus_protocol_prior_observability"]["sigma_max"]),
        "full_sigma_min": close(full_s[-1], old["data_plus_protocol_prior_observability"]["sigma_min"]),
        "state_layout": current["state_layout"] == old["state_layout"] == layout,
        "action_accounting": current["action_residual_accounting"] == old["action_residual_accounting"],
    }
    for action, item in old["action_publishable_information"].items():
        checks[f"action_norm:{action}"] = close(
            current["action_publishable_information"][action]["jacobian_frobenius_norm"],
            item["jacobian_frobenius_norm"],
        )
    new_null = []
    for index in range(95 - full_rank):
        vector = full_vh[-(index + 1)]
        new_null.append({"singular_value": float(full_s[-(index + 1)]), "parameter_block_energy": block_energy(vector, layout)})
    for index, old_direction in enumerate(old["null_directions"]):
        checks[f"null_singular:{index}"] = close(new_null[index]["singular_value"], old_direction["singular_value"])
        old_energy = {item["parameter"]: item["l2_energy"] for item in old_direction["parameter_block_energy"]}
        checks[f"null_block_energy:{index}"] = all(
            close(item["l2_energy"], old_energy[item["parameter"]])
            for item in new_null[index]["parameter_block_energy"]
        )
    accounting, _ = objective.action_slices(truth)
    checks["row_order"] = accounting == old["action_residual_accounting"]
    checks["parameter_order"] = layout == old["state_layout"]
    return {
        "status": "PASS_OLD_SUMMARY_REPRODUCTION" if all(checks.values()) else "FAIL_D0B_EVIDENCE_RECONSTRUCTION_MISMATCH",
        "absolute_tolerance": ABS_TOL,
        "relative_tolerance": REL_TOL,
        "checks": checks,
        "all_pass": all(checks.values()),
    }


def factor_ranges(rows: list[Mapping[str, Any]]) -> list[tuple[tuple[str, str, str], int, int]]:
    output = []
    start = 0
    while start < len(rows):
        key = (rows[start]["classification"], rows[start]["action"], rows[start]["factor"])
        stop = start + 1
        while stop < len(rows) and (rows[stop]["classification"], rows[stop]["action"], rows[stop]["factor"]) == key:
            stop += 1
        output.append((key, start, stop))
        start = stop
    return output


def sensitivity_summary(
    full_jacobian: np.ndarray,
    profiled: np.ndarray,
    rows: list[Mapping[str, Any]],
    inventory: list[Mapping[str, Any]],
    data_rows: int,
    threshold: float,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    names: list[str] = []
    bounds: list[tuple[int, int]] = []
    for item in inventory:
        base = item["parameter_entry"]
        if not names or names[-1] != base:
            names.append(base)
            bounds.append((item["column_index"], item["column_index"] + 1))
        else:
            bounds[-1] = (bounds[-1][0], item["column_index"] + 1)
    action_profiled: dict[str, Any] = {}
    for (classification, action, factor), start, stop in factor_ranges(rows):
        matrix = full_jacobian[start:stop]
        for name, (left, right) in zip(names, bounds):
            norm = float(np.linalg.norm(matrix[:, left:right]))
            entries.append({
                "classification": classification,
                "action": action,
                "factor": factor,
                "parameter": name,
                "jacobian_frobenius_norm": norm,
                "above_reporting_floor": norm > SENSITIVITY_REPORTING_FLOOR,
            })
    data_actions = sorted({item["action"] for item in rows[:data_rows]})
    for action in data_actions:
        indices = [item["row_index"] for item in rows[:data_rows] if item["action"] == action]
        block = profiled[np.asarray(indices, int)]
        singular = np.linalg.svd(block, compute_uv=False)
        action_profiled[action] = {
            "rows": len(indices),
            "profiled_publishable_frobenius_norm": float(np.linalg.norm(block)),
            "profiled_publishable_rank": rank_from_spectrum(singular, threshold),
            "complete_singular_spectrum": singular.tolist(),
        }
    return {
        "reporting_floor": SENSITIVITY_REPORTING_FLOOR,
        "action_factor_parameter": entries,
        "per_action_profiled_information": action_profiled,
    }


def copy_originals(old_dir: Path, destination: Path) -> None:
    destination.mkdir(parents=True)
    for source in sorted(old_dir.iterdir()):
        if source.is_file():
            shutil.copyfile(source, destination / source.name)
            if sha256(source) != sha256(destination / source.name):
                raise RuntimeError(f"byte-copy mismatch: {source}")


def reconstruct(args: argparse.Namespace) -> int:
    freeze = json.loads(args.freeze.read_text())
    verify_frozen_bindings(freeze)
    if args.output.exists():
        raise FileExistsError(args.output)
    sys.path.insert(0, str(ROOT / "Fusion_Part/src"))
    from biospur_fusion.imu_multi_action_revision_d import d0_synthetic as production

    r3d_contract = json.loads(args.r3d_contract.read_text())
    chain_map = json.loads(args.chain_map.read_text())
    action_contract = json.loads(args.action_contract.read_text())
    old = json.loads((args.old_d0b / "D0B_SYNTHETIC_REPLAY_1.json").read_text())

    current, captured = invoke_frozen_production_evaluator(
        production, r3d_contract, chain_map, action_contract
    )
    data_jacobian, full_jacobian = captured["jacobians"]
    timeline, domains, mapping, metadata, truth = captured["dataset"]
    objective = captured["objective"]
    del timeline, domains, mapping, metadata
    threshold = float(action_contract["synthetic_qualification"]["relative_singular_value_rank_threshold"])
    reproduction = compare_old_summary(
        old, current, data_jacobian, full_jacobian, objective, truth,
        production.LAYOUT, threshold,
    )
    if not reproduction["all_pass"]:
        print(json.dumps(reproduction, sort_keys=True))
        return 3

    args.output.mkdir(parents=True)
    copy_originals(args.old_d0b, args.output / "original_d0b")
    dump(args.output / "OLD_SUMMARY_REPRODUCTION.json", reproduction)

    inventory = expand_parameter_inventory(production.LAYOUT)
    rows = build_row_manifest(objective, truth, production.LAYOUT, action_contract)
    data_rows = data_jacobian.shape[0]
    if len(rows) != full_jacobian.shape[0] or sum(item["classification"] == "DATA" for item in rows) != data_rows:
        raise RuntimeError("row manifest does not close against matrices")
    nondata_jacobian = full_jacobian[data_rows:].copy()
    publishable = data_jacobian[:, :55].copy()
    nuisance = data_jacobian[:, 55:].copy()

    nuisance_u, nuisance_s, _ = np.linalg.svd(nuisance, full_matrices=False)
    nuisance_rank = rank_from_spectrum(nuisance_s, threshold)
    nuisance_basis = nuisance_u[:, :nuisance_rank]
    profiled = publishable - nuisance_basis @ (nuisance_basis.T @ publishable)
    data_u, data_s, data_vh = np.linalg.svd(data_jacobian, full_matrices=False)
    full_u, full_s, full_vh = np.linalg.svd(full_jacobian, full_matrices=False)
    nondata_s = np.linalg.svd(nondata_jacobian, compute_uv=False)
    profiled_s = np.linalg.svd(profiled, compute_uv=False)
    data_rank = rank_from_spectrum(data_s, threshold)
    full_rank = rank_from_spectrum(full_s, threshold)
    profiled_rank = rank_from_spectrum(profiled_s, threshold)
    # Preserve the historical direction numbering: index zero is the vector
    # paired with the smallest singular value, then proceed upward.
    data_null = canonicalize_columns(data_vh[data_rank:][::-1].T)
    full_null = canonicalize_columns(full_vh[full_rank:][::-1].T)
    constraint = np.empty((0, 95), dtype=float)
    tangent = np.eye(95, dtype=float)

    arrays = {
        "J_data": data_jacobian,
        "J_nondata": nondata_jacobian,
        "J_full": full_jacobian,
        "J_publishable": publishable,
        "J_nuisance": nuisance,
        "C": constraint,
        "T": tangent,
        "J_eff": profiled,
        "singular_values_data": data_s,
        "singular_values_nondata": nondata_s,
        "singular_values_full": full_s,
        "singular_values_nuisance": nuisance_s,
        "singular_values_profiled_publishable": profiled_s,
        "null_vectors_data": data_null,
        "null_projector_data": data_null @ data_null.T,
        "null_vectors_full": full_null,
        "null_projector_full": full_null @ full_null.T,
        "synthetic_truth_parameter_point": np.asarray(truth, float),
        "residual_data": np.asarray(objective.residual(truth, False), float),
        "residual_nondata": np.asarray(objective.residual(truth, True)[data_rows:], float),
        "residual_full": np.asarray(objective.residual(truth, True), float),
    }
    if not all(np.isfinite(value).all() for value in arrays.values()):
        raise RuntimeError("non-finite reconstructed evidence")
    np.savez_compressed(args.output / "D0B_MATRIX_EVIDENCE.npz", **arrays)
    semantic = {
        name: {"dtype": np.asarray(value).dtype.str, "shape": list(np.asarray(value).shape), "semantic_sha256": semantic_hash(name, value)}
        for name, value in arrays.items()
    }
    dump(args.output / "ARRAY_SEMANTIC_HASHES.json", semantic)
    dump(args.output / "PARAMETER_INVENTORY.json", {
        "dimension": 95,
        "publishable_dimension": 55,
        "nuisance_dimension": 40,
        "coordinate_order_sha256": hashlib.sha256(canonical([item["coordinate_name"] for item in inventory])).hexdigest(),
        "coordinates": inventory,
    })
    dump(args.output / "RESIDUAL_ROW_MANIFEST.json", {
        "rows": len(rows),
        "data_rows": data_rows,
        "nondata_rows": len(rows) - data_rows,
        "row_order_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        "records": rows,
    })
    sensitivity = sensitivity_summary(full_jacobian, profiled, rows, inventory, data_rows, threshold)
    dump(args.output / "ACTION_FACTOR_PARAMETER_SENSITIVITY.json", sensitivity)

    data_directions = [
        {"index": index, "singular_value": float(data_s[-(index + 1)]), "full_components": data_null[:, index].tolist(), "parameter_block_energy": block_energy(data_null[:, index], production.LAYOUT)}
        for index in range(data_null.shape[1])
    ]
    full_directions = [
        {"index": index, "singular_value": float(full_s[-(index + 1)]), "full_components": full_null[:, index].tolist(), "parameter_block_energy": block_energy(full_null[:, index], production.LAYOUT)}
        for index in range(full_null.shape[1])
    ]
    nullspace = {
        "canonical_sign_rule": "largest-absolute component is positive",
        "degenerate_subspace_representation": "projectors persisted in D0B_MATRIX_EVIDENCE.npz",
        "data_only": {"rank": data_rank, "nullity": 95 - data_rank, "directions": data_directions},
        "data_plus_protocol_prior": {"rank": full_rank, "nullity": 95 - full_rank, "directions": full_directions},
    }
    dump(args.output / "NULLSPACE_AUDIT_FULL.json", nullspace)
    matrix_summary = {
        "frozen_relative_singular_value_threshold": threshold,
        "profiling_algorithm": "SVD_LEFT_NULL_PROJECTION_NO_JTJ",
        "data_only_full_rank": data_rank,
        "data_only_full_nullity": 95 - data_rank,
        "data_only_nuisance_rank": nuisance_rank,
        "data_only_joint_rank": data_rank,
        "data_only_profiled_product_rank": profiled_rank,
        "constraint_jacobian_applicable": False,
        "constraint_tangent_dimension": 95,
        "data_plus_prior_rank": full_rank,
        "complete_singular_spectra": {
            "data": data_s.tolist(),
            "nondata": nondata_s.tolist(),
            "full": full_s.tolist(),
            "nuisance": nuisance_s.tolist(),
            "profiled_publishable": profiled_s.tolist(),
        },
        "matrix_shapes": {name: list(value.shape) for name, value in arrays.items()},
        "row_order_sha256": hashlib.sha256(canonical(rows)).hexdigest(),
        "column_order_sha256": hashlib.sha256(canonical([item["coordinate_name"] for item in inventory])).hexdigest(),
    }
    dump(args.output / "MATRIX_SUMMARY.json", matrix_summary)
    dump(args.output / "SYNTHETIC_TRUTH_RECOVERY_SUMMARY.json", {
        "evaluation_point": "EXISTING_DETERMINISTIC_SYNTHETIC_TRUTH",
        "seed": 9007,
        "optimizer_iterations": 0,
        "fit_or_recovery_performed": False,
        "truth_parameter_semantic_sha256": semantic["synthetic_truth_parameter_point"]["semantic_sha256"],
        "historical_verdict": current["terminal_outcome"],
        "note": "This checkpoint evaluates structural observability at the frozen truth point; it is not an optimizer recovery claim.",
    })
    dump(args.output / "RECONSTRUCTION_RESULT.json", {
        "status": "PASS_D0B_EVIDENCE_RECONSTRUCTION_MATCH",
        "historical_verdict": "FAIL_D0B_SYNTHETIC_NULLSPACE",
        "production_evaluator": "biospur_fusion.imu_multi_action_revision_d.d0_synthetic.qualify_d0_synthetic",
        "production_evaluator_call_count": 1,
        "optimizer_iterations": 0,
        "data_only_rank": data_rank,
        "data_only_nuisance_rank": nuisance_rank,
        "data_only_profiled_product_rank": profiled_rank,
        "constraint_tangent_dimension": 95,
        "data_plus_prior_rank": full_rank,
        "REAL_D0_OBJECTIVE": "NOT_RUN",
        "REAL_D0_JACOBIAN": "NOT_RUN",
        "REAL_D0_SOLVER": "NOT_RUN",
    })
    dump(args.output / "DATA_ACCESS_AUDIT.json", {
        "opened": ["FROZEN_SYNTHETIC_EVALUATOR", "FROZEN_SYNTHETIC_CONFIG", "EXISTING_COMPACT_D0B_SUMMARY"],
        "real_calibration_payload_opened": False,
        "q2_cache_payload_opened": False,
        "forbidden_inputs_opened": [],
        "FINAL_STILL": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED", "WALK": "SEALED", "UWB_T4_ANCHOR": "SEALED",
    })
    dump(args.output / "SOURCE_BINDINGS.json", {
        "pre_reconstruction_freeze_sha256": sha256(args.freeze),
        "frozen_head": freeze["head"],
        "runtime_import_closure": freeze["runtime_import_closure"],
        "frozen_inputs": freeze["frozen_inputs"],
        "existing_d0b": freeze["existing_d0b"],
    })
    (args.output / "REPORT.md").write_text(
        "# D0-B evidence-only deterministic reconstruction\n\n"
        "`PASS_D0B_EVIDENCE_RECONSTRUCTION_MATCH`\n\n"
        "The standalone exporter called the frozen production synthetic evaluator once, ran zero optimizer iterations, and reproduced the historical 72/95 data rank and 92/95 data-plus-protocol-prior rank. The historical verdict remains `FAIL_D0B_SYNTHETIC_NULLSPACE`. This report does not classify or repair that nullspace.\n"
    )
    reconstruction_manifest = {
        str(path.relative_to(args.output)): sha256(path)
        for path in sorted(args.output.rglob("*"))
        if path.is_file() and path.name != "RECONSTRUCTION_MANIFEST.json"
    }
    dump(args.output / "RECONSTRUCTION_MANIFEST.json", reconstruction_manifest)
    print(json.dumps({"status": "PASS_D0B_EVIDENCE_RECONSTRUCTION_MATCH", **matrix_summary}, sort_keys=True))
    return 0


def verify_reconstruction_manifest(output: Path) -> None:
    manifest = json.loads((output / "RECONSTRUCTION_MANIFEST.json").read_text())
    for relative, expected in manifest.items():
        if sha256(output / relative) != expected:
            raise RuntimeError(f"reconstruction manifest mismatch: {relative}")


def reload_verify(args: argparse.Namespace) -> int:
    freeze = json.loads(args.freeze.read_text())
    verify_frozen_bindings(freeze)
    verify_reconstruction_manifest(args.output)
    summary = json.loads((args.output / "MATRIX_SUMMARY.json").read_text())
    semantic = json.loads((args.output / "ARRAY_SEMANTIC_HASHES.json").read_text())
    inventory_payload = json.loads((args.output / "PARAMETER_INVENTORY.json").read_text())
    rows_payload = json.loads((args.output / "RESIDUAL_ROW_MANIFEST.json").read_text())
    validate_parameter_inventory(inventory_payload["coordinates"], inventory_payload["dimension"])
    validate_row_manifest(rows_payload["records"])
    validate_order_bindings(
        rows_payload["records"],
        inventory_payload["coordinates"],
        summary["row_order_sha256"],
        summary["column_order_sha256"],
    )
    with np.load(args.output / "D0B_MATRIX_EVIDENCE.npz", allow_pickle=False) as archive:
        arrays = {name: archive[name] for name in archive.files}
    for name, record in semantic.items():
        value = arrays[name]
        if list(value.shape) != record["shape"] or value.dtype.str != record["dtype"] or semantic_hash(name, value) != record["semantic_sha256"]:
            raise RuntimeError(f"array semantic verification failed: {name}")
    if not all(np.isfinite(value).all() for value in arrays.values()):
        raise RuntimeError("reloaded arrays contain non-finite values")
    if not np.array_equal(arrays["J_full"][:arrays["J_data"].shape[0]], arrays["J_data"]):
        raise RuntimeError("J_full data rows differ from J_data")
    if not np.array_equal(arrays["J_full"][arrays["J_data"].shape[0]:], arrays["J_nondata"]):
        raise RuntimeError("J_full nondata rows differ from J_nondata")
    threshold = float(summary["frozen_relative_singular_value_threshold"])
    data_s = np.linalg.svd(arrays["J_data"], compute_uv=False)
    full_s = np.linalg.svd(arrays["J_full"], compute_uv=False)
    nuisance_s = np.linalg.svd(arrays["J_nuisance"], compute_uv=False)
    profiled_s = np.linalg.svd(arrays["J_eff"], compute_uv=False)
    ranks = reload_rank_summary(arrays, threshold)
    checks = {
        "data_rank": ranks["data_only_full_rank"] == summary["data_only_full_rank"] == 72,
        "nuisance_rank": ranks["data_only_nuisance_rank"] == summary["data_only_nuisance_rank"],
        "profiled_rank": ranks["data_only_profiled_product_rank"] == summary["data_only_profiled_product_rank"],
        "full_rank": ranks["data_plus_prior_rank"] == summary["data_plus_prior_rank"] == 92,
        "data_spectrum": np.allclose(data_s, summary["complete_singular_spectra"]["data"], rtol=REL_TOL, atol=ABS_TOL),
        "full_spectrum": np.allclose(full_s, summary["complete_singular_spectra"]["full"], rtol=REL_TOL, atol=ABS_TOL),
        "data_null_projector": np.allclose(arrays["null_vectors_data"] @ arrays["null_vectors_data"].T, arrays["null_projector_data"], rtol=REL_TOL, atol=ABS_TOL),
        "full_null_projector": np.allclose(arrays["null_vectors_full"] @ arrays["null_vectors_full"].T, arrays["null_projector_full"], rtol=REL_TOL, atol=ABS_TOL),
        "row_count": len(rows_payload["records"]) == arrays["J_full"].shape[0],
        "column_count": len(inventory_payload["coordinates"]) == arrays["J_full"].shape[1],
        "row_order_hash": hashlib.sha256(canonical(rows_payload["records"])).hexdigest() == summary["row_order_sha256"],
        "column_order_hash": hashlib.sha256(canonical([item["coordinate_name"] for item in inventory_payload["coordinates"]])).hexdigest() == summary["column_order_sha256"],
    }
    old = json.loads((args.output / "original_d0b/D0B_SYNTHETIC_REPLAY_1.json").read_text())
    checks["old_data_rank"] = old["data_only_observability"]["rank"] == summary["data_only_full_rank"]
    checks["old_full_rank"] = old["data_plus_protocol_prior_observability"]["rank"] == summary["data_plus_prior_rank"]
    if not all(checks.values()):
        raise RuntimeError(f"reload-only verification failed: {checks}")
    result = {
        "status": "PASS_RELOAD_ONLY_VERIFICATION",
        "checks": {key: bool(value) for key, value in checks.items()},
        "all_arrays_finite": True,
        "production_module_imported": False,
        "production_reconstruction_count": 0,
        "data_only_full_rank": summary["data_only_full_rank"],
        "data_only_nuisance_rank": summary["data_only_nuisance_rank"],
        "data_only_profiled_product_rank": summary["data_only_profiled_product_rank"],
        "constraint_tangent_dimension": summary["constraint_tangent_dimension"],
        "data_plus_prior_rank": summary["data_plus_prior_rank"],
    }
    dump(args.output / "RELOAD_ONLY_VERIFICATION.json", result)
    return 0


def finalize_evidence(args: argparse.Namespace) -> int:
    freeze = json.loads(args.freeze.read_text())
    verify_frozen_bindings(freeze)
    reload_result = json.loads(
        (args.evidence_root / "matrix_evidence/RELOAD_ONLY_VERIFICATION.json").read_text()
    )
    if reload_result["status"] != "PASS_RELOAD_ONLY_VERIFICATION":
        raise RuntimeError("reload-only verification is not passing")
    mappings = [
        (args.r3d0, args.evidence_root / "r3d0_gauge_audit"),
        (args.r3d_synthetic, args.evidence_root / "r3d_synthetic"),
        (args.r3d_formal, args.evidence_root / "r3d_formal_compact"),
        (args.d0a, args.evidence_root / "d0a_freeze"),
        (args.old_d0b, args.evidence_root / "matrix_evidence/original_d0b"),
        (args.terminal, args.evidence_root / "terminal_report"),
    ]
    byte_copy_records = []
    for source_root, copy_root in mappings:
        for copied in sorted(copy_root.iterdir()):
            if not copied.is_file():
                continue
            source = source_root / copied.name
            if not source.is_file() or source.read_bytes() != copied.read_bytes():
                raise RuntimeError(f"source/copy byte mismatch: {copied}")
            byte_copy_records.append({
                "source": str(source.resolve()),
                "copy": str(copied.relative_to(args.evidence_root)),
                "size": copied.stat().st_size,
                "sha256": sha256(copied),
            })
    files = {}
    for path in sorted(args.evidence_root.rglob("*")):
        if not path.is_file() or path.name == "EVIDENCE_MANIFEST.json":
            continue
        relative = str(path.relative_to(args.evidence_root))
        role = "RECONSTRUCTED_MATRIX_EVIDENCE" if relative.startswith("matrix_evidence/") else "COMPACT_HISTORICAL_EVIDENCE"
        if relative in {"PRE_RECONSTRUCTION_FREEZE.json", "EVIDENCE_INDEX.md", "TEST_RESULTS.md"}:
            role = "CHECKPOINT_METADATA"
        files[relative] = {
            "sha256": sha256(path),
            "size": path.stat().st_size,
            "role": role,
        }
    omitted = args.r3d_formal / "R3D_NODE_AND_ACTION_ARRAYS.npz"
    manifest = {
        "schema": "biospur-r3d-d0b-synthetic-failure-checkpoint-evidence-v1",
        "branch": "feature/b306-bringup",
        "previous_head": EXPECTED_HEAD,
        "outcomes": {
            "R3D": "PASS_R3D_GAUGE_INVARIANT_BROAD_ACTIVITY",
            "D0_A": "D0A_CONTRACTS_FROZEN",
            "D0_B": "FAIL_D0B_SYNTHETIC_NULLSPACE",
            "RECONSTRUCTION": "PASS_D0B_EVIDENCE_RECONSTRUCTION_MATCH",
            "RELOAD_ONLY": "PASS_RELOAD_ONLY_VERIFICATION",
        },
        "published_ranks": {
            "DATA_ONLY_FULL_RANK": reload_result["data_only_full_rank"],
            "DATA_ONLY_NUISANCE_RANK": reload_result["data_only_nuisance_rank"],
            "DATA_ONLY_PROFILED_PRODUCT_RANK": reload_result["data_only_profiled_product_rank"],
            "CONSTRAINT_TANGENT_DIMENSION": reload_result["constraint_tangent_dimension"],
            "DATA_PLUS_PRIOR_RANK": reload_result["data_plus_prior_rank"],
        },
        "production_reconstruction_count": 1,
        "optimizer_iterations": 0,
        "source_and_config_unchanged_after_reconstruction": True,
        "runtime_import_closure": freeze["runtime_import_closure"],
        "frozen_inputs": freeze["frozen_inputs"],
        "byte_identical_copy_records": byte_copy_records,
        "files": files,
        "omitted_large_local_evidence": [{
            "absolute_path": str(omitted.resolve()),
            "size": omitted.stat().st_size,
            "sha256": sha256(omitted),
            "reason": "Compact checkpoint excludes reconstructable R3D formal array payload; compact summaries and source SHA binding are committed.",
        }],
        "forbidden_payload_committed": False,
        "raw_calibration_ledger_committed": False,
        "q2_cache_payload_committed": False,
        "sealed_status": {
            "FINAL_STILL": "SEALED", "GOLF": "SEALED", "BOXING": "SEALED",
            "WALK": "SEALED", "UWB_T4_ANCHOR": "SEALED",
        },
        "stop_state": {
            "REAL_D0_OBJECTIVE": "NOT_RUN", "REAL_D0_JACOBIAN": "NOT_RUN",
            "REAL_D0_SOLVER": "NOT_RUN", "FREEZE_REPLAY_RENDER": "NOT_STARTED",
        },
        "manifest_excludes_itself": True,
    }
    dump(args.evidence_root / "EVIDENCE_MANIFEST.json", manifest)
    print(json.dumps({
        "status": "PASS_EVIDENCE_MANIFEST",
        "files": len(files),
        "byte_identical_copies": len(byte_copy_records),
        "manifest_sha256": sha256(args.evidence_root / "EVIDENCE_MANIFEST.json"),
    }, sort_keys=True))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    item = sub.add_parser("reconstruct")
    item.add_argument("--freeze", type=Path, required=True)
    item.add_argument("--r3d-contract", type=Path, required=True)
    item.add_argument("--chain-map", type=Path, required=True)
    item.add_argument("--action-contract", type=Path, required=True)
    item.add_argument("--old-d0b", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("verify")
    item.add_argument("--freeze", type=Path, required=True)
    item.add_argument("--output", type=Path, required=True)
    item = sub.add_parser("finalize")
    item.add_argument("--freeze", type=Path, required=True)
    item.add_argument("--evidence-root", type=Path, required=True)
    item.add_argument("--r3d0", type=Path, required=True)
    item.add_argument("--r3d-synthetic", type=Path, required=True)
    item.add_argument("--r3d-formal", type=Path, required=True)
    item.add_argument("--d0a", type=Path, required=True)
    item.add_argument("--old-d0b", type=Path, required=True)
    item.add_argument("--terminal", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "reconstruct":
        return reconstruct(args)
    if args.command == "verify":
        return reload_verify(args)
    return finalize_evidence(args)


if __name__ == "__main__":
    raise SystemExit(main())
