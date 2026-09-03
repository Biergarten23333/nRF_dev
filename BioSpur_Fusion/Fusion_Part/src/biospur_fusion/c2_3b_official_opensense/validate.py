"""Traceability and descriptive checks for official OpenSense artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .adapter import BODY_BY_SEGMENT, IMU_FRAME_BY_SEGMENT


def validate_orientation_replay(episode, sto_path: Path) -> dict[str, object]:
    """Parse with OpenSim and compare every serialized value to the freeze."""

    import opensim as osim

    table = osim.TimeSeriesTableQuaternion(str(sto_path.resolve()))
    labels = list(table.getColumnLabels())
    expected_segments = tuple(BODY_BY_SEGMENT)
    expected_labels = [IMU_FRAME_BY_SEGMENT[name] for name in expected_segments]
    times = np.asarray(table.getIndependentColumn(), dtype=float)
    expected_times = (
        episode.segments["pelvis"].time_root_s
        - episode.segments["pelvis"].time_root_s[0]
    )
    parsed = np.empty((table.getNumRows(), table.getNumColumns(), 4), dtype=float)
    for row_index in range(table.getNumRows()):
        row = table.getRowAtIndex(row_index)
        for column_index in range(table.getNumColumns()):
            quaternion = row.getElt(0, column_index)
            parsed[row_index, column_index] = [
                quaternion.get(component) for component in range(4)
            ]
    expected = np.stack(
        [
            episode.segments[segment].quat_world_segment_wxyz
            for segment in expected_segments
        ],
        axis=1,
    )
    max_quaternion_abs_error = float(np.max(np.abs(parsed - expected)))
    max_time_abs_error_s = float(np.max(np.abs(times - expected_times)))
    result = {
        "parser": "OpenSim::TimeSeriesTableQuaternion",
        "row_count": int(table.getNumRows()),
        "column_count": int(table.getNumColumns()),
        "labels": labels,
        "expected_labels": expected_labels,
        "labels_exact": labels == expected_labels,
        "rows_exact": table.getNumRows() == episode.frame_count,
        "max_quaternion_abs_error": max_quaternion_abs_error,
        "max_time_abs_error_s": max_time_abs_error_s,
        "quaternion_tolerance": 1e-15,
        "time_tolerance_s": 1e-12,
    }
    result["passed"] = bool(
        result["labels_exact"]
        and result["rows_exact"]
        and max_quaternion_abs_error <= result["quaternion_tolerance"]
        and max_time_abs_error_s <= result["time_tolerance_s"]
    )
    return result


def summarize_official_orientation_errors(error_sto: Path) -> dict[str, object]:
    """Summarize the error angles computed by the official IK solver."""

    import opensim as osim

    table = osim.TimeSeriesTable(str(error_sto.resolve()))
    labels = list(table.getColumnLabels())
    values = np.stack(
        [table.getDependentColumn(label).to_numpy() for label in labels], axis=1
    )
    per_sensor = {}
    for index, label in enumerate(labels):
        column = values[:, index]
        per_sensor[label] = {
            "mean_rad": float(np.mean(column)),
            "median_rad": float(np.median(column)),
            "p95_rad": float(np.quantile(column, 0.95)),
            "max_rad": float(np.max(column)),
            "mean_deg": float(np.degrees(np.mean(column))),
            "p95_deg": float(np.degrees(np.quantile(column, 0.95))),
            "max_deg": float(np.degrees(np.max(column))),
        }
    return {
        "owner": "official OpenSim orientation-error output; descriptive summary only",
        "rows": int(table.getNumRows()),
        "sensors": labels,
        "all_finite": bool(np.all(np.isfinite(values))),
        "overall_mean_rad": float(np.mean(values)),
        "overall_p95_rad": float(np.quantile(values, 0.95)),
        "overall_max_rad": float(np.max(values)),
        "overall_mean_deg": float(np.degrees(np.mean(values))),
        "overall_p95_deg": float(np.degrees(np.quantile(values, 0.95))),
        "overall_max_deg": float(np.degrees(np.max(values))),
        "per_sensor": per_sensor,
    }


def write_pilot_validation(frozen, pilot_root: Path) -> dict[str, object]:
    episode_keys = {"00_initial_still": "00", "02_t_pose": "01"}
    rows: dict[str, object] = {}
    for label, key in episode_keys.items():
        episode_root = pilot_root / label
        replay = validate_orientation_replay(
            frozen.episodes[key], episode_root / "input/frozen_orientations.sto"
        )
        errors = summarize_official_orientation_errors(
            episode_root
            / "official_output/official_ik.sto_orientationErrors.sto"
        )
        rows[label] = {
            "frozen_episode": key,
            "orientation_input_replay": replay,
            "official_orientation_errors": errors,
        }
    result = {
        "schema": "biospur-c2-3b-official-opensense-pilot-validation-v1",
        "episodes": rows,
        "all_input_replay_checks_pass": all(
            row["orientation_input_replay"]["passed"] for row in rows.values()
        ),
    }
    path = pilot_root / "PILOT_VALIDATION.json"
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result
