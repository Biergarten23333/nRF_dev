"""Build the offline Root-R3 browser review and accelerated MP4.

The viewer contains only downsampled copies of generated Root-R3 evidence.  It
never interpolates estimator state and it does not participate in estimation.
"""
from __future__ import annotations

import bisect
import json
from pathlib import Path
import subprocess

import numpy as np

from .data import default_authorized_paths, load_c1_uwb, load_m1, position_observations


INIT_END = 219.6501813060022


def _indices(time_s: np.ndarray, maximum: int) -> np.ndarray:
    time_s = np.asarray(time_s, float)
    finite = np.flatnonzero(np.isfinite(time_s))
    if len(finite) <= maximum:
        return finite
    selected = np.linspace(0, len(finite) - 1, maximum, dtype=int)
    return np.unique(finite[selected])


def _round_array(value: np.ndarray, decimals: int = 5):
    array = np.asarray(value)
    if array.dtype.kind in "f":
        array = np.round(array.astype(float), decimals)
        return [[None if not np.isfinite(item) else float(item) for item in row]
                for row in array] if array.ndim == 2 else [None if not np.isfinite(item) else float(item) for item in array]
    if array.dtype.kind == "b":
        return [bool(item) for item in array]
    return [str(item) for item in array]


def _series(label: str, status: str, time_s: np.ndarray, root_m: np.ndarray,
            covariance_m2: np.ndarray | None = None, mode: np.ndarray | None = None,
            accepted: np.ndarray | None = None, *, maximum: int = 3600,
            frame: str = "UNQUALIFIED") -> dict:
    time_s = np.asarray(time_s, float); root_m = np.asarray(root_m, float)
    valid = np.isfinite(time_s) & np.all(np.isfinite(root_m), axis=1)
    source = np.flatnonzero(valid)
    if len(source) > maximum:
        base = source[np.linspace(0, len(source) - 1, maximum, dtype=int)]
        extras: list[int] = []
        if accepted is not None:
            rejected = source[~np.asarray(accepted, bool)[source]]
            if len(rejected):
                extras.extend(rejected[np.linspace(0, len(rejected) - 1, min(500, len(rejected)), dtype=int)])
        if mode is not None and len(source) > 1:
            mode_array = np.asarray(mode).astype(str)
            transitions = source[1:][mode_array[source[1:]] != mode_array[source[:-1]]]
            extras.extend(transitions.tolist())
        source = np.unique(np.r_[base, np.asarray(extras, int)])
    result = {
        "label": label, "status": status, "frame": frame, "available": bool(len(source)),
        "t": _round_array(time_s[source], 4), "p": _round_array(root_m[source], 4),
    }
    if covariance_m2 is not None:
        result["sigma"] = _round_array(np.sqrt(np.maximum(np.asarray(covariance_m2, float)[source], 0.0)), 4)
    else:
        result["sigma"] = [[0.0, 0.0, 0.0] for _ in source]
    result["mode"] = _round_array(np.asarray(mode)[source] if mode is not None else np.full(len(source), status))
    result["accepted"] = _round_array(np.asarray(accepted, bool)[source] if accepted is not None else np.ones(len(source), bool))
    return result


def build_payload(output: Path) -> dict:
    output = Path(output).resolve()
    paths = default_authorized_paths()
    m1 = load_m1(paths.m1_npz)
    table = load_c1_uwb(paths, m1)
    order = np.argsort(table.availability_s, kind="stable")
    with np.load(output / "C1_ROOT_R3_TRAJECTORIES.npz", allow_pickle=False) as archive:
        trajectory = {name: archive[name] for name in archive.files}

    b2_observations = sorted(
        position_observations(table, assumed_rotation_v4_from_m1=np.eye(3), frame_valid=True),
        key=lambda value: (value.availability_time_s, value.measurement_time_s, value.source_sequence),
    )
    b4_observations = [value for value in b2_observations if value.measurement_time_s > INIT_END]
    if len(b2_observations) != len(trajectory["B2_accepted"]):
        raise ValueError("B2 viewer observation alignment mismatch")
    if len(b4_observations) < len(trajectory["B4_accepted"]):
        raise ValueError("B4 viewer observation alignment mismatch")

    b2_accept_by_source = np.zeros(len(table.measurement_s), bool)
    for observation, accepted in zip(b2_observations, trajectory["B2_accepted"]):
        b2_accept_by_source[observation.source_sequence] = bool(accepted)
    b4_accept_by_source = np.zeros(len(table.measurement_s), bool)
    for observation, accepted in zip(b4_observations, trajectory["B4_accepted"]):
        b4_accept_by_source[observation.source_sequence] = bool(accepted)

    b1_covariance = table.covariance_diag_m2[order] + np.array([0.25**2, 0.25**2, 0.30**2])
    baselines = {
        "B0": _series("B0 fixed-root M1", "NEGATIVE CONTROL", trajectory["event_time_s"],
                      trajectory["B0_root_m"], mode=np.full(len(order), "FIXED_ROOT"),
                      frame="M1_ARBITRARY_GAUGE"),
        "B1": _series("B1 UWB root observations", "QUARANTINED IDENTITY-FRAME DIAGNOSTIC",
                      trajectory["event_time_s"], trajectory["B1_identity_diagnostic_root_m"],
                      b1_covariance, np.full(len(order), "RAW_UWB_OBSERVATION"),
                      table.m1_valid[order], frame="R_N_FROM_V4_UNQUALIFIED"),
        "B2": _series("B2 CV + UWB tracker with M1 geometry", "QUARANTINED IDENTITY-FRAME DIAGNOSTIC",
                      trajectory["B2_time_s"], trajectory["B2_root_m"], trajectory["B2_covariance_diag_m2"],
                      trajectory["B2_mode"], trajectory["B2_accepted"], frame="R_N_FROM_V4_UNQUALIFIED"),
        "B3": _series("B3 inertial-only root", "GENUINE EXPERIMENTAL INERTIAL BASELINE",
                      trajectory["B3_time_s"], trajectory["B3_root_m"], trajectory["B3_covariance_diag_m2"],
                      trajectory["B3_mode"], frame="M1_GRAVITY_ALIGNED_ARBITRARY_YAW"),
        "B4": _series("B4 inertial + UWB", "QUARANTINED IDENTITY-FRAME DIAGNOSTIC",
                      trajectory["B4_time_s"], trajectory["B4_root_m"], trajectory["B4_covariance_diag_m2"],
                      trajectory["B4_mode"], trajectory["B4_accepted"], frame="R_N_FROM_V4_UNQUALIFIED"),
        "B5": {"label": "B5 direct-range diagnostic", "status": "NOT RUN — HARD FRAME INVARIANT",
               "frame": "R_N_FROM_V4_UNQUALIFIED", "available": False, "t": [], "p": [],
               "sigma": [], "mode": [], "accepted": []},
    }

    raw_source = order[_indices(table.availability_s[order], 7000)]
    layout = json.loads(paths.layout_json.read_text(encoding="utf-8"))
    full_metrics = json.loads((output / "FULL_C1_SHADOW_REPLAY_METRICS.json").read_text(encoding="utf-8"))
    quality = json.loads((output / "UWB_QUALITY_AND_FDI_AUDIT.json").read_text(encoding="utf-8"))
    degradation = json.loads((output / "DEGRADATION_AND_RECOVERY_AUDIT.json").read_text(encoding="utf-8"))
    final = json.loads((output / "FINAL_RESULT.json").read_text(encoding="utf-8"))
    payload = {
        "schema": "biospur.root_r3.viewer.v1",
        "title": "BioSpur C1 Root-R3 offline review",
        "capture": "C1",
        "warning": "No authoritative proper R_N_from_V4 transform exists. B1, B2, and B4 are identity-frame diagnostics only.",
        "scientific_display": "Nearest recorded sample only; no interpolation, cosmetic smoothing, or future-data alignment.",
        "time_span_s": [float(np.nanmin(table.availability_s)), float(np.nanmax(table.availability_s))],
        "baselines": baselines,
        "tags": list(table.node_ids),
        "anchors": [{"id": int(row["id"]), "label": row["label"],
                     "p": [round(row["x_mm"] / 1000.0, 4), round(row["y_mm"] / 1000.0, 4),
                           round(row["z_mm"] / 1000.0, 4)]} for row in layout["anchors"]],
        "uwb": {
            "t": _round_array(table.availability_s[raw_source], 4),
            "p": _round_array(table.xyz_m[raw_source], 4),
            "tag": [table.node_ids[int(value)] for value in table.node_index[raw_source]],
            "b2Accepted": _round_array(b2_accept_by_source[raw_source]),
            "b4Accepted": _round_array(b4_accept_by_source[raw_source]),
            "anchorMask": [int(value) for value in table.used_mask[raw_source]],
        },
        "health": {
            "B2": full_metrics["B2"]["audit"]["health"],
            "B4": full_metrics["B4"]["audit"]["health"],
        },
        "summary": {
            "events": quality["events"],
            "historicalScaleM": final["historical_robust_scale_m"],
            "betweenTagM": final["within_vs_between"]["between_tag_robust_radial_m"],
            "b2InfluenceMaxM": degradation["B2_maximum_output_influence_m"],
            "b4InfluenceMaxM": degradation["B4_maximum_output_influence_m"],
            "futureDataCounts": {key: final["causality"][key] for key in ("B2", "B3", "B4")},
        },
    }
    return payload


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur C1 Root-R3 Offline Review</title>
<style>
:root{color-scheme:dark;--bg:#071018;--panel:#0d1a25;--panel2:#122332;--line:#2b4355;--text:#eef6fb;--muted:#9ab0bf;--cyan:#43d9d0;--blue:#63a9ff;--amber:#ffbf5b;--red:#ff6f72;--green:#7ddc91;--purple:#c39bff}
*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% 0,#102638 0,var(--bg) 42%);color:var(--text);font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif}button,select,input{font:inherit}button,select{background:var(--panel2);color:var(--text);border:1px solid var(--line);border-radius:6px;padding:.42rem .65rem}button:hover,button:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--cyan);outline-offset:2px}.shell{max-width:1500px;margin:auto;padding:18px}.header{display:flex;gap:18px;justify-content:space-between;align-items:flex-start;border-bottom:1px solid var(--line);padding-bottom:14px}.header h1{font-size:1.35rem;margin:0 0 4px;font-weight:600}.sub{color:var(--muted);font-size:.9rem}.blocked{border:1px solid #8e5253;background:#351b20;color:#ffd9d9;border-radius:6px;padding:8px 10px;max-width:630px}.controls{display:flex;flex-wrap:wrap;align-items:end;gap:10px;padding:14px 0}.control{display:grid;gap:4px}.control label,.toggle{font-size:.78rem;color:var(--muted)}.toggle{display:flex;align-items:center;gap:6px;padding-bottom:7px}.timeline{flex:1;min-width:260px}.timeline input{width:100%}.grid{display:grid;grid-template-columns:minmax(0,2.2fr) minmax(270px,.8fr);gap:14px}.panel{background:color-mix(in srgb,var(--panel) 92%,transparent);border:1px solid var(--line);border-radius:8px;padding:12px}.scene-wrap{position:relative}.scene-wrap canvas{width:100%;height:540px;display:block;background:#07131d;border-radius:5px}.watermark{position:absolute;left:24px;top:22px;color:#ffb1b3;background:#351b20d9;border:1px solid #8e5253;border-radius:4px;padding:4px 7px;font-size:.75rem;letter-spacing:.03em}.status h2,.health h2{font-size:1rem;margin:0 0 9px;font-weight:600}.readout{display:grid;grid-template-columns:1fr auto;gap:5px 12px;margin:0}.readout dt{color:var(--muted)}.readout dd{margin:0;text-align:right;font-variant-numeric:tabular-nums}.mode{margin:12px 0;padding:8px;border-left:3px solid var(--cyan);background:var(--panel2)}.legend{display:flex;gap:12px;flex-wrap:wrap;margin-top:9px;color:var(--muted);font-size:.78rem}.sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:4px}.plots{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-top:14px}.plots canvas{width:100%;height:220px;display:block}.health{margin-top:14px}.tables{display:grid;grid-template-columns:1fr 1fr;gap:16px}.health table{width:100%;border-collapse:collapse;font-size:.8rem}.health th,.health td{padding:4px;border-bottom:1px solid var(--line);text-align:right}.health th:first-child,.health td:first-child{text-align:left}.bar{height:6px;background:#233746;border-radius:3px;overflow:hidden}.bar i{display:block;height:100%;background:var(--green)}.foot{color:var(--muted);font-size:.78rem;margin-top:14px}.blocked-screen{display:grid;place-items:center;height:100%;text-align:center;padding:30px}.blocked-screen strong{font-size:1.25rem;color:#ffb1b3}.kicker{color:var(--amber);font-size:.78rem;text-transform:uppercase;letter-spacing:.08em}@media(max-width:900px){.grid,.plots,.tables{grid-template-columns:1fr}.scene-wrap canvas{height:430px}.header{display:block}.blocked{margin-top:10px}}@media(max-width:520px){.shell{padding:10px}.scene-wrap canvas{height:350px}.controls{align-items:stretch}.control,.timeline{width:100%}}
</style>
</head>
<body>
<main class="shell">
  <header class="header">
    <div><div class="kicker">strict-causal weak absolute reference</div><h1>BioSpur C1 Root-R3 Offline Review</h1><div class="sub">98,342 C1 UWB events · accelerated review · evidence bundle is read-only</div></div>
    <div class="blocked" role="status"><strong>Scientific spatial fusion blocked.</strong> No qualified proper navigation-from-V4 transform exists; identity-frame paths are visibly quarantined.</div>
  </header>
  <section class="controls" aria-label="Review controls">
    <div class="control"><label for="baseline">Baseline</label><select id="baseline"><option>B0</option><option>B1</option><option selected>B2</option><option>B3</option><option>B4</option><option>B5</option></select></div>
    <div class="control"><label for="camera">Camera</label><select id="camera"><option value="world">World-fixed</option><option value="root">Root-following</option></select></div>
    <button id="play" type="button" aria-pressed="false">Play</button>
    <div class="control"><label for="speed">Replay speed</label><select id="speed"><option value="10">10×</option><option value="40" selected>40×</option><option value="100">100×</option></select></div>
    <label class="toggle"><input id="cloud" type="checkbox" checked> raw UWB tag cloud</label>
    <label class="toggle"><input id="rejected" type="checkbox" checked> rejected measurements</label>
    <div class="control timeline"><label for="time">Capture time <output id="timeLabel"></output></label><input id="time" type="range" min="0" max="10000" value="0" step="1"></div>
  </section>
  <section class="grid">
    <div class="panel scene-wrap"><canvas id="scene" role="img" aria-label="Root trajectory, raw UWB tag cloud, anchors, and covariance in plan view"></canvas><div id="watermark" class="watermark"></div></div>
    <aside class="panel status"><h2 id="baselineTitle"></h2><dl class="readout"><dt>Status</dt><dd id="status"></dd><dt>Recorded time</dt><dd id="recorded"></dd><dt>Root XYZ</dt><dd id="xyz"></dd><dt>Velocity proxy</dt><dd id="velocity"></dd><dt>Root σ XYZ</dt><dd id="sigma"></dd><dt>UWB decision</dt><dd id="decision"></dd><dt>Frame</dt><dd id="frame"></dd><dt>Camera</dt><dd id="cameraLabel"></dd></dl><div class="mode"><div class="sub">Active mode</div><strong id="mode"></strong></div><div class="legend"><span><i class="sw" style="background:var(--green)"></i>accepted</span><span><i class="sw" style="background:var(--red)"></i>rejected</span><span><i class="sw" style="background:var(--cyan)"></i>root</span><span><i class="sw" style="background:var(--amber)"></i>anchors</span></div></aside>
  </section>
  <section class="plots"><div class="panel"><canvas id="zplot" role="img" aria-label="Recorded root Z history around the selected time"></canvas></div><div class="panel"><canvas id="covplot" role="img" aria-label="Recorded root position uncertainty around the selected time"></canvas></div></section>
  <section class="panel health"><h2>Tag and anchor health — selected diagnostic path</h2><div id="health" class="tables"></div></section>
  <footer class="foot">Nearest recorded sample only. No estimator-state interpolation, cosmetic smoothing, centered alignment, or future measurement use. Camera motion is presentation-only. B0 is a negative control; B1/B2/B4 are identity-frame diagnostics; B3 is genuine inertial propagation in an arbitrary-yaw M1 gauge; B5 was not run because the hard frame invariant failed.</footer>
</main>
<script>
const DATA=__DATA__;
const $=id=>document.getElementById(id); const baseline=$('baseline'),camera=$('camera'),play=$('play'),slider=$('time'),speed=$('speed'),cloud=$('cloud'),rejected=$('rejected');
let playing=false,lastFrame=0,currentT=DATA.time_span_s[0];
function nearest(a,x){let lo=0,hi=a.length;while(lo<hi){const m=(lo+hi)>>1;if(a[m]<x)lo=m+1;else hi=m}if(lo===0)return 0;if(lo===a.length)return a.length-1;return Math.abs(a[lo]-x)<Math.abs(a[lo-1]-x)?lo:lo-1}
function sizeCanvas(canvas){const dpr=Math.min(devicePixelRatio||1,2),r=canvas.getBoundingClientRect();const w=Math.max(1,Math.round(r.width*dpr)),h=Math.max(1,Math.round(r.height*dpr));if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h}const c=canvas.getContext('2d');c.setTransform(dpr,0,0,dpr,0,0);return {c,w:r.width,h:r.height}}
function colors(){const s=getComputedStyle(document.documentElement);return {text:s.getPropertyValue('--text'),muted:s.getPropertyValue('--muted'),line:s.getPropertyValue('--line'),cyan:s.getPropertyValue('--cyan'),blue:s.getPropertyValue('--blue'),amber:s.getPropertyValue('--amber'),red:s.getPropertyValue('--red'),green:s.getPropertyValue('--green'),purple:s.getPropertyValue('--purple')}}
function selected(){return DATA.baselines[baseline.value]}
function timeFromSlider(){const span=DATA.time_span_s;return span[0]+(+slider.value/10000)*(span[1]-span[0])}
function project(p,b,w,h){return [45+(p[0]-b.x0)/(b.x1-b.x0)*(w-70),h-35-(p[1]-b.y0)/(b.y1-b.y0)*(h-65)]}
function drawScene(){const {c,w,h}=sizeCanvas($('scene')),C=colors(),b=selected();c.clearRect(0,0,w,h);if(!b.available){c.fillStyle=C.text;c.textAlign='center';c.font='600 20px system-ui';c.fillText('B5 was not run',w/2,h/2-12);c.fillStyle=C.red;c.font='14px system-ui';c.fillText('Hard invariant: R_N_from_V4 is unqualified',w/2,h/2+18);return}const k=nearest(b.t,currentT),root=b.p[k],rootOk=root&&root.every(Number.isFinite);let bounds=camera.value==='root'&&rootOk?{x0:root[0]-3,x1:root[0]+3,y0:root[1]-2.4,y1:root[1]+2.4}:{x0:-.8,x1:5.2,y0:-.7,y1:3.8};
 c.strokeStyle=C.line;c.lineWidth=1;c.font='12px system-ui';c.fillStyle=C.muted;c.textAlign='center';for(let x=Math.ceil(bounds.x0);x<=bounds.x1;x++){let a=project([x,bounds.y0],bounds,w,h),d=project([x,bounds.y1],bounds,w,h);c.beginPath();c.moveTo(a[0],a[1]);c.lineTo(d[0],d[1]);c.stroke();c.fillText(x+' m',a[0],h-12)}c.textAlign='right';for(let y=Math.ceil(bounds.y0);y<=bounds.y1;y++){let a=project([bounds.x0,y],bounds,w,h),d=project([bounds.x1,y],bounds,w,h);c.beginPath();c.moveTo(a[0],a[1]);c.lineTo(d[0],d[1]);c.stroke();c.fillText(y+' m',38,a[1]+4)}
 DATA.anchors.forEach(a=>{const q=project(a.p,bounds,w,h);if(q[0]<35||q[0]>w-20||q[1]<15||q[1]>h-25)return;c.fillStyle=C.amber;c.fillRect(q[0]-4,q[1]-4,8,8);c.fillStyle=C.text;c.textAlign='left';c.fillText(a.label,q[0]+7,q[1]+4)});
 if(cloud.checked){const u=DATA.uwb,centre=nearest(u.t,currentT),from=Math.max(0,centre-70),to=Math.min(u.t.length,centre+70),acceptKey=baseline.value==='B4'?'b4Accepted':'b2Accepted';for(let i=from;i<to;i++){if(Math.abs(u.t[i]-currentT)>2.5)continue;const ok=u[acceptKey][i];if(!ok&&!rejected.checked)continue;const q=project(u.p[i],bounds,w,h);if(q[0]<35||q[0]>w-20||q[1]<15||q[1]>h-25)continue;c.fillStyle=ok?C.green:C.red;c.globalAlpha=.62;if(ok){c.beginPath();c.arc(q[0],q[1],2.5,0,Math.PI*2);c.fill()}else{c.strokeStyle=C.red;c.beginPath();c.moveTo(q[0]-3,q[1]-3);c.lineTo(q[0]+3,q[1]+3);c.moveTo(q[0]+3,q[1]-3);c.lineTo(q[0]-3,q[1]+3);c.stroke()}c.globalAlpha=1}}
 c.strokeStyle=C.blue;c.lineWidth=2;c.beginPath();let started=false;const earliest=camera.value==='root'?currentT-35:-Infinity;for(let i=0;i<=k;i++){if(b.t[i]<earliest)continue;const p=b.p[i];if(!p||!p.every(Number.isFinite))continue;const q=project(p,bounds,w,h);if(q[0]<35||q[0]>w-20||q[1]<15||q[1]>h-25){started=false;continue}if(!started){c.moveTo(q[0],q[1]);started=true}else c.lineTo(q[0],q[1])}c.stroke();
 if(rootOk){const q=project(root,bounds,w,h),sig=b.sigma[k]||[0,0,0],rx=Math.min((w-70)*sig[0]/(bounds.x1-bounds.x0),w*.35),ry=Math.min((h-65)*sig[1]/(bounds.y1-bounds.y0),h*.35);c.strokeStyle=C.purple;c.lineWidth=1.5;c.beginPath();c.ellipse(q[0],q[1],Math.max(2,rx),Math.max(2,ry),0,0,Math.PI*2);c.stroke();c.fillStyle=C.cyan;c.beginPath();c.arc(q[0],q[1],6,0,Math.PI*2);c.fill();if(q[0]<35||q[0]>w-20||q[1]<15||q[1]>h-25){c.fillStyle=C.red;c.textAlign='center';c.fillText('root outside world-fixed room view',w/2,24)}}
 c.fillStyle=C.muted;c.textAlign='left';c.fillText(camera.value==='world'?'V4 room coordinates (frame binding unqualified)':'root-following presentation camera',48,20)}
function drawHistory(canvas,key,title,unit){const {c,w,h}=sizeCanvas(canvas),C=colors(),b=selected();c.clearRect(0,0,w,h);c.fillStyle=C.text;c.font='600 13px system-ui';c.fillText(title,10,18);if(!b.available)return;const k=nearest(b.t,currentT),lo=Math.max(0,k-220),hi=Math.min(b.t.length-1,k+220);let values=[];for(let i=lo;i<=hi;i++){if(key==='z')values.push(b.p[i][2]);else values.push(Math.sqrt(b.sigma[i].reduce((s,x)=>s+x*x,0)))}values=values.filter(Number.isFinite);if(!values.length)return;let ymin=Math.min(...values),ymax=Math.max(...values);if(ymax-ymin<1e-6){ymin-=.5;ymax+=.5}const x=i=>42+(i-lo)/Math.max(1,hi-lo)*(w-54),y=v=>h-28-(v-ymin)/(ymax-ymin)*(h-58);c.strokeStyle=C.line;c.strokeRect(42,28,w-54,h-56);c.strokeStyle=key==='z'?C.blue:C.purple;c.lineWidth=2;c.beginPath();let start=false;for(let i=lo;i<=hi;i++){const v=key==='z'?b.p[i][2]:Math.sqrt(b.sigma[i].reduce((s,q)=>s+q*q,0));if(!Number.isFinite(v)){start=false;continue}if(!start){c.moveTo(x(i),y(v));start=true}else c.lineTo(x(i),y(v))}c.stroke();c.strokeStyle=C.cyan;c.beginPath();c.moveTo(x(k),28);c.lineTo(x(k),h-28);c.stroke();c.fillStyle=C.muted;c.font='11px system-ui';c.fillText(ymax.toFixed(2)+' '+unit,4,37);c.fillText(ymin.toFixed(2),8,h-30)}
function updateReadout(){const b=selected(),has=b.available,k=has?nearest(b.t,currentT):0,p=has?b.p[k]:null,s=has?b.sigma[k]:null;$('baselineTitle').textContent=b.label;$('status').textContent=b.status;$('recorded').textContent=has?b.t[k].toFixed(3)+' s':'—';$('xyz').textContent=has?p.map(v=>v.toFixed(2)).join(', ')+' m':'—';if(has&&k>0){const dt=b.t[k]-b.t[k-1],d=Math.hypot(...p.map((v,j)=>v-b.p[k-1][j]));$('velocity').textContent=dt>0?(d/dt).toFixed(2)+' m/s':'—'}else $('velocity').textContent='—';$('sigma').textContent=has?s.map(v=>v.toFixed(2)).join(', ')+' m':'—';$('decision').textContent=has?(b.accepted[k]?'accepted':'rejected'):'—';$('frame').textContent=b.frame;$('cameraLabel').textContent=camera.options[camera.selectedIndex].text;$('mode').textContent=has?b.mode[k]:'BLOCKED';$('watermark').textContent=b.status;slider.disabled=!has}
function healthTable(){const which=baseline.value==='B4'?'B4':'B2',health=DATA.health[which]||DATA.health.B2;function table(name,rows){let s='<div><strong>'+name+'</strong><table><thead><tr><th>ID</th><th>accepted</th><th>rejected</th><th>rate</th></tr></thead><tbody>';Object.entries(rows).forEach(([id,r])=>{const n=r.accepted+r.rejected,rate=n?r.accepted/n:0;s+='<tr><td>'+id+'</td><td>'+r.accepted+'</td><td>'+r.rejected+'</td><td>'+(100*rate).toFixed(1)+'%<div class="bar"><i style="width:'+(100*rate).toFixed(1)+'%"></i></div></td></tr>'});return s+'</tbody></table></div>'}$('health').innerHTML=table(which+' tag health',health.tags)+table(which+' anchor association health',health.anchors)}
function render(){currentT=timeFromSlider();$('timeLabel').textContent=currentT.toFixed(2)+' s';updateReadout();drawScene();drawHistory($('zplot'),'z','Root Z — recorded samples','m');drawHistory($('covplot'),'cov','Position uncertainty ‖σ‖','m')}
function animate(ts){if(!playing)return;if(!lastFrame)lastFrame=ts;const dt=(ts-lastFrame)/1000,last=DATA.time_span_s[1],first=DATA.time_span_s[0];currentT=timeFromSlider()+dt*(+speed.value);slider.value=Math.min(10000,Math.round((currentT-first)/(last-first)*10000));lastFrame=ts;render();if(+slider.value>=10000){playing=false;play.textContent='Play';play.setAttribute('aria-pressed','false');return}requestAnimationFrame(animate)}
play.addEventListener('click',()=>{playing=!playing;play.textContent=playing?'Pause':'Play';play.setAttribute('aria-pressed',String(playing));lastFrame=0;if(playing){if(+slider.value>=10000)slider.value=0;requestAnimationFrame(animate)}});[slider,camera,cloud,rejected].forEach(x=>x.addEventListener('input',render));baseline.addEventListener('change',()=>{healthTable();render()});new ResizeObserver(render).observe(document.querySelector('.shell'));healthTable();render();
</script>
</body></html>
'''


def write_viewer(output: Path, payload: dict | None = None) -> Path:
    output = Path(output).resolve(); payload = build_payload(output) if payload is None else payload
    data = json.dumps(payload, separators=(",", ":"), allow_nan=False)
    path = output / "C1_ROOT_R3_VIEWER_INDEX.html"
    path.write_text(HTML_TEMPLATE.replace("__DATA__", data), encoding="utf-8")
    return path


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    path = Path("/usr/share/fonts/truetype/dejavu") / name
    return ImageFont.truetype(str(path), size) if path.is_file() else ImageFont.load_default()


def write_review_mp4(output: Path, payload: dict | None = None, *, fps: int = 24) -> Path:
    from PIL import Image, ImageDraw

    output = Path(output).resolve(); payload = build_payload(output) if payload is None else payload
    path = output / "C1_ROOT_R3_REVIEW.mp4"
    width, height = 1280, 720
    scenes = [("B0", 2.0), ("B1", 4.0), ("B2", 5.0), ("B3", 4.0), ("B4", 5.0), ("B5", 2.0)]
    command = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{height}", "-r", str(fps), "-i", "-", "-an", "-c:v", "libx264",
               "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    title_font = _font(30, True); body_font = _font(18); small_font = _font(15); big_font = _font(38, True)
    anchors = payload["anchors"]; uwb_t = payload["uwb"]["t"]; uwb_p = payload["uwb"]["p"]
    try:
        for key, duration in scenes:
            baseline = payload["baselines"][key]; frames = max(1, round(duration * fps))
            for frame in range(frames):
                image = Image.new("RGB", (width, height), "#071018"); draw = ImageDraw.Draw(image)
                draw.rectangle((0, 0, width, 82), fill="#0d1a25")
                draw.text((34, 18), "BioSpur C1 Root-R3 — accelerated complete-capture review", font=title_font, fill="#eef6fb")
                draw.text((35, 55), "Nearest recorded samples · no interpolation or cosmetic smoothing", font=small_font, fill="#9ab0bf")
                draw.text((1235, 25), key, anchor="ra", font=big_font, fill="#43d9d0")
                if not baseline["available"]:
                    draw.text((width // 2, 300), "B5 NOT RUN", anchor="mm", font=big_font, fill="#ff6f72")
                    draw.text((width // 2, 350), "Hard invariant failed: R_N_from_V4 unqualified", anchor="mm", font=body_font, fill="#ffb1b3")
                    draw.text((35, 680), baseline["status"], font=small_font, fill="#ffb1b3")
                    process.stdin.write(image.tobytes()); continue
                n = len(baseline["t"]); index = min(n - 1, round(frame / max(1, frames - 1) * (n - 1)))
                time_s = baseline["t"][index]; root = baseline["p"][index]; sigma = baseline["sigma"][index]
                root_follow = key in ("B3", "B4")
                bounds = (root[0] - 3, root[0] + 3, root[1] - 2.4, root[1] + 2.4) if root_follow else (-.8, 5.2, -.7, 3.8)
                left, top, right, bottom = 45, 110, 895, 640
                def project(point):
                    return (left + (point[0] - bounds[0]) / (bounds[1] - bounds[0]) * (right - left),
                            bottom - (point[1] - bounds[2]) / (bounds[3] - bounds[2]) * (bottom - top))
                for x in range(int(np.ceil(bounds[0])), int(np.floor(bounds[1])) + 1):
                    q0, q1 = project((x, bounds[2])), project((x, bounds[3])); draw.line((q0[0], q0[1], q1[0], q1[1]), fill="#2b4355")
                for y in range(int(np.ceil(bounds[2])), int(np.floor(bounds[3])) + 1):
                    q0, q1 = project((bounds[0], y)), project((bounds[1], y)); draw.line((q0[0], q0[1], q1[0], q1[1]), fill="#2b4355")
                for anchor in anchors:
                    q = project(anchor["p"])
                    if left <= q[0] <= right and top <= q[1] <= bottom:
                        draw.rectangle((q[0]-5, q[1]-5, q[0]+5, q[1]+5), fill="#ffbf5b")
                        label_y = q[1] - 19 if anchor["id"] < 4 else q[1] + 4
                        draw.text((q[0]+8, label_y), anchor["label"], font=small_font, fill="#eef6fb")
                if not root_follow:
                    centre = bisect.bisect_left(uwb_t, time_s)
                    for j in range(max(0, centre-55), min(len(uwb_t), centre+55)):
                        if abs(uwb_t[j]-time_s) > 1.2: continue
                        q = project(uwb_p[j])
                        if left <= q[0] <= right and top <= q[1] <= bottom:
                            draw.ellipse((q[0]-2,q[1]-2,q[0]+2,q[1]+2),fill="#7ddc91")
                path_points = []
                start = max(0, index - (220 if root_follow else index))
                for point in baseline["p"][start:index+1]:
                    if point is None or any(value is None for value in point): continue
                    q = project(point)
                    if left <= q[0] <= right and top <= q[1] <= bottom: path_points.append(q)
                if len(path_points) > 1: draw.line(path_points, fill="#63a9ff", width=3)
                q = project(root)
                if left <= q[0] <= right and top <= q[1] <= bottom:
                    sx = min(180, sigma[0] / (bounds[1] - bounds[0]) * (right - left)); sy = min(160, sigma[1] / (bounds[3] - bounds[2]) * (bottom - top))
                    draw.ellipse((q[0]-sx,q[1]-sy,q[0]+sx,q[1]+sy),outline="#c39bff",width=2);draw.ellipse((q[0]-7,q[1]-7,q[0]+7,q[1]+7),fill="#43d9d0")
                draw.rectangle((925, 110, 1245, 640), fill="#0d1a25", outline="#2b4355", width=2)
                rows = [("Baseline", baseline["label"]), ("Status", baseline["status"]), ("Capture time", f"{time_s:.2f} s"),
                        ("Root XYZ", ", ".join(f"{v:.2f}" for v in root)+" m"), ("Root σ", ", ".join(f"{v:.2f}" for v in sigma)+" m"),
                        ("Mode", baseline["mode"][index]), ("UWB decision", "accepted" if baseline["accepted"][index] else "rejected"),
                        ("Camera", "root-following" if root_follow else "world-fixed")]
                y = 125
                for label, value in rows:
                    draw.text((944,y),label,font=small_font,fill="#9ab0bf");y+=18
                    lines=[]; current=""
                    for word in str(value).split():
                        candidate=(current+" "+word).strip()
                        if draw.textlength(candidate,font=body_font)>275 and current: lines.append(current);current=word
                        else: current=candidate
                    lines.append(current)
                    for line in lines[:3]: draw.text((944,y),line,font=body_font,fill="#eef6fb");y+=22
                    y+=5
                warning = "FRAME-QUARANTINED DIAGNOSTIC" if key in ("B1","B2","B4") else baseline["status"]
                draw.rectangle((0, 665, width, 720), fill="#351b20" if "QUARANTINED" in warning else "#122332")
                draw.text((34, 681), warning, font=body_font, fill="#ffb1b3" if "QUARANTINED" in warning else "#eef6fb")
                process.stdin.write(image.tobytes())
    finally:
        if process.stdin:
            process.stdin.close()
    if process.wait() != 0:
        raise RuntimeError("ffmpeg failed while writing Root-R3 review")
    return path


def build_review_artifacts(output: Path) -> tuple[Path, Path]:
    payload = build_payload(output)
    return write_viewer(output, payload), write_review_mp4(output, payload)
