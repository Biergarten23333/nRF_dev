"""Read-only 120 ms body-epoch diagnostics for real C2 raw ranges.

The owner in this module never mutates a navigation or pose stream.  It turns
already clock-aligned node sweeps plus a strictly preceding body pose into an
auditable shared-root proposal.  Production admission remains a separate
transaction owner.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import numpy as np

from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import (
    DirectShadowEvidence,
)
from biospur_fusion.c2_uwb_calibration.shared_root import (
    SharedRangeLink,
    SharedRootResult,
    solve_shared_root,
)
from biospur_fusion.root_r3.models import RootState

from .u0 import ClockModel, UwbRow


C2_BODY_NODES = (
    "BSFEC35", "BSFB165", "BSFAA61", "BSF1120", "BSF31CC",
    "BSFC2CC", "BSF44AD", "BSF3C79", "BSF6C53", "BSF8BC4",
)
DIAGNOSTIC_NODE_RESIDUAL_RMS_LIMIT_M = 0.50


def _readonly3(value: object) -> np.ndarray:
    result = np.asarray(value, dtype=float).reshape(3).copy()
    if not np.isfinite(result).all():
        raise ValueError("expected a finite three-vector")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class NodeSweepInput:
    """One raw node sweep with only pre-outcome pose/geometry evidence."""

    row: UwbRow
    clock: ClockModel
    tag_offset_world_m: np.ndarray
    tag_offset_velocity_world_mps: np.ndarray
    shadow_evidence: tuple[DirectShadowEvidence, ...]
    pose_global_ns: int
    pose_age_ns: float

    def __post_init__(self) -> None:
        if self.row.node not in C2_BODY_NODES:
            raise ValueError("unknown C2 body node")
        if self.row.boot != self.clock.boot_epoch:
            raise ValueError("node sweep and clock boot differ")
        if len(self.shadow_evidence) != 8 or any(
            item.node != self.row.node for item in self.shadow_evidence
        ):
            raise ValueError("shadow evidence must contain exact A-H node rays")
        if not math.isfinite(float(self.pose_age_ns)) or not 0.0 < self.pose_age_ns <= 5_005_000.0:
            raise ValueError("node sweep requires a fresh strictly preceding pose")
        object.__setattr__(self, "tag_offset_world_m", _readonly3(self.tag_offset_world_m))
        object.__setattr__(
            self, "tag_offset_velocity_world_mps",
            _readonly3(self.tag_offset_velocity_world_mps),
        )

    @property
    def reference_time_s(self) -> float:
        slots = self.valid_slots
        if len(slots) < 4:
            raise ValueError("node sweep has fewer than four valid links")
        return float(np.median([
            self.clock.seconds(self.row.strobe_us + 0.5 * self.row.t_round_us[slot])
            for slot in slots
        ]))

    @property
    def valid_slots(self) -> tuple[int, ...]:
        if tuple(self.row.anchor_ids) != tuple(range(8)):
            return ()
        return tuple(
            slot for slot in range(8)
            if self.row.valid_mask & (1 << slot)
            and 0 < self.row.ranges_mm[slot] < 0xFFFF
        )


@dataclass(frozen=True)
class NodeCandidateFact:
    node: str
    sweep: int
    reference_time_s: float
    valid_links: int
    result: SharedRootResult
    residual_rms_m: float
    direct_usable: bool
    direct_reason: str
    links: tuple[SharedRangeLink, ...]
    evidence: tuple[DirectShadowEvidence, ...]


@dataclass(frozen=True)
class BodyEpochFact:
    epoch_sequence: int
    reference_time_s: float
    imu_prediction: RootState
    node_facts: tuple[NodeCandidateFact, ...]
    direct_nodes: tuple[str, ...]
    propagated_nodes: tuple[str, ...]
    shared_result: SharedRootResult | None

    @property
    def candidate_delta_m(self) -> np.ndarray | None:
        if self.shared_result is None or not self.shared_result.success:
            return None
        return self.shared_result.root_position_m - self.imu_prediction.position_m


def _links_for_sweep(
    sweep: NodeSweepInput,
    *,
    body_reference_time_s: float,
    nominal_sigma_m: float,
) -> tuple[SharedRangeLink, ...]:
    if not math.isfinite(nominal_sigma_m) or nominal_sigma_m <= 0.0:
        raise ValueError("nominal sigma must be positive")
    links = []
    for slot in sweep.valid_slots:
        evidence = sweep.shadow_evidence[slot]
        link_time_s = sweep.clock.seconds(
            sweep.row.strobe_us + 0.5 * sweep.row.t_round_us[slot]
        )
        links.append(SharedRangeLink(
            node=sweep.row.node,
            anchor=slot,
            range_m=float(sweep.row.ranges_mm[slot]) / 1000.0,
            tag_offset_world_m=sweep.tag_offset_world_m,
            link_dt_s=float(link_time_s - body_reference_time_s),
            sigma_m=float(nominal_sigma_m)
            * math.sqrt(100.0 / max(float(sweep.row.quality[slot]), 1.0)),
            facing_score=float(evidence.own_facing_score),
            information_weight=float(evidence.b_combined_weight),
            body_occlusion_score=float(max(
                evidence.torso_severity, evidence.limb_severity,
            )),
            body_occluder=(
                "torso" if evidence.torso_severity >= evidence.limb_severity
                and evidence.torso_severity > 0.0 else
                "limb" if evidence.limb_severity > 0.0 else None
            ),
        ))
    return tuple(links)


def evaluate_body_epoch(
    *,
    epoch_sequence: int,
    imu_prediction: RootState,
    sweeps: Sequence[NodeSweepInput],
    anchors_m: np.ndarray,
    nominal_sigma_m: float = 0.12,
    node_residual_rms_limit_m: float = DIAGNOSTIC_NODE_RESIDUAL_RMS_LIMIT_M,
    solver_seed_root_m: np.ndarray | None = None,
    solver_seed_velocity_mps: np.ndarray | None = None,
    root_bounds_m: tuple[np.ndarray, np.ndarray] | None = None,
) -> BodyEpochFact:
    """Build one no-commit shared-root proposal from a 120 ms body epoch.

    A node is directly usable only when its own A-H geometry solves and its
    physical residual RMS is below the existing broad diagnostic guard.  The
    remaining nodes are explicitly reported as FK-propagated, not silently
    discarded.  The final proposal is solved only from directly usable nodes.
    """

    if isinstance(epoch_sequence, bool) or epoch_sequence < 0:
        raise ValueError("epoch sequence must be nonnegative")
    if not sweeps:
        raise ValueError("body epoch is empty")
    if len({item.row.node for item in sweeps}) != len(sweeps):
        raise ValueError("body epoch contains duplicate node sweeps")
    if not math.isfinite(node_residual_rms_limit_m) or node_residual_rms_limit_m <= 0.0:
        raise ValueError("node residual guard must be positive")
    anchors = np.asarray(anchors_m, dtype=float)
    if anchors.shape != (8, 3) or not np.isfinite(anchors).all():
        raise ValueError("anchors must be a finite canonical 8x3 layout")
    reference = float(imu_prediction.time_s)
    solver_seed = (
        imu_prediction.position_m if solver_seed_root_m is None
        else _readonly3(solver_seed_root_m)
    )
    solver_velocity = (
        imu_prediction.velocity_mps if solver_seed_velocity_mps is None
        else _readonly3(solver_seed_velocity_mps)
    )
    facts = []
    accepted_links: list[SharedRangeLink] = []
    for sweep in sorted(sweeps, key=lambda item: item.row.node):
        links = _links_for_sweep(
            sweep,
            body_reference_time_s=reference,
            nominal_sigma_m=nominal_sigma_m,
        )
        if len(links) < 4:
            result = solve_shared_root(
                links, anchors_m=anchors,
                initial_root_m=solver_seed,
                root_velocity_mps=solver_velocity,
                root_bounds_m=root_bounds_m,
            )
            rms = math.inf
            usable = False
            reason = "FEWER_THAN_FOUR_VALID_LINKS"
        else:
            result = solve_shared_root(
                links, anchors_m=anchors,
                initial_root_m=solver_seed,
                root_velocity_mps=solver_velocity,
                root_bounds_m=root_bounds_m,
            )
            rms = (
                float(np.sqrt(np.mean(np.square(result.residuals_m))))
                if len(result.residuals_m) else math.inf
            )
            usable = bool(
                result.success and result.rank == 3
                and math.isfinite(rms) and rms <= node_residual_rms_limit_m
            )
            reason = (
                "DIRECT_GEOMETRY_AND_RESIDUAL_USABLE" if usable
                else result.reason if not result.success
                else "NODE_RESIDUAL_RMS_REJECT"
            )
        if usable:
            accepted_links.extend(links)
        facts.append(NodeCandidateFact(
            sweep.row.node, int(sweep.row.sweep), sweep.reference_time_s,
            len(links), result, rms, usable, reason, links,
            sweep.shadow_evidence,
        ))
    direct = tuple(sorted(fact.node for fact in facts if fact.direct_usable))
    propagated = tuple(sorted(set(C2_BODY_NODES) - set(direct)))
    shared = None
    if accepted_links:
        shared = solve_shared_root(
            accepted_links,
            anchors_m=anchors,
            initial_root_m=solver_seed,
            root_velocity_mps=solver_velocity,
            root_bounds_m=root_bounds_m,
        )
    return BodyEpochFact(
        epoch_sequence, reference, imu_prediction, tuple(facts), direct,
        propagated, shared,
    )


def group_rows_by_pelvis_epoch(
    rows: Sequence[UwbRow],
    clocks: Mapping[str, ClockModel],
) -> tuple[tuple[UwbRow, ...], ...]:
    """Group asynchronous node sweeps by consecutive pelvis 120 ms epochs."""

    timed = []
    for row in rows:
        if row.node not in clocks or row.boot != clocks[row.node].boot_epoch:
            continue
        timed.append((clocks[row.node].seconds(row.strobe_us), row))
    timed.sort(key=lambda item: (item[0], item[1].node, item[1].sweep))
    pelvis = [time_s for time_s, row in timed if row.node == "BSFC2CC"]
    if len(pelvis) < 2 or np.any(np.diff(pelvis) <= 0.0):
        raise ValueError("pelvis epoch boundaries are unavailable")
    groups = []
    for start, stop in zip(pelvis, pelvis[1:]):
        selected = [row for time_s, row in timed if start <= time_s < stop]
        # At most one sweep per node belongs to one 120 ms body transaction.
        by_node = {}
        for row in selected:
            if row.node in by_node:
                raise ValueError("duplicate node sweep inside pelvis epoch")
            by_node[row.node] = row
        groups.append(tuple(by_node[node] for node in C2_BODY_NODES if node in by_node))
    return tuple(groups)
