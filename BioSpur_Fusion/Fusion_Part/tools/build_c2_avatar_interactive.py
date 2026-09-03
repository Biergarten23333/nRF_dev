#!/usr/bin/env python3
"""Build a self-contained interactive 3-D C2 avatar replay."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_coupled_progressive.contracts import (
    EPISODES,
    ROOT,
    load_effective_config,
)
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from biospur_fusion.c2_coupled_progressive.renderer import (
    SKELETON_LINES,
    display_models,
    joints_for_frame,
)


ACTION_TABLE = ROOT / (
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "subject/ACTUAL_ACTION_EXECUTION_TABLE.md"
)
JOINT_NAMES = (
    "pelvis_center",
    "shoulder_mid",
    "shoulder_left",
    "shoulder_right",
    "hip_left",
    "hip_right",
    "elbow_left",
    "wrist_left",
    "elbow_right",
    "wrist_right",
    "knee_left",
    "ankle_left",
    "knee_right",
    "ankle_right",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _descriptions() -> dict[str, str]:
    result: dict[str, str] = {}
    for line in ACTION_TABLE.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0].isdigit():
            result[cells[1].strip("`")] = cells[5]
    missing = [name for name in EPISODES if name not in result]
    if missing:
        raise RuntimeError(f"action descriptions missing: {missing}")
    return result


def _load_trajectory(path: Path) -> dict[str, Any]:
    trajectory: dict[str, dict[str, dict[str, np.ndarray]]] = {}
    with np.load(path, allow_pickle=False) as archive:
        for episode_index in range(len(EPISODES)):
            key = f"{episode_index:02d}"
            trajectory[key] = {}
            for segment in SEGMENTS:
                base = f"trajectory/{key}/{segment}"
                trajectory[key][segment] = {
                    "time_root_s": np.array(archive[f"{base}/time_root_s"]),
                    "quat_world_segment_wxyz": np.array(
                        archive[f"{base}/quat_world_segment_wxyz"]
                    ),
                    "mask": np.array(archive[f"{base}/mask"], dtype=bool),
                }
        output_coordinate_convention = None
        matrix_key = "output_coordinates/matrix_world_output_from_internal"
        normal_key = "output_coordinates/plane_normal_world_internal"
        if matrix_key in archive.files and normal_key in archive.files:
            output_coordinate_convention = {
                "schema": "biospur-c2-capture-wide-output-coordinates-v1",
                "matrix_world_output_from_internal": np.array(archive[matrix_key]),
                "plane_normal_world_internal": np.array(archive[normal_key]),
            }
    result: dict[str, Any] = {"trajectory": trajectory}
    if output_coordinate_convention is not None:
        result["output_coordinate_convention"] = output_coordinate_convention
    return result


def _line_color(first: str, second: str) -> str:
    if first.endswith("_left") and second.endswith("_left"):
        return "left"
    if first.endswith("_right") and second.endswith("_right"):
        return "right"
    return "center"


def _payload(
    trajectory: dict[str, Any],
    *,
    target_fps: float,
    forward_branch_yaw_deg: int,
    global_lateral_mirror: bool,
) -> dict[str, Any]:
    coordinates_solidified = "output_coordinate_convention" in trajectory
    if coordinates_solidified and global_lateral_mirror:
        raise ValueError(
            "trajectory already contains the frozen capture-wide reflection; "
            "refusing to apply it twice"
        )
    descriptions = _descriptions()
    config = load_effective_config()
    model = display_models(config)[1]
    joint_index = {name: index for index, name in enumerate(JOINT_NAMES)}
    lines = [
        [joint_index[first], joint_index[second], _line_color(first, second)]
        for first, second in SKELETON_LINES
    ]
    episodes: list[dict[str, Any]] = []
    for episode_index, action_id in enumerate(EPISODES):
        key = f"{episode_index:02d}"
        source_time = np.asarray(
            trajectory["trajectory"][key]["pelvis"]["time_root_s"],
            dtype=float,
        )
        source_dt = float(np.median(np.diff(source_time)))
        stride = max(1, int(round(1.0 / (target_fps * source_dt))))
        source_indices = np.arange(0, len(source_time), stride, dtype=int)
        if source_indices[-1] != len(source_time) - 1:
            source_indices = np.r_[source_indices, len(source_time) - 1]
        frames: list[list[float]] = []
        for frame in source_indices:
            joints = joints_for_frame(
                trajectory, key, int(frame), model, config
            )
            frames.append([
                round(float(value), 4)
                for name in JOINT_NAMES
                for value in joints[name]
            ])
        description = descriptions[action_id]
        observability_note = ""
        if action_id in {"12_heel_raise_left", "13_heel_raise_right"}:
            observability_note = (
                "无足部 IMU：动画只显示可观测的小腿姿态，不声称重建脚跟或踝关节。"
            )
        episodes.append({
            "id": action_id,
            "instruction": description,
            "note": observability_note,
            "time": np.round(source_time[source_indices] - source_time[0], 3).tolist(),
            "sourceFrame": source_indices.tolist(),
            "frames": frames,
        })
    # A 6-axis trajectory has one arbitrary, drifting global yaw gauge.  The
    # initial-still pelvis frame is the fallback camera gauge; named anatomical
    # views follow the current-frame pelvis lateral axis in JavaScript.  This
    # rotates only the camera and never changes a trajectory sample or FK joint.
    initial = np.asarray(episodes[0]["frames"], dtype=float).reshape(
        -1, len(JOINT_NAMES), 3
    )
    hip_left = initial[:, joint_index["hip_left"], :2]
    hip_right = initial[:, joint_index["hip_right"], :2]
    body_right_rows = hip_right - hip_left
    norms = np.linalg.norm(body_right_rows, axis=1)
    valid = np.isfinite(body_right_rows).all(axis=1) & (norms > 1e-9)
    if not np.any(valid):
        raise RuntimeError("initial-still pelvis lateral axis is not observable")
    unit_right = body_right_rows[valid] / norms[valid, None]
    body_right = np.mean(unit_right, axis=0)
    body_right /= np.linalg.norm(body_right)
    output_coordinate_parity = -1.0 if coordinates_solidified else 1.0
    if forward_branch_yaw_deg not in {0, 180}:
        raise ValueError("forward branch yaw must be either 0 or 180 degrees")
    forward_branch_sign = 1.0 if forward_branch_yaw_deg == 0 else -1.0
    body_forward = output_coordinate_parity * forward_branch_sign * np.array(
        [-body_right[1], body_right[0]], dtype=float
    )
    # A frozen reflection changes Cartesian parity.  Recover the pre-reflection
    # camera lateral axis so the solidified pixels match the user-confirmed A/B
    # mirror result instead of silently cancelling it with a 180-degree camera
    # rotation.
    camera_body_right = output_coordinate_parity * body_right
    lateral_yaw = math.atan2(
        float(camera_body_right[1]), float(camera_body_right[0])
    )
    # Camera presets remain anchored to the pelvis lateral axis.  They must not
    # counter-rotate when the semantic sagittal branch is changed, otherwise a
    # requested 180-degree forward flip becomes invisible in the rendered
    # pixels.  The branch changes the green semantic arrow only.
    # In ``project`` the camera-to-subject depth axis is ``[sin(yaw),
    # cos(yaw)]``.  A front view therefore places the camera on the body's
    # forward side, which is ``yaw = -lateral_yaw`` for the frozen body frame.
    # The former assignments were reversed: the UI and exported MP4 said
    # "rear" while showing the subject from the front.
    front_yaw = -lateral_yaw
    rear_yaw = math.pi - lateral_yaw
    top_yaw = rear_yaw
    view_gauge = {
        "method": (
            "frame_local_pelvis_lateral_axis_for_named_views_with_"
            "initial_still_camera_fallback"
        ),
        "referenceEpisode": EPISODES[0],
        "bodyRightWorldXY": np.round(body_right, 8).tolist(),
        "bodyForwardWorldXY": np.round(body_forward, 8).tolist(),
        "forwardBranchYawDeg": forward_branch_yaw_deg,
        "forwardBranchSign": forward_branch_sign,
        "frontYawRad": float(front_yaw),
        "rearYawRad": float(rear_yaw),
        "rightSideYawRad": float(front_yaw - math.pi / 2.0),
        "topYawRad": float(top_yaw),
        "namedViewsFollowCurrentBodyFrame": True,
        "cameraCounterRotatedWithForwardBranch": False,
        "globalLateralGeometryMirror": bool(global_lateral_mirror),
        "outputCoordinatesSolidified": coordinates_solidified,
        "outputCoordinateParity": output_coordinate_parity,
        "globalMirrorPlane": {
            "normalWorldXY": np.round(body_right, 8).tolist(),
            "scope": "whole_avatar_once",
            "cameraAndGridReflected": False,
        },
        "trajectoryModified": False,
    }
    return {
        "schema": "biospur-c2-interactive-avatar-v1",
        "jointNames": list(JOINT_NAMES),
        "lines": lines,
        "episodes": episodes,
        "targetFps": target_fps,
        "viewGauge": view_gauge,
        "viewerRepair": False,
        "ik": False,
        "retarget": False,
    }


def _append_frozen_hxx(
    data: dict[str, Any],
    base_trajectory: dict[str, Any],
    hxx_run: Path,
    *,
    target_fps: float,
) -> dict[str, Any]:
    """Append Hxx display rows without allowing holdouts to alter C2 state."""

    report_path = hxx_run / "HXX_FROZEN_C2_REPLAY_REPORT.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("calibration_refit_on_hxx") is not False:
        raise RuntimeError("Hxx report does not prove frozen-calibration replay")
    if report.get("viewer_ik_rebase_retarget_or_repair") is not False:
        raise RuntimeError("Hxx report admits viewer repair")
    artifact = report["trajectory"]
    trajectory_path = ROOT / artifact["path"]
    if _sha256(trajectory_path) != artifact["sha256"]:
        raise RuntimeError("Hxx trajectory hash mismatch")
    instruction = {
        row["action"]: row["instruction_zh"] for row in report["source_records"]
    }
    hxx_trajectory: dict[str, Any] = {"trajectory": {}}
    with np.load(trajectory_path, allow_pickle=False) as archive:
        for action in ("H01_boxing", "H02_golf"):
            hxx_trajectory["trajectory"][action] = {}
            for segment in SEGMENTS:
                base = f"trajectory/{action}/{segment}"
                hxx_trajectory["trajectory"][action][segment] = {
                    "time_root_s": np.array(archive[f"{base}/time_root_s"]),
                    "quat_world_segment_wxyz": np.array(
                        archive[f"{base}/quat_world_segment_wxyz"]
                    ),
                    "mask": np.array(archive[f"{base}/mask"], dtype=bool),
                }
        matrix = np.array(
            archive["output_coordinates/matrix_world_output_from_internal"]
        )
        normal = np.array(
            archive["output_coordinates/plane_normal_world_internal"]
        )
    base_convention = base_trajectory.get("output_coordinate_convention")
    if base_convention is None:
        raise RuntimeError("C2 trajectory handedness is not solidified")
    if not np.allclose(
        matrix,
        base_convention["matrix_world_output_from_internal"],
        atol=1e-12,
    ):
        raise RuntimeError("Hxx output convention differs from frozen C2")
    hxx_trajectory["output_coordinate_convention"] = {
        "schema": "biospur-c2-capture-wide-output-coordinates-v1",
        "matrix_world_output_from_internal": matrix,
        "plane_normal_world_internal": normal,
    }
    config = load_effective_config()
    model = display_models(config)[1]
    for action in ("H01_boxing", "H02_golf"):
        source_time = np.asarray(
            hxx_trajectory["trajectory"][action]["pelvis"]["time_root_s"],
            dtype=float,
        )
        source_dt = float(np.median(np.diff(source_time)))
        stride = max(1, int(round(1.0 / (target_fps * source_dt))))
        source_indices = np.arange(0, len(source_time), stride, dtype=int)
        if source_indices[-1] != len(source_time) - 1:
            source_indices = np.r_[source_indices, len(source_time) - 1]
        frames = []
        for frame in source_indices:
            joints = joints_for_frame(
                hxx_trajectory, action, int(frame), model, config
            )
            frames.append([
                round(float(value), 4)
                for name in JOINT_NAMES
                for value in joints[name]
            ])
        data["episodes"].append({
            "id": action,
            "instruction": instruction[action],
            "note": (
                "封存留出回放：沿用前 19 个动作冻结的 C2 标定；"
                "本动作不参与拟合，不是第 20/21 个校准动作。"
            ),
            "time": np.round(
                source_time[source_indices] - source_time[0], 3
            ).tolist(),
            "sourceFrame": source_indices.tolist(),
            "frames": frames,
        })
    return {
        "report": str(report_path.relative_to(ROOT)),
        "report_sha256": _sha256(report_path),
        "trajectory": artifact,
        "actions": ["H01_boxing", "H02_golf"],
        "calibration_refit_on_hxx": False,
    }


def _html(data: dict[str, Any], source_report: str, source_hash: str) -> str:
    encoded = json.dumps(
        data,
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur C2 校准后 3D 动作回放</title>
<style>
:root{{color-scheme:light dark;font-family:"Noto Sans CJK SC","Noto Sans SC",system-ui,sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:#f7f8fa;color:#16191d;overflow:hidden}}
#app{{height:100vh;display:grid;grid-template-rows:auto 1fr auto;background:#f7f8fa}}
header{{padding:12px 16px 8px;border-bottom:1px solid #d8dde5;background:#fff}}
.top{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}
h1{{font-size:17px;margin:0 10px 0 0}}select,button,input{{font:inherit}}
select,button{{border:1px solid #bcc4ce;background:#fff;color:#16191d;padding:6px 9px;border-radius:5px}}
button{{cursor:pointer}}button[aria-pressed="true"]{{border:2px solid #2469a9;color:#145991;padding:5px 8px}}button[data-view-origin="true"]{{border:2px dashed #2469a9;color:#145991;padding:5px 8px}}
#viewStatus{{border:2px solid #2469a9;border-radius:5px;padding:5px 9px;color:#145991;font-weight:700;white-space:nowrap}}
#stage{{position:relative;min-height:0}}canvas{{display:block;width:100%;height:100%;touch-action:none;cursor:grab}}
canvas.dragging{{cursor:grabbing}}#overlay{{position:absolute;left:16px;top:12px;max-width:min(620px,70vw);pointer-events:none}}
#action{{font-size:18px;font-weight:700}}#instruction{{font-size:14px;line-height:1.45;margin-top:4px}}
#note{{font-size:13px;color:#9a5b00;margin-top:4px}}#viewState{{font-size:13px;color:#2b7a55;font-weight:700;margin-top:4px}}#state{{font-size:12px;color:#5f6975;margin-top:5px}}
footer{{padding:10px 16px 12px;border-top:1px solid #d8dde5;background:#fff;display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center}}
#scrub{{width:100%}}.legend{{font-size:12px;color:#5f6975;white-space:nowrap}}
@media (max-width:700px){{header{{padding:8px}}h1{{width:100%}}#overlay{{left:10px;top:8px;max-width:85vw}}footer{{grid-template-columns:auto 1fr;padding:8px}}.legend{{grid-column:1/-1;white-space:normal}}}}
@media (prefers-color-scheme:dark){{body,#app{{background:#111418;color:#edf1f5}}header,footer{{background:#171b20;border-color:#343b44}}select,button{{background:#20262d;color:#edf1f5;border-color:#4c5764}}#state,.legend{{color:#aeb8c3}}#note{{color:#ffc46b}}}}
</style>
<div id="app">
  <header>
    <div class="top">
      <h1>BioSpur C2 · 校准后 3D 动作回放</h1>
      <select id="episode" aria-label="动作"></select>
      <button id="play" type="button">播放</button>
      <select id="speed" aria-label="播放速度"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select>
      <span id="mirrorControls"><span>镜像对称：</span>
      <button class="mirror" data-mirror="false" type="button">原始画面</button>
      <button class="mirror" data-mirror="true" type="button">三维全局镜像</button></span>
      <span>相机：</span>
      <button class="view" data-view="front" type="button">从前方看</button>
      <button class="view" data-view="oblique" type="button">斜前方看</button>
      <button class="view" data-view="rear" type="button">从后方看</button>
      <button class="view" data-view="side" type="button">从右侧看</button>
      <button class="view" data-view="top" type="button">从上方看</button>
      <button class="view" data-view="three" type="button" aria-pressed="true">3D 相机</button>
      <span id="viewStatus">当前：固定3D</span>
    </div>
  </header>
  <div id="stage">
    <canvas id="canvas" aria-label="可旋转的三维火柴人动画"></canvas>
    <div id="overlay"><div id="action"></div><div id="instruction"></div><div id="note"></div><div id="viewState"></div><div id="state"></div></div>
  </div>
  <footer><span id="time">0.00 s</span><input id="scrub" type="range" min="0" max="1" step="1" value="0"><span class="legend">拖动旋转 · 滚轮缩放 · 同一校准轨迹 · 无 IK/repair/rebase</span></footer>
</div>
<script>
const DATA={encoded};
const SOURCE_REPORT={json.dumps(source_report)};
const SOURCE_SHA256={json.dumps(source_hash)};
const root=document.getElementById('app'),canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d');
const episodeSelect=document.getElementById('episode'),playButton=document.getElementById('play'),speedSelect=document.getElementById('speed'),scrub=document.getElementById('scrub');
const actionLabel=document.getElementById('action'),instruction=document.getElementById('instruction'),note=document.getElementById('note'),viewState=document.getElementById('viewState'),stateLabel=document.getElementById('state'),timeLabel=document.getElementById('time'),viewStatus=document.getElementById('viewStatus');
const VIEW=DATA.viewGauge;
if(VIEW.outputCoordinatesSolidified)document.getElementById('mirrorControls').textContent='手性：已固化（三维全局镜像）';
const VIEW_LABEL={{front:'从前方看',oblique:'斜前方看',rear:'从后方看',side:'从右侧看',top:'从上方看',three:'3D 相机'}};
let episodeIndex=0,frameIndex=0,playing=false,lastTick=0,accumulator=0,yaw=VIEW.frontYawRad,pitch=0.42,zoom=1,drag=null,viewMode='three',lastPresetView='three',globalMirror=VIEW.globalLateralGeometryMirror;
for(const [i,ep] of DATA.episodes.entries()){{const o=document.createElement('option');o.value=String(i);o.textContent=`${{String(i+1).padStart(2,'0')}} · ${{ep.id}}`;episodeSelect.appendChild(o)}}
function active(){{return DATA.episodes[episodeIndex]}}
function setEpisode(index){{episodeIndex=Number(index);frameIndex=0;accumulator=0;const ep=active();scrub.max=String(ep.frames.length-1);scrub.value='0';actionLabel.textContent=ep.id;instruction.textContent=ep.instruction;note.textContent=ep.note;draw()}}
function describeView(name){{const rows={{front:'相机从人体前方看。',oblique:'相机从人体右前方略向下看。',rear:'相机从人体后方看。',side:'相机从人体右侧看。',top:'相机从上方看；绿色 F 为全 Capture 前向。',three:'后上方 3D 相机。',manual:'自由旋转相机。'}},geometry=VIEW.outputCoordinatesSolidified?' · 输出手性已在全 Capture 固化。':(globalMirror?' · 几何模式：整副骨架通过固定竖直镜面反射一次。':' · 几何模式：原始未镜像骨架。');viewState.textContent=(rows[name]||rows.manual)+geometry}}
function updateViewButtons(){{for(const b of document.querySelectorAll('.view')){{b.setAttribute('aria-pressed',String(viewMode!=='manual'&&b.dataset.view===viewMode));b.setAttribute('data-view-origin',String(viewMode==='manual'&&b.dataset.view===lastPresetView))}}viewStatus.textContent=viewMode==='manual'?`当前：自由旋转（起点：${{VIEW_LABEL[lastPresetView]}}）`:`当前：${{VIEW_LABEL[viewMode]}}`}}
function updateMirrorButtons(){{for(const b of document.querySelectorAll('.mirror'))b.setAttribute('aria-pressed',String((b.dataset.mirror==='true')===globalMirror))}}
function setView(name){{viewMode=name;lastPresetView=name;updateViewButtons();updateMirrorButtons();describeView(name);draw()}}
function followBodyCamera(raw){{if(!['front','oblique','rear','side','top','three'].includes(viewMode))return;const ji=Object.fromEntries(DATA.jointNames.map((name,index)=>[name,index])),hl=raw[ji.hip_left],hr=raw[ji.hip_right],parity=VIEW.outputCoordinateParity??1,dx=parity*(hr[0]-hl[0]),dy=parity*(hr[1]-hl[1]);if(!Number.isFinite(dx)||!Number.isFinite(dy)||Math.hypot(dx,dy)<1e-9)return;const lateral=Math.atan2(dy,dx),front=-lateral,rear=Math.PI-lateral,top=front;if(viewMode==='front'){{yaw=front;pitch=0}}else if(viewMode==='oblique'){{yaw=front-Math.PI/4;pitch=.18}}else if(viewMode==='rear'){{yaw=rear;pitch=0}}else if(viewMode==='side'){{yaw=front-Math.PI/2;pitch=0}}else if(viewMode==='top'){{yaw=top;pitch=Math.PI/2}}else{{yaw=rear;pitch=0.42}}}}
function resize(){{const r=canvas.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);canvas.width=Math.max(1,Math.round(r.width*d));canvas.height=Math.max(1,Math.round(r.height*d));ctx.setTransform(d,0,0,d,0,0);draw()}}
function project(x,y,z,w,h){{const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch);const rx=cy*x-sy*y,ry=sy*x+cy*y;const rz=z,py=cp*ry-sp*rz,pz=sp*ry+cp*rz;const scale=Math.min(w,h)*0.43*zoom;return [w/2+rx*scale,h*0.72-pz*scale,py]}}
function colors(){{const dark=matchMedia('(prefers-color-scheme:dark)').matches;return dark?{{bg:'#111418',grid:'#2b323a',center:'#e8edf2',left:'#50a8f5',right:'#ff9b45',joint:'#d9e0e7'}}:{{bg:'#f7f8fa',grid:'#dce2e9',center:'#171717',left:'#1f77b4',right:'#ff7f0e',joint:'#20252b'}}}}
function draw(){{if(!DATA.episodes.length)return;const r=canvas.getBoundingClientRect(),w=r.width,h=r.height,c=colors(),ep=active(),flat=ep.frames[frameIndex],sourceRaw=[],pts=[];for(let i=0;i<DATA.jointNames.length;i++)sourceRaw.push([flat[3*i],flat[3*i+1],flat[3*i+2]]);followBodyCamera(sourceRaw);const mn=VIEW.globalMirrorPlane.normalWorldXY,mirrorPoint=p=>{{const dot=p[0]*mn[0]+p[1]*mn[1];return [p[0]-2*dot*mn[0],p[1]-2*dot*mn[1],p[2]]}},mirrorVector=v=>{{const dot=v[0]*mn[0]+v[1]*mn[1];return [v[0]-2*dot*mn[0],v[1]-2*dot*mn[1],v[2]]}},raw=globalMirror?sourceRaw.map(mirrorPoint):sourceRaw;ctx.clearRect(0,0,w,h);ctx.fillStyle=c.bg;ctx.fillRect(0,0,w,h);ctx.lineWidth=1;ctx.strokeStyle=c.grid;for(let i=-2;i<=2;i++){{const a=project(i*.35,-.7,0,w,h),b=project(i*.35,.7,0,w,h);ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke();const d=project(-.7,i*.35,0,w,h),e=project(.7,i*.35,0,w,h);ctx.beginPath();ctx.moveTo(d[0],d[1]);ctx.lineTo(e[0],e[1]);ctx.stroke()}}
 for(let i=0;i<raw.length;i++)pts.push(project(raw[i][0],raw[i][1],raw[i][2],w,h));
 const lines=DATA.lines.map(row=>[...row,(pts[row[0]][2]+pts[row[1]][2])/2]).sort((a,b)=>a[3]-b[3]);ctx.lineCap='round';ctx.lineJoin='round';for(const [a,b,side] of lines){{ctx.strokeStyle=c[side];ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(pts[a][0],pts[a][1]);ctx.lineTo(pts[b][0],pts[b][1]);ctx.stroke()}}ctx.fillStyle=c.joint;for(const p of pts){{ctx.beginPath();ctx.arc(p[0],p[1],3.2,0,Math.PI*2);ctx.fill()}}
 const ji=Object.fromEntries(DATA.jointNames.map((name,index)=>[name,index])),pelvis=raw[ji.pelvis_center],sourcePelvis=sourceRaw[ji.pelvis_center],sourceHl=sourceRaw[ji.hip_left],sourceHr=sourceRaw[ji.hip_right],dx=sourceHr[0]-sourceHl[0],dy=sourceHr[1]-sourceHl[1],dn=Math.max(Math.hypot(dx,dy),1e-9),fs=VIEW.forwardBranchSign,parity=VIEW.outputCoordinateParity??1,sourceForward=[parity*fs*-dy/dn,parity*fs*dx/dn,0],forward=globalMirror?mirrorVector(sourceForward):sourceForward,frontPoint=[pelvis[0]+.18*forward[0],pelvis[1]+.18*forward[1],pelvis[2]],pp=project(...pelvis,w,h),fp=project(...frontPoint,w,h);ctx.strokeStyle='#3ecf8e';ctx.fillStyle='#3ecf8e';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(pp[0],pp[1]);ctx.lineTo(fp[0],fp[1]);ctx.stroke();ctx.font='bold 13px sans-serif';ctx.fillText('身体前 F · 全Capture '+VIEW.forwardBranchYawDeg+'°分支',fp[0]+5,fp[1]-5);ctx.fillStyle=c.left;ctx.fillText('身体左 L',pts[ji.shoulder_left][0]+7,pts[ji.shoulder_left][1]-7);ctx.fillStyle=c.right;ctx.fillText('身体右 R',pts[ji.shoulder_right][0]+7,pts[ji.shoulder_right][1]-7);
 scrub.value=String(frameIndex);timeLabel.textContent=`${{ep.time[frameIndex].toFixed(2)}} s`;stateLabel.textContent=`源帧 ${{ep.sourceFrame[frameIndex]}} · ${{frameIndex+1}}/${{ep.frames.length}} · 轨迹 SHA ${{SOURCE_SHA256.slice(0,12)}}…`;
}}
function animate(t){{if(!lastTick)lastTick=t;const dt=(t-lastTick)/1000;lastTick=t;if(playing){{accumulator+=dt*Number(speedSelect.value);const ep=active();while(frameIndex+1<ep.frames.length&&accumulator>=ep.time[frameIndex+1]-ep.time[frameIndex]){{accumulator-=ep.time[frameIndex+1]-ep.time[frameIndex];frameIndex++}}if(frameIndex>=ep.frames.length-1){{frameIndex=0;accumulator=0}}draw()}}requestAnimationFrame(animate)}}
episodeSelect.addEventListener('change',()=>setEpisode(episodeSelect.value));playButton.addEventListener('click',()=>{{playing=!playing;playButton.textContent=playing?'暂停':'播放'}});scrub.addEventListener('input',()=>{{frameIndex=Number(scrub.value);accumulator=0;draw()}});for(const b of document.querySelectorAll('.view'))b.addEventListener('click',()=>setView(b.dataset.view));
for(const b of document.querySelectorAll('.mirror'))b.addEventListener('click',()=>{{if(VIEW.outputCoordinatesSolidified)return;globalMirror=b.dataset.mirror==='true';updateMirrorButtons();describeView(viewMode);draw()}});
canvas.addEventListener('pointerdown',e=>{{drag=[e.clientX,e.clientY,yaw,pitch];canvas.setPointerCapture(e.pointerId);canvas.classList.add('dragging')}});canvas.addEventListener('pointermove',e=>{{if(!drag)return;viewMode='manual';updateViewButtons();describeView('manual');yaw=drag[2]+(e.clientX-drag[0])*.009;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,drag[3]-(e.clientY-drag[1])*.009));draw()}});canvas.addEventListener('pointerup',()=>{{drag=null;canvas.classList.remove('dragging')}});canvas.addEventListener('wheel',e=>{{e.preventDefault();zoom=Math.max(.55,Math.min(2.4,zoom*Math.exp(-e.deltaY*.001)));draw()}},{{passive:false}});
const params=new URLSearchParams(location.search),requestedEpisode=Number(params.get('episode')??0),requestedFrame=Number(params.get('frame')??0),requestedView=params.get('view')??'three',requestedMirror=params.get('mirror'),allowedViews=new Set(['front','oblique','rear','side','top','three']);
const initialEpisode=Number.isFinite(requestedEpisode)?Math.max(0,Math.min(DATA.episodes.length-1,Math.trunc(requestedEpisode))):0;
if(!VIEW.outputCoordinatesSolidified){{if(requestedMirror==='on')globalMirror=true;else if(requestedMirror==='off')globalMirror=false}}else globalMirror=false;
episodeSelect.value=String(initialEpisode);setEpisode(initialEpisode);frameIndex=Number.isFinite(requestedFrame)?Math.max(0,Math.min(active().frames.length-1,Math.trunc(requestedFrame))):0;scrub.value=String(frameIndex);setView(allowedViews.has(requestedView)?requestedView:'three');
new ResizeObserver(resize).observe(document.getElementById('stage'));matchMedia('(prefers-color-scheme:dark)').addEventListener('change',draw);requestAnimationFrame(animate);
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=5.0)
    parser.add_argument("--output-dir-name", default="INTERACTIVE_3D")
    parser.add_argument(
        "--forward-branch-yaw-deg",
        type=int,
        choices=(0, 180),
        default=0,
        help="capture-wide semantic forward/view branch; never edits trajectory",
    )
    parser.add_argument(
        "--global-lateral-mirror",
        action="store_true",
        help="reflect the whole avatar once across a fixed vertical plane",
    )
    parser.add_argument(
        "--append-hxx-run",
        type=Path,
        help="append frozen-calibration H01/H02 after the 19 C2 episodes",
    )
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    if ROOT not in run_dir.parents:
        raise SystemExit("run directory must remain in canonical Fusion_Part")
    report_path = run_dir / "POSE_RESET_QMT_DIAGNOSTIC.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    trajectory_path = ROOT / report["trajectory"]["path"]
    if _sha256(trajectory_path) != report["trajectory"]["sha256"]:
        raise RuntimeError("source trajectory hash mismatch")
    trajectory = _load_trajectory(trajectory_path)
    data = _payload(
        trajectory,
        target_fps=float(args.fps),
        forward_branch_yaw_deg=int(args.forward_branch_yaw_deg),
        global_lateral_mirror=bool(args.global_lateral_mirror),
    )
    hxx_source = None
    if args.append_hxx_run is not None:
        hxx_run = args.append_hxx_run.resolve()
        if ROOT not in hxx_run.parents:
            raise SystemExit("Hxx run must remain in canonical Fusion_Part")
        hxx_source = _append_frozen_hxx(
            data,
            trajectory,
            hxx_run,
            target_fps=float(args.fps),
        )
    output_dir_name = str(args.output_dir_name)
    if Path(output_dir_name).name != output_dir_name or output_dir_name in {"", ".", ".."}:
        raise SystemExit("output directory name must be one local path component")
    output_dir = run_dir / output_dir_name
    output_dir.mkdir(exist_ok=False)
    output = output_dir / "c2_avatar_3d.html"
    output.write_text(
        _html(
            data,
            str(report_path.relative_to(ROOT)),
            report["trajectory"]["sha256"],
        ),
        encoding="utf-8",
    )
    manifest = {
        "schema": "biospur-c2-interactive-avatar-manifest-v1",
        "html": str(output.relative_to(ROOT)),
        "html_sha256": _sha256(output),
        "source_report": str(report_path.relative_to(ROOT)),
        "source_report_sha256": _sha256(report_path),
        "source_trajectory": report["trajectory"],
        "episode_count": len(data["episodes"]),
        "target_fps": data["targetFps"],
        "viewer_ik_rebase_retarget_or_repair": False,
        "joint_geometry_source": "renderer.joints_for_frame",
        "view_gauge": data["viewGauge"],
        "hxx_source": hxx_source,
    }
    manifest_path = output_dir / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "html": str(output),
        "html_bytes": output.stat().st_size,
        "manifest": str(manifest_path),
        "episode_count": len(data["episodes"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
