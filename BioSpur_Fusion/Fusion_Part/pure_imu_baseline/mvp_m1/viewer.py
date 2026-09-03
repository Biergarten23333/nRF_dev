"""Offline three-capture MVP viewer export."""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from pure_imu_baseline.config import GEOMETRY
from pure_imu_baseline.stage2.config import BONES, SEGMENT_ORIGIN_JOINT


ARRAY_KEYS = ("time_s", "q_GB_wxyz", "working_q_GB_wxyz", "valid", "filter_reset",
              "joint_positions_m", "joint_available", "epoch_per_node", "quality_state",
              "sample_age_s", "time_since_last_valid_s", "time_since_last_reset_s",
              "last_reset_reason", "recenter_gamma_candidate_rad", "pelvis_forward_horizontal_norm")


def array_sha(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def pack(arrays: dict[str, np.ndarray]) -> tuple[bytes, dict]:
    payload = bytearray(); schema = {}
    for name in ARRAY_KEYS:
        value = np.ascontiguousarray(arrays[name])
        padding = (-len(payload)) % max(1, value.dtype.itemsize)
        payload.extend(b"\0"*padding); offset = len(payload); encoded = value.tobytes(); payload.extend(encoded)
        schema[name] = {"dtype": value.dtype.str, "shape": list(value.shape), "offset": offset,
                        "nbytes": len(encoded), "sha256": array_sha(value)}
    return bytes(payload), schema


def write_chunks(path: Path, compressed: bytes) -> None:
    encoded = base64.b64encode(compressed).decode("ascii")
    chunks = [encoded[start:start+1_048_576] for start in range(0, len(encoded), 1_048_576)]
    path.write_text("window.BioSpurPayloadChunks=[\n" + "\n".join(json.dumps(chunk)+"," for chunk in chunks) + "\n];\n", encoding="ascii")


def edges(joint_names: list[str]) -> list[dict]:
    index = {name: position for position, name in enumerate(joint_names)}
    return [{"name": name, "a": index[a], "b": index[b], "class": category} for name, a, b, category in BONES]


def gap_events(raw: dict) -> list[dict]:
    t = raw["time_s"]; valid = raw["valid"]; reset = raw["filter_reset"]
    names = [str(value) for value in raw["segment_names"]]; events = []
    for frame, node in np.argwhere(reset):
        events.append({"time_s": float(t[frame]), "label": f"{names[node]} reset"})
    for node, name in enumerate(names):
        starts = np.flatnonzero((~valid[:, node]) & np.r_[True, valid[:-1, node]])
        for start in starts:
            events.append({"time_s": float(t[start]), "label": f"{name} unavailable"})
    events.sort(key=lambda item: (item["time_s"], item["label"]))
    return events


def page(capture: str, meta: dict) -> str:
    node_options = "".join(f'<option value="{i}">{node} · {segment}</option>' for i, (node, segment) in enumerate(zip(meta["node_ids"], meta["segment_names"])))
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur Pure-IMU MVP — Capture {capture}</title><link rel="stylesheet" href="C123_MVP_VIEWER.css"></head><body>
<main id="biospur-viewer"><header><h1>BioSpur Pure-IMU MVP — Capture {capture}</h1>
<div class="truth-strip"><strong>PURE IMU</strong><strong>ROOT FIXED</strong><strong>RAW AUTHORITATIVE</strong><strong>GLOBAL YAW MAY DRIFT</strong><strong>MANUAL RECENTER CHANGES GAUGE ONLY</strong></div>
<div id="load-status" role="status">Loading immutable raw evidence…</div><div id="gauge-event" role="status">Gauge: RAW · epoch 0</div></header>
<section class="toolbar" aria-label="Playback and gauge controls">
<label>Capture <select id="capture-select"><option value="1">Capture 1</option><option value="2">Capture 2</option><option value="3">Capture 3</option></select></label>
<label>Mode <select id="comparison-mode"><option>RAW</option><option>RECENTERED</option><option>OVERLAY</option></select></label>
<label>Node <select id="node-detail">{node_options}</select></label>
<button id="play-pause" type="button">Play</button><button id="step-back" type="button">−1 frame</button><button id="step-forward" type="button">+1 frame</button>
<label>Speed <select id="speed-select"><option value="0.25">0.25×</option><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select></label>
<button id="recenter-yaw" type="button">Set current pelvis facing as display forward</button><button id="clear-recenter" type="button">Clear recenter · Return to raw gauge</button></section>
<section class="toolbar"><label>Camera <select id="camera-mode"><option>FREE_ORBIT</option><option>WORLD_FIXED_FRONT</option><option>WORLD_FIXED_SIDE</option><option>WORLD_FIXED_TOP</option><option>WORLD_FIXED_OBLIQUE</option><option>PELVIS_FRONT_LOCKED</option><option>PELVIS_SIDE_LOCKED</option></select></label><button id="reset-camera" type="button">Reset camera</button></section>
<section class="timeline"><input id="timeline" type="range" min="0" max="1" step="1" value="0"><output id="time-output">0.000 s</output><label>Timestamp (s) <input id="timestamp-input" type="number" min="0" step="0.001" value="0"></label><button id="jump-time" type="button">Jump</button><label>Gap/reset marker <select id="event-select"></select></label><button id="jump-event" type="button">Jump to marker</button></section>
<section class="toggles"><label><input id="toggle-validity" type="checkbox" checked> Validity overlay</label><label><input id="toggle-joints" type="checkbox"> Joint labels</label><label><input id="toggle-segments" type="checkbox"> Segment names</label><label><input id="toggle-sensors" type="checkbox"> Node labels</label><label><input id="toggle-global-axes" type="checkbox" checked> Global axes</label><label><input id="toggle-body-axes" type="checkbox" checked> Pelvis axes</label><label><input id="toggle-segment-frames" type="checkbox"> Segment frames</label><label><input id="toggle-ground" type="checkbox" checked> Ground</label><label><input id="toggle-ghost" type="checkbox"> Initial raw-pose ghost</label></section>
<section class="viewport-wrap"><canvas id="viewport" aria-label="Interactive raw-authoritative fixed-root Pure-IMU skeleton"></canvas><div id="view-readout"></div></section>
<footer>Manual recenter is an operator-requested global gauge change, not drift correction or heading truth.</footer></main>
<script>window.BioSpurViewerMeta={json.dumps(meta,separators=(',',':'),allow_nan=False)};</script><script src="CAPTURE{capture}_MVP_VIEWER_DATA.js"></script><script src="C123_MVP_VIEWER_CORE.js"></script></body></html>'''


def patched_core(stage2_core: Path) -> str:
    core = stage2_core.read_text(encoding="utf-8")
    core = core.replace('const eventSelect = document.getElementById("event-select");', 'const eventSelect = document.getElementById("event-select");\n  const comparisonMode = document.getElementById("comparison-mode");\n  const nodeDetail = document.getElementById("node-detail");\n  const gaugeEvent = document.getElementById("gauge-event");')
    core = core.replace('const toggleIds = ["joints",', 'const toggleIds = ["validity", "joints",')
    core = core.replace('drag: null,\n  };', 'drag: null,\n    gaugeGamma: 0, gaugeEpoch: 0, gaugeSource: "RAW", gaugeEvents: [],\n  };')
    core = core.replace('{"<f8": Float64Array, "<f4": Float32Array, "|u1": Uint8Array}', '{"<f8": Float64Array, "<f4": Float32Array, "|u1": Uint8Array, "<u4": Uint32Array}')
    marker = '''  function quatRotate(q, v) {
    const qv = [q[1], q[2], q[3]];
    const uv = cross(qv, v);
    const uuv = cross(qv, uv);
    return add(v, mul(add(mul(uv, q[0]), uuv), 2));
  }
'''
    injection = marker + '''
  function quatMultiply(a, b) {
    return [a[0]*b[0]-a[1]*b[1]-a[2]*b[2]-a[3]*b[3], a[0]*b[1]+a[1]*b[0]+a[2]*b[3]-a[3]*b[2], a[0]*b[2]-a[1]*b[3]+a[2]*b[0]+a[3]*b[1], a[0]*b[3]+a[1]*b[2]-a[2]*b[1]+a[3]*b[0]];
  }
  function qz(angle) { return [Math.cos(angle/2), 0, 0, Math.sin(angle/2)]; }
  function gaugeQuaternion(q) { return quatMultiply(qz(state.gaugeGamma), q); }
'''
    if marker not in core: raise RuntimeError("quaternion injection marker missing")
    core = core.replace(marker, injection)
    old_q = '''  function qAt(frame, segment) {
    const offset = (frame * segmentCount + segment) * 4;
    return [arrays.q_GB_wxyz[offset], arrays.q_GB_wxyz[offset+1], arrays.q_GB_wxyz[offset+2], arrays.q_GB_wxyz[offset+3]];
  }

  function pointAt(frame, joint) {
    const offset = (frame * jointCount + joint) * 3;
    return [arrays.joint_positions_m[offset], arrays.joint_positions_m[offset+1], arrays.joint_positions_m[offset+2]];
  }
'''
    new_q = '''  function rawQAt(frame, segment) { const o=(frame*segmentCount+segment)*4; return [arrays.q_GB_wxyz[o],arrays.q_GB_wxyz[o+1],arrays.q_GB_wxyz[o+2],arrays.q_GB_wxyz[o+3]]; }
  function workQAt(frame, segment) { const o=(frame*segmentCount+segment)*4; return [arrays.working_q_GB_wxyz[o],arrays.working_q_GB_wxyz[o+1],arrays.working_q_GB_wxyz[o+2],arrays.working_q_GB_wxyz[o+3]]; }
  function qAt(frame, segment) { return comparisonMode.value === "RAW" ? rawQAt(frame, segment) : gaugeQuaternion(workQAt(frame, segment)); }
  function rawPointAt(frame, joint) { const o=(frame*jointCount+joint)*3; return [arrays.joint_positions_m[o],arrays.joint_positions_m[o+1],arrays.joint_positions_m[o+2]]; }
  function pointAt(frame, joint) { const p=rawPointAt(frame,joint); if (comparisonMode.value === "RAW") return p; const root=rawPointAt(frame,0); const x=p[0]-root[0], y=p[1]-root[1], c=Math.cos(state.gaugeGamma), s=Math.sin(state.gaugeGamma); return [root[0]+c*x-s*y,root[1]+s*x+c*y,p[2]]; }
'''
    if old_q not in core: raise RuntimeError("pose access marker missing")
    core = core.replace(old_q, new_q)
    # Pelvis-locked cameras follow the un-gauged working pose. An operator
    # recenter therefore changes pose only; it cannot silently rotate camera.
    core = core.replace('const q = qAt(frame, pelvis);', 'const q = workQAt(frame, pelvis);')
    ghost_old = '''  function drawGhost(project) {
    const frame = meta.ghost_frame;
    meta.edges.forEach(edge => {
      if (!jointValid(frame, edge.a) || !jointValid(frame, edge.b)) return;
      line(project, pointAt(frame, edge.a), pointAt(frame, edge.b), "#64748b", 3, 0.32);
    });
  }
'''
    ghost_new = '''  function drawGhost(project) {
    const frame = meta.ghost_frame;
    meta.edges.forEach(edge => {
      if (!jointValid(frame, edge.a) || !jointValid(frame, edge.b)) return;
      line(project, rawPointAt(frame, edge.a), rawPointAt(frame, edge.b), "#64748b", 3, 0.32);
    });
  }
'''
    if ghost_old not in core: raise RuntimeError("raw ghost marker missing")
    core = core.replace(ghost_old, ghost_new)
    core = core.replace('else if (state.mode === "WORLD_FIXED_TOP") { direction = [0, 0, 1]; upReference = [1, 0, 0]; }', 'else if (state.mode === "WORLD_FIXED_TOP") { direction = [0, 0, 1]; upReference = [1, 0, 0]; }\n    else if (state.mode === "WORLD_FIXED_OBLIQUE") direction = [1, -1, 0.7];')
    draw_marker = '    if (toggles["global-axes"].checked) axisFrame(project, [0, 0, 0], [1, 0, 0, 0], 0.20);\n\n'
    draw_overlay = draw_marker + '''    if (comparisonMode.value === "OVERLAY") {
      meta.edges.forEach(edge => { if (jointValid(frame,edge.a)&&jointValid(frame,edge.b)) line(project,rawPointAt(frame,edge.a),rawPointAt(frame,edge.b),"#94a3b8",3,0.42); });
    }

'''
    if draw_marker not in core: raise RuntimeError("draw marker missing")
    core = core.replace(draw_marker, draw_overlay)
    old_readout = '    const commonYaw = arrays.common_body_yaw_deg[frame];\n    const spread = arrays.inter_segment_heading_spread_deg[frame];\n    readout.textContent = `frame ${frame.toLocaleString()} / ${(meta.frames-1).toLocaleString()} · ${time.toFixed(3)} s · valid ${validSegments}/10 · common yaw ${Number.isFinite(commonYaw) ? commonYaw.toFixed(1) : "n/a"}° · inter-segment spread ${Number.isFinite(spread) ? spread.toFixed(1) : "n/a"}°${resetNodes.length ? ` · RESET ${resetNodes.join(", ")}` : ""}`;'
    new_readout = '''    const selected=Number(nodeDetail.value), so=frame*segmentCount+selected;
    const qualityNames=["VALID","RECENTLY_RESET","STALE","UNAVAILABLE"], resetNames=["NONE","FILTER_RESET"];
    const age=arrays.time_since_last_valid_s[so], resetAge=arrays.time_since_last_reset_s[so];
    const unavailable=Array.from({length:segmentCount},(_,i)=>arrays.quality_state[frame*segmentCount+i]===3?meta.segment_names[i]:null).filter(Boolean);
    if(toggles.validity.checked&&unavailable.length){ctx.save();ctx.fillStyle="#fca5a5";ctx.font="bold 13px system-ui,sans-serif";ctx.fillText(`UNAVAILABLE — ${unavailable.join(", ")}`,12,22);ctx.restore();}
    readout.textContent=`frame ${frame.toLocaleString()} / ${(meta.frames-1).toLocaleString()} · ${time.toFixed(3)} s · mode ${comparisonMode.value} · valid ${validSegments}/10 · gauge ${state.gaugeGamma.toFixed(6)} rad · epoch ${state.gaugeEpoch} · ${state.gaugeSource} · ${meta.node_ids[selected]} ${qualityNames[arrays.quality_state[so]]} · sample age ${Number.isFinite(age)?age.toFixed(3)+" s":"n/a"} · node epoch ${arrays.epoch_per_node[so]} · last reset ${resetNames[arrays.last_reset_reason[so]]} ${Number.isFinite(resetAge)?resetAge.toFixed(3)+" s ago":"n/a"}${resetNodes.length?` · RESET ${resetNodes.join(", ")}`:""}`;'''
    if old_readout not in core: raise RuntimeError("readout marker missing")
    core = core.replace(old_readout, new_readout)
    core = core.replace('window.location.href = `CAPTURE${captureSelect.value}_INTERACTIVE_3D.html`', 'window.location.href = `CAPTURE${captureSelect.value}_PURE_IMU_MVP.html`')
    bind_marker = '    Object.values(toggles).forEach(toggle => toggle.addEventListener("change", drawFrame));\n\n'
    bind_injection = '''    Object.values(toggles).forEach(toggle => toggle.addEventListener("change", drawFrame));
    comparisonMode.addEventListener("change", drawFrame); nodeDetail.addEventListener("change", drawFrame);
    document.getElementById("recenter-yaw").addEventListener("click", () => {
      const pelvis=meta.segment_names.indexOf("pelvis"); if(!segmentValid(state.frame,pelvis)){gaugeEvent.textContent="Recenter rejected: pelvis unavailable";return;}
      const forward=quatRotate(workQAt(state.frame,pelvis),[1,0,0]), horizontal=Math.hypot(forward[0],forward[1]);
      if(!Number.isFinite(horizontal)||horizontal<=meta.horizontal_projection_minimum_norm){gaugeEvent.textContent="Recenter rejected: degenerate pelvis forward";return;}
      state.gaugeGamma=-Math.atan2(forward[1],forward[0]); state.gaugeEpoch+=1; state.gaugeSource="EXPLICIT_OPERATOR_COMMAND";
      state.gaugeEvents.push({frame:state.frame,time_s:arrays.time_s[state.frame],gamma_rad:state.gaugeGamma,classification:"OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE"});
      gaugeEvent.textContent=`OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE · ${state.gaugeGamma.toFixed(6)} rad · epoch ${state.gaugeEpoch}`; drawFrame();
    });
    document.getElementById("clear-recenter").addEventListener("click", () => { state.gaugeGamma=0;state.gaugeEpoch+=1;state.gaugeSource="RAW";state.gaugeEvents.push({frame:state.frame,time_s:arrays.time_s[state.frame],gamma_rad:0,classification:"OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE"});gaugeEvent.textContent=`Gauge: RAW · epoch ${state.gaugeEpoch}`;drawFrame(); });

'''
    if bind_marker not in core: raise RuntimeError("control marker missing")
    core = core.replace(bind_marker, bind_injection)
    core = core.replace('const cameraModes = ["WORLD_FIXED_FRONT", "WORLD_FIXED_SIDE", "WORLD_FIXED_TOP", "FREE_ORBIT",', 'const cameraModes = ["WORLD_FIXED_FRONT", "WORLD_FIXED_SIDE", "WORLD_FIXED_TOP", "WORLD_FIXED_OBLIQUE", "FREE_ORBIT",')
    core = core.replace('assertion(checks, "typed arrays decoded", arrays.time_s.length === meta.frames && arrays.q_GB_wxyz.length === meta.frames*segmentCount*4);', 'assertion(checks,"typed arrays decoded",arrays.time_s.length===meta.frames&&arrays.q_GB_wxyz.length===meta.frames*segmentCount*4&&arrays.working_q_GB_wxyz.length===arrays.q_GB_wxyz.length);')
    core = core.replace('    const checks = [];', '    const checks = [];\n    const immutablePoseSnapshot=JSON.stringify([arrays.q_GB_wxyz[0],arrays.q_GB_wxyz[arrays.q_GB_wxyz.length-1],arrays.working_q_GB_wxyz[0],arrays.working_q_GB_wxyz[arrays.working_q_GB_wxyz.length-1],arrays.joint_positions_m[0],arrays.joint_positions_m[arrays.joint_positions_m.length-1]]);')
    selftest_marker = '    const result = {capture: meta.capture, pass: checks.every(check => check.pass), checks,\n'
    selftest = '''    const cameraBefore=[state.yaw,state.pitch,state.zoom,state.panX,state.panY], basisBefore=cameraBasis(state.frame); const epochBefore=state.gaugeEpoch;
    document.getElementById("recenter-yaw").click(); assertion(checks,"explicit recenter accepted",state.gaugeEpoch===epochBefore+1&&state.gaugeSource==="EXPLICIT_OPERATOR_COMMAND"&&Number.isFinite(state.gaugeGamma));
    assertion(checks,"recenter matches engine candidate",Math.abs(state.gaugeGamma-arrays.recenter_gamma_candidate_rad[state.frame])<=1e-6);
    assertion(checks,"recenter leaves camera unchanged",JSON.stringify(cameraBefore)===JSON.stringify([state.yaw,state.pitch,state.zoom,state.panX,state.panY]));
    assertion(checks,"recenter leaves camera basis unchanged",JSON.stringify(basisBefore)===JSON.stringify(cameraBasis(state.frame)));
    const recenteredGamma=state.gaugeGamma; comparisonMode.value="RECENTERED";comparisonMode.dispatchEvent(new Event("change",{bubbles:true}));assertion(checks,"RECENTERED mode",comparisonMode.value==="RECENTERED"&&state.gaugeGamma===recenteredGamma);
    comparisonMode.value="OVERLAY";comparisonMode.dispatchEvent(new Event("change",{bubbles:true}));assertion(checks,"OVERLAY shared gauge",comparisonMode.value==="OVERLAY"&&state.gaugeGamma===recenteredGamma);
    document.getElementById("clear-recenter").click(); assertion(checks,"clear returns raw gauge",state.gaugeGamma===0&&state.gaugeSource==="RAW"&&state.gaugeEpoch===epochBefore+2);
    comparisonMode.value="RAW";comparisonMode.dispatchEvent(new Event("change",{bubbles:true}));
    const gaugeAfterClear=state.gaugeEpoch; const resetFrame=arrays.filter_reset.findIndex(value=>value!==0); if(resetFrame>=0)setFrame(Math.floor(resetFrame/segmentCount)); assertion(checks,"reset does not auto-recenter",state.gaugeEpoch===gaugeAfterClear&&state.gaugeGamma===0);
    assertion(checks,"validity overlay control",toggles.validity instanceof HTMLInputElement);
    assertion(checks,"camera and viewer interactions mutate zero pose values",immutablePoseSnapshot===JSON.stringify([arrays.q_GB_wxyz[0],arrays.q_GB_wxyz[arrays.q_GB_wxyz.length-1],arrays.working_q_GB_wxyz[0],arrays.working_q_GB_wxyz[arrays.working_q_GB_wxyz.length-1],arrays.joint_positions_m[0],arrays.joint_positions_m[arrays.joint_positions_m.length-1]]));
    const renderFrames=60, renderStart=performance.now(); for(let i=0;i<renderFrames;i+=1){await new Promise(resolve=>setTimeout(resolve,16));setFrame((state.frame+1)%meta.frames);} const renderElapsed=performance.now()-renderStart;
    const viewerRenderBenchmark={frames:renderFrames,elapsed_ms:renderElapsed,throughput_fps:renderFrames*1000/renderElapsed,measurement:"16 ms timed playback with a full-canvas draw per callback in offline browser"};

''' + selftest_marker.replace('checks,\n', 'checks, viewer_render_benchmark: viewerRenderBenchmark,\n')
    if selftest_marker not in core: raise RuntimeError("selftest marker missing")
    core = core.replace(selftest_marker, selftest)
    core = core.replace('getState: () => ({frame: state.frame, mode: state.mode, zoom: state.zoom, panX: state.panX, panY: state.panY}),', 'getState: () => ({frame:state.frame,cameraMode:state.mode,comparisonMode:comparisonMode.value,zoom:state.zoom,panX:state.panX,panY:state.panY,gaugeGamma:state.gaugeGamma,gaugeEpoch:state.gaugeEpoch,gaugeSource:state.gaugeSource,gaugeEvents:state.gaugeEvents.slice()}),')
    core = core.replace('await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));', 'await new Promise(resolve => setTimeout(resolve,0));')
    return core


def write_shared(output: Path, stage2_source: Path) -> None:
    (output/"C123_MVP_VIEWER_CORE.js").write_text(patched_core(stage2_source/"viewer_core.js"), encoding="utf-8")
    css = (stage2_source/"viewer.css").read_text(encoding="utf-8")
    css += "\n.truth-strip{display:flex;flex-wrap:wrap;gap:.5rem;color:#fbbf24;font-size:.78rem}.truth-strip strong{border:1px solid #6b5a20;padding:.2rem .4rem}.toolbar button#recenter-yaw{border-color:#38bdf8}.toolbar button#clear-recenter{border-color:#94a3b8}#gauge-event{color:#7dd3fc}\n"
    (output/"C123_MVP_VIEWER.css").write_text(css, encoding="utf-8")
    cards = "".join(f'<a class="capture-card" href="CAPTURE{capture}_PURE_IMU_MVP.html"><strong>Capture {capture}</strong><span>Open raw-authoritative MVP replay</span></a>' for capture in "123")
    index = f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>BioSpur Pure-IMU MVP</title><link rel="stylesheet" href="C123_MVP_VIEWER.css"></head><body><main class="index"><h1>BioSpur Pure-IMU MVP</h1><div class="truth-strip"><strong>PURE IMU</strong><strong>ROOT FIXED</strong><strong>RAW AUTHORITATIVE</strong><strong>GLOBAL YAW MAY DRIFT</strong><strong>MANUAL RECENTER CHANGES GAUGE ONLY</strong></div><section class="capture-grid">{cards}</section><p class="note">Offline viewer · no network dependency · manual recenter is a global display gauge only.</p></main></body></html>'''
    (output/"C123_PURE_IMU_MVP_VIEWER_INDEX.html").write_text(index, encoding="utf-8")


def export_capture(capture: str, raw: dict, pose: dict, config: dict, output: Path) -> dict:
    pelvis = [str(value) for value in raw["segment_names"]].index("pelvis")
    qpelvis = pose["working_q_GB_wxyz"][:, pelvis]
    forward = np.full((len(qpelvis), 3), np.nan, dtype=np.float64)
    finite = raw["valid"][:, pelvis]
    from pure_imu_baseline.math3d import rotate
    forward[finite] = rotate(qpelvis[finite], np.array([1.0,0.0,0.0]))
    horizontal = np.hypot(forward[:,0],forward[:,1])
    gamma_candidate = np.where(horizontal>float(config["horizontal_projection_minimum_norm"]), -np.arctan2(forward[:,1],forward[:,0]), np.nan)
    arrays = {
        "time_s": np.ascontiguousarray(raw["time_s"],dtype="<f8"),
        "q_GB_wxyz": np.ascontiguousarray(raw["q_GB_wxyz"],dtype="<f4"),
        "working_q_GB_wxyz": np.ascontiguousarray(pose["working_q_GB_wxyz"],dtype="<f4"),
        "valid": np.ascontiguousarray(raw["valid"],dtype=np.uint8), "filter_reset":np.ascontiguousarray(raw["filter_reset"],dtype=np.uint8),
        "joint_positions_m":np.ascontiguousarray(raw["joint_positions_m"],dtype="<f4"), "joint_available":np.ascontiguousarray(raw["joint_available"],dtype=np.uint8),
        "epoch_per_node":np.ascontiguousarray(pose["epoch_per_node"],dtype="<u4"), "quality_state":np.ascontiguousarray(pose["quality_state"],dtype=np.uint8),
        "sample_age_s":np.ascontiguousarray(pose["sample_age_s"],dtype="<f4"), "time_since_last_valid_s":np.ascontiguousarray(pose["time_since_last_valid_s"],dtype="<f4"),
        "time_since_last_reset_s":np.ascontiguousarray(pose["time_since_last_reset_s"],dtype="<f4"), "last_reset_reason":np.ascontiguousarray(pose["last_reset_reason"],dtype=np.uint8),
        "recenter_gamma_candidate_rad":np.ascontiguousarray(gamma_candidate,dtype="<f4"), "pelvis_forward_horizontal_norm":np.ascontiguousarray(horizontal,dtype="<f4"),
    }
    payload,schema=pack(arrays); compressed=gzip.compress(payload,compresslevel=6,mtime=0)
    write_chunks(output/f"CAPTURE{capture}_MVP_VIEWER_DATA.js",compressed)
    joint_names=[str(value) for value in raw["joint_names"]]; segment_names=[str(value) for value in raw["segment_names"]]
    meta={"schema":"biospur.pure_imu.mvp_m1.viewer.v1","capture":capture,"frames":len(raw["time_s"]),"duration_s":float(raw["time_s"][-1]),
          "display_rate_hz":float(1/np.median(np.diff(raw["time_s"]))),"arrays":schema,"node_ids":[str(value) for value in raw["node_ids"]],
          "segment_names":segment_names,"joint_names":joint_names,"edges":edges(joint_names),"segment_origins":[joint_names.index(SEGMENT_ORIGIN_JOINT[name]) for name in segment_names],
          "events":gap_events(raw),"ghost_frame":0,"ground_z_m":-(GEOMETRY["thigh_left"]+GEOMETRY["shank_left"]),
          "colors":{"left":"#f59e0b","right":"#22d3ee","core":"#f8fafc","pelvis":"#22c55e"},"raw_authoritative":True,"root_mode":"FIXED",
          "horizontal_projection_minimum_norm":float(config["horizontal_projection_minimum_norm"]),
          "payload":{"raw_sha256":hashlib.sha256(payload).hexdigest(),"gzip_sha256":hashlib.sha256(compressed).hexdigest(),"raw_bytes":len(payload),"gzip_bytes":len(compressed)}}
    (output/f"CAPTURE{capture}_PURE_IMU_MVP.html").write_text(page(capture,meta),encoding="utf-8")
    return meta
