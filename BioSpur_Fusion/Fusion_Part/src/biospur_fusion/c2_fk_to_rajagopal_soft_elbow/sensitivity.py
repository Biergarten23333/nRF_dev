"""Bounded synthetic-only relative-weight sensitivity for the soft elbow.

The frozen C2 FK interface has no per-frame SO(3) covariance, and no physical
variance owner exists for the added diagnostic elbow coordinate.  This module
therefore does not claim a covariance-derived weight.  It runs one fixed,
predeclared engineering sensitivity on the already generated 28-row official
model fixture and selects the largest non-zero coordinate-reference weight
that satisfies every existing fixture gate.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_fk_to_scaled_opensense.pipeline import (
    BODY_BY_SEGMENT,
    sha256_file,
)

from .candidate import (
    CANDIDATE_FRAME_BY_SEGMENT,
    _assess_fixture,
    _generated_fixture_episode,
    _prepare_solver_inputs,
    _run_solver,
)


WEIGHT_RATIOS = (0.0, 0.01, 0.03, 0.1, 0.3, 1.0)
PROFILE_WALL_LIMIT_S = 12.0
AGGREGATE_WALL_LIMIT_S = 60.0
ANCHOR_MODEL_SHA256 = "c1543be4896efe9048239b9c904034613091ac82f4c024b70bbbf0f48e6dbeb6"
ANCHOR_INPUT_SHA256 = "f4c5cab36fc59890bf1f234867ae628e843e4913014750e9a1471a6c71c279c0"
OPEN_SIM_COMMIT = "85aaf6450a2f22457dac4d1ab35adfed9d3a8e43"
OPEN_SIM_IK_CPP_BLOB = "bb6e8080b741f50b59d1b66c568df883e03a1b37"
OPEN_SIM_ASSEMBLY_CPP_BLOB = "d44d70d34c1f3d325aef98c15f3e8e54c3aa6dd5"


def _profile_name(weight: float) -> str:
    return f"weight_{weight:.0e}".replace("+", "")


def _profile_summary(result: dict[str, object]) -> dict[str, object]:
    rows = result["rows"]
    target_response = [
        abs(float(row["solved_delta_rad"][column]))
        for row in rows
        for column, targeted in enumerate(row["target_mask"])
        if targeted
    ]
    oop_values = np.asarray(
        [float(row["solved_delta_rad"][2]) for row in rows], dtype=float
    )
    weight = float(result["out_of_plane_weight"])
    errors = result["orientation_errors"]
    return {
        "weight_ratio_coordinate_to_orientation": weight,
        "passed_unchanged_fixture_gate": bool(result["passed"]),
        "minimum_target_response_rad": float(min(target_response)),
        "maximum_off_target_crosstalk_rad": float(
            result["max_off_target_crosstalk_rad"]
        ),
        "minimum_range_margin_rad": float(result["minimum_range_margin_rad"]),
        "orientation_error_mean_rad": float(errors["overall_mean_rad"]),
        "orientation_error_p95_rad": float(errors["overall_p95_rad"]),
        "orientation_error_max_rad": float(errors["overall_max_rad"]),
        "oop_coordinate_rms_rad": float(np.sqrt(np.mean(oop_values**2))),
        "weighted_oop_zero_reference_sum_square": float(weight * np.sum(oop_values**2)),
        "profile_wall_s": float(result["wall_s"]),
    }


def run_weight_sensitivity(
    model_path: Path,
    input_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    """Run the one authorized five-profile synthetic-only sensitivity."""
    started = time.monotonic()
    if sha256_file(model_path) != ANCHOR_MODEL_SHA256:
        raise RuntimeError("fixture anchor model hash changed")
    if sha256_file(input_path) != ANCHOR_INPUT_SHA256:
        raise RuntimeError("fixture anchor orientation-table hash changed")

    # The model and converted orientation table are intentionally loaded once.
    # Every profile then consumes those same immutable OpenSim objects.
    episode, specifications = _generated_fixture_episode(model_path)
    prepared = _prepare_solver_inputs(model_path, input_path)
    if len(specifications) != 28 or len(prepared.times) != 28:
        raise RuntimeError("fixed 28-row fixture identity changed")
    input_manifest = {
        "episode": episode.key,
        "rows": 28,
        "labels": [CANDIDATE_FRAME_BY_SEGMENT[segment] for segment in BODY_BY_SEGMENT],
        "path": str(input_path.resolve()),
        "sha256": ANCHOR_INPUT_SHA256,
        "cached_once_for_all_profiles": True,
    }

    profiles = []
    for index, weight in enumerate(WEIGHT_RATIOS):
        elapsed = time.monotonic() - started
        if profiles:
            mean_wall = float(np.mean([item["profile_wall_s"] for item in profiles]))
            projected = elapsed + mean_wall * (len(WEIGHT_RATIOS) - index)
            if projected >= AGGREGATE_WALL_LIMIT_S:
                raise RuntimeError(
                    f"projected aggregate wall {projected:.6f}s exceeds 60s"
                )
        profile_started = time.monotonic()
        profile_dir = output_dir / "profiles" / _profile_name(weight)
        official = _run_solver(
            model_path,
            input_path,
            profile_dir,
            independent_rows=True,
            out_of_plane_weight=weight,
            prepared_inputs=prepared,
        )
        result = _assess_fixture(
            model_path,
            profile_dir,
            specifications,
            input_manifest,
            official,
            out_of_plane_weight=weight,
            coordinate_ranges=prepared.coordinate_ranges,
        )
        result["wall_s"] = time.monotonic() - profile_started
        (profile_dir / "FIXTURE_RESULT.json").write_text(
            json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        summary = _profile_summary(result)
        profiles.append(summary)
        if summary["profile_wall_s"] >= PROFILE_WALL_LIMIT_S:
            raise RuntimeError(
                f"profile {weight:g} wall {summary['profile_wall_s']:.6f}s exceeds 12s"
            )
        if time.monotonic() - started >= AGGREGATE_WALL_LIMIT_S:
            raise RuntimeError("aggregate sensitivity wall exceeds 60s")

    passing = [
        float(item["weight_ratio_coordinate_to_orientation"])
        for item in profiles
        if item["passed_unchanged_fixture_gate"]
        and float(item["weight_ratio_coordinate_to_orientation"]) > 0.0
    ]
    selected = max(passing) if passing else None
    result = {
        "schema": "biospur.c2.rajagopal_soft_elbow.weight_sensitivity.v1",
        "passed": selected is not None,
        "scientific_pass": False,
        "weight_ratios_predeclared": list(WEIGHT_RATIOS),
        "selection_rule_predeclared": (
            "largest non-zero coordinate-to-orientation weight ratio satisfying "
            "every unchanged 28-row fixture gate"
        ),
        "selected_engineering_weight_ratio": selected,
        "no_real_episode_read_or_selection": True,
        "orientation_covariance_owner": "absent in frozen C2/3A interface",
        "oop_variance_owner": "absent; sensitivity remains engineering-only",
        "zero_weight_role": "no-prior diagnostic comparator only; never selectable",
        "weight_interpretation": (
            "official InverseKinematicsSolver passes CoordinateReference weights "
            "as assembly Q-value goal weights and orientation weights as the "
            "orientation assembly-goal weights; only their relative engineering "
            "tradeoff is studied here"
        ),
        "official_source": {
            "revision": OPEN_SIM_COMMIT,
            "inverse_kinematics_solver_cpp_git_blob": OPEN_SIM_IK_CPP_BLOB,
            "assembly_solver_cpp_git_blob": OPEN_SIM_ASSEMBLY_CPP_BLOB,
            "license": "Apache-2.0",
        },
        "cache": {
            "model_loaded_once": True,
            "orientation_table_loaded_and_converted_once": True,
            "model_sha256": ANCHOR_MODEL_SHA256,
            "orientation_table_sha256": ANCHOR_INPUT_SHA256,
        },
        "unchanged_fixture_gate": (
            "finite errors; every target signed and >=0.02 rad; off-target "
            "crosstalk <=0.02 rad; range margin >=0.05 rad"
        ),
        "profiles": profiles,
        "profile_wall_limit_s": PROFILE_WALL_LIMIT_S,
        "aggregate_wall_limit_s": AGGREGATE_WALL_LIMIT_S,
        "aggregate_wall_s": time.monotonic() - started,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "WEIGHT_SENSITIVITY_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
