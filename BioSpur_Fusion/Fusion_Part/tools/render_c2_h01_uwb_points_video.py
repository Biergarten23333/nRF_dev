#!/usr/bin/env python3
"""Render the ten raw-range-derived C2 H01 UWB point trajectories.

This is deliberately a UWB-only diagnostic.  It decodes the sealed H01 raw
transport slice, maps every UWB sweep to the established common clock, and
feeds each node's A-H ranges to the existing canonical T4 frontend.  It does
not consume IMU samples, the frozen avatar, IK/FK, action semantics, or body
constraints.  Failed/missing solves remain gaps and solved NLOS outliers are
not hidden by smoothing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FFMpegWriter
import numpy as np

matplotlib.rcParams["font.family"] = "sans-serif"
matplotlib.rcParams["font.sans-serif"] = ["Noto Sans CJK SC", "DejaVu Sans"]
matplotlib.rcParams["axes.unicode_minus"] = False

from biospur_fusion.uwb.frontend import CanonicalT4Frontend
from fusion_host_binary import FrameError
from tools.run_c2_hxx_frozen_replay import (
    C2_ROOT,
    _alignment,
    _holdout_record,
)
from v47_real_data_adapter import (
    NODES,
    UWB_DTYPE,
    _decode_host_frame,
    _decode_uwb,
    iter_cobs_records,
)


WORKSPACE = Path(__file__).resolve().parents[1]
LAYOUT_PATH = Path(
    "/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/B306_Part/deployments/"
    "current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
)
RAW_SLICE = C2_ROOT / "holdout/H01_boxing/rep_01/raw/fusion_host_raw.cobs.bin"
ACTION = "H01_boxing"
FPS = 10
TRAIL_SECONDS = 1.8
MAX_DISPLAY_AGE_SECONDS = 0.30

NODE_ROLES = {
    "BSFEC35": "左前臂",
    "BSFB165": "右前臂",
    "BSFAA61": "左上臂",
    "BSF1120": "右上臂",
    "BSF31CC": "躯干",
    "BSFC2CC": "骨盆",
    "BSF44AD": "左大腿",
    "BSF3C79": "右大腿",
    "BSF6C53": "左小腿",
    "BSF8BC4": "右小腿",
}

NODE_ROLES_EN = {
    "BSFEC35": "forearm left",
    "BSFB165": "forearm right",
    "BSFAA61": "upper arm left",
    "BSF1120": "upper arm right",
    "BSF31CC": "torso",
    "BSFC2CC": "pelvis",
    "BSF44AD": "thigh left",
    "BSF3C79": "thigh right",
    "BSF6C53": "shank left",
    "BSF8BC4": "shank right",
}

NODE_COLORS = {
    "BSFEC35": "#2dd4ff",
    "BSFB165": "#ffab40",
    "BSFAA61": "#42a5f5",
    "BSF1120": "#ff7043",
    "BSF31CC": "#f5f5f5",
    "BSFC2CC": "#ce93d8",
    "BSF44AD": "#66bb6a",
    "BSF3C79": "#ffee58",
    "BSF6C53": "#26c6da",
    "BSF8BC4": "#ef5350",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"JSON object required: {path}")
    return value


def _layout_points() -> tuple[np.ndarray, list[str]]:
    payload = _json(LAYOUT_PATH)
    rows = payload.get("anchors")
    if not isinstance(rows, list) or len(rows) != 8:
        raise RuntimeError("canonical layout must contain exactly eight anchors")
    rows = sorted(rows, key=lambda row: int(row["id"]))
    expected = list(range(8))
    if [int(row["id"]) for row in rows] != expected:
        raise RuntimeError("canonical anchor ids are not A-H slots 0-7")
    points = np.array(
        [[float(row["x_mm"]), float(row["y_mm"]), float(row["z_mm"])] for row in rows],
        dtype=np.float64,
    ) / 1000.0
    return points, [chr(ord("A") + index) for index in expected]


def _decode_positions() -> tuple[dict[str, dict[str, np.ndarray]], dict[str, Any]]:
    record = _holdout_record(ACTION)
    models, alignment = _alignment(record)
    frontend = CanonicalT4Frontend(LAYOUT_PATH)
    start_ns = int(alignment["formal_start_shared_ns"])
    stop_ns = int(alignment["formal_stop_shared_ns"])

    decoded_sweeps: Counter[str] = Counter()
    in_window_sweeps: Counter[str] = Counter()
    solve_failures: Counter[str] = Counter()
    times: dict[str, list[float]] = defaultdict(list)
    positions: dict[str, list[np.ndarray]] = defaultdict(list)
    anchors_used: dict[str, list[int]] = defaultdict(list)
    gdop: dict[str, list[float]] = defaultdict(list)

    for _, encoded in iter_cobs_records(RAW_SLICE):
        try:
            frame = _decode_host_frame(encoded)
        except FrameError:
            continue
        node = frame.node_name
        if frame.kind != 1 or node not in models:
            continue
        decoded_sweeps[node] += 1
        temporary = np.empty(1, UWB_DTYPE)
        try:
            _decode_uwb(frame, temporary, 0)
        except FrameError:
            solve_failures[f"{node}:transport_decode"] += 1
            continue
        row = temporary[0]
        global_ns = int(models[node].map_ns(int(row["strobe_us"])))
        if not start_ns <= global_ns <= stop_ns:
            continue
        in_window_sweeps[node] += 1
        try:
            observation = frontend.solve(
                node_id=node,
                sweep=int(row["sweep"]),
                global_time_ns=global_ns,
                global_time_sigma_ns=int(round(models[node].sigma_ns)),
                anchor_ids=row["anchor_id"],
                ranges_mm=row["range_mm"],
                quality=row["quality"],
                valid_mask=int(row["valid_mask"]),
                t_round_us=row["t_round_us"],
            )
        except (RuntimeError, ValueError, np.linalg.LinAlgError) as error:
            solve_failures[f"{node}:{type(error).__name__}"] += 1
            continue
        if observation is None:
            solve_failures[f"{node}:no_solution"] += 1
            continue
        times[node].append((observation.effective_time_ns - start_ns) * 1e-9)
        positions[node].append(np.asarray(observation.xyz_m, dtype=np.float64))
        anchors_used[node].append(len(observation.anchors_used))
        gdop[node].append(float(observation.gdop))

    trajectories: dict[str, dict[str, np.ndarray]] = {}
    per_node: dict[str, Any] = {}
    for node in NODES:
        time = np.asarray(times[node], dtype=np.float64)
        xyz = np.asarray(positions[node], dtype=np.float64)
        if time.size == 0 or xyz.shape != (time.size, 3):
            raise RuntimeError(f"{node}: no H01 T4 point solutions")
        order = np.argsort(time, kind="stable")
        time = time[order]
        xyz = xyz[order]
        used = np.asarray(anchors_used[node], dtype=np.int16)[order]
        gdop_values = np.asarray(gdop[node], dtype=np.float64)[order]
        if not np.all(np.isfinite(xyz)) or np.any(np.diff(time) <= 0.0):
            raise RuntimeError(f"{node}: invalid solved trajectory")
        trajectories[node] = {
            "time_s": time,
            "xyz_m": xyz,
            "anchors_used": used,
            "gdop": gdop_values,
        }
        per_node[node] = {
            "role_zh": NODE_ROLES[node],
            "decoded_slice_sweeps": int(decoded_sweeps[node]),
            "formal_window_sweeps": int(in_window_sweeps[node]),
            "t4_solution_count": int(time.size),
            "time_range_s": [float(time[0]), float(time[-1])],
            "median_xyz_m": np.median(xyz, axis=0).tolist(),
            "minimum_xyz_m": np.min(xyz, axis=0).tolist(),
            "maximum_xyz_m": np.max(xyz, axis=0).tolist(),
            "median_anchors_used": float(np.median(used)),
            "minimum_anchors_used": int(np.min(used)),
            "median_gdop": float(np.median(gdop_values)),
        }

    audit = {
        "schema": "biospur-c2-h01-uwb-only-point-video-v1",
        "scope": "SEALED_C2_H01_UWB_ONLY_DIAGNOSTIC_NOT_FUSION_NOT_PASS",
        "action": ACTION,
        "action_instruction_zh": record["instruction_zh"],
        "formal_duration_s": (stop_ns - start_ns) * 1e-9,
        "formal_start_shared_ns": start_ns,
        "formal_stop_shared_ns": stop_ns,
        "inputs": {
            "raw_slice": str(RAW_SLICE),
            "raw_slice_sha256": _sha256(RAW_SLICE),
            "expected_raw_slice_sha256": record["slice_sha256"],
            "layout": str(LAYOUT_PATH),
            "layout_sha256": _sha256(LAYOUT_PATH),
            "event_path": record["event_path"],
            "event_sha256": record["event_sha256"],
            "range_path": record["range_path"],
            "range_sha256": record["range_sha256"],
        },
        "position_owner": "existing biospur_fusion.uwb.frontend.CanonicalT4Frontend",
        "position_semantics": (
            "per-node A-H raw ranges mapped to the established common clock; "
            "canonical T4 point solution; solved NLOS outliers retained"
        ),
        "display_policy": {
            "avatar": False,
            "imu_translation": False,
            "ik_fk": False,
            "body_constraints": False,
            "action_semantic_pose_truth": False,
            "position_smoothing": False,
            "long_gap_interpolation": False,
            "video_frame_policy": (
                "latest solved point held for at most 0.30 s; otherwise hidden; "
                "trails connect only adjacent solves separated by <=0.30 s"
            ),
            "trail_seconds": TRAIL_SECONDS,
        },
        "per_node": per_node,
        "solve_failures": dict(sorted(solve_failures.items())),
    }
    if audit["inputs"]["raw_slice_sha256"] != audit["inputs"]["expected_raw_slice_sha256"]:
        raise RuntimeError("sealed H01 raw slice hash changed")
    return trajectories, audit


def _box_edges(minimum: np.ndarray, maximum: np.ndarray) -> list[np.ndarray]:
    corners = np.array(
        [
            [x, y, z]
            for x in (minimum[0], maximum[0])
            for y in (minimum[1], maximum[1])
            for z in (minimum[2], maximum[2])
        ],
        dtype=np.float64,
    )
    edges = []
    for index, first in enumerate(corners):
        for second in corners[index + 1 :]:
            if np.count_nonzero(np.abs(first - second) > 1e-12) == 1:
                edges.append(np.stack((first, second)))
    return edges


def _visible_sample(row: dict[str, np.ndarray], time_s: float) -> int | None:
    index = int(np.searchsorted(row["time_s"], time_s, side="right") - 1)
    if index < 0 or time_s - float(row["time_s"][index]) > MAX_DISPLAY_AGE_SECONDS:
        return None
    return index


def _trail(row: dict[str, np.ndarray], index: int) -> np.ndarray:
    time = row["time_s"]
    start = int(np.searchsorted(time, time[index] - TRAIL_SECONDS, side="left"))
    selected = np.arange(start, index + 1, dtype=int)
    if selected.size <= 1:
        return row["xyz_m"][selected]
    gaps = np.diff(time[selected])
    if np.any(gaps > MAX_DISPLAY_AGE_SECONDS):
        last_gap = int(np.flatnonzero(gaps > MAX_DISPLAY_AGE_SECONDS)[-1])
        selected = selected[last_gap + 1 :]
    return row["xyz_m"][selected]


def _render_video(
    path: Path,
    trajectories: dict[str, dict[str, np.ndarray]],
    anchors: np.ndarray,
    anchor_names: list[str],
    duration_s: float,
) -> int:
    background = "#0d1218"
    grid = "#39424c"
    figure = plt.figure(figsize=(16, 9), dpi=100, facecolor=background)
    axis = figure.add_subplot(111, projection="3d", facecolor=background)
    figure.subplots_adjust(left=0.02, right=0.82, top=0.94, bottom=0.04)

    anchor_min = np.min(anchors, axis=0)
    anchor_max = np.max(anchors, axis=0)
    inner_edges = _box_edges(anchor_min, anchor_max)
    margin = np.array([0.50, 0.50, 0.45])
    outer_min = anchor_min - margin
    outer_max = anchor_max + margin
    for line in _box_edges(outer_min, outer_max):
        axis.plot(*line.T, color="#8f98a3", linewidth=1.1, alpha=0.55)
    for line in inner_edges:
        axis.plot(*line.T, color="#2386c8", linewidth=1.0, linestyle="--", alpha=0.65)
    axis.scatter(
        anchors[:, 0], anchors[:, 1], anchors[:, 2],
        s=35, c="#ffc45b", edgecolors="#513e1e", linewidths=0.6, depthshade=False,
    )
    for name, point in zip(anchor_names, anchors, strict=True):
        axis.text(*point, f"  {name}", color="#ffc45b", fontsize=9, weight="bold")

    points = {}
    trails = {}
    labels = {}
    for node in NODES:
        color = NODE_COLORS[node]
        points[node] = axis.scatter(
            [], [], [], s=58, c=color, edgecolors="#0b0e12", linewidths=0.8,
            depthshade=False, label=f"{node}  {NODE_ROLES_EN[node]}",
        )
        (trails[node],) = axis.plot([], [], [], color=color, linewidth=2.0, alpha=0.58)
        labels[node] = axis.text(0.0, 0.0, 0.0, "", color=color, fontsize=7.5, weight="bold")

    axis.set_xlim(float(outer_min[0]), float(outer_max[0]))
    axis.set_ylim(float(outer_min[1]), float(outer_max[1]))
    axis.set_zlim(float(outer_min[2]), float(outer_max[2]))
    axis.set_box_aspect(tuple(outer_max - outer_min))
    axis.view_init(elev=28.0, azim=-58.0)
    axis.set_proj_type("persp", focal_length=0.9)
    axis.set_xlabel("world X / m", color="#aeb8c3", labelpad=8)
    axis.set_ylabel("world Y / m", color="#aeb8c3", labelpad=8)
    axis.set_zlabel("world Z / m", color="#aeb8c3", labelpad=8)
    axis.tick_params(colors="#78838e", labelsize=8)
    for pane in (axis.xaxis.pane, axis.yaxis.pane, axis.zaxis.pane):
        pane.set_facecolor(background)
        pane.set_edgecolor(grid)
        pane.set_alpha(0.06)
    for axis_component in (axis.xaxis, axis.yaxis, axis.zaxis):
        axis_component._axinfo["grid"]["color"] = (0.20, 0.24, 0.28, 0.45)
        axis_component._axinfo["grid"]["linewidth"] = 0.6

    title = figure.text(
        0.025, 0.965,
        "Capture2 / H01 boxing / ten body-worn UWB point trajectories",
        color="#f4f7fa", fontsize=17, weight="bold", va="top",
    )
    subtitle = figure.text(
        0.025, 0.925,
        "A-H raw ranges -> existing canonical T4 / no avatar / no IMU translation / no smoothing",
        color="#9fb0bf", fontsize=10, va="top",
    )
    time_text = figure.text(
        0.025, 0.875, "0.0 s", color="#55e6d1", fontsize=20, weight="bold", va="top",
    )
    count_text = figure.text(
        0.025, 0.838, "", color="#b9c4ce", fontsize=10, va="top",
    )
    legend = axis.legend(
        loc="upper left", bbox_to_anchor=(1.03, 1.00), borderaxespad=0.0,
        frameon=False, fontsize=8.5, labelcolor="#dce3ea", handletextpad=0.5,
    )
    for handle in legend.legend_handles:
        handle.set_alpha(1.0)

    frame_count = int(math.floor(duration_s * FPS)) + 1
    writer = FFMpegWriter(
        fps=FPS,
        codec="libx264",
        bitrate=5000,
        extra_args=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
        metadata={
            "title": "BioSpur C2 H01 UWB-only ten-node point trajectories",
            "comment": "No avatar, no IMU translation, no IK/FK, no smoothing",
        },
    )
    with writer.saving(figure, str(path), dpi=100):
        for frame in range(frame_count):
            time_s = min(frame / FPS, duration_s)
            visible = 0
            for node in NODES:
                row = trajectories[node]
                index = _visible_sample(row, time_s)
                if index is None:
                    points[node]._offsets3d = ([], [], [])
                    trails[node].set_data([], [])
                    trails[node].set_3d_properties([])
                    labels[node].set_text("")
                    continue
                visible += 1
                point = row["xyz_m"][index]
                points[node]._offsets3d = ([point[0]], [point[1]], [point[2]])
                trail = _trail(row, index)
                trails[node].set_data(trail[:, 0], trail[:, 1])
                trails[node].set_3d_properties(trail[:, 2])
                labels[node].set_position((point[0], point[1]))
                labels[node].set_3d_properties(point[2])
                labels[node].set_text(f" {node[-4:]}")
            time_text.set_text(f"{time_s:4.1f} s")
            count_text.set_text(f"visible positions: {visible}/10  /  trail: {TRAIL_SECONDS:.1f} s")
            writer.grab_frame(facecolor=background)
    plt.close(figure)
    del title, subtitle
    return frame_count


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or WORKSPACE / "logs" / f"c2_h01_uwb_points_video_{stamp}"
    output.mkdir(parents=True, exist_ok=False)

    trajectories, audit = _decode_positions()
    anchors, anchor_names = _layout_points()
    archive_values: dict[str, np.ndarray] = {
        "anchors_world_m": anchors,
        "anchor_names": np.asarray(anchor_names),
    }
    for node, row in trajectories.items():
        archive_values[f"{node}/time_s"] = row["time_s"]
        archive_values[f"{node}/xyz_m"] = row["xyz_m"]
        archive_values[f"{node}/anchors_used"] = row["anchors_used"]
        archive_values[f"{node}/gdop"] = row["gdop"]
    archive_path = output / "H01_UWB_T4_POINT_TRAJECTORIES.npz"
    np.savez(archive_path, **archive_values)

    video_path = output / "H01_UWB_ONLY_10_NODE_POINTS_45DEG_10FPS.mp4"
    frame_count = _render_video(
        video_path,
        trajectories,
        anchors,
        anchor_names,
        float(audit["formal_duration_s"]),
    )
    audit["outputs"] = {
        "trajectory_archive": str(archive_path),
        "trajectory_archive_sha256": _sha256(archive_path),
        "video": str(video_path),
        "video_sha256": _sha256(video_path),
        "video_fps": FPS,
        "video_frame_count": frame_count,
        "video_duration_s": frame_count / FPS,
        "camera": {"elevation_deg": 28.0, "azimuth_deg": -58.0, "fixed": True},
    }
    audit_path = output / "H01_UWB_ONLY_POINT_VIDEO_AUDIT.json"
    audit_path.write_text(json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), "video": str(video_path), "audit": str(audit_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
