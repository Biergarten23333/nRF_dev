"""Direct model-space, projection, orientation, and gap diagnostics."""
from __future__ import annotations

from collections import defaultdict

import numpy as np

from pure_imu_baseline.config import GEOMETRY, NODE_ORDER, PARENT_CHILD, SEGMENT_ORDER
from pure_imu_baseline.math3d import mean

from .config import (BONES, CALIBRATION_WINDOWS_S, CAMERA_PROJECTIONS,
                     REQUIRED_C2_TIMES_S)


def quaternion_angle_rad(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Sign-invariant geodesic quaternion angle."""
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    dot = np.sum(a * b, axis=-1)
    return 2.0 * np.arccos(np.clip(np.abs(dot), 0.0, 1.0))


def quaternion_yaw_rad(q: np.ndarray) -> np.ndarray:
    q = np.asarray(q, dtype=float)
    w, x, y, z = np.moveaxis(q, -1, 0)
    return np.arctan2(2.0 * (w*z + x*y), 1.0 - 2.0 * (y*y + z*z))


def quaternion_tilt_rad(q: np.ndarray) -> np.ndarray:
    """Angle between rotated local +Z and global +Z."""
    q = np.asarray(q, dtype=float)
    _, x, y, _ = np.moveaxis(q, -1, 0)
    global_z_component = 1.0 - 2.0 * (x*x + y*y)
    return np.arccos(np.clip(global_z_component, -1.0, 1.0))


def _runs(mask: np.ndarray) -> list[tuple[int, int]]:
    padded = np.r_[False, np.asarray(mask, dtype=bool), False]
    edges = np.flatnonzero(padded[1:] != padded[:-1])
    return [(int(a), int(b)) for a, b in edges.reshape(-1, 2)]


def unwrap_valid(values: np.ndarray, valid: np.ndarray) -> np.ndarray:
    """Unwrap valid runs while retaining a continuous modulo-2pi gauge."""
    out = np.full(len(values), np.nan, dtype=float)
    previous = None
    for start, stop in _runs(valid & np.isfinite(values)):
        current = np.unwrap(values[start:stop])
        if previous is not None:
            current += round((previous-current[0])/(2.0*np.pi)) * 2.0*np.pi
        out[start:stop] = current
        previous = current[-1]
    return out


def _reference_scalar(values: np.ndarray, mask: np.ndarray) -> float:
    finite = values[mask & np.isfinite(values)]
    if not len(finite):
        raise ValueError("no valid calibration reference")
    return float(np.median(finite))


def _block_slope_deg_min(time_s: np.ndarray, values_rad: np.ndarray,
                         block_s: float = 10.0) -> float | None:
    valid = np.isfinite(values_rad)
    if np.sum(valid) < 20:
        return None
    bins = np.floor(time_s[valid] / block_s).astype(int)
    x = []
    y = []
    for value in np.unique(bins):
        use = bins == value
        if np.sum(use) >= 3:
            x.append(float(np.median(time_s[valid][use])))
            y.append(float(np.median(values_rad[valid][use])))
    if len(x) < 3:
        return None
    slope = np.polyfit(np.asarray(x), np.asarray(y), 1)[0]
    return float(np.rad2deg(slope) * 60.0)


def _quantiles(values: np.ndarray) -> dict:
    finite = np.asarray(values)[np.isfinite(values)]
    if not len(finite):
        return {"count": 0}
    p01, p50, p99 = np.percentile(finite, [1, 50, 99])
    return {
        "count": int(len(finite)),
        "minimum": float(np.min(finite)),
        "p01": float(p01),
        "median": float(p50),
        "p99": float(p99),
        "maximum": float(np.max(finite)),
    }


def model_and_projection_diagnostics(data: dict[str, np.ndarray]) -> dict:
    positions = data["joint_positions_m"].astype(float)
    available = data["joint_available"].astype(bool)
    joint_names = [str(x) for x in data["joint_names"]]
    ji = {name: index for index, name in enumerate(joint_names)}
    result = {}
    maximum_error = 0.0
    for name, proximal, distal, _ in BONES:
        a = ji[proximal]
        b = ji[distal]
        valid = available[:, a] & available[:, b]
        delta = positions[:, b] - positions[:, a]
        length = np.linalg.norm(delta, axis=1)
        length[~valid] = np.nan
        error = float(np.nanmax(np.abs(length-GEOMETRY[name])))
        maximum_error = max(maximum_error, error)
        projections = {}
        for camera, components in CAMERA_PROJECTIONS.items():
            apparent = np.linalg.norm(delta[:, components], axis=1)
            apparent[~valid] = np.nan
            projections[camera] = {
                "apparent_length_m": _quantiles(apparent),
                "apparent_to_true_ratio": _quantiles(apparent/GEOMETRY[name]),
            }
        result[name] = {
            "expected_model_length_m": GEOMETRY[name],
            "model_length_m": _quantiles(length),
            "maximum_abs_model_error_m": error,
            "camera_projection": projections,
        }
    return {
        "model_space_is_camera_independent": True,
        "camera_projection_mutates_model_space": False,
        "maximum_abs_bone_length_error_m": maximum_error,
        "bones": result,
    }


def _invalid_gap_events(time_s: np.ndarray, valid: np.ndarray,
                        q_gb: np.ndarray, relative_q: np.ndarray,
                        relative_valid: np.ndarray) -> list[dict]:
    events = []
    for node_index, node in enumerate(NODE_ORDER):
        for start, stop in _runs(~valid[:, node_index]):
            if start == 0 or stop >= len(time_s):
                continue
            duration = float(time_s[stop] - time_s[start-1])
            if duration <= 0.050 + 1e-9:
                continue
            before = start-1
            after = stop
            global_jump = float(np.rad2deg(quaternion_angle_rad(
                q_gb[before, node_index], q_gb[after, node_index])))
            related = {}
            for rel_index, (parent, child) in enumerate(PARENT_CHILD):
                if node not in (NODE_ORDER[SEGMENT_ORDER.index(parent)],
                                NODE_ORDER[SEGMENT_ORDER.index(child)]):
                    continue
                if relative_valid[before, rel_index] and relative_valid[after, rel_index]:
                    related[f"{parent}->{child}"] = float(np.rad2deg(quaternion_angle_rad(
                        relative_q[before, rel_index], relative_q[after, rel_index])))
            events.append({
                "type": "gap_reset",
                "node_id": node,
                "segment": SEGMENT_ORDER[node_index],
                "last_valid_before_s": float(time_s[before]),
                "first_valid_after_s": float(time_s[after]),
                "unobserved_interval_s": duration,
                "raw_global_orientation_change_across_gap_deg": global_jump,
                "related_parent_child_change_across_gap_deg": related,
                "interpretation": "motion and drift during this interval are unobservable; no samples were fabricated",
            })
    return events


def _sudden_orientation_events(time_s: np.ndarray, q_gb: np.ndarray,
                               valid: np.ndarray, relative_q: np.ndarray,
                               relative_valid: np.ndarray,
                               threshold_deg: float = 20.0) -> dict:
    events = []
    for index, name in enumerate(SEGMENT_ORDER):
        pair_valid = valid[:-1, index] & valid[1:, index]
        angle = np.full(len(time_s)-1, np.nan)
        angle[pair_valid] = np.rad2deg(quaternion_angle_rad(
            q_gb[:-1, index][pair_valid], q_gb[1:, index][pair_valid]))
        for frame in np.flatnonzero(angle >= threshold_deg):
            events.append({"time_s": float(time_s[frame+1]), "layer": "q_GB",
                           "name": name, "step_angle_deg": float(angle[frame])})
    for index, pair in enumerate(PARENT_CHILD):
        pair_valid = relative_valid[:-1, index] & relative_valid[1:, index]
        angle = np.full(len(time_s)-1, np.nan)
        angle[pair_valid] = np.rad2deg(quaternion_angle_rad(
            relative_q[:-1, index][pair_valid], relative_q[1:, index][pair_valid]))
        for frame in np.flatnonzero(angle >= threshold_deg):
            events.append({"time_s": float(time_s[frame+1]), "layer": "q_parent_child",
                           "name": f"{pair[0]}->{pair[1]}", "step_angle_deg": float(angle[frame])})
    events.sort(key=lambda item: item["step_angle_deg"], reverse=True)
    return {
        "threshold_deg_per_display_frame": threshold_deg,
        "event_count": len(events),
        "largest_events": events[:25],
        "causal_limit": "large adjacent-frame changes can be fast human articulation, attachment disturbance, or estimator discontinuity; no external truth is claimed",
    }


def _event_index(time_s: np.ndarray, target_s: float) -> int:
    return int(np.argmin(np.abs(time_s-target_s)))


def _snapshot(index: int, time_s: np.ndarray, q_gb: np.ndarray, valid: np.ndarray,
              yaw_delta: np.ndarray, yaw_residual: np.ndarray, tilt: np.ndarray,
              common_yaw: np.ndarray, relative_deviation: np.ndarray,
              relative_valid: np.ndarray, data: dict[str, np.ndarray]) -> dict:
    positions = data["joint_positions_m"].astype(float)
    available = data["joint_available"].astype(bool)
    joint_names = [str(x) for x in data["joint_names"]]
    ji = {name: i for i, name in enumerate(joint_names)}
    bones = {}
    for name, proximal, distal, _ in BONES:
        a, b = ji[proximal], ji[distal]
        if not (available[index, a] and available[index, b]):
            bones[name] = {"available": False}
            continue
        delta = positions[index, b] - positions[index, a]
        bones[name] = {
            "available": True,
            "model_length_m": float(np.linalg.norm(delta)),
            "projected_apparent_length_m": {
                camera: float(np.linalg.norm(delta[list(components)]))
                for camera, components in CAMERA_PROJECTIONS.items()
            },
        }
    segment_values = {}
    for i, name in enumerate(SEGMENT_ORDER):
        segment_values[name] = {
            "node_id": NODE_ORDER[i],
            "valid": bool(valid[index, i]),
            "global_yaw_change_from_calibration_deg": None if not np.isfinite(yaw_delta[index, i]) else float(np.rad2deg(yaw_delta[index, i])),
            "yaw_residual_after_common_body_yaw_deg": None if not np.isfinite(yaw_residual[index, i]) else float(np.rad2deg(yaw_residual[index, i])),
            "local_z_tilt_from_global_up_deg": None if not np.isfinite(tilt[index, i]) else float(np.rad2deg(tilt[index, i])),
        }
    relative = {}
    for i, pair in enumerate(PARENT_CHILD):
        relative[f"{pair[0]}->{pair[1]}"] = {
            "valid": bool(relative_valid[index, i]),
            "geodesic_change_from_calibration_deg": None if not np.isfinite(relative_deviation[index, i]) else float(np.rad2deg(relative_deviation[index, i])),
        }
    return {
        "requested_time_s": None,
        "actual_frame_time_s": float(time_s[index]),
        "frame_index": index,
        "common_body_yaw_change_deg": None if not np.isfinite(common_yaw[index]) else float(np.rad2deg(common_yaw[index])),
        "segments": segment_values,
        "parent_child_relative_orientation": relative,
        "bones": bones,
    }


def analyze_capture(capture: str, data: dict[str, np.ndarray]) -> tuple[dict, dict[str, np.ndarray], list[dict]]:
    time_s = data["time_s"].astype(float)
    q_gb = data["q_GB_wxyz"].astype(float)
    valid = data["valid"].astype(bool)
    relative_q = data["q_parent_child_wxyz"].astype(float)
    relative_valid = data["relative_valid"].astype(bool)
    reset = data["filter_reset"].astype(bool)
    cal_start, cal_stop = CALIBRATION_WINDOWS_S[capture]
    cal = (time_s >= cal_start) & (time_s < cal_stop)

    yaw_unwrapped = np.full(valid.shape, np.nan)
    yaw_delta = np.full(valid.shape, np.nan)
    tilt = np.full(valid.shape, np.nan)
    for i in range(len(SEGMENT_ORDER)):
        yaw_unwrapped[:, i] = unwrap_valid(quaternion_yaw_rad(q_gb[:, i]), valid[:, i])
        ref = _reference_scalar(yaw_unwrapped[:, i], cal & valid[:, i])
        yaw_delta[:, i] = yaw_unwrapped[:, i] - ref
        raw_tilt = quaternion_tilt_rad(q_gb[:, i])
        tilt[:, i] = np.where(valid[:, i], raw_tilt, np.nan)

    core = np.stack((yaw_delta[:, SEGMENT_ORDER.index("pelvis")],
                     yaw_delta[:, SEGMENT_ORDER.index("torso")]), axis=1)
    core_finite = np.isfinite(core)
    core_count = np.sum(core_finite, axis=1)
    common_yaw = np.divide(np.nansum(core, axis=1), core_count,
                           out=np.full(len(core), np.nan), where=core_count > 0)
    yaw_residual = yaw_delta-common_yaw[:, None]
    residual_finite = np.isfinite(yaw_residual)
    # Ten segments are too few for a percentile range: one drifting segment
    # would be mostly suppressed by p90-p10 interpolation.
    high = np.max(np.where(residual_finite, yaw_residual, -np.inf), axis=1)
    low = np.min(np.where(residual_finite, yaw_residual, np.inf), axis=1)
    heading_spread = high-low
    heading_spread[np.sum(residual_finite, axis=1) < 2] = np.nan

    relative_deviation = np.full(relative_valid.shape, np.nan)
    relative_summary = {}
    for i, pair in enumerate(PARENT_CHILD):
        ref_mask = cal & relative_valid[:, i]
        reference = mean(relative_q[ref_mask, i])
        use = relative_valid[:, i]
        relative_deviation[use, i] = quaternion_angle_rad(relative_q[use, i], reference)
        values_deg = np.rad2deg(relative_deviation[:, i])
        relative_summary[f"{pair[0]}->{pair[1]}"] = {
            "geodesic_change_from_calibration_deg": _quantiles(values_deg),
            "ten_second_block_linear_slope_deg_min": _block_slope_deg_min(time_s, relative_deviation[:, i]),
            "interpretation": "contains true articulation plus differential heading/tilt drift; it is not labelled anatomical error",
        }

    heading_summary = {}
    for i, name in enumerate(SEGMENT_ORDER):
        finite = np.flatnonzero(np.isfinite(yaw_residual[:, i]))
        net = None if not len(finite) else float(np.rad2deg(yaw_residual[finite[-1], i]-yaw_residual[finite[0], i]))
        heading_summary[name] = {
            "node_id": NODE_ORDER[i],
            "residual_heading_after_common_body_yaw_deg": _quantiles(np.rad2deg(yaw_residual[:, i])),
            "net_residual_heading_change_deg": net,
            "ten_second_block_linear_slope_deg_min": _block_slope_deg_min(time_s, yaw_residual[:, i]),
        }

    gap_events = _invalid_gap_events(time_s, valid, q_gb, relative_q, relative_valid)
    sudden = _sudden_orientation_events(time_s, q_gb, valid, relative_q, relative_valid)
    reset_events = []
    for frame in np.flatnonzero(np.any(reset, axis=1)):
        nodes = [NODE_ORDER[i] for i in np.flatnonzero(reset[frame])]
        reset_events.append({"type": "filter_reset", "time_s": float(time_s[frame]),
                             "frame_index": int(frame), "nodes": nodes})

    viewer_events = [
        {"type": "calibration", "time_s": cal_start, "label": "calibration start"},
        {"type": "calibration", "time_s": (cal_start+cal_stop)/2.0, "label": "calibration midpoint"},
        {"type": "calibration", "time_s": cal_stop, "label": "calibration end"},
    ]
    viewer_events.extend({"type": "reset", "time_s": e["time_s"],
                          "label": "filter reset: " + ", ".join(e["nodes"])} for e in reset_events)
    viewer_events.extend({"type": "gap", "time_s": e["last_valid_before_s"],
                          "label": f"gap {e['node_id']} ({e['unobserved_interval_s']:.3f} s)"} for e in gap_events)

    observations = {}
    if capture == "2":
        for target in REQUIRED_C2_TIMES_S:
            index = _event_index(time_s, target)
            value = _snapshot(index, time_s, q_gb, valid, yaw_delta, yaw_residual,
                              tilt, common_yaw, relative_deviation, relative_valid, data)
            value["requested_time_s"] = target
            observations[f"{target:.2f}"] = value
            viewer_events.append({"type": "required_observation", "time_s": target,
                                  "label": f"required C2 observation {target:.2f} s"})

    common_finite = np.flatnonzero(np.isfinite(common_yaw))
    common_net = None if not len(common_finite) else float(np.rad2deg(
        common_yaw[common_finite[-1]]-common_yaw[common_finite[0]]))
    geometry = model_and_projection_diagnostics(data)
    diagnostics = {
        "schema": "biospur.pure_imu.stage2.capture_diagnostics.v1",
        "capture": capture,
        "source_layer": "immutable Stage 1 replay arrays",
        "frames": int(len(time_s)),
        "duration_s": float(time_s[-1]),
        "calibration_window_s": [cal_start, cal_stop],
        "model_geometry": geometry,
        "common_global_yaw": {
            "definition": "median of calibration-referenced pelvis and torso global yaw; motion and unobservable gauge drift remain inseparable without external truth",
            "net_change_deg": common_net,
            "ten_second_block_linear_slope_deg_min": _block_slope_deg_min(time_s, common_yaw),
            "distribution_deg": _quantiles(np.rad2deg(common_yaw)),
        },
        "inter_segment_heading": {
            "definition": "per-segment calibration-referenced global yaw minus common body yaw",
            "full_segment_range_deg": _quantiles(np.rad2deg(heading_spread)),
            "segments": heading_summary,
        },
        "parent_child_relative_orientation": relative_summary,
        "tilt": {
            name: {"local_z_from_global_up_deg": _quantiles(np.rad2deg(tilt[:, i])),
                   "interpretation": "pose/tilt observable, but not anatomical error without truth"}
            for i, name in enumerate(SEGMENT_ORDER)
        },
        "sudden_orientation_changes": sudden,
        "gap_reset_effects": gap_events,
        "filter_reset_events": reset_events,
        "required_c2_observations": observations,
        "scope": {
            "raw_vqf_changed": False,
            "frozen_calibration_changed": False,
            "model_coordinates_changed_by_camera": False,
            "uwb_numeric_reads": 0,
            "acceleration_double_integration": False,
        },
    }
    viewer_metrics = {
        "common_body_yaw_deg": np.rad2deg(common_yaw).astype(np.float32),
        "inter_segment_heading_spread_deg": np.rad2deg(heading_spread).astype(np.float32),
        "pelvis_tilt_deg": np.rad2deg(tilt[:, SEGMENT_ORDER.index("pelvis")]).astype(np.float32),
        "torso_tilt_deg": np.rad2deg(tilt[:, SEGMENT_ORDER.index("torso")]).astype(np.float32),
    }
    viewer_events.sort(key=lambda event: (event["time_s"], event["type"], event["label"]))
    return diagnostics, viewer_metrics, viewer_events
