#!/usr/bin/env python3
"""Read-only static validator for the reviewed BioSpur C2 main contract.

This validator deliberately does not enumerate, open, hash, or inspect any C2
payload or holdout path. Runtime sealing, metadata-only preselection, byte-range
planning, and disk gates remain mandatory activation-time checks.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any


PACKAGE = Path(__file__).resolve().parent
WORKSPACE = PACKAGE.parent.parent.resolve()

REQUIRED_FILES = (
    "README.md",
    "REVIEW_CHECKLIST_ZH.md",
    "USER_ANTHROPOMETRY_AMENDMENT_001.json",
    "GEOMETRY_AND_PARAMETER_CONTRACT.json",
    "ACTIVE_PARAMETER_REGISTRY.template.json",
    "MASTER_CONTRACT.md",
    "COMPLIANCE_MATRIX.json",
    "STARTUP_PARAMETERS.json",
    "RUN_START_CONTRACT.template.json",
    "WORK_PROMPT_EN.md",
    "MONITOR_PROMPT_ZH.md",
    "validate_contract.py",
)

EXPECTED_MAPPING = (
    ("BSFEC35", "forearm_left", "anatomical_left"),
    ("BSFB165", "forearm_right", "anatomical_right"),
    ("BSFAA61", "upper_arm_left", "left_rear_posterior_dominant"),
    ("BSF1120", "upper_arm_right", "right_rear_posterior_dominant"),
    ("BSF31CC", "torso", "forward"),
    ("BSFC2CC", "pelvis", "forward"),
    ("BSF44AD", "thigh_left", "forward"),
    ("BSF3C79", "thigh_right", "forward"),
    ("BSF6C53", "shank_left", "anatomical_left_lateral_shank"),
    ("BSF8BC4", "shank_right", "anatomical_right_lateral_shank"),
)

EXPECTED_EDGES = (
    ("pelvis", "torso"),
    ("torso", "upper_arm_left"),
    ("upper_arm_left", "forearm_left"),
    ("torso", "upper_arm_right"),
    ("upper_arm_right", "forearm_right"),
    ("pelvis", "thigh_left"),
    ("thigh_left", "shank_left"),
    ("pelvis", "thigh_right"),
    ("thigh_right", "shank_right"),
)

EXPECTED_ACTIONS = (
    "00_initial_still",
    "02_t_pose",
    "03_pelvis_hula_circle",
    "04_shoulder_left",
    "05_shoulder_right",
    "06_elbow_left",
    "07_elbow_right",
    "08_hip_left",
    "09_hip_right",
    "10_knee_left_seated",
    "11_knee_right_seated",
    "12_heel_raise_left",
    "13_heel_raise_right",
    "14_trunk_flex_extend",
    "15_trunk_axial_rotation",
    "16_squat",
    "17_final_still",
    "18_heel_to_butt_left",
    "19_heel_to_butt_right",
)

EXPECTED_PHASES = (
    "P0_seal_input_firewall",
    "P1_continuous_frontend_stochastic_synthetic_harness",
    "P2_functional_so3_3d_geometry",
    "P3_qmt_heading_nine_edge_tree_branches",
    "P4_controlled_abc",
    "P5_real_c2_progressive",
    "P6_batch_holdout_final_qa",
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_json(name: str) -> dict[str, Any]:
    with (PACKAGE / name).open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    require(isinstance(value, dict), f"{name} root must be an object")
    return value


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def resolve_authority(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else WORKSPACE / path


def walk_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from walk_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk_strings(nested)


def validate() -> dict[str, Any]:
    for name in REQUIRED_FILES:
        require((PACKAGE / name).is_file(), f"missing reviewed contract file: {name}")

    startup = load_json("STARTUP_PARAMETERS.json")
    matrix = load_json("COMPLIANCE_MATRIX.json")
    template = load_json("RUN_START_CONTRACT.template.json")
    geometry = load_json("GEOMETRY_AND_PARAMETER_CONTRACT.json")
    anthropometry_amendment = load_json("USER_ANTHROPOMETRY_AMENDMENT_001.json")
    parameter_template = load_json("ACTIVE_PARAMETER_REGISTRY.template.json")

    require(
        startup["document_status"] == "USER_REVIEW_DRAFT_NOT_ACTIVATED",
        "startup parameters are not a non-active review draft",
    )
    require(
        startup["authorization"]["formal_run_authorized"] is False,
        "review package must not authorize execution",
    )
    require(
        startup["workspace"]["canonical_path"] == str(WORKSPACE),
        "canonical workspace mismatch",
    )

    mapping = tuple(
        (entry["hardware_id"], entry["segment"], entry["minus_z"])
        for entry in startup["node_mapping"]
    )
    require(mapping == EXPECTED_MAPPING, "ten-node mapping or wear direction mismatch")
    require(
        all(entry["minus_y"] == "approximately_ground" for entry in startup["node_mapping"]),
        "all ten -Y priors must be approximately toward ground",
    )
    wear = startup["wear_prior"]
    require(wear["soft_directional_distribution"] is True, "wear prior is not soft")
    require(wear["compact_numeric_cone_as_hard_gate_allowed"] is False, "numeric wear cone became hard gate")
    require("cone_sensitivity_degrees" not in wear, "unproven numeric wear cone remains")
    require(len({entry["hardware_id"] for entry in startup["node_mapping"]}) == 10, "duplicate node")
    require(len({entry["segment"] for entry in startup["node_mapping"]}) == 10, "duplicate segment")

    graph = startup["global_graph"]
    edges = tuple(tuple(edge) for edge in graph["directed_edges"])
    require(edges == EXPECTED_EDGES, "nine-edge topology mismatch")
    require(graph["relative_heading_edges"] == 9, "relative heading edge count mismatch")
    require(graph["pelvis_yaw_gauges"] == 1, "pelvis yaw gauge count mismatch")

    actions = tuple(startup["chronological_actions"])
    attempts = tuple(item["action"] for item in startup["expected_action_attempts_to_verify_metadata_only"])
    require(actions == EXPECTED_ACTIONS, "chronological C2 action list mismatch")
    require(attempts == EXPECTED_ACTIONS, "metadata-only expected attempts mismatch")

    phases = tuple(item["phase"] for item in startup["phase_budgets_hours"])
    require(phases == EXPECTED_PHASES, "phase dependency order mismatch")
    phase_total = sum(float(item["maximum"]) for item in startup["phase_budgets_hours"])
    require(abs(phase_total - 18.0) < 1e-12, "phase budgets must sum to 18 hours")
    require(startup["total_planned_maximum_hours"] == 18.0, "total maximum must be 18 hours")

    require(startup["dataset"]["capture"] == "C2", "dataset scope must be C2")
    require(startup["dataset"]["external_holdout_default"] == "FORBIDDEN", "holdout default mismatch")
    require(startup["heading_integration"]["episode_resets_allowed"] is False, "episode reset allowed")
    require(startup["heading_integration"]["average_delta_to_constant_seed"] is False, "mean heading seed allowed")
    require(startup["heading_integration"]["functional_joint_and_frame_inputs_required_first"] is True, "heading allowed before functional inputs")
    require(startup["heading_integration"]["bespoke_global_nonlinear_heading_solver"] is False, "bespoke heading solver enabled")
    require(startup["global_graph"]["graph_structure"] == "ROOTED_TREE", "nine-edge graph not declared as rooted tree")
    mature = startup["mature_method_first"]
    require(mature["official_parent_child_delta_tree_propagation_is_primary"] is True, "official tree propagation not primary")
    require(mature["bespoke_global_nonlinear_heading_solver_allowed_by_default"] is False, "bespoke global solver allowed by default")
    upstream_firewall = startup["upstream_example_parameter_firewall"]
    require(upstream_firewall["example_subject_parameters_are_c2_truth"] is False, "upstream example subject parameters treated as C2 truth")
    require(upstream_firewall["initial_still_can_gain_known_heading_from_upstream_start_rating"] is False, "upstream startup rating creates known heading")
    require(upstream_firewall["minor_rom_boundary_excursion_is_hard_reject"] is False, "minor human ROM excursion made a hard gate")
    nonideal = startup["human_and_imu_nonideality"]
    require(nonideal["ideal_chip_assumed"] is False and nonideal["ideal_rigid_human_assumed"] is False, "perfect human/IMU assumption enabled")
    require(nonideal["per_action_mount_profiles_allowed"] is False, "per-action mount profiles allowed")
    progressive = startup["progressive_calibration"]
    require(progressive["single_persistent_chronological_state"] is True, "progressive state not persistent")
    require(progressive["final_sealed_heldout_opened_before_fit_freeze"] is False, "sealed heldout leaks before fit freeze")
    require(progressive["final_sealed_heldout_may_trigger_refit"] is False, "heldout may trigger refit")
    require(progressive["count_time_iteration_progress_allowed"] is False, "fake count/time progress allowed")
    require(startup["ordinary_failures_continue_with_causal_pivot"] is True, "ordinary failures must continue")
    require(startup["implementation_failure_is_scientific_fail"] is False, "implementation failure misclassified")
    input_contract = startup["verified_input_contract"]
    require(input_contract["return_rate_hz"] == 200, "JY61P return rate mismatch")
    require(input_contract["b306_hardware_timer_trigger_rate_hz"] == 200, "B306 trigger rate mismatch")
    require(input_contract["bandwidth_hz"] == 98 and input_contract["bandwidth_is_not_sample_rate"] is True, "98 Hz bandwidth misclassified as sample rate")

    geometry_startup = startup["geometry_and_parameter_contract"]
    require(geometry_startup["surface_observations_are_exact_internal_bone_lengths"] is False, "surface lengths relabeled as bone truth")
    require(geometry_startup["measurement_uncertainty_is_currently_bound"] is False, "unknown uncertainty hidden")
    require(geometry_startup["nonzero_measurement_and_landmark_mapping_uncertainty_required_before_real_fit"] is True, "nonzero uncertainty not required")
    require(geometry_startup["absolute_link_length_is_free_real_imu_fit_coordinate"] is False, "absolute link length made free")
    require(geometry_startup["old_hip_vertical_offset_006m_allowed"] is False, "old 0.06 m hip offset allowed")
    require(geometry_startup["old_internal_hip_spacing_022m_allowed"] is False, "old 0.22 m hip spacing allowed")
    require(geometry_startup["old_axial_sensor_offset_model_allowed"] is False, "old axial sensor model allowed")
    require(geometry_startup["unregistered_parameter_allowed"] is False, "unregistered parameter allowed")

    observed = {item["measurement_id"]: item for item in geometry["observed_external_geometry"]}
    expected_observations = {
        "left_forearm_surface_length": [245],
        "right_forearm_surface_length": [245],
        "left_upper_arm_surface_length": [310, 325],
        "right_upper_arm_surface_length": [310, 325],
        "left_thigh_surface_length": [480],
        "right_thigh_surface_length": [480],
        "left_shank_surface_length": [430],
        "right_shank_surface_length": [430],
        "biacromial_breadth": [400, 425],
        "chest_sensor_to_vertex_distance": [470],
        "chest_sensor_to_acromion_line_vertical_distance": [140, 150],
        "pelvis_sensor_to_chest_sensor_center_distance": [280],
        "bicristal_breadth": [335, 315],
        "bitrochanteric_breadth": [335],
        "pelvis_anterior_posterior_depth": [200],
    }
    require(set(expected_observations).issubset(observed), "exact anthropometry entry missing")
    for measurement_id, values in expected_observations.items():
        require(observed[measurement_id].get("observations_mm") == values, f"anthropometry mismatch: {measurement_id}")
    forearm_second = observed["forearm_surface_length_unassigned_second_observer"]
    require(forearm_second["observation_range_mm"] == [260, 265], "second-observer forearm range altered")
    require(forearm_second["side_assignment_in_original_authority"] == "UNKNOWN", "original missing side provenance hidden")
    require(forearm_second["effective_side_assignment_after_user_amendment"] == "BILATERAL_SAME_REPORTED_RANGE", "user bilateral clarification not applied")
    require(forearm_second["effective_left_observation_range_mm"] == [260, 265], "left amended forearm range mismatch")
    require(forearm_second["effective_right_observation_range_mm"] == [260, 265], "right amended forearm range mismatch")
    clarification = anthropometry_amendment["clarification"]
    require(clarification["left_forearm_second_observer_surface_range_mm"] == [260, 265], "left user clarification mismatch")
    require(clarification["right_forearm_second_observer_surface_range_mm"] == [260, 265], "right user clarification mismatch")
    require(clarification["bilateral_reported_range_is_the_same"] is True, "bilateral equality clarification missing")
    require(anthropometry_amendment["raw_authority_file_must_remain_unchanged"] is True, "raw anthropometry overwrite allowed")
    require(anthropometry_amendment["scientific_limits"]["forces_internal_left_right_bone_equality"] is False, "surface clarification forced latent bone equality")
    require(geometry["authoritative_anthropometry"]["measurement_uncertainty_status"] == "UNBOUND_NULL_IN_AUTHORITY", "uncertainty missingness changed")
    require(geometry["authoritative_anthropometry"]["zero_uncertainty_allowed"] is False, "zero measurement uncertainty allowed")
    require(geometry["limb_scale_contract"]["length_is_unconstrained_real_fit_coordinate"] is False, "bone scale unconstrained in real fit")
    require(geometry["runtime_active_parameter_registry"]["unregistered_real_fit_or_viewer_parameter_allowed"] is False, "parameter registry firewall disabled")
    require(parameter_template["template_only_not_active_registry"] is True, "parameter template masquerades as active registry")
    group_ids = [group["group_id"] for group in parameter_template["groups"]]
    require(len(group_ids) == 16 and len(set(group_ids)) == 16, "active parameter registry template must contain 16 unique groups")
    require(all(group["parameters"] for group in parameter_template["groups"]), "empty parameter registry group")
    require(len(parameter_template["required_fields_per_parameter"]) == 14, "active parameter required-field schema mismatch")
    registry_markers = [value for value in walk_strings(parameter_template) if value.startswith("__REQUIRED")]
    require(registry_markers, "active parameter registry template must remain visibly unresolved")

    requirements = matrix["requirements"]
    ids = [item["id"] for item in requirements]
    require(len(requirements) == 58, "compliance matrix must contain 58 requirements")
    require(len(ids) == len(set(ids)), "duplicate compliance requirement id")
    require({item["consequence"] for item in requirements} == {"A", "B", "C", "D"}, "consequence classes mismatch")
    require(
        all(item.get("owner") and item.get("requirement") and item.get("required_evidence") for item in requirements),
        "incomplete compliance requirement",
    )

    require(template["template_only_not_a_seal"] is True, "run-start template masquerades as a seal")
    require(template["first_payload_access"]["occurred"] is False, "template claims payload access")
    required_markers = [value for value in walk_strings(template) if value.startswith("__REQUIRED")]
    require(required_markers, "run-start template must remain visibly unresolved")
    sealed_paths = {item["path"] for item in template["contract_files"]}
    expected_paths = {f"config/{PACKAGE.name}/{name}" for name in REQUIRED_FILES}
    require(sealed_paths == expected_paths, "run-start seal file list mismatch")

    for prompt_name in ("WORK_PROMPT_EN.md", "MONITOR_PROMPT_ZH.md"):
        prompt = (PACKAGE / prompt_name).read_text(encoding="utf-8")
        for name in REQUIRED_FILES:
            require(name in prompt, f"{prompt_name} does not bind {name}")

    authority_existence: dict[str, str] = {}
    for key, value in startup["authorities"].items():
        resolved = resolve_authority(value).resolve(strict=False)
        require(resolved.exists(), f"missing authority path: {key} -> {resolved}")
        authority_existence[key] = str(resolved)

    hashes = {name: sha256(PACKAGE / name) for name in REQUIRED_FILES}
    return {
        "status": "PASS",
        "validation_scope": "STATIC_CONTRACT_ONLY_NO_C2_PAYLOAD_OR_HOLDOUT_ACCESS",
        "execution_authorized": False,
        "counts": {
            "nodes": len(mapping),
            "relative_heading_edges": len(edges),
            "actions": len(actions),
            "compliance_requirements": len(requirements),
            "exact_external_geometry_entries": len(observed),
            "active_parameter_groups": len(group_ids),
            "phase_budget_hours": phase_total,
            "authority_paths_existing": len(authority_existence),
        },
        "reviewed_file_sha256": hashes,
        "authority_paths": authority_existence,
    }


def main() -> int:
    try:
        result = validate()
    except Exception as exc:  # one fail-closed boundary for command-line use
        print(json.dumps({"status": "FAIL", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
