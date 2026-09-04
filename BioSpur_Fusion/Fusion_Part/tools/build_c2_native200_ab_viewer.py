#!/usr/bin/env python3
"""Build a synchronized frozen-vs-native-200-Hz C2 comparison viewer."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from tools.build_c2_avatar_interactive import (
    ROOT,
    _append_frozen_hxx,
    _load_trajectory,
    _payload,
    _sha256,
)


def _comparison_audit(
    left: dict[str, Any], right: dict[str, Any]
) -> dict[str, Any]:
    if left["jointNames"] != right["jointNames"] or left["lines"] != right["lines"]:
        raise RuntimeError("A/B viewers do not share one FK display geometry")
    if len(left["episodes"]) != len(right["episodes"]):
        raise RuntimeError("A/B episode count mismatch")
    episodes: list[dict[str, Any]] = []
    for left_ep, right_ep in zip(left["episodes"], right["episodes"], strict=True):
        if left_ep["id"] != right_ep["id"]:
            raise RuntimeError("A/B episode identity mismatch")
        left_time = left_ep["time"]
        right_time = right_ep["time"]
        if not left_time or not right_time:
            raise RuntimeError(f"empty A/B timeline: {left_ep['id']}")
        if left_time[0] != 0 or right_time[0] != 0:
            raise RuntimeError(f"A/B timeline does not start at zero: {left_ep['id']}")
        if any(b <= a for a, b in zip(left_time, left_time[1:])):
            raise RuntimeError(f"left timeline is not strictly increasing: {left_ep['id']}")
        if any(b <= a for a, b in zip(right_time, right_time[1:])):
            raise RuntimeError(f"right timeline is not strictly increasing: {left_ep['id']}")
        episodes.append(
            {
                "id": left_ep["id"],
                "common_duration_s": min(left_time[-1], right_time[-1]),
                "left_display_frame_count": len(left_time),
                "right_display_frame_count": len(right_time),
            }
        )
    return {
        "same_fk_display_geometry": True,
        "same_elapsed_time_playhead": True,
        "same_world_camera": True,
        "viewer_interpolation": False,
        "episodes": episodes,
    }


def _load_side(
    run_dir: Path,
    hxx_run: Path,
    *,
    target_fps: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    report_path = run_dir / "POSE_RESET_QMT_DIAGNOSTIC.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    trajectory_path = ROOT / report["trajectory"]["path"]
    if _sha256(trajectory_path) != report["trajectory"]["sha256"]:
        raise RuntimeError(f"trajectory hash mismatch: {trajectory_path}")
    trajectory = _load_trajectory(trajectory_path)
    data = _payload(
        trajectory,
        target_fps=target_fps,
        forward_branch_yaw_deg=0,
        global_lateral_mirror=False,
    )
    hxx_source = _append_frozen_hxx(
        data,
        trajectory,
        hxx_run,
        target_fps=target_fps,
    )
    return data, {
        "report": str(report_path.relative_to(ROOT)),
        "report_sha256": _sha256(report_path),
        "trajectory": report["trajectory"],
        "hxx": hxx_source,
    }


def _html(
    left: dict[str, Any],
    right: dict[str, Any],
    provenance: dict[str, Any],
    labels: dict[str, str],
) -> str:
    encoded_left = json.dumps(left, ensure_ascii=False, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    encoded_right = json.dumps(
        right, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    encoded_provenance = json.dumps(
        provenance, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    encoded_labels = json.dumps(
        labels, ensure_ascii=False, separators=(",", ":")
    ).replace("</", "<\\/")
    return f"""<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BioSpur C2 · 同步 FK/IK A/B</title>
<style>
:root{{color-scheme:dark;font-family:"Noto Sans CJK SC","Noto Sans SC",system-ui,sans-serif}}
*{{box-sizing:border-box}}body{{margin:0;background:#101419;color:#edf1f5;overflow:hidden}}
#app{{height:100vh;display:grid;grid-template-rows:auto 1fr auto}}
header{{padding:10px 14px;border-bottom:1px solid #343b44;background:#171b20}}
.top{{display:flex;gap:9px;align-items:center;flex-wrap:wrap}}h1{{font-size:17px;margin:0 8px 0 0}}
select,button,input{{font:inherit}}select,button{{border:1px solid #4c5764;background:#20262d;color:#edf1f5;padding:6px 9px;border-radius:5px}}
button{{cursor:pointer}}button[aria-pressed="true"]{{border:2px solid #398bd4;color:#75b9f5;padding:5px 8px}}
#viewStatus{{border:1px solid #398bd4;border-radius:5px;padding:6px 9px;color:#75b9f5;font-weight:700}}
#stage{{position:relative;min-height:0}}canvas{{display:block;width:100%;height:100%;touch-action:none;cursor:grab}}canvas.dragging{{cursor:grabbing}}
#overlay{{position:absolute;left:14px;top:10px;right:14px;pointer-events:none;text-align:center}}
#action{{font-size:17px;font-weight:700}}#instruction{{font-size:13px;margin-top:3px;color:#c6ced8}}#note{{font-size:12px;margin-top:3px;color:#ffc46b}}
footer{{padding:9px 14px 11px;border-top:1px solid #343b44;background:#171b20;display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center}}
#scrub{{width:100%}}.legend{{font-size:12px;color:#aeb8c3;white-space:nowrap}}
@media (max-width:800px){{h1{{width:100%}}footer{{grid-template-columns:auto 1fr}}.legend{{grid-column:1/-1;white-space:normal}}}}
</style>
<div id="app">
 <header><div class="top">
  <h1>BioSpur C2 · 同步校准 A/B</h1><select id="episode"></select><button id="play">播放</button>
  <select id="speed"><option value="0.5">0.5×</option><option value="1" selected>1×</option><option value="2">2×</option><option value="4">4×</option></select>
  <span>同步相机：</span><button class="view" data-view="front">前方</button><button class="view" data-view="oblique">斜前方</button><button class="view" data-view="rear">后方</button><button class="view" data-view="side">右侧</button><button class="view" data-view="top">上方</button><button class="view" data-view="three" aria-pressed="true">3D</button>
  <span id="viewStatus">同一相机：3D</span>
 </div></header>
 <div id="stage"><canvas id="canvas" aria-label="左右同步三维 A/B 回放"></canvas><div id="overlay"><div id="action"></div><div id="instruction"></div><div id="note"></div></div></div>
 <footer><span id="time">0.000 s</span><input id="scrub" type="range" min="0" max="1" step="1" value="0"><span class="legend">同一时刻 · 同一世界相机 · 拖动/缩放同步 · 无插值/IK/repair/rebase</span></footer>
</div>
<script>
const LEFT={encoded_left},RIGHT={encoded_right},PROV={encoded_provenance},LABELS={encoded_labels};
document.querySelector('.legend').textContent=LABELS.legend;
if(LEFT.episodes.length!==RIGHT.episodes.length)throw new Error('A/B episode count mismatch');
for(let i=0;i<LEFT.episodes.length;i++)if(LEFT.episodes[i].id!==RIGHT.episodes[i].id)throw new Error('A/B episode identity mismatch');
const canvas=document.getElementById('canvas'),ctx=canvas.getContext('2d'),episodeSelect=document.getElementById('episode'),playButton=document.getElementById('play'),speedSelect=document.getElementById('speed'),scrub=document.getElementById('scrub'),timeLabel=document.getElementById('time'),actionLabel=document.getElementById('action'),instruction=document.getElementById('instruction'),note=document.getElementById('note'),viewStatus=document.getElementById('viewStatus');
const VIEW=LEFT.viewGauge,VIEW_LABEL={{front:'前方',oblique:'斜前方',rear:'后方',side:'右侧',top:'上方',three:'3D',manual:'自由旋转'}};
let episodeIndex=0,playhead=0,playing=false,lastTick=0,yaw=VIEW.rearYawRad,pitch=.42,zoom=1,drag=null,viewMode='three';
for(const [i,ep] of LEFT.episodes.entries()){{const o=document.createElement('option');o.value=String(i);o.textContent=`${{String(i+1).padStart(2,'0')}} · ${{ep.id}}`;episodeSelect.appendChild(o)}}
function ep(side){{return side.episodes[episodeIndex]}}function duration(){{return Math.min(ep(LEFT).time.at(-1),ep(RIGHT).time.at(-1))}}
function frameAt(e,t){{let lo=0,hi=e.time.length-1;while(lo<hi){{const mid=Math.ceil((lo+hi)/2);if(e.time[mid]<=t)lo=mid;else hi=mid-1}}return lo}}
function rawFrame(e,index){{const flat=e.frames[index],out=[];for(let i=0;i<LEFT.jointNames.length;i++)out.push([flat[3*i],flat[3*i+1],flat[3*i+2]]);return out}}
function cameraFromLeft(raw){{if(viewMode==='manual')return;const ji=Object.fromEntries(LEFT.jointNames.map((n,i)=>[n,i])),hl=raw[ji.hip_left],hr=raw[ji.hip_right],p=VIEW.outputCoordinateParity??1,dx=p*(hr[0]-hl[0]),dy=p*(hr[1]-hl[1]);if(!Number.isFinite(dx)||!Number.isFinite(dy)||Math.hypot(dx,dy)<1e-9)return;const lateral=Math.atan2(dy,dx),front=-lateral,rear=Math.PI-lateral;if(viewMode==='front'){{yaw=front;pitch=0}}else if(viewMode==='oblique'){{yaw=front-Math.PI/4;pitch=.18}}else if(viewMode==='rear'){{yaw=rear;pitch=0}}else if(viewMode==='side'){{yaw=front-Math.PI/2;pitch=0}}else if(viewMode==='top'){{yaw=front;pitch=Math.PI/2}}else{{yaw=rear;pitch=.42}}}}
function project(p,x0,w,h){{const cy=Math.cos(yaw),sy=Math.sin(yaw),cp=Math.cos(pitch),sp=Math.sin(pitch),rx=cy*p[0]-sy*p[1],ry=sy*p[0]+cy*p[1],pz=sp*ry+cp*p[2],depth=cp*ry-sp*p[2],scale=Math.min(w,h)*.43*zoom;return[x0+w/2+rx*scale,h*.73-pz*scale,depth]}}
const C={{bg:'#111418',grid:'#2b323a',center:'#e8edf2',left:'#50a8f5',right:'#ff9b45',joint:'#d9e0e7'}};
function drawPanel(side,x0,w,h,label,subLabel){{const e=ep(side),fi=frameAt(e,playhead),raw=rawFrame(e,fi),pts=raw.map(p=>project(p,x0,w,h));ctx.fillStyle=C.bg;ctx.fillRect(x0,0,w,h);ctx.strokeStyle=C.grid;ctx.lineWidth=1;for(let i=-2;i<=2;i++){{let a=project([i*.35,-.7,0],x0,w,h),b=project([i*.35,.7,0],x0,w,h);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke();a=project([-.7,i*.35,0],x0,w,h);b=project([.7,i*.35,0],x0,w,h);ctx.beginPath();ctx.moveTo(...a);ctx.lineTo(...b);ctx.stroke()}}const lines=side.lines.map(r=>[...r,(pts[r[0]][2]+pts[r[1]][2])/2]).sort((a,b)=>a[3]-b[3]);ctx.lineCap='round';ctx.lineJoin='round';for(const [a,b,s] of lines){{ctx.strokeStyle=C[s];ctx.lineWidth=4;ctx.beginPath();ctx.moveTo(pts[a][0],pts[a][1]);ctx.lineTo(pts[b][0],pts[b][1]);ctx.stroke()}}ctx.fillStyle=C.joint;for(const p of pts){{ctx.beginPath();ctx.arc(p[0],p[1],3.2,0,Math.PI*2);ctx.fill()}}ctx.textAlign='center';ctx.fillStyle='#edf1f5';ctx.font='700 16px sans-serif';ctx.fillText(label,x0+w/2,30);ctx.fillStyle='#aeb8c3';ctx.font='12px sans-serif';ctx.fillText(subLabel,x0+w/2,49);ctx.fillText(`源帧 ${{e.sourceFrame[fi]}} · 显示帧 ${{fi+1}}/${{e.frames.length}} · t=${{e.time[fi].toFixed(3)}}s`,x0+w/2,h-18);ctx.textAlign='start'}}
function draw(){{const rect=canvas.getBoundingClientRect(),w=rect.width,h=rect.height,half=w/2,leftFrame=rawFrame(ep(LEFT),frameAt(ep(LEFT),playhead));cameraFromLeft(leftFrame);ctx.clearRect(0,0,w,h);drawPanel(LEFT,0,half,h,LABELS.left,LABELS.leftSub);drawPanel(RIGHT,half,half,h,LABELS.right,LABELS.rightSub);ctx.strokeStyle='#65717f';ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(half,0);ctx.lineTo(half,h);ctx.stroke();timeLabel.textContent=`${{playhead.toFixed(3)}} s`;scrub.value=String(Math.round(playhead*1000))}}
function setEpisode(i){{episodeIndex=Number(i);playhead=0;scrub.max=String(Math.round(duration()*1000));scrub.value='0';actionLabel.textContent=ep(LEFT).id;instruction.textContent=ep(LEFT).instruction;note.textContent=LABELS.note;draw()}}
function setView(v){{viewMode=v;for(const b of document.querySelectorAll('.view'))b.setAttribute('aria-pressed',String(b.dataset.view===v));viewStatus.textContent=`同一相机：${{VIEW_LABEL[v]}}`;draw()}}
function resize(){{const r=canvas.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);canvas.width=Math.max(1,Math.round(r.width*d));canvas.height=Math.max(1,Math.round(r.height*d));ctx.setTransform(d,0,0,d,0,0);draw()}}
function animate(t){{if(!lastTick)lastTick=t;const dt=(t-lastTick)/1000;lastTick=t;if(playing){{playhead+=dt*Number(speedSelect.value);if(playhead>duration())playhead=0;draw()}}requestAnimationFrame(animate)}}
episodeSelect.addEventListener('change',()=>setEpisode(episodeSelect.value));playButton.addEventListener('click',()=>{{playing=!playing;playButton.textContent=playing?'暂停':'播放'}});scrub.addEventListener('input',()=>{{playhead=Number(scrub.value)/1000;draw()}});for(const b of document.querySelectorAll('.view'))b.addEventListener('click',()=>setView(b.dataset.view));
canvas.addEventListener('pointerdown',e=>{{drag=[e.clientX,e.clientY,yaw,pitch];canvas.setPointerCapture(e.pointerId);canvas.classList.add('dragging')}});canvas.addEventListener('pointermove',e=>{{if(!drag)return;viewMode='manual';yaw=drag[2]+(e.clientX-drag[0])*.009;pitch=Math.max(-Math.PI/2,Math.min(Math.PI/2,drag[3]-(e.clientY-drag[1])*.009));viewStatus.textContent='同一相机：自由旋转';for(const b of document.querySelectorAll('.view'))b.setAttribute('aria-pressed','false');draw()}});canvas.addEventListener('pointerup',()=>{{drag=null;canvas.classList.remove('dragging')}});canvas.addEventListener('wheel',e=>{{e.preventDefault();zoom=Math.max(.55,Math.min(2.4,zoom*Math.exp(-e.deltaY*.001)));draw()}},{{passive:false}});
const params=new URLSearchParams(location.search),ei=Math.max(0,Math.min(LEFT.episodes.length-1,Number(params.get('episode')??0)|0)),allowed=new Set(['front','oblique','rear','side','top','three']);episodeSelect.value=String(ei);setEpisode(ei);const requestedTime=Number(params.get('time')??0);if(Number.isFinite(requestedTime))playhead=Math.max(0,Math.min(duration(),requestedTime));setView(allowed.has(params.get('view'))?params.get('view'):'three');new ResizeObserver(resize).observe(document.getElementById('stage'));requestAnimationFrame(animate);
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--legacy-run", type=Path, required=True)
    parser.add_argument("--native-run", type=Path, required=True)
    parser.add_argument("--legacy-hxx-run", type=Path, required=True)
    parser.add_argument("--native-hxx-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps", type=float, default=60.0)
    parser.add_argument("--left-label", default="左：冻结旧校准")
    parser.add_argument(
        "--left-sub-label",
        default="00–18：20 Hz轨迹；H01/H02：旧校准作用于200 Hz回放",
    )
    parser.add_argument("--right-label", default="右：原生 200 Hz校准")
    parser.add_argument(
        "--right-sub-label", default="全链路5 ms；显示抽样约60 fps"
    )
    parser.add_argument(
        "--note",
        default="动作、时间、FK几何、坐标手性一致。",
    )
    parser.add_argument(
        "--legend",
        default="同一时刻 · 同一世界相机 · 拖动/缩放同步 · 无显示端插值/rebase",
    )
    args = parser.parse_args()
    paths = [
        args.legacy_run.resolve(),
        args.native_run.resolve(),
        args.legacy_hxx_run.resolve(),
        args.native_hxx_run.resolve(),
        args.output.resolve(),
    ]
    if any(ROOT != path and ROOT not in path.parents for path in paths):
        raise SystemExit("all inputs and output must remain in canonical Fusion_Part")
    if args.fps <= 0:
        raise SystemExit("fps must be positive")
    output = paths[-1]
    output.mkdir(parents=False, exist_ok=False)
    left, left_prov = _load_side(paths[0], paths[2], target_fps=args.fps)
    right, right_prov = _load_side(paths[1], paths[3], target_fps=args.fps)
    comparison_audit = _comparison_audit(left, right)
    provenance = {
        "schema": "biospur-c2-native200-synchronized-ab-v1",
        "comparison": "same action, same elapsed time, same world camera",
        "left": left_prov,
        "right": right_prov,
        "target_fps": args.fps,
        "viewer_interpolation": False,
        "viewer_ik_rebase_retarget_or_repair": False,
        "comparison_audit": comparison_audit,
    }
    labels = {
        "left": args.left_label,
        "leftSub": args.left_sub_label,
        "right": args.right_label,
        "rightSub": args.right_sub_label,
        "note": args.note,
        "legend": args.legend,
    }
    provenance["labels"] = labels
    html_path = output / "c2_calibration_20hz_vs_native200_ab.html"
    html_path.write_text(_html(left, right, provenance, labels), encoding="utf-8")
    manifest = {
        **provenance,
        "html": str(html_path.relative_to(ROOT)),
        "html_sha256": _sha256(html_path),
        "episode_count": len(left["episodes"]),
    }
    manifest_path = output / "MANIFEST.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "html": str(html_path),
                "html_bytes": html_path.stat().st_size,
                "manifest": str(manifest_path),
                "episode_count": len(left["episodes"]),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
