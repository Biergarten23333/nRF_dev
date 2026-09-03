"""Run bounded official-OpenSim probes for the frozen functional joint mapping."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path

import numpy as np

from biospur_fusion.c2_3a_kinematics import (
    load_frozen_c2_3a,
    load_frozen_c2_hxx_diagnostics,
)
from biospur_fusion.c2_fk_to_opensim_ik.adapter import orientation_error_summary

from .functional_axis import HINGE_DOMINANT_ACTIONS
from .pipeline import (
    BODY_BY_SEGMENT,
    FRAME_BY_SEGMENT,
    configure_opensim_log,
    replay_model,
    run_ik,
    sha256_file,
)
from .render import a_points, render_episode


BASE_EVIDENCE = "logs/c2_fk_to_scaled_opensense_20260903_004220"
OLD_EPISODE_DIR = {
    "04": "all_19plus2/primary_04",
    "05": "all_19plus2/primary_05",
    "06": "episodes_attempt_002_body_names/06_upper_dynamic",
    "07": "all_19plus2/primary_07",
    "08": "all_19plus2/primary_08",
    "09": "all_19plus2/primary_09",
    "10": "episodes_attempt_002_body_names/10_lower_dynamic",
    "11": "all_19plus2/primary_11",
    "01": "episodes_attempt_002_body_names/02_t_pose",
    "H01_boxing": "episodes_attempt_002_body_names/H01_boxing",
    "H02_golf": "episodes_attempt_002_body_names/H02_golf",
}
DISPLAY_LABEL = {
    "01": "02_t_pose",
    "04": "04_shoulder_left",
    "05": "05_shoulder_right",
    "06": "06_elbow_left",
    "07": "07_elbow_right",
    "08": "08_hip_left",
    "09": "09_hip_right",
    "10": "10_knee_left",
    "11": "11_knee_right",
    "H01_boxing": "H01_boxing",
    "H02_golf": "H02_golf",
}
TARGET_SENSORS = {
    "04": ("torso_imu", "humerus_l_imu"),
    "05": ("torso_imu", "humerus_r_imu"),
    "06": ("humerus_l_imu", "ulna_l_imu"),
    "07": ("humerus_r_imu", "ulna_r_imu"),
    "08": ("pelvis_imu", "femur_l_imu"),
    "09": ("pelvis_imu", "femur_r_imu"),
    "10": ("femur_l_imu", "tibia_l_imu"),
    "11": ("femur_r_imu", "tibia_r_imu"),
}
TARGET_COORDINATE = {
    action.episode: action.coordinate for action in HINGE_DOMINANT_ACTIONS
}


def _vec3(values):
    import opensim as osim

    return osim.Vec3(*(float(value) for value in values))


def run_signed_fixture(model_path: Path, output_dir: Path) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    output_dir.mkdir(parents=True, exist_ok=True)
    configure_opensim_log(output_dir / "opensim.log")
    model = osim.Model(str(model_path.resolve()))
    coordinates = model.updCoordinateSet()
    labels = [FRAME_BY_SEGMENT[segment] for segment in BODY_BY_SEGMENT]
    rows = []
    truth = []
    for action in HINGE_DOMINANT_ACTIONS:
        for delta in (-0.1, 0.1):
            state = model.initSystem()
            coordinate = coordinates.get(action.coordinate)
            q0 = 0.5 * (
                float(coordinate.getRangeMin()) + float(coordinate.getRangeMax())
            )
            requested = q0 + delta
            coordinate.setValue(state, requested, False)
            model.assemble(state)
            model.realizePosition(state)
            assembled = float(coordinate.getValue(state))
            quaternions = []
            for segment in BODY_BY_SEGMENT:
                frame = model.getComponent(
                    f"/bodyset/{BODY_BY_SEGMENT[segment]}/{FRAME_BY_SEGMENT[segment]}"
                )
                quaternion = frame.getRotationInGround(
                    state
                ).convertRotationToQuaternion()
                quaternions.append(
                    ",".join(f"{quaternion.get(i):.17g}" for i in range(4))
                )
            row = len(rows)
            rows.append(f"{row * 0.01:.2f}\t" + "\t".join(quaternions))
            truth.append(
                {
                    "joint": action.joint,
                    "coordinate": action.coordinate,
                    "q0_rad": q0,
                    "delta_rad": delta,
                    "requested_rad": requested,
                    "assembled_rad": assembled,
                }
            )

    input_path = output_dir / "interior_signed_truth.sto"
    input_path.write_text(
        "DataRate=100\nDataType=Quaternion\nversion=3\nOpenSimVersion=4.6\n"
        "endheader\ntime\t"
        + "\t".join(labels)
        + "\n"
        + "\n".join(rows)
        + "\n"
    )
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(model_path.resolve()))
    tool.set_orientations_file(str(input_path.resolve()))
    tool.set_sensor_to_opensim_rotations(_vec3((0.0, 0.0, 0.0)))
    tool.set_time_range(0, 0.0)
    tool.set_time_range(1, 0.07)
    tool.set_results_directory(str(output_dir.resolve()))
    tool.set_output_motion_file("interior_signed_ik.sto")
    tool.set_report_errors(True)
    tool.set_accuracy(1e-7)
    if not tool.run(False):
        raise RuntimeError("official signed interior IK fixture failed")
    motion_path = output_dir / "interior_signed_ik.sto"
    table = osim.TimeSeriesTable(str(motion_path.resolve()))
    for row, expected in enumerate(truth):
        degrees = float(
            np.asarray(
                table.getDependentColumn(expected["coordinate"]).to_numpy(),
                dtype=float,
            )[row]
        )
        observed = math.radians(degrees)
        expected["official_motion_degrees"] = degrees
        expected["official_motion_rad"] = observed
        expected["error_to_assembled_rad"] = abs(observed - expected["assembled_rad"])
        expected["sign_preserved_about_q0"] = bool(
            math.copysign(1.0, observed - expected["q0_rad"])
            == math.copysign(1.0, expected["delta_rad"])
        )
    errors_path = output_dir / "interior_signed_ik.sto_orientationErrors.sto"
    result = {
        "engine": "official OpenSim IMUInverseKinematicsTool",
        "model_sha256": sha256_file(model_path),
        "rows": len(truth),
        "one_official_ik_call": True,
        "fixture": truth,
        "orientation_errors": orientation_error_summary(errors_path),
        "all_signs_preserved": all(item["sign_preserved_about_q0"] for item in truth),
        "motion_coordinates_source_units": "degree (inDegrees=yes)",
        "orientation_errors_source_units": "radian (official direct output; no inDegrees metadata)",
        "wall_s": time.monotonic() - started,
    }
    (output_dir / "SIGNED_FIXTURE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def _load_episode(workspace: Path, key: str):
    if key.startswith("H"):
        return load_frozen_c2_hxx_diagnostics(workspace=workspace).episodes[key]
    return load_frozen_c2_3a(workspace=workspace).episodes[key]


def run_episode(workspace: Path, model_path: Path, output_root: Path, key: str) -> dict[str, object]:
    episode = _load_episode(workspace, key)
    output_dir = output_root / "episodes" / DISPLAY_LABEL[key]
    result = run_ik(model_path, episode, output_dir)
    (output_dir / "EPISODE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def analyze_episode(workspace: Path, model_path: Path, output_root: Path, key: str) -> dict[str, object]:
    frozen = load_frozen_c2_3a(workspace=workspace)
    episode = _load_episode(workspace, key)
    output_dir = output_root / "episodes" / DISPLAY_LABEL[key]
    _, points, rom = replay_model(
        model_path, output_dir / "official_ik.sto", output_dir / "analysis_opensim.log"
    )
    rendered = render_episode(
        frozen,
        episode,
        key,
        DISPLAY_LABEL[key],
        points,
        output_root / "rendering" / f"{DISPLAY_LABEL[key]}_ab_front_side_top.png",
    )
    changes = []
    for row, b_points in enumerate(points):
        direct = a_points(episode, frozen.geometry, row)
        changes.extend(
            float(np.linalg.norm(direct[name] - b_points[name])) for name in direct
        )
    result = {
        "render": rendered,
        "rom": rom,
        "point_change_m": {
            "mean": float(np.mean(changes)),
            "p95": float(np.quantile(changes, 0.95)),
            "max": float(np.max(changes)),
        },
    }
    (output_dir / "ANALYSIS_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def _motion_column(path: Path, name: str) -> np.ndarray:
    lines = path.read_text(encoding="utf-8").splitlines()
    header = next(index for index, line in enumerate(lines) if line.strip() == "endheader")
    labels = lines[header + 1].split("\t")
    column = labels.index(name)
    return np.asarray([float(line.split("\t")[column]) for line in lines[header + 2 :] if line.strip()])


def compare_calibration(workspace: Path, model_path: Path, output_root: Path) -> dict[str, object]:
    import opensim as osim

    configure_opensim_log(output_root / "comparison_opensim.log")
    model = osim.Model(str(model_path.resolve()))
    coordinates = model.getCoordinateSet()
    ranges = {
        coordinates.get(index).getName(): (
            float(coordinates.get(index).getRangeMin()),
            float(coordinates.get(index).getRangeMax()),
        )
        for index in range(coordinates.getSize())
    }
    prior_root = workspace / BASE_EVIDENCE
    episodes = {}
    for key in ("04", "05", "06", "07", "08", "09", "10", "11"):
        old_root = prior_root / OLD_EPISODE_DIR[key]
        new_root = output_root / "episodes" / DISPLAY_LABEL[key]
        old_errors = json.loads((old_root / "EPISODE_RESULT.json").read_text())["orientation_errors"]
        new_errors = json.loads((new_root / "EPISODE_RESULT.json").read_text())["orientation_errors"]
        sensor_names = TARGET_SENSORS[key]
        target = {}
        for statistic in ("mean_rad", "p95_rad"):
            target["old_" + statistic] = float(
                np.mean([old_errors["sensors"][name][statistic] for name in sensor_names])
            )
            target["new_" + statistic] = float(
                np.mean([new_errors["sensors"][name][statistic] for name in sensor_names])
            )
        item = {
            "mapping_changed": key in TARGET_COORDINATE,
            "target_sensors": sensor_names,
            "target_pair": target,
            "overall_old": {
                name: old_errors[name]
                for name in ("overall_mean_rad", "overall_p95_rad", "overall_max_rad")
            },
            "overall_new": {
                name: new_errors[name]
                for name in ("overall_mean_rad", "overall_p95_rad", "overall_max_rad")
            },
        }
        if key in TARGET_COORDINATE:
            coordinate = TARGET_COORDINATE[key]
            old_values = np.radians(_motion_column(old_root / "official_ik.sto", coordinate))
            lo, hi = ranges[coordinate]
            old_near = int(
                np.count_nonzero(
                    (old_values <= lo + math.radians(1.0))
                    | (old_values >= hi - math.radians(1.0))
                )
            )
            new_analysis = json.loads((new_root / "ANALYSIS_RESULT.json").read_text())
            new_near = int(new_analysis["rom"]["within_1deg_of_limit_count"][coordinate])
            item["target_coordinate"] = coordinate
            item["target_near_limit_rows"] = {"old": old_near, "new": new_near}
            item["directional_pass"] = bool(
                target["new_mean_rad"] < target["old_mean_rad"]
                and target["new_p95_rad"] < target["old_p95_rad"]
                and new_near <= old_near
            )
        episodes[key] = item
    changed = [episodes[key] for key in ("06", "07", "10", "11")]
    result = {
        "schema": "biospur-c2-functional-calibration-comparison-v1",
        "selection_used_holdout": False,
        "criteria_frozen_before_holdout": "all four changed hinge/knee target-pair mean and p95 strictly decrease and target-coordinate near-ROM count does not increase",
        "episodes": episodes,
        "changed_joint_directional_pass_count": sum(item["directional_pass"] for item in changed),
        "changed_joint_count": len(changed),
        "holdout_eligible": all(item["directional_pass"] for item in changed),
    }
    (output_root / "CALIBRATION_COMPARISON.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("signed", "episode", "analyze", "compare"))
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--key")
    args = parser.parse_args()
    if args.command == "signed":
        result = run_signed_fixture(args.model, args.output / "signed_fixture")
    elif args.command == "episode":
        result = run_episode(args.workspace, args.model, args.output, args.key)
    elif args.command == "analyze":
        result = analyze_episode(args.workspace, args.model, args.output, args.key)
    else:
        result = compare_calibration(args.workspace, args.model, args.output)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
