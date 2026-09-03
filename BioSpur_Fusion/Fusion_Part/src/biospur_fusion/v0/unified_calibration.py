"""Unified capture-wide pure-IMU calibration objective.

The implementation extends the observation-backed D0B-R1 scaffold to the
required 55-coordinate publishable state.  All action factors share one state;
PCA is used only for initialization.  Bounds and parameter-only protocol
priors are kept separate from measurement-only observability accounting.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.optimize import differential_evolution, least_squares, minimize_scalar
from scipy.sparse import csr_matrix
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_revision_d.d0b_r1_generator import ACTIONS, SEGMENTS
from biospur_fusion.imu_multi_action_revision_d.d0b_r1_model import (
    JOINTS,
    R1Observation,
    ResidualBlock,
    STATIC_ACTIONS,
    angles_from_axis,
    articulated_pose_directions,
    axis_from_angles,
    s2_residual,
    unit,
    yaw,
)


FUNCTIONAL_JOINTS = tuple(JOINTS)
ZERO_JOINTS = (
    "elbow_L",
    "elbow_R",
    "hip_L",
    "hip_R",
    "knee_L",
    "knee_R",
    "trunk",
)
PRODUCT_DIMENSION = 55
POSE_NUISANCE_DIMENSION = 34
B5_JOINT_EDGES = {
    "trunk": ("pelvis", "torso"),
    **JOINTS,
}
B5_LEVER_ENDPOINTS = tuple(
    (joint, segment)
    for joint, edge in B5_JOINT_EDGES.items()
    for segment in edge
)
LEVER_ARM_NUISANCE_DIMENSION = 3 * len(B5_LEVER_ENDPOINTS)
NUISANCE_DIMENSION = POSE_NUISANCE_DIMENSION + LEVER_ARM_NUISANCE_DIMENSION
FULL_DIMENSION = PRODUCT_DIMENSION + NUISANCE_DIMENSION
S2_LATITUDE_LIMIT = math.pi / 2.0 - 1e-6
B3_HIP_CIRCUMDUCTION = "b3_bilateral_hip_circumduction_two_axis"
B3_KNEE_LEFT = "b3_left_seated_knee_flexion_tibial_axial"
B3_KNEE_RIGHT = "b3_right_seated_knee_flexion_tibial_axial"
B3_TRUNK_LATERAL = "b3_trunk_labelled_left_right_lateral_bend"
B4_EN_BLOC = "b4_supported_braced_en_bloc_two_axis"
B5_ACTION_JOINTS = {
    "arms": ("shoulder_L", "shoulder_R", "elbow_L", "elbow_R"),
    "left_elbow": ("elbow_L",),
    "right_elbow_attempt2": ("elbow_R",),
    "left_knee": ("hip_L",),
    "right_knee": ("hip_R",),
    "left_heel": ("knee_L",),
    "right_heel": ("knee_R",),
    "squats": ("hip_L", "hip_R", "knee_L", "knee_R"),
    B3_HIP_CIRCUMDUCTION: ("hip_L", "hip_R"),
    B3_KNEE_LEFT: ("knee_L",),
    B3_KNEE_RIGHT: ("knee_R",),
    "trunk": ("trunk",),
    B3_TRUNK_LATERAL: ("trunk",),
}


def product_layout() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    cursor = 0
    for segment in SEGMENTS:
        entries.append({"name": f"sensor_axis:{segment}", "block": "SENSOR_LONGITUDINAL_AXIS", "start": cursor, "stop": cursor + 2})
        cursor += 2
    for segment in SEGMENTS[1:]:
        entries.append({"name": f"effective_heading:{segment}", "block": "EFFECTIVE_RELATIVE_HEADING", "start": cursor, "stop": cursor + 1})
        cursor += 1
    for joint in FUNCTIONAL_JOINTS:
        entries.append({"name": f"functional_axis:{joint}", "block": "LIMB_FUNCTIONAL_AXIS", "start": cursor, "stop": cursor + 2})
        cursor += 2
    entries.append({"name": "trunk_functional_frame", "block": "TRUNK_FUNCTIONAL_FRAME", "start": cursor, "stop": cursor + 3})
    cursor += 3
    for joint in ZERO_JOINTS:
        entries.append({"name": f"joint_zero:{joint}", "block": "JOINT_NEUTRAL_ZERO", "start": cursor, "stop": cursor + 1})
        cursor += 1
    assert cursor == PRODUCT_DIMENSION
    return entries


PRODUCT_LAYOUT = product_layout()


def decode_product(x: np.ndarray) -> dict[str, Any]:
    x = np.asarray(x, dtype=float)
    if x.shape[0] < PRODUCT_DIMENSION:
        raise ValueError("product coordinate vector is too short")
    cursor = 0
    axes: dict[str, np.ndarray] = {}
    for segment in SEGMENTS:
        axes[segment] = axis_from_angles(float(x[cursor]), float(x[cursor + 1]))
        cursor += 2
    headings = {"pelvis": 0.0}
    for segment in SEGMENTS[1:]:
        headings[segment] = float(x[cursor])
        cursor += 1
    functional: dict[str, np.ndarray] = {}
    for joint in FUNCTIONAL_JOINTS:
        functional[joint] = axis_from_angles(float(x[cursor]), float(x[cursor + 1]))
        cursor += 2
    trunk_frame = Rotation.from_rotvec(x[cursor:cursor + 3]).as_matrix()
    cursor += 3
    zeros: dict[str, float] = {}
    for joint in ZERO_JOINTS:
        zeros[joint] = float(x[cursor])
        cursor += 1
    assert cursor == PRODUCT_DIMENSION
    return {
        "axes": axes,
        "headings": headings,
        "functional": functional,
        "trunk_frame": trunk_frame,
        "zeros": zeros,
    }


def decode_full(x: np.ndarray) -> tuple[dict[str, Any], dict[str, Any]]:
    x = np.asarray(x, dtype=float)
    if x.shape != (FULL_DIMENSION,):
        raise ValueError(f"expected {FULL_DIMENSION} coordinates")
    nuisance: dict[str, Any] = {
        "initial_still_attempt2": x[PRODUCT_DIMENSION:PRODUCT_DIMENSION + 17],
        "t_pose": x[PRODUCT_DIMENSION + 17:PRODUCT_DIMENSION + 34],
    }
    cursor = PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION
    nuisance["lever_arms"] = {}
    for joint, segment in B5_LEVER_ENDPOINTS:
        nuisance["lever_arms"][(joint, segment)] = x[cursor:cursor + 3]
        cursor += 3
    assert cursor == FULL_DIMENSION
    return decode_product(x), nuisance


def wrap_angle(value: np.ndarray) -> np.ndarray:
    return (np.asarray(value, dtype=float) + math.pi) % (2.0 * math.pi) - math.pi


class UnifiedCalibrationObjective:
    """One objective over every complete calibration action."""

    def __init__(self, observation: R1Observation, contract: Mapping[str, Any]):
        self.obs = observation
        self.contract = contract
        node_index = {node: index for index, node in enumerate(observation.node_order)}
        self.segment_node = {segment: node for node, segment in observation.node_to_segment.items()}
        self.segment_index = {segment: node_index[node] for segment, node in self.segment_node.items()}

    def _rows(self, action: str, segments: tuple[str, ...], *, static: bool = False) -> np.ndarray:
        item = self.obs.r3d_actions[action]
        if static:
            source = np.asarray(item["STATIC_PLATEAU_CANDIDATE"]["row_indices"], dtype=int)
            maximum = int(self.contract["row_selection"]["maximum_static_rows_per_segment"])
        else:
            source = np.asarray(item["BROAD_ACTIVE_ROWS"], dtype=int)
            maximum = int(self.contract["row_selection"]["maximum_dynamic_rows_per_factor"])
        keep = np.ones(len(source), dtype=bool)
        for segment in segments:
            keep &= self.obs.valid[source, self.segment_index[segment]]
        source = source[keep]
        if len(source) > maximum:
            selected = np.unique(np.rint(np.linspace(0, len(source) - 1, maximum)).astype(int))
            source = source[selected]
        return source

    def corrected_direction(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        rotation = self.corrected_rotation(product, segment, rows)
        return np.einsum("nij,j->ni", rotation, product["axes"][segment])

    def corrected_rotation(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        index = self.segment_index[segment]
        return np.einsum("ij,njk->nik", yaw(product["headings"][segment]), self.obs.rotation[rows, index])

    def corrected_omega(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        index = self.segment_index[segment]
        rotation = self.corrected_rotation(product, segment, rows)
        return np.einsum("nij,nj->ni", rotation, self.obs.gyro_rad_s[rows, index])

    def corrected_specific_force(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        if self.obs.accel_mps2 is None:
            raise ValueError("B5 joint-center closure requires synchronized accelerometer observations")
        index = self.segment_index[segment]
        rotation = self.corrected_rotation(product, segment, rows)
        return np.einsum("nij,nj->ni", rotation, self.obs.accel_mps2[rows, index])

    def corrected_alpha(self, product: Mapping[str, Any], segment: str, rows: np.ndarray) -> np.ndarray:
        """Differentiate measured body-frame gyro, then rotate to navigation.

        For a rigid board ``d(R*omega_B)/dt = R*d(omega_B)/dt`` because the
        omitted cross term is ``omega x omega = 0``.  Central differences use
        the actual common-time stamps and never bridge the selected action's
        outer rows.
        """

        index = self.segment_index[segment]
        rows = np.asarray(rows, dtype=int)
        previous = rows - 1
        following = rows + 1
        dt = (self.obs.time_ns[following] - self.obs.time_ns[previous]).astype(float) / 1e9
        body_alpha = (
            self.obs.gyro_rad_s[following, index] - self.obs.gyro_rad_s[previous, index]
        ) / dt[:, None]
        return np.einsum("nij,nj->ni", self.corrected_rotation(product, segment, rows), body_alpha)

    def relative_omega(self, product: Mapping[str, Any], parent: str, child: str, rows: np.ndarray) -> np.ndarray:
        return self.corrected_omega(product, child, rows) - self.corrected_omega(product, parent, rows)

    def _b5_joint_center_specific_force_blocks(
        self,
        product: Mapping[str, Any],
        nuisance: Mapping[str, Any],
    ) -> list[ResidualBlock]:
        """Time-resolved parent/child acceleration equality at a shared joint.

        Accelerometers measure ``a_sensor - g``.  Both endpoints are
        transported to the same joint center, so the common gravity vector
        cancels in their difference.  Lever arms are capture-shared nuisance
        coordinates; no lever prior or bound appears in these values.
        """

        if self.obs.accel_mps2 is None:
            return []
        covariance = self.contract["measurement_covariance"]
        accel_sigma = float(covariance["b5_accelerometer_sigma_mps2"])
        alpha_sigma = float(covariance["b5_gyro_differentiation_sigma_rad_s2"])
        timing_sigma = float(covariance["b5_timing_sigma_s"])
        jerk_scale = float(covariance["b5_timing_jerk_scale_mps3"])
        lever_covariance_scale = float(covariance["b5_lever_covariance_scale_m"])
        model_sigma = float(covariance["b5_joint_center_model_sigma_mps2"])
        sigma = math.sqrt(
            2.0 * accel_sigma * accel_sigma
            + 2.0 * (lever_covariance_scale * alpha_sigma) ** 2
            + 2.0 * (timing_sigma * jerk_scale) ** 2
            + model_sigma * model_sigma
        )
        activation = float(covariance["b5_dynamic_activation_mps2"])
        blocks: list[ResidualBlock] = []

        for action, joints in B5_ACTION_JOINTS.items():
            for joint in joints:
                parent, child = B5_JOINT_EDGES[joint]
                rows = self._rows(action, (parent, child))
                rows = rows[(rows > 0) & (rows + 1 < len(self.obs.time_ns))]
                if not len(rows):
                    continue
                parent_force = self.corrected_specific_force(product, parent, rows)
                child_force = self.corrected_specific_force(product, child, rows)
                parent_omega = self.corrected_omega(product, parent, rows)
                child_omega = self.corrected_omega(product, child, rows)
                parent_alpha = self.corrected_alpha(product, parent, rows)
                child_alpha = self.corrected_alpha(product, child, rows)
                parent_rotation = self.corrected_rotation(product, parent, rows)
                child_rotation = self.corrected_rotation(product, child, rows)
                parent_lever = np.einsum(
                    "nij,j->ni", parent_rotation, nuisance["lever_arms"][(joint, parent)],
                )
                child_lever = np.einsum(
                    "nij,j->ni", child_rotation, nuisance["lever_arms"][(joint, child)],
                )
                parent_joint = (
                    parent_force
                    + np.cross(parent_alpha, parent_lever)
                    + np.cross(parent_omega, np.cross(parent_omega, parent_lever))
                )
                child_joint = (
                    child_force
                    + np.cross(child_alpha, child_lever)
                    + np.cross(child_omega, np.cross(child_omega, child_lever))
                )

                # Candidate-invariant activity prevents gravity/static rows or
                # differentiated near-zero gyro noise from manufacturing rank.
                parent_index = self.segment_index[parent]
                child_index = self.segment_index[child]
                raw_rate = np.maximum(
                    np.linalg.norm(self.obs.gyro_rad_s[rows, parent_index], axis=1),
                    np.linalg.norm(self.obs.gyro_rad_s[rows, child_index], axis=1),
                )
                raw_alpha_parent = np.linalg.norm(parent_alpha, axis=1)
                raw_alpha_child = np.linalg.norm(child_alpha, axis=1)
                excitation = lever_covariance_scale * (
                    np.maximum(raw_alpha_parent, raw_alpha_child) + raw_rate * raw_rate
                )
                weight = np.minimum(1.0, (excitation / max(activation, 1e-12)) ** 2)
                values = (
                    weight[:, None] * (parent_joint - child_joint)
                    / sigma / math.sqrt(len(rows))
                ).ravel()
                blocks.append(ResidualBlock(
                    action,
                    f"b5_joint_center_specific_force_closure:{joint}",
                    "MEASURED_OBSERVATION",
                    values,
                    rows,
                    (self.segment_node[parent], self.segment_node[child]),
                    "m/s^2",
                    "w*(R_p f_p + alpha_p x R_p r_p + omega_p x (omega_p x R_p r_p) - R_c f_c - alpha_c x R_c r_c - omega_c x (omega_c x R_c r_c))/sigma",
                    "two accelerometers, differentiated two-node gyro, common-time timing covariance, joint-center model covariance; action-balanced robust solve",
                    ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "B5_SENSOR_TO_JOINT_LEVER_ARM_NUISANCE"),
                ))
        return blocks

    def functional_axis_world(self, product: Mapping[str, Any], joint: str, rows: np.ndarray) -> np.ndarray:
        parent, _ = JOINTS[joint]
        return np.einsum(
            "nij,j->ni",
            self.corrected_rotation(product, parent, rows),
            product["functional"][joint],
        )

    def static_blocks(self, product: Mapping[str, Any], nuisance: Mapping[str, np.ndarray]) -> list[ResidualBlock]:
        sigma = math.radians(float(self.contract["measurement_covariance"]["static_direction_sigma_deg"]))
        blocks: list[ResidualBlock] = []
        for action in STATIC_ACTIONS:
            predicted = articulated_pose_directions(action, nuisance[action])
            for segment in SEGMENTS:
                rows = self._rows(action, (segment,), static=True)
                if not len(rows):
                    continue
                observed = self.corrected_direction(product, segment, rows)
                values = (s2_residual(np.tile(predicted[segment], (len(rows), 1)), observed) / sigma / math.sqrt(len(rows))).ravel()
                blocks.append(ResidualBlock(
                    action,
                    f"articulated_static_direction:{segment}",
                    "PROTOCOL_CONDITIONED_MEASUREMENT",
                    values,
                    rows,
                    (self.segment_node[segment],),
                    "unit_direction/rad",
                    "LogS2(d_articulated(q_pose), Rz(h_i) R_Ni_Bi a_Bi)",
                    "measured rest covariance plus soft human-pose mismatch",
                    ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", f"ARTICULATED_POSE_{action}"),
                ))
        return blocks

    def _axis_block(self, action: str, joint: str, rows: np.ndarray, product: Mapping[str, Any], sigma: float) -> ResidualBlock | None:
        if not len(rows):
            return None
        parent, child = JOINTS[joint]
        relative = self.relative_omega(product, parent, child, rows)
        axis_world = self.functional_axis_world(product, joint, rows)
        values = (np.cross(relative, axis_world) / sigma / math.sqrt(len(rows))).ravel()
        return ResidualBlock(
            action,
            f"time_resolved_functional_axis:{joint}",
            "MEASURED_OBSERVATION",
            values,
            rows,
            (self.segment_node[parent], self.segment_node[child]),
            "rad/s",
            "cross(omega_child-omega_parent, R_parent_board_to_nav shared_axis_parent_board)/sigma",
            "two-node gyro covariance plus robust off-axis motion scale",
            ("EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS"),
        )

    def _axis_direction_geometry_block(
        self,
        action: str,
        joint: str,
        rows: np.ndarray,
        product: Mapping[str, Any],
    ) -> ResidualBlock | None:
        """Bind a measured functional axis to moving segment directions.

        A hinge/functional rotation axis is perpendicular to the longitudinal
        directions it rotates.  Evaluating this at every active row supplies
        mounting/heading information that relative-rate collinearity alone
        cannot provide.
        """

        if not len(rows):
            return None
        parent, child = JOINTS[joint]
        parent_direction = self.corrected_direction(product, parent, rows)
        child_direction = self.corrected_direction(product, child, rows)
        axis = self.functional_axis_world(product, joint, rows)
        sigma = math.radians(float(self.contract["measurement_covariance"]["functional_axis_geometry_sigma_deg"]))
        parent_error = np.arcsin(np.clip(np.einsum("ni,ni->n", parent_direction, axis), -1.0, 1.0))
        child_error = np.arcsin(np.clip(np.einsum("ni,ni->n", child_direction, axis), -1.0, 1.0))
        values = np.concatenate((parent_error, child_error)) / sigma / math.sqrt(2 * len(rows))
        return ResidualBlock(
            action,
            f"time_resolved_axis_direction_geometry:{joint}",
            "MEASURED_OBSERVATION",
            values,
            rows,
            (self.segment_node[parent], self.segment_node[child]),
            "rad",
            "asin(f dot d_parent), asin(f dot d_child) over active rows",
            "measured direction covariance plus robust functional-axis geometry mismatch",
            ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS"),
        )

    def _bilateral_motion_direction_block(
        self,
        action: str,
        first_joint: str,
        second_joint: str,
        relation: str,
        product: Mapping[str, Any],
    ) -> ResidualBlock | None:
        """Use simultaneous mirrored motion without forcing equal amplitudes.

        The calibrated world-frame relative angular-velocity directions use
        the declared bilateral relation for that joint family: opposition for
        the shoulder/hip protocol and sagittal reflection for mirrored knee
        axes.  Each row is weighted by the weaker measured excitation, so
        rest/turnaround samples contribute no artificial direction.  Magnitude
        equality is never used.
        """

        first_parent, first_child = JOINTS[first_joint]
        second_parent, second_child = JOINTS[second_joint]
        segments = tuple(dict.fromkeys((first_parent, first_child, second_parent, second_child)))
        rows = self._rows(action, segments)
        if not len(rows):
            return None
        first = self.relative_omega(product, first_parent, first_child, rows)
        second = self.relative_omega(product, second_parent, second_child, rows)
        first_norm = np.linalg.norm(first, axis=1)
        second_norm = np.linalg.norm(second, axis=1)
        activation = float(self.contract["measurement_covariance"]["bilateral_activation_scale_rad_s"])
        # Excitation weights must not depend on candidate headings.  The
        # absolute difference of parent/child gyro magnitudes is invariant to
        # every orientation coordinate and is a conservative measured proxy
        # for joint-relative activity.
        def activity(parent: str, child: str) -> np.ndarray:
            parent_gyro = self.obs.gyro_rad_s[rows, self.segment_index[parent]]
            child_gyro = self.obs.gyro_rad_s[rows, self.segment_index[child]]
            return np.abs(np.linalg.norm(child_gyro, axis=1) - np.linalg.norm(parent_gyro, axis=1))

        weaker = np.minimum(
            activity(first_parent, first_child),
            activity(second_parent, second_child),
        )
        # Quadratic attenuation prevents direction-normalized gyro noise near
        # a reversal/rest row from becoming a synthetic bilateral observation.
        weight = weaker * weaker / (weaker * weaker + activation * activation)
        first_unit = np.divide(first, first_norm[:, None], out=np.zeros_like(first), where=first_norm[:, None] > 1e-12)
        second_unit = np.divide(second, second_norm[:, None], out=np.zeros_like(second), where=second_norm[:, None] > 1e-12)
        if relation == "OPPOSED":
            expected_second = -first_unit
            parameter_blocks = ("EFFECTIVE_RELATIVE_HEADING", "BILATERAL_STRUCTURE")
            equation = "excitation_weight*LogS2(-unit(relative_gyro_left), unit(relative_gyro_right))/sigma"
        elif relation == "SAGITTAL_REFLECTION":
            tpose_rows = self._rows("t_pose", tuple(SEGMENTS), static=True)
            tpose_directions = {
                segment: unit(np.median(self.corrected_direction(product, segment, tpose_rows), axis=0))
                for segment in SEGMENTS
            }
            lateral = unit(tpose_directions["upper_arm_L"] - tpose_directions["upper_arm_R"])
            expected_second = first_unit - 2.0 * np.einsum("ni,i->n", first_unit, lateral)[:, None] * lateral
            parameter_blocks = ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "BILATERAL_STRUCTURE")
            equation = "excitation_weight*LogS2(reflect_sagittal(unit(relative_gyro_left)), unit(relative_gyro_right))/sigma"
        else:
            raise ValueError(f"unsupported bilateral relation {relation}")
        sigma = math.radians(float(self.contract["measurement_covariance"]["bilateral_direction_sigma_deg"]))
        values = (
            s2_residual(expected_second, second_unit)
            * weight[:, None]
            / sigma
            / math.sqrt(len(rows))
        ).ravel()
        return ResidualBlock(
            action,
            f"time_resolved_bilateral_{relation.lower()}:{first_joint}:{second_joint}",
            "MEASURED_OBSERVATION",
            values,
            rows,
            tuple(self.segment_node[segment] for segment in segments),
            "unit_direction/rad",
            equation,
            "separate left/right gyro covariance plus broad mirrored-direction mismatch; amplitudes remain independent",
            parameter_blocks,
        )

    def _b3_hip_circumduction_blocks(self, product: Mapping[str, Any]) -> list[ResidualBlock]:
        """Measured two-axis hip motion, distinct from an unlabeled pelvis hula."""

        sigma = float(self.contract["measurement_covariance"]["hip_circumduction_plane_sigma_rad_s"])
        blocks: list[ResidualBlock] = []
        for joint in ("hip_L", "hip_R"):
            parent, child = JOINTS[joint]
            rows = self._rows(B3_HIP_CIRCUMDUCTION, (parent, child))
            if not len(rows):
                continue
            relative = self.relative_omega(product, parent, child, rows)
            pelvis_longitudinal = self.corrected_direction(product, "pelvis", rows)
            values = (
                np.einsum("ni,ni->n", relative, pelvis_longitudinal)
                / sigma
                / math.sqrt(len(rows))
            )
            blocks.append(ResidualBlock(
                B3_HIP_CIRCUMDUCTION,
                f"time_resolved_two_axis_hip_circumduction_plane:{joint}",
                "MEASURED_OBSERVATION",
                values,
                rows,
                (self.segment_node[parent], self.segment_node[child]),
                "rad/s",
                "dot(omega_thigh-omega_pelvis, measured_pelvis_longitudinal)/sigma",
                "two-node gyro covariance plus broad measured circumduction-plane mismatch",
                ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "HIP_CIRCUMDUCTION_PLANE"),
            ))
        bilateral = self._bilateral_motion_direction_block(
            B3_HIP_CIRCUMDUCTION,
            "hip_L",
            "hip_R",
            "SAGITTAL_REFLECTION",
            product,
        )
        if bilateral is not None:
            blocks.append(bilateral)
        return blocks

    def _b3_knee_flexion_axial_blocks(self, product: Mapping[str, Any]) -> list[ResidualBlock]:
        """Stationary-thigh knee flexion followed by tibial axial rotation."""

        sigma = float(self.contract["measurement_covariance"]["knee_flexion_axial_subspace_sigma_rad_s"])
        blocks: list[ResidualBlock] = []
        for action, joint in ((B3_KNEE_LEFT, "knee_L"), (B3_KNEE_RIGHT, "knee_R")):
            parent, child = JOINTS[joint]
            rows = self._rows(action, (parent, child))
            if not len(rows):
                continue
            midpoint = self.obs.windows[action][0] + (self.obs.windows[action][1] - self.obs.windows[action][0]) // 2
            for phase, selected in (
                ("flexion", rows[self.obs.time_ns[rows] <= midpoint]),
                ("tibial_axial", rows[self.obs.time_ns[rows] > midpoint]),
            ):
                if not len(selected):
                    continue
                relative = self.relative_omega(product, parent, child, selected)
                axis = (
                    self.functional_axis_world(product, joint, selected)
                    if phase == "flexion"
                    else self.corrected_direction(product, child, selected)
                )
                values = (np.cross(relative, axis) / sigma / math.sqrt(len(selected))).ravel()
                blocks.append(ResidualBlock(
                    action,
                    f"b3_{phase}_noncollinear_axis:{joint}",
                    "MEASURED_OBSERVATION",
                    values,
                    selected,
                    (self.segment_node[parent], self.segment_node[child]),
                    "rad/s",
                    "cross(relative_gyro, phase_specific knee-functional or measured tibial-longitudinal axis)/sigma",
                    "stationary-thigh two-node gyro covariance; phase label required",
                    ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS"),
                ))
        return blocks

    def _b3_trunk_lateral_blocks(self, product: Mapping[str, Any]) -> list[ResidualBlock]:
        """Lateral-bend axis plus signed left/right excursion evidence."""

        rows = self._rows(B3_TRUNK_LATERAL, ("pelvis", "torso"))
        if not len(rows):
            return []
        relative = self.relative_omega(product, "pelvis", "torso", rows)
        axis = np.einsum(
            "nij,j->ni",
            self.corrected_rotation(product, "pelvis", rows),
            product["trunk_frame"][:, 1],
        )
        sigma = float(self.contract["measurement_covariance"]["trunk_motion_plane_sigma_rad_s"])
        cross_block = ResidualBlock(
            B3_TRUNK_LATERAL,
            "time_resolved_trunk_lateral_bend_axis",
            "MEASURED_OBSERVATION",
            (np.cross(relative, axis) / sigma / math.sqrt(len(rows))).ravel(),
            rows,
            (self.segment_node["pelvis"], self.segment_node["torso"]),
            "rad/s",
            "cross(omega_torso-omega_pelvis, pelvis-board-fixed lateral-bend frame axis)/sigma",
            "two-node gyro covariance plus broad lateral-bend mismatch",
            ("EFFECTIVE_RELATIVE_HEADING", "TRUNK_FUNCTIONAL_FRAME"),
        )

        start, stop = self.obs.windows[B3_TRUNK_LATERAL]
        fraction = (self.obs.time_ns[rows] - start) / max(stop - start, 1)
        sign = np.zeros(len(rows))
        sign[(fraction > 0.15) & (fraction < 0.30)] = 1.0
        sign[(fraction >= 0.30) & (fraction < 0.45)] = -1.0
        sign[(fraction > 0.55) & (fraction < 0.70)] = -1.0
        sign[(fraction >= 0.70) & (fraction < 0.85)] = 1.0
        keep = sign != 0.0
        selected = rows[keep]
        selected_relative = relative[keep]
        selected_axis = axis[keep]
        norms = np.linalg.norm(selected_relative, axis=1)
        unit_relative = np.divide(
            selected_relative,
            norms[:, None],
            out=np.zeros_like(selected_relative),
            where=norms[:, None] > 1e-12,
        )
        pelvis_gyro = self.obs.gyro_rad_s[selected, self.segment_index["pelvis"]]
        torso_gyro = self.obs.gyro_rad_s[selected, self.segment_index["torso"]]
        activity = np.abs(np.linalg.norm(torso_gyro, axis=1) - np.linalg.norm(pelvis_gyro, axis=1))
        activation = float(self.contract["measurement_covariance"]["bilateral_activation_scale_rad_s"])
        weight = activity * activity / (activity * activity + activation * activation)
        direction_sigma = math.radians(float(
            self.contract["measurement_covariance"]["trunk_labelled_direction_sigma_deg"]
        ))
        expected = sign[keep, None] * selected_axis
        # Use a signed chordal direction residual.  The S2 log map is
        # intentionally unsigned at its antipode, while 1-dot is only
        # second-order at the correct solution and therefore contributes no
        # local Jacobian information.  The vector difference is first-order at
        # alignment and remains nonzero for the product-distinct antipode.
        direction_values = (
            (expected - unit_relative)
            * weight[:, None]
            / direction_sigma
            / math.sqrt(len(selected))
        ).ravel()
        direction_block = ResidualBlock(
            B3_TRUNK_LATERAL,
            "time_resolved_labelled_left_right_trunk_lateral_direction",
            "PROTOCOL_CONDITIONED_MEASUREMENT",
            direction_values,
            selected,
            (self.segment_node["pelvis"], self.segment_node["torso"]),
            "unit_direction/rad",
            "signed chordal(label_sign*pelvis-board lateral axis, measured unit relative gyro) on outbound/return rows",
            "labelled left/right phase plus two-node gyro covariance; broad directional mismatch",
            ("EFFECTIVE_RELATIVE_HEADING", "TRUNK_FUNCTIONAL_FRAME", "LABELLED_LATERAL_BEND_SIGN"),
        )
        return [cross_block, direction_block]

    def _b4_en_bloc_common_rate_blocks(self, product: Mapping[str, Any]) -> list[ResidualBlock]:
        """Measurement-only common rigid-body rate relative to pelvis.

        No navigation direction, commanded angle, pose, or motion-plane axis
        appears in this residual.  Non-collinearity and rigidity are separate
        eligibility gates; the factor uses only simultaneous measured rates
        after each segment's candidate heading correction.
        """

        # B4 is a useful synthetic/laboratory diagnostic, but it is not a
        # product-calibration prerequisite.  Ordinary captures that do not
        # contain the optional episode must retain the rest of the objective
        # unchanged instead of failing during factor construction.
        if B4_EN_BLOC not in self.obs.r3d_actions:
            return []
        item = self.obs.r3d_actions[B4_EN_BLOC]
        maximum = int(self.contract["row_selection"]["maximum_dynamic_rows_per_factor"])
        sigma = float(self.contract["measurement_covariance"]["b4_common_rate_sigma_rad_s"])
        activation = float(self.contract["measurement_covariance"]["b4_common_rate_activation_rad_s"])
        blocks: list[ResidualBlock] = []
        for phase, phase_rows in item["B4_COMMON_RATE_PHASE_ROWS"].items():
            source = np.asarray(phase_rows, dtype=int)
            for segment in SEGMENTS[1:]:
                rows = source[
                    self.obs.valid[source, self.segment_index["pelvis"]]
                    & self.obs.valid[source, self.segment_index[segment]]
                ]
                if len(rows) > maximum:
                    selected = np.unique(np.rint(np.linspace(0, len(rows) - 1, maximum)).astype(int))
                    rows = rows[selected]
                if not len(rows):
                    continue
                pelvis = self.corrected_omega(product, "pelvis", rows)
                observed = self.corrected_omega(product, segment, rows)
                pelvis_raw = self.obs.gyro_rad_s[rows, self.segment_index["pelvis"]]
                segment_raw = self.obs.gyro_rad_s[rows, self.segment_index[segment]]
                common_activity = np.minimum(
                    np.linalg.norm(pelvis_raw, axis=1),
                    np.linalg.norm(segment_raw, axis=1),
                )
                weight = common_activity * common_activity / (
                    common_activity * common_activity + activation * activation
                )
                values = (
                    (observed - pelvis)
                    * weight[:, None]
                    / sigma
                    / math.sqrt(len(rows))
                ).ravel()
                blocks.append(ResidualBlock(
                    B4_EN_BLOC,
                    f"time_resolved_en_bloc_common_rate:{phase}:{segment}",
                    "MEASURED_OBSERVATION",
                    values,
                    rows,
                    (self.segment_node["pelvis"], self.segment_node[segment]),
                    "rad/s",
                    "activity_weight*(omega_segment_nav(candidate)-omega_pelvis_nav(candidate))/sigma",
                    "simultaneous two-node gyro covariance plus supported-rigidity mismatch",
                    ("EFFECTIVE_RELATIVE_HEADING", "WHOLE_CAR_RIGID_COMMON_RATE"),
                ))
        return blocks

    def dynamic_blocks(
        self,
        product: Mapping[str, Any],
        nuisance: Mapping[str, Any],
    ) -> list[ResidualBlock]:
        sigma = float(self.contract["measurement_covariance"]["dynamic_hinge_sigma_rad_s"])
        elbow_sigma = float(self.contract["measurement_covariance"]["elbow_curl_pronation_subspace_sigma_rad_s"])
        blocks: list[ResidualBlock] = []
        schedule = {
            "arms": ("shoulder_L", "shoulder_R", "elbow_L", "elbow_R"),
            "left_knee": ("hip_L",),
            "right_knee": ("hip_R",),
            "left_heel": ("knee_L",),
            "right_heel": ("knee_R",),
            "squats": ("hip_L", "hip_R", "knee_L", "knee_R"),
        }
        for action, joints in schedule.items():
            for joint in joints:
                parent, child = JOINTS[joint]
                block = self._axis_block(action, joint, self._rows(action, (parent, child)), product, sigma)
                if block is not None:
                    blocks.append(block)
                geometry = self._axis_direction_geometry_block(
                    action, joint, self._rows(action, (parent, child)), product,
                )
                if geometry is not None:
                    blocks.append(geometry)

        # These factors use synchronized, time-resolved measured directions.
        # They are neither endpoint closure nor a navigation-frame plane
        # template, and they do not force left/right amplitude equality.
        bilateral_pairs = {
            "arms": (("shoulder_L", "shoulder_R", "OPPOSED"),),
            "squats": (
                ("hip_L", "hip_R", "OPPOSED"),
                ("knee_L", "knee_R", "SAGITTAL_REFLECTION"),
            ),
        }
        for action, pairs in bilateral_pairs.items():
            for first_joint, second_joint, relation in pairs:
                bilateral = self._bilateral_motion_direction_block(
                    action, first_joint, second_joint, relation, product,
                )
                if bilateral is not None:
                    blocks.append(bilateral)

        blocks.extend(self._b3_hip_circumduction_blocks(product))
        blocks.extend(self._b3_knee_flexion_axial_blocks(product))
        blocks.extend(self._b3_trunk_lateral_blocks(product))
        blocks.extend(self._b4_en_bloc_common_rate_blocks(product))
        blocks.extend(self._b5_joint_center_specific_force_blocks(product, nuisance))

        # Compound elbow actions provide a second non-collinear direction: curl
        # around the functional axis, then pronation about the measured forearm
        # longitudinal direction.  Both are real time-resolved rows.
        for action, joint in (("left_elbow", "elbow_L"), ("right_elbow_attempt2", "elbow_R")):
            parent, child = JOINTS[joint]
            rows = self._rows(action, (parent, child))
            if not len(rows):
                continue
            midpoint = self.obs.windows[action][0] + (self.obs.windows[action][1] - self.obs.windows[action][0]) // 2
            for phase, selected in (("curl", rows[self.obs.time_ns[rows] <= midpoint]), ("pronation", rows[self.obs.time_ns[rows] > midpoint])):
                if not len(selected):
                    continue
                relative = self.relative_omega(product, parent, child, selected)
                if phase == "curl":
                    axis = self.functional_axis_world(product, joint, selected)
                else:
                    axis = self.corrected_direction(product, child, selected)
                values = (np.cross(relative, axis) / elbow_sigma / math.sqrt(len(selected))).ravel()
                blocks.append(ResidualBlock(
                    action,
                    f"{phase}_noncollinear_axis:{joint}",
                    "MEASURED_OBSERVATION",
                    values,
                    selected,
                    (self.segment_node[parent], self.segment_node[child]),
                    "rad/s",
                    "cross(relative_gyro, phase_specific_measured_axis)/sigma",
                    "phase-conditioned two-node gyro covariance",
                    ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS"),
                ))

        # Two labelled, non-collinear measured trunk modes identify a complete
        # right-handed frame.  One motion-plane normal would leave axial twist.
        rows = self._rows("trunk", ("pelvis", "torso"))
        if len(rows):
            start, stop = self.obs.windows["trunk"]
            cut1 = start + (stop - start) // 3
            cut2 = start + 2 * (stop - start) // 3
            phases = (
                ("axial_left", rows[self.obs.time_ns[rows] <= cut1], product["trunk_frame"][:, 2]),
                ("axial_right", rows[(self.obs.time_ns[rows] > cut1) & (self.obs.time_ns[rows] <= cut2)], product["trunk_frame"][:, 2]),
                ("flexion", rows[self.obs.time_ns[rows] > cut2], product["trunk_frame"][:, 0]),
            )
            trunk_sigma = float(self.contract["measurement_covariance"]["trunk_motion_plane_sigma_rad_s"])
            for phase, selected, axis in phases:
                if not len(selected):
                    continue
                relative = self.relative_omega(product, "pelvis", "torso", selected)
                axis_world = np.einsum(
                    "nij,j->ni",
                    self.corrected_rotation(product, "pelvis", selected),
                    axis,
                )
                values = (np.cross(relative, axis_world) / trunk_sigma / math.sqrt(len(selected))).ravel()
                blocks.append(ResidualBlock(
                    "trunk",
                    f"time_resolved_trunk_{phase}_axis",
                    "MEASURED_OBSERVATION",
                    values,
                    selected,
                    (self.segment_node["pelvis"], self.segment_node["torso"]),
                    "rad/s",
                    "cross(relative_trunk_gyro, shared_right_handed_frame_axis)/sigma",
                    "phase-conditioned relative gyro covariance",
                    ("EFFECTIVE_RELATIVE_HEADING", "TRUNK_FUNCTIONAL_FRAME"),
                ))

            # The measured axial mode also links the pelvis-fixed trunk frame
            # to both observed trunk longitudinal directions.  This is a
            # time-resolved anatomical connectivity relation, not a fixed
            # navigation-frame pose template: all vectors rotate with the
            # measured boards and no north/global-yaw direction is supplied.
            axial_rows = rows[self.obs.time_ns[rows] <= cut2]
            if len(axial_rows):
                pelvis_rotation = self.corrected_rotation(product, "pelvis", axial_rows)
                axial_world = np.einsum("nij,j->ni", pelvis_rotation, product["trunk_frame"][:, 2])
                pelvis_direction = self.corrected_direction(product, "pelvis", axial_rows)
                torso_direction = self.corrected_direction(product, "torso", axial_rows)
                geometry_sigma = math.radians(float(
                    self.contract["measurement_covariance"]["functional_axis_geometry_sigma_deg"]
                ))
                values = np.concatenate((
                    s2_residual(axial_world, pelvis_direction).ravel(),
                    s2_residual(axial_world, torso_direction).ravel(),
                )) / geometry_sigma / math.sqrt(2 * len(axial_rows))
                blocks.append(ResidualBlock(
                    "trunk",
                    "time_resolved_trunk_axial_longitudinal_connectivity",
                    "MEASURED_OBSERVATION",
                    values,
                    axial_rows,
                    (self.segment_node["pelvis"], self.segment_node["torso"]),
                    "rad",
                    "LogS2(R_pelvis_board_to_nav trunk_axial, measured pelvis/torso longitudinal directions)",
                    "measured axial-mode covariance plus broad anatomical connectivity mismatch",
                    ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "TRUNK_FUNCTIONAL_FRAME"),
                ))
        return blocks

    def zero_blocks(self, product: Mapping[str, Any]) -> list[ResidualBlock]:
        sigma = float(self.contract["measurement_covariance"]["neutral_zero_sigma_rad"])
        blocks: list[ResidualBlock] = []
        action = "initial_still_attempt2"
        for zero_name in ZERO_JOINTS:
            if zero_name == "trunk":
                parent, child = "pelvis", "torso"
            else:
                parent, child = JOINTS[zero_name]
            rows = self._rows(action, (parent, child), static=True)
            if not len(rows):
                continue
            if zero_name == "trunk":
                axis = np.einsum(
                    "nij,j->ni",
                    self.corrected_rotation(product, "pelvis", rows),
                    product["trunk_frame"][:, 0],
                )
            else:
                axis = self.functional_axis_world(product, zero_name, rows)
            parent_direction = self.corrected_direction(product, parent, rows)
            child_direction = self.corrected_direction(product, child, rows)
            signed = np.arctan2(
                np.einsum("nj,nj->n", axis, np.cross(parent_direction, child_direction)),
                np.einsum("nj,nj->n", parent_direction, child_direction),
            )
            values = wrap_angle(signed - product["zeros"][zero_name]) / sigma / math.sqrt(len(rows))
            blocks.append(ResidualBlock(
                action,
                f"capture_defined_neutral_zero:{zero_name}",
                "PROTOCOL_CONDITIONED_MEASUREMENT",
                values,
                rows,
                (self.segment_node[parent], self.segment_node[child]),
                "rad",
                "signed_angle(d_parent,d_child,shared_axis)-capture_zero",
                "initial-rest direction covariance; non-clinical reporting convention",
                ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", "JOINT_NEUTRAL_ZERO"),
            ))
        return blocks

    def semantic_functional_blocks(
        self,
        product: Mapping[str, Any],
        nuisance: Mapping[str, np.ndarray],
    ) -> list[ResidualBlock]:
        """Softly orient functional-axis signs from T-pose body structure.

        The body directions are latent and jointly estimated.  Only their
        relative upright/lateral/right-handed structure is used; no north or
        fixed navigation-frame direction enters this factor.
        """

        rows = self._rows("t_pose", tuple(SEGMENTS), static=True)
        if not len(rows):
            return []
        latent = articulated_pose_directions("t_pose", nuisance["t_pose"])
        up = unit(latent["torso"])
        lateral = unit(latent["upper_arm_L"] - latent["upper_arm_R"])
        forward = unit(np.cross(up, lateral))
        sigma = math.radians(float(self.contract["measurement_covariance"]["functional_semantic_sigma_deg"]))
        blocks: list[ResidualBlock] = []
        expected = {
            "shoulder_L": forward,
            "shoulder_R": forward,
            # Elbow axes are fixed in the parent upper-arm frames.  Raising
            # the arms transports the neutral lateral axes to opposite
            # upright directions; treating them as world-fixed lateral axes
            # is the defect this model is explicitly designed to avoid.
            "elbow_L": unit(np.cross(latent["upper_arm_L"], forward)),
            "elbow_R": unit(np.cross(latent["upper_arm_R"], forward)),
            "hip_L": lateral,
            "hip_R": lateral,
            "knee_L": lateral,
            "knee_R": lateral,
        }
        for joint, target in expected.items():
            axes = self.functional_axis_world(product, joint, rows)
            values = (s2_residual(np.tile(target, (len(rows), 1)), axes) / sigma / math.sqrt(len(rows))).ravel()
            parent, child = JOINTS[joint]
            blocks.append(ResidualBlock(
                "t_pose",
                f"soft_bilateral_functional_semantics:{joint}",
                "PROTOCOL_CONDITIONED_MEASUREMENT",
                values,
                rows,
                (self.segment_node[parent], self.segment_node[child]),
                "unit_direction/rad",
                "LogS2(latent body forward/lateral, R_parent_board_to_nav axis_parent_board)",
                "broad T-pose bilateral semantic covariance",
                ("EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS", "ARTICULATED_POSE_t_pose"),
            ))

        pelvis_rotation = self.corrected_rotation(product, "pelvis", rows)
        flex_axes = np.einsum("nij,j->ni", pelvis_rotation, product["trunk_frame"][:, 0])
        axial_axes = np.einsum("nij,j->ni", pelvis_rotation, product["trunk_frame"][:, 2])
        trunk_values = np.concatenate((
            (s2_residual(np.tile(lateral, (len(rows), 1)), flex_axes) / sigma / math.sqrt(2 * len(rows))).ravel(),
            (s2_residual(np.tile(up, (len(rows), 1)), axial_axes) / sigma / math.sqrt(2 * len(rows))).ravel(),
        ))
        blocks.append(ResidualBlock(
            "t_pose",
            "soft_right_handed_trunk_frame_semantics",
            "PROTOCOL_CONDITIONED_MEASUREMENT",
            trunk_values,
            rows,
            (self.segment_node["pelvis"], self.segment_node["torso"]),
            "unit_direction/rad",
            "latent lateral/up versus pelvis-board-fixed flex/axial frame axes",
            "broad T-pose bilateral semantic covariance",
            ("EFFECTIVE_RELATIVE_HEADING", "TRUNK_FUNCTIONAL_FRAME", "ARTICULATED_POSE_t_pose"),
        ))
        return blocks

    def prior_blocks(self, nuisance: Mapping[str, np.ndarray]) -> list[ResidualBlock]:
        blocks: list[ResidualBlock] = []
        for action in STATIC_ACTIONS:
            sigma = math.radians(float(self.contract["measurement_covariance"]["tpose_protocol_sigma_deg"] if action == "t_pose" else self.contract["measurement_covariance"]["natural_pose_sigma_deg"]))
            q = nuisance[action]
            # Root yaw is deliberately absent.  This is a broad soft protocol
            # model and is never included in measurement-only rank.
            values = np.r_[q[:2], q[3:]] / sigma
            blocks.append(ResidualBlock(
                action,
                "broad_soft_pose_protocol",
                "PARAMETER_ONLY_PRIOR",
                values,
                np.empty(0, dtype=int),
                tuple(),
                "rad",
                "broad deviation in connected articulated pose chart",
                "declared protocol covariance; excluded from data-only rank",
                (f"ARTICULATED_POSE_{action}",),
            ))
        return blocks

    def blocks(self, x: np.ndarray, include_nonmeasurement: bool = True) -> list[ResidualBlock]:
        product, nuisance = decode_full(x)
        blocks = (
            self.static_blocks(product, nuisance)
            + self.dynamic_blocks(product, nuisance)
            + self.zero_blocks(product)
            + self.semantic_functional_blocks(product, nuisance)
        )
        if include_nonmeasurement:
            blocks += self.prior_blocks(nuisance)
        return blocks

    def residual(self, x: np.ndarray, include_nonmeasurement: bool = True) -> np.ndarray:
        blocks = self.blocks(x, include_nonmeasurement)
        if not blocks or any(not len(block.values) for block in blocks):
            raise ValueError("missing or empty calibration residual block")
        result = np.concatenate([np.asarray(block.values, dtype=float) for block in blocks])
        if not np.isfinite(result).all():
            raise ValueError("non-finite calibration residual")
        return result


def bounds() -> tuple[np.ndarray, np.ndarray]:
    lower = np.full(FULL_DIMENSION, -math.pi, dtype=float)
    upper = np.full(FULL_DIMENSION, math.pi, dtype=float)
    latitude_indices = [2 * index + 1 for index in range(10)] + [29 + 2 * index + 1 for index in range(8)]
    for index in latitude_indices:
        # This is a coordinate-chart boundary, not anatomical information.
        # The previous +/-1.45 limit excluded valid near-vertical board axes
        # and made some synthetic truths infeasible.  Keep only an epsilon
        # away from the two spherical-coordinate singularities.
        lower[index], upper[index] = -S2_LATITUDE_LIMIT, S2_LATITUDE_LIMIT
    pose_stop = PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION
    lower[PRODUCT_DIMENSION:pose_stop] = -1.6
    upper[PRODUCT_DIMENSION:pose_stop] = 1.6
    # The two latent pose root-yaw coordinates must span the full legal chart.
    # They absorb the capture's arbitrary VQF display frame after the pelvis
    # heading coordinate has fixed the sole global gauge; clipping them to a
    # human joint-angle range would bias every segment heading.
    for index in (PRODUCT_DIMENSION + 2, PRODUCT_DIMENSION + 17 + 2):
        lower[index], upper[index] = -math.pi, math.pi
    # Capture-shared sensor-to-joint lever arms are nuisance variables.  These
    # broad component-wise physical bounds protect the optimizer only and are
    # never rows in the residual/Jacobian or credited as observability.
    lever_limit = 0.65
    lower[pose_stop:] = -lever_limit
    upper[pose_stop:] = lever_limit
    return lower, upper


def production_jacobian(objective: UnifiedCalibrationObjective, x: np.ndarray, include_nonmeasurement: bool = True) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    step = float(objective.contract["solver"]["finite_difference_step"])
    low, high = bounds()
    base = objective.residual(x, include_nonmeasurement)
    jacobian = np.empty((len(base), len(x)), dtype=float)
    for column in range(len(x)):
        if x[column] - step >= low[column] and x[column] + step <= high[column]:
            minus = x.copy(); minus[column] -= step
            plus = x.copy(); plus[column] += step
            jacobian[:, column] = (objective.residual(plus, include_nonmeasurement) - objective.residual(minus, include_nonmeasurement)) / (2.0 * step)
        else:
            direction = 1.0 if x[column] + 2.0 * step <= high[column] else -1.0
            one = x.copy(); one[column] += direction * step
            two = x.copy(); two[column] += direction * 2.0 * step
            jacobian[:, column] = direction * (-3.0 * base + 4.0 * objective.residual(one, include_nonmeasurement) - objective.residual(two, include_nonmeasurement)) / (2.0 * step)
    if not np.isfinite(jacobian).all():
        raise ValueError("non-finite production Jacobian")
    return jacobian


def _principal_axis(values: np.ndarray, fallback: np.ndarray) -> np.ndarray:
    values = np.asarray(values, dtype=float)
    if len(values) < 3 or float(np.linalg.norm(values)) < 1e-10:
        return unit(fallback)
    _, _, vh = np.linalg.svd(values, full_matrices=False)
    axis = vh[0]
    if axis[int(np.argmax(np.abs(axis)))] < 0:
        axis = -axis
    return unit(axis)


def _b5_lever_matrix(rotation: np.ndarray, omega: np.ndarray, alpha: np.ndarray) -> np.ndarray:
    """Map a board-frame lever arm to navigation-frame rotational acceleration."""

    columns = np.transpose(rotation, (0, 2, 1))
    values = np.cross(alpha[:, None, :], columns)
    values += np.cross(omega[:, None, :], np.cross(omega[:, None, :], columns))
    return np.transpose(values, (0, 2, 1))


def initialize_b5_lever_arms(
    observation: R1Observation,
    contract: Mapping[str, Any],
    x: np.ndarray,
) -> np.ndarray:
    """Observation-only linear nuisance initializer; no lever prior is used."""

    x = np.asarray(x, dtype=float).copy()
    if observation.accel_mps2 is None:
        return x
    objective = UnifiedCalibrationObjective(observation, contract)
    product = decode_product(x[:PRODUCT_DIMENSION])
    endpoint_offset = {
        endpoint: PRODUCT_DIMENSION + POSE_NUISANCE_DIMENSION + 3 * index
        for index, endpoint in enumerate(B5_LEVER_ENDPOINTS)
    }
    for joint, (parent, child) in B5_JOINT_EDGES.items():
        designs = []
        targets = []
        for action, joints in B5_ACTION_JOINTS.items():
            if joint not in joints:
                continue
            rows = objective._rows(action, (parent, child))
            rows = rows[(rows > 0) & (rows + 1 < len(observation.time_ns))]
            if not len(rows):
                continue
            rp = objective.corrected_rotation(product, parent, rows)
            rc = objective.corrected_rotation(product, child, rows)
            wp = objective.corrected_omega(product, parent, rows)
            wc = objective.corrected_omega(product, child, rows)
            ap = objective.corrected_alpha(product, parent, rows)
            ac = objective.corrected_alpha(product, child, rows)
            mp = _b5_lever_matrix(rp, wp, ap)
            mc = _b5_lever_matrix(rc, wc, ac)
            designs.append(np.concatenate((mp, -mc), axis=2).reshape(-1, 6))
            targets.append((
                objective.corrected_specific_force(product, child, rows)
                - objective.corrected_specific_force(product, parent, rows)
            ).ravel())
        if not designs:
            continue
        design = np.concatenate(designs)
        target = np.concatenate(targets)
        solution, _, _, _ = np.linalg.lstsq(design, target, rcond=1e-8)
        solution = np.clip(solution, -0.64, 0.64)
        parent_start = endpoint_offset[(joint, parent)]
        child_start = endpoint_offset[(joint, child)]
        x[parent_start:parent_start + 3] = solution[:3]
        x[child_start:child_start + 3] = solution[3:]
    return x


def blind_initialization(observation: R1Observation, contract: Mapping[str, Any]) -> np.ndarray:
    objective = UnifiedCalibrationObjective(observation, contract)
    x = np.zeros(FULL_DIMENSION, dtype=float)
    # Observation-derived per-segment mounting/heading initialization from both
    # static actions.  The nominal poses seed only this initializer; the final
    # shared solve keeps both poses as independent 17-coordinate nuisances.
    for segment_index, segment in enumerate(SEGMENTS):
        samples: list[tuple[np.ndarray, np.ndarray]] = []
        for action in STATIC_ACTIONS:
            rows = objective._rows(action, (segment,), static=True)
            if not len(rows):
                continue
            expected = articulated_pose_directions(action, np.zeros(17))[segment]
            index = objective.segment_index[segment]
            samples.append((observation.rotation[rows, index], expected))
        if not samples:
            continue
        # A rough board-axis seed is enough for the small independent solve.
        candidates = []
        for rotations, expected in samples:
            candidates.extend(np.einsum("nji,j->ni", rotations, expected))
        axis_seed = angles_from_axis(unit(np.median(np.asarray(candidates), axis=0)))

        def mounting_residual(value: np.ndarray) -> np.ndarray:
            axis = axis_from_angles(float(value[0]), float(value[1]))
            heading = float(value[2]) if segment != "pelvis" else 0.0
            arrays = []
            for rotations, expected in samples:
                corrected = np.einsum("ij,njk,k->ni", yaw(heading), rotations, axis)
                arrays.append(s2_residual(np.tile(expected, (len(corrected), 1)), corrected).ravel() / math.sqrt(len(corrected)))
            return np.concatenate(arrays)

        starts = [np.r_[axis_seed, 0.0]]
        if segment != "pelvis":
            starts = [np.r_[axis_seed, angle] for angle in np.linspace(-math.pi, math.pi, 9)[:-1]]
        candidates_fit = []
        for start in starts:
            local_lower = np.array([-math.pi, -S2_LATITUDE_LIMIT, -math.pi])
            local_upper = np.array([math.pi, S2_LATITUDE_LIMIT, math.pi])
            start = np.clip(
                np.asarray(start, dtype=float),
                local_lower + 1e-9,
                local_upper - 1e-9,
            )
            fit = least_squares(
                mounting_residual,
                start,
                bounds=(local_lower, local_upper),
                max_nfev=80,
                xtol=1e-11,
                ftol=1e-11,
                gtol=1e-11,
            )
            candidates_fit.append(fit)
        best = min(candidates_fit, key=lambda item: float(item.cost))
        x[2 * segment_index:2 * segment_index + 2] = best.x[:2]
        if segment != "pelvis":
            x[20 + segment_index - 1] = best.x[2]

    # Recover the heading tree from time-resolved relative angular velocity.
    # For each child, its parent heading is already fixed and the candidate
    # child heading is scored by how nearly the labelled single-axis phase
    # collapses to one axis in the *parent board frame*.  This is precisely the
    # parent-frame correction; using a world-fixed PCA axis here recreates the
    # rejected stitched estimator.
    heading_schedule = (
        ("torso", "pelvis", "trunk", "axial"),
        ("upper_arm_L", "torso", "arms", "all"),
        ("upper_arm_R", "torso", "arms", "all"),
        ("forearm_L", "upper_arm_L", "left_elbow", "first_half"),
        ("forearm_R", "upper_arm_R", "right_elbow_attempt2", "first_half"),
        ("thigh_L", "pelvis", "left_knee", "all"),
        ("thigh_R", "pelvis", "right_knee", "all"),
        ("shank_L", "thigh_L", "left_heel", "all"),
        ("shank_R", "thigh_R", "right_heel", "all"),
    )
    for child, parent, action, phase in heading_schedule:
        rows = objective._rows(action, (parent, child))
        start_ns, stop_ns = observation.windows[action]
        if phase == "first_half":
            rows = rows[observation.time_ns[rows] <= start_ns + (stop_ns - start_ns) // 2]
        elif phase == "axial":
            rows = rows[observation.time_ns[rows] <= start_ns + 2 * (stop_ns - start_ns) // 3]
        product = decode_product(x[:PRODUCT_DIMENSION])
        parent_rotation = objective.corrected_rotation(product, parent, rows)
        parent_omega = objective.corrected_omega(product, parent, rows)
        parent_direction = objective.corrected_direction(product, parent, rows)
        child_index = objective.segment_index[child]

        def candidate_axis(candidate: float) -> np.ndarray:
            values = []
            for static_action in STATIC_ACTIONS:
                static_rows = objective._rows(static_action, (child,), static=True)
                expected = articulated_pose_directions(static_action, np.zeros(17))[child]
                corrected = np.einsum(
                    "ij,njk->nik", yaw(candidate), observation.rotation[static_rows, child_index],
                )
                values.extend(np.einsum("nji,j->ni", corrected, expected))
            return unit(np.median(np.asarray(values), axis=0))

        def heading_cost(candidate: float) -> float:
            angle = float(wrap_angle(np.array(candidate)))
            child_rotation = np.einsum("ij,njk->nik", yaw(angle), observation.rotation[rows, child_index])
            child_omega = np.einsum("nij,nj->ni", child_rotation, observation.gyro_rad_s[rows, child_index])
            relative_parent = np.einsum("nji,nj->ni", parent_rotation, child_omega - parent_omega)
            axis = _principal_axis(relative_parent, np.array([1.0, 0.0, 0.0]))
            off_axis = np.cross(relative_parent, axis)
            axis_world = np.einsum("nij,j->ni", parent_rotation, axis)
            child_direction = np.einsum("nij,j->ni", child_rotation, candidate_axis(angle))
            if child == "torso":
                geometry = np.r_[
                    np.linalg.norm(np.cross(axis_world, parent_direction), axis=1),
                    np.linalg.norm(np.cross(axis_world, child_direction), axis=1),
                ]
            else:
                geometry = np.r_[
                    np.einsum("ni,ni->n", axis_world, parent_direction),
                    np.einsum("ni,ni->n", axis_world, child_direction),
                ]
            return float(
                np.sum(off_axis * off_axis) / max(len(rows), 1)
                + np.sum(geometry * geometry) / max(len(geometry), 1)
            )

        grid = np.linspace(-math.pi, math.pi, 73)[:-1]
        scores = np.asarray([heading_cost(value) for value in grid])
        grid_index = int(np.argmin(scores))
        spacing = 2.0 * math.pi / len(grid)
        refined = minimize_scalar(
            heading_cost,
            bounds=(float(grid[grid_index] - spacing), float(grid[grid_index] + spacing)),
            method="bounded",
            options={"xatol": 1e-10, "maxiter": 100},
        )
        segment_index = SEGMENTS.index(child)
        x[20 + segment_index - 1] = float(wrap_angle(np.array(refined.x)))
        x[2 * segment_index:2 * segment_index + 2] = angles_from_axis(candidate_axis(float(refined.x)))

    # With the dynamic heading tree initialized, gravity-informed static
    # directions give an unambiguous board-fixed longitudinal-axis seed.
    product = decode_product(x[:PRODUCT_DIMENSION])
    for segment_index, segment in enumerate(SEGMENTS):
        candidates = []
        for action in STATIC_ACTIONS:
            rows = objective._rows(action, (segment,), static=True)
            expected = articulated_pose_directions(action, np.zeros(17))[segment]
            corrected = objective.corrected_rotation(product, segment, rows)
            candidates.extend(np.einsum("nji,j->ni", corrected, expected))
        axis = unit(np.median(np.asarray(candidates), axis=0))
        x[2 * segment_index:2 * segment_index + 2] = angles_from_axis(axis)

    cursor = 29
    product = decode_product(x[:PRODUCT_DIMENSION])
    action_for_joint = {
        "shoulder_L": "arms", "shoulder_R": "arms",
        "elbow_L": "left_elbow", "elbow_R": "right_elbow_attempt2",
        "hip_L": "left_knee", "hip_R": "right_knee",
        "knee_L": "left_heel", "knee_R": "right_heel",
    }
    for joint in FUNCTIONAL_JOINTS:
        parent, child = JOINTS[joint]
        rows = objective._rows(action_for_joint[joint], (parent, child))
        relative_world = objective.relative_omega(product, parent, child, rows)
        parent_rotation = objective.corrected_rotation(product, parent, rows)
        relative_parent_board = np.einsum("nji,nj->ni", parent_rotation, relative_world)
        axis = _principal_axis(relative_parent_board, np.array([1.0, 0.0, 0.0]))
        x[cursor:cursor + 2] = angles_from_axis(axis)
        cursor += 2

    # PCA axes are intrinsically unsigned.  Resolve only their initializer
    # signs from measured bilateral T-pose structure, in the same parent-board
    # semantics used by the final objective.  This is not a final stitched PCA
    # estimate: every coordinate remains free in the shared nonlinear solve.
    product = decode_product(x[:PRODUCT_DIMENSION])
    tpose_rows = objective._rows("t_pose", tuple(SEGMENTS), static=True)
    body_direction = {
        segment: unit(np.median(objective.corrected_direction(product, segment, tpose_rows), axis=0))
        for segment in SEGMENTS
    }
    body_up = body_direction["torso"]
    body_lateral = unit(body_direction["upper_arm_L"] - body_direction["upper_arm_R"])
    body_forward = unit(np.cross(body_up, body_lateral))
    initializer_targets = {
        "shoulder_L": body_forward,
        "shoulder_R": body_forward,
        "elbow_L": unit(np.cross(body_direction["upper_arm_L"], body_forward)),
        "elbow_R": unit(np.cross(body_direction["upper_arm_R"], body_forward)),
        "hip_L": body_lateral,
        "hip_R": body_lateral,
        "knee_L": body_lateral,
        "knee_R": body_lateral,
    }
    for joint_index, joint in enumerate(FUNCTIONAL_JOINTS):
        start = 29 + 2 * joint_index
        axis_board = axis_from_angles(float(x[start]), float(x[start + 1]))
        parent = JOINTS[joint][0]
        axis_world = unit(np.median(np.einsum(
            "nij,j->ni",
            objective.corrected_rotation(product, parent, tpose_rows),
            axis_board,
        ), axis=0))
        if float(axis_world @ initializer_targets[joint]) < 0.0:
            x[start:start + 2] = angles_from_axis(-axis_board)

    rows = objective._rows("trunk", ("pelvis", "torso"))
    start, stop = observation.windows["trunk"]
    cut2 = start + 2 * (stop - start) // 3
    axial_rows = rows[observation.time_ns[rows] <= cut2]
    flex_rows = rows[observation.time_ns[rows] > cut2]
    axial_world = objective.relative_omega(product, "pelvis", "torso", axial_rows)
    axial_parent = np.einsum("nji,nj->ni", objective.corrected_rotation(product, "pelvis", axial_rows), axial_world)
    flex_world = objective.relative_omega(product, "pelvis", "torso", flex_rows)
    flex_parent = np.einsum("nji,nj->ni", objective.corrected_rotation(product, "pelvis", flex_rows), flex_world)
    axial = _principal_axis(axial_parent, np.array([0.0, 0.0, 1.0]))
    flex_raw = _principal_axis(flex_parent, np.array([1.0, 0.0, 0.0]))
    flex = unit(flex_raw - axial * float(axial @ flex_raw))
    lateral = unit(np.cross(axial, flex))
    frame = np.column_stack((flex, lateral, axial))
    if np.linalg.det(frame) < 0:
        frame[:, 1] *= -1.0
    pelvis_tpose = objective.corrected_rotation(decode_product(x[:PRODUCT_DIMENSION]), "pelvis", tpose_rows)
    sign_options = (
        np.diag([1.0, 1.0, 1.0]),
        np.diag([-1.0, -1.0, 1.0]),
        np.diag([-1.0, 1.0, -1.0]),
        np.diag([1.0, -1.0, -1.0]),
    )
    def frame_semantic_score(candidate: np.ndarray) -> float:
        world = np.einsum("nij,jk->nik", pelvis_tpose, candidate)
        columns = [unit(np.median(world[:, :, index], axis=0)) for index in range(3)]
        return float(columns[0] @ body_lateral + columns[1] @ body_forward + columns[2] @ body_up)
    frame = max((frame @ signs for signs in sign_options), key=frame_semantic_score)
    x[cursor:cursor + 3] = Rotation.from_matrix(frame).as_rotvec()
    cursor += 3

    # Initialize capture-defined zeros from the measured initial rest.
    initialized_product = decode_product(x[:PRODUCT_DIMENSION])
    for zero_name in ZERO_JOINTS:
        if zero_name == "trunk":
            parent, child = "pelvis", "torso"
        else:
            parent, child = JOINTS[zero_name]
        rows = objective._rows("initial_still_attempt2", (parent, child), static=True)
        if zero_name == "trunk":
            axis = np.einsum(
                "nij,j->ni",
                objective.corrected_rotation(initialized_product, "pelvis", rows),
                initialized_product["trunk_frame"][:, 0],
            )
        else:
            axis = objective.functional_axis_world(initialized_product, zero_name, rows)
        dp = objective.corrected_direction(initialized_product, parent, rows)
        dc = objective.corrected_direction(initialized_product, child, rows)
        signed = np.arctan2(np.einsum("nj,nj->n", axis, np.cross(dp, dc)), np.einsum("nj,nj->n", dp, dc))
        x[cursor] = float(np.median(signed)) if len(signed) else 0.0
        cursor += 1
    assert cursor == PRODUCT_DIMENSION

    # Fit each static nuisance pose from the already observation-derived
    # corrected segment directions.  Starting both 17-coordinate poses at a
    # textbook zero creates a strong local minimum in which mounting errors
    # masquerade as human pose mismatch.  These pose-only fits are initializers
    # and remain independent; the shared final objective refits everything.
    initialized_product = decode_product(x[:PRODUCT_DIMENSION])
    low, high = bounds()
    for pose_index, action in enumerate(STATIC_ACTIONS):
        rows = objective._rows(action, tuple(SEGMENTS), static=True)
        observed = {
            segment: unit(np.median(objective.corrected_direction(initialized_product, segment, rows), axis=0))
            for segment in SEGMENTS
        }
        nominal = articulated_pose_directions(action, np.zeros(17))
        try:
            root, _ = Rotation.align_vectors(
                np.stack([observed[segment] for segment in SEGMENTS]),
                np.stack([nominal[segment] for segment in SEGMENTS]),
            )
            pose_seed = np.zeros(17)
            pose_seed[:3] = root.as_rotvec()
        except ValueError:
            pose_seed = np.zeros(17)
        base = PRODUCT_DIMENSION + 17 * pose_index
        pose_low, pose_high = low[base:base + 17], high[base:base + 17]
        pose_seed = np.clip(pose_seed, pose_low + 1e-6, pose_high - 1e-6)
        sigma = math.radians(float(
            contract["measurement_covariance"]["tpose_protocol_sigma_deg" if action == "t_pose" else "natural_pose_sigma_deg"]
        ))

        def pose_residual(q: np.ndarray) -> np.ndarray:
            predicted = articulated_pose_directions(action, q)
            measured = np.concatenate([
                s2_residual(predicted[segment][None, :], observed[segment][None, :]).ravel()
                for segment in SEGMENTS
            ])
            return np.r_[measured / math.radians(5.0), q[:2] / sigma, q[3:] / sigma]

        pose_fit = least_squares(
            pose_residual,
            pose_seed,
            bounds=(pose_low, pose_high),
            max_nfev=120,
            xtol=1e-11,
            ftol=1e-11,
            gtol=1e-11,
        )
        x[base:base + 17] = pose_fit.x
    return initialize_b5_lever_arms(observation, contract, x)


def _heading_profile(
    observation: R1Observation,
    contract: Mapping[str, Any],
    coordinates: np.ndarray,
) -> np.ndarray:
    """Profile observation-derived blocks for headings and two static yaw nuisances.

    The nine publishable headings are relative to the pelvis gauge.  They are
    not the arbitrary common navigation/body yaw carried by each latent static
    pose.  The earlier initializer silently set both pose yaws to zero while
    reconstructing the board longitudinal axes.  That made even the true
    relative-heading vector profile to a physically different mounting.  The
    two extra coordinates here are initializer-only nuisance seeds; both
    17-coordinate poses remain separate and are refit by the unified objective.
    """

    objective = UnifiedCalibrationObjective(observation, contract)
    coordinates = np.asarray(coordinates, dtype=float)
    if coordinates.shape != (11,):
        raise ValueError("heading profile requires nine relative headings and two static root yaws")
    headings = coordinates[:9]
    static_yaws = coordinates[9:]
    x = np.zeros(FULL_DIMENSION, dtype=float)
    x[20:29] = wrap_angle(np.asarray(headings, dtype=float))
    for pose_index, static_yaw in enumerate(static_yaws):
        x[PRODUCT_DIMENSION + 17 * pose_index + 2] = float(wrap_angle(static_yaw))

    # Static gravity-informed segment-axis seeds, conditional on headings.
    product = decode_product(x[:PRODUCT_DIMENSION])
    for segment_index, segment in enumerate(SEGMENTS):
        candidates = []
        for pose_index, action in enumerate(STATIC_ACTIONS):
            rows = objective._rows(action, (segment,), static=True)
            pose = np.zeros(17)
            pose[2] = static_yaws[pose_index]
            expected = articulated_pose_directions(action, pose)[segment]
            corrected = objective.corrected_rotation(product, segment, rows)
            candidates.extend(np.einsum("nji,j->ni", corrected, expected))
        x[2 * segment_index:2 * segment_index + 2] = angles_from_axis(
            unit(np.median(np.asarray(candidates), axis=0))
        )

    # Dynamic PCA is only a conditional initializer.  The final estimator
    # refits every functional coordinate in the unified objective.
    product = decode_product(x[:PRODUCT_DIMENSION])
    action_for_joint = {
        "shoulder_L": "arms", "shoulder_R": "arms",
        "elbow_L": "left_elbow", "elbow_R": "right_elbow_attempt2",
        "hip_L": "left_knee", "hip_R": "right_knee",
        "knee_L": "left_heel", "knee_R": "right_heel",
    }
    for joint_index, joint in enumerate(FUNCTIONAL_JOINTS):
        parent, child = JOINTS[joint]
        action = action_for_joint[joint]
        rows = objective._rows(action, (parent, child))
        if joint.startswith("elbow"):
            midpoint = observation.windows[action][0] + (
                observation.windows[action][1] - observation.windows[action][0]
            ) // 2
            rows = rows[observation.time_ns[rows] <= midpoint]
        relative = objective.relative_omega(product, parent, child, rows)
        parent_rotation = objective.corrected_rotation(product, parent, rows)
        relative_parent = np.einsum("nji,nj->ni", parent_rotation, relative)
        axis = _principal_axis(relative_parent, np.array([1.0, 0.0, 0.0]))
        start = 29 + 2 * joint_index
        x[start:start + 2] = angles_from_axis(axis)

    # Resolve initializer signs exclusively from measured bilateral structure.
    product = decode_product(x[:PRODUCT_DIMENSION])
    tpose_rows = objective._rows("t_pose", tuple(SEGMENTS), static=True)
    body_direction = {
        segment: unit(np.median(objective.corrected_direction(product, segment, tpose_rows), axis=0))
        for segment in SEGMENTS
    }
    up = body_direction["torso"]
    lateral = unit(body_direction["upper_arm_L"] - body_direction["upper_arm_R"])
    forward = unit(np.cross(up, lateral))
    targets = {
        "shoulder_L": forward, "shoulder_R": forward,
        "elbow_L": unit(np.cross(body_direction["upper_arm_L"], forward)),
        "elbow_R": unit(np.cross(body_direction["upper_arm_R"], forward)),
        "hip_L": lateral, "hip_R": lateral,
        "knee_L": lateral, "knee_R": lateral,
    }
    for joint_index, joint in enumerate(FUNCTIONAL_JOINTS):
        start = 29 + 2 * joint_index
        axis = axis_from_angles(float(x[start]), float(x[start + 1]))
        parent = JOINTS[joint][0]
        world = unit(np.median(np.einsum(
            "nij,j->ni", objective.corrected_rotation(product, parent, tpose_rows), axis,
        ), axis=0))
        if float(world @ targets[joint]) < 0.0:
            x[start:start + 2] = angles_from_axis(-axis)

    # Two measured trunk modes initialize a right-handed pelvis-board frame.
    product = decode_product(x[:PRODUCT_DIMENSION])
    rows = objective._rows("trunk", ("pelvis", "torso"))
    start_ns, stop_ns = observation.windows["trunk"]
    cut2 = start_ns + 2 * (stop_ns - start_ns) // 3
    axial_rows = rows[observation.time_ns[rows] <= cut2]
    flex_rows = rows[observation.time_ns[rows] > cut2]
    axial_relative = objective.relative_omega(product, "pelvis", "torso", axial_rows)
    axial_parent = np.einsum(
        "nji,nj->ni", objective.corrected_rotation(product, "pelvis", axial_rows), axial_relative,
    )
    flex_relative = objective.relative_omega(product, "pelvis", "torso", flex_rows)
    flex_parent = np.einsum(
        "nji,nj->ni", objective.corrected_rotation(product, "pelvis", flex_rows), flex_relative,
    )
    axial = _principal_axis(axial_parent, np.array([0.0, 0.0, 1.0]))
    flex_raw = _principal_axis(flex_parent, np.array([1.0, 0.0, 0.0]))
    flex = unit(flex_raw - axial * float(axial @ flex_raw))
    frame = np.column_stack((flex, unit(np.cross(axial, flex)), axial))
    pelvis_tpose = objective.corrected_rotation(product, "pelvis", tpose_rows)
    sign_options = (
        np.diag([1.0, 1.0, 1.0]), np.diag([-1.0, -1.0, 1.0]),
        np.diag([-1.0, 1.0, -1.0]), np.diag([1.0, -1.0, -1.0]),
    )
    def frame_score(candidate: np.ndarray) -> float:
        world = np.einsum("nij,jk->nik", pelvis_tpose, candidate)
        columns = [unit(np.median(world[:, :, index], axis=0)) for index in range(3)]
        return float(columns[0] @ lateral + columns[1] @ forward + columns[2] @ up)
    frame = max((frame @ signs for signs in sign_options), key=frame_score)
    x[45:48] = Rotation.from_matrix(frame).as_rotvec()

    # Capture-defined neutral zeros are deterministic functions of the profile.
    product = decode_product(x[:PRODUCT_DIMENSION])
    cursor = 48
    for zero_name in ZERO_JOINTS:
        parent, child = ("pelvis", "torso") if zero_name == "trunk" else JOINTS[zero_name]
        rows = objective._rows("initial_still_attempt2", (parent, child), static=True)
        axis = (
            np.einsum("nij,j->ni", objective.corrected_rotation(product, "pelvis", rows), product["trunk_frame"][:, 0])
            if zero_name == "trunk" else objective.functional_axis_world(product, zero_name, rows)
        )
        dp = objective.corrected_direction(product, parent, rows)
        dc = objective.corrected_direction(product, child, rows)
        signed = np.arctan2(
            np.einsum("nj,nj->n", axis, np.cross(dp, dc)),
            np.einsum("nj,nj->n", dp, dc),
        )
        x[cursor] = float(np.median(signed))
        cursor += 1
    return x


def _heading_profile_score(objective: UnifiedCalibrationObjective, x: np.ndarray) -> float:
    """Robust data-and-soft-protocol cost used only by global initialization.

    Parameter-only pose priors are excluded.  This makes the global profile
    ordering use the same measured/static/dynamic/semantic lineage as the
    publishable solve without manufacturing rank from a prior.
    """

    # B5 lever arms are initialized only after each global heading population
    # is selected.  Excluding B5 here preserves the established observation-
    # only heading search and avoids treating zero lever seeds as evidence.
    blocks = [
        block for block in objective.blocks(x, include_nonmeasurement=False)
        if not block.factor.startswith("b5_joint_center_specific_force_closure")
    ]
    residual = np.concatenate([block.values for block in blocks])
    return float(np.sum(np.sqrt(1.0 + residual * residual) - 1.0))


def qualified_initializations(
    observation: R1Observation,
    contract: Mapping[str, Any],
    seed: int,
) -> list[np.ndarray]:
    """Return basin-diverse deterministic observation-only global starts.

    A capture/donning has one arbitrary common body/navigation yaw, so the
    initializer profiles one shared root-yaw seed for the two static actions.
    This does not tie the two latent 17-coordinate pose states in the estimator:
    the returned vectors merely seed both root-yaw coordinates equally and the
    unified least-squares solve refits them independently.
    """

    objective = UnifiedCalibrationObjective(observation, contract)
    cfg = contract["solver"]
    count = int(cfg["global_initializer_populations"])
    if count < 5:
        raise ValueError("global qualification requires at least five deterministic populations")
    stride = int(cfg["global_initializer_seed_stride"])

    def expand(values: np.ndarray) -> np.ndarray:
        values = np.asarray(values, dtype=float)
        return np.r_[values[:9], values[9], values[9]]

    candidates: list[np.ndarray] = []
    for population_index in range(count):
        result = differential_evolution(
            lambda values: _heading_profile_score(
                objective, _heading_profile(observation, contract, expand(values)),
            ),
            [(-math.pi, math.pi)] * 10,
            seed=int(seed) + stride * population_index,
            maxiter=int(cfg["global_initializer_generations"]),
            popsize=int(cfg["global_initializer_population_multiplier"]),
            tol=1e-5,
            atol=1e-7,
            polish=True,
            updating="immediate",
            workers=1,
        )
        candidates.append(initialize_b5_lever_arms(
            observation,
            contract,
            _heading_profile(observation, contract, expand(result.x)),
        ))
    return candidates


def qualified_initialization(observation: R1Observation, contract: Mapping[str, Any], seed: int) -> np.ndarray:
    """Compatibility wrapper returning the best profiled global start."""

    objective = UnifiedCalibrationObjective(observation, contract)
    candidates = qualified_initializations(observation, contract, seed)
    blind = blind_initialization(observation, contract)
    return min((*candidates, blind), key=lambda value: _heading_profile_score(objective, value))


def deterministic_starts(x0: np.ndarray, count: int, seed: int) -> list[np.ndarray]:
    if count < 5:
        raise ValueError("observability-first qualification requires at least five starts")
    rng = np.random.default_rng(seed)
    low, high = bounds()
    starts = [np.clip(np.asarray(x0, dtype=float), low + 1e-6, high - 1e-6)]
    for _ in range(1, count):
        perturb = rng.normal(0.0, 0.06, len(x0))
        # Explore the nine heading coordinates more broadly than local manifold
        # coordinates while remaining capture-blind and deterministic.
        perturb[20:29] = rng.normal(0.0, 0.30, 9)
        starts.append(np.clip(x0 + perturb, low + 1e-6, high - 1e-6))
    return starts


def fit_multistart(
    objective: UnifiedCalibrationObjective,
    x0: np.ndarray | list[np.ndarray],
    contract: Mapping[str, Any],
    seed: int,
) -> list[dict[str, Any]]:
    cfg = contract["solver"]
    low, high = bounds()
    results: list[dict[str, Any]] = []
    # Residual-to-state dependency is sparse.  Derive a conservative union at
    # two deterministic points; this changes only finite-difference grouping,
    # while the independent dense Jacobian remains the rank authority.
    supplied = np.asarray(x0, dtype=float)
    if supplied.ndim == 2:
        if supplied.shape[1] != FULL_DIMENSION or len(supplied) < 5:
            raise ValueError("basin-diverse multistart requires at least five full state vectors")
        starts = [np.clip(value, low + 1e-6, high - 1e-6) for value in supplied]
        structural_seed = starts[0]
    elif supplied.shape == (FULL_DIMENSION,):
        starts = deterministic_starts(supplied, int(cfg["starts"]), seed)
        structural_seed = supplied
    else:
        raise ValueError("invalid multistart initializer shape")
    probe = np.clip(
        structural_seed + 0.03 * np.sin(np.arange(FULL_DIMENSION) + 0.5),
        low + 1e-6,
        high - 1e-6,
    )
    structural = (
        np.abs(production_jacobian(objective, structural_seed, True)) > 1e-13
    ) | (
        np.abs(production_jacobian(objective, probe, True)) > 1e-13
    )
    sparsity = csr_matrix(structural)
    for index, start in enumerate(starts):
        result = least_squares(
            objective.residual,
            start,
            bounds=(low, high),
            jac=str(cfg.get("fit_jacobian", "2-point")),
            jac_sparsity=sparsity,
            tr_solver="lsmr",
            tr_options={"atol": 1e-12, "btol": 1e-12, "maxiter": 500},
            workers=int(cfg.get("workers", 1)),
            loss=str(cfg["loss"]),
            f_scale=float(cfg["f_scale"]),
            max_nfev=int(cfg["maximum_function_evaluations"]),
            xtol=float(cfg["xtol"]),
            ftol=float(cfg["ftol"]),
            gtol=float(cfg["gtol"]),
            x_scale="jac",
        )
        results.append({
            "start": index,
            "x": result.x,
            "cost": float(result.cost),
            "optimality": float(result.optimality),
            "nfev": int(result.nfev),
            "njev": int(result.njev or 0),
            "success": bool(result.success),
            "finite": bool(np.isfinite(result.x).all() and np.isfinite(result.fun).all()),
            "message": str(result.message),
        })
    return results


def profiled_product_observability(jacobian: np.ndarray, relative_threshold: float = 1e-7, absolute_threshold: float = 1e-8) -> dict[str, Any]:
    jacobian = np.asarray(jacobian, dtype=float)
    jp = jacobian[:, :PRODUCT_DIMENSION]
    jn = jacobian[:, PRODUCT_DIMENSION:]
    un, nuisance_singular, _ = np.linalg.svd(jn, full_matrices=False)
    nuisance_cut = max(absolute_threshold, float(nuisance_singular[0]) * relative_threshold if len(nuisance_singular) else 0.0)
    nuisance_rank = int(np.sum(nuisance_singular > nuisance_cut))
    basis = un[:, :nuisance_rank]
    effective = jp - basis @ (basis.T @ jp) if nuisance_rank else jp.copy()
    _, singular, vh = np.linalg.svd(effective, full_matrices=False)
    threshold = max(absolute_threshold, float(singular[0]) * relative_threshold if len(singular) else 0.0)
    rank = int(np.sum(singular > threshold))
    return {
        "rank": rank,
        "nullity": PRODUCT_DIMENSION - rank,
        "threshold": threshold,
        "singular_values": singular,
        "right_vectors": vh,
        "effective_jacobian": effective,
        "nuisance_rank": nuisance_rank,
        "nuisance_singular_values": nuisance_singular,
    }
