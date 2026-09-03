"""Add one soft out-of-plane elbow DOF to the accepted Rajagopal model.

This adapter replaces only the two OpenSim PinJoints with official
UniversalJoints.  Rotation 1 preserves the original PinJoint Z axis and
rotation 2 uses the original Rajagopal joint-frame X axis.  All original
parent/child frame translations, the neutral pose, model constraints, IMU
placement, and other joints remain unchanged.
"""

from __future__ import annotations

import gc
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from biospur_fusion.c2_fk_to_opensim_ik.adapter import (
    C2_FROM_OPENSIM,
    SENSOR_TO_OPENSIM_XYZ_RAD,
    _rotation_wxyz,
    _table_columns,
    _write_scalar_sto,
    orientation_error_summary,
)
from biospur_fusion.c2_fk_to_scaled_opensense.pipeline import (
    BODY_BY_SEGMENT,
    configure_opensim_log,
    sha256_file,
)


SOURCE_MODEL_SHA256 = "7b66b82bc932e161f08405cc409b786774ff0038354c0e937adea0a5d02717ea"
OUT_OF_PLANE_WEIGHT = 0.001
ORIENTATION_WEIGHT = 1.0
SOLVER_ACCURACY = 1e-7
CONSTRAINT_WEIGHT = math.inf
ELBOW_FRAME_BASIS = np.array(
    [[0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]], dtype=float
)
CANDIDATE_BODY_BY_SEGMENT = {
    **BODY_BY_SEGMENT,
    "forearm_left": "radius_l",
    "forearm_right": "radius_r",
}
CANDIDATE_FRAME_BY_SEGMENT = {
    segment: f"{body}_imu" for segment, body in CANDIDATE_BODY_BY_SEGMENT.items()
}


def _matrix(rotation) -> np.ndarray:
    return np.array(
        [[rotation.get(i, j) for j in range(3)] for i in range(3)], dtype=float
    )


def _vector(vector) -> list[float]:
    return [float(vector.get(i)) for i in range(3)]


def _rotation(matrix: np.ndarray):
    import opensim as osim

    return osim.Rotation(osim.Mat33(*(float(value) for value in matrix.reshape(-1))))


def _rotation_distance(first: np.ndarray, second: np.ndarray) -> float:
    cosine = np.clip((np.trace(first.T @ second) - 1.0) / 2.0, -1.0, 1.0)
    return float(np.arccos(cosine))


def _frame_transform(frame) -> tuple[np.ndarray, np.ndarray]:
    offset = frame.getOffsetTransform()
    return _matrix(offset.R()), np.asarray(_vector(offset.p()), dtype=float)


def _body_state(model, state) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    result = {}
    for index in range(model.getBodySet().getSize()):
        body = model.getBodySet().get(index)
        result[body.getName()] = (
            _matrix(body.getRotationInGround(state)),
            np.asarray(_vector(body.getPositionInGround(state)), dtype=float),
        )
    return result


def _replace_elbow(model, side: str) -> dict[str, object]:
    import opensim as osim

    name = f"elbow_{side}"
    old = model.getJointSet().get(name)
    if old.getConcreteClassName() != "PinJoint":
        raise RuntimeError(f"{name} is not the expected PinJoint")
    parent_offset = osim.PhysicalOffsetFrame.safeDownCast(old.getParentFrame())
    child_offset = osim.PhysicalOffsetFrame.safeDownCast(old.getChildFrame())
    if parent_offset is None or child_offset is None:
        raise RuntimeError(f"{name} does not own PhysicalOffsetFrame endpoints")
    parent_rotation, parent_translation = _frame_transform(parent_offset)
    child_rotation, child_translation = _frame_transform(child_offset)
    source_flex = model.getCoordinateSet().get(f"elbow_flex_{side}")
    flex_policy = {
        "default_rad": float(source_flex.getDefaultValue()),
        "range_rad": [float(source_flex.getRangeMin()), float(source_flex.getRangeMax())],
        "default_locked": bool(source_flex.getDefaultLocked()),
        "default_clamped": bool(source_flex.getDefaultClamped()),
    }
    parent_body = model.getBodySet().get(f"humerus_{side}")
    child_body = model.getBodySet().get(f"ulna_{side}")
    parent_orientation = _rotation(
        parent_rotation @ ELBOW_FRAME_BASIS
    ).convertRotationToBodyFixedXYZ()
    child_orientation = _rotation(
        child_rotation @ ELBOW_FRAME_BASIS
    ).convertRotationToBodyFixedXYZ()
    replacement = osim.UniversalJoint(
        name,
        parent_body,
        osim.Vec3(*parent_translation),
        parent_orientation,
        child_body,
        osim.Vec3(*child_translation),
        child_orientation,
    )
    flex = replacement.updCoordinate(osim.UniversalJoint.Coord_Rotation1X)
    flex.setName(f"elbow_flex_{side}")
    flex.setDefaultValue(flex_policy["default_rad"])
    flex.setRangeMin(flex_policy["range_rad"][0])
    flex.setRangeMax(flex_policy["range_rad"][1])
    flex.setDefaultLocked(flex_policy["default_locked"])
    flex.setDefaultClamped(flex_policy["default_clamped"])
    out = replacement.updCoordinate(osim.UniversalJoint.Coord_Rotation2Y)
    out.setName(f"elbow_oop_{side}")
    out.setDefaultValue(0.0)
    out.setDefaultLocked(False)
    out.setDefaultClamped(False)
    out_policy = {
        "default_rad": float(out.getDefaultValue()),
        "range_rad": [float(out.getRangeMin()), float(out.getRangeMax())],
        "default_locked": bool(out.getDefaultLocked()),
        "default_clamped": bool(out.getDefaultClamped()),
    }
    index = model.updJointSet().getIndex(name)
    if not model.updJointSet().set(index, replacement):
        raise RuntimeError(f"official JointSet.set failed for {name}")
    # Set::set adopts this component but OpenSim 4.6 SWIG leaves thisown true;
    # disowning prevents Python from deleting the model-owned component.
    replacement.thisown = False
    return {
        "joint": name,
        "before_type": "PinJoint",
        "after_type": "UniversalJoint",
        "parent_body": parent_body.getName(),
        "child_body": child_body.getName(),
        "parent_translation_m": parent_translation.tolist(),
        "child_translation_m": child_translation.tolist(),
        "parent_rotation_before": parent_rotation.tolist(),
        "child_rotation_before": child_rotation.tolist(),
        "proper_basis": ELBOW_FRAME_BASIS.tolist(),
        "proper_basis_det": float(np.linalg.det(ELBOW_FRAME_BASIS)),
        "rotation1_x_in_new_frame_equals_old_pin_z": True,
        "rotation2_y_in_new_frame_equals_old_joint_x": True,
        "flex_coordinate_policy_inherited_exactly": flex_policy,
        "out_of_plane_coordinate_policy": out_policy,
    }


def configure_model(source_model: Path, output_model: Path, output_root: Path) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    if sha256_file(source_model) != SOURCE_MODEL_SHA256:
        raise RuntimeError("accepted placement-model hash changed")
    configure_opensim_log(output_root / "configure_opensim.log")
    baseline = osim.Model(str(source_model.resolve()))
    baseline_state = baseline.initSystem()
    baseline.realizePosition(baseline_state)
    baseline_bodies = _body_state(baseline, baseline_state)
    baseline_joints = {
        baseline.getJointSet().get(i).getName(): baseline.getJointSet().get(i).getConcreteClassName()
        for i in range(baseline.getJointSet().getSize())
    }

    model = osim.Model(str(source_model.resolve()))
    changes = [_replace_elbow(model, side) for side in ("l", "r")]
    model.finalizeConnections()
    state = model.initSystem()
    model.realizePosition(state)
    candidate_bodies = _body_state(model, state)
    rotation_errors = {
        name: _rotation_distance(baseline_bodies[name][0], candidate_bodies[name][0])
        for name in baseline_bodies
    }
    position_errors = {
        name: float(np.linalg.norm(baseline_bodies[name][1] - candidate_bodies[name][1]))
        for name in baseline_bodies
    }
    # Matrix-trace angle extraction has a sqrt(machine-epsilon) floor near
    # identity; the observed position comparison remains at roundoff.
    if max(rotation_errors.values()) > 1e-7 or max(position_errors.values()) > 1e-10:
        raise RuntimeError("elbow replacement changed the model neutral pose")
    candidate_joints = {
        model.getJointSet().get(i).getName(): model.getJointSet().get(i).getConcreteClassName()
        for i in range(model.getJointSet().getSize())
    }
    changed_joint_types = {
        name: [baseline_joints[name], candidate_joints[name]]
        for name in baseline_joints
        if baseline_joints[name] != candidate_joints[name]
    }
    if set(changed_joint_types) != {"elbow_l", "elbow_r"}:
        raise RuntimeError("a non-elbow joint type changed")
    pro_sup_policy = {}
    for side in ("l", "r"):
        pro_sup = model.getCoordinateSet().get(f"pro_sup_{side}")
        if pro_sup.getLocked(state):
            raise RuntimeError("source radius model pro_sup must remain unlocked")
        pro_sup_policy[f"pro_sup_{side}"] = {
            "range_rad": [float(pro_sup.getRangeMin()), float(pro_sup.getRangeMax())],
            "default_rad": float(pro_sup.getDefaultValue()),
            "locked": False,
            "clamped": bool(pro_sup.getClamped(state)),
        }
        model.getComponent(f"/bodyset/radius_{side}/radius_{side}_imu")

    output_model.parent.mkdir(parents=True, exist_ok=True)
    model.printToXML(str(output_model.resolve()))
    result = {
        "schema": "biospur.c2.rajagopal_soft_elbow_model.v1",
        "source_model": str(source_model.resolve()),
        "source_model_sha256": SOURCE_MODEL_SHA256,
        "output_model": str(output_model.resolve()),
        "output_model_sha256": sha256_file(output_model),
        "opensim_version": osim.GetVersionAndDate(),
        "changes": changes,
        "changed_joint_types": changed_joint_types,
        "all_other_joint_types_unchanged": True,
        "neutral_max_rotation_error_rad": max(rotation_errors.values()),
        "neutral_max_position_error_m": max(position_errors.values()),
        "joint_centres_and_unmeasured_geometry": "unchanged generic Rajagopal model-derived; not subject anatomical truth",
        "measured_scaling": "inherited unchanged from accepted source model",
        "imu_placement": "inherited unchanged from accepted 02 placement model; no IMUPlacer rerun",
        "radioulnar_pro_sup": pro_sup_policy,
        "forearm_observation_body": {"left": "radius_l", "right": "radius_r"},
        "out_of_plane_axis_owner": "existing Rajagopal elbow joint-frame X axis; diagnostic generic-model direction",
        "out_of_plane_reference_weight": OUT_OF_PLANE_WEIGHT,
        "out_of_plane_range_owner": "official OpenSim UniversalJoint Coordinate native default chart; unclamped",
        "out_of_plane_reference_weight_owner": "conservative engineering-only weak prior: ratio 0.001 retained 0.09796 rad of the fixed 0.1-rad noiseless OOP truth in c2_fk_to_rajagopal_soft_elbow_weight_sensitivity_20260903_010401; no covariance, optimality, or scientific ownership claimed",
        "functional_axis_used_as_hard_axis": False,
        "wall_s": time.monotonic() - started,
    }
    (output_root / "MODEL_OWNERSHIP.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _write_orientation_table(episode, output: Path) -> dict[str, object]:
    reference = episode.segments["pelvis"].time_root_s
    elapsed = reference - reference[0]
    labels = [CANDIDATE_FRAME_BY_SEGMENT[segment] for segment in BODY_BY_SEGMENT]
    lines = [
        f"DataRate={1.0 / float(np.median(np.diff(elapsed))):.17g}",
        "DataType=Quaternion",
        "version=3",
        "OpenSimVersion=4.6",
        "endheader",
        "time\t" + "\t".join(labels),
    ]
    for row, time_s in enumerate(elapsed):
        values = []
        for segment in BODY_BY_SEGMENT:
            quaternion = episode.segments[segment].quat_world_segment_wxyz[row]
            values.append(",".join(f"{float(value):.17g}" for value in quaternion))
        lines.append(f"{float(time_s):.17g}\t" + "\t".join(values))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "episode": episode.key,
        "rows": len(elapsed),
        "labels": labels,
        "quaternion_values_changed": False,
        "time_origin_shift_only": True,
        "sha256": sha256_file(output),
    }


def _coordinate_references(osim, out_of_plane_weight: float):
    references = osim.SimTKArrayCoordinateReference()
    owners = []
    for side in ("l", "r"):
        name = f"elbow_oop_{side}"
        function = osim.Constant(0.0)
        reference = osim.CoordinateReference(name, function)
        reference.setWeight(float(out_of_plane_weight))
        references.push_back(reference)
        owners.append((function, reference))
    return references, owners


def _prepare_solver_inputs(model_path: Path, input_path: Path):
    """Load immutable model/orientation inputs once for a bounded profile study."""
    import opensim as osim

    model = osim.Model(str(model_path.resolve()))
    table = osim.TimeSeriesTableQuaternion(str(input_path.resolve()))
    basis = osim.Rotation(SENSOR_TO_OPENSIM_XYZ_RAD[0], osim.Vec3(1.0, 0.0, 0.0))
    osim.OpenSenseUtilities.rotateOrientationTable(table, basis)
    rotations = osim.OpenSenseUtilities.convertQuaternionsToRotations(table)
    coordinates = model.updCoordinateSet()
    return SimpleNamespace(
        model=model,
        orientations=osim.OrientationsReference(rotations),
        markers=osim.MarkersReference(),
        times=np.asarray(rotations.getIndependentColumn(), dtype=float),
        coordinate_names=[
            coordinates.get(i).getName() for i in range(coordinates.getSize())
        ],
        coordinate_ranges={
            coordinates.get(i).getName(): [
                float(coordinates.get(i).getRangeMin()),
                float(coordinates.get(i).getRangeMax()),
            ]
            for i in range(coordinates.getSize())
        },
    )


def _run_solver(
    model_path: Path,
    input_path: Path,
    output_dir: Path,
    *,
    independent_rows: bool = False,
    out_of_plane_weight: float = OUT_OF_PLANE_WEIGHT,
    prepared_inputs=None,
) -> dict[str, object]:
    import opensim as osim

    started = time.monotonic()
    configure_opensim_log(output_dir / "opensim.log")
    prepared = prepared_inputs or _prepare_solver_inputs(model_path, input_path)
    model = prepared.model
    orientations = prepared.orientations
    orientations.setDefaultWeight(ORIENTATION_WEIGHT)
    markers = prepared.markers
    times = prepared.times
    coordinates = model.updCoordinateSet()
    coordinate_names = prepared.coordinate_names
    sensor_names = []
    motion_rows = []
    error_rows = []
    state = None
    solver = None
    references = None
    owners = None
    for row, time_s in enumerate(times):
        if independent_rows or solver is None:
            state = model.initSystem()
            references, owners = _coordinate_references(osim, out_of_plane_weight)
            solver = osim.InverseKinematicsSolver(
                model, markers, orientations, references, CONSTRAINT_WEIGHT
            )
            solver.setAccuracy(SOLVER_ACCURACY)
        state.setTime(float(time_s))
        # Each row is a new externally supplied measurement chart. OpenSim's
        # track() advances the solver's internal prior solution and does not
        # reinitialize that chart; the validated FK->IK adapter lifecycle is
        # therefore one shared solver/state with official assemble() per row.
        solver.assemble(state)
        if not sensor_names:
            # OpenSim constructs the orientation assembly condition during
            # assemble(); querying sensor indices before this point is an
            # invalid lifecycle call and caused the preserved exit-139 probes.
            sensor_names = [
                solver.getOrientationSensorNameForIndex(i)
                for i in range(solver.getNumOrientationSensorsInUse())
            ]
        motion_rows.append(
            [math.degrees(float(coordinates.get(i).getValue(state))) for i in range(coordinates.getSize())]
        )
        errors = osim.SimTKArrayDouble()
        solver.computeCurrentOrientationErrors(errors)
        error_rows.append([float(errors.getElt(i)) for i in range(errors.size())])
        if independent_rows:
            # Destroy the SWIG-wrapped result array before the solver that
            # populated it. Reversing this order triggered an OpenSim 4.6
            # process-exit fault in the preserved fixture attempts.
            del errors, solver, references, owners, state
            solver = references = owners = state = None
            gc.collect()
    motion = output_dir / "official_ik.sto"
    error = output_dir / "official_ik.sto_orientationErrors.sto"
    _write_scalar_sto(
        motion, times, coordinate_names, motion_rows, name="official_ik.sto", in_degrees=True
    )
    _write_scalar_sto(
        error, times, sensor_names, error_rows, name="OrientationErrors", in_degrees=False
    )
    return {
        "engine": "official OpenSim::InverseKinematicsSolver",
        "opensim_version": osim.GetVersionAndDate(),
        "official_coordinate_references": {
            f"elbow_oop_{side}": {"value_rad": 0.0, "weight": out_of_plane_weight}
            for side in ("l", "r")
        },
        "coordinate_reference_owner_count": 2,
        "orientation_weight": ORIENTATION_WEIGHT,
        "constraint_weight": "Infinity (official model constraints exact)",
        "accuracy": SOLVER_ACCURACY,
        "lifecycle": (
            "fresh official solver plus assemble for every independent model-generated fixture row"
            if independent_rows
            else "one official solver/state; externally supplied measurement time followed by official assemble on every row"
        ),
        "motion": str(motion.resolve()),
        "motion_sha256": sha256_file(motion),
        "errors": str(error.resolve()),
        "errors_sha256": sha256_file(error),
        "log_sha256": sha256_file(output_dir / "opensim.log"),
        "wall_s": time.monotonic() - started,
    }


def _generated_fixture_episode(model_path: Path):
    import opensim as osim

    model = osim.Model(str(model_path.resolve()))
    coordinates = model.updCoordinateSet()
    specifications = []
    rows = {segment: [] for segment in BODY_BY_SEGMENT}
    mechanisms = {
        "pure_flex": np.array([1.0, 0.0, 0.0]),
        "pure_pro_sup": np.array([0.0, 1.0, 0.0]),
        "pure_oop": np.array([0.0, 0.0, 1.0]),
        "flex_plus_pro_sup": np.array([1.0, -1.0, 0.0]),
        "flex_plus_oop": np.array([1.0, 0.0, -1.0]),
        "pro_sup_plus_oop": np.array([0.0, 1.0, -1.0]),
        "mixed_all": np.array([1.0, -1.0, 1.0]),
    }
    for side in ("l", "r"):
        names = [f"elbow_flex_{side}", f"pro_sup_{side}", f"elbow_oop_{side}"]
        centers = np.array(
            [
                0.5 * (coordinates.get(names[0]).getRangeMin() + coordinates.get(names[0]).getRangeMax()),
                0.5 * (coordinates.get(names[1]).getRangeMin() + coordinates.get(names[1]).getRangeMax()),
                0.0,
            ],
            dtype=float,
        )
        for mechanism, direction in mechanisms.items():
            for sign in (-1.0, 1.0):
                generated = centers + sign * 0.1 * direction
                state = model.initSystem()
                for coordinate, value in zip(names, generated, strict=True):
                    coordinates.get(coordinate).setValue(state, float(value), False)
                model.realizePosition(state)
                specifications.append(
                    {
                        "side": side,
                        "mechanism": mechanism,
                        "sign": int(sign),
                        "coordinate_names": names,
                        "centers_rad": centers.tolist(),
                        "generated_rad": generated.tolist(),
                        "target_mask": (direction != 0.0).tolist(),
                    }
                )
                for segment in BODY_BY_SEGMENT:
                    frame = model.getComponent(
                        f"/bodyset/{CANDIDATE_BODY_BY_SEGMENT[segment]}/{CANDIDATE_FRAME_BY_SEGMENT[segment]}"
                    )
                    rotation_os = _matrix(frame.getRotationInGround(state))
                    rows[segment].append(_rotation_wxyz(_rotation(C2_FROM_OPENSIM @ rotation_os)))
    times = np.arange(len(specifications), dtype=float) * 0.01
    series = {
        segment: SimpleNamespace(
            time_root_s=times,
            quat_world_segment_wxyz=np.asarray(values),
            mask=np.ones(len(times), dtype=bool),
        )
        for segment, values in rows.items()
    }
    episode = SimpleNamespace(
        key="official_model_generated_signed_interior",
        segments=series,
        frame_count=len(times),
        valid_frame_mask=np.ones(len(times), dtype=bool),
    )
    return episode, specifications


def _assess_fixture(
    model_path: Path,
    output_dir: Path,
    specifications: list[dict[str, object]],
    input_manifest: dict[str, object],
    official: dict[str, object],
    *,
    out_of_plane_weight: float,
    coordinate_ranges: dict[str, list[float]] | None = None,
) -> dict[str, object]:
    import opensim as osim

    _, motion_deg = _table_columns(Path(official["motion"]))
    if coordinate_ranges is None:
        model = osim.Model(str(model_path.resolve()))
        coordinate_set = model.getCoordinateSet()
        ranges = {
            coordinate_set.get(i).getName(): [
                float(coordinate_set.get(i).getRangeMin()),
                float(coordinate_set.get(i).getRangeMax()),
            ]
            for i in range(coordinate_set.getSize())
        }
    else:
        ranges = coordinate_ranges
    rows = []
    for index, specification in enumerate(specifications):
        names = specification["coordinate_names"]
        centers = np.asarray(specification["centers_rad"], dtype=float)
        generated = np.asarray(specification["generated_rad"], dtype=float)
        observed = np.array(
            [math.radians(float(motion_deg[name][index])) for name in names], dtype=float
        )
        generated_delta = generated - centers
        solved_delta = observed - centers
        target_mask = np.asarray(specification["target_mask"], dtype=bool)
        signed_target_recovery = [
            bool(
                math.copysign(1.0, solved_delta[column])
                == math.copysign(1.0, generated_delta[column])
            )
            for column in np.flatnonzero(target_mask)
        ]
        margins = [
            min(observed[column] - ranges[name][0], ranges[name][1] - observed[column])
            for column, name in enumerate(names)
        ]
        item = {
            **specification,
            "generated_delta_rad": generated_delta.tolist(),
            "solved_rad": observed.tolist(),
            "solved_delta_rad": solved_delta.tolist(),
            "absolute_error_rad": np.abs(observed - generated).tolist(),
            "signed_target_recovery": signed_target_recovery,
            "max_off_target_crosstalk_rad": float(
                np.max(np.abs(solved_delta[~target_mask])) if np.any(~target_mask) else 0.0
            ),
            "minimum_range_margin_rad": float(min(margins)),
        }
        rows.append(item)
    errors = orientation_error_summary(Path(official["errors"]))
    all_signed = all(all(row["signed_target_recovery"]) for row in rows)
    all_target_responses_nonzero = all(
        abs(row["solved_delta_rad"][column]) >= 0.02
        for row in rows
        for column, targeted in enumerate(row["target_mask"])
        if targeted
    )
    max_crosstalk = max(row["max_off_target_crosstalk_rad"] for row in rows)
    min_range_margin = min(row["minimum_range_margin_rad"] for row in rows)
    pure_case_by_coordinate = {
        "elbow_flex": "pure_flex",
        "pro_sup": "pure_pro_sup",
        "elbow_oop": "pure_oop",
    }
    crosstalk_matrices = {}
    for side in ("l", "r"):
        response_names = [f"elbow_flex_{side}", f"pro_sup_{side}", f"elbow_oop_{side}"]
        columns = []
        for coordinate in ("elbow_flex", "pro_sup", "elbow_oop"):
            case = pure_case_by_coordinate[coordinate]
            negative = next(
                row for row in rows
                if row["side"] == side and row["mechanism"] == case and row["sign"] == -1
            )
            positive = next(
                row for row in rows
                if row["side"] == side and row["mechanism"] == case and row["sign"] == 1
            )
            columns.append(
                (np.asarray(positive["solved_rad"]) - np.asarray(negative["solved_rad"])) / 0.2
            )
        crosstalk_matrices[side] = {
            "response_rows": response_names,
            "stimulus_columns": response_names,
            "central_signed_recovery_matrix": np.column_stack(columns).tolist(),
        }
    result = {
        "schema": "biospur.c2.rajagopal_soft_elbow.fixture.v1",
        "out_of_plane_weight": out_of_plane_weight,
        "passed": bool(
            errors["all_finite"]
            and all_signed
            and all_target_responses_nonzero
            and max_crosstalk <= 0.02
            and min_range_margin >= 0.05
        ),
        "pass_rule": "all official orientation errors finite; every targeted coordinate preserves sign and responds by >=0.02 rad to the fixed 0.1-rad excitation; every untargeted coordinate changes <=0.02 rad; every solved coordinate remains >=0.05 rad from its official chart limits",
        "mechanism_case_count": 7,
        "row_count": len(rows),
        "max_off_target_crosstalk_rad": max_crosstalk,
        "minimum_range_margin_rad": min_range_margin,
        "pure_case_crosstalk_matrices": crosstalk_matrices,
        "rows": rows,
        "orientation_errors": errors,
        "input": input_manifest,
        "official": official,
        "wall_s": official["wall_s"],
    }
    (output_dir / "FIXTURE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def run_fixture(
    model_path: Path,
    output_dir: Path,
    *,
    out_of_plane_weight: float = OUT_OF_PLANE_WEIGHT,
) -> dict[str, object]:
    started = time.monotonic()
    episode, specifications = _generated_fixture_episode(model_path)
    input_path = output_dir / "model_generated_orientations.sto"
    input_manifest = _write_orientation_table(episode, input_path)
    official = _run_solver(
        model_path,
        input_path,
        output_dir,
        independent_rows=True,
        out_of_plane_weight=out_of_plane_weight,
    )
    result = _assess_fixture(
        model_path,
        output_dir,
        specifications,
        input_manifest,
        official,
        out_of_plane_weight=out_of_plane_weight,
    )
    result["wall_s"] = time.monotonic() - started
    (output_dir / "FIXTURE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def _summary(values: np.ndarray) -> dict[str, float]:
    return {
        "mean_rad": float(np.mean(values)),
        "median_rad": float(np.median(values)),
        "p95_rad": float(np.quantile(values, 0.95)),
        "max_rad": float(np.max(values)),
    }


def _episode_telemetry(model_path: Path, motion_path: Path, errors_path: Path, key: str) -> dict[str, object]:
    import opensim as osim

    times, motion_deg = _table_columns(motion_path)
    error_times, errors = _table_columns(errors_path)
    if not np.array_equal(times, error_times):
        raise RuntimeError("motion and error time rows differ")
    motion = {name: np.deg2rad(values) for name, values in motion_deg.items()}
    model = osim.Model(str(model_path.resolve()))
    ranges = {
        model.getCoordinateSet().get(i).getName(): (
            float(model.getCoordinateSet().get(i).getRangeMin()),
            float(model.getCoordinateSet().get(i).getRangeMax()),
        )
        for i in range(model.getCoordinateSet().getSize())
    }
    coordinate_names = (
        "elbow_flex_l", "elbow_oop_l", "pro_sup_l",
        "elbow_flex_r", "elbow_oop_r", "pro_sup_r",
    )
    coordinates = {}
    for name in coordinate_names:
        values = motion[name]
        lo, hi = ranges[name]
        coordinates[name] = {
            "range_rad": [lo, hi],
            "min_rad": float(np.min(values)),
            "max_rad": float(np.max(values)),
            "near_1deg_lower_rows": int(np.count_nonzero(values <= lo + math.radians(1.0))),
            "near_1deg_upper_rows": int(np.count_nonzero(values >= hi - math.radians(1.0))),
        }
    phases = {}
    if key in ("06", "07"):
        side = "l" if key == "06" else "r"
        for phase, (start, stop) in {
            "flexion": (0.0, 15.0),
            "pronation_supination": (15.0, 30.0),
        }.items():
            mask = (times >= start) & (times < stop)
            sensors = (f"humerus_{side}_imu", f"radius_{side}_imu")
            pooled = np.concatenate([errors[sensor][mask] for sensor in sensors])
            phases[phase] = {
                "interval_s": [start, stop],
                "rows": int(np.count_nonzero(mask)),
                "target_pair": _summary(pooled),
                "sensors": {sensor: _summary(errors[sensor][mask]) for sensor in sensors},
            }
    return {
        "motion_source_units": "degree (inDegrees=yes), converted exactly once",
        "orientation_error_source_units": "radian (no inDegrees metadata)",
        "coordinates": coordinates,
        "phases": phases,
    }


def run_episode(
    episode,
    model_path: Path,
    output_dir: Path,
    *,
    out_of_plane_weight: float = OUT_OF_PLANE_WEIGHT,
    requested_protocol_label: str | None = None,
    display_label: str | None = None,
) -> dict[str, object]:
    started = time.monotonic()
    input_path = output_dir / "frozen_orientations.sto"
    input_manifest = _write_orientation_table(episode, input_path)
    official = _run_solver(
        model_path,
        input_path,
        output_dir,
        out_of_plane_weight=out_of_plane_weight,
    )
    errors = orientation_error_summary(Path(official["errors"]))
    telemetry = _episode_telemetry(
        model_path, Path(official["motion"]), Path(official["errors"]), episode.key
    )
    result = {
        "schema": "biospur.c2.rajagopal_soft_elbow.episode.v1",
        "episode": episode.key,
        "requested_protocol_label": requested_protocol_label,
        "frozen_source_key": episode.key,
        "display_label": display_label,
        "out_of_plane_weight": out_of_plane_weight,
        "finite": errors["all_finite"],
        "input": input_manifest,
        "official": official,
        "orientation_errors": errors,
        "telemetry": telemetry,
        "wall_s": time.monotonic() - started,
    }
    (output_dir / "EPISODE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result


def run_episode_probe(
    episode,
    model_path: Path,
    output_dir: Path,
    row_indices: tuple[int, ...],
    *,
    out_of_plane_weight: float = OUT_OF_PLANE_WEIGHT,
) -> dict[str, object]:
    """Run a fixed sparse-row lifecycle probe before a full real episode."""
    if not row_indices or min(row_indices) < 0 or max(row_indices) >= episode.frame_count:
        raise ValueError("probe row index is outside the frozen episode")
    segments = {
        name: SimpleNamespace(
            time_root_s=series.time_root_s[list(row_indices)],
            quat_world_segment_wxyz=series.quat_world_segment_wxyz[list(row_indices)],
            mask=np.asarray(series.mask)[list(row_indices)],
        )
        for name, series in episode.segments.items()
    }
    probe = SimpleNamespace(
        key=f"{episode.key}_rows_{'_'.join(str(row) for row in row_indices)}",
        segments=segments,
        frame_count=len(row_indices),
        valid_frame_mask=np.ones(len(row_indices), dtype=bool),
    )
    started = time.monotonic()
    input_path = output_dir / "frozen_orientation_probe.sto"
    input_manifest = _write_orientation_table(probe, input_path)
    official = _run_solver(
        model_path,
        input_path,
        output_dir,
        out_of_plane_weight=out_of_plane_weight,
    )
    errors = orientation_error_summary(Path(official["errors"]))
    result = {
        "schema": "biospur.c2.rajagopal_soft_elbow.lifecycle_probe.v1",
        "episode": episode.key,
        "source_row_indices": list(row_indices),
        "out_of_plane_weight": out_of_plane_weight,
        "passed": bool(errors["all_finite"]),
        "input": input_manifest,
        "official": official,
        "orientation_errors": errors,
        "wall_s": time.monotonic() - started,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "LIFECYCLE_PROBE_RESULT.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return result
