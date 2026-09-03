from __future__ import annotations

from pathlib import Path

import numpy as np


from biospur_fusion.v0.contracts import (
    IDENTITY, NODES, SUPERSEDED_INITIAL_STILL, WINDOWS, load_config,
)
from biospur_fusion.v0.frontend import AttitudeTimeline, run_vqf_native_hybrid
from biospur_fusion.v0.data import _stored_npy_memmap
from biospur_fusion.v0.math3d import heading, rotation_angle
from biospur_fusion.v0.model import _profile_audits_without_runtime, resample_window


ROOT = Path(__file__).resolve().parents[2]
IMU_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"),
    ("global_time_ns", "<i8"), ("acc_raw", "<i2", (3,)),
    ("gyro_raw", "<i2", (3,)), ("status", "u1"),
])


def _synthetic_rows(times_ns: np.ndarray, boot: np.ndarray | None = None) -> np.ndarray:
    rows = np.zeros(len(times_ns), dtype=IMU_DTYPE)
    rows["global_time_ns"] = times_ns
    rows["boot_epoch"] = 1 if boot is None else boot
    rows["sequence"] = np.arange(len(rows), dtype=np.uint16)
    rows["acc_raw"][:, 2] = 2048
    rows["gyro_raw"][:, 2] = round(30.0 * 16.384)
    rows["status"] = 1
    return rows


def _timeline(times: np.ndarray, interval: np.ndarray) -> AttitudeTimeline:
    count = len(times)
    rotation = np.repeat(np.eye(3)[None], count, axis=0)
    quaternion = np.repeat(np.array([[1.0, 0.0, 0.0, 0.0]]), count, axis=0)
    zero3 = np.zeros((count, 3))
    return AttitudeTimeline(
        times, rotation, quaternion, zero3, np.full(count, 0.01),
        np.ones(count, bool), interval, zero3, zero3,
        np.r_[np.nan, np.diff(times) * 1e-9],
        np.full(count, "CONTINUOUS", dtype="U24"), {},
    )


def test_exact_identity_and_capture1_authority() -> None:
    config = load_config(ROOT / "config/biospur_fusion_v0/config.json")
    assert config.payload["identity"] == IDENTITY
    assert IDENTITY["BSFEC35"] == "forearm_left"
    assert IDENTITY["BSFB165"] == "forearm_right"
    assert IDENTITY["BSFC2CC"] == "pelvis"
    configured = tuple(
        (row["label"], row["start_global_time_ns"], row["stop_global_time_ns_exclusive"])
        for row in config.payload["capture1_windows"]
    )
    assert configured == WINDOWS
    assert SUPERSEDED_INITIAL_STILL not in {(start, stop) for _, start, stop in WINDOWS}
    assert all("golf" not in label.lower() and "boxing" not in label.lower() for label, _, _ in WINDOWS)


def test_native_jitter_drives_active_yaw_not_fixed_200_hz() -> None:
    dt_ns = np.resize(np.array([4_100_000, 6_300_000, 4_600_000, 5_800_000]), 799)
    times = np.r_[0, np.cumsum(dt_ns)].astype(np.int64)
    rows = _synthetic_rows(times)
    result = run_vqf_native_hybrid(
        rows, node_id="synthetic", max_gap_ns=20_000_000,
        use_vqf_bias_in_native_yaw=False,
    )
    yaw = np.unwrap(np.asarray([heading(value) for value in result.rotation_world_sensor]))
    encoded_rate = np.deg2rad(float(rows["gyro_raw"][0, 2]) / 16.384)
    expected = encoded_rate * float(np.sum(dt_ns)) * 1e-9
    fixed = encoded_rate * (len(rows) - 1) / 200.0
    assert abs((yaw[-1] - yaw[0]) - expected) < 1e-10
    assert abs(expected - fixed) > 1e-3
    assert result.audit["native_dt_expression"] == "(global_time_ns[i]-global_time_ns[i-1])/1e9"
    assert result.audit["fixed_one_over_200_used"] is False


def test_gap_and_boot_reset_are_explicit_and_not_integrated() -> None:
    first = np.arange(0, 300, dtype=np.int64) * 5_000_000
    second = first[-1] + 125_000_000 + np.arange(1, 301, dtype=np.int64) * 5_000_000
    times = np.r_[first, second]
    boot = np.r_[np.ones(len(first), dtype=np.uint16), np.full(len(second), 2, dtype=np.uint16)]
    result = run_vqf_native_hybrid(_synthetic_rows(times, boot), node_id="synthetic", max_gap_ns=20_000_000)
    boundary = len(first)
    assert result.interval_id[boundary] == result.interval_id[boundary - 1] + 1
    assert result.boundary_reason[boundary] == "BOOT_RESET"
    assert result.audit["gap_or_boot_resets"] == 1
    assert result.audit["blocks"][0]["last_time_ns"] == int(times[boundary - 1])
    assert result.audit["blocks"][1]["first_time_ns"] == int(times[boundary])


def test_one_node_gap_becomes_degraded_hold_without_stopping_other_chains() -> None:
    full = np.arange(0, 1_000_000_001, 5_000_000, dtype=np.int64)
    frontends = {node: _timeline(full, np.zeros(len(full), np.int32)) for node in NODES}
    retained = (full < 400_000_000) | (full > 600_000_000)
    gap_times = full[retained]
    gap_interval = (gap_times > 600_000_000).astype(np.int32)
    frontends["BSF3C79"] = _timeline(gap_times, gap_interval)
    evidence = {
        "extrinsic_rotation": {
            node: {"rotvec_segment_from_sensor": [0.0, 0.0, 0.0]} for node in NODES
        }
    }
    output = resample_window(frontends, evidence, rate_hz=10)
    degraded = output.segment_degraded["thigh_right"]
    assert len(output.time_ns) == 11
    assert np.count_nonzero(degraded) >= 2
    assert np.isfinite(output.segment_rotation["thigh_right"]).all()
    assert np.all(output.node_bias_sigma["BSF3C79"][degraded] == np.pi)
    assert not np.any(output.segment_degraded["thigh_left"])
    assert np.any(output.boundary == "NODE_GAP_OR_BOOT")


def test_active_v0_source_has_no_q1_fixed_rate_motion_gate() -> None:
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "src/biospur_fusion/v0").glob("*.py"))
    )
    assert "gyro_dps / 200.0" not in source
    assert "RawUwbRangeFactor(" not in source
    assert "CanonicalT4Frontend(" not in source


def test_frozen_profile_audit_provenance_excludes_nondeterministic_runtime() -> None:
    first = _profile_audits_without_runtime({
        "initial": {"BSFEC35": {"runtime_s": 1.2, "rest_fraction": 0.5}},
    })
    second = _profile_audits_without_runtime({
        "initial": {"BSFEC35": {"runtime_s": 99.9, "rest_fraction": 0.5}},
    })
    assert first == second == {"initial": {"BSFEC35": {"rest_fraction": 0.5}}}


def test_real_arms_near_vertical_heading_has_no_180_degree_frontend_flip() -> None:
    """Regression for the former projection-heading singularity on BSFEC35."""
    config = load_config(ROOT / "config/biospur_fusion_v0/config.json")
    ledger = ROOT / str(config.payload["ledger"])
    rows, _ = _stored_npy_memmap(ledger, "imu_BSFEC35.npy")
    _, start_ns, stop_ns = next(row for row in WINDOWS if row[0] == "arms")
    left = int(np.searchsorted(rows["global_time_ns"], start_ns, side="left"))
    right = int(np.searchsorted(rows["global_time_ns"], stop_ns, side="left"))
    accepted = np.asarray(rows[left:right])
    accepted = accepted[accepted["status"] == 1]
    result = run_vqf_native_hybrid(
        accepted, node_id="BSFEC35", max_gap_ns=config.section("frontend")["max_gap_ns"],
    )
    step_deg = np.degrees(rotation_angle(
        result.rotation_world_sensor[:-1], result.rotation_world_sensor[1:]
    ))
    continuous = result.interval_id[:-1] == result.interval_id[1:]
    assert float(np.max(step_deg[continuous])) < 5.0
    assert not np.any(step_deg[continuous] > 90.0)
