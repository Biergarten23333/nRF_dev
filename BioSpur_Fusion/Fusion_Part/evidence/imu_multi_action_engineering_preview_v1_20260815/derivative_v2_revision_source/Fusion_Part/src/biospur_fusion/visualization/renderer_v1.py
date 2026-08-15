"""Guarded MP4/GIF renderer for calibration-only visualization previews."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable, Mapping

import numpy as np

from .centerline_v1 import DISCLAIMER, TOPOLOGY_EDGES, validate_gates
from .firewall_v1 import CALIBRATION_PREVIEW_ACTIONS, FirewallError


def validate_render_sequence(
    frames: list[Mapping[str, Iterable[float]]],
    timestamps_s: list[float],
    action: str,
    fixed_axes_mm: Mapping[str, list[float]],
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    validate_gates(gates)
    if action not in CALIBRATION_PREVIEW_ACTIONS:
        raise FirewallError(f"action is not calibration-preview authorized: {action}")
    if action in {"walk", "final_still"}:
        raise FirewallError(f"held-out action is sealed: {action}")
    if len(frames) != len(timestamps_s) or not frames:
        raise ValueError("frames and timestamps must have equal non-zero length")
    if any(later <= earlier for earlier, later in zip(timestamps_s, timestamps_s[1:])):
        raise ValueError("timestamps must be strictly increasing")
    required_axes = {"x", "y", "z"}
    if set(fixed_axes_mm) != required_axes:
        raise ValueError("fixed x/y/z axes are required")
    for axis, limits in fixed_axes_mm.items():
        if len(limits) != 2 or float(limits[0]) >= float(limits[1]):
            raise ValueError(f"invalid fixed {axis} limits")
    return {
        "action": action,
        "source_frame_count": len(frames),
        "source_start_s": float(timestamps_s[0]),
        "source_stop_s": float(timestamps_s[-1]),
        "fixed_axes_mm": {axis: [float(value) for value in limits] for axis, limits in fixed_axes_mm.items()},
        "watermark_every_frame": DISCLAIMER,
        "visual_interpolation_analysis_use": "FORBIDDEN",
        "walk_included": False,
        "final_still_included": False,
    }


def interpolate_for_rendering_only(
    frames: list[Mapping[str, Iterable[float]]], timestamps_s: list[float], fps: int
) -> tuple[list[dict[str, list[float]]], list[float]]:
    """Linear visual resampling; output is forbidden as analysis input."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    source_time = np.asarray(timestamps_s, dtype=float)
    target_time = np.arange(source_time[0], source_time[-1] + 0.5 / fps, 1.0 / fps)
    node_names = sorted(set.intersection(*(set(frame) for frame in frames)))
    output: list[dict[str, list[float]]] = []
    for target in target_time:
        rendered: dict[str, list[float]] = {}
        for node in node_names:
            values = np.asarray([list(frame[node]) for frame in frames], dtype=float)
            rendered[node] = [
                float(np.interp(target, source_time, values[:, axis])) for axis in range(3)
            ]
        output.append(rendered)
    return output, [float(value) for value in target_time]


def render_calibration_preview(
    frames: list[Mapping[str, Iterable[float]]],
    timestamps_s: list[float],
    *,
    action: str,
    fixed_axes_mm: Mapping[str, list[float]],
    output_mp4: Path,
    output_gif: Path,
    gates: Mapping[str, Any],
) -> dict[str, Any]:
    """Render only after the caller's firewall has approved the preview plan."""
    audit = validate_render_sequence(frames, timestamps_s, action, fixed_axes_mm, gates)
    rendering = gates["rendering"]
    fps = int(rendering["fps"])
    width_px = int(rendering["width_px"])
    height_px = int(rendering["height_px"])
    if rendering["mp4_codec"] != "h264" or rendering["pixel_format"] != "yuv420p":
        raise ValueError("VISUALIZATION_CENTERLINE_V1 requires H.264/yuv420p output")
    rendered, rendered_times = interpolate_for_rendering_only(frames, timestamps_s, fps)
    import matplotlib.pyplot as plt
    from matplotlib.animation import FFMpegWriter, FuncAnimation, PillowWriter

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    output_gif.parent.mkdir(parents=True, exist_ok=True)
    dpi = 100
    figure = plt.figure(figsize=(width_px / dpi, height_px / dpi), dpi=dpi)
    axes = figure.add_subplot(111, projection="3d")
    axes.set_xlim(*fixed_axes_mm["x"])
    axes.set_ylim(*fixed_axes_mm["y"])
    axes.set_zlim(*fixed_axes_mm["z"])
    axes.set_xlabel("body right (mm)")
    axes.set_ylabel("body anterior (mm)")
    axes.set_zlabel("body superior (mm)")
    axes.set_title(f"{action} — VISUALIZATION_CENTERLINE_V1")
    watermark = figure.text(
        0.5,
        0.015,
        DISCLAIMER,
        ha="center",
        va="bottom",
        fontsize=13,
        color="crimson",
        bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "crimson"},
    )
    artists: list[Any] = [watermark]

    def update(index: int) -> list[Any]:
        while len(artists) > 1:
            artists.pop().remove()
        frame = rendered[index]
        for left, right in TOPOLOGY_EDGES:
            if left not in frame or right not in frame:
                continue
            values = np.asarray([frame[left], frame[right]], dtype=float)
            line, = axes.plot(values[:, 0], values[:, 1], values[:, 2], color="navy", linewidth=2)
            artists.append(line)
        if frame:
            points = np.asarray(list(frame.values()), dtype=float)
            scatter = axes.scatter(points[:, 0], points[:, 1], points[:, 2], color="darkorange", s=28)
            artists.append(scatter)
        axes.set_title(f"{action} — t={rendered_times[index]:.3f}s")
        return artists

    animation = FuncAnimation(figure, update, frames=len(rendered), interval=1000 / fps, blit=False)
    animation.save(
        output_mp4,
        writer=FFMpegWriter(fps=fps, codec="h264", extra_args=["-pix_fmt", "yuv420p"]),
        dpi=dpi,
    )
    animation.save(output_gif, writer=PillowWriter(fps=min(fps, 15)), dpi=max(50, dpi // 2))
    plt.close(figure)
    manifest = {
        "schema": "biospur-visualization-calibration-render-v1",
        **audit,
        "rendered_frame_count": len(rendered),
        "fps": fps,
        "resolution": [width_px, height_px],
        "mp4": str(output_mp4.resolve()),
        "gif": str(output_gif.resolve()),
        "mp4_codec": "h264",
        "pixel_format": "yuv420p",
        "analysis_input": False,
    }
    output_mp4.with_suffix(".manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest
