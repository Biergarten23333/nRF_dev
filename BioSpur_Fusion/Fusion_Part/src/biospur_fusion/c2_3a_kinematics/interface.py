"""Read-only, zero-pose-change kinematics interface over frozen Capture2.

The Cartesian skeleton and every Jacobian in this module retain the frozen
``middle_proxy`` renderer semantics. They are regression/display quantities,
not physical joint centres, antenna positions, or range-prediction geometry.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .provenance import (
    BASE_CONFIG,
    DIAGNOSTIC_REPORT,
    EFFECTIVE_AMENDMENT,
    FROZEN_REPLAY_CALIBRATION,
    HOLDOUT_REPORT,
    HOLDOUT_TRAJECTORY,
    PRIMARY_TRAJECTORY,
    WORKSPACE,
    FrozenC2ProvenanceError,
    FrozenC2Verification,
    verify_frozen_c2,
)


EPISODE_KEYS = tuple(f"{index:02d}" for index in range(19))
HOLDOUT_EPISODE_KEYS = ("H01_boxing", "H02_golf")
SEGMENTS = (
    "forearm_left",
    "forearm_right",
    "upper_arm_left",
    "upper_arm_right",
    "torso",
    "pelvis",
    "thigh_left",
    "thigh_right",
    "shank_left",
    "shank_right",
)
POINT_NAMES = (
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
JACOBIAN_COLUMNS = ("root_translation",) + SEGMENTS
DISPLAY_PROXY_SCOPE = "ZERO_POSE_CHANGE_DISPLAY_PROXY_REGRESSION_ONLY"
SEGMENT_FRAME_SCOPE = "SEALED_C2_SEGMENT_FRAME_NOT_CERTIFIED_ANATOMICAL"

Coordinates = Literal["internal", "display"]
UnknownClassification = Literal["MEASURE", "SOFT_PRIOR", "UNOBSERVABLE"]


def _immutable_array(value: Any, *, dtype: Any | None = None) -> np.ndarray:
    array = np.array(value, dtype=dtype, copy=True)
    array.setflags(write=False)
    return array


def _immutable_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


def _json_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise FrozenC2ProvenanceError(f"JSON object required: {path}")
    return value


def _skew(vector: np.ndarray) -> np.ndarray:
    x, y, z = np.asarray(vector, dtype=float).reshape(3)
    return np.array(
        [[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]], dtype=float
    )


@dataclass(frozen=True)
class SegmentSeries:
    """One frozen segment stream; no covariance is fabricated."""

    time_root_s: np.ndarray
    quat_world_segment_wxyz: np.ndarray
    mask: np.ndarray
    orientation_covariance_rad2: None = None


@dataclass(frozen=True)
class FrozenEpisode:
    """Opaque numeric episode container with no action semantics."""

    key: str
    segments: Mapping[str, SegmentSeries]
    frame_count: int
    valid_frame_mask: np.ndarray


@dataclass(frozen=True)
class SegmentFrame:
    name: str
    node_id: str
    parent: str | None
    frame_scope: str = SEGMENT_FRAME_SCOPE
    additional_rotation_from_frozen: None = None


@dataclass(frozen=True)
class JointEdge:
    name: str
    parent: str
    child: str
    joint_kind: Literal["hinge", "connection"]
    frame_scope: str = SEGMENT_FRAME_SCOPE


@dataclass(frozen=True)
class FrozenHingeAxis:
    """Numeric diagnostic metadata, not an axis-estimation implementation."""

    edge: str
    parent_segment: str
    child_segment: str
    parent_axis_reset_segment: np.ndarray
    child_axis_reset_segment: np.ndarray
    covariance_rad2: None
    source_artifact: str
    source_sha256: str
    source_license: str = "LicenseRef-Unspecified"
    rerun_or_reimplementation_allowed: bool = False


@dataclass(frozen=True)
class UnknownStateSlot:
    """An evidence-classified absent state with no numerical default."""

    state_id: str
    name: str
    classification: UnknownClassification
    owner: str
    validation: str
    value: None = None
    covariance: None = None
    provenance: None = None


@dataclass(frozen=True)
class FutureFusionSlots:
    """Inert schemas only; 3A never applies these slots to pose or FK."""

    antenna_phase_centres: Mapping[str, UnknownStateSlot]
    body_occlusion_geometry: UnknownStateSlot
    world_from_frozen_root: UnknownStateSlot
    root_translation_velocity_drift: UnknownStateSlot
    numerical_consumer_in_3a: bool = False

    @classmethod
    def unset(cls, node_to_segment: Mapping[str, str]) -> "FutureFusionSlots":
        phase_centres = {
            node: UnknownStateSlot(
                state_id="U01",
                name=f"{node}_antenna_phase_centre_in_{segment}_frame_m",
                classification="MEASURE",
                owner="future independent per-node metrology",
                validation=(
                    "measured 3-D phase centre plus covariance and provenance; "
                    "zero and mirrored defaults forbidden"
                ),
            )
            for node, segment in node_to_segment.items()
        }
        return cls(
            antenna_phase_centres=MappingProxyType(phase_centres),
            body_occlusion_geometry=UnknownStateSlot(
                state_id="U06",
                name="body_surface_and_occlusion_geometry",
                classification="MEASURE",
                owner="future body-aware UWB task",
                validation="measured subject/mount geometry and link behavior",
            ),
            world_from_frozen_root=UnknownStateSlot(
                state_id="U08",
                name="proper_world_from_frozen_root_se3",
                classification="UNOBSERVABLE",
                owner="future fixed-anchor UWB solver",
                validation="absent in 3A; any future rotation must have det=+1",
            ),
            root_translation_velocity_drift=UnknownStateSlot(
                state_id="U10",
                name="root_translation_velocity_and_drift",
                classification="UNOBSERVABLE",
                owner="future UWB fusion task",
                validation="zero is not an observation and 3A cannot update it",
            ),
        )

    def assert_compatible(self, node_ids: tuple[str, ...]) -> None:
        if tuple(self.antenna_phase_centres) != node_ids:
            raise ValueError("future phase-centre slots do not match frozen node order")
        slots = (
            *self.antenna_phase_centres.values(),
            self.body_occlusion_geometry,
            self.world_from_frozen_root,
            self.root_translation_velocity_drift,
        )
        if self.numerical_consumer_in_3a:
            raise ValueError("future slots cannot become numerical 3A consumers")
        if any(
            slot.value is not None
            or slot.covariance is not None
            or slot.provenance is not None
            for slot in slots
        ):
            raise ValueError("the frozen C2 gate authorizes only absent future slots")


@dataclass(frozen=True)
class DisplayProxyGeometry:
    """Exact accepted middle-proxy values, never physical anatomy."""

    torso_height_m: float
    hip_span_m: float
    shoulder_span_m: float
    segment_length_m: Mapping[str, float]
    scope: str = DISPLAY_PROXY_SCOPE
    physical_joint_centre_geometry: bool = False
    uwb_antenna_prediction_geometry: bool = False


@dataclass(frozen=True)
class PointJacobian:
    """Display-proxy point differential for local right perturbations."""

    point: str
    coordinates: Coordinates
    dense: np.ndarray
    columns: tuple[str, ...] = JACOBIAN_COLUMNS
    rotation_parameterization: str = (
        "RIGHT_MULTIPLICATIVE_LOCAL_ROTATION_VECTOR_PER_FROZEN_SEGMENT"
    )
    scope: str = DISPLAY_PROXY_SCOPE
    physical_or_uwb_prediction_allowed: bool = False


@dataclass(frozen=True)
class FrozenC2Kinematics3A:
    """Immutable derivative interface around the formally frozen C2 arrays."""

    verification: FrozenC2Verification
    episodes: Mapping[str, FrozenEpisode]
    node_to_segment: Mapping[str, str]
    segment_frames: Mapping[str, SegmentFrame]
    joint_edges: tuple[JointEdge, ...]
    hinge_axes: Mapping[str, FrozenHingeAxis]
    output_matrix_world_display_from_internal: np.ndarray
    geometry: DisplayProxyGeometry
    future_slots: FutureFusionSlots
    scientific_pass: bool = False
    solver: None = None

    def series(self, episode: str, segment: str) -> SegmentSeries:
        try:
            return self.episodes[episode].segments[segment]
        except KeyError as exc:
            raise KeyError(f"unknown frozen episode/segment: {episode}/{segment}") from exc

    def forward_kinematics(
        self,
        episode: str,
        frame: int,
        *,
        coordinates: Coordinates = "display",
    ) -> Mapping[str, np.ndarray]:
        """Return the accepted display-proxy FK without changing pose."""

        matrices = self._frame_matrices(episode, frame)
        points = self._internal_points(matrices)
        if coordinates == "display":
            matrix = self.output_matrix_world_display_from_internal
            points = {name: matrix @ point for name, point in points.items()}
        elif coordinates != "internal":
            raise ValueError("coordinates must be 'internal' or 'display'")
        return MappingProxyType(
            {name: _immutable_array(point, dtype=float) for name, point in points.items()}
        )

    def point_jacobian(
        self,
        episode: str,
        frame: int,
        point: str,
        *,
        coordinates: Coordinates = "display",
    ) -> PointJacobian:
        """Differentiate only the frozen middle-proxy Cartesian construction."""

        if point not in POINT_NAMES:
            raise KeyError(f"unknown display-proxy point: {point}")
        matrices = self._frame_matrices(episode, frame)
        dense = np.zeros((3, 3 * len(JACOBIAN_COLUMNS)), dtype=float)
        dense[:, :3] = np.eye(3)
        terms = self._point_terms()[point]
        for segment, local_vector in terms:
            column = 3 * (1 + SEGMENTS.index(segment))
            dense[:, column : column + 3] += (
                -matrices[segment] @ _skew(local_vector)
            )
        if coordinates == "display":
            dense = self.output_matrix_world_display_from_internal @ dense
        elif coordinates != "internal":
            raise ValueError("coordinates must be 'internal' or 'display'")
        return PointJacobian(
            point=point,
            coordinates=coordinates,
            dense=_immutable_array(dense, dtype=float),
        )

    def _frame_matrices(
        self, episode: str, frame: int
    ) -> Mapping[str, np.ndarray]:
        if episode not in self.episodes:
            raise KeyError(f"unknown frozen episode: {episode}")
        row = self.episodes[episode]
        if not 0 <= frame < row.frame_count:
            raise IndexError(f"frame outside frozen episode {episode}: {frame}")
        if not row.valid_frame_mask[frame]:
            raise ValueError(f"frame is masked in at least one segment: {episode}/{frame}")
        return {
            segment: Rotation.from_quat(
                row.segments[segment].quat_world_segment_wxyz[frame][[1, 2, 3, 0]]
            ).as_matrix()
            for segment in SEGMENTS
        }

    def _internal_points(
        self, matrices: Mapping[str, np.ndarray]
    ) -> dict[str, np.ndarray]:
        length = self.geometry.segment_length_m
        root = np.zeros(3, dtype=float)
        pelvis = matrices["pelvis"]
        torso = matrices["torso"]
        shoulder_mid = root + torso @ np.array(
            [0.0, 0.0, self.geometry.torso_height_m]
        )
        shoulder_left = shoulder_mid + torso @ np.array(
            [-0.5 * self.geometry.shoulder_span_m, 0.0, 0.0]
        )
        shoulder_right = shoulder_mid + torso @ np.array(
            [0.5 * self.geometry.shoulder_span_m, 0.0, 0.0]
        )
        hip_left = root + pelvis @ np.array(
            [-0.5 * self.geometry.hip_span_m, 0.0, 0.0]
        )
        hip_right = root + pelvis @ np.array(
            [0.5 * self.geometry.hip_span_m, 0.0, 0.0]
        )
        elbow_left = shoulder_left + matrices["upper_arm_left"] @ np.array(
            [0.0, 0.0, -length["upper_arm_left"]]
        )
        wrist_left = elbow_left + matrices["forearm_left"] @ np.array(
            [0.0, 0.0, -length["forearm_left"]]
        )
        elbow_right = shoulder_right + matrices["upper_arm_right"] @ np.array(
            [0.0, 0.0, -length["upper_arm_right"]]
        )
        wrist_right = elbow_right + matrices["forearm_right"] @ np.array(
            [0.0, 0.0, -length["forearm_right"]]
        )
        knee_left = hip_left + matrices["thigh_left"] @ np.array(
            [0.0, 0.0, -length["thigh_left"]]
        )
        ankle_left = knee_left + matrices["shank_left"] @ np.array(
            [0.0, 0.0, -length["shank_left"]]
        )
        knee_right = hip_right + matrices["thigh_right"] @ np.array(
            [0.0, 0.0, -length["thigh_right"]]
        )
        ankle_right = knee_right + matrices["shank_right"] @ np.array(
            [0.0, 0.0, -length["shank_right"]]
        )
        return {
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

    def _point_terms(self) -> Mapping[str, tuple[tuple[str, np.ndarray], ...]]:
        length = self.geometry.segment_length_m
        torso_up = np.array([0.0, 0.0, self.geometry.torso_height_m])
        shoulder_left = np.array(
            [-0.5 * self.geometry.shoulder_span_m, 0.0, 0.0]
        )
        shoulder_right = -shoulder_left
        hip_left = np.array([-0.5 * self.geometry.hip_span_m, 0.0, 0.0])
        hip_right = -hip_left

        def down(segment: str) -> np.ndarray:
            return np.array([0.0, 0.0, -length[segment]])

        terms: dict[str, tuple[tuple[str, np.ndarray], ...]] = {
            "pelvis_center": (),
            "shoulder_mid": (("torso", torso_up),),
            "shoulder_left": (("torso", torso_up), ("torso", shoulder_left)),
            "shoulder_right": (("torso", torso_up), ("torso", shoulder_right)),
            "hip_left": (("pelvis", hip_left),),
            "hip_right": (("pelvis", hip_right),),
            "elbow_left": (
                ("torso", torso_up),
                ("torso", shoulder_left),
                ("upper_arm_left", down("upper_arm_left")),
            ),
            "elbow_right": (
                ("torso", torso_up),
                ("torso", shoulder_right),
                ("upper_arm_right", down("upper_arm_right")),
            ),
            "knee_left": (("pelvis", hip_left), ("thigh_left", down("thigh_left"))),
            "knee_right": (("pelvis", hip_right), ("thigh_right", down("thigh_right"))),
        }
        terms["wrist_left"] = terms["elbow_left"] + (
            ("forearm_left", down("forearm_left")),
        )
        terms["wrist_right"] = terms["elbow_right"] + (
            ("forearm_right", down("forearm_right")),
        )
        terms["ankle_left"] = terms["knee_left"] + (
            ("shank_left", down("shank_left")),
        )
        terms["ankle_right"] = terms["knee_right"] + (
            ("shank_right", down("shank_right")),
        )
        return MappingProxyType(terms)


@dataclass(frozen=True)
class FrozenC2HoldoutDiagnostics:
    """Separate read-only H01/H02 surface; it cannot refit calibration."""

    verification: FrozenC2Verification
    episodes: Mapping[str, FrozenEpisode]
    output_matrix_world_display_from_internal: np.ndarray
    source_artifact: str
    source_sha256: str
    frozen_calibration_artifact: str
    frozen_calibration_sha256: str
    calibration_refit_on_hxx: bool = False
    holdout_action_semantics_used_for_fit: bool = False
    viewer_ik_rebase_retarget_or_repair: bool = False
    scientific_pass: bool = False

    def series(self, episode: str, segment: str) -> SegmentSeries:
        try:
            return self.episodes[episode].segments[segment]
        except KeyError as exc:
            raise KeyError(
                f"unknown frozen holdout episode/segment: {episode}/{segment}"
            ) from exc


def _load_contracts(
    workspace: Path,
) -> tuple[
    Mapping[str, str],
    Mapping[str, SegmentFrame],
    tuple[JointEdge, ...],
    DisplayProxyGeometry,
]:
    base = _json_object(workspace / BASE_CONFIG)
    amendment = _json_object(workspace / EFFECTIVE_AMENDMENT)
    node_to_segment_raw = base.get("node_to_segment")
    if not isinstance(node_to_segment_raw, dict):
        raise FrozenC2ProvenanceError("frozen node-to-segment mapping is missing")
    node_to_segment = {
        str(node): str(segment) for node, segment in node_to_segment_raw.items()
    }
    if tuple(node_to_segment.values()) != SEGMENTS or len(node_to_segment) != 10:
        raise FrozenC2ProvenanceError("frozen ten-segment identity changed")

    edge_pairs = tuple(tuple(row) for row in base.get("edges", ()))
    expected_pairs = (
        ("pelvis", "torso"),
        ("torso", "upper_arm_left"),
        ("upper_arm_left", "forearm_left"),
        ("torso", "upper_arm_right"),
        ("upper_arm_right", "forearm_right"),
        ("pelvis", "thigh_left"),
        ("thigh_left", "shank_left"),
        ("pelvis", "thigh_right"),
        ("thigh_right", "shank_right"),
    )
    if edge_pairs != expected_pairs:
        raise FrozenC2ProvenanceError("frozen nine-edge topology changed")
    hinge_by_pair = {
        tuple(pair): str(name) for name, pair in base.get("hinges", {}).items()
    }
    edge_name_by_pair = {
        ("pelvis", "torso"): "pelvis_torso",
        ("torso", "upper_arm_left"): "shoulder_left",
        ("upper_arm_left", "forearm_left"): "elbow_left",
        ("torso", "upper_arm_right"): "shoulder_right",
        ("upper_arm_right", "forearm_right"): "elbow_right",
        ("pelvis", "thigh_left"): "hip_left",
        ("thigh_left", "shank_left"): "knee_left",
        ("pelvis", "thigh_right"): "hip_right",
        ("thigh_right", "shank_right"): "knee_right",
    }
    edges = tuple(
        JointEdge(
            name=hinge_by_pair.get(pair, edge_name_by_pair[pair]),
            parent=pair[0],
            child=pair[1],
            joint_kind="hinge" if pair in hinge_by_pair else "connection",
        )
        for pair in expected_pairs
    )
    parent_by_child = {edge.child: edge.parent for edge in edges}
    node_by_segment = {segment: node for node, segment in node_to_segment.items()}
    frames = MappingProxyType(
        {
            segment: SegmentFrame(
                name=segment,
                node_id=node_by_segment[segment],
                parent=parent_by_child.get(segment),
            )
            for segment in SEGMENTS
        }
    )

    if amendment.get("append_only") is not True:
        raise FrozenC2ProvenanceError("effective geometry amendment is not append-only")
    changes = {
        row.get("path"): row.get("value")
        for row in amendment.get("effective_changes", ())
        if isinstance(row, dict)
    }
    torso = changes.get("proxy_geometry.torso_display_geometry")
    hip = changes.get("proxy_geometry.trochanter_proxy_span_m")
    if (
        not isinstance(torso, dict)
        or torso.get("status") != "UNOBSERVED_NO_SINGLE_REPRESENTATIVE"
        or torso.get("calibration_consumer") is not False
        or torso.get("models_are_anatomical_estimates") is not False
        or not isinstance(torso.get("models_m"), list)
        or len(torso["models_m"]) != 3
        or not isinstance(hip, dict)
        or hip.get("internal_hip_center_spacing") is not False
        or hip.get("may_define_display_hip_joint") is not False
    ):
        raise FrozenC2ProvenanceError("display-proxy geometry restrictions changed")
    proxy = base.get("proxy_geometry")
    if not isinstance(proxy, dict):
        raise FrozenC2ProvenanceError("frozen proxy geometry is missing")
    length = {
        segment: float(proxy[f"{segment}_m"]["nominal"])
        for segment in (
            "upper_arm_left",
            "upper_arm_right",
            "forearm_left",
            "forearm_right",
            "thigh_left",
            "thigh_right",
            "shank_left",
            "shank_right",
        )
    }
    geometry = DisplayProxyGeometry(
        torso_height_m=float(torso["models_m"][1]),
        # This is the accepted middle_proxy renderer value in the hash-bound
        # DIRECT_FK_OWNER, not the 0.335 m external trochanter measurement.
        hip_span_m=0.23,
        shoulder_span_m=float(proxy["acromion_proxy_span_m"]["nominal"]),
        segment_length_m=MappingProxyType(length),
    )
    return MappingProxyType(node_to_segment), frames, edges, geometry


def _load_episode_archive(
    workspace: Path,
    artifact: Path,
    episode_keys: tuple[str, ...],
    expected_array_count: int,
) -> tuple[Mapping[str, FrozenEpisode], np.ndarray]:
    required = {
        f"trajectory/{episode}/{segment}/{field}"
        for episode in episode_keys
        for segment in SEGMENTS
        for field in ("time_root_s", "quat_world_segment_wxyz", "mask")
    }
    required.update(
        {
            "output_coordinates/matrix_world_output_from_internal",
            "output_coordinates/plane_normal_world_internal",
        }
    )
    episodes: dict[str, FrozenEpisode] = {}
    try:
        with np.load(workspace / artifact, allow_pickle=False) as archive:
            if (
                set(archive.files) != required
                or len(archive.files) != expected_array_count
            ):
                raise FrozenC2ProvenanceError("frozen trajectory field set changed")
            for episode in episode_keys:
                rows: dict[str, SegmentSeries] = {}
                frame_count: int | None = None
                masks: list[np.ndarray] = []
                for segment in SEGMENTS:
                    base = f"trajectory/{episode}/{segment}"
                    time_s = _immutable_array(archive[f"{base}/time_root_s"], dtype=float)
                    quat = _immutable_array(
                        archive[f"{base}/quat_world_segment_wxyz"], dtype=float
                    )
                    mask = _immutable_array(archive[f"{base}/mask"], dtype=bool)
                    if (
                        time_s.ndim != 1
                        or quat.shape != (len(time_s), 4)
                        or mask.shape != time_s.shape
                        or not np.all(np.isfinite(time_s))
                        or not np.all(np.isfinite(quat))
                        or np.any(np.diff(time_s) <= 0.0)
                        or not np.allclose(
                            np.linalg.norm(quat, axis=1), 1.0, rtol=0.0, atol=1e-12
                        )
                    ):
                        raise FrozenC2ProvenanceError(
                            f"invalid frozen trajectory row: {episode}/{segment}"
                        )
                    if frame_count is None:
                        frame_count = len(time_s)
                    elif frame_count != len(time_s):
                        raise FrozenC2ProvenanceError(
                            f"segment frame-count mismatch: {episode}"
                        )
                    rows[segment] = SegmentSeries(time_s, quat, mask)
                    masks.append(mask)
                valid = _immutable_array(np.logical_and.reduce(masks), dtype=bool)
                episodes[episode] = FrozenEpisode(
                    key=episode,
                    segments=MappingProxyType(rows),
                    frame_count=int(frame_count or 0),
                    valid_frame_mask=valid,
                )
            output = _immutable_array(
                archive["output_coordinates/matrix_world_output_from_internal"],
                dtype=float,
            )
    except (OSError, ValueError, KeyError) as exc:
        raise FrozenC2ProvenanceError(
            f"cannot load frozen trajectory NPZ: {artifact}"
        ) from exc
    if (
        output.shape != (3, 3)
        or not np.allclose(output.T @ output, np.eye(3), atol=1e-12)
        or not np.allclose(output @ output, np.eye(3), atol=1e-12)
        or not np.isclose(np.linalg.det(output), -1.0, atol=1e-12)
    ):
        raise FrozenC2ProvenanceError("frozen display reflection changed")
    return MappingProxyType(episodes), output


def _load_episodes(
    workspace: Path,
) -> tuple[Mapping[str, FrozenEpisode], np.ndarray]:
    return _load_episode_archive(
        workspace, PRIMARY_TRAJECTORY, EPISODE_KEYS, expected_array_count=572
    )


def _load_hinge_axes(
    workspace: Path,
    edges: tuple[JointEdge, ...],
) -> Mapping[str, FrozenHingeAxis]:
    report = _json_object(workspace / DIAGNOSTIC_REPORT)
    raw = report.get("qmt_olsson_hinge_axes")
    if not isinstance(raw, dict):
        raise FrozenC2ProvenanceError("sealed hinge-axis diagnostics are missing")
    hinge_edges = {edge.name: edge for edge in edges if edge.joint_kind == "hinge"}
    if set(raw) != set(hinge_edges):
        raise FrozenC2ProvenanceError("sealed hinge-axis key set changed")
    result: dict[str, FrozenHingeAxis] = {}
    for name, edge in hinge_edges.items():
        row = raw[name]
        if not isinstance(row, dict) or row.get("primitive") != (
            "qmt.jointAxisEstHingeOlsson_unmodified"
        ):
            raise FrozenC2ProvenanceError(f"hinge-axis provenance changed: {name}")
        parent = _immutable_array(row.get("parent_axis_reset_segment"), dtype=float)
        child = _immutable_array(row.get("child_axis_reset_segment"), dtype=float)
        if (
            parent.shape != (3,)
            or child.shape != (3,)
            or not np.all(np.isfinite(parent))
            or not np.all(np.isfinite(child))
            or not np.isclose(np.linalg.norm(parent), 1.0, atol=1e-12)
            or not np.isclose(np.linalg.norm(child), 1.0, atol=1e-12)
        ):
            raise FrozenC2ProvenanceError(f"invalid frozen hinge axes: {name}")
        result[name] = FrozenHingeAxis(
            edge=name,
            parent_segment=edge.parent,
            child_segment=edge.child,
            parent_axis_reset_segment=parent,
            child_axis_reset_segment=child,
            covariance_rad2=None,
            source_artifact=str(DIAGNOSTIC_REPORT),
            source_sha256=(
                "0e1d1dac79efebd8ddbcc928d5aa3ba56e52bfec4a040bb3301176c4b84b9aaa"
            ),
        )
    return MappingProxyType(result)


def load_frozen_c2_3a(
    *,
    workspace: Path = WORKSPACE,
    future_slots: FutureFusionSlots | None = None,
) -> FrozenC2Kinematics3A:
    """Verify the complete freeze, then load its read-only 3A projection."""

    workspace = workspace.resolve()
    verification = verify_frozen_c2(workspace)
    node_to_segment, frames, edges, geometry = _load_contracts(workspace)
    episodes, output = _load_episodes(workspace)
    hinge_axes = _load_hinge_axes(workspace, edges)
    slots = FutureFusionSlots.unset(node_to_segment) if future_slots is None else future_slots
    slots.assert_compatible(tuple(node_to_segment))
    return FrozenC2Kinematics3A(
        verification=verification,
        episodes=episodes,
        node_to_segment=node_to_segment,
        segment_frames=frames,
        joint_edges=edges,
        hinge_axes=hinge_axes,
        output_matrix_world_display_from_internal=output,
        geometry=geometry,
        future_slots=slots,
    )


def load_frozen_c2_hxx_diagnostics(
    *, workspace: Path = WORKSPACE
) -> FrozenC2HoldoutDiagnostics:
    """Load H01/H02 as a separate immutable, no-refit diagnostic surface."""

    workspace = workspace.resolve()
    verification = verify_frozen_c2(workspace)
    episodes, output = _load_episode_archive(
        workspace,
        HOLDOUT_TRAJECTORY,
        HOLDOUT_EPISODE_KEYS,
        expected_array_count=62,
    )
    report = _json_object(workspace / HOLDOUT_REPORT)
    trajectory = report.get("trajectory")
    frozen = report.get("frozen_calibration")
    artifact = frozen.get("artifact") if isinstance(frozen, dict) else None
    report_output = report.get("output_coordinate_convention")
    if (
        report.get("schema") != "biospur-c2-hxx-frozen-replay-report-v1"
        or report.get("status") != "DIAGNOSTIC_HOLDOUT_REPLAY"
        or report.get("source_scope") != "CAPTURE2_H01_H02_ONLY"
        or report.get("calibration_refit_on_hxx") is not False
        or report.get("holdout_action_semantics_used_for_fit") is not False
        or report.get("viewer_ik_rebase_retarget_or_repair") is not False
        or report.get("scientific_pass") is not False
        or not isinstance(trajectory, dict)
        or trajectory.get("path") != str(HOLDOUT_TRAJECTORY)
        or trajectory.get("sha256")
        != "da0855cb3b440cfbc565d60c4aedc0dbbe855fb3caff1e53ec7350c91929d639"
        or trajectory.get("array_count") != 62
        or not isinstance(artifact, dict)
        or artifact.get("path") != str(FROZEN_REPLAY_CALIBRATION)
        or artifact.get("sha256")
        != "ddc25eef63dce56065478dc331d667f3ec85502193c6e11f3cee1e83abd2431d"
        or artifact.get("holdout_payload_used_during_fit") is not False
        or not isinstance(report_output, dict)
        or report_output.get("quaternions_modified") is not False
        or report_output.get("per_action_selection") is not False
        or not np.array_equal(
            np.asarray(report_output.get("matrix_world_output_from_internal")),
            output,
        )
    ):
        raise FrozenC2ProvenanceError("H01/H02 no-refit diagnostic contract changed")
    return FrozenC2HoldoutDiagnostics(
        verification=verification,
        episodes=episodes,
        output_matrix_world_display_from_internal=output,
        source_artifact=str(HOLDOUT_TRAJECTORY),
        source_sha256=(
            "da0855cb3b440cfbc565d60c4aedc0dbbe855fb3caff1e53ec7350c91929d639"
        ),
        frozen_calibration_artifact=str(FROZEN_REPLAY_CALIBRATION),
        frozen_calibration_sha256=(
            "ddc25eef63dce56065478dc331d667f3ec85502193c6e11f3cee1e83abd2431d"
        ),
    )


__all__ = [
    "DISPLAY_PROXY_SCOPE",
    "EPISODE_KEYS",
    "FrozenC2Kinematics3A",
    "FrozenC2HoldoutDiagnostics",
    "FrozenEpisode",
    "FrozenHingeAxis",
    "FutureFusionSlots",
    "HOLDOUT_EPISODE_KEYS",
    "JACOBIAN_COLUMNS",
    "JointEdge",
    "POINT_NAMES",
    "PointJacobian",
    "SEGMENTS",
    "SEGMENT_FRAME_SCOPE",
    "SegmentFrame",
    "SegmentSeries",
    "UnknownStateSlot",
    "load_frozen_c2_3a",
    "load_frozen_c2_hxx_diagnostics",
]
