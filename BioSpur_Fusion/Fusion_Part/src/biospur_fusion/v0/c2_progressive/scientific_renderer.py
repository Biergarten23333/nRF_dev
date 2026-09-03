"""Qualified fixed-camera renderer for frozen owner-produced C2 trajectories.

The renderer consumes only the immutable arrays exported by
``C2PipelineRuntime``.  It never estimates geometry, changes a branch, rebases
the pelvis, invokes IK, or repairs a rejected candidate.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.spatial.transform import Rotation

from .functional_geometry import EDGE_SPECS
from .quaternion_contract import qmt_wxyz_to_scipy_active
from .scientific_fk import (
    direct_orientation_avatar_fk,
    landmark_proxy_fk_points,
    landmark_proxy_sensitivity_profiles,
)
from .segment_frames import EdgeConnectionVectors
from .timebase import PersistentPairClockState


POSTFREEZE_CONSTANT_HEADING_PROJECTION_DIAGNOSTIC_SOURCE = (
    "FINAL_FROZEN_CONSTANT_HEADING_PROJECTION_DIAGNOSTIC_NOT_TIME_VARYING_QMT"
)
POSTFREEZE_RETROSPECTIVE_QMT_SOURCE = (
    "POSTFREEZE_RECONSTRUCTION_EXISTING_PERSISTENT_HEADING_OWNER_OFFICIAL_QMT"
)
POSTFREEZE_RETROSPECTIVE_DISPLAY_LABEL = (
    "POST-FREEZE RETROSPECTIVE / NOT CAUSAL PROGRESS EVIDENCE / NOT POSE TRUTH"
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _array_binding(value: np.ndarray) -> dict[str, Any]:
    array = np.ascontiguousarray(np.asarray(value))
    header = json.dumps(
        {"dtype": str(array.dtype), "shape": list(array.shape)}, sort_keys=True,
    ).encode("utf-8")
    return {
        "dtype": str(array.dtype),
        "shape": list(array.shape),
        "sha256": hashlib.sha256(header + array.tobytes()).hexdigest(),
    }


def _validated_landmark_proxy_viewer_authority(
    *,
    workspace: Path,
    settings: Mapping[str, Any],
    owner_amendment_path: Path,
) -> Mapping[str, Any]:
    run_start_binding = settings["execution_contract"]["run_start_contract"]
    run_start_path = (workspace / str(run_start_binding["path"])).resolve()
    run_start_path.relative_to(workspace / "logs")
    if (
        not run_start_path.is_file()
        or _sha256_file(run_start_path) != run_start_binding["sha256"]
    ):
        raise RuntimeError("landmark-proxy viewer run-start authority binding failed")
    run_start = json.loads(run_start_path.read_text(encoding="utf-8"))
    authority_hashes = run_start["authority_hashes"]
    authorities = {
        "raw_anthropometry": workspace
        / "config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json",
        "bilateral_forearm_clarification": workspace
        / "config/biospur_fusion_v0_c2_main_contract_20260829/USER_ANTHROPOMETRY_AMENDMENT_001.json",
        "geometry_contract": workspace
        / "config/biospur_fusion_v0_c2_main_contract_20260829/GEOMETRY_AND_PARAMETER_CONTRACT.json",
    }
    expected_hashes = {
        "raw_anthropometry": authority_hashes["anthropometry_sha256"],
        "bilateral_forearm_clarification": authority_hashes[
            "user_anthropometry_amendment_sha256"
        ],
        "geometry_contract": "9486aeeee9d5cb12c235a2ab39797bea49262cd2bded285ea73fd7b76cc2539b",
    }
    authority_bindings: dict[str, Any] = {}
    for name, path in authorities.items():
        path = path.resolve()
        path.relative_to(workspace)
        observed = _sha256_file(path) if path.is_file() else None
        if observed != expected_hashes[name]:
            raise RuntimeError(f"landmark-proxy viewer {name} hash binding failed")
        authority_bindings[name] = {
            "path": str(path.relative_to(workspace)), "sha256": observed,
        }
    amendment_path = Path(owner_amendment_path).resolve()
    amendment_path.relative_to(workspace / "logs")
    if not amendment_path.is_file() or amendment_path.stat().st_mode & 0o222:
        raise RuntimeError("landmark-proxy viewer owner amendment is absent or mutable")
    amendment = json.loads(amendment_path.read_text(encoding="utf-8"))
    if (
        amendment.get("schema")
        != "biospur-c2-user-fixed-measured-geometry-owner-amendment-v1"
        or amendment.get("direct_user_authority_source_thread_id")
        != "01a03f71-e481-7e21-84f0-3c6cbeb58291"
        or amendment.get("relay_and_independent_monitor_thread_id")
        != "01a04d0f-58f1-7240-b72f-3bf5b44a2156"
        or amendment.get("seal_status")
        != "APPEND_ONLY_NON_SEAL_USER_AUTHORITY;NO_FIT_OR_HELDOUT_AUTHORIZATION"
        or amendment.get("owner_correction", {}).get(
            "raw_pairwise_centers_may_rediscover_absolute_link_length"
        ) is not False
        or amendment.get("scientific_limits", {}).get(
            "profiles_are_exact_internal_bone_truth"
        ) is not False
    ):
        raise RuntimeError("landmark-proxy viewer owner amendment content is invalid")
    for name, binding in authority_bindings.items():
        if amendment["immutable_authorities"][name] != binding:
            raise RuntimeError("landmark-proxy amendment authority hash differs from run-start authority")
    profiles = landmark_proxy_sensitivity_profiles(settings["anthropometric_proxy"])
    profile_rows = [_binding_jsonable_for_renderer(row) for row in profiles]
    if amendment.get("nonprobabilistic_landmark_proxy_sensitivity_profiles") != profile_rows:
        raise RuntimeError("landmark-proxy amendment profile rows differ from frozen settings")
    profile_sha = hashlib.sha256(json.dumps(
        profile_rows, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    return {
        "schema": "biospur-c2-landmark-proxy-viewer-authority-binding-v1",
        "run_start_contract": {
            "path": str(run_start_path.relative_to(workspace)),
            "sha256": _sha256_file(run_start_path),
        },
        "immutable_authorities": authority_bindings,
        "owner_amendment": {
            "path": str(amendment_path.relative_to(workspace)),
            "sha256": _sha256_file(amendment_path),
        },
        "landmark_proxy_settings_semantic_sha256": hashlib.sha256(json.dumps(
            _binding_jsonable_for_renderer(settings["anthropometric_proxy"]),
            sort_keys=True, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")).hexdigest(),
        "nonprobabilistic_profile_rows": profile_rows,
        "nonprobabilistic_profile_rows_semantic_sha256": profile_sha,
        "entered_fit_qmt_centers_frames_branch_likelihood_or_physical_acceptance": False,
    }


def _binding_jsonable_for_renderer(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _binding_jsonable_for_renderer(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_binding_jsonable_for_renderer(item) for item in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _validated_postfreeze_retrospective_authority(
    *,
    workspace: Path,
    manifest: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Authenticate the bounded replay without promoting it to causal evidence."""

    binding = manifest.get("postfreeze_retrospective_source_delta")
    if not isinstance(binding, Mapping):
        raise RuntimeError("post-freeze retrospective manifest lacks its source delta")
    source_delta_path = (workspace / str(binding["path"])).resolve()
    source_delta_path.relative_to(workspace / "logs")
    if (
        not source_delta_path.is_file()
        or source_delta_path.stat().st_mode & 0o222
        or _sha256_file(source_delta_path) != binding.get("sha256")
    ):
        raise RuntimeError("post-freeze retrospective source-delta binding failed")
    source_delta = json.loads(source_delta_path.read_text(encoding="utf-8"))
    replay = manifest.get("structure", {}).get(
        "postfreeze_retrospective_heading_replay"
    )
    if (
        source_delta.get("schema")
        != "biospur-c2-postfreeze-retrospective-heading-source-delta-v1"
        or source_delta.get("direct_user_authority_thread_id")
        != "01a03f71-e481-7e21-84f0-3c6cbeb58291"
        or source_delta.get("relay_and_independent_monitor_thread_id")
        != "01a04d0f-58f1-7240-b72f-3bf5b44a2156"
        or source_delta.get("parent_diagnostic_activation")
        != manifest.get("diagnostic_activation")
        or source_delta.get("parent_authorized_source_delta")
        != manifest.get("authorized_source_delta")
        or source_delta.get("parent_frozen_manifest")
        != manifest.get("parent_frozen_manifest")
        or source_delta.get("parent_frozen_npz")
        != manifest.get("parent_frozen_npz")
        or source_delta.get("parent_prefit_seal")
        != manifest.get("prefit_registry_seal")
        or source_delta.get("settings_semantic_sha256")
        != manifest.get("settings_semantic_sha256")
        or source_delta.get("fit_or_progressive_recomputation_authorized") is not False
        or source_delta.get("causal_progressive_state_mutation_authorized") is not False
        or source_delta.get("heldout_opened") is not False
        or source_delta.get("scientific_pass_authorized") is not False
        or source_delta.get("final_scalar_heading_tiled_over_time") is not False
        or source_delta.get("full_time_varying_qmt_arrays_still_persisted") is not True
        or not isinstance(replay, Mapping)
        or replay.get("source") != POSTFREEZE_RETROSPECTIVE_QMT_SOURCE
        or replay.get("action_count") != 19
        or replay.get("persistent_heading_owner_instance_count") != 1
        or replay.get("official_callable") != "qmt.headingCorrection"
        or replay.get("per_action_reset_or_profile_stitch") is not False
        or replay.get("final_scalar_heading_tiled_over_time") is not False
        or replay.get("causal_progressive_state_modified") is not False
    ):
        raise RuntimeError("post-freeze retrospective source authority is inconsistent")
    effective_source_hashes = source_delta.get("effective_source_hashes")
    if not isinstance(effective_source_hashes, Mapping) or not effective_source_hashes:
        raise RuntimeError("post-freeze retrospective source closure is absent")
    for relative, expected_hash in effective_source_hashes.items():
        path = (workspace / str(relative)).resolve()
        path.relative_to(workspace)
        if not path.is_file() or _sha256_file(path) != expected_hash:
            raise RuntimeError(
                f"post-freeze retrospective source closure changed: {relative}"
            )
    return {
        "schema": "biospur-c2-postfreeze-retrospective-render-authority-v1",
        "source_delta": dict(binding),
        "source": POSTFREEZE_RETROSPECTIVE_QMT_SOURCE,
        "action_count": 19,
        "persistent_heading_owner_instance_count": 1,
        "official_callable": "qmt.headingCorrection",
        "time_varying_heading": True,
        "causal_progressive_state_modified": False,
        "scientific_physical_gate_executed": False,
        "scientific_acceptance_pass": False,
    }


def _rotate_world_from_sensor_wxyz(
    quaternion_wxyz: np.ndarray,
    sensor_vector: np.ndarray,
) -> np.ndarray:
    """Rotate one sensor-frame vector with one stored active wxyz quaternion."""

    quaternion = np.asarray(quaternion_wxyz, dtype=float)
    vector = np.asarray(sensor_vector, dtype=float)
    if quaternion.shape != (4,) or vector.shape != (3,):
        raise ValueError("renderer quaternion/vector shapes are invalid")
    norm = float(np.linalg.norm(quaternion))
    if not np.isfinite(norm) or norm <= np.finfo(float).eps:
        raise ValueError("renderer quaternion is nonfinite or degenerate")
    scalar = quaternion[0] / norm
    imaginary = quaternion[1:] / norm
    return (
        vector
        + 2.0 * scalar * np.cross(imaginary, vector)
        + 2.0 * np.cross(imaginary, np.cross(imaginary, vector))
    )


def _render_sensor_axis_checkpoint(
    *,
    plt: Any,
    arrays: Mapping[str, np.ndarray],
    renderer: Mapping[str, Any],
    segment_frames: Mapping[str, Any],
    workspace: Path,
    output_directory: Path,
    chronological_index: int,
    action: str,
) -> dict[str, Any]:
    """Render frozen continuous-VQF and hinge-axis arrays at schematic slots.

    The slot positions are registered display coordinates only.  They never
    enter segment frames, QMT, rooted propagation, FK, or a physical gate.
    """

    policy = renderer.get("partial_sensor_axis_proxy")
    if (
        not isinstance(policy, Mapping)
        or policy.get("schema")
        != "biospur-c2-real-array-sensor-axis-proxy-renderer-v1"
        or policy.get("schematic_positions_are_scientific_geometry") is not False
        or policy.get("allowed_as_fk_input") is not False
    ):
        raise RuntimeError("partial sensor/axis renderer policy is not registered")
    hardware_to_segment = dict(
        segment_frames["wear_authority"]["identity_hardware_to_segment"]
    )
    schematic_positions = {
        str(segment): np.asarray(value, dtype=float)
        for segment, value in policy["schematic_position_m_by_segment"].items()
    }
    if (
        set(schematic_positions) != set(hardware_to_segment.values())
        or any(value.shape != (3,) for value in schematic_positions.values())
    ):
        raise RuntimeError("partial renderer schematic slot closure is invalid")
    quantile = float(policy["sample_quantile_by_action"][action])
    if not 0.0 <= quantile <= 1.0:
        raise RuntimeError("partial renderer sample quantile is invalid")
    orientation_prefix = f"orientation/{chronological_index:02d}"
    quaternion_by_segment: dict[str, np.ndarray] = {}
    selected_row_by_segment: dict[str, int] = {}
    selected_time_by_segment: dict[str, int] = {}
    gap_only_covariance_trace_by_segment: dict[str, float] = {}
    for node, segment in sorted(hardware_to_segment.items()):
        time = np.asarray(
            arrays[f"{orientation_prefix}/{node}/time_us"], dtype=np.int64,
        )
        quaternion = np.asarray(
            arrays[f"{orientation_prefix}/{node}/quat_world_sensor_wxyz"],
            dtype=float,
        )
        if quaternion.shape != (len(time), 4) or not len(time):
            raise RuntimeError("partial renderer orientation array shapes are invalid")
        # Raw node timers have independent absolute epochs.  The registered
        # action quantile is therefore applied separately to each sealed node
        # array; raw timer magnitudes are retained only as provenance and are
        # never compared as if they shared an epoch.
        row = int(round(quantile * (len(time) - 1)))
        quaternion_by_segment[segment] = quaternion[row]
        selected_row_by_segment[segment] = row
        selected_time_by_segment[segment] = int(time[row])
        gap_covariance = np.asarray(
            arrays[f"{orientation_prefix}/{node}/gap_only_covariance_rad2"],
            dtype=float,
        )
        if gap_covariance.shape != (len(time), 3, 3):
            raise RuntimeError("partial renderer gap-only covariance shape is invalid")
        gap_only_covariance_trace_by_segment[segment] = float(
            np.trace(gap_covariance[row])
        )

    axis_by_segment: dict[str, list[tuple[str, np.ndarray]]] = {
        segment: [] for segment in schematic_positions
    }
    geometry_prefix = f"geometry_checkpoint/{chronological_index:02d}/axis"
    accepted_axis_edges: list[str] = []
    axis_covariance_trace_rad2 = 0.0
    for edge, parent, child in EDGE_SPECS:
        parent_key = f"{geometry_prefix}/{edge}/parent"
        child_key = f"{geometry_prefix}/{edge}/child"
        if parent_key not in arrays or child_key not in arrays:
            continue
        parent_axis = _rotate_world_from_sensor_wxyz(
            quaternion_by_segment[parent], arrays[parent_key],
        )
        child_axis = _rotate_world_from_sensor_wxyz(
            quaternion_by_segment[child], arrays[child_key],
        )
        axis_by_segment[parent].append((edge, parent_axis))
        axis_by_segment[child].append((edge, child_axis))
        accepted_axis_edges.append(edge)
        covariance = np.asarray(
            arrays[f"{geometry_prefix}/{edge}/covariance"], dtype=float,
        )
        if covariance.shape != (4, 4):
            raise RuntimeError("partial renderer functional-axis covariance shape is invalid")
        axis_covariance_trace_rad2 += float(np.trace(covariance))

    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    sensor_colors = tuple(policy["sensor_axis_colors"])
    sensor_labels = tuple(policy["sensor_axis_labels"])
    sensor_length = float(policy["sensor_axis_glyph_length_m"])
    hinge_length = float(policy["functional_axis_glyph_length_m"])
    figure, axes = plt.subplots(
        1, 3,
        figsize=tuple(float(value) for value in renderer["figure_size_inches"]),
        dpi=int(renderer["dpi"]),
    )
    for panel, (view_name, (horizontal_name, vertical_name)) in zip(
        axes, view_specs, strict=True,
    ):
        horizontal = coordinate[horizontal_name]
        vertical = coordinate[vertical_name]
        axis_label_used = False
        for segment in sorted(schematic_positions):
            origin = schematic_positions[segment]
            quaternion = quaternion_by_segment[segment]
            for sensor_axis, color, label in zip(
                np.eye(3), sensor_colors, sensor_labels, strict=True,
            ):
                world_axis = _rotate_world_from_sensor_wxyz(quaternion, sensor_axis)
                endpoint = origin + sensor_length * world_axis
                panel.plot(
                    [origin[horizontal], endpoint[horizontal]],
                    [origin[vertical], endpoint[vertical]],
                    color=color, linewidth=1.2, alpha=0.85,
                    label=f"actual VQF sensor {label}" if segment == "pelvis" else None,
                )
            for edge, world_axis in axis_by_segment[segment]:
                endpoints = np.vstack((
                    origin - 0.5 * hinge_length * world_axis,
                    origin + 0.5 * hinge_length * world_axis,
                ))
                panel.plot(
                    endpoints[:, horizontal], endpoints[:, vertical],
                    color=str(policy["functional_axis_color"]), linewidth=2.4,
                    label=(
                        "accepted functional hinge-axis"
                        if not axis_label_used else None
                    ),
                )
                axis_label_used = True
            panel.scatter(
                [origin[horizontal]], [origin[vertical]], s=16,
                marker="s", color=str(policy["schematic_origin_color"]), zorder=3,
            )
            panel.annotate(
                segment, (origin[horizontal], origin[vertical]),
                xytext=(3, 3), textcoords="offset points", fontsize=5.8,
            )
        panel.set_title(view_name)
        panel.set_xlabel(f"fixed schematic {horizontal_name} (m)")
        panel.set_ylabel(f"fixed schematic {vertical_name} (m)")
        panel.set_xlim(*map(float, renderer["horizontal_limits_m"]))
        panel.set_ylim(*map(float, renderer["vertical_limits_m"]))
        if renderer["equal_aspect"]:
            panel.set_aspect("equal", adjustable="box")
        panel.grid(alpha=0.2)
        if view_name == "FRONT":
            handles, labels = panel.get_legend_handles_labels()
            if handles:
                panel.legend(handles, labels, loc="lower left", fontsize=6.2)
    figure.suptitle(
        "REAL C2 SENSOR/AXIS PROXY — FIXED SCHEMATIC ORIGINS — "
        "NO JOINT CENTERS — NON-ANATOMICAL — NOT PASS\n"
        f"{action} • same-action sample quantile={quantile:.3f} • "
        f"accepted hinge axes={len(set(accepted_axis_edges))}/4",
        color="#b91c1c", fontsize=10.5, fontweight="bold",
    )
    footer_artist = figure.text(
        0.5, 0.012,
        "Actual immutable continuous-VQF quaternion glyphs and accepted functional-axis "
        "arrays at registered display slots; slot locations are not measured geometry.\n"
        "Same-action result-independent quantile mapping; raw node timer epochs are not "
        "compared, and no common absolute physical timestamp is claimed.\n"
        f"Gap-only orientation covariance trace sum="
        f"{sum(gap_only_covariance_trace_by_segment.values()):.4g} rad²; axis covariance "
        f"trace sum={axis_covariance_trace_rad2:.4g} rad².\n"
        "No center, FK, QMT trajectory, anthropometry, IK, rebase, repair, or future pose "
        "is substituted.",
        ha="center", va="bottom", fontsize=6.8, linespacing=1.18,
    )
    figure.subplots_adjust(top=0.79, bottom=0.22, wspace=0.28)
    filename = (
        f"SCIENTIFIC_TRIVIEW_{chronological_index:02d}_{action}_"
        "REAL_SENSOR_AXIS_PROXY.png"
    )
    path = output_directory / filename
    figure.canvas.draw()
    canvas_width, canvas_height = figure.canvas.get_width_height()
    footer_bbox = footer_artist.get_window_extent(
        renderer=figure.canvas.get_renderer(),
    )
    footer_margin_px = 2.0
    footer_within_canvas = bool(
        footer_bbox.x0 >= footer_margin_px
        and footer_bbox.y0 >= footer_margin_px
        and footer_bbox.x1 <= canvas_width - footer_margin_px
        and footer_bbox.y1 <= canvas_height - footer_margin_px
    )
    if not footer_within_canvas:
        plt.close(figure)
        raise RuntimeError("partial sensor/axis renderer footer exceeds the pixel canvas")
    figure.savefig(path, facecolor="white")
    pixel_shape = list(np.asarray(figure.canvas.buffer_rgba()).shape)
    plt.close(figure)
    return {
        "path": str(path.relative_to(workspace)),
        "sha256": _sha256_file(path),
        "chronological_index": int(chronological_index),
        "action": action,
        "common_action_sample_quantile": quantile,
        "display_time_mapping": (
            "RESULT_INDEPENDENT_SAME_ACTION_PER_NODE_QUANTILE;"
            "RAW_NODE_TIMER_EPOCHS_NOT_COMPARED"
        ),
        "common_absolute_physical_timestamp_claimed": False,
        "raw_node_timer_epoch_values_used_for_cross_node_nearest_selection": False,
        "selected_row_by_segment": selected_row_by_segment,
        "selected_time_us_by_segment": selected_time_by_segment,
        "gap_only_orientation_covariance_trace_rad2_by_segment": (
            gap_only_covariance_trace_by_segment
        ),
        "functional_axis_covariance_trace_sum_rad2": axis_covariance_trace_rad2,
        "accepted_functional_axis_edges": sorted(set(accepted_axis_edges)),
        "actual_continuous_vqf_quaternion_arrays_rendered": True,
        "actual_accepted_functional_axis_arrays_rendered": bool(accepted_axis_edges),
        "schematic_origins_are_measured_geometry": False,
        "joint_centers_or_skeleton_rendered": False,
        "qmt_trajectory_rendered": False,
        "footer_bbox_pixels": [
            float(footer_bbox.x0), float(footer_bbox.y0),
            float(footer_bbox.x1), float(footer_bbox.y1),
        ],
        "footer_within_canvas": footer_within_canvas,
        "viewer_rebase_ik_repair_or_anthropometric_geometry": False,
        "pixel_shape_rgba": pixel_shape,
        "status": "NON-ANATOMICAL / NOT PASS",
    }


def load_frozen_render_authority(
    manifest_path: Path,
    *,
    settings: Mapping[str, Any],
) -> tuple[Mapping[str, Any], dict[str, np.ndarray]]:
    """Validate and load one immutable frozen-state manifest/NPZ pair."""

    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file() or manifest_path.stat().st_mode & 0o222:
        raise RuntimeError("scientific renderer requires one immutable frozen-state manifest")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("schema")
        != "biospur-c2-reloadable-frozen-scientific-state-manifest-v1"
        or manifest.get("scientific_state_mutable") is not False
        or manifest.get("threshold_or_parameter_override_allowed") is not False
        or manifest.get("fit_refit_rebase_ik_or_anthropometric_geometry_allowed") is not False
        or manifest.get("heldout_opened_when_exported") is not False
    ):
        raise RuntimeError("frozen-state manifest does not preserve the scientific renderer firewall")
    expected_settings_hash = hashlib.sha256(
        json.dumps(settings, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    ).hexdigest()
    if manifest.get("settings_semantic_sha256") != expected_settings_hash:
        raise RuntimeError("renderer settings differ from the frozen runtime authority")
    workspace = Path(str(settings["execution_contract"]["canonical_workspace"])).resolve()
    npz_path = (workspace / str(manifest["npz"]["path"])).resolve()
    npz_path.relative_to(workspace)
    if not npz_path.is_file() or _sha256_file(npz_path) != manifest["npz"].get("sha256"):
        raise RuntimeError("frozen scientific NPZ immutable hash failed")
    with np.load(npz_path, allow_pickle=False) as archive:
        arrays = {name: np.asarray(archive[name]).copy() for name in archive.files}
    bindings = manifest.get("array_bindings")
    if not isinstance(bindings, Mapping) or set(bindings) != set(arrays):
        raise RuntimeError("frozen renderer array-key closure differs from the manifest")
    mismatches = [name for name, value in arrays.items() if _array_binding(value) != bindings[name]]
    if mismatches:
        raise RuntimeError(f"frozen renderer array bindings failed: {mismatches}")
    return manifest, arrays


def _prefix_key(index: int, branch_id: str) -> str:
    return f"physical_trajectory/{index:02d}/{branch_id}"


def _derive_postfreeze_constant_heading_projection_diagnostic(
    *,
    manifest: Mapping[str, Any],
    arrays: dict[str, np.ndarray],
    settings: Mapping[str, Any],
    chronological_index: int,
    action: str,
) -> tuple[Mapping[str, Mapping[str, Any]], Mapping[str, Any]]:
    """Diagnose a constant final-heading projection on one stored early action.

    This bounded read-only calculation is deliberately not a renderer input:
    one final scalar heading per edge is not a genuine time-varying QMT replay.
    It remains available only to document why the discarded shortcut is not
    scientifically equivalent to ``PersistentHeadingOwner.process_span``.
    """

    authority = manifest["structure"]["frozen_evaluation_authority"]
    branch_ids = tuple(str(value) for value in authority["branch_ids"])
    hard_support = np.asarray(arrays["frozen/branch_hard_support"], dtype=bool)
    branch_weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
    if (
        hard_support.shape != (len(branch_ids),)
        or branch_weights.shape != (len(branch_ids),)
        or not np.array_equal(hard_support, np.asarray(authority["hard_support_mask"], dtype=bool))
        or not np.isclose(np.sum(branch_weights), 1.0, atol=1e-10)
    ):
        raise RuntimeError("retrospective avatar frozen branch authority is inconsistent")
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    segments = {name for _, parent, child in EDGE_SPECS for name in (parent, child)}
    if set(node_by_segment) != segments:
        raise RuntimeError("retrospective avatar does not bind the exact ten sensor identities")
    orientation_prefix = f"orientation/{chronological_index:02d}"
    time_by_segment: dict[str, np.ndarray] = {}
    boot_by_segment: dict[str, np.ndarray] = {}
    for segment, node in node_by_segment.items():
        time = np.asarray(arrays[f"{orientation_prefix}/{node}/time_us"], dtype=np.int64)
        boot = np.asarray(
            arrays[f"{orientation_prefix}/{node}/derived_boot_epoch"], dtype=np.int64,
        )
        quaternion = np.asarray(
            arrays[f"{orientation_prefix}/{node}/quat_world_sensor_wxyz"], dtype=float,
        )
        if (
            time.ndim != 1
            or boot.shape != time.shape
            or quaternion.shape != (len(time), 4)
            or len(time) < 3
            or np.any(np.diff(time) <= 0)
        ):
            raise RuntimeError(f"{segment}: retrospective frozen orientation rows are invalid")
        time_by_segment[segment] = time
        boot_by_segment[segment] = boot
    clock = PersistentPairClockState(
        maximum_abs_drift_ppm=float(settings["timing"]["maximum_abs_drift_ppm"]),
        jitter_floor_s=float(settings["timing"]["jitter_floor_s"]),
    )
    clock.restore(authority["pair_clock_checkpoint"])
    tolerance_us = float(settings["timing"]["clock_match_tolerance_s"]) * 1e6
    expected_step_us = int(round(float(settings["timing"]["sample_period_s"]) * 1e6))
    minimum_span_rows = max(
        3, int(settings["timing"]["minimum_contiguous_span_rows"]),
    )
    pair_rows: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    pair_audit: dict[str, Any] = {}
    for edge, parent, child in EDGE_SPECS:
        parent_time = time_by_segment[parent]
        child_time = time_by_segment[child]
        prediction = clock.predict(
            edge=edge, reference_time_s=float(np.median(parent_time)) * 1e-6,
        )
        predicted_offset_us = float(prediction["predicted_offset_s"]) * 1e6
        parent_indices: list[int] = []
        child_indices: list[int] = []
        last_child = -1
        child_float = child_time.astype(float)
        for parent_index, parent_value in enumerate(parent_time.astype(float)):
            target = parent_value - predicted_offset_us
            insertion = int(np.searchsorted(child_float, target))
            options = [
                value for value in (insertion - 1, insertion)
                if last_child < value < len(child_time)
            ]
            if not options:
                continue
            selected = min(options, key=lambda value: abs(child_float[value] - target))
            if abs(child_float[selected] - target) <= tolerance_us:
                parent_indices.append(parent_index)
                child_indices.append(selected)
                last_child = selected
        pi = np.asarray(parent_indices, dtype=np.int64)
        ci = np.asarray(child_indices, dtype=np.int64)
        if len(pi) < minimum_span_rows:
            raise RuntimeError(f"{edge}: frozen clock leaves no retrospective span")
        breaks = np.flatnonzero(
            (np.diff(parent_time[pi]) != expected_step_us)
            | (np.diff(child_time[ci]) != expected_step_us)
            | (np.diff(boot_by_segment[parent][pi]) != 0)
            | (np.diff(boot_by_segment[child][ci]) != 0)
            | (np.diff(pi) != 1)
            | (np.diff(ci) != 1)
        ) + 1
        boundaries = np.r_[0, breaks, len(pi)]
        retained = [
            np.arange(left, right, dtype=np.int64)
            for left, right in zip(boundaries[:-1], boundaries[1:])
            if right - left >= minimum_span_rows
        ]
        if not retained:
            raise RuntimeError(f"{edge}: no gap-safe retrospective clock correspondence")
        keep = np.concatenate(retained)
        pi = pi[keep]
        ci = ci[keep]
        pair_rows[edge] = (pi, ci)
        pair_audit[edge] = {
            "parent_indices": _array_binding(pi),
            "child_indices": _array_binding(ci),
            "frozen_clock_prediction": prediction,
            "gap_or_boot_boundary_crossings": 0,
            "pair_clock_state_updated": False,
        }
    root_time = time_by_segment["pelvis"]
    root_candidates = np.unique(np.rint(
        np.asarray(settings["scientific_renderer"]["sample_quantiles"], dtype=float)
        * (len(root_time) - 1)
    ).astype(np.int64))
    selected_by_segment: dict[str, list[int]] = {segment: [] for segment in segments}
    accepted_root: list[int] = []
    rejected_root: list[Mapping[str, Any]] = []
    projection_tolerance_s = float(
        settings["physical_candidates"]["maximum_pair_projection_time_error_s"]
    )
    for root_index in root_candidates:
        selected = {"pelvis": int(root_index)}
        rejection: str | None = None
        for edge, parent, child in EDGE_SPECS:
            pi, ci = pair_rows[edge]
            parent_time = time_by_segment[parent]
            distances = np.abs(
                parent_time[pi].astype(float) - float(parent_time[selected[parent]])
            ) * 1e-6
            local = int(np.argmin(distances))
            if float(distances[local]) > projection_tolerance_s:
                rejection = f"{edge}:PARENT_PROJECTION_OUTSIDE_REGISTERED_TOLERANCE"
                break
            selected[child] = int(ci[local])
        if rejection is not None or set(selected) != segments:
            rejected_root.append({"root_source_index": int(root_index), "reason": rejection})
            continue
        accepted_root.append(int(root_index))
        for segment in segments:
            selected_by_segment[segment].append(selected[segment])
    if not accepted_root:
        raise RuntimeError("retrospective avatar has no nine-edge clock-mapped sample")
    selected_arrays = {
        segment: np.asarray(indices, dtype=np.int64)
        for segment, indices in selected_by_segment.items()
    }
    heading_rows = {
        (str(row["branch_id"]), str(row["edge"])): row
        for row in authority["heading_prior"]["edge_state"]
    }
    branch_rows: dict[str, Mapping[str, Any]] = {}
    branch_audit: dict[str, Any] = {}
    for branch_index, branch_id in enumerate(branch_ids):
        if not hard_support[branch_index]:
            continue
        global_delta = {"pelvis": 0.0}
        global_variance = {"pelvis": 0.0}
        edge_heading_audit: dict[str, Any] = {}
        for edge, parent, child in EDGE_SPECS:
            row = heading_rows[(branch_id, edge)]
            expected_state = np.asarray([
                float(row["delta_rad"]), float(row["variance_rad2"]),
                float(row["span_count"]), float(row["observation_count"]),
            ])
            observed_state = np.asarray(
                arrays[f"frozen/heading_edge_state/{branch_id}:{edge}"], dtype=float,
            )
            if not np.array_equal(observed_state, expected_state):
                raise RuntimeError("retrospective avatar heading state differs from manifest")
            global_delta[child] = global_delta[parent] + float(row["delta_rad"])
            global_variance[child] = global_variance[parent] + float(row["variance_rad2"])
            edge_heading_audit[edge] = {
                "delta_rad": float(row["delta_rad"]),
                "variance_rad2": float(row["variance_rad2"]),
                "official_observation_count": int(row["observation_count"]),
                "no_update_when_zero_observations": int(row["observation_count"]) == 0,
            }
        prefix = _prefix_key(chronological_index, branch_id)
        arrays[f"{prefix}/common_physical_time_s"] = (
            root_time[np.asarray(accepted_root, dtype=np.int64)].astype(float) * 1e-6
        )
        for segment in segments:
            node = node_by_segment[segment]
            indices = selected_arrays[segment]
            world_from_sensor = qmt_wxyz_to_scipy_active(
                arrays[f"{orientation_prefix}/{node}/quat_world_sensor_wxyz"][indices]
            ).as_matrix()
            segment_from_sensor = np.asarray(
                arrays[f"frames/{branch_id}/segment_from_sensor/{segment}"], dtype=float,
            )
            if (
                segment_from_sensor.shape != (3, 3)
                or not np.allclose(segment_from_sensor.T @ segment_from_sensor, np.eye(3), atol=1e-8)
                or np.linalg.det(segment_from_sensor) <= 0.0
            ):
                raise RuntimeError(f"{segment}: retrospective final frame is not SO(3)")
            raw_world_from_segment = np.einsum(
                "nij,jk->nik", world_from_sensor, segment_from_sensor.T,
            )
            yaw = Rotation.from_rotvec(np.tile(
                np.asarray([0.0, 0.0, global_delta[segment]], dtype=float),
                (len(indices), 1),
            )).as_matrix()
            arrays[f"{prefix}/world_from_segment/{segment}"] = np.einsum(
                "nij,njk->nik", yaw, raw_world_from_segment,
            )
            arrays[f"{prefix}/segment_from_sensor/{segment}"] = segment_from_sensor
            gap_covariance = np.asarray(
                arrays[f"{orientation_prefix}/{node}/gap_only_covariance_rad2"][indices],
                dtype=float,
            )
            frame_covariance = np.asarray(
                arrays[f"frames/{branch_id}/frame_covariance/{segment}"], dtype=float,
            )
            total = gap_covariance + frame_covariance[None, :, :]
            total[:, 2, 2] += global_variance[segment]
            arrays[f"{prefix}/orientation_covariance/{segment}"] = total
        branch_rows[branch_id] = {
            "source": POSTFREEZE_CONSTANT_HEADING_PROJECTION_DIAGNOSTIC_SOURCE,
            "physically_legal": False,
            "final_frozen_hard_support_retained": True,
            "retrospective_pose_truth_claimed": False,
        }
        branch_audit[branch_id] = {
            "final_frozen_weight": float(branch_weights[branch_index]),
            "edge_heading": edge_heading_audit,
            "world_from_segment_source": (
                "stored continuous VQF quaternion × final frozen sensor/segment frame "
                "× rooted final frozen heading posterior"
            ),
        }
    return branch_rows, {
        "schema": "biospur-c2-postfreeze-constant-heading-projection-diagnostic-v1",
        "action": action,
        "chronological_index": int(chronological_index),
        "source": POSTFREEZE_CONSTANT_HEADING_PROJECTION_DIAGNOSTIC_SOURCE,
        "source_orientation_array_prefix": orientation_prefix,
        "selected_source_indices_by_segment": {
            segment: value.tolist() for segment, value in sorted(selected_arrays.items())
        },
        "selected_source_indices_bindings_by_segment": {
            segment: _array_binding(value) for segment, value in sorted(selected_arrays.items())
        },
        "accepted_root_source_indices": accepted_root,
        "rejected_root_candidates": rejected_root,
        "pair_clock_correspondence": pair_audit,
        "branch_audit": branch_audit,
        "final_frozen_calibration_applied_retrospectively": True,
        "time_varying_qmt_replay_executed": False,
        "eligible_for_direct_orientation_avatar_render": False,
        "qmt_corrected_trajectory_claimed": False,
        "causal_progressive_prefix_or_score_modified": False,
        "future_pose_or_action_truth_used": False,
        "qmt_rerun": False,
        "clock_state_updated": False,
        "fit_refit_reweight_or_threshold_change": False,
    }


def _render_unavailable_checkpoint(
    *,
    plt: Any,
    renderer: Mapping[str, Any],
    workspace: Path,
    output_directory: Path,
    chronological_index: int | None,
    action: str,
    checkpoint_role: str | None,
    reason: str,
    real_fit_not_authorized: bool,
    unavailable_subject: str = "FUNCTIONAL_GEOMETRY",
) -> dict[str, Any]:
    """Render one fixed-camera absence record without inventing geometry."""

    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    figure, axes = plt.subplots(
        1, 3,
        figsize=tuple(float(value) for value in renderer["figure_size_inches"]),
        dpi=int(renderer["dpi"]),
    )
    if unavailable_subject not in {
        "FUNCTIONAL_GEOMETRY", "TRAJECTORY", "ABC_COMPARISON",
    }:
        raise RuntimeError("unavailable checkpoint subject is not registered")
    unavailable_label = {
        "FUNCTIONAL_GEOMETRY": "FUNCTIONAL GEOMETRY UNAVAILABLE",
        "TRAJECTORY": "TRAJECTORY UNAVAILABLE",
        "ABC_COMPARISON": "A/B/C UNAVAILABLE",
    }[unavailable_subject]
    center_lines = [unavailable_label]
    if real_fit_not_authorized:
        center_lines.append("REAL FIT NOT AUTHORIZED")
    center_lines.extend((
        "NO FUTURE GEOMETRY OR POSE SUBSTITUTED"
        if unavailable_subject in {"FUNCTIONAL_GEOMETRY", "ABC_COMPARISON"}
        else "NO FUTURE POSE SUBSTITUTED",
    ))
    for axis, (view_name, (horizontal_name, vertical_name)) in zip(
        axes, view_specs, strict=True,
    ):
        axis.set_title(view_name)
        axis.set_xlabel(f"pelvis-frame {horizontal_name} (m)")
        axis.set_ylabel(f"pelvis-frame {vertical_name} (m)")
        axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
        axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
        if renderer["equal_aspect"]:
            axis.set_aspect("equal", adjustable="box")
        axis.grid(alpha=0.2)
        axis.text(
            0.5, 0.5, "\n".join(center_lines),
            transform=axis.transAxes, ha="center", va="center",
            color="#b91c1c", fontsize=9.5, fontweight="bold",
            bbox={"facecolor": "white", "edgecolor": "#b91c1c", "alpha": 0.9},
        )
    figure.suptitle(
        f"{renderer['status_label']}\n"
        f"{f'{checkpoint_role} • ' if checkpoint_role else ''}"
        f"{action} • registered checkpoint unavailable",
        color="#b91c1c", fontsize=12, fontweight="bold",
    )
    figure.text(
        0.5, 0.01,
        f"Reason: {reason}. Same fixed cameras retained; no trajectory, joint center, "
        "future prefix, IK, rebase, or repair was invented.",
        ha="center", va="bottom", fontsize=8,
    )
    figure.subplots_adjust(top=0.80, bottom=0.17, wspace=0.28)
    filename = (
        f"SCIENTIFIC_TRIVIEW_{chronological_index if chronological_index is not None else 'NA'}_"
        f"{action}_UNAVAILABLE.png"
    )
    path = output_directory / filename
    figure.savefig(path, facecolor="white")
    figure.canvas.draw()
    pixel_shape = list(np.asarray(figure.canvas.buffer_rgba()).shape)
    plt.close(figure)
    return {
        "action": action,
        "chronological_index": chronological_index,
        "checkpoint_role": checkpoint_role,
        "reason": reason,
        "path": str(path.relative_to(workspace)),
        "sha256": _sha256_file(path),
        "pixel_shape_rgba": pixel_shape,
        "status": renderer["status_label"],
        "unavailable_subject": unavailable_subject,
        "functional_geometry_unavailable": unavailable_subject == "FUNCTIONAL_GEOMETRY",
        "trajectory_unavailable": unavailable_subject == "TRAJECTORY",
        "abc_comparison_unavailable": unavailable_subject == "ABC_COMPARISON",
        "real_fit_not_authorized": real_fit_not_authorized,
        "future_geometry_or_pose_substituted": False,
    }


def render_prefit_unavailable_scientific_triviews(
    *,
    milestone_id: str,
    checkpoint_rows: Sequence[Mapping[str, Any]],
    output_directory: Path,
    settings: Mapping[str, Any],
    unavailable_subject: str = "FUNCTIONAL_GEOMETRY",
) -> Mapping[str, Any]:
    """Render a real-run milestone absence without requiring or mimicking a fit.

    This path exists only for an overdue intermediate checkpoint while the
    immutable prefit/activation gates still forbid real fitting.  It shares the
    registered camera, canvas, labels, and output owner with the scientific
    renderer, but accepts no trajectory arrays at all.
    """

    milestone_id = str(milestone_id)
    if milestone_id not in {"P2", "P3"}:
        raise RuntimeError("prefit unavailable milestone must be P2 or P3")
    renderer = settings["scientific_renderer"]
    if renderer.get("schema") != "biospur-c2-registered-scientific-renderer-settings-v1":
        raise RuntimeError("scientific renderer settings schema is not registered")
    workspace = Path(str(settings["execution_contract"]["canonical_workspace"])).resolve()
    output_directory = Path(output_directory).resolve()
    output_directory.relative_to(workspace / "logs")
    output_directory.mkdir(parents=True, exist_ok=False)
    chronology = tuple(str(value) for value in settings["execution_contract"][
        "chronological_actions"
    ])
    registered = frozenset(str(value) for value in renderer["checkpoint_actions"])
    normalized_rows = []
    for row in checkpoint_rows:
        action = str(row["action"])
        index = int(row["chronological_index"])
        if action not in registered or index < 0 or index >= len(chronology):
            raise RuntimeError("prefit unavailable checkpoint is not registered")
        if chronology[index] != action:
            raise RuntimeError("prefit unavailable checkpoint chronology mismatch")
        role = str(row["checkpoint_role"])
        if role not in {
            "INITIAL",
            "REPRESENTATIVE_UPPER",
            "REPRESENTATIVE_LEFT_LOWER",
            "REPRESENTATIVE_RIGHT_LOWER",
            "SQUAT",
            "FINAL",
        }:
            raise RuntimeError("prefit unavailable checkpoint role is not registered")
        normalized_rows.append((index, action, role))
    if len(normalized_rows) != len(set(normalized_rows)) or not normalized_rows:
        raise RuntimeError("prefit unavailable checkpoints must be unique and nonempty")

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse

    reason = "PREFIT_SCIENTIFIC_GATES_OPEN_AND_REAL_FIT_NOT_AUTHORIZED"
    artifacts = [
        _render_unavailable_checkpoint(
            plt=plt,
            renderer=renderer,
            workspace=workspace,
            output_directory=output_directory,
            chronological_index=index,
            action=action,
            checkpoint_role=role,
            reason=reason,
            real_fit_not_authorized=True,
            unavailable_subject=unavailable_subject,
        )
        for index, action, role in normalized_rows
    ]
    return {
        "schema": "biospur-c2-prefit-unavailable-scientific-triview-result-v2",
        "milestone_id": milestone_id,
        "artifacts": artifacts,
        "checkpoint_count": len(artifacts),
        "renderer_settings_semantic_sha256": hashlib.sha256(
            json.dumps(
                renderer, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "identical_camera_limits_and_renderer_for_every_artifact": True,
        "trajectory_array_input_count": 0,
        "unavailable_subject": unavailable_subject,
        "payload_reread": False,
        "synthetic_or_future_pose_substituted": False,
        "milestone_acceptance": False,
        "scientific_acceptance_pass": False,
    }


def render_prefit_unavailable_abc_comparison(
    *,
    output_directory: Path,
    settings: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Render three fixed-camera P4 absence slots without scientific inputs."""

    renderer = settings["scientific_renderer"]
    if renderer.get("schema") != "biospur-c2-registered-scientific-renderer-settings-v1":
        raise RuntimeError("scientific renderer settings schema is not registered")
    workspace = Path(str(settings["execution_contract"]["canonical_workspace"])).resolve()
    output_directory = Path(output_directory).resolve()
    output_directory.relative_to(workspace / "logs")
    output_directory.mkdir(parents=True, exist_ok=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    reason = "PREFIT_SCIENTIFIC_GATES_OPEN_AND_REAL_FIT_NOT_AUTHORIZED"
    artifacts = [
        _render_unavailable_checkpoint(
            plt=plt,
            renderer=renderer,
            workspace=workspace,
            output_directory=output_directory,
            chronological_index=None,
            action=f"MODEL_{slot}",
            checkpoint_role=f"P4 A/B/C COMPARISON • SLOT {slot}",
            reason=reason,
            real_fit_not_authorized=True,
            unavailable_subject="ABC_COMPARISON",
        )
        for slot in ("A", "B", "C")
    ]
    return {
        "schema": "biospur-c2-p4-prefit-unavailable-abc-comparison-v1",
        "milestone_id": "P4",
        "artifacts": artifacts,
        "slots": ["A", "B", "C"],
        "slot_count": len(artifacts),
        "renderer_settings_semantic_sha256": hashlib.sha256(
            json.dumps(
                renderer, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ).encode("utf-8")
        ).hexdigest(),
        "identical_camera_limits_and_renderer_for_every_artifact": True,
        "common_physical_timestamp_selected": False,
        "trajectory_array_input_count": 0,
        "geometry_array_input_count": 0,
        "payload_reread": False,
        "synthetic_or_future_geometry_or_pose_substituted": False,
        "ik_rebase_or_repair_used": False,
        "milestone_acceptance": False,
        "scientific_acceptance_pass": False,
    }


def _validated_source_display(
    *,
    physical_source: str,
    manifest: Mapping[str, Any],
    renderer: Mapping[str, Any],
    diagnostic_source_verified: bool = False,
    postfreeze_retrospective_source_verified: bool = False,
) -> tuple[str, bool]:
    policy = renderer.get("source_label_policy", {})
    official = str(policy.get("official_qmt_source", ""))
    synthetic = str(policy.get("synthetic_fixture_source", ""))
    if physical_source == official:
        if diagnostic_source_verified:
            return str(policy.get(
                "real_diagnostic_label",
                "REAL C2 TRAINING-RANGE DIAGNOSTIC / NOT FRESH-VERIFIED",
            )), False
        fresh = manifest.get("fresh_verification")
        if (
            policy.get("official_label_requires_manifest_fresh_verification_pass") is not True
            or not isinstance(fresh, Mapping)
            or fresh.get("schema")
            != policy.get("official_label_required_fresh_verification_schema")
            or fresh.get("scope") != policy.get("official_label_required_fresh_scope")
            or fresh.get("pass") is not True
            or fresh.get("primary_execution_role") != "PRIMARY_CAUSAL"
            or fresh.get("fresh_execution_role") != "FRESH_RAW_RECOMPUTATION"
            or fresh.get("caller_attested_scientific_booleans_consumed") is not False
            or not isinstance(fresh.get("comparisons"), Mapping)
            or not fresh["comparisons"]
            or not all(value is True for value in fresh["comparisons"].values())
            or not isinstance(fresh.get("array_allclose"), Mapping)
            or not fresh["array_allclose"]
            or not all(value is True for value in fresh["array_allclose"].values())
            or not isinstance(fresh.get("causal_prefix_comparison"), Mapping)
            or fresh["causal_prefix_comparison"].get("pass") is not True
            or fresh.get("primary_reader_session_id")
            == fresh.get("fresh_reader_session_id")
        ):
            raise RuntimeError(
                "official QMT renderer label requires the exact fresh-bound manifest verification"
            )
        return str(policy["official_qmt_label"]), True
    if physical_source == synthetic:
        return str(policy["synthetic_fixture_label"]), False
    if physical_source == POSTFREEZE_RETROSPECTIVE_QMT_SOURCE:
        if not postfreeze_retrospective_source_verified:
            raise RuntimeError(
                "post-freeze retrospective renderer label requires its exact owner authority"
            )
        return POSTFREEZE_RETROSPECTIVE_DISPLAY_LABEL, False
    raise RuntimeError("scientific renderer encountered an unregistered physical trajectory source")


def _direct_two_sided_fk_points(
    arrays: Mapping[str, np.ndarray],
    *,
    prefix: str,
    sample_index: int,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], float]:
    positions: dict[str, np.ndarray] = {"pelvis": np.zeros(3, dtype=float)}
    joints: dict[str, np.ndarray] = {}
    maximum_closure = 0.0
    for edge, parent, child in EDGE_SPECS:
        parent_rotation = np.asarray(
            arrays[f"{prefix}/world_from_segment/{parent}"][sample_index], dtype=float,
        )
        child_rotation = np.asarray(
            arrays[f"{prefix}/world_from_segment/{child}"][sample_index], dtype=float,
        )
        for segment, rotation in ((parent, parent_rotation), (child, child_rotation)):
            if (
                rotation.shape != (3, 3)
                or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
                or np.linalg.det(rotation) <= 0.0
            ):
                raise RuntimeError(f"{segment}: frozen renderer rotation is not proper SO(3)")
        parent_segment_from_sensor = np.asarray(
            arrays[f"{prefix}/segment_from_sensor/{parent}"], dtype=float,
        )
        child_segment_from_sensor = np.asarray(
            arrays[f"{prefix}/segment_from_sensor/{child}"], dtype=float,
        )
        parent_vector_sensor = np.asarray(
            arrays[f"{prefix}/connection/{edge}/parent"], dtype=float,
        )
        child_vector_sensor = np.asarray(
            arrays[f"{prefix}/connection/{edge}/child"], dtype=float,
        )
        if parent_vector_sensor.shape != (3,) or child_vector_sensor.shape != (3,):
            raise RuntimeError("scientific renderer requires two full vector3 connections per edge")
        joint_parent = (
            positions[parent]
            + parent_rotation @ parent_segment_from_sensor @ parent_vector_sensor
        )
        positions[child] = (
            joint_parent
            - child_rotation @ child_segment_from_sensor @ child_vector_sensor
        )
        joint_child = (
            positions[child]
            + child_rotation @ child_segment_from_sensor @ child_vector_sensor
        )
        joints[edge] = joint_parent
        maximum_closure = max(maximum_closure, float(np.linalg.norm(joint_parent - joint_child)))
    if set(positions) != {name for _, parent, child in EDGE_SPECS for name in (parent, child)}:
        raise RuntimeError("scientific renderer did not produce the exact connected ten-segment tree")
    if maximum_closure > 1e-10:
        raise RuntimeError("scientific renderer two-sided shared-joint closure failed")
    return positions, joints, maximum_closure


def _direct_orientation_avatar_points(
    arrays: Mapping[str, np.ndarray],
    *,
    prefix: str,
    sample_index: int,
    profile: Mapping[str, Any],
) -> tuple[
    dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], Mapping[str, Any]
]:
    """Compatibility adapter for the corrected full-R3 viewer owner.

    The legacy function name is retained for old frozen-evidence callers, but
    the implementation no longer calls ``direct_orientation_avatar_fk``.
    """

    result = _functional_landmark_proxy_fk_from_frozen_arrays(
        arrays, prefix=prefix, sample_index=sample_index, profile=profile,
    )
    hip_mid = 0.5 * (
        result.shared_joint_positions_m["hip_left"]
        + result.shared_joint_positions_m["hip_right"]
    )
    shoulder_mid = 0.5 * (
        result.shared_joint_positions_m["shoulder_left"]
        + result.shared_joint_positions_m["shoulder_right"]
    )
    landmarks = {
        "pelvis_sensor_surface_ref": result.segment_sensor_positions_m["pelvis"],
        "torso_sensor_surface_ref": result.segment_sensor_positions_m["torso"],
        "hip_mid_functional": hip_mid,
        "shoulder_mid_functional": shoulder_mid,
        **{
            f"shoulder_{side}_joint": result.shared_joint_positions_m[f"shoulder_{side}"]
            for side in ("left", "right")
        },
        **{
            f"hip_{side}_joint": result.shared_joint_positions_m[f"hip_{side}"]
            for side in ("left", "right")
        },
        **{
            f"elbow_{side}": result.shared_joint_positions_m[f"elbow_{side}"]
            for side in ("left", "right")
        },
        **{
            f"knee_{side}": result.shared_joint_positions_m[f"knee_{side}"]
            for side in ("left", "right")
        },
        **{
            f"wrist_{side}": result.distal_landmark_positions_m[f"wrist_{side}"]
            for side in ("left", "right")
        },
        **{
            f"ankle_{side}": result.distal_landmark_positions_m[f"ankle_{side}"]
            for side in ("left", "right")
        },
    }
    line_endpoints = {
        "functional_spine": ("hip_mid_functional", "shoulder_mid_functional"),
        "shoulder_crossbar_left": ("shoulder_mid_functional", "shoulder_left_joint"),
        "shoulder_crossbar_right": ("shoulder_mid_functional", "shoulder_right_joint"),
        "hip_crossbar_left": ("hip_mid_functional", "hip_left_joint"),
        "hip_crossbar_right": ("hip_mid_functional", "hip_right_joint"),
        **{
            f"upper_arm_{side}": (f"shoulder_{side}_joint", f"elbow_{side}")
            for side in ("left", "right")
        },
        **{
            f"forearm_{side}": (f"elbow_{side}", f"wrist_{side}")
            for side in ("left", "right")
        },
        **{
            f"thigh_{side}": (f"hip_{side}_joint", f"knee_{side}")
            for side in ("left", "right")
        },
        **{
            f"shank_{side}": (f"knee_{side}", f"ankle_{side}")
            for side in ("left", "right")
        },
    }
    lines = {
        name: np.vstack((landmarks[start], landmarks[end]))
        for name, (start, end) in line_endpoints.items()
    }
    references = {
        f"{edge}_{segment}_sensor_to_joint": np.vstack((
            result.segment_sensor_positions_m[segment],
            result.shared_joint_positions_m[edge],
        ))
        for edge, parent, child in EDGE_SPECS
        for segment in (parent, child)
    }
    report = {
        **dict(result.report),
        "owner": "FUNCTIONAL_CONNECTION_LANDMARK_PROXY_FK",
        "legacy_direct_orientation_avatar_called": False,
        "surface_sensor_positions_m": {
            key: value.tolist() for key, value in result.segment_sensor_positions_m.items()
        },
        "surface_sensor_position_covariance_m2": {
            key: value.tolist()
            for key, value in result.segment_sensor_position_covariance_m2.items()
        },
        "shared_joint_position_covariance_m2": {
            key: value.tolist()
            for key, value in result.shared_joint_position_covariance_m2.items()
        },
    }
    return (
        landmarks,
        lines,
        references,
        report,
    )


def _functional_landmark_proxy_fk_from_frozen_arrays(
    arrays: Mapping[str, np.ndarray],
    *,
    prefix: str,
    sample_index: int,
    profile: Mapping[str, Any],
):
    """Use frozen full-R3 levers; never promote surface sensors to joints.

    This is the corrected derived-viewer boundary.  Sensor positions and
    shared joints remain separate outputs of ``landmark_proxy_fk_points``.
    The registered 0.280 m surface sensor-to-sensor observation is retained in
    provenance but is not used as a torso axis, torso length, or joint offset.
    """

    segments = {name for _, parent, child in EDGE_SPECS for name in (parent, child)}
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
    return landmark_proxy_fk_points(
        root_sensor_position_m=np.zeros(3, dtype=float),
        world_from_segment={
            segment: np.asarray(
                arrays[f"{prefix}/world_from_segment/{segment}"][sample_index],
                dtype=float,
            )
            for segment in segments
        },
        segment_from_sensor={
            segment: np.asarray(
                arrays[f"{prefix}/segment_from_sensor/{segment}"], dtype=float,
            )
            for segment in segments
        },
        connection_vectors_by_edge=connections,
        profile=profile,
    )


def _materialize_postfreeze_bilateral_flexion_sample(
    *,
    manifest: Mapping[str, Any],
    arrays: dict[str, np.ndarray],
    settings: Mapping[str, Any],
    chronological_index: int,
    selection_branch_id: str,
) -> Mapping[str, Any]:
    """Materialize one exact rooted replay row selected by bilateral knee flexion.

    The replay NPZ deliberately stores only three display samples in each
    ``physical_trajectory`` group, but it retains the full continuous
    orientation stream, every official-QMT span's exact source-row maps, and
    the full rooted heading trajectory.  This viewer-only adapter composes
    those maps without nearest-row lookup or interpolation.  It changes only
    the in-memory renderer copy; the immutable replay NPZ remains untouched.
    """

    support = manifest["structure"]["physical_trajectory_support"].get(
        str(chronological_index), {}
    )
    if selection_branch_id not in support or len(support) != 4:
        raise RuntimeError("bilateral-flexion viewer requires the four retained replay branches")
    node_by_segment = {
        str(row["body_segment"]): str(row["hardware_id"])
        for row in settings["segment_frames"]["wear_authority"]["rows"]
    }
    segments = {name for _, parent, child in EDGE_SPECS for name in (parent, child)}
    if set(node_by_segment) != segments:
        raise RuntimeError("bilateral-flexion viewer sensor identity binding is incomplete")

    orientation_prefix = f"orientation/{chronological_index:02d}"
    pelvis_time_us = np.asarray(
        arrays[f"{orientation_prefix}/{node_by_segment['pelvis']}/time_us"],
        dtype=np.int64,
    )
    if len(pelvis_time_us) < 3 or np.any(np.diff(pelvis_time_us) <= 0):
        raise RuntimeError("bilateral-flexion viewer pelvis time grid is invalid")
    root_by_time_us = {int(value): index for index, value in enumerate(pelvis_time_us)}
    if len(root_by_time_us) != len(pelvis_time_us):
        raise RuntimeError("bilateral-flexion viewer pelvis time grid contains duplicates")

    segment_source_by_root: dict[str, dict[int, int]] = {
        "pelvis": {index: index for index in range(len(pelvis_time_us))},
    }
    common_root_rows = set(range(len(pelvis_time_us)))
    edge_map_audit: dict[str, Any] = {}
    for edge, parent, child in EDGE_SPECS:
        span_prefix = (
            f"heading/{chronological_index:02d}/{selection_branch_id}/{edge}/"
        )
        span_bases = sorted({
            name.rsplit("/", 1)[0]
            for name in arrays
            if name.startswith(span_prefix)
            and name.endswith("/common_physical_time_s")
        })
        if not span_bases:
            raise RuntimeError(f"{edge}: bilateral-flexion viewer has no official-QMT span")
        child_map: dict[int, int] = {}
        parent_map = segment_source_by_root[parent]
        parent_all: list[np.ndarray] = []
        child_all: list[np.ndarray] = []
        root_all: list[np.ndarray] = []
        for base in span_bases:
            common_time_s = np.asarray(
                arrays[f"{base}/common_physical_time_s"], dtype=float,
            )
            parent_indices = np.asarray(
                arrays[f"{base}/selected_parent_source_indices"], dtype=np.int64,
            )
            child_indices = np.asarray(
                arrays[f"{base}/selected_child_source_indices"], dtype=np.int64,
            )
            if (
                common_time_s.ndim != 1
                or parent_indices.shape != common_time_s.shape
                or child_indices.shape != common_time_s.shape
                or np.any(np.diff(common_time_s) <= 0.0)
                or np.any(np.diff(parent_indices) != 1)
                or np.any(np.diff(child_indices) != 1)
            ):
                raise RuntimeError(f"{edge}: official-QMT span source maps are invalid")
            common_time_us = np.rint(common_time_s * 1e6).astype(np.int64)
            if np.max(np.abs(common_time_s - common_time_us.astype(float) * 1e-6)) > 2e-12:
                raise RuntimeError(f"{edge}: official-QMT span is not on the exact integer timer grid")
            root_indices = np.asarray([
                root_by_time_us.get(int(value), -1) for value in common_time_us
            ], dtype=np.int64)
            if np.any(root_indices < 0) or np.any(np.diff(root_indices) != 1):
                raise RuntimeError(f"{edge}: official-QMT span is not an exact pelvis-row map")
            for root_index, parent_index, child_index in zip(
                root_indices, parent_indices, child_indices, strict=True,
            ):
                if parent_map.get(int(root_index)) != int(parent_index):
                    raise RuntimeError(
                        f"{edge}: rooted parent source index differs from the official-QMT map"
                    )
                previous = child_map.setdefault(int(root_index), int(child_index))
                if previous != int(child_index):
                    raise RuntimeError(f"{edge}: overlapping official-QMT spans disagree")
            parent_all.append(parent_indices)
            child_all.append(child_indices)
            root_all.append(root_indices)
        segment_source_by_root[child] = child_map
        common_root_rows.intersection_update(child_map)
        parent_rows = np.concatenate(parent_all)
        child_rows = np.concatenate(child_all)
        root_rows = np.concatenate(root_all)
        edge_map_audit[edge] = {
            "official_qmt_span_count": len(span_bases),
            "exact_rooted_row_count": len(child_map),
            "selected_parent_source_indices": _array_binding(parent_rows),
            "selected_child_source_indices": _array_binding(child_rows),
            "pelvis_source_indices": _array_binding(root_rows),
            "nearest_row_or_interpolation_used": False,
            "gap_or_span_boundary_crossed": False,
        }
    root_rows = np.asarray(sorted(common_root_rows), dtype=np.int64)
    if len(root_rows) < 3:
        raise RuntimeError("bilateral-flexion viewer has no full nine-edge exact row support")

    def world_from_segment(branch_id: str, segment: str) -> np.ndarray:
        source_indices = np.asarray([
            segment_source_by_root[segment][int(root)] for root in root_rows
        ], dtype=np.int64)
        quaternion = np.asarray(
            arrays[
                f"{orientation_prefix}/{node_by_segment[segment]}/quat_world_sensor_wxyz"
            ][source_indices],
            dtype=float,
        )
        world_from_sensor = qmt_wxyz_to_scipy_active(quaternion).as_matrix()
        segment_from_sensor = np.asarray(
            arrays[f"frames/{branch_id}/segment_from_sensor/{segment}"], dtype=float,
        )
        if (
            segment_from_sensor.shape != (3, 3)
            or not np.allclose(
                segment_from_sensor.T @ segment_from_sensor, np.eye(3), atol=1e-8,
            )
            or np.linalg.det(segment_from_sensor) <= 0.0
        ):
            raise RuntimeError(f"{segment}: frozen replay frame is not proper SO(3)")
        raw_world_from_segment = np.einsum(
            "nij,jk->nik", world_from_sensor, segment_from_sensor.T,
        )
        trajectory_prefix = f"trajectory/{chronological_index:02d}/{branch_id}"
        trajectory_time_s = np.asarray(
            arrays[f"{trajectory_prefix}/common_physical_time_s"], dtype=float,
        )
        if (
            trajectory_time_s.shape != pelvis_time_us.shape
            or np.max(np.abs(
                trajectory_time_s - pelvis_time_us.astype(float) * 1e-6
            )) > 2e-12
        ):
            raise RuntimeError("rooted heading trajectory does not use the exact pelvis grid")
        delta = np.asarray(
            arrays[f"{trajectory_prefix}/segment_global_delta/{segment}"], dtype=float,
        )[root_rows]
        yaw = Rotation.from_rotvec(np.column_stack((
            np.zeros(len(delta)), np.zeros(len(delta)), delta,
        ))).as_matrix()
        return np.einsum("nij,njk->nik", yaw, raw_world_from_segment)

    thigh_left = world_from_segment(selection_branch_id, "thigh_left")
    shank_left = world_from_segment(selection_branch_id, "shank_left")
    thigh_right = world_from_segment(selection_branch_id, "thigh_right")
    shank_right = world_from_segment(selection_branch_id, "shank_right")
    proximal_to_distal = np.asarray([0.0, 0.0, -1.0], dtype=float)

    def knee_flexion_deg(proximal: np.ndarray, distal: np.ndarray) -> np.ndarray:
        proximal_axis = np.einsum("nij,j->ni", proximal, proximal_to_distal)
        distal_axis = np.einsum("nij,j->ni", distal, proximal_to_distal)
        cosine = np.sum(proximal_axis * distal_axis, axis=1)
        return np.rad2deg(np.arccos(np.clip(cosine, -1.0, 1.0)))

    left_flexion_deg = knee_flexion_deg(thigh_left, shank_left)
    right_flexion_deg = knee_flexion_deg(thigh_right, shank_right)
    bilateral_score_deg = np.minimum(left_flexion_deg, right_flexion_deg)
    selected_position = int(np.argmax(bilateral_score_deg))
    selected_root = int(root_rows[selected_position])
    selected_time_s = float(pelvis_time_us[selected_root]) * 1e-6

    branch_materialization: dict[str, Any] = {}
    for branch_id in sorted(support):
        prefix = _prefix_key(chronological_index, branch_id)
        original_time = np.asarray(arrays[f"{prefix}/common_physical_time_s"], dtype=float)
        if original_time.shape != (3,):
            raise RuntimeError("bilateral-flexion adapter requires the retained three-sample audit")
        segment_bindings: dict[str, Any] = {}
        for segment in sorted(segments):
            full_rotation = world_from_segment(branch_id, segment)
            arrays[f"{prefix}/world_from_segment/{segment}"] = full_rotation[
                selected_position:selected_position + 1
            ].copy()
            source_index = segment_source_by_root[segment][selected_root]
            arrays[f"{prefix}/segment_source_indices/{segment}"] = np.asarray(
                [source_index], dtype=np.int64,
            )
            original_covariance = np.asarray(
                arrays[f"{prefix}/orientation_covariance/{segment}"], dtype=float,
            )
            if original_covariance.shape != (3, 3, 3):
                raise RuntimeError("bilateral-flexion adapter covariance audit shape changed")
            conservative_covariance = np.sum(original_covariance, axis=0)
            if (
                not np.all(np.isfinite(conservative_covariance))
                or np.min(np.linalg.eigvalsh(conservative_covariance)) < -1e-8
            ):
                raise RuntimeError("bilateral-flexion covariance upper envelope is not PSD")
            arrays[f"{prefix}/orientation_covariance/{segment}"] = (
                conservative_covariance[None].copy()
            )
            segment_bindings[segment] = {
                "source_index": int(source_index),
                "world_from_segment": _array_binding(
                    arrays[f"{prefix}/world_from_segment/{segment}"]
                ),
                "orientation_covariance": _array_binding(
                    arrays[f"{prefix}/orientation_covariance/{segment}"]
                ),
            }
        arrays[f"{prefix}/common_physical_time_s"] = np.asarray(
            [selected_time_s], dtype=float,
        )
        branch_materialization[branch_id] = {
            "segments": segment_bindings,
            "selected_rooted_heading_variance_rad2_by_segment": {
                segment: float(np.asarray(arrays[
                    f"trajectory/{chronological_index:02d}/{branch_id}/"
                    f"segment_global_variance/{segment}"
                ], dtype=float)[selected_root])
                for segment in sorted(segments)
            },
        }

    return {
        "schema": "biospur-c2-postfreeze-bilateral-flexion-viewer-selection-v1",
        "chronological_index": int(chronological_index),
        "selection_rule": (
            "ARGMAX_MIN_LEFT_RIGHT_KNEE_FLEXION_DEG_ON_EXACT_NINE_EDGE_"
            "OFFICIAL_QMT_ROOTED_PELVIS_ROWS;EARLIEST_TIE"
        ),
        "selection_branch_id": selection_branch_id,
        "limb_longitudinal_axis_semantics": (
            "SEGMENT_MINUS_Z_IS_PROXIMAL_TO_DISTAL;NO_MANUAL_SIGN_FLIP"
        ),
        "common_exact_rooted_pelvis_rows": _array_binding(root_rows),
        "common_exact_rooted_pelvis_row_count": len(root_rows),
        "selected_common_position": selected_position,
        "selected_pelvis_source_row": selected_root,
        "selected_common_physical_time_s": selected_time_s,
        "selected_action_fraction_on_full_pelvis_grid": (
            selected_root / float(len(pelvis_time_us) - 1)
        ),
        "selected_left_knee_flexion_deg": float(left_flexion_deg[selected_position]),
        "selected_right_knee_flexion_deg": float(right_flexion_deg[selected_position]),
        "bilateral_minimum_flexion_p90_deg": float(
            np.quantile(bilateral_score_deg, 0.9)
        ),
        "left_knee_maximum_flexion_deg": float(np.max(left_flexion_deg)),
        "right_knee_maximum_flexion_deg": float(np.max(right_flexion_deg)),
        "edge_exact_source_maps": edge_map_audit,
        "retained_branch_ids": sorted(support),
        "branch_materialization": branch_materialization,
        "original_three_sample_covariance_policy": (
            "PSD_SUM_UPPER_ENVELOPE_REUSED_AT_EXACT_SELECTED_ROW;NO_UNCERTAINTY_SUPPRESSION"
        ),
        "nearest_row_or_interpolation_used": False,
        "payload_qmt_fit_or_progressive_rerun": False,
        "manual_pose_ik_rebase_retarget_or_repair": False,
        "immutable_replay_npz_modified": False,
        "scientific_acceptance_pass": False,
    }


def render_registered_scientific_triviews(
    *,
    manifest_path: Path,
    output_directory: Path,
    settings: Mapping[str, Any],
    authorized_renderer_source_delta_path: Path | None = None,
    viewer_geometry_product: str = "SENSOR_ORIGIN_TWO_SIDED_CONNECTION_DIAGNOSTIC",
    landmark_proxy_owner_amendment_path: Path | None = None,
    direct_avatar_debug_pair_only: bool = False,
    postfreeze_retrospective_early_only: bool = False,
) -> Mapping[str, Any]:
    """Render the registered action/sample/branch set with fixed cameras."""

    manifest, arrays = load_frozen_render_authority(manifest_path, settings=settings)
    renderer = settings["scientific_renderer"]
    if renderer.get("schema") != "biospur-c2-registered-scientific-renderer-settings-v1":
        raise RuntimeError("scientific renderer settings schema is not registered")
    workspace = Path(str(settings["execution_contract"]["canonical_workspace"])).resolve()
    output_directory = Path(output_directory).resolve()
    output_directory.relative_to(workspace / "logs")
    output_directory.mkdir(parents=True, exist_ok=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    action_by_index = {
        int(row["chronological_index"]): str(row["action"])
        for row in manifest["structure"]["progressive_prefixes"]
    }
    physical_support = manifest["structure"].get("physical_trajectory_support", {})
    diagnostic_source_verified = False
    postfreeze_retrospective_authority: Mapping[str, Any] | None = None
    postfreeze_retrospective_manifest = isinstance(
        manifest.get("postfreeze_retrospective_source_delta"), Mapping,
    )
    postfreeze_render_scope = (
        "FOUR_EARLY_POSTFREEZE_RETROSPECTIVE_DIRECT_ORIENTATION_AVATARS"
        if postfreeze_retrospective_early_only
        else (
            "POSTFREEZE_RETROSPECTIVE_SQUAT_AND_FINAL_DEBUG_PAIR"
            if direct_avatar_debug_pair_only else None
        )
    )
    renderer_source_delta_binding: dict[str, str] | None = None
    if manifest.get("diagnostic_execution_role") == "REAL_DIAGNOSTIC":
        binding = manifest.get("diagnostic_activation")
        if not isinstance(binding, Mapping):
            raise RuntimeError("diagnostic frozen manifest lacks its activation binding")
        activation_path = (workspace / str(binding["path"])).resolve()
        activation_path.relative_to(workspace)
        if (
            not activation_path.is_file()
            or _sha256_file(activation_path) != binding.get("sha256")
        ):
            raise RuntimeError("diagnostic renderer activation binding failed")
        activation = json.loads(activation_path.read_text(encoding="utf-8"))
        source_delta_binding = manifest.get("authorized_source_delta")
        if not isinstance(source_delta_binding, Mapping):
            raise RuntimeError("diagnostic frozen manifest lacks its source-delta binding")
        source_delta_path = (workspace / str(source_delta_binding["path"])).resolve()
        source_delta_path.relative_to(workspace)
        if (
            not source_delta_path.is_file()
            or _sha256_file(source_delta_path) != source_delta_binding.get("sha256")
        ):
            raise RuntimeError("diagnostic renderer source-delta binding failed")
        source_delta = json.loads(source_delta_path.read_text(encoding="utf-8"))
        if (
            activation.get("schema")
            != "biospur-c2-real-training-range-diagnostic-activation-v1"
            or activation.get("activation_role")
            != "REAL_TRAINING_RANGE_DIAGNOSTIC_ONLY"
            or activation.get("execution_authorized") is not True
            or activation.get("training_ranges_only") is not True
            or activation.get("heldout_opened") is not False
            or activation.get("full_qualification_complete") is not False
            or activation.get("settings_semantic_sha256")
            != manifest.get("settings_semantic_sha256")
            or activation.get("prefit_registry_seal")
            != manifest.get("prefit_registry_seal")
            or source_delta.get("schema")
            != "biospur-c2-real-diagnostic-authorized-source-delta-v1"
            or source_delta.get("parent_real_diagnostic_activation") != binding
            or source_delta.get("parent_prefit_registry_seal")
            != manifest.get("prefit_registry_seal")
            or source_delta.get("settings_semantic_sha256")
            != manifest.get("settings_semantic_sha256")
            or source_delta.get("base_qualified_source_hashes")
            != activation.get("qualified_source_hashes")
            or source_delta.get("effective_qualified_source_hashes")
            != manifest.get("qualified_source_hashes")
            or source_delta.get("heldout_opened") is not False
            or source_delta.get("scientific_pass_authorized") is not False
        ):
            raise RuntimeError("diagnostic renderer activation content is inconsistent")
        renderer_source_path = Path(__file__).resolve()
        scientific_fk_source_path = renderer_source_path.with_name("scientific_fk.py")
        viewer_source_paths = (renderer_source_path, scientific_fk_source_path)
        viewer_source_relatives = tuple(
            str(path.relative_to(workspace)) for path in viewer_source_paths
        )
        parent_viewer_source_hashes = {
            relative: manifest["qualified_source_hashes"].get(relative)
            for relative in viewer_source_relatives
        }
        effective_viewer_source_hashes = {
            relative: _sha256_file(path)
            for relative, path in zip(
                viewer_source_relatives, viewer_source_paths, strict=True,
            )
        }
        if parent_viewer_source_hashes != effective_viewer_source_hashes:
            if authorized_renderer_source_delta_path is None:
                raise RuntimeError(
                    "changed frozen-state viewer requires an immutable render-only source delta"
                )
            render_delta_path = Path(authorized_renderer_source_delta_path).resolve()
            render_delta_path.relative_to(workspace / "logs")
            if not render_delta_path.is_file() or render_delta_path.stat().st_mode & 0o222:
                raise RuntimeError("render-only source delta is absent or mutable")
            render_delta = json.loads(render_delta_path.read_text(encoding="utf-8"))
            frozen_manifest_binding = {
                "path": str(Path(manifest_path).resolve().relative_to(workspace)),
                "sha256": _sha256_file(Path(manifest_path).resolve()),
            }
            focused_validator_binding = render_delta.get("focused_validator")
            if not isinstance(focused_validator_binding, Mapping):
                raise RuntimeError("render-only source delta lacks its focused validator")
            focused_validator_path = (
                workspace / str(focused_validator_binding["path"])
            ).resolve()
            focused_validator_path.relative_to(workspace / "logs")
            if (
                not focused_validator_path.is_file()
                or focused_validator_path.stat().st_mode & 0o222
                or _sha256_file(focused_validator_path)
                != focused_validator_binding.get("sha256")
            ):
                raise RuntimeError("render-only focused-validator binding failed")
            focused_validator = json.loads(
                focused_validator_path.read_text(encoding="utf-8")
            )
            amendment_path = Path(landmark_proxy_owner_amendment_path).resolve()
            amendment_binding = {
                "path": str(amendment_path.relative_to(workspace)),
                "sha256": _sha256_file(amendment_path),
            }
            if (
                render_delta.get("schema")
                != "biospur-c2-frozen-landmark-proxy-viewer-source-delta-v2"
                or render_delta.get("parent_frozen_manifest")
                != frozen_manifest_binding
                or render_delta.get("parent_frozen_arrays_npz") != manifest.get("npz")
                or render_delta.get("parent_authorized_source_delta")
                != source_delta_binding
                or render_delta.get("settings_semantic_sha256")
                != manifest.get("settings_semantic_sha256")
                or render_delta.get("parent_viewer_source_hashes")
                != parent_viewer_source_hashes
                or render_delta.get("effective_viewer_source_hashes")
                != effective_viewer_source_hashes
                or render_delta.get("viewer_geometry_product")
                != "DIRECT_ORIENTATION_AVATAR_FK"
                or render_delta.get("landmark_proxy_owner_amendment")
                != amendment_binding
                or render_delta.get("payload_reread") is not False
                or render_delta.get("scientific_state_refit_or_reweight") is not False
                or render_delta.get("heldout_opened") is not False
                or render_delta.get("scientific_pass_authorized") is not False
                or focused_validator.get("schema")
                != "biospur-c2-frozen-landmark-proxy-viewer-focused-validator-v2"
                or focused_validator.get("pass") is not True
                or focused_validator.get("effective_viewer_source_hashes")
                != effective_viewer_source_hashes
                or focused_validator.get("parent_frozen_manifest")
                != frozen_manifest_binding
                or focused_validator.get("parent_frozen_arrays_npz")
                != manifest.get("npz")
                or focused_validator.get("parent_authorized_source_delta")
                != source_delta_binding
                or focused_validator.get("landmark_proxy_owner_amendment")
                != amendment_binding
                or focused_validator.get("focused_owner_and_viewer_test_count") != 6
                or focused_validator.get("focused_owner_and_viewer_tests_passed")
                is not True
                or focused_validator.get("payload_reread") is not False
                or focused_validator.get("scientific_state_refit_or_reweight") is not False
                or focused_validator.get("heldout_opened") is not False
                or (
                    postfreeze_retrospective_manifest
                    and (
                        render_delta.get("postfreeze_retrospective_manifest")
                        != frozen_manifest_binding
                        or render_delta.get("postfreeze_retrospective_npz")
                        != manifest.get("npz")
                        or render_delta.get("postfreeze_retrospective_source_delta")
                        != manifest.get("postfreeze_retrospective_source_delta")
                        or postfreeze_render_scope is None
                        or render_delta.get("render_scope") != postfreeze_render_scope
                        or focused_validator.get("render_scope")
                        != postfreeze_render_scope
                        or focused_validator.get("postfreeze_retrospective_manifest")
                        != frozen_manifest_binding
                        or focused_validator.get("postfreeze_retrospective_npz")
                        != manifest.get("npz")
                        or focused_validator.get(
                            "postfreeze_retrospective_source_delta"
                        ) != manifest.get("postfreeze_retrospective_source_delta")
                        or focused_validator.get(
                            "missing_or_wrong_render_source_delta_rejected"
                        ) is not True
                        or focused_validator.get(
                            "postfreeze_owner_authority_positive_check_passed"
                        ) is not True
                    )
                )
            ):
                raise RuntimeError("render-only source delta content is inconsistent")
            renderer_source_delta_binding = {
                "path": str(render_delta_path.relative_to(workspace)),
                "sha256": _sha256_file(render_delta_path),
            }
        diagnostic_source_verified = True
    if postfreeze_retrospective_manifest:
        postfreeze_retrospective_authority = (
            _validated_postfreeze_retrospective_authority(
                workspace=workspace,
                manifest=manifest,
            )
        )
    postfreeze_viewer_timestamp_selection: Mapping[str, Any] | None = None
    registered_actions = tuple(str(value) for value in renderer["checkpoint_actions"])
    if direct_avatar_debug_pair_only and postfreeze_retrospective_early_only:
        raise RuntimeError("renderer debug modes are mutually exclusive")
    if direct_avatar_debug_pair_only:
        if viewer_geometry_product != "DIRECT_ORIENTATION_AVATAR_FK":
            raise RuntimeError("direct-avatar debug pair requires the direct avatar product")
        registered_actions = tuple(
            action for action in registered_actions
            if action in {"16_squat", "17_final_still"}
        )
        if registered_actions != ("16_squat", "17_final_still"):
            raise RuntimeError("direct-avatar debug pair actions differ from the registered pair")
        if postfreeze_retrospective_authority is not None:
            final_prefix_row = manifest["structure"]["progressive_prefixes"][-1]
            final_branch_ids = tuple(final_prefix_row["branch_ids"])
            final_weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
            if final_weights.shape != (len(final_branch_ids),):
                raise RuntimeError("post-freeze debug branch-weight authority is invalid")
            supported_branch_ids = set(
                manifest["structure"]["physical_trajectory_support"]["15"]
            )
            supported_branch_indices = [
                index for index, branch_id in enumerate(final_branch_ids)
                if branch_id in supported_branch_ids
            ]
            if len(supported_branch_indices) != 4:
                raise RuntimeError("post-freeze debug mode requires four hard-supported branches")
            selection_branch_index = sorted(
                supported_branch_indices,
                key=lambda value: (-float(final_weights[value]), final_branch_ids[value]),
            )[0]
            postfreeze_viewer_timestamp_selection = (
                _materialize_postfreeze_bilateral_flexion_sample(
                    manifest=manifest,
                    arrays=arrays,
                    settings=settings,
                    chronological_index=15,
                    selection_branch_id=final_branch_ids[selection_branch_index],
                )
            )
    if postfreeze_retrospective_early_only:
        if viewer_geometry_product != "DIRECT_ORIENTATION_AVATAR_FK":
            raise RuntimeError(
                "post-freeze retrospective checkpoints require the direct avatar product"
            )
        registered_actions = (
            "00_initial_still",
            "04_shoulder_left",
            "08_hip_left",
            "09_hip_right",
        )
    requested_action_indices = {
        action: next((index for index, observed in action_by_index.items() if observed == action), None)
        for action in registered_actions
    }
    artifacts: list[dict[str, Any]] = []
    partial_sensor_axis_artifacts: list[dict[str, Any]] = []
    unavailable: list[dict[str, Any]] = []
    unavailable_artifacts: list[dict[str, Any]] = []
    view_specs = (
        ("FRONT", tuple(renderer["front_axes"])),
        ("SIDE", tuple(renderer["side_axes"])),
        ("TOP", tuple(renderer["top_axes"])),
    )
    coordinate = {"x": 0, "y": 1, "z": 2}
    allowed_geometry_products = {
        "SENSOR_ORIGIN_TWO_SIDED_CONNECTION_DIAGNOSTIC",
        "DIRECT_ORIENTATION_AVATAR_FK",
    }
    if viewer_geometry_product not in allowed_geometry_products:
        raise ValueError("renderer geometry product is not registered")
    landmark_proxy_authority = None
    if viewer_geometry_product == "DIRECT_ORIENTATION_AVATAR_FK":
        if landmark_proxy_owner_amendment_path is None:
            raise RuntimeError("fixed landmark-proxy viewer requires its append-only owner amendment")
        landmark_proxy_authority = _validated_landmark_proxy_viewer_authority(
            workspace=workspace,
            settings=settings,
            owner_amendment_path=landmark_proxy_owner_amendment_path,
        )
    landmark_profiles = landmark_proxy_sensitivity_profiles(
        settings["anthropometric_proxy"]
    ) if viewer_geometry_product == "DIRECT_ORIENTATION_AVATAR_FK" else ()
    landmark_profile_groups = (
        (landmark_profiles,) if landmark_profiles else ((None,),)
    )
    for action in registered_actions:
        index = requested_action_indices[action]
        if index is None or str(index) not in physical_support or not physical_support[str(index)]:
            reason = "NO_CAUSAL_OWNER_PRODUCED_POST_QMT_PHYSICAL_TRAJECTORY_AT_THIS_PREFIX"
            unavailable_row = {
                "action": action,
                "chronological_index": index,
                "reason": reason,
            }
            unavailable.append(unavailable_row)
            orientation_key_prefix = (
                f"orientation/{index:02d}/" if index is not None else None
            )
            orientation_available = bool(
                orientation_key_prefix is not None
                and any(name.startswith(orientation_key_prefix) for name in arrays)
            )
            if diagnostic_source_verified and index is not None and orientation_available:
                partial_sensor_axis_artifacts.append(
                    _render_sensor_axis_checkpoint(
                        plt=plt,
                        arrays=arrays,
                        renderer=renderer,
                        segment_frames=settings["segment_frames"],
                        workspace=workspace,
                        output_directory=output_directory,
                        chronological_index=index,
                        action=action,
                    )
                )
                continue
            unavailable_artifacts.append(_render_unavailable_checkpoint(
                plt=plt,
                renderer=renderer,
                workspace=workspace,
                output_directory=output_directory,
                chronological_index=index,
                action=action,
                checkpoint_role=None,
                reason=reason,
                real_fit_not_authorized=False,
            ))
            continue
        branch_rows = physical_support[str(index)]
        prefix_row = next(
            row for row in manifest["structure"]["progressive_prefixes"]
            if int(row["chronological_index"]) == index
        )
        branch_ids = tuple(prefix_row["branch_ids"])
        if postfreeze_retrospective_authority is not None:
            final_prefix_row = manifest["structure"]["progressive_prefixes"][-1]
            weight_branch_ids = tuple(final_prefix_row["branch_ids"])
            weights = np.asarray(arrays["frozen/branch_weights"], dtype=float)
            if weights.shape != (len(weight_branch_ids),):
                raise RuntimeError("frozen retrospective branch weights have wrong shape")
        else:
            weight_branch_ids = branch_ids
            weights = np.asarray(
                arrays[f"progressive_prefix/{index:02d}/branch_weights"], dtype=float,
            )
        candidates = [weight_branch_ids.index(branch_id) for branch_id in branch_rows]
        order = sorted(
            candidates,
            key=lambda value: (-float(weights[value]), weight_branch_ids[value]),
        )
        display_count = int(renderer["renderer_branch_display_count"])
        branch_limit = 1 if (
            direct_avatar_debug_pair_only or postfreeze_retrospective_early_only
        ) else display_count
        for branch_index in order[:branch_limit]:
            branch_id = weight_branch_ids[branch_index]
            branch_display = branch_id.removeprefix("HINGE_SIGN_").replace(
                "elbow_left:", "LE:"
            ).replace("elbow_right:", "RE:").replace(
                "knee_left:", "LK:"
            ).replace("knee_right:", "RK:").replace("_", " ")
            prefix = _prefix_key(index, branch_id)
            physical_source = str(branch_rows[branch_id]["source"])
            source_display_label, official_qmt_source_verified = _validated_source_display(
                physical_source=physical_source,
                manifest=manifest,
                renderer=renderer,
                diagnostic_source_verified=diagnostic_source_verified,
                postfreeze_retrospective_source_verified=(
                    postfreeze_retrospective_authority is not None
                ),
            )
            common_time = np.asarray(arrays[f"{prefix}/common_physical_time_s"], dtype=float)
            if not len(common_time) or np.any(np.diff(common_time) <= 0.0):
                raise RuntimeError("renderer common physical timestamps are empty or nonmonotone")
            sample_indices = np.unique(np.rint(
                np.asarray(renderer["sample_quantiles"], dtype=float) * (len(common_time) - 1)
            ).astype(int))
            if direct_avatar_debug_pair_only or postfreeze_retrospective_early_only:
                sample_indices = np.asarray([sample_indices[len(sample_indices) // 2]])
            for sample_index in sample_indices:
                landmark_profile_group = landmark_profile_groups[0]
                landmark_profile = landmark_profile_group[0]
                avatar_profile_results: list[
                    tuple[
                        Mapping[str, Any], dict[str, np.ndarray], dict[str, np.ndarray],
                        dict[str, np.ndarray], Mapping[str, Any],
                    ]
                ] = []
                if landmark_profile is None:
                    positions, joints, closure = _direct_two_sided_fk_points(
                        arrays, prefix=prefix, sample_index=int(sample_index),
                    )
                    avatar_landmarks: dict[str, np.ndarray] = {}
                    avatar_lines: dict[str, np.ndarray] = {}
                    avatar_reference_lines: dict[str, np.ndarray] = {}
                    geometry_report: Mapping[str, Any] = {
                        "profile_id": None,
                        "result_status": "SENSOR_ORIGIN_CONNECTION_DIAGNOSTIC_NOT_ABSOLUTE_SCALE",
                    }
                else:
                    for profile in landmark_profile_group:
                        (
                            profile_landmarks, profile_lines, profile_reference_lines,
                            profile_report,
                        ) = _direct_orientation_avatar_points(
                            arrays,
                            prefix=prefix,
                            sample_index=int(sample_index),
                            profile=profile,
                        )
                        avatar_profile_results.append((
                            profile, profile_landmarks, profile_lines,
                            profile_reference_lines, profile_report,
                        ))
                    (
                        landmark_profile, avatar_landmarks, avatar_lines,
                        avatar_reference_lines, geometry_report,
                    ) = avatar_profile_results[0]
                    positions = {}
                    joints = {}
                    closure = None
                uncertainty_trace = float(sum(
                    np.trace(np.asarray(
                        arrays[f"{prefix}/orientation_covariance/{segment}"][sample_index],
                        dtype=float,
                    ))
                    for segment in {name for _, parent, child in EDGE_SPECS for name in (parent, child)}
                ))
                figure, axes = plt.subplots(
                    1, 3,
                    figsize=tuple(float(value) for value in renderer["figure_size_inches"]),
                    dpi=int(renderer["dpi"]),
                )
                avatar_annotation_artists: list[Any] = []
                avatar_debug_labels = {
                    "pelvis_sensor_surface_ref": "P_SENSOR",
                    "torso_sensor_surface_ref": "T_SENSOR",
                    "shoulder_mid_functional": "S_MID_FUNC",
                    "shoulder_left_joint": "S_L_FUNC",
                    "shoulder_right_joint": "S_R_FUNC",
                    "hip_mid_functional": "H_MID_FUNC",
                    "hip_left_joint": "H_L_FUNC",
                    "hip_right_joint": "H_R_FUNC",
                    "elbow_left": "EL_L",
                    "elbow_right": "EL_R",
                    "wrist_left": "WR_L",
                    "wrist_right": "WR_R",
                    "knee_left": "KN_L",
                    "knee_right": "KN_R",
                    "ankle_left": "AN_L",
                    "ankle_right": "AN_R",
                }
                avatar_debug_label_positions_by_view = {
                    "FRONT": {
                        "pelvis_sensor_surface_ref": (-0.58, -0.10),
                        "torso_sensor_surface_ref": (-0.58, 0.18),
                        "shoulder_mid_functional": (0.42, 0.56),
                        "hip_mid_functional": (0.42, -0.16),
                        "shoulder_left_joint": (-1.03, 0.96),
                        "elbow_left": (-1.03, 0.67),
                        "wrist_left": (-1.03, 0.39),
                        "hip_left_joint": (-1.03, 0.10),
                        "knee_left": (-1.03, -0.37),
                        "ankle_left": (-1.03, -0.92),
                        "shoulder_right_joint": (0.78, 0.96),
                        "elbow_right": (0.78, 0.67),
                        "wrist_right": (0.78, 0.39),
                        "hip_right_joint": (0.78, 0.10),
                        "knee_right": (0.78, -0.37),
                        "ankle_right": (0.78, -0.92),
                    },
                    "SIDE": {
                        "pelvis_sensor_surface_ref": (-0.55, -0.10),
                        "torso_sensor_surface_ref": (-0.55, 0.18),
                        "shoulder_mid_functional": (0.40, 0.56),
                        "hip_mid_functional": (0.40, -0.16),
                        "shoulder_left_joint": (-1.03, 0.96),
                        "elbow_left": (-1.03, 0.67),
                        "wrist_left": (-1.03, 0.39),
                        "hip_left_joint": (-1.03, 0.10),
                        "knee_left": (-1.03, -0.37),
                        "ankle_left": (-1.03, -0.92),
                        "shoulder_right_joint": (0.78, 0.96),
                        "elbow_right": (0.78, 0.67),
                        "wrist_right": (0.78, 0.39),
                        "hip_right_joint": (0.78, 0.10),
                        "knee_right": (0.78, -0.37),
                        "ankle_right": (0.78, -0.92),
                    },
                    "TOP": {
                        "pelvis_sensor_surface_ref": (-0.32, 1.02),
                        "torso_sensor_surface_ref": (0.22, 1.02),
                        "shoulder_mid_functional": (-0.32, 0.76),
                        "hip_mid_functional": (0.22, 0.76),
                        "shoulder_left_joint": (-1.03, 0.96),
                        "elbow_left": (-1.03, 0.62),
                        "wrist_left": (-1.03, 0.28),
                        "hip_left_joint": (-1.03, -0.06),
                        "knee_left": (-1.03, -0.40),
                        "ankle_left": (-1.03, -0.74),
                        "shoulder_right_joint": (0.78, 0.96),
                        "elbow_right": (0.78, 0.62),
                        "wrist_right": (0.78, 0.28),
                        "hip_right_joint": (0.78, -0.06),
                        "knee_right": (0.78, -0.40),
                        "ankle_right": (0.78, -0.74),
                    },
                }
                for axis, (view_name, (horizontal_name, vertical_name)) in zip(
                    axes, view_specs, strict=True,
                ):
                    horizontal = coordinate[horizontal_name]
                    vertical = coordinate[vertical_name]
                    if avatar_profile_results:
                        profile_colors = ("#111827", "#2563eb", "#dc2626", "#059669", "#9333ea")
                        profile_display_labels = (
                            "Observer A: forearms 0.245 m",
                            "Observer B low: forearms 0.260 m",
                            "Observer B high: forearms 0.265 m",
                            "Cross sensitivity: L-low / R-high",
                            "Cross sensitivity: L-high / R-low",
                        )
                        for profile_index, (
                            profile_row, profile_landmarks, profile_lines, _, _,
                        ) in enumerate(avatar_profile_results):
                            for line_index, (line_name, line) in enumerate(
                                sorted(profile_lines.items())
                            ):
                                axis.plot(
                                    line[:, horizontal], line[:, vertical],
                                    color=profile_colors[profile_index],
                                    linewidth=float(renderer["line_width"]) * (1.25 if profile_index == 0 else 0.8),
                                    alpha=1.0 if profile_index == 0 else 0.42,
                                    linestyle="-" if profile_index == 0 else "--",
                                    zorder=1,
                                    label=(
                                        profile_display_labels[profile_index]
                                        if view_name == "FRONT" and line_index == 0
                                        else None
                                    ),
                                )
                        for line_index, (line_name, line) in enumerate(
                            sorted(avatar_reference_lines.items())
                        ):
                            axis.plot(
                                line[:, horizontal], line[:, vertical],
                                color="#6b7280", linewidth=1.0, linestyle=":",
                                alpha=0.9, zorder=0,
                                label=(
                                    "surface sensor-reference observation"
                                    if view_name == "FRONT" and line_index == 0 else None
                                ),
                            )
                        joint_landmark_names = tuple(
                            name for name in sorted(avatar_landmarks)
                            if not name.endswith("_sensor_surface_ref")
                        )
                        main_points = np.vstack([
                            avatar_landmarks[name] for name in joint_landmark_names
                        ])
                        axis.scatter(
                            main_points[:, horizontal], main_points[:, vertical],
                            color="#2563eb", marker="o",
                            s=float(renderer["joint_marker_size"]) * 0.65,
                            zorder=4, label="functional joint / distal landmark proxy",
                        )
                        surface_sensor_positions = {
                            name: np.asarray(value, dtype=float)
                            for name, value in geometry_report[
                                "surface_sensor_positions_m"
                            ].items()
                        }
                        surface_sensor_points = np.vstack([
                            surface_sensor_positions[name]
                            for name in sorted(surface_sensor_positions)
                        ])
                        axis.scatter(
                            surface_sensor_points[:, horizontal],
                            surface_sensor_points[:, vertical],
                            color="#dc2626", marker="s",
                            s=float(renderer["marker_size"]) * 0.8,
                            zorder=5, label="surface sensor origin",
                        )
                        for segment, point in surface_sensor_positions.items():
                            covariance = np.asarray(
                                geometry_report[
                                    "surface_sensor_position_covariance_m2"
                                ][segment],
                                dtype=float,
                            )
                            block = covariance[np.ix_((horizontal, vertical), (horizontal, vertical))]
                            eigenvalues, eigenvectors = np.linalg.eigh(
                                0.5 * (block + block.T)
                            )
                            eigenvalues = np.maximum(eigenvalues, 0.0)
                            order_eigen = np.argsort(eigenvalues)[::-1]
                            eigenvalues = eigenvalues[order_eigen]
                            eigenvectors = eigenvectors[:, order_eigen]
                            ellipse_angle = float(np.degrees(np.arctan2(
                                eigenvectors[1, 0], eigenvectors[0, 0],
                            )))
                            axis.add_patch(Ellipse(
                                (point[horizontal], point[vertical]),
                                width=2.0 * float(np.sqrt(eigenvalues[0])),
                                height=2.0 * float(np.sqrt(eigenvalues[1])),
                                angle=ellipse_angle,
                                facecolor="#dc2626", edgecolor="#dc2626",
                                alpha=0.08, linewidth=0.6, zorder=0,
                            ))
                        if direct_avatar_debug_pair_only:
                            for node_name, point in sorted(avatar_landmarks.items()):
                                annotation = axis.annotate(
                                    avatar_debug_labels[node_name],
                                    (point[horizontal], point[vertical]),
                                    xytext=avatar_debug_label_positions_by_view[
                                        view_name
                                    ][node_name],
                                    textcoords="data",
                                    fontsize=5.1, color="#111827",
                                    bbox={
                                        "boxstyle": "round,pad=0.08",
                                        "facecolor": "white",
                                        "edgecolor": "none",
                                        "alpha": 0.72,
                                    },
                                    arrowprops={
                                        "arrowstyle": "-",
                                        "color": "#6b7280",
                                        "linewidth": 0.35,
                                        "shrinkA": 0.0,
                                        "shrinkB": 1.5,
                                    },
                                    zorder=5,
                                )
                                avatar_annotation_artists.append(annotation)
                    else:
                        for edge, parent, child in EDGE_SPECS:
                            parent_to_joint = np.vstack((positions[parent], joints[edge]))
                            joint_to_child = np.vstack((joints[edge], positions[child]))
                            axis.plot(
                                parent_to_joint[:, horizontal], parent_to_joint[:, vertical],
                                color=renderer["parent_sensor_to_joint_color"],
                                linewidth=float(renderer["line_width"]), zorder=2,
                            )
                            axis.plot(
                                joint_to_child[:, horizontal], joint_to_child[:, vertical],
                                color=renderer["child_sensor_to_joint_color"],
                                linewidth=float(renderer["line_width"]), linestyle="--", zorder=2,
                            )
                        points = np.vstack([positions[name] for name in sorted(positions)])
                        axis.scatter(
                            points[:, horizontal], points[:, vertical],
                            color=renderer["segment_sensor_origin_color"],
                            marker="s", s=float(renderer["marker_size"]), zorder=3,
                            label="segment/sensor origin",
                        )
                        joint_points = np.vstack([joints[name] for name in sorted(joints)])
                        axis.scatter(
                            joint_points[:, horizontal], joint_points[:, vertical],
                            color=renderer["functional_joint_center_color"],
                            marker="x", s=float(renderer["joint_marker_size"]), zorder=4,
                            label="posterior functional joint-center proxy",
                        )
                    axis.set_title(view_name)
                    coordinate_frame_label = (
                        "replay-world/gauge" if avatar_profile_results else "pelvis-frame"
                    )
                    axis.set_xlabel(
                        f"{coordinate_frame_label} {horizontal_name} (m)"
                    )
                    axis.set_ylabel(
                        f"{coordinate_frame_label} {vertical_name} (m)"
                    )
                    axis.set_xlim(*map(float, renderer["horizontal_limits_m"]))
                    axis.set_ylim(*map(float, renderer["vertical_limits_m"]))
                    if renderer["equal_aspect"]:
                        axis.set_aspect("equal", adjustable="box")
                    axis.grid(alpha=0.2)
                avatar_legend_artist = None
                if avatar_profile_results:
                    legend_handles, legend_labels = axes[0].get_legend_handles_labels()
                    avatar_legend_artist = figure.legend(
                        legend_handles,
                        legend_labels,
                        loc="lower center",
                        bbox_to_anchor=(0.5, 0.092),
                        ncol=4,
                        fontsize=6.2,
                        framealpha=0.92,
                        columnspacing=1.1,
                        handlelength=2.2,
                    )
                else:
                    axes[0].legend(loc="lower left", fontsize=7, framealpha=0.9)
                title_artist = figure.suptitle(
                    f"{('FULL-R3 FUNCTIONAL-CONNECTION + LANDMARK-PROXY VIEWER / NON-ANATOMICAL / NOT PASS' if landmark_profile is not None else renderer['status_label'])}\n"
                    f"{source_display_label}\n"
                    f"{action} • t={common_time[sample_index]:.6f} s • "
                    f"branch signs={branch_display}\n"
                    f"{('final-frozen ' if postfreeze_retrospective_authority is not None else '')}"
                    f"weight={weights[branch_index]:.6g}"
                    + (
                        "" if landmark_profile is None
                        else f" • 5 raw scale profiles overlaid (no weights)"
                    ),
                    color="#b91c1c", fontsize=12, fontweight="bold",
                )
                footer_lead = (
                    "FUNCTIONAL-CONNECTION VIEWER: frozen QMT rotations + all nine "
                    "full-R3 sensor-to-joint means; sensors and joints shown separately.\n"
                    if landmark_profile is not None else
                    "FUNCTIONAL-GEOMETRY PROXY: square=segment/sensor origin; "
                    "x=functional joint-center; solid/dashed=two sensor-to-joint vectors.\n"
                )
                footer_tail = (
                    "raw values remain separate; mapping uncertainty unqualified; "
                    "0.280 m surface sensor distance is provenance-only; connection "
                    "covariance overlay excludes orientation/nonlinear rescale; "
                    "pelvis sensor translation gauge only (no rotational rebase); "
                    "no IK/rebase/retarget/repair."
                    if landmark_profile is not None else
                    "no IK/rebase/repair; not an absolute human-scale claim."
                )
                footer_artist = figure.text(
                    0.5, 0.012,
                    footer_lead
                    + f"Source: {source_display_label}; "
                    + f"orientation covariance trace={uncertainty_trace:.6g} rad²; "
                    + footer_tail,
                    ha="center", va="bottom", fontsize=7.5, linespacing=1.25,
                )
                figure.subplots_adjust(top=0.70, bottom=0.25, wspace=0.28)
                profile_suffix = (
                    "" if landmark_profile is None
                    else "_FIVE_RAW_PROFILE_OVERLAY"
                )
                filename = (
                    f"SCIENTIFIC_TRIVIEW_{index:02d}_{action}_{branch_id}_"
                    f"SAMPLE_{int(sample_index):02d}{profile_suffix}.png"
                )
                path = output_directory / filename
                figure.canvas.draw()
                canvas_width, canvas_height = figure.canvas.get_width_height()
                title_bbox = title_artist.get_window_extent(
                    renderer=figure.canvas.get_renderer(),
                )
                footer_bbox = footer_artist.get_window_extent(
                    renderer=figure.canvas.get_renderer(),
                )
                legend_bbox = (
                    avatar_legend_artist.get_window_extent(
                        renderer=figure.canvas.get_renderer(),
                    ) if avatar_legend_artist is not None else None
                )
                footer_within_canvas = bool(
                    footer_bbox.x0 >= 2.0
                    and footer_bbox.y0 >= 2.0
                    and footer_bbox.x1 <= canvas_width - 2.0
                    and footer_bbox.y1 <= canvas_height - 2.0
                )
                title_within_canvas = bool(
                    title_bbox.x0 >= 2.0
                    and title_bbox.y0 >= 2.0
                    and title_bbox.x1 <= canvas_width - 2.0
                    and title_bbox.y1 <= canvas_height - 2.0
                )
                legend_within_canvas = bool(
                    legend_bbox is None
                    or (
                        legend_bbox.x0 >= 2.0
                        and legend_bbox.y0 >= 2.0
                        and legend_bbox.x1 <= canvas_width - 2.0
                        and legend_bbox.y1 <= canvas_height - 2.0
                    )
                )
                annotation_bboxes = [
                    artist.get_window_extent(renderer=figure.canvas.get_renderer())
                    for artist in avatar_annotation_artists
                ]
                annotations_within_canvas = all(
                    bbox.x0 >= 2.0
                    and bbox.y0 >= 2.0
                    and bbox.x1 <= canvas_width - 2.0
                    and bbox.y1 <= canvas_height - 2.0
                    for bbox in annotation_bboxes
                )
                if (
                    not footer_within_canvas
                    or not title_within_canvas
                    or not legend_within_canvas
                    or not annotations_within_canvas
                ):
                    plt.close(figure)
                    raise RuntimeError(
                        "direct avatar title/footer/legend/annotation exceeds the pixel canvas"
                    )
                figure.savefig(path, facecolor="white")
                pixel_shape = list(np.asarray(figure.canvas.buffer_rgba()).shape)
                plt.close(figure)
                connection_evidence_available = all(
                    f"{prefix}/connection/{edge}/covariance" in arrays
                    and f"{prefix}/connection/{edge}/parent" in arrays
                    and f"{prefix}/connection/{edge}/child" in arrays
                    for edge, _, _ in EDGE_SPECS
                )
                artifacts.append({
                    "path": str(path.relative_to(workspace)),
                    "sha256": _sha256_file(path),
                    "chronological_index": int(index),
                    "action": action,
                    "branch_id": branch_id,
                    "branch_weight": float(weights[branch_index]),
                    "sample_index": int(sample_index),
                    "common_physical_time_s": float(common_time[sample_index]),
                    "physical_source": physical_source,
                    "source_display_label": source_display_label,
                    "official_qmt_source_verified_from_fresh_bound_manifest": (
                        official_qmt_source_verified
                    ),
                    "physically_legal": (
                        None if postfreeze_retrospective_authority is not None
                        else bool(branch_rows[branch_id]["physically_legal"])
                    ),
                    "viewer_only_physical_status": branch_rows[branch_id].get(
                        "viewer_only_physical_status"
                    ),
                    "scientific_physical_gate_executed": branch_rows[branch_id].get(
                        "scientific_physical_gate_executed"
                    ),
                    "postfreeze_retrospective": branch_rows[branch_id].get(
                        "postfreeze_retrospective", False,
                    ),
                    "time_varying_parent_plus_child_deltafilt": branch_rows[
                        branch_id
                    ].get("time_varying_parent_plus_child_deltafilt", False),
                    "orientation_covariance_trace_rad2": uncertainty_trace,
                    "maximum_shared_joint_closure_error_m": closure,
                    "viewer_geometry_product": viewer_geometry_product,
                    "display_coordinate_frame": (
                        "REPLAY_WORLD_AXES_WITH_PELVIS_TRANSLATION_GAUGE_ONLY"
                        if landmark_profile is not None else "PELVIS_FRAME"
                    ),
                    "display_rotational_rebase_applied": False,
                    "landmark_proxy_profile_id": geometry_report.get("profile_id"),
                    "landmark_proxy_profile_ids_overlaid": [
                        str(row[0]["profile_id"]) for row in avatar_profile_results
                    ],
                    "landmark_proxy_profiles_have_weights": False,
                    "landmark_proxy_profiles_are_exhaustive_distribution": False,
                    "cross_side_profiles_are_observed_assignments": False,
                    "landmark_proxy_report": dict(geometry_report),
                    "functional_connection_covariance_trace_m2_by_edge": {
                        edge: float(np.trace(np.asarray(
                            arrays[f"{prefix}/connection/{edge}/covariance"], dtype=float,
                        ))) for edge, _, _ in EDGE_SPECS
                    } if connection_evidence_available else {},
                    "functional_connection_mean_array_binding_by_edge_endpoint": {
                        f"{edge}:{endpoint}": _array_binding(np.asarray(
                            arrays[f"{prefix}/connection/{edge}/{endpoint}"], dtype=float,
                        ))
                        for edge, _, _ in EDGE_SPECS
                        for endpoint in ("parent", "child")
                    } if connection_evidence_available else {},
                    "functional_connection_covariance_array_binding_by_edge": {
                        edge: _array_binding(np.asarray(
                            arrays[f"{prefix}/connection/{edge}/covariance"], dtype=float,
                        )) for edge, _, _ in EDGE_SPECS
                    } if connection_evidence_available else {},
                    "functional_connection_evidence_available_as_optional_validation": False,
                    "functional_connection_evidence_required_by_avatar": True,
                    "functional_connection_evidence_available_and_consumed": connection_evidence_available,
                    "functional_connection_covariance_propagated_through_viewer_rescaling": False,
                    "functional_connection_covariance_overlay_excludes_orientation_and_nonlinear_profile_rescale": True,
                    "functional_connection_arrays_remain_unmodified_in_frozen_scientific_state": True,
                    "raw_surface_observation_called_internal_bone_truth": False,
                    "pixel_shape_rgba": pixel_shape,
                    "footer_bbox_pixels": [
                        float(footer_bbox.x0), float(footer_bbox.y0),
                        float(footer_bbox.x1), float(footer_bbox.y1),
                    ],
                    "footer_within_canvas": footer_within_canvas,
                    "title_bbox_pixels": [
                        float(title_bbox.x0), float(title_bbox.y0),
                        float(title_bbox.x1), float(title_bbox.y1),
                    ],
                    "title_within_canvas": title_within_canvas,
                    "legend_within_canvas": legend_within_canvas,
                    "node_annotations_within_canvas": annotations_within_canvas,
                    "status": renderer["status_label"],
                })
    if direct_avatar_debug_pair_only and len(artifacts) != 2:
        raise RuntimeError("direct-avatar debug mode must render exactly squat and final")
    if postfreeze_retrospective_early_only and len(artifacts) != 4:
        raise RuntimeError(
            "post-freeze retrospective mode must render exactly four early checkpoints"
        )
    return {
        "schema": "biospur-c2-registered-scientific-triview-render-result-v1",
        "frozen_manifest": {
            "path": str(Path(manifest_path).resolve().relative_to(workspace)),
            "sha256": _sha256_file(Path(manifest_path).resolve()),
        },
        "renderer_source_delta": renderer_source_delta_binding,
        "renderer_settings_semantic_sha256": hashlib.sha256(
            json.dumps(renderer, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
        ).hexdigest(),
        "artifacts": artifacts,
        "partial_sensor_axis_artifacts": partial_sensor_axis_artifacts,
        "unavailable_registered_checkpoints": unavailable,
        "unavailable_diagnostic_artifacts": unavailable_artifacts,
        "identical_camera_limits_and_renderer_for_every_artifact": True,
        "caller_selected_timestamp_branch_or_geometry": False,
        "viewer_geometry_product": viewer_geometry_product,
        "viewer_landmark_proxy_scale_context_used": (
            viewer_geometry_product == "DIRECT_ORIENTATION_AVATAR_FK"
        ),
        "landmark_proxy_viewer_authority": landmark_proxy_authority,
        "viewer_landmark_proxy_entered_fit_qmt_frames_branch_likelihood_or_physical_acceptance": False,
        "direct_avatar_debug_pair_only": direct_avatar_debug_pair_only,
        "postfreeze_retrospective_early_only": postfreeze_retrospective_early_only,
        "postfreeze_retrospective_authority": postfreeze_retrospective_authority,
        "postfreeze_viewer_timestamp_selection": postfreeze_viewer_timestamp_selection,
        "postfreeze_retrospective_label": (
            POSTFREEZE_RETROSPECTIVE_DISPLAY_LABEL
            if postfreeze_retrospective_authority is not None else None
        ),
        "viewer_rebase_ik_retarget_or_repair": False,
        "scientific_acceptance_pass": False,
    }
