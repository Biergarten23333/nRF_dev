"""Authoritative time-local orientation uncertainty owner shared by fit/eval."""
from __future__ import annotations

from hashlib import sha256
from typing import Any, Mapping, Sequence

import numpy as np

from .orientation import OrientedAction


def _array_sha256(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return sha256(array.view(np.uint8)).hexdigest()


def cumulative_observed_orientation_terms(
    oriented_actions: Sequence[OrientedAction],
    *,
    node: str,
    current_action_index: int,
    current_source_index: int,
    sample_period_s: float,
) -> Mapping[str, float | int]:
    """Integrate every retained observed row through one selected source row.

    Scheduled gaps, timer resets, and boot transitions are never converted to
    observed elapsed time.  Their uncertainty remains exclusively in the
    continuous orientation owner's separate cumulative gap covariance.
    """

    if not 0 <= int(current_action_index) < len(oriented_actions):
        raise IndexError("orientation uncertainty action index leaves the processed sequence")
    observed_rows = 0
    observed_duration_s = 0.0
    gyro_white_integration_s2 = 0.0
    integrated_rotation_path_rad = 0.0
    for action_index, oriented in enumerate(oriented_actions[: current_action_index + 1]):
        row_count = len(oriented.time_us_by_node[node])
        stop = row_count if action_index < current_action_index else int(current_source_index) + 1
        if stop < 0 or stop > row_count:
            raise IndexError("physical orientation source index leaves current OrientedAction")
        if stop == 0:
            continue
        time = np.asarray(oriented.time_us_by_node[node][:stop], dtype=np.int64)
        boot = np.asarray(oriented.derived_boot_epoch_by_node[node][:stop], dtype=np.int64)
        span = np.asarray(oriented.contiguous_span_id_by_node[node][:stop], dtype=np.int64)
        gyro = np.asarray(oriented.gyro_rads_by_node[node][:stop], dtype=float)
        observed_rows += len(time)
        dt = np.full(len(time), float(sample_period_s), dtype=float)
        if len(time) > 1:
            consecutive = (
                (np.diff(boot) == 0)
                & (np.diff(span) == 0)
                & (np.diff(time) > 0)
            )
            actual = np.diff(time).astype(float) * 1e-6
            dt[1:] = np.where(consecutive, actual, float(sample_period_s))
        observed_duration_s += float(np.sum(dt))
        gyro_white_integration_s2 += float(np.sum(dt**2))
        integrated_rotation_path_rad += float(np.sum(np.linalg.norm(gyro, axis=1) * dt))
    return {
        "observed_rows": observed_rows,
        "observed_duration_s": observed_duration_s,
        "gyro_white_integration_s2": gyro_white_integration_s2,
        "integrated_rotation_path_rad": integrated_rotation_path_rad,
        "inter_episode_or_gap_duration_added_as_observed": False,
    }


def physical_orientation_covariance(
    oriented_actions: Sequence[OrientedAction],
    *,
    current_action_index: int,
    segment: str,
    node: str,
    source_indices: np.ndarray,
    timing_sigma_s: np.ndarray,
    initial_stochastic_state: Mapping[str, Any],
    initial_stochastic_state_semantic_sha256: str,
    orientation_settings: Mapping[str, Any],
    uncertainty_settings: Mapping[str, Any],
) -> tuple[np.ndarray, Mapping[str, Any]]:
    """Build the sole authoritative observed+gap orientation covariance."""

    if not 0 <= int(current_action_index) < len(oriented_actions):
        raise IndexError("orientation covariance action index leaves the processed sequence")
    oriented = oriented_actions[int(current_action_index)]
    indices = np.asarray(source_indices, dtype=np.int64)
    timing_sigma = np.asarray(timing_sigma_s, dtype=float)
    if indices.ndim != 1 or timing_sigma.shape != indices.shape:
        raise ValueError("physical covariance indices/timing sigma must be equal one-dimensional arrays")
    nodes = initial_stochastic_state["nodes"]
    if node not in nodes:
        raise ValueError("physical trajectory node lacks immutable P1 stochastic covariance")
    p1 = nodes[node]
    gyro_covariance = np.asarray(p1["gyro_observation_covariance_rad2_s2"], dtype=float)
    bias_covariance = np.asarray(p1["gyro_bias_covariance_rad2_s2"], dtype=float)
    acc_covariance = np.asarray(p1["accelerometer_observation_covariance_m2_s4"], dtype=float)
    for label, value in (
        ("gyro observation", gyro_covariance),
        ("initial bias", bias_covariance),
        ("accelerometer observation", acc_covariance),
    ):
        if value.shape != (3, 3) or not np.isfinite(value).all():
            raise ValueError(f"{label} P1 covariance must be finite 3x3")
    gap_covariance = np.asarray(
        oriented.gap_only_orientation_covariance_rad2_by_node[node][indices], dtype=float,
    )
    bias_sigma = np.asarray(
        oriented.vqf_residual_bias_sigma_rad_s_by_node[node][indices], dtype=float,
    )
    gyro = np.asarray(oriented.gyro_rads_by_node[node][indices], dtype=float)
    if gap_covariance.shape != (len(indices), 3, 3) or bias_sigma.shape != (len(indices),):
        raise ValueError("orientation uncertainty owner arrays differ from selected physical rows")

    gyro_multiplier = float(uncertainty_settings["gyro_white_noise_multiplier"])
    initial_bias_horizon = float(uncertainty_settings["initial_bias_correlation_time_s"])
    vqf_bias_horizon = float(uncertainty_settings["vqf_residual_bias_correlation_time_s"])
    tilt_sensitivity = float(
        uncertainty_settings["accelerometer_tilt_sensitivity_rad_per_mps2"]
    )
    scale_cross_sigma = float(
        uncertainty_settings["gyro_scale_cross_axis_fraction_sigma"]
    )
    timing_multiplier = float(uncertainty_settings["clock_timing_sigma_multiplier"])
    if min(
        gyro_multiplier,
        initial_bias_horizon,
        vqf_bias_horizon,
        tilt_sensitivity,
        scale_cross_sigma,
        timing_multiplier,
    ) < 0.0:
        raise ValueError("registered physical orientation uncertainty settings must be nonnegative")

    output = np.empty((len(indices), 3, 3), dtype=float)
    component_traces: list[dict[str, Any]] = []
    for output_index, source_index in enumerate(indices):
        terms = cumulative_observed_orientation_terms(
            oriented_actions,
            node=node,
            current_action_index=int(current_action_index),
            current_source_index=int(source_index),
            sample_period_s=float(orientation_settings["sample_period_s"]),
        )
        observed_duration = float(terms["observed_duration_s"])
        gyro_white = (
            gyro_multiplier * float(terms["gyro_white_integration_s2"]) * gyro_covariance
        )
        initial_bias = min(observed_duration, initial_bias_horizon) ** 2 * bias_covariance
        vqf_bias = np.eye(3) * (
            min(observed_duration, vqf_bias_horizon) * float(bias_sigma[output_index])
        ) ** 2
        accelerometer_tilt = np.eye(3) * (
            tilt_sensitivity**2 * max(0.0, float(np.trace(acc_covariance)) / 3.0)
        )
        scale_cross = np.eye(3) * (
            scale_cross_sigma * float(terms["integrated_rotation_path_rad"])
        ) ** 2
        timing = np.eye(3) * (
            timing_multiplier
            * float(timing_sigma[output_index])
            * float(np.linalg.norm(gyro[output_index]))
        ) ** 2
        covariance = (
            gap_covariance[output_index]
            + gyro_white
            + initial_bias
            + vqf_bias
            + accelerometer_tilt
            + scale_cross
            + timing
        )
        covariance = 0.5 * (covariance + covariance.T)
        if float(np.min(np.linalg.eigvalsh(covariance))) < -1e-12:
            raise ValueError("owner-derived physical orientation covariance is not PSD")
        output[output_index] = covariance
        component_traces.append({
            "source_index": int(source_index),
            **dict(terms),
            "gap_only_trace_rad2": float(np.trace(gap_covariance[output_index])),
            "gyro_white_trace_rad2": float(np.trace(gyro_white)),
            "initial_bias_trace_rad2": float(np.trace(initial_bias)),
            "vqf_residual_bias_trace_rad2": float(np.trace(vqf_bias)),
            "accelerometer_tilt_trace_rad2": float(np.trace(accelerometer_tilt)),
            "scale_cross_axis_trace_rad2": float(np.trace(scale_cross)),
            "clock_timing_trace_rad2": float(np.trace(timing)),
            "total_trace_rad2": float(np.trace(covariance)),
        })
    return output, {
        "schema": "biospur-c2-owner-derived-time-local-orientation-uncertainty-v2",
        "authoritative_owner": (
            "biospur_fusion.v0.c2_progressive.orientation_uncertainty."
            "physical_orientation_covariance"
        ),
        "segment": segment,
        "hardware_id": node,
        "source_indices_sha256": _array_sha256(indices),
        "covariance_sha256": _array_sha256(output),
        "initial_stochastic_state_semantic_sha256": initial_stochastic_state_semantic_sha256,
        "settings": dict(uncertainty_settings),
        "components": component_traces,
        "p1_gyro_quantization_variance_rad2_s2": float(
            p1["gyro_quantization_variance_rad2_s2"]
        ),
        "quantization_already_in_observation_covariance_not_added_twice": True,
        "gap_covariance_is_not_total_covariance": True,
        "broad_wear_prior_added_as_irreducible_covariance": False,
        "sparse_selected_rows_treated_as_contiguous_five_ms_samples": False,
    }


def numeric_sparse_quantile_orientation_uncertainty_gate(
    settings: Mapping[str, Any],
    initial_stochastic_state: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Exercise the shared fit/evaluation owner on sparse physical samples.

    Three selected trajectory rows deliberately span a 100-row observed
    sequence.  The gate fails if their uncertainty is accumulated as three
    adjacent 5 ms samples, or if the runtime wrapper diverges from this sole
    authoritative implementation.
    """

    from .heldout_evaluation import FrozenScientificHeldoutEvaluationOwner
    from .pipeline_runtime import C2PipelineRuntime

    node = str(next(iter(initial_stochastic_state["nodes"])))
    count = 100
    sample_period_s = float(settings["orientation"]["sample_period_s"])
    step_us = int(round(sample_period_s * 1e6))
    time = np.arange(count, dtype=np.int64) * step_us + 1_000_000
    gyro = np.column_stack((
        0.08 * np.sin(np.arange(count) * 0.07),
        0.05 * np.cos(np.arange(count) * 0.11),
        0.03 * np.sin(np.arange(count) * 0.13),
    ))
    zeros3 = np.zeros((count, 3), dtype=float)
    oriented = OrientedAction(
        action=str(settings["execution_contract"]["chronological_actions"][0]),
        chronological_index=0,
        time_us_by_node={node: time},
        derived_boot_epoch_by_node={node: np.zeros(count, dtype=np.int64)},
        contiguous_span_id_by_node={node: np.zeros(count, dtype=np.int64)},
        acc_mps2_by_node={node: zeros3.copy()},
        gyro_rads_by_node={node: gyro},
        quat_world_sensor_wxyz_by_node={
            node: np.tile(np.array([1.0, 0.0, 0.0, 0.0]), (count, 1)),
        },
        gap_only_orientation_covariance_rad2_by_node={
            node: np.zeros((count, 3, 3), dtype=float),
        },
        vqf_residual_bias_rad_s_by_node={node: zeros3.copy()},
        vqf_residual_bias_sigma_rad_s_by_node={
            node: np.full(count, 0.002, dtype=float),
        },
        vqf_rest_detected_by_node={node: np.zeros(count, dtype=bool)},
        audit={"synthetic_sparse_quantile_owner_gate": True},
    )
    full_selected = np.array([10, 60, 99], dtype=np.int64)
    heldout_selected = np.arange(len(full_selected), dtype=np.int64)
    timing = np.full(len(full_selected), float(settings["timing"]["jitter_floor_s"]))
    initial_sha = str(
        settings["execution_contract"]["initial_stochastic_state_semantic_sha256"]
    )
    direct, direct_audit = physical_orientation_covariance(
        [oriented],
        current_action_index=0,
        segment="SYNTHETIC_SEGMENT",
        node=node,
        source_indices=full_selected,
        timing_sigma_s=timing,
        initial_stochastic_state=initial_stochastic_state,
        initial_stochastic_state_semantic_sha256=initial_sha,
        orientation_settings=settings["orientation"],
        uncertainty_settings=settings["physical_candidates"]["orientation_uncertainty"],
    )
    runtime = object.__new__(C2PipelineRuntime)
    runtime._current_index = 0
    runtime._oriented_actions = [oriented]
    runtime._initial_stochastic_state = initial_stochastic_state
    runtime._initial_stochastic_state_semantic_sha256 = initial_sha
    runtime.settings = settings
    wrapped, wrapped_audit = C2PipelineRuntime._physical_orientation_covariance(
        runtime,
        segment="SYNTHETIC_SEGMENT",
        node=node,
        source_indices=full_selected,
        timing_sigma_s=timing,
    )
    heldout_oriented = OrientedAction(
        action=oriented.action,
        chronological_index=0,
        time_us_by_node={node: time[full_selected]},
        derived_boot_epoch_by_node={
            node: np.zeros(len(full_selected), dtype=np.int64),
        },
        contiguous_span_id_by_node={
            node: np.zeros(len(full_selected), dtype=np.int64),
        },
        acc_mps2_by_node={node: zeros3[full_selected]},
        gyro_rads_by_node={node: gyro[full_selected]},
        quat_world_sensor_wxyz_by_node={
            node: oriented.quat_world_sensor_wxyz_by_node[node][full_selected],
        },
        gap_only_orientation_covariance_rad2_by_node={
            node: oriented.gap_only_orientation_covariance_rad2_by_node[node][full_selected],
        },
        vqf_residual_bias_rad_s_by_node={
            node: oriented.vqf_residual_bias_rad_s_by_node[node][full_selected],
        },
        vqf_residual_bias_sigma_rad_s_by_node={
            node: oriented.vqf_residual_bias_sigma_rad_s_by_node[node][full_selected],
        },
        vqf_rest_detected_by_node={
            node: oriented.vqf_rest_detected_by_node[node][full_selected],
        },
        audit={
            "nodes": {
                node: {"full_oriented_positions": full_selected.tolist()},
            },
            "synthetic_nonzero_heldout_offset_mapping": True,
        },
    )
    heldout_owner = object.__new__(FrozenScientificHeldoutEvaluationOwner)
    heldout_owner._node_by_segment = {"SYNTHETIC_SEGMENT": node}
    heldout_owner._full_oriented_actions = [oriented]
    heldout_owner._initial = initial_stochastic_state
    heldout_owner._initial_semantic_sha256 = initial_sha
    heldout_owner.settings = settings
    heldout_owner._orientation_uncertainty_audits = []
    heldout_owned = FrozenScientificHeldoutEvaluationOwner._orientation_covariance(
        heldout_owner,
        heldout_oriented,
        segment="SYNTHETIC_SEGMENT",
        source_indices=heldout_selected,
        timing_sigma_s=timing,
    )
    heldout_mapping_audit = heldout_owner._orientation_uncertainty_audits[-1]
    heldout_authoritative_audit = heldout_mapping_audit["authoritative_audit"]
    durations = np.asarray([
        row["observed_duration_s"] for row in direct_audit["components"]
    ], dtype=float)
    expected_durations = (full_selected.astype(float) + 1.0) * sample_period_s
    traces = np.trace(direct, axis1=1, axis2=2)
    passed = bool(
        np.array_equal(direct, wrapped)
        and direct_audit == wrapped_audit
        and np.array_equal(direct, heldout_owned)
        and direct_audit == heldout_authoritative_audit
        and heldout_mapping_audit["nonzero_full_sequence_offset_exercised"]
        and heldout_mapping_audit["mapped_full_oriented_source_indices"]
        == full_selected.tolist()
        and np.allclose(durations, expected_durations, atol=1e-12, rtol=0.0)
        and durations[-1] > len(heldout_selected) * sample_period_s
        and np.all(np.diff(traces) > 0.0)
        and direct_audit["sparse_selected_rows_treated_as_contiguous_five_ms_samples"] is False
    )
    return {
        "schema": "biospur-c2-sparse-quantile-orientation-uncertainty-gate-v1",
        "coverage_class": "EXECUTED_OWNER_LEVEL",
        "owner_call_path": [
            "orientation_uncertainty.physical_orientation_covariance",
            "pipeline_runtime.C2PipelineRuntime._physical_orientation_covariance",
            "heldout_evaluation.FrozenScientificHeldoutEvaluationOwner._orientation_covariance",
        ],
        "full_sequence_selected_source_indices": full_selected.tolist(),
        "heldout_local_source_indices": heldout_selected.tolist(),
        "observed_duration_s": durations.tolist(),
        "expected_full_sequence_observed_duration_s": expected_durations.tolist(),
        "three_adjacent_samples_duration_s": len(heldout_selected) * sample_period_s,
        "covariance_trace_rad2": traces.tolist(),
        "shared_training_runtime_owner_exactly_equal": bool(
            np.array_equal(direct, wrapped) and direct_audit == wrapped_audit
        ),
        "frozen_heldout_index_mapping_and_owner_exactly_equal": bool(
            np.array_equal(direct, heldout_owned)
            and direct_audit == heldout_authoritative_audit
            and heldout_mapping_audit["nonzero_full_sequence_offset_exercised"]
        ),
        "heldout_index_mapping_audit": heldout_mapping_audit,
        "pass": passed,
    }
