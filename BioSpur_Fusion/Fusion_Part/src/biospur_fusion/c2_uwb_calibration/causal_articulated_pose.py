"""Causal owner for the final C2 articulated pose used by root/contact fusion.

An articulated correction is learned at a UWB measurement epoch but cannot be
used until that measurement is available.  This owner holds the latest
available correction, re-expresses it against each current analytic base via
the public biomechanics projector, and evaluates the same FK tree for every
200 Hz contact/root consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from functools import partial
import hashlib
import json
import math
from typing import Any, Callable, Mapping
from types import MappingProxyType
import hmac
import weakref

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_articulated_biomechanics.hinge_temporal import (
    DEFAULT_RETENTION_CONTRACT,
    HingeTemporalRetentionContract,
    HingeTemporalEvidence,
    HingeTemporalValidity,
    ObsoleteNative200SourcePairDiagnostic,
    _CausalHingeTemporalOwner,
    extract_public_hinge_q_rad,
)
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    evaluate_varying_base_hinge_projection_batch,
    project_hinge_corrections,
)

from .articulated_range import SEGMENTS, corrected_proxy_points


HingeProjector = Callable[
    [Mapping[str, np.ndarray], Mapping[str, np.ndarray]],
    tuple[dict[str, np.ndarray], dict[str, Any]],
]


@dataclass(frozen=True)
class CausalArticulatedPoseSample:
    time_s: float
    fraction: float | None
    correction_rotvec: Mapping[str, np.ndarray]
    points_root_m: Mapping[str, np.ndarray]
    ankle_offset_world_m: Mapping[str, np.ndarray]
    ankle_offset_velocity_world_mps: Mapping[str, np.ndarray]
    projection: Mapping[str, Any]
    velocity_baseline_reset: bool
    transition_active: bool
    transition_step_maximum_rad: float
    hinge_temporal: HingeTemporalEvidence | None = None


@dataclass(frozen=True)
class PosePublicationToken:
    authority: object
    revision: int
    latest_sample_s: float
    digest: str


@dataclass(frozen=True)
class _DirectHingeContinuityEvidence:
    authority: object
    pose_revision: int
    temporal_revision: int
    source_time_s: float
    source_node: str
    source_boot_epoch: int
    previous_source_timer_us: int
    source_timer_us: int
    previous_source_global_ns: int
    source_global_ns: int
    source_clock_mapping_digest: str
    correction_generation: int
    correction_hash: str
    projection_hash: str
    joint_q_rad: np.ndarray
    joint_step_maximum_rad: np.ndarray
    joint_rate_maximum_rad_s: np.ndarray
    joint_acceleration_maximum_rad_s2: np.ndarray
    rom_valid: bool
    fk_valid: bool
    provenance: str
    digest: str


@dataclass(frozen=True)
class _PreparedPoseInstallPlan:
    authority: object
    base_revision: int
    base_latest_sample_s: float
    transition_origin_correction: Mapping[str, np.ndarray]
    target_correction: Mapping[str, np.ndarray]
    transition_start_s: float
    latest_availability_s: float
    latest_sample_s: float
    install_count: int
    velocity_baseline_reset_pending: bool
    pose_revision: int
    temporal_snapshot: Any
    direct_hinge: _DirectHingeContinuityEvidence | None
    temporal_rebase: Any | None
    hinge_continuity_generation: int
    digest: str


@dataclass(frozen=True)
class _PoseCommitBundle:
    authority: object
    revision: int
    latest_sample_s: float
    plan: _PreparedPoseInstallPlan


@dataclass(frozen=True)
class _PoseRollbackBundle:
    transition_origin_correction: Mapping[str, np.ndarray]
    target_correction: Mapping[str, np.ndarray]
    transition_start_s: float
    latest_availability_s: float
    latest_sample_s: float
    install_count: int
    velocity_baseline_reset_pending: bool
    pose_revision: int
    hinge_continuity_generation: int
    temporal_history: tuple[Any, ...]
    temporal_last_digest: str | None
    temporal_revision: int
    sample_generation: int


@dataclass(frozen=True)
class Native200PoseBatchInput:
    time_s: float
    source_node: str
    source_boot_epoch: int
    previous_source_timer_us: int
    source_timer_us: int
    previous_source_global_ns: int
    source_global_ns: int
    source_clock_mapping_digest: str
    previous_base_rotations_world: Mapping[str, np.ndarray]
    current_base_rotations_world: Mapping[str, np.ndarray]


@dataclass
class _Native200BatchCommitState:
    authority: object
    consumed: bool = False
    _plan: weakref.ReferenceType | None = None

    def bind(self, plan: "_PreparedNative200PoseBatch", *, authority: object) -> None:
        if authority is not self.authority or self._plan is not None:
            raise RuntimeError("NATIVE200_POSE_BATCH_STATE_ALREADY_BOUND")
        self._plan = weakref.ref(plan)

    def owns(self, plan: object, *, authority: object) -> bool:
        return (
            authority is self.authority
            and self._plan is not None
            and self._plan() is plan
        )


@dataclass(frozen=True)
class _PreparedNative200PoseRow:
    source: Native200PoseBatchInput
    projected: Mapping[str, np.ndarray]
    points: Mapping[str, np.ndarray]
    prior_points: Mapping[str, np.ndarray]
    projection: Mapping[str, Any]
    dt: float


@dataclass(frozen=True)
class _PreparedNative200PoseBatch:
    authority: object
    base_revision: int
    base_latest_sample_s: float
    base_sample_generation: int
    target_digest: str
    temporal_snapshot: Any
    rows: tuple[_PreparedNative200PoseRow, ...]
    state: _Native200BatchCommitState
    digest: str


class CausalArticulatedPose:
    """Hold and evaluate only corrections already available at query time."""

    def __init__(
        self,
        *,
        action_start_s: float,
        action_stop_s: float,
        rotations_at_fraction: Callable[[float], Mapping[str, np.ndarray]],
        geometry: DisplayProxyGeometry,
        hinge_projector: HingeProjector | None,
        derivative_period_s: float = 0.005,
        transition_period_s: float = 0.12,
        hinge_temporal_retention_contract: HingeTemporalRetentionContract = DEFAULT_RETENTION_CONTRACT,
    ) -> None:
        if not (
            math.isfinite(action_start_s)
            and math.isfinite(action_stop_s)
            and action_stop_s > action_start_s
            and math.isfinite(derivative_period_s)
            and derivative_period_s > 0.0
            and math.isfinite(transition_period_s)
            and transition_period_s >= derivative_period_s
        ):
            raise ValueError("causal articulated pose timing is invalid")
        self.action_start_s = float(action_start_s)
        self.action_stop_s = float(action_stop_s)
        self.duration_s = self.action_stop_s - self.action_start_s
        self.rotations_at_fraction = rotations_at_fraction
        self.geometry = geometry
        self.hinge_projector = hinge_projector
        self.derivative_period_s = float(derivative_period_s)
        self.transition_period_s = float(transition_period_s)
        self._transition_origin_correction = {
            segment: np.zeros(3) for segment in SEGMENTS
        }
        self._target_correction = {
            segment: np.zeros(3) for segment in SEGMENTS
        }
        self._transition_start_s = self.action_start_s
        self._latest_availability_s = -math.inf
        self._latest_sample_s = -math.inf
        self._install_count = 0
        self._velocity_baseline_reset_pending = False
        self._pose_revision = 0
        self._hinge_continuity_generation = 0
        self._sample_generation = 0
        self.__hinge_temporal_owner = _CausalHingeTemporalOwner(
            hinge_temporal_retention_contract
        )
        self.__transaction_authority = object()

    @property
    def hinge_temporal_retention_contract(self) -> HingeTemporalRetentionContract:
        return self.__hinge_temporal_owner._retention_contract()

    @property
    def install_count(self) -> int:
        return self._install_count

    @property
    def latest_availability_s(self) -> float:
        return self._latest_availability_s

    def publication_token(self) -> PosePublicationToken:
        digest=self._stable_hash({"revision":self._pose_revision,"latest_sample_s":self._latest_sample_s,
            "latest_availability_s":self._latest_availability_s,"install_count":self._install_count,
            "transition_start_s":self._transition_start_s,"origin":self._transition_origin_correction,
            "target":self._target_correction,"continuity_generation":self._hinge_continuity_generation})
        return PosePublicationToken(self.__transaction_authority,self._pose_revision,self._latest_sample_s,digest)

    def reset_hinge_continuity(self, continuity_generation: int) -> None:
        if (
            not isinstance(continuity_generation, int)
            or isinstance(continuity_generation, bool)
            or continuity_generation <= self._hinge_continuity_generation
        ):
            raise ValueError("hinge continuity generation must increase")
        self._hinge_continuity_generation = continuity_generation
        self._pose_revision += 1

    @property
    def hinge_continuity_generation(self) -> int:
        return self._hinge_continuity_generation

    def obsolete_native200_source_pair_diagnostic(
        self, *, source_node: str, source_boot_epoch: int,
        previous_timer_us: int, current_timer_us: int,
        previous_global_ns: int, current_global_ns: int,
        source_clock_mapping_digest: str,
    ) -> ObsoleteNative200SourcePairDiagnostic | None:
        """Read-only classification of a complete authenticated source pair."""
        integers = (
            source_boot_epoch, previous_timer_us, current_timer_us,
            previous_global_ns, current_global_ns,
        )
        if (
            type(source_node) is not str or not source_node
            or any(type(value) is not int for value in integers)
            or source_boot_epoch < 0
            or current_timer_us - previous_timer_us != 5_000
            or current_global_ns <= previous_global_ns
            or type(source_clock_mapping_digest) is not str
            or len(source_clock_mapping_digest) != 64
            or any(character not in "0123456789abcdef"
                   for character in source_clock_mapping_digest)
        ):
            raise ValueError("NATIVE200_SOURCE_BINDING_INVALID")
        return self.__hinge_temporal_owner._obsolete_native200_source_pair(
            source_node=source_node,
            source_boot_epoch=source_boot_epoch,
            current_timer_us=current_timer_us,
            current_global_ns=current_global_ns,
            source_clock_mapping_digest=source_clock_mapping_digest,
            continuity_generation=self._hinge_continuity_generation,
            include_exact_latest=False,
        )

    def transition_snapshot(self) -> dict[str, Any]:
        """Return an immutable copy of the current causal transition owner."""

        delta_maximum = max(
            float((
                Rotation.from_rotvec(
                    np.asarray(self._transition_origin_correction[segment], dtype=float).copy()
                ).inv()
                * Rotation.from_rotvec(np.asarray(self._target_correction[segment], dtype=float).copy())
            ).magnitude())
            for segment in SEGMENTS
        )
        return {
            "start_time_s": self._transition_start_s,
            "period_s": self.transition_period_s,
            "origin_correction": {
                segment: value.copy()
                for segment, value in self._transition_origin_correction.items()
            },
            "target_correction": {
                segment: value.copy()
                for segment, value in self._target_correction.items()
            },
            "target_delta_maximum_rad": delta_maximum,
        }

    def _fraction(self, time_s: float) -> float:
        if not math.isfinite(float(time_s)):
            raise ValueError("pose query time must be finite")
        return float(np.clip(
            (float(time_s) - self.action_start_s) / self.duration_s,
            0.0,
            1.0,
        ))

    @staticmethod
    def _copy_correction(
        correction: Mapping[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        if set(correction) != set(SEGMENTS):
            raise ValueError("articulated correction inventory mismatch")
        copied = {
            segment: np.asarray(correction[segment], dtype=float).reshape(3).copy()
            for segment in SEGMENTS
        }
        if not all(np.isfinite(value).all() for value in copied.values()):
            raise ValueError("articulated correction must be finite")
        return copied

    def _evaluate(
        self,
        time_s: float,
        correction: Mapping[str, np.ndarray],
    ) -> tuple[
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, Any],
    ]:
        fraction = self._fraction(time_s)
        base = self._copy_base_rotations(self.rotations_at_fraction(fraction))
        return self._evaluate_base(base, correction)

    @staticmethod
    def _copy_base_rotations(
        base_rotations_world: Mapping[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        if set(base_rotations_world) != set(SEGMENTS):
            raise ValueError("articulated base rotation inventory mismatch")
        copied = {
            segment: np.asarray(
                base_rotations_world[segment], dtype=float
            ).reshape(3, 3).copy()
            for segment in SEGMENTS
        }
        if not all(np.isfinite(value).all() for value in copied.values()):
            raise ValueError("articulated base rotations must be finite")
        return copied

    def _evaluate_base(
        self,
        base_rotations_world: Mapping[str, np.ndarray],
        correction: Mapping[str, np.ndarray],
    ) -> tuple[
        dict[str, np.ndarray],
        dict[str, np.ndarray],
        dict[str, Any],
    ]:
        base = self._copy_base_rotations(base_rotations_world)
        carried = self._copy_correction(correction)
        if self.hinge_projector is None:
            projected = carried
            projection: dict[str, Any] = {}
        else:
            projected, projection = self.hinge_projector(base, carried)
            projected = self._copy_correction(projected)
            if projection.get("post_projection_all_inside_rom") is not True:
                raise RuntimeError("current 200 Hz pose projection left hinge ROM")
        points = corrected_proxy_points(base, projected, self.geometry)
        return projected, points, dict(projection)

    def _interpolated_correction(
        self, time_s: float
    ) -> dict[str, np.ndarray]:
        """Return the causal correction transition at an absolute time."""

        alpha = float(np.clip(
            (float(time_s) - self._transition_start_s)
            / self.transition_period_s,
            0.0,
            1.0,
        ))
        interpolated: dict[str, np.ndarray] = {}
        for segment in SEGMENTS:
            origin = Rotation.from_rotvec(
                self._transition_origin_correction[segment]
            )
            delta = origin.inv() * Rotation.from_rotvec(
                self._target_correction[segment]
            )
            interpolated[segment] = (
                origin * Rotation.from_rotvec(delta.as_rotvec() * alpha)
            ).as_rotvec()
        return interpolated

    def install(
        self,
        correction_at_measurement: Mapping[str, np.ndarray],
        *,
        measurement_time_s: float,
        availability_time_s: float,
    ) -> None:
        """Install one correction only when its UWB observation is available."""

        measurement = float(measurement_time_s)
        availability = float(availability_time_s)
        if not (
            math.isfinite(measurement)
            and math.isfinite(availability)
            and availability + 1e-12 >= measurement
            and availability + 1e-12 >= self._latest_availability_s
            and availability + 1e-12 >= self._latest_sample_s
        ):
            raise ValueError("articulated correction availability reversed time")
        measurement_correction, _points, _projection = self._evaluate(
            measurement, correction_at_measurement
        )
        available_target, _points, _projection = self._evaluate(
            availability, measurement_correction
        )
        # Before the first emitted state there is no trajectory continuity to
        # preserve, so the bootstrap observation may initialize the owner.
        # Every later target only starts moving after its availability epoch.
        if self._install_count == 0 and self._latest_sample_s == -math.inf:
            current_at_availability = {
                segment: value.copy()
                for segment, value in available_target.items()
            }
        else:
            current_at_availability, _points, _projection = (
                self._transition_pose(availability)
            )
        self._transition_origin_correction = {
            segment: value.copy()
            for segment, value in current_at_availability.items()
        }
        self._target_correction = {
            segment: value.copy()
            for segment, value in available_target.items()
        }
        self._transition_start_s = availability
        self._latest_availability_s = availability
        self._install_count += 1
        self._velocity_baseline_reset_pending = True
        self._pose_revision += 1

    @staticmethod
    def _freeze_correction(values: Mapping[str,np.ndarray]) -> Mapping[str,np.ndarray]:
        frozen={segment:np.asarray(values[segment],float).reshape(3).copy() for segment in SEGMENTS}
        for value in frozen.values(): value.setflags(write=False)
        return MappingProxyType(frozen)

    def _direct_hinge_digest(self,evidence: _DirectHingeContinuityEvidence) -> str:
        return self._stable_hash({
            "pose_revision":evidence.pose_revision,"temporal_revision":evidence.temporal_revision,
            "source_time_s":evidence.source_time_s,"source_node":evidence.source_node,
            "source_boot_epoch":evidence.source_boot_epoch,
            "previous_source_timer_us":evidence.previous_source_timer_us,
            "source_timer_us":evidence.source_timer_us,
            "previous_source_global_ns":evidence.previous_source_global_ns,
            "source_global_ns":evidence.source_global_ns,
            "source_clock_mapping_digest":evidence.source_clock_mapping_digest,
            "correction_generation":evidence.correction_generation,
            "correction_hash":evidence.correction_hash,"projection_hash":evidence.projection_hash,
            "joint_q":evidence.joint_q_rad,
            "joint_step":evidence.joint_step_maximum_rad,
            "joint_rate":evidence.joint_rate_maximum_rad_s,"joint_acceleration":evidence.joint_acceleration_maximum_rad_s2,
            "rom_valid":evidence.rom_valid,"fk_valid":evidence.fk_valid,"provenance":evidence.provenance})

    def _pose_plan_digest(self,plan: _PreparedPoseInstallPlan) -> str:
        return self._stable_hash({"base_revision":plan.base_revision,"base_latest_sample_s":plan.base_latest_sample_s,
            "origin":plan.transition_origin_correction,"target":plan.target_correction,
            "transition_start_s":plan.transition_start_s,"latest_availability_s":plan.latest_availability_s,
            "latest_sample_s":plan.latest_sample_s,"install_count":plan.install_count,
            "velocity_reset":plan.velocity_baseline_reset_pending,"pose_revision":plan.pose_revision,
            "temporal_digest":plan.temporal_snapshot.digest,
            "direct_hinge_digest":None if plan.direct_hinge is None else plan.direct_hinge.digest,
            "temporal_rebase_digest":None if plan.temporal_rebase is None else plan.temporal_rebase.digest,
            "hinge_continuity_generation":plan.hinge_continuity_generation})

    def _direct_hinge_continuity(
        self,*,correction: Mapping[str,np.ndarray],projection: Mapping[str,Any],
        pose_revision:int,temporal_snapshot:Any,source_binding:Mapping[str,Any],
    ) -> _DirectHingeContinuityEvidence:
        function=self.hinge_projector.func if isinstance(self.hinge_projector,partial) else self.hinge_projector
        if function is not project_hinge_corrections:
            raise ValueError("GUARDED_INSTALL_REQUIRES_PUBLIC_HINGE_PROJECTOR")
        residual=projection.get("fk_direction_residual_maximum_deg")
        rom_valid=projection.get("post_projection_all_inside_rom") is True
        fk_valid=bool(residual is not None and np.isfinite(float(residual)))
        correction_hash=self._stable_hash(correction); projection_hash=self._stable_hash(projection)
        evidence=self.__hinge_temporal_owner._direct_candidate_from_snapshot(
            temporal_snapshot,pose_revision=pose_revision,
            continuity_generation=self._hinge_continuity_generation,
            correction_hash=correction_hash,projection_hash=projection_hash,
            projection=projection,rom_valid=rom_valid,fk_valid=fk_valid,**source_binding)
        arrays=[np.asarray(value,float).copy() for value in
                (evidence.q_rad,evidence.step_rad,evidence.qdot_rad_s,evidence.qddot_rad_s2)]
        for value in arrays: value.setflags(write=False)
        blank=_DirectHingeContinuityEvidence(
            self.__transaction_authority,pose_revision,temporal_snapshot.revision,
            evidence.time_s,source_binding["source_node"],source_binding["source_boot_epoch"],
            source_binding["previous_timer_us"],source_binding["current_timer_us"],
            source_binding["previous_global_ns"],source_binding["current_global_ns"],
            source_binding["source_clock_mapping_digest"],self._hinge_continuity_generation,
            correction_hash,projection_hash,*arrays,rom_valid,fk_valid,
            "direct candidate at authenticated native200 t using actual t-10/t-5 history","")
        return replace(blank,digest=self._direct_hinge_digest(blank))

    def _prepare_install(self,correction_at_measurement,*,measurement_time_s,availability_time_s,
                         guarded,projection_at_source=None,source_binding=None):
        measurement=float(measurement_time_s); availability=float(availability_time_s)
        if not (math.isfinite(measurement) and math.isfinite(availability)
                and availability+1e-12>=measurement and availability+1e-12>=self._latest_availability_s
                and availability+1e-12>=self._latest_sample_s):
            raise ValueError("articulated correction availability reversed time")
        if guarded:
            if not isinstance(projection_at_source,Mapping) or not isinstance(source_binding,Mapping):
                raise ValueError("DIRECT_HINGE_SOURCE_EVIDENCE_REQUIRED")
            target=self._freeze_correction(self._copy_correction(correction_at_measurement))
            projection=dict(projection_at_source)
        else:
            target_value,_points,projection=self._evaluate(measurement,correction_at_measurement)
            target=self._freeze_correction(target_value)
        origin = target
        temporal=self.__hinge_temporal_owner._snapshot_token(); next_revision=self._pose_revision+1
        direct=None if not guarded else self._direct_hinge_continuity(
            correction=target,projection=projection,pose_revision=next_revision,
            temporal_snapshot=temporal,source_binding=source_binding)
        next_generation=self._hinge_continuity_generation+(1 if guarded else 0)
        rebase=None if not guarded else self.__hinge_temporal_owner._prepare_rebase_from_candidate(
            temporal,pose_revision=next_revision,continuity_generation=next_generation,
            source_node=source_binding["source_node"],
            source_boot_epoch=source_binding["source_boot_epoch"],
            source_timer_us=source_binding["current_timer_us"],
            source_global_ns=source_binding["current_global_ns"],
            source_clock_mapping_digest=source_binding["source_clock_mapping_digest"],
            correction_hash=direct.correction_hash,projection_hash=direct.projection_hash,
            q_rad=direct.joint_q_rad,rom_valid=direct.rom_valid,fk_valid=direct.fk_valid)
        blank=_PreparedPoseInstallPlan(self.__transaction_authority,self._pose_revision,
            self._latest_sample_s,origin,target,availability,availability,self._latest_sample_s,
            self._install_count+1,True,next_revision,temporal,direct,rebase,next_generation,"")
        return replace(blank,digest=self._pose_plan_digest(blank))

    def prepare_install(self,correction_at_measurement,*,measurement_time_s,availability_time_s):
        return self._prepare_install(correction_at_measurement,measurement_time_s=measurement_time_s,
            availability_time_s=availability_time_s,guarded=False)

    def prepare_guarded_install(self,correction_at_measurement,*,measurement_time_s,availability_time_s,
                                projection_at_source,source_binding):
        return self._prepare_install(correction_at_measurement,measurement_time_s=measurement_time_s,
            availability_time_s=availability_time_s,guarded=True,
            projection_at_source=projection_at_source,source_binding=source_binding)

    def _prevalidate_install_plan(self, plan: _PreparedPoseInstallPlan) -> _PoseCommitBundle:
        if (not isinstance(plan,_PreparedPoseInstallPlan)
                or plan.authority is not self.__transaction_authority
                or plan.base_revision!=self._pose_revision
                or plan.base_latest_sample_s!=self._latest_sample_s
                or not hmac.compare_digest(plan.digest,self._pose_plan_digest(plan))):
            raise RuntimeError("STALE_POSE_INSTALL_PLAN")
        self.__hinge_temporal_owner._validate_snapshot_token(plan.temporal_snapshot)
        if plan.direct_hinge is not None and (
            plan.direct_hinge.authority is not self.__transaction_authority
            or plan.direct_hinge.pose_revision!=plan.pose_revision
            or plan.direct_hinge.temporal_revision!=plan.temporal_snapshot.revision
            or not hmac.compare_digest(plan.direct_hinge.digest,self._direct_hinge_digest(plan.direct_hinge))):
            raise RuntimeError("STALE_DIRECT_HINGE_CONTINUITY")
        if plan.temporal_rebase is not None:
            self.__hinge_temporal_owner._prevalidate_rebase(plan.temporal_rebase)
        return _PoseCommitBundle(self.__transaction_authority,self._pose_revision,self._latest_sample_s,plan)

    def _apply_prevalidated_install(self, ticket: _PoseCommitBundle) -> None:
        plan=ticket.plan
        self._transition_origin_correction=plan.transition_origin_correction
        self._target_correction=plan.target_correction
        self._transition_start_s=plan.transition_start_s
        self._latest_availability_s=plan.latest_availability_s
        self._latest_sample_s=plan.latest_sample_s
        self._install_count=plan.install_count
        self._velocity_baseline_reset_pending=plan.velocity_baseline_reset_pending
        self._pose_revision=plan.pose_revision
        self._hinge_continuity_generation=plan.hinge_continuity_generation
        self.__hinge_temporal_owner._CausalHingeTemporalOwner__history=(
            plan.temporal_rebase.history if plan.temporal_rebase is not None
            else self.__hinge_temporal_owner._CausalHingeTemporalOwner__history)
        self.__hinge_temporal_owner._CausalHingeTemporalOwner__last_digest=(
            plan.temporal_rebase.last_digest if plan.temporal_rebase is not None
            else self.__hinge_temporal_owner._CausalHingeTemporalOwner__last_digest)
        self.__hinge_temporal_owner._CausalHingeTemporalOwner__revision=(
            plan.temporal_rebase.revision if plan.temporal_rebase is not None
            else self.__hinge_temporal_owner._CausalHingeTemporalOwner__revision)

    def _prepare_install_rollback(self) -> _PoseRollbackBundle:
        return _PoseRollbackBundle(
            self._transition_origin_correction, self._target_correction,
            self._transition_start_s, self._latest_availability_s,
            self._latest_sample_s, self._install_count,
            self._velocity_baseline_reset_pending, self._pose_revision,
            self._hinge_continuity_generation,
            self.__hinge_temporal_owner._CausalHingeTemporalOwner__history,
            self.__hinge_temporal_owner._CausalHingeTemporalOwner__last_digest,
            self.__hinge_temporal_owner._CausalHingeTemporalOwner__revision,
            self._sample_generation,
        )

    def _rollback_prevalidated_install(self, ticket: _PoseRollbackBundle) -> None:
        self._transition_origin_correction = ticket.transition_origin_correction
        self._target_correction = ticket.target_correction
        self._transition_start_s = ticket.transition_start_s
        self._latest_availability_s = ticket.latest_availability_s
        self._latest_sample_s = ticket.latest_sample_s
        self._install_count = ticket.install_count
        self._velocity_baseline_reset_pending = ticket.velocity_baseline_reset_pending
        self._pose_revision = ticket.pose_revision
        self._hinge_continuity_generation = ticket.hinge_continuity_generation
        self.__hinge_temporal_owner._CausalHingeTemporalOwner__history = ticket.temporal_history
        self.__hinge_temporal_owner._CausalHingeTemporalOwner__last_digest = ticket.temporal_last_digest
        self.__hinge_temporal_owner._CausalHingeTemporalOwner__revision = ticket.temporal_revision
        self._sample_generation = ticket.sample_generation

    def install(
        self,correction_at_measurement: Mapping[str,np.ndarray],*,
        measurement_time_s: float,availability_time_s: float,
    ) -> None:
        """Legacy install delegated through the immutable pose plan."""
        plan=self.prepare_install(correction_at_measurement,measurement_time_s=measurement_time_s,
            availability_time_s=availability_time_s)
        self._apply_prevalidated_install(self._prevalidate_install_plan(plan))

    @staticmethod
    def _stable_hash(value: Any) -> str:
        def convert(item: Any) -> Any:
            if isinstance(item, Mapping):
                return {str(key): convert(item[key]) for key in sorted(item)}
            if isinstance(item, (tuple, list)):
                return [convert(value) for value in item]
            if isinstance(item, np.ndarray):
                return item.tolist()
            if isinstance(item, (np.floating, np.integer)):
                return item.item()
            return item
        payload = json.dumps(convert(value), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()

    def _hinge_temporal_preview(
        self,
        *,
        time_s: float,
        correction: Mapping[str, np.ndarray],
        projection: Mapping[str, Any],
        source_node: str | None = None,
        source_boot_epoch: int | None = None,
        source_timer_us: int | None = None,
        source_global_ns: int | None = None,
        source_clock_mapping_digest: str | None = None,
    ) -> Any | None:
        projector = self.hinge_projector
        function = projector.func if isinstance(projector, partial) else projector
        if function is not project_hinge_corrections:
            return None
        prior = self.__hinge_temporal_owner._last_identity()
        sequence = 0 if prior is None else (
            prior[0] if time_s == prior[1] else prior[0] + 1
        )
        correction_payload = {
            segment: np.asarray(correction[segment], dtype=float)
            for segment in SEGMENTS
        }
        residual = projection.get("fk_direction_residual_maximum_deg")
        return self.__hinge_temporal_owner._prepare_from_pose(
            pose_revision=self._pose_revision,
            continuity_generation=self._hinge_continuity_generation,
            publication_sequence=sequence,
            time_s=float(time_s),
            correction_hash=self._stable_hash(correction_payload),
            projection_hash=self._stable_hash(projection),
            projection=projection,
            rom_valid=projection.get("post_projection_all_inside_rom") is True,
            fk_valid=bool(residual is not None and np.isfinite(float(residual))),
            source_node=source_node, source_boot_epoch=source_boot_epoch,
            source_timer_us=source_timer_us, source_global_ns=source_global_ns,
            source_clock_mapping_digest=source_clock_mapping_digest,
        )

    def ankle_offsets_for_published_correction(
        self,
        time_s: float,
        correction_rotvec: Mapping[str, np.ndarray],
    ) -> dict[str, np.ndarray]:
        """Evaluate an already-published correction on the exact-time base.

        The caller owns the causal history and must supply a correction that
        was published no later than ``time_s``.  This pure query does not
        install a target, advance the transition, or alter sampling state.
        """

        offsets, _velocity = self.ankle_kinematics_for_published_correction(
            time_s, correction_rotvec
        )
        return offsets

    def ankle_kinematics_for_published_correction(
        self,
        time_s: float,
        correction_rotvec: Mapping[str, np.ndarray],
        *,
        previous_time_s: float | None = None,
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        """Evaluate one published correction's ankle pose and base velocity.

        Both current and prior points use the same correction owner.  The
        resulting velocity therefore contains native-base motion but no
        correction-transition jump, which lets root re-gauging isolate only
        the change between two published correction gauges.
        """

        query = float(time_s)
        projected, points, _projection = self._evaluate(
            query, correction_rotvec
        )
        prior_time = (
            max(self.action_start_s, query - self.derivative_period_s)
            if previous_time_s is None else float(previous_time_s)
        )
        if not math.isfinite(prior_time) or prior_time > query:
            raise ValueError("ankle kinematics previous time is invalid")
        if query - prior_time <= 1e-12:
            prior_points = points
            dt = self.derivative_period_s
        else:
            _prior, prior_points, _projection = self._evaluate(
                prior_time, projected
            )
            dt = query - prior_time
        ankle_points = {"left": "ankle_left", "right": "ankle_right"}
        offsets = {
            side: np.asarray(points[name], dtype=float).copy()
            for side, name in ankle_points.items()
        }
        velocity = {
            side: (
                np.asarray(points[name], dtype=float)
                - np.asarray(prior_points[name], dtype=float)
            ) / dt
            for side, name in ankle_points.items()
        }
        return offsets, velocity

    @staticmethod
    def _immutable_array(value: np.ndarray) -> np.ndarray:
        array = np.asarray(value)
        return np.frombuffer(
            array.tobytes(order="C"), dtype=array.dtype,
        ).reshape(array.shape)

    @staticmethod
    def _freeze_vector_mapping(
        values: Mapping[str, np.ndarray],
    ) -> Mapping[str, np.ndarray]:
        frozen = {}
        for key, value in values.items():
            frozen[key] = CausalArticulatedPose._immutable_array(
                np.asarray(value, dtype=float)
            )
        return MappingProxyType(frozen)

    @classmethod
    def _deep_freeze(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return MappingProxyType({
                key: cls._deep_freeze(item) for key, item in value.items()
            })
        if isinstance(value, np.ndarray):
            return cls._immutable_array(np.asarray(value))
        if isinstance(value, (tuple, list)):
            return tuple(cls._deep_freeze(item) for item in value)
        if is_dataclass(value):
            # The source token is already owner-validated.  Reconstructing an
            # evidence dataclass would rerun its normalizer and allocate a new
            # writable backing store, so clone its frozen fields directly.
            copied = object.__new__(type(value))
            for field in fields(value):
                object.__setattr__(
                    copied, field.name,
                    cls._deep_freeze(getattr(value, field.name)),
                )
            return copied
        return value

    @staticmethod
    def _validate_native200_source_sample(
        *, query: float, source_node: str | None,
        source_boot_epoch: int | None, previous_source_timer_us: int | None,
        source_timer_us: int | None, previous_source_global_ns: int | None,
        source_global_ns: int | None,
        source_clock_mapping_digest: str | None,
        previous_base_rotations_world: Mapping[str, np.ndarray] | None,
        current_base_rotations_world: Mapping[str, np.ndarray] | None,
    ) -> None:
        values = (
            source_node, source_boot_epoch, previous_source_timer_us,
            source_timer_us, previous_source_global_ns, source_global_ns,
            source_clock_mapping_digest,
        )
        if any(value is None for value in values):
            raise ValueError("complete native200 source pair required")
        if (
            not isinstance(source_node, str) or not source_node
            or isinstance(source_boot_epoch, bool)
            or isinstance(previous_source_timer_us, bool)
            or isinstance(source_timer_us, bool)
            or isinstance(previous_source_global_ns, bool)
            or isinstance(source_global_ns, bool)
            or int(source_timer_us) - int(previous_source_timer_us) != 5_000
            or int(source_global_ns) <= int(previous_source_global_ns)
            or round(query * 1e9) != int(source_global_ns)
            or not isinstance(source_clock_mapping_digest, str)
            or len(source_clock_mapping_digest) != 64
        ):
            raise ValueError("native200 source pair timing/identity invalid")
        if (
            previous_base_rotations_world is None
            or current_base_rotations_world is None
        ):
            raise ValueError("source-owned native200 base pair required")

    def _native200_batch_provenance_digest(
        self, plan: _PreparedNative200PoseBatch,
    ) -> str:
        """Hash compact authoritative inputs for evidence, not authorization."""

        return self._stable_hash({
            "schema": "BIOSPUR_NATIVE200_POSE_BATCH_PROVENANCE_V1",
            "numeric_kernel": "VARYING_BASE_HINGE_AND_BATCH_ROTATION_V1",
            "base_revision": plan.base_revision,
            "base_latest_sample_s": plan.base_latest_sample_s,
            "base_sample_generation": plan.base_sample_generation,
            "target_digest": plan.target_digest,
            "temporal_digest": plan.temporal_snapshot.digest,
            "rows": tuple({
                "time_s": row.source.time_s,
                "source_node": row.source.source_node,
                "source_boot_epoch": row.source.source_boot_epoch,
                "previous_source_timer_us": row.source.previous_source_timer_us,
                "source_timer_us": row.source.source_timer_us,
                "previous_source_global_ns": row.source.previous_source_global_ns,
                "source_global_ns": row.source.source_global_ns,
                "source_clock_mapping_digest": (
                    row.source.source_clock_mapping_digest
                ),
                "previous_base": row.source.previous_base_rotations_world,
                "current_base": row.source.current_base_rotations_world,
            } for row in plan.rows),
        })

    def prepare_native200_batch(
        self, rows: tuple[Native200PoseBatchInput, ...],
    ) -> _PreparedNative200PoseBatch:
        """Purely prepare up to sixteen consecutive source-owned samples."""

        if not isinstance(rows, tuple) or not 1 <= len(rows) <= 16:
            raise ValueError("native200 pose batch must own 1..16 rows")
        previous_row = None
        copied_rows = []
        for row in rows:
            if type(row) is not Native200PoseBatchInput:
                raise TypeError("native200 pose batch row owner required")
            query = float(row.time_s)
            self._validate_native200_source_sample(
                query=query, source_node=row.source_node,
                source_boot_epoch=row.source_boot_epoch,
                previous_source_timer_us=row.previous_source_timer_us,
                source_timer_us=row.source_timer_us,
                previous_source_global_ns=row.previous_source_global_ns,
                source_global_ns=row.source_global_ns,
                source_clock_mapping_digest=row.source_clock_mapping_digest,
                previous_base_rotations_world=row.previous_base_rotations_world,
                current_base_rotations_world=row.current_base_rotations_world,
            )
            if query + 1e-12 < self._latest_sample_s:
                raise ValueError("causal articulated pose query reversed time")
            if previous_row is not None and (
                row.source_node != previous_row.source_node
                or row.source_boot_epoch != previous_row.source_boot_epoch
                or row.source_clock_mapping_digest
                != previous_row.source_clock_mapping_digest
                or int(row.previous_source_timer_us)
                != int(previous_row.source_timer_us)
                or int(row.previous_source_global_ns)
                != int(previous_row.source_global_ns)
            ):
                raise ValueError("native200 pose batch chronology is not consecutive")
            previous_base = self._copy_base_rotations(
                row.previous_base_rotations_world
            )
            current_base = self._copy_base_rotations(
                row.current_base_rotations_world
            )
            copied_rows.append((row, previous_base, current_base))
            previous_row = row

        projector = self.hinge_projector
        function = projector.func if isinstance(projector, partial) else projector
        model = (
            None if not isinstance(projector, partial)
            else (projector.keywords or {}).get("model")
        )
        target = self._copy_correction(self._target_correction)
        current_bases = {
            segment: np.stack([row[2][segment] for row in copied_rows])
            for segment in SEGMENTS
        }
        target_batch = {
            segment: np.stack([target[segment]] * len(rows))
            for segment in SEGMENTS
        }
        if function is project_hinge_corrections and isinstance(model, Mapping):
            projected_rows, projections = (
                evaluate_varying_base_hinge_projection_batch(
                    current_bases, target_batch, model,
                )
            )
        else:
            evaluated = [
                self._evaluate_base(current, target)
                for _source, _previous, current in copied_rows
            ]
            projected_rows = [value[0] for value in evaluated]
            projections = [value[2] for value in evaluated]
        for projection in projections:
            if projection.get("post_projection_all_inside_rom") is not True:
                raise RuntimeError("current 200 Hz pose projection left hinge ROM")
        points_rows = [
            corrected_proxy_points(
                current, projected, self.geometry,
                _batch_rotation_conversion=True,
            )
            for (_source, _previous, current), projected in zip(
                copied_rows, projected_rows
            )
        ]
        previous_bases = {
            segment: np.stack([row[1][segment] for row in copied_rows])
            for segment in SEGMENTS
        }
        projected_batch = {
            segment: np.stack([row[segment] for row in projected_rows])
            for segment in SEGMENTS
        }
        if function is project_hinge_corrections and isinstance(model, Mapping):
            _previous_projected, _previous_metrics = (
                evaluate_varying_base_hinge_projection_batch(
                    previous_bases, projected_batch, model,
                )
            )
            prior_points_rows = [
                corrected_proxy_points(
                    previous, projected, self.geometry,
                    _batch_rotation_conversion=True,
                )
                for (_source, previous, _current), projected in zip(
                    copied_rows, _previous_projected
                )
            ]
        else:
            prior_points_rows = [
                self._evaluate_base(previous, projected)[1]
                for (_source, previous, _current), projected in zip(
                    copied_rows, projected_rows
                )
            ]
        prepared_rows = []
        for (source, previous, current), projected, points, prior_points, projection in zip(
            copied_rows, projected_rows, points_rows, prior_points_rows,
            projections,
        ):
            copied_source = replace(
                source,
                previous_base_rotations_world=self._freeze_vector_mapping(previous),
                current_base_rotations_world=self._freeze_vector_mapping(current),
            )
            prepared_rows.append(_PreparedNative200PoseRow(
                copied_source,
                self._freeze_vector_mapping(projected),
                self._freeze_vector_mapping(points),
                self._freeze_vector_mapping(prior_points),
                self._deep_freeze(projection),
                source.time_s - source.previous_source_global_ns * 1e-9,
            ))
        temporal = self._deep_freeze(
            self.__hinge_temporal_owner._snapshot_token()
        )
        state = _Native200BatchCommitState(self.__transaction_authority)
        blank = _PreparedNative200PoseBatch(
            self.__transaction_authority, self._pose_revision,
            self._latest_sample_s, self._sample_generation,
            self._stable_hash(target), temporal, tuple(prepared_rows),
            state, "",
        )
        plan = replace(
            blank, digest=self._native200_batch_provenance_digest(blank),
        )
        state.bind(plan, authority=self.__transaction_authority)
        return plan

    def commit_native200_batch(
        self, plan: _PreparedNative200PoseBatch,
    ) -> tuple[CausalArticulatedPoseSample, ...]:
        """Commit one prepared native200 batch atomically and in row order."""

        if (
            type(plan) is not _PreparedNative200PoseBatch
            or plan.authority is not self.__transaction_authority
            or not plan.state.owns(
                plan, authority=self.__transaction_authority,
            )
            or plan.state.consumed
            or plan.base_revision != self._pose_revision
            or plan.base_latest_sample_s != self._latest_sample_s
            or plan.base_sample_generation != self._sample_generation
            or not hmac.compare_digest(
                plan.target_digest,
                self._stable_hash(self._target_correction),
            )
        ):
            raise RuntimeError("STALE_OR_FOREIGN_NATIVE200_POSE_BATCH")
        self.__hinge_temporal_owner._validate_snapshot_token(
            plan.temporal_snapshot
        )
        plan.state.consumed = True
        rollback = self._prepare_install_rollback()
        samples = []
        try:
            for row in plan.rows:
                source = row.source
                baseline_reset = self._velocity_baseline_reset_pending
                hinge_plan = self._hinge_temporal_preview(
                    time_s=source.time_s, correction=row.projected,
                    projection=row.projection, source_node=source.source_node,
                    source_boot_epoch=source.source_boot_epoch,
                    source_timer_us=source.source_timer_us,
                    source_global_ns=source.source_global_ns,
                    source_clock_mapping_digest=(
                        source.source_clock_mapping_digest
                    ),
                )
                hinge_temporal = None if hinge_plan is None else (
                    self.__hinge_temporal_owner._commit_from_pose(
                        hinge_plan, pose_revision=self._pose_revision,
                    )
                )
                offsets = {
                    side: np.asarray(row.points[name], dtype=float).copy()
                    for side, name in {
                        "left": "ankle_left", "right": "ankle_right",
                    }.items()
                }
                velocities = {
                    side: (
                        np.asarray(row.points[name], dtype=float)
                        - np.asarray(row.prior_points[name], dtype=float)
                    ) / row.dt
                    for side, name in {
                        "left": "ankle_left", "right": "ankle_right",
                    }.items()
                }
                samples.append(CausalArticulatedPoseSample(
                    time_s=source.time_s, fraction=None,
                    correction_rotvec={
                        key: value.copy() for key, value in row.projected.items()
                    },
                    points_root_m={
                        key: value.copy() for key, value in row.points.items()
                    },
                    ankle_offset_world_m=offsets,
                    ankle_offset_velocity_world_mps=velocities,
                    projection=dict(row.projection),
                    velocity_baseline_reset=baseline_reset,
                    transition_active=False, transition_step_maximum_rad=0.0,
                    hinge_temporal=hinge_temporal,
                ))
                self._latest_sample_s = max(
                    self._latest_sample_s, source.time_s,
                )
                self._velocity_baseline_reset_pending = False
                self._sample_generation += 1
        except BaseException:
            self._rollback_prevalidated_install(rollback)
            raise
        return tuple(samples)

    def sample(
        self, time_s: float, *, source_node: str | None = None,
        source_boot_epoch: int | None = None,
        previous_source_timer_us: int | None = None,
        source_timer_us: int | None = None,
        previous_source_global_ns: int | None = None,
        source_global_ns: int | None = None,
        source_clock_mapping_digest: str | None = None,
        previous_base_rotations_world: Mapping[str, np.ndarray] | None = None,
        current_base_rotations_world: Mapping[str, np.ndarray] | None = None,
        publish_hinge_temporal: bool = True,
    ) -> CausalArticulatedPoseSample:
        """Return one current-base final pose without using future UWB state."""

        if type(publish_hinge_temporal) is not bool:
            raise ValueError("publish_hinge_temporal must be bool")
        query = float(time_s)
        source_values = (
            source_node, source_boot_epoch, previous_source_timer_us,
            source_timer_us, previous_source_global_ns, source_global_ns,
            source_clock_mapping_digest,
        )
        source_owned = any(value is not None for value in source_values)
        if source_owned:
            self._validate_native200_source_sample(
                query=query, source_node=source_node,
                source_boot_epoch=source_boot_epoch,
                previous_source_timer_us=previous_source_timer_us,
                source_timer_us=source_timer_us,
                previous_source_global_ns=previous_source_global_ns,
                source_global_ns=source_global_ns,
                source_clock_mapping_digest=source_clock_mapping_digest,
                previous_base_rotations_world=previous_base_rotations_world,
                current_base_rotations_world=current_base_rotations_world,
            )
        elif (
            previous_base_rotations_world is not None
            or current_base_rotations_world is not None
        ):
            raise ValueError("native200 base pair requires source ownership")
        if not source_owned and query + 1e-12 < self._latest_availability_s:
            raise ValueError("pose requested before installed UWB availability")
        if query + 1e-12 < self._latest_sample_s:
            raise ValueError("causal articulated pose query reversed time")
        current_transition = self._target_correction
        if source_owned:
            projected, points, projection = self._evaluate_base(
                current_base_rotations_world, current_transition
            )
        else:
            projected, points, projection = self._evaluate(
                query, current_transition
            )
        prior_time = (
            int(previous_source_global_ns) * 1e-9
            if source_owned
            else max(self.action_start_s, query - self.derivative_period_s)
        )
        if query - prior_time <= 1e-12:
            prior_points = points
            dt = self.derivative_period_s
        else:
            if source_owned:
                _prior_correction, prior_points, _prior_projection = (
                    self._evaluate_base(previous_base_rotations_world, projected)
                )
            else:
                _prior_correction, prior_points, _prior_projection = self._evaluate(
                    prior_time, projected
                )
            dt = query - prior_time
        ankle_points = {"left": "ankle_left", "right": "ankle_right"}
        offsets = {
            side: np.asarray(points[name], dtype=float).copy()
            for side, name in ankle_points.items()
        }
        velocities = {
            side: (
                np.asarray(points[name], dtype=float)
                - np.asarray(prior_points[name], dtype=float)
            ) / dt
            for side, name in ankle_points.items()
        }
        baseline_reset = self._velocity_baseline_reset_pending
        hinge_plan = None
        if publish_hinge_temporal:
            hinge_plan = self._hinge_temporal_preview(
                time_s=query, correction=projected, projection=projection,
                source_node=source_node, source_boot_epoch=source_boot_epoch,
                source_timer_us=source_timer_us, source_global_ns=source_global_ns,
                source_clock_mapping_digest=source_clock_mapping_digest,
            )
        hinge_temporal = None if hinge_plan is None else (
            self.__hinge_temporal_owner._commit_from_pose(
                hinge_plan, pose_revision=self._pose_revision
            )
        )
        result = CausalArticulatedPoseSample(
            time_s=query,
            fraction=None if source_owned else self._fraction(query),
            correction_rotvec={
                segment: value.copy() for segment, value in projected.items()
            },
            points_root_m={name: value.copy() for name, value in points.items()},
            ankle_offset_world_m=offsets,
            ankle_offset_velocity_world_mps=velocities,
            projection=projection,
            velocity_baseline_reset=baseline_reset,
            transition_active=False,
            transition_step_maximum_rad=0.0,
            hinge_temporal=hinge_temporal,
        )
        self._latest_sample_s = max(self._latest_sample_s, query)
        self._velocity_baseline_reset_pending = False
        self._sample_generation += 1
        return result
