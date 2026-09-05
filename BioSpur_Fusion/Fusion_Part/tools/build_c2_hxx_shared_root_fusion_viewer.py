#!/usr/bin/env python3
"""Place the frozen H01/H02 avatar on the causal shared-root fusion trajectory."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from build_c2_h01_imu_dead_reckoning import (
    DEFAULT_SOURCE,
    WORLD_LAYER_SCRIPT,
    _extract_data,
)
from evaluate_c2_hxx_shared_root_regression import DEFAULT_CLOCK, _load_holdout
from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_uwb_calibration.frozen_body_proxy import (
    NODE_TO_PROXY_POINT,
    FrozenHoldoutBodyProxy,
    frozen_world_alignment,
    output_from_uwb_world,
)
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS as ARTICULATED_SEGMENTS,
    corrected_proxy_points,
)
from biospur_fusion.c2_uwb_calibration.antenna_los import rotation_from_wxyz
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)


SUPPORTED_ACTIONS = ("H01_boxing", "H02_golf")


SMOOTH_PLAYBACK_SCRIPT = r"""
function viewerPlaybackTime(ep){
  const base=Number(ep.time[frameIndex]??0);
  if(!playing||frameIndex>=ep.time.length-1)return base;
  return Math.min(Number(ep.time[ep.time.length-1]),base+accumulator);
}
function viewerBracket(times,t){
  if(!times||times.length<2)return[0,0,0];
  if(t<=times[0])return[0,0,0];
  const last=times.length-1;
  if(t>=times[last])return[last,last,0];
  let lo=0,hi=last;
  while(hi-lo>1){const mid=(lo+hi)>>1;if(times[mid]<=t)lo=mid;else hi=mid}
  const span=Math.max(1e-9,Number(times[hi])-Number(times[lo]));
  return[lo,hi,Math.max(0,Math.min(1,(t-Number(times[lo]))/span))];
}
function viewerLerpVector(a,b,u){return a.map((value,i)=>Number(value)+(Number(b[i])-Number(value))*u)}
function viewerRootAt(ep,t){
  const times=ep.rootTranslationFusionHighRateTime,rows=ep.rootTranslationFusionHighRate;
  if(times&&rows){const [i,j,u]=viewerBracket(times,t);return viewerLerpVector(rows[i],rows[j],u)}
  const [i,j,u]=viewerBracket(ep.time,t),fallback=ep.rootTranslationFusion;
  return fallback?viewerLerpVector(fallback[i],fallback[j],u):[0,0,0];
}
function viewerInterpolatedFrame(ep){
  const t=viewerPlaybackTime(ep),[i,j,u]=viewerBracket(ep.time,t);
  const flat=viewerLerpVector(ep.frames[i],ep.frames[j],u);
  if(!ep.rootTranslationFusion)return flat;
  const coarse=viewerLerpVector(ep.rootTranslationFusion[i],ep.rootTranslationFusion[j],u);
  const native=viewerRootAt(ep,t),delta=native.map((value,axis)=>value-coarse[axis]);
  for(let offset=0;offset<flat.length;offset+=3){flat[offset]+=delta[0];flat[offset+1]+=delta[1];flat[offset+2]+=delta[2]}
  return flat;
}
function viewerInterpolatedTagFrame(ep){
  const rows=ep.uwbTagProxyPositions;if(!rows)return null;
  const t=viewerPlaybackTime(ep),[i,j,u]=viewerBracket(ep.time,t);
  const coarseRoot=ep.rootTranslationFusion?viewerLerpVector(ep.rootTranslationFusion[i],ep.rootTranslationFusion[j],u):[0,0,0];
  const nativeRoot=viewerRootAt(ep,t),delta=nativeRoot.map((value,axis)=>value-coarseRoot[axis]);
  return rows[i].map((point,k)=>viewerLerpVector(point,rows[j][k],u).map((value,axis)=>value+delta[axis]));
}
function viewerNearestHighRateIndex(ep,t){
  const times=ep.rootTranslationFusionHighRateTime;
  if(!times||!times.length)return Math.max(0,Math.min(ep.frames.length-1,frameIndex));
  const [i,j,u]=viewerBracket(times,t);return u<0.5?i:j;
}
"""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _maximum_masked_episode_drift(
    positions: np.ndarray, active: np.ndarray
) -> float:
    maximum = 0.0
    start = None
    for index, is_active in enumerate(active):
        if is_active and start is None:
            start = index
        if start is not None and (
            not is_active or index == len(active) - 1
        ):
            stop = index + 1 if is_active else index
            block = positions[start:stop]
            if len(block):
                maximum = max(maximum, float(np.max(
                    np.linalg.norm(block - block[0], axis=1)
                )))
            start = None
    return maximum


def run(
    output: Path,
    *,
    action: str,
    fusion_npz: Path,
    fusion_result: Path,
    source: Path = DEFAULT_SOURCE,
    pose_source: Path | None = None,
    source_trajectory: Path | None = None,
) -> dict:
    if action not in SUPPORTED_ACTIONS:
        raise ValueError(f"unsupported action: {action}")
    output.mkdir(parents=True, exist_ok=False)
    source_text = source.read_text(encoding="utf-8")
    data, start, stop = _extract_data(source_text)
    if pose_source is not None:
        pose_data, _pose_start, _pose_stop = _extract_data(
            pose_source.read_text(encoding="utf-8")
        )
        for key in ("jointNames", "lines", "episodes", "targetFps", "viewGauge"):
            data[key] = pose_data[key]
    episode = next(row for row in data["episodes"] if row["id"] == action)
    local_frames = np.asarray(episode["frames"], dtype=float).reshape(
        len(episode["frames"]), len(data["jointNames"]), 3
    )
    joint_index = {name: index for index, name in enumerate(data["jointNames"])}
    result = json.loads(fusion_result.read_text())
    if result.get("action") != action or result.get("scientific_pass") is not False:
        raise ValueError("fusion result action/status mismatch")
    clocks = _clock_models(DEFAULT_CLOCK)
    holdout = _load_holdout(action, clocks, _beacon_boundary_bridges(DEFAULT_CLOCK))
    bootstrap_time = float(result["timing"]["bootstrap_time_s"])
    action_start_s = holdout["lo"] * 1e-9
    bootstrap_offset_s = bootstrap_time - action_start_s

    with np.load(fusion_npz, allow_pickle=False) as archive:
        time_s = np.asarray(archive["time_s"], dtype=float)
        fused_world = np.asarray(
            archive["fused_root_position_world_m"], dtype=float
        )
        imu_world = np.asarray(
            archive["imu_only_root_position_world_m"], dtype=float
        )
        anchors_world = np.asarray(archive["anchors_world_m"], dtype=float)
        correction_segment_names = tuple(
            str(value) for value in archive["articulated_segment_order"]
        )
        if "articulated_segment_correction_high_rate_rotvec" in archive:
            correction_time_s = time_s.copy()
            segment_correction = np.asarray(
                archive[
                    "articulated_segment_correction_high_rate_rotvec"
                ], dtype=float
            )
            correction_source = "FINAL_CAUSAL_NATIVE200_POSE_OWNER"
        else:
            correction_time_s = np.asarray(
                archive["shared_root_time_s"], dtype=float
            )
            segment_correction = np.asarray(
                archive["articulated_segment_correction_rotvec"], dtype=float
            )
            correction_source = "LEGACY_UWB_EPOCH_CORRECTION"
        if "adaptive_node_order" in archive:
            adaptive_node_order = tuple(
                str(value) for value in archive["adaptive_node_order"]
            )
            adaptive_node_trusted = np.asarray(
                archive["adaptive_node_trusted"], dtype=bool
            )
            adaptive_mode = np.asarray(archive["adaptive_mode"], dtype=str)
        else:
            adaptive_node_order = tuple(NODE_TO_PROXY_POINT)
            adaptive_node_trusted = np.ones(
                (len(correction_time_s), len(adaptive_node_order)), dtype=bool
            )
            adaptive_mode = np.full(
                len(correction_time_s), "LEGACY_ALL_AVAILABLE", dtype=str
            )
        if "contact_active" in archive:
            contact_active = np.asarray(archive["contact_active"], dtype=bool)
            contact_constrained = np.asarray(
                archive["contact_constrained"], dtype=bool
            )
            contact_confidence = np.asarray(
                archive["contact_confidence"], dtype=float
            )
        else:
            contact_active = np.zeros((len(time_s), 2), dtype=bool)
            contact_constrained = np.zeros((len(time_s), 2), dtype=bool)
            contact_confidence = np.zeros((len(time_s), 2), dtype=float)
    viewer_time = np.asarray(episode["time"], dtype=float)
    query = viewer_time - bootstrap_offset_s
    interpolate = lambda values: np.column_stack([
        np.interp(query, time_s, values[:, axis]) for axis in range(3)
    ])
    fused_world_viewer = interpolate(fused_world)
    imu_world_viewer = interpolate(imu_world)

    calibration = load_frozen_c2_3a()
    diagnostics = load_frozen_c2_hxx_diagnostics()
    body = FrozenHoldoutBodyProxy.create(diagnostics, calibration)
    uwb_from_internal, _frozen_forward = frozen_world_alignment(calibration)
    output_from_internal = np.asarray(
        diagnostics.output_matrix_world_display_from_internal, dtype=float
    )
    output_from_uwb = output_from_uwb_world(
        output_from_internal, uwb_from_internal
    )
    fused_output = fused_world_viewer @ output_from_uwb.T
    imu_output = imu_world_viewer @ output_from_uwb.T
    anchors_output = anchors_world @ output_from_uwb.T
    native_viewer_time = time_s + bootstrap_offset_s
    fused_output_high_rate = fused_world @ output_from_uwb.T

    if correction_segment_names != ARTICULATED_SEGMENTS:
        raise ValueError("articulated segment order does not match solver contract")
    if segment_correction.shape != (
        len(correction_time_s), len(ARTICULATED_SEGMENTS), 3
    ):
        raise ValueError("articulated correction array has an invalid shape")
    articulated_enabled = (
        result["architecture"].get("body_update") == "articulated_consensus"
    )
    native_base_rotations = None
    native_frame_for_viewer = None
    if source_trajectory is not None:
        with np.load(source_trajectory, allow_pickle=False) as archive:
            native_base_rotations = {
                segment: np.asarray(
                    archive[
                        f"trajectory/{action}/{segment}/quat_world_segment_wxyz"
                    ],
                    dtype=float,
                )
                for segment in ARTICULATED_SEGMENTS
            }
            native_time = np.asarray(
                archive[f"trajectory/{action}/pelvis/time_root_s"], dtype=float
            )
        native_relative = native_time - native_time[0]
        viewer_relative = viewer_time - viewer_time[0]
        if (
            len(native_time) < 2
            or not np.all(np.diff(native_time) > 0.0)
            or abs(float(native_relative[-1] - viewer_relative[-1])) > 0.02
        ):
            raise ValueError("source trajectory does not match viewer pose time grid")
        native_frame_for_viewer = np.searchsorted(
            native_relative, viewer_relative, side="left"
        )
        native_frame_for_viewer = np.clip(
            native_frame_for_viewer, 0, len(native_time) - 1
        )
        prior = np.maximum(native_frame_for_viewer - 1, 0)
        use_prior = np.abs(native_relative[prior] - viewer_relative) < np.abs(
            native_relative[native_frame_for_viewer] - viewer_relative
        )
        native_frame_for_viewer[use_prior] = prior[use_prior]
    correction_viewer = np.empty(
        (len(viewer_time), len(ARTICULATED_SEGMENTS), 3), dtype=float
    )
    for segment_index in range(len(ARTICULATED_SEGMENTS)):
        for axis in range(3):
            correction_viewer[:, segment_index, axis] = np.interp(
                query,
                correction_time_s,
                segment_correction[:, segment_index, axis],
            )
    if articulated_enabled:
        source_frames = np.asarray(episode["sourceFrame"], dtype=int)
        source_lo = int(source_frames[0])
        source_span = max(1, int(source_frames[-1]) - source_lo)
        for frame_index, source_frame in enumerate(source_frames):
            if native_base_rotations is None:
                fraction = (int(source_frame) - source_lo) / source_span
                _offsets, _normals, frozen_frame = body.at_fraction(
                    action, fraction, uwb_from_internal
                )
                rotations = {
                    segment: uwb_from_internal @ rotation_from_wxyz(
                        diagnostics.series(action, segment)
                        .quat_world_segment_wxyz[frozen_frame]
                    )
                    for segment in ARTICULATED_SEGMENTS
                }
            else:
                native_frame = int(native_frame_for_viewer[frame_index])
                rotations = {
                    segment: uwb_from_internal @ rotation_from_wxyz(
                        native_base_rotations[segment][native_frame]
                    )
                    for segment in ARTICULATED_SEGMENTS
                }
            corrections = {
                segment: correction_viewer[frame_index, segment_index]
                for segment_index, segment in enumerate(ARTICULATED_SEGMENTS)
            }
            points = corrected_proxy_points(
                rotations, corrections, calibration.geometry
            )
            for point, point_uwb in points.items():
                local_frames[frame_index, joint_index[point]] = (
                    output_from_uwb @ point_uwb
                )

    # Establish one display floor gauge from the first inferred support window.
    # This is a constant translation, not a per-frame grounding operation:
    # later vertical motion, contact release, and a possible flight phase stay
    # visible.  The causal foothold constraint has already acted inside the
    # fusion filter and must not be replaced by renderer-side foot pinning.
    ungrounded_fused_output = fused_output.copy()
    ankle_indices = [joint_index["ankle_left"], joint_index["ankle_right"]]
    lower_ankle_z = np.min(local_frames[:, ankle_indices, 2], axis=1)
    contact_indices_for_viewer = np.clip(
        np.searchsorted(time_s, query, side="right") - 1,
        0,
        len(time_s) - 1,
    )
    supported_rows = np.flatnonzero(
        np.any(contact_constrained[contact_indices_for_viewer], axis=1)
    )
    if not len(supported_rows):
        raise RuntimeError("no inferred support window available for floor gauge")
    gauge_rows = supported_rows[
        supported_rows < supported_rows[0] + max(1, int(round(0.20 / np.median(np.diff(viewer_time)))))
    ]
    support_shift_z = -float(np.median(
        fused_output[gauge_rows, 2] + lower_ankle_z[gauge_rows]
    ))
    fused_output[:, 2] += support_shift_z
    supported_ankle_z = fused_output[:, 2] + lower_ankle_z
    fused_output_high_rate[:, 2] += support_shift_z
    displayed_ankle_world = (
        fused_output[:, None, :] + local_frames[:, ankle_indices, :]
    )
    displayed_contact_constrained = contact_constrained[
        contact_indices_for_viewer
    ]
    final_displayed_ankle_drift = {
        side: {
            "xyz_m": _maximum_masked_episode_drift(
                displayed_ankle_world[:, side_index],
                displayed_contact_constrained[:, side_index],
            ),
            "horizontal_xy_m": _maximum_masked_episode_drift(
                displayed_ankle_world[:, side_index, :2],
                displayed_contact_constrained[:, side_index],
            ),
            "vertical_z_m": _maximum_masked_episode_drift(
                displayed_ankle_world[:, side_index, 2:3],
                displayed_contact_constrained[:, side_index],
            ),
        }
        for side_index, side in enumerate(("left", "right"))
    }

    # Materialize all ten predicted tag proxy positions and independently
    # verify that their coordinate binding lands on the same frozen display
    # points.  This catches a translation/anchor transform that is inconsistent
    # with the avatar transform, which the old hand-written mirror did not.
    node_names = tuple(NODE_TO_PROXY_POINT)
    tag_proxy_output = np.empty((len(viewer_time), len(node_names), 3))
    binding_errors = []
    frame_matches = 0
    source_frames = np.asarray(episode["sourceFrame"], dtype=int)
    for frame_index in range(len(source_frames)):
        # ``local_frames`` is already the authoritative source replay geometry.
        # Do not map a native 200 Hz source frame back onto the older 20 Hz
        # diagnostic index merely to materialize tag proxies; doing so silently
        # reintroduces the display decimation this exporter is meant to remove.
        frame_matches += 1
        pelvis_local = local_frames[frame_index, joint_index["pelvis_center"]]
        for node_index, node in enumerate(node_names):
            point = NODE_TO_PROXY_POINT[node]
            offset_output = (
                local_frames[frame_index, joint_index[point]] - pelvis_local
            )
            tag_proxy_output[frame_index, node_index] = (
                fused_output[frame_index] + offset_output
            )
            displayed_offset = (
                local_frames[frame_index, joint_index[point]] - pelvis_local
            )
            binding_errors.append(float(np.linalg.norm(offset_output - displayed_offset)))
    maximum_binding_error = float(np.max(binding_errors))
    if maximum_binding_error > 2e-4:
        raise RuntimeError(
            f"UWB/avatar coordinate binding mismatch: {maximum_binding_error:.6g} m"
        )

    for frame_index, frame in enumerate(episode["frames"]):
        translation = fused_output[frame_index]
        for offset in range(0, len(frame), 3):
            point = local_frames[frame_index, offset // 3]
            frame[offset] = round(float(point[0] + translation[0]), 4)
            frame[offset + 1] = round(float(point[1] + translation[1]), 4)
            frame[offset + 2] = round(float(point[2] + translation[2]), 4)
    episode["rootTranslationFusion"] = np.round(fused_output, 4).tolist()
    episode["rootTranslationFusionHighRateTime"] = np.round(
        native_viewer_time, 6
    ).tolist()
    episode["rootTranslationFusionHighRate"] = np.round(
        fused_output_high_rate, 5
    ).tolist()
    episode["rootTranslationFusionUngrounded"] = np.round(
        ungrounded_fused_output, 4
    ).tolist()
    episode["rootTranslationImuOnly"] = np.round(imu_output, 4).tolist()
    episode["uwbTagProxyNodeNames"] = list(node_names)
    episode["uwbTagProxyPositions"] = np.round(tag_proxy_output, 4).tolist()
    trust_indices = np.clip(
        np.searchsorted(correction_time_s, query, side="right") - 1,
        0,
        len(correction_time_s) - 1,
    )
    trust_column = {node: index for index, node in enumerate(adaptive_node_order)}
    if set(trust_column) != set(node_names):
        raise ValueError("adaptive trust inventory does not match viewer nodes")
    viewer_trusted = np.column_stack([
        adaptive_node_trusted[trust_indices, trust_column[node]]
        for node in node_names
    ])
    viewer_mode = adaptive_mode[trust_indices]
    episode["uwbTagProxyTrusted"] = viewer_trusted.tolist()
    episode["uwbAdaptiveMode"] = viewer_mode.tolist()
    contact_indices = np.clip(
        np.searchsorted(time_s, query, side="right") - 1,
        0,
        len(time_s) - 1,
    )
    episode["ankleContactActive"] = contact_active[contact_indices].tolist()
    episode["ankleContactConstrained"] = contact_constrained[contact_indices].tolist()
    episode["ankleContactConfidence"] = np.round(
        contact_confidence[contact_indices], 3
    ).tolist()
    episode["ankleContactActiveHighRate"] = contact_active.tolist()
    episode["ankleContactConstrainedHighRate"] = contact_constrained.tolist()
    episode["ankleContactConfidenceHighRate"] = np.round(
        contact_confidence, 3
    ).tolist()
    episode["note"] = (
        "共享十节点 UWB + 骨盆 IMU 因果根平移；"
        + (
            "UWB 留出验证后的关节修正已通过冻结 FK 树传播。"
            if articulated_enabled else "冻结相对姿态未修改。"
        )
    )
    episode["worldMotion"] = {
        "global_translation_applied": True,
        "relative_joint_geometry_modified": articulated_enabled,
        "translation_source": "causal_shared_root_uwb_plus_pelvis_imu",
        "vertical_support_constraint": "CAUSAL_IN_FILTER_INFERRED_ANKLE_FOOTHOLD",
        "display_floor_gauge": "ONE_CONSTANT_Z_SHIFT_FROM_FIRST_SUPPORT_WINDOW",
        "vertical_support_constraint_scope": "INFERRED_CONTACT_EPISODES_ONLY",
        "vertical_support_constraint_is_per_joint_pose_fit": False,
        "vertical_support_constraint_application": "FUSION_STATE_NOT_RENDERER_PINNING",
        "ten_tag_proxy_positions_materialized": True,
        "bilateral_ankle_contact_overlay": True,
        "ten_tag_proxy_positions_are_independent_trilateration": False,
        "old_t4_consumed": False,
    }

    anchor_min = np.min(anchors_output, axis=0)
    anchor_max = np.max(anchors_output, axis=0)
    padding = np.array([0.75, 0.75, 0.75])
    padded_min = anchor_min - padding
    padded_max = anchor_max + padding
    centre = 0.5 * (padded_min + padded_max)
    span = padded_max - padded_min
    zoom = float(np.clip(1.45 / np.max(span), 0.12, 0.35))
    names = tuple("ABCDEFGH")
    data["viewGauge"]["trajectoryModified"] = True
    data["worldMotionDiagnostic"] = {
        "schema": "biospur-c2-hxx-shared-root-imu-fusion-viewer-v2",
        "action": action,
        "relative_pose_source_frozen": not articulated_enabled,
        "global_translation_added": True,
        "fixed_overview_center_output_m": np.round(centre, 4).tolist(),
        "fixed_overview_yaw_rad": float(data["viewGauge"]["frontYawRad"] - np.pi / 4),
        "fixed_overview_pitch_rad": float(np.deg2rad(25.0)),
        "fixed_overview_pitch_deg": 25.0,
        "follow_zoom": 0.55,
        "overview_zoom": zoom,
        "hide_ground_plane": True,
        "uwb_scene": {
            "geometry_role": "fixed_world_frame_and_anchor_volume",
            "anchors_output_m": {
                name: np.round(anchors_output[index], 6).tolist()
                for index, name in enumerate(names)
            },
            "anchor_bounds_output_m": {
                "min": np.round(anchor_min, 6).tolist(),
                "max": np.round(anchor_max, 6).tolist(),
            },
            "padded_bounds_output_m": {
                "min": np.round(padded_min, 6).tolist(),
                "max": np.round(padded_max, 6).tolist(),
            },
            "matrix_uwb_world_from_output": output_from_uwb.T.tolist(),
            "matrix_output_from_uwb_world": output_from_uwb.tolist(),
            "tag_proxy_node_names": list(node_names),
            "tag_proxy_role": "adaptive_x_of_ten_constrained_fk_not_independent_trilateration",
            "maximum_avatar_tag_proxy_binding_error_m": maximum_binding_error,
            "matching_frozen_source_frames": frame_matches,
            "total_viewer_frames": len(source_frames),
            "ground_plane_output_z_m": 0.0,
            "display_floor_gauge_shift_z_m": support_shift_z,
            "first_support_window_max_abs_ankle_height_m": float(
                np.max(np.abs(supported_ankle_z[gauge_rows]))
            ),
        },
        "scientific_position_pass": False,
    }
    native_rate_hz = float(1.0 / np.median(np.diff(time_s)))
    pose_rate_hz = float(1.0 / np.median(np.diff(viewer_time)))
    data["targetFps"] = 60.0
    data["worldMotionDiagnostic"]["playback"] = {
        "fusion_root_sample_rate_hz": native_rate_hz,
        "pose_keyframe_rate_hz": pose_rate_hz,
        "render_clock": "requestAnimationFrame",
        "render_target_fps": 60.0,
        "root_interpolation": "native_200hz_linear_time_interpolation",
        "pose_interpolation": "display_only_cartesian_keyframe_interpolation",
    }
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    derived = source_text[:start] + encoded + source_text[stop:]
    draw_marker = "function draw(){"
    if draw_marker not in derived:
        raise RuntimeError("source viewer draw marker not found")
    derived = derived.replace(
        draw_marker, SMOOTH_PLAYBACK_SCRIPT + "\n" + draw_marker, 1
    )
    base_frame_marker = "flat=ep.frames[frameIndex],sourceRaw=[]"
    mannequin_frame_marker = "const ep=active(),flat=ep.frames[frameIndex],rows=[];"
    if base_frame_marker not in derived or mannequin_frame_marker not in derived:
        raise RuntimeError("source viewer frame lookup markers not found")
    derived = derived.replace(
        base_frame_marker, "flat=viewerInterpolatedFrame(ep),sourceRaw=[]", 1
    )
    derived = derived.replace(
        mannequin_frame_marker,
        "const ep=active(),flat=viewerInterpolatedFrame(ep),rows=[];",
        1,
    )
    action_index = next(
        index for index, row in enumerate(data["episodes"])
        if row["id"] == action
    )
    default_episode_marker = (
        "requestedEpisode=Number(params.get('episode')??0)"
    )
    default_view_marker = "requestedView=params.get('view')??'three'"
    if default_episode_marker not in derived:
        raise RuntimeError("source viewer default-episode marker not found")
    if default_view_marker not in derived:
        raise RuntimeError("source viewer default-view marker not found")
    derived = derived.replace(
        default_episode_marker,
        f"requestedEpisode=Number(params.get('episode')??{action_index})",
        1,
    )
    derived = derived.replace(
        default_view_marker,
        "requestedView=params.get('view')??'oblique'",
        1,
    )
    ground_loop = "for(let i=-2;i<=2;i++){"
    if ground_loop not in derived:
        raise RuntimeError("white-mannequin ground-grid loop not found")
    derived = derived.replace(
        ground_loop,
        "if(!DATA.worldMotionDiagnostic?.hide_ground_plane) " + ground_loop,
    )
    layer = (
        WORLD_LAYER_SCRIPT
        .replace("rootTranslationImuDr", "rootTranslationFusion")
        .replace("rootTranslationRaw3dImuDr", "rootTranslationImuOnly")
        .replace("显示起点（非 UWB 定位）", "融合轨迹起点")
        .replace("IMU raw Δz", "纯 IMU Δz")
        .replace("本动作无 IMU 世界平移层", "本动作无融合世界平移层")
        .replace("固定45°俯视", "固定斜前方25°俯视 · 下侧踝端点接触z=0")
    )
    root_lookup_marker = (
        "const ep=active(),rows=ep.rootTranslationFusion;\n"
        "  return rows?rows[Math.max(0,Math.min(rows.length-1,frameIndex))]:[0,0,0];"
    )
    if root_lookup_marker not in layer:
        raise RuntimeError("world-layer root lookup marker not found")
    layer = layer.replace(
        root_lookup_marker,
        "const ep=active();\n  return viewerRootAt(ep,viewerPlaybackTime(ep));",
        1,
    )
    trail_marker = "  mannequinCtx.strokeStyle='rgba(78,209,196,.22)'"
    if trail_marker not in layer:
        raise RuntimeError("world-layer trail marker not found")
    tag_overlay = r"""
  const anchorA=UWB_SCENE.anchors_output_m.A;
  const zTop=[anchorA[0],anchorA[1],anchorA[2]+1.0];
  const z0=project(...anchorA,w,h),z1=project(...zTop,w,h);
  mannequinCtx.strokeStyle='#4ade80';mannequinCtx.lineWidth=2;mannequinCtx.setLineDash([]);
  mannequinCtx.beginPath();mannequinCtx.moveTo(z0[0],z0[1]);mannequinCtx.lineTo(z1[0],z1[1]);mannequinCtx.stroke();
  mannequinCtx.fillStyle='#4ade80';mannequinCtx.font='bold 12px sans-serif';mannequinCtx.fillText('世界 Z ↑',z1[0]+6,z1[1]-5);
  const floor=UWB_SCENE.anchor_bounds_output_m;
  mannequinCtx.strokeStyle='rgba(148,163,184,.28)';mannequinCtx.lineWidth=1;mannequinCtx.setLineDash([3,5]);
  for(let i=0;i<=6;i+=1){
    const u=i/6,x=floor.min[0]+u*(floor.max[0]-floor.min[0]),y=floor.min[1]+u*(floor.max[1]-floor.min[1]);
    imuDrawSegment([x,floor.min[1],0],[x,floor.max[1],0],w,h);
    imuDrawSegment([floor.min[0],y,0],[floor.max[0],y,0],w,h);
  }
  mannequinCtx.setLineDash([]);
  const tagRows=ep.uwbTagProxyPositions,tagNames=ep.uwbTagProxyNodeNames,trustRows=ep.uwbTagProxyTrusted,modeRows=ep.uwbAdaptiveMode;
  if(tagRows&&tagNames){
    const tagFrame=viewerInterpolatedTagFrame(ep);
    const trustFrame=trustRows?trustRows[Math.max(0,Math.min(trustRows.length-1,frameIndex))]:tagFrame.map(()=>true);
    tagFrame.forEach((q,i)=>{const p=project(q[0],q[1],q[2],w,h);mannequinCtx.fillStyle=trustFrame[i]?'#22c55e':'#f472b6';mannequinCtx.beginPath();mannequinCtx.arc(p[0],p[1],trustFrame[i]?4.0:3.0,0,2*Math.PI);mannequinCtx.fill();if(i<2){mannequinCtx.font='bold 10px sans-serif';mannequinCtx.fillText(tagNames[i].slice(-4),p[0]+4,p[1]-4)}});
    const trusted=trustFrame.filter(Boolean).length,mode=modeRows?modeRows[Math.max(0,Math.min(modeRows.length-1,frameIndex))]:'';
    const trustLabel=`可信 UWB 节点 ${trusted}/10 · ${mode}`;
    mannequinCtx.font='bold 13px sans-serif';
    const trustWidth=mannequinCtx.measureText(trustLabel).width;
    const trustX=Math.max(10,w-trustWidth-26);
    mannequinCtx.fillStyle='rgba(15,23,42,0.88)';mannequinCtx.fillRect(trustX,10,trustWidth+16,24);
    mannequinCtx.strokeStyle='#334155';mannequinCtx.strokeRect(trustX+0.5,10.5,trustWidth+15,23);
    mannequinCtx.fillStyle='#e2e8f0';mannequinCtx.fillText(trustLabel,trustX+8,27);
  }
  const contactRows=ep.ankleContactActiveHighRate||ep.ankleContactActive,constrainedRows=ep.ankleContactConstrainedHighRate||ep.ankleContactConstrained,confidenceRows=ep.ankleContactConfidenceHighRate||ep.ankleContactConfidence;
  if(contactRows&&constrainedRows&&confidenceRows){
    const ci=viewerNearestHighRateIndex(ep,viewerPlaybackTime(ep));
    const names=['L','R'];
    const parts=names.map((name,i)=>`${name}:${contactRows[ci][i]?'触地':'离地'}${constrainedRows[ci][i]?'★':''} ${Number(confidenceRows[ci][i]).toFixed(2)}`);
    const label=`脚踝支撑 ${parts.join(' · ')}`;
    mannequinCtx.font='bold 13px sans-serif';
    const width=mannequinCtx.measureText(label).width;
    const x=Math.max(10,w-width-26);
    mannequinCtx.fillStyle='rgba(15,23,42,0.88)';mannequinCtx.fillRect(x,38,width+16,24);
    mannequinCtx.strokeStyle='#334155';mannequinCtx.strokeRect(x+0.5,38.5,width+15,23);
    mannequinCtx.fillStyle='#facc15';mannequinCtx.fillText(label,x+8,55);
  }
"""
    layer = layer.replace(trail_marker, tag_overlay + "\n" + trail_marker)
    if "</body>" in derived:
        derived = derived.replace("</body>", layer + "\n</body>")
    else:
        derived += "\n" + layer
    html = output / f"c2_avatar_{action}_shared_root_imu_fusion.html"
    html.write_text(derived, encoding="utf-8")
    audit = {
        "schema": "biospur-c2-hxx-shared-root-fusion-viewer-audit-v2",
        "status": "DISPLAY_EXPORT_COMPLETE_NOT_SCIENTIFIC_PASS",
        "action": action,
        "relative_joint_pose_modified": articulated_enabled,
        "root_translation_source": str(fusion_npz),
        "root_translation_source_sha256": _sha256(fusion_npz),
        "fusion_result": str(fusion_result),
        "fusion_result_sha256": _sha256(fusion_result),
        "source_viewer": str(source),
        "source_viewer_sha256": _sha256(source),
        "pose_source_viewer": None if pose_source is None else str(pose_source),
        "pose_source_viewer_sha256": (
            None if pose_source is None else _sha256(pose_source)
        ),
        "source_trajectory": (
            None if source_trajectory is None else str(source_trajectory)
        ),
        "source_trajectory_sha256": (
            None if source_trajectory is None else _sha256(source_trajectory)
        ),
        "articulated_base_rotation_rate": (
            "NATIVE_POSE_GRID" if native_base_rotations is not None
            else "LEGACY_DIAGNOSTIC_GRID"
        ),
        "articulated_base_source_sample_rate_hz": (
            None if native_base_rotations is None
            else float(1.0 / np.median(np.diff(native_time)))
        ),
        "bootstrap_offset_from_action_start_s": bootstrap_offset_s,
        "output": str(html),
        "output_sha256": _sha256(html),
        "camera": "FIXED_25_DEG_ELEVATION_45_DEG_AZIMUTH_WORLD_BOX",
        "ground_plane_hidden": True,
        "anchor_world_ground_plane_rendered": True,
        "camera_pitch_deg": 25.0,
        "queryless_default_episode_index": action_index,
        "queryless_default_episode_id": action,
        "queryless_default_view": "oblique",
        "uwb_volume_visible_for_non_fusion_episodes": True,
        "matrix_output_from_uwb_world": output_from_uwb.tolist(),
        "maximum_avatar_tag_proxy_binding_error_m": maximum_binding_error,
        "matching_frozen_source_frames": frame_matches,
        "total_viewer_frames": len(source_frames),
        "ten_tag_proxy_positions_materialized": True,
        "adaptive_trust_overlay": True,
        "bilateral_ankle_contact_overlay": True,
        "fusion_root_sample_rate_hz": native_rate_hz,
        "pose_keyframe_rate_hz": pose_rate_hz,
        "render_target_fps": 60.0,
        "continuous_playback_interpolation": True,
        "trusted_node_color": "GREEN",
        "propagated_node_color": "PINK",
        "tag_proxy_positions_are_independent_trilateration": False,
        "articulated_correction_source": (
            correction_source
            if articulated_enabled else "DISABLED"
        ),
        "maximum_articulated_segment_correction_rad": float(np.max(
            np.linalg.norm(correction_viewer, axis=2)
        )),
        "support_constraint": "CAUSAL_IN_FILTER_INFERRED_ANKLE_FOOTHOLD",
        "display_floor_gauge": "ONE_CONSTANT_Z_SHIFT_FROM_FIRST_SUPPORT_WINDOW",
        "display_floor_gauge_shift_z_m": support_shift_z,
        "first_support_window_max_abs_ankle_height_m": float(
            np.max(np.abs(supported_ankle_z[gauge_rows]))
        ),
        "final_displayed_articulated_ankle_constrained_drift": (
            final_displayed_ankle_drift
        ),
        "final_displayed_ankle_drift_owner": (
            "FUSED_ROOT_PLUS_NATIVE_POSE_PLUS_FINAL_INTERPOLATED_"
            "ARTICULATED_CORRECTIONS"
        ),
    }
    audit_path = output / "VIEWER_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2) + "\n")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--action", required=True, choices=SUPPORTED_ACTIONS)
    parser.add_argument("--fusion-npz", required=True, type=Path)
    parser.add_argument("--fusion-result", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--pose-source", type=Path)
    parser.add_argument("--source-trajectory", type=Path)
    args = parser.parse_args()
    print(json.dumps(run(
        args.output.resolve(),
        action=args.action,
        fusion_npz=args.fusion_npz.resolve(),
        fusion_result=args.fusion_result.resolve(),
        source=args.source.resolve(),
        pose_source=(
            None if args.pose_source is None else args.pose_source.resolve()
        ),
        source_trajectory=(
            None if args.source_trajectory is None
            else args.source_trajectory.resolve()
        ),
    ), indent=2))


if __name__ == "__main__":
    main()
