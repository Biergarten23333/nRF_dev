"""Attributable A/B display-proxy rendering for diagnostic inspection."""

from __future__ import annotations

from pathlib import Path
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .contracts import LINK_ROWS, POINT_NAMES, DisplayGeometry
from .proxy import forward_points


def render_ab_montage(
    path: Path,
    rows: list[tuple[str, np.ndarray, np.ndarray]],
    geometry: DisplayGeometry,
    output_matrix: np.ndarray,
    *,
    title: str,
) -> None:
    views = ((0, 2, "front"), (1, 2, "side"), (0, 1, "top"))
    figure = plt.figure(figsize=(12.0, 3.7 * len(rows)), constrained_layout=True)
    for row_index, (name, a_matrices, b_matrices) in enumerate(rows):
        points_a = forward_points(a_matrices, geometry, output_matrix)
        points_b = forward_points(b_matrices, geometry, output_matrix)
        all_values = np.concatenate(
            [np.stack(list(points_a.values())), np.stack(list(points_b.values()))], axis=0
        )
        for column, (horizontal, vertical, view_name) in enumerate(views):
            axis = figure.add_subplot(len(rows), 3, row_index * 3 + column + 1)
            for pose_points, color, label, linestyle in (
                (points_a, "#555555", "A direct FK", "--"),
                (points_b, "#c62828", "B constrained (rejected)", "-"),
            ):
                first = True
                for _, start, end in LINK_ROWS:
                    line = np.stack([pose_points[start], pose_points[end]])
                    axis.plot(
                        line[:, horizontal],
                        line[:, vertical],
                        color=color,
                        linestyle=linestyle,
                        linewidth=1.8,
                        alpha=0.9,
                        label=label if first else None,
                    )
                    first = False
                values = np.stack([pose_points[name] for name in POINT_NAMES])
                axis.scatter(
                    values[:, horizontal], values[:, vertical], color=color, s=9, alpha=0.8
                )
            projected = all_values[:, [horizontal, vertical]]
            minimum = np.min(projected, axis=0)
            maximum = np.max(projected, axis=0)
            center = 0.5 * (minimum + maximum)
            radius = max(float(np.max(maximum - minimum)) * 0.58, 0.12)
            axis.set_xlim(center[0] - radius, center[0] + radius)
            axis.set_ylim(center[1] - radius, center[1] + radius)
            axis.set_aspect("equal", adjustable="box")
            axis.set_title(f"{name} — {view_name}")
            axis.set_axis_off()
            if row_index == 0 and column == 0:
                axis.legend(loc="upper left", fontsize=8)
    figure.suptitle(title)
    figure.savefig(path, dpi=160)
    plt.close(figure)
