#!/usr/bin/env python3
"""Build native-200 Action00 A/B from held-out real ten-node raw UWB."""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

import numpy as np

from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    Native200PublicationProducer,
    accepted_pose_frame,
)
from biospur_fusion.c2_coupled_progressive.calibration_native200_archive import (
    CalibrationNative200Archive,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
from biospur_fusion.c2_uwb_root_world.action00_session_offsets import (
    StaticSessionOffsetProfile,
    robust_corrected_epoch_consensus,
)
from biospur_fusion.c2_uwb_root_world.root_correction_slew import (
    CausalRootCorrectionSlew,
)
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    PositionObservation,
    RootFilterConfig,
)
from biospur_fusion.root_r3.replay import initial_state
from tools.build_c2_action00_imu_uwb_diagnostic import (
    ACTION as ACTION00,
    JOINT_NAMES,
    LINES,
    _scene,
)
from tools.build_c2_hxx_fusion_ab_viewer import _html


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _profile(document: dict) -> StaticSessionOffsetProfile:
    value = document["profile"]
    return StaticSessionOffsetProfile(
        document["training_start_s"], document["training_stop_s_exclusive"],
        value["common_root_gauge_m"], value["offsets_by_node_m"],
        value["samples_by_node"], 30, value["provenance"], value["digest"],
    )


def _observations(
    facts_path: Path,
    profile: StaticSessionOffsetProfile,
    *,
    exclude_profile_training_prefix: bool,
):
    observations = []
    audits = []
    for line in facts_path.read_text().splitlines():
        row = json.loads(line)
        time_s = float(row["reference_time_s"])
        if exclude_profile_training_prefix and time_s < profile.training_stop_s:
            continue
        candidates = {
            node["node"]: node["candidate_root_world_m"]
            for node in row["nodes"] if node["direct_usable"]
        }
        try:
            consensus = robust_corrected_epoch_consensus(profile, candidates)
        except ValueError:
            continue
        retained = np.asarray([
            consensus.corrected_positions_m[node] for node in consensus.trusted_nodes
        ])
        spread = np.linalg.norm(retained - consensus.root_position_m, axis=1)
        sigma = max(0.12, float(np.median(spread)))
        observations.append(PositionObservation(
            time_s, time_s, consensus.root_position_m,
            np.eye(3) * sigma**2, "C2_BODY_CONSENSUS",
            tuple(range(8)), "HELDOUT_SESSION_OFFSET_ROBUST_CONSENSUS",
            True, True, int(row["epoch_sequence"]),
        ))
        audits.append({
            "epoch_sequence": row["epoch_sequence"],
            "time_s": time_s,
            "position_m": consensus.root_position_m.tolist(),
            "trusted_nodes": list(consensus.trusted_nodes),
            "rejected_nodes": list(consensus.rejected_nodes),
            "cutoff_m": consensus.cutoff_m,
            "robust_scale_m": consensus.robust_scale_m,
            "covariance_sigma_m": sigma,
        })
    if len(observations) < 20:
        raise RuntimeError("insufficient held-out UWB consensus observations")
    return observations, audits


def _run_roots(producer, source, observations):
    clock = producer._clocks[ACTION00]
    global_ns = np.asarray([
        clock.global_ns(int(timer)) for timer in source.time_us
    ], dtype=np.int64)
    absolute_s = global_ns.astype(float) * 1e-9
    first_obs = observations[0]
    start = max(0, int(np.searchsorted(
        absolute_s, first_obs.measurement_time_s, side="right",
    )) - 1)
    p0 = first_obs.root_position_m.copy()
    config = RootFilterConfig(
        inertial_acceleration_noise_mps2_sqrt_hz=0.30,
        accelerometer_bias_rw_mps3_sqrt_hz=0.003,
        nis_limit_3d=100.0,
        maximum_position_influence_m=0.05,
        fixed_lag_s=0.20,
        uwb_stale_s=0.60,
        uwb_dropout_s=1.20,
        recovery_good_events=3,
    )
    pure = CausalDelayedRootFilter(initial_state(absolute_s[start], p0), config, inertial=True)
    fused = CausalDelayedRootFilter(initial_state(absolute_s[start], p0), config, inertial=True)
    publication_slew = CausalRootCorrectionSlew(
        release_period_s=0.12,
        maximum_correction_m=config.maximum_position_influence_m,
    )
    publication_slew.sample(
        float(absolute_s[start]), fused.current_state.position_m,
        fused.current_state.velocity_mps,
    )
    rotations = np.asarray([
        producer._alignment @ rotation_from_wxyz(value)
        for value in source.sensor_quat_wxyz
    ])
    frames = []
    roots_a = []
    roots_b = []
    decisions = []
    cursor = 0
    while cursor < len(observations) and observations[cursor].measurement_time_s <= absolute_s[start]:
        cursor += 1
    for frame in range(start + 1, len(absolute_s)):
        sample = ImuSample(
            float(absolute_s[frame]), float(absolute_s[frame]),
            source.calibrated_acc_mps2[frame], rotations[frame], frame,
        )
        if not pure.add_imu(sample) or not fused.add_imu(sample):
            raise RuntimeError(f"native-200 IMU rejected at frame {frame}")
        while cursor < len(observations) and observations[cursor].measurement_time_s <= absolute_s[frame] + 1e-12:
            original = observations[cursor]
            observation = PositionObservation(
                original.measurement_time_s, float(absolute_s[frame]),
                original.root_position_m, original.covariance_m2,
                original.tag_id, original.anchors, original.quality_state,
                original.frame_valid, original.physical_point_valid,
                original.source_sequence,
            )
            decision = fused.add_position(
                observation, processing_time_s=float(absolute_s[frame]),
            )
            if decision.accepted:
                publication_slew.install(
                    float(absolute_s[frame]),
                    decision.availability_applied_position_delta_m,
                )
            decisions.append({
                "source_sequence": observation.source_sequence,
                "measurement_time_s": observation.measurement_time_s,
                "availability_time_s": observation.availability_time_s,
                "accepted": bool(decision.accepted),
                "reason": decision.reason,
                "nis": None if decision.nis is None else float(decision.nis),
                "applied_delta_m": decision.applied_position_delta_m.tolist(),
                "availability_applied_delta_m": (
                    decision.availability_applied_position_delta_m.tolist()
                ),
            })
            cursor += 1
        published = publication_slew.sample(
            float(absolute_s[frame]), fused.current_state.position_m,
            fused.current_state.velocity_mps,
        )
        frames.append(frame)
        roots_a.append(pure.current_state.position_m.copy())
        roots_b.append(published.position_m.copy())
    return (
        np.asarray(frames, dtype=int), absolute_s[np.asarray(frames)] - absolute_s[start],
        np.asarray(roots_a), np.asarray(roots_b), decisions, p0,
    )


def _panel(producer, source, action, source_frames, times_s, roots, scene):
    keep = np.arange(0, len(source_frames), 3, dtype=int)
    if keep[-1] != len(source_frames) - 1:
        keep = np.r_[keep, len(source_frames) - 1]
    rendered = []
    for local in keep:
        frame = int(source_frames[local])
        _, points, _ = accepted_pose_frame(
            source.trajectory, frame,
            producer._alignment, producer._geometry,
        )
        rendered.append([
            round(float(value), 5)
            for name in JOINT_NAMES for value in points[name] + roots[local]
        ])
    index = {name: number for number, name in enumerate(JOINT_NAMES)}
    return {
        "jointNames": list(JOINT_NAMES),
        "lines": [[index[a], index[b], side] for a, b, side in LINES],
        "viewGauge": {"frontYawRad": 0.0, "outputCoordinateParity": 1.0},
        "episode": {
            "id": action,
            "instruction": (
                "Action00 后缀盲测：前8秒只用于节点相对偏置初始化。"
                if action == ACTION00 else
                "节点相对偏置仅来自 Action00 前8秒；本动作没有重拟合。"
            ),
            "note": "同一原生200 Hz FK/IK；右侧仅加入十节点raw-UWB共识根校正。",
            "time": np.round(times_s[keep], 4).tolist(),
            "sourceFrame": source_frames[keep].tolist(),
            "frames": rendered,
        },
        "uwbScene": scene,
    }


def _metrics(root: np.ndarray, p0: np.ndarray) -> dict:
    displacement = np.linalg.norm(root - p0, axis=1)
    steps = np.linalg.norm(np.diff(root, axis=0), axis=1)
    return {
        "endpoint_displacement_m": float(displacement[-1]),
        "maximum_displacement_m": float(displacement.max()),
        "rms_displacement_m": float(np.sqrt(np.mean(displacement**2))),
        "maximum_native200_step_m": float(steps.max()),
        "p99_native200_step_m": float(np.quantile(steps, 0.99)),
    }


def run(
    facts_path: Path,
    profile_path: Path,
    output: Path,
    *,
    action: str = ACTION00,
) -> dict:
    output = output.resolve()
    if output.exists() or ROOT not in output.parents:
        raise ValueError("output must be a new directory under Fusion_Part")
    output.mkdir(parents=False)
    profile_doc = json.loads(profile_path.read_text())
    profile = _profile(profile_doc)
    observations, observation_audit = _observations(
        facts_path, profile,
        exclude_profile_training_prefix=action == ACTION00,
    )
    producer = Native200PublicationProducer.from_sealed_archives()
    archive = CalibrationNative200Archive.from_sealed_archives()
    if action not in archive.actions:
        raise ValueError("action is not in the acquired calibration inventory")
    source = archive.actions[action]
    frames, times_s, roots_a, roots_b, decisions, p0 = _run_roots(
        producer, source, observations,
    )
    metrics_a = _metrics(roots_a, p0)
    metrics_b = _metrics(roots_b, p0)
    scene = _scene()
    audit = {
        "action": action,
        "same_elapsed_time_playhead": True,
        "same_world_camera": True,
        "same_uwb_volume": True,
        "pose_contract": "IDENTICAL_NATIVE200_FK_IK_ROOT_TRANSLATION_ONLY",
    }
    accepted = sum(row["accepted"] for row in decisions)
    viewer = output / f"c2_{action}_pure_imu_vs_raw_ten_node_uwb_ab.html"
    viewer.write_text(_html(
        _panel(producer, source, action, frames, times_s, roots_a, scene),
        _panel(producer, source, action, frames, times_s, roots_b, scene),
        audit,
        left_label="A：纯 IMU（原生 200 Hz）",
        right_label="B：同一 IMU + 十节点 raw-UWB 共识",
        left_drift=f"末端漂移 {metrics_a['endpoint_displacement_m']:.2f} m",
        right_drift=(
            f"末端偏移 {metrics_b['endpoint_displacement_m']:.2f} m · "
            f"UWB {accepted}/{len(decisions)} 接受"
        ),
        render_style="skeleton",
    ), encoding="utf-8")
    np.savez_compressed(
        output / "ROOT_TRAJECTORIES.npz", time_s=times_s,
        source_frame=frames, pure_imu_root_m=roots_a,
        raw_ten_node_imu_uwb_root_m=roots_b, initial_root_m=p0,
    )
    (output / "UWB_OBSERVATIONS.json").write_text(json.dumps(
        observation_audit, indent=2, sort_keys=True,
    ) + "\n")
    (output / "UWB_DECISIONS.json").write_text(json.dumps(
        decisions, indent=2, sort_keys=True,
    ) + "\n")
    result = {
        "schema": "biospur.c2.calibration.raw_ten_node_consensus_ab.v1",
        "status": "CALIBRATION_ACTION_AB_COMPLETE",
        "action": action,
        "product_ready": False,
        "scientific_pass": False,
        "native200_frames": len(frames),
        "duration_s": float(times_s[-1]),
        "uwb_observations": len(observations),
        "uwb_decisions": len(decisions),
        "uwb_accepted": accepted,
        "uwb_rejected": len(decisions) - accepted,
        "decision_reasons": dict(Counter(row["reason"] for row in decisions)),
        "pure_imu": metrics_a,
        "imu_plus_raw_ten_node_uwb": metrics_b,
        "maximum_displacement_suppression_fraction": (
            1.0 - metrics_b["maximum_displacement_m"]
            / metrics_a["maximum_displacement_m"]
        ),
        "profile_digest": profile.digest,
        "facts_sha256": _sha256(facts_path),
        "profile_result_sha256": _sha256(profile_path),
        "limitations": [
            "session-relative offsets conflate stable node disagreement causes",
            "runtime covariance and gates remain diagnostic",
            "Action00 is protocol stillness, not external motion ground truth",
        ],
        "outputs": {
            "viewer": viewer.name,
            "trajectories": "ROOT_TRAJECTORIES.npz",
            "observations": "UWB_OBSERVATIONS.json",
            "decisions": "UWB_DECISIONS.json",
        },
    }
    result_path = output / "RESULT.json"
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    files = [viewer, output / "ROOT_TRAJECTORIES.npz", output / "UWB_OBSERVATIONS.json",
             output / "UWB_DECISIONS.json", result_path]
    (output / "SHA256SUMS").write_text("".join(
        f"{_sha256(path)}  {path.name}\n" for path in files
    ))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--facts", type=Path, required=True)
    parser.add_argument("--profile-result", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", default=ACTION00)
    arguments = parser.parse_args()
    print(json.dumps(run(
        arguments.facts, arguments.profile_result, arguments.output,
        action=arguments.action,
    ), indent=2, sort_keys=True))
