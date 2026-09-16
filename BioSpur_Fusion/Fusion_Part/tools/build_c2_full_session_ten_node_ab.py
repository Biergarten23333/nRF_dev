#!/usr/bin/env python3
"""Construct, but never execute, the complete-session ten-node A/B owners."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, is_dataclass
from functools import partial
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import stat
import resource
import time
from typing import Any, Mapping
from collections import Counter

import numpy as np

from biospur_fusion.c2_3a_kinematics import load_frozen_c2_3a
from biospur_fusion.c2_articulated_biomechanics.model import fit_articulated_model
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import project_hinge_corrections
from biospur_fusion.c2_coupled_progressive.continuous_frontend import (
    CONTINUOUS_FRONTEND_SCHEMA, ContinuousClockOwner, NodeClockBinding,
)
from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import FullSessionContinuousReader
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import (
    CONTINUOUS_HINGE_RETENTION_CONTRACT,
)
from biospur_fusion.c2_coupled_progressive.continuous_session_initializer import (
    CalibratedImuErrorStateOrigin, ContinuousSessionInitializer,
    InitializationCovariancePolicy, InitializationStateOwner,
    ProspectiveInitializationPolicy,
)
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES, NODE_TO_SEGMENT
from biospur_fusion.c2_coupled_progressive.full_session_ten_node_ab import FullSessionTenNodeABCoordinator
from biospur_fusion.c2_coupled_progressive.native200_publication_producer import Native200PublicationProducer
from biospur_fusion.c2_uwb_calibration.adaptive_nodes import AdaptiveNodeTrustConfig
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectNodeLinkClock
from biospur_fusion.c2_uwb_root_world.causal_update_guard import ReachabilityClass, ReachabilityEnvelope
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusDriftOwner,
)
from biospur_fusion.c2_uwb_root_world.full_session_body_pose import FullSessionBodyPoseOwner, SESSION_ID
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import U3SigmaOwner, U5BSigmaOwner
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
    ROOT_FREE_REACHABILITY_EVIDENCE_SCHEMA, ROOT_FREE_REACHABILITY_ROLE,
    RangeInformationOwner, ReachabilityPolicyEvidence, RootFreeReferenceOwnerTemplate,
    materialize_mechanism_reference_owner,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models_document
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig
from biospur_fusion.c2_uwb_root_world.split_fusion import FixedLagConsensusDriftConfig
from biospur_fusion.root_r3 import RootFilterConfig


ROOT = Path(__file__).resolve().parents[1]
LAYOUT = ROOT.parent / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
CLOCK = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
HINGE_REPORT = ROOT / "logs/c2_native200_calibration_v3_20260904/POSE_RESET_QMT_DIAGNOSTIC.json"
HINGE_TRAJECTORY = ROOT / "logs/c2_native200_calibration_v3_20260904/POSE_RESET_QMT_TRAJECTORY.npz"
CONSENSUS_DRIFT_CONFIG_SOURCE = ROOT / "tools/build_c2_calibration_continuous_consensus_ab.py"
EXPECTED = {
    LAYOUT: "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1",
    CLOCK: "b3c18d2d0ece3826498d2adc3cd41f3e4412794557f8525adc2f73bfa4ae3a66",
    HINGE_REPORT: "cc96b1ba03c0a0ab36ca807ae8c86e0aea4284d0a918981be71df496315c3d4f",
    HINGE_TRAJECTORY: "6f93d7a0efbe3d1bfcc8cef9e2dbd81c1d99ff4ba46b163eab37de9398eada12",
    CONSENSUS_DRIFT_CONFIG_SOURCE: "16cca24d4d68107676c1ab022f2792f3f2028fc0b5f97099ecc80e14c2bd05b0",
}


def _stable_bytes(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"factory input is not regular: {path}")
        chunks = []
        while block := os.read(fd, 1 << 20):
            chunks.append(block)
        after = os.fstat(fd)
    finally:
        os.close(fd)
    payload = b"".join(chunks)
    identity = lambda row: (row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns)
    if identity(before) != identity(after) or hashlib.sha256(payload).hexdigest() != EXPECTED[path]:
        raise RuntimeError(f"factory input identity changed: {path}")
    return payload


def _static_inputs():
    layout = json.loads(_stable_bytes(LAYOUT))
    clock_document = json.loads(_stable_bytes(CLOCK))
    models = _clock_models_document(clock_document, source_sha256=clock_document["source_sha256"])
    bindings, clocks = [], {}
    for node, model in sorted(models.items()):
        row = clock_document["models"][node]
        mapping = hashlib.sha256(json.dumps({
            "node": node, "boot_epoch": model.boot_epoch,
            "a_ns_per_us": model.a_ns_per_us, "b_ns": model.b_ns,
            "clock_owner_sha256": EXPECTED[CLOCK],
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        bindings.append(NodeClockBinding(
            node, model.boot_epoch, "B306_TIMER2", mapping,
            model.a_ns_per_us, model.b_ns, EXPECTED[CLOCK], clock_document["source_sha256"],
        ))
        clocks[node] = DirectNodeLinkClock(
            node, model.a_ns_per_us, model.b_ns, model.boot_epoch,
            int(row["first_timer_us"]), int(row["last_timer_us"]),
        )
    if set(clocks) != set(NODE_TO_SEGMENT):
        raise RuntimeError("factory clock inventory is not the exact ten nodes")
    rows = layout["anchors"]
    if layout["anchor_ids"] != list(range(8)) or [row["id"] for row in rows] != list(range(8)):
        raise RuntimeError("factory layout lacks canonical anchors")
    anchors = np.asarray([[row[key] for key in ("x_mm", "y_mm", "z_mm")] for row in rows]) / 1_000.0
    delays = np.asarray([row["d_anchor_mm"] for row in rows]) / 1_000.0
    return ContinuousClockOwner(CONTINUOUS_FRONTEND_SCHEMA, tuple(bindings)), clocks, anchors, delays, float(layout["tag_delay_mm"]) / 1_000.0


def _hinges():
    report = json.loads(_stable_bytes(HINGE_REPORT))
    trajectory = {}
    with np.load(BytesIO(_stable_bytes(HINGE_TRAJECTORY)), allow_pickle=False) as archive:
        for index, _episode in enumerate(EPISODES):
            key = f"{index:02d}"
            trajectory[key] = {segment: {
                "quat_world_segment_wxyz": np.array(archive[f"trajectory/{key}/{segment}/quat_world_segment_wxyz"]),
            } for segment in NODE_TO_SEGMENT.values()}
    model = fit_articulated_model({"trajectory": trajectory}, report)
    if set(model) != {"elbow_left", "elbow_right", "knee_left", "knee_right"}:
        raise RuntimeError("accepted hinge owner did not reconstruct four hinges")
    return model


def _b_consensus_drift_owner() -> ContinuousConsensusDriftOwner:
    """Build the B-only velocity correction from the sealed diagnostic policy."""
    _stable_bytes(CONSENSUS_DRIFT_CONFIG_SOURCE)
    config = FixedLagConsensusDriftConfig(
        minimum_lag_s=0.32,
        maximum_lag_s=0.72,
        update_period_s=0.48,
        minimum_consensus_pairs=4,
        rank_relative_tolerance=1e-2,
        maximum_velocity_step_mps=0.50,
        covariance_floor=1e-12,
        acceleration_bias=None,
    )
    stream_owner_digest = hashlib.sha256(json.dumps({
        "session_id": SESSION_ID,
        "role": "B_DIAGNOSTIC_UNQUALIFIED_CONTINUOUS_SHARED_ROOT_CONSENSUS_VELOCITY",
        "config_source_sha256": EXPECTED[CONSENSUS_DRIFT_CONFIG_SOURCE],
        "config": asdict(config),
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return ContinuousConsensusDriftOwner(
        config=config, stream_owner_digest=stream_owner_digest,
    )


@dataclass(frozen=True)
class ConstructedFullSessionTenNodeAB:
    reader: FullSessionContinuousReader
    coordinator: FullSessionTenNodeABCoordinator
    clock_owner: ContinuousClockOwner
    static_digest: str
    body_owner_digest: str


def build(*, root: Path = ROOT) -> ConstructedFullSessionTenNodeAB:
    if Path(root).resolve() != ROOT:
        raise ValueError("factory accepts only the canonical workspace")
    clock_owner, clocks, anchors, delays, tag_delay = _static_inputs()
    producer = Native200PublicationProducer.from_sealed_archives()
    kinematics = load_frozen_c2_3a()
    hinges = _hinges()
    body = FullSessionBodyPoseOwner(clock_owner, producer, hinges, kinematics=kinematics)
    range_config = RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=0.12)
    a_sigma = U3SigmaOwner(0.0564866166214546, 0.10, "SEALED_U3_LAYOUT_PLUS_FLOOR_REFERENCE")
    b_sigma = U5BSigmaOwner(range_config, range_config.uncertainty_provenance)
    envelope = ReachabilityEnvelope(
        ReachabilityClass.NOMINAL, 20.0, 100.0, 1000.0, 1.0, 100.0, 1000.0,
        1.0, 1.0, 1.0, 0.01, 2, 20.0, 1e8,
        "EXISTING_U1_NOMINAL_ROOT_POSITION_POLICY",
    )
    reachability = ReachabilityPolicyEvidence(
        ROOT_FREE_REACHABILITY_EVIDENCE_SCHEMA, ROOT_FREE_REACHABILITY_ROLE,
        envelope, EXPECTED[LAYOUT], EXPECTED[CLOCK], "MECHANISM_ONLY_UNQUALIFIED",
    )
    range_owner = RangeInformationOwner(
        0.12, 0.12, {node: np.ones(8) for node in clocks},
        "PROVISIONAL_UNCALIBRATED_DIAGNOSTIC_FULL_SESSION",
    )
    template = RootFreeReferenceOwnerTemplate(
        RootFilterConfig(), True, anchors, clocks, delays, tag_delay, range_owner,
        reachability, AdaptiveNodeTrustConfig(), "EXISTING_ROOT_FILTER_CONFIG",
        f"SEALED_LAYOUT_SHA256:{EXPECTED[LAYOUT]}",
        f"CLOCK_TABLE_SHA256:{EXPECTED[CLOCK]}", "U1_NOMINAL_ROOT_POSITION_POLICY",
    )
    first = min(clock.a_ns_per_us * clock.first_timer_us + clock.b_ns
                for clock in clocks.values()) * 1e-9
    last = max(clock.a_ns_per_us * clock.last_timer_us + clock.b_ns
               for clock in clocks.values()) * 1e-9
    identity = {segment: np.eye(3) for segment in NODE_TO_SEGMENT.values()}
    pose_factory = lambda: CausalArticulatedPose(
        action_start_s=first, action_stop_s=last,
        rotations_at_fraction=lambda _fraction: identity,
        geometry=kinematics.geometry,
        hinge_projector=partial(project_hinge_corrections, model=hinges),
        hinge_temporal_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
    )
    covariance = InitializationCovariancePolicy(
        1e-6, 1.0, 0.25, "FULL_SESSION_PRIOR_FREE_JACOBIAN_COVARIANCE",
    )
    initializer = ContinuousSessionInitializer(
        static_template=template, pose_factory=pose_factory,
        a_sigma_owner=a_sigma, b_sigma_owner=b_sigma,
        native200_clock_owner_sha256=EXPECTED[CLOCK],
        native200_base_pose_owner_digest=body.owner_digest,
        b_shadow_provenance="FROZEN_C2_DISPLAY_PROXY_BODY_HISTORY",
        history_provenance="FULL_SESSION_TEN_NODE_NATIVE200_HISTORY",
        acquired_action_ids=frozenset((SESSION_ID,)),
        policy=ProspectiveInitializationPolicy(), covariance_policy=covariance,
        state_owner=InitializationStateOwner(
            None, CalibratedImuErrorStateOrigin(producer.frontend_sha256, EXPECTED[HINGE_REPORT], True),
            covariance.digest,
        ), clock_owner=clock_owner,
        reference_materializer=materialize_mechanism_reference_owner,
        b_consensus_drift=_b_consensus_drift_owner(),
    )
    coordinator = FullSessionTenNodeABCoordinator(body=body, initializer=initializer)
    reader = FullSessionContinuousReader(root=ROOT, clock_owner=clock_owner)
    return ConstructedFullSessionTenNodeAB(reader, coordinator, clock_owner, template.digest, body.owner_digest)


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return value


def _root_summary(snapshot) -> dict[str, Any]:
    state = snapshot.root_state
    return {
        "time_s": state.time_s,
        "vector": state.vector.tolist(),
        "covariance_diagonal": np.diag(state.covariance).tolist(),
        "publication_revision": snapshot.publication_revision,
        "publication_digest": snapshot.publication_digest,
        "counters": dict(snapshot.counters),
        "journal": _jsonable(snapshot.journal),
        "direct_node_histogram": None,
        "propagated_node_histogram": None,
        "node_histogram_status": "UNAVAILABLE_NOT_RETAINED_BY_ENGINE",
    }


def _admission_identity(item: object) -> tuple[object, ...]:
    return (
        item.branch, item.packet_digest, item.epoch_digest,
        item.candidate_digest, item.bucket, item.source_sequence,
        item.source_identity, tuple(item.trusted_partition),
        item.prepared_accepted, item.prepared_reason,
        item.commit_intent, item.commit_attempted, item.commit_succeeded,
        item.outcome, item.pre_pose_digest, item.result_pose_digest,
        item.diagnostic_digest,
        None if item.diagnostic is None else _jsonable(item.diagnostic),
    )


def _accepted_admission_classification(item: object | None) -> str | None:
    if item is None or not item.prepared_accepted:
        return None
    reason = str(item.prepared_reason)
    if reason.startswith("ACCEPTED_ROOT_FALLBACK_ARTICULATED_REJECTED:"):
        return "ARTICULATED_REJECTED_ROOT_FALLBACK"
    if reason == "ACCEPTED_ROOT_FALLBACK_OBSOLETE_NATIVE200_SOURCE_PAIR":
        return "OBSOLETE_NATIVE200_SOURCE_PAIR_ROOT_FALLBACK"
    if reason == "ACCEPTED" and len(item.trusted_partition) > 1:
        return "PRIMARY_ARTICULATED"
    return "OTHER_ACCEPTED"


class _WholeSessionMetrics:
    """Bounded observation-only summaries of already committed owner state."""

    def __init__(self, started: float) -> None:
        self.started = started
        self.checkpoints: list[dict[str, Any]] = []
        self.next_event = 250_000
        self.next_group = 500
        self.groups = 0
        self.pending_high_water = 0
        self.initial: dict[str, np.ndarray] = {}
        self.previous: dict[str, np.ndarray] = {}
        self.path = {"a": 0.0, "b": 0.0}
        self.radius = {"a": 0.0, "b": 0.0}
        self.correction = {"a": 0.0, "b": 0.0}
        self.credible = {"a": Counter(), "b": Counter()}
        self.accepted = Counter()
        self.rejected = Counter()
        self.rejection_reasons = {"a": Counter(), "b": Counter()}
        self.latest_articulated_rejection = {"a": None, "b": None}
        self.b_commit_intent = self.b_commit_attempted = self.b_commit_succeeded = 0
        self.b_outcomes = Counter()
        self.primary_articulated_accepted = Counter()
        self.root_fallback_accepted = Counter()
        self.articulated_rejected_root_fallback_accepted = Counter()
        self.obsolete_source_pair_root_fallback_accepted = Counter()
        self.root_fallback_reasons = Counter()
        self.other_accepted = Counter()
        self.diagnostic_counts = Counter()
        self.pose_publication_checks = Counter()
        self.latest_pose_publication_check: dict[str, Any] | None = None
        self._last_ab_transaction_total = 0
        self.admission_pairs = 0

    @staticmethod
    def _rejections(counters: Mapping[str, int]) -> dict[str, int]:
        return {
            key: int(value) for key, value in sorted(counters.items())
            if any(word in key for word in ("REJECT", "CAUSAL", "TEMPORAL", "CONTACT"))
        }

    def _validated_unseen_admission_pairs(
        self, coordinator: object,
    ) -> tuple[object, ...]:
        audit = coordinator.audit()
        journal = tuple(audit.ab_transaction_journal)
        total = audit.ab_transaction_total
        if (type(total) is not int or total < 0 or len(journal) > 64
                or total < len(journal)):
            raise RuntimeError("authoritative A/B transaction counter is invalid")
        ring_start = total - len(journal)
        if self._last_ab_transaction_total > total:
            raise RuntimeError("authoritative A/B transaction counter regressed")
        if self._last_ab_transaction_total < ring_start:
            raise RuntimeError("authoritative A/B transaction journal overflowed observer cursor")
        unseen = journal[self._last_ab_transaction_total - ring_start:]
        for item in unseen:
            if (type(item.provenance_digest) is not str
                    or len(item.provenance_digest) != 64
                    or item.a is None or item.b is None):
                raise RuntimeError("authoritative A/B transaction provenance is invalid")
        return unseen

    def consume_authoritative_admissions(self, coordinator: object) -> None:
        pairs = self._validated_unseen_admission_pairs(coordinator)
        for pair in pairs:
            for disposition in (pair.a, pair.b):
                item = disposition.admission
                if (_accepted_admission_classification(item)
                        == "OBSOLETE_NATIVE200_SOURCE_PAIR_ROOT_FALLBACK"
                        and item.pre_pose_digest != item.result_pose_digest):
                    raise RuntimeError(
                        "obsolete source-pair root fallback changed pose/joint state"
                    )
        for pair in pairs:
            for branch, disposition in (("a", pair.a), ("b", pair.b)):
                item = disposition.admission
                if item is None:
                    self.rejection_reasons[branch][str(disposition.reason)] += 1
                    continue
                self.credible[branch][str(len(item.trusted_partition))] += 1
                if item.prepared_accepted:
                    self.accepted[branch] += 1
                else:
                    self.rejected[branch] += 1
                    self.rejection_reasons[branch][str(item.prepared_reason)] += 1
                if item.diagnostic is not None:
                    self.diagnostic_counts[branch] += 1
                    self.latest_articulated_rejection[branch] = _jsonable(item.diagnostic)
            right = pair.b.admission
            if right is not None:
                self.b_commit_intent += int(right.commit_intent)
                self.b_commit_attempted += int(right.commit_attempted)
                self.b_commit_succeeded += int(right.commit_succeeded)
                self.b_outcomes[str(right.outcome)] += 1
            reason = None if right is None else str(right.prepared_reason)
            classification = _accepted_admission_classification(right)
            if classification == "ARTICULATED_REJECTED_ROOT_FALLBACK":
                self.root_fallback_accepted["b"] += 1
                self.articulated_rejected_root_fallback_accepted["b"] += 1
                self.root_fallback_reasons[reason] += 1
            elif classification == "OBSOLETE_NATIVE200_SOURCE_PAIR_ROOT_FALLBACK":
                self.root_fallback_accepted["b"] += 1
                self.obsolete_source_pair_root_fallback_accepted["b"] += 1
                self.root_fallback_reasons[reason] += 1
            elif classification == "PRIMARY_ARTICULATED":
                self.primary_articulated_accepted["b"] += 1
            elif classification == "OTHER_ACCEPTED":
                self.other_accepted["b"] += 1
            if classification is not None:
                self.pose_publication_checks[classification] += 1
                self.latest_pose_publication_check = {
                    "classification": classification,
                    "pre_pose_digest": right.pre_pose_digest,
                    "result_pose_digest": right.result_pose_digest,
                    "pose_unchanged": (
                        right.pre_pose_digest == right.result_pose_digest
                    ),
                }
            self.admission_pairs += 1
        self._last_ab_transaction_total += len(pairs)
    def _roots(self, coordinator, *, update_path: bool = True) -> dict[str, Any]:
        if coordinator._a is None:
            return {"a": None, "b": None}
        publication = coordinator.diagnostic_publication()
        output = {}
        for name, snapshot in (("a", publication.a), ("b", publication.b)):
            point = np.asarray(snapshot.root_state.vector[:3], float)
            if name not in self.initial:
                self.initial[name] = point.copy()
                self.previous[name] = point.copy()
            if update_path:
                step = float(np.linalg.norm(point - self.previous[name]))
                self.path[name] += step
                self.correction[name] = step
                self.radius[name] = max(
                    self.radius[name], float(np.linalg.norm(point - self.initial[name])),
                )
                self.previous[name] = point.copy()
            counters = dict(snapshot.counters)
            output[name] = {
                "root_m": point.tolist(), "path_length_m": self.path[name],
                "displacement_m": float(np.linalg.norm(point - self.initial[name])),
                "maximum_radius_m": self.radius[name],
                "correction_m": self.correction[name],
                "accepted_updates": int(self.accepted[name]),
                "rejected_updates": int(self.rejected[name]),
                "credible_node_x_of_10_histogram": dict(sorted(self.credible[name].items())),
                "causal_temporal_contact_rejections": {
                    **self._rejections(counters),
                    **{key: int(value) for key, value in
                       sorted(self.rejection_reasons[name].items())
                       if any(word in key for word in
                              ("REJECT", "CAUSAL", "TEMPORAL", "CONTACT"))},
                },
                "latest_articulated_range_rejection": (
                    self.latest_articulated_rejection[name]
                ),
                "prepared_accepted": int(self.accepted[name]),
                "prepared_rejected": int(self.rejected[name]),
                "diagnostic_count": int(self.diagnostic_counts[name]),
                "primary_articulated_accepted": int(self.primary_articulated_accepted[name]),
                "accepted_root_fallback": int(self.root_fallback_accepted[name]),
                "accepted_root_fallback_articulated_rejected": int(
                    self.articulated_rejected_root_fallback_accepted[name]
                ),
                "accepted_root_fallback_obsolete_native200_source_pair": int(
                    self.obsolete_source_pair_root_fallback_accepted[name]
                ),
                "other_accepted": int(self.other_accepted[name]),
            }
            if name == "b":
                classified = (
                    self.primary_articulated_accepted[name]
                    + self.root_fallback_accepted[name]
                    + self.other_accepted[name]
                )
                if classified != self.accepted[name]:
                    raise RuntimeError("accepted admission accounting does not conserve")
                output[name].update({
                    "commit_intent": self.b_commit_intent,
                    "commit_attempted": self.b_commit_attempted,
                    "commit_succeeded": self.b_commit_succeeded,
                    "commit_outcomes": dict(sorted(self.b_outcomes.items())),
                    "root_fallback_reasons": dict(sorted(self.root_fallback_reasons.items())),
                    "accepted_admission_accounting_conserved": True,
                    "pose_publication_checks": dict(
                        sorted(self.pose_publication_checks.items())
                    ),
                    "latest_pose_publication_check": self.latest_pose_publication_check,
                })
        return output

    def checkpoint(self, coordinator, *, batches: int, final: bool) -> None:
        if len(self.checkpoints) >= 64:
            raise OverflowError("whole-session checkpoint capacity exceeded")
        audit = coordinator.audit()
        pending = len(coordinator._pending) + int(coordinator._deferred is not None)
        if coordinator._a is not None:
            groups = dict(coordinator._a.counters).get("PREPARED_COMPLETE_GROUP", 0)
            self.groups = max(self.groups, int(groups))
        self.pending_high_water = max(self.pending_high_water, pending)
        row = {
            "final": bool(final), "elapsed_wall_s": time.monotonic() - self.started,
            "events": audit.events, "batches": batches, "groups": self.groups,
            "roots": self._roots(coordinator, update_path=False), "pending": pending,
            "pending_high_water": self.pending_high_water,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        if final and self.checkpoints and self.checkpoints[-1]["final"]:
            raise RuntimeError("whole-session final checkpoint is not unique")
        self.checkpoints.append(row)

    def due(self, coordinator, *, batches: int) -> bool:
        audit = coordinator.audit()
        if coordinator._a is not None:
            self.groups = max(
                self.groups,
                int(dict(coordinator._a.counters).get("PREPARED_COMPLETE_GROUP", 0)),
            )
        due = audit.events >= self.next_event or self.groups >= self.next_group
        while audit.events >= self.next_event:
            self.next_event += 250_000
        while self.groups >= self.next_group:
            self.next_group += 500
        return due


def _bounded_admission_journal_diagnostic(coordinator: object) -> dict[str, Any]:
    """Capture exact bounded journal tails without advancing metric cursors."""

    audit = coordinator.audit()
    output: dict[str, Any] = {}
    for branch, journal_value in (
        ("a", audit.a_admission_journal),
        ("b", audit.b_admission_journal),
    ):
        journal = tuple(journal_value)
        tail = None
        if journal:
            item = journal[-1]
            tail = {
                "identity": _jsonable(_admission_identity(item)),
                "outcome": str(item.outcome),
            }
        output[branch] = {"length": len(journal), "tail": tail}
    return output


def execute(
    owners: ConstructedFullSessionTenNodeAB, *, checkpoint_output: Path | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    metrics = _WholeSessionMetrics(started)
    coordinator = owners.coordinator
    original = coordinator.consume_record_ticket
    batches = 0
    callback_observer_error: dict[str, str] | None = None
    callback_journal_diagnostic: dict[str, Any] | None = None

    def observed(ticket):
        nonlocal batches, callback_observer_error, callback_journal_diagnostic
        try:
            original(ticket)
        except BaseException:
            if coordinator._a is not None and coordinator._b is not None:
                try:
                    callback_journal_diagnostic = (
                        _bounded_admission_journal_diagnostic(coordinator)
                    )
                    metrics._validated_unseen_admission_pairs(coordinator)
                except BaseException as observer_failure:
                    callback_observer_error = {
                        "type": type(observer_failure).__name__,
                        "message": str(observer_failure),
                    }
            raise
        if coordinator._a is not None and coordinator._b is not None:
            metrics.consume_authoritative_admissions(coordinator)
        batches += 1
        previous_groups = metrics.groups
        due = metrics.due(coordinator, batches=batches)
        if metrics.groups > previous_groups:
            metrics._roots(coordinator)
        if due:
            metrics.checkpoint(coordinator, batches=batches, final=False)
            if checkpoint_output is not None:
                _write_json_atomic_replace(
                    checkpoint_output, {"status": "RUNNING", "checkpoints": metrics.checkpoints},
                )

    coordinator.consume_record_ticket = observed
    try:
        stream, coordinator_audit = coordinator.run(owners.reader)
    except BaseException as error:
        metrics.checkpoint(coordinator, batches=batches, final=True)
        result = {
            "status": "FAILED", "product_ready": False, "scientific_pass": False,
            "failure": {"type": type(error).__name__, "message": str(error)},
            "observer_error": callback_observer_error,
            "authoritative_admission_journals": callback_journal_diagnostic,
            "wall_s": time.monotonic() - started,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "checkpoints": metrics.checkpoints,
            "per_action_metrics": None,
            "per_action_metrics_status": "OMITTED_NO_ACTION_LABEL_CONTROL_OR_PASS_FAIL",
        }
        if checkpoint_output is not None:
            _write_json_atomic_replace(checkpoint_output, result)
        return result
    metrics.checkpoint(coordinator, batches=batches, final=True)
    if checkpoint_output is not None:
        _write_json_atomic_replace(
            checkpoint_output, {"status": "COMPLETE", "checkpoints": metrics.checkpoints},
        )
    publication = owners.coordinator.diagnostic_publication()
    return {
        "status": "RUNNABLE_DIAGNOSTIC",
        "product_ready": False,
        "scientific_pass": False,
        "wall_s": time.monotonic() - started,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "stream_audit": _jsonable(stream),
        "coordinator_audit": _jsonable(coordinator_audit),
        "body_audit": _jsonable(owners.coordinator.body_audit()),
        "whole_session_checkpoints": metrics.checkpoints,
        "initializer_preworld_omissions": coordinator_audit.preworld_pose_omissions,
        "a": _root_summary(publication.a),
        "b": _root_summary(publication.b),
        "per_action_metrics": None,
        "per_action_metrics_status": "OMITTED_NO_ACTION_LABEL_CONTROL_OR_PASS_FAIL",
    }


def _write_json_atomic_new(path: Path, document: Mapping[str, Any]) -> None:
    target = path.absolute()
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(_jsonable(document), sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode()
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o444,
    )
    try:
        if os.write(descriptor, encoded) != len(encoded):
            raise RuntimeError("short diagnostic result write")
        os.fsync(descriptor)
        os.link(temporary, target)
    finally:
        os.close(descriptor)
        temporary.unlink(missing_ok=True)


def _write_json_atomic_replace(path: Path, document: Mapping[str, Any]) -> None:
    target = path.absolute()
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    encoded = (json.dumps(_jsonable(document), sort_keys=True, separators=(",", ":"),
                          allow_nan=False) + "\n").encode()
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0), 0o444,
    )
    try:
        if os.write(descriptor, encoded) != len(encoded):
            raise RuntimeError("short checkpoint write")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, target)


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--construct-only", action="store_true")
    mode.add_argument("--execute", action="store_true")
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()
    if arguments.execute != (arguments.output is not None):
        parser.error("--execute requires --output and construct-only forbids it")
    owners = build()
    if arguments.execute:
        checkpoint_output = arguments.output.with_name(
            f"{arguments.output.stem}.checkpoints.json"
        )
        result = execute(owners, checkpoint_output=checkpoint_output)
        _write_json_atomic_new(arguments.output, result)
        return 0 if result["status"] != "FAILED" else 1
    print(json.dumps({
        "status": "CONSTRUCTED_NOT_EXECUTED", "reader_consumed": owners.reader._consumed,
        "clock_nodes": sorted(binding.node_id for binding in owners.clock_owner.bindings),
        "static_digest": owners.static_digest, "body_owner_digest": owners.body_owner_digest,
        "product_ready": False, "scientific_pass": False,
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
