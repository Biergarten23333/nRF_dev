"""Non-promotable controlled root A/B: identical IMU, B-only position UWB."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math

import numpy as np

from biospur_fusion.c2_coupled_progressive.action00_missing_tilt_adapter import Action00MissingTiltInitialization
from biospur_fusion.c2_coupled_progressive.continuous_frontend import ContinuousEvent
from biospur_fusion.c2_coupled_progressive.continuous_group_epoch_owner import canonical_epoch_bucket
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRangeLink, solve_shared_root
from biospur_fusion.ingest.events import TypedEvent
from biospur_fusion.root_r3.estimator import CausalDelayedRootFilter, RootTranslationEdgeMode
from biospur_fusion.root_r3.models import ImuSample, PositionObservation, RootState

from .continuous_root_ab import uwb_row_from_event
from .diagnostic_action00_pose_source import DiagnosticAction00StrictFloorPoseSource
from .diagnostic_c2_static_owner import DiagnosticC2StaticOwner
from .diagnostic_pelvis_orientation import DiagnosticPelvisOrientationFrame, PELVIS_NODE
from .offline_unified_wiring import build_causal_links, group_epoch_times_ns

_QUALIFICATION = "DIAGNOSTIC_ROOT_ONLY_NON_PROMOTABLE"
_CONSENSUS_M = 0.05
_RESIDUAL_RMS_M = 0.50
_MAXIMUM_CONDITION = 1e8
_POSITION_REGULARIZATION_M2 = 1e-6
_VELOCITY_VARIANCE = 1.0
_BIAS_VARIANCE = 0.25
_PROVISIONAL_SIGMA_M = 0.10


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class DiagnosticBootstrapResult:
    measurement_time_s: float
    availability_time_s: float
    root_position_m: np.ndarray
    residual_rms_m: float
    links: int
    owner_digest: str

    def __post_init__(self):
        root = np.asarray(self.root_position_m, float).reshape(3).copy()
        root.setflags(write=False)
        if self.links != 80 or not np.isfinite(root).all() or len(self.owner_digest) != 64:
            raise ValueError("invalid diagnostic bootstrap result")
        object.__setattr__(self, "root_position_m", root)


class DiagnosticSharedRootABOwner:
    """A diagnostic state owner deliberately unreachable from production registries."""
    qualification = _QUALIFICATION
    product_ready = False
    scientific_pass = False

    def __init__(self, *, static: DiagnosticC2StaticOwner,
                 pose_source: DiagnosticAction00StrictFloorPoseSource,
                 gauge: DiagnosticPelvisOrientationFrame) -> None:
        if type(static) is not DiagnosticC2StaticOwner:
            raise TypeError("A/B owner requires the sealed diagnostic static owner")
        static.validate_integrity()
        if type(pose_source) is not DiagnosticAction00StrictFloorPoseSource:
            raise TypeError("A/B owner requires the typed strict-floor pose source")
        if type(gauge) is not DiagnosticPelvisOrientationFrame or gauge.region_id != "00_initial_still":
            raise ValueError("A/B owner requires the first owned Action00 pelvis gauge")
        self.__static = static
        self.__pose = pose_source
        self.__gauge = gauge
        self.__a: CausalDelayedRootFilter | None = None
        self.__b: CausalDelayedRootFilter | None = None
        self.__bootstrap: DiagnosticBootstrapResult | None = None
        self.__missing_digest: str | None = None
        self.__prebootstrap = 0
        self.__imu = 0
        self.__uwb = 0
        self.__last_measurement = -math.inf
        self.__last_availability = -math.inf
        self.__owner = _digest({
            "schema": "biospur.c2.diagnostic_shared_root_ab.v1",
            "static": static.digest, "pose": pose_source.owner_digest,
            "gauge": gauge.digest, "root_config": static.root_config.__dict__,
            "bootstrap": (_CONSENSUS_M, _RESIDUAL_RMS_M, _MAXIMUM_CONDITION),
            "covariance": (_POSITION_REGULARIZATION_M2, _VELOCITY_VARIANCE, _BIAS_VARIANCE),
            "range_sigma": (_PROVISIONAL_SIGMA_M, "DIAGNOSTIC_ONLY_UNCALIBRATED"),
            "branches": "IDENTICAL_INERTIAL;A_NO_UWB;B_POSITION_INDICES_0_1_2",
            "terminal_missing": "AUDIT_ONLY_NO_MODE_CHANGE", "qualification": self.qualification,
        })

    @property
    def located(self): return self.__a is not None
    @property
    def branch_a(self): return self.__a
    @property
    def branch_b(self): return self.__b
    @property
    def prebootstrap_event_count(self): return self.__prebootstrap

    def note_prebootstrap_event(self) -> None:
        if self.located: raise RuntimeError("root is already bootstrapped")
        self.__prebootstrap += 1

    def bootstrap_first_complete_action00_group(self, events: tuple[ContinuousEvent, ...]) -> DiagnosticBootstrapResult:
        if self.located: raise RuntimeError("diagnostic root already bootstrapped")
        if len(events) != 10:
            self.__prebootstrap += len(events)
            raise ValueError("bootstrap requires the first complete ten-node group")
        ordered = tuple(sorted(events, key=lambda event: event.node_id))
        if tuple(event.node_id for event in ordered) != tuple(sorted(self.__static.clocks)):
            raise ValueError("bootstrap node inventory mismatch")
        if any(event.kind != "UWB" or event.action_index != 0 or event.action_id != "00_initial_still" or event.region_id is not None for event in ordered):
            raise ValueError("bootstrap group is not exact Action00 UWB")
        if len({canonical_epoch_bucket(event.common_global_ns) for event in ordered}) != 1:
            raise ValueError("bootstrap rows are not one epoch")
        if min(event.common_global_ns for event in ordered) < round(self.__gauge.availability_time_s * 1e9) or min(event.availability_global_ns for event in ordered) < round(self.__gauge.availability_time_s * 1e9):
            self.__prebootstrap += len(events)
            raise ValueError("bootstrap group predates pelvis gauge availability")
        rows = tuple(uwb_row_from_event(event.payload_owner) for event in ordered)
        links, _audit, measurement, availability = build_causal_links(
            rows, clocks=self.__static.clocks,
            strict_floor_offset=lambda node, query: self.__pose.selection_for(node, query).offset,
            anchor_delay_m=self.__static.anchor_delay_m, tag_delay_m=self.__static.tag_delay_m,
            sigma_for_quality=lambda quality: _PROVISIONAL_SIGMA_M,
        )
        if len(links) != 80:
            raise ValueError("bootstrap requires all 80 node-anchor links")
        anchors = self.__static.anchors_m
        lower, upper = anchors.min(0), anchors.max(0)
        inset = np.minimum(0.15 * np.maximum(upper - lower, 1e-6), 0.25)
        starts = tuple(np.array((x, y, z)) for x in (lower[0]+inset[0], upper[0]-inset[0])
                       for y in (lower[1]+inset[1], upper[1]-inset[1])
                       for z in (lower[2]+inset[2], upper[2]-inset[2]))
        solved = [solve_shared_root(links, anchors_m=anchors, initial_root_m=start,
                                    maximum_condition=_MAXIMUM_CONDITION) for start in starts]
        accepted = sorted((item for item in solved if item.success and item.rank == 3 and math.isfinite(item.condition)), key=lambda item: (item.cost, tuple(item.root_position_m)))
        if not accepted: raise RuntimeError("PRIOR_FREE_SHARED_ROOT_DID_NOT_CONVERGE")
        best = accepted[0]
        if max(np.linalg.norm(item.root_position_m-best.root_position_m) for item in accepted) > _CONSENSUS_M:
            raise RuntimeError("PRIOR_FREE_SHARED_ROOT_LACKS_CONSENSUS")
        rms = float(np.sqrt(np.mean(np.square(best.residuals_m))))
        if not math.isfinite(rms) or rms > _RESIDUAL_RMS_M:
            raise RuntimeError("PRIOR_FREE_SHARED_ROOT_RESIDUAL_REJECTED")
        delta = best.root_position_m[None, :] + np.stack([link.tag_offset_world_m for link in links]) - anchors[[link.anchor for link in links]]
        jac = delta / np.linalg.norm(delta, axis=1)[:, None]
        covariance = np.zeros((9, 9)); covariance[:3, :3] = np.linalg.inv(jac.T @ jac / _PROVISIONAL_SIGMA_M**2) + np.eye(3)*_POSITION_REGULARIZATION_M2
        covariance[3:6, 3:6] = np.eye(3)*_VELOCITY_VARIANCE; covariance[6:9, 6:9] = np.eye(3)*_BIAS_VARIANCE
        state = RootState(measurement, np.r_[best.root_position_m, np.zeros(6)], covariance)
        self.__a = CausalDelayedRootFilter(deepcopy(state), self.__static.root_config, inertial=True)
        self.__b = CausalDelayedRootFilter(deepcopy(state), self.__static.root_config, inertial=True)
        result = DiagnosticBootstrapResult(measurement, availability, best.root_position_m, rms, len(links), self.__owner)
        self.__bootstrap = result; self.__last_measurement = measurement; self.__last_availability = availability
        return result

    def add_pelvis_imu(self, frame: DiagnosticPelvisOrientationFrame) -> None:
        if not self.located: self.__prebootstrap += 1; return
        if type(frame) is not DiagnosticPelvisOrientationFrame or frame.measurement_time_s <= self.__last_measurement or frame.availability_time_s < self.__last_availability:
            raise ValueError("diagnostic pelvis IMU chronology mismatch")
        sample = ImuSample(frame.measurement_time_s, frame.availability_time_s,
                           frame.specific_force_sensor_mps2, frame.rotation_world_from_sensor,
                           frame.source_sequence)
        a, b = deepcopy(self.__a), deepcopy(self.__b)
        if not a.add_imu(sample, edge_mode=RootTranslationEdgeMode.INERTIAL, following_input_mode=RootTranslationEdgeMode.INERTIAL) or not b.add_imu(sample, edge_mode=RootTranslationEdgeMode.INERTIAL, following_input_mode=RootTranslationEdgeMode.INERTIAL):
            raise RuntimeError("identical diagnostic IMU propagation rejected")
        self.__a, self.__b = a, b; self.__imu += 1
        self.__last_measurement = frame.measurement_time_s; self.__last_availability = frame.availability_time_s

    def audit_terminal_missing(self, initialization: Action00MissingTiltInitialization) -> None:
        if type(initialization) is not Action00MissingTiltInitialization or initialization.policy.status != "MISSING":
            raise ValueError("terminal audit requires sealed Action00 MISSING initialization")
        if self.__missing_digest is not None: raise RuntimeError("terminal MISSING already audited")
        pelvis = next(row for row in initialization.nodes if row.evidence.source_node == PELVIS_NODE)
        self.__missing_digest = _digest((initialization.policy.digest, pelvis.evidence.digest, "AUDIT_ONLY"))

    def add_pelvis_uwb(self, event: ContinuousEvent):
        if not self.located: self.__prebootstrap += 1; return None
        if type(event) is not ContinuousEvent or event.kind != "UWB" or event.node_id != PELVIS_NODE or event.action_id == "00_initial_still":
            raise ValueError("post-bootstrap UWB must be gap/Action02 pelvis only")
        row = uwb_row_from_event(event.payload_owner)
        epochs, measurement_ns, availability_ns = group_epoch_times_ns((row,), clocks=self.__static.clocks)
        links = []
        for anchor in range(8):
            if not (row.valid_mask & (1 << anchor)) or row.anchor_ids[anchor] != anchor: continue
            link_ns = self.__static.clocks[row.node].link_time_ns(event_boot_epoch=row.boot, strobe_us=row.strobe_us, t_round_us=row.t_round_us[anchor])
            links.append(SharedRangeLink(row.node, anchor, row.ranges_mm[anchor]/1000-self.__static.anchor_delay_m[anchor]-self.__static.tag_delay_m,
                                         np.zeros(3), (link_ns-measurement_ns)*1e-9, _PROVISIONAL_SIGMA_M))
        candidate = solve_shared_root(links, anchors_m=self.__static.anchors_m,
                                      initial_root_m=self.__b.current_state.vector[:3], root_velocity_mps=self.__b.current_state.vector[3:6], maximum_condition=_MAXIMUM_CONDITION)
        if not candidate.success: raise RuntimeError(f"PELVIS_ROOT_{candidate.reason}")
        observation = PositionObservation(measurement_ns*1e-9, availability_ns*1e-9,
            candidate.root_position_m, np.eye(3)*_PROVISIONAL_SIGMA_M**2,
            "C2_DIAGNOSTIC_PELVIS_UWB", candidate.anchors_used,
            "DIAGNOSTIC_ONLY_UNCALIBRATED_POSITION_ONLY", source_sequence=row.sequence)
        a_token = self.__a.publication_token(); b_before = self.__b.current_state.vector.copy()
        trial = deepcopy(self.__b)
        plan = trial.prepare_position(observation, state_update_indices=(0, 1, 2))
        decision = trial._apply_prevalidated_position(trial._prevalidate_position_plan(plan))
        if not decision.accepted: return decision
        if self.__a.publication_token().digest != a_token.digest or not np.array_equal(trial.current_state.vector[3:], b_before[3:]):
            raise RuntimeError("position-only A/B ownership invariant failed")
        self.__b = trial; self.__uwb += 1
        self.__last_measurement = measurement_ns*1e-9; self.__last_availability = availability_ns*1e-9
        return decision

    def owner_bytes(self) -> bytes:
        return json.dumps({"owner": self.__owner, "bootstrap": None if self.__bootstrap is None else self.__bootstrap.owner_digest,
            "a": None if self.__a is None else self.__a.publication_token().digest,
            "b": None if self.__b is None else self.__b.publication_token().digest,
            "missing": self.__missing_digest, "prebootstrap": self.__prebootstrap,
            "imu": self.__imu, "uwb": self.__uwb,
            "last": (self.__last_measurement, self.__last_availability)}, sort_keys=True, separators=(",", ":")).encode()
