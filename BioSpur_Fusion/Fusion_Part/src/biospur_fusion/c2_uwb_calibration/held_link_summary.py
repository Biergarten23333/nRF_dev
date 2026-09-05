"""Fail-closed summary of Capture2 leave-one-anchor diagnostics."""
from __future__ import annotations

from collections import defaultdict
from statistics import median
from typing import Any, Mapping


EXPECTED_NODES = 10
EXPECTED_ANCHORS = 8


def summarize_held_links(document: Mapping[str, Any]) -> dict[str, Any]:
    """Summarize computation without promoting residuals to physical truth."""

    links = document.get("links", {})
    if len(links) != EXPECTED_NODES * EXPECTED_ANCHORS:
        raise ValueError("held-link table is not the complete 10 x 8 grid")
    by_node: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_anchor: dict[int, list[dict[str, Any]]] = defaultdict(list)
    rows = []
    for key, value in sorted(links.items()):
        try:
            node, anchor_text = key.split("/anchor_")
            anchor = int(anchor_text)
        except (ValueError, AttributeError) as error:
            raise ValueError(f"invalid held-link key: {key}") from error
        if not 0 <= anchor < EXPECTED_ANCHORS or value.get("available") is not True:
            raise ValueError(f"unavailable or invalid held-link result: {key}")
        row = {
            "node": node,
            "anchor": anchor,
            "positive_bias_enabled": bool(value["positive_bias_enabled"]),
            "bias_m": float(value["bias_m"]),
            "sigma_bias_m": float(value["sigma_bias_m"]),
            "sigma_history_m": float(value["sigma_history_m"]),
            "new_median_abs_m": float(value["paired_new_median_abs_m"]),
            "t4_median_abs_m": float(value["paired_t4_median_abs_m"]),
            "paired_count": int(value["paired_count"]),
        }
        rows.append(row)
        by_node[node].append(row)
        by_anchor[anchor].append(row)
    if len(by_node) != EXPECTED_NODES or any(
        len(values) != EXPECTED_ANCHORS for values in by_node.values()
    ):
        raise ValueError("held-link node grid is incomplete")
    if set(by_anchor) != set(range(EXPECTED_ANCHORS)) or any(
        len(values) != EXPECTED_NODES for values in by_anchor.values()
    ):
        raise ValueError("held-link anchor grid is incomplete")

    def group(values: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "links": len(values),
            "positive_bias_links": sum(
                row["positive_bias_enabled"] for row in values
            ),
            "median_bias_m": median(row["bias_m"] for row in values),
            "median_history_sigma_m": median(
                row["sigma_history_m"] for row in values
            ),
            "median_new_abs_residual_m": median(
                row["new_median_abs_m"] for row in values
            ),
            "median_t4_abs_residual_m": median(
                row["t4_median_abs_m"] for row in values
            ),
            "new_better_links": sum(
                row["new_median_abs_m"] < row["t4_median_abs_m"]
                for row in values
            ),
        }

    overall = group(rows)
    by_anchor_summary = {
        chr(ord("A") + anchor): group(values)
        for anchor, values in sorted(by_anchor.items())
    }
    systematic_anchor_candidates = [
        anchor for anchor, value in by_anchor_summary.items()
        if value["positive_bias_links"] >= 8
    ]
    comparator_majority = overall["new_better_links"] > len(rows) / 2
    comparator_median = (
        overall["median_new_abs_residual_m"]
        < overall["median_t4_abs_residual_m"]
    )
    return {
        "schema": "biospur.c2.uwb.held_link_diagnostic_summary.v1",
        "status": "DIAGNOSTIC_COMPLETE_INDEPENDENT_NODE_SOLVER_NOT_PROMOTED",
        "scientific_pass": False,
        "links": len(rows),
        "overall": overall,
        "by_node": {
            node: group(values) for node, values in sorted(by_node.items())
        },
        "by_anchor": by_anchor_summary,
        "systematic_anchor_candidates": systematic_anchor_candidates,
        "diagnostic_comparator": {
            "new_better_on_majority": comparator_majority,
            "new_lower_overall_median": comparator_median,
            "promotion_allowed": comparator_majority and comparator_median,
        },
        "interpretation_boundary": (
            "LEAVE_ONE_ANCHOR_RESIDUAL_COMBINES_HELD_LINK_ERROR_WITH_ERRORS_"
            "FROM_THE_OTHER_SEVEN_LINKS_AND_IS_NOT_A_DIRECT_NLOS_LABEL"
        ),
        "next_model": (
            "ONE_SHARED_BODY_ROOT_POSTERIOR_WITH_SEPARATE_ANCHOR_COMMON_AND_"
            "NODE_ANCHOR_NONNEGATIVE_NLOS_TERMS"
        ),
    }
