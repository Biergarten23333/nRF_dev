"""Conservative V0 development comparison with a qmt-off product default."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from biospur_fusion.root_r6a2a.shadow import corrected_body_model

from .contracts import (
    IDENTITY, NODES, WINDOWS, assert_profile_boundary, dump_json, load_config,
    load_release_mode, sha256_file,
)
from .data import load_capture1_imu_only
from .math3d import rotation_angle
from .raw_validation import load_raw_action_imu_only
from .validation import (
    _arrays_digest, _compare_variants, _execute_variants, _variant_metrics,
)
from .viewer import write_viewer


RELEASE_MODE_PATH = Path("config/biospur_fusion_v0/release_mode.json")
PROFILE_PATH = Path("logs/biospur_fusion_v0_golden_20260826T173258Z/V0_SESSION_PROFILE.json")
VARIANT_KEYS = (
    "qmt_off", "qmt_off_no_shared_ik",
    "always_on_qmt", "always_on_qmt_no_shared_ik",
)


def _distribution(value: np.ndarray) -> dict[str, float]:
    flat = np.asarray(value, float).reshape(-1)
    return {
        "median": float(np.median(flat)),
        "q95": float(np.quantile(flat, 0.95)),
        "maximum": float(np.max(flat)),
    }


def _concat(chunks: list[Mapping[str, np.ndarray]], labels: list[str]) -> dict[str, np.ndarray]:
    if len(chunks) != len(labels) or not chunks:
        raise ValueError("variant chunk/label mismatch")
    metadata = {"segment_names", "joint_names", "node_names"}
    output: dict[str, np.ndarray] = {}
    for key in chunks[0]:
        if key in metadata:
            output[key] = np.asarray(chunks[0][key]).copy()
            continue
        values = []
        for label, chunk in zip(labels, chunks):
            value = np.asarray(chunk[key]).copy()
            if key == "window":
                value[:] = label
            values.append(value)
        output[key] = np.concatenate(values, axis=0)
    return output


def _closure(audits_by_window: Mapping[str, Any], variant: str) -> float:
    values = []
    for audit in audits_by_window.values():
        shared = audit[variant]["shared_ik"]
        value = shared.get(
            "canonical_fk_closure_max_abs",
            shared.get("canonical_fk_observation_closure_max_abs_rad"),
        )
        if value is None:
            raise KeyError(f"missing FK closure for {variant}")
        values.append(float(value))
    return max(values)


def _per_node(arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    segment_index = {str(value): i for i, value in enumerate(arrays["segment_names"])}
    output = {}
    for node, segment in IDENTITY.items():
        i = segment_index[segment]
        output[node] = {
            "segment": segment,
            "observation_residual_deg": _distribution(np.degrees(arrays["observation_residual_rad"][:, i])),
            "uncertainty_deg": _distribution(np.degrees(arrays["segment_sigma_rad"][:, i])),
        }
    return output


def _uncertainty_thirds(arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    count = len(arrays["global_time_ns"])
    boundaries = np.linspace(0, count, 4, dtype=int)
    output = {}
    for third, (start, stop) in zip(("ONSET", "MIDDLE", "OFFSET"), zip(boundaries[:-1], boundaries[1:])):
        output[third] = _distribution(np.degrees(arrays["segment_sigma_rad"][start:stop]))
    return output


def _body_relative_yaw(arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    names = [str(value) for value in arrays["segment_names"]]
    root = names.index("pelvis")
    rotations = arrays["segment_rotation"]
    windows = arrays["window"]
    output = {}
    for label in dict.fromkeys(str(value) for value in windows):
        selected = np.flatnonzero(windows == label)
        by_segment = {}
        for i, segment in enumerate(names):
            relative = np.einsum(
                "nji,njk->nik", rotations[selected, root], rotations[selected, i],
            )
            yaw = np.unwrap(np.arctan2(relative[:, 1, 0], relative[:, 0, 0]))
            relative_start = np.degrees(yaw - yaw[0])
            by_segment[segment] = {
                "net_deg": float(relative_start[-1]),
                "excursion_deg": float(np.ptp(np.degrees(yaw))),
                "absolute_change": _distribution(np.abs(relative_start)),
            }
        output[label] = by_segment
    return output


def _segment_excursion(arrays: Mapping[str, np.ndarray]) -> dict[str, Any]:
    names = [str(value) for value in arrays["segment_names"]]
    windows = arrays["window"]
    output = {}
    for label in dict.fromkeys(str(value) for value in windows):
        selected = np.flatnonzero(windows == label)
        by_segment = {}
        for i, segment in enumerate(names):
            angle = np.degrees(rotation_angle(
                arrays["segment_rotation"][selected[0], i],
                arrays["segment_rotation"][selected, i],
            ))
            by_segment[segment] = _distribution(angle)
        output[label] = by_segment
    return output


def _qmt_diagnostic(audits_by_window: Mapping[str, Any]) -> dict[str, Any]:
    windows = {}
    for label, audit in audits_by_window.items():
        heading = audit["always_on_qmt"]["relative_heading"]
        joints = {}
        for joint, row in heading["joints"].items():
            raw_range = [float(value) for value in row["raw_delta_range_deg"]]
            joints[joint] = {
                "child_segment": row["child"],
                "applied_delta_rms_deg": float(row["applied_delta_rms_deg"]),
                "applied_delta_max_abs_deg": float(row["applied_delta_max_abs_deg"]),
                "maximum_applied_step_deg": float(row["maximum_applied_step_deg"]),
                "raw_delta_range_deg": raw_range,
                "raw_delta_span_deg": raw_range[1] - raw_range[0],
                "qmt_state_counts": row["qmt_state_counts"],
                "rating_median": float(row["rating_median"]),
                "rating_q95": float(row["rating_q95"]),
                "branch_margin_available": False,
                "branch_change_count_available": False,
            }
        windows[label] = joints
    return {
        "mode": "ALWAYS_ON_QMT_DIAGNOSTIC_ONLY",
        "qmt_version": "0.2.4",
        "startup_compares_pi_separated_initializations": True,
        "competing_branch_cost_margin_exposed_by_runtime_api": False,
        "rating_is_branch_margin": False,
        "windows": windows,
    }


def _summarize(
    root: Path,
    variants: Mapping[str, Mapping[str, np.ndarray]],
    repeats: Mapping[str, Mapping[str, np.ndarray]],
    audits_by_window: Mapping[str, Any],
    release_mode: Mapping[str, Any],
) -> dict[str, Any]:
    model = corrected_body_model(
        root, identity_mapping=IDENTITY,
        identity_provenance="LEGACY_CAPTURE1_CONSERVATIVE_PROFILE_EXPLICIT_IDENTITY",
    )
    numerical = {
        key: _variant_metrics(variants[key], model, _closure(audits_by_window, key))
        for key in VARIANT_KEYS
    }
    deterministic = {
        key: {
            "first_digest": _arrays_digest(variants[key]),
            "repeat_digest": _arrays_digest(repeats[key]),
            "identical": _arrays_digest(variants[key]) == _arrays_digest(repeats[key]),
        }
        for key in VARIANT_KEYS
    }
    selected = variants[str(release_mode["selected_variant"])]
    return {
        "schema": "biospur-fusion-v0-conservative-development-comparison-v1",
        "selected_v0_mode": release_mode["selected_v0_mode"],
        "confidence_gated_qmt": release_mode["confidence_gated_qmt"],
        "numerical_integrity": numerical,
        "deterministic_replay": deterministic,
        "per_node": {key: _per_node(variants[key]) for key in VARIANT_KEYS},
        "uncertainty_by_time_third": {
            key: _uncertainty_thirds(variants[key]) for key in VARIANT_KEYS
        },
        "common_root_gauge_removed_relative_yaw": {
            key: _body_relative_yaw(variants[key]) for key in VARIANT_KEYS
        },
        "segment_excursion_from_window_start": {
            key: _segment_excursion(variants[key]) for key in VARIANT_KEYS
        },
        "ik_attribution": {
            "qmt_off_ik_on_versus_off": _compare_variants(
                variants["qmt_off"], variants["qmt_off_no_shared_ik"],
            ),
            "always_on_qmt_ik_on_versus_off": _compare_variants(
                variants["always_on_qmt"], variants["always_on_qmt_no_shared_ik"],
            ),
        },
        "qmt_off_versus_always_on": _compare_variants(
            variants["qmt_off"], variants["always_on_qmt"],
        ),
        "qmt_correction_and_branch_diagnostic": _qmt_diagnostic(audits_by_window),
        "selected_state": {
            "frames": int(len(selected["global_time_ns"])),
            "qmt_correction_active": False,
            "qmt_bypass_reason": "PRODUCT_MODE_QMT_OFF",
            "uncertainty_retained_not_clipped": True,
        },
        "threshold_policy": "FROZEN_PROTOCOL_PHYSICAL_INVARIANTS_AND_MATCHED_ABLATIONS_ONLY",
    }


def _write_outputs(
    root: Path,
    output: Path,
    variants: Mapping[str, Mapping[str, np.ndarray]],
    repeats: Mapping[str, Mapping[str, np.ndarray]],
    audits_by_window: Mapping[str, Any],
    access: Mapping[str, Any],
    profile_path: Path,
    profile_sha_before: str,
    release_mode: Mapping[str, Any],
) -> dict[str, Any]:
    comparison = _summarize(root, variants, repeats, audits_by_window, release_mode)
    selected = variants[str(release_mode["selected_variant"])]
    file_names = {
        "qmt_off": "STATE_QMT_OFF.npz",
        "qmt_off_no_shared_ik": "STATE_QMT_OFF_NO_SHARED_IK.npz",
        "always_on_qmt": "STATE_ALWAYS_ON_QMT.npz",
        "always_on_qmt_no_shared_ik": "STATE_ALWAYS_ON_QMT_NO_SHARED_IK.npz",
    }
    for key, name in file_names.items():
        np.savez_compressed(output / name, **variants[key])
    dump_json(output / "COMPARISON.json", comparison)
    dump_json(output / "ACCESS_AUDIT.json", access)
    dump_json(output / "MODULE_AUDITS.json", audits_by_window)
    segment_names = tuple(str(value) for value in selected["segment_names"])
    inverse = {segment: node for node, segment in IDENTITY.items()}
    viewer = write_viewer(
        output / "VIEWER_QMT_OFF.html",
        time_ns=selected["global_time_ns"], window=selected["window"],
        boundary=selected["boundary"], segment_names=segment_names,
        segment_position=selected["segment_position"],
        segment_rotation=selected["segment_rotation"],
        segment_confidence=selected["segment_confidence"],
        joint_rotvec=selected["joint_rotvec"],
        segment_sigma_rad=selected["segment_sigma_rad"],
        node_by_segment=tuple(inverse[name] for name in segment_names),
        qmt_mode="QMT_OFF",
    )
    profile_sha_after = sha256_file(profile_path)
    immutability = {
        "profile": str(profile_path),
        "sha256_before": profile_sha_before,
        "sha256_after": profile_sha_after,
        "byte_exact_unchanged": profile_sha_before == profile_sha_after,
        "runtime_state_written_back": False,
    }
    dump_json(output / "PROFILE_IMMUTABILITY.json", immutability)
    deterministic_pass = all(
        row["identical"] for row in comparison["deterministic_replay"].values()
    )
    numerical_pass = all(
        row["finite"] and row["native_time_strictly_increasing"]
        and row["continuous_steps_over_90_deg"] == 0
        and row["canonical_fk_closure_max_abs"] < 1e-7
        for row in comparison["numerical_integrity"].values()
    )
    result = {
        "schema": "biospur-fusion-v0-conservative-development-result-v1",
        "selected_v0_mode": release_mode["selected_v0_mode"],
        "qmt_off_executed": True,
        "always_on_qmt_control_executed": True,
        "confidence_gated_qmt": "NOT_JUSTIFIED",
        "full_vertical_slice_executed": True,
        "automatic_integrity_pass": bool(
            deterministic_pass and numerical_pass and immutability["byte_exact_unchanged"]
        ),
        "viewer": viewer,
        "viewer_state_correspondence": {
            "frames_equal": viewer["frames"] == len(selected["global_time_ns"]),
            "qmt_mode_equal": viewer["qmt_mode"] == "QMT_OFF",
            "source_state_sha256": sha256_file(output / file_names["qmt_off"]),
        },
        "profile_immutability": immutability,
        "release_mode_sha256": release_mode["sha256"],
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
        "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
        "GOLF_BOXING_USED_FOR_TUNING": "NO",
        "GOLF_BOXING_USED_FOR_SCORING": "NO"
    }
    dump_json(output / "RESULT.json", result)
    return result


def _load_authority(root: Path) -> tuple[Any, Mapping[str, Any], Path, str, Mapping[str, Any]]:
    config = load_config(root / "config/biospur_fusion_v0/config.json")
    release_mode = load_release_mode(root / RELEASE_MODE_PATH)
    profile_path = (root / PROFILE_PATH).resolve()
    profile_sha = sha256_file(profile_path)
    if profile_sha != release_mode["historical_profile_sha256"]:
        raise ValueError("historical profile differs from release-mode provenance")
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    assert_profile_boundary(profile)
    if profile.get("config_sha256") != config.sha256:
        raise ValueError("historical profile and scientific config disagree")
    return config, profile, profile_path, profile_sha, release_mode


def run_capture1_development(root: Path, output: Path) -> dict[str, Any]:
    root = Path(root).resolve(); output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.mkdir(parents=True)
    config, profile, profile_path, profile_sha, release_mode = _load_authority(root)
    ledger = root / str(config.payload["ledger"])
    windows, access = load_capture1_imu_only(ledger)
    first_chunks = {key: [] for key in VARIANT_KEYS}
    repeat_chunks = {key: [] for key in VARIANT_KEYS}
    labels = []
    audits_by_window = {}
    for label, _, _ in WINDOWS:
        first, audits = _execute_variants(root, windows[label], profile, config)
        repeat, _ = _execute_variants(root, windows[label], profile, config)
        labels.append(label); audits_by_window[label] = audits
        for key in VARIANT_KEYS:
            first_chunks[key].append(first[key]); repeat_chunks[key].append(repeat[key])
    variants = {key: _concat(first_chunks[key], labels) for key in VARIANT_KEYS}
    repeats = {key: _concat(repeat_chunks[key], labels) for key in VARIANT_KEYS}
    return _write_outputs(
        root, output, variants, repeats, audits_by_window, access,
        profile_path, profile_sha, release_mode,
    )


def run_raw_development(root: Path, predeclaration_path: Path, output: Path) -> dict[str, Any]:
    root = Path(root).resolve(); output = Path(output).resolve()
    if output.exists():
        raise ValueError(f"output already exists: {output}")
    output.mkdir(parents=True)
    config, profile, profile_path, profile_sha, release_mode = _load_authority(root)
    predeclaration = json.loads(Path(predeclaration_path).resolve().read_text(encoding="utf-8"))
    if str(predeclaration.get("profile_sha256")) != profile_sha:
        raise ValueError("development predeclaration profile differs")
    rows, access = load_raw_action_imu_only(predeclaration)
    first, audits = _execute_variants(root, rows, profile, config)
    repeat, _ = _execute_variants(root, rows, profile, config)
    label = str(predeclaration["action_identifier"])
    variants = {key: _concat([first[key]], [label]) for key in VARIANT_KEYS}
    repeats = {key: _concat([repeat[key]], [label]) for key in VARIANT_KEYS}
    return _write_outputs(
        root, output, variants, repeats, {label: audits}, access,
        profile_path, profile_sha, release_mode,
    )
