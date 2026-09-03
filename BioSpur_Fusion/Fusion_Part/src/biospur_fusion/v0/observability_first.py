"""Foundation checks for the observability-first pure-IMU V0 estimator.

This module deliberately contains no real-capture loader.  Milestone A uses it
to make the yaw gauge and the rejected one-common-yaw counterexample explicit
before the real-data estimator is changed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable

import numpy as np


SEGMENTS = (
    "pelvis",
    "torso",
    "upper_arm_left",
    "upper_arm_right",
    "forearm_left",
    "forearm_right",
    "thigh_left",
    "thigh_right",
    "shank_left",
    "shank_right",
)

# The physical joint graph is a tree, so its incidence matrix has rank N - 1.
JOINT_EDGES = (
    ("pelvis", "torso"),
    ("torso", "upper_arm_left"),
    ("torso", "upper_arm_right"),
    ("upper_arm_left", "forearm_left"),
    ("upper_arm_right", "forearm_right"),
    ("pelvis", "thigh_left"),
    ("pelvis", "thigh_right"),
    ("thigh_left", "shank_left"),
    ("thigh_right", "shank_right"),
)


def wrap_angle_rad(value: np.ndarray | float) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    return (value + math.pi) % (2.0 * math.pi) - math.pi


def heading_incidence_jacobian(
    segments: Iterable[str] = SEGMENTS,
    edges: Iterable[tuple[str, str]] = JOINT_EDGES,
) -> np.ndarray:
    """Return d(h_child - h_parent)/d(h_0, ..., h_9)."""

    segment_tuple = tuple(segments)
    index = {segment: column for column, segment in enumerate(segment_tuple)}
    rows = []
    for parent, child in edges:
        row = np.zeros(len(segment_tuple), dtype=float)
        row[index[parent]] = -1.0
        row[index[child]] = 1.0
        rows.append(row)
    return np.asarray(rows)


def heading_graph_observability(relative_threshold: float = 1e-12) -> dict[str, object]:
    """Audit the single legal common-yaw gauge analytically and numerically."""

    jacobian = heading_incidence_jacobian()
    _, singular, vh = np.linalg.svd(jacobian, full_matrices=True)
    threshold = float(singular[0] * relative_threshold)
    rank = int(np.sum(singular > threshold))
    null = vh[rank:]
    common = np.ones(len(SEGMENTS), dtype=float) / math.sqrt(len(SEGMENTS))
    alignment = float(np.max(np.abs(null @ common))) if len(null) else 0.0
    pelvis_column = SEGMENTS.index("pelvis")
    quotiented = np.delete(jacobian, pelvis_column, axis=1)
    q_singular = np.linalg.svd(quotiented, compute_uv=False)
    q_rank = int(np.sum(q_singular > q_singular[0] * relative_threshold))
    return {
        "schema": "biospur-v0-heading-graph-observability-v1",
        "segments": list(SEGMENTS),
        "joint_edges": [list(edge) for edge in JOINT_EDGES],
        "unquotiented": {
            "shape": list(jacobian.shape),
            "rank": rank,
            "nullity": int(jacobian.shape[1] - rank),
            "singular_values": singular.tolist(),
            "common_yaw_null_alignment": alignment,
            "analytic_identity": "B @ ones(10) == 0",
            "analytic_identity_max_abs": float(np.max(np.abs(jacobian @ np.ones(len(SEGMENTS))))),
        },
        "pelvis_gauge_quotient": {
            "shape": list(quotiented.shape),
            "rank": q_rank,
            "nullity": int(quotiented.shape[1] - q_rank),
            "singular_values": q_singular.tolist(),
            "pelvis_heading_fixed_to_zero": True,
        },
        "pass": bool(
            rank == len(SEGMENTS) - 1
            and len(null) == 1
            and alignment > 1.0 - 1e-10
            and q_rank == len(SEGMENTS) - 1
            and np.max(np.abs(jacobian @ np.ones(len(SEGMENTS)))) < 1e-12
        ),
    }


@dataclass(frozen=True)
class TposeAzimuthCounterexample:
    segment_names: tuple[str, ...]
    independent_frontend_yaw_deg: tuple[float, ...]

    @classmethod
    def decisive(cls) -> "TposeAzimuthCounterexample":
        # Four independent arm-chain gauges reproduce the failure scale seen in
        # the rejected V0 references.  Pelvis/common display yaw is already the
        # legal gauge and cannot remove their relative differences.
        return cls(
            segment_names=(
                "upper_arm_left",
                "forearm_left",
                "upper_arm_right",
                "forearm_right",
            ),
            independent_frontend_yaw_deg=(97.0, 172.0, -111.0, 146.0),
        )

    def evaluate(self) -> dict[str, object]:
        gauges = np.radians(np.asarray(self.independent_frontend_yaw_deg, dtype=float))
        # Least-squares circular common correction.  It is one display gauge,
        # not four independent physical heading coordinates.
        circular_mean = math.atan2(float(np.mean(np.sin(gauges))), float(np.mean(np.cos(gauges))))
        common_correction = -circular_mean
        common_error = np.abs(wrap_angle_rad(gauges + common_correction))
        relative_correction = -gauges
        relative_error = np.abs(wrap_angle_rad(gauges + relative_correction))
        common_deg = np.degrees(common_error)
        relative_deg = np.degrees(relative_error)
        return {
            "schema": "biospur-v0-one-common-yaw-tpose-counterexample-v1",
            "segments": list(self.segment_names),
            "independent_frontend_yaw_deg": list(self.independent_frontend_yaw_deg),
            "best_common_yaw_correction_deg": math.degrees(common_correction),
            "one_common_yaw_absolute_azimuth_error_deg": common_deg.tolist(),
            "one_common_yaw_q90_error_deg": float(np.quantile(common_deg, 0.90)),
            "one_common_yaw_max_error_deg": float(np.max(common_deg)),
            "nine_relative_heading_absolute_azimuth_error_deg": relative_deg.tolist(),
            "nine_relative_heading_max_error_deg": float(np.max(relative_deg)),
            "legacy_one_common_yaw_fails": bool(np.quantile(common_deg, 0.90) > 80.0),
            "required_nine_heading_formulation_passes": bool(np.max(relative_deg) < 1e-10),
        }


def foundation_qualification() -> dict[str, object]:
    heading = heading_graph_observability()
    counterexample = TposeAzimuthCounterexample.decisive().evaluate()
    return {
        "schema": "biospur-pure-imu-v0-observability-foundation-qualification-v1",
        "heading_graph": heading,
        "one_common_yaw_counterexample": counterexample,
        "real_capture_payload_accessed": False,
        "hxx_golf_boxing_payload_accessed": False,
        "pass": bool(
            heading["pass"]
            and counterexample["legacy_one_common_yaw_fails"]
            and counterexample["required_nine_heading_formulation_passes"]
        ),
    }
