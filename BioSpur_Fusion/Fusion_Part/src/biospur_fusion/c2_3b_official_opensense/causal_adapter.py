"""Frozen frame-adapter variants for the official OpenSense causal pilot.

This module changes only OpenSim orientation-frame configuration and the
official ``sensor_to_opensim_rotations`` property.  OpenSim remains the sole
owner of inverse kinematics, orientation errors, residuals, and derivatives.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from .adapter import (
    BODY_BY_SEGMENT,
    IMU_FRAME_BY_SEGMENT,
    configure_opensim_log,
    sha256_file,
)


BASIS_EULER_SPACE_XYZ_RAD = (-math.pi / 2.0, 0.0, 0.0)
BINDING_SHA256 = "56dd45af3be6153d258d4d5b4d974b18ca881735e9d9bf809c4c775693c482f1"
ZERO_MODEL_SHA256 = "e5f8878bdfc379957f9a6709df0a9cef3bf9851de5e307dba881edab6e2d0ac9"
INPUT_SHA256_BY_CAPTURE: Mapping[str, str] = {
    "00_initial_still": "27162f17cde88f8bceb94fbf4c67f2a4354b26995d291a12a1f07ae715da6d23",
    "02_t_pose": "e8c5c563b4e8c709b28c71e13b2a06009ee67f68929f37e05298e3529ad5cb29",
}


def _require_sha256(path: Path, expected: str, owner: str) -> None:
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(
            f"{owner} SHA-256 mismatch: expected {expected}, found {actual}: {path}"
        )


def _simtk_rotation(rows: Sequence[Sequence[float]]):
    """Represent one preregistered matrix through official SimTK types."""

    import opensim as osim

    matrix = np.asarray(rows, dtype=float)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        raise ValueError("frame binding must be one finite 3x3 matrix")
    return osim.Rotation(osim.Mat33(*matrix.reshape(-1).tolist()))


def _rotation_numpy(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(row, column) for column in range(3)] for row in range(3)],
        dtype=float,
    )


def configure_deterministic_binding_model(
    zero_model: Path, binding_path: Path, output_model: Path, log_path: Path
) -> dict[str, object]:
    """Apply only the ten frozen proper rotations to existing IMU frames."""

    import opensim as osim

    _require_sha256(zero_model, ZERO_MODEL_SHA256, "V0 configured model")
    _require_sha256(binding_path, BINDING_SHA256, "pre-code frame binding")
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    if binding.get("method") != (
        "UNIQUE_DETERMINISTIC_CANONICAL_FRAME_TO_OFFICIAL_NEUTRAL_FRAME"
    ):
        raise ValueError("unexpected deterministic frame-binding owner")
    if set(binding["segments"]) != set(BODY_BY_SEGMENT):
        raise ValueError("frame-binding segment set differs from the official adapter")

    configure_opensim_log(log_path)
    model = osim.Model(str(zero_model.resolve()))
    applied: dict[str, object] = {}
    for segment, body_name in BODY_BY_SEGMENT.items():
        frame = osim.PhysicalOffsetFrame.safeDownCast(
            model.findComponent(IMU_FRAME_BY_SEGMENT[segment])
        )
        if frame is None:
            raise ValueError(f"missing PhysicalOffsetFrame for {segment}")
        if frame.getParentFrame().getName() != body_name:
            raise ValueError(f"unexpected parent body for {segment}")
        rows = binding["segments"][segment]["R_B_from_F"]
        rotation = _simtk_rotation(rows)
        frame.setOffsetTransform(
            osim.Transform(rotation, osim.Vec3(0.0, 0.0, 0.0))
        )
        applied[segment] = {
            "official_body": body_name,
            "imu_frame": IMU_FRAME_BY_SEGMENT[segment],
            "R_B_from_F": rows,
            "translation_m": [0.0, 0.0, 0.0],
        }

    model.finalizeConnections()
    model.initSystem()
    output_model = output_model.resolve()
    output_model.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model))

    # Reopen the serialized model and independently reconcile every official
    # frame transform with the preregistered matrix before it can be used.
    reopened = osim.Model(str(output_model))
    reopened.initSystem()
    maximum_rotation_abs_error = 0.0
    maximum_translation_abs_m = 0.0
    for segment in BODY_BY_SEGMENT:
        frame = osim.PhysicalOffsetFrame.safeDownCast(
            reopened.findComponent(IMU_FRAME_BY_SEGMENT[segment])
        )
        transform = frame.getOffsetTransform()
        observed = _rotation_numpy(transform.R())
        expected = np.asarray(applied[segment]["R_B_from_F"], dtype=float)
        maximum_rotation_abs_error = max(
            maximum_rotation_abs_error, float(np.max(np.abs(observed - expected)))
        )
        translation = transform.p()
        maximum_translation_abs_m = max(
            maximum_translation_abs_m,
            max(abs(float(translation[index])) for index in range(3)),
        )
    if maximum_rotation_abs_error > 1e-12 or maximum_translation_abs_m != 0.0:
        raise ValueError("serialized V2 PhysicalOffsetFrame reconciliation failed")

    result = {
        "owner": "thin official OpenSim PhysicalOffsetFrame configuration",
        "source_zero_model": str(zero_model.resolve()),
        "source_zero_model_sha256": sha256_file(zero_model),
        "binding": str(binding_path.resolve()),
        "binding_sha256": sha256_file(binding_path),
        "configured_model": str(output_model),
        "configured_model_sha256": sha256_file(output_model),
        "maximum_serialized_rotation_abs_error": maximum_rotation_abs_error,
        "maximum_serialized_translation_abs_m": maximum_translation_abs_m,
        "applied": applied,
        "physical_translation_source": "zero/unobservable",
        "imu_placer_used": False,
        "display_proxy_used_by_model": False,
        "episode_row_used": False,
    }
    manifest_path = output_model.parent / "v2_model_manifest.json"
    manifest_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def run_official_basis_variant(
    *,
    variant: str,
    capture_label: str,
    configured_model: Path,
    orientations_file: Path,
    output_dir: Path,
    first_time_s: float,
    last_time_s: float,
) -> dict[str, object]:
    """Run one frozen variant through the official OpenSense IK tool."""

    import opensim as osim

    if variant not in {"V1_BASIS_ONLY", "V2_DETERMINISTIC_BINDING"}:
        raise ValueError(f"new execution is forbidden for {variant}")
    _require_sha256(
        orientations_file,
        INPUT_SHA256_BY_CAPTURE[capture_label],
        f"{capture_label} immutable input",
    )
    if variant == "V1_BASIS_ONLY":
        _require_sha256(configured_model, ZERO_MODEL_SHA256, "V1 zero-offset model")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "opensim.log"
    configure_opensim_log(log_path)

    motion_name = "official_ik.sto"
    tool = osim.IMUInverseKinematicsTool()
    tool.set_model_file(str(configured_model.resolve()))
    tool.set_orientations_file(str(orientations_file.resolve()))
    tool.set_sensor_to_opensim_rotations(
        osim.Vec3(*BASIS_EULER_SPACE_XYZ_RAD)
    )
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
        raise RuntimeError(f"official OpenSense IK failed for {variant}/{capture_label}")
    result = {
        "variant": variant,
        "capture": capture_label,
        "engine": "OpenSim::IMUInverseKinematicsTool/InverseKinematicsSolver",
        "opensim_version": osim.GetVersionAndDate(),
        "succeeded": succeeded,
        "report_errors": True,
        "sensor_to_opensim_rotations_rad": list(BASIS_EULER_SPACE_XYZ_RAD),
        "orientation_weights": "OFFICIAL_DEFAULT_EQUAL_NO_WEIGHT_OBJECT",
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
        "custom_solver_or_residual": False,
    }
    manifest_path = output_dir / "official_ik_manifest.json"
    manifest_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
