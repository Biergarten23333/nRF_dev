#!/usr/bin/env python3
"""Add an explicit IMU dead-reckoning root layer to the frozen C2 H01 avatar.

The frozen relative pose is never refit.  This tool estimates one pelvis-root
translation from the pelvis accelerometer and the continuous VQF orientation,
then applies that same translation to every displayed joint.  The result is a
diagnostic, not UWB-quality world positioning.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct
from typing import Any

import numpy as np
import qmt
from scipy.integrate import cumulative_trapezoid
from scipy.signal import butter, sosfiltfilt
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT, ROOT
from biospur_fusion.c2_coupled_progressive.holdout_replay import (
    G,
    IMU_SAMPLE,
    SAMPLE_PERIOD_S,
    _complete_cobs_frames,
    _imu_envelope,
)
from tools.run_c2_hxx_frozen_replay import (
    CANONICAL_RAW,
    INITIAL_TRAINING_RANGE,
    _alignment,
    _holdout_record,
    _json,
)


ACTION = "H01_boxing"
PELVIS_NODE = next(
    node for node, segment in NODE_TO_SEGMENT.items() if segment == "pelvis"
)
DEFAULT_SOURCE = (
    ROOT
    / "logs/c2_avatar_white_mannequin_v9_20260901_075126/c2_avatar_3d.html"
)
DEFAULT_OUTPUT_MATRIX = (
    ROOT
    / "logs/c2_hxx_frozen_replay_20260831_220900/"
    "HXX_FROZEN_C2_REPLAY_TRAJECTORY.npz"
)
DEFAULT_UWB_WORLD_BINDING = (
    ROOT
    / "logs/c2_uwb_world_geometry_binding_20260901_112938/"
    "C2_UWB_WORLD_GEOMETRY_BINDING.json"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _extract_data(source: str) -> tuple[dict[str, Any], int, int]:
    prefix = "const DATA="
    start = source.index(prefix) + len(prefix)
    value, length = json.JSONDecoder().raw_decode(source[start:])
    if not isinstance(value, dict):
        raise ValueError("viewer DATA must be an object")
    return value, start, start + length


def _continuous_pelvis_samples(
    start_byte: int, stop_byte: int
) -> tuple[np.ndarray, np.ndarray, np.ndarray, dict[str, Any]]:
    block = qmt.OriEstVQFBlock(SAMPLE_PERIOD_S)
    timers: list[int] = []
    accels: list[np.ndarray] = []
    quats: list[np.ndarray] = []
    last_timer: int | None = None
    decoded_frames = 0
    decode_errors: dict[str, int] = {}
    for _raw_start, _raw_stop, encoded in _complete_cobs_frames(
        CANONICAL_RAW, start_byte, stop_byte
    ):
        if not encoded:
            continue
        try:
            decoded = _imu_envelope(encoded)
            if decoded is None or decoded[0] != PELVIS_NODE:
                continue
            _, payload = decoded
            if len(payload) < 14:
                raise ValueError("short IMU header")
            version, count, _sequence, base_us = struct.unpack_from(
                "<BBHQ", payload, 0
            )
            if (
                version != 7
                or not 1 <= count <= 16
                or len(payload) != 14 + count * IMU_SAMPLE.size
            ):
                raise ValueError("IMU contract")
            decoded_frames += 1
            for index in range(count):
                delta, ax, ay, az, gx, gy, gz = IMU_SAMPLE.unpack_from(
                    payload, 14 + index * IMU_SAMPLE.size
                )
                timer_us = int(base_us + delta)
                if last_timer is not None and timer_us <= last_timer:
                    raise RuntimeError("non-monotone pelvis timer")
                last_timer = timer_us
                acc = np.array([ax, ay, az], dtype=float) / 2048.0 * G
                gyr = np.deg2rad(
                    np.array([gx, gy, gz], dtype=float) / 16.384
                )
                quat = np.asarray(block.step(gyr, acc, None), dtype=float)
                timers.append(timer_us)
                accels.append(acc)
                quats.append(quat)
        except (ValueError, struct.error, IndexError) as exc:
            key = f"{type(exc).__name__}:{exc}"
            decode_errors[key] = decode_errors.get(key, 0) + 1
    if len(timers) < 2:
        raise RuntimeError("insufficient pelvis samples")
    return (
        np.asarray(timers, dtype=np.int64),
        np.stack(accels),
        np.stack(quats),
        {
            "pelvis_node": PELVIS_NODE,
            "decoded_pelvis_frames": decoded_frames,
            "native_pelvis_samples": len(timers),
            "decode_errors": decode_errors,
            "continuous_vqf_instances": 1,
            "vqf_state_resets": 0,
        },
    )


def _integrate_root(
    timer_us: np.ndarray,
    acc_sensor: np.ndarray,
    quat_world_sensor_wxyz: np.ndarray,
    node_model: Any,
    formal_start_ns: int,
    formal_stop_ns: int,
) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    mapped_ns = np.array(
        [node_model.map_ns(int(value)) for value in timer_us], dtype=np.int64
    )
    mask = (mapped_ns >= formal_start_ns) & (mapped_ns <= formal_stop_ns)
    if np.count_nonzero(mask) < 100:
        raise RuntimeError("formal H01 pelvis interval too short")
    mapped_ns = mapped_ns[mask]
    acc_sensor = acc_sensor[mask]
    quat = quat_world_sensor_wxyz[mask]
    time_s = (mapped_ns - mapped_ns[0]).astype(float) * 1e-9

    # qmt uses scalar-first quaternions and returns sensor-to-world orientation.
    acc_world = Rotation.from_quat(quat[:, [1, 2, 3, 0]]).apply(acc_sensor)
    linear_world = acc_world - np.array([0.0, 0.0, G])
    sample_rate_hz = 1.0 / float(np.median(np.diff(time_s)))
    sos = butter(4, 8.0, btype="lowpass", fs=sample_rate_hz, output="sos")
    filtered = sosfiltfilt(sos, linear_world, axis=0)

    velocity_uncorrected = cumulative_trapezoid(
        filtered, time_s, axis=0, initial=0.0
    )
    duration_s = float(time_s[-1])
    # H01 begins and ends in protocol rest.  A single constant acceleration
    # correction enforces v(0)=v(T)=0; it is not a trajectory fit and it does
    # not suppress residual time-varying drift.
    constant_accel_correction = velocity_uncorrected[-1] / duration_s
    corrected_accel = filtered - constant_accel_correction
    velocity = cumulative_trapezoid(
        corrected_accel, time_s, axis=0, initial=0.0
    )
    position = cumulative_trapezoid(velocity, time_s, axis=0, initial=0.0)

    return time_s, position, {
        "duration_s": duration_s,
        "native_rows": int(len(time_s)),
        "median_dt_s": float(np.median(np.diff(time_s))),
        "sample_rate_hz": sample_rate_hz,
        "lowpass_hz": 8.0,
        "gravity_m_s2": G,
        "unconstrained_end_velocity_m_s": velocity_uncorrected[-1].tolist(),
        "constant_accel_correction_m_s2": constant_accel_correction.tolist(),
        "corrected_end_velocity_m_s": velocity[-1].tolist(),
        "corrected_end_position_internal_m": position[-1].tolist(),
        "maximum_horizontal_speed_m_s": float(
            np.max(np.linalg.norm(velocity[:, :2], axis=1))
        ),
        "stationary_endpoint_assumption": "protocol start/end rest; v(0)=v(T)=0",
    }


WORLD_LAYER_SCRIPT = r"""
<style id="imuWorldMotionStyle">
#imuWorldMotionStatus{font-size:12px;color:#62d9b0;font-weight:700;white-space:nowrap}
@media (prefers-color-scheme:light){#imuWorldMotionStatus{color:#087f5b}}
</style>
<script id="imuWorldMotionLayer">
const IMU_WORLD=DATA.worldMotionDiagnostic;
const imuBaseProject=project;
const imuBaseFollowBodyCamera=followBodyCamera;
const UWB_SCENE=IMU_WORLD.uwb_scene;
let imuWorldCameraFollow=false;
function imuRoot(){
  const ep=active(),rows=ep.rootTranslationImuDr;
  return rows?rows[Math.max(0,Math.min(rows.length-1,frameIndex))]:[0,0,0];
}
function imuCameraCenter(){
  if(active().id!==IMU_WORLD.action)return[0,0,0];
  return imuWorldCameraFollow?imuRoot():IMU_WORLD.fixed_overview_center_output_m;
}
project=function(x,y,z,w,h){
  const c=imuCameraCenter();
  return imuBaseProject(x-c[0],y-c[1],z-c[2],w,h);
};
followBodyCamera=function(raw){
  if(active().id===IMU_WORLD.action&&!imuWorldCameraFollow)return;
  imuBaseFollowBodyCamera(raw);
};
const imuFollowButton=document.createElement('button');
imuFollowButton.type='button';imuFollowButton.textContent='跟随人体（诊断）';
const imuOverviewButton=document.createElement('button');
imuOverviewButton.type='button';imuOverviewButton.textContent='固定空间总览';
const imuStatus=document.createElement('span');imuStatus.id='imuWorldMotionStatus';
topBar.append(imuFollowButton,imuOverviewButton,imuStatus);
function updateImuButtons(){
  imuFollowButton.setAttribute('aria-pressed',String(imuWorldCameraFollow));
  imuOverviewButton.setAttribute('aria-pressed',String(!imuWorldCameraFollow));
}
function setImuWorldCameraMode(follow,requestedZoom){
  imuWorldCameraFollow=follow;
  zoom=Number.isFinite(requestedZoom)?requestedZoom:(follow?IMU_WORLD.follow_zoom:IMU_WORLD.overview_zoom);
  if(!follow){
    yaw=IMU_WORLD.fixed_overview_yaw_rad;
    pitch=IMU_WORLD.fixed_overview_pitch_rad;
  }
  updateImuButtons();draw();
}
imuFollowButton.addEventListener('click',()=>setImuWorldCameraMode(true,IMU_WORLD.follow_zoom));
imuOverviewButton.addEventListener('click',()=>setImuWorldCameraMode(false,IMU_WORLD.overview_zoom));
function imuCorners(bounds){
  const lo=bounds.min,hi=bounds.max;
  return [
    [lo[0],lo[1],lo[2]],[hi[0],lo[1],lo[2]],[hi[0],hi[1],lo[2]],[lo[0],hi[1],lo[2]],
    [lo[0],lo[1],hi[2]],[hi[0],lo[1],hi[2]],[hi[0],hi[1],hi[2]],[lo[0],hi[1],hi[2]],
  ];
}
const imuBoxEdges=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
function imuDrawSegment(a,b,w,h){
  const p=project(a[0],a[1],a[2],w,h),q=project(b[0],b[1],b[2],w,h);
  mannequinCtx.beginPath();mannequinCtx.moveTo(p[0],p[1]);mannequinCtx.lineTo(q[0],q[1]);mannequinCtx.stroke();
}
function imuDrawBox(bounds,w,h,stroke,width,dash){
  const corners=imuCorners(bounds);
  mannequinCtx.strokeStyle=stroke;mannequinCtx.lineWidth=width;mannequinCtx.setLineDash(dash);
  imuBoxEdges.forEach(([a,b])=>imuDrawSegment(corners[a],corners[b],w,h));
}
function renderImuWorldScene(){
  const ep=active(),rows=ep.rootTranslationImuDr;
  if(!rows){imuStatus.textContent='本动作无 IMU 世界平移层';return}
  const r=mannequinCanvas.getBoundingClientRect(),w=r.width,h=r.height;
  const all=rows.map(q=>project(q[0],q[1],q[2],w,h));
  const upto=Math.max(1,Math.min(all.length,frameIndex+1));
  mannequinCtx.save();mannequinCtx.lineCap='round';mannequinCtx.lineJoin='round';
  imuDrawBox(UWB_SCENE.padded_bounds_output_m,w,h,'rgba(205,214,223,.72)',2,[]);
  imuDrawBox(UWB_SCENE.anchor_bounds_output_m,w,h,'rgba(73,163,255,.72)',1.5,[7,5]);
  mannequinCtx.setLineDash([]);
  const outerLabel=project(...UWB_SCENE.padded_bounds_output_m.max,w,h);
  const innerLabel=project(...UWB_SCENE.anchor_bounds_output_m.max,w,h);
  mannequinCtx.font='bold 12px sans-serif';
  mannequinCtx.fillStyle='rgba(225,231,238,.9)';mannequinCtx.fillText('固定显示运动包络',outerLabel[0]+6,outerLabel[1]-7);
  mannequinCtx.fillStyle='rgba(91,177,255,.95)';mannequinCtx.fillText('A–H UWB 锚点体积',innerLabel[0]+6,innerLabel[1]+15);
  for(const [name,q] of Object.entries(UWB_SCENE.anchors_output_m)){
    const p=project(q[0],q[1],q[2],w,h);
    mannequinCtx.fillStyle='#ffd166';mannequinCtx.beginPath();mannequinCtx.arc(p[0],p[1],4,0,2*Math.PI);mannequinCtx.fill();
    mannequinCtx.font='bold 12px sans-serif';mannequinCtx.fillText(name,p[0]+6,p[1]-5);
  }
  mannequinCtx.strokeStyle='rgba(78,209,196,.22)';mannequinCtx.lineWidth=2;
  mannequinCtx.beginPath();all.forEach((q,i)=>i?mannequinCtx.lineTo(q[0],q[1]):mannequinCtx.moveTo(q[0],q[1]));mannequinCtx.stroke();
  mannequinCtx.strokeStyle='#4ed1c4';mannequinCtx.lineWidth=3;
  mannequinCtx.beginPath();all.slice(0,upto).forEach((q,i)=>i?mannequinCtx.lineTo(q[0],q[1]):mannequinCtx.moveTo(q[0],q[1]));mannequinCtx.stroke();
  const origin=all[0],now=all[upto-1];
  mannequinCtx.fillStyle='#f6c85f';mannequinCtx.beginPath();mannequinCtx.arc(origin[0],origin[1],5,0,2*Math.PI);mannequinCtx.fill();
  mannequinCtx.fillStyle='#4ed1c4';mannequinCtx.beginPath();mannequinCtx.arc(now[0],now[1],5,0,2*Math.PI);mannequinCtx.fill();
  mannequinCtx.font='bold 12px sans-serif';mannequinCtx.fillStyle='#f6c85f';mannequinCtx.fillText('显示起点（非 UWB 定位）',origin[0]+8,origin[1]-8);
  mannequinCtx.restore();
  const q=imuRoot(),m=UWB_SCENE.matrix_uwb_world_from_output;
  const world=m.map(row=>row[0]*q[0]+row[1]*q[1]+row[2]*q[2]);
  const rawZ=ep.rootTranslationRaw3dImuDr[frameIndex][2];
  imuStatus.textContent=`UWB场地坐标读数: x=${world[0].toFixed(2)} m · y=${world[1].toFixed(2)} m · IMU raw Δz=${rawZ.toFixed(2)} m · ${imuWorldCameraFollow?'相机跟随':'固定45°俯视'}`;
}
const imuFrozenRenderMannequin=renderMannequin;
renderMannequin=function(){imuFrozenRenderMannequin();renderImuWorldScene()};
updateImuButtons();setImuWorldCameraMode(false,IMU_WORLD.overview_zoom);
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--output-matrix-artifact", type=Path, default=DEFAULT_OUTPUT_MATRIX
    )
    parser.add_argument(
        "--uwb-world-binding", type=Path, default=DEFAULT_UWB_WORLD_BINDING
    )
    args = parser.parse_args()
    source_path = args.source.resolve()
    output_dir = args.output_dir.resolve()
    matrix_path = args.output_matrix_artifact.resolve()
    uwb_binding_path = args.uwb_world_binding.resolve()
    if (
        ROOT not in source_path.parents
        or ROOT not in output_dir.parents
        or ROOT not in uwb_binding_path.parents
    ):
        raise SystemExit(
            "source, UWB binding, and output must remain under canonical Fusion_Part"
        )
    if output_dir.exists():
        raise SystemExit(f"refusing to reuse output directory: {output_dir}")
    output_dir.mkdir(parents=True)

    record = _holdout_record(ACTION)
    models, alignment = _alignment(record)
    initial_range = _json(INITIAL_TRAINING_RANGE)
    timer, accel, quat, decode_audit = _continuous_pelvis_samples(
        int(initial_range["start_byte_inclusive"]), record["stop_byte"]
    )
    time_s, position_internal, integration_audit = _integrate_root(
        timer,
        accel,
        quat,
        models[PELVIS_NODE],
        alignment["formal_start_shared_ns"],
        alignment["formal_stop_shared_ns"],
    )
    with np.load(matrix_path, allow_pickle=False) as archive:
        matrix = np.asarray(
            archive["output_coordinates/matrix_world_output_from_internal"],
            dtype=float,
        )
    if matrix.shape != (3, 3) or not np.allclose(
        matrix.T @ matrix, np.eye(3), atol=1e-6
    ):
        raise RuntimeError("invalid frozen output-coordinate matrix")
    position_output = position_internal @ matrix.T

    uwb_binding = _json(uwb_binding_path)
    geometry = uwb_binding["canonical_anchor_geometry"]
    geometry_path = Path(geometry["path"])
    if _sha256(geometry_path) != geometry["sha256"]:
        raise RuntimeError("canonical UWB geometry hash mismatch")
    anchor_names = tuple("ABCDEFGH")
    if tuple(sorted(geometry["anchors_world_m"])) != anchor_names:
        raise RuntimeError("UWB geometry must contain exactly anchors A-H")
    anchors_uwb = np.asarray(
        [geometry["anchors_world_m"][name] for name in anchor_names], dtype=float
    )
    if anchors_uwb.shape != (8, 3) or not np.isfinite(anchors_uwb).all():
        raise RuntimeError("invalid UWB anchor coordinates")
    # The frozen avatar output has parity -1.  Reflecting UWB +Y into output -Y
    # maps the right-handed anchor-local world to the already-frozen display
    # coordinates without changing any relative joint pose.
    matrix_output_from_uwb = np.diag([1.0, -1.0, 1.0])
    anchors_output = anchors_uwb @ matrix_output_from_uwb.T
    anchor_min_output = np.min(anchors_output, axis=0)
    anchor_max_output = np.max(anchors_output, axis=0)
    scene_padding_m = np.array([0.55, 0.55, 0.35], dtype=float)
    minimum_scene_min_output = anchor_min_output - scene_padding_m
    minimum_scene_max_output = anchor_max_output + scene_padding_m
    initial_pelvis_uwb_m = np.array(
        [
            float(np.mean([np.min(anchors_uwb[:, 0]), np.max(anchors_uwb[:, 0])])),
            float(np.mean([np.min(anchors_uwb[:, 1]), np.max(anchors_uwb[:, 1])])),
            0.92,
        ]
    )
    initial_pelvis_output_m = initial_pelvis_uwb_m @ matrix_output_from_uwb.T

    source_text = source_path.read_text(encoding="utf-8")
    data, start, stop = _extract_data(source_text)
    episode = next(row for row in data["episodes"] if row["id"] == ACTION)
    viewer_time = np.asarray(episode["time"], dtype=float)
    resampled = np.column_stack(
        [np.interp(viewer_time, time_s, position_output[:, axis]) for axis in range(3)]
    )
    display_translation = resampled.copy()
    display_translation[:, 2] = 0.0
    display_translation += initial_pelvis_output_m
    for frame_index, frame in enumerate(episode["frames"]):
        delta = display_translation[frame_index]
        for joint_offset in range(0, len(frame), 3):
            frame[joint_offset] = round(float(frame[joint_offset] + delta[0]), 4)
            frame[joint_offset + 1] = round(
                float(frame[joint_offset + 1] + delta[1]), 4
            )
            frame[joint_offset + 2] = round(
                float(frame[joint_offset + 2] + delta[2]), 4
            )
    episode["rootTranslationImuDr"] = np.round(display_translation, 4).tolist()
    episode["rootTranslationRaw3dImuDr"] = np.round(resampled, 4).tolist()
    episode["worldMotion"] = {
        "global_translation_applied": True,
        "relative_joint_geometry_modified": False,
        "dynamic_vertical_display_translation_applied": False,
        "constant_initial_pelvis_height_assumption_applied": True,
        "method": "pelvis IMU continuous VQF + gravity removal + double integration",
        "uwb_geometry_used_for_scene_only": True,
        "uwb_ranges_or_positions_used_for_translation": False,
    }
    episode["note"] = (
        "冻结相对姿态未重拟合；新增骨盆 IMU 水平航位推算平移。"
        "A-H UWB 几何只定义固定场地框和坐标轴；该层保留累计漂移，"
        "不是 UWB 级世界定位。"
    )
    mins = np.min(display_translation[:, :2], axis=0)
    maxs = np.max(display_translation[:, :2], axis=0)
    all_display_joints = np.asarray(episode["frames"], dtype=float).reshape(-1, 3)
    padded_min_output = np.minimum(
        minimum_scene_min_output, np.min(all_display_joints, axis=0) - 0.35
    )
    padded_max_output = np.maximum(
        minimum_scene_max_output, np.max(all_display_joints, axis=0) + 0.35
    )
    camera_min_output = padded_min_output - 0.15
    camera_max_output = padded_max_output + 0.15
    center = (camera_min_output + camera_max_output) / 2
    span = np.maximum(camera_max_output - camera_min_output, 1e-3)
    overview_zoom = float(np.clip(1.45 / float(np.max(span)), 0.07, 0.35))
    data["viewGauge"]["trajectoryModified"] = True
    data["worldMotionDiagnostic"] = {
        "schema": "biospur-c2-h01-pelvis-imu-dead-reckoning-v1",
        "action": ACTION,
        "relative_pose_source_frozen": True,
        "global_translation_added": True,
        "dynamic_translation_displayed_horizontally_only": True,
        "vertical_dead_reckoning_retained_as_readout_only": True,
        "fixed_overview_center_output_m": np.round(center, 4).tolist(),
        "fixed_overview_yaw_rad": float(data["viewGauge"]["frontYawRad"] - np.pi / 4),
        "fixed_overview_pitch_rad": float(np.pi / 4),
        "follow_zoom": 0.55,
        "overview_zoom": overview_zoom,
        "hide_ground_plane": True,
        "uwb_scene": {
            "geometry_role": "fixed_scene_scale_and_axes_only_not_position_fusion",
            "binding_path": str(uwb_binding_path.relative_to(ROOT)),
            "binding_sha256": _sha256(uwb_binding_path),
            "canonical_geometry_path": geometry["path"],
            "canonical_geometry_sha256": geometry["sha256"],
            "anchors_uwb_world_m": {
                name: np.round(anchors_uwb[index], 6).tolist()
                for index, name in enumerate(anchor_names)
            },
            "anchors_output_m": {
                name: np.round(anchors_output[index], 6).tolist()
                for index, name in enumerate(anchor_names)
            },
            "anchor_bounds_output_m": {
                "min": np.round(anchor_min_output, 6).tolist(),
                "max": np.round(anchor_max_output, 6).tolist(),
            },
            "padded_bounds_output_m": {
                "min": np.round(padded_min_output, 6).tolist(),
                "max": np.round(padded_max_output, 6).tolist(),
            },
            "minimum_padding_each_side_m": scene_padding_m.tolist(),
            "outer_box_role": "fixed_display_motion_envelope",
            "outer_box_expanded_to_contain_complete_imu_dr_avatar": True,
            "matrix_output_from_uwb_world": matrix_output_from_uwb.tolist(),
            "matrix_uwb_world_from_output": matrix_output_from_uwb.T.tolist(),
            "initial_pelvis_display_assumption_uwb_world_m": np.round(
                initial_pelvis_uwb_m, 6
            ).tolist(),
            "camera_fit_bounds_output_m": {
                "min": np.round(camera_min_output, 6).tolist(),
                "max": np.round(camera_max_output, 6).tolist(),
            },
            "uwb_ranges_or_positions_used_for_translation": False,
        },
        "scientific_position_pass": False,
    }
    encoded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    derived_text = source_text[:start] + encoded + source_text[stop:]
    ground_loop = "for(let i=-2;i<=2;i++){"
    if ground_loop not in derived_text:
        raise RuntimeError("white-mannequin ground-grid loop not found")
    derived_text = derived_text.replace(
        ground_loop,
        "if(!DATA.worldMotionDiagnostic?.hide_ground_plane) " + ground_loop,
    )
    if "</body>" in derived_text:
        derived_text = derived_text.replace(
            "</body>", WORLD_LAYER_SCRIPT + "\n</body>"
        )
    else:
        derived_text += "\n" + WORLD_LAYER_SCRIPT
    html_path = output_dir / "c2_avatar_h01_imu_world_motion.html"
    html_path.write_text(derived_text, encoding="utf-8")

    npz_path = output_dir / "H01_PELVIS_IMU_DEAD_RECKONING.npz"
    np.savez_compressed(
        npz_path,
        time_s=time_s,
        position_internal_raw3d_m=position_internal,
        position_output_raw3d_m=position_output,
        viewer_time_s=viewer_time,
        viewer_position_output_raw3d_m=resampled,
        viewer_position_output_display_m=display_translation,
        matrix_world_output_from_internal=matrix,
        matrix_output_from_uwb_world=matrix_output_from_uwb,
        anchors_uwb_world_m=anchors_uwb,
        anchors_output_m=anchors_output,
        initial_pelvis_display_assumption_uwb_world_m=initial_pelvis_uwb_m,
    )
    integration_audit["end_position_output_raw3d_m"] = position_output[-1].tolist()
    integration_audit["display_horizontal_extent_m"] = {
        "x": [float(mins[0]), float(maxs[0])],
        "y": [float(mins[1]), float(maxs[1])],
    }
    audit = {
        "schema": "biospur-c2-h01-imu-dead-reckoning-audit-v1",
        "status": "IMU_DEAD_RECKONING_DIAGNOSTIC_NOT_WORLD_POSITION_PASS",
        "action": ACTION,
        "source_frozen_viewer": {
            "path": str(source_path.relative_to(ROOT)),
            "sha256": _sha256(source_path),
            "relative_joint_pose_refit": False,
        },
        "derived_viewer": {
            "path": str(html_path.relative_to(ROOT)),
            "sha256": _sha256(html_path),
        },
        "trajectory_artifact": {
            "path": str(npz_path.relative_to(ROOT)),
            "sha256": _sha256(npz_path),
        },
        "output_coordinate_matrix_artifact": {
            "path": str(matrix_path.relative_to(ROOT)),
            "sha256": _sha256(matrix_path),
        },
        "uwb_world_geometry_binding": {
            "path": str(uwb_binding_path.relative_to(ROOT)),
            "sha256": _sha256(uwb_binding_path),
            "canonical_geometry_path": geometry["path"],
            "canonical_geometry_sha256": geometry["sha256"],
            "used_for_scene_box_and_axes_only": True,
            "used_for_translation_estimation": False,
        },
        "decode": decode_audit,
        "integration": integration_audit,
        "display_contract": {
            "same_translation_applied_to_all_14_joints": True,
            "relative_joint_geometry_modified": False,
            "horizontal_dynamic_translation_displayed": True,
            "constant_initial_pelvis_height_assumption_applied": True,
            "raw_vertical_translation_displayed_as_numeric_readout_only": True,
            "camera_modes": ["body_following_diagnostic", "uwb_scene_fixed_45deg"],
            "root_trail_and_origin_drawn": True,
            "uwb_anchor_volume_drawn": True,
            "uwb_padded_scene_box_drawn": True,
            "ground_plane_hidden": True,
        },
        "limitations": [
            "UWB anchor geometry only defines the fixed scene scale and axes; no UWB range or position correction is used.",
            "The initial pelvis placement at the UWB-footprint centre and 0.92 m height is an explicit display assumption, not a solved UWB position.",
            "Double integration retains time-varying inertial drift.",
            "The endpoint zero-velocity condition removes one constant acceleration mode.",
            "Dynamic vertical translation is not applied to the avatar until foot/contact or UWB constraints exist; only the explicit constant 0.92 m display placement is applied.",
            "This diagnostic is not an accurate world-position estimate.",
        ],
    }
    audit_path = output_dir / "H01_IMU_DEAD_RECKONING_AUDIT.json"
    audit_path.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "html": str(html_path),
                "npz": str(npz_path),
                "audit": str(audit_path),
                "end_position_output_raw3d_m": position_output[-1].tolist(),
                "overview_zoom": overview_zoom,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
