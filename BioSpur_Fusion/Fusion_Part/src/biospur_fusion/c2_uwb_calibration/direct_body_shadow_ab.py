"""Direct causal A/B weighting for C2 body-shadow diagnostics.

This module owns only a bounded diagnostic policy.  It does not classify a
link as LOS/NLOS and it never removes a geometrically valid range because of
body-shadow evidence.  Both branches consume the same raw range links:

``A`` applies the existing antenna ``-Z`` reliability and a strong torso
display-proxy factor.  ``B`` multiplies ``A`` by a lighter other-limb factor.

All geometry is evaluated from one immutable pose/root snapshot published
strictly before the UWB sweep.  Range values are deliberately absent from the
feature API, which keeps the same mechanism suitable for a future bounded
online evaluator.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from types import MappingProxyType
from typing import Callable, Mapping, Sequence

import numpy as np
from biospur_fusion.c2_timing_contract import MAXIMUM_POSE_AGE_NS

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry

from .antenna_los import outward_facing_reliability, outward_facing_score
from .body_occlusion import _body_segments, _closest_segment_parameters
from .shared_root import SharedRangeLink, SharedRootResult, solve_shared_root


NATIVE_PERIOD_US = 5_000
MATERIAL_WEIGHT_FLOOR = 0.05
MINIMUM_FRESH_POSE_FRACTION = 0.99
MINIMUM_FRESH_SWEEPS = 100


# This diagnostic owns an explicit emitting-segment exclusion table.  It is
# deliberately independent of the prior held-link/LOO evaluator.
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


def _immutable_vector(value: np.ndarray) -> np.ndarray:
    result = np.array(value, dtype=float, copy=True).reshape(3)
    if not np.all(np.isfinite(result)):
        raise ValueError("pose vectors must be finite")
    result.setflags(write=False)
    return result


def _immutable_mapping(values: Mapping[str, np.ndarray]) -> Mapping[str, np.ndarray]:
    return MappingProxyType({key: _immutable_vector(value) for key, value in values.items()})


@dataclass(frozen=True)
class DirectPoseSnapshot:
    """One strictly pre-sweep immutable pose/root publication."""

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
        if not int(self.pose_global_ns) < float(self.query_global_ns):
            raise ValueError("pose snapshot must strictly precede the sweep")
        if not math.isfinite(float(self.pose_age_ns)) or float(self.pose_age_ns) <= 0.0:
            raise ValueError("pose age must be finite and positive")
        object.__setattr__(self, "root_world_m", _immutable_vector(self.root_world_m))
        object.__setattr__(self, "offsets_world_m", _immutable_mapping(self.offsets_world_m))
        object.__setattr__(self, "normals_world", _immutable_mapping(self.normals_world))
        object.__setattr__(
            self, "joints_relative_world_m", _immutable_mapping(self.joints_relative_world_m)
        )


@dataclass(frozen=True)
class DirectPoseIndex:
    frame: int
    pose_global_ns: int
    query_global_ns: float
    age_ns: float


@dataclass(frozen=True)
class PoseUnavailableAudit:
    reason: str
    query_global_ns: float
    selected_frame: int | None
    selected_timer_us: int | None
    selected_pose_global_ns: int | None
    selected_span_id: int | None
    signed_age_ns: float | None
    required_inequality: str = "0 < age_ns <= 5005000"


class PoseUnavailableError(ValueError):
    """A genuine absent/stale pose, distinct from clock-integrity errors."""

    def __init__(self, audit: PoseUnavailableAudit) -> None:
        self.audit = audit
        super().__init__(f"POSE_UNAVAILABLE:{audit.reason}")


def summarize_pose_support(statuses: Sequence[str]) -> dict[str, int | float | bool]:
    """Audit that missing pose support is only a short terminal suffix."""

    frozen = tuple(str(status) for status in statuses)
    if not frozen or any(status not in {"FRESH", "POSE_UNAVAILABLE"} for status in frozen):
        raise ValueError("pose support history is empty or invalid")
    first_unavailable = next(
        (index for index, status in enumerate(frozen) if status == "POSE_UNAVAILABLE"),
        None,
    )
    terminal_suffix = bool(
        first_unavailable is None
        or all(status == "POSE_UNAVAILABLE" for status in frozen[first_unavailable:])
    )
    fresh = frozen.count("FRESH")
    unavailable = frozen.count("POSE_UNAVAILABLE")
    fraction = fresh / len(frozen)
    passed = bool(
        terminal_suffix
        and fresh >= MINIMUM_FRESH_SWEEPS
        and fraction >= MINIMUM_FRESH_POSE_FRACTION
    )
    return {
        "attempted_sweeps": len(frozen),
        "fresh_sweeps": fresh,
        "pose_unavailable_sweeps": unavailable,
        "fresh_fraction": fraction,
        "terminal_unavailable_suffix": terminal_suffix,
        "minimum_fresh_sweeps": MINIMUM_FRESH_SWEEPS,
        "minimum_fresh_fraction": MINIMUM_FRESH_POSE_FRACTION,
        "pass": passed,
    }


class DirectNative200Clock:
    """Hash-bound pelvis TIMER2 to common-clock strict-floor owner."""

    def __init__(
        self,
        *,
        action: str,
        time_root_s: np.ndarray,
        source_pelvis_timer_us: np.ndarray,
        source_contiguous_span_id: np.ndarray,
        common_clock_a_ns_per_us: float,
        common_clock_b_ns: float,
        valid_mask: np.ndarray,
    ) -> None:
        times = np.asarray(time_root_s, dtype=float).reshape(-1)
        timer = np.rint(times * 1e6).astype(np.int64)
        source = np.asarray(source_pelvis_timer_us, dtype=np.int64).reshape(-1)
        spans = np.asarray(source_contiguous_span_id, dtype=np.int64).reshape(-1)
        valid = np.asarray(valid_mask, dtype=bool).reshape(-1)
        if not (
            len(times) >= 2
            and times.shape == source.shape == spans.shape == valid.shape
            and np.all(np.isfinite(times))
            and np.allclose(times * 1e6, timer, atol=1e-5, rtol=0.0)
            and np.array_equal(timer, source)
        ):
            raise ValueError("native200 times are not exact source pelvis TIMER2")
        delta = np.diff(timer)
        same_span = spans[1:] == spans[:-1]
        if np.any(delta <= 0) or np.any(delta[same_span] != NATIVE_PERIOD_US):
            raise ValueError("native200 pose is not a gap-safe 5 ms grid")
        a = float(common_clock_a_ns_per_us)
        b = float(common_clock_b_ns)
        if not math.isfinite(a) or not math.isfinite(b) or a <= 0.0:
            raise ValueError("pelvis common clock is invalid")
        global_ns = np.rint(a * timer.astype(float) + b).astype(np.int64)
        if np.any(np.diff(global_ns) <= 0) or np.count_nonzero(valid) < 2:
            raise ValueError("mapped native200 pose clock is invalid")
        self.action = str(action)
        self.timer_us = timer.copy()
        self.global_ns = global_ns.copy()
        self.valid = valid.copy()
        self.contiguous_span_id = spans.copy()
        for array in (self.timer_us, self.global_ns, self.valid, self.contiguous_span_id):
            array.setflags(write=False)

    def strict_floor(self, query_global_ns: float) -> DirectPoseIndex:
        query = float(query_global_ns)
        if not math.isfinite(query):
            raise ValueError("link time must be finite")
        frames = np.flatnonzero(self.valid)
        local = int(np.searchsorted(self.global_ns[frames], query, side="left")) - 1
        if local < 0:
            raise PoseUnavailableError(PoseUnavailableAudit(
                reason="NO_STRICTLY_PRECEDING_VALID_POSE",
                query_global_ns=query,
                selected_frame=None,
                selected_timer_us=None,
                selected_pose_global_ns=None,
                selected_span_id=None,
                signed_age_ns=None,
            ))
        frame = int(frames[local])
        pose_time = int(self.global_ns[frame])
        age = query - pose_time
        if not 0.0 < age <= MAXIMUM_POSE_AGE_NS:
            if age <= 0.0:
                raise RuntimeError("strict-floor clock produced nonpositive pose age")
            raise PoseUnavailableError(PoseUnavailableAudit(
                reason="STALE_VALID_POSE",
                query_global_ns=query,
                selected_frame=frame,
                selected_timer_us=int(self.timer_us[frame]),
                selected_pose_global_ns=pose_time,
                selected_span_id=int(self.contiguous_span_id[frame]),
                signed_age_ns=float(age),
            ))
        return DirectPoseIndex(frame, pose_time, query, age)

    def exact_tick(
        self,
        query_global_ns: int,
        *,
        source_timer_us: int | None = None,
    ) -> DirectPoseIndex:
        """Select one exact valid native-200 tick for pose publication.

        This is deliberately distinct from :meth:`strict_floor`: UWB pre-link
        ownership needs a strictly earlier pose, whereas publishing a decoded
        native-200 sample must publish the pose from that same source tick.
        """

        if isinstance(query_global_ns, (bool, np.bool_)):
            raise ValueError("native200 publication tick must be an integer")
        try:
            query = int(query_global_ns)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("native200 publication tick must be an integer") from error
        if query != query_global_ns:
            raise ValueError("native200 publication tick must be exact")
        matches = np.flatnonzero(self.valid & (self.global_ns == query))
        if len(matches) != 1:
            raise PoseUnavailableError(PoseUnavailableAudit(
                reason="NO_EXACT_VALID_NATIVE200_POSE",
                query_global_ns=float(query),
                selected_frame=None,
                selected_timer_us=None,
                selected_pose_global_ns=None,
                selected_span_id=None,
                signed_age_ns=None,
                required_inequality="pose_global_ns == source_global_ns",
            ))
        frame = int(matches[0])
        if (
            source_timer_us is not None
            and int(source_timer_us) != int(self.timer_us[frame])
        ):
            raise ValueError("native200 publication timer/global identity mismatch")
        return DirectPoseIndex(frame, query, float(query), 0.0)

@dataclass(frozen=True)
class DirectNodeLinkClock:
    """One node's sealed B306 link-time mapping and support interval."""

    node: str
    a_ns_per_us: float
    b_ns: float
    boot_epoch: int
    first_timer_us: int
    last_timer_us: int

    def __post_init__(self) -> None:
        if (
            not math.isfinite(float(self.a_ns_per_us))
            or not math.isfinite(float(self.b_ns))
            or float(self.a_ns_per_us) <= 0.0
            or int(self.last_timer_us) <= int(self.first_timer_us)
        ):
            raise ValueError("node link clock is invalid")

    def link_time_ns(
        self, *, event_boot_epoch: int, strobe_us: int, t_round_us: float
    ) -> float:
        if int(event_boot_epoch) != int(self.boot_epoch):
            raise ValueError("UWB event boot differs from sealed node clock")
        round_trip = float(t_round_us)
        if not math.isfinite(round_trip) or round_trip < 0.0:
            raise ValueError("t_round_us must be finite and nonnegative")
        query_timer = float(strobe_us) + 0.5 * round_trip
        if not (
            int(self.first_timer_us) <= int(strobe_us) <= int(self.last_timer_us)
            and float(self.first_timer_us) <= query_timer <= float(self.last_timer_us)
        ):
            raise ValueError("UWB link time is outside sealed node clock support")
        return float(self.a_ns_per_us) * query_timer + float(self.b_ns)


@dataclass(frozen=True)
class DirectShadowPolicy:
    """Prospectively fixed, outcome-independent diagnostic weight mapping."""

    torso_strength: float = 0.80
    limb_strength: float = 0.50
    near_field_scale_m: float = 0.05
    maximum_condition: float = 1e8
    maximum_nfev: int = 75

    def __post_init__(self) -> None:
        if not 0.0 <= float(self.torso_strength) < 1.0:
            raise ValueError("torso strength must be in [0, 1)")
        if not 0.0 <= float(self.limb_strength) < 1.0:
            raise ValueError("limb strength must be in [0, 1)")
        if (
            not math.isfinite(float(self.near_field_scale_m))
            or float(self.near_field_scale_m) <= 0.0
        ):
            raise ValueError("near-field scale must be finite and positive")
        if (
            not math.isfinite(float(self.maximum_condition))
            or float(self.maximum_condition) <= 0.0
        ):
            raise ValueError("maximum condition must be finite and positive")
        if (
            isinstance(self.maximum_nfev, bool)
            or int(self.maximum_nfev) != self.maximum_nfev
            or int(self.maximum_nfev) <= 0
        ):
            raise ValueError("maximum_nfev must be a positive integer")


@dataclass(frozen=True)
class SegmentShadowDescriptor:
    name: str
    family: str
    normalized_clearance: float
    normalized_chord_depth: float
    chord_length_m: float
    ray_fraction: float
    exposure: float
    near_field_ambiguous: bool


@dataclass(frozen=True)
class DirectShadowEvidence:
    node: str
    own_facing_score: float
    torso_severity: float
    limb_severity: float
    a_large_weight: float
    b_limb_factor: float
    b_combined_weight: float
    incident_segments_excluded: tuple[str, ...]
    near_field_ambiguous_segments: tuple[str, ...]
    segments: tuple[SegmentShadowDescriptor, ...]


@dataclass(frozen=True)
class BranchGeometry:
    rank: int
    condition: float
    singular_values: tuple[float, ...]
    unique_anchors: tuple[int, ...]


@dataclass(frozen=True)
class DirectABPrepared:
    a_links: tuple[SharedRangeLink, ...]
    b_links: tuple[SharedRangeLink, ...]
    geometry: BranchGeometry
    a_material: "BranchMaterialAudit"
    b_material: "BranchMaterialAudit"
    evidence: tuple[DirectShadowEvidence, ...]
    forced_retained_identities: tuple[tuple[str, int], ...] = ()
    forced_retain_reason: str = "NOT_APPLICABLE_ALL_POSITIVE_LINKS_RETAINED"


@dataclass(frozen=True)
class DirectABResult:
    prepared: DirectABPrepared
    a_result: SharedRootResult
    b_result: SharedRootResult


@dataclass(frozen=True)
class BranchMaterialAudit:
    ordered_identities: tuple[tuple[str, int], ...]
    ordered_weights: tuple[float, ...]
    best_four_identities: tuple[tuple[str, int], ...]
    material_count: int
    support_prefix_identities: tuple[tuple[str, int], ...]
    support_prefix_rank: int
    support_prefix_condition: float
    support_prefix_singular_values: tuple[float, ...]
    weight_floor: float = MATERIAL_WEIGHT_FLOOR


def _bounded_union(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    rows = np.asarray(values, dtype=float)
    if not np.all(np.isfinite(rows)) or np.any((rows < 0.0) | (rows > 1.0)):
        raise ValueError("shadow contributions must be finite in [0, 1]")
    return float(np.clip(1.0 - math.prod(1.0 - float(row) for row in rows), 0.0, 1.0))


def segment_shadow_exposure(
    normalized_clearance: float,
    distance_from_tag_m: float,
    *,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> tuple[float, float]:
    """Return ``(exposure, normalized_chord_depth)`` for one proxy segment."""

    normalized = float(normalized_clearance)
    distance = float(distance_from_tag_m)
    if (
        not math.isfinite(normalized)
        or normalized < 0.0
        or not math.isfinite(distance)
        or distance < 0.0
    ):
        raise ValueError("normalized clearance and path distance must be finite/nonnegative")
    chord_depth = math.sqrt(max(0.0, 1.0 - normalized * normalized))
    near_field = 1.0 - math.exp(-distance / float(policy.near_field_scale_m))
    exposure = (
        near_field
        * math.exp(-0.5 * normalized * normalized)
        * (0.5 + 0.5 * chord_depth)
    )
    return float(np.clip(exposure, 0.0, 1.0)), chord_depth


def shadow_reliability_factors(
    *, torso_severity: float, limb_severity: float,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> tuple[float, float]:
    """Return the large-body and lighter limb multiplicative factors."""

    torso = float(torso_severity)
    limb = float(limb_severity)
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (torso, limb)):
        raise ValueError("class severity must be finite in [0,1]")
    return (
        1.0 - float(policy.torso_strength) * torso,
        1.0 - float(policy.limb_strength) * limb,
    )


def direct_shadow_evidence(
    *,
    node: str,
    anchor_position_world_m: np.ndarray,
    snapshot: DirectPoseSnapshot,
    geometry: DisplayProxyGeometry,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> DirectShadowEvidence:
    """Return continuous large- and small-shadow evidence for one anchor ray.

    The per-segment field is dimensionless and uses only the already-owned
    skeleton-scale display proxy.  ``normalized_chord_depth`` is the chord of
    the local proxy cross-section divided by its diameter; it is zero outside
    the proxy and one through its centre.  A Gaussian clearance term retains a
    mild, continuous grazing response instead of creating a binary capsule.
    """

    policy.__post_init__()
    if node not in INCIDENT_SEGMENTS_BY_NODE:
        raise KeyError(node)
    if not float(snapshot.pose_global_ns) < float(snapshot.query_global_ns):
        raise ValueError("shadow snapshot must be published strictly before the sweep")
    try:
        offset = np.asarray(snapshot.offsets_world_m[node], dtype=float).reshape(3)
        normal = np.asarray(snapshot.normals_world[node], dtype=float).reshape(3)
    except KeyError as exc:
        raise ValueError("snapshot does not contain the requested node") from exc
    root = np.asarray(snapshot.root_world_m, dtype=float).reshape(3)
    anchor = np.asarray(anchor_position_world_m, dtype=float).reshape(3)
    tag = root + offset
    if not all(np.all(np.isfinite(row)) for row in (root, anchor, tag, normal)):
        raise ValueError("shadow geometry must be finite")
    ray_length = float(np.linalg.norm(anchor - tag))
    if ray_length <= np.finfo(float).eps:
        raise ValueError("tag and anchor must be distinct")
    points = {
        name: root + np.asarray(value, dtype=float).reshape(3)
        for name, value in snapshot.joints_relative_world_m.items()
    }
    excluded = INCIDENT_SEGMENTS_BY_NODE[node]
    rows: list[SegmentShadowDescriptor] = []
    for name, family, start, stop, proximal, distal in _body_segments(points, geometry):
        if name in excluded:
            continue
        distance, ray_fraction, body_fraction = _closest_segment_parameters(
            tag, anchor, start, stop
        )
        radius = float(proximal + body_fraction * (distal - proximal))
        if not math.isfinite(radius) or radius <= 0.0:
            raise ValueError("body proxy radius must be finite and positive")
        normalized = float(distance / radius)
        distance_from_tag = max(0.0, float(ray_fraction) * ray_length)
        exposure, chord_depth = segment_shadow_exposure(
            normalized, distance_from_tag, policy=policy
        )
        chord_length = min(ray_length, 2.0 * radius * chord_depth)
        origin_connected = bool(
            ray_fraction <= 32.0 * np.finfo(float).eps and normalized <= 1.0
        )
        if origin_connected:
            exposure = 0.0
        rows.append(SegmentShadowDescriptor(
            name=name,
            family=family,
            normalized_clearance=normalized,
            normalized_chord_depth=chord_depth,
            chord_length_m=float(chord_length),
            ray_fraction=float(ray_fraction),
            exposure=float(np.clip(exposure, 0.0, 1.0)),
            near_field_ambiguous=origin_connected,
        ))
    rows.sort(key=lambda row: row.name)
    torso = _bounded_union([row.exposure for row in rows if row.family == "torso"])
    limb = _bounded_union([row.exposure for row in rows if row.family == "limb"])
    facing = outward_facing_score(tag, anchor, normal)
    facing_weight = outward_facing_reliability(facing)
    torso_factor, limb_factor = shadow_reliability_factors(
        torso_severity=torso, limb_severity=limb, policy=policy
    )
    large_weight = facing_weight * torso_factor
    combined = large_weight * limb_factor
    if not (
        0.0 < large_weight <= 1.0
        and 0.0 < limb_factor <= 1.0
        and 0.0 < combined <= 1.0
    ):
        raise RuntimeError("direct shadow reliability left its positive bounds")
    return DirectShadowEvidence(
        node=node,
        own_facing_score=facing,
        torso_severity=torso,
        limb_severity=limb,
        a_large_weight=large_weight,
        b_limb_factor=limb_factor,
        b_combined_weight=combined,
        incident_segments_excluded=tuple(sorted(excluded)),
        near_field_ambiguous_segments=tuple(
            row.name for row in rows if row.near_field_ambiguous
        ),
        segments=tuple(rows),
    )


def _direct_shadow_batch_core(
    *, node: str, anchor_positions_world_m: np.ndarray,
    snapshot: DirectPoseSnapshot, geometry: DisplayProxyGeometry,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
):
    """Compute one node's eight rays without materializing evidence objects."""
    policy.__post_init__()
    anchors = np.asarray(anchor_positions_world_m, dtype=float)
    if anchors.shape != (8, 3) or not np.isfinite(anchors).all():
        raise ValueError("batch anchors must be eight finite 3-vectors")
    if node not in INCIDENT_SEGMENTS_BY_NODE:
        raise KeyError(node)
    root = np.asarray(snapshot.root_world_m, dtype=float).reshape(3)
    offset = np.asarray(snapshot.offsets_world_m[node], dtype=float).reshape(3)
    normal = np.asarray(snapshot.normals_world[node], dtype=float).reshape(3)
    tag = root + offset
    points = {name: root + np.asarray(value, dtype=float).reshape(3)
              for name, value in snapshot.joints_relative_world_m.items()}
    prepared = [row for row in _body_segments(points, geometry)
                if row[0] not in INCIDENT_SEGMENTS_BY_NODE[node]]
    prepared.sort(key=lambda row: row[0])
    starts = np.asarray([row[2] for row in prepared], dtype=float)
    stops = np.asarray([row[3] for row in prepared], dtype=float)
    proximal = np.asarray([row[4] for row in prepared], dtype=float)
    distal = np.asarray([row[5] for row in prepared], dtype=float)
    rays = anchors - tag
    ray_length = np.linalg.norm(rays, axis=1)
    if np.any(ray_length <= np.finfo(float).eps):
        raise ValueError("tag and anchor must be distinct")
    body = stops - starts
    w = tag - starts
    aa = np.einsum("ai,ai->a", rays, rays)[:, None]
    bb = np.einsum("ai,si->as", rays, body)
    cc = np.einsum("si,si->s", body, body)[None, :]
    dd = np.einsum("ai,si->as", rays, w)
    ee = np.einsum("si,si->s", body, w)[None, :]
    if np.any(cc <= np.finfo(float).eps):
        raise ValueError("body segments must have non-zero length")
    denominator = aa * cc - bb * bb
    ray_fraction = np.where(denominator <= np.finfo(float).eps, 0.0,
                            (bb * ee - cc * dd) / denominator)
    ray_fraction = np.clip(ray_fraction, 0.0, 1.0)
    body_fraction = np.clip((bb * ray_fraction + ee) / cc, 0.0, 1.0)
    ray_fraction = np.clip((bb * body_fraction - dd) / aa, 0.0, 1.0)
    closest_ray = tag + ray_fraction[:, :, None] * rays[:, None, :]
    closest_body = starts[None, :, :] + body_fraction[:, :, None] * body[None, :, :]
    distance = np.linalg.norm(closest_ray - closest_body, axis=2)
    radius = proximal[None, :] + body_fraction * (distal - proximal)[None, :]
    if np.any(~np.isfinite(radius)) or np.any(radius <= 0.0):
        raise ValueError("body proxy radius must be finite and positive")
    normalized = distance / radius
    chord_depth = np.sqrt(np.maximum(0.0, 1.0 - normalized * normalized))
    distance_from_tag = np.maximum(0.0, ray_fraction * ray_length[:, None])
    exposure = ((1.0 - np.exp(-distance_from_tag / float(policy.near_field_scale_m)))
                * np.exp(-0.5 * normalized * normalized) * (0.5 + 0.5 * chord_depth))
    origin_connected = ((ray_fraction <= 32.0 * np.finfo(float).eps) & (normalized <= 1.0))
    exposure = np.where(origin_connected, 0.0, np.clip(exposure, 0.0, 1.0))
    return (anchors,tag,normal,prepared,ray_length,normalized,chord_depth,
            radius,ray_fraction,exposure,origin_connected)


def _direct_shadow_weight_rows(core, policy):
    anchors,tag,normal,prepared,_ray_length,_normalized,_chord_depth,_radius,_ray_fraction,exposure,_origin_connected=core
    output=[]
    for anchor_index,anchor in enumerate(anchors):
        torso=_bounded_union([float(exposure[anchor_index,index]) for index,row in enumerate(prepared) if row[1]=="torso"])
        limb=_bounded_union([float(exposure[anchor_index,index]) for index,row in enumerate(prepared) if row[1]=="limb"])
        facing=outward_facing_score(tag,anchor,normal)
        torso_factor,limb_factor=shadow_reliability_factors(torso_severity=torso,limb_severity=limb,policy=policy)
        large=outward_facing_reliability(facing)*torso_factor
        output.append((facing,torso,limb,large,limb_factor,large*limb_factor))
    return tuple(output)


def direct_shadow_weights_batch(
    *, node: str, anchor_positions_world_m: np.ndarray,
    snapshot: DirectPoseSnapshot, geometry: DisplayProxyGeometry,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> np.ndarray:
    """Return only the eight online reliability weights from the shared core."""
    core=_direct_shadow_batch_core(node=node,anchor_positions_world_m=anchor_positions_world_m,
        snapshot=snapshot,geometry=geometry,policy=policy)
    result=np.asarray([row[5] for row in _direct_shadow_weight_rows(core,policy)],dtype=float)
    result.setflags(write=False)
    return result


def direct_shadow_evidence_batch(
    *, node: str, anchor_positions_world_m: np.ndarray,
    snapshot: DirectPoseSnapshot, geometry: DisplayProxyGeometry,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> tuple[DirectShadowEvidence, ...]:
    """Evaluate exact eight-ray evidence after one shared numerical core."""
    core=_direct_shadow_batch_core(node=node,anchor_positions_world_m=anchor_positions_world_m,
        snapshot=snapshot,geometry=geometry,policy=policy)
    anchors,_tag,_normal,prepared,ray_length,normalized,chord_depth,radius,ray_fraction,exposure,origin_connected=core
    weight_rows=_direct_shadow_weight_rows(core,policy)
    output = []
    excluded = tuple(sorted(INCIDENT_SEGMENTS_BY_NODE[node]))
    for anchor_index, anchor in enumerate(anchors):
        rows = tuple(SegmentShadowDescriptor(
            name=prepared[index][0], family=prepared[index][1],
            normalized_clearance=float(normalized[anchor_index, index]),
            normalized_chord_depth=float(chord_depth[anchor_index, index]),
            chord_length_m=float(min(ray_length[anchor_index], 2.0 * radius[anchor_index, index] * chord_depth[anchor_index, index])),
            ray_fraction=float(ray_fraction[anchor_index, index]),
            exposure=float(exposure[anchor_index, index]),
            near_field_ambiguous=bool(origin_connected[anchor_index, index]))
            for index in range(len(prepared)))
        facing,torso,limb,large,limb_factor,combined=weight_rows[anchor_index]
        output.append(DirectShadowEvidence(
            node, facing, torso, limb, large, limb_factor, combined,
            excluded, tuple(row.name for row in rows if row.near_field_ambiguous), rows))
    return tuple(output)


def _preoutcome_geometry(
    links: Sequence[SharedRangeLink],
    *,
    anchors_m: np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray,
    maximum_condition: float,
) -> BranchGeometry:
    if len(links) < 4:
        raise ValueError("direct A/B solve requires at least four links")
    identities = tuple((str(link.node), int(link.anchor)) for link in links)
    if len(set(identities)) != len(identities):
        raise ValueError("duplicate node-anchor identity in direct A/B sweep")
    if len({anchor for _node, anchor in identities}) < 4:
        raise ValueError("direct A/B solve requires four unique anchors")
    nodes = {node for node, _anchor in identities}
    if len(nodes) != 1:
        raise ValueError("direct A/B owner accepts exactly one tag sweep")
    anchors = np.asarray(anchors_m, dtype=float)
    initial = np.asarray(initial_root_m, dtype=float).reshape(3)
    velocity = np.asarray(root_velocity_mps, dtype=float).reshape(3)
    if anchors.shape != (8, 3) or not np.all(np.isfinite(anchors)):
        raise ValueError("exact finite eight-anchor geometry is required")
    tags = np.asarray([
        initial
        + np.asarray(link.tag_offset_world_m, dtype=float).reshape(3)
        + float(link.link_dt_s) * velocity
        for link in links
    ])
    anchor_rows = np.asarray([anchors[int(link.anchor)] for link in links])
    delta = anchor_rows - tags
    distance = np.linalg.norm(delta, axis=1)
    if np.any(distance <= np.finfo(float).eps):
        raise ValueError("pre-outcome geometry is singular at an anchor")
    design = delta / distance[:, None]
    singular = np.linalg.svd(design, compute_uv=False)
    tolerance = max(design.shape) * np.finfo(float).eps * singular[0]
    rank = int(np.sum(singular > tolerance))
    condition = float(np.linalg.cond(design.T @ design)) if rank == 3 else math.inf
    if rank != 3 or not math.isfinite(condition) or condition > maximum_condition:
        raise ValueError("pre-outcome direct A/B geometry gate failed")
    return BranchGeometry(
        rank=rank,
        condition=condition,
        singular_values=tuple(float(value) for value in singular),
        unique_anchors=tuple(sorted({anchor for _node, anchor in identities})),
    )


def _weighted_material_audit(
    links: Sequence[SharedRangeLink],
    *,
    anchors_m: np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray,
    maximum_condition: float,
) -> BranchMaterialAudit:
    ordered = tuple(sorted(
        links,
        key=lambda link: (-float(link.information_weight), int(link.anchor)),
    ))
    material = tuple(
        link for link in ordered
        if float(link.information_weight) >= MATERIAL_WEIGHT_FLOOR
    )
    if len(material) < 4 or len({int(link.anchor) for link in material}) < 4:
        raise ValueError("fewer than four unique links meet the fixed material floor")
    anchors = np.asarray(anchors_m, dtype=float)
    initial = np.asarray(initial_root_m, dtype=float).reshape(3)
    velocity = np.asarray(root_velocity_mps, dtype=float).reshape(3)
    selected: tuple[SharedRangeLink, ...] | None = None
    selected_rank = 0
    selected_condition = math.inf
    selected_singular = np.empty(0, dtype=float)
    for count in range(4, len(material) + 1):
        candidate = material[:count]
        tags = np.asarray([
            initial
            + np.asarray(link.tag_offset_world_m, dtype=float).reshape(3)
            + float(link.link_dt_s) * velocity
            for link in candidate
        ])
        anchor_rows = np.asarray([anchors[int(link.anchor)] for link in candidate])
        delta = anchor_rows - tags
        distance = np.linalg.norm(delta, axis=1)
        if np.any(distance <= np.finfo(float).eps):
            raise ValueError("material geometry is singular at an anchor")
        unit_los = delta / distance[:, None]
        weights = np.sqrt(np.asarray([
            float(link.information_weight) for link in candidate
        ]))
        design = weights[:, None] * unit_los
        singular = np.linalg.svd(design, compute_uv=False)
        tolerance = max(design.shape) * np.finfo(float).eps * singular[0]
        rank = int(np.sum(singular > tolerance))
        condition = (
            float(np.linalg.cond(design.T @ design)) if rank == 3 else math.inf
        )
        if rank == 3 and math.isfinite(condition) and condition <= maximum_condition:
            selected = candidate
            selected_rank = rank
            selected_condition = condition
            selected_singular = singular
            break
    if selected is None:
        raise ValueError("material reliability prefix failed weighted rank/condition")
    return BranchMaterialAudit(
        ordered_identities=tuple((str(link.node), int(link.anchor)) for link in ordered),
        ordered_weights=tuple(float(link.information_weight) for link in ordered),
        best_four_identities=tuple(
            (str(link.node), int(link.anchor)) for link in material[:4]
        ),
        material_count=len(material),
        support_prefix_identities=tuple(
            (str(link.node), int(link.anchor)) for link in selected
        ),
        support_prefix_rank=selected_rank,
        support_prefix_condition=selected_condition,
        support_prefix_singular_values=tuple(float(value) for value in selected_singular),
    )


def _assert_only_information_weight_differs(
    a_links: Sequence[SharedRangeLink], b_links: Sequence[SharedRangeLink]
) -> None:
    if len(a_links) != len(b_links):
        raise RuntimeError("A/B solver link counts changed")
    for a_link, b_link in zip(a_links, b_links, strict=True):
        for field in SharedRangeLink.__dataclass_fields__:
            if field == "information_weight":
                continue
            a_value = getattr(a_link, field)
            b_value = getattr(b_link, field)
            if isinstance(a_value, np.ndarray) or isinstance(b_value, np.ndarray):
                if not np.array_equal(np.asarray(a_value), np.asarray(b_value)):
                    raise RuntimeError(f"A/B solver link field changed: {field}")
            elif a_value != b_value:
                raise RuntimeError(f"A/B solver link field changed: {field}")


def prepare_direct_ab_links(
    links: Sequence[SharedRangeLink],
    *,
    evidence_by_identity: Mapping[tuple[str, int], DirectShadowEvidence],
    anchors_m: np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> DirectABPrepared:
    """Apply positive A/B information weights without deleting a range."""

    ordered = tuple(links)
    geometry = _preoutcome_geometry(
        ordered,
        anchors_m=anchors_m,
        initial_root_m=initial_root_m,
        root_velocity_mps=root_velocity_mps,
        maximum_condition=float(policy.maximum_condition),
    )
    evidence: list[DirectShadowEvidence] = []
    a_links: list[SharedRangeLink] = []
    b_links: list[SharedRangeLink] = []
    for link in ordered:
        identity = (str(link.node), int(link.anchor))
        try:
            row = evidence_by_identity[identity]
        except KeyError as exc:
            raise ValueError(f"missing pre-epoch shadow evidence for {identity}") from exc
        if row.node != link.node:
            raise ValueError("shadow evidence identity changed")
        base_weight = float(link.information_weight)
        if not math.isfinite(base_weight) or not 0.0 < base_weight <= 1.0:
            raise ValueError("base information weight must be in (0, 1]")
        a_weight = base_weight * row.a_large_weight
        b_weight = base_weight * row.b_combined_weight
        a_links.append(replace(link, information_weight=a_weight))
        b_links.append(replace(link, information_weight=b_weight))
        evidence.append(row)
    if set(evidence_by_identity) != {
        (str(link.node), int(link.anchor)) for link in ordered
    }:
        raise ValueError("shadow evidence contains identities outside the sweep")
    a_tuple = tuple(a_links)
    b_tuple = tuple(b_links)
    _assert_only_information_weight_differs(a_tuple, b_tuple)
    a_material = _weighted_material_audit(
        a_tuple,
        anchors_m=anchors_m,
        initial_root_m=initial_root_m,
        root_velocity_mps=root_velocity_mps,
        maximum_condition=float(policy.maximum_condition),
    )
    b_material = _weighted_material_audit(
        b_tuple,
        anchors_m=anchors_m,
        initial_root_m=initial_root_m,
        root_velocity_mps=root_velocity_mps,
        maximum_condition=float(policy.maximum_condition),
    )
    return DirectABPrepared(
        a_links=a_tuple,
        b_links=b_tuple,
        geometry=geometry,
        a_material=a_material,
        b_material=b_material,
        evidence=tuple(evidence),
    )


def solve_direct_ab(
    links: Sequence[SharedRangeLink],
    *,
    evidence_by_identity: Mapping[tuple[str, int], DirectShadowEvidence],
    anchors_m: np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray,
    policy: DirectShadowPolicy = DirectShadowPolicy(),
) -> DirectABResult:
    """Run exactly one same-setting shared-root solve for each A/B branch."""

    prepared = prepare_direct_ab_links(
        links,
        evidence_by_identity=evidence_by_identity,
        anchors_m=anchors_m,
        initial_root_m=initial_root_m,
        root_velocity_mps=root_velocity_mps,
        policy=policy,
    )
    common = dict(
        anchors_m=anchors_m,
        initial_root_m=initial_root_m,
        root_velocity_mps=root_velocity_mps,
        maximum_condition=float(policy.maximum_condition),
        maximum_nfev=int(policy.maximum_nfev),
    )
    a_result = solve_shared_root(prepared.a_links, **common)
    b_result = solve_shared_root(prepared.b_links, **common)
    return DirectABResult(prepared=prepared, a_result=a_result, b_result=b_result)


def commit_a_after_paired_results(
    paired: DirectABResult,
    commit: Callable[[np.ndarray], None],
) -> None:
    """Commit only branch A after both paired results have succeeded.

    This makes the pilot's ownership explicit: B is a local incremental
    counterfactual, not a separately propagated filter.  A failure in either
    branch blocks the sweep and leaves future tracker state untouched.
    """

    if not paired.a_result.success or not paired.b_result.success:
        raise RuntimeError(
            "paired solver failure: "
            f"A={paired.a_result.reason},B={paired.b_result.reason}"
        )
    commit(np.array(paired.a_result.root_position_m, dtype=float, copy=True))
