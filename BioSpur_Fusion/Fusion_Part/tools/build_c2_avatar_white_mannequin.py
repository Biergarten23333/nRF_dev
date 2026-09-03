#!/usr/bin/env python3
"""Add a display-only white mannequin over the formally frozen C2 viewer."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_new(path: Path, text: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(descriptor, text.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def embedded_viewer_data(html: str) -> dict:
    marker = "const DATA="
    start = html.index(marker) + len(marker)
    data, _ = json.JSONDecoder().raw_decode(html[start:])
    return data


def correct_reversed_named_camera_presets(html: str) -> tuple[str, bool]:
    """Migrate the frozen viewer's reversed front/rear camera labels.

    The trajectory and joint samples are embedded separately and remain byte-for-byte
    unchanged.  Only camera metadata and the named-view camera formula are swapped.
    New viewers generated after the source fix are returned unchanged.
    """
    old_formula = (
        "const lateral=Math.atan2(dy,dx),front=Math.PI-lateral,"
        "rear=-lateral,top=-lateral;"
    )
    new_formula = (
        "const lateral=Math.atan2(dy,dx),front=-lateral,"
        "rear=Math.PI-lateral,top=front;"
    )
    if old_formula not in html:
        if new_formula in html:
            return html, False
        raise RuntimeError("named camera preset formula is unrecognized")

    data = embedded_viewer_data(html)
    view = data["viewGauge"]
    old_front = json.dumps(view["frontYawRad"], separators=(",", ":"))
    old_rear = json.dumps(view["rearYawRad"], separators=(",", ":"))
    metadata = f'"frontYawRad":{old_front},"rearYawRad":{old_rear}'
    swapped = f'"frontYawRad":{old_rear},"rearYawRad":{old_front}'
    if html.count(metadata) != 1:
        raise RuntimeError("named camera preset metadata is not uniquely identifiable")
    corrected = html.replace(metadata, swapped, 1).replace(old_formula, new_formula, 1)
    return corrected, True


def add_oblique_front_named_camera_preset(html: str) -> tuple[str, bool]:
    """Add a body-following right-front three-quarter display camera.

    This is a camera-only compatibility migration for the formally frozen viewer.
    Embedded trajectory and joint data remain byte-for-byte unchanged.
    """
    if 'data-view="oblique"' in html:
        return html, False
    replacements = (
        (
            '<button class="view" data-view="front" type="button">从前方看</button>',
            '<button class="view" data-view="front" type="button">从前方看</button>'
            '<button class="view" data-view="oblique" type="button">斜前方看</button>',
        ),
        (
            "const VIEW_LABEL={front:'从前方看',rear:'从后方看',side:'从右侧看',top:'从上方看',three:'3D 相机'};",
            "const VIEW_LABEL={front:'从前方看',oblique:'斜前方看',rear:'从后方看',side:'从右侧看',top:'从上方看',three:'3D 相机'};",
        ),
        (
            "front:'相机从人体前方看。',rear:'相机从人体后方看。'",
            "front:'相机从人体前方看。',oblique:'相机从人体右前方略向下看。',rear:'相机从人体后方看。'",
        ),
        (
            "if(!['front','rear','side','top','three'].includes(viewMode))return;",
            "if(!['front','oblique','rear','side','top','three'].includes(viewMode))return;",
        ),
        (
            "if(viewMode==='front'){yaw=front;pitch=0}else if(viewMode==='rear')",
            "if(viewMode==='front'){yaw=front;pitch=0}else if(viewMode==='oblique'){yaw=front-Math.PI/4;pitch=.18}else if(viewMode==='rear')",
        ),
        (
            "allowedViews=new Set(['front','rear','side','top','three']);",
            "allowedViews=new Set(['front','oblique','rear','side','top','three']);",
        ),
    )
    migrated = html
    for old, new in replacements:
        if migrated.count(old) != 1:
            raise RuntimeError(f"oblique camera migration target is not unique: {old}")
        migrated = migrated.replace(old, new, 1)
    return migrated, True


def golf_hand_proxy_audit(data: dict, hand_length_m: float) -> dict:
    joint_index = {name: index for index, name in enumerate(data["jointNames"])}
    episode = next(row for row in data["episodes"] if row["id"] == "H02_golf")

    def point(frame: list[float], name: str) -> list[float]:
        offset = 3 * joint_index[name]
        return frame[offset : offset + 3]

    def distal_endpoint(frame: list[float], side: str) -> list[float]:
        elbow = point(frame, f"elbow_{side}")
        wrist = point(frame, f"wrist_{side}")
        direction = [wrist[i] - elbow[i] for i in range(3)]
        norm = math.sqrt(sum(value * value for value in direction))
        if norm <= 1e-9:
            raise RuntimeError(f"degenerate H02 {side} forearm direction")
        return [
            wrist[i] + hand_length_m * direction[i] / norm for i in range(3)
        ]

    spans = []
    for frame in episode["frames"]:
        left = distal_endpoint(frame, "left")
        right = distal_endpoint(frame, "right")
        spans.append(math.dist(left, right))
    ordered = sorted(spans)

    def quantile(fraction: float) -> float:
        return ordered[round((len(ordered) - 1) * fraction)]

    return {
        "frame_count": len(spans),
        "two_hand_grip_point_span_m": {
            "minimum": ordered[0],
            "q25": quantile(0.25),
            "median": quantile(0.50),
            "q75": quantile(0.75),
            "maximum": ordered[-1],
        },
    }


STYLE = r"""
<style id="whiteMannequinStyle">
#stage #mannequinCanvas{position:absolute;inset:0;z-index:1;pointer-events:none}
#stage #overlay{z-index:2}
#canvas.mannequin-active{opacity:0}
#mannequinBoundary{font-size:12px;color:#7a5b19;font-weight:700;white-space:nowrap}
@media (prefers-color-scheme:dark){#mannequinBoundary{color:#ffd27a}}
</style>
"""


SCRIPT = r"""
<script id="whiteMannequinLayer">
if(!VIEW.outputCoordinatesSolidified)throw new Error('white mannequin requires frozen C2 handedness');
const stage=document.getElementById('stage');
const mannequinCanvas=document.createElement('canvas');
mannequinCanvas.id='mannequinCanvas';
mannequinCanvas.setAttribute('aria-label','冻结关节点驱动的人体白模显示层');
stage.insertBefore(mannequinCanvas,document.getElementById('overlay'));
const mannequinCtx=mannequinCanvas.getContext('2d');
const modeLabel=document.createElement('span');
modeLabel.textContent='显示：';
const skeletonButton=document.createElement('button');
skeletonButton.type='button';skeletonButton.textContent='火柴骨架';
const mannequinButton=document.createElement('button');
mannequinButton.type='button';mannequinButton.textContent='人体白模';
const boundary=document.createElement('span');
boundary.id='mannequinBoundary';
boundary.textContent='白模仅为显示层 · 关节点未修改';
const topBar=document.querySelector('.top');
topBar.append(modeLabel,skeletonButton,mannequinButton,boundary);
let mannequinEnabled=true;

function mannequinResize(){
  const r=mannequinCanvas.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);
  mannequinCanvas.width=Math.max(1,Math.round(r.width*d));
  mannequinCanvas.height=Math.max(1,Math.round(r.height*d));
  mannequinCtx.setTransform(d,0,0,d,0,0);
}
function mannequinRaw(){
  const ep=active(),flat=ep.frames[frameIndex],rows=[];
  for(let i=0;i<DATA.jointNames.length;i++)rows.push([flat[3*i],flat[3*i+1],flat[3*i+2]]);
  if(!globalMirror)return rows;
  const mn=VIEW.globalMirrorPlane.normalWorldXY;
  return rows.map(p=>{const dot=p[0]*mn[0]+p[1]*mn[1];return[p[0]-2*dot*mn[0],p[1]-2*dot*mn[1],p[2]]});
}
function vadd(a,b,s=1){return[a[0]+s*b[0],a[1]+s*b[1],a[2]+s*b[2]]}
function vsub(a,b){return[a[0]-b[0],a[1]-b[1],a[2]-b[2]]}
function vunit(a){const n=Math.max(1e-9,Math.hypot(a[0],a[1],a[2]));return[a[0]/n,a[1]/n,a[2]/n]}
function vdistance(a,b){return Math.hypot(a[0]-b[0],a[1]-b[1],a[2]-b[2])}
function vdot(a,b){return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]}
function distanceToSegment(point,a,b){
  const ab=vsub(b,a),den=Math.max(1e-9,vdot(ab,ab));
  const t=Math.max(0,Math.min(1,vdot(vsub(point,a),ab)/den));
  return vdistance(point,vadd(a,ab,t));
}
function mannequinPalette(){
  const dark=matchMedia('(prefers-color-scheme:dark)').matches;
  return dark?{bg:'#111418',grid:'#293038',skin:'#f0f0ed',shade:'#d5d8d9',edge:'#717983',shaft:'#cbd0d5',grip:'#42484f',clubHead:'#9da5ad'}:{bg:'#f7f8fa',grid:'#dce2e9',skin:'#fafaf7',shade:'#e1e3e3',edge:'#717983',shaft:'#aeb5bc',grip:'#3d4349',clubHead:'#858e97'};
}
function capsule(a,b,radius,color,edge,depth){return{kind:'capsule',a,b,radius,color,edge,depth}}
function disk(center,radius,color,edge,depth){return{kind:'disk',center,radius,color,edge,depth}}
function polygon(points,color,edge,depth){return{kind:'polygon',points,color,edge,depth}}
function renderMannequin(){
  mannequinResize();
  const r=mannequinCanvas.getBoundingClientRect(),w=r.width,h=r.height,c=mannequinPalette();
  mannequinCtx.clearRect(0,0,w,h);
  const golf=active().id==='H02_golf';
  boundary.textContent=golf?'双手/球杆为显示代理 · 冻结关节点未修改':(mannequinEnabled?'白模仅为显示层 · 关节点未修改':'冻结关节点原始火柴骨架');
  if(!mannequinEnabled&&!golf)return;
  if(mannequinEnabled){
    mannequinCtx.fillStyle=c.bg;mannequinCtx.fillRect(0,0,w,h);
    mannequinCtx.strokeStyle=c.grid;mannequinCtx.lineWidth=1;
    for(let i=-2;i<=2;i++){
      let a=project(i*.35,-.7,0,w,h),b=project(i*.35,.7,0,w,h);
      mannequinCtx.beginPath();mannequinCtx.moveTo(a[0],a[1]);mannequinCtx.lineTo(b[0],b[1]);mannequinCtx.stroke();
      a=project(-.7,i*.35,0,w,h);b=project(.7,i*.35,0,w,h);
      mannequinCtx.beginPath();mannequinCtx.moveTo(a[0],a[1]);mannequinCtx.lineTo(b[0],b[1]);mannequinCtx.stroke();
    }
  }
  const raw=mannequinRaw(),ji=Object.fromEntries(DATA.jointNames.map((name,index)=>[name,index]));
  const p=name=>raw[ji[name]],screen=point=>project(point[0],point[1],point[2],w,h);
  const bodyUp=vunit(vsub(p('shoulder_mid'),p('pelvis_center')));
  const neckTop=vadd(p('shoulder_mid'),bodyUp,.075),headCenter=vadd(neckTop,bodyUp,.105);
  const shapes=[];
  const addBone=(a,b,radius,color=c.skin)=>{const pa=screen(p(a)),pb=screen(p(b));shapes.push(capsule(pa,pb,radius,color,c.edge,(pa[2]+pb[2])/2))};
  if(mannequinEnabled){
    addBone('shoulder_left','elbow_left',.055);addBone('elbow_left','wrist_left',.045,c.shade);
    addBone('shoulder_right','elbow_right',.055);addBone('elbow_right','wrist_right',.045,c.shade);
    addBone('hip_left','knee_left',.074);addBone('knee_left','ankle_left',.061,c.shade);
    addBone('hip_right','knee_right',.074);addBone('knee_right','ankle_right',.061,c.shade);
    const torsoNames=['shoulder_left','shoulder_right','hip_right','hip_left'];
    const torso=torsoNames.map(name=>screen(p(name)));
    shapes.push(polygon(torso,c.skin,c.edge,torso.reduce((s,q)=>s+q[2],0)/4));
    const pelvis=[screen(p('hip_left')),screen(p('hip_right')),screen(p('pelvis_center'))];
    shapes.push(polygon(pelvis,c.shade,c.edge,pelvis.reduce((s,q)=>s+q[2],0)/3));
    const neckA=screen(p('shoulder_mid')),neckB=screen(neckTop),head=screen(headCenter);
    shapes.push(capsule(neckA,neckB,.042,c.shade,c.edge,(neckA[2]+neckB[2])/2));
    shapes.push(disk(head,.092,c.skin,c.edge,head[2]));
    if(!golf)for(const name of ['wrist_left','wrist_right']){const q=screen(p(name));shapes.push(disk(q,.047,c.skin,c.edge,q[2]))}
    for(const name of ['ankle_left','ankle_right']){const q=screen(p(name));shapes.push(disk(q,.062,c.shade,c.edge,q[2]))}
  }
  if(golf){
    const handLength=.17;
    const leftDirection=vunit(vsub(p('wrist_left'),p('elbow_left')));
    const rightDirection=vunit(vsub(p('wrist_right'),p('elbow_right')));
    const leftGrip=vadd(p('wrist_left'),leftDirection,handLength);
    const rightGrip=vadd(p('wrist_right'),rightDirection,handLength);
    const wristMid=[0,1,2].map(i=>(p('wrist_left')[i]+p('wrist_right')[i])/2);
    const gripMid=[0,1,2].map(i=>(leftGrip[i]+rightGrip[i])/2);
    const shaftDirection=vunit(vsub(gripMid,wristMid));
    const bodyRight=vunit(vsub(p('hip_right'),p('hip_left')));
    const gripTop=vadd(gripMid,shaftDirection,-.12);
    const gripBottom=vadd(gripMid,shaftDirection,.12);
    const shaftStart=gripBottom;
    const shaftEnd=vadd(gripMid,shaftDirection,.92);
    const clubToe=vadd(shaftEnd,bodyRight,.14);
    const leftWrist=screen(p('wrist_left')),rightWrist=screen(p('wrist_right'));
    const leftHand=screen(leftGrip),rightHand=screen(rightGrip);
    const gripA=screen(gripTop),gripB=screen(gripBottom);
    const shaftA=screen(shaftStart),shaftB=screen(shaftEnd),toe=screen(clubToe);
    shapes.push(capsule(leftWrist,leftHand,.037,c.skin,c.edge,(leftWrist[2]+leftHand[2])/2));
    shapes.push(capsule(rightWrist,rightHand,.037,c.skin,c.edge,(rightWrist[2]+rightHand[2])/2));
    shapes.push(disk(leftHand,.044,c.skin,c.edge,leftHand[2]));
    shapes.push(disk(rightHand,.044,c.skin,c.edge,rightHand[2]));
    shapes.push(capsule(gripA,gripB,.018,c.grip,c.edge,(gripA[2]+gripB[2])/2));
    shapes.push(capsule(shaftA,shaftB,.010,c.shaft,c.edge,(shaftA[2]+shaftB[2])/2));
    shapes.push(capsule(shaftB,toe,.026,c.clubHead,c.edge,(shaftB[2]+toe[2])/2));
    const gripMiss=100*Math.max(distanceToSegment(leftGrip,gripTop,gripBottom),distanceToSegment(rightGrip,gripTop,gripBottom));
    boundary.textContent=`H02 双手各17 cm · 固定24 cm握把 · 最大握持偏离 ${gripMiss.toFixed(1)} cm · 冻结关节点未修改`;
  }
  shapes.sort((a,b)=>a.depth-b.depth);
  const scale=Math.min(w,h)*.43*zoom;
  for(const shape of shapes){
    mannequinCtx.fillStyle=shape.color;mannequinCtx.strokeStyle=shape.edge;mannequinCtx.lineJoin='round';mannequinCtx.lineCap='round';
    if(shape.kind==='capsule'){
      mannequinCtx.lineWidth=Math.max(2,2*shape.radius*scale+3);mannequinCtx.beginPath();mannequinCtx.moveTo(shape.a[0],shape.a[1]);mannequinCtx.lineTo(shape.b[0],shape.b[1]);mannequinCtx.stroke();
      mannequinCtx.strokeStyle=shape.color;mannequinCtx.lineWidth=Math.max(1,2*shape.radius*scale);mannequinCtx.beginPath();mannequinCtx.moveTo(shape.a[0],shape.a[1]);mannequinCtx.lineTo(shape.b[0],shape.b[1]);mannequinCtx.stroke();
    }else if(shape.kind==='disk'){
      mannequinCtx.lineWidth=2;mannequinCtx.beginPath();mannequinCtx.arc(shape.center[0],shape.center[1],shape.radius*scale,0,Math.PI*2);mannequinCtx.fill();mannequinCtx.stroke();
    }else{
      mannequinCtx.lineWidth=2;mannequinCtx.beginPath();shape.points.forEach((q,i)=>i?mannequinCtx.lineTo(q[0],q[1]):mannequinCtx.moveTo(q[0],q[1]));mannequinCtx.closePath();mannequinCtx.fill();mannequinCtx.stroke();
    }
  }
  mannequinCtx.fillStyle=matchMedia('(prefers-color-scheme:dark)').matches?'#ffd27a':'#7a5b19';
  mannequinCtx.font='bold 12px sans-serif';
  mannequinCtx.fillText(golf?'通用白模 + 双手17 cm + 球杆显示代理 · 姿态来自冻结关节点':'通用白模显示代理 · 姿态来自冻结关节点',16,h-16);
}
const frozenDraw=draw;
draw=function(){frozenDraw();renderMannequin()};
function setMannequinMode(enabled){
  mannequinEnabled=enabled;canvas.classList.toggle('mannequin-active',enabled);
  mannequinButton.setAttribute('aria-pressed',String(enabled));
  skeletonButton.setAttribute('aria-pressed',String(!enabled));
  boundary.textContent=active().id==='H02_golf'?'双手/球杆为显示代理 · 冻结关节点未修改':(enabled?'白模仅为显示层 · 关节点未修改':'冻结关节点原始火柴骨架');
  draw();
}
skeletonButton.addEventListener('click',()=>setMannequinMode(false));
mannequinButton.addEventListener('click',()=>setMannequinMode(true));
new ResizeObserver(()=>{mannequinResize();renderMannequin()}).observe(stage);
const requestedDisplayZoom=Number(new URLSearchParams(location.search).get('zoom'));
if(Number.isFinite(requestedDisplayZoom)){
  zoom=Math.max(.35,Math.min(2.4,requestedDisplayZoom));
}
setMannequinMode(true);
</script>
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--freeze", type=Path, required=True)
    parser.add_argument("--base-html", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    freeze = args.freeze.resolve()
    base_html = args.base_html.resolve()
    output = args.output.resolve()
    if any(ROOT not in path.parents for path in (freeze, base_html, output)):
        raise SystemExit("all paths must remain under canonical Fusion_Part")
    seal = json.loads((freeze / "FORMAL_FREEZE_SEAL.json").read_text(encoding="utf-8"))
    manifest_path = ROOT / seal["manifest"]
    if seal["status"] != "FORMAL_FREEZE_SEALED" or sha256(manifest_path) != seal["manifest_sha256"]:
        raise RuntimeError("Capture2 formal freeze seal is invalid")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bound = {row["path"]: row for row in manifest["accepted_artifacts"]}
    relative_base = str(base_html.relative_to(ROOT))
    if relative_base not in bound or sha256(base_html) != bound[relative_base]["sha256"]:
        raise RuntimeError("base viewer is not the frozen accepted artifact")
    output.mkdir(parents=True, exist_ok=False)
    base_source = base_html.read_text(encoding="utf-8")
    base_source, camera_presets_corrected = correct_reversed_named_camera_presets(
        base_source
    )
    base_source, oblique_camera_added = add_oblique_front_named_camera_preset(
        base_source
    )
    golf_audit = golf_hand_proxy_audit(embedded_viewer_data(base_source), 0.17)
    html = base_source + STYLE + SCRIPT
    html_path = output / "c2_avatar_white_mannequin.html"
    write_new(html_path, html)
    compatibility_path = output / "c2_avatar_3d.html"
    write_new(compatibility_path, html)
    result = {
        "schema": "biospur-capture2-white-mannequin-display-layer-v1",
        "status": "DISPLAY_LAYER_OVER_FORMALLY_FROZEN_CAPTURE2",
        "html": str(html_path.relative_to(ROOT)),
        "html_sha256": sha256(html_path),
        "existing_viewer_url_compatibility_alias": str(
            compatibility_path.relative_to(ROOT)
        ),
        "formal_freeze_seal": str((freeze / "FORMAL_FREEZE_SEAL.json").relative_to(ROOT)),
        "formal_freeze_manifest_sha256": seal["manifest_sha256"],
        "base_viewer": relative_base,
        "base_viewer_sha256": bound[relative_base]["sha256"],
        "joint_samples_modified": False,
        "orientation_or_calibration_refit": False,
        "ik_rebase_retarget_or_repair": False,
        "named_camera_presets": {
            "front_rear_semantics_corrected": camera_presets_corrected,
            "oblique_front_camera_added": oblique_camera_added,
            "oblique_front_camera_definition": {
                "azimuth_from_front_deg": 45.0,
                "elevation_deg": round(math.degrees(0.18), 3),
                "body_following": True,
            },
            "trajectory_or_joint_samples_modified": False,
        },
        "camera_zoom_query_parameter": {
            "name": "zoom",
            "range": [0.35, 2.4],
            "trajectory_or_joint_samples_modified": False,
        },
        "generic_display_proxies": [
            "surface_thickness",
            "head",
            "hands",
            "ankle_endcaps",
            "H02_two_17cm_hands_and_golf_club",
        ],
        "golf_bilateral_hand_display_proxy": {
            "action": "H02_golf",
            "hand_length_to_sensor_m_user_measurement": 0.17,
            "actual_sensor_origin_present_in_frozen_viewer": False,
            "display_anchor": "frozen_anatomical_wrist_endpoint",
            "display_direction": "continued_frozen_elbow_to_wrist_direction",
            "joint_samples_modified": False,
            "grip_span_is_not_forced_or_repaired": True,
            "audit": golf_audit,
        },
        "golf_club_display_proxy": {
            "action": "H02_golf",
            "club_sensor_present": False,
            "display_only": True,
            "grip_center": "midpoint_of_left_and_right_17cm_hand_proxy_endpoints",
            "shaft_direction": "bilateral_wrist_midpoint_to_hand_proxy_midpoint",
            "fixed_grip_length_m": 0.24,
            "hand_endpoints_forced_onto_grip": False,
            "nominal_shaft_extension_m": 0.92,
            "club_head_lateral_proxy_m": 0.14,
        },
        "default_mode": "WHITE_MANNEQUIN",
        "skeleton_toggle_available": True,
    }
    manifest_output = output / "WHITE_MANNEQUIN_MANIFEST.json"
    write_new(manifest_output, json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
