"""Locked-candidate independent-action validation plumbing for BioSpur V0.

This module does not calibrate or tune.  It verifies a content-addressed
candidate, loads one predeclared IMU-only action, applies the frozen profile,
and exports the full candidate plus two diagnostic ablations.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping

import numpy as np

from biospur_fusion.root_r6a0.body import KeyframeState
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.shadow import corrected_body_model

from .contracts import (
    HELD_OUT_LABELS, IDENTITY, NODES, assert_profile_boundary, dump_json,
    load_config, sha256_file,
)
from .data import load_authorized_action_imu_only
from .frontend import run_vqf_native_hybrid
from .heading import apply_soft_qmt_heading
from .math3d import matrix_to_quat_wxyz, rotation_angle
from .model import (
    _direct_configuration, display_static_calibration,
    initialize_common_action_display_yaw, resample_window, run_shared_ik,
)
from .viewer import write_viewer


LOCK_SCHEMA = "biospur-fusion-v0-candidate-lock-manifest-v1"
PREDECLARATION_SCHEMA = "biospur-fusion-v0-independent-action-predeclaration-v1"


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ).stdout


def _locked_paths(root: Path, profile_path: Path) -> list[Path]:
    paths = list(sorted((root / "src/biospur_fusion/v0").glob("*.py")))
    paths += [
        root / "src/biospur_fusion/imu/frontend.py",
        root / "src/biospur_fusion/imu/q1.py",
        root / "src/biospur_fusion/time/common_clock.py",
        root / "src/biospur_fusion/root_r6a0/body.py",
        root / "src/biospur_fusion/root_r6a0/contracts.py",
        root / "src/biospur_fusion/root_r6a0/math3d.py",
        root / "src/biospur_fusion/root_r6a2a/shadow.py",
        root / "config/biospur_fusion_v0/config.json",
        root / "config/biospur_fusion_v0/release_mode.json",
        root / "config/biospur_fusion_v0/requirements-lock.txt",
        root / "docs/biospur_fusion_v0.md",
        root / "tools/run_biospur_fusion_v0.py",
        root / "tools/run_biospur_fusion_v0.sh",
        root / "tools/run_biospur_fusion_v0_validation.py",
        root / "tools/run_biospur_fusion_v0_conservative.py",
        root / "tools/verify_biospur_fusion_v0.py",
        root / "tests/v0/test_v0_baseline.py",
        root / "tests/v0/test_v0_raw_validation.py",
        root / "tests/v0/test_v0_validation_plumbing.py",
        root / "tests/synthetic/test_common_clock.py",
        profile_path,
    ]
    resolved = sorted({path.resolve() for path in paths})
    missing = [str(path) for path in resolved if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"candidate lock input missing: {missing}")
    return resolved


def create_candidate_lock_manifest(root: Path, profile_path: Path, destination: Path) -> dict[str, Any]:
    """Seal the V0-owned validation inputs without including runtime output."""
    root = Path(root).resolve(); profile_path = Path(profile_path).resolve()
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError(f"candidate lock already exists: {destination}")
    files = []
    for path in _locked_paths(root, profile_path):
        try:
            name = str(path.relative_to(root))
        except ValueError:
            name = str(path)
        files.append({"path": name, "sha256": sha256_file(path), "bytes": path.stat().st_size})
    status = _git(root, "status", "--porcelain=v1", "-z")
    dependencies = {}
    for package in ("numpy", "scipy", "vqf", "qmt"):
        dependencies[package] = importlib.metadata.version(package)
    manifest = {
        "schema": LOCK_SCHEMA,
        "candidate": "BioSpur Fusion V0 independent-action validation candidate",
        "locked": True,
        "scope": "V0_OWNED_SOURCE_CONFIG_DEPENDENCIES_WRAPPERS_IK_FK_RUNNER_VIEWER_PROFILE_TESTS",
        "files": files,
        "profile_path": str(profile_path),
        "profile_sha256": sha256_file(profile_path),
        "dependencies": dependencies,
        "python": sys.version.split()[0],
        "current_HEAD": _git(root, "rev-parse", "HEAD").decode().strip(),
        "current_branch": _git(root, "branch", "--show-current").decode().strip(),
        "worktree_status_digest": hashlib.sha256(status).hexdigest(),
        "worktree_status_entry_count": int(status.count(b"\0")),
        "excludes": [
            "runtime telemetry", "generated validation output", "execution timestamps",
            "unrelated worktree files", "caches",
        ],
        "final_release_freeze": False,
    }
    digest = hashlib.sha256()
    for row in files:
        digest.update(row["path"].encode("utf-8")); digest.update(bytes.fromhex(row["sha256"]))
    manifest["covered_file_set_digest"] = digest.hexdigest()
    destination.parent.mkdir(parents=True, exist_ok=True)
    dump_json(destination, manifest)
    return manifest


def verify_candidate_lock(
    root: Path, manifest_path: Path, expected_manifest_sha256: str,
) -> dict[str, Any]:
    root = Path(root).resolve(); manifest_path = Path(manifest_path).resolve()
    actual_manifest_sha = sha256_file(manifest_path)
    if actual_manifest_sha != expected_manifest_sha256:
        raise ValueError("candidate manifest SHA-256 mismatch")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != LOCK_SCHEMA or manifest.get("locked") is not True:
        raise ValueError("candidate manifest is not a sealed V0 lock")
    mismatches = []
    for row in manifest["files"]:
        raw = Path(row["path"])
        path = raw if raw.is_absolute() else root / raw
        if not path.is_file():
            mismatches.append({"path": row["path"], "reason": "MISSING"})
        else:
            actual = sha256_file(path)
            if actual != row["sha256"]:
                mismatches.append({"path": row["path"], "expected": row["sha256"], "actual": actual})
    if mismatches:
        raise RuntimeError(f"covered candidate inputs changed: {mismatches}")
    return {
        "manifest_sha256": actual_manifest_sha,
        "covered_files": len(manifest["files"]),
        "mismatches": mismatches,
        "pass": True,
    }


def validate_predeclaration(root: Path, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Fail closed before any validation-ledger member is opened."""
    required = {
        "schema", "capture_identifier", "action_identifier", "attempt_number",
        "start_global_time_ns", "stop_global_time_ns_exclusive", "ledger",
        "ledger_sha256", "authoritative_role_source", "authoritative_role_source_sha256",
        "role", "ordinary_reason", "not_held_out_reason", "expected_gross_motion_semantics",
        "candidate_manifest", "candidate_manifest_sha256", "profile", "profile_sha256",
        "selected_from_metadata_before_payload_access", "complete_ten_node_coverage_declared",
    }
    missing = required - set(payload)
    if missing:
        raise ValueError(f"predeclaration missing fields: {sorted(missing)}")
    if payload["schema"] != PREDECLARATION_SCHEMA:
        raise ValueError("unsupported action predeclaration schema")
    label = str(payload["action_identifier"]).strip().lower().replace("-", "_").replace(" ", "_")
    if any(forbidden in label for forbidden in HELD_OUT_LABELS):
        raise ValueError("held-out Golf/Boxing action is forbidden")
    role = str(payload["role"]).upper()
    if "CALIBRATION" in role or not any(token in role for token in ("ORDINARY", "DEVELOPMENT_VALIDATION")):
        raise ValueError("action role is not unambiguously ordinary/development-validation")
    if payload["selected_from_metadata_before_payload_access"] is not True:
        raise ValueError("action was not selected before payload access")
    if payload["complete_ten_node_coverage_declared"] is not True:
        raise ValueError("complete ten-node coverage was not declared from metadata")
    start = int(payload["start_global_time_ns"]); stop = int(payload["stop_global_time_ns_exclusive"])
    if stop <= start:
        raise ValueError("invalid predeclared action interval")
    role_source = Path(str(payload["authoritative_role_source"]))
    if not role_source.is_absolute():
        role_source = root / role_source
    if sha256_file(role_source) != payload["authoritative_role_source_sha256"]:
        raise ValueError("authoritative role source changed after selection")
    if not str(payload["ordinary_reason"]).strip() or not str(payload["not_held_out_reason"]).strip():
        raise ValueError("role reasons must be explicit")
    semantics = payload["expected_gross_motion_semantics"]
    if not isinstance(semantics, (list, tuple)) or not semantics:
        raise ValueError("gross action semantics must be predeclared")
    if "raw_input" in payload:
        access_declarations = {
            "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
            "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
            "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
            "GOLF_BOXING_USED_FOR_TUNING": "NO",
            "GOLF_BOXING_USED_FOR_SCORING": "NO",
            "SELECTED_ACTION_PREVIOUSLY_USED_FOR_TUNING": "NO",
        }
        for name, expected in access_declarations.items():
            if payload.get(name) != expected:
                raise ValueError(f"raw predeclaration access declaration {name} must be {expected}")
        execution_mode = str(payload.get("execution_mode", "FIRST_INDEPENDENT_EXECUTION"))
        prior_expected = (
            "YES" if execution_mode == "RELOCKED_ACCESS_EVIDENCE_RERUN" else "NO"
        )
        for name in (
            "SELECTED_ACTION_PREVIOUSLY_DECODED",
            "SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED",
        ):
            if payload.get(name) != prior_expected:
                raise ValueError(
                    f"raw predeclaration access declaration {name} must be {prior_expected} "
                    f"for {execution_mode}"
                )
        if execution_mode == "RELOCKED_ACCESS_EVIDENCE_RERUN":
            if len(str(payload.get("selection_predeclaration_sha256", ""))) != 64:
                raise ValueError("relocked rerun lacks immutable original-selection hash")
            if payload.get("reconstruction_parameters_changed_after_original_execution") is not False:
                raise ValueError("relocked rerun changed reconstruction parameters")
        raw_input = payload["raw_input"]
        raw_required = {
            "start_host_monotonic_ns", "stop_host_monotonic_ns_exclusive", "slice_sha256",
        }
        if not raw_required <= set(raw_input):
            raise ValueError(f"raw predeclaration missing fields: {sorted(raw_required-set(raw_input))}")
        if not (
            {"start_byte_inclusive", "stop_byte_exclusive"} <= set(raw_input)
            or {"start_byte_exclusive", "stop_byte_inclusive"} <= set(raw_input)
        ):
            raise ValueError("raw predeclaration lacks a half-open byte interval")
        raw_start = int(raw_input.get("start_byte_inclusive", raw_input.get("start_byte_exclusive")))
        raw_stop = int(raw_input.get("stop_byte_exclusive", raw_input.get("stop_byte_inclusive")))
        if raw_stop <= raw_start or len(str(raw_input["slice_sha256"])) != 64:
            raise ValueError("invalid raw action byte interval or slice hash")
        timing = payload.get("common_clock_timing_sources", {})
        timing_required = {
            "fusion_timing_log", "listener_directory", "capture_identity_authority",
            "capture_identity_authority_sha256",
        }
        if not timing_required <= set(timing):
            raise ValueError(f"raw timing predeclaration missing fields: {sorted(timing_required-set(timing))}")
        if not payload.get("forbidden_golf_boxing_raw_byte_ranges"):
            raise ValueError("raw predeclaration lacks explicit Golf/Boxing byte sentinels")
        forbidden_timing = payload.get("forbidden_golf_boxing_timing_intervals_ns", [])
        if not forbidden_timing:
            raise ValueError("raw predeclaration lacks explicit Golf/Boxing timing sentinels")
        for row in forbidden_timing:
            if not {
                "start_host_monotonic_ns", "stop_host_monotonic_ns_exclusive",
            } <= set(row):
                raise ValueError("invalid Golf/Boxing timing sentinel")
            if int(row["stop_host_monotonic_ns_exclusive"]) <= int(row["start_host_monotonic_ns"]):
                raise ValueError("empty Golf/Boxing timing sentinel")
        if len(str(payload.get("sealed_container_sha256", payload.get("ledger_sha256", "")))) != 64:
            raise ValueError("raw predeclaration lacks imported sealed container identity")
        for name in ("authoritative_action_event_source", "authoritative_action_event_source_sha256"):
            if name not in payload:
                raise ValueError(f"raw predeclaration missing {name}")
    return {
        "pass": True,
        "label": str(payload["action_identifier"]),
        "start_ns": start,
        "stop_ns": stop,
        "role_source": str(role_source.resolve()),
    }


def _arrays_digest(arrays: Mapping[str, np.ndarray]) -> str:
    digest = hashlib.sha256()
    for name in sorted(arrays):
        value = np.ascontiguousarray(arrays[name])
        digest.update(name.encode()); digest.update(str(value.dtype).encode())
        digest.update(np.asarray(value.shape, np.int64).tobytes()); digest.update(value.tobytes())
    return digest.hexdigest()


def _quaternions(rotation: np.ndarray) -> np.ndarray:
    return np.stack([
        matrix_to_quat_wxyz(rotation[:, index]) for index in range(rotation.shape[1])
    ], axis=1)


def _pack_shared_result(label: str, source, result, heading_confidence: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
    arrays = {
        "global_time_ns": result.time_ns,
        "window": np.full(len(result.time_ns), label, dtype="U64"),
        "boundary": source.boundary,
        "segment_rotation": result.segment_rotation,
        "segment_position": result.segment_position,
        "joint_rotvec": result.joint_rotvec,
        "joint_rate_rad_s": result.joint_rate_rad_s,
        "segment_confidence": result.segment_confidence,
        "segment_sigma_rad": result.segment_sigma_rad,
        "observation_residual_rad": result.observation_residual_rad,
        "gyro_bias_rad_s": np.stack([source.node_bias[node] for node in NODES], axis=1),
        "bias_sigma_rad_s": np.stack([source.node_bias_sigma[node] for node in NODES], axis=1),
        "rest_detected": np.stack([source.node_rest[node] for node in NODES], axis=1),
        "heading_observation_confidence": np.stack([
            heading_confidence[name] for name in result.segment_names
        ], axis=1),
        "segment_names": np.asarray(result.segment_names, dtype="U24"),
        "joint_names": np.asarray(result.joint_names, dtype="U24"),
        "node_names": np.asarray(NODES, dtype="U8"),
    }
    arrays["segment_quaternion_wxyz"] = _quaternions(arrays["segment_rotation"])
    return arrays


def _run_direct_fk_ablation(
    *, model, static, source, observed_rotation: Mapping[str, np.ndarray],
    heading_confidence: Mapping[str, np.ndarray], profile: Mapping[str, Any], label: str,
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Canonical FK of direct observations with no shared-IK temporal feedback."""
    times = source.time_ns; segments = tuple(model.segments); joints = tuple(model.joint_ids)
    count = len(times); dimension = 3 + 3 * len(joints)
    direct = np.empty((count, dimension))
    for i in range(count):
        direct[i] = _direct_configuration(
            model, static, {name: observed_rotation[name][i] for name in segments},
        )
    joint_value = direct[:, 3:].reshape(count, len(joints), 3)
    joint_rate = np.zeros_like(joint_value)
    dt = np.diff(times) * 1e-9
    for i in range(1, count):
        joint_rate[i] = np.asarray([
            so3_log(so3_exp(joint_value[i - 1, j]).T @ so3_exp(joint_value[i, j])) / dt[i - 1]
            for j in range(len(joints))
        ])
    rotation = np.empty((count, len(segments), 3, 3)); position = np.empty((count, len(segments), 3))
    residual = np.empty((count, len(segments))); sigma = np.empty_like(residual)
    confidence = np.empty_like(residual); closure = 0.0
    zero_joint_rate = {joint: np.zeros(3) for joint in joints}
    zero_node = {node: np.zeros(3) for node in NODES}
    covariance = np.zeros((9 + 6 * len(joints) + 6 * len(NODES),) * 2)
    node_for_segment = {segment: node for node, segment in profile["identity"].items()}
    for i in range(count):
        state = KeyframeState(
            time_s=float(times[i]) * 1e-9,
            root_translation_model_m=np.zeros(3),
            root_rotation_model_rotvec=direct[i, :3].copy(),
            root_velocity_model_mps=np.zeros(3),
            joint_rotvec={joint: joint_value[i, j].copy() for j, joint in enumerate(joints)},
            joint_rate_rad_s=zero_joint_rate,
            gyro_bias_rad_s=zero_node,
            accel_bias_mps2=zero_node,
            covariance=covariance,
        )
        poses = model.segment_poses(state, static)
        for s, segment in enumerate(segments):
            rotation[i, s] = poses[segment].rotation; position[i, s] = poses[segment].translation
            residual[i, s] = float(rotation_angle(poses[segment].rotation, observed_rotation[segment][i]))
            node = node_for_segment[segment]
            extrinsic = np.asarray(profile["sensor_to_segment_rotation"][node]["one_sigma_rad"], float)
            heading_sigma = (1.0 - float(heading_confidence[segment][i])) * np.deg2rad(15.0)
            sigma[i, s] = float(
                np.sqrt(np.mean(extrinsic ** 2)) + heading_sigma
                + source.node_bias_sigma[node][i] * max(0.0, (times[i] - times[0]) * 1e-9)
            )
            confidence[i, s] = float(1.0 / (1.0 + sigma[i, s] + residual[i, s]))
            closure = max(closure, float(residual[i, s]))
    class Direct:
        pass
    result = Direct()
    result.time_ns = times; result.segment_names = segments; result.joint_names = joints
    result.segment_rotation = rotation; result.segment_position = position
    result.joint_rotvec = joint_value; result.joint_rate_rad_s = joint_rate
    result.segment_confidence = confidence; result.segment_sigma_rad = sigma
    result.observation_residual_rad = residual
    arrays = _pack_shared_result(label, source, result, heading_confidence)
    audit = {
        "schema": "biospur-fusion-v0-no-shared-ik-ablation-v1",
        "shared_ik_feedback_executed": False,
        "same_frontend_and_relative_heading_as_full": True,
        "direct_full_so3_generalized_coordinates": True,
        "canonical_body_model_fk_executed": True,
        "canonical_fk_observation_closure_max_abs_rad": closure,
        "root_translation": "DISPLAY_GAUGE_FIXED_ZERO",
    }
    return arrays, audit


def _execute_variants(root: Path, rows: Mapping[str, np.ndarray], profile: Mapping[str, Any], config) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    max_gap_ns = int(config.section("frontend")["max_gap_ns"])
    frontends = {
        node: run_vqf_native_hybrid(rows[node], node_id=node, max_gap_ns=max_gap_ns)
        for node in NODES
    }
    evidence = {"extrinsic_rotation": profile["sensor_to_segment_rotation"]}
    identity = profile["identity"]
    raw_source = resample_window(
        frontends, evidence, int(config.section("frontend")["output_rate_hz"]),
        identity=identity,
    )
    frame_contract = profile.get("capture_attitude_frame")
    if frame_contract is None:
        source = raw_source
        frame_audit = {
            "schema": "biospur-fusion-v0-common-action-display-yaw-initialization-v1",
            "initialization_executed": False,
            "legacy_profile_without_capture_attitude_frame": True,
        }
    else:
        source, frame_audit = initialize_common_action_display_yaw(
            raw_source, frame_contract,
        )
    model = corrected_body_model(
        root,
        identity_mapping=profile["identity"],
        identity_provenance=(
            "CAPTURE_BOUND_PROFILE_IDENTITY:"
            f"{profile.get('capture_id', profile.get('profile_kind', 'LEGACY_PROFILE'))}"
        ),
    )
    static = display_static_calibration(model, profile, config.section("display_geometry"))
    heading_rotation, heading_confidence, heading_audit = apply_soft_qmt_heading(
        source.time_ns, source.segment_rotation, source.segment_gyro,
        profile["functional_axes"], config.section("relative_heading"), source.segment_degraded,
    )
    full = run_shared_ik(
        model, static, source.time_ns, heading_rotation, source.node_bias_sigma,
        profile, config.section("shared_ik"), axis_enabled=True,
        segment_observation_confidence=heading_confidence,
    )
    full_arrays = _pack_shared_result("ACTION", source, full, heading_confidence)
    no_ik_arrays, no_ik_audit = _run_direct_fk_ablation(
        model=model, static=static, source=source, observed_rotation=heading_rotation,
        heading_confidence=heading_confidence, profile=profile, label="ACTION",
    )
    # A six-axis VQF trajectory supplies a valid attitude observation (tilt
    # plus integrated yaw), but no independent heading observation. QMT_OFF
    # therefore keeps non-degraded VQF attitude available to shared IK while
    # separately reporting zero heading-evidence confidence. This general
    # mode-wide contract retains the existing missing-heading uncertainty term.
    attitude_observation_confidence = {
        segment: np.where(source.segment_degraded[segment], 0.0, 1.0)
        for segment in model.segments
    }
    no_heading_evidence_confidence = {
        segment: np.zeros(len(source.time_ns), float) for segment in model.segments
    }
    no_heading = run_shared_ik(
        model, static, source.time_ns, source.segment_rotation, source.node_bias_sigma,
        profile, config.section("shared_ik"), axis_enabled=True,
        segment_observation_confidence=attitude_observation_confidence,
        segment_heading_evidence_confidence=no_heading_evidence_confidence,
    )
    no_heading_arrays = _pack_shared_result(
        "ACTION", source, no_heading, no_heading_evidence_confidence,
    )
    no_heading_no_ik_arrays, no_heading_no_ik_audit = _run_direct_fk_ablation(
        model=model, static=static, source=source, observed_rotation=source.segment_rotation,
        heading_confidence=no_heading_evidence_confidence, profile=profile, label="ACTION",
    )
    qmt_off_audit = {
        "schema": "biospur-fusion-v0-qmt-off-pass-through-v1",
        "relative_heading_mode": "QMT_OFF",
        "relative_heading_executed": False,
        "qmt_correction_active": False,
        "qmt_bypass_reason": "PRODUCT_MODE_QMT_OFF",
        "fallback": "EXACT_FIXED_PROFILE_VQF_AFTER_ONE_COMMON_DISPLAY_YAW",
        "common_display_yaw_is_coordinate_change_not_heading_observation": True,
        "per_segment_yaw_parameters_fitted": 0,
        "per_segment_extrinsics_refitted": False,
        "joint_rest_refitted": False,
        "action_specific_pose_template_used": False,
        "state_propagated_from_other_action": False,
        "heading_evidence_confidence_contract": "ZERO_NO_INDEPENDENT_HEADING_OBSERVATION",
        "heading_evidence_confidence_minimum": 0.0,
        "heading_evidence_confidence_maximum": 0.0,
        "missing_heading_uncertainty_term_preserved": True,
        "attitude_observation_weight_contract": "ONE_WHEN_NONDEGRADED_ZERO_WHEN_DEGRADED",
        "attitude_and_heading_confidence_decoupled": True,
        "applied_correction_rms_deg": 0.0,
        "applied_correction_max_abs_deg": 0.0,
        "branch_changes": 0,
        "branch_margin_available": False,
        "same_frontend_resampling_ik_and_fk_as_always_on_control": True,
    }
    variants = {
        "qmt_off": no_heading_arrays,
        "qmt_off_no_shared_ik": no_heading_no_ik_arrays,
        "always_on_qmt": full_arrays,
        "always_on_qmt_no_shared_ik": no_ik_arrays,
        # Compatibility names retained for the historical validation surface.
        "full": full_arrays,
        "no_shared_ik": no_ik_arrays,
        "no_relative_heading": no_heading_arrays,
    }
    sensor_rotation = np.stack([
        np.einsum(
            "nij,jk->nik", source.segment_rotation[identity[node]],
            so3_exp(np.asarray(
                profile["sensor_to_segment_rotation"][node]["rotvec_segment_from_sensor"], float,
            )),
        )
        for node in NODES
    ], axis=1)
    for arrays in variants.values():
        segment_index = {str(name): index for index, name in enumerate(arrays["segment_names"])}
        arrays["node_orientation_world_sensor"] = sensor_rotation.copy()
        arrays["parent_child_relative_rotation"] = np.stack([
            np.einsum(
                "nji,njk->nik",
                arrays["segment_rotation"][:, segment_index[joint.parent]],
                arrays["segment_rotation"][:, segment_index[joint.child]],
            )
            for joint in model.joints
        ], axis=1)
    audits = {
        "frontend": {node: frontends[node].audit for node in NODES},
        "capture_attitude_frame": frame_audit,
        "full": {
            "relative_heading": heading_audit,
            "shared_ik": {"shared_ik_feedback_executed": True, **full.audit},
        },
        "no_shared_ik": no_ik_audit,
        "no_relative_heading": {
            **qmt_off_audit,
            "same_frontend_resampling_shared_ik_and_fk_as_full": True,
            "shared_ik": {"shared_ik_feedback_executed": True, **no_heading.audit},
        },
        "qmt_off": {
            "relative_heading": qmt_off_audit,
            "shared_ik": {"shared_ik_feedback_executed": True, **no_heading.audit},
        },
        "qmt_off_no_shared_ik": {
            "relative_heading": qmt_off_audit,
            "shared_ik": no_heading_no_ik_audit,
        },
        "always_on_qmt": {
            "relative_heading": heading_audit,
            "shared_ik": {"shared_ik_feedback_executed": True, **full.audit},
        },
        "always_on_qmt_no_shared_ik": {
            "relative_heading": heading_audit,
            "shared_ik": no_ik_audit,
        },
        "initialization_policy": "IDENTICAL_ONE_CAUSAL_FRONTEND_TIMELINE_SHARED_BY_ALL_VARIANTS",
        "profile_writeback": False,
    }
    return variants, audits


def _distribution(value: np.ndarray) -> dict[str, float]:
    flat = np.asarray(value, float).reshape(-1)
    return {
        "median": float(np.median(flat)), "q95": float(np.quantile(flat, 0.95)),
        "q99": float(np.quantile(flat, 0.99)), "maximum": float(np.max(flat)),
    }


def _variant_metrics(arrays: Mapping[str, np.ndarray], model, fk_closure: float) -> dict[str, Any]:
    rotation = arrays["segment_rotation"]; boundary = arrays["boundary"]
    continuous = boundary[1:] == "CONTINUOUS"
    segment_step = np.degrees(rotation_angle(rotation[:-1], rotation[1:]))
    joint_step_rows = []
    names = [str(value) for value in arrays["segment_names"]]; index = {name: i for i, name in enumerate(names)}
    for joint in model.joints:
        parent = rotation[:, index[joint.parent]]; child = rotation[:, index[joint.child]]
        relative = np.einsum("nji,njk->nik", parent, child)
        joint_step_rows.append(np.degrees(rotation_angle(relative[:-1], relative[1:])))
    joint_step = np.stack(joint_step_rows, axis=1)
    quat_dot = np.sum(
        arrays["segment_quaternion_wxyz"][:-1] * arrays["segment_quaternion_wxyz"][1:], axis=2,
    )
    matrix = rotation.reshape(-1, 3, 3)
    orthogonality = np.einsum("nji,njk->nik", matrix, matrix)
    numeric_names = (
        "segment_rotation", "segment_position", "joint_rotvec", "joint_rate_rad_s",
        "segment_confidence", "segment_sigma_rad", "observation_residual_rad",
        "gyro_bias_rad_s", "bias_sigma_rad_s",
    )
    root = names.index(model.root_segment)
    return {
        "frames": int(len(rotation)),
        "finite": bool(all(np.isfinite(arrays[name]).all() for name in numeric_names)),
        "rotation_determinant_max_abs_error": float(np.max(np.abs(np.linalg.det(matrix) - 1.0))),
        "rotation_orthogonality_max_abs_error": float(np.max(np.abs(orthogonality - np.eye(3)))),
        "continuous_segment_step_deg": _distribution(segment_step[continuous]),
        "continuous_parent_child_relative_step_deg": _distribution(joint_step[continuous]),
        "continuous_quaternion_dot_minimum": float(np.min(quat_dot[continuous])),
        "continuous_steps_over_90_deg": int(np.count_nonzero(segment_step[continuous] > 90.0)),
        "boundary_frames": int(np.count_nonzero(boundary != "CONTINUOUS")),
        "boundary_values": dict(Counter(str(value) for value in boundary)),
        "native_time_strictly_increasing": bool(np.all(np.diff(arrays["global_time_ns"]) > 0)),
        "canonical_fk_closure_max_abs": float(fk_closure),
        "root_display_position_max_abs": float(np.max(np.abs(arrays["segment_position"][:, root]))),
        "maximum_segment_origin_norm_display_units": float(np.max(np.linalg.norm(arrays["segment_position"], axis=2))),
        "observation_residual_deg": _distribution(np.degrees(arrays["observation_residual_rad"])),
        "segment_uncertainty_deg": _distribution(np.degrees(arrays["segment_sigma_rad"])),
    }


def _gross_motion_metrics(arrays: Mapping[str, np.ndarray], model) -> dict[str, Any]:
    rotation = arrays["segment_rotation"]; segment_names = [str(x) for x in arrays["segment_names"]]
    output = {"segments": {}, "joints": {}}
    for s, name in enumerate(segment_names):
        excursion = np.degrees(rotation_angle(rotation[0, s], rotation[:, s]))
        output["segments"][name] = {
            "excursion_from_action_start_deg": _distribution(excursion),
            "net_start_to_end_deg": float(np.degrees(rotation_angle(rotation[0, s], rotation[-1, s]))),
        }
    index = {name: i for i, name in enumerate(segment_names)}
    for joint in model.joints:
        parent = rotation[:, index[joint.parent]]; child = rotation[:, index[joint.child]]
        relative = np.einsum("nji,njk->nik", parent, child)
        excursion = np.degrees(rotation_angle(relative[0], relative))
        output["joints"][joint.joint_id] = {
            "relative_excursion_from_action_start_deg": _distribution(excursion),
            "net_relative_start_to_end_deg": float(np.degrees(rotation_angle(relative[0], relative[-1]))),
        }
    ranked = sorted(
        ((row["excursion_from_action_start_deg"]["q95"], name) for name, row in output["segments"].items()),
        reverse=True,
    )
    output["segments_ranked_by_q95_excursion_deg"] = [
        {"segment": name, "q95_deg": value} for value, name in ranked
    ]
    return output


def _compare_variants(full: Mapping[str, np.ndarray], other: Mapping[str, np.ndarray]) -> dict[str, Any]:
    return {
        "segment_rotation_difference_deg": _distribution(np.degrees(rotation_angle(
            full["segment_rotation"], other["segment_rotation"],
        ))),
        "segment_position_difference_display_units": _distribution(np.linalg.norm(
            full["segment_position"] - other["segment_position"], axis=2,
        )),
        "joint_rotvec_vector_difference_deg": _distribution(np.degrees(np.linalg.norm(
            full["joint_rotvec"] - other["joint_rotvec"], axis=2,
        ))),
        "full_observation_residual_deg": _distribution(np.degrees(full["observation_residual_rad"])),
        "ablation_observation_residual_deg": _distribution(np.degrees(other["observation_residual_rad"])),
    }


def _uncertainty_localization(arrays: Mapping[str, np.ndarray], profile: Mapping[str, Any]) -> dict[str, Any]:
    sigma_deg = np.degrees(arrays["segment_sigma_rad"]); count, segment_count = sigma_deg.shape
    segments = [str(value) for value in arrays["segment_names"]]
    nodes = [str(value) for value in arrays["node_names"]]
    node_for_segment = {segment: node for node, segment in profile["identity"].items()}
    node_index = {node: i for i, node in enumerate(nodes)}
    phases = np.asarray([
        "ONSET_THIRD" if i < count / 3 else "MIDDLE_THIRD" if i < 2 * count / 3 else "OFFSET_THIRD"
        for i in range(count)
    ])
    flat = sigma_deg.reshape(-1); top_count = max(1, int(math.ceil(0.01 * len(flat))))
    top_flat = np.argpartition(flat, -top_count)[-top_count:]
    top_flat = top_flat[np.argsort(flat[top_flat])[::-1]]
    top_rows = []
    segment_counter: Counter[str] = Counter(); node_counter: Counter[str] = Counter()
    phase_counter: Counter[str] = Counter(); boundary_counter: Counter[str] = Counter()
    not_rest = 0
    for flat_index in top_flat:
        frame, segment_i = np.unravel_index(int(flat_index), sigma_deg.shape)
        segment = segments[segment_i]; node = node_for_segment[segment]; n = node_index[node]
        boundary = str(arrays["boundary"][frame]); phase = str(phases[frame])
        segment_counter[segment] += 1; node_counter[node] += 1; phase_counter[phase] += 1
        boundary_counter[boundary] += 1; not_rest += int(not arrays["rest_detected"][frame, n])
        if len(top_rows) < 50:
            extrinsic = np.asarray(profile["sensor_to_segment_rotation"][node]["one_sigma_rad"], float)
            elapsed = float(arrays["global_time_ns"][frame] - arrays["global_time_ns"][0]) * 1e-9
            top_rows.append({
                "frame": int(frame), "global_time_ns": int(arrays["global_time_ns"][frame]),
                "node": node, "segment": segment, "phase": phase, "boundary": boundary,
                "rest_detected": bool(arrays["rest_detected"][frame, n]),
                "uncertainty_deg": float(sigma_deg[frame, segment_i]),
                "extrinsic_component_deg": float(np.degrees(np.sqrt(np.mean(extrinsic ** 2)))),
                "relative_heading_confidence": float(arrays["heading_observation_confidence"][frame, segment_i]),
                "heading_confidence_component_deg": float(
                    (1.0 - arrays["heading_observation_confidence"][frame, segment_i]) * 15.0
                ),
                "bias_growth_component_deg": float(np.degrees(arrays["bias_sigma_rad_s"][frame, n] * elapsed)),
                "observation_residual_deg": float(np.degrees(arrays["observation_residual_rad"][frame, segment_i])),
            })
    by_segment = {
        segment: _distribution(sigma_deg[:, index]) for index, segment in enumerate(segments)
    }
    by_phase = {
        phase: _distribution(sigma_deg[phases == phase]) for phase in ("ONSET_THIRD", "MIDDLE_THIRD", "OFFSET_THIRD")
    }
    degraded = arrays["boundary"] != "CONTINUOUS"
    by_health = {
        "CONTINUOUS": _distribution(sigma_deg[~degraded]),
        "BOUNDARY_OR_DEGRADED": _distribution(sigma_deg[degraded]),
    } if np.any(degraded) and np.any(~degraded) else {
        "CONTINUOUS": _distribution(sigma_deg),
        "BOUNDARY_OR_DEGRADED": None,
    }
    return {
        "schema": "biospur-fusion-v0-action-uncertainty-localization-v1",
        "units": "degrees",
        "global": _distribution(sigma_deg),
        "by_segment": by_segment,
        "by_node": {node_for_segment[segment]: row for segment, row in by_segment.items()},
        "by_action_phase": by_phase,
        "by_gap_reset_health": by_health,
        "top_one_percent": {
            "values_considered": int(top_count),
            "threshold_deg": float(np.min(flat[top_flat])),
            "segment_counts": dict(segment_counter), "node_counts": dict(node_counter),
            "phase_counts": dict(phase_counter), "boundary_counts": dict(boundary_counter),
            "not_rest_fraction": float(not_rest / top_count),
            "highest_50_causal_rows": top_rows,
        },
        "extreme_values_clipped": False,
        "estimator_components_reported": [
            "profile extrinsic", "relative-heading confidence", "VQF bias uncertainty growth",
            "shared-IK observation residual",
        ],
    }


def write_checksums(output: Path) -> dict[str, str]:
    output = Path(output).resolve(); manifest = {}
    for path in sorted(output.iterdir()):
        if path.is_file() and path.name != "SHA256SUMS":
            manifest[path.name] = sha256_file(path)
    (output / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in sorted(manifest.items())),
        encoding="utf-8",
    )
    return manifest


def run_locked_action_validation(root: Path, predeclaration_path: Path, output: Path) -> dict[str, Any]:
    root = Path(root).resolve(); output = Path(output).resolve()
    predeclaration_path = Path(predeclaration_path).resolve()
    predeclaration = json.loads(predeclaration_path.read_text(encoding="utf-8"))
    predecl_check = validate_predeclaration(root, predeclaration)
    manifest_path = Path(str(predeclaration["candidate_manifest"]))
    if not manifest_path.is_absolute(): manifest_path = root / manifest_path
    profile_path = Path(str(predeclaration["profile"]))
    if not profile_path.is_absolute(): profile_path = root / profile_path
    candidate_before = verify_candidate_lock(
        root, manifest_path, str(predeclaration["candidate_manifest_sha256"]),
    )
    profile_sha_before = sha256_file(profile_path)
    if profile_sha_before != predeclaration["profile_sha256"]:
        raise ValueError("frozen profile SHA-256 mismatch before action access")
    profile = json.loads(profile_path.read_text(encoding="utf-8")); assert_profile_boundary(profile)
    config_path = root / "config/biospur_fusion_v0/config.json"; config = load_config(config_path)
    if profile.get("config_sha256") != config.sha256:
        raise ValueError("frozen profile and active V0 configuration disagree")

    # This is the first validation-payload access point.  Every authorization,
    # role, candidate, profile, and metadata hash check above must pass first.
    ledger = Path(str(predeclaration["ledger"]))
    if not ledger.is_absolute(): ledger = root / ledger
    rows, access = load_authorized_action_imu_only(
        ledger,
        action_label=predecl_check["label"],
        start_ns=predecl_check["start_ns"], stop_ns=predecl_check["stop_ns"],
        expected_ledger_sha256=str(predeclaration["ledger_sha256"]),
    )
    variants, audits = _execute_variants(root, rows, profile, config)
    repeat, _ = _execute_variants(root, rows, profile, config)
    deterministic = {
        name: {
            "first_digest": _arrays_digest(arrays),
            "repeat_digest": _arrays_digest(repeat[name]),
            "identical": _arrays_digest(arrays) == _arrays_digest(repeat[name]),
        }
        for name, arrays in variants.items()
    }
    action_label = str(predeclaration["action_identifier"])
    for arrays in variants.values(): arrays["window"][:] = action_label
    for arrays in repeat.values(): arrays["window"][:] = action_label
    model = corrected_body_model(
        root,
        identity_mapping=profile["identity"],
        identity_provenance=(
            "CAPTURE_BOUND_PROFILE_IDENTITY:"
            f"{profile.get('capture_id', profile.get('profile_kind', 'LEGACY_PROFILE'))}"
        ),
    )
    numerical = {
        "full": _variant_metrics(
            variants["full"], model,
            audits["full"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
        "no_shared_ik": _variant_metrics(
            variants["no_shared_ik"], model,
            audits["no_shared_ik"]["canonical_fk_observation_closure_max_abs_rad"],
        ),
        "no_relative_heading": _variant_metrics(
            variants["no_relative_heading"], model,
            audits["no_relative_heading"]["shared_ik"]["canonical_fk_closure_max_abs"],
        ),
    }
    metrics = {
        "schema": "biospur-fusion-v0-independent-action-metrics-v1",
        "predeclared_expected_gross_motion_semantics": predeclaration["expected_gross_motion_semantics"],
        "numerical_integrity": numerical,
        "deterministic_replay": deterministic,
        "gross_motion": _gross_motion_metrics(variants["full"], model),
        "full_versus_no_shared_ik": _compare_variants(variants["full"], variants["no_shared_ik"]),
        "full_versus_no_relative_heading": _compare_variants(variants["full"], variants["no_relative_heading"]),
        "module_audits": audits,
        "threshold_policy": "NO_NEW_PRODUCT_THRESHOLDS_PHYSICAL_INVARIANTS_AND_ABLATIONS_ONLY",
    }
    uncertainty = _uncertainty_localization(variants["full"], profile)
    np.savez_compressed(output / "ACTION_STATE_FULL.npz", **variants["full"])
    np.savez_compressed(output / "ACTION_STATE_NO_SHARED_IK.npz", **variants["no_shared_ik"])
    np.savez_compressed(output / "ACTION_STATE_NO_RELATIVE_HEADING.npz", **variants["no_relative_heading"])
    dump_json(output / "ACTION_VALIDATION_METRICS.json", metrics)
    dump_json(output / "UNCERTAINTY_LOCALIZATION.json", uncertainty)
    viewer = write_viewer(
        output / "ACTION_VIEWER.html",
        time_ns=variants["full"]["global_time_ns"], window=variants["full"]["window"],
        boundary=variants["full"]["boundary"],
        segment_names=tuple(str(x) for x in variants["full"]["segment_names"]),
        segment_position=variants["full"]["segment_position"],
        segment_rotation=variants["full"]["segment_rotation"],
        segment_confidence=variants["full"]["segment_confidence"],
        joint_rotvec=variants["full"]["joint_rotvec"],
    )
    profile_sha_after = sha256_file(profile_path)
    candidate_after = verify_candidate_lock(
        root, manifest_path, str(predeclaration["candidate_manifest_sha256"]),
    )
    access_and_immutability = {
        "schema": "biospur-fusion-v0-action-access-and-immutability-v1",
        "predeclaration_sha256": sha256_file(predeclaration_path),
        "exact_predeclared_action_opened": bool(
            access["ledger"] == str(ledger.resolve())
            and access["action_label"] == action_label
            and access["start_global_time_ns"] == int(predeclaration["start_global_time_ns"])
            and access["stop_global_time_ns_exclusive"] == int(predeclaration["stop_global_time_ns_exclusive"])
        ),
        "access": access,
        "candidate_before": candidate_before, "candidate_after": candidate_after,
        "candidate_modified_after_action_access": False,
        "profile_sha256_before": profile_sha_before, "profile_sha256_after": profile_sha_after,
        "profile_byte_exact_unchanged": profile_sha_before == profile_sha_after,
        "capture1_used_only_through_frozen_profile": True,
        "capture1_payload_opened_by_validation_runner": False,
        "runtime_state_written_back_to_profile": False,
        "opened_payload_classes": access["opened_payload_classes"],
        "held_out_golf_boxing_accessed": False,
        "uwb_spatial_members_opened": [],
        "uwb_spatial_observations_consumed": False,
    }
    dump_json(output / "ACCESS_AND_PROFILE_IMMUTABILITY.json", access_and_immutability)
    hard_integrity = bool(
        all(row["finite"] and row["native_time_strictly_increasing"] for row in numerical.values())
        and all(row["identical"] for row in deterministic.values())
        and access["imu_only_selection_pass"]
        and access_and_immutability["profile_byte_exact_unchanged"]
    )
    result = {
        "schema": "biospur-fusion-v0-independent-action-final-result-v1",
        "classification": {
            "OVERALL_SYSTEM_DIRECTION": "MIXED",
            "INDEPENDENT_ORDINARY_ACTION_SELECTED": "YES",
            "INDEPENDENT_ACTION_RECONSTRUCTION_EXECUTED": "YES",
            "CALIBRATION_PROFILE_FROZEN_BEFORE_ACTION": "YES",
            "V0_CANDIDATE_LOCKED_DURING_VALIDATION": "YES",
            "CANDIDATE_MODIFIED_AFTER_ACTION_ACCESS": "NO",
            "FINAL_V0_FROZEN": "NO",
            "INTERNAL_PHYSICAL_COHERENCE": "INCONCLUSIVE",
            "READY_FOR_OPERATOR_AUTHORIZED_V0_FREEZE": "NO",
        },
        "automatic_execution_integrity_pass": hard_integrity,
        "selected_action": {
            "capture_identifier": predeclaration["capture_identifier"],
            "action_identifier": action_label,
            "attempt_number": predeclaration["attempt_number"],
            "start_global_time_ns": int(predeclaration["start_global_time_ns"]),
            "stop_global_time_ns_exclusive": int(predeclaration["stop_global_time_ns_exclusive"]),
            "role": predeclaration["role"],
        },
        "candidate_manifest_sha256": predeclaration["candidate_manifest_sha256"],
        "profile_sha256_before": profile_sha_before,
        "profile_sha256_after": profile_sha_after,
        "opened_payload_classes": access["opened_payload_classes"],
        "held_out_golf_boxing_accessed": False,
        "uwb_spatial_access": "NONE",
        "state_continuity": {name: row["continuous_segment_step_deg"] for name, row in numerical.items()},
        "fk_closure": {name: row["canonical_fk_closure_max_abs"] for name, row in numerical.items()},
        "gross_action_semantic_result": "PENDING_EVIDENCE_INTERPRETATION_AND_LIVE_VIEWER_QA",
        "full_versus_no_shared_ik_result": "PENDING_EVIDENCE_INTERPRETATION",
        "full_versus_no_relative_heading_result": "PENDING_EVIDENCE_INTERPRETATION",
        "dominant_uncertainty_locations": uncertainty["top_one_percent"],
        "viewer": viewer,
        "live_viewer_qa": "NOT_YET_PERFORMED_NOT_CLAIMED",
        "external_accuracy_claim_boundary": "NO_EXTERNAL_MOCAP_TRUTH_NO_CENTIMETRE_OR_DEGREE_ACCURACY_CLAIM",
        "release_action": "NO_COMMIT_NO_FREEZE_WAIT_FOR_EVIDENCE_INTERPRETATION",
    }
    dump_json(output / "FINAL_RESULT.json", result)
    (output / "FINAL_RESULT.md").write_text(
        "\n".join(f"{key}: {value}" for key, value in result["classification"].items())
        + "\n\n# Independent ordinary-action validation\n\n"
        + "Execution artifacts are complete. Physical interpretation and live Viewer QA are pending; "
        + "this provisional classification must not be treated as a release decision.\n",
        encoding="utf-8",
    )
    write_checksums(output)
    return result
