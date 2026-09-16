"""Prior-free first-group initialization for the continuous articulated chain."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from enum import Enum
from typing import Callable

import numpy as np

from biospur_fusion.c2_timing_contract import MAXIMUM_POSE_AGE_NS
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import CausalArticulatedPose
from biospur_fusion.c2_uwb_calibration.shared_root import solve_shared_root
from biospur_fusion.c2_uwb_root_world.async_root_worker import RootWorkerEvent
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import AuthoritativeArticulatedFusion
from biospur_fusion.c2_uwb_root_world.continuous_consensus_drift import (
    ContinuousConsensusDriftOwner,
)
from biospur_fusion.c2_uwb_root_world.offline_unified_wiring import (
    StrictFloorOffset, build_causal_links, group_epoch_times_ns,
)
from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import (
    BoundGroupPacket,
    PristineRobustBootstrapCandidate,
    prepare_pristine_robust_bootstrap_candidate,
)
from biospur_fusion.c2_uwb_root_world.root_worker_owner_wiring import (
    PoseTagLinkOwner,
    RootFreeReferenceOwnerTemplate,
    materialize_reference_owner,
)
from biospur_fusion.root_r3.models import RootState

from .continuous_frontend import ContinuousClockOwner, ContinuousEvent, validate_event_clock
from .continuous_group_epoch_owner import (
    CONTINUOUS_HINGE_RETENTION_CONTRACT,
    AuthoritativeContinuousGroupComposition,
    AuthoritativeContinuousHistoryOwner,
    ContinuousGroupEpochOwner,
    OwnedNative200HistoryFrame,
    _row_from_event,
    canonical_group_availability_time_s,
    canonical_epoch_bucket,
)

ACTION00_INITIALIZATION_SCHEMA = "biospur.c2.action00.prospective_initialization.v1"
CONTINUOUS_SESSION_INITIALIZATION_SCHEMA = (
    "biospur.c2.full_session.prospective_initialization.v1"
)
def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class BootstrapPoseReadinessStatus(str, Enum):
    READY = "READY"
    RETRYABLE_POSE_NOT_READY = "RETRYABLE_POSE_NOT_READY"
    TERMINALLY_MISSED = "TERMINALLY_MISSED"


@dataclass(frozen=True)
class BootstrapPoseReadiness:
    status: BootstrapPoseReadinessStatus
    unresolved_link_queries_ns: tuple[float, ...]
    latest_pose_time_ns: int | None
    required_history_frames_missing: int
    history_revision: int
    digest: str


@dataclass(frozen=True)
class ProspectiveInitializationPolicy:
    consensus_m: float = 0.05
    residual_rms_m: float = 0.50
    maximum_condition: float = 1e8

    def __post_init__(self) -> None:
        if (
            self.consensus_m != 0.05 or self.residual_rms_m != 0.50
            or self.maximum_condition != 1e8
        ):
            raise ValueError("initialization policy is not the established prior-free policy")


@dataclass(frozen=True)
class InitializationCovariancePolicy:
    position_regularization_m2: float
    velocity_variance_m2s2: float
    bias_variance_m2s4: float
    provenance: str
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            self.position_regularization_m2 != 1e-6
            or self.velocity_variance_m2s2 != 1.0
            or self.bias_variance_m2s4 != 0.25
            or not self.provenance
        ):
            raise ValueError("initial covariance policy is not established")
        value = _digest({
            "position_regularization_m2": self.position_regularization_m2,
            "velocity_variance_m2s2": self.velocity_variance_m2s2,
            "bias_variance_m2s4": self.bias_variance_m2s4,
            "provenance": self.provenance,
        })
        if self.digest and self.digest != value:
            raise ValueError("initial covariance policy digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class Action00StationarityEvidence:
    start_global_ns: int
    end_global_ns: int
    source_sha256: str
    metric_mps: float
    threshold_mps: float
    metric_owner_digest: str
    stationarity_pass: bool
    digest: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.start_global_ns) is not int or type(self.end_global_ns) is not int
            or self.end_global_ns <= self.start_global_ns
            or any(len(value) != 64 for value in (self.source_sha256, self.metric_owner_digest))
            or not math.isfinite(self.metric_mps) or not math.isfinite(self.threshold_mps)
            or self.metric_mps < 0 or self.threshold_mps <= 0
            or self.stationarity_pass is not True or self.metric_mps > self.threshold_mps
        ):
            raise ValueError("Action00 stationarity evidence is not qualified")
        value = _digest({"action_index": 0, "action_id": "00_initial_still",
                         "interval": [self.start_global_ns, self.end_global_ns],
                         "source": self.source_sha256, "metric_mps": self.metric_mps,
                         "threshold_mps": self.threshold_mps,
                         "metric_owner": self.metric_owner_digest, "pass": True})
        if self.digest and self.digest != value:
            raise ValueError("stationarity evidence digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class CalibratedImuErrorStateOrigin:
    calibration_owner_digest: str
    source_sha256: str
    qualified: bool
    digest: str = ""

    def __post_init__(self) -> None:
        if self.qualified is not True or any(len(value) != 64 for value in (
            self.calibration_owner_digest, self.source_sha256,
        )):
            raise ValueError("calibrated IMU error-state origin is unqualified")
        value = _digest({"owner": self.calibration_owner_digest,
                         "source": self.source_sha256, "qualified": True})
        if self.digest and self.digest != value:
            raise ValueError("calibrated IMU origin digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class InitializationStateOwner:
    stationarity: Action00StationarityEvidence | None
    imu_origin: CalibratedImuErrorStateOrigin
    covariance_policy_digest: str
    digest: str = ""

    def __post_init__(self) -> None:
        if len(self.covariance_policy_digest) != 64:
            raise ValueError("initial state covariance owner is missing")
        value = _digest({"stationarity": (
                             None if self.stationarity is None
                             else self.stationarity.digest
                         ),
                         "imu_origin": self.imu_origin.digest,
                         "covariance": self.covariance_policy_digest})
        if self.digest and self.digest != value:
            raise ValueError("initial state owner digest mismatch")
        object.__setattr__(self, "digest", value)


@dataclass(frozen=True)
class LocatedContinuousArticulatedOwners:
    root_state: RootState
    a: ContinuousGroupEpochOwner
    b: ContinuousGroupEpochOwner
    bootstrap_bucket: int
    bootstrap_group_digest: str
    node_positions_world_m: tuple[tuple[str, tuple[float, float, float]], ...]
    direct_nodes: tuple[str, ...]
    propagated_nodes: tuple[str, ...]
    provenance_digest: str


@dataclass(frozen=True)
class PreparedProspectiveInitialization:
    owner_key: object
    base_revision: int
    history_digest: str
    group_digest: str
    bucket: int
    installation: LocatedContinuousArticulatedOwners
    consumed: list[bool]


@dataclass(frozen=True)
class ProspectiveInitializationOutcome:
    accepted: bool
    reason: str
    prepared: PreparedProspectiveInitialization | None
    robust_candidate: PristineRobustBootstrapCandidate | None

    def __post_init__(self) -> None:
        if (
            type(self.accepted) is not bool or not self.reason
            or self.accepted != (self.prepared is not None)
            or self.robust_candidate is None
            or (self.accepted and not self.robust_candidate.accepted)
        ):
            raise ValueError("invalid prospective initialization outcome")


class ProspectiveContinuousSessionInitializer:
    """Hold causal pose chronology until one ten-node group defines world gauge.

    With no stationarity evidence this owner is label- and action-independent:
    readiness is solely the first complete causal ten-node observation.
    """

    def __init__(
        self, *, static_template: RootFreeReferenceOwnerTemplate,
        pose_factory: Callable[[], CausalArticulatedPose],
        a_sigma_owner, b_sigma_owner,
        native200_clock_owner_sha256: str,
        native200_base_pose_owner_digest: str,
        b_shadow_provenance: str, history_provenance: str,
        acquired_action_ids: frozenset[str],
        policy: ProspectiveInitializationPolicy,
        covariance_policy: InitializationCovariancePolicy,
        state_owner: InitializationStateOwner,
        clock_owner: ContinuousClockOwner,
        reference_materializer: Callable[..., object] = materialize_reference_owner,
        b_consensus_drift: ContinuousConsensusDriftOwner | None = None,
    ) -> None:
        static_template.validate_integrity()
        self._static = static_template
        self._pose_factory = pose_factory
        self._a_sigma = a_sigma_owner
        self._b_sigma = b_sigma_owner
        self._clock_sha = native200_clock_owner_sha256
        self._base_pose_digest = native200_base_pose_owner_digest
        self._shadow_provenance = b_shadow_provenance
        self._history_provenance = history_provenance
        self._actions = acquired_action_ids
        self._policy = policy
        self._covariance_policy = covariance_policy
        if state_owner.covariance_policy_digest != covariance_policy.digest:
            raise ValueError("initial state/covariance owner mismatch")
        if {binding.node_id for binding in clock_owner.bindings} != set(static_template.clocks):
            raise ValueError("UWB event clock owner inventory mismatch")
        for binding in clock_owner.bindings:
            static_clock = static_template.clocks[binding.node_id]
            if (
                binding.boot_epoch != static_clock.boot_epoch
                or binding.clock_domain != "B306_TIMER2"
                or binding.a_ns_per_us != static_clock.a_ns_per_us
                or binding.b_ns != static_clock.b_ns
            ):
                raise ValueError("continuous/static clock owner mismatch")
        self._state_owner = state_owner
        self._schema = (
            ACTION00_INITIALIZATION_SCHEMA
            if state_owner.stationarity is not None
            else CONTINUOUS_SESSION_INITIALIZATION_SCHEMA
        )
        self._clock_owner = clock_owner
        if not callable(reference_materializer):
            raise TypeError("reference materializer must be callable")
        self._reference_materializer = reference_materializer
        if b_consensus_drift is not None and (
            type(b_consensus_drift) is not ContinuousConsensusDriftOwner
            or b_consensus_drift.revision != 0
            or b_consensus_drift.pending_count != 0
            or not np.array_equal(
                b_consensus_drift.cumulative_absolute_position_correction_m,
                np.zeros(3),
            )
        ):
            raise ValueError("B consensus drift template must be pristine")
        self._b_consensus_drift = (
            None if b_consensus_drift is None else b_consensus_drift.clone()
        )
        self._frames: tuple[OwnedNative200HistoryFrame, ...] = ()
        self._preworld_pose_omissions = 0
        self._retain_recent_only = state_owner.stationarity is None
        self._revision = 0
        self._located: LocatedContinuousArticulatedOwners | None = None
        self.__owner_key = object()

    @property
    def located(self) -> bool:
        return self._located is not None

    @property
    def publication(self) -> LocatedContinuousArticulatedOwners | None:
        return self._located

    @property
    def preworld_pose_omissions(self) -> int:
        return self._preworld_pose_omissions

    def owner_bytes(self) -> bytes:
        payload = {
            "schema": self._schema, "revision": self._revision,
            "frames": [frame.digest for frame in self._frames],
            "preworld_pose_omissions": self._preworld_pose_omissions,
            "located": None if self._located is None else self._located.provenance_digest,
        }
        if self._b_consensus_drift is not None:
            payload["b_consensus_drift"] = self._b_consensus_drift.owner_digest
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()

    def accept_native200(self, frame: OwnedNative200HistoryFrame) -> None:
        if self.located:
            raise RuntimeError("INITIALIZER_ALREADY_LOCATED")
        if type(frame) is not OwnedNative200HistoryFrame:
            raise ValueError("initializer requires a source-owned native200 frame")
        if frame.clock_owner_sha256 != self._clock_sha or frame.base_pose_owner_digest != self._base_pose_digest:
            raise ValueError("native200 initialization owner mismatch")
        stationarity = self._state_owner.stationarity
        if (stationarity is not None and not
                stationarity.start_global_ns <= frame.source_global_ns
                < stationarity.end_global_ns):
            raise ValueError("native200 frame lies outside owned Action00 stationarity interval")
        if self._frames:
            previous = self._frames[-1]
            if (frame.source_timer_us - previous.source_timer_us != 5_000
                    or frame.publication_revision != previous.publication_revision + 1):
                raise ValueError("native200 initialization chronology is not consecutive")
        if len(self._frames) >= 52 and not self._retain_recent_only:
            raise OverflowError("UNLOCATED_NATIVE200_HISTORY_CAPACITY_EXCEEDED")
        if len(self._frames) >= 52:
            self._frames = self._frames[1:]
            self._preworld_pose_omissions += 1
        self._frames += (frame,)
        self._revision += 1

    def _strict_floor_frame(self, node: str, query_ns: float) -> tuple[StrictFloorOffset, OwnedNative200HistoryFrame]:
        eligible = [frame for frame in self._frames if frame.source_global_ns < query_ns]
        if not eligible:
            raise ValueError("first UWB group predates source-owned pose history")
        frame = eligible[-1]
        return StrictFloorOffset(
            frame.offsets_world_m[node], frame.source_global_ns, query_ns,
            query_ns - frame.source_global_ns, frame.source_frame,
        ), frame

    def _strict_floor(self, node: str, query_ns: float) -> StrictFloorOffset:
        return self._strict_floor_frame(node, query_ns)[0]

    def _validated_first_group_rows(
        self, events: tuple[ContinuousEvent, ...],
    ) -> tuple[tuple[object, ...], int]:
        if len(events) != 10:
            raise ValueError("first initialization needs ten nodes and native200 history")
        rows = tuple(sorted(
            (_row_from_event(event) for event in events), key=lambda row: row.node,
        ))
        if tuple(row.node for row in rows) != tuple(sorted(self._static.clocks)):
            raise ValueError("first group node inventory mismatch")
        for event, row in zip(sorted(events, key=lambda item: item.node_id), rows):
            clock = self._static.clocks[row.node]
            binding = self._clock_owner.binding_for(row.node)
            validate_event_clock(event, self._clock_owner)
            action00_envelope = (
                event.action_index == 0
                and event.action_id == "00_initial_still"
                and event.region_id is None
            )
            full_session_envelope = (
                event.action_index == -1
                and event.action_id == "FULL_SESSION_CONTINUOUS_00_TO_19"
                and event.region_id == "FULL_SESSION_CONTINUOUS_00_TO_19"
            )
            if (
                event.kind != "UWB"
                or not (action00_envelope or full_session_envelope)
                or event.node_id != row.node or event.boot_epoch != row.boot
                or binding.a_ns_per_us != clock.a_ns_per_us
                or binding.b_ns != clock.b_ns
            ):
                raise ValueError("first UWB event identity/time/clock owner mismatch")
        buckets = {canonical_epoch_bucket(event.common_global_ns) for event in events}
        if len(buckets) != 1:
            raise ValueError("first rows do not form one epoch bucket")
        return rows, next(iter(buckets))

    def bootstrap_pose_readiness(
        self, events: tuple[ContinuousEvent, ...],
    ) -> BootstrapPoseReadiness:
        """Classify exact per-link strict-past readiness without mutation."""
        if self.located:
            raise RuntimeError("INITIALIZER_ALREADY_LOCATED")
        rows, _bucket = self._validated_first_group_rows(events)
        queries: list[float] = []
        for row in rows:
            clock = self._static.clocks[row.node]
            for anchor in range(8):
                queries.append(float(clock.link_time_ns(
                    event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us,
                    t_round_us=float(row.t_round_us[anchor]),
                )))
        unresolved: list[float] = []
        terminal = False
        latest = None if not self._frames else self._frames[-1].source_global_ns
        for query_ns in sorted(queries):
            eligible = [
                frame for frame in self._frames
                if frame.source_global_ns < query_ns
            ]
            valid = bool(eligible) and (
                query_ns - eligible[-1].source_global_ns <= MAXIMUM_POSE_AGE_NS
            )
            if not valid:
                unresolved.append(query_ns)
                # Accepted source frames are monotone.  Once their horizon has
                # reached this exact link epoch, no future frame can become its
                # strict-past predecessor.
                terminal = terminal or (latest is not None and latest >= query_ns)
        missing_history = max(0, 2 - len(self._frames))
        if terminal:
            status = BootstrapPoseReadinessStatus.TERMINALLY_MISSED
        elif unresolved or missing_history:
            status = BootstrapPoseReadinessStatus.RETRYABLE_POSE_NOT_READY
        else:
            status = BootstrapPoseReadinessStatus.READY
        digest = _digest({
            "schema": self._schema,
            "status": status.value,
            "unresolved_link_queries_ns": unresolved,
            "latest_pose_time_ns": latest,
            "required_history_frames_missing": missing_history,
            "history_revision": self._revision,
            "history": [frame.digest for frame in self._frames],
        })
        return BootstrapPoseReadiness(
            status, tuple(unresolved), latest, missing_history,
            self._revision, digest,
        )

    def _branch(
        self, state: RootState, pose_links: tuple[PoseTagLinkOwner, ...],
        *, bucket: int, group_digest: str,
        consensus_drift: ContinuousConsensusDriftOwner | None = None,
    ) -> ContinuousGroupEpochOwner:
        engine, history = self._make_engine_history(state, pose_links)

        def clone_factory():
            return self._make_engine_history(state, pose_links)

        composition = AuthoritativeContinuousGroupComposition(
            engine=engine, history=history, clone_factory=clone_factory,
            consensus_drift=consensus_drift,
        )
        return ContinuousGroupEpochOwner.from_consumed_bootstrap(
            composition=composition, static_nodes=tuple(sorted(engine.static.clocks)),
            acquired_action_ids=self._actions,
            bootstrap_bucket=bucket, bootstrap_group_digest=group_digest,
            bootstrap_region_identity=(
                "00_initial_still"
                if self._state_owner.stationarity is not None
                else "FULL_SESSION_CONTINUOUS_00_TO_19"
            ),
        )

    def _make_engine_history(
        self, state: RootState, pose_links: tuple[PoseTagLinkOwner, ...],
    ):
        static = self._reference_materializer(
            self._static,
            initial_state=state,
            pose_links=pose_links,
            initial_state_provenance=(
                f"{self._schema};state_owner={self._state_owner.digest};"
                f"covariance={self._covariance_policy.digest}"
            ),
        )
        pose = self._pose_factory()
        pristine_token = pose.publication_token()
        if pristine_token.revision != 0 or math.isfinite(pristine_token.latest_sample_s):
            raise ValueError("prospective pose factory must return a pristine pose owner")
        engine = AuthoritativeArticulatedFusion(
            static_owner=static, pose=pose,
            native200_clock_owner_sha256=self._clock_sha,
            native200_base_pose_owner_digest=self._base_pose_digest,
        )
        history = AuthoritativeContinuousHistoryOwner.from_prospective_frames(
            engine=engine, a_sigma_owner=self._a_sigma,
            b_sigma_owner=self._b_sigma,
            b_shadow_provenance=self._shadow_provenance,
            history_provenance=self._history_provenance,
            hinge_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
            frames=self._frames,
        )
        if history.frames != self._frames or history.revision != len(self._frames):
            raise RuntimeError("prospective pose/history binding failed")
        return engine, history

    def _make_pristine_engine(
        self, state: RootState, pose_links: tuple[PoseTagLinkOwner, ...],
    ) -> AuthoritativeArticulatedFusion:
        static = self._reference_materializer(
            self._static, initial_state=state, pose_links=pose_links,
            initial_state_provenance=(
                f"{self._schema};state_owner={self._state_owner.digest};"
                f"covariance={self._covariance_policy.digest}"
            ),
        )
        pose = self._pose_factory()
        token = pose.publication_token()
        if token.revision != 0 or math.isfinite(token.latest_sample_s):
            raise ValueError("prospective pose factory must return a pristine pose owner")
        engine = AuthoritativeArticulatedFusion(
            static_owner=static, pose=pose,
            native200_clock_owner_sha256=self._clock_sha,
            native200_base_pose_owner_digest=self._base_pose_digest,
        )
        # Preserve every structural failure that legacy history replay performed;
        # only the expensive pose projections are deferred until admission passes.
        AuthoritativeContinuousHistoryOwner._validate_prospective_frames(
            engine=engine, frames=self._frames,
        )
        return engine

    def _bootstrap_packet(
        self, engine: AuthoritativeArticulatedFusion,
        rows: tuple[object, ...], *, availability_global_ns: int,
        member_region_identities: tuple[str, ...], evidence_class: str,
    ) -> BoundGroupPacket:
        availability_time_s = canonical_group_availability_time_s(
            rows, clocks=engine.static.clocks,
            availability_global_ns=availability_global_ns,
        )
        _epochs, measurement_ns, _frame_lower_ns = group_epoch_times_ns(
            rows, clocks=engine.static.clocks,
        )
        sidecars = AuthoritativeContinuousHistoryOwner.prospective_sidecars_without_pose_replay(
            engine=engine, a_sigma_owner=self._a_sigma,
            b_sigma_owner=self._b_sigma,
            b_shadow_provenance=self._shadow_provenance,
            history_provenance=self._history_provenance,
            hinge_retention_contract=CONTINUOUS_HINGE_RETENTION_CONTRACT,
            frames=self._frames, rows=rows,
            measurement_time_s=measurement_ns * 1e-9,
            availability_time_s=availability_time_s,
            member_region_identities=member_region_identities,
            evidence_class=evidence_class,
        )
        event = RootWorkerEvent(
            max(row.sequence for row in rows), availability_time_s, "UWB", rows,
            sidecars.dynamic_envelope, sidecars.activity, sidecars.consensus,
            sidecars.contact,
        )
        return BoundGroupPacket(
            engine.static.digest, event, sidecars.pose_links, (),
            sidecars.a_sigma_owner, sidecars.b_sigma_owner,
            sidecars.b_shadow_owner,
            availability_global_ns=availability_global_ns,
        )

    def prepare_first_group_outcome(
        self, events: tuple[ContinuousEvent, ...],
    ) -> ProspectiveInitializationOutcome:
        if self.located:
            raise RuntimeError("INITIALIZER_ALREADY_LOCATED")
        if len(events) != 10 or len(self._frames) < 2:
            raise ValueError("first initialization needs ten nodes and native200 history")
        rows, bucket = self._validated_first_group_rows(events)
        links, _audit, measurement_s, _availability_s = build_causal_links(
            rows, clocks=self._static.clocks, strict_floor_offset=self._strict_floor,
            anchor_delay_m=self._static.anchor_delay_m, tag_delay_m=self._static.tag_delay_m,
            sigma_for_quality=self._static.range_information.sigma,
        )
        pose_links = []
        for row in rows:
            clock = self._static.clocks[row.node]
            for anchor in range(8):
                query_ns = clock.link_time_ns(
                    event_boot_epoch=row.boot,
                    strobe_us=row.strobe_us,
                    t_round_us=float(row.t_round_us[anchor]),
                )
                strict_floor, frame = self._strict_floor_frame(row.node, query_ns)
                pose_links.append(PoseTagLinkOwner(
                    row.node,
                    anchor,
                    query_ns,
                    strict_floor.pose_time_ns,
                    strict_floor.offset_world_m,
                    frame.offset_velocities_world_mps[row.node],
                    frame.source_frame,
                    frame.publication_revision,
                    frame.pose_publication_digest,
                ))
        pose_links = tuple(pose_links)
        anchors = np.asarray(self._static.anchors_m)
        lower, upper = anchors.min(axis=0), anchors.max(axis=0)
        fallback_seed = 0.5 * (lower + upper)
        inset = np.minimum(0.15 * np.maximum(upper - lower, 1e-6), 0.25)
        starts = tuple(np.array((x, y, z)) for x in (lower[0] + inset[0], upper[0] - inset[0])
                       for y in (lower[1] + inset[1], upper[1] - inset[1])
                       for z in (lower[2] + inset[2], upper[2] - inset[2]))
        solved = [solve_shared_root(
            links, anchors_m=anchors, initial_root_m=start,
            maximum_condition=self._policy.maximum_condition,
        ) for start in starts]
        accepted = sorted((item for item in solved if item.success and item.rank == 3
                           and math.isfinite(item.condition)), key=lambda item: (item.cost, tuple(item.root_position_m)))
        best = accepted[0] if accepted else None
        all_link_residual_rms = (
            None if best is None
            else float(np.sqrt(np.mean(np.square(best.residuals_m))))
        )
        all_link_consensus_m = (
            None if best is None
            else max(float(np.linalg.norm(item.root_position_m - best.root_position_m))
                     for item in accepted)
        )
        # This fit is only a numerical hint.  Its residual and cross-start
        # consensus are diagnostics, never admission gates for the runtime
        # robust x/10 path.  The anchor owner supplies a deterministic world-
        # frame seed when no all-link start converges.
        seed_source = (
            "ALL_LINK_BEST_SUCCESS"
            if best is not None else "ANCHOR_VOLUME_CENTER_FALLBACK"
        )
        provisional_root = best.root_position_m if best is not None else fallback_seed
        provisional_covariance = np.zeros((9, 9))
        provisional_covariance[:3, :3] = np.eye(3) * self._covariance_policy.position_regularization_m2
        provisional_covariance[3:6, 3:6] = np.eye(3) * self._covariance_policy.velocity_variance_m2s2
        provisional_covariance[6:9, 6:9] = np.eye(3) * self._covariance_policy.bias_variance_m2s4
        provisional_state = RootState(
            measurement_s, np.r_[provisional_root, np.zeros(6)],
            provisional_covariance,
        )
        availability_global_ns = max(
            event.availability_global_ns for event in events
        )
        provisional_engine = self._make_pristine_engine(
            provisional_state, pose_links,
        )
        regions = tuple(
            "FULL_SESSION_CONTINUOUS_00_TO_19"
            if self._state_owner.stationarity is None else event.action_id
            for event in sorted(events, key=lambda item: item.node_id)
        )
        packet = self._bootstrap_packet(
            provisional_engine, rows,
            availability_global_ns=availability_global_ns,
            member_region_identities=regions,
            evidence_class=(
                "DYNAMIC_ONLY" if self._state_owner.stationarity is None
                else "ACTION_EVIDENCE"
            ),
        )
        robust = prepare_pristine_robust_bootstrap_candidate(
            provisional_engine.static, provisional_engine.root,
            packet,
        )
        if not robust.accepted:
            return ProspectiveInitializationOutcome(False, robust.reason, None, robust)
        if robust.physical_residual_rms_m > self._policy.residual_rms_m:
            return ProspectiveInitializationOutcome(
                False, "ROBUST_SELECTED_LINK_RESIDUAL_RMS_REJECTED", None, robust,
            )
        covariance = provisional_covariance.copy()
        covariance[:3, :3] = robust.covariance_m2
        state = RootState(
            measurement_s, np.r_[robust.root_position_m, np.zeros(6)], covariance,
        )
        group_digest = _digest({"rows": [row.__dict__ for row in rows],
                                "events": [(event.event_id, event.common_global_ns,
                                            event.availability_global_ns,
                                            event.clock_mapping_digest,
                                            event.clock_owner_sha256,
                                            event.clock_source_sha256)
                                           for event in sorted(events, key=lambda item: item.node_id)]})
        a = self._branch(state, pose_links, bucket=bucket, group_digest=group_digest)
        b = self._branch(
            state, pose_links, bucket=bucket, group_digest=group_digest,
            consensus_drift=(
                None if self._b_consensus_drift is None
                else self._b_consensus_drift.clone()
            ),
        )
        if a.mutable_owner_tokens() & b.mutable_owner_tokens():
            raise RuntimeError("INITIALIZED_AB_OWNER_ALIAS")
        current = self._frames[-1]
        nodes = tuple((node, tuple((robust.root_position_m + current.offsets_world_m[node]).tolist())) for node in sorted(self._static.clocks))
        provenance = _digest({"schema": self._schema, "group": group_digest,
                              "frames": [f.digest for f in self._frames], "policy": self._policy.__dict__,
                              "state_owner": self._state_owner.digest,
                              "covariance_policy": self._covariance_policy.digest,
                              "provisional_root": provisional_root.tolist(),
                              "provisional_seed_source": seed_source,
                              "all_link_residual_rms": all_link_residual_rms,
                              "all_link_consensus_m": all_link_consensus_m,
                              "selected_link_physical_residual_rms": robust.physical_residual_rms_m,
                              "root": robust.root_position_m.tolist(),
                              "trusted_nodes": robust.trusted_nodes})
        frozen_vector = state.vector.copy(); frozen_vector.setflags(write=False)
        frozen_covariance = state.covariance.copy(); frozen_covariance.setflags(write=False)
        published_state = RootState(state.time_s, frozen_vector, frozen_covariance)
        installation = LocatedContinuousArticulatedOwners(
            published_state, a, b, bucket, group_digest, nodes,
            robust.trusted_nodes,
            tuple(sorted(set(self._static.clocks) - set(robust.trusted_nodes))),
            provenance,
        )
        prepared = PreparedProspectiveInitialization(
            self.__owner_key, self._revision, _digest([f.digest for f in self._frames]),
            group_digest, bucket, installation, [False],
        )
        return ProspectiveInitializationOutcome(True, "ACCEPTED", prepared, robust)

    def prepare_first_group(
        self, events: tuple[ContinuousEvent, ...],
    ) -> PreparedProspectiveInitialization:
        outcome = self.prepare_first_group_outcome(events)
        if not outcome.accepted:
            raise RuntimeError(outcome.reason)
        assert outcome.prepared is not None
        return outcome.prepared

    def commit_first_group(self, prepared: PreparedProspectiveInitialization) -> LocatedContinuousArticulatedOwners:
        if (type(prepared) is not PreparedProspectiveInitialization
                or prepared.owner_key is not self.__owner_key or prepared.consumed[0]
                or self.located or prepared.base_revision != self._revision
                or prepared.history_digest != _digest([f.digest for f in self._frames])):
            raise RuntimeError("STALE_REPLAYED_OR_FOREIGN_INITIALIZATION")
        prepared.consumed[0] = True
        self._located = prepared.installation
        self._revision += 1
        return self._located


class ProspectiveAction00Initializer(ProspectiveContinuousSessionInitializer):
    """Compatibility owner retaining the qualified Action00 interval contract."""

    def __init__(self, **kwargs) -> None:
        state_owner = kwargs.get("state_owner")
        if (type(state_owner) is not InitializationStateOwner
                or state_owner.stationarity is None):
            raise ValueError("Action00 initializer requires stationarity evidence")
        super().__init__(**kwargs)
