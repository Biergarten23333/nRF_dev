#!/usr/bin/env python3
"""Build a synchronized, fixed-world HXX fusion A/B viewer.

The inputs are already-materialized fusion viewers.  This exporter only reads
their trajectory payloads and renders both sides with one elapsed-time
playhead, one world camera, and one UWB volume.  It deliberately contains no
pose, root, contact, IK, or floor correction.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from tools.build_c2_h01_imu_dead_reckoning import _extract_data


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_panel(path: Path, action: str) -> tuple[dict[str, Any], dict[str, Any]]:
    data, _start, _stop = _extract_data(path.read_text(encoding="utf-8"))
    episode = next(row for row in data["episodes"] if row["id"] == action)
    scene = data["worldMotionDiagnostic"]["uwb_scene"]
    panel = {
        "jointNames": data["jointNames"],
        "lines": data["lines"],
        "viewGauge": data["viewGauge"],
        "episode": episode,
        "uwbScene": scene,
    }
    return panel, {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "frame_count": len(episode["frames"]),
        "start_time_s": episode["time"][0],
        "stop_time_s": episode["time"][-1],
        "pose_keyframe_rate_hz": float(
            1.0 / np.median(np.diff(np.asarray(episode["time"], dtype=float)))
        ),
    }


def _audit(
    left: dict[str, Any],
    right: dict[str, Any],
    left_source: dict[str, Any],
    right_source: dict[str, Any],
) -> dict[str, Any]:
    if left["jointNames"] != right["jointNames"] or left["lines"] != right["lines"]:
        raise RuntimeError("A/B inputs do not share one display skeleton")
    if left["episode"]["id"] != right["episode"]["id"]:
        raise RuntimeError("A/B action mismatch")
    left_scene = left["uwbScene"]
    right_scene = right["uwbScene"]
    for key in ("anchors_output_m", "anchor_bounds_output_m", "padded_bounds_output_m"):
        if not np.allclose(
            np.asarray(list(left_scene[key].values()) if key == "anchors_output_m" else [left_scene[key]["min"], left_scene[key]["max"]], dtype=float),
            np.asarray(list(right_scene[key].values()) if key == "anchors_output_m" else [right_scene[key]["min"], right_scene[key]["max"]], dtype=float),
            atol=1e-9,
            rtol=0.0,
        ):
            raise RuntimeError(f"A/B UWB scene mismatch: {key}")
    for side, panel in (("left", left), ("right", right)):
        times = np.asarray(panel["episode"]["time"], dtype=float)
        if len(times) < 2 or times[0] != 0.0 or not np.all(np.diff(times) > 0.0):
            raise RuntimeError(f"{side} timeline is invalid")
    return {
        "schema": "biospur-c2-hxx-fusion-fixed-world-ab-audit-v1",
        "action": left["episode"]["id"],
        "same_elapsed_time_playhead": True,
        "same_world_camera": True,
        "same_uwb_volume": True,
        "viewer_pose_root_contact_or_floor_correction": False,
        "viewer_interpolation": "DISPLAY_ONLY_CARTESIAN_BETWEEN_EXISTING_KEYFRAMES",
        "left": left_source,
        "right": right_source,
        "common_duration_s": min(
            float(left["episode"]["time"][-1]),
            float(right["episode"]["time"][-1]),
        ),
    }


def _html(
    left: dict[str, Any],
    right: dict[str, Any],
    audit: dict[str, Any],
    *,
    left_label: str,
    right_label: str,
    left_drift: str,
    right_drift: str,
) -> str:
    encoded_left = json.dumps(left, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    encoded_right = json.dumps(right, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    encoded_audit = json.dumps(audit, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    labels = json.dumps(
        {
            "left": left_label,
            "right": right_label,
            "leftDrift": left_drift,
            "rightDrift": right_drift,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur C2 · H02 触地融合固定世界 A/B</title>
<style>
:root{{color-scheme:dark;font-family:"Noto Sans CJK SC","Noto Sans SC",system-ui,sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:#0e1318;color:#eef2f6;overflow:hidden}}
#app{{height:100vh;display:grid;grid-template-rows:auto 1fr auto}}header,footer{{background:#151b21;border-color:#39434e}}
header{{padding:9px 12px;border-bottom:1px solid;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
h1{{font-size:17px;margin:0 10px 0 0}}button,select{{font:inherit;color:#edf2f7;background:#202832;border:1px solid #4d5a68;border-radius:5px;padding:6px 9px;cursor:pointer}}
button[aria-pressed=true]{{border:2px solid #3e9ae8;color:#7cc2ff;padding:5px 8px}}#camera{{color:#77c4ff;border:1px solid #398bd4;border-radius:5px;padding:6px 9px;font-weight:700}}
#stage{{min-height:0;position:relative}}canvas{{display:block;width:100%;height:100%;touch-action:none;cursor:grab}}canvas.dragging{{cursor:grabbing}}
#notice{{position:absolute;top:8px;left:50%;transform:translateX(-50%);font-size:12px;color:#ffc566;text-align:center;pointer-events:none;white-space:nowrap}}
footer{{padding:8px 12px 10px;border-top:1px solid;display:grid;grid-template-columns:auto 1fr auto;gap:11px;align-items:center}}#scrub{{width:100%}}.legend{{font-size:12px;color:#b7c0ca;white-space:nowrap}}
@media(max-width:850px){{h1{{width:100%}}footer{{grid-template-columns:auto 1fr}}.legend{{grid-column:1/-1;white-space:normal}}}}
</style>
<div id="app"><header><h1>BioSpur C2 · H02 触地融合固定世界 A/B</h1><button id="play">播放</button><select id="speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option></select><span>同步相机：</span><button class="view" data-view="front">前方</button><button class="view" data-view="oblique" aria-pressed="true">斜前方</button><button class="view" data-view="rear">后方</button><button class="view" data-view="side">右侧</button><button class="view" data-view="top">上方</button><span id="camera">同一相机：斜前方</span></header>
<div id="stage"><canvas id="canvas"></canvas><div id="notice">只显示既有计算轨迹：无显示端 IK、脚位固定、root 修复或逐帧贴地</div></div>
<footer><span id="time">0.000 s</span><input id="scrub" type="range" min="0" max="1" step="1"><span class="legend">同一时刻 · 同一世界相机 · 同一 A–H UWB 体积 · 拖动/滚轮同步</span></footer></div>
<script>
const LEFT={encoded_left},RIGHT={encoded_right},AUDIT={encoded_audit},LABELS={labels};
const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d'),playButton=document.getElementById('play'),speed=document.getElementById('speed'),scrub=document.getElementById('scrub'),timeLabel=document.getElementById('time'),cameraLabel=document.getElementById('camera');
const duration=AUDIT.common_duration_s,scene=LEFT.uwbScene,lo=scene.padded_bounds_output_m.min,hi=scene.padded_bounds_output_m.max,center=lo.map((v,i)=>(v+hi[i])/2);
const EDGES=[[0,1],[1,2],[2,3],[3,0],[4,5],[5,6],[6,7],[7,4],[0,4],[1,5],[2,6],[3,7]];
let playhead=0,playing=false,lastTick=0,yaw=RIGHT.viewGauge.frontYawRad-Math.PI/4,pitch=25*Math.PI/180,zoom=1,drag=null,viewMode='oblique';
function corners(b){{const a=b.min,z=b.max;return[[a[0],a[1],a[2]],[z[0],a[1],a[2]],[z[0],z[1],a[2]],[a[0],z[1],a[2]],[a[0],a[1],z[2]],[z[0],a[1],z[2]],[z[0],z[1],z[2]],[a[0],z[1],z[2]]]}}
function rotated(p){{const x=p[0]-center[0],y=p[1]-center[1],z=p[2]-center[2],cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch),rx=cy*x-sy*y,ry=sy*x+cy*y;return[rx,sp*ry+cp*z,cp*ry-sp*z]}}
function panelScale(w,h){{const q=corners(scene.padded_bounds_output_m).map(rotated),xs=q.map(p=>p[0]),ys=q.map(p=>p[1]);return .84*Math.min(w/(Math.max(...xs)-Math.min(...xs)),h/(Math.max(...ys)-Math.min(...ys)))*zoom}}
function project(p,x0,w,h){{const q=rotated(p),s=panelScale(w,h);return[x0+w/2+q[0]*s,h/2-q[1]*s,q[2]]}}
function bracket(times,t){{if(t<=times[0])return[0,0,0];const last=times.length-1;if(t>=times[last])return[last,last,0];let a=0,b=last;while(b-a>1){{const m=(a+b)>>1;if(times[m]<=t)a=m;else b=m}}return[a,b,(t-times[a])/(times[b]-times[a])]}}
function lerp(a,b,u){{return a.map((v,i)=>Number(v)+(Number(b[i])-Number(v))*u)}}
function frame(panel){{const e=panel.episode,[a,b,u]=bracket(e.time,playhead),fa=e.frames[a],fb=e.frames[b],rows=[];for(let i=0;i<fa.length;i+=3)rows.push([fa[i]+(fb[i]-fa[i])*u,fa[i+1]+(fb[i+1]-fa[i+1])*u,fa[i+2]+(fb[i+2]-fa[i+2])*u]);return{{rows,a,b,u}}}}
function tagState(panel,a,b,u){{const e=panel.episode;if(!e.uwbTagProxyPositions)return null;const p=e.uwbTagProxyPositions[a].map((q,i)=>lerp(q,e.uwbTagProxyPositions[b][i],u)),trusted=e.uwbTagProxyTrusted?e.uwbTagProxyTrusted[a]:p.map(()=>true);return{{p,trusted,names:e.uwbTagProxyNodeNames}}}}
function drawLine(a,b,x0,w,h){{const p=project(a,x0,w,h),q=project(b,x0,w,h);ctx.beginPath();ctx.moveTo(p[0],p[1]);ctx.lineTo(q[0],q[1]);ctx.stroke()}}
function drawBox(bounds,x0,w,h,color,width,dash){{const c=corners(bounds);ctx.strokeStyle=color;ctx.lineWidth=width;ctx.setLineDash(dash);for(const [a,b] of EDGES)drawLine(c[a],c[b],x0,w,h);ctx.setLineDash([])}}
function drawPanel(panel,x0,w,h,label,drift){{ctx.fillStyle='#0f1419';ctx.fillRect(x0,0,w,h);drawBox(scene.padded_bounds_output_m,x0,w,h,'rgba(205,214,223,.68)',1.5,[]);drawBox(scene.anchor_bounds_output_m,x0,w,h,'rgba(73,163,255,.78)',1.3,[7,5]);
  ctx.font='bold 11px sans-serif';for(const [name,q] of Object.entries(scene.anchors_output_m)){{const p=project(q,x0,w,h);ctx.fillStyle='#ffd166';ctx.beginPath();ctx.arc(p[0],p[1],3.5,0,Math.PI*2);ctx.fill();ctx.fillText(name,p[0]+5,p[1]-4)}}
  const z0=scene.anchors_output_m.A,z1=[z0[0],z0[1],z0[2]+.7];ctx.strokeStyle='#4ade80';ctx.lineWidth=2;drawLine(z0,z1,x0,w,h);const zp=project(z1,x0,w,h);ctx.fillStyle='#4ade80';ctx.fillText('世界 Z ↑',zp[0]+5,zp[1]-4);
  const f=frame(panel),pts=f.rows.map(q=>project(q,x0,w,h)),lines=panel.lines.map(row=>[...row,(pts[row[0]][2]+pts[row[1]][2])/2]).sort((a,b)=>a[3]-b[3]);ctx.lineCap='round';ctx.lineJoin='round';for(const [a,b,side] of lines){{ctx.strokeStyle=side==='left'?'#50a8f5':side==='right'?'#ff9b45':'#edf2f7';ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(pts[a][0],pts[a][1]);ctx.lineTo(pts[b][0],pts[b][1]);ctx.stroke()}}ctx.fillStyle='#e3e8ed';for(const p of pts){{ctx.beginPath();ctx.arc(p[0],p[1],2.5,0,Math.PI*2);ctx.fill()}}
  const tags=tagState(panel,f.a,f.b,f.u);if(tags)tags.p.forEach((q,i)=>{{const p=project(q,x0,w,h);ctx.fillStyle=tags.trusted[i]?'#22c55e':'#f472b6';ctx.beginPath();ctx.arc(p[0],p[1],3.2,0,Math.PI*2);ctx.fill()}});
  ctx.textAlign='center';ctx.fillStyle='#eef2f6';ctx.font='700 16px sans-serif';ctx.fillText(label,x0+w/2,27);ctx.fillStyle='#ffcc70';ctx.font='12px sans-serif';ctx.fillText(drift,x0+w/2,47);ctx.fillStyle='#aeb9c4';ctx.fillText(`关键帧 ${{f.a+1}}/${{panel.episode.frames.length}} · t=${{playhead.toFixed(3)}}s`,x0+w/2,h-15);ctx.textAlign='start';}}
function draw(){{const r=canvas.getBoundingClientRect(),w=r.width,h=r.height,half=w/2;ctx.clearRect(0,0,w,h);drawPanel(LEFT,0,half,h,LABELS.left,LABELS.leftDrift);drawPanel(RIGHT,half,half,h,LABELS.right,LABELS.rightDrift);ctx.strokeStyle='#687581';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(half,0);ctx.lineTo(half,h);ctx.stroke();timeLabel.textContent=playhead.toFixed(3)+' s';scrub.value=String(Math.round(playhead*1000))}}
function setView(v){{viewMode=v;const front=RIGHT.viewGauge.frontYawRad;if(v==='front'){{yaw=front;pitch=0}}else if(v==='oblique'){{yaw=front-Math.PI/4;pitch=25*Math.PI/180}}else if(v==='rear'){{yaw=front+Math.PI;pitch=0}}else if(v==='side'){{yaw=front-Math.PI/2;pitch=0}}else{{yaw=front;pitch=Math.PI/2}}for(const b of document.querySelectorAll('.view'))b.setAttribute('aria-pressed',String(b.dataset.view===v));cameraLabel.textContent='同一相机：'+({{front:'前方',oblique:'斜前方',rear:'后方',side:'右侧',top:'上方'}}[v]||'自由旋转');draw()}}
function resize(){{const r=canvas.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);canvas.width=Math.max(1,Math.round(r.width*d));canvas.height=Math.max(1,Math.round(r.height*d));ctx.setTransform(d,0,0,d,0,0);draw()}}
function animate(t){{if(!lastTick)lastTick=t;const dt=(t-lastTick)/1000;lastTick=t;if(playing){{playhead+=dt*Number(speed.value);if(playhead>duration)playhead=0;draw()}}requestAnimationFrame(animate)}}
scrub.max=String(Math.round(duration*1000));scrub.value='0';playButton.addEventListener('click',()=>{{playing=!playing;playButton.textContent=playing?'暂停':'播放'}});scrub.addEventListener('input',()=>{{playhead=Number(scrub.value)/1000;draw()}});for(const b of document.querySelectorAll('.view'))b.addEventListener('click',()=>setView(b.dataset.view));canvas.addEventListener('pointerdown',e=>{{drag=[e.clientX,e.clientY,yaw,pitch];canvas.setPointerCapture(e.pointerId);canvas.classList.add('dragging')}});canvas.addEventListener('pointermove',e=>{{if(!drag)return;yaw=drag[2]+(e.clientX-drag[0])*.009;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,drag[3]-(e.clientY-drag[1])*.009));for(const b of document.querySelectorAll('.view'))b.setAttribute('aria-pressed','false');cameraLabel.textContent='同一相机：自由旋转';draw()}});canvas.addEventListener('pointerup',()=>{{drag=null;canvas.classList.remove('dragging')}});canvas.addEventListener('wheel',e=>{{e.preventDefault();zoom=Math.max(.55,Math.min(2.5,zoom*Math.exp(-e.deltaY*.001)));draw()}},{{passive:false}});new ResizeObserver(resize).observe(document.getElementById('stage'));setView('oblique');requestAnimationFrame(animate);
</script>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--left-viewer", type=Path, required=True)
    parser.add_argument("--right-viewer", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--action", default="H02_golf")
    parser.add_argument("--left-label", default="左：旧 accepted contact-v5")
    parser.add_argument("--right-label", default="右：native200 analytic IK + 统一融合")
    parser.add_argument("--left-drift", required=True)
    parser.add_argument("--right-drift", required=True)
    args = parser.parse_args()
    paths = [args.left_viewer.resolve(), args.right_viewer.resolve(), args.output.resolve()]
    if any(path != ROOT and ROOT not in path.parents for path in paths):
        raise SystemExit("all paths must remain in canonical Fusion_Part")
    left, left_source = _load_panel(paths[0], args.action)
    right, right_source = _load_panel(paths[1], args.action)
    audit = _audit(left, right, left_source, right_source)
    output = paths[2]
    output.mkdir(parents=False, exist_ok=False)
    html_path = output / "c2_H02_contact_v5_vs_unified_fixed_world_ab.html"
    html_path.write_text(
        _html(
            left,
            right,
            audit,
            left_label=args.left_label,
            right_label=args.right_label,
            left_drift=args.left_drift,
            right_drift=args.right_drift,
        ),
        encoding="utf-8",
    )
    manifest = {
        **audit,
        "status": "DISPLAY_DIAGNOSTIC_COMPLETE_NOT_SCIENTIFIC_PASS",
        "output": str(html_path.relative_to(ROOT)),
        "output_sha256": _sha256(html_path),
        "labels": {
            "left": args.left_label,
            "right": args.right_label,
            "left_drift": args.left_drift,
            "right_drift": args.right_drift,
        },
    }
    manifest_path = output / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"html": str(html_path), "manifest": str(manifest_path), "html_sha256": manifest["output_sha256"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
