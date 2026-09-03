"""Synthetic-only S0/S1 structural analysis for the torso ambiguity.

No function in this module accepts a real-capture path.  It operates only on
the deterministic in-memory synthetic problem.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .core import SEGMENTS, axis_angle_rad
from .synthetic import (
    ACTIONS, HINGES, OracleSyntheticObservabilityProblem,
    generate_synthetic_dataset,
)


Z_H = np.array([0.0, 0.0, 1.0])


def huber_cost(residual: np.ndarray, f_scale: float) -> float:
    magnitude = np.abs(np.asarray(residual, float))
    rho = np.where(magnitude <= f_scale, magnitude*magnitude,
                   2.0*f_scale*magnitude-f_scale*f_scale)
    return float(0.5*np.sum(rho))


class TorsoGaugeAudit:
    def __init__(self, gates: Mapping[str, Any], repair_gates: Mapping[str, Any],
                 template: Mapping[str, Any]):
        self.gates = gates
        self.repair_gates = repair_gates
        self.template = template
        self.dataset, self.truth = generate_synthetic_dataset(gates)
        self.problem = OracleSyntheticObservabilityProblem(
            self.dataset, self.truth, gates
        )
        self.zero = np.zeros(self.problem.parameter_count)
        initial_start = self.dataset.action_windows["initial_still_attempt2"][0]
        stream = self.dataset.nodes[self.problem.segment_nodes["torso"]]
        index = int(np.searchsorted(stream.time_ns, initial_start, side="left"))
        self.reference_time_ns = int(stream.time_ns[index])
        self.R_N_torso_from_B_torso_reference = stream.R_N_i_from_B_i[index]
        # heading increases by +alpha, so the board-frame axes rotate about
        # -R_B_from_N*z_N to preserve their H vectors at the reference pose.
        self.torso_board_rotation_axis_per_heading = (
            -self.R_N_torso_from_B_torso_reference.T @ Z_H
        )
        self.parameter_direction = np.zeros(self.problem.parameter_count)
        self.parameter_direction[self.problem.slices["frame:torso"]] = (
            self.torso_board_rotation_axis_per_heading
        )
        self.parameter_direction[self.problem.slices["heading:torso"]] = 1.0

    def transform_value(self, alpha_rad: float) -> np.ndarray:
        value = self.zero.copy()
        value[self.problem.slices["frame:torso"]] = (
            float(alpha_rad)*self.torso_board_rotation_axis_per_heading
        )
        value[self.problem.slices["heading:torso"]] = float(alpha_rad)
        return value

    def analytic_transform_manifest(self) -> dict:
        q = Rotation.from_matrix(
            self.R_N_torso_from_B_torso_reference
        ).as_quat().tolist()  # scipy xyzw
        return {
            "schema": "biospur-torso-finite-transform-v1",
            "candidate_only_until_product_invariance_passes": True,
            "reference_time_ns": self.reference_time_ns,
            "active_rotation_convention": "R_DST_from_SRC maps SRC coordinates into DST",
            "quaternion_convention": "Hamilton active; serialized order x,y,z,w; left factor acts last on a vector",
            "R_N_torso_from_B_torso_reference_xyzw": q,
            "heading_update": "psi_torso_prime = psi_torso + alpha",
            "board_frame_update": (
                "C_B_prime = G_B(alpha) C_B, where G_B(alpha) = "
                "R_N_from_B_ref^T Rz_N(-alpha) R_N_from_B_ref"
            ),
            "quaternion_board_frame_update": (
                "q_G_B(alpha) = inverse(q_N_from_B_ref) tensor "
                "q_z_N(-alpha) tensor q_N_from_B_ref; "
                "q_C_B_prime = q_G_B(alpha) tensor q_C_B"
            ),
            "output_composition": (
                "R_H_from_B_prime(t) = Rz_H(psi_torso(t)+alpha) "
                "R_N_torso_from_B_torso(t)"
            ),
            "infinitesimal_parameter_direction": {
                "frame_torso_rotvec_rad_per_alpha": self.torso_board_rotation_axis_per_heading.tolist(),
                "heading_torso_rad_per_alpha": 1.0,
                "all_other_parameters": 0.0,
            },
            "sign_check_at_reference": (
                "Rz_H(+alpha) R_N_from_B_ref G_B(alpha) = "
                "R_N_from_B_ref exactly up to floating-point roundoff"
            ),
        }

    def _torso_vector_derivative(self, times: np.ndarray,
                                 vector_B: np.ndarray) -> np.ndarray:
        _, _, _, _, _, heading, delta = self.problem.unpack(self.zero)
        R = self.problem.R_H_from_B("torso", times, heading, delta)
        predicted = np.einsum("nij,j->ni", R, vector_B)
        local_derivative = np.cross(
            np.broadcast_to(self.torso_board_rotation_axis_per_heading,
                            (len(times), 3)),
            np.broadcast_to(vector_B, (len(times), 3)),
        )
        return (np.cross(np.broadcast_to(Z_H, predicted.shape), predicted)
                + np.einsum("nij,nj->ni", R, local_derivative))

    def analytic_residual_directional_derivative(self) -> np.ndarray:
        sigma = float(self.gates["noise_floors"]["orientation_sigma_rad"])
        pieces = []
        for action, factor, rows in self.problem.residual_blocks(self.zero):
            derivative = np.zeros_like(rows)
            if factor in ("static_segment_direction:torso",
                          "transverse_direction:torso"):
                times = self.problem.refine_times[action]
                pick = np.linspace(0, len(times)-1, 125).round().astype(int)
                times = times[pick]
                vector = (self.truth.a_B["torso"] if
                          factor.startswith("static_segment") else
                          self.truth.transverse_B["torso"])
                derivative = np.ravel(
                    self._torso_vector_derivative(times, vector)/sigma
                )
            elif factor == "pelvis_torso_transverse_endpoint_consistency":
                times = self.problem.refine_times["trunk"][[0, -1]]
                derivative = np.ravel(
                    -self._torso_vector_derivative(
                        times, self.truth.transverse_B["torso"]
                    )/sigma
                )
            pieces.append(derivative)
        return np.concatenate(pieces)

    def product_outputs(self, value: np.ndarray) -> dict[str, dict[str, Any]]:
        a, b, hp, hc, _, heading, delta = self.problem.unpack(value)
        dims = self.template["dimensions"]
        outputs = {}
        for action in ACTIONS:
            times = self.problem.refine_times[action]
            axes = {}
            rotations = {}
            for segment in SEGMENTS:
                rotations[segment] = self.problem.R_H_from_B(
                    segment, times, heading, delta
                )
                axes[segment] = np.einsum(
                    "nij,j->ni", rotations[segment], a[segment]
                )
            transverse = {
                segment: np.einsum(
                    "nij,j->ni", rotations[segment], b[segment]
                )
                for segment in ("pelvis", "torso")
            }
            count = len(times)
            pelvis = np.zeros((count, 3))
            c7 = pelvis + float(dims["C7Proxy_to_PelvisProxy_m"])*axes["torso"]
            joints = {"PelvisProxy": pelvis, "C7Proxy": c7, "Central": c7}
            for side, sign in (("L", 1.0), ("R", -1.0)):
                shoulder = c7 + sign*0.5*float(
                    dims["graphical_shoulder_width_m"]
                )*transverse["torso"]
                elbow = shoulder + float(
                    dims[f"rendering_upper_arm_length_{side}_m"]
                )*axes[f"upper_arm_{side}"]
                wrist = elbow + float(
                    dims[f"rendering_forearm_length_{side}_m"]
                )*axes[f"forearm_{side}"]
                hip = pelvis + sign*0.5*float(
                    dims["graphical_hip_width_m"]
                )*transverse["pelvis"]
                knee = hip + float(
                    dims[f"rendering_thigh_length_{side}_m"]
                )*axes[f"thigh_{side}"]
                ankle = knee + float(
                    dims[f"rendering_shank_length_{side}_m"]
                )*axes[f"shank_{side}"]
                joints.update({
                    f"ShoulderProxy_{side}": shoulder,
                    f"Elbow_{side}": elbow,
                    f"Wrist_{side}": wrist,
                    f"HipProxy_{side}": hip,
                    f"Knee_{side}": knee,
                    f"Ankle_{side}": ankle,
                })
            hinges = {}
            for joint, (parent, child, _) in HINGES.items():
                hinges[joint] = {
                    "parent": np.einsum(
                        "nij,j->ni", rotations[parent], hp[joint]
                    ),
                    "child": np.einsum(
                        "nij,j->ni", rotations[child], hc[joint]
                    ),
                }
            outputs[action] = {
                "time_ns": times,
                "segment_axes": axes,
                "graphical_joint_nodes_m": joints,
                "hinge_predictions": hinges,
            }
        return outputs

    @staticmethod
    def _maximum_vector_angle(left: np.ndarray, right: np.ndarray) -> float:
        # atan2(||a x b||, a.b) is well conditioned near zero; arccos(a.b)
        # would report spurious ~1e-8 rad changes for byte-identical unit axes.
        cross = np.linalg.norm(np.cross(left, right), axis=1)
        dots = np.sum(left*right, axis=1)
        return float(np.max(np.arctan2(cross, dots)))

    def compare_products(self, base: Mapping[str, Any],
                         moved: Mapping[str, Any]) -> dict:
        per_action = {}
        global_axis = 0.0
        global_joint = 0.0
        global_hinge = 0.0
        for action in ACTIONS:
            axes = {}
            for segment in SEGMENTS:
                change = self._maximum_vector_angle(
                    base[action]["segment_axes"][segment],
                    moved[action]["segment_axes"][segment],
                )
                axes[segment] = change
                global_axis = max(global_axis, change)
            joints = {}
            for node, before in base[action]["graphical_joint_nodes_m"].items():
                change = float(np.max(np.linalg.norm(
                    moved[action]["graphical_joint_nodes_m"][node]-before,
                    axis=1,
                )))
                joints[node] = change
                global_joint = max(global_joint, change)
            hinges = {}
            for joint in HINGES:
                parent = self._maximum_vector_angle(
                    base[action]["hinge_predictions"][joint]["parent"],
                    moved[action]["hinge_predictions"][joint]["parent"],
                )
                child = self._maximum_vector_angle(
                    base[action]["hinge_predictions"][joint]["child"],
                    moved[action]["hinge_predictions"][joint]["child"],
                )
                hinges[joint] = {"parent_angle_rad": parent,
                                 "child_angle_rad": child}
                global_hinge = max(global_hinge, parent, child)
            per_action[action] = {
                "maximum_segment_axis_angle_rad": max(axes.values()),
                "maximum_graphical_joint_displacement_m": max(joints.values()),
                "maximum_hinge_prediction_angle_rad": max(
                    max(x.values()) for x in hinges.values()
                ),
                "segment_axis_angle_rad": axes,
                "graphical_joint_displacement_m": joints,
                "hinge_prediction_angle_rad": hinges,
            }
        return {
            "maximum_segment_axis_angle_rad": global_axis,
            "maximum_graphical_joint_displacement_m": global_joint,
            "maximum_hinge_prediction_angle_rad": global_hinge,
            "per_action": per_action,
        }

    def finite_transform_scan(self) -> dict:
        base_blocks = self.problem.residual_blocks(self.zero)
        base_residual = self.problem.residual(self.zero)
        base_outputs = self.product_outputs(self.zero)
        f_scale = float(self.gates["optimizer"]["f_scale"])
        base_ls = float(0.5*np.sum(base_residual*base_residual))
        base_huber = huber_cost(base_residual, f_scale)
        tolerances = self.repair_gates["finite_transform_invariance_tolerances"]
        rows = []
        for alpha in self.repair_gates["candidate_transform_alpha_rad"]:
            moved_value = self.transform_value(float(alpha))
            moved_blocks = self.problem.residual_blocks(moved_value)
            moved_residual = self.problem.residual(moved_value)
            per_block = []
            action_accumulator: dict[str, list[np.ndarray]] = {}
            action_base: dict[str, list[np.ndarray]] = {}
            action_moved: dict[str, list[np.ndarray]] = {}
            for (action0, factor0, before), (action1, factor1, after) in zip(
                    base_blocks, moved_blocks, strict=True):
                if (action0, factor0) != (action1, factor1):
                    raise RuntimeError("residual block ordering changed")
                delta = after-before
                per_block.append({
                    "action": action0,
                    "factor": factor0,
                    "rows": int(len(before)),
                    "maximum_absolute_delta": float(np.max(np.abs(delta))) if len(delta) else 0.0,
                    "l2_delta": float(np.linalg.norm(delta)),
                    "base_least_squares_cost": float(0.5*np.sum(before*before)),
                    "transformed_least_squares_cost": float(0.5*np.sum(after*after)),
                    "base_huber_cost": huber_cost(before, f_scale),
                    "transformed_huber_cost": huber_cost(after, f_scale),
                })
                action_accumulator.setdefault(action0, []).append(delta)
                action_base.setdefault(action0, []).append(before)
                action_moved.setdefault(action0, []).append(after)
            per_action_residual = {}
            for action, pieces in action_accumulator.items():
                delta = np.concatenate(pieces)
                before = np.concatenate(action_base[action])
                after = np.concatenate(action_moved[action])
                per_action_residual[action] = {
                    "rows": int(len(delta)),
                    "maximum_absolute_delta": float(np.max(np.abs(delta))),
                    "l2_delta": float(np.linalg.norm(delta)),
                    "base_least_squares_cost": float(0.5*np.sum(before*before)),
                    "transformed_least_squares_cost": float(0.5*np.sum(after*after)),
                    "base_huber_cost": huber_cost(before, f_scale),
                    "transformed_huber_cost": huber_cost(after, f_scale),
                }
            moved_ls = float(0.5*np.sum(moved_residual*moved_residual))
            moved_huber = huber_cost(moved_residual, f_scale)
            product = self.compare_products(
                base_outputs, self.product_outputs(moved_value)
            )
            residual_max = float(np.max(np.abs(moved_residual-base_residual)))
            cost_relative = max(
                abs(moved_ls-base_ls)/max(abs(base_ls), 1.0),
                abs(moved_huber-base_huber)/max(abs(base_huber), 1.0),
            )
            residual_invariant = (
                residual_max <= float(tolerances["residual_max_abs"])
                and cost_relative <= float(tolerances["total_cost_relative"])
            )
            product_invariant = (
                product["maximum_segment_axis_angle_rad"] <=
                float(tolerances["segment_axis_angle_rad"])
                and product["maximum_graphical_joint_displacement_m"] <=
                float(tolerances["graphical_joint_displacement_m"])
                and product["maximum_hinge_prediction_angle_rad"] <=
                float(tolerances["hinge_prediction_angle_rad"])
            )
            rows.append({
                "alpha_rad": float(alpha),
                "residual": {
                    "maximum_absolute_delta": residual_max,
                    "l2_delta": float(np.linalg.norm(moved_residual-base_residual)),
                    "least_squares_cost": moved_ls,
                    "huber_cost": moved_huber,
                    "maximum_relative_cost_delta": cost_relative,
                    "per_action": per_action_residual,
                    "per_block": per_block,
                    "invariant": residual_invariant,
                },
                "products": product,
                "products_invariant": product_invariant,
                "candidate_is_gauge_at_this_alpha": residual_invariant and product_invariant,
            })
        all_residual = all(row["residual"]["invariant"] for row in rows)
        all_products = all(row["products_invariant"] for row in rows)
        return {
            "schema": "biospur-torso-finite-transform-scan-v1",
            "tolerances": tolerances,
            "base_least_squares_cost": base_ls,
            "base_huber_cost": base_huber,
            "rows": rows,
            "all_residual_vectors_and_costs_invariant": all_residual,
            "all_publishable_centerline_products_invariant": all_products,
            "analytic_gauge_proven": all_residual and all_products,
            "verdict": ("LEGAL_ANALYTIC_GAUGE" if all_residual and all_products
                        else "NOT_A_PRODUCT_GAUGE"),
        }

    def jacobian_audit(self) -> tuple[np.ndarray, dict]:
        J = self.problem.numerical_jacobian(self.zero)
        _, singular, Vh = np.linalg.svd(J, full_matrices=False)
        candidate_v = self.parameter_direction
        analytic = self.analytic_residual_directional_derivative()
        parameter_jv = J @ candidate_v
        step = float(self.repair_gates["directional_derivative"]["central_difference_step_rad"])
        central = (
            self.problem.residual(self.transform_value(step))
            - self.problem.residual(self.transform_value(-step))
        )/(2.0*step)
        relative_gate = float(
            self.repair_gates["observability_gate_relative_singular_value_threshold"]
        )
        absolute_gate = float(singular[0]*relative_gate)
        rank = int(np.sum(singular > absolute_gate))
        null = Vh[-1]
        if float(null@candidate_v) < 0.0:
            null = -null
        normalized_candidate = candidate_v/np.linalg.norm(candidate_v)
        blocks = self.parameter_blocks()
        block_energy = {}
        for name, columns in blocks.items():
            block_energy[name] = float(np.sum(null[columns]*null[columns]))
        components = [
            {"index": index, "name": name, "coefficient": float(null[index])}
            for index, name in enumerate(self.problem.parameter_names)
        ]
        column_norms = np.linalg.norm(J, axis=0)
        threshold_sweep = []
        for relative in self.repair_gates["diagnostic_rank_threshold_sweep"]:
            absolute = float(singular[0]*float(relative))
            sweep_rank = int(np.sum(singular > absolute))
            threshold_sweep.append({
                "relative_threshold": float(relative),
                "absolute_threshold": absolute,
                "rank": sweep_rank,
                "nullity": self.problem.parameter_count-sweep_rank,
            })
        count = int(self.repair_gates["bottom_singular_spectrum_count"])
        report = {
            "schema": "biospur-s0-scaled-jacobian-audit-v1",
            "jacobian_shape": [int(x) for x in J.shape],
            "jacobian_method": "CENTRAL_FINITE_DIFFERENCE_OF_ACTUAL_WHITENED_RESIDUAL",
            "analytic_directional_derivative_method": "CLOSED_FORM_SO3_PRODUCT_RULE_FOR_T_ALPHA",
            "parameter_direction_source": "ANALYTIC_DERIVATIVE_OF_FINITE_TRANSFORM_T_ALPHA_AT_ZERO",
            "sigma_max": float(singular[0]),
            "sigma_370": float(singular[-2]),
            "sigma_371": float(singular[-1]),
            "bottom_singular_values": singular[-count:].tolist(),
            "spectral_gap_sigma_370_over_sigma_371": float(singular[-2]/singular[-1]),
            "relative_rank_threshold": relative_gate,
            "absolute_rank_threshold": absolute_gate,
            "rank": rank,
            "nullity": self.problem.parameter_count-rank,
            "rank_threshold_sweep": threshold_sweep,
            "candidate_direction_vs_svd_null_absolute_cosine": float(abs(normalized_candidate@null)),
            "directional_derivative_crosscheck": {
                "analytic_l2": float(np.linalg.norm(analytic)),
                "parameter_central_jacobian_times_v_l2": float(np.linalg.norm(parameter_jv)),
                "finite_transform_central_difference_l2": float(np.linalg.norm(central)),
                "analytic_vs_parameter_jacobian_max_abs": float(np.max(np.abs(analytic-parameter_jv))),
                "analytic_vs_finite_transform_central_max_abs": float(np.max(np.abs(analytic-central))),
                "parameter_jacobian_vs_finite_transform_central_max_abs": float(np.max(np.abs(parameter_jv-central))),
            },
            "actual_null_vector_all_components": components,
            "actual_null_vector_block_energy": block_energy,
            "column_scaling": {
                "optimizer_x_scale": float(self.gates["optimizer"]["x_scale"]),
                "all_parameter_units": "radian or radian tangent coordinate",
                "per_column_l2_norm": [
                    {"index": i, "name": self.problem.parameter_names[i],
                     "norm": float(column_norms[i])}
                    for i in range(self.problem.parameter_count)
                ],
            },
            "residual_whitening": {
                "orientation_sigma_rad": float(self.gates["noise_floors"]["orientation_sigma_rad"]),
                "gyro_sigma_rad_s_measured_with_floor": float(self.problem.noise["gyro_sigma_rad_s"]),
                "accel_sigma_mps2_measured_with_floor": float(self.problem.noise["accel_sigma_mps2"]),
                "yaw_random_walk_floor_rad_sqrt_s": float(self.gates["noise_floors"]["yaw_random_walk_sigma_rad_sqrt_s"]),
                "robust_loss": self.gates["optimizer"]["loss"],
                "huber_f_scale": float(self.gates["optimizer"]["f_scale"]),
            },
            "parameter_units": self.parameter_unit_manifest(),
        }
        return J, report

    def parameter_blocks(self) -> dict[str, np.ndarray]:
        blocks = {}
        for key, slc in self.problem.slices.items():
            blocks[key] = np.arange(slc.start, slc.stop, dtype=int)
        return blocks

    def parameter_unit_manifest(self) -> list[dict]:
        rows = []
        for name, columns in self.parameter_blocks().items():
            rows.append({
                "block": name,
                "columns": columns.tolist(),
                "unit": "rad",
                "meaning": (
                    "unit-vector tangent rotation" if name.startswith(("axis:", "hinge:"))
                    else "SO3 rotvec" if name.startswith("frame:")
                    else "joint zero angle" if name == "zeros"
                    else "relative heading" if name.startswith("heading:")
                    else "yaw spline delta"
                ),
            })
        return rows

    def sensitivity_and_ablation(self, J: np.ndarray,
                                 jacobian_report: Mapping[str, Any]) -> tuple[dict, dict]:
        blocks = self.problem.residual_blocks(self.zero)
        parameter_blocks = self.parameter_blocks()
        matrix = []
        action_rows: dict[str, list[int]] = {}
        cursor = 0
        for action, factor, residual in blocks:
            rows = np.arange(cursor, cursor+len(residual), dtype=int)
            cursor += len(residual)
            action_rows.setdefault(action, []).extend(rows.tolist())
            sensitivity = {}
            for block_name, columns in parameter_blocks.items():
                value = float(np.linalg.norm(J[np.ix_(rows, columns)], ord="fro"))
                sensitivity[block_name] = value
            matrix.append({
                "action": action,
                "residual_factor": factor,
                "residual_rows": int(len(rows)),
                "parameter_block_frobenius_sensitivity": sensitivity,
                "nonzero_parameter_blocks": [
                    name for name, value in sensitivity.items() if value > 1e-12
                ],
            })
        action_summary = {}
        for action in ACTIONS:
            rows = np.asarray(action_rows.get(action, []), dtype=int)
            sensitivity = {}
            for block_name, columns in parameter_blocks.items():
                sensitivity[block_name] = (
                    float(np.linalg.norm(J[np.ix_(rows, columns)], ord="fro"))
                    if len(rows) else 0.0
                )
            declared_estimation = action not in ("left_heel", "right_heel")
            meaningful = any(value > 1e-12 for value in sensitivity.values())
            action_summary[action] = {
                "residual_rows": int(len(rows)),
                "declared_role": ("CALIBRATION" if declared_estimation
                                  else "VALIDATION_ONLY_UNSUPPORTED_FOOT_DOF"),
                "parameter_block_frobenius_sensitivity": sensitivity,
                "has_nonzero_static_parameter_information": meaningful,
                "declared_action_unused_failure": declared_estimation and not meaningful,
            }
        sensitivity_report = {
            "schema": "biospur-action-residual-parameter-sensitivity-v1",
            "entries": matrix,
            "action_summary": action_summary,
            "declared_action_unused": [
                action for action, value in action_summary.items()
                if value["declared_action_unused_failure"]
            ],
        }

        full_sigma_max = float(jacobian_report["sigma_max"])
        gate = float(jacobian_report["relative_rank_threshold"])
        ablations = []
        all_rows = np.arange(J.shape[0], dtype=int)
        for action in ACTIONS:
            removed = np.asarray(action_rows.get(action, []), dtype=int)
            keep = np.setdiff1d(all_rows, removed, assume_unique=False)
            singular = np.linalg.svd(J[keep], compute_uv=False)
            absolute = float(singular[0]*gate)
            rank = int(np.sum(singular > absolute))
            ablations.append({
                "removed_action": action,
                "removed_rows": int(len(removed)),
                "remaining_rows": int(len(keep)),
                "sigma_max": float(singular[0]),
                "weakest_singular_value": float(singular[-1]),
                "rank": rank,
                "nullity": self.problem.parameter_count-rank,
                "relative_threshold": gate,
                "absolute_threshold": absolute,
                "full_sigma_max_reference": full_sigma_max,
            })
        return sensitivity_report, {
            "schema": "biospur-synthetic-action-ablation-v1",
            "method": "REMOVE_EXACT_ACTION_RESIDUAL_ROWS_FROM_UNCHANGED_FULL_JACOBIAN",
            "ablations": ablations,
        }


def run_s0_s1_structural_repair(
    gates: Mapping[str, Any], repair_gates: Mapping[str, Any],
    template: Mapping[str, Any],
) -> dict[str, Any]:
    audit = TorsoGaugeAudit(gates, repair_gates, template)
    transform = audit.analytic_transform_manifest()
    scan = audit.finite_transform_scan()
    J, jacobian = audit.jacobian_audit()
    sensitivity, ablation = audit.sensitivity_and_ablation(J, jacobian)
    analytic_limits = repair_gates["directional_derivative"]
    crosscheck = jacobian["directional_derivative_crosscheck"]
    derivative_pass = (
        crosscheck["analytic_vs_finite_transform_central_max_abs"] <=
        float(analytic_limits["analytic_vs_central_max_abs"])
        and crosscheck["analytic_vs_parameter_jacobian_max_abs"] <=
        float(analytic_limits["analytic_vs_parameter_jacobian_max_abs"])
    )
    legal_gauge = bool(scan["analytic_gauge_proven"] and derivative_pass)
    if legal_gauge:
        phase = "S1_REPARAMETERIZATION_REQUIRED_NOT_YET_EXECUTED"
        verdict = "S0_LEGAL_GAUGE_PROVEN"
    else:
        phase = "STOPPED_AFTER_S0_AS_REQUIRED"
        verdict = "FAIL_MULTI_ACTION_NULLSPACE"
    return {
        "verdict": verdict,
        "phase_status": phase,
        "candidate_nullspace_is_analytic_product_gauge": legal_gauge,
        "transform": transform,
        "finite_transform_scan": scan,
        "jacobian_audit": jacobian,
        "action_parameter_sensitivity": sensitivity,
        "action_ablation": ablation,
        "repair_before_after": {
            "before": {
                "parameter_count": audit.problem.parameter_count,
                "rank": jacobian["rank"],
                "nullity": jacobian["nullity"],
                "torso_state": ["frame:torso SO3 3-DOF", "heading:torso 1-DOF"],
                "parameter_table": audit.parameter_unit_manifest(),
            },
            "after": {
                "status": "NO_REPAIR_APPLIED_BECAUSE_CANDIDATE_FAILED_PRODUCT_INVARIANCE",
                "parameter_count": audit.problem.parameter_count,
                "rank": jacobian["rank"],
                "nullity": jacobian["nullity"],
                "torso_state": ["frame:torso SO3 3-DOF", "heading:torso 1-DOF"],
                "parameter_table": "IDENTICAL_TO_BEFORE",
            },
        },
        "synthetic_recovery": "NOT_RERUN_AFTER_REPARAMETERIZATION_BECAUSE_S1_FORBIDDEN",
        "five_start_shared_multistart": "NOT_RUN_BECAUSE_S0_DID_NOT_AUTHORIZE_S1",
        "double_replay_determinism": "TO_BE_CHECKED_BY_RUNNER_FOR_COMPLETE_S0_ARTIFACT",
        "real_data_status": "ALL_REAL_AND_HELD_OUT_INPUTS_REMAIN_SEALED",
    }
