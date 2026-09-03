"""Direct two-sided scientific forward kinematics; no viewer repair path."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import hmac
import json
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .architecture_guard import C2ExecutionGuard, ClassAGuardViolation, ROOTED_EDGES
from .functional_geometry import EDGE_SPECS
from .segment_frames import EdgeConnectionVectors, SegmentFrameBranch


EDGE_NAME_BY_ENDPOINTS = {(parent, child): edge for edge, parent, child in EDGE_SPECS}


@dataclass(frozen=True)
class ScientificFKResult:
    segment_sensor_positions_m: Mapping[str, np.ndarray]
    shared_joint_positions_m: Mapping[str, np.ndarray]
    distal_landmark_positions_m: Mapping[str, np.ndarray]
    segment_sensor_position_covariance_m2: Mapping[str, np.ndarray]
    shared_joint_position_covariance_m2: Mapping[str, np.ndarray]
    shared_joint_closure_error_m: Mapping[str, float]
    report: Mapping[str, Any]


@dataclass(frozen=True)
class DirectOrientationAvatarResult:
    landmark_positions_m: Mapping[str, np.ndarray]
    line_segments_m: Mapping[str, np.ndarray]
    reference_line_segments_m: Mapping[str, np.ndarray]
    report: Mapping[str, Any]


@dataclass(frozen=True)
class PhysicalTrajectoryCandidateAssessment:
    branch_id: str
    physically_legal: bool
    rom_log_likelihood: float
    bilateral_log_likelihood: float
    gravity_log_likelihood: float
    soft_total_log_likelihood: float
    report: Mapping[str, Any]


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


def _binding_jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {
            str(key): _binding_jsonable(item)
            for key, item in sorted(value.items(), key=lambda row: str(row[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_binding_jsonable(item) for item in value]
    return value


def landmark_proxy_sensitivity_profiles(
    anthropometric_proxy: Mapping[str, Any],
) -> tuple[Mapping[str, Any], ...]:
    """Return raw-observation proxy profiles without midpoint or invented sigma."""

    expected = {
        "upper_arm_m": {"left": [0.31, 0.325], "right": [0.31, 0.325]},
        "forearm_m": {
            "left": [0.245, [0.26, 0.265]],
            "right": [0.245, [0.26, 0.265]],
        },
        "thigh_m": {"left": [0.48], "right": [0.48]},
        "shank_m": {"left": [0.43], "right": [0.43]},
        "torso_sensor_distance_m": [0.28],
        "shoulder_surface_proxy": {
            "biacromial_m": [0.4, 0.425],
            "chest_to_acromion_line_m": [0.14, 0.15],
        },
        "hip_surface_proxy": {"bitrochanteric_m": [0.335]},
    }
    for key, value in expected.items():
        if _binding_jsonable(anthropometric_proxy.get(key)) != value:
            raise ValueError(f"landmark proxy {key} differs from the exact raw observation registry")
    if (
        anthropometric_proxy.get("surface_readings_preserved_separately") is not True
        or anthropometric_proxy.get("surface_to_internal_truth_claimed") is not False
        or anthropometric_proxy.get("quantitative_mapping_covariance") is not None
    ):
        raise ValueError("landmark proxy authority lost raw separation or invented internal truth/sigma")
    fixed = {
        "thigh_left_m": 0.480,
        "thigh_right_m": 0.480,
        "shank_left_m": 0.430,
        "shank_right_m": 0.430,
    }
    rows = (
        {
            "profile_id": "RAW_OBSERVER_A_BILATERAL_ROWS",
            "upper_arm_left_m": 0.310, "upper_arm_right_m": 0.310,
            "forearm_left_m": 0.245, "forearm_right_m": 0.245,
            "shoulder_surface_breadth_m": 0.400,
            "chest_to_acromion_line_observation_m": 0.140,
        },
        {
            "profile_id": "RAW_OBSERVER_B_INTERVAL_LOW_BILATERAL_ROWS",
            "upper_arm_left_m": 0.325, "upper_arm_right_m": 0.325,
            "forearm_left_m": 0.260, "forearm_right_m": 0.260,
            "shoulder_surface_breadth_m": 0.425,
            "chest_to_acromion_line_observation_m": 0.150,
        },
        {
            "profile_id": "RAW_OBSERVER_B_INTERVAL_HIGH_BILATERAL_ROWS",
            "upper_arm_left_m": 0.325, "upper_arm_right_m": 0.325,
            "forearm_left_m": 0.265, "forearm_right_m": 0.265,
            "shoulder_surface_breadth_m": 0.425,
            "chest_to_acromion_line_observation_m": 0.150,
        },
        {
            "profile_id": "RAW_CROSS_SIDE_SENSITIVITY_LEFT_LOW_RIGHT_HIGH",
            "upper_arm_left_m": 0.310, "upper_arm_right_m": 0.325,
            "forearm_left_m": 0.260, "forearm_right_m": 0.265,
            "shoulder_surface_breadth_m": 0.400,
            "chest_to_acromion_line_observation_m": 0.140,
        },
        {
            "profile_id": "RAW_CROSS_SIDE_SENSITIVITY_LEFT_HIGH_RIGHT_LOW",
            "upper_arm_left_m": 0.325, "upper_arm_right_m": 0.310,
            "forearm_left_m": 0.265, "forearm_right_m": 0.260,
            "shoulder_surface_breadth_m": 0.425,
            "chest_to_acromion_line_observation_m": 0.150,
        },
    )
    return tuple({
        **row,
        **fixed,
        "hip_surface_breadth_m": 0.335,
        "torso_surface_proxy_length_m": 0.280,
    } for row in rows)


def direct_orientation_avatar_fk(
    *,
    world_from_segment: Mapping[str, np.ndarray],
    profile: Mapping[str, Any],
    pelvis_gauge_position_m: np.ndarray | None = None,
) -> DirectOrientationAvatarResult:
    """Reject the superseded surface-sensor-as-skeleton construction.

    Segment rotations and raw surface-distance observations do not identify
    internal pelvis, spine, shoulder, or hip attachment nodes.  The supported
    viewer path is ``landmark_proxy_fk_points`` with frozen full-R3 functional
    connection vectors; callers must keep sensor origins and shared joints as
    separate outputs.
    """

    del world_from_segment, profile, pelvis_gauge_position_m
    raise RuntimeError(
        "DIRECT_ORIENTATION_AVATAR_FK is disabled: surface sensor origins and "
        "0.280 m surface separation cannot define internal skeleton nodes; use "
        "landmark_proxy_fk_points with full-R3 connection ownership"
    )


def fixed_landmark_proxy_avatar_fk(
    *,
    world_from_segment: Mapping[str, np.ndarray],
    profile: Mapping[str, Any],
    graphical_spine_vector_m: np.ndarray | None = None,
    graphical_spine_mapping: Mapping[str, Any] | None = None,
    viewer_gauge_position_m: np.ndarray | None = None,
) -> DirectOrientationAvatarResult:
    """Build a display-only fixed surface-landmark proxy skeleton.

    This owner is deliberately separate from scientific functional geometry.
    It uses the registered raw surface-landmark profile only as a fixed viewer
    scale sensitivity case.  Its shoulder/hip attachments and spine are proxy
    display nodes, not anatomical joints.  Functional sensor-to-joint centers
    and their covariance are therefore neither required nor silently promoted
    into the fixed links; callers may show them as a separate evidence overlay.

    The caller must supply a separately owned, preregistered three-dimensional
    graphical spine mapping. Scalar sensor/surface distances are not converted
    into this vector here. In particular, the 0.280 m pelvis-sensor to torso-
    sensor separation and chest-to-acromion observations cannot be summed or
    constrained to segment Z by this owner.
    """

    expected_segments = {name for endpoints in ROOTED_EDGES for name in endpoints}
    if set(world_from_segment) != expected_segments:
        raise ValueError("fixed landmark-proxy avatar requires all ten segment rotations")
    rotations: dict[str, np.ndarray] = {}
    for segment in expected_segments:
        rotation = np.asarray(world_from_segment[segment], dtype=float)
        if (
            rotation.shape != (3, 3)
            or not np.isfinite(rotation).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
            or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-8)
        ):
            raise ValueError(f"{segment}: fixed landmark-proxy avatar requires proper SO(3)")
        rotations[segment] = rotation

    root = (
        np.zeros(3, dtype=float)
        if viewer_gauge_position_m is None
        else np.asarray(viewer_gauge_position_m, dtype=float)
    )
    if root.shape != (3,) or not np.isfinite(root).all():
        raise ValueError("fixed landmark-proxy viewer gauge must be finite R3")

    required_lengths = (
        "upper_arm_left_m", "upper_arm_right_m",
        "forearm_left_m", "forearm_right_m",
        "thigh_left_m", "thigh_right_m",
        "shank_left_m", "shank_right_m",
        "shoulder_surface_breadth_m", "hip_surface_breadth_m",
        "torso_surface_proxy_length_m",
        "chest_to_acromion_line_observation_m",
    )
    lengths = {name: float(profile[name]) for name in required_lengths}
    if any(not np.isfinite(value) or value <= 0.0 for value in lengths.values()):
        raise ValueError("fixed landmark-proxy profile values must be finite positive")

    if graphical_spine_vector_m is None or graphical_spine_mapping is None:
        raise RuntimeError(
            "fixed landmark-proxy avatar requires a separately owned "
            "preregistered 3D graphical spine mapping; scalar surface "
            "distances cannot define the vector"
        )
    spine_vector = np.asarray(graphical_spine_vector_m, dtype=float)
    if (
        spine_vector.shape != (3,)
        or not np.isfinite(spine_vector).all()
        or float(np.linalg.norm(spine_vector)) <= 1e-9
    ):
        raise ValueError("graphical spine mapping must provide a finite nonzero R3 vector")
    if (
        graphical_spine_mapping.get("owner")
        != "PREREGISTERED_VIEWER_ONLY_GRAPHICAL_SPINE_MAPPING"
        or graphical_spine_mapping.get("surface_measurements_are_internal_truth")
        is not False
        or not graphical_spine_mapping.get("uncertainty_or_sensitivity")
    ):
        raise ValueError("graphical spine mapping provenance/uncertainty is incomplete")
    shoulder_half = 0.5 * lengths["shoulder_surface_breadth_m"]
    hip_half = 0.5 * lengths["hip_surface_breadth_m"]
    landmarks: dict[str, np.ndarray] = {
        "hip_mid_landmark_proxy": root.copy(),
        "shoulder_mid_landmark_proxy": (
            root + spine_vector
        ),
    }
    landmarks.update({
        "shoulder_left_landmark_proxy": (
            landmarks["shoulder_mid_landmark_proxy"]
            + rotations["torso"] @ np.array([0.0, shoulder_half, 0.0])
        ),
        "shoulder_right_landmark_proxy": (
            landmarks["shoulder_mid_landmark_proxy"]
            + rotations["torso"] @ np.array([0.0, -shoulder_half, 0.0])
        ),
        "hip_left_landmark_proxy": (
            root + rotations["pelvis"] @ np.array([0.0, hip_half, 0.0])
        ),
        "hip_right_landmark_proxy": (
            root + rotations["pelvis"] @ np.array([0.0, -hip_half, 0.0])
        ),
    })
    for side in ("left", "right"):
        landmarks[f"elbow_{side}_landmark_proxy"] = (
            landmarks[f"shoulder_{side}_landmark_proxy"]
            + rotations[f"upper_arm_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"upper_arm_{side}_m"]])
        )
        landmarks[f"wrist_{side}_landmark_proxy"] = (
            landmarks[f"elbow_{side}_landmark_proxy"]
            + rotations[f"forearm_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"forearm_{side}_m"]])
        )
        landmarks[f"knee_{side}_landmark_proxy"] = (
            landmarks[f"hip_{side}_landmark_proxy"]
            + rotations[f"thigh_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"thigh_{side}_m"]])
        )
        landmarks[f"ankle_{side}_landmark_proxy"] = (
            landmarks[f"knee_{side}_landmark_proxy"]
            + rotations[f"shank_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"shank_{side}_m"]])
        )

    adjacency = {
        "spine_landmark_proxy": (
            "hip_mid_landmark_proxy", "shoulder_mid_landmark_proxy",
        ),
        "shoulder_crossbar_left_landmark_proxy": (
            "shoulder_mid_landmark_proxy", "shoulder_left_landmark_proxy",
        ),
        "shoulder_crossbar_right_landmark_proxy": (
            "shoulder_mid_landmark_proxy", "shoulder_right_landmark_proxy",
        ),
        "hip_crossbar_left_landmark_proxy": (
            "hip_mid_landmark_proxy", "hip_left_landmark_proxy",
        ),
        "hip_crossbar_right_landmark_proxy": (
            "hip_mid_landmark_proxy", "hip_right_landmark_proxy",
        ),
    }
    for side in ("left", "right"):
        adjacency.update({
            f"upper_arm_{side}": (
                f"shoulder_{side}_landmark_proxy",
                f"elbow_{side}_landmark_proxy",
            ),
            f"forearm_{side}": (
                f"elbow_{side}_landmark_proxy",
                f"wrist_{side}_landmark_proxy",
            ),
            f"thigh_{side}": (
                f"hip_{side}_landmark_proxy",
                f"knee_{side}_landmark_proxy",
            ),
            f"shank_{side}": (
                f"knee_{side}_landmark_proxy",
                f"ankle_{side}_landmark_proxy",
            ),
        })
    lines = {
        name: np.vstack((landmarks[first], landmarks[second]))
        for name, (first, second) in adjacency.items()
    }

    degrees = {name: 0 for name in landmarks}
    for first, second in adjacency.values():
        degrees[first] += 1
        degrees[second] += 1
    expected_degrees = {
        "hip_mid_landmark_proxy": 3,
        "shoulder_mid_landmark_proxy": 3,
        "shoulder_left_landmark_proxy": 2,
        "shoulder_right_landmark_proxy": 2,
        "hip_left_landmark_proxy": 2,
        "hip_right_landmark_proxy": 2,
        "elbow_left_landmark_proxy": 2,
        "elbow_right_landmark_proxy": 2,
        "knee_left_landmark_proxy": 2,
        "knee_right_landmark_proxy": 2,
        "wrist_left_landmark_proxy": 1,
        "wrist_right_landmark_proxy": 1,
        "ankle_left_landmark_proxy": 1,
        "ankle_right_landmark_proxy": 1,
    }
    if degrees != expected_degrees or len(lines) != 13:
        raise RuntimeError("fixed landmark-proxy topology/degree invariant failed")
    expected_line_lengths = {
        "spine_landmark_proxy": float(np.linalg.norm(spine_vector)),
        "shoulder_crossbar_left_landmark_proxy": shoulder_half,
        "shoulder_crossbar_right_landmark_proxy": shoulder_half,
        "hip_crossbar_left_landmark_proxy": hip_half,
        "hip_crossbar_right_landmark_proxy": hip_half,
        **{
            f"{segment}_{side}": lengths[f"{segment}_{side}_m"]
            for side in ("left", "right")
            for segment in ("upper_arm", "forearm", "thigh", "shank")
        },
    }
    for name, expected_length in expected_line_lengths.items():
        actual = float(np.linalg.norm(lines[name][1] - lines[name][0]))
        if not np.isclose(actual, expected_length, atol=1e-10, rtol=1e-10):
            raise RuntimeError(f"{name}: fixed landmark-proxy length invariant failed")

    return DirectOrientationAvatarResult(
        landmark_positions_m={key: value.copy() for key, value in landmarks.items()},
        line_segments_m={key: value.copy() for key, value in lines.items()},
        reference_line_segments_m={},
        report={
            "schema": "biospur-c2-fixed-landmark-proxy-avatar-fk-v1",
            "owner": "VIEWER_ONLY_NON_ANATOMICAL_FIXED_LANDMARK_PROXY_GEOMETRY",
            "profile_id": str(profile["profile_id"]),
            "topology_and_node_degree_assertions_passed": True,
            "fixed_profile_distance_assertions_passed": True,
            "surface_profile_observations_are_internal_anatomical_truth": False,
            "shoulder_surface_breadth_used_as_internal_joint_spacing": False,
            "hip_surface_breadth_used_as_internal_joint_spacing": False,
            "torso_surface_sensor_separation_used_as_anatomical_torso_length": False,
            "graphical_spine_mapping": _binding_jsonable(graphical_spine_mapping),
            "graphical_spine_vector_m": spine_vector.tolist(),
            "scalar_surface_observations_converted_to_spine_vector_here": False,
            "torso_surface_sensor_separation_observation_m": lengths[
                "torso_surface_proxy_length_m"
            ],
            "chest_to_acromion_line_observation_m": lengths[
                "chest_to_acromion_line_observation_m"
            ],
            "functional_center_or_connection_required": False,
            "functional_center_mean_used_as_fixed_link_geometry": False,
            "functional_center_evidence_may_be_rendered_separately": True,
            "profiles_are_nonprobabilistic_sensitivity_cases": True,
            "inverse_kinematics": False,
            "viewer_rebase": False,
            "retarget": False,
            "repair": False,
            "result_status": "FIXED_LANDMARK_PROXY_NON_ANATOMICAL_NOT_PASS",
        },
    )


def _legacy_invalid_surface_sensor_avatar_fk(
    *,
    world_from_segment: Mapping[str, np.ndarray],
    profile: Mapping[str, Any],
    pelvis_gauge_position_m: np.ndarray | None = None,
) -> DirectOrientationAvatarResult:
    """Preserved only to explain hashes of already frozen INVALID evidence.

    New code must not call this function.  It promotes surface sensor locations
    and scalar distances into internal attachment nodes and therefore cannot be
    a geometry owner.
    """

    expected_segments = {name for endpoints in ROOTED_EDGES for name in endpoints}
    if set(world_from_segment) != expected_segments:
        raise ValueError("direct orientation avatar requires the exact ten segment rotations")
    rotations: dict[str, np.ndarray] = {}
    for segment in expected_segments:
        rotation = np.asarray(world_from_segment[segment], dtype=float)
        if (
            rotation.shape != (3, 3)
            or not np.isfinite(rotation).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
            or not np.isclose(np.linalg.det(rotation), 1.0, atol=1e-8)
        ):
            raise ValueError(f"{segment}: direct orientation avatar requires proper SO(3)")
        rotations[segment] = rotation
    root = np.zeros(3, dtype=float) if pelvis_gauge_position_m is None else np.asarray(
        pelvis_gauge_position_m, dtype=float,
    )
    if root.shape != (3,) or not np.isfinite(root).all():
        raise ValueError("direct orientation avatar pelvis gauge must be finite R3")

    required_lengths = (
        "upper_arm_left_m", "upper_arm_right_m",
        "forearm_left_m", "forearm_right_m",
        "thigh_left_m", "thigh_right_m",
        "shank_left_m", "shank_right_m",
        "shoulder_surface_breadth_m", "hip_surface_breadth_m",
        "torso_surface_proxy_length_m",
    )
    lengths = {name: float(profile[name]) for name in required_lengths}
    if any(not np.isfinite(value) or value <= 0.0 for value in lengths.values()):
        raise ValueError("direct orientation avatar profile lengths must be finite positive")

    chest_sensor_ref = root + rotations["torso"] @ np.array([
        0.0, 0.0, lengths["torso_surface_proxy_length_m"],
    ])
    shoulder_mid = chest_sensor_ref + rotations["torso"] @ np.array([
        0.0, 0.0, float(profile["chest_to_acromion_line_observation_m"]),
    ])
    shoulder_half = 0.5 * lengths["shoulder_surface_breadth_m"]
    hip_half = 0.5 * lengths["hip_surface_breadth_m"]
    landmarks: dict[str, np.ndarray] = {
        "pelvis_ref": root,
        "chest_sensor_ref": chest_sensor_ref,
        "shoulder_mid_proxy": shoulder_mid,
        "shoulder_left_attach_proxy": shoulder_mid + rotations["torso"] @ np.array([0.0, shoulder_half, 0.0]),
        "shoulder_right_attach_proxy": shoulder_mid + rotations["torso"] @ np.array([0.0, -shoulder_half, 0.0]),
        "hip_mid_proxy": root.copy(),
        "hip_left_attach_proxy": root + rotations["pelvis"] @ np.array([0.0, hip_half, 0.0]),
        "hip_right_attach_proxy": root + rotations["pelvis"] @ np.array([0.0, -hip_half, 0.0]),
    }
    for side in ("left", "right"):
        landmarks[f"elbow_{side}"] = (
            landmarks[f"shoulder_{side}_attach_proxy"]
            + rotations[f"upper_arm_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"upper_arm_{side}_m"]])
        )
        landmarks[f"wrist_{side}"] = (
            landmarks[f"elbow_{side}"]
            + rotations[f"forearm_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"forearm_{side}_m"]])
        )
        landmarks[f"knee_{side}"] = (
            landmarks[f"hip_{side}_attach_proxy"]
            + rotations[f"thigh_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"thigh_{side}_m"]])
        )
        landmarks[f"ankle_{side}"] = (
            landmarks[f"knee_{side}"]
            + rotations[f"shank_{side}"]
            @ np.array([0.0, 0.0, -lengths[f"shank_{side}_m"]])
        )
    line_names = {
        "spine_mid_proxy": ("hip_mid_proxy", "shoulder_mid_proxy"),
        "shoulder_crossbar_left": ("shoulder_mid_proxy", "shoulder_left_attach_proxy"),
        "shoulder_crossbar_right": ("shoulder_mid_proxy", "shoulder_right_attach_proxy"),
        "hip_crossbar_left": ("hip_mid_proxy", "hip_left_attach_proxy"),
        "hip_crossbar_right": ("hip_mid_proxy", "hip_right_attach_proxy"),
        "upper_arm_left": ("shoulder_left_attach_proxy", "elbow_left"),
        "forearm_left": ("elbow_left", "wrist_left"),
        "upper_arm_right": ("shoulder_right_attach_proxy", "elbow_right"),
        "forearm_right": ("elbow_right", "wrist_right"),
        "thigh_left": ("hip_left_attach_proxy", "knee_left"),
        "shank_left": ("knee_left", "ankle_left"),
        "thigh_right": ("hip_right_attach_proxy", "knee_right"),
        "shank_right": ("knee_right", "ankle_right"),
    }
    expected_adjacency = {
        tuple(sorted(edge)) for edge in (
            ("hip_mid_proxy", "shoulder_mid_proxy"),
            ("shoulder_mid_proxy", "shoulder_left_attach_proxy"),
            ("shoulder_mid_proxy", "shoulder_right_attach_proxy"),
            ("hip_mid_proxy", "hip_left_attach_proxy"),
            ("hip_mid_proxy", "hip_right_attach_proxy"),
            ("shoulder_left_attach_proxy", "elbow_left"),
            ("elbow_left", "wrist_left"),
            ("shoulder_right_attach_proxy", "elbow_right"),
            ("elbow_right", "wrist_right"),
            ("hip_left_attach_proxy", "knee_left"),
            ("knee_left", "ankle_left"),
            ("hip_right_attach_proxy", "knee_right"),
            ("knee_right", "ankle_right"),
        )
    }
    observed_adjacency = {tuple(sorted(edge)) for edge in line_names.values()}
    if observed_adjacency != expected_adjacency:
        raise RuntimeError("direct orientation avatar adjacency differs from the fixed proxy tree")
    node_degrees = {name: 0 for edge in expected_adjacency for name in edge}
    for first, second in expected_adjacency:
        node_degrees[first] += 1
        node_degrees[second] += 1
    expected_degrees = {
        "shoulder_mid_proxy": 3, "hip_mid_proxy": 3,
        "shoulder_left_attach_proxy": 2, "shoulder_right_attach_proxy": 2,
        "hip_left_attach_proxy": 2, "hip_right_attach_proxy": 2,
        "elbow_left": 2, "elbow_right": 2,
        "knee_left": 2, "knee_right": 2,
        "wrist_left": 1, "wrist_right": 1,
        "ankle_left": 1, "ankle_right": 1,
    }
    if node_degrees != expected_degrees:
        raise RuntimeError("direct orientation avatar node degrees differ from the fixed proxy tree")
    lines = {
        name: np.vstack((landmarks[start], landmarks[end]))
        for name, (start, end) in line_names.items()
    }
    references = {
        "pelvis_to_chest_sensor_surface_observation": np.vstack((
            landmarks["pelvis_ref"], landmarks["chest_sensor_ref"],
        )),
        "chest_sensor_to_shoulder_mid_surface_proxy": np.vstack((
            landmarks["chest_sensor_ref"], landmarks["shoulder_mid_proxy"],
        )),
    }
    fixed_distance_checks = {
        "pelvis_ref_to_chest_sensor_ref_m": (
            np.linalg.norm(landmarks["chest_sensor_ref"] - landmarks["pelvis_ref"]),
            lengths["torso_surface_proxy_length_m"],
        ),
        "chest_sensor_ref_to_shoulder_mid_proxy_m": (
            np.linalg.norm(landmarks["shoulder_mid_proxy"] - landmarks["chest_sensor_ref"]),
            float(profile["chest_to_acromion_line_observation_m"]),
        ),
        "shoulder_crossbar_m": (
            np.linalg.norm(
                landmarks["shoulder_left_attach_proxy"]
                - landmarks["shoulder_right_attach_proxy"]
            ),
            lengths["shoulder_surface_breadth_m"],
        ),
        "hip_crossbar_m": (
            np.linalg.norm(
                landmarks["hip_left_attach_proxy"]
                - landmarks["hip_right_attach_proxy"]
            ),
            lengths["hip_surface_breadth_m"],
        ),
    }
    for side in ("left", "right"):
        fixed_distance_checks[f"upper_arm_{side}_m"] = (
            np.linalg.norm(
                landmarks[f"shoulder_{side}_attach_proxy"] - landmarks[f"elbow_{side}"]
            ), lengths[f"upper_arm_{side}_m"],
        )
        fixed_distance_checks[f"forearm_{side}_m"] = (
            np.linalg.norm(landmarks[f"elbow_{side}"] - landmarks[f"wrist_{side}"]),
            lengths[f"forearm_{side}_m"],
        )
        fixed_distance_checks[f"thigh_{side}_m"] = (
            np.linalg.norm(
                landmarks[f"hip_{side}_attach_proxy"] - landmarks[f"knee_{side}"]
            ), lengths[f"thigh_{side}_m"],
        )
        fixed_distance_checks[f"shank_{side}_m"] = (
            np.linalg.norm(landmarks[f"knee_{side}"] - landmarks[f"ankle_{side}"]),
            lengths[f"shank_{side}_m"],
        )
    if any(not np.isclose(observed, expected, atol=1e-12) for observed, expected in fixed_distance_checks.values()):
        raise RuntimeError("direct orientation avatar fixed proxy distance assertion failed")
    return DirectOrientationAvatarResult(
        landmark_positions_m={name: value.copy() for name, value in landmarks.items()},
        line_segments_m={name: value.copy() for name, value in lines.items()},
        reference_line_segments_m={name: value.copy() for name, value in references.items()},
        report={
            "schema": "biospur-c2-direct-orientation-avatar-fk-v1",
            "owner": "DIRECT_ORIENTATION_AVATAR_FK",
            "profile_id": str(profile["profile_id"]),
            "rooted_topology": [list(edge) for edge in ROOTED_EDGES],
            "pelvis_translation_gauge_m": root.tolist(),
            "qmt_corrected_world_from_segment_required": True,
            "functional_centers_or_connection_vectors_required": False,
            "functional_centers_allowed_only_as_optional_overlay_or_validation": True,
            "exact_skeleton_adjacency": [list(edge) for edge in sorted(expected_adjacency)],
            "exact_skeleton_node_degrees": node_degrees,
            "fixed_profile_distance_assertions_passed": True,
            "sensor_reference_nodes_are_not_anatomical_centers": True,
            "surface_attachment_proxy_nodes_are_not_anatomical_centers": True,
            "hip_mid_proxy_colocated_with_pelvis_ref_due_absent_qualified_mapping": True,
            "limb_longitudinal_axis_convention": "SEGMENT_PLUS_Z_DISTAL_TO_PROXIMAL;VIEWER_PROXIMAL_TO_DISTAL_USES_SEGMENT_MINUS_Z",
            "limb_longitudinal_axis_convention_source": "segment_frames.py:_construct_sensor_from_segment z_hint proximal_center-minus-distal_center;one-center distal segments use sensor-to-proximal-joint as plus-Z;_frame_from_z_y returns sensor-from-segment with z as plus-Z",
            "hinge_sign_branch_changes_longitudinal_z_sign": False,
            "surface_landmark_profiles_are_nonprobabilistic_nonexhaustive": True,
            "cross_side_profile_is_observed_assignment": False,
            "surface_to_internal_mapping_uncertainty": "NONZERO_UNQUALIFIED",
            "chest_to_acromion_line_observation_retained_not_used_as_internal_joint_offset_m": float(
                profile["chest_to_acromion_line_observation_m"]
            ),
            "wear_distribution_hardened_into_avatar_direction": False,
            "inverse_kinematics": False,
            "viewer_rebase": False,
            "retarget": False,
            "repair": False,
            "result_status": "DIRECT_ORIENTATION_LANDMARK_PROXY_AVATAR_NON_ANATOMICAL_NOT_PASS",
        },
    )


def landmark_proxy_fk_points(
    *,
    root_sensor_position_m: np.ndarray,
    world_from_segment: Mapping[str, np.ndarray],
    segment_from_sensor: Mapping[str, np.ndarray],
    connection_vectors_by_edge: Mapping[str, EdgeConnectionVectors],
    profile: Mapping[str, Any],
) -> ScientificFKResult:
    """Compose fixed raw landmark scale with functional offset direction/centroid.

    This is direct forward geometry. It is not IK, rebase, retargeting, or a
    fit. Surface-length profiles remain separate sensitivity cases and are
    never relabelled as exact internal bone lengths.
    """

    expected_segments = {name for endpoints in ROOTED_EDGES for name in endpoints}
    expected_edges = set(EDGE_NAME_BY_ENDPOINTS.values())
    if set(world_from_segment) != expected_segments or set(segment_from_sensor) != expected_segments:
        raise ValueError("landmark-proxy FK requires all ten segment orientations/frames")
    if set(connection_vectors_by_edge) != expected_edges:
        raise ValueError("landmark-proxy FK requires the exact nine functional connections")
    rotations: dict[str, np.ndarray] = {}
    frames: dict[str, np.ndarray] = {}
    for segment in expected_segments:
        rotation = np.asarray(world_from_segment[segment], dtype=float)
        frame = np.asarray(segment_from_sensor[segment], dtype=float)
        if (
            rotation.shape != (3, 3) or frame.shape != (3, 3)
            or not np.isfinite(rotation).all() or not np.isfinite(frame).all()
            or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
            or not np.allclose(frame.T @ frame, np.eye(3), atol=1e-8)
            or np.linalg.det(rotation) <= 0.0 or np.linalg.det(frame) <= 0.0
        ):
            raise ValueError(f"{segment}: landmark-proxy FK requires proper SO(3) inputs")
        rotations[segment] = rotation
        frames[segment] = frame

    raw_segment_vectors: dict[tuple[str, str], np.ndarray] = {}
    for edge, parent, child in EDGE_SPECS:
        connection = connection_vectors_by_edge[edge]
        if (connection.parent, connection.child) != (parent, child):
            raise ValueError(f"{edge}: functional connection endpoint ownership mismatch")
        raw_segment_vectors[(edge, parent)] = frames[parent] @ np.asarray(
            connection.parent_sensor_to_joint_m, dtype=float,
        )
        raw_segment_vectors[(edge, child)] = frames[child] @ np.asarray(
            connection.child_sensor_to_joint_m, dtype=float,
        )

    scaled_segment_vectors = {
        key: value.copy() for key, value in raw_segment_vectors.items()
    }
    scale_audit: dict[str, Any] = {}
    for segment, proximal_edge, distal_edge in (
        ("upper_arm_left", "shoulder_left", "elbow_left"),
        ("upper_arm_right", "shoulder_right", "elbow_right"),
        ("thigh_left", "hip_left", "knee_left"),
        ("thigh_right", "hip_right", "knee_right"),
    ):
        proximal = raw_segment_vectors[(proximal_edge, segment)]
        distal = raw_segment_vectors[(distal_edge, segment)]
        raw_delta = distal - proximal
        raw_length = float(np.linalg.norm(raw_delta))
        if not np.isfinite(raw_length) or raw_length <= 1e-9:
            raise ValueError(f"{segment}: functional centers do not define a finite link direction")
        proxy_length = float(profile[f"{segment}_m"])
        if not np.isfinite(proxy_length) or proxy_length <= 0.0:
            raise ValueError(f"{segment}: raw-observation proxy length must be positive")
        direction = raw_delta / raw_length
        centroid = 0.5 * (proximal + distal)
        scaled_segment_vectors[(proximal_edge, segment)] = centroid - 0.5 * proxy_length * direction
        scaled_segment_vectors[(distal_edge, segment)] = centroid + 0.5 * proxy_length * direction
        scale_audit[segment] = {
            "raw_functional_center_separation_m": raw_length,
            "landmark_proxy_length_m": proxy_length,
            "functional_direction_and_centroid_preserved": True,
            "raw_surface_observation_called_internal_bone_truth": False,
        }

    positions = {"pelvis": np.asarray(root_sensor_position_m, dtype=float)}
    if positions["pelvis"].shape != (3,) or not np.isfinite(positions["pelvis"]).all():
        raise ValueError("landmark-proxy root sensor position must be finite R3")
    position_covariance = {"pelvis": np.zeros((3, 3), dtype=float)}
    joints: dict[str, np.ndarray] = {}
    joint_covariance: dict[str, np.ndarray] = {}
    closure: dict[str, float] = {}
    for parent, child in ROOTED_EDGES:
        edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
        connection = connection_vectors_by_edge[edge]
        parent_vector = scaled_segment_vectors[(edge, parent)]
        child_vector = scaled_segment_vectors[(edge, child)]
        joint_parent = positions[parent] + rotations[parent] @ parent_vector
        positions[child] = joint_parent - rotations[child] @ child_vector
        joint_child = positions[child] + rotations[child] @ child_vector
        joints[edge] = joint_parent
        closure[edge] = float(np.linalg.norm(joint_parent - joint_child))

        # This is the connection-owner covariance in world coordinates.  For
        # upper arms and thighs the viewer mean above is rescaled to the raw
        # landmark profile, while this covariance deliberately remains the
        # unmodified scientific full-R3 connection covariance.  It is exposed
        # as a separate uncertainty overlay and is not claimed to be the
        # covariance of the nonlinear viewer rescaling.
        covariance = np.asarray(connection.covariance_m2, dtype=float)
        if (
            covariance.shape != (6, 6)
            or not np.isfinite(covariance).all()
            or not np.allclose(covariance, covariance.T, atol=1e-10)
            or float(np.min(np.linalg.eigvalsh(covariance))) < -1e-10
        ):
            raise ValueError(f"{edge}: connection covariance must be finite PSD 6x6")
        parent_map = rotations[parent] @ frames[parent]
        child_map = rotations[child] @ frames[child]
        parent_block = covariance[:3, :3]
        transform = np.hstack((parent_map, -child_map))
        joint_covariance[edge] = (
            position_covariance[parent]
            + parent_map @ parent_block @ parent_map.T
        )
        position_covariance[child] = (
            position_covariance[parent]
            + transform @ covariance @ transform.T
        )
        joint_covariance[edge] = 0.5 * (
            joint_covariance[edge] + joint_covariance[edge].T
        )
        position_covariance[child] = 0.5 * (
            position_covariance[child] + position_covariance[child].T
        )

    distal_landmarks: dict[str, np.ndarray] = {}
    for segment, joint_edge, label in (
        ("forearm_left", "elbow_left", "wrist_left"),
        ("forearm_right", "elbow_right", "wrist_right"),
        ("shank_left", "knee_left", "ankle_left"),
        ("shank_right", "knee_right", "ankle_right"),
    ):
        proxy_length = float(profile[f"{segment}_m"])
        distal_landmarks[label] = joints[joint_edge] + rotations[segment] @ np.array(
            [0.0, 0.0, -proxy_length], dtype=float,
        )
        scale_audit[segment] = {
            "landmark_proxy_length_m": proxy_length,
            "proximal_joint_to_distal_surface_landmark_along_functional_segment_minus_z": True,
            "raw_surface_observation_called_internal_bone_truth": False,
        }
    return ScientificFKResult(
        segment_sensor_positions_m={key: value.copy() for key, value in positions.items()},
        shared_joint_positions_m={key: value.copy() for key, value in joints.items()},
        distal_landmark_positions_m={
            key: value.copy() for key, value in distal_landmarks.items()
        },
        segment_sensor_position_covariance_m2={
            key: value.copy() for key, value in position_covariance.items()
        },
        shared_joint_position_covariance_m2={
            key: value.copy() for key, value in joint_covariance.items()
        },
        shared_joint_closure_error_m=closure,
        report={
            "schema": "biospur-c2-fixed-measured-landmark-proxy-direct-fk-v1",
            "renderer_geometry_source": "FIXED_RAW_MEASURED_LANDMARK_PROXY_PLUS_FUNCTIONAL_CENTER_OFFSETS",
            "profile_id": str(profile["profile_id"]),
            "scale_audit_by_segment": scale_audit,
            "surface_to_internal_mapping_uncertainty": "NONZERO_UNQUALIFIED",
            "profiles_are_nonprobabilistic_sensitivity_cases": True,
            "hard_left_right_internal_equality_imposed": False,
            "internal_shoulder_hip_torso_substitution_used": False,
            "functional_center_direction_and_centroid_used_by_viewer_proxy": True,
            "functional_center_covariance_propagated_through_viewer_rescaling": False,
            "functional_center_covariance_remains_unmodified_in_frozen_scientific_state": True,
            "functional_connection_covariance_overlay_frame": "WORLD",
            "functional_connection_covariance_overlay_scope": (
                "UNSCALED_FULL_R3_CONNECTION_OWNER_ONLY;ORIENTATION_AND_VIEWER_RESCALE_NOT_PROPAGATED"
            ),
            "functional_connection_covariance_trace_m2_by_edge": {
                edge: float(np.trace(np.asarray(
                    connection_vectors_by_edge[edge].covariance_m2, dtype=float,
                )))
                for edge in sorted(connection_vectors_by_edge)
            },
            "surface_sensor_origins_and_shared_joint_nodes_are_distinct": True,
            "pelvis_surface_sensor_origin_used_only_as_translation_gauge": True,
            "torso_surface_sensor_separation_m_used_as_internal_axis_or_length": False,
            "torso_surface_sensor_distance_observation_m": float(
                profile["torso_surface_proxy_length_m"]
            ),
            "viewer_proxy_rescales_within_segment_proximal_distal_separation": True,
            "inverse_kinematics": False,
            "viewer_rebase": False,
            "retarget": False,
            "repair": False,
            "result_status": "LANDMARK_PROXY_NON_ANATOMICAL_NOT_PASS",
        },
    )


def physical_input_binding_token(
    secret: bytes,
    binding_without_token: Mapping[str, Any],
) -> tuple[str, str]:
    """Return semantic payload hash and HMAC for one runtime/QMT-owned input."""

    if not isinstance(secret, bytes) or len(secret) < 16:
        raise ValueError("physical input binding secret must contain at least 128 bits")
    if any(
        key in binding_without_token
        for key in ("runtime_owner_token", "runtime_owner_binding_payload_sha256")
    ):
        raise ValueError("physical input binding payload must exclude its token fields")
    encoded = json.dumps(
        _binding_jsonable(binding_without_token),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    payload_sha256 = sha256(encoded).hexdigest()
    token = hmac.new(secret, encoded, sha256).hexdigest()
    return payload_sha256, token


class ScientificForwardKinematicsOwner:
    """Locate every child sensor by closing both fitted sensor-to-joint vectors."""

    def __init__(
        self,
        *,
        execution_guard: C2ExecutionGuard,
        physical_settings: Mapping[str, Any] | None = None,
        expected_runtime_owner_id: str | None = None,
        runtime_binding_secret: bytes | None = None,
    ) -> None:
        self.execution_guard = execution_guard
        self.physical_settings = physical_settings
        if (expected_runtime_owner_id is None) != (runtime_binding_secret is None):
            raise ValueError("physical runtime owner ID and binding secret must be configured together")
        self._expected_runtime_owner_id = expected_runtime_owner_id
        self._runtime_binding_secret = runtime_binding_secret

    @staticmethod
    def _rotation(value: np.ndarray, *, segment: str) -> np.ndarray:
        rotation = np.asarray(value, dtype=float)
        if rotation.shape != (3, 3) or not np.isfinite(rotation).all():
            raise ValueError(f"{segment}: scientific FK rotation must be finite 3x3")
        if not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8) or np.linalg.det(rotation) <= 0.0:
            raise ValueError(f"{segment}: scientific FK rotation must be proper SO(3)")
        return rotation

    def forward(
        self,
        *,
        root_sensor_position_m: np.ndarray,
        world_from_segment: Mapping[str, np.ndarray],
        frame_branch: SegmentFrameBranch,
        connection_frame_mode: str = "SENSOR_TO_SEGMENT_THEN_WORLD",
        viewer_mode: str = "DIRECT_SCIENTIFIC_FK_NO_REBASE_NO_IK_NO_REPAIR",
    ) -> ScientificFKResult:
        self.execution_guard.validate_viewer_transform(mode=viewer_mode)
        self.execution_guard.validate_connection_frame_mode(connection_frame_mode)
        expected_edges = set(EDGE_NAME_BY_ENDPOINTS.values())
        connection_vectors_by_edge = frame_branch.connection_vectors_by_edge
        if set(connection_vectors_by_edge) != expected_edges:
            raise ValueError("scientific FK requires the exact nine named edge connections")
        expected_segments = {name for endpoints in ROOTED_EDGES for name in endpoints}
        if set(world_from_segment) != expected_segments:
            raise ValueError("scientific FK requires all ten segment orientations")

        positions = {"pelvis": np.asarray(root_sensor_position_m, dtype=float)}
        if positions["pelvis"].shape != (3,) or not np.isfinite(positions["pelvis"]).all():
            raise ValueError("root sensor position must be finite R3")
        position_covariance = {"pelvis": np.zeros((3, 3), dtype=float)}
        joint_positions: dict[str, np.ndarray] = {}
        joint_covariance: dict[str, np.ndarray] = {}
        closure: dict[str, float] = {}
        for parent, child in ROOTED_EDGES:
            edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
            connection = connection_vectors_by_edge[edge]
            if (connection.parent, connection.child) != (parent, child):
                raise ValueError(f"{edge}: connection endpoint ownership mismatch")
            parent_vector = np.asarray(connection.parent_sensor_to_joint_m, dtype=float)
            child_vector = np.asarray(connection.child_sensor_to_joint_m, dtype=float)
            self.execution_guard.validate_connection_vector(parent_vector)
            self.execution_guard.validate_connection_vector(child_vector)
            parent_rotation = self._rotation(world_from_segment[parent], segment=parent)
            child_rotation = self._rotation(world_from_segment[child], segment=child)
            parent_segment_vector = np.asarray(frame_branch.segment_from_sensor[parent], dtype=float) @ parent_vector
            child_segment_vector = np.asarray(frame_branch.segment_from_sensor[child], dtype=float) @ child_vector

            # Locate the joint from the parent sensor. Then place the child
            # sensor so its independently fitted vector reaches the same joint.
            joint_from_parent = positions[parent] + parent_rotation @ parent_segment_vector
            positions[child] = joint_from_parent - child_rotation @ child_segment_vector
            joint_from_child = positions[child] + child_rotation @ child_segment_vector
            joint_positions[edge] = joint_from_parent
            closure[edge] = float(np.linalg.norm(joint_from_parent - joint_from_child))
            covariance = np.asarray(connection.covariance_m2, dtype=float)
            if (
                covariance.shape != (6, 6)
                or not np.isfinite(covariance).all()
                or not np.allclose(covariance, covariance.T, atol=1e-10)
                or float(np.min(np.linalg.eigvalsh(covariance))) < -1e-10
            ):
                raise ValueError(f"{edge}: connection covariance must be finite PSD 6x6")
            parent_map = parent_rotation @ np.asarray(
                frame_branch.segment_from_sensor[parent], dtype=float,
            )
            child_map = child_rotation @ np.asarray(
                frame_branch.segment_from_sensor[child], dtype=float,
            )
            joint_covariance[edge] = (
                position_covariance[parent]
                + parent_map @ covariance[:3, :3] @ parent_map.T
            )
            transform = np.hstack((parent_map, -child_map))
            position_covariance[child] = (
                position_covariance[parent]
                + transform @ covariance @ transform.T
            )
            joint_covariance[edge] = 0.5 * (
                joint_covariance[edge] + joint_covariance[edge].T
            )
            position_covariance[child] = 0.5 * (
                position_covariance[child] + position_covariance[child].T
            )

        pelvis_rotation = self._rotation(world_from_segment["pelvis"], segment="pelvis")
        pelvis_origin = positions["pelvis"]
        pelvis_frame_positions = {
            segment: pelvis_rotation.T @ (position - pelvis_origin)
            for segment, position in positions.items()
        }
        self.execution_guard.validate_geometry(pelvis_frame_positions, ROOTED_EDGES)
        if max(closure.values(), default=0.0) > 1e-10:
            raise RuntimeError("scientific FK failed shared-joint closure")
        return ScientificFKResult(
            segment_sensor_positions_m={key: value.copy() for key, value in positions.items()},
            shared_joint_positions_m={key: value.copy() for key, value in joint_positions.items()},
            distal_landmark_positions_m={},
            segment_sensor_position_covariance_m2={
                key: value.copy() for key, value in position_covariance.items()
            },
            shared_joint_position_covariance_m2={
                key: value.copy() for key, value in joint_covariance.items()
            },
            shared_joint_closure_error_m=closure,
            report={
                "schema": "biospur-c2-two-sided-scientific-fk-v2",
                "renderer_geometry_source": "DIRECT_FROZEN_SCIENTIFIC_FK",
                "edge_count": len(ROOTED_EDGES),
                "connection_vectors_per_edge": 2,
                "connection_vector_dimension": 3,
                "stored_connection_vector_frame": "SENSOR",
                "fk_connection_vector_frame": "SEGMENT_AFTER_RETAINED_BRANCH_SEGMENT_FROM_SENSOR_TRANSFORM",
                "child_origin_equation": "p_child_sensor = p_shared_joint - R_world_child_segment @ R_child_segment_from_sensor @ v_child_sensor_to_joint",
                "maximum_shared_joint_closure_error_m": max(closure.values(), default=0.0),
                "topology_gate_coordinate_frame": "CURRENT_BRANCH_PELVIS_SEGMENT_FRAME_X_FORWARD_Y_LEFT_Z_UP",
                "viewer_rebase": False,
                "inverse_kinematics": False,
                "repair": False,
            },
        )

    def validate_candidate_points(
        self,
        points: Mapping[str, np.ndarray],
        *,
        edges: tuple[tuple[str, str], ...] = ROOTED_EDGES,
        viewer_mode: str = "DIRECT_SCIENTIFIC_FK_NO_REBASE_NO_IK_NO_REPAIR",
    ) -> None:
        self.execution_guard.validate_viewer_transform(mode=viewer_mode)
        self.execution_guard.validate_geometry(points, edges)

    def assess_prefix_trajectory(
        self,
        *,
        frame_branch: SegmentFrameBranch,
        world_from_segment_trajectory: Mapping[str, np.ndarray],
        orientation_tangent_covariance_rad2: Mapping[str, np.ndarray],
        owner_input_binding: Mapping[str, Any],
    ) -> PhysicalTrajectoryCandidateAssessment:
        """Hard topology/gross gates plus soft ROM from an owner-bound prefix."""

        if self.physical_settings is None:
            raise RuntimeError("physical trajectory assessment requires sealed physical-candidate settings")
        expected_segments = {name for edge in ROOTED_EDGES for name in edge}
        if set(world_from_segment_trajectory) != expected_segments or set(orientation_tangent_covariance_rad2) != expected_segments:
            raise ValueError("physical candidate trajectory must cover all ten rooted-tree segments")
        counts = {len(np.asarray(value)) for value in world_from_segment_trajectory.values()}
        if len(counts) != 1 or not counts or next(iter(counts)) < 1:
            raise ValueError("physical candidate segment trajectories must share at least one timestamp")
        count = next(iter(counts))
        binding = dict(owner_input_binding)
        if binding.get("schema") != "biospur-c2-runtime-owned-physical-prefix-input-v1":
            raise ValueError("physical candidate lacks the runtime-owned trajectory token")
        if self._expected_runtime_owner_id is None or self._runtime_binding_secret is None:
            self.execution_guard.reject_raw_unqmt_physical_substitution(
                "physical candidate assessment lacks an active runtime/QMT binding authority"
            )
        if binding.get("runtime_owner_id") != self._expected_runtime_owner_id:
            self.execution_guard.reject_raw_unqmt_physical_substitution(
                "physical candidate token belongs to another runtime owner"
            )
        token_payload = {
            key: value for key, value in binding.items()
            if key not in {
                "runtime_owner_token", "runtime_owner_binding_payload_sha256",
            }
        }
        payload_sha256, expected_token = physical_input_binding_token(
            self._runtime_binding_secret, token_payload,
        )
        if (
            binding.get("runtime_owner_binding_payload_sha256") != payload_sha256
            or not hmac.compare_digest(str(binding.get("runtime_owner_token", "")), expected_token)
        ):
            self.execution_guard.reject_raw_unqmt_physical_substitution(
                "physical candidate runtime/QMT owner token is absent or forged"
            )
        if binding.get("branch_id") != frame_branch.branch_id:
            raise ValueError("physical trajectory token belongs to another branch")
        allowed_sources = {
            "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_CURRENT_SEALED_ORIENTED_ACTION",
            "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_FROZEN_HELDOUT_ORIENTED_ACTION",
            "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_WITH_HASH_BOUND_NONHINGE_S1_PRIOR_TRAINING_ONLY",
        }
        if (
            binding.get("source") not in allowed_sources
            or binding.get("raw_unqmt_orientation_allowed_to_drive_physical_gate") is not False
            or binding.get("qmt_branch_evidence_ingested_before_physical_gate") is not False
            or binding.get("rooted_qmt_trajectory_report", {}).get("tree_semantics")
            != "child_global = parent_global + time_varying_edge_deltaFilt"
        ):
            self.execution_guard.reject_raw_unqmt_physical_substitution(
                "physical candidate must consume official QMT rooted propagation, never raw VQF orientation"
            )
        for segment in expected_segments:
            rotation = np.asarray(world_from_segment_trajectory[segment], dtype=float)
            covariance = np.asarray(orientation_tangent_covariance_rad2[segment], dtype=float)
            if rotation.shape != (count, 3, 3) or covariance.shape != (count, 3, 3):
                raise ValueError("physical trajectory rotation/covariance shape differs from owner contract")
            if binding["world_from_segment_sha256"][segment] != _array_sha256(rotation):
                raise ValueError("physical trajectory rotation hash differs from runtime-owned input")
            if binding["orientation_covariance_sha256"][segment] != _array_sha256(covariance):
                raise ValueError("physical trajectory covariance hash differs from runtime-owned input")

        rejection_codes: list[str] = []
        topology_samples: list[dict[str, Any]] = []
        pelvis_frame_positions_by_sample: list[tuple[int, Mapping[str, np.ndarray]]] = []
        for sample_index in range(count):
            sample_rotations = {
                segment: np.asarray(world_from_segment_trajectory[segment][sample_index], dtype=float)
                for segment in expected_segments
            }
            try:
                result = self.forward(
                    root_sensor_position_m=np.zeros(3),
                    world_from_segment=sample_rotations,
                    frame_branch=frame_branch,
                )
            except ClassAGuardViolation as exc:
                if exc.code not in {
                    "COLLAPSED_GEOMETRY",
                    "LEFT_RIGHT_CROSSING_GEOMETRY",
                    "BILATERAL_MIRROR_GEOMETRY",
                    "DISCONNECTED_ROOTED_GRAPH",
                }:
                    raise
                rejection_codes.append(exc.code)
                topology_samples.append({
                    "sample_index": sample_index,
                    "status": "HARD_TOPOLOGY_REJECTION",
                    "rejection_code": exc.code,
                })
            except ValueError as exc:
                rejection_codes.append("IMPROPER_OR_INCOMPLETE_ROTATION_OR_CONNECTION")
                topology_samples.append({
                    "sample_index": sample_index,
                    "status": "HARD_ROTATION_OR_CONNECTION_REJECTION",
                    "detail": str(exc),
                })
            else:
                pelvis_rotation = sample_rotations["pelvis"]
                pelvis_position = np.asarray(result.segment_sensor_positions_m["pelvis"], dtype=float)
                pelvis_frame_positions_by_sample.append((
                    sample_index,
                    {
                        segment: pelvis_rotation.T @ (
                            np.asarray(position, dtype=float) - pelvis_position
                        )
                        for segment, position in result.segment_sensor_positions_m.items()
                    },
                ))
                topology_samples.append({
                    "sample_index": sample_index,
                    "status": "TOPOLOGY_AND_SHARED_JOINT_CLOSURE_VALID",
                    "maximum_shared_joint_closure_error_m": max(
                        result.shared_joint_closure_error_m.values(), default=0.0,
                    ),
                })

        confidence_multiplier = float(self.physical_settings["hard_guard_sigma_multiplier"])
        bilateral_settings = self.physical_settings["bilateral_crossing"]
        bilateral_margin_m = float(bilateral_settings["gross_crossing_margin_m"])
        bilateral_required_fraction = float(
            bilateral_settings["gross_sustained_evidence_fraction"]
        )
        bilateral_soft_sigma_m = float(bilateral_settings["soft_likelihood_sigma_m"])
        if (
            bilateral_margin_m < 0.0
            or not 0.0 < bilateral_required_fraction <= 1.0
            or bilateral_soft_sigma_m <= 0.0
        ):
            raise ValueError("registered bilateral crossing settings are outside their physical ranges")
        bilateral_pairs = (
            ("upper_arm_left", "upper_arm_right"),
            ("thigh_left", "thigh_right"),
            ("shank_left", "shank_right"),
        )
        bilateral_rows: list[dict[str, Any]] = []
        bilateral_confirmed_by_pair: dict[str, list[bool]] = {
            f"{left}|{right}": [] for left, right in bilateral_pairs
        }
        bilateral_soft_terms: list[float] = []
        for sample_index, pelvis_positions in pelvis_frame_positions_by_sample:
            # Conservative first-order isotropic position uncertainty.  Each
            # child accumulates parent position variance, both fitted vector
            # covariance blocks, and rotation-tangent sensitivity scaled by
            # the actual sensor-to-joint lever arms.  Shared model floors stay
            # in the connection/frame covariances and are not divided by the
            # number of trajectory samples.
            position_variance_m2: dict[str, float] = {"pelvis": 0.0}
            for parent, child in ROOTED_EDGES:
                edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
                connection = frame_branch.connection_vectors_by_edge[edge]
                connection_covariance = np.asarray(connection.covariance_m2, dtype=float)
                if connection_covariance.shape != (6, 6):
                    raise ValueError("physical bilateral gate requires full two-vector 6x6 covariance")
                parent_rotation_covariance = (
                    np.asarray(frame_branch.frame_tangent_covariance_rad2[parent], dtype=float)
                    + np.asarray(orientation_tangent_covariance_rad2[parent][sample_index], dtype=float)
                )
                child_rotation_covariance = (
                    np.asarray(frame_branch.frame_tangent_covariance_rad2[child], dtype=float)
                    + np.asarray(orientation_tangent_covariance_rad2[child][sample_index], dtype=float)
                )
                vector_variance = max(0.0, 2.0 * float(np.max(np.linalg.eigvalsh(
                    0.5 * (connection_covariance + connection_covariance.T)
                ))))
                rotation_variance = (
                    float(np.linalg.norm(connection.parent_sensor_to_joint_m)) ** 2
                    * max(0.0, float(np.max(np.linalg.eigvalsh(parent_rotation_covariance))))
                    + float(np.linalg.norm(connection.child_sensor_to_joint_m)) ** 2
                    * max(0.0, float(np.max(np.linalg.eigvalsh(child_rotation_covariance))))
                )
                position_variance_m2[child] = (
                    position_variance_m2[parent] + vector_variance + rotation_variance
                )
            for left, right in bilateral_pairs:
                separation_m = float(pelvis_positions[left][1] - pelvis_positions[right][1])
                separation_sigma_m = float(np.sqrt(max(
                    0.0, position_variance_m2[left] + position_variance_m2[right],
                )))
                confirmed = separation_m < -(
                    bilateral_margin_m + confidence_multiplier * separation_sigma_m
                )
                standardized_soft_violation = max(0.0, -separation_m) / (
                    bilateral_soft_sigma_m + separation_sigma_m
                )
                soft_term = -0.5 * standardized_soft_violation**2
                bilateral_confirmed_by_pair[f"{left}|{right}"].append(bool(confirmed))
                bilateral_soft_terms.append(float(soft_term))
                bilateral_rows.append({
                    "sample_index": sample_index,
                    "left_segment": left,
                    "right_segment": right,
                    "left_minus_right_y_m": separation_m,
                    "separation_sigma_m": separation_sigma_m,
                    "gross_margin_m": bilateral_margin_m,
                    "hard_guard_sigma_multiplier": confidence_multiplier,
                    "gross_uncertainty_confirmed": bool(confirmed),
                    "soft_log_likelihood_contribution": float(soft_term),
                })
        bilateral_confirmed_fraction_by_pair = {
            pair: float(np.mean(values)) if values else 0.0
            for pair, values in bilateral_confirmed_by_pair.items()
        }
        bilateral_log_likelihood = float(np.mean(bilateral_soft_terms)) if bilateral_soft_terms else 0.0
        if any(
            fraction >= bilateral_required_fraction
            for fraction in bilateral_confirmed_fraction_by_pair.values()
        ):
            rejection_codes.append("GROSS_SUSTAINED_BILATERAL_CROSSING_OR_MIRROR")

        gravity_margin = float(self.physical_settings["gross_gravity_wrong_hemisphere_margin_rad"])
        gravity_settings = self.physical_settings["gravity_evidence"]
        hard_upright_segments = tuple(str(value) for value in gravity_settings["hard_upright_segments"])
        if hard_upright_segments != ("pelvis", "torso"):
            raise ValueError("hard gravity support is restricted to registered pelvis/torso axial segments")
        gravity_required_fraction = float(gravity_settings["gross_sustained_evidence_fraction"])
        gravity_soft_dot_sigma = float(gravity_settings["soft_likelihood_dot_sigma"])
        if not 0.0 < gravity_required_fraction <= 1.0 or gravity_soft_dot_sigma <= 0.0:
            raise ValueError("registered gravity evidence settings are outside their physical ranges")
        gravity_evidence: dict[str, Any] = {}
        gravity_soft_terms: list[float] = []
        for segment in sorted(expected_segments):
            branch_covariance = np.asarray(frame_branch.frame_tangent_covariance_rad2[segment], dtype=float)
            dots: list[float] = []
            confirmed: list[bool] = []
            sigmas: list[float] = []
            for sample_index in range(count):
                covariance = branch_covariance + np.asarray(
                    orientation_tangent_covariance_rad2[segment][sample_index], dtype=float,
                )
                sigma = float(np.sqrt(max(0.0, float(np.max(np.linalg.eigvalsh(covariance))))))
                effective_margin = gravity_margin + confidence_multiplier * sigma
                dot = float(world_from_segment_trajectory[segment][sample_index, :, 2] @ np.array([0.0, 0.0, 1.0]))
                dots.append(dot)
                sigmas.append(sigma)
                confirmed.append(bool(
                    effective_margin < np.pi / 2.0 and dot < -float(np.sin(effective_margin))
                ))
            confirmed_fraction = float(np.mean(confirmed))
            if segment in hard_upright_segments and confirmed_fraction >= gravity_required_fraction:
                rejection_codes.append("GROSS_GRAVITY_UP_WRONG_HEMISPHERE")
            soft_terms = [
                -0.5 * (max(0.0, -dot) / (gravity_soft_dot_sigma + np.sin(min(sigma, np.pi / 2.0)))) ** 2
                for dot, sigma in zip(dots, sigmas)
            ]
            # Limb long-axis reversals are permitted motion. They are retained
            # as diagnostics and never contribute a hidden pose profile.
            if segment in hard_upright_segments:
                gravity_soft_terms.extend(soft_terms)
            gravity_evidence[segment] = {
                "up_dot_world_gravity_opposite": dots,
                "maximum_tangent_sigma_rad": max(sigmas),
                "confirmed_gross_wrong_count": int(np.count_nonzero(confirmed)),
                "confirmed_gross_wrong_fraction": confirmed_fraction,
                "hard_upright_segment": segment in hard_upright_segments,
                "limb_long_axis_opposite_hemisphere_hard_rejected": False,
                "soft_log_likelihood_contribution": (
                    float(np.mean(soft_terms)) if segment in hard_upright_segments else 0.0
                ),
            }
        gravity_log_likelihood = float(np.mean(gravity_soft_terms)) if gravity_soft_terms else 0.0

        knee_threshold = float(self.physical_settings["bilateral_knee_minimum_signed_flexion_rad"])
        knee_required_fraction = float(self.physical_settings["bilateral_knee_opposition_minimum_fraction"])
        left_angles: list[float] = []
        right_angles: list[float] = []
        opposite_informed: list[bool] = []
        for sample_index in range(count):
            current: dict[str, tuple[float, float]] = {}
            for side in ("left", "right"):
                thigh = np.asarray(world_from_segment_trajectory[f"thigh_{side}"][sample_index])
                shank = np.asarray(world_from_segment_trajectory[f"shank_{side}"][sample_index])
                hinge = thigh[:, 1]
                thigh_long = thigh[:, 2]
                shank_long = shank[:, 2]
                angle = float(np.arctan2(hinge @ np.cross(shank_long, thigh_long), shank_long @ thigh_long))
                covariance = (
                    np.asarray(frame_branch.frame_tangent_covariance_rad2[f"thigh_{side}"])
                    + np.asarray(frame_branch.frame_tangent_covariance_rad2[f"shank_{side}"])
                    + np.asarray(orientation_tangent_covariance_rad2[f"thigh_{side}"][sample_index])
                    + np.asarray(orientation_tangent_covariance_rad2[f"shank_{side}"][sample_index])
                )
                sigma = float(np.sqrt(max(0.0, float(np.max(np.linalg.eigvalsh(covariance))))))
                current[side] = (angle, sigma)
            left_angles.append(current["left"][0])
            right_angles.append(current["right"][0])
            left_informed = abs(current["left"][0]) > knee_threshold + confidence_multiplier * current["left"][1]
            right_informed = abs(current["right"][0]) > knee_threshold + confidence_multiplier * current["right"][1]
            opposite_informed.append(bool(
                left_informed and right_informed
                and np.sign(current["left"][0]) != np.sign(current["right"][0])
            ))
        opposite_fraction = float(np.mean(opposite_informed))
        if opposite_fraction >= knee_required_fraction:
            rejection_codes.append("ONE_KNEE_FORWARD_ONE_KNEE_BACK")

        rom_reference = float(self.physical_settings["rom_soft_reference_rad"])
        rom_sigma = float(self.physical_settings["rom_soft_sigma_rad"])
        rom_log_likelihood = 0.0
        rom_evidence: dict[str, Any] = {}
        for parent, child in ROOTED_EDGES:
            relative = np.einsum(
                "nji,njk->nik",
                np.asarray(world_from_segment_trajectory[parent]),
                np.asarray(world_from_segment_trajectory[child]),
            )
            angles = Rotation.from_matrix(relative).magnitude()
            excess = np.maximum(0.0, angles - rom_reference)
            contribution = -0.5 * float(np.sum((excess / rom_sigma) ** 2)) / count
            rom_log_likelihood += contribution
            rom_evidence[f"{parent}->{child}"] = {
                "maximum_relative_rotation_rad": float(np.max(angles)),
                "soft_log_likelihood_contribution": contribution,
                "hard_rom_gate": False,
            }

        unique_rejections = tuple(dict.fromkeys(rejection_codes))
        soft_total_log_likelihood = float(
            rom_log_likelihood + bilateral_log_likelihood + gravity_log_likelihood
        )
        return PhysicalTrajectoryCandidateAssessment(
            branch_id=frame_branch.branch_id,
            physically_legal=not unique_rejections,
            rom_log_likelihood=float(rom_log_likelihood),
            bilateral_log_likelihood=bilateral_log_likelihood,
            gravity_log_likelihood=gravity_log_likelihood,
            soft_total_log_likelihood=soft_total_log_likelihood,
            report={
                "schema": "biospur-c2-owner-derived-physical-prefix-assessment-v1",
                "owner_input_binding": binding,
                "official_qmt_rooted_trajectory_consumed_before_physical_assessment": True,
                "qmt_branch_likelihood_ingested_only_after_this_gate": True,
                "topology_samples": topology_samples,
                "bilateral_crossing_or_mirror_evidence": {
                    "rows": bilateral_rows,
                    "gross_uncertainty_confirmed_fraction_by_pair": bilateral_confirmed_fraction_by_pair,
                    "gross_sustained_required_fraction": bilateral_required_fraction,
                    "soft_log_likelihood": bilateral_log_likelihood,
                    "single_marginal_or_transient_sample_is_hard_rejection": False,
                    "ordinary_human_worn_uncertainty_consumed": True,
                },
                "gravity_evidence": gravity_evidence,
                "gravity_soft_log_likelihood": gravity_log_likelihood,
                "hard_gravity_segments": list(hard_upright_segments),
                "hard_gravity_requires_sustained_uncertainty_confirmed_fraction": gravity_required_fraction,
                "limb_opposite_hemisphere_is_diagnostic_not_hard_gate": True,
                "bilateral_knee_evidence": {
                    "left_signed_flexion_rad": left_angles,
                    "right_signed_flexion_rad": right_angles,
                    "opposite_informed_fraction": opposite_fraction,
                    "required_fraction": knee_required_fraction,
                    "uncertainty_qualified": True,
                },
                "rom_evidence": rom_evidence,
                "rom_is_soft_probabilistic_not_hard_gate": True,
                "soft_total_log_likelihood": soft_total_log_likelihood,
                "hard_rejection_codes": list(unique_rejections),
                "caller_matrix_or_pose_truth_used": False,
            },
        )

    def audit(self) -> dict[str, Any]:
        return {
            "schema": "biospur-c2-scientific-fk-owner-v2",
            "renderer_geometry_source": "DIRECT_FROZEN_SCIENTIFIC_FK",
            "two_full_r3_connection_vectors_per_edge": True,
            "shared_joint_closure_required": True,
            "owner_derived_physical_prefix_assessment_available": self.physical_settings is not None,
            "runtime_qmt_input_binding_required": True,
            "runtime_qmt_input_binding_authority_configured": (
                self._expected_runtime_owner_id is not None
                and self._runtime_binding_secret is not None
            ),
            "viewer_rebase": False,
            "inverse_kinematics": False,
            "repair": False,
        }


def numeric_sensor_segment_connection_gate() -> dict[str, Any]:
    """Independent known-rotation oracle for the sensor/segment vector boundary."""

    sensor_from_segment = Rotation.from_euler("xyz", [37.0, -21.0, 13.0], degrees=True).as_matrix()
    segment_from_sensor = sensor_from_segment.T
    world_from_segment = Rotation.from_euler("zyx", [-31.0, 17.0, 9.0], degrees=True).as_matrix()
    world_from_sensor = world_from_segment @ segment_from_sensor
    sensor_vector = np.array([0.17, -0.08, 0.29])
    expected_direct = world_from_sensor @ sensor_vector
    correct_two_step = world_from_segment @ (segment_from_sensor @ sensor_vector)
    forbidden_swap = world_from_segment @ sensor_vector
    correct_error = float(np.linalg.norm(correct_two_step - expected_direct))
    forbidden_error = float(np.linalg.norm(forbidden_swap - expected_direct))
    return {
        "schema": "biospur-c2-sensor-segment-connection-known-rotation-gate-v1",
        "sensor_vector_m": sensor_vector.tolist(),
        "sensor_from_segment": sensor_from_segment.tolist(),
        "world_from_segment": world_from_segment.tolist(),
        "correct_two_step_vs_direct_world_sensor_error_m": correct_error,
        "forbidden_sensor_as_segment_swap_error_m": forbidden_error,
        "pass": correct_error <= 1e-12 and forbidden_error >= 1e-3,
    }


def numeric_physical_candidate_uncertainty_gate(
    settings: Mapping[str, Any],
    *,
    propagate_relabelled_raw_self_hash_mutation: bool = False,
) -> dict[str, Any]:
    """Owner-level marginal/gross crossing and raw-orientation mutations."""

    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    synthetic_runtime_owner_id = "SYNTHETIC_PHYSICAL_NUMERIC_OWNER"
    synthetic_binding_secret = b"biospur-c2-physical-numeric-owner-secret"
    owner = ScientificForwardKinematicsOwner(
        execution_guard=guard,
        physical_settings=settings["physical_candidates"],
        expected_runtime_owner_id=synthetic_runtime_owner_id,
        runtime_binding_secret=synthetic_binding_secret,
    )
    segment_names = tuple(dict.fromkeys(name for edge in ROOTED_EDGES for name in edge))
    base_positions = {
        "pelvis": np.array([0.0, 0.0, 0.0]),
        "torso": np.array([0.0, 0.0, 0.30]),
        "upper_arm_left": np.array([0.0, 0.24, 0.30]),
        "forearm_left": np.array([0.0, 0.54, 0.28]),
        "upper_arm_right": np.array([0.0, -0.24, 0.30]),
        "forearm_right": np.array([0.0, -0.54, 0.28]),
        "thigh_left": np.array([0.0, 0.11, -0.22]),
        "shank_left": np.array([0.0, 0.12, -0.66]),
        "thigh_right": np.array([0.0, -0.11, -0.22]),
        "shank_right": np.array([0.0, -0.12, -0.66]),
    }

    def branch_from_positions(
        positions: Mapping[str, np.ndarray],
        *,
        connection_sigma_m: float,
        world_from_segment_reference: Mapping[str, np.ndarray],
    ) -> SegmentFrameBranch:
        connections: dict[str, EdgeConnectionVectors] = {}
        for parent, child in ROOTED_EDGES:
            edge = EDGE_NAME_BY_ENDPOINTS[(parent, child)]
            joint = 0.5 * (np.asarray(positions[parent]) + np.asarray(positions[child]))
            parent_rotation = np.asarray(world_from_segment_reference[parent], dtype=float)
            child_rotation = np.asarray(world_from_segment_reference[child], dtype=float)
            connections[edge] = EdgeConnectionVectors(
                edge=edge,
                parent=parent,
                child=child,
                parent_sensor_to_joint_m=(
                    parent_rotation.T @ (joint - np.asarray(positions[parent]))
                ),
                child_sensor_to_joint_m=(
                    child_rotation.T @ (joint - np.asarray(positions[child]))
                ),
                covariance_m2=np.eye(6) * connection_sigma_m**2,
            )
        frame_covariance = {segment: np.eye(3) * np.deg2rad(1.0) ** 2 for segment in segment_names}
        return SegmentFrameBranch(
            branch_id="SYNTHETIC_PHYSICAL_BRANCH",
            axis_sign_by_edge={edge: 1 for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")},
            segment_from_sensor={segment: np.eye(3) for segment in segment_names},
            sensor_from_segment={segment: np.eye(3) for segment in segment_names},
            joint_frame_tangent_covariance_rad2=np.eye(30) * np.deg2rad(1.0) ** 2,
            frame_tangent_covariance_rad2=frame_covariance,
            paired_hinge_frame_tangent_covariance_rad2={
                edge: np.eye(6) * np.deg2rad(1.0) ** 2
                for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
            },
            connection_vectors_by_edge=connections,
            prior_weight=1.0,
            wear_log_likelihood=0.0,
            wear_profile_log_likelihood={"PRIMARY": 0.0},
            wear_gross_wrong_hemisphere=False,
            retained=True,
            report={"schema": "synthetic-physical-candidate-branch-v1"},
        )

    def assess(
        positions: Mapping[str, np.ndarray],
        *,
        connection_sigma_m: float,
        raw_source_mutation: bool = False,
        relabelled_raw_self_hash_mutation: bool = False,
        rotation_by_segment: Mapping[str, np.ndarray] | None = None,
        orientation_sigma_rad: float = np.deg2rad(1.0),
    ) -> PhysicalTrajectoryCandidateAssessment:
        count = 5
        rotation_by_segment = {} if rotation_by_segment is None else rotation_by_segment
        reference_rotations = {
            segment: np.asarray(rotation_by_segment.get(segment, np.eye(3)), dtype=float)
            for segment in segment_names
        }
        branch = branch_from_positions(
            positions,
            connection_sigma_m=connection_sigma_m,
            world_from_segment_reference=reference_rotations,
        )
        rotations = {
            segment: np.repeat(
                reference_rotations[segment][None, :, :],
                count,
                axis=0,
            )
            for segment in segment_names
        }
        covariance = {
            segment: np.repeat(
                (np.eye(3) * float(orientation_sigma_rad) ** 2)[None, :, :],
                count,
                axis=0,
            )
            for segment in segment_names
        }
        binding = {
            "schema": "biospur-c2-runtime-owned-physical-prefix-input-v1",
            "runtime_owner_id": synthetic_runtime_owner_id,
            "branch_id": branch.branch_id,
            "chronological_index": 0,
            "action": "SYNTHETIC_PHYSICAL_NUMERIC_GATE",
            "source": (
                "RAW_CONTINUOUS_VQF_UNCORRECTED_MUTATION"
                if raw_source_mutation
                else "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_CURRENT_SEALED_ORIENTED_ACTION"
            ),
            "raw_unqmt_orientation_allowed_to_drive_physical_gate": False,
            "qmt_branch_evidence_ingested_before_physical_gate": False,
            "rooted_qmt_trajectory_report": {
                "tree_semantics": "child_global = parent_global + time_varying_edge_deltaFilt",
            },
            "world_from_segment_sha256": {
                segment: _array_sha256(value) for segment, value in rotations.items()
            },
            "orientation_covariance_sha256": {
                segment: _array_sha256(value) for segment, value in covariance.items()
            },
        }
        if relabelled_raw_self_hash_mutation:
            binding["source"] = (
                "OFFICIAL_QMT_ROOTED_PARENT_PLUS_CHILD_CORRECTED_CURRENT_SEALED_ORIENTED_ACTION"
            )
            encoded = json.dumps(
                _binding_jsonable(binding), sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
            unkeyed_self_hash = sha256(encoded).hexdigest()
            binding["runtime_owner_binding_payload_sha256"] = unkeyed_self_hash
            binding["runtime_owner_token"] = unkeyed_self_hash
        else:
            payload_sha256, token = physical_input_binding_token(
                synthetic_binding_secret, binding,
            )
            binding["runtime_owner_binding_payload_sha256"] = payload_sha256
            binding["runtime_owner_token"] = token
        return owner.assess_prefix_trajectory(
            frame_branch=branch,
            world_from_segment_trajectory=rotations,
            orientation_tangent_covariance_rad2=covariance,
            owner_input_binding=binding,
        )

    if propagate_relabelled_raw_self_hash_mutation:
        # This negative path deliberately reaches the real FK owner.  A caller
        # may relabel raw arrays as official and recompute every public hash,
        # but cannot forge the same-runtime HMAC issued after QMT/rooted-tree
        # assembly.  Let the owner's exact Class-A rejection propagate to the
        # architecture mutation harness.
        assess(
            base_positions,
            connection_sigma_m=0.01,
            relabelled_raw_self_hash_mutation=True,
        )
        raise RuntimeError("relabeled raw/self-hashed arrays reached the physical candidate owner")

    marginal_positions = {key: value.copy() for key, value in base_positions.items()}
    gross_one_pair_positions = {key: value.copy() for key, value in base_positions.items()}
    for left, right in (
        ("upper_arm_left", "upper_arm_right"),
        ("forearm_left", "forearm_right"),
        ("thigh_left", "thigh_right"),
        ("shank_left", "shank_right"),
    ):
        marginal_positions[left][1], marginal_positions[right][1] = -0.005, 0.005
    gross_one_pair_positions["upper_arm_left"][1] = -0.45
    gross_one_pair_positions["upper_arm_right"][1] = 0.45
    marginal = assess(marginal_positions, connection_sigma_m=0.20)
    gross_one_pair = assess(gross_one_pair_positions, connection_sigma_m=1e-5)
    half_turn_x = Rotation.from_rotvec([np.pi, 0.0, 0.0]).as_matrix()
    half_turn_y = Rotation.from_rotvec([0.0, np.pi, 0.0]).as_matrix()
    limb_opposite = assess(
        base_positions,
        connection_sigma_m=0.01,
        rotation_by_segment={
            "upper_arm_left": half_turn_x,
            "forearm_left": half_turn_x,
        },
    )
    axial_opposite = assess(
        base_positions,
        connection_sigma_m=0.01,
        rotation_by_segment={"pelvis": half_turn_y, "torso": half_turn_y},
    )
    axial_uncertainty_covered = assess(
        base_positions,
        connection_sigma_m=0.01,
        rotation_by_segment={"pelvis": half_turn_y, "torso": half_turn_y},
        orientation_sigma_rad=np.deg2rad(35.0),
    )
    try:
        assess(base_positions, connection_sigma_m=0.01, raw_source_mutation=True)
    except ValueError as exc:
        raw_rejected = "must consume official QMT rooted propagation" in str(exc)
        raw_observed = str(exc)
    else:
        raw_rejected = False
        raw_observed = "RAW_UNQMT_ORIENTATION_REACHED_PHYSICAL_GATE"
    try:
        assess(
            base_positions,
            connection_sigma_m=0.01,
            relabelled_raw_self_hash_mutation=True,
        )
    except ValueError as exc:
        relabelled_raw_rejected = "runtime/QMT owner token is absent or forged" in str(exc)
        relabelled_raw_observed = str(exc)
    else:
        relabelled_raw_rejected = False
        relabelled_raw_observed = "RELABELLED_RAW_SELF_HASH_REACHED_PHYSICAL_GATE"
    return {
        "schema": "biospur-c2-owner-level-physical-candidate-uncertainty-gate-v1",
        "marginal_uncertainty_covered_crossing": {
            "physically_legal": marginal.physically_legal,
            "soft_log_likelihood": marginal.soft_total_log_likelihood,
            "report": dict(marginal.report),
            "pass": bool(marginal.physically_legal and marginal.soft_total_log_likelihood < 0.0),
        },
        "gross_sustained_single_pair_crossing": {
            "physically_legal": gross_one_pair.physically_legal,
            "report": dict(gross_one_pair.report),
            "pass": bool(
                not gross_one_pair.physically_legal
                and "GROSS_SUSTAINED_BILATERAL_CROSSING_OR_MIRROR"
                in gross_one_pair.report["hard_rejection_codes"]
                and gross_one_pair.report["bilateral_crossing_or_mirror_evidence"]
                ["gross_uncertainty_confirmed_fraction_by_pair"]
                ["upper_arm_left|upper_arm_right"]
                >= float(settings["physical_candidates"]["bilateral_crossing"]
                         ["gross_sustained_evidence_fraction"])
                and gross_one_pair.report["bilateral_crossing_or_mirror_evidence"]
                ["gross_uncertainty_confirmed_fraction_by_pair"]
                ["thigh_left|thigh_right"] == 0.0
            ),
        },
        "limb_opposite_gravity_hemisphere_is_diagnostic": {
            "physically_legal": limb_opposite.physically_legal,
            "report": dict(limb_opposite.report),
            "pass": bool(
                limb_opposite.physically_legal
                and limb_opposite.report["gravity_evidence"]["upper_arm_left"]
                ["confirmed_gross_wrong_fraction"] == 1.0
                and not limb_opposite.report["gravity_evidence"]["upper_arm_left"]
                ["hard_upright_segment"]
                and limb_opposite.report["gravity_evidence"]["upper_arm_left"]
                ["soft_log_likelihood_contribution"] == 0.0
            ),
        },
        "axial_sustained_gravity_contradiction": {
            "physically_legal": axial_opposite.physically_legal,
            "report": dict(axial_opposite.report),
            "pass": bool(
                not axial_opposite.physically_legal
                and "GROSS_GRAVITY_UP_WRONG_HEMISPHERE"
                in axial_opposite.report["hard_rejection_codes"]
            ),
        },
        "axial_gravity_uncertainty_covered": {
            "physically_legal": axial_uncertainty_covered.physically_legal,
            "report": dict(axial_uncertainty_covered.report),
            "pass": bool(
                axial_uncertainty_covered.physically_legal
                and "GROSS_GRAVITY_UP_WRONG_HEMISPHERE"
                not in axial_uncertainty_covered.report["hard_rejection_codes"]
            ),
        },
        "soft_total_composition": {
            "rom_log_likelihood": marginal.rom_log_likelihood,
            "bilateral_log_likelihood": marginal.bilateral_log_likelihood,
            "gravity_log_likelihood": marginal.gravity_log_likelihood,
            "soft_total_log_likelihood": marginal.soft_total_log_likelihood,
            "pass": bool(
                marginal.bilateral_log_likelihood < 0.0
                and np.isclose(
                    marginal.soft_total_log_likelihood,
                    marginal.rom_log_likelihood
                    + marginal.bilateral_log_likelihood
                    + marginal.gravity_log_likelihood,
                    rtol=0.0,
                    atol=1e-12,
                )
            ),
        },
        "raw_unqmt_orientation_substitution": {
            "observed": raw_observed,
            "pass": raw_rejected,
        },
        "raw_arrays_relabelled_official_with_self_hash": {
            "injected": (
                "RAW_ARRAYS_RELABELLED_AS_OFFICIAL_WITH_RECOMPUTED_UNKEYED_HASHES"
            ),
            "expected": "REJECT_MISSING_SAME_RUNTIME_QMT_ROOTED_HMAC_OWNER_TOKEN",
            "observed": relabelled_raw_observed,
            "pass": relabelled_raw_rejected,
        },
        "pass": bool(
            marginal.physically_legal
            and marginal.soft_total_log_likelihood < 0.0
            and not gross_one_pair.physically_legal
            and limb_opposite.physically_legal
            and not axial_opposite.physically_legal
            and axial_uncertainty_covered.physically_legal
            and np.isclose(
                marginal.soft_total_log_likelihood,
                marginal.rom_log_likelihood
                + marginal.bilateral_log_likelihood
                + marginal.gravity_log_likelihood,
                rtol=0.0,
                atol=1e-12,
            )
            and raw_rejected
            and relabelled_raw_rejected
        ),
    }
