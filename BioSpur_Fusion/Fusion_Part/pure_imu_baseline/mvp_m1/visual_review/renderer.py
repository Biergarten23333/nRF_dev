"""Fast deterministic orthographic skeleton renderer and H.264 encoder."""
from __future__ import annotations

import hashlib
import math
import subprocess
from dataclasses import dataclass,field
from pathlib import Path

import numpy as np
from PIL import Image,ImageDraw,ImageFont

from pure_imu_baseline.stage2.config import BONES
from pure_imu_baseline.mvp_m1.config import load_config
from pure_imu_baseline.mvp_m1.engine import COMMAND_CLEAR,COMMAND_RECENTER,GaugeCommand,PoseEngine

FPS=30
WIDTH=1280
HEIGHT=720
COLORS={"background":"#050b11","panel":"#08131d","border":"#274156","text":"#e7eef6","muted":"#9fb3c8",
        "warning":"#ff7070","accent":"#fbbf24","left":"#f59e0b","right":"#22d3ee","core":"#f8fafc","pelvis":"#22c55e",
        "axis_x":"#ef4444","axis_y":"#22c55e","axis_z":"#3b82f6"}
FONT=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",16)
FONT_SMALL=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",13)
FONT_BOLD=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",18)
FONT_TITLE=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",22)


def sha_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def unit(value: np.ndarray) -> np.ndarray:
    norm=np.linalg.norm(value)
    return value/(norm if norm else 1.0)


def camera_basis(name: str, orbit_angle: float | None=None) -> tuple[np.ndarray,np.ndarray,np.ndarray]:
    up_ref=np.array([0.0,0.0,1.0])
    if name=="FRONT": direction=np.array([1.0,0.0,0.0])
    elif name=="SIDE": direction=np.array([0.0,-1.0,0.0])
    elif name=="TOP": direction=np.array([0.0,0.0,1.0]); up_ref=np.array([1.0,0.0,0.0])
    elif name=="OBLIQUE": direction=np.array([1.0,-1.0,0.7])
    elif name=="ORBIT":
        angle=float(orbit_angle or 0.0); pitch=0.32
        direction=np.array([math.cos(pitch)*math.cos(angle),math.cos(pitch)*math.sin(angle),math.sin(pitch)])
    else: raise ValueError(name)
    direction=unit(direction); forward=-direction; right=unit(np.cross(forward,up_ref)); up=unit(np.cross(right,forward))
    return right,up,forward


def project(points: np.ndarray, rect: tuple[int,int,int,int], camera: str, pixels_per_m: float,
            orbit_angle: float | None=None) -> tuple[np.ndarray,np.ndarray]:
    x0,y0,x1,y1=rect; right,up,forward=camera_basis(camera,orbit_angle); center=np.array([0.0,0.0,-0.15]); rel=points-center
    px=(x0+x1)/2+rel@right*pixels_per_m; py=(y0+y1)/2-rel@up*pixels_per_m; depth=rel@forward
    return np.column_stack([px,py]),depth


def draw_axis_triad(draw: ImageDraw.ImageDraw, rect: tuple[int,int,int,int], camera: str, orbit_angle: float|None=None) -> None:
    x0,y0,x1,y1=rect; origin=np.array([x0+42,y1-40],float); right,up,_=camera_basis(camera,orbit_angle)
    axes=((np.array([1.,0.,0.]),COLORS["axis_x"],"+X"),(np.array([0.,1.,0.]),COLORS["axis_y"],"+Y"),(np.array([0.,0.,1.]),COLORS["axis_z"],"+Z"))
    for vector,color,label in axes:
        delta=np.array([vector@right,-vector@up])*26; end=origin+delta
        draw.line([tuple(origin),tuple(end)],fill=color,width=3); draw.text((end[0]+2,end[1]-7),label,font=FONT_SMALL,fill=color)


def draw_legend(draw: ImageDraw.ImageDraw, x: int, y: int) -> None:
    for label,color in (("LEFT",COLORS["left"]),("RIGHT",COLORS["right"]),("CORE",COLORS["core"]),("PELVIS",COLORS["pelvis"])):
        draw.line([(x,y+8),(x+22,y+8)],fill=color,width=5); draw.text((x+28,y),label,font=FONT_SMALL,fill=COLORS["muted"]); x+=92


def _edge_data(joint_names: np.ndarray) -> list[tuple[int,int,str,str]]:
    index={str(name):i for i,name in enumerate(joint_names)}
    return [(index[a],index[b],category,name) for name,a,b,category in BONES]


def draw_pose_panel(draw: ImageDraw.ImageDraw, rect: tuple[int,int,int,int], positions: np.ndarray,
                    available: np.ndarray, joint_names: np.ndarray, camera: str, pixels_per_m: float,
                    orbit_angle: float|None=None, title: str|None=None, subtitle: str|None=None) -> int:
    x0,y0,x1,y1=rect; draw.rectangle(rect,fill=COLORS["panel"],outline=COLORS["border"],width=2)
    projected,depth=project(positions,rect,camera,pixels_per_m,orbit_angle); edges=_edge_data(joint_names); skipped=0
    for a,b,category,_ in sorted(edges,key=lambda edge:(depth[edge[0]]+depth[edge[1]])/2):
        if not (available[a] and available[b] and np.all(np.isfinite(positions[[a,b]]))): skipped+=1; continue
        draw.line([tuple(projected[a]),tuple(projected[b])],fill=COLORS[category],width=7 if category=="pelvis" else 5)
    for joint in range(len(joint_names)):
        if available[joint] and np.all(np.isfinite(positions[joint])):
            x,y=projected[joint]; draw.ellipse((x-3,y-3,x+3,y+3),fill=COLORS["text"])
    if title: draw.text((x0+10,y0+8),title,font=FONT_BOLD,fill=COLORS["text"])
    if subtitle: draw.text((x0+10,y0+34),subtitle[:72],font=FONT_SMALL,fill=COLORS["muted"])
    draw_axis_triad(draw,rect,camera,orbit_angle)
    return skipped


def header(draw: ImageDraw.ImageDraw, capture: str, timestamp: float, label: str, valid: int,
           reset_nodes: list[str], unavailable_nodes: list[str], extra: str="") -> None:
    readable=label.replace("_"," ")
    draw.text((14,6),f"Capture {capture}  ·  source {timestamp:9.3f} s",font=FONT_TITLE,fill=COLORS["text"])
    if extra: draw.text((710,9),extra,font=FONT_SMALL,fill=COLORS["accent"])
    draw.text((14,32),readable[:64],font=FONT_BOLD,fill=COLORS["text"])
    draw_legend(draw,800,32)
    draw.text((14,58),"RAW  ·  PURE IMU  ·  ROOT FIXED  ·  GLOBAL YAW MAY DRIFT",font=FONT_BOLD,fill=COLORS["accent"])
    draw.text((650,61),f"valid nodes {valid}/10",font=FONT_SMALL,fill=COLORS["text"])
    warnings=[]
    if reset_nodes: warnings.append("RESET: "+", ".join(reset_nodes))
    if unavailable_nodes: warnings.append("UNAVAILABLE: "+", ".join(unavailable_nodes))
    if warnings: draw.text((800,61),(" | ".join(warnings))[:66],font=FONT_SMALL,fill=COLORS["warning"])


@dataclass
class RenderStats:
    name: str
    encoded_frames: int=0
    source_frames: int=0
    frames_with_unavailable: int=0
    reset_assertions: int=0
    skipped_bone_draws: int=0
    source_frame_index_sha256: str=""
    source_timestamp_sha256: str=""
    render_input_coordinate_sha256: str=""
    camera_pose_array_mutations: int=0
    extra: dict=field(default_factory=dict)


class FrameAudit:
    def __init__(self):
        self.indices=hashlib.sha256(); self.timestamps=hashlib.sha256(); self.coordinates=hashlib.sha256(); self.count=0
    def update(self,index: int,timestamp: float,positions: np.ndarray):
        self.indices.update(np.asarray(index,dtype="<i8").tobytes());self.timestamps.update(np.asarray(timestamp,dtype="<f8").tobytes());self.coordinates.update(np.ascontiguousarray(positions).tobytes());self.count+=1
    def finish(self,stats: RenderStats):
        stats.source_frames=self.count;stats.source_frame_index_sha256=self.indices.hexdigest();stats.source_timestamp_sha256=self.timestamps.hexdigest();stats.render_input_coordinate_sha256=self.coordinates.hexdigest()


class Encoder:
    def __init__(self,path: Path,width: int,height: int):
        self.path=path;self.width=width;self.height=height
        command=["ffmpeg","-hide_banner","-loglevel","error","-y","-f","rawvideo","-pix_fmt","rgb24","-s",f"{width}x{height}","-r",str(FPS),"-i","-","-an","-c:v","libx264","-preset","veryfast","-crf","19","-profile:v","high","-level","4.1","-pix_fmt","yuv420p","-movflags","+faststart",str(path)]
        self.process=subprocess.Popen(command,stdin=subprocess.PIPE,stderr=subprocess.PIPE)
    def write(self,image: Image.Image):
        assert self.process.stdin is not None; self.process.stdin.write(image.tobytes())
    def close(self):
        assert self.process.stdin is not None and self.process.stderr is not None
        self.process.stdin.close();stderr=self.process.stderr.read().decode("utf-8",errors="replace");code=self.process.wait()
        if code: raise RuntimeError(f"ffmpeg failed for {self.path}: {stderr[-4000:]}")


def interval_frames(interval: dict) -> range:
    return range(int(interval["start_frame"]),int(interval["end_frame"])+1,2)


def render_four_view(path: Path,capture: str,raw: dict,intervals: list[dict]) -> dict:
    before=sha_array(raw["joint_positions_m"]); encoder=Encoder(path,WIDTH,HEIGHT);stats=RenderStats(path.name);audit=FrameAudit();nodes=[str(x) for x in raw["segment_names"]]
    rects={"FRONT":(8,86,636,398),"SIDE":(644,86,1272,398),"TOP":(8,406,636,712),"OBLIQUE":(644,406,1272,712)}
    for interval in intervals:
        for frame in interval_frames(interval):
            positions=raw["joint_positions_m"][frame];available=raw["joint_available"][frame];valid=raw["valid"][frame];reset=raw["filter_reset"][frame]
            image=Image.new("RGB",(WIDTH,HEIGHT),COLORS["background"]);draw=ImageDraw.Draw(image)
            header(draw,capture,float(raw["time_s"][frame]),interval["label"],int(valid.sum()),[nodes[i] for i in np.flatnonzero(reset)],[nodes[i] for i in np.flatnonzero(~valid)])
            for camera,rect in rects.items():stats.skipped_bone_draws+=draw_pose_panel(draw,rect,positions,available,raw["joint_names"],camera,150.0,title=camera)
            encoder.write(image);audit.update(frame,float(raw["time_s"][frame]),positions);stats.encoded_frames+=1;stats.frames_with_unavailable+=int(not valid.all());stats.reset_assertions+=int(reset.sum())
    encoder.close();audit.finish(stats);stats.camera_pose_array_mutations=int(before!=sha_array(raw["joint_positions_m"]));return stats.__dict__


def render_orbit(path: Path,capture: str,raw: dict,intervals: list[dict]) -> dict:
    before=sha_array(raw["joint_positions_m"]);all_frames=[(interval,frame) for interval in intervals for frame in interval_frames(interval)];encoder=Encoder(path,WIDTH,HEIGHT);stats=RenderStats(path.name);audit=FrameAudit();nodes=[str(x) for x in raw["segment_names"]]
    for output_index,(interval,frame) in enumerate(all_frames):
        angle=-0.72+2*math.pi*output_index/max(1,len(all_frames)-1);positions=raw["joint_positions_m"][frame];available=raw["joint_available"][frame];valid=raw["valid"][frame];reset=raw["filter_reset"][frame]
        image=Image.new("RGB",(WIDTH,HEIGHT),COLORS["background"]);draw=ImageDraw.Draw(image)
        header(draw,capture,float(raw["time_s"][frame]),interval["label"],int(valid.sum()),[nodes[i] for i in np.flatnonzero(reset)],[nodes[i] for i in np.flatnonzero(~valid)],"CAMERA ORBIT — POSE DATA UNCHANGED")
        stats.skipped_bone_draws+=draw_pose_panel(draw,(8,86,1272,712),positions,available,raw["joint_names"],"ORBIT",320.0,orbit_angle=angle,title=f"deterministic orbit {math.degrees(angle)%360:05.1f}°")
        encoder.write(image);audit.update(frame,float(raw["time_s"][frame]),positions);stats.encoded_frames+=1;stats.frames_with_unavailable+=int(not valid.all());stats.reset_assertions+=int(reset.sum())
    encoder.close();audit.finish(stats);stats.camera_pose_array_mutations=int(before!=sha_array(raw["joint_positions_m"]));stats.extra["orbit_equation"]="yaw=-0.72+2*pi*output_index/(N-1), pitch=0.32 rad";return stats.__dict__


def render_comparison(path: Path,raws: dict[str,dict],intervals: dict[str,dict]) -> dict:
    width=1920;height=720;before={c:sha_array(raw["joint_positions_m"]) for c,raw in raws.items()};frame_lists={c:list(interval_frames(intervals[c])) for c in "123"};count=min(map(len,frame_lists.values()));encoder=Encoder(path,width,height);stats=RenderStats(path.name);audits={c:FrameAudit() for c in "123"}
    for output_index in range(count):
        image=Image.new("RGB",(width,height),COLORS["background"]);draw=ImageDraw.Draw(image)
        draw.text((14,8),"BioSpur Pure-IMU MVP-M1 · three-capture RAW comparison",font=FONT_TITLE,fill=COLORS["text"]);draw.text((14,38),"PRODUCT-LEVEL INSPECTION ONLY  ·  independent source timestamps  ·  ROOT FIXED  ·  GLOBAL YAW MAY DRIFT",font=FONT_BOLD,fill=COLORS["accent"])
        for panel,capture in enumerate("123"):
            raw=raws[capture];frame=frame_lists[capture][output_index];positions=raw["joint_positions_m"][frame];available=raw["joint_available"][frame];valid=raw["valid"][frame]
            rect=(panel*640+6,72,(panel+1)*640-6,712);title=f"Capture {capture} · {raw['time_s'][frame]:.3f} s · valid {int(valid.sum())}/10";subtitle=intervals[capture]['label'].replace('_',' ')
            stats.skipped_bone_draws+=draw_pose_panel(draw,rect,positions,available,raw["joint_names"],"OBLIQUE",150.0,title=title,subtitle=subtitle);audits[capture].update(frame,float(raw["time_s"][frame]),positions);stats.frames_with_unavailable+=int(not valid.all());stats.reset_assertions+=int(raw["filter_reset"][frame].sum())
        encoder.write(image);stats.encoded_frames+=1
    encoder.close();stats.source_frames=count*3;stats.source_frame_index_sha256=hashlib.sha256("".join(a.indices.hexdigest() for a in audits.values()).encode()).hexdigest();stats.source_timestamp_sha256=hashlib.sha256("".join(a.timestamps.hexdigest() for a in audits.values()).encode()).hexdigest();stats.render_input_coordinate_sha256=hashlib.sha256("".join(a.coordinates.hexdigest() for a in audits.values()).encode()).hexdigest();stats.camera_pose_array_mutations=sum(before[c]!=sha_array(raws[c]["joint_positions_m"]) for c in "123");stats.extra["panel_source_frames"]={c:len(frame_lists[c][:count]) for c in "123"};return stats.__dict__


def render_recenter_demo(path: Path,raw: dict,interval: dict) -> dict:
    frames=list(interval_frames(interval));recenter_frame=frames[min(60,len(frames)-1)];clear_frame=frames[min(150,len(frames)-1)]
    pose=PoseEngine(load_config()).process(raw,[GaugeCommand(recenter_frame,COMMAND_RECENTER),GaugeCommand(clear_frame,COMMAND_CLEAR)])
    before=sha_array(raw["joint_positions_m"]);encoder=Encoder(path,WIDTH,HEIGHT);stats=RenderStats(path.name);audit=FrameAudit();relative_delta=float(np.nanmax(np.abs(pose["display_q_PC_wxyz"]-pose["working_q_PC_wxyz"])))
    for frame in frames:
        raw_positions=raw["joint_positions_m"][frame];display_positions=pose["display_joint_positions_m"][frame];available=raw["joint_available"][frame];gamma=float(pose["global_yaw_gauge_rad"][frame]);epoch=int(pose["global_yaw_gauge_epoch"][frame])
        if frame<recenter_frame: phase="RAW DISPLAY · waiting for explicit operator command"
        elif frame<clear_frame: phase="OPERATOR-REQUESTED GLOBAL GAUGE CHANGE · NOT AUTOMATIC DRIFT CORRECTION"
        else: phase="CLEAR RECENTER · RETURN TO RAW GAUGE"
        image=Image.new("RGB",(WIDTH,HEIGHT),COLORS["background"]);draw=ImageDraw.Draw(image)
        draw.text((14,6),f"Manual global-yaw recenter demonstration · source {raw['time_s'][frame]:.3f} s",font=FONT_TITLE,fill=COLORS["text"]);draw.text((14,34),phase[:94],font=FONT_BOLD,fill=COLORS["accent"])
        draw.text((14,62),f"one common gamma {gamma:+.6f} rad · gauge epoch {epoch} · parent-child qPC Δ max {relative_delta:.3e}",font=FONT_SMALL,fill=COLORS["text"])
        stats.skipped_bone_draws+=draw_pose_panel(draw,(8,86,636,712),raw_positions,available,raw["joint_names"],"OBLIQUE",250.0,title="RAW reference")
        stats.skipped_bone_draws+=draw_pose_panel(draw,(644,86,1272,712),display_positions,available,raw["joint_names"],"OBLIQUE",250.0,title="DISPLAY gauge branch")
        encoder.write(image);audit.update(frame,float(raw["time_s"][frame]),raw_positions);stats.encoded_frames+=1;stats.frames_with_unavailable+=int(not raw["valid"][frame].all());stats.reset_assertions+=int(raw["filter_reset"][frame].sum())
    encoder.close();audit.finish(stats);stats.camera_pose_array_mutations=int(before!=sha_array(raw["joint_positions_m"]));stats.extra={"recenter_frame":recenter_frame,"clear_frame":clear_frame,"global_yaw_gauge_events":pose["manual_recenter_events"],"display_q_PC_vs_working_q_PC_max":relative_delta,"main_review_branch":"RAW_ONLY"};return stats.__dict__
