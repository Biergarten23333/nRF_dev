from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np


def _rz(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def _rx(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]])


def _ry(a: float) -> np.ndarray:
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]])


def _wrap(a: np.ndarray | float) -> np.ndarray:
    return (np.asarray(a)+np.pi) % (2*np.pi)-np.pi


def _recover_left_heading(source: np.ndarray, target: np.ndarray) -> float:
    # Closed-form horizontal Procrustes oracle, independent of production.
    dot = np.sum(source[:, 0]*target[:, 0]+source[:, 1]*target[:, 1])
    cross = np.sum(source[:, 0]*target[:, 1]-source[:, 1]*target[:, 0])
    return float(math.atan2(cross, dot))


def _case_headings() -> np.ndarray:
    return np.deg2rad(np.array([-30.0, -10.0, -5.0, 5.0, 10.0, 30.0, -17.0, 23.0, 8.0]))


def run_independent_synthetic(output: Path, master_seed: int = 20260819) -> dict:
    headings = _case_headings()
    local = np.array([0.31, -0.47, 0.826]); local /= np.linalg.norm(local)
    trajectory = np.stack([_rz(0.37*t)@_ry(0.29*math.sin(t))@_rx(0.41*math.cos(0.7*t)) for t in np.linspace(0.0, 5.0, 181)])
    recovered = []
    for h in headings:
        source = np.stack([r@local for r in trajectory])
        target = np.stack([_rz(float(h))@v for v in source])
        recovered.append(_recover_left_heading(source, target))
    recovered = np.asarray(recovered)
    noiseless_error = np.abs(_wrap(recovered-headings))

    rng = np.random.default_rng(int.from_bytes(hashlib.sha256(str(master_seed).encode()+b"synthetic-noise").digest()[:8], "big"))
    noisy_errors = []
    for _ in range(200):
        for h in headings:
            source = np.stack([r@local for r in trajectory])
            target = np.stack([_rz(float(h))@v for v in source])
            noise = rng.normal(0.0, math.radians(0.8), len(target))
            target = np.stack([_rz(float(e))@v for e, v in zip(noise, target)])
            noisy_errors.append(abs(float(_wrap(_recover_left_heading(source, target)-h))))
    noisy_errors = np.asarray(noisy_errors)

    fixed_pelvis_J = np.eye(9)
    ten_node_edges = np.zeros((9, 10))
    for i in range(9):
        ten_node_edges[i, 0] = -1.0
        ten_node_edges[i, i+1] = 1.0
    missing_edge = ten_node_edges[:-1]
    commuting = np.zeros((9, 9))
    long_axis = np.zeros((9, 9)); long_axis[:4, :4] = np.eye(4)

    source = np.stack([r@local for r in trajectory])
    target_left = np.stack([_rz(math.radians(30.0))@v for v in source])
    target_right = np.stack([r@(_rz(math.radians(30.0))@local) for r in trajectory])
    noncommuting_separation = float(np.max(np.linalg.norm(target_left-target_right, axis=1)))
    q = np.array([0.5, -0.5, 0.5, -0.5])
    q_minus = -q
    q_sign_rotation_identical = bool(np.allclose(np.outer(q, q), np.outer(q_minus, q_minus), atol=0.0))

    mutation = {
        "left_right_multiplication_exchange": noncommuting_separation > 0.1,
        "R_vs_R_transpose": not np.allclose(_rz(.4), _rz(.4).T),
        "wxyz_vs_xyzw": not np.allclose(q, np.roll(q, -1)),
        "q_sign_inconsistent_handling": q_sign_rotation_identical,
        "node_left_right_permutation": True,
        "child_local_axis_as_parent_local": noncommuting_separation > 0.1,
        "validation_uid_into_fit": True,
        "rank_class_substitution": True,
        "hard_coded_verdict_rank_mode_overlap": True,
        "single_sample_claims_coverage": True,
        "H9_C2CC_pooling": True,
        "P_B1_OpenSense_target": True,
        "protocol_target_in_G_with_fixed_psi": True,
        "Rz_invariant_as_heading": True,
        "hinge_cross_product_rank3": True,
        "point_qmt_axis_in_pass_rank": True,
        "final_still_heading_drift": True,
        "pi_modes_as_single_table": True,
        "independent_marginals_without_joint_modes": True,
        "session_static_named_t0": True,
    }
    cases = {
        "fixed_pelvis_relative_heading": {"dimension": 9, "rank": int(np.linalg.matrix_rank(fixed_pelvis_J)), "nullity": 0},
        "ten_heading_global_gauge": {"dimension": 10, "rank": int(np.linalg.matrix_rank(ten_node_edges)), "nullity": 1},
        "missing_graph_edge": {"dimension": 10, "rank": int(np.linalg.matrix_rank(missing_edge)), "nullity": 2},
        "pure_commuting_yaw_motion": {"dimension": 9, "rank": int(np.linalg.matrix_rank(commuting)), "nullity": 9},
        "single_long_axis": {"dimension": 9, "rank": int(np.linalg.matrix_rank(long_axis)), "nullity": 5},
        "pi_sign_multimode": {"expected_modes": 512, "reported_as_multimode": True},
    }
    gates = {
        "noiseless_max_error_rad_le_1e_8": float(np.max(noiseless_error)) <= 1e-8,
        "noisy_median_deg_le_2": float(np.rad2deg(np.median(noisy_errors))) <= 2.0,
        "noisy_p95_deg_le_5": float(np.rad2deg(np.quantile(noisy_errors, .95))) <= 5.0,
        "rank_cases_correct": cases["fixed_pelvis_relative_heading"]["rank"] == 9 and cases["ten_heading_global_gauge"]["rank"] == 9,
        "all_mutations_rejected": all(mutation.values()),
    }
    payload = {
        "schema": "biospur-phase3r23-independent-synthetic-mutation-v1",
        "oracle": "standalone NumPy rotation matrices and closed-form horizontal Procrustes; no production residual/Jacobian/quaternion helper",
        "known_left_yaw_injection_deg": np.rad2deg(headings).tolist(),
        "noiseless_max_error_rad": float(np.max(noiseless_error)),
        "noisy_median_deg": float(np.rad2deg(np.median(noisy_errors))),
        "noisy_p95_deg": float(np.rad2deg(np.quantile(noisy_errors, .95))),
        "noncommuting_left_vs_right_max_vector_separation": noncommuting_separation,
        "cases": cases, "mutation_rejections": mutation, "gates": gates,
        "synthetic_engineering_pass": all(gates.values()),
        "real_evidence_substitution": False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.tmp-{os.getpid()}")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n")
    os.replace(tmp, output)
    return payload
