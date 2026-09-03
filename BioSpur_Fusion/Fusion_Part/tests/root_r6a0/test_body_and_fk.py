from __future__ import annotations

from pathlib import Path

import numpy as np

from biospur_fusion.root_r6a0.body import frozen_uncertain_calibration


def test_complete_whole_body_graph(model):
    assert len(model.segments) == 10
    assert len(model.joints) == 9
    assert len(model.imus) == 10
    assert len(model.tags) == 10
    assert len(model.anchors) == 8
    assert set(model.identity_mapping.values()) == set(model.segments)


def test_active_physical_mapping_overrides_inactive_aliases(model):
    assert model.identity_mapping["BSFB165"] == "forearm_left"
    assert model.identity_mapping["BSFEC35"] == "forearm_right"
    assert model.identity_provenance["source_sha256"] == "39a78d53e9fef4539dce609a9f27f4881a04884da40a73ed80b43b878edd562b"


def test_core_algorithms_are_generic_over_ids():
    source = Path(__file__).resolve().parents[2] / "src/biospur_fusion/root_r6a0"
    forbidden = ("BSFEC35", "BSF6C53", "named_anchor_special_case")
    core_modules = (
        "authority.py", "body.py", "contracts.py", "evidence.py", "factors.py",
        "math3d.py", "observability.py", "rehearsal.py", "synthetic.py",
    )
    for name in core_modules:
        path = source / name
        text = path.read_text(encoding="utf-8")
        assert not any(token in text for token in forbidden), path


def test_fk_is_only_geometry_path_and_joint_centres_coincide(scenario):
    for state in scenario.states:
        predictions = scenario.model.all_predictions(state, scenario.calibration)
        assert set(predictions["segments"]) == set(scenario.model.segments)
        assert set(predictions["imus"]) == set(scenario.model.imu_ids)
        assert set(predictions["tags"]) == set(scenario.model.tag_ids)
        assert np.max(np.abs(predictions["kinematic_residuals"])) < 1e-12


def test_bone_lengths_are_static_calibration_not_epoch_variables(scenario):
    lengths = {name: slot.value for name, slot in scenario.calibration.slots.items()
               if name.startswith("bone_length:")}
    assert len(lengths) == 8
    assert all(value is not None and value[0] > 0 for value in lengths.values())
    moved = scenario.states[-1]
    scenario.model.segment_poses(moved, scenario.calibration)
    assert lengths == {name: slot.value for name, slot in scenario.calibration.slots.items()
                       if name.startswith("bone_length:")}


def test_non_normal_asymmetric_pose_is_preserved(scenario):
    state = scenario.states[2]
    assert not np.allclose(state.joint_rotvec["elbow_left"], state.joint_rotvec["elbow_right"])
    assert not np.allclose(state.joint_rotvec["knee_left"], state.joint_rotvec["knee_right"])
    left = scenario.model.anatomical_points(state, scenario.calibration)["wrist_left"]
    right = scenario.model.anatomical_points(state, scenario.calibration)["wrist_right"]
    assert not np.allclose(np.abs(left), np.abs(right))


def test_unknown_real_calibration_never_becomes_precise_zero(model):
    calibration = frozen_uncertain_calibration(model)
    assert calibration.slots
    assert all(slot.value is None for slot in calibration.slots.values())
    assert all(slot.covariance is not None for slot in calibration.slots.values())
    assert all(not slot.fitted_from_c1 for slot in calibration.slots.values())


def test_state_contract_has_independent_biases_and_covariance(scenario):
    state = scenario.states[0]
    assert set(state.gyro_bias_rad_s) == set(scenario.model.imu_ids)
    assert set(state.accel_bias_mps2) == set(scenario.model.imu_ids)
    assert len({tuple(value) for value in state.gyro_bias_rad_s.values()}) == 10
    assert state.covariance.shape == (123, 123)
    assert np.min(np.linalg.eigvalsh(state.covariance)) > 0
