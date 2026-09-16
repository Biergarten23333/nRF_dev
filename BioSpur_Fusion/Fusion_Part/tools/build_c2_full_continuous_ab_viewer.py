#!/usr/bin/env python3
"""Render continuous A/B solver outputs without changing their state or gauge.

NPZ arrays: time_s[N], roots_a/roots_b[N,3], joints_relative[N,J,3],
optional joints_relative_b[N,J,3] (otherwise both panels use joints_relative),
joint_names[J], anchors_m[8,3], optional anchor_names[8]. All positions must
already use the same UWB world frame, metres, Z up. Actions JSON is a list of
{id, label, start_s, end_s} records on the absolute time_s clock. The exporter
only subsamples for display; it never recomputes poses or root trajectories.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

import numpy as np


LINES = (
    ("pelvis_center", "shoulder_mid", "center"),
    ("shoulder_left", "shoulder_right", "center"),
    ("hip_left", "hip_right", "center"),
    ("shoulder_left", "elbow_left", "left"),
    ("elbow_left", "wrist_left", "left"),
    ("shoulder_right", "elbow_right", "right"),
    ("elbow_right", "wrist_right", "right"),
    ("hip_left", "knee_left", "left"),
    ("knee_left", "ankle_left", "left"),
    ("hip_right", "knee_right", "right"),
    ("knee_right", "ankle_right", "right"),
)


def _pack(array: np.ndarray, dtype: str = "<f4") -> str:
    return base64.b64encode(np.asarray(array, dtype=dtype).tobytes()).decode("ascii")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def build(source: Path, actions_path: Path, output: Path, *, display_hz: float = 60,
          summary_path: Path | None = None) -> dict:
    if not 1 <= display_hz <= 60:
        raise ValueError("display_hz must be between 1 and 60")
    with np.load(source, allow_pickle=False) as data:
        time = np.asarray(data["time_s"], dtype=float)
        a, b = np.asarray(data["roots_a"]), np.asarray(data["roots_b"])
        pose = np.asarray(data["joints_relative"])
        separate_pose_b = "joints_relative_b" in data
        pose_b = np.asarray(data["joints_relative_b"]) if separate_pose_b else pose
        names = data["joint_names"].astype(str).tolist()
        anchor_key = "anchors_world_m" if "anchors_world_m" in data else "anchors_m"
        anchors = np.asarray(data[anchor_key], dtype=float)
        anchor_names = data["anchor_names"].astype(str).tolist() if "anchor_names" in data else list("ABCDEFGH")
    if time.ndim != 1 or len(time) < 2 or not np.all(np.diff(time) > 0):
        raise ValueError("time_s must be a strictly increasing continuous clock")
    if a.shape != (len(time), 3) or b.shape != a.shape or pose.shape != (len(time), len(names), 3):
        raise ValueError("root/pose arrays must share the complete native timeline")
    if pose_b.shape != pose.shape:
        raise ValueError("joints_relative_b must share the complete native pose shape")
    if anchors.shape != (8, 3) or set(anchor_names) != set("ABCDEFGH"):
        raise ValueError("exactly eight identified A–H anchors are required")
    if any(not np.isfinite(row).all() for row in (time, a, b, pose, pose_b, anchors)):
        raise ValueError("nonfinite coordinates must be diagnosed upstream, not hidden by viewer")
    index = {name: i for i, name in enumerate(names)}
    lines = [[index[x], index[y], side] for x, y, side in LINES]
    actions = json.loads(actions_path.read_text())
    if isinstance(actions, dict):
        if "regions" in actions:
            actions = [{"id": row["action_id"], "label": row["action_id"],
                        "start_s": row["start_ns"] * 1e-9,
                        "end_s": row["stop_ns"] * 1e-9}
                       for row in actions["regions"] if row["kind"] == "ACTION"]
        else:
            actions = actions["actions"]
    for action in actions:
        if not (np.isfinite(action["start_s"]) and np.isfinite(action["end_s"]) and action["start_s"] <= action["end_s"]):
            raise ValueError("invalid action interval")
    actions = sorted(actions, key=lambda row: row["start_s"])
    # Select real solver samples only. No resampling, smoothing or interpolation.
    step = max(1, int(np.ceil(1 / (display_hz * np.median(np.diff(time))))))
    selected = np.unique(np.r_[np.arange(0, len(time), step), len(time) - 1])
    origin = float(time[0])
    native_hz = float(1 / np.median(np.diff(time)))
    summary = json.loads(summary_path.read_text()) if summary_path else {}
    labels = summary.get("comparison_labels", ["Pure IMU", "IMU + UWB"])
    if not isinstance(labels, list) or len(labels) != 2 or not all(isinstance(v, str) and v for v in labels):
        raise ValueError("comparison_labels requires two nonempty strings")
    same_pose = bool(np.array_equal(pose, pose_b))
    policy = summary.get("viewer_policy", (
        "固定初始标定 + hinge FK；十节点原始 UWB 更新共同 root，非逐关节 UWB IK。"
        if not separate_pose_b else "分别显示输入的 A/B 相对骨架；更新机制以输入诊断报告为准。"))
    if not isinstance(policy, str) or not policy.strip():
        raise ValueError("viewer_policy requires a nonempty string")
    audit = {
        "schema": "biospur-full-continuous-ab-viewer-v1", "source": str(source.resolve()),
        "source_sha256": _sha256(source), "native_samples": len(time),
        "native_hz": native_hz, "display_samples": len(selected),
        "display_hz": native_hz / step, "clock_origin_s": origin,
        "duration_s": float(time[-1] - time[0]), "viewer_state_correction": False,
        "display_interpolation": "NONE_NEAREST_PRECEDING_DISPLAY_SAMPLE",
        "coordinate_frame": "direct UWB world, metres, Z up; identical for skeleton and anchors",
        "fixed_volume_derived_from": "A–H anchors only, padded 0.5 m",
        "anchor_volume_edges": ["AB", "BC", "CD", "DA", "EF", "FG", "GH", "HE", "AE", "BF", "CG", "DH"],
        "action_selection": "seek only; no solver state exists in viewer",
        "diagnostic_scope": "offline measurement-time-ordered diagnostic, not online availability or latency benchmark; " + policy,
        "viewer_policy": policy,
        "separate_pose_b_supplied": separate_pose_b,
        "relative_pose_a_b_exactly_equal": same_pose,
        "origin_a_m": a[0].tolist(), "origin_b_m": b[0].tolist(),
        "last_a_m": a[-1].tolist(), "last_b_m": b[-1].tolist(),
        "input_summary": summary,
        "comparison_labels": labels,
    }
    payload = {"time": _pack(time[selected] - origin, "<f8"),
               "a": _pack(a[selected]), "b": _pack(b[selected]),
               "pose": _pack(pose[selected]), "names": names, "lines": lines,
               "anchors": dict(zip(anchor_names, anchors.tolist())),
               "lo": (anchors.min(axis=0) - .5).tolist(),
               "hi": (anchors.max(axis=0) + .5).tolist(), "audit": audit,
               "actions": [dict(row, start_s=float(row["start_s"] - origin),
                                end_s=float(row["end_s"] - origin)) for row in actions]}
    if separate_pose_b:
        payload["pose_b"] = _pack(pose_b[selected])
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).replace("</", "<\\/")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(HTML.replace("__PAYLOAD__", encoded), encoding="utf-8")
    output.with_suffix(".audit.json").write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n")
    return audit


HTML = r'''<!doctype html>
<html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>C2 全程连续 · Pure IMU / IMU+UWB</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif}*{box-sizing:border-box}body{margin:0;background:#10161d;color:#eaf0f6}header{padding:10px 14px;background:#19222d;display:flex;align-items:center;gap:9px;flex-wrap:wrap}h1{font-size:17px;margin:0 12px 0 0}button,select{font:inherit;background:#253342;color:inherit;border:1px solid #526172;border-radius:4px;padding:6px 9px}button{cursor:pointer}#stage{height:calc(100vh - 320px);min-height:360px}canvas{display:block;width:100%;height:100%;touch-action:none}#status,#policy{padding:7px 14px;color:#ffd089;font-size:13px}#controls{display:flex;align-items:center;gap:12px;padding:8px 14px}#scrub{flex:1;min-width:70px}#clock{font-variant-numeric:tabular-nums;min-width:128px}#plot{height:130px}details{margin:10px 14px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px}#policy{color:#afbfce;border-top:1px solid #475461}label{font-size:13px}@media(max-width:650px){#stage{min-height:420px}h1{width:100%}#status{min-height:48px}#controls{flex-wrap:wrap}}
</style>
<header><h1>C2 · 全程连续 A/B</h1><button id="play">播放</button><label>速度 <select id="speed"><option>.5</option><option selected>1</option><option>2</option><option>5</option><option>10</option></select></label><label>动作跳转 <select id="action"><option value="">完整时间线</option></select></label><label>同步相机 <select id="view"><option value="oblique">斜前</option><option value="front">前方</option><option value="rear">后方</option><option value="side">侧面</option><option value="top">上方</option></select></label><button id="fixed">固定 A–H 体积</button><button id="out">缩小 2×</button></header>
<div style="padding:6px 14px"><label>三维根轨迹 <select id="trail"><option value="history">起点至当前</option><option value="full">全程轨迹</option><option value="off">隐藏</option></select></label> · 左橙 / 右青 · 圆点为起点；轨迹仅抽样绘制，不改数据</div>
<div id="policy"></div><div id="result" style="padding:6px 14px;color:#ffad63"></div><div id="stage"><canvas id="canvas" aria-label="同步的左右三维骨架；左纯 IMU，右 IMU+UWB"></canvas></div><div id="status" aria-live="polite"></div>
<div id="controls"><span id="clock"></span><input id="scrub" aria-label="全程共同时间轴" type="range" min="0" step="0.001"><span id="actionLabel"></span></div>
<canvas id="plot" aria-label="全程根位置相对初始值位移，单位米；点击跳转"></canvas>
<details><summary>坐标、初始原点与诊断证据（非验证通过声明）</summary><pre id="summary"></pre></details>
<script>
'use strict';const DATA=__PAYLOAD__;
function unpack(s,Type){const raw=atob(s),bytes=new Uint8Array(raw.length);for(let i=0;i<raw.length;i++)bytes[i]=raw.charCodeAt(i);return new Type(bytes.buffer)}
const times=unpack(DATA.time,Float64Array),A=unpack(DATA.a,Float32Array),B=unpack(DATA.b,Float32Array),POSE=unpack(DATA.pose,Float32Array),POSE_B=DATA.pose_b?unpack(DATA.pose_b,Float32Array):POSE,J=DATA.names.length,N=times.length;
const $=id=>document.getElementById(id),canvas=$('canvas'),ctx=canvas.getContext('2d'),plot=$('plot'),pc=plot.getContext('2d'),lo=DATA.lo,hi=DATA.hi,center=lo.map((v,i)=>(v+hi[i])/2),duration=times[N-1];
let t=0,frameIndex=0,playing=false,yaw=-Math.PI/4,pitch=.43,zoom=1,drag=null,last=0,lastPaint=0;
const edges=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
const corners=[[lo[0],lo[1],lo[2]],[hi[0],lo[1],lo[2]],[hi[0],hi[1],lo[2]],[lo[0],hi[1],lo[2]],[lo[0],lo[1],hi[2]],[hi[0],lo[1],hi[2]],[hi[0],hi[1],hi[2]],[lo[0],hi[1],hi[2]]];
function rotate(p){const x=p[0]-center[0],y=p[1]-center[1],z=p[2]-center[2],rx=Math.cos(yaw)*x-Math.sin(yaw)*y,ry=Math.sin(yaw)*x+Math.cos(yaw)*y;return[rx,Math.sin(pitch)*ry+Math.cos(pitch)*z,Math.cos(pitch)*ry-Math.sin(pitch)*z]}
function projector(x,w,h){const c=corners.map(rotate),extent=k=>Math.max(...c.map(p=>p[k]))-Math.min(...c.map(p=>p[k])),scale=.78*Math.min(w/extent(0),(h-70)/extent(1))*zoom;return p=>{const q=rotate(p);return[x+w/2+q[0]*scale,h/2+15-q[1]*scale,q[2]]}}
function root(data,i){return Array.from(data.subarray(i*3,i*3+3))}
function indexAt(time){let a=0,b=N;while(a+1<b){const m=(a+b)>>1;if(times[m]<=time)a=m;else b=m}return a}
function drawTrail(data,project){
if($('trail').value==='off')return;
const end=$('trail').value==='full'?N-1:frameIndex,stride=Math.max(1,Math.ceil(end/2500));
ctx.save();ctx.strokeStyle=data===A?'#ffad63':'#3cddd5';ctx.fillStyle=ctx.strokeStyle;ctx.lineWidth=2;ctx.globalAlpha=.85;ctx.beginPath();
for(let i=0;i<=end;i+=stride){const p=project(root(data,i));if(i===0)ctx.moveTo(p[0],p[1]);else ctx.lineTo(p[0],p[1])}
const last=project(root(data,end));ctx.lineTo(last[0],last[1]);ctx.stroke();
const start=project(root(data,0));ctx.beginPath();ctx.arc(start[0],start[1],4,0,Math.PI*2);ctx.fill();ctx.restore();
}
function panel(data,pose,x,w,h,title){ctx.save();ctx.beginPath();ctx.rect(x,0,w,h);ctx.clip();const project=projector(x,w,h),r=root(data,frameIndex),rows=Array.from({length:J},(_,j)=>[0,1,2].map(k=>pose[(frameIndex*J+j)*3+k]+r[k]));
ctx.strokeStyle='#66788c';ctx.lineWidth=1;for(const [a,b]of edges){const p=project(corners[a]),q=project(corners[b]);ctx.beginPath();ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke()}
ctx.strokeStyle='#62acfa';ctx.lineWidth=1.4;ctx.setLineDash([7,5]);for(const edge of DATA.audit.anchor_volume_edges){const p=project(DATA.anchors[edge[0]]),q=project(DATA.anchors[edge[1]]);ctx.beginPath();ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke()}ctx.setLineDash([]);
drawTrail(data,project);
ctx.font='12px system-ui';for(const [name,a]of Object.entries(DATA.anchors)){const p=project(a);ctx.fillStyle='#ffd166';ctx.beginPath();ctx.arc(p[0],p[1],4,0,7);ctx.fill();ctx.fillText(name,p[0]+6,p[1]-5)}
const origin=DATA.anchors.A;for(const [axis,col]of [[0,'#ff8b83'],[1,'#7bbaff'],[2,'#74e4ae']]){const end=origin.map((v,k)=>v+(k===axis?.6:0)),p=project(origin),q=project(end);ctx.strokeStyle=col;ctx.beginPath();ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke();ctx.fillStyle=col;ctx.fillText('XYZ'[axis],q[0]+5,q[1])}
const points=rows.map(project),lines=DATA.lines.map(l=>[...l,(points[l[0]][2]+points[l[1]][2])/2]).sort((a,b)=>a[3]-b[3]);ctx.lineCap='round';ctx.lineWidth=4;for(const [a,b,side]of lines){ctx.strokeStyle=side==='left'?'#64baff':side==='right'?'#ffad63':'#f0f4f8';ctx.beginPath();ctx.moveTo(points[a][0],points[a][1]);ctx.lineTo(points[b][0],points[b][1]);ctx.stroke()}ctx.fillStyle='#eaf0f6';for(const p of points){ctx.beginPath();ctx.arc(p[0],p[1],2.5,0,7);ctx.fill()}
ctx.textAlign='center';ctx.font='bold 17px system-ui';ctx.fillText(title,x+w/2,25);ctx.font='12px system-ui';ctx.fillStyle='#bed0e0';ctx.fillText('root [m] '+r.map(v=>v.toFixed(2)).join(', '),x+w/2,46);ctx.restore();
return{outside:r.some((v,k)=>v<lo[k]||v>hi[k]),offscreen:points.every(p=>p[0]<x||p[0]>x+w||p[1]<55||p[1]>h)};}
const displacement=data=>Array.from({length:N},(_,i)=>Math.hypot(...root(data,i).map((v,k)=>v-data[k]))),DA=displacement(A),DB=displacement(B);let ymax=1;for(const values of[DA,DB])for(const v of values)ymax=Math.max(ymax,v);
$('result').textContent='全程末端距起点：A '+DA[N-1].toFixed(2)+' m / B '+DB[N-1].toFixed(2)+' m。诊断结果，不代表抗漂移通过；离屏时可点击缩小。';
function drawPlot(){const w=plot.clientWidth,h=plot.clientHeight,l=68,r=w-18,top=23,bottom=h-29;pc.clearRect(0,0,w,h);pc.font='12px system-ui';pc.fillStyle='#d8e4ef';pc.fillText('根位移 |p(t)−p(0)| [m] · 左橙 / 右青 · 点击时间跳转',l,15);pc.strokeStyle='#586a7b';pc.strokeRect(l,top,r-l,bottom-top);for(const f of[0,.5,1]){pc.fillText((f*ymax).toPrecision(3),5,bottom-f*(bottom-top));pc.fillText((f*duration).toFixed(1)+' s',Math.max(l,Math.min(r-45,l+f*(r-l)-20)),h-7)}for(const [values,color]of[[DA,'#ffad63'],[DB,'#64baff']]){pc.strokeStyle=color;pc.lineWidth=1.5;pc.beginPath();const stride=Math.max(1,Math.floor(N/(w*2)));for(let i=0;i<N;i+=stride){const x=l+times[i]/duration*(r-l),y=bottom-values[i]/ymax*(bottom-top);i?pc.lineTo(x,y):pc.moveTo(x,y)}pc.stroke()}pc.strokeStyle='#f4f7fa';const x=l+t/duration*(r-l);pc.beginPath();pc.moveTo(x,top);pc.lineTo(x,bottom);pc.stroke()}
function actionAt(){const hit=DATA.actions.find(a=>t>=a.start_s&&t<=a.end_s);if(hit)return hit.label||hit.id;const prev=DATA.actions.filter(a=>a.end_s<t).at(-1),next=DATA.actions.find(a=>a.start_s>t);return '动作间过渡：'+(prev?.id||'会话开始')+' → '+(next?.id||'会话结束')}
function draw(){frameIndex=indexAt(t);const w=canvas.clientWidth,h=canvas.clientHeight;ctx.clearRect(0,0,w,h);const a=panel(A,POSE,0,w/2,h,'左：'+DATA.audit.comparison_labels[0]),b=panel(B,POSE_B,w/2,w/2,h,'右：'+DATA.audit.comparison_labels[1]);ctx.strokeStyle='#526578';ctx.beginPath();ctx.moveTo(w/2,0);ctx.lineTo(w/2,h);ctx.stroke();$('clock').textContent=t.toFixed(3)+' / '+duration.toFixed(1)+' s';$('scrub').value=t;$('actionLabel').textContent=actionAt();$('status').textContent=[a.outside?'左根位置已离开加宽显示框':'左根位置在加宽显示框内',b.outside?'右根位置已离开加宽显示框':'右根位置在加宽显示框内',a.offscreen||b.offscreen?'骨架已离屏；可主动缩小查看，轨迹未裁正':'蓝色虚线为真实 A–H 连边 / 灰框外扩 0.5 m','显示样本 '+(frameIndex+1)+'/'+N].join(' · ');drawPlot()}
function resize(){for(const[c,cx]of[[canvas,ctx],[plot,pc]]){const d=Math.min(devicePixelRatio||1,2);c.width=Math.round(c.clientWidth*d);c.height=Math.round(c.clientHeight*d);cx.setTransform(d,0,0,d,0,0)}draw()}
function seek(value){t=Math.max(0,Math.min(duration,value));draw()}
$('trail').onchange=()=>draw();
for(const a of DATA.actions){const option=document.createElement('option');option.value=a.start_s;option.textContent=a.label||a.id;$('action').appendChild(option)}
$('action').onchange=e=>{if(e.target.value!=='')seek(Number(e.target.value))};$('scrub').max=duration;$('scrub').oninput=e=>seek(Number(e.target.value));$('play').onclick=()=>{if(t===duration)t=0;playing=!playing;$('play').textContent=playing?'暂停':'播放'};
$('view').onchange=e=>{const v=e.target.value;yaw=v==='rear'?Math.PI:v==='side'?Math.PI/2:v==='oblique'?-Math.PI/4:0;pitch=v==='top'?Math.PI/2:v==='oblique'?.43:0;draw()};$('fixed').onclick=()=>{zoom=1;draw()};$('out').onclick=()=>{zoom=Math.max(1e-7,zoom/2);draw()};canvas.onpointerdown=e=>{drag=[e.clientX,e.clientY,yaw,pitch];canvas.setPointerCapture(e.pointerId)};canvas.onpointermove=e=>{if(!drag)return;yaw=drag[2]+(e.clientX-drag[0])*.008;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,drag[3]-(e.clientY-drag[1])*.008));draw()};canvas.onpointerup=canvas.onpointercancel=()=>{drag=null};canvas.addEventListener('wheel',e=>{e.preventDefault();zoom=Math.max(1e-7,Math.min(8,zoom*Math.exp(-e.deltaY*.001)));draw()},{passive:false});plot.onclick=e=>{const r=plot.getBoundingClientRect();seek((e.clientX-r.left-68)/(r.width-86)*duration)};
$('summary').textContent=JSON.stringify(DATA.audit,null,2);$('policy').textContent='离线按测量时间排序诊断，不是在线时延/可用性验证。'+DATA.audit.viewer_policy+' 求解 '+DATA.audit.native_hz.toFixed(1)+' Hz；显示 '+DATA.audit.display_hz.toFixed(1)+' Hz（只取原样本，无插值/平滑）。00–19 共用连续时钟；'+(DATA.audit.relative_pose_a_b_exactly_equal?'A/B 相对骨架完全相同':'A/B 使用各自输入的相对骨架')+'；动作选择只跳转，不重置状态。';
if(DATA.audit.input_summary.role==='FINITE_HORIZON_PUBLICATION_ONLY_NOT_ESTIMATOR_CHANGE')$('policy').textContent+=' 本文件的位置发布采用 '+(DATA.audit.input_summary.release_period_s*1000).toFixed(0)+' ms 有限时域纠正释放；内部滤波后验单独保留，显示层不另行插值。';
if(DATA.audit.input_summary.comparison_note)$('policy').textContent+=' '+DATA.audit.input_summary.comparison_note;
canvas.setAttribute('aria-label','同步的左右三维骨架；左：'+DATA.audit.comparison_labels[0]+'；右：'+DATA.audit.comparison_labels[1]);
function animate(now){const wasPlaying=playing;if(last&&playing){t=Math.min(duration,t+(now-last)/1000*Number($('speed').value));if(t>=duration){playing=false;$('play').textContent='播放'}}last=now;if(wasPlaying&&(now-lastPaint>=1000/60||!playing)){draw();lastPaint=now}requestAnimationFrame(animate)}new ResizeObserver(resize).observe($('stage'));new ResizeObserver(resize).observe(plot);resize();requestAnimationFrame(animate);
</script></html>'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--summary", type=Path)
    parser.add_argument("--display-hz", type=float, default=60)
    args = parser.parse_args()
    audit = build(args.input, args.actions, args.output, display_hz=args.display_hz,
                  summary_path=args.summary)
    print(json.dumps({"output": str(args.output), "native_samples": audit["native_samples"],
                      "display_samples": audit["display_samples"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
