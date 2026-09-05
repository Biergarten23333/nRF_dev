"""Robust held-node calibration for persistent C2 node--anchor range bias."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np


@dataclass(frozen=True)
class PairBiasEstimate:
    node: str
    anchor: int
    bias_m: float
    robust_sigma_m: float
    sample_count: int


@dataclass(frozen=True)
class PairBiasUse:
    fixed_correction_m: float
    additional_sigma_m: float
    initial_pose_nlos_state: bool
    fixed_bias_limit_m: float


def estimate_pair_bias(
    node: str,
    anchor: int,
    residuals_m: Iterable[float],
    *,
    minimum_samples: int = 30,
) -> PairBiasEstimate:
    """Estimate one fixed bias from residuals produced without that node.

    The location is the median and the dispersion is scaled MAD.  There is no
    iterative refit, clipping toward a desired answer, or LOS claim.
    """

    values = np.asarray(tuple(residuals_m), dtype=float)
    if values.ndim != 1 or len(values) < minimum_samples:
        raise ValueError("pair bias has insufficient held-node residual support")
    if not np.all(np.isfinite(values)):
        raise ValueError("pair bias residuals must be finite")
    median = float(np.median(values))
    robust_sigma = float(1.4826 * np.median(np.abs(values - median)))
    return PairBiasEstimate(
        node=str(node),
        anchor=int(anchor),
        bias_m=median,
        robust_sigma_m=robust_sigma,
        sample_count=int(len(values)),
    )


def separate_fixed_bias_from_initial_pose_nlos(
    estimate: PairBiasEstimate,
    *,
    layout_sigma_m: float,
    absolute_floor_m: float = 0.20,
    maximum_nlos_sigma_m: float = 1.0,
) -> PairBiasUse:
    """Prevent a large pose-specific offset from becoming fixed correction.

    Corrected V4 anchor delays leave no physical basis for treating metre-scale
    offsets as constant hardware bias.  Estimates outside three layout sigmas
    (with a 0.20 m engineering floor) are retained as initial-pose NLOS
    evidence: no offset is subtracted and their magnitude inflates uncertainty.
    """

    if not np.isfinite(layout_sigma_m) or layout_sigma_m <= 0.0:
        raise ValueError("layout sigma must be finite and positive")
    if absolute_floor_m <= 0.0 or maximum_nlos_sigma_m <= 0.0:
        raise ValueError("bias separation limits must be positive")
    limit = max(3.0 * float(layout_sigma_m), float(absolute_floor_m))
    pose_nlos = abs(estimate.bias_m) > limit
    if pose_nlos:
        additional = float(np.hypot(
            estimate.robust_sigma_m,
            min(abs(estimate.bias_m), maximum_nlos_sigma_m),
        ))
        correction = 0.0
    else:
        additional = float(estimate.robust_sigma_m)
        correction = float(estimate.bias_m)
    return PairBiasUse(
        fixed_correction_m=correction,
        additional_sigma_m=additional,
        initial_pose_nlos_state=pose_nlos,
        fixed_bias_limit_m=limit,
    )


def validate_complete_pair_bias_table(
    table: Mapping[tuple[str, int], PairBiasEstimate],
    *,
    nodes: Iterable[str],
    anchor_count: int = 8,
) -> None:
    """Reject incomplete or internally inconsistent bias ownership."""

    expected = {(str(node), anchor) for node in nodes for anchor in range(anchor_count)}
    if set(table) != expected:
        missing = sorted(expected - set(table))
        extra = sorted(set(table) - expected)
        raise ValueError(f"pair bias table key mismatch; missing={missing}, extra={extra}")
    for key, estimate in table.items():
        if key != (estimate.node, estimate.anchor):
            raise ValueError("pair bias estimate identity mismatch")
        if (
            not np.isfinite(estimate.bias_m)
            or not np.isfinite(estimate.robust_sigma_m)
            or estimate.robust_sigma_m < 0.0
            or estimate.sample_count <= 0
        ):
            raise ValueError("pair bias estimate is invalid")


def aggregate_causal_pair_bias_tables(
    history: Sequence[Mapping[tuple[str, int], PairBiasEstimate]],
    *,
    nodes: Iterable[str],
    anchor_count: int = 8,
) -> dict[tuple[str, int], PairBiasEstimate]:
    """Combine held-node estimates from completed earlier episodes.

    Episode medians receive equal weight, preventing a longer action from
    silently owning the result. Between-episode variation is added to the
    within-episode robust dispersion and therefore can only reduce confidence.
    The caller must append the current episode after its prequential score.
    """

    if not history:
        raise ValueError("causal pair-bias history is empty")
    node_names = tuple(str(node) for node in nodes)
    for table in history:
        validate_complete_pair_bias_table(
            table, nodes=node_names, anchor_count=anchor_count
        )
    output: dict[tuple[str, int], PairBiasEstimate] = {}
    for node in node_names:
        for anchor in range(anchor_count):
            estimates = [table[(node, anchor)] for table in history]
            centres = np.asarray([item.bias_m for item in estimates], dtype=float)
            within = np.asarray(
                [item.robust_sigma_m for item in estimates], dtype=float
            )
            centre = float(np.median(centres))
            between = float(1.4826 * np.median(np.abs(centres - centre)))
            output[(node, anchor)] = PairBiasEstimate(
                node=node,
                anchor=anchor,
                bias_m=centre,
                robust_sigma_m=float(np.hypot(np.median(within), between)),
                sample_count=sum(item.sample_count for item in estimates),
            )
    validate_complete_pair_bias_table(
        output, nodes=node_names, anchor_count=anchor_count
    )
    return output


def propagate_causal_pair_uncertainty(
    fixed_bias: Mapping[tuple[str, int], PairBiasEstimate],
    history: Sequence[Mapping[tuple[str, int], PairBiasEstimate]],
    *,
    nodes: Iterable[str],
    anchor_count: int = 8,
) -> dict[tuple[str, int], PairBiasEstimate]:
    """Keep the 00 bias fixed while propagating cross-episode instability.

    Dynamic pose residuals are not allowed to become hardware corrections.
    Their robust episode-to-episode departure from the fixed calibration can
    only inflate that pair's uncertainty. This provides causal link-health
    propagation without relabelling body shadow or proxy error as fixed bias.
    """

    node_names = tuple(str(node) for node in nodes)
    validate_complete_pair_bias_table(
        fixed_bias, nodes=node_names, anchor_count=anchor_count
    )
    if not history:
        raise ValueError("causal uncertainty history is empty")
    for table in history:
        validate_complete_pair_bias_table(
            table, nodes=node_names, anchor_count=anchor_count
        )
    output: dict[tuple[str, int], PairBiasEstimate] = {}
    for node in node_names:
        for anchor in range(anchor_count):
            key = (node, anchor)
            owner = fixed_bias[key]
            departures = np.asarray(
                [table[key].bias_m - owner.bias_m for table in history],
                dtype=float,
            )
            dynamic_sigma = float(1.4826 * np.median(np.abs(departures)))
            output[key] = PairBiasEstimate(
                node=node,
                anchor=anchor,
                bias_m=owner.bias_m,
                robust_sigma_m=float(np.hypot(
                    owner.robust_sigma_m, dynamic_sigma
                )),
                sample_count=owner.sample_count + sum(
                    table[key].sample_count for table in history[1:]
                ),
            )
    validate_complete_pair_bias_table(
        output, nodes=node_names, anchor_count=anchor_count
    )
    return output


def load_pair_bias_table(
    path: Path,
    *,
    nodes: Iterable[str],
    anchor_count: int = 8,
    allow_shadow: bool = False,
) -> dict[tuple[str, int], PairBiasEstimate]:
    """Load and strictly validate one sealed pair-bias JSON artifact."""

    document = json.loads(Path(path).read_text())
    if document.get("schema") != "biospur-c2-held-node-pair-bias-v1":
        raise ValueError("unsupported pair bias table schema")
    contract = (document.get("source_episode"), document.get("method"))
    supported = {
        (
            "00_initial_still",
            "single_median_of_residuals_from_other_nine_node_root",
        ),
        (
            "00_to_19_prequential_after_scoring",
            "fixed_00_bias_plus_causal_cross_episode_uncertainty",
        ),
    }
    if contract not in supported:
        raise ValueError("unsupported pair bias ownership/method contract")
    if contract[0] == "00_to_19_prequential_after_scoring":
        if document.get("shadow_only") is not True:
            raise ValueError("prequential table must remain explicitly shadow-only")
        if not allow_shadow:
            raise ValueError("shadow pair uncertainty table is not production-loadable")
    estimates = {}
    for row in document.get("estimates", []):
        estimate = PairBiasEstimate(
            node=str(row["node"]),
            anchor=int(row["anchor"]),
            bias_m=float(row["bias_m"]),
            robust_sigma_m=float(row["robust_sigma_m"]),
            sample_count=int(row["sample_count"]),
        )
        key = (estimate.node, estimate.anchor)
        if key in estimates:
            raise ValueError(f"duplicate pair bias estimate: {key}")
        estimates[key] = estimate
    validate_complete_pair_bias_table(
        estimates, nodes=nodes, anchor_count=anchor_count
    )
    return estimates
