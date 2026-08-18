from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import hashlib
import json
from typing import Iterable, Mapping

import numpy as np

from . import so3
from .types import CalibrationBundle, SEGMENTS, SegmentCalibration


EXPECTED_NODES = frozenset({
    "BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
    "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35",
})
H9 = EXPECTED_NODES-{"BSFC2CC"}


@dataclass(frozen=True, slots=True)
class CalibrationObservation:
    action_id: str
    cycle_id: str
    split_class: str
    node_id: str
    direction_S: np.ndarray
    direction_I: np.ndarray
    weight: float
    source_uid: str


def validate_mapping(payload: Mapping) -> dict[str, str]:
    mapping = dict(payload["node_to_role"])
    if set(mapping) != EXPECTED_NODES or set(mapping.values()) != set(SEGMENTS):
        raise ValueError("operator mapping must be exact 10x10 bijection")
    if mapping.get("BSFC2CC") != "pelvis" or "BSFC22C" in mapping:
        raise ValueError("C2CC identity invariant")
    if payload.get("automapping_runtime_factor_count") != 0:
        raise ValueError("automapping must remain structurally inactive")
    return mapping


def assert_h9_pool(nodes: Iterable[str]) -> None:
    nodes = set(nodes)
    if "BSFC2CC" in nodes or not nodes <= H9:
        raise ValueError("C2CC/unknown node cannot enter H9 pooling")


def _fit_rotation(rows: list[CalibrationObservation]) -> tuple[np.ndarray, np.ndarray, int, float, tuple[tuple[int, float], ...]]:
    moment = np.zeros((3, 3))
    information = np.zeros((3, 3))
    for row in rows:
        source = np.asarray(row.direction_S, float); source /= np.linalg.norm(source)
        target = np.asarray(row.direction_I, float); target /= np.linalg.norm(target)
        moment += row.weight*np.outer(target, source)
    left, _, right_t = np.linalg.svd(moment)
    correction = np.diag([1.0, 1.0, np.linalg.det(left@right_t)])
    rotation = left@correction@right_t
    for row in rows:
        source = np.asarray(row.direction_S, float); source /= np.linalg.norm(source)
        direction = rotation@source
        information += row.weight*(np.eye(3)-np.outer(direction, direction))
    eig = np.linalg.eigvalsh(information)
    tolerance = max(float(eig[-1])*1e-6, 1e-10)
    rank = int(np.sum(eig > tolerance))
    regularized = information+np.eye(3)*np.deg2rad(20)**-2*max(0, 3-rank)
    covariance = np.linalg.solve(regularized, np.eye(3))
    residual = sum(row.weight*np.linalg.norm(rotation@(
        np.asarray(row.direction_S)/np.linalg.norm(row.direction_S)
    )-np.asarray(row.direction_I)/np.linalg.norm(row.direction_I))**2 for row in rows)
    antipodal = sum(row.weight*np.linalg.norm(rotation@(
        -np.asarray(row.direction_S)/np.linalg.norm(row.direction_S)
    )-np.asarray(row.direction_I)/np.linalg.norm(row.direction_I))**2 for row in rows)
    odds = np.exp(-0.5*np.clip(antipodal-residual, -100, 100))
    positive = 1/(1+odds)
    return so3.from_matrix(rotation), covariance, rank, float(residual), ((1, float(positive)), (-1, float(1-positive)))


def fit_joint_calibration(observations: Iterable[CalibrationObservation], mapping: Mapping[str, str],
                          expected_fit_actions: Iterable[str]) -> CalibrationBundle:
    mapping = dict(mapping)
    if set(mapping) != EXPECTED_NODES or set(mapping.values()) != set(SEGMENTS):
        raise ValueError("exact operator mapping required")
    expected = tuple(sorted(expected_fit_actions))
    if len(expected) != 18 or "17_final_still" in expected:
        raise ValueError("exact eighteen FIT-bearing actions required; final still is validation-only")
    rows = [row for row in observations if row.split_class == "FIT"]
    if any(row.action_id == "17_final_still" for row in rows):
        raise ValueError("final still cannot enter static calibration")
    action_counts = Counter(row.action_id for row in rows)
    if set(action_counts) != set(expected) or any(action_counts[action] == 0 for action in expected):
        raise ValueError("all eighteen actions must contribute FIT factors")
    by_node_rows: dict[str, list[CalibrationObservation]] = defaultdict(list)
    for row in rows:
        if row.node_id not in mapping: raise ValueError("unmapped calibration node")
        by_node_rows[row.node_id].append(row)
    if set(by_node_rows) != EXPECTED_NODES:
        raise ValueError("joint calibration must cover all ten nodes")
    by_node: dict[str, SegmentCalibration] = {}
    serial = []
    for node in sorted(by_node_rows):
        q, covariance, rank, residual, signs = _fit_rotation(by_node_rows[node])
        actions = tuple(sorted({row.action_id for row in by_node_rows[node]}))
        calibration = SegmentCalibration(
            node, mapping[node], q, covariance, np.zeros((3, 3)), rank,
            "TWIST_IDENTIFIED" if rank == 3 else "TWIST_UNRESOLVED_S1",
            0.0, signs, float(max(0.0, (3-rank)/3)), actions,
            "C2CC_DISTINCT" if node == "BSFC2CC" else "H9",
        )
        by_node[node] = calibration
        serial.append({
            "node": node, "segment": mapping[node], "q_I_S": q.round(15).tolist(),
            "covariance": covariance.round(15).tolist(), "rank": rank,
            "residual": round(residual, 15), "actions": actions, "signs": signs,
        })
    # Marginalize one shared directional nuisance per action.  This Schur
    # complement retains cross-node calibration covariance that would be lost
    # by mechanically concatenating the ten Wahba marginals.
    parameter_order = tuple(sorted(by_node))
    node_index = {node: index for index, node in enumerate(parameter_order)}
    action_index = {action: index for index, action in enumerate(expected)}
    Htt = np.zeros((30, 30)); Hta = np.zeros((30, 3*len(expected)))
    Haa = np.eye(3*len(expected)) * np.deg2rad(10.0) ** -2
    for row in rows:
        source = np.asarray(row.direction_S, float); source /= np.linalg.norm(source)
        rotation = so3.matrix(by_node[row.node_id].q_I_S)
        Jt = -rotation @ so3.skew(source)
        Ja = rotation
        ts = slice(3*node_index[row.node_id], 3*node_index[row.node_id]+3)
        a = action_index[row.action_id]; ass = slice(3*a, 3*a+3)
        Htt[ts, ts] += row.weight * Jt.T @ Jt
        Hta[ts, ass] += row.weight * Jt.T @ Ja
        Haa[ass, ass] += row.weight * Ja.T @ Ja
    schur = Htt - Hta @ np.linalg.solve(Haa, Hta.T)
    schur += np.eye(30) * np.deg2rad(20.0) ** -2
    parameter_covariance = np.linalg.solve(schur, np.eye(30))
    parameter_covariance = 0.5 * (parameter_covariance + parameter_covariance.T)
    for node, index in node_index.items():
        block = slice(3*index, 3*index+3)
        by_node[node] = replace(by_node[node], covariance_rad2=parameter_covariance[block, block].copy())

    payload = {
        "schema": "biospur-phase3r2-calibration-bundle-v1",
        "mapping": mapping, "fit_actions": expected,
        "fit_factor_counts": dict(sorted(action_counts.items())), "nodes": serial,
        "final_still_static_factor_count": 0,
        "parameter_order": parameter_order,
        "parameter_covariance_sha256": hashlib.sha256(
            np.ascontiguousarray(parameter_covariance, dtype="<f8").tobytes()
        ).hexdigest(),
    }
    frozen = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return CalibrationBundle.freeze(
        by_node, mapping, expected, dict(action_counts), parameter_order,
        parameter_covariance, frozen,
    )


def bundle_payload(bundle: CalibrationBundle) -> dict:
    return {
        "schema": "biospur-phase3r2-session-calibration-bundle-v1",
        "authority": "OPERATOR_MAPPED_RESEARCH_CALIBRATION",
        "frame_algebra": "q_WS=q_WI*q_IS; q_IS maps S to I; right-local tangent",
        "mapping": dict(sorted(bundle.mapping.items())),
        "fit_action_ids": list(bundle.fit_action_ids),
        "fit_factor_counts": dict(sorted(bundle.fit_factor_counts.items())),
        "parameter_order": list(bundle.parameter_order),
        "parameter_covariance_rad2": bundle.parameter_covariance_rad2.tolist(),
        "nodes": {
            node: {
                "segment": row.segment, "q_IS_S_to_I_scalar_first": row.q_I_S.tolist(),
                "covariance_rad2_right_local_S": row.covariance_rad2.tolist(),
                "identified_direction_rank": row.identified_direction_rank,
                "twist_status": row.twist_status, "sign_hypotheses": [list(x) for x in row.sign_hypotheses],
                "prior_dominance": row.prior_dominance, "fit_action_ids": list(row.fit_action_ids),
                "layout_class": row.layout_class,
            } for node, row in sorted(bundle.by_node.items())
        },
        "final_still_static_factor_count": bundle.final_still_static_factor_count,
        "frozen_sha256": bundle.frozen_sha256,
    }


def apply_calibration(calibration: SegmentCalibration, q_WI: np.ndarray,
                      covariance_WI: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    q_WS = so3.normalize(so3.mul(q_WI, calibration.q_I_S))
    covariance = so3.compose_right_covariance(
        calibration.q_I_S, covariance_WI, calibration.covariance_rad2,
        calibration.cross_covariance_rad2,
    )
    return q_WS, covariance
