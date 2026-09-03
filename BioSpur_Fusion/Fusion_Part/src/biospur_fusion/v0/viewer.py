"""Self-contained interactive body-relative V0 skeleton viewer."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


POINT_NAMES = (
    "pelvis", "torso", "upper_arm_left", "forearm_left", "wrist_left",
    "upper_arm_right", "forearm_right", "wrist_right", "thigh_left",
    "shank_left", "ankle_left", "thigh_right", "shank_right", "ankle_right", "head_proxy",
)
EDGES = (
    ("pelvis", "torso"), ("torso", "head_proxy"),
    ("torso", "upper_arm_left"), ("upper_arm_left", "forearm_left"), ("forearm_left", "wrist_left"),
    ("torso", "upper_arm_right"), ("upper_arm_right", "forearm_right"), ("forearm_right", "wrist_right"),
    ("pelvis", "thigh_left"), ("thigh_left", "shank_left"), ("shank_left", "ankle_left"),
    ("pelvis", "thigh_right"), ("thigh_right", "shank_right"), ("shank_right", "ankle_right"),
)


def skeleton_points(segment_names: tuple[str, ...], positions: np.ndarray, rotations: np.ndarray) -> np.ndarray:
    index = {name: i for i, name in enumerate(segment_names)}
    output = np.empty((len(positions), len(POINT_NAMES), 3))
    for p, name in enumerate(POINT_NAMES):
        if name in index:
            output[:, p] = positions[:, index[name]]
            continue
        if name == "wrist_left": segment, local = "forearm_left", np.array([0.0, 0.0, -0.26])
        elif name == "wrist_right": segment, local = "forearm_right", np.array([0.0, 0.0, -0.26])
        elif name == "ankle_left": segment, local = "shank_left", np.array([0.0, 0.0, -0.42])
        elif name == "ankle_right": segment, local = "shank_right", np.array([0.0, 0.0, -0.42])
        else: segment, local = "torso", np.array([0.0, 0.0, 0.34])
        s = index[segment]
        output[:, p] = positions[:, s] + np.einsum("nij,j->ni", rotations[:, s], local)
    return output


HTML = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur Fusion V0 — IMU-only body-relative replay</title>
<style>
:root{color-scheme:dark;--bg:#071018;--panel:#0d1a25;--line:#2b4355;--text:#eef6fb;--muted:#9ab0bf;--cyan:#43d9d0;--amber:#ffbf5b;--green:#7ddc91;--red:#ff6f72}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui}.shell{max-width:1280px;margin:auto;padding:18px}.header,.controls,.grid{display:flex;gap:14px;align-items:center}.header{justify-content:space-between;margin-bottom:12px}.kicker{color:var(--cyan);font-weight:700;letter-spacing:.08em;text-transform:uppercase}h1{font-size:24px;margin:4px 0}.sub{color:var(--muted)}.badge{padding:10px 12px;border:1px solid var(--amber);color:var(--amber);border-radius:8px;max-width:430px}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px}.controls{padding:10px;margin-bottom:12px;flex-wrap:wrap}.controls label{color:var(--muted)}button,select,input{accent-color:var(--cyan);background:#122332;color:var(--text);border:1px solid var(--line);border-radius:5px;padding:6px}.timeline{flex:1;min-width:260px}.timeline input{width:100%}.grid{align-items:stretch}.scene{flex:1;min-width:0;position:relative}.scene canvas{width:100%;height:650px;display:block}.status{width:330px;padding:14px}.status h2{margin-top:0}.status dl{display:grid;grid-template-columns:115px 1fr;gap:8px}.status dt{color:var(--muted)}.status dd{margin:0;word-break:break-word}.bar{height:8px;background:#182c3b;border-radius:9px;overflow:hidden}.bar i{display:block;height:100%;background:var(--green)}.legend{margin-top:18px;color:var(--muted);line-height:1.6}.watermark{position:absolute;left:14px;bottom:12px;color:var(--red);font-weight:700;background:#071018cc;padding:6px 8px;border-radius:5px}@media(max-width:850px){.grid{display:block}.status{width:auto;margin-top:12px}.scene canvas{height:480px}.header{display:block}.badge{margin-top:10px}}
</style></head><body><main class="shell">
<header class="header"><div><div class="kicker">BioSpur Fusion V0</div><h1>Ten-node IMU-only body-relative replay</h1><div class="sub" id="pipeline"></div></div><div class="badge">Root position is a fixed display gauge. Global yaw is not north and may drift. Geometry is display-only, not qualified metric anthropometry.</div></header>
<section class="panel controls"><button id="play">Play</button><label>Speed <select id="speed"><option value="1">1×</option><option value="4" selected>4×</option><option value="10">10×</option></select></label><label>Window <select id="window"></select></label><button id="front">Front</button><button id="side">Side</button><button id="top">Top</button><label>Camera yaw <input id="yaw" type="range" min="-180" max="180" value="-35"></label><label>Elevation <input id="pitch" type="range" min="-80" max="80" value="18"></label><label class="timeline">Native timeline <input id="time" type="range" min="0" max="10000" value="0"></label></section>
<section class="grid"><div class="panel scene"><canvas id="canvas"></canvas><div class="watermark">IMU ONLY · BODY RELATIVE · ROOT DISPLAY GAUGE FIXED</div></div><aside class="panel status"><h2 id="action"></h2><dl><dt>Capture</dt><dd id="captureId"></dd><dt>Profile</dt><dd id="profileId"></dd><dt>Action role</dt><dd id="actionRole"></dd><dt>Locked state</dt><dd id="lockedState"></dd><dt>Shared IK</dt><dd id="sharedIk"></dd><dt>global_time_ns</dt><dd id="timestamp"></dd><dt>Frame</dt><dd id="frame"></dd><dt>Boundary</dt><dd id="boundary"></dd><dt>qmt state</dt><dd id="qmtState"></dd><dt>qmt applied</dt><dd id="qmtApplied"></dd><dt>Mean confidence</dt><dd><span id="confidence"></span><div class="bar"><i id="confidenceBar"></i></div></dd><dt>Worst segment</dt><dd id="worst"></dd><dt>Worst node</dt><dd id="worstNode"></dd><dt>Max uncertainty</dt><dd id="uncertainty"></dd><dt>Max joint angle</dt><dd id="joint"></dd><dt>Root mode</dt><dd>DISPLAY_GAUGE_FIXED</dd><dt>Global yaw</dt><dd>UNOBSERVABLE / MAY DRIFT</dd><dt>Geometry</dt><dd id="geometryStatus">DISPLAY ONLY / NON-METRIC</dd><dt>Skin slip</dt><dd>ROBUST_OR_UNMODELLED</dd></dl><div class="legend">Use playback, timeline, window selection, and camera presets/sliders to inspect the 3D structure. L/R labels remain fixed to body side. Lines are canonical-FK display geometry. Confidence and uncertainty are internal estimator health, not external accuracy.</div></aside></section>
</main><script>
const D=__DATA__,E=__EDGES__,P=__POINTS__,S=D.segment_names,$=x=>document.getElementById(x);let playing=false,last=0;
const canvas=$('canvas'),ctx=canvas.getContext('2d'),slider=$('time'),windowSel=$('window');$('pipeline').textContent=D.pipeline_label;$('captureId').textContent=D.viewer_metadata.capture_id||'UNSPECIFIED';$('profileId').textContent=(D.viewer_metadata.profile_id||'UNSPECIFIED')+' / '+(D.viewer_metadata.profile_sha256||'').slice(0,16);$('actionRole').textContent=D.viewer_metadata.action_role||'UNSPECIFIED';$('lockedState').textContent=D.viewer_metadata.locked_state_source||'UNSPECIFIED';$('sharedIk').textContent=D.viewer_metadata.shared_ik_mode||'UNSPECIFIED';$('geometryStatus').textContent=D.viewer_metadata.display_geometry_status||'DISPLAY ONLY / NON-METRIC';
const windows=[...new Set(D.window)];windows.forEach(w=>{const o=document.createElement('option');o.textContent=w;o.value=w;windowSel.appendChild(o)});
function indices(){let a=[];for(let i=0;i<D.window.length;i++)if(windowSel.value==='ALL'||D.window[i]===windowSel.value)a.push(i);return a}
{const o=document.createElement('option');o.textContent='ALL';o.value='ALL';windowSel.insertBefore(o,windowSel.firstChild);windowSel.value='ALL'}
function resize(){const d=Math.min(devicePixelRatio||1,2),r=canvas.getBoundingClientRect();canvas.width=Math.round(r.width*d);canvas.height=Math.round(r.height*d);ctx.setTransform(d,0,0,d,0,0)}
function project(point,w,h){const ya=+$('yaw').value*Math.PI/180,pi=+$('pitch').value*Math.PI/180,c=Math.cos(ya),s=Math.sin(ya),cp=Math.cos(pi),sp=Math.sin(pi);let x=c*point[0]-s*point[1],y=s*point[0]+c*point[1],z=point[2];let yy=cp*z-sp*y,depth=cp*y+sp*z;const k=Math.min(w,h)*.38;return [w/2+k*x,h*.58-k*yy,depth]}
function render(){resize();const list=indices(),u=+slider.value/10000,idx=list[Math.min(list.length-1,Math.round(u*(list.length-1)))],r=canvas.getBoundingClientRect(),w=r.width,h=r.height;ctx.clearRect(0,0,w,h);const pts=D.points[idx],proj=pts.map(p=>project(p,w,h));ctx.lineWidth=5;E.forEach(e=>{const a=proj[e[0]],b=proj[e[1]];ctx.strokeStyle='#63a9ff';ctx.beginPath();ctx.moveTo(a[0],a[1]);ctx.lineTo(b[0],b[1]);ctx.stroke()});const order=proj.map((p,i)=>[p[2],i]).sort((a,b)=>a[0]-b[0]);order.forEach(([,i])=>{ctx.fillStyle=i===0?'#43d9d0':'#ffbf5b';ctx.beginPath();ctx.arc(proj[i][0],proj[i][1],i===0?8:6,0,Math.PI*2);ctx.fill()});ctx.font='bold 16px system-ui';ctx.textAlign='center';[['L',P.indexOf('wrist_left')],['R',P.indexOf('wrist_right')],['L',P.indexOf('ankle_left')],['R',P.indexOf('ankle_right')]].forEach(([label,i])=>{ctx.fillStyle=label==='L'?'#43d9d0':'#ffbf5b';ctx.fillText(label,proj[i][0],proj[i][1]-10)});$('action').textContent=D.window[idx];$('timestamp').textContent=D.time_ns[idx];$('frame').textContent=(idx+1)+' / '+D.time_ns.length;$('boundary').textContent=D.boundary[idx];$('qmtState').textContent=D.qmt_state[idx];$('qmtApplied').textContent=D.qmt_applied_correction_deg[idx].toFixed(3)+'°';const c=D.mean_confidence[idx];$('confidence').textContent=(100*c).toFixed(1)+'%';$('confidenceBar').style.width=(100*c).toFixed(1)+'%';let wi=0;D.confidence[idx].forEach((v,i)=>{if(v<D.confidence[idx][wi])wi=i});$('worst').textContent=S[wi]+' ('+(100*D.confidence[idx][wi]).toFixed(1)+'%)';let ui=0;D.uncertainty_deg[idx].forEach((v,i)=>{if(v>D.uncertainty_deg[idx][ui])ui=i});$('worstNode').textContent=D.node_by_segment[ui];$('uncertainty').textContent=D.uncertainty_deg[idx][ui].toFixed(1)+'° ('+S[ui]+')';$('joint').textContent=D.max_joint_deg[idx].toFixed(1)+'°'}
function tick(ts){if(!playing)return;if(!last)last=ts;let v=+slider.value+(ts-last)/1000*10000/(Math.max(1,D.duration_s)/+$('speed').value);last=ts;if(v>=10000){v=10000;playing=false;$('play').textContent='Play'}slider.value=v;render();if(playing)requestAnimationFrame(tick)}
$('play').onclick=()=>{playing=!playing;$('play').textContent=playing?'Pause':'Play';last=0;if(playing){if(+slider.value>=10000)slider.value=0;requestAnimationFrame(tick)}};$('front').onclick=()=>{$('yaw').value=0;$('pitch').value=0;render()};$('side').onclick=()=>{$('yaw').value=90;$('pitch').value=0;render()};$('top').onclick=()=>{$('yaw').value=0;$('pitch').value=80;render()};[slider,windowSel,$('yaw'),$('pitch')].forEach(x=>x.oninput=render);new ResizeObserver(render).observe(canvas);render();
</script></body></html>'''


def write_viewer(
    path: Path,
    *,
    time_ns: np.ndarray,
    window: np.ndarray,
    boundary: np.ndarray,
    segment_names: tuple[str, ...],
    segment_position: np.ndarray,
    segment_rotation: np.ndarray,
    segment_confidence: np.ndarray,
    joint_rotvec: np.ndarray,
    segment_sigma_rad: np.ndarray | None = None,
    node_by_segment: tuple[str, ...] | None = None,
    qmt_mode: str = "UNSPECIFIED_LEGACY",
    qmt_state: np.ndarray | None = None,
    qmt_applied_correction_deg: np.ndarray | None = None,
    viewer_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    points = skeleton_points(segment_names, segment_position, segment_rotation)
    # The estimator output is already a 10 Hz replay grid. Round display-only
    # JSON without changing the machine-readable NPZ state.
    count = len(time_ns)
    uncertainty_available = segment_sigma_rad is not None
    if segment_sigma_rad is None:
        segment_sigma_rad = np.zeros_like(segment_confidence)
    if node_by_segment is None:
        node_by_segment = tuple("UNMAPPED" for _ in segment_names)
    if qmt_state is None:
        state_label = "BYPASS_QMT_OFF" if qmt_mode == "QMT_OFF" else qmt_mode
        qmt_state = np.full(count, state_label, dtype="U32")
    if qmt_applied_correction_deg is None:
        qmt_applied_correction_deg = np.zeros(count, float)
    default_pipeline_label = (
        "VQF attitude/bias · qmt disabled (exact pass-through) · shared full-SO(3) IK · canonical FK"
        if qmt_mode == "QMT_OFF" else
        "VQF attitude/bias · always-on qmt diagnostic · shared full-SO(3) IK · canonical FK"
    )
    pipeline_label = str((viewer_metadata or {}).get(
        "pipeline_label", default_pipeline_label,
    ))
    payload = {
        "time_ns": [str(int(x)) for x in time_ns],
        "window": [str(x) for x in window],
        "boundary": [str(x) for x in boundary],
        "segment_names": list(segment_names),
        "points": np.round(points, 5).tolist(),
        "confidence": np.round(segment_confidence, 5).tolist(),
        "mean_confidence": np.round(np.mean(segment_confidence, axis=1), 5).tolist(),
        "uncertainty_deg": np.round(np.degrees(segment_sigma_rad), 4).tolist(),
        "node_by_segment": list(node_by_segment),
        "qmt_mode": qmt_mode,
        "qmt_state": [str(value) for value in qmt_state],
        "qmt_applied_correction_deg": np.round(qmt_applied_correction_deg, 4).tolist(),
        "pipeline_label": pipeline_label,
        "max_joint_deg": np.round(np.degrees(np.max(np.linalg.norm(joint_rotvec, axis=2), axis=1)), 4).tolist(),
        "duration_s": float(sum((int(time_ns[i]) - int(time_ns[i - 1])) * 1e-9 for i in range(1, len(time_ns)) if window[i] == window[i - 1])),
        "viewer_metadata": dict(viewer_metadata or {}),
    }
    point_index = {name: index for index, name in enumerate(POINT_NAMES)}
    edges = [[point_index[left], point_index[right]] for left, right in EDGES]
    html = HTML.replace("__DATA__", json.dumps(payload, separators=(",", ":"), allow_nan=False))
    html = html.replace("__EDGES__", json.dumps(edges)).replace("__POINTS__", json.dumps(POINT_NAMES))
    Path(path).write_text(html, encoding="utf-8")
    return {
        "schema": "biospur-fusion-v0-viewer-v1",
        "path": str(Path(path).resolve()),
        "frames": int(len(time_ns)),
        "interactive_3d_projection": True,
        "native_timestamps_displayed": True,
        "health_confidence_displayed": True,
        "per_node_uncertainty_displayed": uncertainty_available,
        "qmt_correction_or_bypass_state_displayed": True,
        "qmt_mode": qmt_mode,
        "viewer_metadata": dict(viewer_metadata or {}),
        "left_right_labels_displayed": True,
        "root_gauge_labelled": True,
        "display_only_geometry_labelled": True,
        "camera_presets": ["front", "side", "top"],
        "uwb_spatial_data_displayed": False,
    }
