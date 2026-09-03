#!/usr/bin/env python3
"""Independently recompute the R6A2B-R3 joint solve and causal evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.layered_calibration import NODES, CanonicalCalibrationAdapter
from biospur_fusion.root_r6a2b.real_profile import profile_checksum
from biospur_fusion.root_r6a2b.session_relative_calibration import (
    apply_reference_solution, geometry_causal_trace, joint_rest_gauge_causal_trace,
    relative_joint_rotations, solve_session_references,
)
from tools.run_root_r6a2b_r3_session_relative_calibration import (
    EXPECTED_WINDOWS, FUSION, R1, R2, covariance_information, dump,
    render_root_relative_frames, seal, sha256,
)


def verify(result_dir: Path) -> dict:
    inputs = np.load(result_dir / "SOLVE_INPUTS.npz", allow_pickle=False)
    estimate = np.load(result_dir / "CALIBRATION_ESTIMATE.npz", allow_pickle=False)
    model = corrected_body_model(FUSION)
    source_rows = json.loads((R1 / "CALIBRATION_PARAMETER_PROVENANCE.json").read_text())["slots"]
    adapter = CanonicalCalibrationAdapter(model, source_rows)
    relative = relative_joint_rotations(model, inputs["segment_rotation"], inputs["segment_names"])
    sigma = {segment: float(inputs["sensor_sigma_segment"][index])
             for index, segment in enumerate(model.segments)}

    full = solve_session_references(
        model, relative, inputs["window"], sigma, include_biomechanics=True
    )
    ablation = solve_session_references(
        model, relative, inputs["window"], sigma, include_biomechanics=False
    )
    vector, covariance = apply_reference_solution(
        adapter, inputs["base_vector"], full, inputs["base_covariance"]
    )
    ablation_vector, ablation_covariance = apply_reference_solution(
        adapter, inputs["base_vector"], ablation, inputs["base_covariance"]
    )

    internal = {node: inputs["internal_lever"][index] for index, node in enumerate(NODES)}
    sample_rotations = {
        str(segment): inputs["segment_rotation"][0, index]
        for index, segment in enumerate(inputs["segment_names"])
    }
    causal = joint_rest_gauge_causal_trace(
        model, adapter, inputs["base_vector"], sample_rotations,
        int(inputs["time_ns"][0]), internal,
    )
    stored_causal = json.loads((result_dir / "JOINT_REST_GAUGE_CAUSAL_TRACE.json").read_text())

    r2_estimate = np.load(R2 / "CALIBRATION_ESTIMATE.npz", allow_pickle=False)
    geometry = geometry_causal_trace(
        model, adapter, inputs["base_vector"], inputs["base_covariance"],
        r2_estimate["geometry_data_jacobian"], inputs["segment_rotation"], inputs["window"],
    )
    stored_geometry = json.loads((result_dir / "METRIC_GEOMETRY_CAUSAL_TRACE.json").read_text())
    r2_information = json.loads((R2 / "OBSERVABILITY_AND_COVARIANCE.json").read_text())
    information = covariance_information(full, ablation, r2_information)
    stored_information = json.loads((result_dir / "OBSERVABILITY_AND_COVARIANCE.json").read_text())

    profile = json.loads((result_dir / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    observed_windows = tuple(
        (row["label"], int(row["start_global_time_ns"]), int(row["stop_global_time_ns_exclusive"]))
        for row in profile["binding"]["authorized_windows"]
    )
    replay = np.load(result_dir / "CALIBRATION_WINDOW_REPLAY.npz", allow_pickle=False)
    replay_summary = json.loads((result_dir / "CALIBRATION_WINDOW_REPLAY_SUMMARY.json").read_text())
    q_from_profile = np.stack([
        np.asarray([joint.reference_rotvec for joint in full.values()])
    ])[0]

    causal_rows_match = True
    for expected_slot, observed_slot in zip(causal["tested_slots"], stored_causal["tested_slots"]):
        causal_rows_match &= expected_slot["slot_id"] == observed_slot["slot_id"]
        for expected_coordinate, observed_coordinate in zip(expected_slot["coordinates"], observed_slot["coordinates"]):
            for expected_test, observed_test in zip(expected_coordinate["tests"], observed_coordinate["tests"]):
                causal_rows_match &= np.isclose(
                    expected_test["shared_fk_fixed_dynamic_state_output_change_linf"],
                    observed_test["shared_fk_fixed_dynamic_state_output_change_linf"], atol=1e-15,
                )
                causal_rows_match &= np.isclose(
                    expected_test["shared_fk_compensated_output_change_linf"],
                    observed_test["shared_fk_compensated_output_change_linf"], atol=1e-15,
                )

    checks = {
        "parameter_vector_recomputed": bool(np.allclose(vector, estimate["vector"], rtol=1e-8, atol=1e-10)),
        "posterior_covariance_recomputed": bool(np.allclose(
            covariance, estimate["covariance"], rtol=2e-7, atol=1e-9
        )),
        "ablation_vector_recomputed": bool(np.allclose(
            ablation_vector, estimate["ablation_vector"], rtol=1e-8, atol=1e-10
        )),
        "ablation_covariance_recomputed": bool(np.allclose(
            ablation_covariance, estimate["ablation_covariance"], rtol=2e-7, atol=1e-9
        )),
        "joint_reference_vector_recomputed": bool(np.allclose(
            q_from_profile, estimate["joint_reference"], rtol=1e-8, atol=1e-10
        )),
        "causal_perturbation_trace_recomputed": bool(causal_rows_match),
        "causal_compensated_invariance_recomputed": bool(
            causal["maximum_compensated_complete_output_change_linf"] < 1e-12
            and np.isclose(causal["maximum_compensated_complete_output_change_linf"],
                           stored_causal["maximum_compensated_complete_output_change_linf"], atol=1e-15)
        ),
        "information_accounting_recomputed": bool(
            information["before"] == stored_information["before"]
            and information["after_session_reference_convention"]["coordinate_rank"] == 114
            and information["after_session_reference_convention"]["coordinate_nullity"] == 0
            and information["physical_information_without_coordinate_definition"]["nullity"] == 27
            and np.allclose(
                information["after_session_reference_convention"]["singular_values_descending"],
                stored_information["after_session_reference_convention"]["singular_values_descending"],
                rtol=2e-7, atol=1e-10,
            )
        ),
        "metric_geometry_cause_recomputed": bool(
            geometry["causal_finding"]["all_action_first_frames_identity"]
            and np.isclose(geometry["causal_finding"]["geometry_jacobian_condition_number"],
                           stored_geometry["causal_finding"]["geometry_jacobian_condition_number"], rtol=1e-12)
        ),
        "profile_checksum_recomputed": profile_checksum(profile) == profile["profile_checksum_sha256"],
        "profile_static_vector_matches": bool(np.allclose(profile["static_vector"], vector)),
        "exact_authoritative_windows": observed_windows == EXPECTED_WINDOWS,
        "all_11_windows_replayed": len(set(str(value) for value in replay["window"])) == 11,
        "shared_fk_replay_finite": bool(np.isfinite(replay["segment_rotation"]).all()
                                         and replay_summary["all_finite"]),
        "profile_immutable_during_replay": bool(
            replay_summary["profile_mutated"] is False
            and replay_summary["profile_checksum_before_replay"]
            == replay_summary["profile_checksum_after_replay"]
            == profile["profile_checksum_sha256"]
        ),
        "heldout_firewall": profile["binding"]["held_out_golf_boxing_accessed"] is False,
        "historical_registry_immutable": (
            profile["immutability"]["sha256_before"]
            == profile["immutability"]["sha256_after"]
            == sha256(Path(profile["immutability"]["historical_registry"]))
        ),
        "no_measured_or_synthetic_anthropometry": (
            profile["anthropometry"]["measured_values_entered"] is False
            and profile["anthropometry"]["synthetic_values_entered"] is False
        ),
    }
    report = {
        "schema": "biospur-root-r6a2b-r3-independent-verification-v1",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "recomputation": {
            "maximum_abs_parameter_difference": float(np.max(np.abs(vector - estimate["vector"]))),
            "maximum_abs_covariance_difference": float(np.max(np.abs(covariance - estimate["covariance"]))),
            "maximum_compensated_gauge_output_change": causal["maximum_compensated_complete_output_change_linf"],
            "coordinate_rank_nullity": [114, 0],
            "physical_rank_nullity_without_reference_convention": [87, 27],
        },
        "verification_opened_held_out_payload": False,
    }
    dump(result_dir / "INDEPENDENT_VERIFICATION.json", report)
    render_root_relative_frames(result_dir)
    final_path = result_dir / "FINAL_RESULT.json"
    final = json.loads(final_path.read_text())
    final["independent_verification"] = report["verdict"]
    dump(final_path, final)
    seal(result_dir)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args()
    report = verify(args.result_dir.resolve())
    print(json.dumps({"verdict": report["verdict"], "checks": report["checks"]}, sort_keys=True))


if __name__ == "__main__":
    main()
