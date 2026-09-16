from __future__ import annotations

from dataclasses import replace
import inspect
from pathlib import Path

import numpy as np
import pytest

import biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab as owner
from biospur_fusion.c2_uwb_calibration.direct_body_shadow_ab import DirectShadowPolicy
from biospur_fusion.c2_uwb_calibration.shared_root import SharedRootResult, solve_shared_root
import qualify_c2_direct_body_shadow_ab_cap75 as qualification


def _result() -> SharedRootResult:
    return SharedRootResult(
        root_position_m=np.array([1.0, 2.0, 3.0]), success=True,
        reason="ACCEPTED", residuals_m=np.array([0.1]),
        standardized_residuals=np.array([1.0]), anchors_used=(1,),
        nodes_used=("N",), rank=3, condition=2.0, nfev=4, cost=0.5,
    )


def test_cap_owner_is_direct_only_and_shared_default_remains_50() -> None:
    assert DirectShadowPolicy().maximum_nfev == 75
    assert inspect.signature(solve_shared_root).parameters["maximum_nfev"].default == 50
    source = inspect.getsource(owner.solve_direct_ab)
    assert "maximum_nfev=int(policy.maximum_nfev)" in source


def test_pair_timer_passes_one_identical_cap_to_exactly_two_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def fake_solve(links, **kwargs):
        calls.append(kwargs)
        return _result()

    monkeypatch.setattr(owner, "solve_shared_root", fake_solve)
    monkeypatch.setattr(owner, "prepare_direct_ab_links", lambda *a, **k: type(
        "Prepared", (), {"a_links": (), "b_links": ()}
    )())
    result, _elapsed, timings, _prepare = qualification._timed_pair(
        (), evidence_by_identity={}, anchors_m=np.zeros((8, 3)),
        initial_root_m=np.zeros(3), root_velocity_mps=np.zeros(3),
        policy=DirectShadowPolicy(),
    )
    assert result.a_result.success and result.b_result.success
    assert len(calls) == len(timings) == 2
    assert calls[0] == calls[1]
    assert calls[0]["maximum_nfev"] == 75


def test_bitwise_result_comparison_covers_every_field() -> None:
    expected = _result()
    qualification._assert_result_bitwise_equal(expected, replace(expected))
    with pytest.raises(RuntimeError, match="cost"):
        qualification._assert_result_bitwise_equal(
            expected, replace(expected, cost=np.nextafter(expected.cost, np.inf))
        )
    changed = expected.root_position_m.copy()
    changed[0] = np.nextafter(changed[0], np.inf)
    with pytest.raises(RuntimeError, match="root_position_m"):
        qualification._assert_result_bitwise_equal(
            expected, replace(expected, root_position_m=changed)
        )


def test_qualification_scope_and_resource_contract_are_fixed() -> None:
    assert qualification.EXPECTED_PREFIX_ROWS == 5_352
    assert qualification.TARGET == ("06_elbow_left", 36, "BSF44AD")
    assert qualification.SERVICE_INTERVAL_MS == pytest.approx(12.004801920768307)
    assert qualification.FEATURE_P99_GATE_MS == 5.0
    assert qualification.HARD_WALL_S == 300.0
    assert qualification.DISK_CAP_BYTES == 50_000_000
    assert qualification.RSS_CAP_KB == 1_500_000
    source = Path(qualification.__file__).read_text()
    assert "100" not in source.split("policy50 =", 1)[1].split("policy75 =", 1)[0]
    assert "maximum_nfev=150" not in source
    assert "H01" not in source and "H02" not in source
