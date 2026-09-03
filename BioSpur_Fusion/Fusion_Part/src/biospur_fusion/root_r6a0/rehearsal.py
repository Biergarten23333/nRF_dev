"""Bounded real-C1 factor-wiring rehearsal. It never evaluates or updates a pose."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from .body import BodyModel, frozen_uncertain_calibration
from .contracts import (
    ActivationState,
    AuthorityScope,
    EvidenceRecord,
    EvidenceRepresentation,
    FactorProposal,
    FaultDomain,
    GraphSpec,
    ServiceDOF,
    state_blocks_for_keyframe,
)
from .evidence import EvidenceLedger


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while block := handle.read(16 << 20):
            digest.update(block)
    return digest.hexdigest()


def _imu_proposal(model: BodyModel, evidence: EvidenceRecord) -> FactorProposal:
    blocks = list(model.dependency_blocks("imu", evidence.owner_id, evidence.measurement_time_s))
    stamp = f"{evidence.measurement_time_s:.9f}"
    blocks.extend((f"kf:{stamp}:gyro_bias:{evidence.owner_id}", f"kf:{stamp}:accel_bias:{evidence.owner_id}"))
    return FactorProposal(
        f"c1_imu_interface:{evidence.event_uid}", "real_imu_preintegration_interface_blocked",
        (evidence.physical_event_uid,), evidence.raw_ancestry,
        evidence.measurement_time_s, evidence.availability_time_s,
        tuple(dict.fromkeys(blocks)), 6, evidence.covariance_provenance,
        (FaultDomain.SINGLE_EVENT, FaultDomain.IMU_NODE, FaultDomain.CLOCK_TIMING,
         FaultDomain.BODY_GEOMETRY, FaultDomain.SHARED_SOFTWARE_MODEL),
        ActivationState.BLOCKED, AuthorityScope.PROBE_ONLY,
        (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.JOINT_ANGLES, ServiceDOF.GLOBAL_POSITION,
         ServiceDOF.GLOBAL_YAW),
    )


def _uwb_proposal(model: BodyModel, evidence: EvidenceRecord, anchor_id: int) -> FactorProposal:
    blocks = list(model.dependency_blocks("tag", evidence.owner_id, evidence.measurement_time_s))
    blocks.extend((f"calibration:anchor_position:{anchor_id}", f"calibration:anchor_delay:{anchor_id}"))
    return FactorProposal(
        f"c1_raw_range:{evidence.event_uid}", "real_raw_uwb_range_wiring_shadow",
        (evidence.physical_event_uid,), evidence.raw_ancestry,
        evidence.measurement_time_s, evidence.availability_time_s,
        tuple(blocks), 1, evidence.covariance_provenance,
        (FaultDomain.SINGLE_EVENT, FaultDomain.TAG_ANCHOR_LINK, FaultDomain.TAG,
         FaultDomain.ANCHOR, FaultDomain.CLOCK_TIMING, FaultDomain.ANCHOR_MAP_FRAME,
         FaultDomain.BODY_GEOMETRY),
        ActivationState.SHADOW_ONLY, AuthorityScope.LOCAL_SEGMENT_ONLY,
        (ServiceDOF.BODY_RELATIVE_POSE, ServiceDOF.GLOBAL_POSITION, ServiceDOF.GLOBAL_YAW),
    )


def run_c1_wiring_rehearsal(*, model: BodyModel, typed_ledger_path: Path,
                            raw_range_events_path: Path, m1_path: Path,
                            event_accounting_path: Path, duration_s: float = 30.0) -> tuple[dict, GraphSpec]:
    """Instantiate all C1 evidence in one deterministic <=60 s interval."""
    if not 0.0 < duration_s <= 60.0:
        raise ValueError("C1 rehearsal duration must be in (0,60] seconds")
    typed_ledger_path = Path(typed_ledger_path).resolve()
    raw_range_events_path = Path(raw_range_events_path).resolve()
    m1_path = Path(m1_path).resolve()
    event_accounting_path = Path(event_accounting_path).resolve()
    for path in (typed_ledger_path, raw_range_events_path, m1_path, event_accounting_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    accounting = json.loads(event_accounting_path.read_text(encoding="utf-8"))
    formal_start_ns = int(accounting["formal_global_start_ns"])
    interval_start_s = 0.0
    interval_end_s = interval_start_s + float(duration_s)
    calibration = frozen_uncertain_calibration(model)
    ledger = EvidenceLedger()
    evidence_count = {"RAW_IMU": 0, "RAW_UWB": 0}
    imu_counts: dict[str, int] = {}
    uwb_counts: dict[str, int] = {node: 0 for node in model.tag_ids}
    proposals: list[FactorProposal] = []
    imu_examples = []
    uwb_examples = []
    with np.load(typed_ledger_path, allow_pickle=False) as archive:
        actual_imu_nodes = {key.removeprefix("imu_") for key in archive.files if key.startswith("imu_")}
        if actual_imu_nodes != set(model.imu_ids):
            raise RuntimeError(f"C1 IMU inventory mismatch: {sorted(actual_imu_nodes)}")
        for node in model.imu_ids:
            rows = archive[f"imu_{node}"]
            relative_time = (rows["global_time_ns"].astype(np.float64) - formal_start_ns) / 1e9
            selected = rows[(rows["status"] == 1) & (relative_time >= interval_start_s)
                            & (relative_time < interval_end_s)]
            imu_counts[node] = int(len(selected))
            for row in selected:
                measurement_time_s = (int(row["global_time_ns"]) - formal_start_ns) / 1e9
                uid = (f"C1:RAW_IMU:{node}:record={int(row['raw_record_index'])}:"
                       f"sample={int(row['raw_sample_index'])}:timer={int(row['node_timer_us'])}")
                evidence = EvidenceRecord(
                    uid, uid, EvidenceRepresentation.RAW_IMU, frozenset({uid}),
                    measurement_time_s, None, node,
                    f"typed_ledger_global_time_sigma_ns={int(row['global_time_sigma_ns'])};availability_common_time_unavailable",
                    (FaultDomain.SINGLE_EVENT, FaultDomain.IMU_NODE, FaultDomain.CLOCK_TIMING),
                    f"{typed_ledger_path}:record={int(row['raw_record_index'])}:sample={int(row['raw_sample_index'])}",
                )
                ledger.register(evidence)
                proposal = _imu_proposal(model, evidence)
                proposals.append(proposal); ledger.activate(proposal, (evidence.event_uid,))
                evidence_count["RAW_IMU"] += 1
                if len(imu_examples) < 10:
                    imu_examples.append({"event_uid": uid, "node": node, "measurement_time_s": measurement_time_s,
                                         "residual_dimension": proposal.residual_dimension,
                                         "activation": proposal.activation_state.value,
                                         "connected_blocks": list(proposal.connected_variable_blocks)})
    with np.load(m1_path, allow_pickle=False) as archive:
        historical_nodes = tuple(str(value) for value in archive["node_ids"])
        historical_segments = tuple(str(value) for value in archive["segment_names"])
    if set(historical_nodes) != set(model.tag_ids):
        raise RuntimeError("Root-R4 node inventory does not match active numerical identity inventory")
    historical_mapping = dict(zip(historical_nodes, historical_segments))
    mapping_mismatches = {node: {"historical": historical_mapping[node], "active": model.identity_mapping[node]}
                          for node in model.tag_ids if historical_mapping[node] != model.identity_mapping[node]}
    with np.load(raw_range_events_path, allow_pickle=False) as archive:
        tag_index = archive["tag_index"]
        measurement = archive["measurement_time_s"]
        availability = archive["availability_time_s"]
        selected_indices = np.flatnonzero((measurement >= interval_start_s) & (measurement < interval_end_s))
        for index in selected_indices:
            node = historical_nodes[int(tag_index[index])]
            anchor = int(archive["anchor_id"][index])
            uid = (f"C1:RAW_UWB:{node}:record={int(archive['raw_record_index'][index])}:"
                   f"anchor={anchor}:source={int(archive['source_index'][index])}")
            evidence = EvidenceRecord(
                uid, uid, EvidenceRepresentation.RAW_UWB, frozenset({uid}),
                float(measurement[index]), float(availability[index]), node,
                "ROOT_R4_EXACT_PER_LINK_TIME_AND_RAW_RANGE_PROVENANCE_REFERENCE_ONLY",
                (FaultDomain.SINGLE_EVENT, FaultDomain.TAG_ANCHOR_LINK, FaultDomain.TAG,
                 FaultDomain.ANCHOR, FaultDomain.CLOCK_TIMING),
                f"{raw_range_events_path}:row={int(index)}",
            )
            ledger.register(evidence)
            proposal = _uwb_proposal(model, evidence, anchor)
            proposals.append(proposal); ledger.activate(proposal, (evidence.event_uid,))
            uwb_counts[node] += 1; evidence_count["RAW_UWB"] += 1
            if len(uwb_examples) < 10:
                uwb_examples.append({"event_uid": uid, "node": node, "anchor": anchor,
                                     "measurement_time_s": float(measurement[index]),
                                     "availability_time_s": float(availability[index]),
                                     "residual_dimension": proposal.residual_dimension,
                                     "activation": proposal.activation_state.value,
                                     "connected_blocks": list(proposal.connected_variable_blocks)})
    state_blocks = state_blocks_for_keyframe(interval_start_s, model.joint_ids, model.imu_ids)
    graph = GraphSpec(
        schema="biospur.root_r6a0.graph_spec.v1",
        mode="REAL_C1_SHADOW",
        segments=model.segments, joints=model.joint_ids, imu_nodes=model.imu_ids,
        uwb_tags=model.tag_ids, anchors=tuple(range(8)), state_blocks=state_blocks,
        calibration_slots=tuple(calibration.slots[key] for key in sorted(calibration.slots)),
        factor_proposals=tuple(proposals),
        constraint_tiers={
            "A_exact_structural": ActivationState.ACTIVE_STRUCTURAL,
            "B_calibrated_invariants": ActivationState.BLOCKED,
            "C_soft_anatomy": ActivationState.SHADOW_ONLY,
            "D_contextual_biomechanics": ActivationState.DISABLED,
        },
        expected_nullspace=("global_translation_x", "global_translation_y", "global_translation_z", "global_yaw",
                            "calibration_motion_couplings"),
        inverse_problem="MAP_OVER_THIS_GRAPH_USING_SHARED_BODYMODEL_FK",
        production_authorized=False,
        metadata={
            "real_pose_optimized": False,
            "real_state_updated": False,
            "calibration_fitted": False,
            "real_preintegration": "BLOCKED_NOT_REQUALIFIED_FOR_ROOT_R6A0",
            "historical_mapping_mismatches": mapping_mismatches,
        },
    )
    validation = graph.validate()
    summary: dict[str, Any] = {
        "schema": "biospur.root_r6a0.c1_wiring_rehearsal.v1",
        "status": "COMPLETED_SHADOW_WIRING_ONLY",
        "interval_s": [interval_start_s, interval_end_s],
        "duration_s": duration_s,
        "inputs": {
            "typed_ledger": {"path": str(typed_ledger_path), "bytes": typed_ledger_path.stat().st_size, "sha256": sha256(typed_ledger_path)},
            "raw_range_events": {"path": str(raw_range_events_path), "bytes": raw_range_events_path.stat().st_size, "sha256": sha256(raw_range_events_path)},
            "m1_identity_reference": {"path": str(m1_path), "bytes": m1_path.stat().st_size, "sha256": sha256(m1_path)},
            "event_accounting": {"path": str(event_accounting_path), "bytes": event_accounting_path.stat().st_size, "sha256": sha256(event_accounting_path)},
        },
        "evidence_counts": evidence_count,
        "per_node_imu_counts": imu_counts,
        "per_tag_raw_uwb_link_counts": uwb_counts,
        "all_ten_imu_nodes_present": set(imu_counts) == set(model.imu_ids) and all(value > 0 for value in imu_counts.values()),
        "all_ten_uwb_tags_present": set(uwb_counts) == set(model.tag_ids) and all(value > 0 for value in uwb_counts.values()),
        "all_evidence_instantiated": sum(evidence_count.values()) == len(ledger.records),
        "factor_count": len(proposals),
        "factor_residual_shapes": {"raw_imu_interface": 6, "raw_uwb_range": 1},
        "factor_connectivity_examples": {"imu": imu_examples, "uwb": uwb_examples},
        "unresolved_calibration_slot_count": sum(slot.status.value == "FROZEN_UNCERTAIN" for slot in calibration.slots.values()),
        "unknown_values_are_none": all(slot.value is None for slot in calibration.slots.values()),
        "historical_root_r4_mapping_mismatches": mapping_mismatches,
        "historical_root_r4_used_as_state_input": False,
        "ancestry_audit": ledger.audit(),
        "graph_validation": validation,
        "real_pose_optimized": False,
        "real_state_updated": False,
        "real_preintegration_executed": False,
        "calibration_fitted": False,
        "threshold_fitted": False,
        "bad_device_label_created": False,
        "production_world_correction_generated": False,
        "production_authorized": False,
    }
    summary["pass"] = bool(
        summary["all_ten_imu_nodes_present"] and summary["all_ten_uwb_tags_present"]
        and summary["all_evidence_instantiated"] and summary["unknown_values_are_none"]
        and summary["ancestry_audit"]["pass"] and validation["pass"]
    )
    return summary, graph
