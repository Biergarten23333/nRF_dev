#!/usr/bin/env python3
"""Reconstruct the C2 continuous frontend and replay official QMT heading.

This entrypoint reads only the immutable prefit training ranges.  It does not
run functional fitting, update RUN013, open held-out data, or alter causal
progressive scores.  The replay owns one persistent heading state across all
19 chronological actions and uses the final frozen RUN013 frame branches.
"""
from __future__ import annotations

import sys

sys.dont_write_bytecode = True

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import traceback
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation


WORKSPACE = Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part")
RUN_RELATIVE = Path("logs/c2_basis_progressive_20260829T102836Z")
SEAL_RELATIVE = RUN_RELATIVE / "P2_PREFIT_REGISTRY_SEAL_021_SOURCE_CORRECTION_001.json"
ACTIVATION_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_ACTIVATION_009.json"
BASE_SOURCE_DELTA_RELATIVE = (
    RUN_RELATIVE
    / "C2_REAL_DIAGNOSTIC_ACTIVATION_009_AUTHORIZED_SOURCE_DELTA_001_RUNTIME_BUGFIX_002.json"
)
FROZEN_MANIFEST_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.json"
FROZEN_NPZ_RELATIVE = RUN_RELATIVE / "C2_REAL_DIAGNOSTIC_FROZEN_STATE_013.npz"
SOURCE_DELTA_RELATIVE = (
    RUN_RELATIVE
    / "C2_NONHINGE_TRAINING_REPLAY_SOURCE_DELTA_001.json"
)
OUTPUT_RELATIVE = (
    RUN_RELATIVE / "CONTINUATION_SPRINT" / "C2_NONHINGE_TRAINING_REPLAY_001"
)
RETROSPECTIVE_SOURCE = (
    "POSTFREEZE_RECONSTRUCTION_EXISTING_PERSISTENT_HEADING_OWNER_OFFICIAL_QMT"
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _semantic_sha(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(np.asarray(value))
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def _array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode()
    return {
        "dtype": str(array.dtype), "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


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


def _write_new_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(_jsonable(value), handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
    path.chmod(0o444)


def _write_new_npz(path: Path, arrays: Mapping[str, np.ndarray]) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(
            handle,
            **{name: np.asarray(value) for name, value in sorted(arrays.items())},
        )
    path.chmod(0o444)


def _load_immutable(relative: Path) -> dict[str, Any]:
    path = (WORKSPACE / relative).resolve()
    path.relative_to(WORKSPACE)
    if not path.is_file() or path.stat().st_mode & 0o222:
        raise RuntimeError(f"immutable retrospective authority is absent or mutable: {relative}")
    return json.loads(path.read_text(encoding="utf-8"))


def _binding(relative: Path) -> dict[str, str]:
    return {"path": str(relative), "sha256": _sha(WORKSPACE / relative)}


def _validate_authority() -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    seal = _load_immutable(SEAL_RELATIVE)
    amendment_path = Path(seal["amendment"]["path"])
    amendment = _load_immutable(amendment_path)
    activation = _load_immutable(ACTIVATION_RELATIVE)
    base_delta = _load_immutable(BASE_SOURCE_DELTA_RELATIVE)
    frozen = _load_immutable(FROZEN_MANIFEST_RELATIVE)
    source_delta = _load_immutable(SOURCE_DELTA_RELATIVE)
    settings = amendment["effective_settings"]
    expected_sources = source_delta.get("effective_source_hashes", {})
    source_checks = []
    for relative, expected in sorted(expected_sources.items()):
        path = (WORKSPACE / relative).resolve()
        path.relative_to(WORKSPACE)
        observed = _sha(path) if path.is_file() else None
        source_checks.append({
            "path": relative, "expected_sha256": expected,
            "observed_sha256": observed, "pass": observed == expected,
        })
    if (
        seal.get("schema") != "biospur-c2-p2-prefit-registry-seal-v2"
        or seal.get("amendment") != _binding(amendment_path)
        or seal.get("settings_semantic_sha256") != _semantic_sha(settings)
        or activation.get("schema")
        != "biospur-c2-real-training-range-diagnostic-activation-v1"
        or activation.get("activation_role") != "REAL_TRAINING_RANGE_DIAGNOSTIC_ONLY"
        or activation.get("execution_authorized") is not True
        or activation.get("training_ranges_only") is not True
        or activation.get("heldout_opened") is not False
        or frozen.get("schema")
        != "biospur-c2-reloadable-frozen-scientific-state-manifest-v1"
        or frozen.get("npz") != _binding(FROZEN_NPZ_RELATIVE)
        or frozen.get("heldout_opened_when_exported") is not False
        or source_delta.get("schema")
        != "biospur-c2-nonhinge-training-replay-source-delta-v1"
        or source_delta.get("parent_prefit_seal") != _binding(SEAL_RELATIVE)
        or source_delta.get("parent_diagnostic_activation") != _binding(ACTIVATION_RELATIVE)
        or source_delta.get("parent_authorized_source_delta")
        != _binding(BASE_SOURCE_DELTA_RELATIVE)
        or source_delta.get("parent_postfreeze_replay_source_delta")
        != _binding(
            RUN_RELATIVE
            / "C2_POSTFREEZE_RETROSPECTIVE_HEADING_SOURCE_DELTA_001_RUNTIME_BUGFIX_001.json"
        )
        or source_delta.get("parent_frozen_manifest") != _binding(FROZEN_MANIFEST_RELATIVE)
        or source_delta.get("parent_frozen_npz") != _binding(FROZEN_NPZ_RELATIVE)
        or source_delta.get("settings_semantic_sha256") != seal.get("settings_semantic_sha256")
        or source_delta.get("exact_19_prefit_training_ranges_reread_authorized") is not True
        or source_delta.get("heldout_opened") is not False
        or source_delta.get("fit_or_progressive_recomputation_authorized") is not False
        or source_delta.get("nonhinge_full_s1_likelihood_authorized") is not True
        or source_delta.get("calibrated_accelerometer_export_authorized") is not True
        or source_delta.get("new_seal_created") is not False
        or not source_checks
        or not all(row["pass"] for row in source_checks)
    ):
        raise RuntimeError("post-freeze retrospective authority/source closure is inconsistent")
    return settings, frozen, {"source_delta": source_delta, "source_checks": source_checks}


def _reconstruct_frame_branches(
    *,
    frozen: Mapping[str, Any],
    arrays: Mapping[str, np.ndarray],
) -> tuple[Any, ...]:
    from biospur_fusion.v0.c2_progressive.functional_geometry import EDGE_SPECS, HINGE_EDGES
    from biospur_fusion.v0.c2_progressive.segment_frames import (
        EdgeConnectionVectors,
        SegmentFrameBranch,
    )

    branch_rows = {
        str(row["branch_id"]): row for row in frozen["structure"]["frame_branches"]
    }
    frozen_authority = frozen["structure"]["frozen_evaluation_authority"]
    branch_ids = tuple(str(value) for value in frozen_authority["branch_ids"])
    support = np.asarray(arrays["frozen/branch_hard_support"], dtype=bool)
    if support.shape != (len(branch_ids),):
        raise RuntimeError("frozen frame branch hard-support shape is invalid")
    output = []
    for branch_index, branch_id in enumerate(branch_ids):
        if not support[branch_index]:
            continue
        row = branch_rows[branch_id]
        prefix = f"frames/{branch_id}"
        segment_from_sensor = {
            segment: np.asarray(arrays[f"{prefix}/segment_from_sensor/{segment}"], dtype=float)
            for segment in row["segments"]
        }
        connections = {
            edge: EdgeConnectionVectors(
                edge=edge,
                parent=parent,
                child=child,
                parent_sensor_to_joint_m=np.asarray(
                    arrays[f"{prefix}/connection/{edge}/parent"], dtype=float,
                ),
                child_sensor_to_joint_m=np.asarray(
                    arrays[f"{prefix}/connection/{edge}/child"], dtype=float,
                ),
                covariance_m2=np.asarray(
                    arrays[f"{prefix}/connection/{edge}/covariance"], dtype=float,
                ),
            )
            for edge, parent, child in EDGE_SPECS
        }
        output.append(SegmentFrameBranch(
            branch_id=branch_id,
            axis_sign_by_edge={str(k): int(v) for k, v in row["axis_sign_by_edge"].items()},
            segment_from_sensor=segment_from_sensor,
            sensor_from_segment={
                segment: rotation.T for segment, rotation in segment_from_sensor.items()
            },
            joint_frame_tangent_covariance_rad2=np.asarray(
                arrays[f"{prefix}/joint_frame_covariance"], dtype=float,
            ),
            frame_tangent_covariance_rad2={
                segment: np.asarray(
                    arrays[f"{prefix}/frame_covariance/{segment}"], dtype=float,
                ) for segment in row["segments"]
            },
            paired_hinge_frame_tangent_covariance_rad2={
                edge: np.asarray(
                    arrays[f"{prefix}/paired_hinge_frame_covariance/{edge}"], dtype=float,
                ) for edge in HINGE_EDGES
            },
            connection_vectors_by_edge=connections,
            prior_weight=float(row["prior_weight"]),
            wear_log_likelihood=float(row["wear_log_likelihood"]),
            wear_profile_log_likelihood={
                str(k): float(v) for k, v in row["wear_profile_log_likelihood"].items()
            },
            wear_gross_wrong_hemisphere=bool(row["wear_gross_wrong_hemisphere"]),
            retained=bool(row["retained"]),
            report={
                "source": "IMMUTABLE_RUN013_FINAL_FROZEN_FRAME_BRANCH",
                "retrospective_only": True,
            },
        ))
    if not output:
        raise RuntimeError("RUN013 has no hard-supported frozen frame branch")
    return tuple(output)


def _compose_exact_rooted_pair_maps(
    *,
    pair_by_edge: Mapping[str, Any],
    edge_specs: Sequence[tuple[str, str, str]],
    root_row_count: int,
    root_clock_sigma_s: float,
) -> tuple[
    dict[str, dict[int, int]],
    dict[str, float],
    np.ndarray,
    Mapping[str, Any],
]:
    """Compose the production pair rows into exact pelvis-index mappings.

    No timestamp interpolation or nearest-row projection is allowed here.  A
    child row exists on the viewer grid only when every rooted pair owner on
    its path supplies that exact parent-to-child index correspondence.
    """

    if root_row_count < 1:
        raise ValueError("rooted pair composition requires a nonempty pelvis grid")
    segment_maps: dict[str, dict[int, int]] = {
        "pelvis": {index: index for index in range(int(root_row_count))},
    }
    timing_variance_s2: dict[str, float] = {
        "pelvis": float(root_clock_sigma_s) ** 2,
    }
    edge_audits: dict[str, Any] = {}
    for edge, parent, child in edge_specs:
        pair = pair_by_edge.get(edge)
        parent_map = segment_maps.get(parent, {})
        if pair is None or not parent_map:
            segment_maps[child] = {}
            timing_variance_s2[child] = float("inf")
            edge_audits[edge] = {
                "status": "LOCAL_NO_UPDATE_MISSING_PAIR_OR_ROOTED_PARENT_MAP",
                "exact_rooted_correspondence_rows": 0,
            }
            continue
        parent_source = np.asarray(pair.alignment.parent_indices, dtype=np.int64)
        child_source = np.asarray(pair.alignment.child_indices, dtype=np.int64)
        if (
            parent_source.shape != child_source.shape
            or np.any(np.diff(parent_source) <= 0)
            or np.any(np.diff(child_source) <= 0)
        ):
            raise ValueError(f"{edge}: production aligned-pair indices are invalid")
        exact_pair_map = {
            int(parent_index): int(child_index)
            for parent_index, child_index in zip(parent_source, child_source, strict=True)
        }
        child_map = {
            int(root_index): exact_pair_map[int(parent_index)]
            for root_index, parent_index in parent_map.items()
            if int(parent_index) in exact_pair_map
        }
        segment_maps[child] = child_map
        lag_sigma_s = float(pair.alignment.report["lag_uncertainty_s"])
        timing_variance_s2[child] = timing_variance_s2[parent] + lag_sigma_s**2
        edge_audits[edge] = {
            "status": "EXACT_PRODUCTION_PAIR_ROWS_COMPOSED",
            "exact_rooted_correspondence_rows": len(child_map),
            "parent_source_indices_sha256": _array_sha(parent_source),
            "child_source_indices_sha256": _array_sha(child_source),
            "lag_uncertainty_s": lag_sigma_s,
            "nearest_timer_projection_used": False,
            "fractional_anchor_interpolation_used": False,
        }
    required_segments = {name for _, parent, child in edge_specs for name in (parent, child)}
    if set(segment_maps) != required_segments:
        raise RuntimeError("rooted exact-pair composition did not visit the ten segments")
    common_root = np.asarray(
        sorted(set.intersection(*(set(segment_maps[name]) for name in sorted(segment_maps)))),
        dtype=np.int64,
    )
    return segment_maps, timing_variance_s2, common_root, {
        "schema": "biospur-c2-postfreeze-exact-rooted-pair-row-composition-v1",
        "edge_rows": edge_audits,
        "common_pelvis_source_row_count": int(len(common_root)),
        "common_pelvis_source_indices_sha256": _array_sha(common_root),
        "same_action_fractional_anchor_mapping_used": False,
        "nearest_raw_timer_matching_used": False,
        "pair_owner": "functional_geometry.aligned_pair",
    }


def _heading_chunks_from_exact_pair_map(
    *,
    pair: Any,
    parent_root_map: Mapping[int, int],
    child_root_map: Mapping[int, int],
    minimum_rows: int,
) -> tuple[tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...], Mapping[str, Any]]:
    """Retain only pair rows that map exactly to one common pelvis row."""

    root_by_parent = {int(source): int(root) for root, source in parent_root_map.items()}
    chunks: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    for span in pair.contiguous_spans:
        local = np.arange(int(span.start), int(span.stop), dtype=np.int64)
        parent = np.asarray(pair.alignment.parent_indices[span], dtype=np.int64)
        child = np.asarray(pair.alignment.child_indices[span], dtype=np.int64)
        roots = np.asarray([root_by_parent.get(int(value), -1) for value in parent], dtype=np.int64)
        valid = np.asarray([
            root >= 0 and child_root_map.get(int(root)) == int(child_value)
            for root, child_value in zip(roots, child, strict=True)
        ], dtype=bool)
        if not np.any(valid):
            continue
        selected = np.flatnonzero(valid)
        breaks = np.flatnonzero(
            (np.diff(selected) != 1)
            | (np.diff(parent[selected]) != 1)
            | (np.diff(child[selected]) != 1)
            | (np.diff(roots[selected]) != 1)
        ) + 1
        boundaries = np.r_[0, breaks, len(selected)]
        for left, right in zip(boundaries[:-1], boundaries[1:], strict=True):
            positions = selected[left:right]
            if len(positions) >= int(minimum_rows):
                chunks.append((
                    parent[positions].copy(),
                    child[positions].copy(),
                    roots[positions].copy(),
                ))
    parent_all = np.concatenate([row[0] for row in chunks]) if chunks else np.empty(0, dtype=np.int64)
    child_all = np.concatenate([row[1] for row in chunks]) if chunks else np.empty(0, dtype=np.int64)
    root_all = np.concatenate([row[2] for row in chunks]) if chunks else np.empty(0, dtype=np.int64)
    return tuple(chunks), {
        "schema": "biospur-c2-postfreeze-heading-exact-pair-chunks-v1",
        "edge": str(pair.edge),
        "chunk_count": len(chunks),
        "chunk_lengths": [len(row[0]) for row in chunks],
        "parent_source_indices_sha256": _array_sha(parent_all),
        "child_source_indices_sha256": _array_sha(child_all),
        "pelvis_source_indices_sha256": _array_sha(root_all),
        "all_retained_source_and_pelvis_indices_unit_contiguous": bool(
            all(
                np.all(np.diff(row[0]) == 1)
                and np.all(np.diff(row[1]) == 1)
                and np.all(np.diff(row[2]) == 1)
                for row in chunks
            )
        ),
        "pair_contiguous_span_boundaries_crossed": 0,
        "nearest_or_interpolated_row_created": False,
    }


def _precompute_branch_independent_segment_values(
    segments: Sequence[str],
    owner: Any,
) -> dict[str, Any]:
    """Evaluate a branch-independent owner exactly once per segment."""

    ordered = tuple(str(segment) for segment in segments)
    if len(ordered) != len(set(ordered)):
        raise ValueError("branch-independent segment precompute requires unique segments")
    return {segment: owner(segment) for segment in ordered}


def main() -> int:
    if Path.cwd().resolve() != WORKSPACE:
        raise RuntimeError("retrospective replay requires canonical Fusion_Part")
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.dont_write_bytecode = True
    output = (WORKSPACE / OUTPUT_RELATIVE).resolve()
    output.relative_to(WORKSPACE / "logs")
    output.mkdir(parents=True, exist_ok=False)
    tmp = output / "tmp"
    tmp.mkdir()
    os.environ["TMPDIR"] = str(tmp)
    running_path = output / "RUNNING.json"
    terminal_path = output / "REPLAY_AUDIT.json"
    started = _now()
    progress: dict[str, Any] = {
        "stage": "AUTHORITY_BEFORE_TRAINING_RANGE_REREAD",
        "actions_read": 0,
        "actions_replayed": 0,
        "heldout_opened": False,
    }
    try:
        settings, frozen, authority = _validate_authority()
        initial_relative = Path(
            settings["execution_contract"]["initial_stochastic_state_relative_path"]
        )
        initial = _load_immutable(initial_relative)
        if (
            _sha(WORKSPACE / initial_relative)
            != settings["execution_contract"]["initial_stochastic_state_file_sha256"]
            or _semantic_sha(initial)
            != settings["execution_contract"]["initial_stochastic_state_semantic_sha256"]
        ):
            raise RuntimeError("retrospective initial stochastic authority changed")
        _write_new_json(running_path, {
            "schema": "biospur-c2-postfreeze-retrospective-heading-running-v1",
            "started_local": started,
            "source_delta": _binding(SOURCE_DELTA_RELATIVE),
            "parent_frozen_manifest": _binding(FROZEN_MANIFEST_RELATIVE),
            "parent_frozen_npz": _binding(FROZEN_NPZ_RELATIVE),
            "training_ranges_only": True,
            "heldout_opened": False,
            "fit_or_progressive_recomputation": False,
            "execution_role": "POSTFREEZE_RETROSPECTIVE_HEADING_RECONSTRUCTION",
        })

        from biospur_fusion.v0.c2_progressive.architecture_guard import C2ExecutionGuard
        from biospur_fusion.v0.c2_progressive.functional_geometry import (
            EDGE_SPECS,
            HINGE_EDGES,
            aligned_pair as build_aligned_pair,
        )
        from biospur_fusion.v0.c2_progressive.heading import PersistentHeadingOwner
        from biospur_fusion.v0.c2_progressive.nonhinge_heading import (
            PersistentNonhingeHeadingLikelihoodOwner,
            edge_local_joint_acceleration_heading_log_likelihood,
        )
        from biospur_fusion.v0.c2_progressive.orientation import ContinuousVQFState
        from biospur_fusion.v0.c2_progressive.orientation_uncertainty import (
            physical_orientation_covariance,
        )
        from biospur_fusion.v0.c2_progressive.pipeline_runtime import (
            _owner_authenticated_orientation_replay_arrays,
        )
        from biospur_fusion.v0.c2_progressive.quaternion_contract import (
            qmt_wxyz_to_scipy_active,
        )
        from biospur_fusion.v0.c2_progressive.range_reader import SealedPrefitRangeReader
        from biospur_fusion.v0.c2_progressive.timebase import PersistentPairClockState

        execution = settings["execution_contract"]
        chronology = tuple(str(value) for value in execution["chronological_actions"])
        node_by_segment = {
            str(row["body_segment"]): str(row["hardware_id"])
            for row in settings["segment_frames"]["wear_authority"]["rows"]
        }
        nodes = tuple(node_by_segment.values())
        guard = C2ExecutionGuard(settings)
        guard.begin_capture("C2")
        orientation = ContinuousVQFState(
            initial,
            execution_guard=guard,
            sample_period_s=float(settings["orientation"]["sample_period_s"]),
            unknown_boot_orientation_sigma_rad=float(
                settings["orientation"]["unknown_boot_orientation_sigma_rad"]
            ),
            unknown_unusable_episode_orientation_sigma_rad=float(
                settings["orientation"]["unknown_unusable_episode_orientation_sigma_rad"]
            ),
            calibration_settings=settings["calibration_posterior"],
        )
        plan_relative = Path(execution["payload_byte_access_plan_relative_path"])
        reader = SealedPrefitRangeReader(
            root=WORKSPACE,
            plan_path=WORKSPACE / plan_relative,
            expected_plan_sha256=execution["payload_byte_access_plan_sha256"],
            nodes=nodes,
        )
        oriented_actions = []
        clock = PersistentPairClockState(
            maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
            jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
        )
        pairs_by_action: list[dict[str, Any]] = []
        pair_audits_by_action: list[dict[str, Any]] = []
        replay_arrays: dict[str, np.ndarray] = {}
        read_bindings = []
        for action_index, action in enumerate(chronology):
            progress.update(stage="SEALED_TRAINING_RANGE_FRONTEND_RECONSTRUCTION", action=action)
            decoded = reader.read_action(action_index)
            if decoded.action != action:
                raise RuntimeError("retrospective range-reader chronology mismatch")
            oriented = orientation.process(decoded)
            oriented_actions.append(oriented)
            replay_arrays.update(_owner_authenticated_orientation_replay_arrays(oriented))
            read_path = output / f"READ_{action_index:02d}.json"
            _write_new_json(read_path, {
                "schema": "biospur-c2-postfreeze-retrospective-per-action-read-v1",
                "reader_session_id": reader.reader_session_id,
                "chronological_index": action_index,
                "action": action,
                "access_audit": decoded.access_audit,
                "decode_audit": decoded.decode_audit,
                "orientation_audit": oriented.audit,
                "training_ranges_only": True,
                "heldout_opened": False,
            })
            read_bindings.append({
                "path": str(read_path.relative_to(WORKSPACE)), "sha256": _sha(read_path),
            })
            progress["actions_read"] = action_index + 1
            root_node = node_by_segment["pelvis"]
            node_clock_update = clock.observe_episode_node_grids(
                action=action,
                chronological_index=action_index,
                root_node=root_node,
                time_us_by_node=oriented.time_us_by_node,
                boot_epoch_by_node=oriented.derived_boot_epoch_by_node,
                contiguous_span_id_by_node=oriented.contiguous_span_id_by_node,
            )
            pair_by_edge: dict[str, Any] = {}
            pair_failures: list[dict[str, Any]] = []
            for edge, parent, child in EDGE_SPECS:
                clock_checkpoint = clock.checkpoint()
                guard_checkpoint = guard.checkpoint()
                try:
                    pair_by_edge[edge] = build_aligned_pair(
                        oriented,
                        edge=edge,
                        parent_node=node_by_segment[parent],
                        child_node=node_by_segment[child],
                        timing=settings["timing"],
                        clock_state=clock,
                        execution_guard=guard,
                    )
                except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError) as exc:
                    clock.restore(clock_checkpoint)
                    guard.restore(guard_checkpoint)
                    pair_failures.append({
                        "edge": edge,
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc),
                        "clock_and_guard_rolled_back": True,
                        "later_heading_action": "EXPLICIT_EDGE_LOCAL_NO_UPDATE",
                    })
            pairs_by_action.append(pair_by_edge)
            for edge, pair in pair_by_edge.items():
                pair_prefix = f"replay_input/{action_index:02d}/{edge}"
                replay_arrays[f"{pair_prefix}/parent_source_indices"] = np.asarray(
                    pair.alignment.parent_indices, dtype=np.int64,
                )
                replay_arrays[f"{pair_prefix}/child_source_indices"] = np.asarray(
                    pair.alignment.child_indices, dtype=np.int64,
                )
                replay_arrays[f"{pair_prefix}/contiguous_span_half_open"] = np.asarray(
                    [[span.start, span.stop] for span in pair.contiguous_spans],
                    dtype=np.int64,
                )
            pair_audits_by_action.append({
                "node_clock_update": node_clock_update,
                "pair_provenance": {
                    edge: dict(pair.provenance) for edge, pair in pair_by_edge.items()
                },
                "pair_alignment_reports": {
                    edge: dict(pair.alignment.report) for edge, pair in pair_by_edge.items()
                },
                "ordinary_pair_failures": pair_failures,
                "pair_clock_checkpoint_semantic_sha256": _semantic_sha(clock.checkpoint()),
            })

        with np.load(WORKSPACE / FROZEN_NPZ_RELATIVE, allow_pickle=False) as archive:
            frozen_arrays = {name: np.asarray(archive[name]).copy() for name in archive.files}
        for name, value in frozen_arrays.items():
            replay_arrays.setdefault(name, value)
        frontend_arrays = {
            name: value for name, value in replay_arrays.items()
            if name.startswith("orientation/") or name.startswith("replay_input/")
        }
        frontend_npz_path = output / "FRONTEND_RECONSTRUCTION_INPUTS.npz"
        _write_new_npz(frontend_npz_path, frontend_arrays)
        frontend_manifest_path = output / "FRONTEND_RECONSTRUCTION_INPUTS.json"
        _write_new_json(frontend_manifest_path, {
            "schema": "biospur-c2-postfreeze-retrospective-frontend-reconstruction-v1",
            "created_local": _now(),
            "reader_session_id": reader.reader_session_id,
            "chronological_actions": list(chronology),
            "per_action_read_audits": read_bindings,
            "pair_clock_and_alignment_audits": pair_audits_by_action,
            "final_reconstructed_pair_clock_checkpoint": clock.checkpoint(),
            "npz": {
                "path": str(frontend_npz_path.relative_to(WORKSPACE)),
                "sha256": _sha(frontend_npz_path),
            },
            "array_bindings": {
                name: _array_binding(value) for name, value in sorted(frontend_arrays.items())
            },
            "calibrated_gyro_and_continuous_quaternion_rows_persisted": True,
            "exact_pair_source_indices_and_span_boundaries_persisted": True,
            "frontend_reconstruction_applies_frozen_final_calibration_posterior_backwards": False,
            "frontend_reconstruction_role": (
                "CHRONOLOGICAL_RECONSTRUCTION_OF_ESTABLISHED_CAPTURE_WIDE_POSTERIOR"
            ),
            "future_resume_requires_payload_reread": False,
            "heldout_opened": False,
            "fit_geometry_or_progressive_recomputed": False,
        })
        branches = _reconstruct_frame_branches(frozen=frozen, arrays=frozen_arrays)
        heading_guard = C2ExecutionGuard(settings)
        heading_guard.begin_capture("C2")
        heading = PersistentHeadingOwner(
            settings["heading"], branches,
            execution_guard=heading_guard,
            first_chronological_index=0,
        )
        nonhinge_edges = tuple(
            edge for edge, _, _ in EDGE_SPECS if edge not in HINGE_EDGES
        )
        nonhinge_grid = np.deg2rad(np.arange(-180, 180, dtype=float))
        nonhinge_heading = PersistentNonhingeHeadingLikelihoodOwner(
            branch_ids=tuple(branch.branch_id for branch in branches),
            nonhinge_edges=nonhinge_edges,
            delta_grid_rad=nonhinge_grid,
            gap_diffusion_rad2_s=float(
                settings["heading"]["persistent_filter"]["gap_diffusion_rad2_s"]
            ),
            unknown_interval_variance_floor_rad2=float(
                settings["heading"]["persistent_filter"][
                    "unknown_interval_variance_floor_rad2"
                ]
            ),
        )
        minimum_rows = max(3, int(settings["timing"]["minimum_contiguous_span_rows"]))
        root_clock_sigma_s = float(settings["physical_candidates"]["root_clock_sigma_s"])
        replay_support: dict[str, Any] = {}
        action_audit_bindings = []
        for action_index, oriented in enumerate(oriented_actions):
            progress.update(stage="OFFICIAL_PERSISTENT_QMT_RETROSPECTIVE_REPLAY", action=oriented.action)
            root_node = node_by_segment["pelvis"]
            root_time_s = np.asarray(oriented.time_us_by_node[root_node], dtype=float) * 1e-6
            if len(root_time_s) < 3 or np.any(np.diff(root_time_s) <= 0.0):
                raise RuntimeError(f"{oriented.action}: retrospective pelvis grid is unusable")
            pair_by_edge = pairs_by_action[action_index]
            (
                exact_segment_maps,
                segment_timing_variance_s2,
                common_root_indices,
                rooted_pair_audit,
            ) = _compose_exact_rooted_pair_maps(
                pair_by_edge=pair_by_edge,
                edge_specs=EDGE_SPECS,
                root_row_count=len(root_time_s),
                root_clock_sigma_s=root_clock_sigma_s,
            )
            chunks_by_edge: dict[str, tuple[tuple[np.ndarray, np.ndarray, np.ndarray], ...]] = {}
            chunk_audits: dict[str, Any] = {}
            for edge, parent, child in EDGE_SPECS:
                pair = pair_by_edge.get(edge)
                if pair is None:
                    chunks_by_edge[edge] = ()
                    chunk_audits[edge] = {
                        "schema": "biospur-c2-postfreeze-heading-exact-pair-chunks-v1",
                        "edge": edge,
                        "chunk_count": 0,
                        "cause": "PRODUCTION_PAIR_OWNER_LOCAL_NO_UPDATE",
                        "nearest_or_interpolated_row_created": False,
                    }
                    continue
                chunks, audit = _heading_chunks_from_exact_pair_map(
                    pair=pair,
                    parent_root_map=exact_segment_maps[parent],
                    child_root_map=exact_segment_maps[child],
                    minimum_rows=minimum_rows,
                )
                chunks_by_edge[edge] = chunks
                chunk_audits[edge] = audit
            display_root_indices = np.empty(0, dtype=np.int64)
            display_source_indices: dict[str, np.ndarray] = {}
            display_world_from_sensor: dict[str, np.ndarray] = {}
            display_owner_covariance: dict[str, np.ndarray] = {}
            display_owner_covariance_audit: dict[str, Any] = {}
            display_timing_sigma_s: dict[str, np.ndarray] = {}
            if len(common_root_indices) >= 3:
                quantiles = np.asarray(
                    settings["scientific_renderer"]["sample_quantiles"], dtype=float,
                )
                selected_positions = np.unique(np.rint(
                    quantiles * (len(common_root_indices) - 1)
                ).astype(np.int64))
                display_root_indices = np.asarray(
                    common_root_indices[selected_positions], dtype=np.int64,
                )
                def build_segment_display_inputs(segment: str) -> Mapping[str, Any]:
                    node = node_by_segment[segment]
                    source_indices = np.asarray([
                        exact_segment_maps[segment][int(index)]
                        for index in display_root_indices
                    ], dtype=np.int64)
                    timing_sigma_s = np.full(
                        len(source_indices),
                        np.sqrt(segment_timing_variance_s2[segment]),
                        dtype=float,
                    )
                    covariance, covariance_audit = physical_orientation_covariance(
                        oriented_actions,
                        current_action_index=action_index,
                        segment=segment,
                        node=node,
                        source_indices=source_indices,
                        timing_sigma_s=timing_sigma_s,
                        initial_stochastic_state=initial,
                        initial_stochastic_state_semantic_sha256=(
                            execution["initial_stochastic_state_semantic_sha256"]
                        ),
                        orientation_settings=settings["orientation"],
                        uncertainty_settings=settings["physical_candidates"][
                            "orientation_uncertainty"
                        ],
                    )
                    return {
                        "source_indices": source_indices,
                        "world_from_sensor": qmt_wxyz_to_scipy_active(
                            oriented.quat_world_sensor_wxyz_by_node[node][source_indices]
                        ).as_matrix(),
                        "covariance": covariance,
                        "covariance_audit": covariance_audit,
                        "timing_sigma_s": timing_sigma_s,
                    }

                branch_independent = _precompute_branch_independent_segment_values(
                    tuple(node_by_segment), build_segment_display_inputs,
                )
                display_source_indices = {
                    segment: np.asarray(row["source_indices"], dtype=np.int64)
                    for segment, row in branch_independent.items()
                }
                display_world_from_sensor = {
                    segment: np.asarray(row["world_from_sensor"], dtype=float)
                    for segment, row in branch_independent.items()
                }
                display_owner_covariance = {
                    segment: np.asarray(row["covariance"], dtype=float)
                    for segment, row in branch_independent.items()
                }
                display_owner_covariance_audit = {
                    segment: row["covariance_audit"]
                    for segment, row in branch_independent.items()
                }
                display_timing_sigma_s = {
                    segment: np.asarray(row["timing_sigma_s"], dtype=float)
                    for segment, row in branch_independent.items()
                }
            action_failures = []
            action_span_reports = []
            action_nonhinge_reports = []
            action_branch_rows = {}
            for branch in branches:
                for edge, parent, child in EDGE_SPECS:
                    if edge in HINGE_EDGES:
                        continue
                    pair = pair_by_edge.get(edge)
                    shared_nuisance_statistics = None
                    if pair is None:
                        result = nonhinge_heading.record_action_no_update(
                            branch_id=branch.branch_id,
                            edge=edge,
                            chronological_index=action_index,
                            action=oriented.action,
                            cause="PRODUCTION_PAIR_OWNER_LOCAL_NO_UPDATE",
                            timing_pair=None,
                        )
                    else:
                        parent_node = node_by_segment[parent]
                        child_node = node_by_segment[child]
                        parent_indices = np.asarray(
                            pair.alignment.parent_indices, dtype=np.int64,
                        )
                        child_indices = np.asarray(
                            pair.alignment.child_indices, dtype=np.int64,
                        )
                        parent_prediction = oriented.audit["nodes"][parent_node][
                            "calibration_prediction"
                        ]
                        child_prediction = oriented.audit["nodes"][child_node][
                            "calibration_prediction"
                        ]
                        try:
                            if parent_prediction is None or child_prediction is None:
                                raise RuntimeError(
                                    "capture-wide calibration prediction is absent"
                                )
                            (
                                action_log_likelihood,
                                likelihood_report,
                                shared_nuisance_statistics,
                            ) = (
                                edge_local_joint_acceleration_heading_log_likelihood(
                                    pair=pair,
                                    connection=branch.connection_vectors_by_edge[edge],
                                    parent_quaternion_world_sensor_wxyz=np.asarray(
                                        oriented.quat_world_sensor_wxyz_by_node[
                                            parent_node
                                        ][parent_indices],
                                        dtype=float,
                                    ),
                                    child_quaternion_world_sensor_wxyz=np.asarray(
                                        oriented.quat_world_sensor_wxyz_by_node[
                                            child_node
                                        ][child_indices],
                                        dtype=float,
                                    ),
                                    delta_grid_rad=nonhinge_grid,
                                    sample_period_s=float(
                                        settings["orientation"]["sample_period_s"]
                                    ),
                                    savgol_window_samples=int(
                                        settings["joint_center"][
                                            "savgol_window_samples"
                                        ]
                                    ),
                                    savgol_polynomial=int(
                                        settings["joint_center"]["savgol_polynomial"]
                                    ),
                                    estimation_rate_hz=float(
                                        settings["heading"]["explicit_est_settings"][
                                            "estimationRate"
                                        ]
                                    ),
                                    effective_epoch_cap=int(
                                        settings["heading"]["branch_evidence"][
                                            "effective_epoch_cap_per_edge"
                                        ]
                                    ),
                                    parent_accelerometer_covariance_m2_s4=(
                                        np.asarray(
                                            initial["nodes"][parent_node][
                                                "accelerometer_observation_covariance_m2_s4"
                                            ], dtype=float,
                                        )
                                        + np.asarray(
                                            initial["nodes"][parent_node][
                                                "accelerometer_quantization_variance_m2_s4"
                                            ], dtype=float,
                                        )
                                    ),
                                    child_accelerometer_covariance_m2_s4=(
                                        np.asarray(
                                            initial["nodes"][child_node][
                                                "accelerometer_observation_covariance_m2_s4"
                                            ], dtype=float,
                                        )
                                        + np.asarray(
                                            initial["nodes"][child_node][
                                                "accelerometer_quantization_variance_m2_s4"
                                            ], dtype=float,
                                        )
                                    ),
                                    parent_gyroscope_covariance_rad2_s2=(
                                        np.asarray(
                                            initial["nodes"][parent_node][
                                                "gyro_observation_covariance_rad2_s2"
                                            ], dtype=float,
                                        )
                                        + np.asarray(
                                            initial["nodes"][parent_node][
                                                "gyro_quantization_variance_rad2_s2"
                                            ], dtype=float,
                                        )
                                    ),
                                    child_gyroscope_covariance_rad2_s2=(
                                        np.asarray(
                                            initial["nodes"][child_node][
                                                "gyro_observation_covariance_rad2_s2"
                                            ], dtype=float,
                                        )
                                        + np.asarray(
                                            initial["nodes"][child_node][
                                                "gyro_quantization_variance_rad2_s2"
                                            ], dtype=float,
                                        )
                                    ),
                                    parent_gap_orientation_covariance_rad2=np.asarray(
                                        oriented.gap_only_orientation_covariance_rad2_by_node[
                                            parent_node
                                        ][parent_indices],
                                        dtype=float,
                                    ),
                                    child_gap_orientation_covariance_rad2=np.asarray(
                                        oriented.gap_only_orientation_covariance_rad2_by_node[
                                            child_node
                                        ][child_indices],
                                        dtype=float,
                                    ),
                                    parent_calibration_parameter_covariance=np.asarray(
                                        parent_prediction[
                                            "predictive_calibration_posterior"
                                        ]["mixture_covariance"],
                                        dtype=float,
                                    ),
                                    child_calibration_parameter_covariance=np.asarray(
                                        child_prediction[
                                            "predictive_calibration_posterior"
                                        ]["mixture_covariance"],
                                        dtype=float,
                                    ),
                                    parent_calibration_parameter_reference_mean=np.asarray(
                                        parent_prediction["applied_mixture_mean"],
                                        dtype=float,
                                    ),
                                    child_calibration_parameter_reference_mean=np.asarray(
                                        child_prediction["applied_mixture_mean"],
                                        dtype=float,
                                    ),
                                    noise_sigma_multiplier=float(
                                        settings["joint_center"][
                                            "noise_sigma_multiplier"
                                        ]
                                    ),
                                )
                            )
                            result = nonhinge_heading.process(
                                branch_id=branch.branch_id,
                                chronological_index=action_index,
                                likelihood_log_weights=action_log_likelihood,
                                pair=pair,
                                likelihood_report=likelihood_report,
                                shared_nuisance_statistics=(
                                    shared_nuisance_statistics
                                ),
                            )
                        except (
                            ValueError, RuntimeError, FloatingPointError,
                            np.linalg.LinAlgError,
                        ) as exc:
                            result = nonhinge_heading.record_action_no_update(
                                branch_id=branch.branch_id,
                                edge=edge,
                                chronological_index=action_index,
                                action=oriented.action,
                                cause=f"{type(exc).__name__}:{exc}",
                                timing_pair=pair,
                            )
                            action_failures.append({
                                "branch_id": branch.branch_id,
                                "edge": edge,
                                "owner": "NONHINGE_FULL_R3_JOINT_ACCELERATION_S1",
                                "exception_type": type(exc).__name__,
                                "exception_message": str(exc),
                                "local_no_update": True,
                            })
                    nonhinge_prefix = (
                        f"nonhinge_heading/{action_index:02d}/"
                        f"{branch.branch_id}/{edge}"
                    )
                    replay_arrays[f"{nonhinge_prefix}/delta_grid_rad"] = (
                        result.delta_grid_rad
                    )
                    replay_arrays[f"{nonhinge_prefix}/action_log_likelihood"] = (
                        result.action_log_likelihood
                    )
                    replay_arrays[f"{nonhinge_prefix}/posterior_weights"] = (
                        result.posterior_weights
                    )
                    if shared_nuisance_statistics is not None:
                        replay_arrays[
                            f"{nonhinge_prefix}/heading_information"
                        ] = shared_nuisance_statistics.heading_information
                        replay_arrays[
                            f"{nonhinge_prefix}/shared_nuisance_score"
                        ] = shared_nuisance_statistics.shared_score
                        replay_arrays[
                            f"{nonhinge_prefix}/shared_nuisance_covariance"
                        ] = shared_nuisance_statistics.shared_covariance
                        replay_arrays[
                            f"{nonhinge_prefix}/nuisance_normal_j_t_w_j"
                        ] = shared_nuisance_statistics.nuisance_normal
                        replay_arrays[
                            f"{nonhinge_prefix}/nuisance_score_j_t_w_r"
                        ] = shared_nuisance_statistics.nuisance_score
                        replay_arrays[
                            f"{nonhinge_prefix}/residual_quadratic_r_t_w_r"
                        ] = shared_nuisance_statistics.residual_quadratic
                        replay_arrays[
                            f"{nonhinge_prefix}/independent_covariance_logdet"
                        ] = (
                            shared_nuisance_statistics
                            .independent_covariance_log_determinant
                        )
                        replay_arrays[
                            f"{nonhinge_prefix}/shared_nuisance_reference_mean"
                        ] = shared_nuisance_statistics.shared_nuisance_reference_mean
                    action_nonhinge_reports.append({
                        "branch_id": branch.branch_id,
                        "edge": edge,
                        "report": result.report,
                    })
                for edge, parent, child in EDGE_SPECS:
                    completed_spans = 0
                    for span_index, (parent_indices, child_indices, root_indices) in enumerate(
                        chunks_by_edge[edge]
                    ):
                        parent_node = node_by_segment[parent]
                        child_node = node_by_segment[child]
                        parent_gyro = np.asarray(
                            oriented.gyro_rads_by_node[parent_node][parent_indices], dtype=float,
                        )
                        child_gyro = np.asarray(
                            oriented.gyro_rads_by_node[child_node][child_indices], dtype=float,
                        )
                        parent_quat = np.asarray(
                            oriented.quat_world_sensor_wxyz_by_node[parent_node][parent_indices],
                            dtype=float,
                        )
                        child_quat = np.asarray(
                            oriented.quat_world_sensor_wxyz_by_node[child_node][child_indices],
                            dtype=float,
                        )
                        common_time = root_time_s[root_indices]
                        binding = {
                            "schema": "biospur-c2-runtime-owned-heading-span-input-v1",
                            "runtime_owner_token": _semantic_sha({
                                "role": "POSTFREEZE_RETROSPECTIVE_HEADING_SPAN",
                                "branch_id": branch.branch_id,
                                "edge": edge,
                                "chronological_index": action_index,
                                "span_index": span_index,
                                "pair_provenance": (
                                    None if edge not in pair_by_edge
                                    else pair_by_edge[edge].provenance
                                ),
                                "parent_indices_sha256": _array_sha(parent_indices),
                                "child_indices_sha256": _array_sha(child_indices),
                                "pelvis_indices_sha256": _array_sha(root_indices),
                            }),
                            "edge": edge,
                            "chronological_index": action_index,
                            "action": oriented.action,
                            "parent_gyro_sha256": _array_sha(parent_gyro),
                            "child_gyro_sha256": _array_sha(child_gyro),
                            "parent_quaternion_wxyz_sha256": _array_sha(parent_quat),
                            "child_quaternion_wxyz_sha256": _array_sha(child_quat),
                            "common_physical_time_s_sha256": _array_sha(common_time),
                            "selected_source_row_indices_sha256": _array_sha(parent_indices),
                            "source": (
                                "FRESH_SEALED_RANGE_CONTINUOUS_FRONTEND_PLUS_"
                                "PRODUCTION_ALIGNED_PAIR_EXACT_ROOTED_PELVIS_GRID"
                            ),
                        }
                        owner_checkpoint = heading.checkpoint()
                        guard_checkpoint = heading_guard.checkpoint()
                        try:
                            result = heading.process_span(
                                branch_id=branch.branch_id,
                                edge=edge,
                                chronological_index=action_index,
                                action=oriented.action,
                                parent_gyro_sensor=parent_gyro,
                                child_gyro_sensor=child_gyro,
                                parent_quaternion_world_sensor_wxyz=parent_quat,
                                child_quaternion_world_sensor_wxyz=child_quat,
                                common_physical_time_s=common_time,
                                selected_source_row_indices=parent_indices,
                                owner_input_binding=binding,
                                reset_requested=False,
                                profile_stitch_requested=False,
                            )
                        except (ValueError, RuntimeError, FloatingPointError, np.linalg.LinAlgError) as exc:
                            heading.restore(owner_checkpoint)
                            heading_guard.restore(guard_checkpoint)
                            action_failures.append({
                                "branch_id": branch.branch_id, "edge": edge,
                                "span_index": span_index,
                                "exception_type": type(exc).__name__,
                                "exception_message": str(exc),
                                "rolled_back_before_local_no_update": True,
                            })
                            continue
                        prefix = (
                            f"heading/{action_index:02d}/{branch.branch_id}/{edge}/"
                            f"span_{span_index:02d}"
                        )
                        replay_arrays[f"{prefix}/common_physical_time_s"] = common_time
                        replay_arrays[f"{prefix}/selected_parent_source_indices"] = parent_indices
                        replay_arrays[f"{prefix}/selected_child_source_indices"] = child_indices
                        replay_arrays[f"{prefix}/persistent_delta_filt_rad"] = (
                            result.persistent_delta_filt_rad
                        )
                        replay_arrays[f"{prefix}/qmt_observation_delta_rad"] = (
                            result.qmt_observation_delta_rad
                        )
                        replay_arrays[f"{prefix}/qmt_rating"] = result.qmt_rating
                        replay_arrays[f"{prefix}/qmt_state_out"] = result.qmt_state_out
                        replay_arrays[f"{prefix}/posterior_variance_rad2"] = (
                            result.posterior_variance_rad2
                        )
                        replay_arrays[f"{prefix}/corrected_child_quaternion_wxyz"] = (
                            result.corrected_child_segment_quaternion_wxyz
                        )
                        replay_arrays[f"{prefix}/official_qmt_corrected_child_quaternion_wxyz"] = (
                            result.official_qmt_corrected_child_segment_quaternion_wxyz
                        )
                        action_span_reports.append({
                            "branch_id": branch.branch_id,
                            "edge": edge,
                            "span_index": span_index,
                            "report": result.report,
                            "parent_source_indices": _array_binding(parent_indices),
                            "child_source_indices": _array_binding(child_indices),
                            "pelvis_source_indices": _array_binding(root_indices),
                            "common_physical_time_s": _array_binding(common_time),
                        })
                        completed_spans += 1
                    if completed_spans == 0:
                        heading.record_action_no_update(
                            branch_id=branch.branch_id,
                            edge=edge,
                            chronological_index=action_index,
                            action=oriented.action,
                            base_common_physical_time_s=root_time_s,
                            cause="NO_SUCCESSFUL_GAP_SAFE_OFFICIAL_QMT_SPAN_LOCAL_NO_UPDATE",
                        )
                trajectory = heading.assemble_action_rooted_trajectory(
                    branch.branch_id,
                    chronological_index=action_index,
                    action=oriented.action,
                    base_common_physical_time_s=root_time_s,
                )
                root_indices = display_root_indices
                if len(root_indices) < 3:
                    continue
                prefix = f"physical_trajectory/{action_index:02d}/{branch.branch_id}"
                replay_arrays[f"{prefix}/common_physical_time_s"] = root_time_s[root_indices]
                for segment, node in node_by_segment.items():
                    source_indices = display_source_indices[segment]
                    world_from_sensor = display_world_from_sensor[segment]
                    raw_world_from_segment = np.einsum(
                        "nij,jk->nik", world_from_sensor,
                        np.asarray(branch.sensor_from_segment[segment], dtype=float),
                    )
                    segment_delta = np.asarray(
                        trajectory.segment_global_delta_rad[segment], dtype=float,
                    )[root_indices]
                    yaw = Rotation.from_rotvec(np.column_stack((
                        np.zeros(len(segment_delta)), np.zeros(len(segment_delta)), segment_delta,
                    ))).as_matrix()
                    replay_arrays[f"{prefix}/world_from_segment/{segment}"] = np.einsum(
                        "nij,njk->nik", yaw, raw_world_from_segment,
                    )
                    timing_sigma_s = display_timing_sigma_s[segment]
                    owner_covariance = display_owner_covariance[segment]
                    owner_covariance_audit = display_owner_covariance_audit[segment]
                    replay_arrays[f"{prefix}/orientation_covariance/{segment}"] = (
                        owner_covariance
                        + np.asarray(
                            branch.frame_tangent_covariance_rad2[segment], dtype=float,
                        )[None]
                    )
                    replay_arrays[
                        f"{prefix}/orientation_covariance/{segment}"
                    ][:, 2, 2] += np.asarray(
                        trajectory.segment_global_variance_rad2[segment], dtype=float,
                    )[root_indices]
                    replay_arrays[f"{prefix}/segment_source_indices/{segment}"] = source_indices
                    replay_arrays[f"{prefix}/timing_sigma_s/{segment}"] = timing_sigma_s
                    action_branch_rows.setdefault(branch.branch_id, {}).setdefault(
                        "orientation_uncertainty_audit", {}
                    )[segment] = owner_covariance_audit
                action_branch_rows[branch.branch_id].update({
                    "source": RETROSPECTIVE_SOURCE,
                    "viewer_only_physical_status": (
                        "POSTFREEZE_RETROSPECTIVE_NOT_SCIENTIFIC_PHYSICAL_GATE"
                    ),
                    "scientific_physical_gate_executed": False,
                    "postfreeze_retrospective": True,
                    "time_varying_parent_plus_child_deltafilt": True,
                    "pose_or_action_truth_claimed": False,
                })
                trajectory_prefix = f"trajectory/{action_index:02d}/{branch.branch_id}"
                replay_arrays[f"{trajectory_prefix}/common_physical_time_s"] = (
                    trajectory.common_physical_time_s
                )
                for segment, value in trajectory.segment_global_delta_rad.items():
                    replay_arrays[f"{trajectory_prefix}/segment_global_delta/{segment}"] = value
                for segment, value in trajectory.segment_global_variance_rad2.items():
                    replay_arrays[f"{trajectory_prefix}/segment_global_variance/{segment}"] = value
            replay_support[str(action_index)] = action_branch_rows
            action_audit_path = output / f"REPLAY_{action_index:02d}.json"
            _write_new_json(action_audit_path, {
                "schema": "biospur-c2-postfreeze-retrospective-heading-action-v1",
                "chronological_index": action_index,
                "action": oriented.action,
                "production_pair_clock_and_alignment": pair_audits_by_action[action_index],
                "exact_rooted_pair_row_composition": rooted_pair_audit,
                "edge_gap_safe_chunks": chunk_audits,
                "branch_ids": [branch.branch_id for branch in branches],
                "ordinary_span_failures": action_failures,
                "official_qmt_span_reports": action_span_reports,
                "nonhinge_full_s1_likelihood_reports": action_nonhinge_reports,
                "every_edge_advanced_by_qmt_or_explicit_no_update": True,
                "per_action_reset_or_profile_stitch": False,
                "causal_progressive_state_modified": False,
            })
            action_audit_bindings.append({
                "path": str(action_audit_path.relative_to(WORKSPACE)),
                "sha256": _sha(action_audit_path),
            })
            progress["actions_replayed"] = action_index + 1

        npz_path = output / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.npz"
        _write_new_npz(npz_path, replay_arrays)
        structure = deepcopy(frozen["structure"])
        structure["physical_trajectory_support"] = replay_support
        structure["postfreeze_retrospective_heading_replay"] = {
            "source": RETROSPECTIVE_SOURCE,
            "chronological_actions_replayed": list(chronology),
            "action_count": len(chronology),
            "branch_ids": [branch.branch_id for branch in branches],
            "persistent_heading_owner_instance_count": 1,
            "official_callable": "qmt.headingCorrection",
            "per_action_reset_or_profile_stitch": False,
            "final_scalar_heading_tiled_over_time": False,
            "causal_progressive_state_modified": False,
            "nonhinge_heading_owner": (
                "FULL_R3_JOINT_ACCELERATION_FACTORIAL_FULL_S1_POSTERIOR"
            ),
            "nonhinge_heading_owner_audit": nonhinge_heading.audit(),
            "nonhinge_hard_argmax_or_carried_zero_used": False,
        }
        manifest_path = output / "POSTFREEZE_RETROSPECTIVE_QMT_STATE.json"
        replay_manifest = {
            **{key: value for key, value in frozen.items() if key not in {"npz", "array_bindings", "structure", "structure_semantic_sha256"}},
            "schema": "biospur-c2-reloadable-frozen-scientific-state-manifest-v1",
            "created_utc": _now(),
            "npz": {"path": str(npz_path.relative_to(WORKSPACE)), "sha256": _sha(npz_path)},
            "array_bindings": {
                name: _array_binding(value) for name, value in sorted(replay_arrays.items())
            },
            "structure": structure,
            "structure_semantic_sha256": _semantic_sha(structure),
            "parent_frozen_manifest": _binding(FROZEN_MANIFEST_RELATIVE),
            "parent_frozen_npz": _binding(FROZEN_NPZ_RELATIVE),
            "postfreeze_retrospective_source_delta": _binding(SOURCE_DELTA_RELATIVE),
            "postfreeze_retrospective_action_audits": action_audit_bindings,
            "postfreeze_frontend_reader_session_id": reader.reader_session_id,
            "scientific_state_mutable": False,
            "threshold_or_parameter_override_allowed": False,
            "fit_refit_rebase_ik_or_anthropometric_geometry_allowed": False,
            "heldout_opened_when_exported": False,
            "fresh_verification_claimed": False,
            "scientific_acceptance_pass": False,
        }
        _write_new_json(manifest_path, replay_manifest)
        _write_new_json(terminal_path, {
            "schema": "biospur-c2-postfreeze-retrospective-heading-replay-audit-v1",
            "started_local": started,
            "completed_local": _now(),
            "source_delta": _binding(SOURCE_DELTA_RELATIVE),
            "parent_frozen_manifest": _binding(FROZEN_MANIFEST_RELATIVE),
            "parent_frozen_npz": _binding(FROZEN_NPZ_RELATIVE),
            "reader_session_id": reader.reader_session_id,
            "per_action_read_audits": read_bindings,
            "per_action_replay_audits": action_audit_bindings,
            "replay_manifest": {
                "path": str(manifest_path.relative_to(WORKSPACE)), "sha256": _sha(manifest_path),
            },
            "replay_npz": {"path": str(npz_path.relative_to(WORKSPACE)), "sha256": _sha(npz_path)},
            "frontend_reconstruction_manifest": {
                "path": str(frontend_manifest_path.relative_to(WORKSPACE)),
                "sha256": _sha(frontend_manifest_path),
            },
            "frontend_reconstruction_npz": {
                "path": str(frontend_npz_path.relative_to(WORKSPACE)),
                "sha256": _sha(frontend_npz_path),
            },
            "chronological_action_count": len(chronology),
            "persistent_heading_owner_instance_count": 1,
            "persistent_nonhinge_heading_owner_instance_count": 1,
            "official_qmt_callable": "qmt.headingCorrection",
            "full_time_varying_delta_rating_state_and_row_bindings_persisted": True,
            "final_scalar_heading_tiled_over_time": False,
            "payload_access": "EXACT_SEALED_PREFIT_TRAINING_RANGES_ONLY",
            "heldout_opened": False,
            "fit_geometry_or_progressive_recomputed": False,
            "parent_frozen_hashes_unchanged": True,
            "scientific_acceptance_pass": False,
        })
        print(json.dumps({
            "replay_audit": {"path": str(terminal_path.relative_to(WORKSPACE)), "sha256": _sha(terminal_path)},
            "replay_manifest": {"path": str(manifest_path.relative_to(WORKSPACE)), "sha256": _sha(manifest_path)},
            "replay_npz": {"path": str(npz_path.relative_to(WORKSPACE)), "sha256": _sha(npz_path)},
            "heldout_opened": False,
            "scientific_acceptance_pass": False,
        }, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        failure_path = output / "FAILURE.json"
        _write_new_json(failure_path, {
            "schema": "biospur-c2-postfreeze-retrospective-heading-failure-v1",
            "started_local": started,
            "failed_local": _now(),
            "progress": progress,
            "exception_type": type(exc).__name__,
            "exception_message": str(exc),
            "traceback": traceback.format_exc(),
            "heldout_opened": False,
            "fit_geometry_or_progressive_recomputed": False,
            "scientific_acceptance_pass": False,
        })
        print(json.dumps({
            "failure": {"path": str(failure_path.relative_to(WORKSPACE)), "sha256": _sha(failure_path)},
            "exception": f"{type(exc).__name__}:{exc}",
            "stage": progress["stage"],
        }, indent=2, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
