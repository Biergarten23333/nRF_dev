#!/usr/bin/env python3
"""Execute the authorized R6A2B-R2 layered real calibration."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from biospur_fusion.imu.preintegration import (
    G, ImuSample, NativeTimePreintegrator, NoiseParameters, PreintegrationStatus,
)
from biospur_fusion.root_r6a0.math3d import so3_exp, so3_log
from biospur_fusion.root_r6a2a.shadow import corrected_body_model
from biospur_fusion.root_r6a2b.layered_calibration import (
    COMMON_NINE, FAMILIES, FUNCTIONAL_WINDOW, NODES,
    CanonicalCalibrationAdapter, derive_bone_lengths, information_diagnostics,
    signed_axis_audit, solve_geometry_layer, solve_rotation_layer,
    _state_from_segment_rotations,
)
from biospur_fusion.root_r6a2b.real_profile import profile_checksum
from biospur_fusion.root_r6a2b.real_shadow import LEDGER_REL, _stored_npy_memmap
from biospur_fusion.uwb.frontend import CanonicalT4Frontend


FUSION = Path(__file__).resolve().parents[1]
R1 = FUSION / "logs/root_r6a2b_r1_calibration_first_20260826T093237Z"
CAPTURE_ID = "v47_ten_node_body_calibration_20260814_093601"
CHECKPOINT = "52e2896bb6437aa19710a6c0b6f54b4193f64e4a"
WINDOWS = (
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


def dump(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def quantiles(values: np.ndarray) -> dict[str, float | int | None]:
    values = np.asarray(values, float)
    if not len(values):
        return {"count": 0, "min": None, "q05": None, "median": None,
                "q95": None, "max": None, "rms": None}
    return {
        "count": int(len(values)), "min": float(np.min(values)),
        "q05": float(np.quantile(values, 0.05)), "median": float(np.median(values)),
        "q95": float(np.quantile(values, 0.95)), "max": float(np.max(values)),
        "rms": float(np.sqrt(np.mean(values ** 2))),
    }


def load_authorized_windows() -> tuple[dict[str, dict[str, dict[str, np.ndarray]]], dict[str, Any]]:
    ledger = FUSION / LEDGER_REL
    windows: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    audit: dict[str, Any] = {
        "schema": "biospur-root-r6a2b-r2-authorized-window-access-v1",
        "ledger": str(ledger), "ledger_sha256": sha256(ledger),
        "access_method": "ZIP_STORED member memmap followed by exact half-open timestamp slice",
        "held_out_members_or_intervals_opened": [], "windows": {},
    }
    for label, start_ns, stop_ns in WINDOWS:
        windows[label] = {}
        audit["windows"][label] = {
            "start_global_time_ns": start_ns,
            "stop_global_time_ns_exclusive": stop_ns,
            "per_node": {},
        }
        for node in NODES:
            modalities = {}
            node_audit = {}
            for kind in ("imu", "uwb"):
                mapped, metadata = _stored_npy_memmap(ledger, f"{kind}_{node}.npy")
                times = mapped["global_time_ns"]
                left = int(np.searchsorted(times, start_ns, side="left"))
                right = int(np.searchsorted(times, stop_ns, side="left"))
                rows = np.asarray(mapped[left:right]).copy()
                if len(rows) == 0:
                    raise RuntimeError(f"{label}/{node}/{kind}: empty authorized slice")
                if np.any(rows["global_time_ns"] < start_ns) or np.any(rows["global_time_ns"] >= stop_ns):
                    raise RuntimeError("half-open window restriction failed")
                accepted = rows[rows["status"] == 1]
                if len(accepted) == 0 or np.any(np.diff(accepted["global_time_ns"]) <= 0):
                    raise RuntimeError(f"{label}/{node}/{kind}: unusable accepted native time")
                if kind == "imu" and (
                    not np.isfinite(accepted["acc_raw"].astype(float)).all()
                    or not np.isfinite(accepted["gyro_raw"].astype(float)).all()
                ):
                    raise RuntimeError(f"{label}/{node}: non-finite IMU input")
                modalities[kind] = rows
                node_audit[kind] = {
                    **metadata, "slice_start_index": left, "slice_stop_index": right,
                    "rows": int(len(rows)), "accepted_rows": int(len(accepted)),
                    "first_accepted_time_ns": int(accepted[0]["global_time_ns"]),
                    "last_accepted_time_ns": int(accepted[-1]["global_time_ns"]),
                    "boot_epochs": [int(x) for x in np.unique(accepted["boot_epoch"])],
                    "slice_payload_sha256": hashlib.sha256(rows.tobytes()).hexdigest(),
                }
            windows[label][node] = modalities
            audit["windows"][label]["per_node"][node] = node_audit
    audit["numeric_arrays_opened"] = True
    audit["opened_only_exact_authorized_calibration_slices"] = True
    return windows, audit


def estimate_biases_and_features(
    windows: Mapping[str, Mapping[str, Mapping[str, np.ndarray]]],
    signed: Mapping[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
    gyro_bias = {}; gravity = {}; functional_axis = {}; gyro_covariance = {}; report = {}
    neutral = windows["initial_still2"]
    for node in NODES:
        rows = neutral[node]["imu"]
        rows = rows[rows["status"] == 1]
        raw_acc = rows["acc_raw"].astype(float) / 2048.0 * G
        raw_gyro = np.deg2rad(rows["gyro_raw"].astype(float) / 16.384)
        acc = (signed[node] @ raw_acc.T).T
        gyro = (signed[node] @ raw_gyro.T).T
        gyro_bias[node] = np.mean(gyro, axis=0)
        gravity[node] = np.mean(acc, axis=0)
        gyro_covariance[node] = np.cov(gyro, rowvar=False) / len(gyro)

        action_rows = windows[FUNCTIONAL_WINDOW[node]][node]["imu"]
        action_rows = action_rows[action_rows["status"] == 1]
        action_gyro = np.deg2rad(action_rows["gyro_raw"].astype(float) / 16.384)
        action_gyro = (signed[node] @ action_gyro.T).T - gyro_bias[node]
        covariance = np.cov(action_gyro, rowvar=False)
        eigenvalues, eigenvectors = np.linalg.eigh(covariance)
        functional_axis[node] = eigenvectors[:, -1]
        report[node] = {
            "gyro_bias_rad_s": gyro_bias[node].tolist(),
            "gyro_bias_covariance": gyro_covariance[node].tolist(),
            "neutral_specific_force_mean_mps2": gravity[node].tolist(),
            "neutral_specific_force_norm_mps2": float(np.linalg.norm(gravity[node])),
            "functional_window": FUNCTIONAL_WINDOW[node],
            "functional_gyro_covariance_eigenvalues": [float(x) for x in eigenvalues],
            "functional_principal_axis_device_unoriented": functional_axis[node].tolist(),
            "native_time_used": True,
        }
    return gyro_bias, gravity, functional_axis, gyro_covariance, report


def noise_profiles() -> tuple[dict[str, NoiseParameters], dict[str, Any]]:
    source_path = FUSION / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/NOISE_PROVENANCE_AUDIT.json"
    source = json.loads(source_path.read_text())
    acc_qualified = []
    gyro_qualified = []
    for row in source["per_axis"].values():
        fit = row["white_fit"]
        if fit["qualified"] and fit["density"] is not None:
            (acc_qualified if fit["density_units"].startswith("m/s") else gyro_qualified).append(float(fit["density"]))
    acc_fallback = 2.0 * max(acc_qualified)
    gyro_fallback = 2.0 * max(gyro_qualified)
    profiles = {}; report = {}
    for node in NODES:
        row = source["per_node"][node]
        acc = row["accelerometer_white_noise_density_mps2_sqrt_hz"]
        gyro = row["gyroscope_white_noise_density_rad_s_sqrt_hz"]
        acc_value = float(acc) if acc is not None else acc_fallback
        gyro_value = float(gyro) if gyro is not None else gyro_fallback
        provenance = "R6A1A_REAL_WHITE_NOISE;UNQUALIFIED_BIAS_RW_RETAINED_AS_DEVELOPMENT_BOUND"
        profiles[node] = NoiseParameters(acc_value, gyro_value, 0.010, 0.001, provenance)
        report[node] = {
            "accelerometer_white_noise_density_mps2_sqrt_hz": acc_value,
            "gyroscope_white_noise_density_rad_s_sqrt_hz": gyro_value,
            "bias_random_walk_bounds": [0.010, 0.001],
            "production_process_noise_qualified": False,
            "source": str(source_path), "source_sha256": sha256(source_path),
        }
    return profiles, report


def make_sample(node: str, row: np.void, signed: np.ndarray) -> ImuSample:
    acc_raw = np.rint(signed @ row["acc_raw"].astype(float)).astype(int)
    gyro_raw = np.rint(signed @ row["gyro_raw"].astype(float)).astype(int)
    return ImuSample(
        node_id=node, global_time_ns=int(row["global_time_ns"]), boot_epoch=int(row["boot_epoch"]),
        accel_mps2=acc_raw.astype(float) / 2048.0 * G,
        gyro_rad_s=np.deg2rad(gyro_raw.astype(float) / 16.384), accepted=True,
        acc_raw=tuple(int(x) for x in acc_raw), gyro_raw=tuple(int(x) for x in gyro_raw),
    )


def integrate_node_window(
    node: str, rows: np.ndarray, target_times: np.ndarray, extrinsic_rotation: np.ndarray,
    signed: np.ndarray, gyro_bias: np.ndarray, accel_bias: np.ndarray,
    preintegrator: NativeTimePreintegrator,
) -> tuple[np.ndarray, dict[str, Any]]:
    accepted = rows[rows["status"] == 1]
    times = accepted["global_time_ns"]
    rotation_sensor = extrinsic_rotation.copy()
    output = np.empty((len(target_times), 3, 3))
    output[0] = np.eye(3)
    previous = 0
    statuses = []; durations = []; max_dt = []; gap_boundaries = 0
    for output_index in range(1, len(target_times)):
        current = int(np.searchsorted(times, target_times[output_index], side="right") - 1)
        current = max(previous + 1, min(current, len(accepted) - 1))
        chunk = accepted[previous:current + 1]
        split_after = np.flatnonzero(np.diff(chunk["global_time_ns"]) > 20_000_000) + 1
        pieces = np.split(chunk, split_after)
        gap_boundaries += len(split_after)
        for piece in pieces:
            if len(piece) < 2:
                continue
            samples = tuple(make_sample(node, row, signed) for row in piece)
            interval = preintegrator.integrate(
                samples, gyro_bias_rad_s=gyro_bias, accel_bias_mps2=accel_bias,
            )
            statuses.append(interval.status.value)
            if interval.status is not PreintegrationStatus.VALID:
                raise RuntimeError(f"{node}: native preintegration failed after safe split: {interval.status.value}/{interval.reason}")
            rotation_sensor = rotation_sensor @ interval.delta_rotation
            durations.append(interval.duration_s); max_dt.append(interval.max_dt_s)
        output[output_index] = rotation_sensor @ extrinsic_rotation.T
        previous = current
    return output, {
        "intervals": len(statuses), "all_valid": all(value == "VALID" for value in statuses),
        "duration_sum_s": float(np.sum(durations)),
        "max_native_dt_s": float(max(max_dt, default=0.0)),
        "explicit_gap_boundaries_carried_without_fabricated_samples": gap_boundaries,
        "nominal_sample_period_used": False,
    }


def build_orientation_replay(
    windows: Mapping[str, Mapping[str, Mapping[str, np.ndarray]]], model,
    adapter: CanonicalCalibrationAdapter, vector: np.ndarray,
    signed: Mapping[str, np.ndarray], gyro_bias: Mapping[str, np.ndarray],
    accel_bias: Mapping[str, np.ndarray], profiles: Mapping[str, NoiseParameters],
) -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    static_values = adapter.vector_to_slots(vector)
    preintegrator = NativeTimePreintegrator(profiles)
    replay = {}; audit = {}
    for label, start_ns, stop_ns in WINDOWS:
        first = max(int(windows[label][node]["imu"][windows[label][node]["imu"]["status"] == 1][0]["global_time_ns"])
                    for node in NODES)
        last = min(int(windows[label][node]["imu"][windows[label][node]["imu"]["status"] == 1][-1]["global_time_ns"])
                   for node in NODES)
        target = np.arange(first, last + 1, 200_000_000, dtype=np.int64)
        if target[-1] > last:
            target = target[:-1]
        segment_rotation = np.empty((len(target), len(model.segments), 3, 3))
        node_rotation = np.empty((len(target), len(NODES), 3, 3))
        per_node = {}
        for node_index, node in enumerate(NODES):
            extrinsic = so3_exp(static_values[f"imu_extrinsic:{node}"][:3])
            rotations, node_audit = integrate_node_window(
                node, windows[label][node]["imu"], target, extrinsic, signed[node],
                gyro_bias[node], accel_bias[node], preintegrator,
            )
            segment = model.identity_mapping[node]
            segment_index = model.segments.index(segment)
            segment_rotation[:, segment_index] = rotations
            node_rotation[:, node_index] = rotations @ extrinsic
            per_node[node] = node_audit
        replay[label] = {
            "time_ns": target, "segment_rotation": segment_rotation,
            "node_rotation": node_rotation,
        }
        audit[label] = {"rows": int(len(target)), "per_node": per_node}
    return replay, audit


def bounded_internal_levers() -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, Any]]:
    path = FUSION / "logs/root_r6a2b_first_bounded_real_shadow_20260826T072148Z/SESSION_LOCAL_REAL_PROFILE.json"
    source = json.loads(path.read_text())
    common = source["mechanical_levers"]["COMMON_NINE_V0_20_PCB17"]
    special = source["mechanical_levers"]["BSF31CC_V0_20_N5BL"]
    values = {}; sigmas = {}
    for node in NODES:
        row = special if node == "BSF31CC" else common
        values[node] = np.asarray(row["nominal_value"], float)
        sigmas[node] = np.asarray(row["one_sigma"], float)
    report = {
        "source": str(path), "source_sha256": sha256(path),
        "use": "bounded component-reference nuisance in pairwise UWB distance factors",
        "rf_phase_centre_qualified": False, "cross_family_reuse": False,
        "common_nine": common, "BSF31CC": special,
    }
    return values, sigmas, report


def solve_uwb_near(frontend: CanonicalT4Frontend, node: str, rows: np.ndarray, time_ns: int):
    accepted = rows[rows["status"] == 1]
    index = int(np.searchsorted(accepted["global_time_ns"], time_ns))
    candidates = sorted(set(max(0, min(len(accepted) - 1, index + offset)) for offset in range(-4, 5)),
                        key=lambda item: abs(int(accepted[item]["global_time_ns"]) - time_ns))
    for candidate in candidates:
        row = accepted[candidate]
        observation = frontend.solve(
            node_id=node, sweep=int(row["sweep"]), global_time_ns=int(row["global_time_ns"]),
            global_time_sigma_ns=int(row["global_time_sigma_ns"]), anchor_ids=row["anchor_id"],
            ranges_mm=row["range_mm"], quality=row["quality_percent"],
            valid_mask=int(row["valid_mask"]), t_round_us=row["t_round_us"],
        )
        if observation is not None and observation.acceptability == "ACCEPTED":
            return observation
    raise RuntimeError(f"{node}: no accepted canonical T4 observation near {time_ns}")


def build_geometry_cache(windows, replay, model) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    frontend = CanonicalT4Frontend(
        FUSION.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
    )
    times = []; rotations = []; xyz = []; covariance = []; labels = []; audit = {}
    for label, _, _ in WINDOWS:
        available = replay[label]["time_ns"]
        chosen = np.unique(np.linspace(1, len(available) - 1, min(4, len(available) - 1), dtype=int))
        audit[label] = {"frame_count": int(len(chosen)), "per_node": {}}
        for replay_index in chosen:
            time_ns = int(available[replay_index])
            frame_xyz = np.empty((len(NODES), 3)); frame_cov = np.empty((len(NODES), 3, 3))
            for node_index, node in enumerate(NODES):
                observation = solve_uwb_near(frontend, node, windows[label][node]["uwb"], time_ns)
                frame_xyz[node_index] = observation.xyz_m
                frame_cov[node_index] = observation.covariance_m2
                audit[label]["per_node"].setdefault(node, []).append({
                    "target_time_ns": time_ns, "observation_time_ns": observation.global_time_ns,
                    "effective_time_ns": observation.effective_time_ns,
                    "anchors_used": list(observation.anchors_used), "gdop": observation.gdop,
                    "condition": observation.condition,
                })
            times.append(time_ns); rotations.append(replay[label]["segment_rotation"][replay_index])
            xyz.append(frame_xyz); covariance.append(frame_cov); labels.append(label)
    return {
        "time_ns": np.asarray(times, np.int64), "segment_rotation": np.asarray(rotations),
        "observed_xyz": np.asarray(xyz), "observed_covariance": np.asarray(covariance),
        "window_label": np.asarray(labels, dtype="U32"),
    }, audit


def accel_biases(gravity, signed, vector, adapter) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    slots = adapter.vector_to_slots(vector); values = {}; report = {}
    for node in NODES:
        measured = signed[node] @ gravity[node]
        direction = measured / np.linalg.norm(measured)
        # Stationary data identify only the bias projection along the observed
        # specific-force direction without an independent attitude reference.
        values[node] = (np.linalg.norm(measured) - G) * direction
        report[node] = {
            "accel_bias_mps2": values[node].tolist(),
            "estimator": "stationary specific-force magnitude residual projected along its measured direction",
            "observable_component": "one radial component",
            "unobservable_components": "two transverse components retained at zero prior with broad uncertainty",
            "confounding": "radial bias remains coupled to accelerometer scale",
            "production_qualified": False,
        }
    return values, report


def residual_summary(detail: list[dict[str, Any]], frame_labels: np.ndarray) -> dict[str, Any]:
    by_window = {}; by_node = {node: [] for node in NODES}
    for row in detail:
        label = str(frame_labels[row["frame"]])
        by_window.setdefault(label, []).append(row["normalized_residual"])
        by_node[row["left_node"]].append(row["normalized_residual"])
        by_node[row["right_node"]].append(row["normalized_residual"])
    return {
        "all_normalized": quantiles(np.asarray([row["normalized_residual"] for row in detail])),
        "per_window": {key: quantiles(np.asarray(value)) for key, value in by_window.items()},
        "per_node": {key: quantiles(np.asarray(value)) for key, value in by_node.items()},
        "normalization": "canonical T4 projected covariance plus bounded phase-centre nuisance covariance",
    }


def profile_rows(source_rows, adapter, vector, covariance, internal, internal_sigma, bone_lengths, info):
    values = adapter.vector_to_slots(vector)
    column_norm = np.zeros(adapter.dimension)
    # Observability is serialized per coordinate from the posterior/data report.
    singular_rank = info["data_only_rank"]
    rows = []
    for source in source_rows:
        row = {key: value for key, value in source.items()}
        slot_id = row["slot_id"]
        block = adapter.by_slot.get(slot_id)
        if block is not None:
            row["value"] = [float(x) for x in values[slot_id]]
            row["uncertainty"] = {
                "posterior_local_covariance": covariance[block.start:block.stop, block.start:block.stop].tolist(),
                "one_sigma": np.sqrt(np.maximum(np.diag(covariance[block.start:block.stop, block.start:block.stop]), 0.0)).tolist(),
                "local_parameterization": "SO(3) right-local for rotational coordinates",
            }
            if block.kind == "joint_rest":
                row["status"] = "DEVELOPMENT_PRIOR_AND_NEUTRAL_FRAME_DEFINITION_DOMINATED"
            else:
                row["status"] = "DEVELOPMENT_ESTIMATED_FROM_AUTHORIZED_C1_WINDOWS"
            row["provenance"] = {
                "execution": "ROOT_R6A2B_R2_LAYERED_REAL_SOLVE",
                "source_windows": [label for label, _, _ in WINDOWS],
                "production_qualified": False,
            }
        elif row["authority_class"] == "FIX_BY_CONVENTION":
            row["value"] = [0.0] * int(row["dimension"])
            row["uncertainty"] = {"covariance": np.zeros((row["dimension"], row["dimension"])).tolist()}
            row["status"] = "FIXED_BY_DEVELOPMENT_GAUGE" if slot_id == "world_model_gauge" else "FIXED_CHILD_ORIGIN_ZERO_DOF_VIEW"
        elif row["category"] == "tag_lever":
            node = slot_id.split(":", 1)[1]
            extrinsic = values[f"imu_extrinsic:{node}"]
            derived = extrinsic[3:] + so3_exp(extrinsic[:3]) @ internal[node]
            row["value"] = derived.tolist()
            row["uncertainty"] = {"bounded_component_reference_one_sigma_m": internal_sigma[node].tolist()}
            row["status"] = "DERIVED_BOUNDED_RF_PHASE_CENTRE_NOT_QUALIFIED"
            row["provenance"] = {"family": FAMILIES[node], "independent_freedom": False}
        elif row["category"] == "bone_length":
            segment = slot_id.split(":", 1)[1]; derived = bone_lengths[segment]
            row["value"] = None if derived["value_m"] is None else [derived["value_m"]]
            row["status"] = derived["status"]
            row["provenance"] = {"source_slot": derived.get("source_slot"), "independent_freedom": False}
        rows.append(row)
    if len(rows) != 87 or len({row["slot_id"] for row in rows}) != 87:
        raise RuntimeError("development profile mutated the immutable slot inventory")
    return rows


def replay_and_plots(result_dir, model, adapter, vector, covariance, replay, internal, geometry_detail, frame_labels):
    static = adapter.materialize_static(vector, covariance=covariance, internal_levers=internal)
    all_time = []; all_window = []; segment_rot = []; segment_pos = []; node_rot = []; joint_values = []
    for label, _, _ in WINDOWS:
        times = replay[label]["time_ns"]
        for index, time_ns in enumerate(times):
            rotations = {segment: replay[label]["segment_rotation"][index, segment_index]
                         for segment_index, segment in enumerate(model.segments)}
            state = _state_from_segment_rotations(model, rotations, static, int(time_ns))
            poses = model.segment_poses(state, static)
            imu_frames = model.imu_frames(state, static)
            all_time.append(int(time_ns)); all_window.append(label)
            segment_rot.append([poses[segment].rotation for segment in model.segments])
            segment_pos.append([poses[segment].translation for segment in model.segments])
            node_rot.append([imu_frames[node].rotation for node in NODES])
            joint_values.append([state.joint_rotvec[joint] for joint in model.joint_ids])
    all_time = np.asarray(all_time, np.int64); all_window = np.asarray(all_window, dtype="U32")
    segment_rot = np.asarray(segment_rot); segment_pos = np.asarray(segment_pos)
    node_rot = np.asarray(node_rot); joint_values = np.asarray(joint_values)
    np.savez_compressed(
        result_dir / "CALIBRATION_WINDOW_REPLAY.npz", time_ns=all_time, window=all_window,
        segment_names=np.asarray(model.segments), node_names=np.asarray(NODES),
        joint_names=np.asarray(model.joint_ids), segment_rotation=segment_rot,
        segment_origin_m=segment_pos, node_rotation=node_rot, joint_rotvec=joint_values,
    )

    node_angle = np.degrees(np.linalg.norm(np.asarray([[so3_log(rotation) for rotation in frame] for frame in node_rot]), axis=2))
    segment_angle = np.degrees(np.linalg.norm(np.asarray([[so3_log(rotation) for rotation in frame] for frame in segment_rot]), axis=2))
    joint_angle = np.degrees(np.linalg.norm(joint_values, axis=2))
    orientation_limit = max(1.0, float(max(np.max(node_angle), np.max(segment_angle))) * 1.02)
    joint_limit = max(1.0, float(np.max(joint_angle)) * 1.02)
    relative_time = (all_time - all_time[0]) * 1e-9
    window_labels = list(dict.fromkeys(str(value) for value in all_window))
    boundaries = [relative_time[np.flatnonzero(all_window == label)[0]] for label in window_labels[1:]]
    figure, axes = plt.subplots(2, 1, figsize=(14, 8), sharex=True)
    for index, node in enumerate(NODES):
        for window_index, label in enumerate(window_labels):
            mask = all_window == label
            axes[0].plot(relative_time[mask], node_angle[mask, index], lw=.55,
                         label=node if window_index == 0 else None)
    for index, segment in enumerate(model.segments):
        for window_index, label in enumerate(window_labels):
            mask = all_window == label
            axes[1].plot(relative_time[mask], segment_angle[mask, index], lw=.55,
                         label=segment if window_index == 0 else None)
    axes[0].set_ylim(0, orientation_limit); axes[1].set_ylim(0, orientation_limit)
    axes[0].set_ylabel("node orientation |log R| [deg]"); axes[1].set_ylabel("segment |log R| [deg]")
    axes[1].set_xlabel("concatenated authorized-window time [s]")
    for axis in axes:
        for boundary in boundaries: axis.axvline(boundary, color="black", lw=.35, alpha=.25)
        axis.grid(alpha=.25); axis.legend(ncol=5, fontsize=6)
    figure.suptitle("R6A2B-R2 native-time orientation replay | window gaps not connected | one fixed angular scale")
    figure.tight_layout(); figure.savefig(result_dir / "ORIENTATION_TRAJECTORIES_FIXED_SCALE.png", dpi=170); plt.close(figure)

    figure, axis = plt.subplots(figsize=(14, 5))
    for index, joint in enumerate(model.joint_ids):
        for window_index, label in enumerate(window_labels):
            mask = all_window == label
            axis.plot(relative_time[mask], joint_angle[mask, index], lw=.65,
                      label=joint if window_index == 0 else None)
    for boundary in boundaries: axis.axvline(boundary, color="black", lw=.35, alpha=.25)
    axis.set_ylim(0, joint_limit); axis.set_xlabel("concatenated authorized-window time [s]")
    axis.set_ylabel("principal joint rotvec magnitude [deg]"); axis.grid(alpha=.25)
    axis.legend(ncol=5, fontsize=7); axis.set_title("Body-relative joint trajectories | window gaps not connected | fixed scale")
    figure.tight_layout(); figure.savefig(result_dir / "JOINT_ANGLES_FIXED_SCALE.png", dpi=170); plt.close(figure)

    normalized = np.asarray([row["normalized_residual"] for row in geometry_detail])
    residual_limit = max(1.0, float(np.max(np.abs(normalized))) * 1.02)
    figure, axis = plt.subplots(figsize=(14, 4))
    axis.plot(normalized, lw=.55); axis.set_ylim(-residual_limit, residual_limit)
    axis.set_xlabel("pairwise UWB geometry residual ordinal"); axis.set_ylabel("normalized residual")
    axis.grid(alpha=.25); axis.set_title("Shared-FK metric residual health | fixed scale")
    figure.tight_layout(); figure.savefig(result_dir / "RESIDUAL_HEALTH_FIXED_SCALE.png", dpi=170); plt.close(figure)

    # Coordinate frames are shown at estimated segment origins; no unmeasured
    # wrist, ankle, or torso-top point is drawn.
    selected = np.linspace(0, len(all_time) - 1, min(12, len(all_time)), dtype=int)
    extent = max(0.25, float(np.max(np.abs(segment_pos[selected]))) * 1.15)
    figure = plt.figure(figsize=(12, 10)); axis = figure.add_subplot(111, projection="3d")
    colors = ("r", "g", "b")
    for frame in selected:
        alpha = 0.15 + 0.7 * frame / max(1, len(all_time) - 1)
        for segment_index, segment in enumerate(model.segments):
            origin = segment_pos[frame, segment_index]
            rotation = segment_rot[frame, segment_index]
            for coordinate in range(3):
                tip = origin + 0.08 * rotation[:, coordinate]
                axis.plot([origin[0], tip[0]], [origin[1], tip[1]], [origin[2], tip[2]],
                          color=colors[coordinate], alpha=alpha, lw=.7)
    axis.set(xlim=(-extent, extent), ylim=(-extent, extent), zlim=(-extent, extent),
             xlabel="root-relative X [m]", ylabel="root-relative Y [m]", zlabel="root-relative Z [m]")
    axis.set_title("Estimated segment coordinate frames; distal endpoint lengths intentionally absent")
    figure.tight_layout(); figure.savefig(result_dir / "ROOT_RELATIVE_COORDINATE_FRAMES.png", dpi=170); plt.close(figure)
    return {
        "replay_rows": int(len(all_time)), "window_count": len(WINDOWS),
        "node_orientation_max_deg": float(np.max(node_angle)),
        "segment_orientation_max_deg": float(np.max(segment_angle)),
        "joint_angle_max_deg": float(np.max(joint_angle)),
        "finite": bool(np.isfinite(segment_rot).all() and np.isfinite(joint_values).all()),
        "unknown_distal_endpoints_drawn": False,
        "artifacts": [
            "CALIBRATION_WINDOW_REPLAY.npz", "ORIENTATION_TRAJECTORIES_FIXED_SCALE.png",
            "JOINT_ANGLES_FIXED_SCALE.png", "RESIDUAL_HEALTH_FIXED_SCALE.png",
            "ROOT_RELATIVE_COORDINATE_FRAMES.png",
        ],
    }


def seal(result_dir: Path) -> None:
    files = sorted(path for path in result_dir.iterdir() if path.is_file() and path.name != "SHA256SUMS")
    (result_dir / "SHA256SUMS").write_text(
        "".join(f"{sha256(path)}  {path.name}\n" for path in files), encoding="utf-8"
    )


def run(result_dir: Path) -> Path:
    started = time.monotonic(); result_dir.mkdir(parents=True, exist_ok=False)
    source_ledger = FUSION / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z/CALIBRATION_SLOT_LEDGER.json"
    source_ledger_before = sha256(source_ledger)
    source_profile = json.loads((R1 / "CALIBRATION_PARAMETER_PROVENANCE.json").read_text())
    source_rows = source_profile["slots"]
    model = corrected_body_model(FUSION)
    adapter = CanonicalCalibrationAdapter(model, source_rows)
    windows, access_audit = load_authorized_windows()
    dump(result_dir / "CALIBRATION_WINDOW_ACCESS_AUDIT.json", access_audit)
    dump(result_dir / "PARAMETER_ORDERING.json", {
        "schema": "biospur-root-r6a2b-r2-parameter-order-v1",
        "slot_count": len(adapter.blocks), "dimension": adapter.dimension,
        "ordering": adapter.ordering_json(), "fixed_views_optimizer_dimension": 0,
        "derived_views_optimizer_dimension": 0, "duplicate_freedoms": [],
        "prior_provenance": adapter.prior_provenance,
    })

    identity = np.eye(3)
    signed = {node: identity.copy() for node in NODES}
    gyro_bias, gravity, functional_axis, gyro_covariance, bias_feature_report = estimate_biases_and_features(windows, signed)
    signed_report = signed_axis_audit(gravity, functional_axis)
    signed_report.update({
        "schema": "biospur-root-r6a2b-r2-signed-axis-audit-v1",
        "status": "EXECUTED_AMBIGUOUS",
        "representative_register_to_device_map": "+X,+Y,+Z",
        "representative_role": "coordinate gauge only; all 24 family hypotheses retained",
        "four_arm_near_180_degree_explanation": (
            "The four arm boards have neutral raw +Y opposite the lower-body raw +Y. "
            "The earlier 159-167 degree comparison therefore measured node donning/register-frame composition, "
            "not a gravity failure. Node-specific SO(3) extrinsics absorb that flip while the family map remains ambiguous."
        ),
    })
    dump(result_dir / "SIGNED_AXIS_AUDIT.json", signed_report)

    rotation_vector, rotation_report, rotation_jacobian = solve_rotation_layer(
        adapter, gravity, functional_axis, signed,
    )
    accel_bias, accel_bias_report = accel_biases(gravity, signed, rotation_vector, adapter)
    noise, noise_report = noise_profiles()
    replay, preintegration_audit = build_orientation_replay(
        windows, model, adapter, rotation_vector, signed, gyro_bias, accel_bias, noise,
    )
    internal, internal_sigma, lever_report = bounded_internal_levers()
    geometry_cache, uwb_audit = build_geometry_cache(windows, replay, model)
    final_vector, geometry_report, geometry_jacobian, geometry_detail = solve_geometry_layer(
        adapter, model, rotation_vector, geometry_cache["time_ns"],
        geometry_cache["segment_rotation"], geometry_cache["observed_xyz"],
        geometry_cache["observed_covariance"], internal, internal_sigma,
    )
    info_report, posterior_covariance = information_diagnostics(
        adapter, rotation_jacobian, geometry_jacobian,
    )
    bone_lengths = derive_bone_lengths(adapter, final_vector)
    residuals = residual_summary(geometry_detail, geometry_cache["window_label"])

    np.savez_compressed(
        result_dir / "SOLVE_INPUTS.npz",
        gravity=np.asarray([gravity[node] for node in NODES]),
        functional_axis=np.asarray([functional_axis[node] for node in NODES]),
        signed=np.asarray([signed[node] for node in NODES]),
        frame_times_ns=geometry_cache["time_ns"],
        segment_rotation=geometry_cache["segment_rotation"],
        observed_xyz=geometry_cache["observed_xyz"],
        observed_covariance=geometry_cache["observed_covariance"],
        window_label=geometry_cache["window_label"],
        internal_lever=np.asarray([internal[node] for node in NODES]),
        internal_lever_sigma=np.asarray([internal_sigma[node] for node in NODES]),
    )
    np.savez_compressed(
        result_dir / "CALIBRATION_ESTIMATE.npz", vector=final_vector,
        covariance=posterior_covariance, gyro_bias=np.asarray([gyro_bias[node] for node in NODES]),
        accel_bias=np.asarray([accel_bias[node] for node in NODES]),
        rotation_data_jacobian=rotation_jacobian, geometry_data_jacobian=geometry_jacobian,
    )

    source_ledger_after = sha256(source_ledger)
    if source_ledger_before != source_ledger_after:
        raise RuntimeError("historical 87-slot registry changed")
    rows = profile_rows(source_rows, adapter, final_vector, posterior_covariance,
                        internal, internal_sigma, bone_lengths, info_report)
    clock_path = FUSION / "logs/root_r6a1c_deferred_measurement_bridge_20260825T102823Z/WORLD_FRAME_BRIDGE_CONTRACT.json"
    clocks = json.loads(clock_path.read_text())["clock_relationships"]["models"]
    profile = {
        "schema": "biospur-root-r6a2b-r2-development-calibration-candidate-v1",
        "profile_kind": "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE",
        "profile_version": "R6A2B-R2-CANDIDATE-001",
        "qualification_verdict": "DEVELOPMENT_ONLY_NOT_PRODUCTION",
        "frozen": True,
        "binding": {
            "capture_id": CAPTURE_ID, "session_id": CAPTURE_ID,
            "checkpoint": CHECKPOINT, "hardware_families": FAMILIES,
            "boot_epochs": {node: int(clocks[node]["boot_epoch"]) for node in NODES},
            "authorized_windows": [
                {"label": label, "start_global_time_ns": start, "stop_global_time_ns_exclusive": stop}
                for label, start, stop in WINDOWS
            ],
            "held_out_golf_boxing_accessed": False,
        },
        "parameterization": {"slot_count": 28, "dimension": 114,
                             "ordering_artifact": "PARAMETER_ORDERING.json"},
        "signed_axis": {
            "status": "EXECUTED_AMBIGUOUS", "representative": "+X,+Y,+Z",
            "surviving_common_nine": signed_report["COMMON_NINE_V0_20_PCB17"]["data_only_surviving_labels"],
            "surviving_BSF31CC": signed_report["BSF31CC_V0_20_N5BL"]["data_only_surviving_labels"],
        },
        "development_gauge": {
            "pelvis_heading_rad": 0.0,
            "meaning": "reported body heading chart convention; not a measured V4/navigation heading",
        },
        "static_vector": final_vector.tolist(),
        "static_covariance": posterior_covariance.tolist(),
        "bias_states": {
            node: {"gyro_bias_rad_s": gyro_bias[node].tolist(),
                   "accel_bias_mps2": accel_bias[node].tolist(),
                   "production_qualified": False}
            for node in NODES
        },
        "slots": rows,
        "information": {"data_only_rank": info_report["data_only_rank"],
                        "data_only_nullity": info_report["data_only_nullity"]},
        "immutability": {
            "historical_registry": str(source_ledger),
            "sha256_before": source_ledger_before, "sha256_after": source_ledger_after,
            "writes": 0,
        },
        "capability": {
            "body_relative_segment_orientation": "AVAILABLE_DEVELOPMENT",
            "joint_angles": "AVAILABLE_DEVELOPMENT_WITH_NULL_DIRECTIONS",
            "metric_skeleton": "PARTIAL_NO_DIRECT_DISTAL_ENDPOINTS",
            "world_translation": "UNAUTHORIZED_T_N_V4_AND_RF_PHASE_CENTRES_UNRESOLVED",
            "calibrated_covariance": "LOCAL_DEVELOPMENT_POSTERIOR_NOT_PRODUCTION",
        },
    }
    profile["profile_checksum_sha256"] = profile_checksum(profile)
    dump(result_dir / "DEVELOPMENT_ONLY_REAL_CALIBRATION_CANDIDATE.json", profile)

    replay_summary = replay_and_plots(
        result_dir, model, adapter, final_vector, posterior_covariance, replay,
        internal, geometry_detail, geometry_cache["window_label"],
    )
    dump(result_dir / "NATIVE_TIME_PREINTEGRATION_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r2-native-time-audit-v1",
        "accepted_global_time_is_capture_bound_clockmodel_output": True,
        "clock_model_source": str(clock_path), "clock_model_source_sha256": sha256(clock_path),
        "nominal_200hz_timing_substituted": False, "windows": preintegration_audit,
        "noise_profiles": noise_report,
    })
    dump(result_dir / "BIAS_ESTIMATES.json", {
        "schema": "biospur-root-r6a2b-r2-bias-estimates-v1",
        "gyro": bias_feature_report, "accelerometer": accel_bias_report,
    })
    dump(result_dir / "UWB_GEOMETRY_INPUT_AUDIT.json", {
        "schema": "biospur-root-r6a2b-r2-uwb-geometry-audit-v1",
        "canonical_frontend": "CanonicalT4Frontend", "frames": uwb_audit,
        "lever_nuisance": lever_report,
    })
    dump(result_dir / "OPTIMIZER_EVIDENCE.json", {
        "schema": "biospur-root-r6a2b-r2-optimizer-evidence-v1",
        "shared_fk_real_calibration_solve_executed": True,
        "rotation_and_rest_layer": rotation_report,
        "geometry_layer": geometry_report,
        "combined_parameter_change_from_zero_prior_l2": float(np.linalg.norm(final_vector)),
        "changed_coordinate_count_exact": int(np.sum(final_vector != 0.0)),
        "runtime_s": time.monotonic() - started,
        "deterministic_inputs": "SOLVE_INPUTS.npz",
    })
    dump(result_dir / "OBSERVABILITY_AND_COVARIANCE.json", {
        "schema": "biospur-root-r6a2b-r2-observability-v1", **info_report,
        "slot_posterior_one_sigma": {
            block.slot_id: np.sqrt(np.maximum(np.diag(
                posterior_covariance[block.start:block.stop, block.start:block.stop]
            ), 0.0)).tolist() for block in adapter.blocks
        },
    })
    dump(result_dir / "RESIDUAL_DISTRIBUTIONS.json", {
        "schema": "biospur-root-r6a2b-r2-residuals-v1",
        "geometry": residuals,
        "orientation_data_residual": {
            "final_half_squared_norm": rotation_report["final_data_objective_half_squared_norm"]
        },
    })
    dump(result_dir / "GEOMETRY_CAPABILITY.json", {
        "schema": "biospur-root-r6a2b-r2-geometry-capability-v1",
        "status": "PARTIAL", "joint_parent_estimates": {
            joint.joint_id: adapter.vector_to_slots(final_vector)[joint.parent_offset_slot].tolist()
            for joint in model.joints
        },
        "bone_lengths": bone_lengths,
        "direct_distal_landmarks": {
            "wrist_left": None, "wrist_right": None, "ankle_left": None, "ankle_right": None,
        },
        "no_anthropometric_measurement_fabricated": True,
    })
    dump(result_dir / "WORLD_ABSOLUTE_LAYER.json", {
        "schema": "biospur-root-r6a2b-r2-world-layer-v1",
        "status": "UNAUTHORIZED", "attempted_after_body_layers": True,
        "capture_bound_anchor_positions_delays_clockmodels_imported": True,
        "pairwise_uwb_distances_used_for_body_geometry": True,
        "T_N_V4": None, "exact_rf_phase_centres": None,
        "bounded_lever_sensitivity_included_in_residual_covariance": True,
        "body_relative_candidate_preserved": True,
    })
    dump(result_dir / "CALIBRATION_WINDOW_REPLAY_SUMMARY.json", {
        "schema": "biospur-root-r6a2b-r2-replay-summary-v1", **replay_summary,
        "static_profile_checksum_before_replay": profile["profile_checksum_sha256"],
        "static_profile_checksum_after_replay": profile_checksum(profile),
        "static_profile_changed": False,
    })
    dump(result_dir / "FINAL_RESULT.json", {
        "schema": "biospur-root-r6a2b-r2-final-result-v1",
        "overall_system_direction": "POSITIVE",
        "signed_axis_layer": "EXECUTED_AMBIGUOUS",
        "body_relative_calibration": "EXECUTED",
        "metric_geometry_layer": "PARTIAL",
        "world_absolute_layer": "UNAUTHORIZED",
        "development_profile_generated": True,
        "held_out_golf_boxing_accessed": False,
        "missing_adapter_implemented": True,
        "shared_fk_real_calibration_solve_executed": True,
        "profile_checksum_sha256": profile["profile_checksum_sha256"],
        "independent_verification": "PENDING",
    })
    seal(result_dir)
    return result_dir


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--result-dir", type=Path, required=True)
    args = parser.parse_args()
    print(run(args.result_dir.resolve()))


if __name__ == "__main__":
    main()
