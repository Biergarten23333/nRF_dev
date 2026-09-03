"""One-way display-proxy failure screens; never anatomical validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from .contracts import (
    CROSSING_LINK_PAIRS,
    LINK_ROWS,
    POINT_NAMES,
    SEGMENTS,
    WORLD_UP_DISPLAY,
    DisplayGeometry,
)
from .metrics import geodesic_steps_deg, linear_quantile


@dataclass(frozen=True)
class ProxyFrameResult:
    passed: bool
    failures: tuple[str, ...]
    points_a: Mapping[str, np.ndarray]
    points_b: Mapping[str, np.ndarray]


def forward_points(
    matrices: np.ndarray,
    geometry: DisplayGeometry,
    output_matrix: np.ndarray,
) -> dict[str, np.ndarray]:
    index = {name: position for position, name in enumerate(SEGMENTS)}
    r = {name: matrices[position] for name, position in index.items()}
    length = geometry.segment_lengths_m
    root = np.zeros(3, dtype=np.float64)
    shoulder_mid = root + r["torso"] @ np.array([0.0, 0.0, geometry.torso_height_m])
    shoulder_left = shoulder_mid + r["torso"] @ np.array([-0.5 * geometry.shoulder_span_m, 0.0, 0.0])
    shoulder_right = shoulder_mid + r["torso"] @ np.array([0.5 * geometry.shoulder_span_m, 0.0, 0.0])
    hip_left = root + r["pelvis"] @ np.array([-0.5 * geometry.hip_span_m, 0.0, 0.0])
    hip_right = root + r["pelvis"] @ np.array([0.5 * geometry.hip_span_m, 0.0, 0.0])

    def down(point: np.ndarray, segment: str) -> np.ndarray:
        return point + r[segment] @ np.array([0.0, 0.0, -length[segment]])

    elbow_left = down(shoulder_left, "upper_arm_left")
    wrist_left = down(elbow_left, "forearm_left")
    elbow_right = down(shoulder_right, "upper_arm_right")
    wrist_right = down(elbow_right, "forearm_right")
    knee_left = down(hip_left, "thigh_left")
    ankle_left = down(knee_left, "shank_left")
    knee_right = down(hip_right, "thigh_right")
    ankle_right = down(knee_right, "shank_right")
    internal = {
        "pelvis_center": root,
        "shoulder_mid": shoulder_mid,
        "shoulder_left": shoulder_left,
        "shoulder_right": shoulder_right,
        "hip_left": hip_left,
        "hip_right": hip_right,
        "elbow_left": elbow_left,
        "wrist_left": wrist_left,
        "elbow_right": elbow_right,
        "wrist_right": wrist_right,
        "knee_left": knee_left,
        "ankle_left": ankle_left,
        "knee_right": knee_right,
        "ankle_right": ankle_right,
    }
    return {name: output_matrix @ internal[name] for name in POINT_NAMES}


def link_records(points: Mapping[str, np.ndarray]) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    return {
        name: (np.array(points[start], copy=True), np.array(points[end], copy=True))
        for name, start, end in LINK_ROWS
    }


def _point_segment_distance(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    vector = end - start
    denominator = float(vector @ vector)
    if denominator <= 1e-30:
        return float(np.linalg.norm(point - start))
    fraction = float(np.clip(((point - start) @ vector) / denominator, 0.0, 1.0))
    return float(np.linalg.norm(point - (start + fraction * vector)))


def closed_segment_distance(
    p0: np.ndarray,
    p1: np.ndarray,
    q0: np.ndarray,
    q1: np.ndarray,
) -> float:
    u = p1 - p0
    v = q1 - q0
    w = p0 - q0
    a = float(u @ u)
    b = float(u @ v)
    c = float(v @ v)
    d = float(u @ w)
    e = float(v @ w)
    denominator = a * c - b * b
    if denominator <= 1e-15:
        return min(
            _point_segment_distance(p0, q0, q1),
            _point_segment_distance(p1, q0, q1),
            _point_segment_distance(q0, p0, p1),
            _point_segment_distance(q1, p0, p1),
        )
    s = float(np.clip((b * e - c * d) / denominator, 0.0, 1.0))
    t = float(np.clip((a * e - b * d) / denominator, 0.0, 1.0))
    for _ in range(2):
        s = float(np.clip((b * t - d) / a, 0.0, 1.0)) if a > 0.0 else 0.0
        t = float(np.clip((b * s + e) / c, 0.0, 1.0)) if c > 0.0 else 0.0
    return float(np.linalg.norm((p0 + s * u) - (q0 + t * v)))


def endpoint_failures(links: Mapping[str, tuple[np.ndarray, np.ndarray]]) -> list[str]:
    groups = {
        "shoulder_left": (links["shoulder_bar"][0], links["upper_arm_left"][0]),
        "shoulder_right": (links["shoulder_bar"][1], links["upper_arm_right"][0]),
        "hip_left": (links["hip_bar"][0], links["thigh_left"][0]),
        "hip_right": (links["hip_bar"][1], links["thigh_right"][0]),
        "elbow_left": (links["upper_arm_left"][1], links["forearm_left"][0]),
        "elbow_right": (links["upper_arm_right"][1], links["forearm_right"][0]),
        "knee_left": (links["thigh_left"][1], links["shank_left"][0]),
        "knee_right": (links["thigh_right"][1], links["shank_right"][0]),
    }
    return [name for name, (a, b) in groups.items() if np.linalg.norm(a - b) > 1e-12]


def screen_frame(
    a_matrices: np.ndarray,
    b_matrices: np.ndarray,
    geometry: DisplayGeometry,
    output_matrix: np.ndarray,
) -> ProxyFrameResult:
    points_a = forward_points(a_matrices, geometry, output_matrix)
    points_b = forward_points(b_matrices, geometry, output_matrix)
    links = link_records(points_b)
    failures: list[str] = []
    if not all(np.all(np.isfinite(value)) for value in points_b.values()):
        failures.append("NONFINITE_POINT")
    failures.extend(f"DISCONNECTED_{name}" for name in endpoint_failures(links))
    for name, (start, end) in links.items():
        length = float(np.linalg.norm(end - start))
        if not np.isfinite(length) or length < 0.05:
            failures.append(f"SHORT_LINK_{name}")
    for left, right in CROSSING_LINK_PAIRS:
        if closed_segment_distance(*links[left], *links[right]) < 0.005:
            failures.append(f"CROSSING_{left}_{right}")
    z_a = np.array([points_a[name][2] for name in POINT_NAMES])
    z_b = np.array([points_b[name][2] for name in POINT_NAMES])
    extent_a = float(np.max(z_a) - np.min(z_a))
    extent_b = float(np.max(z_b) - np.min(z_b))
    if extent_b < 0.35 and extent_b < 0.50 * extent_a:
        failures.append("SEVERE_VERTICAL_COLLAPSE")
    right_a = points_a["hip_right"] - points_a["hip_left"]
    norm = float(np.linalg.norm(right_a))
    if not np.isfinite(norm) or norm <= 1e-12:
        failures.append("MIRROR_REFERENCE_DEGENERATE")
    else:
        right_a /= norm
        for joint in ("shoulder", "hip", "knee", "ankle"):
            d_a = float((points_a[f"{joint}_right"] - points_a[f"{joint}_left"]) @ right_a)
            d_b = float((points_b[f"{joint}_right"] - points_b[f"{joint}_left"]) @ right_a)
            if abs(d_a) >= 0.05 and d_a * d_b < 0.0:
                failures.append(f"MIRROR_{joint}")
    return ProxyFrameResult(not failures, tuple(failures), points_a, points_b)


def standing_knee_split_failures(
    matrices: np.ndarray,
    time_s: np.ndarray,
    valid: np.ndarray,
    geometry: DisplayGeometry,
    output_matrix: np.ndarray,
) -> tuple[int, tuple[str, ...]]:
    steps, transition_mask = geodesic_steps_deg(matrices, valid)
    speed = np.full(len(time_s), np.nan, dtype=np.float64)
    speed[1:] = np.nanmax(steps[1:], axis=1) / np.diff(time_s)
    standing = np.zeros(len(time_s), dtype=bool)
    failures: list[str] = []
    for center in range(10, len(time_s) - 10):
        if not np.all(valid[center - 10 : center + 11]) or not np.all(
            transition_mask[center - 9 : center + 11]
        ):
            continue
        values = speed[center - 9 : center + 11]
        standing[center] = (
            linear_quantile(values, 0.5) <= 10.0
            and linear_quantile(values, 0.95) <= 20.0
        )
    run = 0
    maximum_run = 0
    for center in range(len(time_s)):
        split = False
        if standing[center]:
            points = forward_points(matrices[center], geometry, output_matrix)
            horizontal = points["hip_right"] - points["hip_left"]
            horizontal = horizontal - float(horizontal @ WORLD_UP_DISPLAY) * WORLD_UP_DISPLAY
            norm = float(np.linalg.norm(horizontal))
            if not np.isfinite(norm) or norm <= 1e-12:
                failures.append(f"STANDING_HIP_AXIS_DEGENERATE_{center}")
            else:
                body_right = horizontal / norm
                forward = np.cross(WORLD_UP_DISPLAY, body_right)
                forward_norm = float(np.linalg.norm(forward))
                if not np.isfinite(forward_norm) or forward_norm <= 1e-12:
                    failures.append(f"STANDING_FORWARD_DEGENERATE_{center}")
                else:
                    forward /= forward_norm
                    left = float((points["knee_left"] - points["hip_left"]) @ forward)
                    right = float((points["knee_right"] - points["hip_right"]) @ forward)
                    split = left * right < 0.0 and abs(left) > 0.03 and abs(right) > 0.03
        run = run + 1 if split else 0
        maximum_run = max(maximum_run, run)
    if maximum_run >= 10:
        failures.append(f"STANDING_KNEE_SPLIT_RUN_{maximum_run}")
    return maximum_run, tuple(failures)
