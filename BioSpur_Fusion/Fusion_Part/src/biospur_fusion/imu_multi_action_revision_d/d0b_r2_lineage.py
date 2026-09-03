"""Frozen observation-lineage selector and objective for D0B-R2.

Selection is completed before evaluator construction.  The residual function
never searches for replacement rows, so solver, Jacobian, rank, and replay
audits bind to one immutable row manifest.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any, Mapping

import numpy as np

from .d0b_r1_generator import ACTIONS, SEGMENTS
from .d0b_r1_model import (
    FUNCTIONAL_JOINTS, JOINTS, R1Objective, R1Observation, ResidualBlock,
    articulated_pose_directions, s2_residual,
)


MEASUREMENT_CLASSES = {"MEASURED_OBSERVATION", "PROTOCOL_CONDITIONED_MEASUREMENT"}


def _hash_json(value: Any) -> str:
    return hashlib.sha256((json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()).hexdigest()


def _uniform(rows: np.ndarray, maximum: int) -> np.ndarray:
    rows = np.asarray(rows, int)
    if len(rows) <= maximum:
        return rows.copy()
    indices = np.unique(np.rint(np.linspace(0, len(rows) - 1, maximum)).astype(int))
    return rows[indices]


@dataclass(frozen=True)
class FactorSelection:
    factor_block_id: str
    action_id: str
    phase_id: str
    chain_id: str
    factor: str
    classification: str
    parent_segment: str | None
    child_segment: str
    parent_node: str | None
    child_node: str
    candidate_rows: np.ndarray
    selected_rows: np.ndarray
    candidate_pool_id: str
    validity_mask_id: str
    source_stream: str
    source_channel: str
    selection_reason: str
    whitening_source: str
    weight: float
    residual_components_per_sample: int
    status: str


def _phase_rows(observation: R1Observation, action: str, phase: str, rows: np.ndarray) -> np.ndarray:
    rows = np.asarray(rows, int)
    start, stop = observation.windows[action]
    time = observation.time_ns[rows]
    if phase in ("STATIC_PLATEAU", "BROAD_ACTIVE"):
        return rows
    if phase in ("CURL", "PRONATION_SUPINATION"):
        cut = start + (stop - start) // 2
        return rows[time <= cut] if phase == "CURL" else rows[time > cut]
    first = start + (stop - start) // 3
    second = start + 2 * (stop - start) // 3
    if phase == "LEFT_TURN": return rows[time <= first]
    if phase == "RIGHT_TURN": return rows[(time > first) & (time <= second)]
    if phase == "FORWARD_FLEXION_RECOVERY": return rows[time > second]
    raise ValueError(f"unknown phase {phase}")


def factor_definitions() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for action in ("initial_still_attempt2", "t_pose"):
        for segment in SEGMENTS:
            result.append({"action": action, "phase": "STATIC_PLATEAU", "chain": segment, "factor": f"articulated_static_direction:{segment}", "classification": "PROTOCOL_CONDITIONED_MEASUREMENT", "parent": None, "child": segment, "components": 3})
    schedule = {
        "arms": ("shoulder_L", "shoulder_R", "elbow_L", "elbow_R"),
        "left_knee": ("hip_L",), "right_knee": ("hip_R",),
        "left_heel": ("knee_L",), "right_heel": ("knee_R",),
        "squats": ("hip_L", "hip_R", "knee_L", "knee_R"),
    }
    for action, joints in schedule.items():
        for joint in joints:
            parent, child = JOINTS[joint]
            result.append({"action": action, "phase": "BROAD_ACTIVE", "chain": joint, "factor": f"soft_functional_axis:{joint}", "classification": "MEASURED_OBSERVATION", "parent": parent, "child": child, "components": 3})
    for action, joint in (("left_elbow", "elbow_L"), ("right_elbow_attempt2", "elbow_R")):
        parent, child = JOINTS[joint]
        for phase, prefix in (("CURL", "curl"), ("PRONATION_SUPINATION", "pronation_supination")):
            result.append({"action": action, "phase": phase, "chain": joint, "factor": f"{prefix}_functional_axis:{joint}", "classification": "MEASURED_OBSERVATION", "parent": parent, "child": child, "components": 3})
    for phase, prefix in (("LEFT_TURN", "left_turn"), ("RIGHT_TURN", "right_turn"), ("FORWARD_FLEXION_RECOVERY", "forward_flexion_recovery")):
        result.append({"action": "trunk", "phase": phase, "chain": "pelvis_to_torso", "factor": f"{prefix}_motion_plane", "classification": "MEASURED_OBSERVATION", "parent": "pelvis", "child": "torso", "components": 1})
    return result


def freeze_selection(
    observation: R1Observation,
    contract: Mapping[str, Any],
    *,
    remove_complete_pool: set[str] | None = None,
    withhold_selected: Mapping[str, np.ndarray] | None = None,
) -> tuple[tuple[FactorSelection, ...], dict[str, Any]]:
    """Freeze candidate pools and selected rows before objective evaluation."""
    remove_complete_pool = remove_complete_pool or set()
    withhold_selected = withhold_selected or {}
    segment_node = {segment: node for node, segment in observation.node_to_segment.items()}
    node_index = {node: index for index, node in enumerate(observation.node_order)}
    selections = []
    for definition in factor_definitions():
        action = definition["action"]
        plateau = observation.r3d_actions[action]["STATIC_PLATEAU_CANDIDATE"]
        source = (plateau["row_indices"] if plateau is not None else []) if definition["phase"] == "STATIC_PLATEAU" else observation.r3d_actions[action]["BROAD_ACTIVE_ROWS"]
        source = _phase_rows(observation, action, definition["phase"], np.asarray(source, int))
        required_segments = tuple(item for item in (definition["parent"], definition["child"]) if item is not None)
        validity = np.ones(len(source), bool)
        for segment in required_segments:
            validity &= observation.valid[source, node_index[segment_node[segment]]]
        candidate = source[validity]
        block_id = f"{action}::{definition['phase']}::{definition['chain']}::{definition['factor']}"
        if block_id in remove_complete_pool:
            candidate = np.empty(0, int)
        withheld = np.asarray(withhold_selected.get(block_id, np.empty(0, int)), int)
        if len(withheld): candidate = candidate[~np.isin(candidate, withheld)]
        maximum = int(contract["row_selection"]["maximum_static_rows_per_segment"] if definition["phase"] == "STATIC_PLATEAU" else contract["row_selection"]["maximum_dynamic_rows_per_factor"])
        selected = _uniform(candidate, maximum)
        namespace = {
            "action": action, "phase": definition["phase"], "chain": definition["chain"],
            "required_nodes": [segment_node[item] for item in required_segments],
            "validity": "ALL_REQUIRED_NODE_COMMON_TIME_VALID",
        }
        pool_id = "pool:" + _hash_json(namespace)
        validity_id = "validity:" + _hash_json({"pool": pool_id, "candidate_rows": candidate.tolist()})
        sigma = (
            math.radians(float(contract["measurement_covariance"]["static_direction_sigma_deg"]))
            if definition["phase"] == "STATIC_PLATEAU" else
            float(contract["measurement_covariance"]["elbow_curl_pronation_subspace_sigma_rad_s"])
            if definition["phase"] in ("CURL", "PRONATION_SUPINATION") else
            float(contract["measurement_covariance"]["trunk_motion_plane_sigma_rad_s"])
            if action == "trunk" else
            float(contract["measurement_covariance"]["dynamic_hinge_sigma_rad_s"])
        )
        weight = 1.0 / sigma / math.sqrt(len(selected)) if len(selected) else 0.0
        parent_node = segment_node[definition["parent"]] if definition["parent"] else None
        child_node = segment_node[definition["child"]]
        selections.append(FactorSelection(
            block_id, action, definition["phase"], definition["chain"], definition["factor"], definition["classification"],
            definition["parent"], definition["child"], parent_node, child_node,
            candidate, selected, pool_id, validity_id, "PRODUCTION_Q2_COMMON_TIME", "rotation" if definition["phase"] == "STATIC_PLATEAU" else "gyro_rad_s+rotation",
            "NO_SOURCE_SUPPORT" if not len(candidate) else "DETERMINISTIC_UNIFORM_WITHIN_FROZEN_ALLOWLIST",
            "measured plateau covariance plus human model mismatch" if definition["phase"] == "STATIC_PLATEAU" else "phase/chain dynamic covariance plus human model mismatch",
            weight, int(definition["components"]), "NO_SOURCE_SUPPORT" if not len(candidate) else "AVAILABLE",
        ))
    manifest = selection_manifest(observation, tuple(selections))
    return tuple(selections), manifest


def selection_manifest(observation: R1Observation, selections: tuple[FactorSelection, ...]) -> dict[str, Any]:
    rows = []
    for selection in selections:
        for source_row in selection.selected_rows:
            for component in range(selection.residual_components_per_sample):
                source_id = f"common_time:{int(source_row)}:{int(observation.time_ns[source_row])}"
                identity = {
                    "factor_block_id": selection.factor_block_id, "source_sample_id": source_id,
                    "component": component,
                }
                rows.append({
                    "residual_row_id": "residual:" + _hash_json(identity),
                    "factor_block_id": selection.factor_block_id, "action_id": selection.action_id,
                    "phase_id": selection.phase_id, "chain_id": selection.chain_id,
                    "parent_node": selection.parent_node, "child_node": selection.child_node,
                    "source_stream/channel": f"{selection.source_stream}/{selection.source_channel}",
                    "source_sample_id": source_id, "global_time_ns": int(observation.time_ns[source_row]),
                    "validity_mask_id": selection.validity_mask_id, "candidate_pool_id": selection.candidate_pool_id,
                    "selector_version": "D0B_R2_OBSERVATION_LINEAGE_V2",
                    "selection_reason": selection.selection_reason,
                    "whitening_source": selection.whitening_source, "weight": selection.weight,
                    "source_row": int(source_row), "component": component,
                })
    factors = [{
        "factor_block_id": item.factor_block_id, "action_id": item.action_id,
        "phase_id": item.phase_id, "chain_id": item.chain_id,
        "candidate_pool_id": item.candidate_pool_id, "candidate_rows": item.candidate_rows.tolist(),
        "selected_rows": item.selected_rows.tolist(), "status": item.status,
    } for item in selections]
    payload = {"schema": "biospur-d0b-r2-frozen-row-manifest-v1", "selector_version": "D0B_R2_OBSERVATION_LINEAGE_V2", "factors": factors, "residual_rows": rows}
    payload["manifest_sha256"] = _hash_json(payload)
    return payload


class R2Objective(R1Objective):
    def __init__(self, observation: R1Observation, contract: Mapping[str, Any], selections: tuple[FactorSelection, ...], manifest_sha256: str):
        super().__init__(observation, contract)
        self.selections = selections
        self.manifest_sha256 = manifest_sha256

    def _block_for(self, selection: FactorSelection, product: Mapping[str, Any], nuisance: Mapping[str, np.ndarray]) -> ResidualBlock | None:
        rows = selection.selected_rows
        if selection.status != "AVAILABLE" or not len(rows): return None
        if selection.phase_id == "STATIC_PLATEAU":
            predicted = articulated_pose_directions(selection.action_id, nuisance[selection.action_id])[selection.child_segment]
            observed = self.corrected_direction(product, selection.child_segment, rows)
            values = (s2_residual(np.tile(predicted, (len(rows), 1)), observed) * selection.weight).ravel()
            equation = "LogS2(d_articulated(q_pose), Rz(h_i) R_Ni_Bi a_Bi)"
            blocks = ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING", f"ARTICULATED_POSE_{selection.action_id}")
            nodes = (selection.child_node,)
            unit = "unit_direction/rad"
        else:
            rel = self._relative_omega(product, selection.parent_segment, selection.child_segment, rows)
            if selection.phase_id == "PRONATION_SUPINATION":
                axis = self.corrected_direction(product, selection.child_segment, rows)
                values = (np.cross(rel, axis) * selection.weight).ravel()
                equation = "cross(relative_gyro, instantaneous_child_long_axis)/sigma"
                blocks = ("SENSOR_LONGITUDINAL_AXIS", "EFFECTIVE_RELATIVE_HEADING")
            elif selection.action_id == "trunk":
                values = (rel @ product["trunk_normal"] * selection.weight).ravel()
                equation = "dot(relative_gyro, minimal_trunk_motion_plane_normal)/sigma"
                blocks = ("EFFECTIVE_RELATIVE_HEADING", "TRUNK_MOTION_PLANE_NORMAL")
            else:
                joint = selection.chain_id
                axis = np.tile(product["functional"][joint], (len(rows), 1))
                values = (np.cross(rel, axis) * selection.weight).ravel()
                equation = "cross(relative_gyro, functional_axis)/sigma"
                blocks = ("EFFECTIVE_RELATIVE_HEADING", "LIMB_FUNCTIONAL_AXIS")
            nodes = (selection.parent_node, selection.child_node)
            unit = "rad/s"
        return ResidualBlock(selection.action_id, selection.factor, selection.classification, values, rows, nodes, unit, equation, selection.whitening_source, blocks)

    def blocks(self, x: np.ndarray, include_nonmeasurement: bool = True) -> list[ResidualBlock]:
        from .d0b_r1_model import decode_full
        product, nuisance = decode_full(x)
        result = []
        for selection in self.selections:
            block = self._block_for(selection, product, nuisance)
            if block is not None: result.append(block)
        if include_nonmeasurement: result.extend(self.prior_blocks(nuisance))
        return result

    def residual(self, x: np.ndarray, include_nonmeasurement: bool = True) -> np.ndarray:
        blocks = self.blocks(x, include_nonmeasurement)
        if not blocks: raise ValueError("no residual blocks")
        value = np.concatenate([np.asarray(block.values, float) for block in blocks])
        if not np.isfinite(value).all(): raise ValueError("non-finite R2 residual")
        return value


def factor_value_map(objective: R2Objective, x: np.ndarray) -> dict[str, np.ndarray]:
    lookup = {(item.action_id, item.factor): item.factor_block_id for item in objective.selections}
    return {lookup[(block.action, block.factor)]: np.asarray(block.values).copy() for block in objective.blocks(x, False)}
