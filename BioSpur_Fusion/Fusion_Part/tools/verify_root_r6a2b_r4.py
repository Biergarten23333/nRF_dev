#!/usr/bin/env python3
"""Independent source-evidence recomputation for R6A2B-R4."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.functional_axis import (
    axis_line_angle, estimate_undirected_axis, stationary_noise_distribution,
    synthetic_causal_suite,
)
from biospur_fusion.root_r6a2b.layered_calibration import CanonicalCalibrationAdapter
from biospur_fusion.root_r6a2b.real_profile import profile_checksum
from biospur_fusion.root_r6a2b.session_relative_calibration import relative_joint_rotations
from tools.run_root_r6a2b_r3_session_relative_calibration import FUSION, R1, dump, seal, sha256
from tools.run_root_r6a2b_r4_functional_axis import (
    JOINTS, R3, historical_reproduction, integrate_calibration,
    native_joint_evidence, validate_r3,
)


def verify(result_dir: Path) -> dict[str, object]:
    r3_profile = validate_r3(); slots = {row["slot_id"]: row for row in r3_profile["slots"]}
    model = corrected_body_model(FUSION)
    source_rows = json.loads((R1 / "CALIBRATION_PARAMETER_PROVENANCE.json").read_text())["slots"]
    adapter = CanonicalCalibrationAdapter(model, source_rows)
    r3_reference = json.loads((R3 / "SESSION_RELATIVE_JOINT_REFERENCE.json").read_text())
    historical, replay, relative_replay = historical_reproduction(model, r3_reference)
    stored_historical = json.loads((result_dir / "HISTORICAL_FUNCTIONAL_AXIS_REPRODUCTION.json").read_text())
    stored_native = json.loads((result_dir / "NATIVE_TIME_FUNCTIONAL_AXIS_ESTIMATES.json").read_text())["per_joint"]
    stored_bouts = json.loads((result_dir / "FUNCTIONAL_AXIS_BOUT_AUDIT.json").read_text())["per_joint"]
    stored_synthetic = json.loads((result_dir / "SYNTHETIC_FUNCTIONAL_AXIS_CAUSAL_TESTS.json").read_text())
    estimate = np.load(result_dir / "CALIBRATION_ESTIMATE.npz", allow_pickle=False)

    fits = {}; per_joint_checks = {}; array_hashes = {}
    for joint, binding in JOINTS.items():
        neutral, _ = native_joint_evidence(joint, "initial_still2", r3_profile, slots)
        stationary = stationary_noise_distribution(neutral)
        action, _ = native_joint_evidence(joint, binding["action"], r3_profile, slots)
        fit = estimate_undirected_axis(action, stationary["scale_rms_rad_s"])
        fits[joint] = fit
        stored = stored_native[joint]["axis_estimate"]
        arrays = np.concatenate((action.start_time_ns.view(np.uint8).ravel(),
                                 action.phi_parent_session.view(np.uint8).ravel()))
        array_hashes[joint] = __import__("hashlib").sha256(arrays.tobytes()).hexdigest()
        per_joint_checks[joint] = {
            "historical_dispersion": np.isclose(
                historical["per_joint"][joint]["computed_rms_dispersion_rad"],
                stored_historical["per_joint"][joint]["computed_rms_dispersion_rad"], atol=1e-15,
            ),
            "axis_line": axis_line_angle(
                np.asarray(fit["axis_parent_segment_session_reference"]),
                np.asarray(stored["axis_parent_segment_session_reference"]),
            ) < 1e-12,
            "weighted_rms": np.isclose(
                fit["weighted_axial_rms_dispersion_rad"], stored["weighted_axial_rms_dispersion_rad"],
                rtol=1e-12, atol=1e-14,
            ),
            "weighted_median": np.isclose(
                fit["weighted_median_axial_dispersion_rad"], stored["weighted_median_axial_dispersion_rad"],
                rtol=1e-12, atol=1e-14,
            ),
            "weighted_q95": np.isclose(
                fit["weighted_q95_axial_dispersion_rad"], stored["weighted_q95_axial_dispersion_rad"],
                rtol=1e-12, atol=1e-14,
            ),
            "effective_weight": np.isclose(fit["effective_sample_weight"], stored["effective_sample_weight"]),
            "bout_count": fit["bout_count"] == stored_bouts[joint]["bout_count"],
            "bout_bootstrap_uncertainty": fit["principal_axis_uncertainty_bout_bootstrap"]
            == stored["principal_axis_uncertainty_bout_bootstrap"],
            "increment_count": fit["increment_count"] == stored["increment_count"],
        }

    vector, covariance, ablation_vector, ablation_covariance, _, _, integration = integrate_calibration(
        model, adapter, r3_profile, relative_replay, replay, fits,
    )
    stored_integration = json.loads((result_dir / "CALIBRATION_INTEGRATION.json").read_text())
    profile = json.loads((result_dir / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    access = json.loads((result_dir / "RAW_EVIDENCE_ACCESS_AUDIT.json").read_text())
    synthetic = synthetic_causal_suite()
    checks = {
        "historical_all_four_reproduced": historical["all_four_within_tolerance"]
        and stored_historical["all_four_within_tolerance"],
        "native_axes_dispersion_weights_and_bouts_recomputed": all(
            all(bool(value) for value in row.values()) for row in per_joint_checks.values()
        ),
        "synthetic_causal_suite_recomputed": all(
            np.isclose(synthetic[key], stored_synthetic[key], rtol=1e-12, atol=1e-14)
            for key in (
                "perfect_hinge_stationary_parent_axis_error_rad",
                "perfect_hinge_moving_parent_axis_error_rad",
                "moving_parent_invariance_axis_line_error_rad",
                "flexion_extension_undirected_sign_error_rad",
                "quaternion_sign_flip_phi_difference_linf",
                "near_pi_log_norm_rad", "irregular_dt_axis_error_rad",
            )
        ),
        "no_cross_gap_recomputed": synthetic["cross_gap_increment_created"] is False,
        "calibration_vector_recomputed": bool(np.allclose(vector, estimate["vector"], rtol=1e-8, atol=1e-10)),
        "calibration_covariance_recomputed": bool(np.allclose(
            covariance, estimate["covariance"], rtol=2e-7, atol=1e-9
        )),
        "ablation_vector_recomputed": bool(np.allclose(
            ablation_vector, estimate["ablation_vector"], rtol=1e-8, atol=1e-10
        )),
        "ablation_covariance_recomputed": bool(np.allclose(
            ablation_covariance, estimate["ablation_covariance"], rtol=2e-7, atol=1e-9
        )),
        "integration_objective_recomputed": np.isclose(
            integration["final_objective_half_squared_norm"],
            stored_integration["final_objective_half_squared_norm"], rtol=1e-10,
        ),
        "profile_checksum_recomputed": profile_checksum(profile) == profile["profile_checksum_sha256"],
        "profile_static_vector_matches": bool(np.allclose(profile["static_vector"], vector)),
        "profile_predecessor_immutable": profile["predecessor"]["profile_checksum"]
        == r3_profile["profile_checksum_sha256"],
        "heldout_firewall": access["held_out_members_or_intervals_opened"] == []
        and access["Golf_or_Boxing_opened"] is False,
        "only_authorized_scope_opened": set(access["opened_actions"])
        == {"initial_still2", "left_elbow", "right_elbow2", "left_knee", "right_knee"},
    }
    report = {
        "schema": "biospur-root-r6a2b-r4-independent-verification-v1",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks, "per_joint_recomputation": per_joint_checks,
        "source_evidence_recomputed_hashes": array_hashes,
        "maximum_abs_vector_difference": float(np.max(np.abs(vector - estimate["vector"]))),
        "maximum_abs_covariance_difference": float(np.max(np.abs(covariance - estimate["covariance"]))),
        "verification_opened_held_out_payload": False,
    }
    dump(result_dir / "INDEPENDENT_VERIFICATION.json", report)
    final_path = result_dir / "FINAL_RESULT.json"; final = json.loads(final_path.read_text())
    final["independent_verification"] = report["verdict"]; dump(final_path, final)
    seal(result_dir); return report


def main() -> None:
    parser = argparse.ArgumentParser(); parser.add_argument("result_dir", type=Path)
    args = parser.parse_args(); report = verify(args.result_dir.resolve())
    print(json.dumps({"verdict": report["verdict"],
                      "checks": {key: bool(value) for key, value in report["checks"].items()}},
                     sort_keys=True))


if __name__ == "__main__": main()
