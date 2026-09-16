"""Leakage-proof primitives for offline C2 held-range body-shadow validation.

This module deliberately separates two owners:

* :class:`Native200CommonClock` maps the accepted native-200 pose samples to
  the Beacon/B306 common clock and provides a strict, past-only pose lookup.
* :class:`HeldRangeLabeler` may use the other ranges in the current sweep to
  form a leave-one-anchor-out label, but those ranges never enter
  :func:`causal_shadow_features`.

The body evidence remains a display-proxy diagnostic.  It is not a LOS label,
does not delete links, and is not consumed by a production solver here.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT
from biospur_fusion.c2_timing_contract import (
    MAXIMUM_POSE_AGE_NS, NATIVE200_PERIOD_US,
)

from .antenna_los import outward_facing_score
from .body_occlusion import _body_segments, _closest_segment_parameters
from .shared_root import SharedRangeLink, solve_shared_root


PELVIS_NODE = "BSFC2CC"
NATIVE_PERIOD_US = NATIVE200_PERIOD_US
MAXIMUM_LOO_CONDITION = 1e8

# A sensor located at a joint is local to both incident centre-lines.  These
# exclusions prevent the other-body feature from recreating own-body facing.
# The pelvis proxy has no pelvis centre-line in the accepted body proxy, so its
# torso overlap is reported as local ambiguity rather than other-body shadow.
INCIDENT_SEGMENTS_BY_NODE: Mapping[str, frozenset[str]] = MappingProxyType({
    "BSF31CC": frozenset({"torso"}),
    "BSFC2CC": frozenset({"torso"}),
    "BSFAA61": frozenset({"upper_arm_left", "forearm_left"}),
    "BSF1120": frozenset({"upper_arm_right", "forearm_right"}),
    "BSFEC35": frozenset({"forearm_left"}),
    "BSFB165": frozenset({"forearm_right"}),
    "BSF44AD": frozenset({"thigh_left", "shank_left"}),
    "BSF3C79": frozenset({"thigh_right", "shank_right"}),
    "BSF6C53": frozenset({"shank_left"}),
    "BSF8BC4": frozenset({"shank_right"}),
})

TRAIN_ACTIONS = (
    "00_initial_still",
    "02_t_pose",
    "03_pelvis_hula_circle",
    "04_shoulder_left",
    "05_shoulder_right",
    "06_elbow_left",
    "07_elbow_right",
    "08_hip_left",
    "09_hip_right",
    "10_knee_left_seated",
    "11_knee_right_seated",
)
VALIDATION_ACTIONS = (
    "12_heel_raise_left",
    "13_heel_raise_right",
    "14_trunk_flex_extend",
    "15_trunk_axial_rotation",
    "16_squat",
    "17_final_still",
    "18_heel_to_butt_left",
    "19_heel_to_butt_right",
)
PILOT_ACTIONS = (
    "00_initial_still",
    "06_elbow_left",
    "09_hip_right",
    "16_squat",
)

# These are model coefficients, not body radii.  The display-proxy envelopes
# remain fixed by body_occlusion.py and are never fitted in O2-PRE.
NESTED_MODEL_CONTRACT = MappingProxyType({
    "response": "signed_held_range_innovation_m=measured_m-predicted_m",
    "common_nuisance": (
        "reference-coded node_anchor_pair_intercept; reference BSFEC35/0",
        "causal_predicted_range_m",
        "causal_tag_origin_x_m",
        "causal_tag_origin_y_m",
        "causal_tag_origin_z_m",
        "base_sigma_m",
        "one common residual scale",
    ),
    "forbidden_nuisance": ("action_intercept", "time_intercept", "posture_intercept"),
    "B0": ("common_nuisance", "own_inward_probability"),
    "B1": ("common_nuisance", "own_inward_probability", "torso_exposure"),
    "B2": (
        "common_nuisance",
        "own_inward_probability",
        "torso_exposure",
        "other_limb_exposure",
    ),
    "shadow_parameter_count": 3,
    "shadow_parameters": (
        "nonnegative beta_own_inward_m",
        "nonnegative beta_torso_m",
        "nonnegative beta_other_limb_m",
    ),
    "pair_gauge": "beta_pair[BSFEC35,0]=0; identical pair basis in B0/B1/B2",
    "fit_scope": "future train actions only; O2-PRE pilot performs no fit",
    "nuisance_scaling": (
        "continuous columns centred and RMS-scaled using train rows only; "
        "the complete reference-coded design is then train-RMS-scaled before SVD/fit"
    ),
    "nuisance_rank_owner": (
        "direct SVD of the response-free train design before any innovation is read"
    ),
    "body_width_owner": (
        "fixed dimensionless middle display-proxy sensitivity envelope from "
        "body_occlusion.py; not anatomy and not fitted"
    ),
    "full_study_requirement": (
        "repeat the predeclared fixed display-proxy sensitivity envelopes; "
        "O2-PRE pilot uses only the nominal existing envelope"
    ),
})


def _immutable_vector(value: np.ndarray, *, size: int = 3) -> np.ndarray:
    result = np.array(value, dtype=float, copy=True).reshape(size)
    if not np.all(np.isfinite(result)):
        raise ValueError("vector must be finite")
    result.setflags(write=False)
    return result


def _immutable_mapping(
    values: Mapping[str, np.ndarray],
) -> Mapping[str, np.ndarray]:
    return MappingProxyType({
        str(name): _immutable_vector(value) for name, value in values.items()
    })


@dataclass(frozen=True)
class StrictPoseIndex:
    frame: int
    pose_global_ns: int
    link_global_ns: float
    age_ns: float


class Native200CommonClock:
    """One action's accepted pose times on the LBD/B306 common clock.

    ``time_root_s`` is the pelvis node's B306 TIMER2 time in seconds.  It is
    converted back to the exact integer microsecond samples and then mapped by
    the same sealed affine clock used for UWB strobe/frame and IMU trigger/base.
    Missing 5 ms samples are retained as gaps; a query spanning a gap fails the
    pose-age gate instead of interpolating.
    """

    def __init__(
        self,
        *,
        action: str,
        time_root_s: np.ndarray,
        source_pelvis_timer_us: np.ndarray,
        source_contiguous_span_id: np.ndarray,
        common_clock_a_ns_per_us: float,
        common_clock_b_ns: float,
        valid_mask: np.ndarray | None = None,
    ) -> None:
        times = np.asarray(time_root_s, dtype=float).reshape(-1)
        if len(times) < 2 or not np.all(np.isfinite(times)):
            raise ValueError("native200 pose times must be finite and non-empty")
        recovered = np.rint(times * 1e6).astype(np.int64)
        if not np.allclose(times * 1e6, recovered, atol=1e-5, rtol=0.0):
            raise ValueError("time_root_s is not an integer B306 microsecond grid")
        source_timer = np.asarray(source_pelvis_timer_us, dtype=np.int64).reshape(-1)
        if not np.array_equal(recovered, source_timer):
            raise ValueError(
                "accepted trajectory is not exactly bound to source pelvis TIMER2"
            )
        span_id = np.asarray(source_contiguous_span_id, dtype=np.int64).reshape(-1)
        if span_id.shape != recovered.shape:
            raise ValueError("source contiguous-span ownership is incompatible")
        delta = np.diff(recovered)
        same_span = span_id[1:] == span_id[:-1]
        if np.any(delta <= 0) or np.any(delta[same_span] != NATIVE_PERIOD_US):
            raise ValueError("pose time is not a gap-safe native 5 ms grid")
        mask = (
            np.ones(len(times), dtype=bool)
            if valid_mask is None
            else np.asarray(valid_mask, dtype=bool).reshape(-1)
        )
        if mask.shape != times.shape or np.count_nonzero(mask) < 2:
            raise ValueError("pose validity mask is incompatible or empty")
        a = float(common_clock_a_ns_per_us)
        b = float(common_clock_b_ns)
        if not math.isfinite(a) or not math.isfinite(b) or a <= 0.0:
            raise ValueError("common clock must be finite with positive slope")
        global_ns = np.rint(a * recovered.astype(float) + b).astype(np.int64)
        if np.any(np.diff(global_ns) <= 0):
            raise ValueError("mapped pose time is not strictly increasing")
        self.action = str(action)
        self.a_ns_per_us = a
        self.b_ns = b
        self.timer_us = recovered.copy()
        self.global_ns = global_ns.copy()
        self.valid = mask.copy()
        self.contiguous_span_id = span_id.copy()
        for array in (
            self.timer_us, self.global_ns, self.valid, self.contiguous_span_id
        ):
            array.setflags(write=False)

    def strict_floor(self, link_global_ns: float) -> StrictPoseIndex:
        query = float(link_global_ns)
        if not math.isfinite(query):
            raise ValueError("link time must be finite")
        valid_frames = np.flatnonzero(self.valid)
        valid_times = self.global_ns[valid_frames]
        local = int(np.searchsorted(valid_times, query, side="left")) - 1
        if local < 0:
            raise ValueError("no strictly preceding native200 pose")
        frame = int(valid_frames[local])
        pose_time = int(self.global_ns[frame])
        age = query - pose_time
        if not 0.0 < age <= MAXIMUM_POSE_AGE_NS:
            raise ValueError(
                f"strict native200 pose age outside (0,5.005ms]: {age} ns"
            )
        return StrictPoseIndex(frame, pose_time, query, age)

    def audit(self) -> dict[str, object]:
        delta = np.diff(self.timer_us)
        same_span = self.contiguous_span_id[1:] == self.contiguous_span_id[:-1]
        return {
            "action": self.action,
            "sample_count": int(len(self.timer_us)),
            "first_timer_us": int(self.timer_us[0]),
            "last_timer_us": int(self.timer_us[-1]),
            "first_global_ns": int(self.global_ns[0]),
            "last_global_ns": int(self.global_ns[-1]),
            "nominal_period_us": NATIVE_PERIOD_US,
            "gap_count": int(np.count_nonzero(~same_span)),
            "same_span_period_exact": bool(
                np.all(delta[same_span] == NATIVE_PERIOD_US)
            ),
            "maximum_gap_us": int(np.max(delta)),
            "mapping": (
                "round(time_root_s*1e6) is pelvis B306 TIMER2; "
                "global_ns=round(a_ns_per_us*timer_us+b_ns)"
            ),
            "source_pelvis_timer_exact_equality": True,
            "progress_or_formal_bound_scaling": False,
        }


@dataclass(frozen=True)
class NodeLinkClock:
    """One target node's sealed LBD/B306 clock for UWB per-link epochs."""

    node: str
    a_ns_per_us: float
    b_ns: float
    boot_epoch: int
    first_timer_us: int
    last_timer_us: int

    def __post_init__(self) -> None:
        if self.node not in NODE_TO_SEGMENT:
            raise ValueError("unknown C2 node clock")
        if (
            not math.isfinite(float(self.a_ns_per_us))
            or not math.isfinite(float(self.b_ns))
            or float(self.a_ns_per_us) <= 0.0
        ):
            raise ValueError("node clock must be finite with positive slope")
        if int(self.last_timer_us) <= int(self.first_timer_us):
            raise ValueError("node clock support interval is invalid")

    def link_time_ns(
        self,
        *,
        event_boot_epoch: int,
        strobe_us: int,
        t_round_us: float,
    ) -> float:
        if int(event_boot_epoch) != int(self.boot_epoch):
            raise ValueError("UWB event boot differs from sealed node clock")
        round_trip = float(t_round_us)
        if not math.isfinite(round_trip) or round_trip < 0.0:
            raise ValueError("t_round_us must be finite and nonnegative")
        query_timer_us = float(strobe_us) + 0.5 * round_trip
        if not (
            int(self.first_timer_us) <= int(strobe_us) <= int(self.last_timer_us)
            and float(self.first_timer_us)
            <= query_timer_us
            <= float(self.last_timer_us)
        ):
            raise ValueError("UWB link time is outside sealed node clock support")
        return float(self.a_ns_per_us) * (
            query_timer_us
        ) + float(self.b_ns)


@dataclass(frozen=True)
class CausalPoseSnapshot:
    action: str
    frame: int
    pose_global_ns: int
    query_global_ns: float
    pose_age_ns: float
    root_world_m: np.ndarray
    offsets_world_m: Mapping[str, np.ndarray]
    normals_world: Mapping[str, np.ndarray]
    joints_relative_world_m: Mapping[str, np.ndarray]

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_world_m", _immutable_vector(self.root_world_m))
        object.__setattr__(
            self, "offsets_world_m", _immutable_mapping(self.offsets_world_m)
        )
        object.__setattr__(
            self, "normals_world", _immutable_mapping(self.normals_world)
        )
        object.__setattr__(
            self,
            "joints_relative_world_m",
            _immutable_mapping(self.joints_relative_world_m),
        )


@dataclass(frozen=True)
class ShadowFeatureEvidence:
    node: str
    own_facing_score: float
    own_inward_probability: float
    torso_exposure: float
    other_limb_exposure: float
    combined_other_body_exposure: float
    incident_segments_excluded: tuple[str, ...]
    near_field_ambiguous_segments: tuple[str, ...]
    dominant_other_segment: str | None
    minimum_normalized_clearance: float

    def nested(self) -> Mapping[str, tuple[float, ...]]:
        return MappingProxyType({
            "B0": (self.own_inward_probability,),
            "B1": (self.own_inward_probability, self.torso_exposure),
            "B2": (
                self.own_inward_probability,
                self.torso_exposure,
                self.other_limb_exposure,
            ),
        })


def causal_shadow_features(
    *,
    node: str,
    anchor_position_world_m: np.ndarray,
    snapshot: CausalPoseSnapshot,
    geometry: DisplayProxyGeometry,
) -> ShadowFeatureEvidence:
    """Evaluate antenna/torso/limb evidence from one pre-epoch snapshot.

    No range value is accepted by this API.  Multiple proxy contributions use
    the bounded, order-invariant union ``1 - prod(1-f_i)``.  An origin-connected
    proxy is reported as near-field ambiguous and contributes zero usable
    other-body evidence; the link itself is never deleted.
    """

    if node not in NODE_TO_SEGMENT or node not in INCIDENT_SEGMENTS_BY_NODE:
        raise KeyError(node)
    try:
        offset = snapshot.offsets_world_m[node]
        normal = snapshot.normals_world[node]
    except KeyError as exc:
        raise ValueError("snapshot does not contain the requested node") from exc
    root = np.asarray(snapshot.root_world_m, dtype=float)
    tag = root + np.asarray(offset, dtype=float)
    anchor = _immutable_vector(anchor_position_world_m)
    ray_length = float(np.linalg.norm(anchor - tag))
    if ray_length <= np.finfo(float).eps:
        raise ValueError("tag and anchor must be distinct")
    points = {
        name: root + np.asarray(value, dtype=float)
        for name, value in snapshot.joints_relative_world_m.items()
    }
    excluded = INCIDENT_SEGMENTS_BY_NODE[node]
    contributions: list[tuple[str, str, float, float]] = []
    ambiguous: list[str] = []
    for name, family, start, stop, proximal, distal in _body_segments(
        points, geometry
    ):
        if name in excluded:
            continue
        distance, ray_fraction, body_fraction = _closest_segment_parameters(
            tag, anchor, start, stop
        )
        radius = proximal + body_fraction * (distal - proximal)
        normalized = distance / max(radius, np.finfo(float).eps)
        origin_connected = (
            ray_fraction <= 32.0 * np.finfo(float).eps and normalized <= 1.0
        )
        if origin_connected:
            ambiguous.append(name)
            contribution = 0.0
        else:
            distance_from_tag = ray_fraction * ray_length
            near_field_ramp = 1.0 - math.exp(
                -max(0.0, distance_from_tag) / 0.05
            )
            contribution = near_field_ramp * math.exp(
                -0.5 * normalized * normalized
            )
        contributions.append((name, family, float(contribution), float(normalized)))
    contributions.sort(key=lambda row: row[0])

    def union(family: str) -> float:
        values = [row[2] for row in contributions if row[1] == family]
        return 0.0 if not values else 1.0 - math.prod(1.0 - value for value in values)

    torso = union("torso")
    limb = union("limb")
    combined = 1.0 - (1.0 - torso) * (1.0 - limb)
    facing = outward_facing_score(tag, anchor, normal)
    dominant = max(contributions, key=lambda row: row[2]) if contributions else None
    return ShadowFeatureEvidence(
        node=node,
        own_facing_score=facing,
        own_inward_probability=0.5 * (1.0 - facing),
        torso_exposure=float(np.clip(torso, 0.0, 1.0)),
        other_limb_exposure=float(np.clip(limb, 0.0, 1.0)),
        combined_other_body_exposure=float(np.clip(combined, 0.0, 1.0)),
        incident_segments_excluded=tuple(sorted(excluded)),
        near_field_ambiguous_segments=tuple(sorted(ambiguous)),
        dominant_other_segment=(
            dominant[0] if dominant is not None and dominant[2] > 1e-12 else None
        ),
        minimum_normalized_clearance=(
            float(min(row[3] for row in contributions))
            if contributions else math.inf
        ),
    )


@dataclass(frozen=True)
class HeldLinkPrediction:
    node: str
    anchor: int
    predicted_range_m: float
    signed_innovation_m: float
    root_position_m: np.ndarray
    rank: int
    condition: float
    eligibility_rank: int
    eligibility_condition: float
    training_identities: tuple[tuple[str, int], ...]
    omitted_identity_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_position_m", _immutable_vector(self.root_position_m))


@dataclass(frozen=True)
class HeldRangeLabeler:
    """Same-node leave-one-anchor-out label owner for one range epoch."""

    anchors_m: Mapping[int, np.ndarray] | np.ndarray
    maximum_condition: float = MAXIMUM_LOO_CONDITION
    maximum_nfev: int = 75

    def __post_init__(self) -> None:
        anchors = np.asarray(self.anchors_m, dtype=float)
        if anchors.shape != (8, 3) or not np.all(np.isfinite(anchors)):
            raise ValueError("the exact eight-anchor C2 layout is required")
        condition = float(self.maximum_condition)
        if not math.isfinite(condition) or condition <= 0.0:
            raise ValueError("maximum condition must be finite and positive")
        maximum_nfev = self.maximum_nfev
        if (
            isinstance(maximum_nfev, (bool, np.bool_))
            or not isinstance(maximum_nfev, (int, np.integer))
            or int(maximum_nfev) <= 0
        ):
            raise ValueError("maximum_nfev must be a positive integer")
        frozen_anchors = anchors.copy()
        frozen_anchors.setflags(write=False)
        object.__setattr__(self, "anchors_m", frozen_anchors)
        object.__setattr__(self, "maximum_condition", condition)
        object.__setattr__(self, "maximum_nfev", int(maximum_nfev))

    @staticmethod
    def omit_identity(
        links: Sequence[SharedRangeLink], identity: tuple[str, int]
    ) -> tuple[tuple[SharedRangeLink, ...], int]:
        target = (str(identity[0]), int(identity[1]))
        kept = tuple(
            link for link in links
            if (str(link.node), int(link.anchor)) != target
        )
        return kept, len(links) - len(kept)

    def label(
        self,
        links: Sequence[SharedRangeLink],
        *,
        target: SharedRangeLink,
        initial_root_m: np.ndarray,
        root_velocity_mps: np.ndarray,
    ) -> HeldLinkPrediction:
        identity = (str(target.node), int(target.anchor))
        same_node = tuple(link for link in links if str(link.node) == identity[0])
        full_identities = tuple(
            (str(link.node), int(link.anchor)) for link in same_node
        )
        if len(set(full_identities)) != len(full_identities):
            raise ValueError("duplicate node-anchor identity in held-label epoch")
        training, omitted_count = self.omit_identity(same_node, identity)
        if omitted_count != 1:
            raise ValueError("held identity must occur exactly once in the epoch")
        identities = tuple((str(link.node), int(link.anchor)) for link in training)
        if len(set(identities)) != len(identities):
            raise ValueError("duplicate node-anchor identity remains after omission")
        unique_anchors = {anchor for _node, anchor in identities}
        if len(training) < 4 or len(unique_anchors) < 4:
            raise ValueError("held label needs at least four unique remaining anchors")
        initial = np.asarray(initial_root_m, dtype=float).reshape(3)
        velocity = np.asarray(root_velocity_mps, dtype=float).reshape(3)
        if not np.all(np.isfinite(initial)) or not np.all(np.isfinite(velocity)):
            raise ValueError("causal prior root and velocity must be finite")
        prior_tag = np.asarray([
            initial
            + np.asarray(link.tag_offset_world_m, dtype=float)
            + float(link.link_dt_s) * velocity
            for link in training
        ])
        anchor_rows = np.asarray([
            self.anchors_m[int(link.anchor)] for link in training
        ])
        delta = anchor_rows - prior_tag
        distance = np.linalg.norm(delta, axis=1)
        if np.any(distance <= np.finfo(float).eps):
            raise ValueError("pre-outcome held geometry is singular at an anchor")
        geometry = delta / distance[:, None]
        singular = np.linalg.svd(geometry, compute_uv=False)
        tolerance = max(geometry.shape) * np.finfo(float).eps * singular[0]
        eligibility_rank = int(np.sum(singular > tolerance))
        eligibility_condition = (
            float(np.linalg.cond(geometry.T @ geometry))
            if eligibility_rank == 3 else math.inf
        )
        if (
            eligibility_rank != 3
            or not math.isfinite(eligibility_condition)
            or eligibility_condition > self.maximum_condition
        ):
            raise ValueError("pre-outcome held geometry eligibility failed")
        result = solve_shared_root(
            training,
            anchors_m=self.anchors_m,
            initial_root_m=initial,
            root_velocity_mps=velocity,
            maximum_condition=self.maximum_condition,
            maximum_nfev=self.maximum_nfev,
        )
        if not result.success or result.rank != 3:
            raise RuntimeError(f"eligible held root solver failed: {result.reason}")
        if not math.isfinite(result.condition) or result.condition > self.maximum_condition:
            raise ValueError("held root condition gate failed")
        tag = (
            result.root_position_m
            + np.asarray(target.tag_offset_world_m, dtype=float)
            + float(target.link_dt_s) * np.asarray(root_velocity_mps, dtype=float)
        )
        predicted = float(np.linalg.norm(self.anchors_m[identity[1]] - tag))
        return HeldLinkPrediction(
            node=identity[0],
            anchor=identity[1],
            predicted_range_m=predicted,
            signed_innovation_m=float(target.range_m) - predicted,
            root_position_m=result.root_position_m,
            rank=result.rank,
            condition=result.condition,
            eligibility_rank=eligibility_rank,
            eligibility_condition=eligibility_condition,
            training_identities=identities,
            omitted_identity_count=omitted_count,
        )


def common_nuisance_values(
    *,
    node: str,
    anchor: int,
    causal_tag_origin_m: np.ndarray,
    anchor_position_m: np.ndarray,
    base_sigma_m: float,
) -> Mapping[str, float | str]:
    """Return the response-free nuisance row shared by B0/B1/B2.

    Pair identity is a fixed effect with one frozen reference gauge.  The five
    continuous columns are causal geometric/measurement-quality controls;
    action, time, posture, shadow interactions, and held residuals are absent.
    """

    if node not in NODE_TO_SEGMENT or not 0 <= int(anchor) < 8:
        raise ValueError("nuisance identity is outside the C2 inventory")
    tag = _immutable_vector(causal_tag_origin_m)
    anchor_row = _immutable_vector(anchor_position_m)
    sigma = float(base_sigma_m)
    if not math.isfinite(sigma) or sigma <= 0.0:
        raise ValueError("base sigma must be finite and positive")
    predicted = float(np.linalg.norm(anchor_row - tag))
    if predicted <= np.finfo(float).eps:
        raise ValueError("causal predicted range is degenerate")
    return MappingProxyType({
        "pair_identity": f"{node}/{int(anchor)}",
        "causal_predicted_range_m": predicted,
        "causal_tag_origin_x_m": float(tag[0]),
        "causal_tag_origin_y_m": float(tag[1]),
        "causal_tag_origin_z_m": float(tag[2]),
        "base_sigma_m": sigma,
    })


def compact_feature_bytes(evidence: ShadowFeatureEvidence) -> bytes:
    """Stable numeric representation used by leakage mutation tests."""

    values = np.asarray([
        evidence.own_facing_score,
        evidence.own_inward_probability,
        evidence.torso_exposure,
        evidence.other_limb_exposure,
        evidence.combined_other_body_exposure,
        evidence.minimum_normalized_clearance,
    ], dtype="<f8")
    text = "\0".join((
        evidence.node,
        ",".join(evidence.incident_segments_excluded),
        ",".join(evidence.near_field_ambiguous_segments),
        evidence.dominant_other_segment or "",
    )).encode("utf-8")
    return values.tobytes() + text
