from dataclasses import fields, replace
from functools import partial
from types import SimpleNamespace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_3a_kinematics.interface import DisplayProxyGeometry
from biospur_fusion.c2_articulated_biomechanics.model import HINGE_SPECS, HingeJoint
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    project_hinge_corrections,
)
from biospur_fusion.c2_uwb_calibration import causal_articulated_pose as owner
from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS
from biospur_fusion.c2_uwb_calibration.causal_articulated_pose import (
    CausalArticulatedPose,
    Native200PoseBatchInput,
)


def _correction(left_x: float) -> dict[str, np.ndarray]:
    result = {segment: np.zeros(3) for segment in SEGMENTS}
    result["shank_left"] = np.array([left_x, 0.0, 0.0])
    return result


def _batch_pose() -> CausalArticulatedPose:
    model = {
        name: HingeJoint(
            name, parent, child, action, (1.0, 0.0, 0.0),
            (1.0, 0.0, 0.0), (0.0, 0.0, 0.0, 1.0), 1.0,
            minimum, maximum, 1, 1,
        )
        for name, (parent, child, action, minimum, maximum)
        in HINGE_SPECS.items()
    }
    geometry = DisplayProxyGeometry(
        0.55, 0.30, 0.40,
        {
            "upper_arm_left": 0.28, "forearm_left": 0.25,
            "upper_arm_right": 0.28, "forearm_right": 0.25,
            "thigh_left": 0.42, "shank_left": 0.40,
            "thigh_right": 0.42, "shank_right": 0.40,
        },
    )
    return CausalArticulatedPose(
        action_start_s=0.0, action_stop_s=2.0,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=geometry,
        hinge_projector=partial(project_hinge_corrections, model=model),
    )


def _batch_inputs(count: int) -> tuple[Native200PoseBatchInput, ...]:
    generator = np.random.default_rng(1000 + count)
    bases = [
        {
            segment: Rotation.from_rotvec(
                generator.normal(size=3) * 0.15
            ).as_matrix()
            for segment in SEGMENTS
        }
        for _ in range(count + 1)
    ]
    return tuple(
        Native200PoseBatchInput(
            time_s=(index + 1) * 0.005,
            source_node="BSFC2CC", source_boot_epoch=7,
            previous_source_timer_us=index * 5_000,
            source_timer_us=(index + 1) * 5_000,
            previous_source_global_ns=index * 5_000_000,
            source_global_ns=(index + 1) * 5_000_000,
            source_clock_mapping_digest="a" * 64,
            previous_base_rotations_world=bases[index],
            current_base_rotations_world=bases[index + 1],
        )
        for index in range(count)
    )


def _canonical(value):
    if isinstance(value, np.ndarray):
        return (value.dtype.str, value.shape, value.tobytes())
    if isinstance(value, dict) or hasattr(value, "items"):
        return tuple((key, _canonical(item)) for key, item in value.items())
    if hasattr(value, "__dataclass_fields__"):
        return tuple((field.name, _canonical(getattr(value, field.name)))
                     for field in fields(value) if field.name != "authority")
    if isinstance(value, (tuple, list)):
        return tuple(_canonical(item) for item in value)
    return value


def test_pose_owner_installs_only_at_availability_and_excludes_regauge_velocity(
    monkeypatch,
) -> None:
    def fake_points(base, correction, _geometry):
        base_y = float(base["pelvis"][0, 0])
        return {
            "ankle_left": np.array([
                correction["shank_left"][0], base_y, -0.9
            ]),
            "ankle_right": np.array([0.1, base_y, -0.9]),
        }

    monkeypatch.setattr(owner, "corrected_proxy_points", fake_points)

    def rotations(fraction):
        result = {segment: np.eye(3) for segment in SEGMENTS}
        result["pelvis"] = np.diag([fraction, 1.0, 1.0])
        return result

    projector_calls = []

    def projector(_base, correction):
        projector_calls.append(correction["shank_left"][0])
        return correction, {
            "post_projection_all_inside_rom": True,
            "fk_direction_residual_maximum_deg": 0.0,
        }

    pose = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=rotations,
        geometry=SimpleNamespace(),
        hinge_projector=projector,
    )
    before = pose.sample(0.1)
    assert before.ankle_offset_world_m["left"][0] == 0.0
    assert before.hinge_temporal is None

    pose.install(
        _correction(0.25), measurement_time_s=0.1, availability_time_s=0.2
    )
    with pytest.raises(ValueError, match="before installed UWB availability"):
        pose.sample(0.199)
    installed = pose.sample(0.2)
    assert installed.velocity_baseline_reset
    assert installed.ankle_offset_world_m["left"][0] == 0.25
    # The 25 cm estimator re-gauge is not divided by a 5 ms sample period.
    assert abs(installed.ankle_offset_velocity_world_mps["left"][0]) < 1e-12
    assert np.isclose(
        installed.ankle_offset_velocity_world_mps["left"][1], 1.0
    )
    held = pose.sample(0.205)
    assert not held.velocity_baseline_reset
    assert held.ankle_offset_world_m["left"][0] == 0.25
    assert not held.transition_active
    # The transition itself is also an estimator re-gauge, so it is excluded
    # from the contact detector's physical ankle-speed feature.
    assert abs(held.ankle_offset_velocity_world_mps["left"][0]) < 1e-12
    assert len(projector_calls) >= 5


def test_source_owned_sample_uses_actual_previous_global_tick(monkeypatch) -> None:
    def rotations(_fraction):
        raise AssertionError("source-owned sampling must not call action fraction")

    monkeypatch.setattr(
        owner,
        "corrected_proxy_points",
        lambda base, _correction, _geometry: {
            "ankle_left": np.array([base["pelvis"][0, 0], 0.0, -0.9]),
            "ankle_right": np.array([0.0, base["pelvis"][0, 0], -0.9]),
        },
    )
    pose = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=rotations,
        geometry=SimpleNamespace(),
        hinge_projector=lambda _base, correction: (
            correction,
            {"post_projection_all_inside_rom": True},
        ),
    )
    previous_global_ns = 5_000_061
    current_global_ns = 10_000_000
    dt = (current_global_ns - previous_global_ns) * 1e-9
    previous_base = {segment: np.eye(3) for segment in SEGMENTS}
    current_base = {segment: np.eye(3) for segment in SEGMENTS}
    previous_base["pelvis"] = np.diag([1.0 - dt, 1.0, 1.0])
    sample = pose.sample(
        current_global_ns * 1e-9,
        source_node="BSFC2CC",
        source_boot_epoch=7,
        previous_source_timer_us=1000,
        source_timer_us=6000,
        previous_source_global_ns=previous_global_ns,
        source_global_ns=current_global_ns,
        source_clock_mapping_digest="a" * 64,
        previous_base_rotations_world=previous_base,
        current_base_rotations_world=current_base,
    )
    assert sample.fraction is None
    assert sample.ankle_offset_velocity_world_mps["left"][0] == pytest.approx(1.0)
    assert dt == pytest.approx(0.004999939)

    untouched = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=rotations,
        geometry=SimpleNamespace(),
        hinge_projector=None,
    )
    before = untouched.publication_token()
    with pytest.raises(ValueError, match="complete native200 source pair"):
        untouched.sample(
            0.01, source_node="BSFC2CC", source_timer_us=6000,
        )
    assert untouched.publication_token() == before


def test_pose_owner_rejects_availability_reversal(monkeypatch) -> None:
    monkeypatch.setattr(
        owner,
        "corrected_proxy_points",
        lambda _base, _correction, _geometry: {
            "ankle_left": np.zeros(3), "ankle_right": np.zeros(3)
        },
    )
    pose = CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=SimpleNamespace(),
        hinge_projector=lambda _base, correction: (
            correction,
            {
                "post_projection_all_inside_rom": True,
                "fk_direction_residual_maximum_deg": 0.0,
            },
        ),
    )
    pose.install(
        _correction(0.1), measurement_time_s=0.1, availability_time_s=0.2
    )
    with pytest.raises(ValueError, match="availability reversed"):
        pose.install(
            _correction(0.2), measurement_time_s=0.05,
            availability_time_s=0.15,
        )


def _partition_pose(monkeypatch) -> CausalArticulatedPose:
    monkeypatch.setattr(
        owner,
        "corrected_proxy_points",
        lambda _base, correction, _geometry: {
            "ankle_left": np.array([
                correction["shank_left"][0], 0.0, -0.9
            ]),
            "ankle_right": np.array([0.1, 0.0, -0.9]),
        },
    )
    return CausalArticulatedPose(
        action_start_s=0.0,
        action_stop_s=1.0,
        rotations_at_fraction=lambda _fraction: {
            segment: np.eye(3) for segment in SEGMENTS
        },
        geometry=SimpleNamespace(),
        hinge_projector=lambda _base, correction: (
            correction,
            {
                "post_projection_all_inside_rom": True,
                "fk_direction_residual_maximum_deg": 0.0,
            },
        ),
    )


def test_transition_is_partition_invariant_and_same_time_idempotent(
    monkeypatch,
) -> None:
    owners = [_partition_pose(monkeypatch) for _ in range(3)]
    for pose in owners:
        pose.install(_correction(0.0), measurement_time_s=0.0, availability_time_s=0.0)
        pose.sample(0.0)
        pose.install(_correction(0.24), measurement_time_s=0.0, availability_time_s=0.01)
    for time_s in np.arange(0.015, 0.131, 0.005):
        dense = owners[0].sample(float(time_s))
    sparse = owners[1].sample(0.13)
    for time_s in (0.011, 0.017, 0.017, 0.043, 0.071, 0.129, 0.13):
        extra = owners[2].sample(time_s)
    np.testing.assert_allclose(
        dense.correction_rotvec["shank_left"],
        sparse.correction_rotvec["shank_left"], atol=1e-14,
    )
    np.testing.assert_allclose(
        extra.correction_rotvec["shank_left"],
        sparse.correction_rotvec["shank_left"], atol=1e-14,
    )
    repeated = owners[2].sample(0.13)
    np.testing.assert_array_equal(
        repeated.correction_rotvec["shank_left"],
        extra.correction_rotvec["shank_left"],
    )


def test_nonpublishing_contact_query_skips_hinge_temporal_owner(
    monkeypatch,
) -> None:
    pose = _partition_pose(monkeypatch)

    def unexpected_preview(**_kwargs):
        raise AssertionError("contact-only pose query published hinge evidence")

    monkeypatch.setattr(pose, "_hinge_temporal_preview", unexpected_preview)
    sample = pose.sample(0.1, publish_hinge_temporal=False)
    assert sample.hinge_temporal is None
    with pytest.raises(ValueError, match="must be bool"):
        pose.sample(0.1, publish_hinge_temporal=1)


@pytest.mark.parametrize("count", [1, 10, 16])
def test_native200_batch_matches_independent_samples_exactly(count) -> None:
    rows = _batch_inputs(count)
    batched = _batch_pose()
    scalar = _batch_pose()
    plan = batched.prepare_native200_batch(rows)
    actual = batched.commit_native200_batch(plan)
    expected = tuple(scalar.sample(
        row.time_s, source_node=row.source_node,
        source_boot_epoch=row.source_boot_epoch,
        previous_source_timer_us=row.previous_source_timer_us,
        source_timer_us=row.source_timer_us,
        previous_source_global_ns=row.previous_source_global_ns,
        source_global_ns=row.source_global_ns,
        source_clock_mapping_digest=row.source_clock_mapping_digest,
        previous_base_rotations_world=row.previous_base_rotations_world,
        current_base_rotations_world=row.current_base_rotations_world,
    ) for row in rows)
    assert _canonical(actual) == _canonical(expected)
    batch_token = batched.publication_token()
    scalar_token = scalar.publication_token()
    assert (
        batch_token.revision, batch_token.latest_sample_s, batch_token.digest,
    ) == (
        scalar_token.revision, scalar_token.latest_sample_s, scalar_token.digest,
    )
    assert _canonical(batched._prepare_install_rollback()) == _canonical(
        scalar._prepare_install_rollback()
    )
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        batched.commit_native200_batch(plan)


def test_native200_batch_is_deeply_immutable_and_stale_after_owner_changes() -> None:
    rows = _batch_inputs(2)
    pose = _batch_pose()
    plan = pose.prepare_native200_batch(rows)
    with pytest.raises(ValueError):
        plan.rows[0].source.current_base_rotations_world[SEGMENTS[0]][0, 0] = 2.0
    joint = next(iter(plan.rows[0].projection["joint"].values()))
    with pytest.raises(TypeError):
        joint["flexion_deg"] = 9.0

    foreign = _batch_pose()
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        foreign.commit_native200_batch(plan)
    pose.sample(
        rows[0].time_s, source_node=rows[0].source_node,
        source_boot_epoch=rows[0].source_boot_epoch,
        previous_source_timer_us=rows[0].previous_source_timer_us,
        source_timer_us=rows[0].source_timer_us,
        previous_source_global_ns=rows[0].previous_source_global_ns,
        source_global_ns=rows[0].source_global_ns,
        source_clock_mapping_digest=rows[0].source_clock_mapping_digest,
        previous_base_rotations_world=rows[0].previous_base_rotations_world,
        current_base_rotations_world=rows[0].current_base_rotations_world,
    )
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        pose.commit_native200_batch(plan)

    installed = _batch_pose()
    installed_plan = installed.prepare_native200_batch(rows)
    installed.install(
        _correction(0.0), measurement_time_s=0.0, availability_time_s=0.0,
    )
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        installed.commit_native200_batch(installed_plan)
    reset = _batch_pose()
    reset_plan = reset.prepare_native200_batch(rows)
    reset.reset_hinge_continuity(1)
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        reset.commit_native200_batch(reset_plan)

    malformed = list(_batch_inputs(2))
    malformed[1] = replace(
        malformed[1], time_s=0.010000001,
        previous_source_timer_us=5_001, source_timer_us=10_001,
        previous_source_global_ns=5_000_001,
        source_global_ns=10_000_001,
    )
    with pytest.raises(ValueError, match="chronology is not consecutive"):
        _batch_pose().prepare_native200_batch(tuple(malformed))
    bad_identity = list(_batch_inputs(1))
    bad_identity[0] = replace(bad_identity[0], source_node="")
    with pytest.raises(ValueError, match="timing/identity invalid"):
        _batch_pose().prepare_native200_batch(tuple(bad_identity))


def test_native200_batch_exact_plan_capability_rejects_all_replacement_tampering() -> None:
    pose = _batch_pose()
    plan = pose.prepare_native200_batch(_batch_inputs(1))
    row = plan.rows[0]
    source = row.source
    projected = dict(row.projected)
    first_segment = next(iter(projected))
    changed_data = projected[first_segment].copy()
    changed_data[0] += 1e-12
    data_row = replace(row, projected={**projected, first_segment: changed_data})
    shape_row = replace(
        row, projected={**projected, first_segment: projected[first_segment].reshape(1, 3)},
    )
    dtype_row = replace(
        row, projected={**projected, first_segment: projected[first_segment].astype(np.float32)},
    )
    endian_row = replace(
        row, projected={**projected, first_segment: projected[first_segment].astype(">f8")},
    )
    projection = dict(row.projection)
    joints = dict(projection["joint"])
    first_joint = next(iter(joints))
    joint = dict(joints[first_joint])
    joint["post_projection_signed_deg"] += 1e-12
    projection_row = replace(
        row, projection={**projection, "joint": {**joints, first_joint: joint}},
    )
    variants = (
        replace(plan),
        replace(plan, rows=()),
        replace(plan, rows=(replace(row, dt=row.dt + 1e-12),)),
        replace(plan, rows=(replace(
            row, source=replace(source, source_timer_us=source.source_timer_us + 1),
        ),)),
        replace(plan, rows=(data_row,)),
        replace(plan, rows=(shape_row,)),
        replace(plan, rows=(dtype_row,)),
        replace(plan, rows=(endian_row,)),
        replace(plan, rows=(projection_row,)),
        replace(plan, base_revision=plan.base_revision + 1),
        replace(plan, base_sample_generation=plan.base_sample_generation + 1),
        replace(plan, target_digest="f" * 64),
        replace(plan, temporal_snapshot=replace(
            plan.temporal_snapshot,
            history_digest="f" * 64,
        )),
    )
    for changed in variants:
        with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
            pose.commit_native200_batch(changed)
    independent = pose.prepare_native200_batch(_batch_inputs(1))
    assert independent is not plan and independent.state is not plan.state
    pose.commit_native200_batch(plan)
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        pose.commit_native200_batch(plan)


def test_native200_prepared_plan_arrays_are_recursively_bytes_backed() -> None:
    pose = _batch_pose()
    pose.commit_native200_batch(pose.prepare_native200_batch(_batch_inputs(1)))
    plan = pose.prepare_native200_batch(_batch_inputs(2)[1:])
    arrays = []

    def visit(value):
        if isinstance(value, np.ndarray):
            arrays.append(value)
            return
        if isinstance(value, dict) or hasattr(value, "items"):
            for child in value.values():
                visit(child)
            return
        if hasattr(value, "__dataclass_fields__"):
            for field in fields(value):
                if field.name not in ("authority", "state"):
                    visit(getattr(value, field.name))
            return
        if isinstance(value, (tuple, list)):
            for child in value:
                visit(child)

    visit(plan)
    assert arrays
    for array in arrays:
        assert array.flags.writeable is False
        with pytest.raises(ValueError):
            array.setflags(write=True)
        with pytest.raises(ValueError):
            array.flat[0] = array.flat[0]


@pytest.mark.parametrize(
    "source",
    (
        np.arange(12, dtype=np.float64).reshape(3, 4),
        np.asfortranarray(np.arange(12, dtype=np.float32).reshape(3, 4)),
        np.arange(30, dtype=np.float64).reshape(5, 6)[::2, 1::2],
        np.arange(12, dtype=np.float32).reshape(3, 4)[:, ::-1],
        np.arange(6, dtype=">f8").reshape(2, 3),
        np.empty((0, 3), dtype=np.float64),
        np.array([0x7FF8000000000001, 0xFFF8000000000042], dtype=np.uint64).view(
            np.float64
        ),
    ),
    ids=(
        "c-float64", "f-float32", "strided-float64", "reversed-float32",
        "big-endian-float64", "zero-size", "nan-payload-bits",
    ),
)
def test_immutable_array_preserves_exact_c_order_bytes_without_alias(
    source: np.ndarray,
) -> None:
    expected = np.array(source, copy=True, order="C", subok=False)
    frozen = CausalArticulatedPose._immutable_array(source)

    assert type(frozen) is np.ndarray
    assert frozen.dtype == expected.dtype
    assert frozen.shape == expected.shape
    assert frozen.tobytes(order="C") == expected.tobytes(order="C")
    assert frozen.flags.c_contiguous
    assert frozen.flags.writeable is False
    with pytest.raises(ValueError):
        frozen.setflags(write=True)

    before = frozen.tobytes(order="C")
    if source.size:
        source.flat[0] = 123.0
    assert frozen.tobytes(order="C") == before


def test_immutable_array_accepts_readonly_source_and_retains_no_alias() -> None:
    source = np.arange(9, dtype=np.float64).reshape(3, 3)
    source.setflags(write=False)
    frozen = CausalArticulatedPose._immutable_array(source)
    assert frozen.tobytes() == source.tobytes()
    assert frozen is not source
    assert frozen.base is not source
    with pytest.raises(ValueError):
        frozen.flat[0] = 99.0


def test_native200_batch_instances_remain_independent() -> None:
    rows = _batch_inputs(10)
    left = _batch_pose()
    right = _batch_pose()
    left_result = left.commit_native200_batch(left.prepare_native200_batch(rows))
    right_result = right.commit_native200_batch(right.prepare_native200_batch(rows))
    assert _canonical(left_result) == _canonical(right_result)
    left_result[0].correction_rotvec[SEGMENTS[0]][0] += 1.0
    assert left_result[0].correction_rotvec[SEGMENTS[0]].tobytes() != (
        right_result[0].correction_rotvec[SEGMENTS[0]].tobytes()
    )
    assert left.publication_token().digest == right.publication_token().digest


def test_native200_batch_mid_commit_failure_rolls_back_exactly(monkeypatch) -> None:
    pose = _batch_pose()
    plan = pose.prepare_native200_batch(_batch_inputs(10))
    before = _canonical(pose._prepare_install_rollback())
    temporal = pose._CausalArticulatedPose__hinge_temporal_owner
    actual = temporal._commit_from_pose
    calls = 0

    def fail_third(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("INJECTED_BATCH_COMMIT_FAILURE")
        return actual(*args, **kwargs)

    monkeypatch.setattr(temporal, "_commit_from_pose", fail_third)
    with pytest.raises(RuntimeError, match="INJECTED_BATCH_COMMIT_FAILURE"):
        pose.commit_native200_batch(plan)
    assert _canonical(pose._prepare_install_rollback()) == before
    with pytest.raises(RuntimeError, match="STALE_OR_FOREIGN"):
        pose.commit_native200_batch(plan)


def test_mid_transition_retarget_freezes_old_pose_without_future_leak(
    monkeypatch,
) -> None:
    dense = _partition_pose(monkeypatch)
    sparse = _partition_pose(monkeypatch)
    for pose in (dense, sparse):
        pose.install(_correction(0.0), measurement_time_s=0.0, availability_time_s=0.0)
        pose.sample(0.0)
        pose.install(_correction(0.24), measurement_time_s=0.0, availability_time_s=0.01)
    for time_s in np.arange(0.015, 0.06, 0.005):
        dense.sample(float(time_s))
    # Neither caller samples at the retarget epoch.  install() itself freezes
    # the old absolute-time transition before assigning the new causal target.
    for pose in (dense, sparse):
        pose.install(_correction(-0.12), measurement_time_s=0.05, availability_time_s=0.06)
        with pytest.raises(ValueError, match="before installed UWB availability"):
            pose.sample(0.059)
    dense_result = dense.sample(0.09)
    sparse_result = sparse.sample(0.09)
    np.testing.assert_allclose(
        dense_result.correction_rotvec["shank_left"],
        sparse_result.correction_rotvec["shank_left"], atol=1e-14,
    )
