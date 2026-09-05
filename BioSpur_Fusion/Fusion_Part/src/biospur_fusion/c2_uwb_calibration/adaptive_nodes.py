"""Adaptive body-node trust for C2 raw-range fusion.

The owner of a range is the body node that measured it.  This module first
checks whether each node can independently place its frozen FK proxy point in
the anchor volume, then compares those node-derived roots.  It returns the
currently trusted ``x/10`` node set; it never invents a missing node or treats
packet availability as measurement trust.

One trusted node is sufficient only for a conservative world-translation
update.  Relative-pose updates remain the responsibility of the articulated
solver and require at least two trusted nodes.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Sequence

import numpy as np

from .shared_root import SharedRangeLink, solve_shared_root


@dataclass(frozen=True)
class AdaptiveNodeTrustConfig:
    minimum_links_per_node: int = 4
    maximum_root_condition: float = 1e6
    maximum_median_standardized_residual: float = 3.5
    minimum_trust_score: float = 0.25
    minimum_consensus_radius_m: float = 0.25
    maximum_consensus_radius_m: float = 0.75

    def validate(self) -> None:
        if self.minimum_links_per_node < 4:
            raise ValueError("a 3-D node fix requires at least four ranges")
        positive = (
            self.maximum_root_condition,
            self.maximum_median_standardized_residual,
            self.minimum_trust_score,
            self.minimum_consensus_radius_m,
            self.maximum_consensus_radius_m,
        )
        if not all(math.isfinite(value) and value > 0.0 for value in positive):
            raise ValueError("node-trust thresholds must be finite and positive")
        if self.minimum_trust_score > 1.0:
            raise ValueError("minimum trust score cannot exceed one")
        if self.minimum_consensus_radius_m > self.maximum_consensus_radius_m:
            raise ValueError("consensus-radius bounds are reversed")


@dataclass(frozen=True)
class NodeTrustAssessment:
    node: str
    trusted: bool
    reason: str
    score: float
    link_count: int
    root_position_m: np.ndarray
    root_condition: float
    median_abs_standardized_residual: float
    consensus_distance_m: float
    facing_confirmed_positive_nlos_fraction: float


@dataclass(frozen=True)
class AdaptiveNodeSelection:
    mode: str
    trusted_nodes: tuple[str, ...]
    trusted_links: tuple[SharedRangeLink, ...]
    assessments: tuple[NodeTrustAssessment, ...]
    consensus_root_m: np.ndarray
    consensus_radius_m: float


def propagation_mode(trusted_count: int, total_nodes: int = 10) -> str:
    if trusted_count < 0 or total_nodes < 1 or trusted_count > total_nodes:
        raise ValueError("invalid trusted-node count")
    if trusted_count == 0:
        return "NO_TRUSTED_NODE"
    if trusted_count == 1:
        return "SINGLE_NODE_ROOT_TRANSLATION"
    if trusted_count < total_nodes:
        return "PARTIAL_NODE_FK_PROPAGATION"
    return "FULL_NODE_CONSTRAINED_CONSENSUS"


def adaptive_root_minimum_std_m(
    trusted_count: int,
    *,
    total_nodes: int = 10,
    full_node_std_m: float = 0.12,
) -> float:
    """Inflate root uncertainty when fewer body nodes own the observation."""

    if trusted_count < 1 or trusted_count > total_nodes:
        raise ValueError("trusted count must be within the body inventory")
    if not math.isfinite(full_node_std_m) or full_node_std_m <= 0.0:
        raise ValueError("full-node standard deviation must be positive")
    return float(full_node_std_m * math.sqrt(total_nodes / trusted_count))


def _confirmed_nlos_fraction(
    links: Sequence[SharedRangeLink], residuals_m: np.ndarray
) -> float:
    sigma = np.asarray([link.sigma_m for link in links], dtype=float)
    facing = np.asarray([
        0.0 if link.facing_score is None else float(link.facing_score)
        for link in links
    ])
    confirmed = (residuals_m > sigma) & (facing < 0.0)
    return float(np.mean(confirmed)) if len(confirmed) else 0.0


def select_trusted_body_nodes(
    links: Sequence[SharedRangeLink],
    *,
    anchors_m: Mapping[int, np.ndarray] | np.ndarray,
    initial_root_m: np.ndarray,
    root_velocity_mps: np.ndarray | None = None,
    total_nodes: int = 10,
    config: AdaptiveNodeTrustConfig = AdaptiveNodeTrustConfig(),
) -> AdaptiveNodeSelection:
    """Select the high-confidence ``x/10`` body nodes for one UWB epoch."""

    config.validate()
    initial = np.asarray(initial_root_m, dtype=float).reshape(3)
    grouped: dict[str, list[SharedRangeLink]] = {}
    for link in links:
        grouped.setdefault(str(link.node), []).append(link)

    provisional = []
    for node, node_links in sorted(grouped.items()):
        if len(node_links) < config.minimum_links_per_node:
            provisional.append({
                "node": node,
                "links": node_links,
                "result": None,
                "reason": "FEWER_THAN_FOUR_NODE_RANGES",
                "median": math.inf,
                "nlos": 0.0,
            })
            continue
        result = solve_shared_root(
            node_links,
            anchors_m=anchors_m,
            initial_root_m=initial,
            root_velocity_mps=root_velocity_mps,
            maximum_condition=config.maximum_root_condition,
        )
        median = (
            float(np.median(np.abs(result.standardized_residuals)))
            if result.success else math.inf
        )
        nlos = (
            _confirmed_nlos_fraction(node_links, result.residuals_m)
            if result.success else 0.0
        )
        provisional.append({
            "node": node,
            "links": node_links,
            "result": result,
            "reason": result.reason,
            "median": median,
            "nlos": nlos,
        })

    successful = [
        row for row in provisional
        if row["result"] is not None and row["result"].success
    ]
    if successful:
        roots = np.stack([row["result"].root_position_m for row in successful])
        consensus = np.median(roots, axis=0)
        distances = np.linalg.norm(roots - consensus, axis=1)
        if len(distances) == 1:
            radius = math.inf
        elif len(distances) == 2:
            radius = config.maximum_consensus_radius_m
        else:
            centre = float(np.median(distances))
            mad = float(np.median(np.abs(distances - centre)))
            radius = float(np.clip(
                centre + 3.0 * 1.4826 * mad,
                config.minimum_consensus_radius_m,
                config.maximum_consensus_radius_m,
            ))
    else:
        consensus = initial.copy()
        radius = config.minimum_consensus_radius_m

    assessments = []
    trusted_nodes = []
    for row in provisional:
        result = row["result"]
        if result is None or not result.success:
            assessment = NodeTrustAssessment(
                row["node"], False, row["reason"], 0.0,
                len(row["links"]), initial.copy(), math.inf, math.inf,
                math.inf, float(row["nlos"]),
            )
        else:
            distance = float(np.linalg.norm(result.root_position_m - consensus))
            availability = min(1.0, len(row["links"]) / 8.0)
            fit = math.exp(-0.5 * (
                row["median"] / config.maximum_median_standardized_residual
            ) ** 2)
            geometry = min(1.0, (
                config.maximum_root_condition / max(result.condition, 1.0)
            ) ** 0.25)
            agreement = 1.0 if math.isinf(radius) else math.exp(
                -0.5 * (distance / radius) ** 2
            )
            nlos = 1.0 - 0.5 * float(row["nlos"])
            score = float(availability * fit * geometry * agreement * nlos)
            trusted = bool(
                row["median"] <= config.maximum_median_standardized_residual
                and distance <= radius
                and score >= config.minimum_trust_score
            )
            reason = "TRUSTED" if trusted else (
                "NODE_FIT_RESIDUAL" if row["median"]
                > config.maximum_median_standardized_residual
                else "NODE_ROOT_DISAGREEMENT" if distance > radius
                else "NODE_TRUST_SCORE"
            )
            assessment = NodeTrustAssessment(
                row["node"], trusted, reason, score, len(row["links"]),
                result.root_position_m.copy(), result.condition,
                float(row["median"]), distance, float(row["nlos"]),
            )
        assessments.append(assessment)
        if assessment.trusted:
            trusted_nodes.append(assessment.node)

    selected = tuple(
        link for link in links if str(link.node) in set(trusted_nodes)
    )
    return AdaptiveNodeSelection(
        mode=propagation_mode(len(trusted_nodes), total_nodes),
        trusted_nodes=tuple(trusted_nodes),
        trusted_links=selected,
        assessments=tuple(assessments),
        consensus_root_m=consensus.copy(),
        consensus_radius_m=float(radius),
    )
