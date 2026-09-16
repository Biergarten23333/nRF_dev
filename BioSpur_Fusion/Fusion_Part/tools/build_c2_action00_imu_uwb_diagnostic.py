#!/usr/bin/env python3
"""Build a small, explicit Action00 pure-IMU versus IMU+UWB diagnostic.

This is intentionally not a calibration or production-solver entry point.  It
keeps one native-200 pose/FK stream on both sides and changes only root
translation so the observable effect of UWB drift correction is easy to see.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_coupled_progressive.native200_publication_producer import (
    Native200PublicationProducer,
    accepted_pose_frame,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    DATASET,
    LAYOUT,
    PHYSICAL_DIRECTORY,
)
from biospur_fusion.c2_uwb_root_world.u0 import decode_uwb_only
from biospur_fusion.root_r3 import (
    CausalDelayedRootFilter,
    ImuSample,
    PositionObservation,
    RootFilterConfig,
)
from biospur_fusion.root_r3.replay import initial_state
from tools.build_c2_hxx_fusion_ab_viewer import _html


ROOT = Path(__file__).resolve().parents[1]
ACTION = "00_initial_still"
PELVIS = "BSFC2CC"
POSITION_ROWS = (
    ROOT
    / "logs/c2_uwb_antenna_geometry_selected_solve_00_20260903_185400/"
    "POSITION_ROWS.jsonl"
)
SOURCE_UWB_RESULT = POSITION_ROWS.parent / "PILOT_RESULT.json"
SHARED_ROOT_GAUGE = (
    ROOT
    / "logs/c2_00_ankle_contact_root_crosscheck_v2_20260904/"
    "00_initial_still_SHARED_ROOT_IMU_FUSION.npz"
)
JOINT_NAMES = (
    "pelvis_center",
    "shoulder_mid",
    "shoulder_left",
    "shoulder_right",
    "hip_left",
    "hip_right",
    "elbow_left",
    "wrist_left",
    "elbow_right",
    "wrist_right",
    "knee_left",
    "ankle_left",
    "knee_right",
    "ankle_right",
)
LINES = (
    ("pelvis_center", "shoulder_mid", "center"),
    ("shoulder_left", "shoulder_right", "center"),
    ("hip_left", "hip_right", "center"),
    ("shoulder_left", "elbow_left", "left"),
    ("elbow_left", "wrist_left", "left"),
    ("shoulder_right", "elbow_right", "right"),
    ("elbow_right", "wrist_right", "right"),
    ("hip_left", "knee_left", "left"),
    ("knee_left", "ankle_left", "left"),
    ("hip_right", "knee_right", "right"),
    ("knee_right", "ankle_right", "right"),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _pelvis_positions() -> dict[int, np.ndarray]:
    result: dict[int, np.ndarray] = {}
    for line in POSITION_ROWS.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row["episode"] != ACTION or row["node"] != PELVIS or row["policy"] != "top_6":
            continue
        sweep = int(row["sweep"])
        if sweep in result:
            raise RuntimeError(f"duplicate pelvis UWB sweep {sweep}")
        result[sweep] = np.asarray(row["position_v4_m"], dtype=float)
    if len(result) != 63:
        raise RuntimeError(f"expected 63 existing pelvis positions, got {len(result)}")
    return result


def _uwb_observations(
    producer: Native200PublicationProducer,
    first_imu_global_ns: int,
) -> tuple[list[tuple[float, int, np.ndarray]], Path, dict[str, Any]]:
    positions = _pelvis_positions()
    raw_path = (
        DATASET
        / "actions"
        / PHYSICAL_DIRECTORY[ACTION]
        / "rep_01/raw/fusion_host_raw.cobs.bin"
    )
    decoded, decode_summary = decode_uwb_only(raw_path)
    clock = producer._clocks[ACTION]
    rows: dict[int, tuple[float, int, np.ndarray]] = {}
    for record in decoded:
        if record.node != PELVIS or int(record.sweep) not in positions:
            continue
        sweep = int(record.sweep)
        time_s = (
            clock.global_ns(int(record.strobe_us)) - first_imu_global_ns
        ) * 1e-9
        rows[sweep] = (float(time_s), sweep, positions[sweep])
    ordered = sorted(rows.values(), key=lambda row: row[0])
    if len(ordered) != len(positions) or any(
        right[0] <= left[0] for left, right in zip(ordered, ordered[1:])
    ):
        raise RuntimeError("failed to recover exact ordered pelvis UWB times")
    return ordered, raw_path, decode_summary


def _run_roots(
    times_s: np.ndarray,
    forces: np.ndarray,
    rotations: np.ndarray,
    uwb: list[tuple[float, int, np.ndarray]],
) -> tuple[np.ndarray, np.ndarray, list[dict[str, Any]], np.ndarray]:
    # One robust initial world gauge only.  Both branches receive the identical
    # constant; it cannot create the difference between A and B.
    p0 = np.median(np.asarray([row[2] for row in uwb[:7]], dtype=float), axis=0)
    config = RootFilterConfig(
        inertial_acceleration_noise_mps2_sqrt_hz=0.30,
        accelerometer_bias_rw_mps3_sqrt_hz=0.003,
        nis_limit_3d=100.0,
        maximum_position_influence_m=0.25,
        fixed_lag_s=0.20,
        uwb_stale_s=0.60,
        uwb_dropout_s=1.20,
    )
    pure = CausalDelayedRootFilter(initial_state(0.0, p0), config, inertial=True)
    fused = CausalDelayedRootFilter(initial_state(0.0, p0), config, inertial=True)
    roots_a = np.empty((len(times_s), 3), dtype=float)
    roots_b = np.empty_like(roots_a)
    roots_a[0] = p0
    roots_b[0] = p0
    decisions: list[dict[str, Any]] = []
    cursor = 0
    for frame in range(1, len(times_s)):
        sample = ImuSample(
            measurement_time_s=float(times_s[frame]),
            availability_time_s=float(times_s[frame]),
            specific_force_sensor_mps2=forces[frame],
            rotation_world_from_sensor=rotations[frame],
            source_sequence=frame,
        )
        if not pure.add_imu(sample) or not fused.add_imu(sample):
            raise RuntimeError(f"IMU rejected at frame {frame}")
        while cursor < len(uwb) and uwb[cursor][0] <= times_s[frame] + 1e-12:
            measurement_s, sweep, position = uwb[cursor]
            observation = PositionObservation(
                measurement_time_s=measurement_s,
                availability_time_s=float(times_s[frame]),
                root_position_m=position,
                covariance_m2=np.eye(3) * 0.12**2,
                tag_id=PELVIS,
                quality_state="ACTION00_EXISTING_TOP6_DIAGNOSTIC",
                frame_valid=True,
                physical_point_valid=True,
                source_sequence=sweep,
            )
            decision = fused.add_position(
                observation,
                processing_time_s=float(times_s[frame]),
            )
            decisions.append(
                {
                    "time_s": measurement_s,
                    "sweep": sweep,
                    "accepted": bool(decision.accepted),
                    "reason": decision.reason,
                    "nis": None if decision.nis is None else float(decision.nis),
                    "applied_position_delta_m": np.asarray(
                        decision.applied_position_delta_m, dtype=float
                    ).tolist(),
                }
            )
            cursor += 1
        roots_a[frame] = pure.current_state.position_m
        roots_b[frame] = fused.current_state.position_m
    if cursor != len(uwb):
        raise RuntimeError("not all UWB observations were consumed")
    return roots_a, roots_b, decisions, p0


def _scene() -> dict[str, Any]:
    layout = json.loads(LAYOUT.read_text(encoding="utf-8"))
    anchors = {
        str(row["label"]): [
            float(row["x_mm"]) / 1000.0,
            float(row["y_mm"]) / 1000.0,
            float(row["z_mm"]) / 1000.0,
        ]
        for row in layout["anchors"]
    }
    array = np.asarray(list(anchors.values()), dtype=float)
    low, high = array.min(axis=0), array.max(axis=0)
    return {
        "geometry_role": "fixed V4-io world and A-H anchor volume",
        "anchors_output_m": anchors,
        "anchor_bounds_output_m": {"min": low.tolist(), "max": high.tolist()},
        "padded_bounds_output_m": {
            "min": (low - np.array([0.85, 0.85, 0.65])).tolist(),
            "max": (high + np.array([0.85, 0.85, 0.65])).tolist(),
        },
    }


def _panel(
    source: Any,
    producer: Native200PublicationProducer,
    times_s: np.ndarray,
    roots: np.ndarray,
    keep: np.ndarray,
    scene: dict[str, Any],
) -> dict[str, Any]:
    frames: list[list[float]] = []
    for frame in keep:
        _, points, _ = accepted_pose_frame(
            source.trajectory, int(frame), producer._alignment, producer._geometry
        )
        frames.append(
            [
                round(float(value), 5)
                for name in JOINT_NAMES
                for value in points[name] + roots[frame]
            ]
        )
    index = {name: idx for idx, name in enumerate(JOINT_NAMES)}
    return {
        "jointNames": list(JOINT_NAMES),
        "lines": [[index[a], index[b], side] for a, b, side in LINES],
        "viewGauge": {
            "frontYawRad": 0.0,
            "outputCoordinateParity": 1.0,
        },
        "episode": {
            "id": ACTION,
            "instruction": "自然站立；允许呼吸、微摆、小幅姿势调整和空间漂移。",
            "note": "同一原生200 Hz姿态/FK；仅根平移来源不同。",
            "time": np.round(times_s[keep], 4).tolist(),
            "sourceFrame": keep.tolist(),
            "frames": frames,
        },
        "uwbScene": scene,
    }


def _drift_metrics(root: np.ndarray, p0: np.ndarray) -> dict[str, Any]:
    displacement = np.linalg.norm(root - p0, axis=1)
    return {
        "endpoint_vector_m": (root[-1] - p0).tolist(),
        "endpoint_norm_m": float(displacement[-1]),
        "maximum_norm_m": float(displacement.max()),
        "rms_norm_m": float(np.sqrt(np.mean(displacement**2))),
        "p95_norm_m": float(np.quantile(displacement, 0.95)),
    }


def _display_gauge(
    producer: Native200PublicationProducer,
    source: Any,
    raw_initial_root: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Place both branches at one already-derived ten-node world XY gauge.

    The shared-root archive owns horizontal world placement.  Z is shifted
    once for display so the two initial ankle proxies meet the lower anchor
    plane; this is not fed back into either root filter.
    """

    with np.load(SHARED_ROOT_GAUGE, allow_pickle=False) as archive:
        shared_initial = np.asarray(
            archive["shared_root_position_world_m"][0], dtype=float
        )
    _, points, _ = accepted_pose_frame(
        source.trajectory, 0, producer._alignment, producer._geometry
    )
    ankle_z = np.mean([points["ankle_left"][2], points["ankle_right"][2]])
    display_target = np.array(
        [shared_initial[0], shared_initial[1], -ankle_z], dtype=float
    )
    return display_target - raw_initial_root, display_target, shared_initial


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if ROOT not in output.parents or output.exists():
        raise SystemExit("output must be a new directory under Fusion_Part")
    output.mkdir(parents=False)

    producer = Native200PublicationProducer.from_sealed_archives()
    source = producer._sources[ACTION]
    clock = producer._clocks[ACTION]
    global_ns = np.asarray(
        [clock.global_ns(int(timer)) for timer in source.time_us], dtype=np.int64
    )
    times_s = (global_ns - global_ns[0]).astype(float) * 1e-9
    rotations = np.asarray(
        [producer._alignment @ rotation_from_wxyz(q) for q in source.sensor_quat_wxyz]
    )
    uwb, raw_path, decode_summary = _uwb_observations(producer, int(global_ns[0]))
    roots_a, roots_b, decisions, p0 = _run_roots(
        times_s, source.calibrated_acc_mps2, rotations, uwb
    )
    display_shift, display_initial, shared_initial = _display_gauge(
        producer, source, p0
    )
    display_roots_a = roots_a + display_shift
    display_roots_b = roots_b + display_shift
    scene = _scene()
    keep = np.arange(0, len(times_s), 3, dtype=int)
    if keep[-1] != len(times_s) - 1:
        keep = np.r_[keep, len(times_s) - 1]
    left = _panel(source, producer, times_s, display_roots_a, keep, scene)
    right = _panel(source, producer, times_s, display_roots_b, keep, scene)
    accepted = sum(row["accepted"] for row in decisions)
    metrics_a = _drift_metrics(roots_a, p0)
    metrics_b = _drift_metrics(roots_b, p0)
    suppression = 1.0 - metrics_b["maximum_norm_m"] / metrics_a["maximum_norm_m"]
    audit = {
        "schema": "biospur-c2-action00-imu-uwb-root-diagnostic-v1",
        "action": ACTION,
        "same_elapsed_time_playhead": True,
        "same_world_camera": True,
        "same_uwb_volume": True,
        "viewer_pose_root_contact_or_floor_correction": False,
        "viewer_interpolation": "DISPLAY_ONLY_CARTESIAN_BETWEEN_NATIVE200_DECIMATION",
        "common_duration_s": float(times_s[-1]),
    }
    html_path = output / "c2_00_initial_still_pure_imu_vs_imu_uwb_ab.html"
    html_path.write_text(
        _html(
            left,
            right,
            audit,
            left_label="A：纯 IMU（200 Hz 积分）",
            right_label="B：同一 IMU + 骨盆 UWB",
            left_drift=f"末端漂移 {metrics_a['endpoint_norm_m']:.2f} m",
            right_drift=f"最大偏移 {metrics_b['maximum_norm_m']:.2f} m · UWB {accepted}/{len(decisions)} 接受",
            render_style="skeleton",
        ),
        encoding="utf-8",
    )
    np.savez_compressed(
        output / "ROOT_TRAJECTORIES.npz",
        time_s=times_s,
        pure_imu_root_m=roots_a,
        imu_uwb_root_m=roots_b,
        initial_root_m=p0,
        display_pure_imu_root_m=display_roots_a,
        display_imu_uwb_root_m=display_roots_b,
        display_initial_root_m=display_initial,
        display_world_gauge_shift_m=display_shift,
        uwb_time_s=np.asarray([row[0] for row in uwb]),
        uwb_pelvis_position_m=np.asarray([row[2] for row in uwb]),
        display_uwb_pelvis_position_m=np.asarray(
            [row[2] + display_shift for row in uwb]
        ),
    )
    result = {
        "status": "ACTION00_ROOT_DRIFT_DIAGNOSTIC_COMPLETE",
        "scientific_pass": False,
        "purpose": "visualize pure-IMU root drift and the isolated effect of existing UWB position updates",
        "pose_contract": "identical calibrated native-200 pose/FK on A and B; root translation only",
        "time_contract": "pelvis UWB strobe and pelvis IMU TIMER2 mapped by the same frozen B306/Beacon clock owner",
        "duration_s": float(times_s[-1]),
        "imu_frames": int(len(times_s)),
        "viewer_frames": int(len(keep)),
        "viewer_nominal_fps": float(1.0 / np.median(np.diff(times_s[keep]))),
        "uwb": {
            "node": PELVIS,
            "policy": "existing top_6 selected-link V4 position",
            "observations": len(decisions),
            "accepted": accepted,
            "rejected": len(decisions) - accepted,
            "first_measurement_s": uwb[0][0],
            "last_measurement_s": uwb[-1][0],
            "median_rate_hz": float(1.0 / np.median(np.diff([row[0] for row in uwb]))),
            "covariance_std_m": 0.12,
            "maximum_position_influence_m": 0.25,
            "nis_limit_3d": 100.0,
        },
        "pure_imu": metrics_a,
        "imu_plus_uwb": metrics_b,
        "maximum_displacement_suppression_fraction": suppression,
        "display_world_gauge": {
            "horizontal_owner": "existing Action00 ten-node shared-root first epoch",
            "horizontal_initial_root_m": shared_initial[:2].tolist(),
            "vertical_owner": "one constant display-only ankle-to-lower-anchor-plane shift",
            "display_initial_root_m": display_initial.tolist(),
            "applied_to_both_branches_m": display_shift.tolist(),
            "fed_back_into_filter": False,
        },
        "limitations": [
            "00_initial_still is a protocol label, not external motion ground truth",
            "UWB positions are reused diagnostic top-6 outputs, not a new raw-range solve",
            "only the pelvis UWB node is used; this does not test ten-node propagation",
            "filter influence and covariance are diagnostic settings, not frozen production tuning",
            "pose/calibration is intentionally held identical to isolate root translation",
        ],
        "sources": {
            "raw": str(raw_path.relative_to(ROOT)),
            "raw_sha256": _sha256(raw_path),
            "position_rows": str(POSITION_ROWS.relative_to(ROOT)),
            "position_rows_sha256": _sha256(POSITION_ROWS),
            "position_result": str(SOURCE_UWB_RESULT.relative_to(ROOT)),
            "position_result_sha256": _sha256(SOURCE_UWB_RESULT),
            "shared_root_gauge": str(SHARED_ROOT_GAUGE.relative_to(ROOT)),
            "shared_root_gauge_sha256": _sha256(SHARED_ROOT_GAUGE),
            "decode_summary": asdict(decode_summary),
            "base_pose_owner_digest": producer.base_pose_owner_digest,
        },
        "outputs": {
            "viewer": html_path.name,
            "root_trajectories": "ROOT_TRAJECTORIES.npz",
            "uwb_decisions": "UWB_DECISIONS.json",
        },
    }
    _json(output / "UWB_DECISIONS.json", decisions)
    _json(output / "RESULT.json", result)
    files = [html_path, output / "ROOT_TRAJECTORIES.npz", output / "UWB_DECISIONS.json", output / "RESULT.json"]
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in files),
        encoding="utf-8",
    )
    print(json.dumps({
        "viewer": str(html_path),
        "pure_imu_endpoint_m": metrics_a["endpoint_norm_m"],
        "pure_imu_maximum_m": metrics_a["maximum_norm_m"],
        "imu_uwb_endpoint_m": metrics_b["endpoint_norm_m"],
        "imu_uwb_maximum_m": metrics_b["maximum_norm_m"],
        "suppression_fraction": suppression,
        "uwb_accepted": accepted,
        "uwb_total": len(decisions),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
