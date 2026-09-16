from __future__ import annotations

from dataclasses import replace
import time

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_articulated_biomechanics.model import (
    HINGE_SPECS,
    DOWN,
    HingeJoint,
    _rotation,
    hinge_coordinate_deg,
)
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    _project_hinge_corrections_scalar,
    evaluate_hinge_projection_batch,
    evaluate_varying_base_hinge_projection_batch,
    project_hinge_corrections,
    reconstruct_distal_orientation,
    solve_hinge_flexion_deg,
)
from biospur_fusion.c2_articulated_biomechanics import orientation_ik
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.articulated_range import corrected_proxy_points


def test_functional_actions_are_bound_to_the_matching_joint() -> None:
    assert HINGE_SPECS["elbow_left"][2] == "05"
    assert HINGE_SPECS["elbow_right"][2] == "06"
    assert HINGE_SPECS["knee_left"][2] == "09"
    assert HINGE_SPECS["knee_right"][2] == "10"


def _wxyz(rotation: Rotation) -> np.ndarray:
    q = rotation.as_quat()
    return np.c_[q[:, 3], q[:, :3]]


def _joint() -> HingeJoint:
    return HingeJoint(
        name="elbow_right",
        parent="upper_arm_right",
        child="forearm_right",
        functional_episode="06",
        parent_axis=(1.0, 0.0, 0.0),
        child_axis=(1.0, 0.0, 0.0),
        neutral_parent_from_child_xyzw=(0.0, 0.0, 0.0, 1.0),
        positive_sign=1.0,
        minimum_deg=-5.0,
        maximum_deg=150.0,
        neutral_frame_count=1,
        functional_frame_count=1,
    )


def _four_hinge_model() -> dict[str, HingeJoint]:
    return {
        name: HingeJoint(
            name, parent, child, action, (1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.0,
            minimum, maximum, 1, 1,
        )
        for name, (parent, child, action, minimum, maximum) in HINGE_SPECS.items()
    }


def _proxy_geometry() -> DisplayProxyGeometry:
    return DisplayProxyGeometry(
        0.55, 0.30, 0.40,
        {
            "upper_arm_left": 0.28, "forearm_left": 0.25,
            "upper_arm_right": 0.28, "forearm_right": 0.25,
            "thigh_left": 0.42, "shank_left": 0.40,
            "thigh_right": 0.42, "shank_right": 0.40,
        },
    )


def _assert_physical_projection_matches_scipy_owner(
    base, actual, actual_metrics, expected, expected_metrics, model,
) -> None:
    for segment in base:
        actual_matrix = base[segment] @ Rotation.from_rotvec(
            actual[segment]
        ).as_matrix()
        expected_matrix = base[segment] @ Rotation.from_rotvec(
            expected[segment]
        ).as_matrix()
        np.testing.assert_allclose(actual_matrix, expected_matrix, atol=1e-12, rtol=0)
    actual_points = corrected_proxy_points(base, actual, _proxy_geometry())
    expected_points = corrected_proxy_points(base, expected, _proxy_geometry())
    for point in expected_points:
        np.testing.assert_allclose(
            actual_points[point], expected_points[point], atol=1e-12, rtol=0,
        )
    for name in model:
        actual_joint = actual_metrics["joint"][name]
        expected_joint = expected_metrics["joint"][name]
        for field in (
            "pre_projection_signed_deg", "post_projection_signed_deg",
            "flexion_deg", "observed_unsigned_bend_deg",
        ):
            assert abs(actual_joint[field] - expected_joint[field]) <= 1e-9
        for field in (
            "pre_projection_below_rom", "pre_projection_above_rom",
            "post_projection_inside_rom",
        ):
            assert actual_joint[field] is expected_joint[field]
        # Both residuals describe exact direction closure.  The new atan2
        # diagnostic avoids the old arccos(1-epsilon) microradian artefact.
        assert actual_joint["fk_direction_residual_deg"] <= 1e-9
        assert expected_joint["fk_direction_residual_deg"] <= 3e-6
    for field in (
        "pre_projection_below_rom_count", "pre_projection_above_rom_count",
        "post_projection_all_inside_rom",
    ):
        assert actual_metrics[field] == expected_metrics[field]
    assert actual_metrics["numeric_kernel"] == "matrix-rodrigues-float64-v1"


def test_one_frame_matrix_kernel_is_physically_equivalent_to_scipy_owner(monkeypatch) -> None:
    model = _four_hinge_model()
    segments = list(SEGMENTS)
    generator = np.random.default_rng(20260910)
    cases = [
        (
            {segment: np.eye(3) for segment in segments},
            {segment: np.zeros(3) for segment in segments},
        ),
        (
            {segment: np.eye(3) for segment in segments},
            {
                segment: np.radians(np.array([
                    model[next(name for name, joint in model.items()
                               if joint.child == segment)].maximum_deg
                    if any(joint.child == segment for joint in model.values()) else 0.0,
                    0.0, 0.0,
                ]))
                for segment in segments
            },
        ),
    ]
    cases.extend((
        {
            segment: Rotation.from_rotvec(generator.normal(size=3) * 2.0).as_matrix()
            for segment in segments
        },
        {segment: generator.normal(size=3) * 0.5 for segment in segments},
    ) for _ in range(64))
    near_pi = {
        segment: np.array([np.pi - 1e-12, 0.0, 0.0])
        for segment in segments
    }
    cases.append(({segment: np.eye(3) for segment in segments}, near_pi))
    for magnitude in (1e-16, 1e-12, 1e-8):
        cases.append((
            {segment: np.eye(3) for segment in segments},
            {segment: np.array([magnitude, -magnitude, magnitude]) for segment in segments},
        ))
    storage = {
        segment: np.r_[generator.normal(size=3), np.zeros(3)]
        for segment in segments
    }
    cases.append((
        {segment: np.eye(3)[:, ::-1][:, ::-1] for segment in segments},
        {segment: storage[segment][::2] for segment in segments},
    ))
    reversed_segments = tuple(reversed(segments))
    cases.append((
        {segment: np.eye(3) for segment in reversed_segments},
        {segment: np.zeros(3) for segment in reversed_segments},
    ))

    calls = []
    fast = orientation_ik._project_hinge_corrections_one_frame

    def observed(*args, **kwargs):
        calls.append(1)
        return fast(*args, **kwargs)

    monkeypatch.setattr(
        orientation_ik, "_project_hinge_corrections_one_frame", observed,
    )
    for base, corrections in cases:
        expected, expected_metrics = _project_hinge_corrections_scalar(
            base, corrections, model,
        )
        actual, actual_metrics = project_hinge_corrections(base, corrections, model)
        _assert_physical_projection_matches_scipy_owner(
            base, actual, actual_metrics, expected, expected_metrics, model,
        )
    assert len(calls) == len(cases)


def test_exact_antipodal_alignment_uses_deterministic_fallback_axis() -> None:
    joint = _joint()
    model = {joint.name: joint}
    base = {segment: np.eye(3) for segment in SEGMENTS}
    distal_twist_deg = 37.0
    child_before = (
        Rotation.from_rotvec(np.radians([-90.0, 0.0, 0.0]))
        * Rotation.from_rotvec(np.radians([0.0, 0.0, distal_twist_deg]))
    ).as_matrix()
    correction = {segment: np.zeros(3) for segment in SEGMENTS}
    correction[joint.child] = Rotation.from_matrix(child_before).as_rotvec()

    # At 90 degrees in the negative bend plane, the measured child direction
    # is exactly antipodal to the +90-degree anatomical target.  The accepted
    # shortest-alignment policy chooses cross(source, +X), hence +Z here.
    fallback = Rotation.from_rotvec(np.array([0.0, 0.0, np.pi])).as_matrix()
    expected_child = fallback @ child_before
    expected_correction = {
        segment: value.copy() for segment, value in correction.items()
    }
    expected_correction[joint.child] = Rotation.from_matrix(
        expected_child
    ).as_rotvec()

    scalar, scalar_metrics = project_hinge_corrections(base, correction, model)
    repeated, repeated_metrics = project_hinge_corrections(base, correction, model)
    batched, batch_metrics = evaluate_hinge_projection_batch(
        base,
        {segment: value[None, :] for segment, value in correction.items()},
        model,
    )
    oracle, oracle_metrics = _project_hinge_corrections_scalar(
        base, correction, model,
    )

    assert scalar_metrics == repeated_metrics == batch_metrics[0]
    for segment in SEGMENTS:
        assert scalar[segment].tobytes() == repeated[segment].tobytes()
        assert scalar[segment].tobytes() == batched[0][segment].tobytes()
    actual_child = Rotation.from_rotvec(scalar[joint.child]).as_matrix()
    np.testing.assert_allclose(actual_child, expected_child, atol=1e-12, rtol=0)
    _assert_physical_projection_matches_scipy_owner(
        base, scalar, scalar_metrics, oracle, oracle_metrics, model,
    )
    expected_points = corrected_proxy_points(
        base, expected_correction, _proxy_geometry(),
    )
    actual_points = corrected_proxy_points(base, scalar, _proxy_geometry())
    for point in expected_points:
        np.testing.assert_allclose(
            actual_points[point], expected_points[point], atol=1e-12, rtol=0,
        )

    # The fallback is a left-multiplied shortest direction alignment, so the
    # child's full transverse basis—and therefore its 37-degree local axial
    # twist—is transported without alteration.
    np.testing.assert_allclose(
        actual_child[:, 0], fallback @ child_before[:, 0], atol=1e-12, rtol=0,
    )
    assert scalar_metrics["joint"][joint.name]["pre_projection_signed_deg"] == pytest.approx(
        -90.0, abs=1e-9,
    )
    assert scalar_metrics["joint"][joint.name]["post_projection_signed_deg"] == pytest.approx(
        90.0, abs=1e-9,
    )
    assert scalar_metrics["joint"][joint.name]["pre_projection_below_rom"]
    assert not scalar_metrics["joint"][joint.name]["pre_projection_above_rom"]
    assert scalar_metrics["joint"][joint.name]["post_projection_inside_rom"]


def test_one_frame_matrix_kernel_has_one_scipy_logarithm_boundary(monkeypatch) -> None:
    model = _four_hinge_model()
    base = {segment: np.eye(3) for segment in SEGMENTS}
    corrections = {segment: np.zeros(3) for segment in SEGMENTS}
    original = Rotation
    matrix_shapes = []

    class ObservedRotation:
        @staticmethod
        def from_rotvec(value):
            raise AssertionError("production kernel must not use SciPy exponential")

        @staticmethod
        def from_matrix(value):
            matrix_shapes.append(np.asarray(value).shape)
            return original.from_matrix(value)

        from_quat = staticmethod(original.from_quat)

    monkeypatch.setattr(orientation_ik, "Rotation", ObservedRotation)
    actual, metrics = orientation_ik._project_hinge_corrections_one_frame(
        base, corrections, model,
    )
    assert matrix_shapes == [(len(model), 3, 3)]
    assert metrics["numeric_kernel"] == "matrix-rodrigues-float64-v1"
    assert all(np.all(np.isfinite(actual[key])) for key in SEGMENTS)


@pytest.mark.parametrize(
    "base,corrections,message",
    [
        ({"upper_arm_right": np.eye(3)}, {}, "same segments"),
        (
            {"upper_arm_right": np.eye(3), "forearm_right": np.eye(3)},
            {"upper_arm_right": np.zeros(3), "forearm_right": np.array([np.nan, 0, 0])},
            "inputs must be finite",
        ),
        (
            {"upper_arm_right": np.eye(2), "forearm_right": np.eye(3)},
            {"upper_arm_right": np.zeros(3), "forearm_right": np.zeros(3)},
            "cannot reshape",
        ),
    ],
)
def test_one_frame_fast_path_replays_scalar_exception_exactly(base, corrections, message):
    model = {_joint().name: _joint()}
    with pytest.raises(Exception) as scalar:
        _project_hinge_corrections_scalar(base, corrections, model)
    with pytest.raises(type(scalar.value), match=message) as fast:
        project_hinge_corrections(base, corrections, model)
    assert str(fast.value) == str(scalar.value)


def test_hinge_coordinate_preserves_signed_flexion() -> None:
    parent = _wxyz(Rotation.identity(2))
    child = _wxyz(Rotation.from_rotvec(np.radians([[30.0, 0, 0], [-20.0, 0, 0]])))
    np.testing.assert_allclose(
        hinge_coordinate_deg(parent, child, _joint()), [30.0, -20.0], atol=1e-10
    )


def test_reconstruction_sets_fk_direction_and_preserves_axial_twist() -> None:
    parent = _wxyz(Rotation.identity(3))
    flexion = np.array([20.0, 55.0, 90.0])
    target = Rotation.from_rotvec(np.radians(np.c_[flexion, np.zeros((3, 2))]))
    # Add rotation about the child's own long axis.  Direction-only IK must
    # retain it while restoring the requested parent-to-child bend.
    child = _wxyz(target * Rotation.from_rotvec(
        np.c_[np.zeros((3, 2)), np.radians([15.0, -25.0, 40.0])]
    ))

    corrected, metrics = reconstruct_distal_orientation(
        parent, child, flexion, _joint()
    )

    expected_down = target.apply(DOWN)
    np.testing.assert_allclose(
        _rotation(corrected).apply(DOWN), expected_down, atol=1e-12
    )
    assert metrics["fk_direction_residual_maximum_deg"] <= 1e-6


def test_orientation_ik_uses_unsigned_bend_and_caps_rom() -> None:
    parent = _wxyz(Rotation.identity(3))
    child = _wxyz(Rotation.from_rotvec(
        np.radians([[30.0, 0.0, 0.0], [-40.0, 0.0, 0.0], [170.0, 0.0, 0.0]])
    ))

    flexion, metrics = solve_hinge_flexion_deg(parent, child, _joint())

    np.testing.assert_allclose(flexion, [30.0, 40.0, 150.0], atol=1e-10)
    assert metrics["above_rom_count"] == 1


def test_public_projector_repairs_negative_incremental_hinge_update() -> None:
    base = {
        "upper_arm_right": np.eye(3),
        "forearm_right": Rotation.from_rotvec(
            np.radians([40.0, 0.0, 0.0])
        ).as_matrix(),
    }
    correction = {
        "upper_arm_right": np.zeros(3),
        # Propose enough opposite rotation to turn +40 degrees into -20.
        "forearm_right": np.radians(np.array([-60.0, 0.0, 0.0])),
    }
    joint = _joint()
    projected, metrics = project_hinge_corrections(
        base, correction, {joint.name: joint}
    )
    parent = Rotation.from_matrix([
        base[joint.parent] @ Rotation.from_rotvec(projected[joint.parent]).as_matrix()
    ])
    child = Rotation.from_matrix([
        base[joint.child] @ Rotation.from_rotvec(projected[joint.child]).as_matrix()
    ])
    coordinate = hinge_coordinate_deg(_wxyz(parent), _wxyz(child), joint)[0]

    assert metrics["pre_projection_below_rom_count"] == 1
    assert metrics["post_projection_all_inside_rom"]
    assert 0.0 <= coordinate <= joint.maximum_deg
    assert metrics["fk_direction_residual_maximum_deg"] <= 1e-6


def test_public_projector_reexpresses_stale_carry_on_changed_base() -> None:
    joint = _joint()
    first_base = {
        joint.parent: np.eye(3),
        joint.child: Rotation.from_rotvec(
            np.radians([40.0, 0.0, 0.0])
        ).as_matrix(),
    }
    raw = {
        joint.parent: np.zeros(3),
        joint.child: np.radians(np.array([-60.0, 0.0, 0.0])),
    }
    first, _ = project_hinge_corrections(first_base, raw, {joint.name: joint})
    # The native pose base has moved from +40 to +10 degrees.  Reusing the
    # old -20-degree right correction creates a negative bend; current-base
    # transport must re-express it and then be idempotent at this base.
    second_base = {
        joint.parent: np.eye(3),
        joint.child: Rotation.from_rotvec(
            np.radians([10.0, 0.0, 0.0])
        ).as_matrix(),
    }
    second, metrics = project_hinge_corrections(
        second_base, first, {joint.name: joint}
    )
    repeated, repeated_metrics = project_hinge_corrections(
        second_base, second, {joint.name: joint}
    )

    assert metrics["pre_projection_below_rom_count"] == 1
    assert metrics["post_projection_all_inside_rom"]
    assert repeated_metrics["post_projection_all_inside_rom"]
    for segment in second:
        np.testing.assert_allclose(repeated[segment], second[segment], atol=1e-12)


def test_exact_batch_matches_scalar_bytes_and_preserves_immutable_inputs() -> None:
    joint = _joint()
    model = {joint.name: joint}
    base = {
        joint.parent: Rotation.from_euler(
            "xyz", [15.0, -7.0, 30.0], degrees=True
        ).as_matrix(),
        joint.child: Rotation.from_euler(
            "xyz", [25.0, 20.0, -9.0], degrees=True
        ).as_matrix(),
    }
    generator = np.random.default_rng(7)
    batch = {
        segment: generator.normal(size=(25, 3)) * 0.15 for segment in base
    }
    before = {
        segment: (value.tobytes(), value.shape, value.dtype, value.flags.writeable)
        for segment, value in batch.items()
    }
    for value in batch.values():
        value.setflags(write=False)
    before = {
        segment: (value.tobytes(), value.shape, value.dtype, value.flags.writeable)
        for segment, value in batch.items()
    }

    projected, metrics = evaluate_hinge_projection_batch(base, batch, model)

    for index in range(25):
        scalar_projected, scalar_metrics = project_hinge_corrections(
            base, {segment: value[index] for segment, value in batch.items()}, model
        )
        assert scalar_metrics == metrics[index]
        for segment in base:
            np.testing.assert_array_equal(
                scalar_projected[segment], projected[index][segment]
            )
    assert before == {
        segment: (value.tobytes(), value.shape, value.dtype, value.flags.writeable)
        for segment, value in batch.items()
    }


def test_batch_validates_finite_inputs_without_scalar_fallback(monkeypatch) -> None:
    joint = _joint()
    model = {joint.name: joint}
    base = {joint.parent: np.eye(3), joint.child: np.eye(3)}
    batch = {
        joint.parent: np.zeros((5, 3)),
        joint.child: np.zeros((5, 3)),
    }
    batch[joint.child][3, 1] = np.nan
    calls = []
    scalar = project_hinge_corrections

    def observed(*args, **kwargs):
        calls.append(len(calls))
        return scalar(*args, **kwargs)

    monkeypatch.setattr(orientation_ik, "project_hinge_corrections", observed)
    with pytest.raises(ValueError, match="hinge projection inputs must be finite"):
        evaluate_hinge_projection_batch(base, batch, model)
    assert calls == []


def test_batch_uses_authoritative_matrix_coordinate_without_helper_reentry(monkeypatch) -> None:
    joint = _joint()
    base = {joint.parent: np.eye(3), joint.child: np.eye(3)}
    batch = {
        joint.parent: np.zeros((5, 3)),
        joint.child: np.zeros((5, 3)),
    }
    calls = []

    def outside(parent_q, _child_q, _joint):
        calls.append(len(parent_q))
        return np.full(len(parent_q), 200.0)

    monkeypatch.setattr(orientation_ik, "hinge_coordinate_deg", outside)
    _projected, projections = evaluate_hinge_projection_batch(
        base, batch, {joint.name: joint}
    )

    assert len(projections) == 5
    assert calls == []
    assert all(row["post_projection_all_inside_rom"] for row in projections)


def _assert_varying_batch_matches_scalar(base, corrections, model) -> None:
    actual, actual_metrics = evaluate_varying_base_hinge_projection_batch(
        base, corrections, model,
    )
    for index in range(len(next(iter(corrections.values())))):
        expected, expected_metrics = project_hinge_corrections(
            {segment: value[index] for segment, value in base.items()},
            {segment: value[index] for segment, value in corrections.items()},
            model,
        )
        assert actual_metrics[index] == expected_metrics
        assert tuple(actual[index]) == tuple(expected)
        for segment in expected:
            assert actual[index][segment].tobytes() == expected[segment].tobytes()


def test_varying_base_batch_is_byte_exact_and_supports_two_stage_use() -> None:
    generator = np.random.default_rng(20260910)
    model = dict(reversed(tuple(_four_hinge_model().items())))
    count = 16
    base = {
        segment: Rotation.from_rotvec(
            generator.normal(size=(count, 3)) * 2.0
        ).as_matrix()
        for segment in reversed(SEGMENTS)
    }
    corrections = {
        segment: generator.normal(size=(count, 3)) * 0.35
        for segment in reversed(SEGMENTS)
    }
    for values in corrections.values():
        values[0] = 0.0
        values[1] = (np.pi - 1e-12, 0.0, 0.0)
        values[2] = 0.0
    for values in base.values():
        values[2] = np.eye(3)
    for joint in model.values():
        corrections[joint.child][2] = (
            np.asarray(joint.parent_axis)
            * joint.positive_sign
            * np.radians(joint.maximum_deg)
        )
    before = {
        ("base", segment): value.tobytes() for segment, value in base.items()
    } | {
        ("correction", segment): value.tobytes()
        for segment, value in corrections.items()
    }
    _assert_varying_batch_matches_scalar(base, corrections, model)

    current, _metrics = evaluate_varying_base_hinge_projection_batch(
        base, corrections, model,
    )
    second_corrections = {
        segment: np.stack([row[segment] for row in current])
        for segment in corrections
    }
    previous_base = {
        segment: np.roll(value, 1, axis=0) for segment, value in base.items()
    }
    _assert_varying_batch_matches_scalar(
        previous_base, second_corrections, model,
    )
    assert before == {
        ("base", segment): value.tobytes() for segment, value in base.items()
    } | {
        ("correction", segment): value.tobytes()
        for segment, value in corrections.items()
    }


def test_varying_base_batch_preserves_fallback_and_validation() -> None:
    joint = _joint()
    overlapping = {
        "first": joint,
        "second": replace(
            joint, name="second", parent=joint.child, child=joint.parent,
        ),
    }
    base = {
        joint.parent: np.repeat(np.eye(3)[None, :, :], 3, axis=0),
        joint.child: np.repeat(np.eye(3)[None, :, :], 3, axis=0),
    }
    corrections = {
        joint.parent: np.zeros((3, 3)), joint.child: np.zeros((3, 3)),
    }
    _assert_varying_batch_matches_scalar(base, corrections, overlapping)
    with pytest.raises(
        ValueError, match="base rotations and corrections must own the same segments",
    ):
        evaluate_varying_base_hinge_projection_batch(
            base, {joint.parent: corrections[joint.parent]}, overlapping,
        )
    malformed = {key: value.copy() for key, value in corrections.items()}
    malformed[joint.child][1, 0] = np.nan
    with pytest.raises(ValueError, match="hinge projection inputs must be finite"):
        evaluate_varying_base_hinge_projection_batch(base, malformed, overlapping)
    with pytest.raises(
        ValueError, match="varying-base hinge projection batch exceeds 16 rows",
    ):
        evaluate_varying_base_hinge_projection_batch(
            {key: np.repeat(value[:1], 17, axis=0) for key, value in base.items()},
            {key: np.repeat(value[:1], 17, axis=0)
             for key, value in corrections.items()},
            overlapping,
        )


def test_varying_base_batch_n16_kernel_is_at_least_twice_as_fast() -> None:
    generator = np.random.default_rng(7)
    model = _four_hinge_model()
    base = {
        segment: Rotation.from_rotvec(
            generator.normal(size=(16, 3))
        ).as_matrix()
        for segment in SEGMENTS
    }
    corrections = {
        segment: generator.normal(size=(16, 3)) * 0.2 for segment in SEGMENTS
    }

    def batched():
        return evaluate_varying_base_hinge_projection_batch(
            base, corrections, model,
        )

    def scalar():
        return [
            project_hinge_corrections(
                {segment: value[index] for segment, value in base.items()},
                {segment: value[index]
                 for segment, value in corrections.items()},
                model,
            )
            for index in range(16)
        ]

    batched()
    scalar()
    batch_ns = []
    scalar_ns = []
    for _ in range(7):
        start = time.perf_counter_ns(); batched()
        batch_ns.append(time.perf_counter_ns() - start)
        start = time.perf_counter_ns(); scalar()
        scalar_ns.append(time.perf_counter_ns() - start)
    assert np.median(scalar_ns) / np.median(batch_ns) >= 2.0
