#!/usr/bin/env python3
"""Recompute nonhinge prefixes from persisted training-only replay arrays.

No payload is opened. The tool rebuilds the exact saved AlignedPair rows and
uses the Rao-Blackwellized dynamic-heading/shared-nuisance owner. Prefix-local
connection ownership must be resolved before this entrypoint may execute.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping

sys.dont_write_bytecode = True

import numpy as np


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"
SPRINT = RUN / "CONTINUATION_SPRINT"
SOURCE_REPLAY = SPRINT / "C2_NONHINGE_TRAINING_REPLAY_001"
SOURCE_NPZ = SOURCE_REPLAY / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
SETTINGS_PATH = RUN / "P2_PREFIT_ACTIVE_PARAMETER_REGISTRY_AMENDMENT_021_SOURCE_CORRECTION_001.json"
OUT = SPRINT / "C2_NONHINGE_JOINT_RAO_REPLAY_002"
PREFIX_COUNT = 19


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _write_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(
            handle,
            **{key: np.asarray(value) for key, value in sorted(arrays.items())},
        )
    path.chmod(0o444)


def _pair(
    arrays: Mapping[str, np.ndarray],
    *,
    index: int,
    action: str,
    edge: str,
    parent_node: str,
    child_node: str,
    alignment_report: Mapping[str, Any],
):
    from biospur_fusion.v0.c2_progressive.functional_geometry import AlignedPair
    from biospur_fusion.v0.c2_progressive.timebase import PairAlignment

    prefix = f"replay_input/{index:02d}/{edge}"
    parent_indices = np.asarray(
        arrays[f"{prefix}/parent_source_indices"], dtype=np.int64,
    )
    child_indices = np.asarray(
        arrays[f"{prefix}/child_source_indices"], dtype=np.int64,
    )
    spans = tuple(
        slice(int(start), int(stop))
        for start, stop in np.asarray(
            arrays[f"{prefix}/contiguous_span_half_open"], dtype=np.int64,
        )
    )

    def source(node: str, field: str) -> np.ndarray:
        return np.asarray(arrays[f"orientation/{index:02d}/{node}/{field}"])

    parent_time = source(parent_node, "time_us")
    child_time = source(child_node, "time_us")
    return AlignedPair(
        edge=edge,
        action=action,
        parent_acc=np.asarray(source(parent_node, "acc_mps2")[parent_indices], dtype=float),
        child_acc=np.asarray(source(child_node, "acc_mps2")[child_indices], dtype=float),
        parent_gyro=np.asarray(source(parent_node, "gyro_rads")[parent_indices], dtype=float),
        child_gyro=np.asarray(source(child_node, "gyro_rads")[child_indices], dtype=float),
        parent_observed_time_s=np.asarray(parent_time[parent_indices], dtype=float) * 1e-6,
        child_observed_time_s=np.asarray(child_time[child_indices], dtype=float) * 1e-6,
        parent_boot_epoch=np.asarray(
            source(parent_node, "derived_boot_epoch")[parent_indices], dtype=np.int64,
        ),
        child_boot_epoch=np.asarray(
            source(child_node, "derived_boot_epoch")[child_indices], dtype=np.int64,
        ),
        alignment=PairAlignment(
            parent_indices=parent_indices.copy(),
            child_indices=child_indices.copy(),
            lag_samples=int(alignment_report["selected_lag_samples"]),
            report=dict(alignment_report),
        ),
        contiguous_spans=spans,
        provenance={
            "source": "IMMUTABLE_TRAINING_ONLY_REPLAY_ARRAYS",
            "parent_source_indices_sha256": _array_sha(parent_indices),
            "child_source_indices_sha256": _array_sha(child_indices),
            "heldout": False,
        },
    )


def _latest_causal_connection(
    arrays: Mapping[str, np.ndarray],
    *,
    chronological_index: int,
    edge: str,
    parent: str,
    child: str,
) -> tuple[Any | None, Mapping[str, Any]]:
    """Resolve only an owner-exported geometry checkpoint at/before a prefix."""

    from biospur_fusion.v0.c2_progressive.segment_frames import (
        EdgeConnectionVectors,
    )

    checkpoints = sorted({
        int(key.split("/")[1])
        for key in arrays
        if key.startswith("geometry_checkpoint/")
        and "/center/" in key
        and key.endswith("/mean")
    })
    eligible = [
        value for value in checkpoints
        if value <= chronological_index
        and f"geometry_checkpoint/{value:02d}/center/{edge}/mean" in arrays
        and f"geometry_checkpoint/{value:02d}/center/{edge}/covariance" in arrays
    ]
    if not eligible:
        return None, {
            "status": "CAUSAL_CONNECTION_UNAVAILABLE_LOCAL_NO_UPDATE",
            "chronological_index": int(chronological_index),
            "edge": edge,
            "final_frames_substituted": False,
            "future_geometry_checkpoint_substituted": False,
        }
    checkpoint = eligible[-1]
    prefix = f"geometry_checkpoint/{checkpoint:02d}/center/{edge}"
    mean = np.asarray(arrays[f"{prefix}/mean"], dtype=float)
    covariance = np.asarray(arrays[f"{prefix}/covariance"], dtype=float)
    if mean.shape != (6,) or covariance.shape != (6, 6):
        raise RuntimeError(f"{edge}: causal center checkpoint shape changed")
    # CenterEstimate stores joint->sensor.  The connection owner consumes the
    # full-R3 sensor->joint levers; negating all six coordinates leaves the
    # full 6x6 covariance (including cross-blocks) unchanged.
    connection = EdgeConnectionVectors(
        edge=edge,
        parent=parent,
        child=child,
        parent_sensor_to_joint_m=-mean[:3],
        child_sensor_to_joint_m=-mean[3:],
        covariance_m2=covariance.copy(),
    )
    return connection, {
        "status": "CAUSAL_CONNECTION_AVAILABLE",
        "chronological_index": int(chronological_index),
        "checkpoint_index": int(checkpoint),
        "mean_sha256": _array_sha(mean),
        "covariance_sha256": _array_sha(covariance),
        "full_r3_parent_child_levers_retained": True,
        "full_6x6_cross_block_covariance_retained": True,
        "joint_to_sensor_negated_once_for_sensor_to_joint_owner": True,
        "final_frames_substituted": False,
        "future_geometry_checkpoint_substituted": False,
    }


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("prefix likelihood replay requires canonical Fusion_Part")
    from biospur_fusion.v0.c2_progressive.functional_geometry import (
        EDGE_SPECS,
        HINGE_EDGES,
    )
    from biospur_fusion.v0.c2_progressive.nonhinge_heading import (
        PersistentNonhingeHeadingLikelihoodOwner,
        edge_local_joint_acceleration_heading_log_likelihood,
    )
    from biospur_fusion.v0.c2_progressive.segment_frames import EdgeConnectionVectors

    settings_document = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    settings = settings_document["effective_settings"]
    initial_path = WORKSPACE / settings["execution_contract"][
        "initial_stochastic_state_relative_path"
    ]
    initial = json.loads(initial_path.read_text(encoding="utf-8"))
    replay_reports = [
        json.loads((SOURCE_REPLAY / f"REPLAY_{index:02d}.json").read_text(encoding="utf-8"))
        for index in range(PREFIX_COUNT)
    ]
    read_reports = [
        json.loads((SOURCE_REPLAY / f"READ_{index:02d}.json").read_text(encoding="utf-8"))
        for index in range(PREFIX_COUNT)
    ]
    branch_ids = tuple(str(value) for value in replay_reports[0]["branch_ids"])
    nonhinge_edges = tuple(
        edge for edge, _, _ in EDGE_SPECS if edge not in HINGE_EDGES
    )
    grid = np.deg2rad(np.arange(-180, 180, dtype=float))
    owner = PersistentNonhingeHeadingLikelihoodOwner(
        branch_ids=branch_ids,
        nonhinge_edges=nonhinge_edges,
        delta_grid_rad=grid,
        gap_diffusion_rad2_s=float(
            settings["heading"]["persistent_filter"]["gap_diffusion_rad2_s"]
        ),
        unknown_interval_variance_floor_rad2=float(
            settings["heading"]["persistent_filter"][
                "unknown_interval_variance_floor_rad2"
            ]
        ),
    )
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    output_arrays: dict[str, np.ndarray] = {}
    action_rows: list[dict[str, Any]] = []
    source_hash_before = _sha(SOURCE_NPZ)
    with np.load(SOURCE_NPZ, allow_pickle=False) as arrays:
        for index, (replay, read) in enumerate(zip(
            replay_reports, read_reports, strict=True,
        )):
            action = str(replay["action"])
            connections: dict[str, EdgeConnectionVectors] = {}
            connection_provenance: dict[str, Mapping[str, Any]] = {}
            for edge, parent, child in EDGE_SPECS:
                connection, provenance = _latest_causal_connection(
                    arrays,
                    chronological_index=index,
                    edge=edge,
                    parent=parent,
                    child=child,
                )
                connection_provenance[edge] = provenance
                if connection is not None:
                    connections[edge] = connection
            calibration_nodes = read["orientation_audit"]["nodes"]
            alignment_by_edge = replay["production_pair_clock_and_alignment"][
                "pair_alignment_reports"
            ]
            per_edge_statistics: dict[str, Any] = {}
            per_edge_pair: dict[str, Any] = {}
            for edge, parent, child in EDGE_SPECS:
                if edge in HINGE_EDGES:
                    continue
                parent_node = node_by_segment[parent]
                child_node = node_by_segment[child]
                pair = _pair(
                    arrays,
                    index=index,
                    action=action,
                    edge=edge,
                    parent_node=parent_node,
                    child_node=child_node,
                    alignment_report=alignment_by_edge[edge],
                )
                per_edge_pair[edge] = pair
                if edge not in connections:
                    per_edge_statistics[edge] = None
                    continue
                parent_prediction_row = calibration_nodes[parent_node][
                    "calibration_prediction"
                ]
                child_prediction_row = calibration_nodes[child_node][
                    "calibration_prediction"
                ]
                parent_prediction = parent_prediction_row[
                    "predictive_calibration_posterior"
                ]
                child_prediction = child_prediction_row[
                    "predictive_calibration_posterior"
                ]
                parent_indices = pair.alignment.parent_indices
                child_indices = pair.alignment.child_indices
                action_log, likelihood_report, statistics = (
                    edge_local_joint_acceleration_heading_log_likelihood(
                        pair=pair,
                        connection=connections[edge],
                        parent_quaternion_world_sensor_wxyz=np.asarray(
                            arrays[
                                f"orientation/{index:02d}/{parent_node}/"
                                "quat_world_sensor_wxyz"
                            ][parent_indices], dtype=float,
                        ),
                        child_quaternion_world_sensor_wxyz=np.asarray(
                            arrays[
                                f"orientation/{index:02d}/{child_node}/"
                                "quat_world_sensor_wxyz"
                            ][child_indices], dtype=float,
                        ),
                        delta_grid_rad=grid,
                        sample_period_s=float(settings["orientation"]["sample_period_s"]),
                        savgol_window_samples=int(
                            settings["joint_center"]["savgol_window_samples"]
                        ),
                        savgol_polynomial=int(
                            settings["joint_center"]["savgol_polynomial"]
                        ),
                        estimation_rate_hz=float(
                            settings["heading"]["explicit_est_settings"]["estimationRate"]
                        ),
                        effective_epoch_cap=int(
                            settings["heading"]["branch_evidence"][
                                "effective_epoch_cap_per_edge"
                            ]
                        ),
                        parent_accelerometer_covariance_m2_s4=(
                            np.asarray(initial["nodes"][parent_node][
                                "accelerometer_observation_covariance_m2_s4"
                            ], dtype=float)
                            + np.asarray(initial["nodes"][parent_node][
                                "accelerometer_quantization_variance_m2_s4"
                            ], dtype=float)
                        ),
                        child_accelerometer_covariance_m2_s4=(
                            np.asarray(initial["nodes"][child_node][
                                "accelerometer_observation_covariance_m2_s4"
                            ], dtype=float)
                            + np.asarray(initial["nodes"][child_node][
                                "accelerometer_quantization_variance_m2_s4"
                            ], dtype=float)
                        ),
                        parent_gyroscope_covariance_rad2_s2=(
                            np.asarray(initial["nodes"][parent_node][
                                "gyro_observation_covariance_rad2_s2"
                            ], dtype=float)
                            + np.asarray(initial["nodes"][parent_node][
                                "gyro_quantization_variance_rad2_s2"
                            ], dtype=float)
                        ),
                        child_gyroscope_covariance_rad2_s2=(
                            np.asarray(initial["nodes"][child_node][
                                "gyro_observation_covariance_rad2_s2"
                            ], dtype=float)
                            + np.asarray(initial["nodes"][child_node][
                                "gyro_quantization_variance_rad2_s2"
                            ], dtype=float)
                        ),
                        parent_gap_orientation_covariance_rad2=np.asarray(
                            arrays[
                                f"orientation/{index:02d}/{parent_node}/"
                                "gap_only_covariance_rad2"
                            ][parent_indices], dtype=float,
                        ),
                        child_gap_orientation_covariance_rad2=np.asarray(
                            arrays[
                                f"orientation/{index:02d}/{child_node}/"
                                "gap_only_covariance_rad2"
                            ][child_indices], dtype=float,
                        ),
                        parent_calibration_parameter_covariance=np.asarray(
                            parent_prediction["mixture_covariance"], dtype=float,
                        ),
                        child_calibration_parameter_covariance=np.asarray(
                            child_prediction["mixture_covariance"], dtype=float,
                        ),
                        parent_calibration_parameter_reference_mean=np.asarray(
                            parent_prediction_row["applied_mixture_mean"], dtype=float,
                        ),
                        child_calibration_parameter_reference_mean=np.asarray(
                            child_prediction_row["applied_mixture_mean"], dtype=float,
                        ),
                        noise_sigma_multiplier=float(
                            settings["joint_center"]["noise_sigma_multiplier"]
                        ),
                    )
                )
                per_edge_statistics[edge] = (action_log, likelihood_report, statistics)

            branch_rows = []
            for branch in branch_ids:
                for edge in nonhinge_edges:
                    statistics_row = per_edge_statistics[edge]
                    if statistics_row is None:
                        result = owner.record_action_no_update(
                            branch_id=branch,
                            edge=edge,
                            chronological_index=index,
                            action=action,
                            cause=(
                                "CAUSAL_PREFIX_CONNECTION_NOT_YET_OWNER_EXPORTED"
                            ),
                            timing_pair=per_edge_pair[edge],
                        )
                        statistics = None
                    else:
                        action_log, likelihood_report, statistics = statistics_row
                        result = owner.process(
                            branch_id=branch,
                            chronological_index=index,
                            likelihood_log_weights=action_log,
                            pair=per_edge_pair[edge],
                            likelihood_report=likelihood_report,
                            shared_nuisance_statistics=statistics,
                        )
                    prefix = f"nonhinge_heading/{index:02d}/{branch}/{edge}"
                    output_arrays[f"{prefix}/delta_grid_rad"] = result.delta_grid_rad
                    output_arrays[f"{prefix}/action_log_likelihood"] = (
                        result.action_log_likelihood
                    )
                    output_arrays[f"{prefix}/posterior_weights"] = result.posterior_weights
                    if statistics is not None:
                        output_arrays[f"{prefix}/heading_information"] = (
                            statistics.heading_information
                        )
                        output_arrays[f"{prefix}/shared_nuisance_score"] = (
                            statistics.shared_score
                        )
                        output_arrays[f"{prefix}/shared_nuisance_covariance"] = (
                            statistics.shared_covariance
                        )
                        output_arrays[f"{prefix}/nuisance_normal_j_t_w_j"] = (
                            statistics.nuisance_normal
                        )
                        output_arrays[f"{prefix}/nuisance_score_j_t_w_r"] = (
                            statistics.nuisance_score
                        )
                        output_arrays[f"{prefix}/residual_quadratic_r_t_w_r"] = (
                            statistics.residual_quadratic
                        )
                        output_arrays[f"{prefix}/independent_covariance_logdet"] = (
                            statistics.independent_covariance_log_determinant
                        )
                        output_arrays[f"{prefix}/shared_nuisance_reference_mean"] = (
                            statistics.shared_nuisance_reference_mean
                        )
                    branch_rows.append({
                        "branch_id": branch,
                        "edge": edge,
                        "report": result.report,
                    })
            action_rows.append({
                "chronological_index": index,
                "action": action,
                "nonhinge_reports": branch_rows,
                "connection_provenance_by_edge": connection_provenance,
                "one_likelihood_evaluation_per_edge_reused_across_branches": True,
            })
            print(json.dumps({"completed_prefix": index, "action": action}), flush=True)

    if _sha(SOURCE_NPZ) != source_hash_before:
        raise RuntimeError("source replay NPZ changed during derived recomputation")
    OUT.mkdir(parents=True, exist_ok=False)
    npz_path = OUT / "CORRECTED_PREFIX_NONHINGE_STATE.npz"
    _write_npz(npz_path, output_arrays)
    audit = {
        "schema": "biospur-c2-nonhinge-joint-rao-replay-v1",
        "source_replay_npz": {"path": str(SOURCE_NPZ), "sha256": source_hash_before},
        "source_replay_audit": {
            "path": str(SOURCE_REPLAY / "REPLAY_AUDIT.json"),
            "sha256": _sha(SOURCE_REPLAY / "REPLAY_AUDIT.json"),
        },
        "source_floor_ablation": {
            "path": str(SPRINT / "C2_NONHINGE_SHARED_FLOOR_ABLATION_001/AUDIT.json"),
            "sha256": _sha(SPRINT / "C2_NONHINGE_SHARED_FLOOR_ABLATION_001/AUDIT.json"),
        },
        "corrected_npz": {"path": str(npz_path), "sha256": _sha(npz_path)},
        "prefix_count": PREFIX_COUNT,
        "action_rows": action_rows,
        "shared_nuisance_treatment": (
            "RAO_BLACKWELLIZED_CIRCULAR_DYNAMIC_HEADING_WITH_ONE_"
            "CAPTURE_WIDE_CONDITIONAL_GAUSSIAN_NUISANCE_STATE"
        ),
        "persisted_sufficient_statistics": {
            "A": "nuisance_normal_j_t_w_j",
            "b": "nuisance_score_j_t_w_r",
            "q": "residual_quadratic_r_t_w_r",
            "candidate_logdet": "independent_covariance_logdet",
            "local_absolute_basis_reference": "shared_nuisance_reference_mean",
        },
        "joint_linear_gaussian_nuisance_marginal_performed": True,
        "joint_irls_nuisance_marginal_performed": False,
        "pre_nuisance_one_step_irls_used_for_owner_update": False,
        "registered_robust_action_diagnostic_retained": True,
        "owner_output_qualified_as_pose_posterior": False,
        "bounded_robust_treatment": (
            "GAP_SAFE_BLOCK_MEDIANS_THEN_GAUSSIAN_SHARED_NUISANCE_MARGINAL"
        ),
        "candidate_log_determinant_included": True,
        "chronological_heading_diffusion_applied_between_actions": True,
        "nonzero_heading_transition_conditional_nuisance_approximation": (
            "FIRST_AND_SECOND_MOMENT_MATCH_PER_S1_CELL"
        ),
        "covariance_owner": (
            "ACTUAL_GENERATIVE_WHITE_CANDIDATE_COVARIANCE_PLUS_"
            "CANDIDATE_INDEPENDENT_EPISTEMIC_GAP_CLOCK_ENVELOPE_WITH_EXACT_LOGDET"
        ),
        "three_way_covariance_ablation_recorded_per_action": [
            "GLOBAL_ISOTROPIC_ENVELOPE",
            "PERMUTATION_INVARIANT_MEAN_CENTERED_LOEWNER_ENVELOPE",
            "ACTUAL_GENERATIVE_WHITE_PLUS_COMMON_EPISTEMIC_ENVELOPE_ACTIVE_OWNER",
            "RAW_HETEROSCEDASTIC_GAP_CLOCK_COVARIANCE_DIAGNOSTIC_ONLY",
        ],
        "zero_residual_covariance_only_information_separately_audited": True,
        "gap_or_clock_covariance_alignment_used_as_functional_motion_information": False,
        "final_connection_state_backflow_used": False,
        "causal_connection_owner": (
            "LATEST_EXACT_GEOMETRY_CHECKPOINT_AT_OR_BEFORE_ACTION"
        ),
        "connection_unavailable_before_owner_checkpoint": (
            "EXPLICIT_LOCAL_NO_UPDATE_WITH_EXACT_PAIR_TIME_ADVANCE"
        ),
        "no_update_elapsed_interval_double_diffused": False,
        "historical_max_low_sensitivity_floor_used": False,
        "payload_reread": False,
        "heldout_opened": False,
        "fit_qmt_or_progressive_recomputed": False,
        "hard_argmax_or_threshold_relaxation_used": False,
        "scientific_acceptance_pass": False,
    }
    audit_path = OUT / "AUDIT.json"
    _write_json(audit_path, audit)
    print(json.dumps({
        "audit": str(audit_path),
        "audit_sha256": _sha(audit_path),
        "npz": str(npz_path),
        "npz_sha256": _sha(npz_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
