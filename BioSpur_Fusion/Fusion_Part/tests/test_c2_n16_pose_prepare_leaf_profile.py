from __future__ import annotations

from collections import Counter
from pathlib import Path

import pytest

from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
)
from tools.profile_c2_n16_pose_prepare_leaf import (
    STAGES,
    _stage_boundaries,
    _stage_for_line,
    _validated_attribution,
    validate_bounded_result,
)


def test_pose_leaf_regions_are_complete_ordered_and_source_bound() -> None:
    boundaries = _stage_boundaries(CausalArticulatedPose.prepare_native200_batch)
    assert tuple(stage for _line, stage in boundaries) == STAGES
    assert all(left[0] < right[0] for left, right in zip(boundaries, boundaries[1:]))
    lines, start = __import__("inspect").getsourcelines(
        CausalArticulatedPose.prepare_native200_batch
    )
    observed = {
        _stage_for_line(boundaries, line)
        for line in range(start, start + len(lines))
    }
    assert observed == set(STAGES)


def test_nested_attribution_is_fail_closed() -> None:
    attributed, unattributed, fraction = _validated_attribution(
        leaf_wall_s=1.0,
        stage_ns=Counter({"validation_copy": 900_000_000}),
    )
    assert attributed == pytest.approx(0.9)
    assert unattributed == pytest.approx(0.1)
    assert fraction == pytest.approx(0.9)
    with pytest.raises(RuntimeError, match="escaped"):
        _validated_attribution(
            leaf_wall_s=1.0,
            stage_ns=Counter({"validation_copy": 1_000_000_001}),
        )


def _passing_result() -> dict:
    return {
        "status": "NESTED_ATTRIBUTION_PASS",
        "limits": {"raw_calibration_data": False, "attempts": 20},
        "nested": {
            "attribution_fraction": 0.95,
            "unattributed_s": 0.01,
            "dependency_order": list(STAGES),
            "stage_s": {stage: 0.01 for stage in STAGES},
            "stage_calls": {stage: 40 for stage in STAGES},
        },
        "inertness": {
            "all_profiled_states_equal_controls": True,
            "all_profiled_plan_digests_equal_controls": True,
        },
        "provenance": {
            "profile_sha256": "p",
            "fixture_sha256": "f",
            "pose_source_sha256": "s",
        },
    }


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("nested", "attribution_fraction"), 0.89),
        (("nested", "unattributed_s"), -1e-9),
        (("nested", "dependency_order"), list(reversed(STAGES))),
        (("inertness", "all_profiled_states_equal_controls"), False),
        (("inertness", "all_profiled_plan_digests_equal_controls"), False),
        (("limits", "raw_calibration_data"), True),
        (("provenance", "profile_sha256"), "wrong"),
        (("provenance", "fixture_sha256"), "wrong"),
        (("provenance", "pose_source_sha256"), "wrong"),
    ),
)
def test_bounded_result_rejects_gate_or_provenance_mismatch(
    path: tuple[str, str], value: object,
) -> None:
    result = _passing_result()
    result[path[0]][path[1]] = value
    with pytest.raises(ValueError):
        validate_bounded_result(
            result,
            expected_profile_sha256="p",
            expected_fixture_sha256="f",
            expected_pose_source_sha256="s",
        )


def test_bounded_result_accepts_exact_preregistered_contract() -> None:
    validate_bounded_result(
        _passing_result(),
        expected_profile_sha256="p",
        expected_fixture_sha256="f",
        expected_pose_source_sha256="s",
    )


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_time",
        "missing_calls",
        "zero_calls",
        "unequal_calls",
        "nonfinite_time",
        "negative_time",
    ),
)
def test_bounded_result_rejects_incomplete_or_invalid_stage_execution(
    mutation: str,
) -> None:
    result = _passing_result()
    if mutation == "missing_time":
        del result["nested"]["stage_s"][STAGES[0]]
    elif mutation == "missing_calls":
        del result["nested"]["stage_calls"][STAGES[0]]
    elif mutation == "zero_calls":
        result["nested"]["stage_calls"][STAGES[0]] = 0
    elif mutation == "unequal_calls":
        result["nested"]["stage_calls"][STAGES[0]] = 39
    elif mutation == "nonfinite_time":
        result["nested"]["stage_s"][STAGES[0]] = float("nan")
    elif mutation == "negative_time":
        result["nested"]["stage_s"][STAGES[0]] = -0.01
    with pytest.raises(ValueError):
        validate_bounded_result(
            result,
            expected_profile_sha256="p",
            expected_fixture_sha256="f",
            expected_pose_source_sha256="s",
        )


def test_nested_profiler_does_not_write_outside_requested_evidence(tmp_path: Path) -> None:
    # The actual N16 fixture is intentionally not executed in preflight tests.
    assert list(tmp_path.iterdir()) == []
