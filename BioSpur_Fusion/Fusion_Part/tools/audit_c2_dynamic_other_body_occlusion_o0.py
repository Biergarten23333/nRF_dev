#!/usr/bin/env python3
"""Read-only C2 dynamic other-body UWB ray occlusion audit.

This is a diagnostic association study.  It neither edits nor selects ranges
for the production solver, and a capsule intersection is never called measured
LOS/NLOS truth.  Every scored range is omitted from its own root prediction.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import fields
import hashlib
import inspect
import json
import math
from pathlib import Path
import time
from typing import Any, Iterable

import numpy as np

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_coupled_progressive.renderer import joints_for_frame
from biospur_fusion.c2_uwb_calibration.antenna_los import (
    outward_facing_score,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    FrozenHoldoutBodyProxy,
    NODE_TO_PROXY_POINT,
    frozen_world_alignment,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    solve_shared_root,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)

from evaluate_c2_hxx_shared_root_regression import DEFAULT_CLOCK, _load_holdout
from evaluate_c2_pair_bias_gate import (
    _base_sigma,
    _load_layout,
    _prediction,
    _reference_time,
    _tracker,
    _update_tracker,
    _valid_slots,
)
from run_c2_h01_shared_root_imu_fusion import (
    _load_analytic_pose_owner,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RESULT = (
    ROOT / "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "FINAL_RESULT.json"
)
DEFAULT_CALIBRATION_REPORT = (
    ROOT / "logs/c2_native200_calibration_v3_20260904/"
    "POSE_RESET_QMT_DIAGNOSTIC.json"
)
DEFAULT_PRE_IK_REPORT = (
    ROOT / "logs/c2_hxx_native200_calibration_v3_20260904/"
    "HXX_FROZEN_C2_REPLAY_REPORT.json"
)

RADIUS_ENVELOPES_M = {
    "small": {
        "torso": 0.12,
        "upper_arm": 0.035,
        "forearm": 0.030,
        "thigh": 0.055,
        "shank": 0.040,
    },
    "nominal_proxy": {
        "torso": 0.16,
        "upper_arm": 0.055,
        "forearm": 0.045,
        "thigh": 0.080,
        "shank": 0.060,
    },
    "large": {
        "torso": 0.20,
        "upper_arm": 0.075,
        "forearm": 0.060,
        "thigh": 0.105,
        "shank": 0.080,
    },
}
NEAR_FIELD_M = 0.15
MINIMUM_BLOCKED_AND_CLEAR_EPOCH_CLUSTERS = 30
MAXIMUM_CAUSAL_POSE_AGE_S = 0.005005
MAXIMUM_GEOMETRY_CONDITION = 1e8
DISK_CAP_BYTES = 200_000_000
# Measured synthetic nested descriptor: 17,650 B/link.  This fixed pre-run
# allowance adds >40% for real numeric strings/link metadata plus 10 MB seal.
PROJECTED_SERIALIZED_BYTES_PER_LINK = 25_000
PROJECTED_FIXED_OUTPUT_BYTES = 10_000_000
FACING_BINS = np.asarray([-1.0000001, -0.5, 0.0, 0.5, 1.0000001])
RANGE_BINS_M = np.asarray([0.0, 3.0, 5.0, 7.0, math.inf])

BODY_SEGMENTS = {
    "torso": ("pelvis_center", "shoulder_mid", "torso"),
    "upper_arm_left": ("shoulder_left", "elbow_left", "upper_arm"),
    "forearm_left": ("elbow_left", "wrist_left", "forearm"),
    "upper_arm_right": ("shoulder_right", "elbow_right", "upper_arm"),
    "forearm_right": ("elbow_right", "wrist_right", "forearm"),
    "thigh_left": ("hip_left", "knee_left", "thigh"),
    "shank_left": ("knee_left", "ankle_left", "shank"),
    "thigh_right": ("hip_right", "knee_right", "thigh"),
    "shank_right": ("knee_right", "ankle_right", "shank"),
}

NODE_LOCAL_BODY_SEGMENT = {
    "BSF31CC": "torso",
    # The pelvis tag is a point proxy, not a torso capsule owner.  Torso rays
    # remain visible and near-field overlap is reported as ambiguous.
    "BSFC2CC": "PELVIS_POINT_ONLY_NO_CAPSULE",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSFEC35": "forearm_left",
    "BSFB165": "forearm_right",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def _strict_preceding_index(absolute_times_ns: np.ndarray, query_ns: float) -> int:
    """Return the strict (< query) owner index; exact equality uses prior row."""

    index = int(np.searchsorted(absolute_times_ns, query_ns, side="left")) - 1
    if index < 0:
        raise ValueError("no strictly preceding timestamp")
    return index


def _per_link_time_s(clock: Any, raw: Any, anchor: int) -> float:
    """B306/Beacon strobe plus measured per-anchor half-round-trip time."""

    return float(clock.seconds(raw.strobe_us + 0.5 * raw.t_round_us[anchor]))


def _signed_range_innovation_m(measured_m: float, predicted_m: float) -> float:
    """Positive means the measured path is longer than its LOO prediction."""

    return float(measured_m) - float(predicted_m)


def _loo_training_rows(
    exact: list[dict[str, Any]], target_index: int, *, same_node: bool
) -> list[dict[str, Any]]:
    target = exact[target_index]["link"]
    return [
        row for index, row in enumerate(exact)
        if index != target_index and (not same_node or row["link"].node == target.node)
    ]


def _segment_distance(
    p1: np.ndarray, q1: np.ndarray, p2: np.ndarray, q2: np.ndarray
) -> float:
    """Shortest distance between finite 3-D line segments."""

    p1 = np.asarray(p1, dtype=float)
    q1 = np.asarray(q1, dtype=float)
    p2 = np.asarray(p2, dtype=float)
    q2 = np.asarray(q2, dtype=float)
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a, e = float(d1 @ d1), float(d2 @ d2)
    eps = 1e-15
    if a <= eps and e <= eps:
        return float(np.linalg.norm(r))
    if a <= eps:
        s = 0.0
        t = float(np.clip((d2 @ r) / e, 0.0, 1.0))
    else:
        c = float(d1 @ r)
        if e <= eps:
            t = 0.0
            s = float(np.clip(-c / a, 0.0, 1.0))
        else:
            b = float(d1 @ d2)
            denom = a * e - b * b
            s = float(np.clip((b * float(d2 @ r) - c * e) / denom, 0.0, 1.0)) if denom > eps else 0.0
            t = (b * s + float(d2 @ r)) / e
            if t < 0.0:
                t = 0.0
                s = float(np.clip(-c / a, 0.0, 1.0))
            elif t > 1.0:
                t = 1.0
                s = float(np.clip((b - c) / a, 0.0, 1.0))
    return float(np.linalg.norm((p1 + s * d1) - (p2 + t * d2)))


def _quadratic_interval(
    a: float, b: float, c: float, lo: float, hi: float
) -> list[tuple[float, float]]:
    """Return the part of [lo, hi] where a*t^2+b*t+c <= 0."""

    eps = 1e-14
    if hi <= lo:
        return []
    if abs(a) <= eps:
        if abs(b) <= eps:
            return [(lo, hi)] if c <= 0.0 else []
        root = -c / b
        interval = (lo, min(hi, root)) if b > 0.0 else (max(lo, root), hi)
        return [interval] if interval[1] > interval[0] else []
    discriminant = b * b - 4.0 * a * c
    if discriminant < 0.0:
        return [(lo, hi)] if a < 0.0 else []
    width = math.sqrt(max(0.0, discriminant))
    first, second = sorted(((-b - width) / (2.0 * a), (-b + width) / (2.0 * a)))
    if a > 0.0:
        interval = (max(lo, first), min(hi, second))
        return [interval] if interval[1] > interval[0] else []
    output = []
    if first > lo:
        output.append((lo, min(hi, first)))
    if second < hi:
        output.append((max(lo, second), hi))
    return [row for row in output if row[1] > row[0]]


def _merge_intervals(intervals: Iterable[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for start, stop in sorted(intervals):
        if stop <= start:
            continue
        if not merged or start > merged[-1][1] + 1e-12:
            merged.append([float(start), float(stop)])
        else:
            merged[-1][1] = max(merged[-1][1], float(stop))
    return [(row[0], row[1]) for row in merged]


def _ray_capsule_intervals(
    origin: np.ndarray,
    anchor: np.ndarray,
    segment_start: np.ndarray,
    segment_stop: np.ndarray,
    radius_m: float,
) -> list[tuple[float, float]]:
    """Analytic along-ray intervals inside a finite capped segment capsule.

    The returned coordinate is metres from ``origin`` along the tag-anchor ray.
    The closest point on the body segment is piecewise endpoint/interior, making
    each interval a quadratic inequality rather than a sampled approximation.
    """

    origin = np.asarray(origin, dtype=float)
    anchor = np.asarray(anchor, dtype=float)
    start = np.asarray(segment_start, dtype=float)
    stop = np.asarray(segment_stop, dtype=float)
    ray = anchor - origin
    length = float(np.linalg.norm(ray))
    direction = ray / length
    body = stop - start
    body_sq = float(body @ body)
    if body_sq <= 1e-15:
        breaks = [0.0, length]
    else:
        projection_0 = float((origin - start) @ body) / body_sq
        projection_rate = float(direction @ body) / body_sq
        breaks = [0.0, length]
        if abs(projection_rate) > 1e-15:
            for value in ((0.0 - projection_0) / projection_rate, (1.0 - projection_0) / projection_rate):
                if 0.0 < value < length:
                    breaks.append(float(value))
        breaks.sort()
    intervals = []
    for lo, hi in zip(breaks[:-1], breaks[1:]):
        mid = 0.5 * (lo + hi)
        raw_projection = (
            float((origin + mid * direction - start) @ body) / body_sq
            if body_sq > 1e-15 else 0.0
        )
        if raw_projection <= 0.0 or body_sq <= 1e-15:
            base, slope = origin - start, direction
        elif raw_projection >= 1.0:
            base, slope = origin - stop, direction
        else:
            projector = np.eye(3) - np.outer(body, body) / body_sq
            base, slope = projector @ (origin - start), projector @ direction
        a = float(slope @ slope)
        b = 2.0 * float(base @ slope)
        c = float(base @ base) - float(radius_m) ** 2
        intervals.extend(_quadratic_interval(a, b, c, lo, hi))
    return _merge_intervals(intervals)


def _closest_ray_fraction(
    origin: np.ndarray,
    anchor: np.ndarray,
    segment_start: np.ndarray,
    segment_stop: np.ndarray,
) -> float:
    """Deterministic closest-approach fraction via convex 1-D minimization."""

    origin = np.asarray(origin, dtype=float)
    anchor = np.asarray(anchor, dtype=float)
    start = np.asarray(segment_start, dtype=float)
    stop = np.asarray(segment_stop, dtype=float)
    # Ternary search is used only for a descriptor, never solver ownership.
    lo, hi = 0.0, 1.0
    for _ in range(48):
        first = (2.0 * lo + hi) / 3.0
        second = (lo + 2.0 * hi) / 3.0
        p_first = origin + first * (anchor - origin)
        p_second = origin + second * (anchor - origin)
        if _segment_distance(p_first, p_first, start, stop) <= _segment_distance(p_second, p_second, start, stop):
            hi = second
        else:
            lo = first
    return float(0.5 * (lo + hi))


def _geometry_svd(origins: np.ndarray, anchors: np.ndarray) -> dict[str, Any]:
    if len(origins) != len(anchors) or len(origins) == 0:
        return {"count": int(len(origins)), "rank": 0, "smallest_singular": 0.0, "condition": None}
    delta = anchors - origins
    distance = np.linalg.norm(delta, axis=1)
    if np.any(distance <= np.finfo(float).eps):
        return {"count": int(len(origins)), "rank": 0, "smallest_singular": 0.0, "condition": None}
    matrix = delta / distance[:, None]
    singular = np.linalg.svd(matrix, compute_uv=False)
    tolerance = max(matrix.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    return {
        "count": int(len(origins)),
        "rank": rank,
        "smallest_singular": float(singular[-1]) if len(singular) >= 3 else 0.0,
        "condition": float(singular[0] / singular[-1]) if rank == 3 else None,
    }


class StrictCausalPose:
    """Map exact common-clock link time to a strictly earlier analytic frame."""

    def __init__(
        self,
        body: FrozenHoldoutBodyProxy,
        action: str,
        actual_common_interval_ns: tuple[int, int],
        grid_period_ns: int,
        grid_rows: int,
        alignment: np.ndarray,
    ) -> None:
        self.body = body
        self.action = action
        self.common_start_ns = int(actual_common_interval_ns[0])
        self.common_stop_ns = int(actual_common_interval_ns[1])
        self.alignment = np.asarray(alignment, dtype=float)
        row = body._row(action, "pelvis")
        time_s = np.asarray(row["time_root_s"], dtype=float)
        masks = [
            np.asarray(body._row(action, segment)["mask"], dtype=bool)
            for segment in body.calibration.node_to_segment.values()
        ]
        valid = np.flatnonzero(np.logical_and.reduce(masks))
        if len(valid) < 2:
            raise ValueError(f"{action}: insufficient common native200 pose")
        self.time_s = time_s
        self.valid = valid
        self.t0 = float(time_s[valid[0]])
        self.t1 = float(time_s[valid[-1]])
        self._cache: dict[int, tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray]]] = {}
        expected_stop_ns = self.common_start_ns + (int(grid_rows) - 1) * int(grid_period_ns)
        if (
            len(time_s) != int(grid_rows)
            or len(valid) != len(time_s)
            or int(grid_period_ns) != 5_000_000
            or self.common_stop_ns != expected_stop_ns
            or abs(self.t0) > 1e-12
            or abs(self.t1 * 1e9 - (self.common_stop_ns - self.common_start_ns)) > 1.0
            or not np.allclose(np.diff(time_s), 0.005, atol=2e-12, rtol=0.0)
        ):
            raise ValueError(f"{action}: native200 absolute clock binding mismatch")

    def at(self, link_time_s: float) -> dict[str, Any]:
        link_time_ns = float(link_time_s) * 1e9
        absolute_pose_ns = self.common_start_ns + self.time_s[self.valid] * 1e9
        local = _strict_preceding_index(absolute_pose_ns, link_time_ns)
        frame = int(self.valid[min(local, len(self.valid) - 1)])
        pose_abs_s = (self.common_start_ns + float(self.time_s[frame]) * 1e9) / 1e9
        if not pose_abs_s < float(link_time_s):
            raise RuntimeError("pose prior is not strictly before link time")
        if frame not in self._cache:
            joints = self._exact_joints(frame)
            offsets, normals = self._exact_offsets_normals(frame, joints)
            self._cache[frame] = (offsets, normals, joints)
        offsets, normals, joints = self._cache[frame]
        return {
            "frame": frame,
            "pose_time_s": float(self.time_s[frame]),
            "pose_absolute_time_s": pose_abs_s,
            "pose_age_s": float(link_time_s) - pose_abs_s,
            "offsets": offsets,
            "normals": normals,
            "joints": joints,
        }

    def _exact_joints(self, frame: int) -> dict[str, np.ndarray]:
        joints = joints_for_frame(
            self.body.trajectory,
            self.action,
            frame,
            self.body.model,
            self.body.config,
            apply_output_coordinates=False,
        )
        pelvis = np.asarray(joints["pelvis_center"], dtype=float)
        return {
            name: self.alignment @ (np.asarray(value, dtype=float) - pelvis)
            for name, value in joints.items()
        }

    def _exact_offsets_normals(
        self, frame: int, joints: dict[str, np.ndarray]
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        from biospur_fusion.c2_uwb_calibration.antenna_los import (
            outward_normal_world,
        )

        offsets = {
            node: np.asarray(joints[point], dtype=float)
            for node, point in NODE_TO_PROXY_POINT.items()
        }
        normals = {
            node: outward_normal_world(
                node,
                self.body._row(self.action, segment)[
                    "quat_world_segment_wxyz"
                ][frame],
                self.alignment,
            )
            for node, segment in self.body.calibration.node_to_segment.items()
        }
        return offsets, normals


def _exact_links(
    group: list[Any],
    *,
    pose: StrictCausalPose,
    anchors: np.ndarray,
    delays: np.ndarray,
    tag_delay: float,
    layout_sigma: float,
    clocks: dict[str, Any],
    reference_time_s: float,
    predicted_root: np.ndarray,
    velocity: np.ndarray,
) -> list[dict[str, Any]]:
    rows = []
    for raw in group:
        for anchor in _valid_slots(raw):
            link_time_s = _per_link_time_s(clocks[raw.node], raw, anchor)
            prior = pose.at(link_time_s)
            offset = np.asarray(prior["offsets"][raw.node], dtype=float)
            normal = np.asarray(prior["normals"][raw.node], dtype=float)
            corrected = (
                float(raw.ranges_mm[anchor]) / 1000.0
                - float(delays[anchor])
                - float(tag_delay)
            )
            tag_predicted = (
                np.asarray(predicted_root, dtype=float)
                + offset
                + (link_time_s - reference_time_s) * velocity
            )
            link = SharedRangeLink(
                node=raw.node,
                anchor=int(anchor),
                range_m=corrected,
                tag_offset_world_m=offset,
                link_dt_s=float(link_time_s - reference_time_s),
                sigma_m=_base_sigma(layout_sigma, int(raw.quality[anchor])),
                facing_score=outward_facing_score(
                    tag_predicted, anchors[anchor], normal
                ),
            )
            rows.append({
                "link": link,
                "raw": raw,
                "link_time_s": float(link_time_s),
                "t_round_us": float(raw.t_round_us[anchor]),
                "prior": prior,
                "causal_prior_tag_origin_m": tag_predicted.copy(),
            })
    return rows


def _body_classification(
    *,
    node: str,
    origin: np.ndarray,
    anchor: np.ndarray,
    joints_relative: dict[str, np.ndarray],
) -> dict[str, Any]:
    direction = np.asarray(anchor, dtype=float) - np.asarray(origin, dtype=float)
    length = float(np.linalg.norm(direction))
    if length <= NEAR_FIELD_M:
        raise ValueError("anchor lies inside emitter near-field envelope")
    unit = direction / length
    trimmed_origin = np.asarray(origin, dtype=float) + NEAR_FIELD_M * unit
    emitter_proxy = np.asarray(
        joints_relative[NODE_TO_PROXY_POINT[node]], dtype=float
    )
    world_joints = {
        name: np.asarray(origin, dtype=float) + np.asarray(value, dtype=float) - emitter_proxy
        for name, value in joints_relative.items()
    }
    reconstruction_error = float(
        np.linalg.norm(world_joints[NODE_TO_PROXY_POINT[node]] - origin)
    )
    local = NODE_LOCAL_BODY_SEGMENT[node]
    clearances: dict[str, dict[str, Any]] = {}
    for envelope, radii in RADIUS_ENVELOPES_M.items():
        candidates = []
        per_occluder = []
        for name, (start, stop, kind) in BODY_SEGMENTS.items():
            if name == local:
                continue
            full_distance = _segment_distance(
                origin, anchor, world_joints[start], world_joints[stop]
            )
            trimmed_distance = _segment_distance(
                trimmed_origin, anchor, world_joints[start], world_joints[stop]
            )
            intervals = _ray_capsule_intervals(
                origin, anchor, world_joints[start], world_joints[stop], radii[kind]
            )
            # Never clip an origin/near-field-connected chord and relabel its
            # far tail as usable blockage.  The whole connected interval is
            # ambiguous; only a disjoint interval starting beyond 0.15 m is
            # a usable proxy obstruction.
            ambiguous_intervals = [
                (lo, hi) for lo, hi in intervals if lo < NEAR_FIELD_M
            ]
            trimmed_intervals = _merge_intervals(
                (lo, hi) for lo, hi in intervals if lo >= NEAR_FIELD_M
            )
            tag_to_centerline = _segment_distance(
                origin, origin, world_joints[start], world_joints[stop]
            )
            angular_radius = math.asin(min(1.0, radii[kind] / max(tag_to_centerline, radii[kind])))
            solid_angle = 2.0 * math.pi * (1.0 - math.cos(angular_radius))
            closest_fraction = _closest_ray_fraction(
                origin, anchor, world_joints[start], world_joints[stop]
            )
            per_occluder.append({
                "segment": name,
                "kind": kind,
                "radius_m": float(radii[kind]),
                "centerline_distance_full_ray_m": float(full_distance),
                "centerline_distance_trimmed_ray_m": float(trimmed_distance),
                "normalized_signed_clearance": float((trimmed_distance - radii[kind]) / radii[kind]),
                "full_ray_chord_intervals_m_from_tag": [[float(lo), float(hi)] for lo, hi in intervals],
                "usable_ray_chord_intervals_m_from_tag": [[float(lo), float(hi)] for lo, hi in trimmed_intervals],
                "near_field_ambiguous_chord_intervals_m_from_tag": [[float(lo), float(hi)] for lo, hi in ambiguous_intervals],
                "usable_chord_length_m": float(sum(hi - lo for lo, hi in trimmed_intervals)),
                "closest_approach_fraction": closest_fraction,
                "first_usable_intersection_fraction": (
                    float(trimmed_intervals[0][0] / length) if trimmed_intervals else None
                ),
                "angular_radius_proxy_rad": float(angular_radius),
                "solid_angle_proxy_sr": float(solid_angle),
                "angular_proxy_semantics": "TAG_VIEW_BOUNDING_CAPSULE_ENVELOPE_NOT_OCCLUDED_ANTENNA_AREA",
            })
            candidates.append((
                trimmed_distance - radii[kind],
                full_distance - radii[kind],
                name,
            ))
        minimum = min(candidates)
        full_minimum = min(candidates, key=lambda row: row[1])
        union_intervals = _merge_intervals(
            interval
            for row in per_occluder
            for interval in row["usable_ray_chord_intervals_m_from_tag"]
        )
        any_ambiguous = any(
            row["near_field_ambiguous_chord_intervals_m_from_tag"]
            for row in per_occluder
        )
        state = (
            "OTHER_BODY_BLOCKED_PROXY"
            if union_intervals
            else (
                "OTHER_BODY_NEAR_FIELD_AMBIGUOUS"
                if any_ambiguous else "OTHER_BODY_CLEAR_PROXY"
            )
        )
        usable_rows = [
            row for row in per_occluder
            if row["usable_ray_chord_intervals_m_from_tag"]
        ]
        confirmatory_rows = (
            usable_rows if usable_rows else ([] if any_ambiguous else per_occluder)
        )
        usable_normalized_clearance = (
            min(row["normalized_signed_clearance"] for row in usable_rows)
            if usable_rows
            else (
                min(row["normalized_signed_clearance"] for row in per_occluder)
                if not any_ambiguous else None
            )
        )
        clearances[envelope] = {
            "state": state,
            "minimum_trimmed_clearance_m": float(minimum[0]),
            "minimum_full_ray_clearance_m": float(full_minimum[1]),
            "closest_trimmed_segment": minimum[2],
            "closest_full_ray_segment": full_minimum[2],
            "minimum_normalized_signed_clearance": float(min(
                row["normalized_signed_clearance"] for row in per_occluder
            )),
            "minimum_usable_normalized_signed_clearance": (
                None if usable_normalized_clearance is None
                else float(usable_normalized_clearance)
            ),
            "union_usable_chord_intervals_m_from_tag": [
                [float(lo), float(hi)] for lo, hi in union_intervals
            ],
            "union_usable_chord_length_m": float(sum(hi - lo for lo, hi in union_intervals)),
            "maximum_angular_radius_proxy_rad": float(max(
                row["angular_radius_proxy_rad"] for row in per_occluder
            )),
            "maximum_solid_angle_proxy_sr": float(max(
                row["solid_angle_proxy_sr"] for row in per_occluder
            )),
            "maximum_usable_angular_radius_proxy_rad": (
                float(max(row["angular_radius_proxy_rad"] for row in confirmatory_rows))
                if confirmatory_rows else None
            ),
            "maximum_usable_solid_angle_proxy_sr": (
                float(max(row["solid_angle_proxy_sr"] for row in confirmatory_rows))
                if confirmatory_rows else None
            ),
            "per_occluder": per_occluder,
            "multiple_occluder_aggregate": "MIN_CLEARANCE_UNION_CHORD_INTERVALS_MAX_ANGULAR_ENVELOPE_NOT_SUM",
            "fresnel": {
                "status": "NOT_COMPUTED_UNKNOWN_EXACT_CHANNEL_WAVELENGTH",
                "numeric_claim": False,
            },
        }
    return {
        "ray_length_m": length,
        "emitter_local_segment_excluded": local,
        "emitter_reconstruction_error_m": reconstruction_error,
        "near_field_length_m": NEAR_FIELD_M,
        "envelopes": clearances,
    }


def _causal_geometry_features(
    target: dict[str, Any],
    link: SharedRangeLink,
    anchors: np.ndarray,
) -> tuple[np.ndarray, float, dict[str, Any]]:
    """Evaluate facing/body geometry from the pre-epoch prior only.

    The current-epoch ranges, including the other anchors used for LOO
    innovation, are deliberately absent from this geometry owner.
    """

    origin = np.asarray(target["causal_prior_tag_origin_m"], dtype=float)
    normal = np.asarray(target["prior"]["normals"][link.node], dtype=float)
    facing = outward_facing_score(origin, anchors[link.anchor], normal)
    body = _body_classification(
        node=link.node,
        origin=origin,
        anchor=anchors[link.anchor],
        joints_relative=target["prior"]["joints"],
    )
    return origin, facing, body


def _current_behavior_audit() -> dict[str, Any]:
    from biospur_fusion.c2_uwb_calibration import articulated_range, shared_root
    from biospur_fusion.c2_uwb_calibration import antenna_los

    link_fields = [field.name for field in fields(SharedRangeLink)]
    solver_source = inspect.getsource(shared_root.solve_shared_root)
    articulated_source = inspect.getsource(articulated_range.solve_articulated_ranges)
    los_source = inspect.getsource(antenna_los)
    forbidden = ("ray_intersection", "body_clearance", "capsule_intersection")
    if any(token in solver_source + articulated_source for token in forbidden):
        raise RuntimeError("unexpected current solver body-intersection owner")
    return {
        "shared_range_link_fields": link_fields,
        "shared_root_accepts_body_occlusion_feature": False,
        "articulated_range_accepts_body_occlusion_feature": False,
        "own_antenna_facing_field_present": "facing_score" in link_fields,
        "antenna_los_exports_ray_body_intersection": any(
            token in los_source for token in forbidden
        ),
        "proof": (
            "SharedRangeLink carries range, tag offset, per-link time, sigma, and "
            "optional own-facing score only. Both production range objectives have "
            "no ray/body clearance input or intersection call."
        ),
        "source_sha256": {
            "shared_root.py": _sha256(Path(inspect.getsourcefile(shared_root))),
            "articulated_range.py": _sha256(Path(inspect.getsourcefile(articulated_range))),
            "antenna_los.py": _sha256(Path(inspect.getsourcefile(antenna_los))),
        },
    }


def _summaries(rows: list[dict[str, Any]], geometry_rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        raise ValueError("no successful leave-one-link-out rows")
    output: dict[str, Any] = {
        "by_action_and_envelope": {},
        "within_fixed_strata_continuous_association": {},
        "stratified_calibration": [],
    }
    actions = sorted({row["action"] for row in rows})
    for action in actions:
        action_rows = [row for row in rows if row["action"] == action]
        for envelope in RADIUS_ENVELOPES_M:
            key = f"{action}/{envelope}"
            blocked = np.asarray([
                row["positive_loo_innovation_m"] for row in action_rows
                if row["occlusion"][envelope]["state"] == "OTHER_BODY_BLOCKED_PROXY"
            ], dtype=float)
            clear = np.asarray([
                row["positive_loo_innovation_m"] for row in action_rows
                if row["occlusion"][envelope]["state"] == "OTHER_BODY_CLEAR_PROXY"
            ], dtype=float)
            ambiguous = sum(
                row["occlusion"][envelope]["state"] == "OTHER_BODY_NEAR_FIELD_AMBIGUOUS"
                for row in action_rows
            )
            blocked_epochs = {
                row["epoch_index"] for row in action_rows
                if row["occlusion"][envelope]["state"] == "OTHER_BODY_BLOCKED_PROXY"
            }
            clear_epochs = {
                row["epoch_index"] for row in action_rows
                if row["occlusion"][envelope]["state"] == "OTHER_BODY_CLEAR_PROXY"
            }
            epoch_ids = sorted({row["epoch_index"] for row in action_rows})
            rng = np.random.default_rng(20260905)
            boot = []
            by_epoch = defaultdict(list)
            for row in action_rows:
                by_epoch[row["epoch_index"]].append(row)
            if len(blocked) and len(clear):
                for _ in range(500):
                    sampled = rng.choice(epoch_ids, size=len(epoch_ids), replace=True)
                    sample_rows = [row for epoch in sampled for row in by_epoch[int(epoch)]]
                    b = [row["positive_loo_innovation_m"] for row in sample_rows if row["occlusion"][envelope]["state"] == "OTHER_BODY_BLOCKED_PROXY"]
                    c = [row["positive_loo_innovation_m"] for row in sample_rows if row["occlusion"][envelope]["state"] == "OTHER_BODY_CLEAR_PROXY"]
                    if b and c:
                        boot.append(float(np.mean(b) - np.mean(c)))
            signed_blocked = np.asarray([
                row["signed_loo_innovation_m"] for row in action_rows
                if row["occlusion"][envelope]["state"] == "OTHER_BODY_BLOCKED_PROXY"
            ], dtype=float)
            signed_clear = np.asarray([
                row["signed_loo_innovation_m"] for row in action_rows
                if row["occlusion"][envelope]["state"] == "OTHER_BODY_CLEAR_PROXY"
            ], dtype=float)
            output["by_action_and_envelope"][key] = {
                "blocked_count": int(len(blocked)),
                "clear_count": int(len(clear)),
                "near_field_ambiguous_count": int(ambiguous),
                "blocked_epoch_cluster_count": int(len(blocked_epochs)),
                "clear_epoch_cluster_count": int(len(clear_epochs)),
                "adequate_epoch_cluster_support": bool(
                    len(blocked_epochs) >= MINIMUM_BLOCKED_AND_CLEAR_EPOCH_CLUSTERS
                    and len(clear_epochs) >= MINIMUM_BLOCKED_AND_CLEAR_EPOCH_CLUSTERS
                ),
                "blocked_positive_innovation_mean_m": float(np.mean(blocked)) if len(blocked) else None,
                "clear_positive_innovation_mean_m": float(np.mean(clear)) if len(clear) else None,
                "blocked_positive_innovation_median_m": float(np.median(blocked)) if len(blocked) else None,
                "clear_positive_innovation_median_m": float(np.median(clear)) if len(clear) else None,
                "blocked_minus_clear_mean_m": float(np.mean(blocked) - np.mean(clear)) if len(blocked) and len(clear) else None,
                "epoch_cluster_bootstrap_95_ci_m": (
                    [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))]
                    if boot else None
                ),
                "positive_over_one_sigma_fraction_blocked": float(np.mean(signed_blocked > np.asarray([
                    row["sigma_m"] for row in action_rows if row["occlusion"][envelope]["state"] == "OTHER_BODY_BLOCKED_PROXY"
                ]))) if len(signed_blocked) else None,
                "positive_over_one_sigma_fraction_clear": float(np.mean(signed_clear > np.asarray([
                    row["sigma_m"] for row in action_rows if row["occlusion"][envelope]["state"] == "OTHER_BODY_CLEAR_PROXY"
                ]))) if len(signed_clear) else None,
            }

            # Fixed bins are descriptive controls for own-facing, range, and
            # node.  No coefficient, composite score, or envelope is fitted.
            strata: dict[tuple[str, int, int, int], list[dict[str, Any]]] = defaultdict(list)
            association_strata: dict[tuple[str, int, int, int], list[dict[str, Any]]] = defaultdict(list)
            for row in action_rows:
                facing_bin = int(np.searchsorted(FACING_BINS, row["facing_score"], side="right") - 1)
                range_bin = int(np.searchsorted(RANGE_BINS_M, row["measured_range_m"], side="right") - 1)
                stratum = (row["node"], int(row["anchor"]), facing_bin, range_bin)
                strata[stratum].append(row)
                if row["occlusion"][envelope]["state"] != "OTHER_BODY_NEAR_FIELD_AMBIGUOUS":
                    association_strata[stratum].append(row)
            descriptor_names = {
                "obstruction_depth_normalized": lambda occ: -float(occ["minimum_usable_normalized_signed_clearance"]),
                "union_chord_length_m": lambda occ: float(occ["union_usable_chord_length_m"]),
                "maximum_solid_angle_proxy_sr": lambda occ: float(occ["maximum_usable_solid_angle_proxy_sr"]),
            }
            association = {}
            for descriptor, getter in descriptor_names.items():
                centred_x, centred_y = [], []
                qualified_strata = 0
                for values in association_strata.values():
                    if len(values) < 3:
                        continue
                    x = np.asarray([getter(row["occlusion"][envelope]) for row in values])
                    y = np.asarray([row["positive_loo_innovation_m"] for row in values])
                    if np.ptp(x) <= 1e-12:
                        continue
                    centred_x.extend((x - np.mean(x)).tolist())
                    centred_y.extend((y - np.mean(y)).tolist())
                    qualified_strata += 1
                correlation = (
                    float(np.corrcoef(centred_x, centred_y)[0, 1])
                    if len(centred_x) >= 3 and np.std(centred_x) > 0.0 and np.std(centred_y) > 0.0
                    else None
                )
                association[descriptor] = {
                    "within_strata_correlation": correlation,
                    "sample_count": int(len(centred_x)),
                    "qualified_strata": int(qualified_strata),
                }
            output["within_fixed_strata_continuous_association"][key] = association
            for (node, anchor, facing_bin, range_bin), values in sorted(strata.items()):
                states = Counter(row["occlusion"][envelope]["state"] for row in values)
                output["stratified_calibration"].append({
                    "action": action,
                    "envelope": envelope,
                    "node": node,
                    "anchor": anchor,
                    "facing_bin": facing_bin,
                    "facing_bounds": FACING_BINS[[facing_bin, facing_bin + 1]].tolist(),
                    "range_bin": range_bin,
                    "range_bounds_m": [float(RANGE_BINS_M[range_bin]), None if math.isinf(RANGE_BINS_M[range_bin + 1]) else float(RANGE_BINS_M[range_bin + 1])],
                    "count": len(values),
                    "states": dict(states),
                    "positive_innovation_mean_m": float(np.mean([row["positive_loo_innovation_m"] for row in values])),
                    "positive_over_one_sigma_fraction": float(np.mean([row["signed_loo_innovation_m"] > row["sigma_m"] for row in values])),
                })

    for envelope in RADIUS_ENVELOPES_M:
        values = [row for row in geometry_rows if row["envelope"] == envelope]
        output.setdefault("remaining_anchor_geometry", {})[envelope] = {
            "node_sweeps": int(len(values)),
            "count_histogram": dict(sorted(Counter(row["remaining_count"] for row in values).items())),
            "rank_loss_count": int(sum(row["rank"] < 3 for row in values)),
            "fewer_than_four_count": int(sum(row["remaining_count"] < 4 for row in values)),
            "smallest_singular_minimum": float(min(row["smallest_singular"] for row in values)) if values else None,
            "condition_p95": float(np.quantile([row["condition"] for row in values if row["condition"] is not None], .95)) if any(row["condition"] is not None for row in values) else None,
        }
    for envelope in RADIUS_ENVELOPES_M:
        descriptor_rows = {}
        for action in actions:
            descriptor_rows[action] = output[
                "within_fixed_strata_continuous_association"
            ].get(f"{action}/{envelope}", {})
        consistency = {}
        for descriptor in (
            "obstruction_depth_normalized",
            "union_chord_length_m",
            "maximum_solid_angle_proxy_sr",
        ):
            values = [
                descriptor_rows.get(action, {}).get(descriptor, {}).get(
                    "within_strata_correlation"
                )
                for action in actions
            ]
            consistency[descriptor] = {
                "correlation_by_action": dict(zip(actions, values)),
                "same_nonzero_sign_across_actions": bool(
                    len(values) == 2
                    and all(value is not None and value != 0.0 for value in values)
                    and math.copysign(1.0, values[0]) == math.copysign(1.0, values[1])
                ),
            }
        output.setdefault("cross_action_sign_consistency", {})[envelope] = consistency
    return output


def run(args: argparse.Namespace) -> dict[str, Any]:
    started = time.perf_counter()
    if args.output.exists():
        raise FileExistsError(args.output)
    clocks = _clock_models(args.clock)
    bridges = _beacon_boundary_bridges(args.clock)
    anchors, delays, tag_delay, layout_sigma = _load_layout()
    calibration = load_frozen_c2_3a()
    holdout = load_frozen_c2_hxx_diagnostics()
    alignment, frozen_forward = frozen_world_alignment(calibration)
    trajectory, _hinge_model, analytic_owner = _load_analytic_pose_owner(
        args.analytic_result,
        args.calibration_report,
        args.pre_ik_report,
    )
    body = FrozenHoldoutBodyProxy.create(
        holdout, calibration, trajectory=trajectory
    )
    pre_ik_document = json.loads(args.pre_ik_report.read_text(encoding="utf-8"))
    if _sha256(args.pre_ik_report) != analytic_owner["pre_ik_hxx_report_sha256"]:
        raise RuntimeError("absolute native200 clock report hash binding changed")
    current_behavior = _current_behavior_audit()

    episodes = {
        action: _load_holdout(action, clocks, bridges) for action in args.actions
    }
    projected_link_rows = sum(
        len(_valid_slots(raw))
        for action in args.actions
        for group in episodes[action]["groups"][::args.epoch_stride]
        for raw in group
    )
    projected_output_bytes = (
        projected_link_rows * PROJECTED_SERIALIZED_BYTES_PER_LINK
        + PROJECTED_FIXED_OUTPUT_BYTES
    )
    size_projection = {
        "projected_link_rows": int(projected_link_rows),
        "fixed_bytes_per_link": PROJECTED_SERIALIZED_BYTES_PER_LINK,
        "fixed_other_output_allowance_bytes": PROJECTED_FIXED_OUTPUT_BYTES,
        "projected_total_bytes": int(projected_output_bytes),
        "hard_cap_bytes": DISK_CAP_BYTES,
        "pass": bool(projected_output_bytes <= DISK_CAP_BYTES),
    }
    if not size_projection["pass"]:
        raise RuntimeError("pre-run output size projection exceeds 200 MB cap")
    args.output.mkdir(parents=True)
    _write_json(args.output / "CURRENT_BEHAVIOR_AUDIT.json", current_behavior)

    link_rows: list[dict[str, Any]] = []
    geometry_rows: list[dict[str, Any]] = []
    action_audit: dict[str, Any] = {}
    for action_number, action in enumerate(args.actions):
        elapsed = time.perf_counter() - started
        if elapsed > args.soft_timeout_s and action_number > 0:
            action_audit[action] = {"status": "SKIPPED_SOFT_STOP"}
            break
        episode = episodes[action]
        synchronization = pre_ik_document["synchronization"][action]
        pose = StrictCausalPose(
            body,
            action,
            tuple(int(value) for value in synchronization["actual_common_interval_ns"]),
            int(synchronization["grid_period_ns"]),
            int(synchronization["grid_rows"]),
            alignment,
        )
        room_initial = np.array([
            float(np.mean(anchors[:, 0])),
            float(np.mean(anchors[:, 1])),
            0.95,
        ])
        tracker = _tracker(room_initial)
        failures = Counter()
        ages = []
        attempted_loo_links = 0
        action_start_rows = len(link_rows)
        retained_groups = episode["groups"][::args.epoch_stride]
        for sampled_index, group in enumerate(retained_groups):
            if time.perf_counter() - started > args.hard_timeout_s:
                raise TimeoutError("O0 hard wall reached")
            reference_time = _reference_time(group, clocks)
            predicted_root, dt = _prediction(tracker, reference_time)
            exact = _exact_links(
                group,
                pose=pose,
                anchors=anchors,
                delays=delays,
                tag_delay=tag_delay,
                layout_sigma=layout_sigma,
                clocks=clocks,
                reference_time_s=reference_time,
                predicted_root=predicted_root,
                velocity=tracker["velocity"],
            )
            if len(exact) < 5:
                failures["GROUP_FEWER_THAN_FIVE_LINKS"] += 1
                continue
            exact_identities = [
                (row["link"].node, int(row["link"].anchor)) for row in exact
            ]
            if len(exact_identities) != len(set(exact_identities)):
                raise RuntimeError("duplicate node+anchor observation in exact epoch")
            attempted_loo_links += len(exact)
            epoch_rows = []
            for target_index, target in enumerate(exact):
                shared_training_rows = _loo_training_rows(
                    exact, target_index, same_node=False
                )
                same_node_training_rows = _loo_training_rows(
                    exact, target_index, same_node=True
                )
                shared_training = [row["link"] for row in shared_training_rows]
                same_node_training = [row["link"] for row in same_node_training_rows]
                target_identity = (target["link"].node, int(target["link"].anchor))
                shared_identities = {
                    (link.node, int(link.anchor)) for link in shared_training
                }
                same_node_identities = {
                    (link.node, int(link.anchor)) for link in same_node_training
                }
                tested_link_excluded = bool(
                    target_identity not in shared_identities
                    and target_identity not in same_node_identities
                )
                if not tested_link_excluded:
                    raise RuntimeError("tested link leaked into LOO training identities")
                if len(same_node_training) < 4:
                    failures["SAME_NODE_LOO_FEWER_THAN_FOUR_LINKS"] += 1
                    continue
                solved = solve_shared_root(
                    same_node_training,
                    anchors_m=anchors,
                    initial_root_m=predicted_root,
                    root_velocity_mps=tracker["velocity"],
                )
                if not solved.success:
                    failures[f"SAME_NODE_{solved.reason}"] += 1
                    continue
                shared_solved = solve_shared_root(
                    shared_training,
                    anchors_m=anchors,
                    initial_root_m=predicted_root,
                    root_velocity_mps=tracker["velocity"],
                )
                if not shared_solved.success:
                    failures[f"SHARED_ROOT_{shared_solved.reason}"] += 1
                    continue
                link = target["link"]
                tag_origin = (
                    solved.root_position_m
                    + link.tag_offset_world_m
                    + link.link_dt_s * tracker["velocity"]
                )
                predicted_range = float(np.linalg.norm(anchors[link.anchor] - tag_origin))
                signed = _signed_range_innovation_m(link.range_m, predicted_range)
                shared_tag_origin = (
                    shared_solved.root_position_m
                    + link.tag_offset_world_m
                    + link.link_dt_s * tracker["velocity"]
                )
                shared_signed = _signed_range_innovation_m(
                    link.range_m,
                    float(np.linalg.norm(anchors[link.anchor] - shared_tag_origin)),
                )
                geometry_origin, facing, body_result = _causal_geometry_features(
                    target, link, anchors
                )
                if body_result["emitter_reconstruction_error_m"] > 1e-12:
                    raise RuntimeError("LOO body translation reconstruction failed")
                ages.append(float(target["prior"]["pose_age_s"]))
                row = {
                    "action": action,
                    "epoch_index": int(sampled_index * args.epoch_stride),
                    "sampled_epoch_index": int(sampled_index),
                    "node": link.node,
                    "boot": int(target["raw"].boot),
                    "sweep": int(target["raw"].sweep),
                    "anchor": int(link.anchor),
                    "strobe_time_s": float(clocks[link.node].seconds(target["raw"].strobe_us)),
                    "link_time_s": float(target["link_time_s"]),
                    "t_round_us": float(target["t_round_us"]),
                    "reference_time_s": float(reference_time),
                    "link_dt_s": float(link.link_dt_s),
                    "pose_frame": int(target["prior"]["frame"]),
                    "pose_absolute_time_s": float(target["prior"]["pose_absolute_time_s"]),
                    "pose_age_s": float(target["prior"]["pose_age_s"]),
                    "strict_pose_before_link": bool(target["prior"]["pose_absolute_time_s"] < target["link_time_s"]),
                    "loo_scope": "SAME_NODE_HELD_ANCHOR_PRIMARY",
                    "loo_training_link_count": int(len(same_node_training)),
                    "shared_root_loo_scope": "SHARED_ROOT_LOO_DIAGNOSTIC_MIXES_OTHER_NODE_AND_FK_ERROR",
                    "shared_root_loo_training_link_count": int(len(shared_training)),
                    "tested_link_excluded_from_root": tested_link_excluded,
                    "tested_link_identity": [target_identity[0], target_identity[1]],
                    "loo_rank": int(solved.rank),
                    "loo_condition": float(solved.condition),
                    "loo_root_reference_m": solved.root_position_m.tolist(),
                    "causal_velocity_mps": np.asarray(tracker["velocity"]).tolist(),
                    "tag_origin_loo_m": tag_origin.tolist(),
                    "causal_prior_tag_origin_m": np.asarray(
                        target["causal_prior_tag_origin_m"]
                    ).tolist(),
                    "occlusion_geometry_origin_m": geometry_origin.tolist(),
                    "current_epoch_ranges_used_for_occlusion_geometry": False,
                    "anchor_position_m": anchors[link.anchor].tolist(),
                    "measured_range_m": float(link.range_m),
                    "predicted_range_m": predicted_range,
                    "signed_loo_innovation_m": signed,
                    "positive_loo_innovation_m": max(0.0, signed),
                    "shared_root_signed_loo_innovation_m": shared_signed,
                    "shared_root_positive_loo_innovation_m": max(0.0, shared_signed),
                    "sigma_m": float(link.sigma_m),
                    "facing_score": facing,
                    "emitter_local_segment_excluded": body_result["emitter_local_segment_excluded"],
                    "near_field_length_m": body_result["near_field_length_m"],
                    "emitter_reconstruction_error_m": body_result["emitter_reconstruction_error_m"],
                    "occlusion": body_result["envelopes"],
                }
                link_rows.append(row)
                epoch_rows.append(row)

            # Hypothetical-mask geometry is diagnostic only; it never feeds a solve.
            by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in epoch_rows:
                by_node[row["node"]].append(row)
            for node, node_rows in by_node.items():
                for envelope in RADIUS_ENVELOPES_M:
                    remaining = [
                        row for row in node_rows
                        if row["occlusion"][envelope]["state"] != "OTHER_BODY_BLOCKED_PROXY"
                    ]
                    svd = _geometry_svd(
                        np.asarray([
                            row["causal_prior_tag_origin_m"] for row in remaining
                        ]),
                        np.asarray([row["anchor_position_m"] for row in remaining]),
                    )
                    geometry_rows.append({
                        "action": action,
                        "epoch_index": int(sampled_index * args.epoch_stride),
                        "node": node,
                        "envelope": envelope,
                        "original_count": int(len(node_rows)),
                        "remaining_count": int(len(remaining)),
                        "jacobian_origin_owner": "PRE_EPOCH_TRACKER_PLUS_STRICT_CAUSAL_NATIVE200_OFFSET_AT_LINK_TIME",
                        "current_epoch_ranges_used_for_jacobian_origins": False,
                        **svd,
                    })

            all_result = solve_shared_root(
                [row["link"] for row in exact],
                anchors_m=anchors,
                initial_root_m=predicted_root,
                root_velocity_mps=tracker["velocity"],
            )
            if all_result.success:
                _update_tracker(
                    tracker, all_result.root_position_m, reference_time, dt
                )
            else:
                failures[f"ALL_LINK_{all_result.reason}"] += 1
        action_rows = link_rows[action_start_rows:]
        if not action_rows:
            raise RuntimeError(f"{action}: no LOO results")
        action_audit[action] = {
            "status": "COMPLETE",
            "raw_path": str(episode["raw"]),
            "raw_sha256": _sha256(episode["raw"]),
            "events_path": str(episode["events"]),
            "events_sha256": _sha256(episode["events"]),
            "available_complete_epochs": int(len(episode["groups"])),
            "sampled_epochs": int(len(retained_groups)),
            "epoch_stride_predeclared": int(args.epoch_stride),
            "successful_loo_links": int(len(action_rows)),
            "attempted_loo_links": int(attempted_loo_links),
            "complete_loo_coverage": bool(len(action_rows) == attempted_loo_links),
            "failures": dict(failures),
            "pose_age_s": {
                "minimum": float(np.min(ages)),
                "p50": float(np.median(ages)),
                "maximum": float(np.max(ages)),
                "strictly_positive_all": bool(np.all(np.asarray(ages) > 0.0)),
            },
            "native200_absolute_clock_binding": {
                "source": str(args.pre_ik_report),
                "sha256": analytic_owner["pre_ik_hxx_report_sha256"],
                **synchronization,
            },
        }

    summary = _summaries(link_rows, geometry_rows)
    owner_gate = {
        "both_h01_h02_complete": all(
            action_audit.get(action, {}).get("status") == "COMPLETE"
            for action in ("H01_boxing", "H02_golf")
        ),
        "all_pose_ages_strictly_positive_and_at_most_5_005ms": bool(
            link_rows
            and all(
                0.0 < row["pose_age_s"] <= MAXIMUM_CAUSAL_POSE_AGE_S
                for row in link_rows
            )
        ),
        "all_tested_links_omitted": bool(
            link_rows and all(row["tested_link_excluded_from_root"] for row in link_rows)
        ),
        "all_emitter_reconstructions_exact": bool(
            link_rows
            and max(row["emitter_reconstruction_error_m"] for row in link_rows) <= 1e-12
        ),
        "all_sampled_links_have_successful_loo": bool(all(
            action_audit.get(action, {}).get("complete_loo_coverage") is True
            for action in ("H01_boxing", "H02_golf")
        )),
    }
    association_gate = {}
    for envelope in RADIUS_ENVELOPES_M:
        h01 = summary["by_action_and_envelope"].get(f"H01_boxing/{envelope}", {})
        h02 = summary["by_action_and_envelope"].get(f"H02_golf/{envelope}", {})
        h01_ci = h01.get("epoch_cluster_bootstrap_95_ci_m")
        fixed = summary["within_fixed_strata_continuous_association"]
        correlations = [
            fixed.get(f"{action}/{envelope}", {})
            .get("obstruction_depth_normalized", {})
            .get("within_strata_correlation")
            for action in ("H01_boxing", "H02_golf")
        ]
        association_gate[envelope] = {
            "adequate_h01_epoch_clusters": bool(h01.get("adequate_epoch_cluster_support")),
            "adequate_h02_epoch_clusters": bool(h02.get("adequate_epoch_cluster_support")),
            "h01_bootstrap_ci_strictly_positive": bool(h01_ci and h01_ci[0] > 0.0),
            "h02_direction_positive": bool(
                h02.get("blocked_minus_clear_mean_m") is not None
                and h02["blocked_minus_clear_mean_m"] > 0.0
            ),
            "fixed_strata_do_not_reverse": bool(
                all(value is not None and value >= 0.0 for value in correlations)
            ),
        }
    geometry_gate = {
        envelope: {
            "all_hypothetical_masks_keep_at_least_four": bool(all(
                row["remaining_count"] >= 4
                for row in geometry_rows if row["envelope"] == envelope
            )),
            "all_hypothetical_masks_rank3": bool(all(
                row["rank"] == 3
                for row in geometry_rows if row["envelope"] == envelope
            )),
            "all_hypothetical_masks_finite_condition_at_most_1e8": bool(all(
                row["condition"] is not None
                and row["condition"] <= MAXIMUM_GEOMETRY_CONDITION
                for row in geometry_rows if row["envelope"] == envelope
            )),
        }
        for envelope in RADIUS_ENVELOPES_M
    }
    predeclared_gate = {
        "declared_before_result_inspection": True,
        "thresholds": {
            "minimum_blocked_and_clear_epoch_clusters_per_action_envelope": MINIMUM_BLOCKED_AND_CLEAR_EPOCH_CLUSTERS,
            "maximum_causal_pose_age_s": MAXIMUM_CAUSAL_POSE_AGE_S,
            "maximum_geometry_condition": MAXIMUM_GEOMETRY_CONDITION,
            "emitter_reconstruction_tolerance_m": 1e-12,
        },
        "owner_gate": owner_gate,
        "association_gate": association_gate,
        "geometry_gate": geometry_gate,
    }
    ownership_pass = all(owner_gate.values())
    association_pass = all(
        all(values.values()) for values in association_gate.values()
    )
    geometry_pass = all(
        all(values.values()) for values in geometry_gate.values()
    )
    status = (
        "BLOCKED_CAUSAL_OR_COVERAGE_GATE"
        if not ownership_pass
        else (
            "READY_FOR_O1_FIXTURE_ONLY"
            if association_pass and geometry_pass
            else "DIAGNOSTIC_ONLY_NOT_PROMOTED"
        )
    )
    result = {
        "schema": "biospur.c2.dynamic_other_body_occlusion_o0.v1",
        "status": status,
        "scientific_pass": False,
        "production_changes": False,
        "range_selection_or_deletion_performed": False,
        "selected_link_solver_performed": False,
        "current_behavior": current_behavior,
        "ownership": {
            "clock": "BEACON_LBD_B306_TIMER2_COMMON_CLOCK",
            "range_epoch": "STROBE_PLUS_PER_ANCHOR_T_ROUND_OVER_2",
            "pose": analytic_owner,
            "pose_query": "STRICT_FLOOR_BEFORE_EACH_LINK_TIME",
            "root_prediction": "TESTED_LINK_OMITTED_CURRENT_EPOCH; PRIOR_EPOCH_TRACKER_ONLY",
            "signed_innovation": "CORRECTED_MEASURED_MINUS_PREDICTED_AT_PER_LINK_TIME_METRES",
            "body_geometry": "NATIVE200_ANALYTIC_PUBLIC_FK_PROXY_CAPSULES",
        },
        "geometry_proxy_boundary": {
            "radius_envelopes_m": RADIUS_ENVELOPES_M,
            "all_envelopes_reported_no_selection": True,
            "own_local_segment_excluded": True,
            "near_field_m": NEAR_FIELD_M,
            "near_field_intersections_labelled_ambiguous_not_silently_removed": True,
            "not_measured_body_surface_or_los_truth": True,
            "world_alignment": alignment.tolist(),
            "frozen_forward": frozen_forward.tolist(),
        },
        "clock": {
            "path": str(args.clock),
            "sha256": _sha256(args.clock),
        },
        "analytic_owner": analytic_owner,
        "action_audit": action_audit,
        "summary": summary,
        "predeclared_gate": predeclared_gate,
        "counts": {
            "link_rows": int(len(link_rows)),
            "geometry_rows": int(len(geometry_rows)),
        },
        "output_size_projection": size_projection,
        "interpretation": (
            "Associations are diagnostic proxy evidence. Positive LOO innovation is "
            "not NLOS ground truth; capsule radii are sensitivity envelopes; no link "
            "was deleted or downweighted."
        ),
        "wall_s": float(time.perf_counter() - started),
    }
    with (args.output / "LINK_ROWS.jsonl").open("w", encoding="utf-8") as stream:
        for row in link_rows:
            stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
    with (args.output / "GEOMETRY_ROWS.jsonl").open("w", encoding="utf-8") as stream:
        for row in geometry_rows:
            stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
    _write_json(args.output / "RESULT.json", result)
    report = [
        "# Phase O0 dynamic other-body self-occlusion audit",
        "",
        "This is a read-only proxy association study, not a LOS classifier or a solver change.",
        "",
        f"- Status: `{result['status']}`",
        f"- Scored leave-one-link-out ranges: {len(link_rows)}",
        f"- Wall time: {result['wall_s']:.3f} s",
        "- The current solver has own-antenna facing only; it has no other-body ray/capsule feature.",
        "- Every tested link was omitted from its root prediction and used a strictly earlier native-200 pose.",
        "- All three body-radius envelopes are reported; none was selected or fitted.",
        "",
        "See `RESULT.json`, `LINK_ROWS.jsonl`, and `GEOMETRY_ROWS.jsonl` for exact evidence.",
    ]
    (args.output / "REPORT.md").write_text("\n".join(report) + "\n")
    provenance = {
        "command": args.command,
        "tool": str(Path(__file__).resolve()),
        "tool_sha256": _sha256(Path(__file__).resolve()),
        "runtime_s": float(time.perf_counter() - started),
        "cpu_workers": 1,
        "gpu_used": False,
        "parameter_scan": False,
    }
    _write_json(args.output / "PROVENANCE.json", provenance)
    actual_before_audit = sum(
        path.stat().st_size for path in args.output.iterdir() if path.is_file()
    )
    disk_audit = {
        "schema": "biospur.c2.dynamic_other_body_occlusion_o0.disk.v1",
        "projected_total_bytes": int(projected_output_bytes),
        "actual_bytes_before_disk_audit_and_seal": int(actual_before_audit),
        "hard_cap_bytes": DISK_CAP_BYTES,
    }
    _write_json(args.output / "DISK_AUDIT.json", disk_audit)
    actual_before_seal = sum(
        path.stat().st_size for path in args.output.iterdir() if path.is_file()
    )
    estimated_seal_bytes = 96 * (len(list(args.output.iterdir())) + 1)
    if actual_before_seal + estimated_seal_bytes > DISK_CAP_BYTES:
        raise RuntimeError("actual O0 evidence would exceed 200 MB before seal")
    sealed = sorted(path for path in args.output.iterdir() if path.name != "SHA256SUMS")
    (args.output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in sealed)
    )
    actual_sealed_bytes = sum(
        path.stat().st_size for path in args.output.iterdir() if path.is_file()
    )
    if actual_sealed_bytes > DISK_CAP_BYTES:
        raise RuntimeError("sealed O0 evidence exceeds 200 MB")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--clock", type=Path, default=DEFAULT_CLOCK)
    parser.add_argument("--analytic-result", type=Path, default=DEFAULT_RESULT)
    parser.add_argument("--calibration-report", type=Path, default=DEFAULT_CALIBRATION_REPORT)
    parser.add_argument("--pre-ik-report", type=Path, default=DEFAULT_PRE_IK_REPORT)
    parser.add_argument("--actions", default="H01_boxing,H02_golf")
    parser.add_argument("--epoch-stride", type=int, default=6)
    parser.add_argument("--soft-timeout-s", type=float, default=900.0)
    parser.add_argument("--hard-timeout-s", type=float, default=2700.0)
    args = parser.parse_args()
    args.output = args.output.resolve()
    args.clock = args.clock.resolve()
    args.analytic_result = args.analytic_result.resolve()
    args.calibration_report = args.calibration_report.resolve()
    args.pre_ik_report = args.pre_ik_report.resolve()
    args.actions = tuple(value.strip() for value in args.actions.split(",") if value.strip())
    if not args.actions or any(value not in {"H01_boxing", "H02_golf"} for value in args.actions):
        raise ValueError("O0 supports H01_boxing and H02_golf only")
    if args.epoch_stride < 1 or not 0 < args.soft_timeout_s <= args.hard_timeout_s:
        raise ValueError("invalid stride/time budget")
    import sys
    args.command = " ".join(sys.argv)
    result = run(args)
    print(json.dumps({
        "status": result["status"],
        "counts": result["counts"],
        "wall_s": result["wall_s"],
        "output": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
