#!/usr/bin/env python3
"""Recompute the R6A2B-R2 numerical solve and parameter accounting."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.layered_calibration import (
    NODES, CanonicalCalibrationAdapter, information_diagnostics,
    solve_geometry_layer, solve_rotation_layer,
)
from biospur_fusion.root_r6a2b.real_profile import profile_checksum


FUSION = Path(__file__).resolve().parents[1]
R1 = FUSION / "logs/root_r6a2b_r1_calibration_first_20260826T093237Z"
EXPECTED_WINDOWS = (
    ("initial_still2", 2986078873797, 2994078940466),
    ("t_pose", 3019030103768, 3027030170523),
    ("arms", 3065724244760, 3212615253685),
    ("left_elbow", 3371591610404, 3411475048316),
    ("right_elbow2", 3494725933278, 3528015255640),
    ("left_knee", 3551740910191, 3579592651754),
    ("right_knee", 3602476636179, 3627048980515),
    ("left_heel", 3666677754354, 3687781716166),
    ("right_heel", 3712252142978, 3737709976189),
    ("squats", 3761427161163, 3785916206867),
    ("trunk", 3814053447917, 3854622450716),
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def seal(result_dir: Path) -> None:
    files = sorted(path for path in result_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (result_dir / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def verify(result_dir: Path) -> dict:
    source_rows = json.loads((R1 / "CALIBRATION_PARAMETER_PROVENANCE.json").read_text())["slots"]
    model = corrected_body_model(FUSION)
    adapter = CanonicalCalibrationAdapter(model, source_rows)
    cache = np.load(result_dir / "SOLVE_INPUTS.npz", allow_pickle=False)
    estimate = np.load(result_dir / "CALIBRATION_ESTIMATE.npz", allow_pickle=False)
    gravity = {node: cache["gravity"][index] for index, node in enumerate(NODES)}
    axis = {node: cache["functional_axis"][index] for index, node in enumerate(NODES)}
    signed = {node: cache["signed"][index] for index, node in enumerate(NODES)}
    internal = {node: cache["internal_lever"][index] for index, node in enumerate(NODES)}
    internal_sigma = {node: cache["internal_lever_sigma"][index] for index, node in enumerate(NODES)}

    rotation_vector, rotation_report, rotation_jacobian = solve_rotation_layer(adapter, gravity, axis, signed)
    recomputed_vector, geometry_report, geometry_jacobian, _ = solve_geometry_layer(
        adapter, model, rotation_vector, cache["frame_times_ns"], cache["segment_rotation"],
        cache["observed_xyz"], cache["observed_covariance"], internal, internal_sigma,
    )
    information, covariance = information_diagnostics(adapter, rotation_jacobian, geometry_jacobian)
    profile = json.loads((result_dir / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json").read_text())
    access = json.loads((result_dir / "CALIBRATION_WINDOW_ACCESS_AUDIT.json").read_text())
    observed_windows = tuple(
        (label, int(access["windows"][label]["start_global_time_ns"]),
         int(access["windows"][label]["stop_global_time_ns_exclusive"]))
        for label, _, _ in EXPECTED_WINDOWS
    )
    checks = {
        "adapter_28_slots": len(adapter.blocks) == 28,
        "adapter_114_dimensions": adapter.dimension == 114,
        "parameter_vector_recomputed": bool(np.allclose(recomputed_vector, estimate["vector"], rtol=1e-9, atol=1e-10)),
        "posterior_covariance_recomputed": bool(np.allclose(covariance, estimate["covariance"], rtol=1e-8, atol=1e-10)),
        "rotation_objective_decreased": (
            rotation_report["final_objective_half_squared_norm"]
            < rotation_report["initial_objective_half_squared_norm"]
        ),
        "geometry_objective_decreased": (
            geometry_report["final_objective_half_squared_norm"]
            < geometry_report["initial_objective_half_squared_norm"]
        ),
        "shared_fk_solve_nonzero": bool(np.linalg.norm(recomputed_vector) > 0.0),
        "information_accounting": information["dimension"] == 114,
        "exact_authorized_windows": observed_windows == EXPECTED_WINDOWS,
        "heldout_access_empty": access["held_out_members_or_intervals_opened"] == [],
        "profile_checksum": profile_checksum(profile) == profile["profile_checksum_sha256"],
        "profile_static_vector_matches": bool(np.allclose(profile["static_vector"], recomputed_vector)),
        "historical_registry_immutable": (
            profile["immutability"]["sha256_before"] == profile["immutability"]["sha256_after"]
            == sha256(Path(profile["immutability"]["historical_registry"]))
        ),
    }
    report = {
        "schema": "biospur-root-r6a2b-r2-independent-verification-v1",
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "recomputation": {
            "rotation_optimizer": rotation_report,
            "geometry_optimizer": geometry_report,
            "data_only_rank": information["data_only_rank"],
            "data_only_nullity": information["data_only_nullity"],
            "maximum_abs_vector_difference": float(np.max(np.abs(recomputed_vector - estimate["vector"]))),
            "maximum_abs_covariance_difference": float(np.max(np.abs(covariance - estimate["covariance"]))),
        },
        "verification_opened_held_out_payload": False,
    }
    dump(result_dir / "INDEPENDENT_VERIFICATION.json", report)
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
