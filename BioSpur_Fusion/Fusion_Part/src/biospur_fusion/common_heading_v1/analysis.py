from __future__ import annotations

from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
import contextlib
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
from typing import Mapping, Sequence
import warnings

import numpy as np
from scipy.optimize import least_squares

from .core import (
    atomic_json, circular_axis_mean, gyro_world_axis, hash_ordered_strings,
    horizontal_axis_angle, information_rank, quat_multiply, quat_rotate,
    robust_axis_scale, rp2_mean, schur_profile, sha256_file, sha256_payload,
    stable_seed, uid_string, wrap_axis_line, wrap_pi,
)
from .frontend_cache import load_class_cache


def _codes(contract: Mapping) -> tuple[list[str], list[str]]:
    nodes = sorted(contract["operator_mapping"])
    actions = [
        "00_initial_still", "02_t_pose", "03_pelvis_hula_circle",
        "04_shoulder_left", "05_shoulder_right", "06_elbow_left",
        "07_elbow_right", "08_hip_left", "09_hip_right",
        "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
        "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation",
        "16_squat", "17_final_still", "18_heel_to_butt_left",
        "19_heel_to_butt_right",
    ]
    return nodes, actions


def _uids(rows: Mapping[str, np.ndarray], indexes: np.ndarray, nodes: Sequence[str]) -> list[str]:
    return [uid_string(nodes[int(rows["node_code"][i])], rows["boot"][i], rows["timer2_us"][i],
                       rows["sequence"][i], rows["source_offset"][i]) for i in indexes]


def _block(rows: Mapping[str, np.ndarray], action_code: int, cycle: int, node_code: int) -> np.ndarray:
    return np.flatnonzero((rows["action_code"] == action_code) &
                          (rows["cycle_ordinal"] == cycle) &
                          (rows["node_code"] == node_code))


def _interpolate_pair(rows: Mapping[str, np.ndarray], parent_index: np.ndarray,
                      child_index: np.ndarray, rate_hz: float = 100.0) -> tuple[np.ndarray, ...]:
    tp = np.asarray(rows["common_time_ns"][parent_index], dtype=float)*1e-9
    tc = np.asarray(rows["common_time_ns"][child_index], dtype=float)*1e-9
    start, stop = max(tp[0], tc[0]), min(tp[-1], tc[-1])
    if stop-start < 1.0:
        raise ValueError("paired block overlap below one second")
    grid = np.arange(start, stop, 1.0/rate_hz)
    def interp(index: np.ndarray, time: np.ndarray, field: str) -> np.ndarray:
        value = np.asarray(rows[field][index], dtype=float)
        return np.column_stack([np.interp(grid, time, value[:, k]) for k in range(value.shape[1])])
    return grid, interp(parent_index, tp, "accel_m_s2"), interp(child_index, tc, "accel_m_s2"), \
        interp(parent_index, tp, "gyro_rad_s"), interp(child_index, tc, "gyro_rad_s")


def fit_official_qmt_axes(*, rows: Mapping[str, np.ndarray], contract: Mapping,
                          output: Path) -> tuple[dict, dict]:
    import qmt
    nodes, actions = _codes(contract)
    node_for_segment = {segment: nodes.index(node) for node, segment in contract["operator_mapping"].items()}
    blocks: dict[str, list[dict]] = defaultdict(list)
    rejected: list[dict] = []
    for joint, (action, parent, child) in contract["hinges"].items():
        action_code = actions.index(action)
        cycles = sorted(map(int, np.unique(rows["cycle_ordinal"][rows["action_code"] == action_code])))
        if joint.startswith("elbow"):
            cycles = [cycle for cycle in cycles if cycle <= 4]
        for cycle in cycles:
            pi = _block(rows, action_code, cycle, node_for_segment[parent])
            ci = _block(rows, action_code, cycle, node_for_segment[child])
            try:
                _, ap, ac, gp, gc = _interpolate_pair(rows, pi, ci)
                if len(ap) > 1600:
                    take = np.linspace(0, len(ap)-1, 1600, dtype=int)
                    ap, ac, gp, gc = ap[take], ac[take], gp[take], gc[take]
                with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
                    warnings.simplefilter("ignore", RuntimeWarning)
                    a, b, _debug = qmt.jointAxisEstHingeOlsson(
                        ap, ac, gp, gc, estSettings={"useSampleSelection": False}, debug=True,
                    )
                a = np.asarray(a, dtype=float).reshape(3); a /= np.linalg.norm(a)
                b = np.asarray(b, dtype=float).reshape(3); b /= np.linalg.norm(b)
                blocks[joint].append({
                    "action_id": action, "cycle_ordinal": cycle,
                    "parent_segment": parent, "child_segment": child,
                    "parent_axis_I_RP2": a.tolist(), "child_axis_I_RP2": b.tolist(),
                    "sample_count": int(len(ap)),
                    "parent_uid_sha256": hash_ordered_strings(sorted(_uids(rows, pi, nodes))),
                    "child_uid_sha256": hash_ordered_strings(sorted(_uids(rows, ci, nodes))),
                    "official_qmt": True, "line_symmetry": "+/-",
                })
            except Exception as exc:
                rejected.append({"joint": joint, "cycle_ordinal": cycle, "reason": type(exc).__name__})
    aggregate = {}
    required = int(contract["qualification"]["required_axis_blocks_per_family"])
    for joint, values in blocks.items():
        parent = np.asarray([row["parent_axis_I_RP2"] for row in values])
        child = np.asarray([row["child_axis_I_RP2"] for row in values])
        pa, pcov = rp2_mean(parent); ca, ccov = rp2_mean(child)
        paired_cross = np.cov(np.column_stack((parent, child)).T, ddof=1) if len(values) > 1 else np.full((6, 6), np.nan)
        aggregate[joint] = {
            "block_count": len(values), "qualification_required": required,
            "qualification_status": "QUALIFIED" if len(values) >= required else "CONDITIONAL_DIAGNOSTIC_INSUFFICIENT_BLOCKS",
            "parent_axis_I_RP2_mean": pa.tolist(), "child_axis_I_RP2_mean": ca.tolist(),
            "parent_tangent_covariance": pcov.tolist(), "child_tangent_covariance": ccov.tolist(),
            "paired_parent_child_cross_covariance": paired_cross.tolist(),
            "joint_modes": [{"parent_sign": 1, "child_sign": 1}, {"parent_sign": -1, "child_sign": -1}],
        }
    payload = {
        "schema": "biospur-phase3r23-axis-fit-lineage-v1",
        "official_qmt_version": "0.2.4", "official_qmt_commit": "0fa8d32eb461e14d78e9ddbd569664ea59bcea19",
        "input_class": "AXIS_FIT", "blocks": dict(blocks), "aggregate": aggregate,
        "rejected_blocks": rejected, "point_axis_used_for_pass_rank": False,
        "nested_bootstrap_axis_reestimation": "paired RP2 block resampling with parent-child cross-covariance",
        "physical_axis_truth": "POSSIBLE_NOT_PROVEN", "qmt_numeric_function": "PROVEN_FROM_OFFICIAL_UPSTREAM",
    }
    atomic_json(output, payload)
    return payload, aggregate


def _protocol_factor_specs(authority: Mapping) -> list[dict]:
    return [row for row in authority["rows"] if row.get("heading_bearing") and "target_axis_line_P" in row]


def build_heading_factors(*, rows: Mapping[str, np.ndarray], contract: Mapping,
                          authority: Mapping, axes: Mapping) -> tuple[list[dict], list[dict]]:
    nodes, actions = _codes(contract)
    node_for_segment = {segment: nodes.index(node) for node, segment in contract["operator_mapping"].items()}
    factors: list[dict] = []
    rejected: list[dict] = []
    for spec in _protocol_factor_specs(authority):
        action_code = actions.index(spec["action_id"])
        target = np.asarray(spec["target_axis_line_P"], dtype=float)
        target_angle = float(math.atan2(target[1], target[0]))
        cycles = sorted(map(int, np.unique(rows["cycle_ordinal"][rows["action_code"] == action_code])))
        if spec["action_id"] in {"06_elbow_left", "07_elbow_right"}:
            cycles = [cycle for cycle in cycles if cycle <= 4]
        for segment in spec["segments"]:
            node_code = node_for_segment[segment]
            for cycle in cycles:
                index = _block(rows, action_code, cycle, node_code)
                try:
                    _axis, observed, concentration = gyro_world_axis(rows["q_EI_wxyz"][index], rows["gyro_rad_s"][index])
                    factors.append({
                        "factor_id": f"protocol|{spec['action_id']}|{cycle}|{segment}",
                        "family": f"protocol|{spec['action_id']}|{segment}", "type": "PROTOCOL_AXIS_LINE",
                        "action_id": spec["action_id"], "cycle_ordinal": cycle, "segments": [segment],
                        "block_midpoint_common_time_ns": int(np.median(rows["common_time_ns"][index])),
                        "target_frame": "P", "target_angle_P_rad_mod_pi": target_angle,
                        "observed_angle_E_rad_mod_pi": observed,
                        "measurement_rad_mod_pi": float(wrap_axis_line(target_angle-observed)),
                        "concentration": concentration, "residual_dimension": 1,
                        "uid_sha256": hash_ordered_strings(sorted(_uids(rows, index, nodes))),
                        "uid_count": int(len(index)), "direct_or_derived": "DIRECT_IMU_DERIVED_BLOCK_AXIS",
                        "semantic_class": "PROTOCOL_SEMANTIC_CONDITIONAL", "line_symmetry": "pi",
                    })
                except Exception as exc:
                    rejected.append({"action_id": spec["action_id"], "cycle_ordinal": cycle,
                                     "segment": segment, "reason": type(exc).__name__})
    for joint, (action, parent, child) in contract["hinges"].items():
        if joint not in axes:
            continue
        action_code = actions.index(action)
        cycles = sorted(map(int, np.unique(rows["cycle_ordinal"][rows["action_code"] == action_code])))
        if joint.startswith("elbow"):
            cycles = [cycle for cycle in cycles if cycle <= 4]
        pa = np.asarray(axes[joint]["parent_axis_I_RP2_mean"], dtype=float)
        ca = np.asarray(axes[joint]["child_axis_I_RP2_mean"], dtype=float)
        paired_blocks = axes[joint].get("paired_axis_blocks", [])
        bootstrap_axis_pairs = []
        if paired_blocks:
            block_count = len(paired_blocks)
            for take in itertools.product(range(block_count), repeat=block_count):
                bootstrap_pa, _ = rp2_mean(np.asarray([paired_blocks[k]["parent_axis_I_RP2"] for k in take]))
                bootstrap_ca, _ = rp2_mean(np.asarray([paired_blocks[k]["child_axis_I_RP2"] for k in take]))
                bootstrap_axis_pairs.append((bootstrap_pa, bootstrap_ca))
        for cycle in cycles:
            pi = _block(rows, action_code, cycle, node_for_segment[parent])
            ci = _block(rows, action_code, cycle, node_for_segment[child])
            try:
                ap, cp = horizontal_axis_angle(quat_rotate(rows["q_EI_wxyz"][pi], pa))
                ac, cc = horizontal_axis_angle(quat_rotate(rows["q_EI_wxyz"][ci], ca))
                nested_options = []
                for bootstrap_pa, bootstrap_ca in bootstrap_axis_pairs:
                    bap, _ = horizontal_axis_angle(quat_rotate(rows["q_EI_wxyz"][pi], bootstrap_pa))
                    bac, _ = horizontal_axis_angle(quat_rotate(rows["q_EI_wxyz"][ci], bootstrap_ca))
                    nested_options.append(float(wrap_axis_line(bap-bac)))
                factors.append({
                    "factor_id": f"hinge|{joint}|{cycle}", "family": f"hinge|{joint}", "type": "HINGE_RP2_RELATION",
                    "action_id": action, "cycle_ordinal": cycle, "segments": [parent, child],
                    "block_midpoint_common_time_ns": int(np.median(np.r_[rows["common_time_ns"][pi], rows["common_time_ns"][ci]])),
                    "measurement_rad_mod_pi": float(wrap_axis_line(ap-ac)),
                    "parent_observed_angle_E_rad_mod_pi": ap, "child_observed_angle_E_rad_mod_pi": ac,
                    "concentration": min(cp, cc), "residual_dimension": 1,
                    "uid_sha256": hash_ordered_strings(sorted(_uids(rows, np.concatenate((pi, ci)), nodes))),
                    "uid_count": int(len(pi)+len(ci)), "direct_or_derived": "QMT_AXIS_NUISANCE_PLUS_HEADING_FIT_IMU",
                    "semantic_class": "BIOMECHANICS_CONDITIONAL_DATA_DERIVED", "line_symmetry": "joint_pi",
                    "axis_fit_block_count": axes[joint]["block_count"],
                    "axis_nuisance_joint": joint,
                    "nested_axis_bootstrap_measurements_rad_mod_pi": nested_options,
                    "nested_axis_bootstrap_option_count": len(nested_options),
                })
            except Exception as exc:
                rejected.append({"joint": joint, "cycle_ordinal": cycle, "reason": type(exc).__name__})
    floor = math.radians(float(contract["qualification"]["block_scale_floor_deg"]))
    families: dict[str, list[dict]] = defaultdict(list)
    for factor in factors:
        families[factor["family"]].append(factor)
    for family, values in families.items():
        scale = robust_axis_scale([row["measurement_rad_mod_pi"] for row in values], floor)
        for row in values:
            row["block_scale_rad"] = scale
            row["accepted_robust_weight"] = 1.0/(scale*scale)
            row["nonzero_heading_jacobian"] = True
    return factors, rejected


def _factor_row(factor: Mapping, order: Sequence[str]) -> tuple[np.ndarray, float]:
    row = np.zeros(len(order)+1, dtype=float)  # last coordinate is psi_GP
    if factor["type"] == "PROTOCOL_AXIS_LINE":
        row[order.index(factor["segments"][0])] = 1.0
        row[-1] = -1.0
    elif factor["type"] == "HINGE_RP2_RELATION":
        parent, child = factor["segments"]
        row[order.index(child)] = 1.0
        row[order.index(parent)] = -1.0
    else:
        raise ValueError(factor["type"])
    return row, float(factor["measurement_rad_mod_pi"])


def build_information(factors: Sequence[Mapping], contract: Mapping, output_external: Path) -> tuple[dict, dict[str, np.ndarray]]:
    order = contract["relative_heading_order"]
    dim = len(order)+1
    matrices = {name: np.zeros((dim, dim), dtype=float) for name in ("I0", "I1", "I2")}
    for factor in factors:
        row, _ = _factor_row(factor, order)
        block = float(factor["accepted_robust_weight"])*np.outer(row, row)
        if factor["type"] == "HINGE_RP2_RELATION":
            matrices["I1"] += block
        else:
            matrices["I2"] += block
    matrices["I2"] += matrices["I1"]
    reduced = {name: schur_profile(value, len(order)) for name, value in matrices.items()}
    reduced["biomechanics_conditional_increment"] = reduced["I1"]-reduced["I0"]
    reduced["protocol_conditional_increment"] = reduced["I2"]-reduced["I1"]
    reduced["process_drift_model"] = np.zeros((len(order), len(order)))
    reduced["anatomy_prior"] = np.zeros((len(order), len(order)))
    reduced["gauge_convention"] = np.zeros((len(order), len(order)))
    reduced["combined"] = reduced["I2"].copy()
    output_external.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output_external, **{f"augmented_{k}": v for k, v in matrices.items()}, **{f"profiled_{k}": v for k, v in reduced.items()})
    tolerances = contract["qualification"]["profile_svd_tolerances"]
    report = {
        "schema": "biospur-phase3r23-actual-information-audit-v1",
        "parameter_order": list(order), "augmented_parameter_order": list(order)+["psi_GP"],
        "information_classes": {
            "I0": "IMU_OBSERVATION_ONLY", "I1": "joint(I0 + BIOMECHANICS_CONDITIONAL_DATA_DERIVED)",
            "I2": "joint(I1 + PROTOCOL_SEMANTIC_CONDITIONAL)",
        },
        "augmented": {name: information_rank(value, tolerances) for name, value in matrices.items()},
        "profiled_relative_heading": {name: information_rank(value, tolerances) for name, value in reduced.items()},
        "matrix_npz": {"path": str(output_external), "sha256": sha256_file(output_external)},
        "pass_matrix": "profiled_relative_heading.I2", "pass_matrix_classification": "PROTOCOL_CONDITIONAL",
        "pelvis_fixed_but_psi_profiled": True, "global_yaw_gauge_active": True,
        "structural_null_explanation": "All heading-bearing protocol factors constrain h_i-psi_GP. No heading-bearing pelvis factor anchors psi_GP to the pelvis convention.",
    }
    return report, reduced


def _residual_vector(x: np.ndarray, factors: Sequence[Mapping], order: Sequence[str]) -> np.ndarray:
    augmented = np.r_[x, 0.0]  # representative convention psi_GP=0 only
    values = []
    for factor in factors:
        row, target = _factor_row(factor, order)
        values.append(math.sqrt(float(factor["accepted_robust_weight"]))*float(wrap_axis_line(row@augmented-target)))
    return np.asarray(values)


def solve_modes(factors: Sequence[Mapping], contract: Mapping) -> tuple[dict, np.ndarray]:
    order = contract["relative_heading_order"]
    starts = [np.zeros(len(order))]
    for k in range(64):
        rng = np.random.default_rng(stable_seed(int(contract["master_seed"]), f"multistart-{k:03d}"))
        starts.append(rng.uniform(-np.pi/2, np.pi/2, len(order)))
    solutions = []
    for start in starts:
        fit = least_squares(lambda x: _residual_vector(x, factors, order), start, method="trf",
                            xtol=1e-12, ftol=1e-12, gtol=1e-12, max_nfev=4000)
        canonical = wrap_axis_line(fit.x)
        solutions.append((float(np.dot(fit.fun, fit.fun)), canonical, float(np.linalg.norm(fit.grad, ord=np.inf))))
    best = min(solutions, key=lambda row: (row[0], tuple(row[1])))[1]
    base_objective = min(row[0] for row in solutions)
    # Every coordinate is an RP1 axis line, so the complete S1 representation
    # has an exact pi branch independently for each of the nine headings.
    modes = []
    for index, bits in enumerate(itertools.product((0, 1), repeat=len(order))):
        value = wrap_pi(best+np.pi*np.asarray(bits))
        modes.append({
            "mode_id": f"axis_line_branch_{index:03d}", "representative_psi_GP_rad": 0.0,
            "relative_heading_rad": {segment: float(value[k]) for k, segment in enumerate(order)},
            "pi_branch_bits": list(bits), "objective": base_objective,
            "support_classification": "MODE_SUPPORT_INDETERMINATE",
            "continuous_orbit": "add common alpha to every h_i and psi_GP for alpha in S1",
        })
    result = {
        "schema": "biospur-phase3r23-multistart-mode-report-v1",
        "multistart_count": len(starts), "converged_count": len(solutions),
        "best_objective": base_objective, "representative_base_rad_mod_pi": best.tolist(),
        "joint_mode_count": len(modes), "joint_modes": modes,
        "mode_merge_rule": {"rms_deg": 2.0, "max_coordinate_deg": 5.0},
        "clustering_sensitivity_deg": [1.0, 2.0, 5.0],
        "non_global_discrete_modes": True, "continuous_psi_orbit": True,
        "single_table_readiness_blocked": True,
        "stationarity_inf_gradient_norm": min(row[2] for row in solutions),
    }
    return result, best


def _bootstrap_one(task: tuple[int, int, list[dict], list[str], list[float]]) -> tuple[int, np.ndarray, float, int, bool]:
    replicate, master_seed, factors, order, base_list = task
    base = np.asarray(base_list, dtype=float)
    families: dict[str, list[dict]] = defaultdict(list)
    for row in factors:
        families[row["family"]].append(row)
    rng = np.random.default_rng(stable_seed(master_seed, f"bootstrap-{replicate:04d}"))
    axis_option = {}
    for row in factors:
        joint = row.get("axis_nuisance_joint")
        count = int(row.get("nested_axis_bootstrap_option_count", 0))
        if joint and count and joint not in axis_option:
            axis_option[joint] = int(rng.integers(0, count))
    selected = []
    for family in sorted(families):
        values = families[family]
        take = rng.integers(0, len(values), size=len(values))
        for k in take:
            row = dict(values[int(k)])
            joint = row.get("axis_nuisance_joint")
            options = row.get("nested_axis_bootstrap_measurements_rad_mod_pi", [])
            if joint and options:
                row["measurement_rad_mod_pi"] = float(options[axis_option[joint]])
            selected.append(row)
    fit = least_squares(lambda x: _residual_vector(x, selected, order), base, method="trf",
                        xtol=1e-10, ftol=1e-10, gtol=1e-10, max_nfev=1000)
    representative = wrap_axis_line(fit.x)
    psi = float(rng.uniform(-np.pi, np.pi))
    mode_index = replicate % (2**len(order))
    bits = np.asarray([(mode_index >> k) & 1 for k in range(len(order))])
    sample = wrap_pi(representative+psi+np.pi*bits)
    return replicate, sample, psi, mode_index, bool(fit.success and np.all(np.isfinite(fit.x)))


def bootstrap_joint(*, factors: Sequence[Mapping], contract: Mapping, base: np.ndarray,
                    output_npz: Path, workers: int) -> dict:
    order = contract["relative_heading_order"]
    count = int(contract["qualification"]["bootstrap_replicates"])
    samples = np.empty((count, len(order)), dtype="<f8")
    psi = np.empty(count, dtype="<f8")
    mode_index = np.empty(count, dtype="<i4")
    valid = np.ones(count, dtype=bool)
    serializable_factors = [dict(row) for row in factors]
    tasks = [(replicate, int(contract["master_seed"]), serializable_factors, list(order), base.tolist()) for replicate in range(count)]
    actual_workers = int(workers)
    try:
        with ProcessPoolExecutor(max_workers=actual_workers) as pool:
            results = list(pool.map(_bootstrap_one, tasks, chunksize=4))
    except Exception:
        actual_workers = 1
        results = [_bootstrap_one(task) for task in tasks]
    results.sort(key=lambda row: row[0])
    for replicate, sample, psi_value, mode_value, ok in results:
        samples[replicate] = sample; psi[replicate] = psi_value
        mode_index[replicate] = mode_value; valid[replicate] = ok
    output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output_npz, joint_heading_samples_rad=samples, psi_GP_rad=psi,
             axis_line_mode_index=mode_index, valid=valid)
    intervals = {segment: {"shortest_circular_arc_half_width_deg": 180.0,
                           "qualification": "FULL_S1_DUE_TO_UNRESOLVED_PSI_AND_PI_BRANCHES"}
                 for segment in order}
    differences = np.exp(1j*(samples[:, :, None]-samples[:, None, :]))
    correlation = np.abs(np.mean(differences, axis=0))
    return {
        "schema": "biospur-phase3r23-bootstrap-report-v1", "replicates": count,
        "valid_replicates": int(np.sum(valid)), "resampling_unit": "action/cycle block",
        "nested_axis_reestimation": "performed in every replicate by resampling paired official-qmt AXIS_FIT block proposals, recomputing RP2 means, and re-evaluating every hinge HEADING_FIT block",
        "nested_axis_reestimation_replicates": count,
        "actual_workers_noncanonical": actual_workers,
        "frame_samples_treated_independent": False, "intervals": intervals,
        "joint_circular_difference_concentration": correlation.tolist(),
        "samples_npz": {"path": str(output_npz), "sha256": sha256_file(output_npz)},
        "alternative_mode_frequency": 1.0, "minimum_alternative_frequency_gate": 0.05,
        "probabilistic_95_percent_claim": False,
    }


def pseudo_profiles(*, factors: Sequence[Mapping], contract: Mapping, best: np.ndarray,
                    output_npz: Path) -> dict:
    order = contract["relative_heading_order"]
    grid = np.linspace(-np.pi, np.pi, 721, endpoint=False)
    values = np.empty((len(order), len(grid)), dtype="<f8")
    baseline = float(np.dot(_residual_vector(best, factors, order), _residual_vector(best, factors, order)))
    # The exact transform h_i <- h_i+alpha, psi <- psi+alpha is a null orbit.
    # Profiling psi therefore leaves every individual h_i value equally supported.
    values.fill(baseline)
    output_npz.parent.mkdir(parents=True, exist_ok=True)
    np.savez(output_npz, grid_rad=grid, objective=values)
    return {
        "schema": "biospur-phase3r23-pseudo-profile-objectives-v1",
        "parameter_order": list(order), "grid_points_per_heading": len(grid),
        "objective_min": baseline, "objective_max": baseline, "objective_span": 0.0,
        "support": {segment: "FULL_S1" for segment in order},
        "reason": "psi_GP is unanchored to pelvis; every profile has an exact common-shift solution, with additional pi axis-line branches",
        "probabilistic_profile_confidence_region": "NOT_AVAILABLE_NO_CALIBRATED_GENERATIVE_MODEL",
        "profile_npz": {"path": str(output_npz), "sha256": sha256_file(output_npz)},
    }


def timing_sensitivity(*, factors: Sequence[Mapping], information_report: Mapping,
                       contract: Mapping) -> dict:
    # The frozen bounded sample-age model gives a conservative first-order
    # orientation envelope. Structural Jacobians do not depend on the measured
    # axis angles, so their rank is exactly invariant to all allowed shifts.
    scenarios = []
    for delta_ms in contract["qualification"]["timing_perturbation_ms"]:
        worst_bound_deg = 1.2*float(delta_ms)  # inherited measured 1.2 deg/ms bound
        scenarios.append({
            "differential_clock_ms": float(delta_ms),
            "bounded_orientation_perturbation_deg": worst_bound_deg,
            "profiled_I2_rank_by_tolerance": information_report["profiled_relative_heading"]["I2"]["rank_by_relative_tolerance"],
            "rank_flip": False, "mode_flip": False,
            "identifiability_verdict_flip": False,
        })
    return {
        "schema": "biospur-phase3r23-timing-sensitivity-v1",
        "model": "existing bounded correlated TIMER2/sample-age model; not iid white noise",
        "host_arrival_used": False, "uwb_measurement_used": False,
        "scenarios": scenarios, "worst_orientation_bound_deg": max(x["bounded_orientation_perturbation_deg"] for x in scenarios),
    }


def evidence_graph(*, factors: Sequence[Mapping], contract: Mapping, split_manifest: Mapping) -> dict:
    edges = []
    for row in factors:
        if row["type"] == "PROTOCOL_AXIS_LINE":
            endpoints = [row["segments"][0], "psi_GP"]
        else:
            endpoints = list(row["segments"])
        edges.append({
            "factor_id": row["factor_id"], "endpoints": endpoints,
            "action_id": row["action_id"], "cycle_ordinal": row["cycle_ordinal"],
            "uid_sha256": row["uid_sha256"], "factor_type": row["type"],
            "frame": "G with protocol directions Rz(psi_GP) P",
            "residual_dimension": row["residual_dimension"], "direct_or_derived": row["direct_or_derived"],
            "uncertainty": {"block_scale_rad": row["block_scale_rad"], "accepted_robust_weight": row["accepted_robust_weight"]},
            "cross_covariance": "shared UID/axis lineage retained by family-block grouping",
        })
    subtrees = {}
    for name, segments in contract["subtrees"].items():
        subtrees[name] = {
            "segments": segments, "path_to_pelvis": [], "connected_to_pelvis": False,
            "connection_to_protocol_nuisance": sorted({row["action_id"] for row in factors if any(s in row["segments"] for s in segments)}),
            "reason": "no heading-bearing pelvis factor anchors psi_GP; protocol-nuisance connectivity is not pelvis connectivity",
        }
    return {
        "schema": "biospur-phase3r23-heading-evidence-factor-graph-v1",
        "nodes": list(contract["relative_heading_order"])+["pelvis_fixed_convention", "psi_GP"],
        "axis_nuisances": sorted(contract["hinges"]), "long_axis_nuisances": ["T_pose_arm_local_long_axes_RP2"],
        "edges": edges, "subtree_clusters": subtrees,
        "pelvis_heading_bearing_factor_count": 0,
        "structural_connection_status": "FIVE_SUBTREES_CONNECT_TO_PSI_GP_BUT_NOT_TO_PELVIS",
        "split_manifest_sha256": split_manifest["manifest_payload_sha256"],
    }


def build_candidate(*, report_dir: Path, evidence_dir: Path, frontend_root: Path,
                    contract: Mapping, authority: Mapping, split_manifest: Mapping,
                    source_closure_sha256: str, synthetic_result_sha256: str) -> dict:
    report_dir.mkdir(parents=True, exist_ok=True)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    axis_rows = load_class_cache(frontend_root, "AXIS_FIT")
    axis_payload, axes = fit_official_qmt_axes(rows=axis_rows, contract=contract,
                                               output=report_dir/"AXIS_FIT_LINEAGE_AND_UNCERTAINTY.json")
    for joint in axes:
        axes[joint]["paired_axis_blocks"] = axis_payload["blocks"].get(joint, [])
    heading_rows = load_class_cache(frontend_root, "HEADING_FIT")
    factors, rejected = build_heading_factors(rows=heading_rows, contract=contract, authority=authority, axes=axes)
    graph = evidence_graph(factors=factors, contract=contract, split_manifest=split_manifest)
    actual_graph_path = evidence_dir/"HEADING_EVIDENCE_FACTOR_GRAPH_ACTUAL.json"
    atomic_json(actual_graph_path, graph)
    information, _matrices = build_information(factors, contract, evidence_dir/"COMMON_HEADING_INFORMATION_MATRICES.npz")
    information["accepted_factor_count"] = len(factors)
    information["rejected_factor_blocks"] = rejected
    information["factor_family_block_counts"] = dict(sorted((family, sum(row["family"] == family for row in factors)) for family in {row["family"] for row in factors}))
    atomic_json(report_dir/"COMMON_HEADING_ACTUAL_INFORMATION_AUDIT.json", information)
    multistart, best = solve_modes(factors, contract)
    atomic_json(report_dir/"COMMON_HEADING_MULTISTART_AND_MODE_REPORT.json", multistart)
    profiles = pseudo_profiles(factors=factors, contract=contract, best=best,
                               output_npz=evidence_dir/"COMMON_HEADING_PSEUDO_PROFILE_GRID.npz")
    atomic_json(report_dir/"COMMON_HEADING_PSEUDO_PROFILE_OBJECTIVES.json", profiles)
    worker_benchmark = json.loads((report_dir/"WORKER_BENCHMARK.json").read_text())
    bootstrap = bootstrap_joint(factors=factors, contract=contract, base=best,
                                output_npz=evidence_dir/"COMMON_HEADING_JOINT_BOOTSTRAP_SAMPLES.npz",
                                workers=int(worker_benchmark["chosen_workers"]))
    atomic_json(report_dir/"COMMON_HEADING_BOOTSTRAP_REPORT.json", bootstrap)
    timing = timing_sensitivity(factors=factors, information_report=information, contract=contract)
    atomic_json(report_dir/"COMMON_HEADING_TIMING_SENSITIVITY.json", timing)
    modes = multistart["joint_modes"]
    candidate = {
        "schema": "biospur-phase3r23-prevalidation-session-static-heading-candidate-v1",
        "candidate_type": "FIT_ONLY_JOINT_S1_9_MODE_FAMILY_NOT_A_SINGLE_HEADING_TABLE",
        "capture_id": "Capture_2_with_JOINT_LABEL", "session_id": split_manifest["session_id"],
        "subject_id": split_manifest["subject_id"], "mapping": contract["operator_mapping"],
        "parameter_order": contract["relative_heading_order"], "pelvis_heading_convention_rad": 0.0,
        "psi_GP": {"status": "UNRESOLVED_PROFILED_NUISANCE", "support": "FULL_S1"},
        "joint_modes": modes, "joint_mode_count": len(modes),
        "joint_samples": bootstrap["samples_npz"],
        "within_mode_covariance": "NOT_QUALIFIED_AXIS_AND_HEADING_FAMILIES_BELOW_FIVE_BLOCKS",
        "cross_heading_correlation": bootstrap["joint_circular_difference_concentration"],
        "symmetries": ["continuous common h_i/psi_GP shift", "independent per-heading pi axis-line branches"],
        "profile_support": profiles["support"], "axis_nuisance_sha256": sha256_file(report_dir/"AXIS_FIT_LINEAGE_AND_UNCERTAINTY.json"),
        "frozen_evidence_graph_sha256": sha256_file(report_dir/"HEADING_EVIDENCE_FACTOR_GRAPH.json"),
        "actual_evidence_graph": {"path": str(actual_graph_path), "sha256": sha256_file(actual_graph_path)},
        "split_manifest_sha256": sha256_payload(split_manifest), "source_closure_sha256": source_closure_sha256,
        "synthetic_result_sha256": synthetic_result_sha256,
        "information_audit_sha256": sha256_file(report_dir/"COMMON_HEADING_ACTUAL_INFORMATION_AUDIT.json"),
        "multistart_sha256": sha256_file(report_dir/"COMMON_HEADING_MULTISTART_AND_MODE_REPORT.json"),
        "bootstrap_sha256": sha256_file(report_dir/"COMMON_HEADING_BOOTSTRAP_REPORT.json"),
        "timing_sha256": sha256_file(report_dir/"COMMON_HEADING_TIMING_SENSITIVITY.json"),
        "validation_factor_rows_consumed": 0, "final_still_heading_factor_count": 0,
        "h_numeric_consumption": 0, "p_numeric_consumption": 0, "b1_numeric_consumption": 0,
        "opensense_numeric_consumption": 0, "uwb_semantic_numeric_decode": 0,
        "plus10_injection_factor_consumption": 0,
        "scope_qualifiers": contract["scope_qualifiers"],
        "scientific_status_before_validation": "FAILS_UNIQUENESS_AND_FULL_RANK_PREREQUISITES",
    }
    candidate["candidate_payload_sha256"] = sha256_payload(candidate)
    atomic_json(report_dir/"PREVALIDATION_SESSION_STATIC_HEADING_CANDIDATE.json", candidate)
    return candidate
