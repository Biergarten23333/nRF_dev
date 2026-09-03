"""Functional sensor-to-segment calibration and qmt baselines."""
from __future__ import annotations

from dataclasses import dataclass, field
from dataclasses import replace
import itertools
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.v0.raw6_heading import (
    EDGE_BY_NAME,
    Raw6Episode,
    SEGMENTS,
    evaluate_b5,
    estimate_hinge_axes_qmt,
    profile_b5,
    qmt_edge_heading,
)

from .factors import FactorBundle
from .geometry import BodyGeometry, LIMBS
from .mounts import MountBranch, candidate_bank, wear_direction_report
from .orientation_trajectories import (
    EdgeHeadingTrajectory,
    extract_qmt_heading_trajectories,
)
from .staging import BoundedStage


HINGE_ACTIONS = {
    "elbow_left": ("06_elbow_left",),
    "elbow_right": ("07_elbow_right",),
    "knee_left": ("10_knee_left_seated", "16_squat", "18_heel_to_butt_left"),
    "knee_right": ("11_knee_right_seated", "16_squat", "19_heel_to_butt_right"),
}


@dataclass(frozen=True)
class HingeAxisEstimate:
    edge: str
    parent: str
    child: str
    parent_axis_sensor: np.ndarray
    child_axis_sensor: np.ndarray
    qmt_axis_report: Mapping[str, Any]
    qmt_heading_report: Mapping[str, Any]
    heading_trajectories: Mapping[str, EdgeHeadingTrajectory] = field(
        default_factory=dict
    )


@dataclass(frozen=True)
class FunctionalCandidate:
    candidate_id: str
    base_mount_branch: str
    axis_signs: Mapping[str, tuple[int, int]]
    body_from_sensor_by_segment: Mapping[str, np.ndarray]
    hinge_axes: Mapping[str, HingeAxisEstimate]
    wear_report: Mapping[str, Any]
    correction_angles_deg: Mapping[str, float]
    remaining_axis_misalignment_deg: Mapping[str, float]
    functional_alignment_fraction: float


@dataclass(frozen=True)
class CenterRefinement:
    candidate: FunctionalCandidate
    axial_offset_seed_m: np.ndarray
    report: Mapping[str, Any]


def _minimal_alignment(source_body: np.ndarray, target_body: np.ndarray) -> np.ndarray:
    source = np.asarray(source_body, dtype=float)
    source /= np.linalg.norm(source)
    target = np.asarray(target_body, dtype=float)
    target /= np.linalg.norm(target)
    cross = np.cross(source, target)
    dot = float(np.clip(source @ target, -1.0, 1.0))
    if np.linalg.norm(cross) < 1e-10:
        if dot > 0.0:
            return np.eye(3)
        seed = np.array([1.0, 0.0, 0.0])
        if abs(float(seed @ source)) > 0.8:
            seed = np.array([0.0, 0.0, 1.0])
        axis = seed - source * float(seed @ source)
        axis /= np.linalg.norm(axis)
        return Rotation.from_rotvec(np.pi * axis).as_matrix()
    angle = np.arctan2(np.linalg.norm(cross), dot)
    return Rotation.from_rotvec(angle * cross / np.linalg.norm(cross)).as_matrix()


def estimate_hinge_baselines(
    episodes: Sequence[Raw6Episode],
    config: Mapping[str, Any],
    *,
    previous: Mapping[str, HingeAxisEstimate] | None = None,
    deadline: BoundedStage | None = None,
) -> dict[str, HingeAxisEstimate]:
    output: dict[str, HingeAxisEstimate] = {}
    qmt_episodes = tuple(replace(episode, partition="IDENTIFICATION_TRAIN") for episode in episodes)
    available = {episode.action for episode in episodes}
    for edge, intended in HINGE_ACTIONS.items():
        relevant = [episode for episode in qmt_episodes if episode.action in intended]
        if not relevant:
            # A causal prefix cannot borrow an unrelated action or a future
            # hinge episode to manufacture a functional axis.
            continue
        relevant_actions = [episode.action for episode in relevant]
        prior = None if previous is None else previous.get(edge)
        prior_actions = (
            list(prior.qmt_axis_report.get("predeclared_relevant_actions", []))
            if prior is not None else []
        )
        if prior is not None and prior_actions == relevant_actions:
            output[edge] = replace(prior, qmt_axis_report={
                **prior.qmt_axis_report,
                "incremental_cache": {
                    "reused": True,
                    "reason": "NO_NEW_PREDECLARED_HINGE_ACTION_IN_PREFIX",
                    "relevant_actions": relevant_actions,
                },
            })
            if deadline is not None:
                deadline.checkpoint(f"{edge}:reuse_cached_axis")
            continue
        qmt_config = config["bounded_pipeline"]["qmt_hinge_axis"]
        parent_axis, child_axis, axis_report = estimate_hinge_axes_qmt(
            edge,
            relevant,
            maximum_samples=int(qmt_config["maximum_input_samples_before_selection"]),
            qmt_settings=qmt_config,
            bounded_call=(deadline.run if deadline is not None else None),
        )
        parent, child, _ = EDGE_BY_NAME[edge]
        endpoint_excitation = {}
        for role, segment in (("parent", parent), ("child", child)):
            gyro = np.concatenate([episode.gyro[segment] for episode in relevant])
            centered = gyro - np.median(gyro, axis=0)
            endpoint_excitation[f"{role}_gyro_rms_rad_s"] = float(
                np.sqrt(np.mean(centered * centered))
            )
            selected_distribution = axis_report["input_degeneracy"][
                "qmt_selected_distribution_by_start"
            ][int(axis_report["selected_start"])]["selected_denominator_audit"][role][
                "distribution"
            ]
            endpoint_excitation[f"{role}_selected_excitation_report"] = (
                selected_distribution
            )
            endpoint_excitation[f"{role}_selected_excitation_qualified"] = bool(
                not selected_distribution["material_selected_degeneracy"]
            )
        axis_report = {
            **axis_report,
            **endpoint_excitation,
            "axis_uncertainty_sigma_rad": float(np.radians(max(
                float(qmt_config["minimum_hinge_axis_uncertainty_sigma_deg"]),
                float(axis_report["multistart_parent_axis_max_spread_deg"]),
            ))),
            "axis_uncertainty_model": {
                "source": "MAXIMUM_OF_CAPTURE_INDEPENDENT_FLOOR_AND_MULTISTART_SPREAD",
                "minimum_sigma_deg": float(
                    qmt_config["minimum_hinge_axis_uncertainty_sigma_deg"]
                ),
                "multistart_spread_deg": float(
                    axis_report["multistart_parent_axis_max_spread_deg"]
                ),
                "fixed_exact_hinge_axis_assumed": False,
            },
            "incremental_cache": {
                "reused": False,
                "reason": "NEW_PREDECLARED_HINGE_ACTION_OR_FIRST_ESTIMATE",
                "relevant_actions": relevant_actions,
            },
        }
        heading_report_with_trajectories = qmt_edge_heading(
            edge,
            qmt_episodes,
            parent_axis,
            child_axis,
            intended_actions=set(intended) & available,
            axis_multistart_spread_deg=float(axis_report["multistart_parent_axis_max_spread_deg"]),
            noise_significance_contract=config["bounded_pipeline"][
                "qmt_heading_signal"
            ],
            bounded_call=(deadline.run if deadline is not None else None),
        )
        heading_trajectories, heading_report = extract_qmt_heading_trajectories(
            edge, heading_report_with_trajectories, qmt_episodes,
        )
        output[edge] = HingeAxisEstimate(
            edge, parent, child, parent_axis, child_axis, axis_report, heading_report,
            heading_trajectories,
        )
    return output


def functional_candidates(
    branch: MountBranch,
    hinge_axes: Mapping[str, HingeAxisEstimate],
    node_to_segment: Mapping[str, str],
    *,
    maximum_candidates: int = 6,
    alignment_fractions: tuple[float, ...] = (0.0, 0.5, 1.0),
) -> tuple[FunctionalCandidate, ...]:
    """Retain sign branches, rank them only by broad wear consistency."""

    segment_to_node = {segment: node for node, segment in node_to_segment.items()}
    candidates: list[FunctionalCandidate] = []
    sign_keys = tuple(
        (edge, endpoint) for edge in hinge_axes for endpoint in ("parent", "child")
    )
    base_transforms = {
        segment: np.asarray(branch.body_from_sensor[node], dtype=float).copy()
        for node, segment in node_to_segment.items()
    }
    endpoint_segment: dict[tuple[str, str], str] = {}
    endpoint_options: dict[tuple[float, str, str, int], Mapping[str, Any]] = {}
    used_segments: set[str] = set()
    for edge, estimate in hinge_axes.items():
        for endpoint, segment, axis in (
            ("parent", estimate.parent, estimate.parent_axis_sensor),
            ("child", estimate.child, estimate.child_axis_sensor),
        ):
            if segment in used_segments:
                raise ValueError("hinge endpoint segment appears in multiple functional axes")
            used_segments.add(segment)
            endpoint_segment[(edge, endpoint)] = segment
            current = base_transforms[segment] @ axis
            for sign in (-1, 1):
                target = np.array([0.0, float(sign), 0.0])
                full_correction = _minimal_alignment(current, target)
                full_rotvec = Rotation.from_matrix(full_correction).as_rotvec()
                full_angle_deg = float(np.degrees(np.linalg.norm(full_rotvec)))
                for alignment_fraction in alignment_fractions:
                    correction = Rotation.from_rotvec(
                        float(alignment_fraction) * full_rotvec
                    ).as_matrix()
                    transform = correction @ base_transforms[segment]
                    corrected_axis = transform @ axis
                    endpoint_options[(
                        float(alignment_fraction), edge, endpoint, sign,
                    )] = {
                        "transform": transform,
                        "correction_deg": full_angle_deg * float(alignment_fraction),
                        "remaining_deg": float(np.degrees(np.arccos(np.clip(
                            corrected_axis @ target, -1.0, 1.0,
                        )))),
                    }
    for signs, alignment_fraction in itertools.product(
        itertools.product((-1, 1), repeat=len(sign_keys)), alignment_fractions,
    ):
        endpoint_sign = dict(zip(sign_keys, signs))
        sign_map = {
            edge: (endpoint_sign[(edge, "parent")], endpoint_sign[(edge, "child")])
            for edge in hinge_axes
        }
        transforms = {
            segment: matrix.copy() for segment, matrix in base_transforms.items()
        }
        corrections: dict[str, float] = {}
        remaining: dict[str, float] = {}
        for edge, endpoint in sign_keys:
            segment = endpoint_segment[(edge, endpoint)]
            option = endpoint_options[(
                float(alignment_fraction), edge, endpoint,
                endpoint_sign[(edge, endpoint)],
            )]
            transforms[segment] = np.asarray(option["transform"], dtype=float).copy()
            corrections[segment] = float(option["correction_deg"])
            remaining[segment] = float(option["remaining_deg"])
        by_node = {node: transforms[segment] for node, segment in node_to_segment.items()}
        wear = wear_direction_report(by_node)
        candidates.append(FunctionalCandidate(
            candidate_id=(branch.branch_id + f"_ALIGN{alignment_fraction:.1f}_" + "_".join(
                f"{edge}:p{'+' if sign_map[edge][0] > 0 else '-'}c{'+' if sign_map[edge][1] > 0 else '-'}"
                for edge in hinge_axes
            )),
            base_mount_branch=branch.branch_id,
            axis_signs=sign_map,
            body_from_sensor_by_segment=transforms,
            hinge_axes=hinge_axes,
            wear_report=wear,
            correction_angles_deg=corrections,
            remaining_axis_misalignment_deg=remaining,
            functional_alignment_fraction=float(alignment_fraction),
        ))
    candidates.sort(key=lambda candidate: (
        not candidate.wear_report["hard_pass"],
        max(candidate.remaining_axis_misalignment_deg.values(), default=180.0),
        candidate.wear_report["soft_cost"],
        max(candidate.correction_angles_deg.values(), default=0.0),
    ))
    return tuple(candidates[:maximum_candidates])


def _unit(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=float)
    norm = float(np.linalg.norm(value))
    if norm <= 1e-9:
        raise ValueError("functional center direction is degenerate")
    return value / norm


def _kinematic_noise_significance(
    blocks: Sequence[Any], *, endpoint: int, sample_period_s: float,
) -> Mapping[str, Any]:
    name = "parent_kinematic" if endpoint == 0 else "child_kinematic"
    gyro_name = "parent_gyro" if endpoint == 0 else "child_gyro"
    covariance_name = (
        "parent_gyro_noise_cov" if endpoint == 0 else "child_gyro_noise_cov"
    )
    source_name = "parent_noise_cov_source" if endpoint == 0 else "child_noise_cov_source"
    kinematic = np.concatenate([getattr(block, name) for block in blocks])
    gyro = np.concatenate([getattr(block, gyro_name) for block in blocks])
    covariance = np.asarray(getattr(blocks[0], covariance_name), dtype=float)
    if any(not np.allclose(
        np.asarray(getattr(block, covariance_name), dtype=float), covariance,
        rtol=1e-9, atol=1e-15,
    ) for block in blocks):
        raise ValueError("capture-wide kinematic gyro covariance changed")
    maximum_variance = float(np.max(np.linalg.eigvalsh(covariance)))
    dt = float(sample_period_s)
    alpha_variance = maximum_variance / (2.0 * dt * dt)
    rate_q90 = float(np.quantile(np.linalg.norm(gyro, axis=1), 0.90))
    centripetal_variance = (4.0 * rate_q90) ** 2 * maximum_variance
    noise_sigma = float(np.sqrt(alpha_variance + centripetal_variance))
    rms = float(np.sqrt(np.mean(kinematic * kinematic)))
    return {
        "kinematic_rms_s2": rms,
        "propagated_kinematic_noise_sigma_s2": noise_sigma,
        "noise_standardized_significance": rms / noise_sigma,
        "gyro_noise_covariance_rad2_s2": covariance.tolist(),
        "gyro_noise_covariance_source": sorted(set(
            str(getattr(block, source_name)) for block in blocks
        )),
        "gyro_rate_q90_rad_s": rate_q90,
        "angular_acceleration_noise_variance_s4": alpha_variance,
        "centripetal_noise_variance_s4": centripetal_variance,
        "dynamic_residual_or_jerk_used_as_noise": False,
    }


def refine_candidate_from_joint_centers(
    candidate: FunctionalCandidate,
    bundle: FactorBundle,
    geometry: BodyGeometry,
    state: np.ndarray,
    node_to_segment: Mapping[str, str],
    config: Mapping[str, Any],
    *,
    profiled_centers: tuple[Mapping[str, np.ndarray], Mapping[str, Any]] | None = None,
) -> CenterRefinement:
    """Use train-only profiled centers to recover limb longitudinal mounts."""

    value = np.asarray(state, dtype=float)
    if value.shape != (17,):
        raise ValueError("center refinement requires the complete C2 state")
    if profiled_centers is None:
        lever_by_edge, edge_report = profile_joint_centers(bundle, value)
    else:
        lever_by_edge, edge_report = profiled_centers

    center_vectors = {
        "upper_arm_left": (
            lever_by_edge["shoulder_left"][3:] - lever_by_edge["elbow_left"][:3],
        ),
        "forearm_left": (lever_by_edge["elbow_left"][3:],),
        "upper_arm_right": (
            lever_by_edge["shoulder_right"][3:] - lever_by_edge["elbow_right"][:3],
        ),
        "forearm_right": (lever_by_edge["elbow_right"][3:],),
        "thigh_left": (
            lever_by_edge["hip_left"][3:] - lever_by_edge["knee_left"][:3],
        ),
        "shank_left": (lever_by_edge["knee_left"][3:],),
        "thigh_right": (
            lever_by_edge["hip_right"][3:] - lever_by_edge["knee_right"][:3],
        ),
        "shank_right": (lever_by_edge["knee_right"][3:],),
    }
    axis_by_segment: dict[str, np.ndarray] = {}
    axis_source_by_segment: dict[str, Mapping[str, Any]] = {}
    for edge_name, estimate in candidate.hinge_axes.items():
        parent_sign, child_sign = candidate.axis_signs[edge_name]
        for role, segment, sign, axis in (
            ("parent", estimate.parent, parent_sign, estimate.parent_axis_sensor),
            ("child", estimate.child, child_sign, estimate.child_axis_sensor),
        ):
            gyro_rms = float(estimate.qmt_axis_report[f"{role}_gyro_rms_rad_s"])
            excitation_report = estimate.qmt_axis_report[
                f"{role}_selected_excitation_report"
            ]
            excitation_qualified = bool(estimate.qmt_axis_report[
                f"{role}_selected_excitation_qualified"
            ])
            if excitation_qualified:
                axis_by_segment[segment] = sign * axis
                source = "QMT_HINGE_AXIS_SELECTED_ROWS_NOISE_STANDARDIZED_QUALIFIED"
            else:
                # The center axis fixes two mount DOF. For an unexcited hinge
                # endpoint, keep the surviving qualitative-sector axial twist
                # instead of promoting a stable-but-unobservable QMT result.
                axis_by_segment[segment] = candidate.body_from_sensor_by_segment[segment][1]
                source = "QUALITATIVE_SECTOR_TWIST_QMT_ENDPOINT_UNEXCITED"
            axis_source_by_segment[segment] = {
                "source": source,
                "endpoint_gyro_rms_rad_s_secondary_diagnostic": gyro_rms,
                "selected_row_noise_standardized_excitation": excitation_report,
                "qualified": excitation_qualified,
                "raw_rms_is_gate": False,
            }

    transforms = {
        segment: np.asarray(matrix, dtype=float).copy()
        for segment, matrix in candidate.body_from_sensor_by_segment.items()
    }
    mount_rows = {}
    for segment in LIMBS:
        z_sensor = _unit(np.sum([_unit(vector) for vector in center_vectors[segment]], axis=0))
        y_sensor = _unit(axis_by_segment[segment])
        y_sensor = _unit(y_sensor - z_sensor * float(y_sensor @ z_sensor))
        x_sensor = _unit(np.cross(y_sensor, z_sensor))
        refined = np.vstack((x_sensor, y_sensor, z_sensor))
        if np.linalg.det(refined) < 0.999:
            raise RuntimeError(f"{segment}: center-derived mount is not right handed")
        transforms[segment] = refined
        mount_rows[segment] = {
            "body_x_axis_in_sensor": x_sensor.tolist(),
            "body_y_axis_in_sensor": y_sensor.tolist(),
            "body_z_axis_in_sensor": z_sensor.tolist(),
            "center_direction_count": len(center_vectors[segment]),
        }

    # Pelvis and torso each have three measured, non-collinear connection
    # vectors, so their complete sensor-to-segment rotations are observable
    # from train-only functional centers via Wahba alignment.
    nominal_offsets, _, _ = geometry.axial_offset_defaults()
    body_points = geometry.edge_points_body(nominal_offsets)
    trunk_connections = {
        "pelvis": (
            ("pelvis_torso", 0), ("hip_left", 0), ("hip_right", 0),
        ),
        "torso": (
            ("pelvis_torso", 1), ("shoulder_left", 0), ("shoulder_right", 0),
        ),
    }
    for segment, connections in trunk_connections.items():
        body_vectors = []
        sensor_vectors = []
        endpoint_kinematic_reports = []
        for edge_name, endpoint in connections:
            body_vectors.append(body_points[edge_name][endpoint])
            sensor_vectors.append(
                lever_by_edge[edge_name][:3] if endpoint == 0
                else lever_by_edge[edge_name][3:]
            )
            edge_blocks = bundle.edges[edge_name].train
            endpoint_kinematic_reports.append(_kinematic_noise_significance(
                edge_blocks,
                endpoint=endpoint,
                sample_period_s=1.0 / float(config["sampling"]["working_rate_hz"]),
            ))
        minimum_trunk_significance = float(
            config["wear_direction"]["minimum_trunk_kinematic_noise_significance"]
        )
        if min(
            row["noise_standardized_significance"]
            for row in endpoint_kinematic_reports
        ) >= minimum_trunk_significance:
            refined, rssd = Rotation.align_vectors(
                np.asarray(body_vectors), np.asarray(sensor_vectors),
            )
            transforms[segment] = refined.as_matrix()
            method = "TRAIN_ONLY_THREE_CONNECTION_WAHBA"
            distance = float(rssd)
        else:
            method = "QUALITATIVE_SECTOR_ENDPOINT_KINEMATICS_UNEXCITED"
            distance = None
        mount_rows[segment] = {
            "method": method,
            "connection_edges": [name for name, _ in connections],
            "endpoint_kinematic_noise_standardized": endpoint_kinematic_reports,
            "minimum_kinematic_noise_significance": minimum_trunk_significance,
            "raw_kinematic_rms_is_gate": False,
            "weighted_root_sum_squared_distance_m": distance,
        }

    lengths = {segment: geometry.segments[segment].value_m for segment in LIMBS}
    offset = {}
    mismatch = {}
    for segment, proximal in (
        ("upper_arm_left", lever_by_edge["shoulder_left"][3:]),
        ("upper_arm_right", lever_by_edge["shoulder_right"][3:]),
        ("thigh_left", lever_by_edge["hip_left"][3:]),
        ("thigh_right", lever_by_edge["hip_right"][3:]),
    ):
        distal_edge = {
            "upper_arm_left": "elbow_left", "upper_arm_right": "elbow_right",
            "thigh_left": "knee_left", "thigh_right": "knee_right",
        }[segment]
        distal = lever_by_edge[distal_edge][:3]
        offset[segment] = 0.5 * (
            float(np.linalg.norm(distal))
            + lengths[segment] - float(np.linalg.norm(proximal))
        )
        mismatch[segment] = abs(
            float(np.linalg.norm(proximal) + np.linalg.norm(distal)) - lengths[segment]
        )
    for segment, edge_name in (
        ("forearm_left", "elbow_left"), ("forearm_right", "elbow_right"),
        ("shank_left", "knee_left"), ("shank_right", "knee_right"),
    ):
        offset[segment] = lengths[segment] - float(np.linalg.norm(lever_by_edge[edge_name][3:]))
        mismatch[segment] = 0.0
    nominal, lower, upper = geometry.axial_offset_defaults()
    offset_vector = np.asarray([offset[segment] for segment in LIMBS])
    offset_gate = bool(np.all((offset_vector >= lower) & (offset_vector <= upper)))
    length_gate = max(mismatch.values()) <= float(
        config["wear_direction"]["maximum_functional_center_length_mismatch_m"]
    )
    segment_to_node = {segment: node for node, segment in node_to_segment.items()}
    by_node = {segment_to_node[segment]: matrix for segment, matrix in transforms.items()}
    wear = wear_direction_report(by_node)
    base_branch = next(
        branch for branch in candidate_bank()
        if branch.branch_id == candidate.base_mount_branch
    )
    base_by_segment = {
        node_to_segment[node]: matrix for node, matrix in base_branch.body_from_sensor.items()
    }
    corrections = {
        segment: float(np.degrees(Rotation.from_matrix(
            transforms[segment] @ base_by_segment[segment].T
        ).magnitude()))
        for segment in transforms
    }
    correction_gate = max(corrections.values()) <= float(
        config["wear_direction"]["maximum_final_mount_correction_deg"]
    )
    remaining = {
        segment: float(np.degrees(np.arccos(np.clip(
            (transforms[segment] @ axis_by_segment[segment]) @ np.array([0.0, 1.0, 0.0]),
            -1.0, 1.0,
        ))))
        for segment in LIMBS
    }
    gates = {
        "center_profile_rank": True,
        "fixed_length_connection_consistency": length_gate,
        "axial_offsets_inside_preregistered_bounds": offset_gate,
        "immutable_wear_metadata": wear["hard_pass"],
        "mount_correction_bound": correction_gate,
    }
    if not all(gates.values()):
        raise RuntimeError(f"center-derived functional candidate failed gates: {gates}")
    refined_candidate = replace(
        candidate,
        candidate_id=candidate.candidate_id + "_CENTER_PROFILED",
        body_from_sensor_by_segment=transforms,
        wear_report=wear,
        correction_angles_deg=corrections,
        remaining_axis_misalignment_deg=remaining,
        functional_alignment_fraction=1.0,
    )
    return CenterRefinement(refined_candidate, offset_vector, {
        "schema": "biospur-c2-functional-center-refinement-v1",
        "edge_profiles": edge_report,
        "mount_axes": mount_rows,
        "hinge_axis_source_by_segment": axis_source_by_segment,
        "axial_offset_seed_m": dict(zip(LIMBS, offset_vector.tolist())),
        "fixed_length_mismatch_m": mismatch,
        "gates": gates,
        "pass": True,
        "held_out_used_to_fit": False,
    })


def profile_joint_centers(
    bundle: FactorBundle, state: np.ndarray,
) -> tuple[Mapping[str, np.ndarray], Mapping[str, Any]]:
    """Profile edge-local Seel lever pairs once for all sign/mount branches."""

    value = np.asarray(state, dtype=float)
    if value.shape != (17,):
        raise ValueError("center profiling requires the complete C2 state")
    heading = {"pelvis": 0.0, **dict(zip(SEGMENTS[1:], value[:9]))}
    lever_by_edge: dict[str, np.ndarray] = {}
    edge_report: dict[str, Any] = {}
    for edge_name, edge in bundle.edges.items():
        delta = float(
            (heading[edge.child] - heading[edge.parent] + np.pi) % (2.0 * np.pi) - np.pi
        )
        lever, residual, profile = profile_b5(edge.train, delta)
        if profile["lever_rank"] < 6 or not np.isfinite(lever).all():
            raise RuntimeError(f"{edge_name}: functional center profile is rank deficient")
        lever_by_edge[edge_name] = lever
        edge_report[edge_name] = {
            "relative_heading_deg": float(np.degrees(delta)),
            "lever_parent_m": lever[:3].tolist(),
            "lever_child_m": lever[3:].tolist(),
            "train_weighted_rms": float(np.sqrt(np.mean(residual * residual))),
            "profile": profile,
            "held_out_prediction": evaluate_b5(edge.held_out, delta, lever),
        }
    return lever_by_edge, edge_report
