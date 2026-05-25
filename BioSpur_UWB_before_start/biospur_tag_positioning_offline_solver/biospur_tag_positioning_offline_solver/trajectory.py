from __future__ import annotations

import json
from pathlib import Path

from .c_solver import TagPositionSolver
from .capture_io import read_tr_all_frames, summarize_anchor_counts
from .layout_io import load_layout_json
from .models import SolveResult, SolverConfig, TrajectoryResult


def solve_capture_trajectory(
    layout_path: str | Path,
    capture_path: str | Path,
    method: str = "T1",
    anchor_sigma_path: str | Path | None = None,
    tags: set[str] | None = None,
    tag_delay_by_tag: dict[str, float] | None = None,
    max_frames: int = 0,
    tail_rows: int = 0,
) -> TrajectoryResult:
    config = SolverConfig(method=method)  # type: ignore[arg-type]
    layout = load_layout_json(layout_path, anchor_sigma_path)
    frames = read_tr_all_frames(capture_path, tags=tags, min_anchors=config.min_anchors, tail_rows=tail_rows)
    if max_frames > 0:
        frames = frames[:max_frames]
    imu_frames = [f for f in frames if f.imu is not None and f.imu.valid]
    solver = TagPositionSolver(layout, config, tag_delay_by_tag=tag_delay_by_tag)
    results: list[SolveResult] = []
    for frame in frames:
        result = solver.solve_frame(frame)
        if result is not None:
            results.append(result)
    return TrajectoryResult(
        layout_path=str(layout_path),
        method=config.method,
        frames_input=len(frames),
        frames_solved=len(results),
        results=results,
        metadata={
            "capture_path": str(capture_path),
            "anchor_sigma_path": str(anchor_sigma_path) if anchor_sigma_path else "",
            "tag_delay_by_tag": tag_delay_by_tag or {},
            "anchor_count_distribution": summarize_anchor_counts(frames),
            "imu_frames_valid": len(imu_frames),
            "imu_frames_total": len(frames),
        },
    )


def trajectory_to_jsonable(result: TrajectoryResult) -> dict:
    return {
        "layout_path": result.layout_path,
        "method": result.method,
        "frames_input": result.frames_input,
        "frames_solved": result.frames_solved,
        "metadata": result.metadata,
        "points": [
            {
                "tag": row.tag,
                "sweep": row.sweep,
                "host_elapsed_s": row.host_elapsed_s,
                "host_epoch_s": row.host_epoch_s,
                "x_mm": row.x_mm,
                "y_mm": row.y_mm,
                "z_mm": row.z_mm,
                "anchors_used": row.anchors_used,
                "anchors_input": row.anchors_input,
                "rejected_anchor_id": row.rejected_anchor_id,
                "residual_rms_mm": row.residual_rms_mm,
                "residual_p95_abs_mm": row.residual_p95_abs_mm,
                "max_abs_residual_mm": row.max_abs_residual_mm,
                "residuals_by_anchor": row.residuals_by_anchor,
                "used_by_anchor": row.used_by_anchor,
                "imu_sample_count": row.imu_sample_count,
                "imu_acc_norm_std_mg": row.imu_acc_norm_std_mg,
                "imu_prior_scale": row.imu_prior_scale,
                "temporal_prior_sigma_used_mm": row.temporal_prior_sigma_used_mm,
            }
            for row in result.results
        ],
    }


def write_trajectory_json(result: TrajectoryResult, out: str | Path) -> None:
    p = Path(out)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(trajectory_to_jsonable(result), indent=2), encoding="utf-8")
