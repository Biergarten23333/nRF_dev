from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pytest

from tools.profile_c2_n16_record_path import (
    _ExclusiveTimer,
    _validated_attribution,
    validate_bounded_result,
)


class _Clock:
    def __init__(self, values):
        self._values = iter(values)

    def __call__(self):
        return next(self._values)


class _Owner:
    def work(self):
        return "work"

    def snapshot(self):
        return "fingerprint"


def _passing_result():
    return {
        "status": "ATTRIBUTION_PASS",
        "control": {"p95_s": 0.072},
        "profiled": {"attribution_fraction": 0.90, "unattributed_s": 0.01},
        "inertness": {"all_profiled_states_equal_controls": True},
        "limits": {"raw_calibration_data": False},
        "provenance": {"profile_sha256": "a" * 64, "fixture_sha256": "b" * 64},
    }


def test_timer_uninstall_excludes_post_interval_fingerprint_work():
    owner = _Owner()
    timer = _ExclusiveTimer(clock_ns=_Clock((100, 190)))
    timer.wrap(owner, "work", "work")
    timer.wrap(owner, "snapshot", "snapshot")
    assert owner.work() == "work"
    timer.uninstall()
    assert owner.snapshot() == "fingerprint"
    assert timer.exclusive_ns == Counter({"work": 90})
    assert timer.calls == Counter({"work": 1})


def test_attribution_is_bounded_and_unattributed_is_nonnegative():
    attributed, unattributed, fraction = _validated_attribution(
        profiled_wall_s=1e-6, exclusive_ns=Counter({"work": 900}),
    )
    assert attributed == pytest.approx(9e-7)
    assert unattributed == pytest.approx(1e-7)
    assert fraction == pytest.approx(0.9)
    with pytest.raises(RuntimeError, match="escaped"):
        _validated_attribution(
            profiled_wall_s=1e-6, exclusive_ns=Counter({"work": 1_001}),
        )


def test_bounded_result_rejects_p95_and_provenance_mismatch():
    passing = _passing_result()
    validate_bounded_result(
        passing, expected_profile_sha256="a" * 64,
        expected_fixture_sha256="b" * 64,
    )
    slow = deepcopy(passing)
    slow["control"]["p95_s"] = 0.072000001
    with pytest.raises(ValueError, match="72 ms"):
        validate_bounded_result(
            slow, expected_profile_sha256="a" * 64,
            expected_fixture_sha256="b" * 64,
        )
    for field, message in (
        ("profile_sha256", "profiler provenance"),
        ("fixture_sha256", "fixture provenance"),
    ):
        foreign = deepcopy(passing)
        foreign["provenance"][field] = "c" * 64
        with pytest.raises(ValueError, match=message):
            validate_bounded_result(
                foreign, expected_profile_sha256="a" * 64,
                expected_fixture_sha256="b" * 64,
            )
