"""Fast fixed-camera replay rendering through Pillow and ffmpeg."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .config import VIDEO_RATE_HZ
from .skeleton import JOINT_NAMES

PANEL_W, HEIGHT = 360, 420
EDGES = (
    ("pelvis", "torso_top", "torso"),
    ("shoulder_left", "shoulder_right", "torso"),
    ("hip_left", "hip_right", "pelvis"),
    ("shoulder_left", "elbow_left", "left"), ("elbow_left", "wrist_left", "left"),
    ("shoulder_right", "elbow_right", "right"), ("elbow_right", "wrist_right", "right"),
    ("hip_left", "knee_left", "left"), ("knee_left", "ankle_left", "left"),
    ("hip_right", "knee_right", "right"), ("knee_right", "ankle_right", "right"),
)
COLORS = {"torso": (235, 235, 235), "pelvis": (105, 220, 140),
          "left": (60, 200, 255), "right": (255, 155, 55)}


def project(points: np.ndarray, view: str) -> np.ndarray:
    if view == "front":
        horizontal, vertical = points[:, 1], points[:, 2]
    elif view == "side":
        horizontal, vertical = points[:, 0], points[:, 2]
    elif view == "3d":
        horizontal = 0.82*points[:, 1] + 0.48*points[:, 0]
        vertical = points[:, 2] - 0.25*points[:, 0]
    else:
        raise ValueError(view)
    return np.column_stack((PANEL_W/2 + 210*horizontal, 172 - 210*vertical))


def _panel(points: np.ndarray, availability: np.ndarray, view: str,
           capture: str, time_s: float) -> Image.Image:
    image = Image.new("RGB", (PANEL_W, HEIGHT), (13, 17, 24)); draw = ImageDraw.Draw(image)
    draw.line((0, 350, PANEL_W, 350), fill=(45, 55, 70), width=1)
    draw.text((10, 8), f"Capture {capture} | {view.upper()} | {time_s:8.2f} s", fill=(240, 240, 245))
    missing = int(np.sum(~availability))
    draw.text((10, 27), f"PURE IMU | root fixed | missing joints: {missing}",
              fill=(255, 110, 95) if missing else (130, 205, 150))
    xy = project(points, view); index = {name: i for i, name in enumerate(JOINT_NAMES)}
    for a, b, group in EDGES:
        ia, ib = index[a], index[b]
        if availability[ia] and availability[ib] and np.all(np.isfinite(xy[[ia, ib]])):
            draw.line((*xy[ia], *xy[ib]), fill=COLORS[group], width=6)
    for i, point in enumerate(xy):
        if availability[i] and np.all(np.isfinite(point)):
            x, y = point; draw.ellipse((x-4, y-4, x+4, y+4), fill=(245, 245, 245))
    return image


def render_capture(npz_path: Path, output_dir: Path, capture: str) -> dict:
    with np.load(npz_path, allow_pickle=False) as data:
        times = data["time_s"]; positions = data["joint_positions_m"]; available = data["joint_available"]
    replay_rate = 1.0/float(np.median(np.diff(times)))
    stride = max(1, int(round(replay_rate/VIDEO_RATE_HZ)))
    indexes = np.arange(0, len(times), stride, dtype=int)
    composite = output_dir / f".CAPTURE{capture}_COMPOSITE_TEMP.mp4"
    width = 3*PANEL_W
    command = ["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", f"{width}x{HEIGHT}", "-r", str(VIDEO_RATE_HZ), "-i", "-", "-an",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p", str(composite)]
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    assert process.stdin is not None
    for i in indexes:
        frame = Image.new("RGB", (width, HEIGHT))
        for panel, view in enumerate(("3d", "front", "side")):
            frame.paste(_panel(positions[i], available[i], view, capture, float(times[i])), (panel*PANEL_W, 0))
        process.stdin.write(frame.tobytes())
    process.stdin.close(); return_code = process.wait()
    if return_code:
        raise RuntimeError(f"ffmpeg composite failed {return_code}")
    outputs = {}
    names = (("3D", 0), ("FRONT", 1), ("SIDE", 2))
    for label, panel in names:
        path = output_dir / f"CAPTURE{capture}_PURE_IMU_{'SKELETON_3D' if label == '3D' else label}.mp4"
        crop = f"crop={PANEL_W}:{HEIGHT}:{panel*PANEL_W}:0"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(composite), "-vf", crop,
                        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                        "-pix_fmt", "yuv420p", str(path)], check=True)
        outputs[label.lower()] = str(path)
    composite.unlink()
    return {"source_replay_rate_hz": replay_rate, "video_rate_hz": VIDEO_RATE_HZ,
            "video_frames": len(indexes), "duration_s": len(indexes)/VIDEO_RATE_HZ,
            "outputs": outputs}


def render_comparison(npz_paths: dict[str, Path], output: Path, duration_s: float = 20.0) -> dict:
    loaded = {}
    for capture, path in npz_paths.items():
        data = np.load(path, allow_pickle=False)
        loaded[capture] = (data, data["time_s"], data["joint_positions_m"], data["joint_available"])
    frames = int(round(duration_s*VIDEO_RATE_HZ)); width = 3*PANEL_W
    process = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
                                "-s", f"{width}x{HEIGHT}", "-r", str(VIDEO_RATE_HZ), "-i", "-", "-an",
                                "-c:v", "libx264", "-preset", "veryfast", "-crf", "24", "-pix_fmt", "yuv420p",
                                str(output)], stdin=subprocess.PIPE)
    assert process.stdin is not None
    for frame_index in range(frames):
        t = frame_index/VIDEO_RATE_HZ; frame = Image.new("RGB", (width, HEIGHT))
        for panel, capture in enumerate(("1", "2", "3")):
            data, times, pos, avail = loaded[capture]
            i = min(len(times)-1, int(np.searchsorted(times, t)))
            frame.paste(_panel(pos[i], avail[i], "3d", capture, float(times[i])), (panel*PANEL_W, 0))
        process.stdin.write(frame.tobytes())
    process.stdin.close(); rc = process.wait()
    for data, *_ in loaded.values(): data.close()
    if rc: raise RuntimeError(f"comparison ffmpeg failed {rc}")
    return {"meaningful_common_semantics": "first 20 seconds of labelled initial standing block",
            "duration_s": duration_s, "video_rate_hz": VIDEO_RATE_HZ, "path": str(output)}
