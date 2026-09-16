"""Synthetic-only U4 sequencing of existing pose, contact, root, and U2 owners.

No profile fitting, articulated install, or estimator is owned here.  The
coordinator publishes native-200 pose/contact first and admits only root UWB
candidates.  An accepted root may replay the last immutable contact operator;
a rejected root performs no pose, detector, foothold, temporal, or contact
constraint operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np

from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
    CausalArticulatedPoseSample,
)
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter
from biospur_fusion.root_r3.models import ImuSample, PositionObservation
from .ankle_contact import (
    AnkleContactDetector,
    DualFootFootholdCorrector,
    FootContactEvidence,
    FootSupportState,
    FootholdConstraintDecision,
    FootholdPoseReconcileDecision,
    SIDES,
    positive_swing_cues,
)
from .causal_update_guard import (
    CandidateKind,
    CausalContactTransitionEvidence,
    ReachabilityEnvelope,
)
from .causal_update_transaction import (
    TransactionResult,
    execute_causal_update_transaction,
)

U3_ORIGINAL_SEAL_SHA256 = (
    "938862a8c8d6daacd2c3c894865e3373eec2ae159a2a1f8f27e5c8d5559b2640"
)
U3_CORRECTION_SEAL_SHA256 = (
    "3faa2fe70872818877a2545a828a55580d3276a53de908d19cb337bdaca9035b"
)


@dataclass(frozen=True)
class AnkleImuInput:
    acceleration_mps2: np.ndarray
    gyro_rad_s: np.ndarray
    relative_height_m: float
    relative_speed_mps: float


@dataclass(frozen=True)
class NativeContactPublication:
    time_s: float
    pose_sample: CausalArticulatedPoseSample
    evidence: Mapping[str, FootContactEvidence]
    reconcile: FootholdPoseReconcileDecision | None
    contact: FootholdConstraintDecision
    contact_transition: CausalContactTransitionEvidence | None
    pose_sample_calls: int
    detector_updates: int
    foothold_updates: int


@dataclass(frozen=True)
class RootTransactionPublication:
    transaction: TransactionResult
    immutable_contact_replayed: bool
    transaction_calls: int
    pose_sample_calls: int
    detector_updates: int
    foothold_updates: int


class OfflineUnifiedContactWiring:
    """Single-thread, offline-only root/contact fixture coordinator."""

    def __init__(
        self, *, root: CausalDelayedRootFilter, pose: CausalArticulatedPose,
        detector: AnkleContactDetector, footholds: DualFootFootholdCorrector,
        maximum_root_age_s: float,
    ) -> None:
        if pose.install_count != 0:
            raise ValueError("U4_ROOT_ONLY_REQUIRES_ZERO_POSE_INSTALLS")
        if not np.isfinite(maximum_root_age_s) or maximum_root_age_s <= 0.0:
            raise ValueError("maximum root age must be explicit and positive")
        self.root = root
        self.pose = pose
        self.detector = detector
        self.footholds = footholds
        self.maximum_root_age_s = float(maximum_root_age_s)
        self._evidence = {
            side: FootContactEvidence(
                side, float(root.current_state.time_s), 0.0, False,
                np.nan, np.nan, np.nan, "NO_NATIVE_SAMPLE",
                support_state=FootSupportState.UNOBSERVABLE.value,
            )
            for side in SIDES
        }
        self._previous_pose_offsets: dict[str, np.ndarray] | None = None
        self._previous_pose_velocity: dict[str, np.ndarray] | None = None
        self._last_operator = None
        self._last_transition: CausalContactTransitionEvidence | None = None
        self._latest_native_time_s = float(root.current_state.time_s)

    @property
    def evidence(self) -> dict[str, FootContactEvidence]:
        return dict(self._evidence)

    def publish_native200(
        self, *, pelvis_imu: ImuSample,
        ankle_imu: Mapping[str, AnkleImuInput],
        analytic_ankle_offset_world_m: Mapping[str, np.ndarray],
    ) -> NativeContactPublication:
        time_s = float(pelvis_imu.availability_time_s)
        if (
            pelvis_imu.measurement_time_s != time_s
            or time_s <= self._latest_native_time_s
            or set(ankle_imu) != set(SIDES)
            or set(analytic_ankle_offset_world_m) != set(SIDES)
        ):
            raise ValueError("native200 bilateral publication is incomplete or reversed")
        if self.pose.install_count != 0:
            raise RuntimeError("U4_FORBIDS_POSE_INSTALL")

        previous_state = {
            side: self._evidence[side].resolved_support_state.value for side in SIDES
        }
        # Both cues read the same pre-update detector/foothold/root snapshot.
        cues = positive_swing_cues(
            query_time_s=time_s,
            analytic_ankle_offset_world_m=analytic_ankle_offset_world_m,
            root_state=self.root.current_state,
            foothold_corrector=self.footholds,
            evidence=self._evidence,
            maximum_root_age_s=self.maximum_root_age_s,
            positive_swing_height_m=self.detector.config.maximum_height_margin_m,
        )
        updated: dict[str, FootContactEvidence] = {}
        for side in SIDES:
            row = ankle_imu[side]
            updated[side] = self.detector.update(
                side, time_s=time_s,
                acceleration_mps2=row.acceleration_mps2,
                gyro_rad_s=row.gyro_rad_s,
                relative_height_m=row.relative_height_m,
                relative_speed_mps=row.relative_speed_mps,
                positive_swing=bool(cues[side]["positive"]),
                swing_observable=bool(cues[side]["observable"]),
            )

        if not self.root.add_imu(pelvis_imu):
            raise RuntimeError("in-order native200 pelvis IMU was rejected")
        pose_sample = self.pose.sample(time_s)
        if self.pose.install_count != 0:
            raise RuntimeError("native publication installed an articulated correction")
        reconcile = None
        if self._previous_pose_offsets is not None:
            reconciled, reconcile = self.footholds.reconcile_pose_change(
                self.root.current_state, evidence=updated,
                previous_ankle_offset_world_m=self._previous_pose_offsets,
                previous_ankle_offset_velocity_world_mps=self._previous_pose_velocity,
                ankle_offset_world_m=pose_sample.ankle_offset_world_m,
                ankle_offset_velocity_world_mps=pose_sample.ankle_offset_velocity_world_mps,
            )
            if reconcile.accepted:
                self.root.apply_current_constraint(
                    reconciled, operator=reconcile.replay_operator,
                    owner="U4_NATIVE200_POSE_REGAUGE")
        constrained, contact = self.footholds.update(
            self.root.current_state, evidence=updated,
            ankle_offset_world_m=pose_sample.ankle_offset_world_m,
            ankle_offset_velocity_world_mps=pose_sample.ankle_offset_velocity_world_mps)
        if contact.accepted:
            self.root.apply_current_constraint(
                constrained, operator=contact.replay_operator,
                owner="U4_NATIVE200_FOOTHOLD_SOFT_UPDATE")
        self._last_operator = contact.replay_operator
        self._evidence = updated
        self._previous_pose_offsets = {
            side: np.asarray(pose_sample.ankle_offset_world_m[side]).copy() for side in SIDES}
        self._previous_pose_velocity = {
            side: np.asarray(pose_sample.ankle_offset_velocity_world_mps[side]).copy() for side in SIDES}
        current_state = {
            side: updated[side].resolved_support_state.value for side in SIDES}
        released = tuple(
            side for side in SIDES
            if previous_state[side] in (
                FootSupportState.STANCE_CONFIRMED.value,
                FootSupportState.UNCERTAIN.value,
            ) and current_state[side] == FootSupportState.SWING_CONFIRMED.value)
        self._last_transition = (
            CausalContactTransitionEvidence(
                time_s=time_s, previous_state=previous_state,
                current_state=current_state, released_sides=released,
                swing_sides=tuple(side for side in SIDES if current_state[side]
                                  == FootSupportState.SWING_CONFIRMED.value),
                provenance="AnkleContactDetector frozen native200 lifecycle transition",
            ) if released else None
        )
        self._latest_native_time_s = time_s
        return NativeContactPublication(
            time_s, pose_sample, dict(updated), reconcile, contact,
            self._last_transition, 1, 2, 1)

    def admit_root_observation(
        self, observation: PositionObservation, *,
        nominal_envelope: ReachabilityEnvelope,
    ) -> RootTransactionPublication:
        if observation.availability_time_s < self._latest_native_time_s:
            raise ValueError("UWB availability precedes published native200 state")
        pose_before = self.pose.publication_token()
        install_before = self.pose.install_count
        transaction = execute_causal_update_transaction(
            root=self.root, observation=observation,
            kind=CandidateKind.ROOT_POSITION,
            nominal_envelope=nominal_envelope,
            contact=self._last_transition,
        )
        replayed = False
        if transaction.root_committed and self._last_operator is not None:
            updated = self._last_operator.apply(self.root.current_state)
            self.root.apply_current_constraint(
                updated, operator=self._last_operator,
                owner="U4_ACCEPTED_UWB_IMMUTABLE_CONTACT_REPLAY")
            replayed = True
        if self.pose.install_count != install_before or (
            self.pose.publication_token().digest != pose_before.digest
        ):
            raise RuntimeError("root transaction mutated pose owner")
        return RootTransactionPublication(transaction, replayed, 1, 0, 0, 0)
