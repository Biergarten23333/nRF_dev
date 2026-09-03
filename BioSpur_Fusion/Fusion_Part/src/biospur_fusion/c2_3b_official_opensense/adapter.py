"""Official OpenSense integration with no project-owned IK mathematics.

This module only converts the immutable C2 orientation table, configures the
official model through OpenSim APIs, and invokes ``IMUInverseKinematicsTool``.
It deliberately contains no optimizer, residual, Jacobian, calibration fit,
or action-dependent model setting.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Mapping

import numpy as np

if TYPE_CHECKING:
    from biospur_fusion.c2_3a_kinematics import FrozenEpisode


BODY_BY_SEGMENT: Mapping[str, str] = {
    "pelvis": "pelvis",
    "torso": "torso",
    "upper_arm_left": "humerus_l",
    "upper_arm_right": "humerus_r",
    "forearm_left": "ulna_l",
    "forearm_right": "ulna_r",
    "thigh_left": "femur_l",
    "thigh_right": "femur_r",
    "shank_left": "tibia_l",
    "shank_right": "tibia_r",
}
IMU_FRAME_BY_SEGMENT: Mapping[str, str] = {
    segment: f"{segment}_imu" for segment in BODY_BY_SEGMENT
}

# Independent coordinates supported by the ten observed orientations.  The
# official coupled knee-beta coordinates are neither changed nor unlocked.
ENABLED_COORDINATES = frozenset(
    {
        "pelvis_tilt",
        "pelvis_list",
        "pelvis_rotation",
        "hip_flexion_r",
        "hip_adduction_r",
        "hip_rotation_r",
        "knee_angle_r",
        "hip_flexion_l",
        "hip_adduction_l",
        "hip_rotation_l",
        "knee_angle_l",
        "lumbar_extension",
        "lumbar_bending",
        "lumbar_rotation",
        "arm_flex_r",
        "arm_add_r",
        "arm_rot_r",
        "elbow_flex_r",
        "arm_flex_l",
        "arm_add_l",
        "arm_rot_l",
        "elbow_flex_l",
    }
)
COUPLED_COORDINATES = frozenset({"knee_angle_r_beta", "knee_angle_l_beta"})


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def configure_opensim_log(log_path: Path) -> None:
    """Route OpenSim output away from every upstream input directory."""

    import opensim as osim

    log_path.parent.mkdir(parents=True, exist_ok=True)
    osim.Logger.removeFileSink()
    osim.Logger.addFileSink(str(log_path.resolve()))


def write_frozen_orientation_sto(
    episode: "FrozenEpisode", output_path: Path
) -> dict[str, object]:
    """Write immutable WXYZ rows to the official OpenSim quaternion format."""

    segments = tuple(BODY_BY_SEGMENT)
    reference_time = episode.segments["pelvis"].time_root_s
    if not np.all(episode.valid_frame_mask):
        raise ValueError(f"masked rows are forbidden in pilot episode {episode.key}")
    for segment in segments:
        series = episode.segments[segment]
        if not np.array_equal(series.time_root_s, reference_time):
            raise ValueError(f"time rows differ in {episode.key}/{segment}")
        if not np.all(series.mask):
            raise ValueError(f"invalid orientation row in {episode.key}/{segment}")

    # Retain exact elapsed intervals while avoiding large absolute-time values
    # in the IK table.  This changes the table origin only, not a quaternion.
    elapsed_s = reference_time - reference_time[0]
    labels = [IMU_FRAME_BY_SEGMENT[segment] for segment in segments]
    lines = [
        f"DataRate={1.0 / float(np.median(np.diff(elapsed_s))):.17g}",
        "DataType=Quaternion",
        "version=3",
        "OpenSimVersion=4.6",
        "endheader",
        "time\t" + "\t".join(labels),
    ]
    for index, time_s in enumerate(elapsed_s):
        quaternions = []
        for segment in segments:
            quat = episode.segments[segment].quat_world_segment_wxyz[index]
            quaternions.append(",".join(f"{value:.17g}" for value in quat))
        lines.append(f"{time_s:.17g}\t" + "\t".join(quaternions))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "episode": episode.key,
        "rows": episode.frame_count,
        "first_source_time_s": float(reference_time[0]),
        "last_source_time_s": float(reference_time[-1]),
        "first_elapsed_time_s": float(elapsed_s[0]),
        "last_elapsed_time_s": float(elapsed_s[-1]),
        "quaternion_values_changed": False,
        "time_origin_shift_only": True,
        "labels": labels,
        "sha256": sha256_file(output_path),
    }


def configure_official_model(source_model: Path, output_model: Path) -> dict[str, object]:
    """Add zero-offset IMU frames and lock unobserved official coordinates."""

    import opensim as osim

    model = osim.Model(str(source_model.resolve()))
    coordinate_set = model.updCoordinateSet()
    coordinate_policy: dict[str, str] = {}
    for index in range(coordinate_set.getSize()):
        coordinate = coordinate_set.get(index)
        name = coordinate.getName()
        if name in COUPLED_COORDINATES:
            coordinate_policy[name] = "official_coupled_coordinate_unchanged"
            continue
        if name in {"pro_sup_l", "pro_sup_r"}:
            coordinate.setDefaultValue(0.0)
            coordinate.setDefaultLocked(True)
            coordinate_policy[name] = "official_default_zero_locked_unobserved"
        elif name in ENABLED_COORDINATES:
            coordinate.setDefaultLocked(False)
            coordinate_policy[name] = "enabled_by_observed_segment_orientation"
        else:
            coordinate.setDefaultLocked(True)
            coordinate_policy[name] = "locked_unobserved"

    for segment, body_name in BODY_BY_SEGMENT.items():
        body = model.updBodySet().get(body_name)
        frame = osim.PhysicalOffsetFrame()
        frame.setName(IMU_FRAME_BY_SEGMENT[segment])
        frame.setParentFrame(body)
        frame.set_translation(osim.Vec3(0.0, 0.0, 0.0))
        frame.set_orientation(osim.Vec3(0.0, 0.0, 0.0))
        body.addComponent(frame)

    model.finalizeConnections()
    model.initSystem()
    output_path = output_model.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_path))
    return {
        "source_model": str(source_model.resolve()),
        "source_model_sha256": sha256_file(source_model),
        "configured_model": str(output_path),
        "configured_model_sha256": sha256_file(output_path),
        "body_by_segment": dict(BODY_BY_SEGMENT),
        "imu_frame_by_segment": dict(IMU_FRAME_BY_SEGMENT),
        "imu_translation_m": [0.0, 0.0, 0.0],
        "imu_orientation_xyz_rad": [0.0, 0.0, 0.0],
        "coordinate_policy": coordinate_policy,
        "subject_specific_anthropometry_inserted": False,
        "display_proxy_used_by_model": False,
    }


def run_official_imu_ik(
    configured_model: Path,
    orientations_file: Path,
    output_dir: Path,
    *,
    first_time_s: float,
    last_time_s: float,
) -> dict[str, object]:
    """Invoke official OpenSim 4.6 IMU IK and preserve its native outputs."""

    import opensim as osim

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "opensim.log"
    configure_opensim_log(log_path)

    motion_name = "official_ik.sto"
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(configured_model.resolve()))
    tool.set_orientations_file(str(orientations_file.resolve()))
    tool.set_sensor_to_opensim_rotations(osim.Vec3(0.0, 0.0, 0.0))
    tool.set_time_range(0, float(first_time_s))
    tool.set_time_range(1, float(last_time_s))
    tool.set_results_directory(str(output_dir))
    tool.set_output_motion_file(motion_name)
    tool.set_report_errors(True)
    setup_path = output_dir / "official_imu_ik_setup.xml"
    tool.printToXML(str(setup_path))
    succeeded = bool(tool.run(False))

    motion_path = output_dir / motion_name
    error_path = output_dir / f"{motion_name}_orientationErrors.sto"
    if not succeeded or not motion_path.is_file() or not error_path.is_file():
        raise RuntimeError("official IMU IK did not produce motion and error outputs")
    result = {
        "engine": "OpenSim::IMUInverseKinematicsTool/InverseKinematicsSolver",
        "opensim_version": osim.GetVersionAndDate(),
        "succeeded": succeeded,
        "report_errors": True,
        "sensor_to_opensim_rotations_rad": [0.0, 0.0, 0.0],
        "time_range_s": [float(first_time_s), float(last_time_s)],
        "configured_model": str(configured_model.resolve()),
        "configured_model_sha256": sha256_file(configured_model),
        "orientations_file": str(orientations_file.resolve()),
        "orientations_sha256": sha256_file(orientations_file),
        "setup_xml": str(setup_path),
        "setup_xml_sha256": sha256_file(setup_path),
        "motion_file": str(motion_path),
        "motion_sha256": sha256_file(motion_path),
        "orientation_errors_file": str(error_path),
        "orientation_errors_sha256": sha256_file(error_path),
        "opensim_log": str(log_path),
        "opensim_log_sha256": sha256_file(log_path),
    }
    (output_dir / "official_ik_manifest.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
