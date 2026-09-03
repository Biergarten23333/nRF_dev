"""One generalized-coordinate body model shared by every Root-R6A0 factor."""
from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from .contracts import CalibrationSlot, CalibrationStatus
from .math3d import Pose, central_jacobian, so3_exp


class CalibrationUnavailable(RuntimeError):
    """Raised when executable geometry would require an unresolved real slot."""


@dataclass(frozen=True)
class JointDefinition:
    joint_id: str
    parent: str
    child: str
    dof_family: str
    parent_offset_slot: str
    child_offset_slot: str
    rest_rotation_slot: str


@dataclass(frozen=True)
class SensorDefinition:
    sensor_id: str
    segment: str
    extrinsic_slot: str
    clock_slot: str


@dataclass(frozen=True)
class TagDefinition:
    tag_id: str
    segment: str
    lever_slot: str


@dataclass(frozen=True)
class AnchorDefinition:
    anchor_id: int
    position_slot: str
    delay_slot: str


@dataclass(frozen=True)
class DerivedPointDefinition:
    point_id: str
    segment: str
    offset_slot: str


@dataclass(frozen=True)
class StaticCalibration:
    slots: Mapping[str, CalibrationSlot]

    def slot(self, slot_id: str) -> CalibrationSlot:
        if slot_id not in self.slots:
            raise KeyError(f"missing calibration slot {slot_id}")
        return self.slots[slot_id]

    def vector(self, slot_id: str, dimension: int) -> np.ndarray:
        slot = self.slot(slot_id)
        if slot.value is None:
            raise CalibrationUnavailable(f"{slot_id} is {slot.status.value}")
        value = np.asarray(slot.value, float)
        if value.shape != (dimension,):
            raise ValueError(f"{slot_id} expected dimension {dimension}, got {value.shape}")
        return value

    def pose(self, slot_id: str) -> Pose:
        value = self.vector(slot_id, 6)
        return Pose(so3_exp(value[:3]), value[3:])

    def with_value(self, slot_id: str, value: Sequence[float]) -> "StaticCalibration":
        current = self.slot(slot_id)
        updated = dict(self.slots)
        updated[slot_id] = replace(current, value=tuple(float(x) for x in value))
        return StaticCalibration(updated)


@dataclass(frozen=True)
class KeyframeState:
    time_s: float
    root_translation_model_m: np.ndarray
    root_rotation_model_rotvec: np.ndarray
    root_velocity_model_mps: np.ndarray
    joint_rotvec: Mapping[str, np.ndarray]
    joint_rate_rad_s: Mapping[str, np.ndarray]
    gyro_bias_rad_s: Mapping[str, np.ndarray]
    accel_bias_mps2: Mapping[str, np.ndarray]
    covariance: np.ndarray

    def validate(self, model: "BodyModel") -> None:
        for name, value in (
            ("root_translation_model_m", self.root_translation_model_m),
            ("root_rotation_model_rotvec", self.root_rotation_model_rotvec),
            ("root_velocity_model_mps", self.root_velocity_model_mps),
        ):
            if np.asarray(value, float).shape != (3,) or not np.isfinite(value).all():
                raise ValueError(f"invalid {name}")
        expected_joints = set(model.joint_ids)
        expected_nodes = set(model.imu_ids)
        if set(self.joint_rotvec) != expected_joints or set(self.joint_rate_rad_s) != expected_joints:
            raise ValueError("state joint inventory does not match BodyModel")
        if set(self.gyro_bias_rad_s) != expected_nodes or set(self.accel_bias_mps2) != expected_nodes:
            raise ValueError("independent bias inventory does not match IMU inventory")
        vectors = list(self.joint_rotvec.values()) + list(self.joint_rate_rad_s.values())
        vectors += list(self.gyro_bias_rad_s.values()) + list(self.accel_bias_mps2.values())
        if any(np.asarray(value, float).shape != (3,) or not np.isfinite(value).all() for value in vectors):
            raise ValueError("joint/rate/bias values must be finite vectors")
        expected_dimension = 9 + 6 * len(model.joint_ids) + 6 * len(model.imu_ids)
        covariance = np.asarray(self.covariance, float)
        if covariance.shape != (expected_dimension, expected_dimension):
            raise ValueError(f"state covariance expected {(expected_dimension, expected_dimension)}")
        if not np.isfinite(covariance).all() or not np.allclose(covariance, covariance.T, atol=1e-12):
            raise ValueError("state covariance must be finite and symmetric")
        if np.min(np.linalg.eigvalsh(covariance)) < -1e-12:
            raise ValueError("state covariance must be PSD")

    def configuration_vector(self, joint_order: Sequence[str]) -> np.ndarray:
        return np.concatenate((
            np.asarray(self.root_translation_model_m, float),
            np.asarray(self.root_rotation_model_rotvec, float),
            *(np.asarray(self.joint_rotvec[joint], float) for joint in joint_order),
        ))

    def with_configuration(self, vector: np.ndarray, joint_order: Sequence[str]) -> "KeyframeState":
        value = np.asarray(vector, float)
        expected = 6 + 3 * len(joint_order)
        if value.shape != (expected,):
            raise ValueError(f"configuration vector expected {expected}")
        joints = {joint: value[6 + 3 * index:9 + 3 * index].copy()
                  for index, joint in enumerate(joint_order)}
        return replace(
            self,
            root_translation_model_m=value[:3].copy(),
            root_rotation_model_rotvec=value[3:6].copy(),
            joint_rotvec=joints,
        )


class BodyModel:
    """Canonical tree and the sole path to segment/sensor/tag geometry."""

    def __init__(self, definition: Mapping[str, Any]):
        self.schema = str(definition["schema"])
        self.root_segment = str(definition["root_segment"])
        self.segments = tuple(str(value) for value in definition["segments"])
        self.joints = tuple(JointDefinition(
            str(row["id"]), str(row["parent"]), str(row["child"]), str(row["dof_family"]),
            str(row["parent_offset_slot"]), str(row["child_offset_slot"]), str(row["rest_rotation_slot"]),
        ) for row in definition["joints"])
        self.imus = tuple(SensorDefinition(
            str(row["id"]), str(row["segment"]), str(row["extrinsic_slot"]), str(row["clock_slot"]),
        ) for row in definition["imu_nodes"])
        self.tags = tuple(TagDefinition(str(row["id"]), str(row["segment"]), str(row["lever_slot"]))
                          for row in definition["uwb_tags"])
        self.anchors = tuple(AnchorDefinition(int(row["id"]), str(row["position_slot"]), str(row["delay_slot"]))
                             for row in definition["anchors"])
        self.derived_points = tuple(DerivedPointDefinition(str(row["id"]), str(row["segment"]), str(row["offset_slot"]))
                                    for row in definition["derived_anatomical_points"])
        mapping = definition["active_identity_mapping"]
        self.identity_mapping = {str(key): str(value) for key, value in mapping["mapping"].items()}
        self.identity_provenance = {
            "source": str(mapping["source"]),
            "source_sha256": str(mapping["source_sha256"]),
            "rule": str(mapping["rule"]),
        }
        self.conventions = dict(definition["conventions"])
        self.real_calibration_policy = dict(definition["real_calibration_policy"])
        self._validate_definition()

    @property
    def joint_ids(self) -> tuple[str, ...]:
        return tuple(joint.joint_id for joint in self.joints)

    @property
    def imu_ids(self) -> tuple[str, ...]:
        return tuple(sensor.sensor_id for sensor in self.imus)

    @property
    def tag_ids(self) -> tuple[str, ...]:
        return tuple(tag.tag_id for tag in self.tags)

    def _validate_definition(self) -> None:
        if len(self.segments) != 10 or len(set(self.segments)) != 10:
            raise ValueError("BodyModel must contain ten unique segments")
        if len(self.joints) != 9 or len({joint.joint_id for joint in self.joints}) != 9:
            raise ValueError("BodyModel must contain nine unique joints")
        if len(self.imus) != 10 or len(set(self.imu_ids)) != 10:
            raise ValueError("BodyModel must contain ten unique IMUs")
        if len(self.tags) != 10 or len(set(self.tag_ids)) != 10:
            raise ValueError("BodyModel must contain ten unique UWB tags")
        if len(self.anchors) != 8 or {anchor.anchor_id for anchor in self.anchors} != set(range(8)):
            raise ValueError("BodyModel must contain numerical anchors 0..7")
        children = {joint.child for joint in self.joints}
        if self.root_segment in children or children != set(self.segments) - {self.root_segment}:
            raise ValueError("joints do not form the required rooted segment inventory")
        if set(self.identity_mapping) != set(self.imu_ids) or set(self.identity_mapping.values()) != set(self.segments):
            raise ValueError("active identity mapping must be one-to-one over all segments")
        if any(self.identity_mapping[sensor.sensor_id] != sensor.segment for sensor in self.imus):
            raise ValueError("IMU placement conflicts with active physical identity mapping")
        if any(self.identity_mapping[tag.tag_id] != tag.segment for tag in self.tags):
            raise ValueError("tag placement conflicts with active physical identity mapping")
        reached = {self.root_segment}
        pending = list(self.joints)
        while pending:
            ready = [joint for joint in pending if joint.parent in reached]
            if not ready:
                raise ValueError("body graph is cyclic or disconnected")
            for joint in ready:
                reached.add(joint.child)
                pending.remove(joint)
        if reached != set(self.segments):
            raise ValueError("body graph is disconnected")

    def segment_poses(self, state: KeyframeState, calibration: StaticCalibration) -> dict[str, Pose]:
        state.validate(self)
        gauge = calibration.pose("world_model_gauge")
        root_model = Pose(so3_exp(np.asarray(state.root_rotation_model_rotvec, float)),
                          np.asarray(state.root_translation_model_m, float))
        poses = {self.root_segment: gauge.compose(root_model)}
        pending = list(self.joints)
        while pending:
            progress = False
            for joint in pending[:]:
                if joint.parent not in poses:
                    continue
                parent = poses[joint.parent]
                parent_offset = calibration.vector(joint.parent_offset_slot, 3)
                child_offset = calibration.vector(joint.child_offset_slot, 3)
                rest = so3_exp(calibration.vector(joint.rest_rotation_slot, 3))
                child_rotation = parent.rotation @ rest @ so3_exp(np.asarray(state.joint_rotvec[joint.joint_id], float))
                centre = parent.transform_point(parent_offset)
                child_translation = centre - child_rotation @ child_offset
                poses[joint.child] = Pose(child_rotation, child_translation)
                pending.remove(joint)
                progress = True
            if not progress:
                raise ValueError("body graph traversal did not close")
        return poses

    def joint_centres(self, state: KeyframeState, calibration: StaticCalibration) -> dict[str, np.ndarray]:
        poses = self.segment_poses(state, calibration)
        return {joint.joint_id: poses[joint.parent].transform_point(calibration.vector(joint.parent_offset_slot, 3))
                for joint in self.joints}

    def imu_frames(self, state: KeyframeState, calibration: StaticCalibration) -> dict[str, Pose]:
        poses = self.segment_poses(state, calibration)
        return {sensor.sensor_id: poses[sensor.segment].compose(calibration.pose(sensor.extrinsic_slot))
                for sensor in self.imus}

    def tag_phase_centres(self, state: KeyframeState, calibration: StaticCalibration) -> dict[str, np.ndarray]:
        poses = self.segment_poses(state, calibration)
        return {tag.tag_id: poses[tag.segment].transform_point(calibration.vector(tag.lever_slot, 3))
                for tag in self.tags}

    def anatomical_points(self, state: KeyframeState, calibration: StaticCalibration) -> dict[str, np.ndarray]:
        poses = self.segment_poses(state, calibration)
        return {point.point_id: poses[point.segment].transform_point(calibration.vector(point.offset_slot, 3))
                for point in self.derived_points}

    def kinematic_residuals(self, state: KeyframeState, calibration: StaticCalibration) -> np.ndarray:
        poses = self.segment_poses(state, calibration)
        rows = []
        for joint in self.joints:
            parent_point = poses[joint.parent].transform_point(calibration.vector(joint.parent_offset_slot, 3))
            child_point = poses[joint.child].transform_point(calibration.vector(joint.child_offset_slot, 3))
            rows.append(parent_point - child_point)
        return np.concatenate(rows)

    def all_predictions(self, state: KeyframeState, calibration: StaticCalibration) -> dict[str, Any]:
        return {
            "segments": self.segment_poses(state, calibration),
            "joints": self.joint_centres(state, calibration),
            "imus": self.imu_frames(state, calibration),
            "tags": self.tag_phase_centres(state, calibration),
            "anatomical_points": self.anatomical_points(state, calibration),
            "kinematic_residuals": self.kinematic_residuals(state, calibration),
        }

    def dependency_blocks(self, target_kind: str, target_id: str, time_s: float) -> tuple[str, ...]:
        if target_kind in ("imu", "tag"):
            definitions = self.imus if target_kind == "imu" else self.tags
            segment = next(item.segment for item in definitions
                           if (item.sensor_id if target_kind == "imu" else item.tag_id) == target_id)
        elif target_kind == "segment":
            segment = target_id
        else:
            segment = next(point.segment for point in self.derived_points if point.point_id == target_id)
        path = []
        cursor = segment
        by_child = {joint.child: joint for joint in self.joints}
        while cursor != self.root_segment:
            joint = by_child[cursor]
            path.append(joint.joint_id)
            cursor = joint.parent
        stamp = f"{time_s:.9f}"
        blocks = [f"kf:{stamp}:root_pose"]
        blocks.extend(f"kf:{stamp}:joint:{joint}" for joint in reversed(path))
        if target_kind == "imu":
            blocks.append(f"calibration:imu_extrinsic:{target_id}")
        elif target_kind == "tag":
            blocks.append(f"calibration:tag_lever:{target_id}")
        return tuple(blocks)

    def point_jacobian(self, state: KeyframeState, calibration: StaticCalibration,
                       target_kind: str, target_id: str, step: float = 1e-6) -> np.ndarray:
        vector = state.configuration_vector(self.joint_ids)

        def evaluate(candidate: np.ndarray) -> np.ndarray:
            current = state.with_configuration(candidate, self.joint_ids)
            if target_kind == "tag":
                return self.tag_phase_centres(current, calibration)[target_id]
            if target_kind == "anatomical_point":
                return self.anatomical_points(current, calibration)[target_id]
            return self.segment_poses(current, calibration)[target_id].translation

        return central_jacobian(evaluate, vector, step)

    def schema_summary(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "segments": list(self.segments),
            "joints": [joint.__dict__ for joint in self.joints],
            "imus": [sensor.__dict__ for sensor in self.imus],
            "tags": [tag.__dict__ for tag in self.tags],
            "anchors": [anchor.__dict__ for anchor in self.anchors],
            "derived_anatomical_points": [point.__dict__ for point in self.derived_points],
            "active_identity_mapping": self.identity_mapping,
            "identity_provenance": self.identity_provenance,
            "state_contract": {
                "per_keyframe_dimension": 9 + 6 * len(self.joints) + 6 * len(self.imus),
                "root_pose": 6,
                "root_velocity": 3,
                "relative_joint_states": {joint.joint_id: 3 for joint in self.joints},
                "relative_joint_rates": {joint.joint_id: 3 for joint in self.joints},
                "independent_gyro_biases": {node: 3 for node in self.imu_ids},
                "independent_accel_biases": {node: 3 for node in self.imu_ids},
                "full_covariance": true_value(),
            },
            "geometry_path": "T_world_model * T_model_root(t) * FK_i(joint_state, subject_geometry)",
            "ik_contract": "MAP inverse over this BodyModel and the same factors; never downstream cleanup",
        }


def true_value() -> bool:
    """Keep JSON-facing booleans explicit without magic string values."""
    return True


def load_body_model(path: Path) -> BodyModel:
    return BodyModel(json.loads(Path(path).read_text(encoding="utf-8")))


def _covariance(dimension: int, sigma: float) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(sigma * sigma if row == column else 0.0) for column in range(dimension))
                 for row in range(dimension))


def _known(slot_id: str, kind: str, owner: str, value: Sequence[float], sigma: float,
           provenance: str = "DETERMINISTIC_SYNTHETIC_SANDBOX") -> CalibrationSlot:
    vector = tuple(float(item) for item in value)
    return CalibrationSlot(slot_id, kind, owner, CalibrationStatus.KNOWN_SYNTHETIC,
                           vector, _covariance(len(vector), sigma), provenance)


def synthetic_calibration(model: BodyModel) -> StaticCalibration:
    """Known, nonzero sandbox calibration; never usable as subject calibration."""
    slots: dict[str, CalibrationSlot] = {}
    slots["world_model_gauge"] = _known("world_model_gauge", "static_world_model_gauge", "whole_body",
                                                   (0.02, -0.01, 0.18, 0.40, -0.25, 0.10), 1e-5)
    joint_geometry = {
        "pelvis_torso": ((0.0, 0.0, 0.10), (0.0, 0.0, -0.20)),
        "shoulder_left": ((-0.19, 0.01, 0.23), (0.0, 0.0, 0.0)),
        "elbow_left": ((0.0, 0.0, -0.31), (0.0, 0.0, 0.0)),
        "shoulder_right": ((0.19, -0.01, 0.22), (0.0, 0.0, 0.0)),
        "elbow_right": ((0.0, 0.0, -0.305), (0.0, 0.0, 0.0)),
        "hip_left": ((-0.095, 0.015, -0.055), (0.0, 0.0, 0.0)),
        "knee_left": ((0.0, 0.0, -0.435), (0.0, 0.0, 0.0)),
        "hip_right": ((0.097, -0.012, -0.052), (0.0, 0.0, 0.0)),
        "knee_right": ((0.0, 0.0, -0.428), (0.0, 0.0, 0.0)),
    }
    rest = {
        joint.joint_id: (0.002 * (index + 1), -0.001 * (index + 1), 0.0005 * (index + 1))
        for index, joint in enumerate(model.joints)
    }
    for joint in model.joints:
        parent, child = joint_geometry[joint.joint_id]
        slots[joint.parent_offset_slot] = _known(joint.parent_offset_slot, "local_joint_centre", joint.parent, parent, 1e-5)
        slots[joint.child_offset_slot] = _known(joint.child_offset_slot, "local_joint_centre", joint.child, child, 1e-5)
        slots[joint.rest_rotation_slot] = _known(joint.rest_rotation_slot, "joint_rest_rotation", joint.joint_id, rest[joint.joint_id], 1e-5)
    lengths = {
        "upper_arm_left": 0.31, "forearm_left": 0.265,
        "upper_arm_right": 0.305, "forearm_right": 0.258,
        "thigh_left": 0.435, "shank_left": 0.425,
        "thigh_right": 0.428, "shank_right": 0.419,
    }
    for segment, length in lengths.items():
        slots[f"bone_length:{segment}"] = _known(f"bone_length:{segment}", "subject_bone_length", segment, (length,), 1e-5)
    for index, sensor in enumerate(model.imus):
        sign = -1.0 if index % 2 else 1.0
        value = (0.006 + index * 0.0003, sign * (0.004 + index * 0.0002), 0.003,
                 0.012 + index * 0.0005, sign * 0.009, 0.018 + index * 0.0004)
        slots[sensor.extrinsic_slot] = _known(sensor.extrinsic_slot, "imu_to_segment_extrinsic", sensor.sensor_id, value, 2e-5)
        slots[sensor.clock_slot] = _known(sensor.clock_slot, "uwb_imu_time_relationship", sensor.sensor_id,
                                          (2.0e-5 * (index + 1), 1.0 + 1.0e-6 * (index - 4)), 1e-7)
    for index, tag in enumerate(model.tags):
        sign = -1.0 if index % 2 else 1.0
        value = (0.021 + 0.001 * index, sign * (0.012 + 0.0005 * index), 0.016 + 0.0007 * index)
        slots[tag.lever_slot] = _known(tag.lever_slot, "uwb_phase_centre_to_segment", tag.tag_id, value, 2e-5)
    point_values = {
        "wrist_left": (0.0, 0.0, -lengths["forearm_left"]),
        "wrist_right": (0.0, 0.0, -lengths["forearm_right"]),
        "ankle_left": (0.0, 0.0, -lengths["shank_left"]),
        "ankle_right": (0.0, 0.0, -lengths["shank_right"]),
        "torso_top": (0.0, 0.0, 0.26),
    }
    for point in model.derived_points:
        slots[point.offset_slot] = _known(point.offset_slot, "local_anatomical_point", point.segment,
                                          point_values[point.point_id], 1e-5)
    anchor_positions = (
        (-2.0, -1.7, 0.25), (2.2, -1.6, 0.35), (2.1, 1.8, 0.30), (-2.1, 1.9, 0.40),
        (-1.8, -1.5, 2.35), (2.0, -1.4, 2.25), (2.2, 1.7, 2.40), (-2.0, 1.8, 2.30),
    )
    for anchor, position in zip(model.anchors, anchor_positions):
        slots[anchor.position_slot] = _known(anchor.position_slot, "anchor_position", str(anchor.anchor_id), position, 1e-5)
        slots[anchor.delay_slot] = _known(anchor.delay_slot, "verified_anchor_delay", str(anchor.anchor_id),
                                          (0.010 + 0.001 * anchor.anchor_id,), 1e-5)
    return StaticCalibration(slots)


def frozen_uncertain_calibration(model: BodyModel) -> StaticCalibration:
    """Expand every real-data slot as unresolved; no C1 value is inserted."""
    slots: dict[str, CalibrationSlot] = {}

    def add(slot_id: str, kind: str, owner: str, dimension: int, sigma: float) -> None:
        slots[slot_id] = CalibrationSlot(
            slot_id, kind, owner, CalibrationStatus.FROZEN_UNCERTAIN, None,
            _covariance(dimension, sigma), "UNRESOLVED_REAL_CALIBRATION_NOT_FITTED_FROM_C1",
        )

    add("world_model_gauge", "static_world_model_gauge", "whole_body", 6, 10.0)
    for joint in model.joints:
        add(joint.parent_offset_slot, "local_joint_centre", joint.parent, 3, 0.20)
        add(joint.child_offset_slot, "local_joint_centre", joint.child, 3, 0.20)
        add(joint.rest_rotation_slot, "joint_rest_rotation", joint.joint_id, 3, np.pi)
    for segment in model.segments:
        if segment not in ("pelvis", "torso"):
            add(f"bone_length:{segment}", "subject_bone_length", segment, 1, 0.30)
    for sensor in model.imus:
        add(sensor.extrinsic_slot, "imu_to_segment_extrinsic", sensor.sensor_id, 6, np.pi)
        add(sensor.clock_slot, "uwb_imu_time_relationship", sensor.sensor_id, 2, 0.10)
    for tag in model.tags:
        add(tag.lever_slot, "uwb_phase_centre_to_segment", tag.tag_id, 3, 0.20)
    for point in model.derived_points:
        add(point.offset_slot, "local_anatomical_point", point.segment, 3, 0.30)
    for anchor in model.anchors:
        add(anchor.position_slot, "anchor_position", str(anchor.anchor_id), 3, 5.0)
        add(anchor.delay_slot, "verified_anchor_delay", str(anchor.anchor_id), 1, 1.0)
    return StaticCalibration(slots)


def empty_state(model: BodyModel, time_s: float) -> KeyframeState:
    joints = {joint: np.zeros(3) for joint in model.joint_ids}
    nodes = {node: np.zeros(3) for node in model.imu_ids}
    dimension = 9 + 6 * len(model.joint_ids) + 6 * len(model.imu_ids)
    state = KeyframeState(
        float(time_s), np.zeros(3), np.zeros(3), np.zeros(3), joints,
        {key: value.copy() for key, value in joints.items()},
        {key: value.copy() for key, value in nodes.items()},
        {key: value.copy() for key, value in nodes.items()},
        np.eye(dimension) * 1e-3,
    )
    state.validate(model)
    return state
