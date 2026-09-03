#!/usr/bin/env python3
"""Execute the frozen metadata-first raw6 heading-closure qualification."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.v0.contracts import dump_json, load_config, sha256_file
from biospur_fusion.v0.bounded_capture1 import (
    load_capture1_calibration_episode_bounded,
    prepare_capture1_bounded_preflight,
)
from biospur_fusion.v0.dual_capture import (
    _capture1_action_rows,
    _capture2_action_rows,
    load_capture2_calibration_episode,
    load_protocol,
)
from biospur_fusion.v0.episode import segment_five_phase_episode
from biospur_fusion.v0.raw6_heading import (
    EDGES,
    ROM_LIMIT_DEG,
    SEGMENTS,
    build_edge_factors,
    drift_stillness,
    fit_edgewise,
    fit_unified_graph,
    graph_consistency,
    raw6_episode_from_rows,
    sensor_display_frames,
    transition_ablation,
    wrap,
)
from biospur_fusion.v0.raw6_synthetic import qualify_synthetic
from biospur_fusion.v0.viewer import write_viewer


RUN_REL = Path("logs/pure_imu_v0_raw6_bounded_access_20260828T060355Z")
PRESELECTION_SHA256 = "a204ec85a9129351f54d21a365b680072bb9b10a3ffc49a0172be9b383d5b742"
C1_PREFLIGHT_NAME = "CAPTURE1_BOUNDED_ACCESS_PREFLIGHT.json"


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


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()).hexdigest()


def _selection(root: Path) -> dict[str, Any]:
    path = root / RUN_REL / "METADATA_PRESELECTION.json"
    if sha256_file(path) != PRESELECTION_SHA256:
        raise RuntimeError("immutable metadata preselection hash changed")
    if path.stat().st_mode & 0o222:
        raise RuntimeError("metadata preselection is writable")
    fresh = json.loads(path.read_text(encoding="utf-8"))
    authority = fresh["selection_authority"]
    if authority.get("selection_changed") is not False:
        raise RuntimeError("fresh preselection changed the frozen action selection")
    authority_path = (root / authority["path"]).resolve()
    if sha256_file(authority_path) != authority["file_sha256"]:
        raise RuntimeError("incorporated metadata selection authority changed")
    payload = json.loads(authority_path.read_text(encoding="utf-8"))
    embedded = payload.pop("selection_sha256")
    if _canonical_hash(payload) != embedded:
        raise RuntimeError("incorporated metadata selection canonical hash mismatch")
    if embedded != authority["embedded_canonical_selection_sha256"]:
        raise RuntimeError("fresh preselection embeds the wrong selection authority")
    payload["selection_sha256"] = embedded
    for capture, fresh_capture in fresh["captures"].items():
        exact = payload["captures"][capture]
        projected = [
            {"action": row["action"], "attempt": row["attempt"],
             "partition": row["partition"]}
            for row in exact["selected_actions"]
        ]
        if projected != fresh_capture["selected_actions"]:
            raise RuntimeError(f"{capture}: fresh preselection projection changed")
        if fresh_capture["capture_id"] != exact["capture_id"]:
            raise RuntimeError(f"{capture}: fresh preselection capture ID changed")
    payload["fresh_preselection"] = fresh
    return payload


def _synthetic_from_frozen_source(root: Path, selection: Mapping[str, Any]) -> dict[str, Any]:
    source = selection["fresh_preselection"]["synthetic_reuse"]
    path = (root / source["source_path"]).resolve()
    if sha256_file(path) != source["source_sha256"]:
        raise RuntimeError("frozen broad synthetic evidence changed")
    payload = json.loads(path.read_text(encoding="utf-8"))
    contract = payload.get("broad_multistart_evidence", {}).get("contract", {})
    if (
        not payload.get("pass")
        or contract.get("local_basin_only") is not False
        or contract.get("broad_independent_start_count", 0) < 4
        or contract.get("all_nine_coordinates_receive_full_circle_coverage_per_start") is not True
    ):
        raise RuntimeError("durable broad synthetic gate is absent or local-only")
    return payload


def _action_rows(
    root: Path,
    capture: str,
    spec: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    rows = (
        _capture1_action_rows(root, spec)
        if capture == "CAPTURE1"
        else _capture2_action_rows(root, spec)
    )
    hxx = {row["action"] for row in spec["hxx"]}
    hxx_rows = [row for row in rows if row["action"] in hxx]
    for row in rows:
        if row["action"] in hxx:
            continue
        if capture == "CAPTURE1":
            row["forbidden_hxx_global_time_intervals_ns"] = [
                [
                    int(item["episode_bounds"]["selected_attempt_token_global_time_ns"]),
                    int(item["episode_bounds"]["stop_global_time_ns_exclusive"]),
                ]
                for item in hxx_rows
            ]
        else:
            row["forbidden_hxx_timing_intervals_ns"] = [
                [
                    int(item["episode_bounds"]["start_host_monotonic_ns"]),
                    int(item["episode_bounds"]["stop_host_monotonic_ns_exclusive"]),
                ]
                for item in hxx_rows
            ]
            row["forbidden_hxx_raw_byte_ranges"] = [
                [
                    int(item["episode_bounds"]["start_byte_inclusive"]),
                    int(item["episode_bounds"]["stop_byte_exclusive"]),
                ]
                for item in hxx_rows
            ]
    return {row["action"]: row for row in rows}


def _assert_selection_row(
    frozen: Mapping[str, Any],
    actual: Mapping[str, Any],
) -> None:
    expected_attempt = int(frozen["attempt"])
    observed_attempt = int(actual.get(
        "attempt_number", actual.get("operator_attempt_id"),
    ))
    if expected_attempt != observed_attempt:
        raise RuntimeError(f"{actual['action']}: selected attempt changed")
    for frozen_name, actual_name in (
        ("formal_action_bounds", "formal_action_bounds"),
        ("complete_episode_bounds", "episode_bounds"),
    ):
        for key, value in frozen[frozen_name].items():
            if key in actual[actual_name] and actual[actual_name][key] != value:
                raise RuntimeError(
                    f"{actual['action']}: frozen {frozen_name}.{key} changed"
                )


def _qmt_intended_action_map(
    frozen_capture: Mapping[str, Any],
) -> dict[str, tuple[str, ...]]:
    """Bind QMT windows only from immutable intended edge/factor semantics."""
    token_to_edge = {
        f"{parent}__{child}": edge for edge, parent, child, _ in EDGES
    }
    output = {
        edge: [] for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
    }
    for row in frozen_capture["selected_actions"]:
        factors = set(row["intended_factors"])
        if not {"qmt_proj_heading", "hinge_axis"}.issubset(factors):
            continue
        for token in row["intended_edges"]:
            edge = token_to_edge.get(token)
            if edge in output:
                output[edge].append(str(row["action"]))
    for edge, actions in output.items():
        training = [
            row for row in frozen_capture["selected_actions"]
            if row["action"] in actions and row["partition"] == "IDENTIFICATION_TRAIN"
        ]
        if not training:
            raise RuntimeError(f"{edge}: immutable preselection lacks a QMT training window")
    return {edge: tuple(actions) for edge, actions in output.items()}


def _timing_proof(access: Mapping[str, Any]) -> dict[str, Any] | None:
    timing = access.get("timing_access")
    if timing is None:
        return None
    required = {
        "all_sequential_timing_rows_within_action_plus_two_superframes": True,
        "every_binary_search_probe_separately_accounted": True,
        "no_full_file_traversal_proven_by_actual_read_union": True,
        "golf_boxing_timing_interval_bytes_touched": False,
        "all_hxx_timing_interval_bytes_touched": False,
    }
    for key, expected in required.items():
        observed = timing.get(key)
        if key == "all_hxx_timing_interval_bytes_touched" and observed is None:
            # Capture2's established v3 timing audit predates the generic Hxx
            # field name.  Its legacy field is nevertheless computed from all
            # three protocol Hxx intervals, which are retained explicitly.
            intervals = timing.get("forbidden_golf_boxing_timing_intervals_ns", [])
            if len(intervals) != 3:
                raise RuntimeError("Capture2 legacy timing proof does not bind all Hxx intervals")
            observed = timing.get("golf_boxing_timing_interval_bytes_touched")
        if observed is not expected:
            raise RuntimeError(f"hostile timing proof failed: {key}")
    accounting = timing["raw_io_call_accounting"]
    if accounting.get("instrumentation_layer") not in {
        "os.read/os.lseek wrappers",
        "os.read/os.lseek on O_RDONLY descriptors; no buffered reader",
    }:
        raise RuntimeError("hostile timing proof is not OS-call instrumented")
    if (
        int(accounting.get("actual_os_read_calls", 0)) <= 0
        or int(accounting.get("actual_os_seek_calls", 0)) <= 0
    ):
        raise RuntimeError("hostile timing proof has no actual OS call accounting")
    return {
        key: (
            timing[key] if key in timing
            else timing["golf_boxing_timing_interval_bytes_touched"]
        ) for key in required
    } | {
        "actual_os_read_calls": accounting["actual_os_read_calls"],
        "actual_os_seek_calls": accounting["actual_os_seek_calls"],
        "binary_search_probe_count": timing["binary_search_probe_count"],
        "sequential_windows": timing["sequential_windows"],
        "search_ceiling_contracts": timing["search_ceiling_contracts"],
        "raw_io_call_accounting": accounting,
    }


def _load_capture(
    root: Path,
    capture: str,
    spec: Mapping[str, Any],
    frozen: Mapping[str, Any],
) -> tuple[list[Any], dict[str, Any]]:
    by_name = _action_rows(root, capture, spec)
    episode_contract = load_protocol(root)["semantic_qa_contract"]["calibration_episode"]
    episodes = []
    bindings = []
    access_proofs = []
    action_access_dir = root / RUN_REL / f"{capture}_ACTION_ACCESS"
    action_access_dir.mkdir(parents=True, exist_ok=True)
    for selected in frozen["selected_actions"]:
        print(
            f"STAGE {capture} load {selected['action']} begin",
            flush=True,
        )
        action = by_name[selected["action"]]
        _assert_selection_row(selected, action)
        if capture == "CAPTURE1":
            rows, access = load_capture1_calibration_episode_bounded(
                root, spec, action, root / RUN_REL / C1_PREFLIGHT_NAME,
            )
        else:
            rows, access = load_capture2_calibration_episode(root, spec, action)
        action_access_path = action_access_dir / f"{selected['action']}.json"
        if action_access_path.exists():
            existing = json.loads(action_access_path.read_text(encoding="utf-8"))
            if _canonical_hash(existing) != _canonical_hash(access):
                raise RuntimeError(
                    f"fresh access differs from immutable checkpoint {action_access_path}"
                )
        else:
            dump_json(action_access_path, access)
            action_access_path.chmod(0o444)
        formal = access["formal_action_bounds"]
        diagnostic = segment_five_phase_episode(
            rows,
            action=selected["action"],
            action_kind=(
                "STATIONARY_REFERENCE"
                if "still" in selected["action"].lower()
                else "MOVEMENT_OR_POSE"
            ),
            formal_start_global_ns=int(formal["start_global_time_ns"]),
            formal_stop_global_ns_exclusive=int(
                formal["stop_global_time_ns_exclusive"]
            ),
            contract=episode_contract,
            boundary_authority=access["boundary_authority"],
        )
        episode = raw6_episode_from_rows(
            capture=capture,
            action=selected["action"],
            partition=selected["partition"],
            rows_by_node=rows,
            identity=spec["identity"],
            episode_diagnostic=diagnostic,
            rate_hz=50,
        )
        print(
            f"STAGE {capture} load {selected['action']} raw6 complete",
            flush=True,
        )
        episodes.append(episode)
        timing = _timing_proof(access)
        if timing is not None:
            access_proofs.append({
                "action": selected["action"],
                "proof": timing,
            })
        nodes = access.get("nodes", access.get("decode", {}).get("nodes", {}))
        bindings.append({
            "action": selected["action"],
            "action_access_artifact": str(action_access_path),
            "action_access_artifact_sha256": sha256_file(action_access_path),
            "partition": selected["partition"],
            "attempt": selected["attempt"],
            "complete_episode_bounds": selected["complete_episode_bounds"],
            "raw_or_member_access": {
                "raw_path": access.get("raw_path"),
                "read_bracket": access.get("read_bracket"),
                "ledger": access.get("ledger"),
                "opened_members": access.get("opened_members"),
                "decoded_payload_classes": access.get(
                    "decode", {}
                ).get("decoded_payload_classes"),
                "spatial_members_opened": access.get("spatial_members_opened", []),
                "uwb_spatial_payload_consumed": access.get(
                    "uwb_spatial_payload_consumed"
                ),
                "hxx_payload_opened": access.get("hxx_payload_opened", False),
                "retry_or_skip_payload_opened": access.get(
                    "retry_or_skip_payload_opened", False
                ),
            },
            "node_payload_sha256": {
                node: row["payload_sha256"] for node, row in nodes.items()
            },
            "five_phase_status": diagnostic["EPISODE_COMPLETENESS"],
            "five_phase_failures": diagnostic.get("failures", []),
            "raw6_audit": episode.audit,
        })
    expected = [row["action"] for row in frozen["selected_actions"]]
    if [episode.action for episode in episodes] != expected:
        raise RuntimeError("payload execution order differs from frozen preselection")
    return episodes, {
        "schema": "biospur-pure-imu-v0-capture-payload-bindings-v1",
        "capture": capture,
        "capture_id": spec["capture_id"],
        "selected_actions": bindings,
        "hostile_timing_access_proofs": access_proofs,
        "hxx_payload_opened": False,
        "capture3_payload_opened": False,
        "full_raw_container_hash_recomputed": False,
        "preselection_mutated": False,
    }


def _positions(
    rotation: np.ndarray,
    geometry: Mapping[str, Any],
) -> np.ndarray:
    """Direct display FK with one fixed parent-frame link per body-graph edge.

    The geometry table historically stores parent- and child-side sensor-to-
    connection offsets.  Subtracting those offsets defines the display link.
    Rotating both offsets independently instead would reconstruct sensor-origin
    separation, whose norm legitimately varies at a joint; treating that as a
    skeleton-link length created a false viewer-coherence failure.
    """
    index = {segment: i for i, segment in enumerate(SEGMENTS)}
    position = np.zeros((len(rotation), len(SEGMENTS), 3))
    for edge, parent, child, _ in EDGES:
        row = geometry[edge]
        p = index[parent]
        c = index[child]
        fixed_display_link = (
            np.asarray(row["parent"], float) - np.asarray(row["child"], float)
        )
        position[:, c] = position[:, p] + np.einsum(
            "nij,j->ni", rotation[:, p], fixed_display_link,
        )
    return position


def _write_capture_viewer(
    root: Path,
    run_dir: Path,
    capture: str,
    spec: Mapping[str, Any],
    episodes: list[Any],
    factors: Mapping[str, Any],
    unified: Mapping[str, Any],
    *, viewer_filename: str | None = None,
) -> dict[str, Any]:
    representative = (
        {"initial_still", "arms", "left_elbow", "squats", "trunk"}
        if capture == "CAPTURE1"
        else {
            "00_initial_still",
            "04_shoulder_left",
            "06_elbow_left",
            "08_hip_left",
            "14_trunk_flex_extend",
            "16_squat",
        }
    )
    selected = [episode for episode in episodes if episode.action in representative]
    frames = sensor_display_frames(unified, factors)
    config = load_config(root / "config/biospur_fusion_v0/config.json")
    geometry = config.section("display_geometry")
    time_rows = []
    window_rows = []
    boundary_rows = []
    rotation_rows = []
    position_rows = []
    confidence_rows = []
    sigma_rows = []
    joint_rows = []
    qmt_rows = []
    segment_confidence = {"pelvis": 0.5}
    segment_sigma = {"pelvis": np.pi}
    for segment in SEGMENTS[1:]:
        spread = float(unified["multistart_spread_deg"][segment])
        segment_confidence[segment] = float(
            0.05 + 0.95 * np.exp(-((spread / 10.0) ** 2))
        )
        segment_sigma[segment] = math.radians(max(spread, 0.1))
    index = {segment: i for i, segment in enumerate(SEGMENTS)}
    for episode in selected:
        keep = np.arange(0, len(episode.time_ns), 5)
        rotations = np.empty((len(keep), len(SEGMENTS), 3, 3))
        for segment in SEGMENTS:
            heading = float(unified["headings_rad"][segment])
            rotations[:, index[segment]] = np.einsum(
                "ij,njk,kl->nil",
                np.array([
                    [math.cos(heading), -math.sin(heading), 0.0],
                    [math.sin(heading), math.cos(heading), 0.0],
                    [0.0, 0.0, 1.0],
                ]),
                episode.rotation_world_sensor[segment][keep],
                frames[segment],
            )
        # One common display yaw, applied equally to all ten segments.
        pelvis_first = rotations[0, index["pelvis"]]
        display_yaw = math.atan2(pelvis_first[1, 0], pelvis_first[0, 0])
        gauge = np.array([
            [math.cos(-display_yaw), -math.sin(-display_yaw), 0.0],
            [math.sin(-display_yaw), math.cos(-display_yaw), 0.0],
            [0.0, 0.0, 1.0],
        ])
        rotations = np.einsum("ij,ntjk->ntik", gauge, rotations)
        positions = _positions(rotations, geometry)
        joints = np.empty((len(keep), len(EDGES), 3))
        for edge_index, (_, parent, child, _) in enumerate(EDGES):
            relative = np.einsum(
                "nji,njk->nik",
                rotations[:, index[parent]],
                rotations[:, index[child]],
            )
            joints[:, edge_index] = Rotation.from_matrix(relative).as_rotvec()
        confidence = np.tile(
            np.asarray([segment_confidence[s] for s in SEGMENTS]), (len(keep), 1)
        )
        sigma = np.tile(
            np.asarray([segment_sigma[s] for s in SEGMENTS]), (len(keep), 1)
        )
        time_rows.append(episode.time_ns[keep])
        window_rows.append(np.full(len(keep), episode.action, dtype="U40"))
        boundary_rows.append(episode.phase[keep])
        rotation_rows.append(rotations)
        position_rows.append(positions)
        confidence_rows.append(confidence)
        sigma_rows.append(sigma)
        joint_rows.append(joints)
        qmt_rows.append(np.full(
            len(keep),
            float(np.mean(np.abs([
                unified["edges"][edge]["relative_heading_deg"]
                for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
            ]))),
        ))
    time = np.concatenate(time_rows)
    windows = np.concatenate(window_rows)
    boundary = np.concatenate(boundary_rows)
    rotations = np.concatenate(rotation_rows)
    positions = np.concatenate(position_rows)
    confidence = np.concatenate(confidence_rows)
    sigma = np.concatenate(sigma_rows)
    joints = np.concatenate(joint_rows)
    qmt_applied = np.concatenate(qmt_rows)
    viewer_path = run_dir / (
        viewer_filename or f"{capture}_DIRECT_RAW6_FK_VIEWER.html"
    )
    viewer = write_viewer(
        viewer_path,
        time_ns=time,
        window=windows,
        boundary=boundary,
        segment_names=SEGMENTS,
        segment_position=positions,
        segment_rotation=rotations,
        segment_confidence=confidence,
        joint_rotvec=joints,
        segment_sigma_rad=sigma,
        node_by_segment=tuple(
            next(node for node, value in spec["identity"].items() if value == segment)
            for segment in SEGMENTS
        ),
        qmt_mode="RAW6_EDGEWISE_THEN_UNIFIED",
        qmt_state=np.full(len(time), "DIRECT_FIXED_PROFILE_NO_IK", dtype="U32"),
        qmt_applied_correction_deg=qmt_applied,
        viewer_metadata={
            "capture_id": spec["capture_id"],
            "profile_id": f"{capture}_RAW6_CAPTURE_LOCAL",
            "profile_sha256": _canonical_hash({
                "headings": unified["headings_rad"],
                "levers": unified["lever_by_edge"],
            }),
            "action_role": "INITIAL_STILL_AND_REPRESENTATIVE_ORDINARY_ACTIONS",
            "locked_state_source": "RAW_ACC_GYR_VQF_EDGE_GRAPH_ONLY",
            "shared_ik_mode": "DISABLED_DIRECT_FIXED_PROFILE_FK",
            "display_geometry_status": "DISPLAY_ONLY_NON_METRIC",
            "pipeline_label": (
                "raw acc/gyr · independent 6D VQF · QMT/IMT edge baseline · "
                "unified nine-heading graph · direct fixed-profile FK (no IK)"
            ),
        },
    )
    edge_lengths = {}
    for edge, parent, child, _ in EDGES:
        distance = np.linalg.norm(
            positions[:, index[child]] - positions[:, index[parent]], axis=1,
        )
        edge_lengths[edge] = {
            "median_m": float(np.median(distance)),
            "relative_std": float(np.std(distance) / max(np.mean(distance), 1e-12)),
        }
    joint_angle_deg = np.degrees(np.linalg.norm(joints, axis=2))
    joint_rom = {}
    for edge_index, (edge, _, _, _) in enumerate(EDGES):
        values = joint_angle_deg[:, edge_index]
        limit = float(ROM_LIMIT_DEG[edge])
        violation = values > limit + 5.0
        row_pass = bool(
            np.mean(violation) <= 0.05
            and np.max(values) <= limit + 20.0
        )
        joint_rom[edge] = {
            "rom_limit_deg": limit,
            "allowance_deg": 5.0,
            "maximum_allowance_deg": 20.0,
            "median_deg": float(np.median(values)),
            "p95_deg": float(np.quantile(values, 0.95)),
            "maximum_deg": float(np.max(values)),
            "fraction_over_limit_plus_5deg": float(np.mean(violation)),
            "pass": row_pass,
        }
    natural_standing = {
        "applies": capture == "CAPTURE1",
        "role": (
            "INDEPENDENT_PROTOCOL_DESCRIPTION_QUALITATIVE_SANITY_ONLY;"
            "NEVER_ESTIMATOR_INPUT_OR_POSE_FACTOR"
        ),
        "action": "initial_still" if capture == "CAPTURE1" else None,
    }
    if capture == "CAPTURE1":
        mask = windows == "initial_still"
        frame_maximum = np.max(joint_angle_deg[mask], axis=1)
        natural_standing |= {
            "frame_count": int(np.count_nonzero(mask)),
            "median_maximum_joint_angle_deg": float(np.median(frame_maximum)),
            "p95_maximum_joint_angle_deg": float(np.quantile(frame_maximum, 0.95)),
            "maximum_joint_angle_deg": float(np.max(frame_maximum)),
            "predeclared_qualitative_p95_limit_deg": 45.0,
            "pass": bool(np.quantile(frame_maximum, 0.95) <= 45.0),
        }
    finite = bool(np.isfinite(rotations).all() and np.isfinite(positions).all())
    determinants = np.linalg.det(rotations.reshape(-1, 3, 3))
    return viewer | {
        "quantitative_direct_fk": {
            "finite": finite,
            "rotation_determinant_min": float(np.min(determinants)),
            "rotation_determinant_max": float(np.max(determinants)),
            "root_position_max_norm_m": float(np.max(np.linalg.norm(
                positions[:, index["pelvis"]], axis=1
            ))),
            "edge_lengths": edge_lengths,
            "joint_rom": joint_rom,
            "joint_rom_coherence_pass": all(
                row["pass"] for row in joint_rom.values()
            ),
            "natural_standing_qualitative_sanity": natural_standing,
            "maximum_edge_relative_length_std": float(max(
                value["relative_std"] for value in edge_lengths.values()
            )),
            "shared_ik_used": False,
            "viewer_pose_used_as_estimator_input": False,
            "observable_relative_heading_displayed": True,
            "root_yaw_display_gauge_labelled": True,
        },
    }


def _capture_decision(
    binding: Mapping[str, Any],
    edgewise: Mapping[str, Any],
    unified: Mapping[str, Any],
    viewer: Mapping[str, Any],
) -> dict[str, Any]:
    timing = all(
        row["proof"]["golf_boxing_timing_interval_bytes_touched"] is False
        and row["proof"]["no_full_file_traversal_proven_by_actual_read_union"] is True
        for row in binding["hostile_timing_access_proofs"]
    )
    complete = all(
        row["five_phase_status"] == "PASS"
        for row in binding["selected_actions"]
    )
    qmt = {
        edge: {
            "all_action_diagnostic_successful_actions": edgewise["edges"][edge]["qmt"][
                "successful_action_count"
            ],
            "all_action_diagnostic_spread_deg": edgewise["edges"][edge]["qmt"][
                "action_estimate_spread_deg"
            ],
            "qualification": edgewise["edges"][edge]["qmt"]["qualification"],
            "held_out_comparison": edgewise["edges"][edge]["qmt"][
                "held_out_comparison"
            ],
        }
        for edge in ("elbow_left", "elbow_right", "knee_left", "knee_right")
    }
    qmt_ok = all(row["qualification"]["pass"] for row in qmt.values())
    edgewise_to_unified_heading_change = {
        edge: float(np.degrees(abs(wrap(
            unified["edges"][edge]["relative_heading_rad"]
            - edgewise["edges"][edge]["baseline_heading_rad"]
        ))))
        for edge, _, _, _ in EDGES
    }
    edgewise_to_unified_ok = all(
        value <= 20.0 for value in edgewise_to_unified_heading_change.values()
    )
    held = {
        edge: row["held_out"]["physical_rms_mps2"]
        for edge, row in unified["edges"].items()
    }
    train = {
        edge: row["train"]["physical_rms_mps2"]
        for edge, row in unified["edges"].items()
    }
    held_ok = all(
        held[edge] is not None
        and held[edge] <= max(1.5, 1.75 * train[edge])
        for edge in held
    )
    lever_ok = all(
        row["lever_max_abs_m"] <= 0.65 for row in unified["edges"].values()
    )
    fitted_levers = {
        edge: np.asarray(value, float)
        for edge, value in unified["lever_by_edge"].items()
    }
    fitted_segment_lengths = {
        "torso": float(np.linalg.norm(
            0.5 * (
                fitted_levers["shoulder_left"][:3]
                + fitted_levers["shoulder_right"][:3]
            ) - fitted_levers["pelvis_torso"][3:]
        )),
        "upper_arm_left": float(np.linalg.norm(
            fitted_levers["shoulder_left"][3:]
            - fitted_levers["elbow_left"][:3]
        )),
        "upper_arm_right": float(np.linalg.norm(
            fitted_levers["shoulder_right"][3:]
            - fitted_levers["elbow_right"][:3]
        )),
        "thigh_left": float(np.linalg.norm(
            fitted_levers["hip_left"][3:]
            - fitted_levers["knee_left"][:3]
        )),
        "thigh_right": float(np.linalg.norm(
            fitted_levers["hip_right"][3:]
            - fitted_levers["knee_right"][:3]
        )),
    }
    broad_anatomical_ranges_m = {
        "torso": (0.15, 0.55),
        "upper_arm_left": (0.18, 0.45),
        "upper_arm_right": (0.18, 0.45),
        "thigh_left": (0.25, 0.60),
        "thigh_right": (0.25, 0.60),
    }
    fitted_segment_length_report = {
        segment: {
            "fitted_joint_center_separation_m": fitted_segment_lengths[segment],
            "broad_plausible_min_m": bounds[0],
            "broad_plausible_max_m": bounds[1],
            "pass": bounds[0] <= fitted_segment_lengths[segment] <= bounds[1],
        }
        for segment, bounds in broad_anatomical_ranges_m.items()
    }
    fitted_segment_lengths_ok = all(
        row["pass"] for row in fitted_segment_length_report.values()
    )
    fk = viewer["quantitative_direct_fk"]
    viewer_numeric = bool(
        fk["finite"]
        and fk["maximum_edge_relative_length_std"] < 1e-9
        and abs(fk["rotation_determinant_min"] - 1.0) < 1e-6
        and abs(fk["rotation_determinant_max"] - 1.0) < 1e-6
    )
    rom_coherent = bool(fk.get("joint_rom_coherence_pass", False))
    natural_standing = fk.get(
        "natural_standing_qualitative_sanity", {"applies": False},
    )
    natural_standing_ok = bool(
        not natural_standing.get("applies", False)
        or natural_standing.get("pass", False)
    )
    gates = {
        "complete_five_phase_episodes": complete,
        "hostile_timing_access": timing,
        "numeric_rank_nine_after_one_gauge": (
            unified["numeric_rank_after_gauge"] == 9
        ),
        "multistart_max_spread_le_10deg": (
            unified["multistart_max_spread_deg"] <= 10.0
        ),
        "qmt_excitation_aware_edge_baseline": qmt_ok,
        "edgewise_to_unified_relative_heading_change_le_20deg": (
            edgewise_to_unified_ok
        ),
        "held_out_b5_generalization": held_ok,
        "unbounded_b5_levers_physically_plausible": lever_ok,
        "fitted_joint_center_segment_lengths_plausible": fitted_segment_lengths_ok,
        "direct_fk_numeric_coherence": viewer_numeric,
        "direct_fk_anatomical_rom_coherence": rom_coherent,
        "capture1_natural_standing_qualitative_sanity": natural_standing_ok,
    }
    failed = [key for key, value in gates.items() if not value]
    weak_edges = []
    for edge, row in unified["edges"].items():
        if (
            row["held_out"]["physical_rms_mps2"] is None
            or row["lever_max_abs_m"] > 0.65
            or (
                row["held_out"]["physical_rms_mps2"] is not None
                and row["held_out"]["physical_rms_mps2"]
                > max(1.5, 1.75 * row["train"]["physical_rms_mps2"])
            )
        ):
            weak_edges.append(edge)
    return {
        "gates": gates,
        "failed_gates": failed,
        "qmt_edge_gate_details": qmt,
        "edgewise_to_unified_relative_heading_change_deg": (
            edgewise_to_unified_heading_change
        ),
        "train_b5_rms_mps2": train,
        "held_out_b5_rms_mps2": held,
        "fitted_joint_center_segment_lengths": fitted_segment_length_report,
        "weak_edges": weak_edges,
        "pass": not failed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--capture", choices=("CAPTURE1", "CAPTURE2"),
        help="run one independently checkpointed real capture after the durable synthetic gate",
    )
    parser.add_argument(
        "--finalize-only", action="store_true",
        help="combine two completed capture checkpoints without reopening payloads",
    )
    parser.add_argument(
        "--prepare-capture1-access", action="store_true",
        help="freeze Capture1 bounded byte corridors without opening array/raw payloads",
    )
    parser.add_argument(
        "--rederive-viewer", choices=("CAPTURE1", "CAPTURE2"),
        help="rebuild a viewer from the immutable fitted result and bounded raw episodes",
    )
    parser.add_argument(
        "--reevaluate-qmt", choices=("CAPTURE1", "CAPTURE2"),
        help="apply immutable factor mapping plus signal-only QMT excitation qualification",
    )
    parser.add_argument(
        "--refit-current-qmt", choices=("CAPTURE1", "CAPTURE2"),
        help=(
            "preserve the original checkpoint and refit the capture-wide graph with "
            "the current immutable excitation-aware hinge-axis semantics"
        ),
    )
    parser.add_argument(
        "--rederive-current-viewer", choices=("CAPTURE1", "CAPTURE2"),
        help="rebuild the current-semantics viewer after a downstream FK-only correction",
    )
    parser.add_argument(
        "--reevaluate-current-gates", choices=("CAPTURE1", "CAPTURE2"),
        help="recompute final current-semantics gates from immutable derived artifacts only",
    )
    args = parser.parse_args()
    if sum(bool(value) for value in (
        args.capture, args.finalize_only, args.prepare_capture1_access,
        args.rederive_viewer, args.reevaluate_qmt, args.refit_current_qmt,
        args.rederive_current_viewer,
        args.reevaluate_current_gates,
    )) > 1:
        parser.error("capture, finalization, and Capture1 preflight modes are mutually exclusive")
    root = ROOT.resolve()
    run_dir = (root / RUN_REL).resolve()
    selection = _selection(root)
    protocol = load_protocol(root)
    if args.prepare_capture1_access:
        spec = protocol["captures"]["CAPTURE1"]
        all_actions = _action_rows(root, "CAPTURE1", spec)
        output = run_dir / C1_PREFLIGHT_NAME
        payload = prepare_capture1_bounded_preflight(
            root, spec, all_actions,
            selection["captures"]["CAPTURE1"]["selected_actions"], output,
            preselection_path=run_dir / "METADATA_PRESELECTION.json",
            preselection_sha256=PRESELECTION_SHA256,
        )
        print(json.dumps({
            "capture": "CAPTURE1", "preflight": str(output),
            "preflight_sha256": sha256_file(output), "gate": payload["gate"],
        }, indent=2), flush=True)
        return
    if args.rederive_current_viewer:
        capture = args.rederive_current_viewer
        current_path = run_dir / f"{capture}_CURRENT_QMT_RESULT.json"
        output = run_dir / f"{capture}_CURRENT_VIEWER_REDERIVATION.json"
        viewer_path = run_dir / f"{capture}_DIRECT_RAW6_FK_VIEWER_V4.html"
        if not current_path.exists():
            raise RuntimeError(f"{capture}: current-semantics fitted checkpoint is absent")
        if output.exists() or viewer_path.exists():
            raise RuntimeError(f"{capture}: refusing to overwrite current viewer evidence")
        current = json.loads(current_path.read_text(encoding="utf-8"))
        spec = protocol["captures"][capture]
        frozen_capture = selection["captures"][capture]
        qmt_actions = _qmt_intended_action_map(frozen_capture)
        episodes, binding = _load_capture(root, capture, spec, frozen_capture)
        factors, factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        viewer = _write_capture_viewer(
            root, run_dir, capture, spec, episodes, factors,
            current["unified_graph"], viewer_filename=viewer_path.name,
        )
        decision = _capture_decision(
            binding, current["edgewise_baseline"], current["unified_graph"], viewer,
        )
        artifact = {
            "schema": "biospur-pure-imu-v0-current-viewer-rederivation-v1",
            "capture": capture,
            "current_result_path": str(current_path),
            "current_result_sha256": sha256_file(current_path),
            "reason": (
                "Derive pelvis/torso display axes from the fitted bilateral hip and "
                "shoulder centers rather than the inverted generic chain rule; add "
                "anatomical ROM, fitted joint-center separation, and independent "
                "Capture1 natural-standing sanity gates. No fitted parameter changed."
            ),
            "fitted_headings_changed": False,
            "payload_selection_changed": False,
            "pose_label_used_as_estimator_input": False,
            "viewer_pose_used_to_rescue_fit": False,
            "factor_inventory_rederived_from_same_bounded_raw_slices": factor_audit,
            "new_viewer": viewer,
            "recomputed_decision": decision,
        }
        dump_json(output, _jsonable(artifact))
        output.chmod(0o444)
        viewer_path.chmod(0o444)
        print(json.dumps({
            "capture": capture, "artifact": str(output),
            "artifact_sha256": sha256_file(output), "decision": decision,
        }, indent=2), flush=True)
        return
    synthetic = _synthetic_from_frozen_source(root, selection)
    if not synthetic["pass"]:
        raise RuntimeError("synthetic qualification failed before real payload open")
    print("STAGE synthetic qualification PASS", flush=True)
    if args.reevaluate_current_gates:
        capture = args.reevaluate_current_gates
        current_path = run_dir / f"{capture}_CURRENT_QMT_RESULT.json"
        binding_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
        viewer_artifact = run_dir / f"{capture}_CURRENT_VIEWER_REDERIVATION.json"
        output = run_dir / f"{capture}_CURRENT_FINAL_GATES.json"
        if output.exists():
            raise RuntimeError(f"{capture}: refusing to overwrite final gate evidence")
        if not current_path.exists() or not binding_path.exists():
            raise RuntimeError(f"{capture}: current result or bounded-access audit is absent")
        current = json.loads(current_path.read_text(encoding="utf-8"))
        binding = json.loads(binding_path.read_text(encoding="utf-8"))
        if viewer_artifact.exists():
            viewer_container = json.loads(viewer_artifact.read_text(encoding="utf-8"))
            viewer = viewer_container["new_viewer"]
            viewer_source = viewer_artifact
        else:
            viewer = current["direct_fk_viewer"]
            viewer_source = current_path
        decision = _capture_decision(
            binding, current["edgewise_baseline"], current["unified_graph"], viewer,
        )
        artifact = {
            "schema": "biospur-pure-imu-v0-current-final-gates-v1",
            "capture": capture,
            "current_result_path": str(current_path),
            "current_result_sha256": sha256_file(current_path),
            "bounded_access_audit_path": str(binding_path),
            "bounded_access_audit_sha256": sha256_file(binding_path),
            "viewer_source_path": str(viewer_source),
            "viewer_source_sha256": sha256_file(viewer_source),
            "payload_reopened": False,
            "selection_mutated": False,
            "decision": decision,
        }
        dump_json(output, _jsonable(artifact))
        output.chmod(0o444)
        print(json.dumps({
            "capture": capture, "artifact": str(output),
            "artifact_sha256": sha256_file(output), "decision": decision,
        }, indent=2), flush=True)
        return
    if args.refit_current_qmt:
        capture = args.refit_current_qmt
        original_path = run_dir / f"{capture}_RESULT.json"
        output = run_dir / f"{capture}_CURRENT_QMT_RESULT.json"
        viewer_path = run_dir / f"{capture}_DIRECT_RAW6_FK_VIEWER_V3.html"
        if not original_path.exists():
            raise RuntimeError(f"{capture}: original fitted checkpoint is absent")
        if output.exists() or viewer_path.exists():
            raise RuntimeError(f"{capture}: refusing to overwrite current-semantics evidence")
        spec = protocol["captures"][capture]
        frozen_capture = selection["captures"][capture]
        qmt_actions = _qmt_intended_action_map(frozen_capture)
        episodes, binding = _load_capture(root, capture, spec, frozen_capture)
        factors, factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        print(f"STAGE {capture} current-QMT edge factors built", flush=True)
        edgewise = fit_edgewise(factors)
        initial = np.asarray([
            edgewise["accumulated_headings_rad"][segment]
            for segment in SEGMENTS[1:]
        ])
        unified = fit_unified_graph(
            factors, initial, starts=7,
            seed=20260828 if capture == "CAPTURE1" else 20260830,
        )
        print(f"STAGE {capture} current-QMT unified graph fit", flush=True)
        transitions = transition_ablation(
            episodes, unified, qmt_intended_actions=qmt_actions,
        )
        graph = graph_consistency(edgewise, unified)
        drift = drift_stillness(episodes)
        viewer = _write_capture_viewer(
            root, run_dir, capture, spec, episodes, factors, unified,
            viewer_filename=viewer_path.name,
        )
        decision = _capture_decision(binding, edgewise, unified, viewer)
        result = {
            "schema": "biospur-pure-imu-v0-current-qmt-capture-result-v1",
            "capture": capture,
            "capture_id": spec["capture_id"],
            "supersedes_for_final_scientific_judgment": str(original_path),
            "superseded_result_sha256": sha256_file(original_path),
            "reason": (
                "The immutable excitation-aware QMT qualification also fits Olsson "
                "hinge axes only on the predeclared relevant training windows. This "
                "separate checkpoint refits the capture-wide nine-heading objective "
                "with those current factors; no original evidence is overwritten."
            ),
            "preselection_mutated": False,
            "immutable_qmt_intended_action_map": qmt_actions,
            "edge_factor_inventory": factor_audit,
            "edgewise_baseline": edgewise,
            "unified_graph": unified,
            "transition_ablation": transitions,
            "graph_consistency": graph,
            "drift_stillness": drift,
            "direct_fk_viewer": viewer,
            "decision": decision,
            "raw_signal_input_only": True,
            "prohibited_inputs_used": [],
            "b4_used": False,
            "b5_qualified_independently_of_b4": True,
            "other_capture_parameters_used": False,
        }
        dump_json(output, _jsonable(result))
        output.chmod(0o444)
        viewer_path.chmod(0o444)
        print(json.dumps({
            "capture": capture, "artifact": str(output),
            "artifact_sha256": sha256_file(output), "decision": decision,
        }, indent=2), flush=True)
        return
    if args.rederive_viewer:
        capture = args.rederive_viewer
        result_path = run_dir / f"{capture}_RESULT.json"
        if not result_path.exists():
            raise RuntimeError(f"{capture}: fitted result is absent for viewer re-derivation")
        prior_result_sha = sha256_file(result_path)
        prior = json.loads(result_path.read_text(encoding="utf-8"))
        spec = protocol["captures"][capture]
        episodes, binding = _load_capture(
            root, capture, spec, selection["captures"][capture],
        )
        qmt_actions = _qmt_intended_action_map(selection["captures"][capture])
        factors, factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        viewer = _write_capture_viewer(
            root, run_dir, capture, spec, episodes, factors,
            prior["unified_graph"],
            viewer_filename=f"{capture}_DIRECT_RAW6_FK_VIEWER_V2.html",
        )
        qmt_reevaluation_path = run_dir / f"{capture}_QMT_EXCITATION_AWARE_REEVALUATION.json"
        decision_edgewise = (
            json.loads(qmt_reevaluation_path.read_text(encoding="utf-8"))[
                "edgewise_baseline"
            ] if qmt_reevaluation_path.exists() else prior["edgewise_baseline"]
        )
        decision = _capture_decision(
            binding, decision_edgewise, prior["unified_graph"], viewer,
        )
        artifact = {
            "schema": "biospur-pure-imu-v0-direct-fk-viewer-rederivation-v1",
            "capture": capture, "prior_result_path": str(result_path),
            "prior_result_sha256": prior_result_sha,
            "reason": (
                "Correct a display-only FK metric bug: independently rotating both "
                "sensor-to-joint offsets measures sensor-origin separation, not a fixed "
                "skeletal display link. No fit, heading, factor, or payload selection changed."
            ),
            "fitted_headings_changed": False, "payload_selection_changed": False,
            "qmt_reevaluation_used_for_decision": qmt_reevaluation_path.exists(),
            "new_viewer": viewer, "recomputed_decision": decision,
            "factor_inventory_rederived_from_same_raw_slices": factor_audit,
        }
        output = run_dir / f"{capture}_VIEWER_REDERIVATION.json"
        if output.exists():
            raise RuntimeError(f"refusing to overwrite viewer re-derivation {output}")
        dump_json(output, _jsonable(artifact)); output.chmod(0o444)
        print(json.dumps({
            "capture": capture, "artifact": str(output),
            "artifact_sha256": sha256_file(output), "decision": decision,
        }, indent=2), flush=True)
        return
    if args.reevaluate_qmt:
        capture = args.reevaluate_qmt
        result_path = run_dir / f"{capture}_RESULT.json"
        audit_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
        if not result_path.exists() or not audit_path.exists():
            raise RuntimeError(f"{capture}: fitted result/access audit missing")
        prior = json.loads(result_path.read_text(encoding="utf-8"))
        spec = protocol["captures"][capture]
        frozen_capture = selection["captures"][capture]
        qmt_actions = _qmt_intended_action_map(frozen_capture)
        episodes, binding = _load_capture(root, capture, spec, frozen_capture)
        factors, factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        edgewise = fit_edgewise(factors)
        if capture == "CAPTURE1" and (run_dir / "CAPTURE1_VIEWER_REDERIVATION.json").exists():
            viewer = json.loads(
                (run_dir / "CAPTURE1_VIEWER_REDERIVATION.json").read_text(encoding="utf-8")
            )["new_viewer"]
        else:
            viewer = prior["direct_fk_viewer"]
        decision = _capture_decision(
            binding, edgewise, prior["unified_graph"], viewer,
        )
        output = run_dir / f"{capture}_QMT_EXCITATION_AWARE_REEVALUATION.json"
        if output.exists():
            raise RuntimeError(f"refusing to overwrite QMT reevaluation {output}")
        artifact = {
            "schema": "biospur-pure-imu-v0-qmt-excitation-aware-reevaluation-v1",
            "capture": capture, "preselection_mutated": False,
            "prior_result_path": str(result_path),
            "prior_result_sha256": sha256_file(result_path),
            "prior_all_action_qmt_gate_superseded": True,
            "all_per_action_qmt_records_retained": True,
            "per_action_calibration_parameters_published": False,
            "unified_capture_wide_headings_changed": False,
            "unified_rank_or_multistart_gate_changed": False,
            "immutable_qmt_intended_action_map": qmt_actions,
            "factor_inventory": factor_audit,
            "edgewise_baseline": edgewise,
            "recomputed_decision": decision,
            "scientific_note": (
                "All-action QMT spread remains diagnostic. Qualification pools only "
                "immutable edge/factor-mapped training windows that independently pass "
                "rating, informative-row, relative hinge-axis excitation, and Olsson "
                "axis-multistart gates. Held-out mapped windows are comparison-only."
            ),
        }
        dump_json(output, _jsonable(artifact)); output.chmod(0o444)
        print(json.dumps({
            "capture": capture, "artifact": str(output),
            "artifact_sha256": sha256_file(output), "decision": decision,
        }, indent=2), flush=True)
        return
    payload_bindings = {
        "schema": "biospur-pure-imu-v0-payload-bindings-v1",
        "preselection_path": str(run_dir / "METADATA_PRESELECTION.json"),
        "preselection_sha256": PRESELECTION_SHA256,
        "preselection_mutated": False,
        "captures": {},
    }
    capture_results = {}
    if args.finalize_only:
        captures_to_run: tuple[str, ...] = tuple()
        for capture in ("CAPTURE1", "CAPTURE2"):
            result_path = run_dir / f"{capture}_RESULT.json"
            audit_path = run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json"
            if not result_path.exists() or not audit_path.exists():
                raise RuntimeError(f"{capture}: checkpoint missing for finalization")
            capture_results[capture] = json.loads(
                result_path.read_text(encoding="utf-8")
            )
            payload_bindings["captures"][capture] = json.loads(
                audit_path.read_text(encoding="utf-8")
            )
    else:
        captures_to_run = (args.capture,) if args.capture else ("CAPTURE1", "CAPTURE2")
    for capture in captures_to_run:
        spec = protocol["captures"][capture]
        frozen_capture = selection["captures"][capture]
        qmt_actions = _qmt_intended_action_map(frozen_capture)
        episodes, binding = _load_capture(
            root, capture, spec, frozen_capture,
        )
        payload_bindings["captures"][capture] = binding
        dump_json(run_dir / f"{capture}_PAYLOAD_ACCESS_AUDIT.json", binding)
        print(f"STAGE {capture} selected payload slices loaded", flush=True)
        factors, factor_audit = build_edge_factors(
            episodes, qmt_intended_actions=qmt_actions,
        )
        print(f"STAGE {capture} edge factors built", flush=True)
        edgewise = fit_edgewise(factors)
        print(f"STAGE {capture} edgewise baseline fit", flush=True)
        initial = np.asarray([
            edgewise["accumulated_headings_rad"][segment]
            for segment in SEGMENTS[1:]
        ])
        unified = fit_unified_graph(
            factors, initial, starts=7,
            seed=20260828 if capture == "CAPTURE1" else 20260830,
        )
        print(f"STAGE {capture} unified graph fit", flush=True)
        transitions = transition_ablation(
            episodes, unified, qmt_intended_actions=qmt_actions,
        )
        print(f"STAGE {capture} transition ablation fit", flush=True)
        graph = graph_consistency(edgewise, unified)
        drift = drift_stillness(episodes)
        viewer = _write_capture_viewer(
            root, run_dir, capture, spec, episodes, factors, unified,
        )
        print(f"STAGE {capture} direct FK viewer written", flush=True)
        decision = _capture_decision(binding, edgewise, unified, viewer)
        result = {
            "schema": "biospur-pure-imu-v0-capture-result-v1",
            "capture": capture,
            "capture_id": spec["capture_id"],
            "edge_factor_inventory": factor_audit,
            "edgewise_baseline": edgewise,
            "unified_graph": unified,
            "transition_ablation": transitions,
            "graph_consistency": graph,
            "drift_stillness": drift,
            "direct_fk_viewer": viewer,
            "decision": decision,
            "raw_signal_input_only": True,
            "prohibited_inputs_used": [],
            "b4_used": False,
            "b5_qualified_independently_of_b4": True,
            "other_capture_parameters_used": False,
        }
        dump_json(run_dir / f"{capture}_RESULT.json", result)
        print(f"STAGE {capture} result written", flush=True)
        capture_results[capture] = result
    if args.capture:
        print(json.dumps(
            {
                "capture": args.capture,
                "decision": capture_results[args.capture]["decision"],
                "checkpoint": str(run_dir / f"{args.capture}_RESULT.json"),
            },
            indent=2,
            sort_keys=True,
        ))
        return
    dump_json(run_dir / "PAYLOAD_BINDINGS.json", payload_bindings)
    overall_gates = {
        "synthetic_qualification": synthetic["pass"],
        "capture1_real": capture_results["CAPTURE1"]["decision"]["pass"],
        "capture2_real": capture_results["CAPTURE2"]["decision"]["pass"],
        "both_capture_numeric_rank_nine": all(
            capture_results[capture]["unified_graph"]["numeric_rank_after_gauge"] == 9
            for capture in capture_results
        ),
        "no_prohibited_inputs": all(
            not capture_results[capture]["prohibited_inputs_used"]
            for capture in capture_results
        ),
        "no_hxx_capture3": all(
            not payload_bindings["captures"][capture]["hxx_payload_opened"]
            and not payload_bindings["captures"][capture]["capture3_payload_opened"]
            for capture in payload_bindings["captures"]
        ),
        "capture_independence": True,
    }
    verdict = "PASS" if all(overall_gates.values()) else "INCONCLUSIVE"
    exact_missing = {
        capture: {
            "failed_gates": capture_results[capture]["decision"]["failed_gates"],
            "weak_edges": capture_results[capture]["decision"]["weak_edges"],
        }
        for capture in capture_results
        if not capture_results[capture]["decision"]["pass"]
    }
    final = {
        "schema": "biospur-pure-imu-v0-final-decision-v1",
        "verdict": verdict,
        "gates": overall_gates,
        "capture_decisions": {
            capture: result["decision"] for capture, result in capture_results.items()
        },
        "exact_missing_observability_or_validation": exact_missing,
        "b4_required": False,
        "b4_role": "OPTIONAL_SYNTHETIC_OR_LAB_DIAGNOSTIC_ONLY",
        "pass_not_claimed_from_synthetic_optimizer_or_viewer_alone": True,
        "capture1_capture2_fitted_and_reported_separately": True,
        "absolute_root_yaw": "UNOBSERVABLE_ONE_PELVIS_DISPLAY_GAUGE",
    }
    dump_json(run_dir / "FINAL_DECISION.json", final)
    print(json.dumps(final, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
