from __future__ import annotations

import json

import numpy as np
import pytest

from biospur_fusion.root_r6a2a.contracts import (
    ALL_NODES,
    COMMON_NINE,
    CORRECT_NODE_MAP,
    FAMILY_BSF31CC,
    FAMILY_COMMON_NINE,
    HealthState,
    registry_from_sealed_addendum,
)
from biospur_fusion.root_r6a2a.qualification import SCENARIOS
from biospur_fusion.root_r6a2a.shadow import build_synthetic_calibration, corrected_body_model, truth_state


@pytest.mark.parametrize("gate", list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["AA", "AB"])
def test_mandatory_gate(gate, qualification):
    assert qualification["gates"]["results"][gate]["pass"], qualification["gates"]["results"][gate]


def test_exact_gate_inventory(qualification):
    expected = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ") + ["AA", "AB"]
    assert qualification["gates"]["order"] == expected
    assert qualification["gates"]["total"] == 28
    assert qualification["gates"]["passed"] == 28
    assert qualification["gates"]["all_pass"]


def test_one_estimator_two_exact_families(fusion, qualification):
    registry = registry_from_sealed_addendum(fusion)
    assert set(registry.family_by_node) == set(ALL_NODES)
    assert registry.family("BSF31CC") == FAMILY_BSF31CC
    assert all(registry.family(node) == FAMILY_COMMON_NINE for node in COMMON_NINE)
    assert registry.profile("BSF31CC").imu_to_uwb_nominal_m != registry.profile(COMMON_NINE[0]).imu_to_uwb_nominal_m
    assert not qualification["contracts"]["architecture"]["separate_family_estimators"]


def test_corrected_identity_is_instantiated_before_body_model(fusion):
    model = corrected_body_model(fusion)
    assert model.identity_mapping == CORRECT_NODE_MAP
    assert model.identity_mapping["BSFEC35"] == "forearm_left"
    assert model.identity_mapping["BSFB165"] == "forearm_right"
    assert model.identity_provenance["source"] == "ROOT_R6A1C_CORRECTED_IDENTITY_ADAPTER"


def test_shared_fk_closes_every_scenario(qualification):
    for result in qualification["scenario_results"].values():
        assert result["state_execution_count"] > 0
        assert result["metrics"]["joint_closure_max_m"] < 1e-10
        assert result["metrics"]["bone_length_max_change_m"] == 0.0


def test_tag_levers_are_derived_from_family_profile(fusion):
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    calibration = build_synthetic_calibration(model, registry)
    for node in model.tag_ids:
        extrinsic = calibration.pose(f"imu_extrinsic:{node}")
        expected = extrinsic.translation + extrinsic.rotation @ np.asarray(registry.profile(node).imu_to_uwb_nominal_m)
        assert np.allclose(calibration.vector(f"tag_lever:{node}", 3), expected)
        assert registry.family(node) in calibration.slot(f"tag_lever:{node}").provenance


def test_torso_top_is_shoulder_midpoint(fusion):
    registry = registry_from_sealed_addendum(fusion)
    model = corrected_body_model(fusion)
    calibration = build_synthetic_calibration(model, registry)
    midpoint = 0.5 * (
        calibration.vector("joint_parent:shoulder_left", 3)
        + calibration.vector("joint_parent:shoulder_right", 3)
    )
    assert np.array_equal(calibration.vector("anatomical_point:torso_top", 3), midpoint)


def test_native_preintegration_fault_statuses_are_executed(qualification):
    scenarios = qualification["scenario_results"]
    expected = {
        "imu_long_gap": "GAP_EXCEEDS_ENVELOPE",
        "imu_duplicate_timestamp": "DUPLICATE_TIMESTAMP",
        "imu_timestamp_reversal": "TIME_REVERSAL",
        "imu_boot_epoch_reset": "BOOT_EPOCH_CHANGE",
        "imu_saturation": "SATURATION",
        "imu_invalid_value": "INVALID_SAMPLE_STATUS",
        "imu_single_node_dropout": "INSUFFICIENT_SAMPLES",
    }
    for name, status in expected.items():
        assert scenarios[name]["preintegration_status_counts"].get(status, 0) > 0
    assert scenarios["imu_bounded_sample_gap"]["metrics"]["bounded_gap_count"] > 0


def test_preintegrator_covariance_and_bias_jacobians_reach_estimator(qualification):
    for name in ("clean_baseline_seed_6201", "clean_baseline_seed_6223"):
        metrics = qualification["scenario_results"][name]["metrics"]
        assert metrics["preintegration_covariance_consumed_count"] >= 80
        assert metrics["preintegration_bias_jacobian_consumed_count"] >= 80
        assert metrics["preintegration_bias_jacobian_norm_min"] > 0.0
        assert metrics["native_variable_dt_exercised"]
        assert metrics["native_start_time_unique_count"] == 10


def test_uwb_uses_measurement_time_interpolation(qualification):
    for result in qualification["scenario_results"].values():
        metrics = result["metrics"]
        if result["scenario"]["fault"] != "global_uwb_outage":
            assert metrics["uwb_measurement_time_interpolation_queries"] > 0
        assert not metrics["nearest_sample_shortcut_used"]
        assert not metrics["trust_depends_on_acceleration"]


def test_health_hierarchy_and_transition_evidence(qualification):
    contract = qualification["contracts"]["health"]
    assert contract["states"] == [state.value for state in HealthState]
    assert len(contract["scopes"]) == 7
    transitions = qualification["scenario_results"]["uwb_full_outage_recovery"]["health_transitions"]
    assert transitions
    assert all("entry_or_exit_evidence" in row and "covariance_consequence" in row and "measurement_weight" in row for row in transitions)


def test_covariance_is_finite_symmetric_psd_and_grows_in_outage(qualification):
    covariance = qualification["covariance"]
    assert covariance["all_finite"]
    assert covariance["maximum_symmetry_error"] <= 1e-10
    assert covariance["minimum_eigenvalue"] >= -1e-10
    assert covariance["outage_root_covariance_growth"] > 0.0
    assert covariance["outage_yaw_covariance_growth"] > 0.0


def test_recovery_is_hysteretic_and_controlled(qualification):
    result = qualification["scenario_results"]["uwb_full_outage_recovery"]
    modes = result["metrics"]["mode_sequence"]
    assert "GLOBAL_UWB_OUTAGE" in modes
    assert "RECOVERY_PENDING" in modes
    assert "CONTROLLED_REENTRY" in modes
    transitions = [row["to"] for row in result["health_transitions"] if row["scope"] == "global_observability"]
    assert transitions == ["SUSPECT", "DEGRADED", "ISOLATED", "RECOVERING", "REQUALIFYING", "HEALTHY"]
    assert result["metrics"]["maximum_uwb_correction_m"] <= 0.0800001


def test_ambiguous_model_and_slip_not_overdiagnosed(qualification):
    for name in ("model_wrong_synthetic_lever", "model_rotational_skin_slip", "model_wrong_bone_geometry"):
        assert qualification["scenario_results"][name]["metrics"]["fault_attribution"] == "AMBIGUOUS_MULTI_CAUSE"
    slip = qualification["scenario_results"]["model_rotational_skin_slip"]["metrics"]
    assert slip["skin_slip_nuisance_dimension"] == 30
    assert not slip["skin_slip_per_sample_unconstrained"]
    assert slip["skin_slip_max_rotvec_norm_rad"] <= 0.25
    assert slip["skin_slip_covariance_max_trace"] > 3e-5


def test_fail_closed_validators_execute_no_state(qualification):
    validators = qualification["validator_results"]
    assert not validators["obsolete_wrist_map_attempt"]["authorized"]
    assert "OBSOLETE_WRIST_MAP" in validators["obsolete_wrist_map_attempt"]["blockers"]
    assert validators["wrong_hardware_family_attempt"]["failed_closed"]
    assert not validators["missing_real_calibration_real_mode_attempt"]["authorized"]
    assert all(row["state_execution_count"] == 0 for row in validators.values())


def test_real_registry_stays_immutable(qualification, fusion):
    isolation = qualification["contracts"]["isolation"]
    assert isolation["real_registry"]["total"] == 87
    assert isolation["real_registry"]["value_null"] == 87
    assert isolation["real_registry"]["FROZEN_UNCERTAIN"] == 87
    assert isolation["real_registry"]["writes_performed"] == 0
    ledger = json.loads((fusion / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json").read_text())
    assert all(row["value"] is None and row["status"] == "FROZEN_UNCERTAIN" for row in ledger["slots"])


def test_multiple_seeds_and_structural_geometries(qualification):
    seeds = {result["scenario"]["seed"] for result in qualification["scenario_results"].values()}
    geometries = {result["scenario"]["geometry"] for result in qualification["scenario_results"].values()}
    assert len(seeds) >= 3
    assert geometries == {"WELL_CONDITIONED_3D", "LOW_VERTICAL_DIVERSITY"}


def test_deterministic_replay_hash_is_bound(qualification):
    digest = qualification["gates"]["results"]["AB"]["metrics"]["replay_sha256"]
    assert len(digest) == 64
    assert digest == qualification["scenario_results"][SCENARIOS[0].name]["deterministic_replay_sha256"]
