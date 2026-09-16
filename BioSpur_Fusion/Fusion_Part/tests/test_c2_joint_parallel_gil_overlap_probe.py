from __future__ import annotations

import numpy as np
import pytest

import probe_c2_joint_parallel_gil_overlap as probe


INPUT_SHA256 = "374e69dd4fa681185e6bcf49e07a973b6c30dc91d3a1dec4640ce01b70559b39"


def test_exact_readonly_schedule_materializes_at_qualified_local_boundary(
    monkeypatch,
):
    def forbidden_task(*_args, **_kwargs):
        raise AssertionError("dry materialization must not submit a task")

    monkeypatch.setattr(probe, "_joint_task", forbidden_task)
    result = probe._dry_materialization(INPUT_SHA256)
    assert result["status"] == "DRY_MATERIALIZATION_PASS"
    assert result["input_sha256"] == INPUT_SHA256
    assert result["output_exact"] is True
    assert result["sealed_owner_unchanged"] is True
    assert all(
        row["writeable"] is False
        for row in result["sealed_owner_flags"].values()
    )
    assert result["tasks_submitted"] == 0
    assert result["modes_reaching_pre_task_absolute"] == [1, 2, 4]
    assert result["measured_repetitions"] == 0
    assert result["executor_constructed"] is False


@pytest.mark.parametrize(
    "values",
    (
        [1.0, 2.0, 3.0],
        (value for value in (1.0, 2.0, 3.0)),
        np.asarray([1.0, 2.0, 3.0]),
        [2.5],
    ),
)
def test_summary_materializes_supported_iterables_without_formula_change(values):
    result = probe._summary(values)
    expected = np.asarray([1.0, 2.0, 3.0] if result["count"] == 3 else [2.5])
    assert result == {
        "count": len(expected),
        "mean": float(np.mean(expected)),
        "p50": float(np.percentile(expected, 50)),
        "p99": float(np.percentile(expected, 99)),
        "maximum": float(np.max(expected)),
    }


def test_summary_rejects_empty_iterable_deterministically():
    with pytest.raises(ValueError, match="summary requires at least one value"):
        probe._summary(iter(()))
