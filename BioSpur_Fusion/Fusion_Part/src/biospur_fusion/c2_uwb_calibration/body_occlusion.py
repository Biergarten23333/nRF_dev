"""Causal, online-ready body-shadow evidence for C2 UWB links.

The geometry in this module is deliberately a *soft prior*.  It never labels
LOS/NLOS on its own and never deletes a range.  A link is downweighted only
when a positive range innovation agrees with antenna/body geometry.  All
state is bounded to the fixed ten-node/eight-anchor inventory, so offline
replay and a future live host use the same one-epoch-at-a-time interface.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Mapping

import numpy as np

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from .shared_root import SharedRangeLink


NODE_LOCAL_SEGMENT = {
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSFAA61": "upper_arm_left",
    "BSF1120": "upper_arm_right",
    "BSFEC35": "forearm_left",
    "BSFB165": "forearm_right",
    "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left",
    "BSF8BC4": "shank_right",
}


@dataclass(frozen=True)
class BodyOcclusionEvidence:
    score: float
    torso_score: float
    limb_score: float
    dominant_segment: str | None
    minimum_normalized_clearance: float


@dataclass(frozen=True)
class LinkReliabilityConfig:
    minimum_information_weight: float = 0.05
    positive_innovation_scale_m: float = 0.20
    history_alpha: float = 0.20
    history_gain: float = 0.35

    def validate(self) -> None:
        if not 0.0 < self.minimum_information_weight <= 1.0:
            raise ValueError("minimum information weight must be in (0, 1]")
        if not math.isfinite(self.positive_innovation_scale_m) or (
            self.positive_innovation_scale_m <= 0.0
        ):
            raise ValueError("positive innovation scale must be positive")
        if not 0.0 < self.history_alpha <= 1.0:
            raise ValueError("history alpha must be in (0, 1]")
        if not 0.0 <= self.history_gain <= 1.0:
            raise ValueError("history gain must be in [0, 1]")


@dataclass(frozen=True)
class LinkReliabilityDecision:
    node: str
    anchor: int
    information_weight: float
    body_occlusion_score: float
    facing_inward_probability: float
    prior_history_probability: float
    positive_excess_m: float
    dominant_body_segment: str | None
    reason: str


def _closest_segment_parameters(
    p0: np.ndarray,
    p1: np.ndarray,
    q0: np.ndarray,
    q1: np.ndarray,
) -> tuple[float, float, float]:
    """Return distance and clamped parameters of two finite 3-D segments."""

    p0 = np.asarray(p0, dtype=float).reshape(3)
    p1 = np.asarray(p1, dtype=float).reshape(3)
    q0 = np.asarray(q0, dtype=float).reshape(3)
    q1 = np.asarray(q1, dtype=float).reshape(3)
    if not all(np.isfinite(value).all() for value in (p0, p1, q0, q1)):
        raise ValueError("segment endpoints must be finite")
    u = p1 - p0
    v = q1 - q0
    w = p0 - q0
    a = float(u @ u)
    b = float(u @ v)
    c = float(v @ v)
    d = float(u @ w)
    e = float(v @ w)
    if a <= np.finfo(float).eps or c <= np.finfo(float).eps:
        raise ValueError("body and radio segments must have non-zero length")
    denominator = a * c - b * b
    s = 0.0 if denominator <= np.finfo(float).eps else (b * e - c * d) / denominator
    s = float(np.clip(s, 0.0, 1.0))
    t = float(np.clip((b * s + e) / c, 0.0, 1.0))
    s = float(np.clip((b * t - d) / a, 0.0, 1.0))
    closest_p = p0 + s * u
    closest_q = q0 + t * v
    return float(np.linalg.norm(closest_p - closest_q)), s, t


def _body_segments(
    points_world_m: Mapping[str, np.ndarray],
    geometry: DisplayProxyGeometry,
) -> tuple[tuple[str, str, np.ndarray, np.ndarray, float, float], ...]:
    required = {
        "pelvis_center", "shoulder_mid", "shoulder_left", "shoulder_right",
        "hip_left", "hip_right", "elbow_left", "elbow_right", "wrist_left",
        "wrist_right", "knee_left", "knee_right", "ankle_left", "ankle_right",
    }
    missing = required - set(points_world_m)
    if missing:
        raise ValueError(f"body points are incomplete: {sorted(missing)}")
    point = {
        name: np.asarray(points_world_m[name], dtype=float).reshape(3)
        for name in required
    }
    if any(not value.shape == (3,) or not np.isfinite(value).all() for value in point.values()):
        raise ValueError("body points must be finite 3-vectors")
    length = geometry.segment_length_m
    torso_radius = 0.30 * max(geometry.shoulder_span_m, geometry.hip_span_m)
    rows = [
        ("torso", "torso", point["pelvis_center"], point["shoulder_mid"],
         torso_radius, 0.82 * torso_radius),
    ]
    definitions = (
        ("upper_arm_left", "shoulder_left", "elbow_left", 0.18, 0.14),
        ("forearm_left", "elbow_left", "wrist_left", 0.16, 0.11),
        ("upper_arm_right", "shoulder_right", "elbow_right", 0.18, 0.14),
        ("forearm_right", "elbow_right", "wrist_right", 0.16, 0.11),
        ("thigh_left", "hip_left", "knee_left", 0.20, 0.15),
        ("shank_left", "knee_left", "ankle_left", 0.17, 0.12),
        ("thigh_right", "hip_right", "knee_right", 0.20, 0.15),
        ("shank_right", "knee_right", "ankle_right", 0.17, 0.12),
    )
    for name, start, stop, proximal_ratio, distal_ratio in definitions:
        segment_length = float(length[name])
        rows.append((
            name, "limb", point[start], point[stop],
            proximal_ratio * segment_length,
            distal_ratio * segment_length,
        ))
    return tuple(rows)


def body_occlusion_evidence(
    *,
    node: str,
    tag_position_world_m: np.ndarray,
    anchor_position_world_m: np.ndarray,
    points_world_m: Mapping[str, np.ndarray],
    geometry: DisplayProxyGeometry,
) -> BodyOcclusionEvidence:
    """Score how strongly other body parts occupy one tag--anchor ray.

    Widths are dimensionless proportions of the already-owned FK geometry;
    they are not subject measurements and are not interpreted as skin surfaces.
    The tapered Gaussian field makes centre crossings stronger than grazing
    paths without introducing a hard cylinder boundary.
    """

    if node not in NODE_LOCAL_SEGMENT:
        raise ValueError(f"unknown body node: {node}")
    tag = np.asarray(tag_position_world_m, dtype=float).reshape(3)
    anchor = np.asarray(anchor_position_world_m, dtype=float).reshape(3)
    ray_length = float(np.linalg.norm(anchor - tag))
    if not np.isfinite(tag).all() or not np.isfinite(anchor).all() or ray_length <= 0.0:
        raise ValueError("tag--anchor ray must be finite and non-zero")

    contributions: list[tuple[str, str, float, float]] = []
    local = NODE_LOCAL_SEGMENT[node]
    for name, family, start, stop, proximal, distal in _body_segments(
        points_world_m, geometry
    ):
        if name == local or (local == "pelvis" and name == "torso"):
            continue
        distance, ray_fraction, body_fraction = _closest_segment_parameters(
            tag, anchor, start, stop
        )
        radius = proximal + body_fraction * (distal - proximal)
        normalized = distance / max(radius, np.finfo(float).eps)
        # Do not treat the immediate antenna near field as another-body
        # evidence.  The ramp is smooth and reaches 95% at about 15 cm.
        distance_from_tag = ray_fraction * ray_length
        near_field_ramp = 1.0 - math.exp(-max(0.0, distance_from_tag) / 0.05)
        contribution = near_field_ramp * math.exp(-0.5 * normalized * normalized)
        contributions.append((name, family, contribution, normalized))

    if not contributions:
        return BodyOcclusionEvidence(0.0, 0.0, 0.0, None, math.inf)
    torso = 1.0 - math.prod(
        1.0 - value for _name, family, value, _clearance in contributions
        if family == "torso"
    )
    limb = 1.0 - math.prod(
        1.0 - value for _name, family, value, _clearance in contributions
        if family == "limb"
    )
    combined = 1.0 - (1.0 - torso) * (1.0 - limb)
    dominant = max(contributions, key=lambda row: row[2])
    return BodyOcclusionEvidence(
        score=float(np.clip(combined, 0.0, 1.0)),
        torso_score=float(np.clip(torso, 0.0, 1.0)),
        limb_score=float(np.clip(limb, 0.0, 1.0)),
        dominant_segment=dominant[0] if dominant[2] > 1e-6 else None,
        minimum_normalized_clearance=float(min(row[3] for row in contributions)),
    )


class OnlineLinkReliability:
    """Bounded per-link history with a causal assess-then-observe contract."""

    def __init__(
        self, config: LinkReliabilityConfig = LinkReliabilityConfig()
    ) -> None:
        config.validate()
        self.config = config
        self._history: dict[tuple[str, int], float] = {}

    @property
    def state_size(self) -> int:
        return len(self._history)

    def assess(
        self,
        *,
        node: str,
        anchor: int,
        innovation_m: float,
        sigma_m: float,
        facing_score: float,
        body: BodyOcclusionEvidence,
    ) -> LinkReliabilityDecision:
        if node not in NODE_LOCAL_SEGMENT or not 0 <= int(anchor) < 8:
            raise ValueError("link identity is outside the C2 inventory")
        values = (innovation_m, sigma_m, facing_score, body.score)
        if not all(math.isfinite(float(value)) for value in values):
            raise ValueError("link reliability inputs must be finite")
        if sigma_m <= 0.0 or not -1.0 <= facing_score <= 1.0:
            raise ValueError("invalid range uncertainty or facing score")
        key = (str(node), int(anchor))
        history = float(self._history.get(key, 0.0))
        inward = 0.5 * (1.0 - float(facing_score))
        geometry = 1.0 - (1.0 - body.score) * (1.0 - inward)
        prior = 1.0 - (1.0 - geometry) * (
            1.0 - self.config.history_gain * history
        )
        positive_excess = max(0.0, float(innovation_m) - float(sigma_m))
        ratio = positive_excess / self.config.positive_innovation_scale_m
        weight = 1.0 / (1.0 + prior * ratio * ratio)
        weight = float(np.clip(
            weight, self.config.minimum_information_weight, 1.0
        ))
        reason = (
            "NOMINAL_OR_NONPOSITIVE_INNOVATION" if positive_excess == 0.0
            else "BODY_AND_FACING_SUPPORTED_POSITIVE_NLOS"
            if body.score > 0.25 and inward > 0.25
            else "BODY_SUPPORTED_POSITIVE_NLOS" if body.score > 0.25
            else "FACING_SUPPORTED_POSITIVE_NLOS" if inward > 0.25
            else "HISTORY_OR_ROBUST_POSITIVE_NLOS"
        )
        return LinkReliabilityDecision(
            node=str(node),
            anchor=int(anchor),
            information_weight=weight,
            body_occlusion_score=body.score,
            facing_inward_probability=inward,
            prior_history_probability=history,
            positive_excess_m=positive_excess,
            dominant_body_segment=body.dominant_segment,
            reason=reason,
        )

    def observe(
        self,
        decision: LinkReliabilityDecision,
        *,
        postfit_innovation_m: float,
        sigma_m: float,
    ) -> None:
        """Commit current evidence only after its solve has completed."""

        if not math.isfinite(postfit_innovation_m) or not math.isfinite(sigma_m):
            raise ValueError("observed innovation must be finite")
        if sigma_m <= 0.0:
            raise ValueError("observed sigma must be positive")
        key = (decision.node, decision.anchor)
        evidence = float(np.clip(
            max(0.0, postfit_innovation_m - sigma_m)
            / self.config.positive_innovation_scale_m,
            0.0,
            1.0,
        ))
        previous = float(self._history.get(key, 0.0))
        alpha = self.config.history_alpha
        self._history[key] = (1.0 - alpha) * previous + alpha * evidence
        if len(self._history) > 80:
            raise RuntimeError("link reliability state exceeded fixed C2 inventory")


def annotate_links_from_prior(
    links: list[SharedRangeLink],
    *,
    anchors_m: np.ndarray,
    predicted_root_m: np.ndarray,
    root_velocity_mps: np.ndarray,
    body_points_relative_world_m: Mapping[str, np.ndarray],
    geometry: DisplayProxyGeometry,
    reliability: OnlineLinkReliability,
) -> tuple[list[SharedRangeLink], tuple[LinkReliabilityDecision, ...]]:
    """Freeze per-link weights from one causal pre-update state."""

    anchors = np.asarray(anchors_m, dtype=float)
    root = np.asarray(predicted_root_m, dtype=float).reshape(3)
    velocity = np.asarray(root_velocity_mps, dtype=float).reshape(3)
    if anchors.shape != (8, 3) or not np.isfinite(anchors).all():
        raise ValueError("anchors must be the finite canonical 8x3 layout")
    if not np.isfinite(root).all() or not np.isfinite(velocity).all():
        raise ValueError("prior root and velocity must be finite")
    world_points = {
        name: root + np.asarray(point, dtype=float).reshape(3)
        for name, point in body_points_relative_world_m.items()
    }
    annotated = []
    decisions = []
    for link in links:
        tag = (
            root
            + np.asarray(link.tag_offset_world_m, dtype=float)
            + float(link.link_dt_s) * velocity
        )
        predicted_range = float(np.linalg.norm(anchors[link.anchor] - tag))
        body = body_occlusion_evidence(
            node=link.node,
            tag_position_world_m=tag,
            anchor_position_world_m=anchors[link.anchor],
            points_world_m=world_points,
            geometry=geometry,
        )
        decision = reliability.assess(
            node=link.node,
            anchor=link.anchor,
            innovation_m=float(link.range_m) - predicted_range,
            sigma_m=link.sigma_m,
            facing_score=(
                0.0 if link.facing_score is None else link.facing_score
            ),
            body=body,
        )
        annotated.append(replace(
            link,
            information_weight=decision.information_weight,
            body_occlusion_score=decision.body_occlusion_score,
            body_occluder=decision.dominant_body_segment,
        ))
        decisions.append(decision)
    return annotated, tuple(decisions)


def observe_postfit_links(
    links: list[SharedRangeLink],
    decisions: tuple[LinkReliabilityDecision, ...],
    postfit_innovation_m: np.ndarray,
    *,
    reliability: OnlineLinkReliability,
) -> None:
    """Commit post-fit residual history after the current update is complete."""

    residual = np.asarray(postfit_innovation_m, dtype=float)
    if len(links) != len(decisions) or residual.shape != (len(links),):
        raise ValueError("post-fit reliability rows do not match their links")
    for link, decision, value in zip(links, decisions, residual):
        if (link.node, link.anchor) != (decision.node, decision.anchor):
            raise ValueError("post-fit reliability identity changed")
        reliability.observe(
            decision,
            postfit_innovation_m=float(value),
            sigma_m=float(link.sigma_m),
        )
