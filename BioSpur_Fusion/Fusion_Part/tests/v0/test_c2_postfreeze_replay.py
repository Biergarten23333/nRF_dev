from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from biospur_fusion.v0.c2_progressive.architecture_guard import C2ExecutionGuard
from biospur_fusion.v0.c2_progressive.functional_geometry import (
    EDGE_SPECS,
    aligned_pair,
)
from biospur_fusion.v0.c2_progressive.orientation import OrientedAction
from biospur_fusion.v0.c2_progressive.timebase import PersistentPairClockState
from tools.replay_c2_postfreeze_heading import (
    _compose_exact_rooted_pair_maps,
    _heading_chunks_from_exact_pair_map,
    _precompute_branch_independent_segment_values,
)
from tools.recompute_c2_nonhinge_prefix_likelihood import (
    _latest_causal_connection,
)


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN = WORKSPACE / "logs/c2_basis_progressive_20260829T102836Z"


def _settings() -> dict:
    seal = json.loads(
        (RUN / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json").read_text()
    )
    amendment = json.loads((WORKSPACE / seal["amendment"]["path"]).read_text())
    return amendment["effective_settings"]


def _oriented_fixture(settings: dict) -> tuple[OrientedAction, dict[str, str]]:
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    count = 900
    step_us = 5_000
    pelvis_time = 4_000_000_000 + np.arange(count, dtype=np.int64) * step_us
    phase = np.arange(count, dtype=float) * step_us * 1e-6
    gyro = np.column_stack((
        np.sin(2.3 * phase) + 0.2 * np.sin(7.1 * phase),
        np.cos(3.7 * phase + 0.2),
        0.5 * np.sin(5.1 * phase - 0.3),
    ))
    time_by_node = {}
    for node_index, node in enumerate(sorted(node_by_segment.values())):
        # Large absolute clock epochs differ per node, while every row still
        # represents the same synthetic capture instant.
        time_by_node[node] = pelvis_time + node_index * 173_000_000
    pelvis_node = node_by_segment["pelvis"]
    pelvis_shift = time_by_node[pelvis_node][0] - pelvis_time[0]
    time_by_node = {
        node: values - pelvis_shift for node, values in time_by_node.items()
    }
    nodes = tuple(time_by_node)
    identity = np.tile(np.array((1.0, 0.0, 0.0, 0.0)), (count, 1))
    action = str(settings["execution_contract"]["chronological_actions"][0])
    return OrientedAction(
        action=action,
        chronological_index=0,
        time_us_by_node=time_by_node,
        derived_boot_epoch_by_node={node: np.zeros(count, dtype=np.int64) for node in nodes},
        contiguous_span_id_by_node={node: np.zeros(count, dtype=np.int32) for node in nodes},
        acc_mps2_by_node={
            node: np.tile(np.array((0.0, 0.0, 9.80665)), (count, 1)) for node in nodes
        },
        gyro_rads_by_node={node: gyro.copy() for node in nodes},
        quat_world_sensor_wxyz_by_node={node: identity.copy() for node in nodes},
        gap_only_orientation_covariance_rad2_by_node={
            node: np.zeros((count, 3, 3)) for node in nodes
        },
        vqf_residual_bias_rad_s_by_node={node: np.zeros((count, 3)) for node in nodes},
        vqf_residual_bias_sigma_rad_s_by_node={node: np.full(count, 0.01) for node in nodes},
        vqf_rest_detected_by_node={node: np.zeros(count, dtype=bool) for node in nodes},
        audit={"fixture": "POSTFREEZE_PRODUCTION_PAIR_OWNER_EXACT_ROOT_COMPOSITION"},
    ), node_by_segment


def _production_pairs() -> tuple[
    dict,
    OrientedAction,
    dict[str, str],
    dict[str, object],
]:
    settings = _settings()
    oriented, node_by_segment = _oriented_fixture(settings)
    guard = C2ExecutionGuard(settings)
    guard.begin_capture("C2")
    clock = PersistentPairClockState(
        maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
        jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
    )
    clock.observe_episode_node_grids(
        action=oriented.action,
        chronological_index=0,
        root_node=node_by_segment["pelvis"],
        time_us_by_node=oriented.time_us_by_node,
        boot_epoch_by_node=oriented.derived_boot_epoch_by_node,
        contiguous_span_id_by_node=oriented.contiguous_span_id_by_node,
    )
    pairs = {
        edge: aligned_pair(
            oriented,
            edge=edge,
            parent_node=node_by_segment[parent],
            child_node=node_by_segment[child],
            timing=settings["timing"],
            clock_state=clock,
            execution_guard=guard,
        )
        for edge, parent, child in EDGE_SPECS
    }
    return settings, oriented, node_by_segment, pairs


def test_replay_uses_production_pair_owner_and_exact_rooted_composition() -> None:
    settings, oriented, node_by_segment, pairs = _production_pairs()
    pelvis_rows = len(oriented.time_us_by_node[node_by_segment["pelvis"]])
    maps, timing_variance, common_root, audit = _compose_exact_rooted_pair_maps(
        pair_by_edge=pairs,
        edge_specs=EDGE_SPECS,
        root_row_count=pelvis_rows,
        root_clock_sigma_s=float(settings["physical_candidates"]["root_clock_sigma_s"]),
    )
    assert len(common_root) >= int(settings["timing"]["minimum_contiguous_span_rows"])
    assert set(maps) == set(node_by_segment)
    assert all(np.isfinite(value) and value > 0.0 for value in timing_variance.values())
    assert audit["pair_owner"] == "functional_geometry.aligned_pair"
    assert audit["same_action_fractional_anchor_mapping_used"] is False
    assert audit["nearest_raw_timer_matching_used"] is False
    for edge, parent, child in EDGE_SPECS:
        pair = pairs[edge]
        chunks, chunk_audit = _heading_chunks_from_exact_pair_map(
            pair=pair,
            parent_root_map=maps[parent],
            child_root_map=maps[child],
            minimum_rows=int(settings["timing"]["minimum_contiguous_span_rows"]),
        )
        assert chunks
        assert chunk_audit["all_retained_source_and_pelvis_indices_unit_contiguous"]
        assert chunk_audit["pair_contiguous_span_boundaries_crossed"] == 0
        assert chunk_audit["nearest_or_interpolated_row_created"] is False


def test_replay_drops_exact_mapping_mismatch_instead_of_projecting_a_row() -> None:
    settings, oriented, node_by_segment, pairs = _production_pairs()
    maps, _, _, _ = _compose_exact_rooted_pair_maps(
        pair_by_edge=pairs,
        edge_specs=EDGE_SPECS,
        root_row_count=len(oriented.time_us_by_node[node_by_segment["pelvis"]]),
        root_clock_sigma_s=float(settings["physical_candidates"]["root_clock_sigma_s"]),
    )
    edge, parent, child = EDGE_SPECS[0]
    pair = pairs[edge]
    mutated_child_map = dict(maps[child])
    selected_root = sorted(mutated_child_map)[len(mutated_child_map) // 2]
    mutated_child_map[selected_root] += 1
    chunks, audit = _heading_chunks_from_exact_pair_map(
        pair=pair,
        parent_root_map=maps[parent],
        child_root_map=mutated_child_map,
        minimum_rows=int(settings["timing"]["minimum_contiguous_span_rows"]),
    )
    retained_roots = np.concatenate([row[2] for row in chunks])
    assert selected_root not in set(retained_roots.tolist())
    assert audit["nearest_or_interpolated_row_created"] is False


def test_orientation_covariance_owner_is_called_ten_times_not_once_per_branch() -> None:
    segments = tuple(f"segment_{index}" for index in range(10))
    calls: list[str] = []

    def owner(segment: str) -> np.ndarray:
        calls.append(segment)
        return np.eye(3) * (len(calls) + 1.0)

    precomputed = _precompute_branch_independent_segment_values(segments, owner)
    for _branch_index in range(4):
        assert all(precomputed[segment].shape == (3, 3) for segment in segments)
    assert calls == list(segments)
    assert len(calls) == 10


def test_causal_connection_uses_latest_checkpoint_without_future_backflow() -> None:
    covariance_03 = np.diag(np.arange(1.0, 7.0))
    covariance_08 = covariance_03 * 2.0
    arrays = {
        "geometry_checkpoint/03/center/pelvis_torso/mean": np.array(
            (0.1, -0.2, 0.3, -0.4, 0.5, -0.6), dtype=float,
        ),
        "geometry_checkpoint/03/center/pelvis_torso/covariance": covariance_03,
        "geometry_checkpoint/08/center/pelvis_torso/mean": np.ones(6),
        "geometry_checkpoint/08/center/pelvis_torso/covariance": covariance_08,
    }
    missing, missing_audit = _latest_causal_connection(
        arrays,
        chronological_index=2,
        edge="pelvis_torso",
        parent="pelvis",
        child="torso",
    )
    assert missing is None
    assert missing_audit["future_geometry_checkpoint_substituted"] is False
    connection, audit = _latest_causal_connection(
        arrays,
        chronological_index=7,
        edge="pelvis_torso",
        parent="pelvis",
        child="torso",
    )
    assert connection is not None
    assert audit["checkpoint_index"] == 3
    assert np.array_equal(
        connection.parent_sensor_to_joint_m, np.array((-0.1, 0.2, -0.3)),
    )
    assert np.array_equal(
        connection.child_sensor_to_joint_m, np.array((0.4, -0.5, 0.6)),
    )
    assert np.array_equal(connection.covariance_m2, covariance_03)
    assert audit["final_frames_substituted"] is False
