"""Causal session-relative node offsets learned only from initial stillness.

These offsets absorb all stable node-specific disagreement visible in the
opening stationary interval.  They are deliberately not labelled antenna
phase-centre calibration or per-anchor range bias because Action00 alone
cannot identify those physical causes separately.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Mapping, Sequence

import numpy as np


def _vector(value: object) -> np.ndarray:
    result = np.asarray(value, dtype=float).reshape(3).copy()
    if not np.isfinite(result).all():
        raise ValueError("node root candidate must be a finite three-vector")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class StaticSessionOffsetProfile:
    training_start_s: float
    training_stop_s: float
    common_root_gauge_m: np.ndarray
    offsets_by_node_m: Mapping[str, np.ndarray]
    samples_by_node: Mapping[str, int]
    minimum_samples: int
    provenance: str
    digest: str = ""

    def __post_init__(self) -> None:
        if not self.training_start_s < self.training_stop_s:
            raise ValueError("static-offset training interval is empty")
        if self.minimum_samples < 2 or not self.provenance:
            raise ValueError("static-offset profile lacks fixed support/provenance")
        gauge = _vector(self.common_root_gauge_m)
        offsets = {str(node): _vector(value) for node, value in self.offsets_by_node_m.items()}
        counts = {str(node): int(value) for node, value in self.samples_by_node.items()}
        if set(offsets) != set(counts) or any(
            count < self.minimum_samples for count in counts.values()
        ):
            raise ValueError("static-offset node support is incomplete")
        payload = {
            "training_start_s": self.training_start_s,
            "training_stop_s": self.training_stop_s,
            "common_root_gauge_m": gauge.tolist(),
            "offsets_by_node_m": {node: value.tolist() for node, value in sorted(offsets.items())},
            "samples_by_node": dict(sorted(counts.items())),
            "minimum_samples": self.minimum_samples,
            "provenance": self.provenance,
            "scientific_pass": False,
        }
        expected = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode()).hexdigest()
        if self.digest and self.digest != expected:
            raise ValueError("static-offset profile digest mismatch")
        object.__setattr__(self, "common_root_gauge_m", gauge)
        object.__setattr__(self, "offsets_by_node_m", MappingProxyType(offsets))
        object.__setattr__(self, "samples_by_node", MappingProxyType(counts))
        object.__setattr__(self, "digest", expected)

    def correct(self, node: str, candidate_root_m: object) -> np.ndarray:
        corrected = _vector(candidate_root_m) - self.offsets_by_node_m[node]
        corrected.setflags(write=False)
        return corrected


@dataclass(frozen=True)
class CorrectedNodeConsensus:
    root_position_m: np.ndarray
    trusted_nodes: tuple[str, ...]
    rejected_nodes: tuple[str, ...]
    corrected_positions_m: Mapping[str, np.ndarray]
    distances_m: Mapping[str, float]
    cutoff_m: float
    robust_scale_m: float

    def __post_init__(self) -> None:
        root = _vector(self.root_position_m)
        positions = {
            str(node): _vector(value)
            for node, value in self.corrected_positions_m.items()
        }
        distances = {str(node): float(value) for node, value in self.distances_m.items()}
        if (
            set(positions) != set(distances)
            or set(self.trusted_nodes) | set(self.rejected_nodes) != set(positions)
            or set(self.trusted_nodes) & set(self.rejected_nodes)
            or any(not np.isfinite(value) or value < 0.0 for value in distances.values())
            or not np.isfinite(self.cutoff_m) or self.cutoff_m <= 0.0
            or not np.isfinite(self.robust_scale_m) or self.robust_scale_m < 0.0
        ):
            raise ValueError("invalid corrected node consensus")
        object.__setattr__(self, "root_position_m", root)
        object.__setattr__(self, "corrected_positions_m", MappingProxyType(positions))
        object.__setattr__(self, "distances_m", MappingProxyType(distances))


def fit_static_session_offsets(
    samples: Sequence[tuple[float, str, object]],
    *,
    training_start_s: float,
    training_stop_s: float,
    minimum_samples: int = 30,
    provenance: str,
) -> StaticSessionOffsetProfile:
    """Fit per-node robust translations from one preregistered time prefix."""

    by_node: dict[str, list[np.ndarray]] = {}
    for time_s, node, candidate in samples:
        if training_start_s <= float(time_s) < training_stop_s:
            by_node.setdefault(str(node), []).append(_vector(candidate))
    medians = {
        node: np.median(np.stack(values), axis=0)
        for node, values in by_node.items() if len(values) >= minimum_samples
    }
    if len(medians) < 4:
        raise ValueError("fewer than four nodes support static session offsets")
    gauge = np.median(np.stack(list(medians.values())), axis=0)
    offsets = {node: median - gauge for node, median in medians.items()}
    return StaticSessionOffsetProfile(
        float(training_start_s), float(training_stop_s), gauge, offsets,
        {node: len(by_node[node]) for node in offsets}, int(minimum_samples),
        provenance,
    )


def corrected_epoch_consensus(
    profile: StaticSessionOffsetProfile,
    candidates_by_node: Mapping[str, object],
    *,
    minimum_nodes: int = 4,
) -> tuple[np.ndarray, Mapping[str, np.ndarray]]:
    corrected = {
        node: profile.correct(node, candidate)
        for node, candidate in candidates_by_node.items()
        if node in profile.offsets_by_node_m
    }
    if len(corrected) < minimum_nodes:
        raise ValueError("insufficient calibrated nodes for epoch consensus")
    centre = np.median(np.stack(list(corrected.values())), axis=0)
    centre.setflags(write=False)
    return centre, MappingProxyType(corrected)


def robust_corrected_epoch_consensus(
    profile: StaticSessionOffsetProfile,
    candidates_by_node: Mapping[str, object],
    *,
    minimum_nodes: int = 4,
    minimum_cutoff_m: float = 0.12,
    mad_multiplier: float = 3.0,
) -> CorrectedNodeConsensus:
    """Select a causal corrected-node consensus without forcing four nodes."""

    centre, corrected = corrected_epoch_consensus(
        profile, candidates_by_node, minimum_nodes=minimum_nodes,
    )
    distances = {
        node: float(np.linalg.norm(value - centre))
        for node, value in corrected.items()
    }
    values = np.asarray(list(distances.values()), dtype=float)
    median = float(np.median(values))
    scale = float(1.4826 * np.median(np.abs(values - median)))
    cutoff = max(float(minimum_cutoff_m), median + float(mad_multiplier) * scale)
    trusted = tuple(sorted(node for node, value in distances.items() if value <= cutoff))
    rejected = tuple(sorted(set(corrected) - set(trusted)))
    if len(trusted) < minimum_nodes:
        raise ValueError("robust node consensus has fewer than four trusted nodes")
    root = np.median(np.stack([corrected[node] for node in trusted]), axis=0)
    return CorrectedNodeConsensus(
        root, trusted, rejected, corrected, distances, cutoff, scale,
    )
