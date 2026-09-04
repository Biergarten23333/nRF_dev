#!/usr/bin/env python3
"""Run native-200-Hz analytic hinge IK on C2 calibration and H01/H02."""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from biospur_fusion.c2_articulated_biomechanics import (
    apply_orientation_constrained_ik,
    fit_articulated_model,
    hinge_coordinate_deg,
)
from biospur_fusion.c2_coupled_progressive.contracts import ROOT
from biospur_fusion.c2_coupled_progressive.estimator import SEGMENTS
from tools.build_c2_avatar_interactive import _load_trajectory


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _load_hxx(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"trajectory": {}}
    with np.load(path, allow_pickle=False) as archive:
        for episode in ("H01_boxing", "H02_golf"):
            result["trajectory"][episode] = {}
            for segment in SEGMENTS:
                base = f"trajectory/{episode}/{segment}"
                result["trajectory"][episode][segment] = {
                    "time_root_s": np.array(archive[f"{base}/time_root_s"]),
                    "quat_world_segment_wxyz": np.array(
                        archive[f"{base}/quat_world_segment_wxyz"]
                    ),
                    "mask": np.array(archive[f"{base}/mask"], dtype=bool),
                }
        result["output_coordinate_convention"] = {
            "schema": "biospur-c2-capture-wide-output-coordinates-v1",
            "matrix_world_output_from_internal": np.array(
                archive["output_coordinates/matrix_world_output_from_internal"]
            ),
            "plane_normal_world_internal": np.array(
                archive["output_coordinates/plane_normal_world_internal"]
            ),
        }
    return result


def _save_trajectory(path: Path, trajectory: dict[str, Any]) -> dict[str, Any]:
    payload: dict[str, np.ndarray] = {}
    for episode, segments in trajectory["trajectory"].items():
        for segment, values in segments.items():
            for field, value in values.items():
                payload[f"trajectory/{episode}/{segment}/{field}"] = np.asarray(value)
    convention = trajectory["output_coordinate_convention"]
    payload["output_coordinates/matrix_world_output_from_internal"] = np.asarray(
        convention["matrix_world_output_from_internal"]
    )
    payload["output_coordinates/plane_normal_world_internal"] = np.asarray(
        convention["plane_normal_world_internal"]
    )
    np.savez_compressed(path, **payload)
    return {
        "path": str(path.relative_to(ROOT)),
        "sha256": _sha256(path),
        "array_count": len(payload),
    }


def _serializable(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        episode: {
            joint: {key: value for key, value in row.items() if key != "flexion_deg"}
            for joint, row in joints.items()
        }
        for episode, joints in metrics.items()
    }


def _probes(
    original: dict[str, Any],
    corrected: dict[str, Any],
    metrics: dict[str, Any],
    model: dict[str, Any],
) -> list[dict[str, Any]]:
    episode = "H01_boxing"
    joint = model["elbow_right"]
    parent = original["trajectory"][episode][joint.parent]
    old_child = original["trajectory"][episode][joint.child]
    new_child = corrected["trajectory"][episode][joint.child]
    old_signed = hinge_coordinate_deg(
        parent["quat_world_segment_wxyz"],
        old_child["quat_world_segment_wxyz"],
        joint,
    )
    new_signed = hinge_coordinate_deg(
        parent["quat_world_segment_wxyz"],
        new_child["quat_world_segment_wxyz"],
        joint,
    )
    flexion = metrics[episode]["elbow_right"]["flexion_deg"]
    time_s = np.asarray(parent["time_root_s"], dtype=float)
    rows = []
    for elapsed in (12.915, 13.635, 18.375):
        frame = int(np.argmin(np.abs(time_s - (time_s[0] + elapsed))))
        rows.append({
            "elapsed_s": float(time_s[frame] - time_s[0]),
            "frame": frame,
            "old_signed_projection_deg": float(old_signed[frame]),
            "observed_long_axis_bend_deg": float(flexion[frame]),
            "reconstructed_hinge_coordinate_deg": float(new_signed[frame]),
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--calibration-run", type=Path, required=True)
    parser.add_argument("--hxx-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    calibration_run = args.calibration_run.resolve()
    hxx_run = args.hxx_run.resolve()
    output = args.output.resolve()
    for path in (calibration_run, hxx_run, output):
        if ROOT != path and ROOT not in path.parents:
            raise SystemExit("all paths must remain in canonical Fusion_Part")
    output.mkdir(parents=False, exist_ok=False)
    started = time.monotonic()

    calibration_report_path = calibration_run / "POSE_RESET_QMT_DIAGNOSTIC.json"
    calibration_report = json.loads(calibration_report_path.read_text(encoding="utf-8"))
    calibration_path = ROOT / calibration_report["trajectory"]["path"]
    if _sha256(calibration_path) != calibration_report["trajectory"]["sha256"]:
        raise RuntimeError("calibration trajectory hash mismatch")
    calibration = _load_trajectory(calibration_path)

    hxx_report_path = hxx_run / "HXX_FROZEN_C2_REPLAY_REPORT.json"
    hxx_report = json.loads(hxx_report_path.read_text(encoding="utf-8"))
    hxx_path = ROOT / hxx_report["trajectory"]["path"]
    if _sha256(hxx_path) != hxx_report["trajectory"]["sha256"]:
        raise RuntimeError("Hxx trajectory hash mismatch")
    hxx = _load_hxx(hxx_path)

    model = fit_articulated_model(calibration, calibration_report)
    calibration_fixed, calibration_metrics = apply_orientation_constrained_ik(
        calibration, model
    )
    hxx_fixed, hxx_metrics = apply_orientation_constrained_ik(hxx, model)
    calibration_artifact = _save_trajectory(
        output / "ARTICULATED_CALIBRATION_TRAJECTORY.npz", calibration_fixed
    )
    hxx_artifact = _save_trajectory(
        output / "ARTICULATED_HXX_TRAJECTORY.npz", hxx_fixed
    )
    all_rows = [
        row
        for collection in (calibration_metrics, hxx_metrics)
        for joints in collection.values()
        for row in joints.values()
    ]
    mechanism_pass = bool(all(
        row["fk_direction_residual_maximum_deg"] <= 3e-6
        for row in all_rows
    ))
    probes = _probes(hxx, hxx_fixed, hxx_metrics, model)
    model_report = {
        "schema": "biospur-c2-native200-orientation-ik-model-v1",
        "sample_rate_hz": 200.0,
        "ball_joints": "measured segment orientation retained",
        "hinge_flexion": "native parent/distal long-axis angle",
        "hinge_plane": "QMT/Olsson functional axis from registered 06/07/10/11 actions",
        "distal_axial_twist": "measured pronation/supination retained",
        "rom_deg": {
            name: [joint.minimum_deg, joint.maximum_deg]
            for name, joint in model.items()
        },
        "fk_owner": "biospur_fusion.c2_coupled_progressive.renderer.joints_for_frame",
        "joint_centres_and_lengths": "unchanged frozen C2 display-proxy geometry",
    }
    _write_json(output / "ARTICULATED_MODEL.json", model_report)
    common = {
        "status": "NATIVE200_ANALYTIC_HINGE_IK_VISUAL_CANDIDATE",
        "mechanism_pass": mechanism_pass,
        "scientific_pass": False,
        "sample_rate_hz": 200.0,
        "pose_interpolation": False,
        "biomechanics_active": True,
        "model": model_report,
    }
    _write_json(output / "POSE_RESET_QMT_DIAGNOSTIC.json", {
        "schema": "biospur-c2-native200-orientation-ik-calibration-v1",
        **common,
        "trajectory": calibration_artifact,
        "metrics": _serializable(calibration_metrics),
    })
    _write_json(output / "HXX_FROZEN_C2_REPLAY_REPORT.json", {
        "schema": "biospur-c2-native200-orientation-ik-hxx-v1",
        **common,
        "calibration_refit_on_hxx": False,
        "viewer_ik_rebase_retarget_or_repair": False,
        "frozen_calibration": hxx_report["frozen_calibration"],
        "trajectory": hxx_artifact,
        "source_records": hxx_report["source_records"],
        "metrics": _serializable(hxx_metrics),
        "right_elbow_probes": probes,
    })
    final = {
        "schema": "biospur-c2-native200-orientation-ik-result-v1",
        **common,
        "calibration_trajectory": calibration_artifact,
        "hxx_trajectory": hxx_artifact,
        "right_elbow_probes": probes,
        "wall_s": float(time.monotonic() - started),
    }
    _write_json(output / "FINAL_RESULT.json", final)
    print(json.dumps(final, indent=2, sort_keys=True))
    return 0 if mechanism_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
