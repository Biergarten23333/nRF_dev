"""Same-scale causal comparison rendering for official OpenSense outputs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .adapter import BODY_BY_SEGMENT, configure_opensim_log, sha256_file
from .render import (
    VIEWS,
    _plot_pose,
    display_proxy_points,
    official_body_orientations,
)


FRAME_INDICES = (0, 175, 350, 525, 700)
VARIANT_COLORS = {
    "V0_ZERO": "#d62728",
    "V1_BASIS_ONLY": "#1f77b4",
    "V2_DETERMINISTIC_BINDING": "#2ca02c",
}


def _predicted_c2_frame_rows(
    body_rows: list[dict[str, np.ndarray]],
    basis_c: np.ndarray,
    offsets_b_from_f: Mapping[str, np.ndarray],
) -> list[dict[str, np.ndarray]]:
    """Convert official predicted tracked frames back to C2 coordinates."""

    return [
        {
            segment: basis_c.T @ row[segment] @ offsets_b_from_f[segment]
            for segment in BODY_BY_SEGMENT
        }
        for row in body_rows
    ]


def _shared_bounds(point_sets) -> dict[str, dict[str, float]]:
    bounds: dict[str, dict[str, float]] = {}
    for horizontal, vertical, view_name in VIEWS:
        projected = np.concatenate(
            [
                np.stack(list(points.values()))[:, [horizontal, vertical]]
                for points in point_sets
            ],
            axis=0,
        )
        minimum = np.min(projected, axis=0)
        maximum = np.max(projected, axis=0)
        center = 0.5 * (minimum + maximum)
        radius = max(float(np.max(maximum - minimum)) * 0.56, 0.2)
        bounds[view_name] = {
            "x_min": float(center[0] - radius),
            "x_max": float(center[0] + radius),
            "y_min": float(center[1] - radius),
            "y_max": float(center[1] + radius),
        }
    return bounds


def _render_variant(
    *,
    capture_label: str,
    variant: str,
    points_a_by_frame,
    points_b_by_frame,
    bounds,
    output_path: Path,
) -> None:
    figure, axes = plt.subplots(
        len(FRAME_INDICES), 3, figsize=(11.5, 15.5), constrained_layout=True
    )
    for row_index, frame in enumerate(FRAME_INDICES):
        for column_index, (horizontal, vertical, view_name) in enumerate(VIEWS):
            axis = axes[row_index, column_index]
            _plot_pose(
                axis,
                points_a_by_frame[frame],
                horizontal,
                vertical,
                color="#4c4c4c",
                label="A frozen direct FK",
                linestyle="--",
            )
            _plot_pose(
                axis,
                points_b_by_frame[frame],
                horizontal,
                vertical,
                color=VARIANT_COLORS[variant],
                label=f"B official {variant}",
                linestyle="-",
            )
            view_bounds = bounds[view_name]
            axis.set_xlim(view_bounds["x_min"], view_bounds["x_max"])
            axis.set_ylim(view_bounds["y_min"], view_bounds["y_max"])
            axis.set_aspect("equal", adjustable="box")
            axis.set_axis_off()
            axis.set_title(f"frame {frame} | {view_name}")
            if row_index == 0 and column_index == 0:
                axis.legend(loc="upper left", fontsize=7)
    figure.suptitle(
        f"{capture_label}: A unchanged vs B official {variant}\n"
        "Identical per-view scale across A/V0/V1/V2; proxy geometry is display-only",
        fontsize=11,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(output_path, dpi=180)
    plt.close(figure)


def render_causal_comparison(
    *,
    frozen,
    capture_label: str,
    frozen_episode: str,
    variant_motion_model: Mapping[str, tuple[Path, Path]],
    binding: Mapping[str, object],
    output_dir: Path,
) -> dict[str, object]:
    """Render every variant with one preregistered scale per view."""

    output_dir.mkdir(parents=True, exist_ok=True)
    configure_opensim_log(output_dir / f"{capture_label}_render_opensim.log")
    identity = {segment: np.eye(3) for segment in BODY_BY_SEGMENT}
    offsets = {
        variant: (
            {
                segment: np.asarray(
                    binding["segments"][segment]["R_B_from_F"], dtype=float
                )
                for segment in BODY_BY_SEGMENT
            }
            if variant == "V2_DETERMINISTIC_BINDING"
            else identity
        )
        for variant in variant_motion_model
    }
    basis_matrix = np.asarray(binding["basis_C_Rx_minus_pi_over_2"], dtype=float)
    basis = {
        "V0_ZERO": np.eye(3),
        "V1_BASIS_ONLY": basis_matrix,
        "V2_DETERMINISTIC_BINDING": basis_matrix,
    }

    predicted_rows = {}
    row_counts = {}
    for variant, (model_path, motion_path) in variant_motion_model.items():
        times, body_rows = official_body_orientations(model_path, motion_path)
        if len(body_rows) != 701:
            raise ValueError(f"{variant}/{capture_label} does not contain 701 rows")
        predicted_rows[variant] = _predicted_c2_frame_rows(
            body_rows, basis[variant], offsets[variant]
        )
        row_counts[variant] = {
            "rows": len(body_rows),
            "first_time_s": float(times[0]),
            "last_time_s": float(times[-1]),
        }

    points_a = {
        frame: frozen.forward_kinematics(
            frozen_episode, frame, coordinates="display"
        )
        for frame in FRAME_INDICES
    }
    points_b = {
        variant: {
            frame: display_proxy_points(
                frozen, predicted_rows[variant][frame]
            )
            for frame in FRAME_INDICES
        }
        for variant in predicted_rows
    }
    point_sets = list(points_a.values())
    for variant in points_b:
        point_sets.extend(points_b[variant].values())
    bounds = _shared_bounds(point_sets)

    images = {}
    for variant in ("V0_ZERO", "V1_BASIS_ONLY", "V2_DETERMINISTIC_BINDING"):
        output_path = output_dir / f"{capture_label}_{variant}_ab_front_side_top.png"
        _render_variant(
            capture_label=capture_label,
            variant=variant,
            points_a_by_frame=points_a,
            points_b_by_frame=points_b[variant],
            bounds=bounds,
            output_path=output_path,
        )
        images[variant] = {
            "path": str(output_path.resolve()),
            "sha256": sha256_file(output_path),
        }
    return {
        "capture": capture_label,
        "frozen_episode": frozen_episode,
        "frames": list(FRAME_INDICES),
        "views": {name: [horizontal, vertical] for horizontal, vertical, name in VIEWS},
        "shared_bounds": bounds,
        "row_counts": row_counts,
        "images": images,
        "predicted_frame_equation": "R_C2_from_F = transpose(C_variant) @ R_OS_from_B @ R_B_from_F",
        "geometry_scope": "ZERO_POSE_CHANGE_DISPLAY_PROXY_COMPARISON_ONLY",
        "display_geometry_used_by_model_or_solver": False,
    }


def write_render_manifest(result: Mapping[str, object], path: Path) -> None:
    path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
