from __future__ import annotations

import numpy as np

from biospur_fusion.v0.c2_basis.nuisance_identifiability import (
    MAX_INTERPOLATION_BRACKET_NS,
    audit_node_observability,
    chronological_action_split,
    gap_safe_native200,
    normalized_time_blocks,
    parameter_registry,
)


IDENTITY = {"N0": "pelvis", "N1": "torso"}


def _rows(times: np.ndarray) -> np.ndarray:
    dtype = np.dtype([
        ("status", "u1"), ("global_time_ns", "i8"), ("boot_epoch", "u2"),
        ("acc_raw", "i2", (3,)), ("gyro_raw", "i2", (3,)),
    ])
    rows = np.zeros(len(times), dtype=dtype)
    rows["status"] = 1
    rows["global_time_ns"] = times
    rows["boot_epoch"] = 7
    k = np.arange(len(times))
    rows["acc_raw"] = np.c_[100 + k, 200 - k, 2048 + 2 * k]
    rows["gyro_raw"] = np.c_[20 + k, 40 - k, 10 + 3 * k]
    return rows


def test_registry_never_shares_calibration_across_nodes() -> None:
    registry = parameter_registry(IDENTITY)
    names = registry["active_parameter_names"]
    assert len(names) == 60
    assert len(set(names)) == 60
    assert all(row["cross_node_parameter_equality"] is False for row in registry["nodes"])


def test_chronological_split_is_frozen_action_prefix() -> None:
    actions = [f"{index:02d}" for index in range(19)]
    split = chronological_action_split(actions)
    assert split["train_actions"] == actions[:11]
    assert split["validation_actions"] == actions[11:]


def test_gap_safe_native200_discards_wide_bracket_for_every_node() -> None:
    base = np.arange(30, dtype=np.int64) * 5_000_000
    altered = np.delete(base, [5, 6])  # 15 ms, wider than frozen 12.5 ms gate.
    episode = gap_safe_native200({"N0": _rows(base), "N1": _rows(altered)})
    assert episode.audit["native_rate_hz"] == 200
    assert episode.audit["maximum_interpolation_bracket_ns"] == MAX_INTERPOLATION_BRACKET_NS
    assert not np.any(np.isin(episode.time_ns, [25_000_000, 30_000_000]))
    assert episode.audit["retained_steps_are_integer_native_steps"] is True


def test_actual_design_is_directly_excited_but_schur_exposes_sensor_basis_gauge() -> None:
    rng = np.random.default_rng(2309)
    lengths = [300, 220]
    times = normalized_time_blocks(lengths)
    blocks = []
    for n, t in zip(lengths, times, strict=True):
        blocks.append((
            rng.normal(size=(n, 3)) + np.c_[t, t * t, np.sin(3 * t)],
            rng.normal(size=(n, 3)) + np.c_[np.cos(t), t, t * t],
            t,
        ))
    audit = audit_node_observability("N0", blocks)
    assert audit["direct_scaled_rank"] == 30
    assert audit["schur_scaled_rank"] == 0
    assert audit["gyro_scale_cross_axis_rank_increment"] == 0
    assert audit["schur_projection_witness_max_abs"] == 0.0
    assert audit["full_gyro_scale_cross_axis_informed"] is False
    assert len(audit["named_null_vectors"]) == 30


def test_zero_raw_axis_is_named_uninformed_before_priors() -> None:
    n = 100
    t = np.linspace(-1.0, 1.0, n)
    acc = np.c_[np.ones(n), np.linspace(0.0, 1.0, n), np.zeros(n)]
    gyro = np.c_[np.ones(n), np.linspace(1.0, 2.0, n), np.zeros(n)]
    audit = audit_node_observability("N0", [(acc, gyro, t)])
    assert audit["direct_uninformed_columns"]
    assert all("scale_cross_axis" in name for name in audit["direct_uninformed_columns"])
