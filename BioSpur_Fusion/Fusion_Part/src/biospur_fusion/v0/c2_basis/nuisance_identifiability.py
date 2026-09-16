"""K0-A data-only observability audit for per-node inertial calibration.

This module deliberately stops before joint centres or anatomical frames.  It
models each raw six-axis sample with an unconstrained sample-local physical
signal.  Eliminating that signal exposes, rather than hides, the sensor-basis
gauge in a full scale/cross-axis calibration block.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import numpy as np

from biospur_fusion.v0.data import G


NATIVE_RATE_HZ = 200
NATIVE_STEP_NS = 5_000_000
# Existing C2 QMT causal frontend owner; this is a validity envelope, not a
# fitted noise or biomechanics threshold.
MAX_INTERPOLATION_BRACKET_NS = 12_500_000
PHYSICAL_COLUMN_SUFFIXES = tuple(
    [f"acc_scale_cross_axis[{i},{j}]" for i in range(3) for j in range(3)]
    + [f"gyro_scale_cross_axis[{i},{j}]" for i in range(3) for j in range(3)]
    + [f"acc_bias[{i}]" for i in range(3)]
    + [f"gyro_bias[{i}]" for i in range(3)]
    + [f"acc_drift[{i}]" for i in range(3)]
    + [f"gyro_drift[{i}]" for i in range(3)]
)


@dataclass(frozen=True)
class GapSafeEpisode:
    time_ns: np.ndarray
    acc_mps2: Mapping[str, np.ndarray]
    gyro_rad_s: Mapping[str, np.ndarray]
    audit: Mapping[str, Any]


def parameter_registry(identity: Mapping[str, str]) -> dict[str, Any]:
    """Return the pre-outcome owner registry; no parameters are cross-node."""

    nodes = []
    active_names: list[str] = []
    for node, segment in sorted(identity.items()):
        names = [f"{node}.{suffix}" for suffix in PHYSICAL_COLUMN_SUFFIXES]
        active_names.extend(names)
        nodes.append({
            "node": node,
            "segment": segment,
            "time_invariant_within_node": {
                "accelerometer_scale_cross_axis_3x3": names[0:9],
                "gyroscope_scale_cross_axis_3x3": names[9:18],
            },
            "per_node_capture_state": {
                "accelerometer_bias_3": names[18:21],
                "gyroscope_bias_3": names[21:24],
                "accelerometer_drift_3": names[24:27],
                "gyroscope_drift_3": names[27:30],
            },
            "sample_local_nuisance": [
                f"{node}.true_specific_force[t,0:3]",
                f"{node}.true_angular_rate[t,0:3]",
            ],
            "cross_node_parameter_equality": False,
        })
    return {
        "schema": "biospur-c2-k0a-parameter-registry-v1",
        "active_parameter_count": len(active_names),
        "active_parameter_names": active_names,
        "nodes": nodes,
        "shared_semantics": (
            "scale/cross-axis blocks are shared only across time and episodes "
            "within one physical node; accelerometer and gyro matrices are "
            "distinct and no value is shared across nodes"
        ),
        "deferred_distinct_not_active_in_k0a": {
            "pair_clock": "one state per physical adjacent-node pair",
            "hip_lever": "left and right are distinct; K0-B only",
            "mounting_frame": "one frame per physical node; K0-C only",
            "joint_centres": "forbidden in K0-A",
        },
        "priors_and_bounds_contribute_rank": False,
    }


def gauge_registry(identity: Mapping[str, str]) -> dict[str, Any]:
    gauges = []
    for node in sorted(identity):
        for modality in ("acc", "gyro"):
            for output_axis in range(3):
                for input_axis in range(3):
                    gauges.append({
                        "name": (
                            f"{node}.{modality}_sensor_basis_"
                            f"{output_axis}_{input_axis}"
                        ),
                        "physical_column": (
                            f"{node}.{modality}_scale_cross_axis"
                            f"[{output_axis},{input_axis}]"
                        ),
                        "local_compensation": (
                            f"delta_true_{modality}[t,{output_axis}] = "
                            f"delta_M[{output_axis},{input_axis}] * "
                            f"raw_{modality}[t,{input_axis}]"
                        ),
                        "owner": "WITHIN_NODE_SAMPLE_LOCAL_SENSOR_BASIS_GAUGE",
                    })
    return {
        "schema": "biospur-c2-k0a-named-gauge-registry-v1",
        "gauge_count": len(gauges),
        "gauges": gauges,
        "schur_semantics": (
            "local true 3-vector per modality spans every measurement-residual "
            "row; priors/bounds are excluded from both direct and Schur rank"
        ),
    }


def chronological_action_split(actions: Sequence[str]) -> dict[str, Any]:
    actions = tuple(str(value) for value in actions)
    if len(actions) != 19 or len(set(actions)) != len(actions):
        raise ValueError("K0-A requires exactly nineteen ordered unique actions")
    train_count = int(np.floor(0.60 * len(actions)))
    if train_count != 11:
        raise AssertionError("frozen 60/40 action split changed")
    return {
        "schema": "biospur-c2-k0a-chronological-action-split-v1",
        "algorithm": "first floor(0.60*N) complete actions train; suffix validates",
        "row_or_label_outcome_used": False,
        "train_fraction_actions": train_count / len(actions),
        "validation_fraction_actions": (len(actions) - train_count) / len(actions),
        "train_actions": list(actions[:train_count]),
        "validation_actions": list(actions[train_count:]),
    }


def gap_safe_native200(rows_by_node: Mapping[str, np.ndarray]) -> GapSafeEpisode:
    """Interpolate only brackets within the existing 12.5 ms validity owner."""

    if not rows_by_node:
        raise ValueError("empty episode")
    accepted: dict[str, np.ndarray] = {}
    start = -2**63
    stop = 2**63 - 1
    for node, rows in rows_by_node.items():
        current = rows[rows["status"] == 1]
        if len(current) < 3:
            raise ValueError(f"{node}: fewer than three accepted rows")
        times = current["global_time_ns"].astype(np.int64)
        if np.any(np.diff(times) <= 0):
            raise ValueError(f"{node}: accepted time is not strictly increasing")
        accepted[node] = current
        start = max(start, int(times[0]))
        stop = min(stop, int(times[-1]))
    first = ((start + NATIVE_STEP_NS - 1) // NATIVE_STEP_NS) * NATIVE_STEP_NS
    last = (stop // NATIVE_STEP_NS) * NATIVE_STEP_NS
    if last <= first:
        raise ValueError("no common native-200 interval")
    grid = np.arange(first, last + 1, NATIVE_STEP_NS, dtype=np.int64)
    acc_out: dict[str, np.ndarray] = {}
    gyro_out: dict[str, np.ndarray] = {}
    valid_all = np.ones(len(grid), dtype=bool)
    node_audit: dict[str, Any] = {}
    staged: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for node, rows in accepted.items():
        times = rows["global_time_ns"].astype(np.int64)
        right = np.searchsorted(times, grid, side="left")
        right = np.clip(right, 1, len(times) - 1)
        left = right - 1
        gap = times[right] - times[left]
        same_boot = rows["boot_epoch"][left] == rows["boot_epoch"][right]
        valid = (
            (times[left] <= grid)
            & (grid <= times[right])
            & (gap <= MAX_INTERPOLATION_BRACKET_NS)
            & same_boot
        )
        alpha = ((grid - times[left]) / gap).reshape(-1, 1)
        acc = rows["acc_raw"].astype(float) / 2048.0 * G
        gyro = np.deg2rad(rows["gyro_raw"].astype(float) / 16.384)
        staged[node] = (
            acc[left] + alpha * (acc[right] - acc[left]),
            gyro[left] + alpha * (gyro[right] - gyro[left]),
        )
        valid_all &= valid
        node_audit[node] = {
            "accepted_source_rows": int(len(rows)),
            "maximum_source_dt_ns": int(np.max(np.diff(times))),
            "maximum_retained_bracket_ns_before_common_mask": (
                int(np.max(gap[valid])) if np.any(valid) else None
            ),
            "invalid_grid_rows_before_common_mask": int(np.count_nonzero(~valid)),
        }
    if np.count_nonzero(valid_all) < 20:
        raise ValueError("fewer than twenty common gap-safe native-200 rows")
    selected_grid = grid[valid_all]
    for node, (acc, gyro) in staged.items():
        acc_out[node] = np.ascontiguousarray(acc[valid_all])
        gyro_out[node] = np.ascontiguousarray(gyro[valid_all])
    dt = np.diff(selected_grid)
    return GapSafeEpisode(
        time_ns=selected_grid,
        acc_mps2=acc_out,
        gyro_rad_s=gyro_out,
        audit={
            "schema": "biospur-c2-k0a-gap-safe-native200-v1",
            "native_rate_hz": NATIVE_RATE_HZ,
            "native_step_ns": NATIVE_STEP_NS,
            "maximum_interpolation_bracket_ns": MAX_INTERPOLATION_BRACKET_NS,
            "threshold_owner": "tools/run_c2_qmt_open_source_baseline.py",
            "common_candidate_rows": int(len(grid)),
            "retained_common_rows": int(len(selected_grid)),
            "removed_common_rows": int(np.count_nonzero(~valid_all)),
            "retained_time_strictly_increasing": bool(np.all(dt > 0)),
            "retained_steps_are_integer_native_steps": bool(
                np.all(dt % NATIVE_STEP_NS == 0)
            ),
            "contiguous_span_count": int(1 + np.count_nonzero(dt != NATIVE_STEP_NS)),
            "nodes": node_audit,
        },
    )


def _chunk_jacobian(
    acc: np.ndarray, gyro: np.ndarray, time_coordinate: np.ndarray,
) -> np.ndarray:
    n = len(acc)
    out = np.zeros((6 * n, 30), dtype=float)
    for axis in range(3):
        rows = 6 * np.arange(n) + axis
        out[rows, 3 * axis:3 * axis + 3] = acc
        out[rows, 18 + axis] = 1.0
        out[rows, 24 + axis] = time_coordinate
        rows = 6 * np.arange(n) + 3 + axis
        out[rows, 9 + 3 * axis:9 + 3 * axis + 3] = gyro
        out[rows, 21 + axis] = 1.0
        out[rows, 27 + axis] = time_coordinate
    return out


def audit_node_observability(
    node: str,
    blocks: Sequence[tuple[np.ndarray, np.ndarray, np.ndarray]],
    *,
    rank_tolerance: float = 1e-10,
    chunk_rows: int = 4096,
) -> dict[str, Any]:
    """Compute direct Gram/SVD and exact local-signal Schur information."""

    if not blocks:
        raise ValueError(f"{node}: no data blocks")
    gram = np.zeros((30, 30), dtype=float)
    residual_rows = 0
    row_count = 0
    for acc, gyro, time_coordinate in blocks:
        acc = np.asarray(acc, float)
        gyro = np.asarray(gyro, float)
        time_coordinate = np.asarray(time_coordinate, float)
        if acc.shape != gyro.shape or acc.ndim != 2 or acc.shape[1] != 3:
            raise ValueError(f"{node}: invalid inertial block shape")
        if time_coordinate.shape != (len(acc),):
            raise ValueError(f"{node}: invalid time coordinate")
        if not np.all(np.isfinite(np.c_[acc, gyro, time_coordinate])):
            raise ValueError(f"{node}: nonfinite data")
        for start in range(0, len(acc), chunk_rows):
            stop = min(len(acc), start + chunk_rows)
            jac = _chunk_jacobian(acc[start:stop], gyro[start:stop], time_coordinate[start:stop])
            gram += jac.T @ jac
            residual_rows += len(jac)
        row_count += len(acc)
    rms = np.sqrt(np.diag(gram) / max(1, residual_rows))
    uninformed_direct = [
        f"{node}.{PHYSICAL_COLUMN_SUFFIXES[index]}"
        for index in np.flatnonzero(~np.isfinite(rms) | (rms <= 0.0))
    ]
    scale = np.divide(1.0, rms, out=np.zeros_like(rms), where=rms > 0.0)
    scaled_gram = scale[:, None] * gram * scale[None, :]
    eigenvalues, vectors = np.linalg.eigh((scaled_gram + scaled_gram.T) * 0.5)
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = np.maximum(eigenvalues[order], 0.0)
    vectors = vectors[:, order]
    singular = np.sqrt(eigenvalues)
    cutoff = rank_tolerance * max(1.0, float(singular[0]))
    direct_rank = int(np.count_nonzero(singular > cutoff))
    nonzero = singular[singular > cutoff]
    direct_condition = (
        float(nonzero[0] / nonzero[-1]) if len(nonzero) else float("inf")
    )

    # Each sample owns an unconstrained 6-vector z and its Jacobian is -I6.
    # Therefore P_perp = I - B(B'B)^-1B' is exactly zero, independently for
    # every actual row.  We still exercise the numerical cancellation on the
    # first actual chunk to bind the proof to this data and column scaling.
    first_acc, first_gyro, first_time = blocks[0]
    take = min(256, len(first_acc))
    witness = _chunk_jacobian(first_acc[:take], first_gyro[:take], first_time[:take])
    witness = witness * scale[None, :]
    projected_witness = witness - np.eye(len(witness)) @ witness
    schur = projected_witness.T @ projected_witness
    schur_eigen = np.linalg.eigvalsh((schur + schur.T) * 0.5)[::-1]
    schur_eigen = np.maximum(schur_eigen, 0.0)
    schur_rank = int(np.count_nonzero(schur_eigen > rank_tolerance))
    # A parameter subset is identifiable only when adding that subset raises
    # the rank by its full dimension.  Eigenvalues are ordered by magnitude,
    # not by physical column, so indexing eigenvalues 9:18 would not test the
    # gyroscope scale/cross-axis coordinates.
    non_gyro = np.r_[0:9, 18:30]
    schur_without_gyro = schur[np.ix_(non_gyro, non_gyro)]
    without_gyro_eigen = np.linalg.eigvalsh(
        (schur_without_gyro + schur_without_gyro.T) * 0.5
    )
    without_gyro_rank = int(
        np.count_nonzero(np.maximum(without_gyro_eigen, 0.0) > rank_tolerance)
    )
    gyro_scale_cross_axis_rank_increment = schur_rank - without_gyro_rank
    named_nulls = []
    for index, suffix in enumerate(PHYSICAL_COLUMN_SUFFIXES):
        named_nulls.append({
            "name": f"{node}.null::{suffix}",
            "physical_column_index": index,
            "physical_column": f"{node}.{suffix}",
            "scaled_physical_unit_vector": [
                1.0 if j == index else 0.0 for j in range(30)
            ],
            "local_signal_compensation_l2": float(np.sqrt(scaled_gram[index, index])),
            "schur_quadratic_information": 0.0,
        })
    return {
        "node": node,
        "sample_rows": row_count,
        "measurement_residual_rows": residual_rows,
        "physical_columns": [f"{node}.{value}" for value in PHYSICAL_COLUMN_SUFFIXES],
        "column_rms": rms.tolist(),
        "column_scale": scale.tolist(),
        "direct_scaled_singular_values": singular.tolist(),
        "direct_scaled_rank": direct_rank,
        "direct_scaled_condition": direct_condition,
        "direct_uninformed_columns": uninformed_direct,
        "smallest_direct_right_singular_vector": vectors[:, -1].tolist(),
        "schur_local_nuisance": "sample-local true acc/gyro six-vector; block Jacobian -I6",
        "schur_scaled_eigenvalues": schur_eigen.tolist(),
        "schur_scaled_rank": schur_rank,
        "gyro_scale_cross_axis_rank_increment": gyro_scale_cross_axis_rank_increment,
        "schur_projection_witness_max_abs": float(np.max(np.abs(projected_witness))),
        "named_null_vectors": named_nulls,
        "full_gyro_scale_cross_axis_informed": (
            gyro_scale_cross_axis_rank_increment == 9
        ),
    }


def normalized_time_blocks(lengths: Sequence[int]) -> tuple[np.ndarray, ...]:
    total = int(sum(int(value) for value in lengths))
    if total < 2:
        raise ValueError("time normalization needs at least two rows")
    cursor = 0
    output = []
    for length in lengths:
        indices = np.arange(cursor, cursor + int(length), dtype=float)
        output.append(2.0 * indices / (total - 1) - 1.0)
        cursor += int(length)
    return tuple(output)
