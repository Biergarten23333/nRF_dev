import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.body_graph.fixed_lag import ArticulatedFixedLagEstimator, PositionFactor
from biospur_fusion.body_graph.model import BodyState, SEGMENTS, default_body_model


def state(time=0.0):
    rotations = {segment: np.eye(3) for segment in SEGMENTS}
    return BodyState(time, np.array([0., 0., 1.]), rotations, np.zeros(3))


def test_joint_centres_and_lengths_are_exact_by_construction():
    model = default_body_model(); s = state()
    assert max(model.constraint_residuals(s).values()) < 1e-14
    before = model.immutable_lengths()
    rotations = dict(s.rotations_N_from_S)
    rotations["Forearm_L"] = Rotation.from_rotvec([0, .8, 0]).as_matrix()
    moved = BodyState(.1, s.pelvis_origin_m, rotations, np.zeros(3))
    assert model.immutable_lengths() == before
    assert max(model.constraint_residuals(moved).values()) < 1e-14


def test_one_bad_node_does_not_teleport_body():
    model = default_body_model(); prior = state(); estimator = ArticulatedFixedLagEstimator(model)
    baseline = model.antennas(prior); orientations = prior.rotations_N_from_S
    for flyer in (.5, 1.0, 2.0):
        factor = PositionFactor("Forearm_L", baseline["Forearm_L"] + np.array([flyer, 0, 0]), np.eye(3)*.0025)
        result = estimator.update(.01 + flyer, prior, orientations, (factor,))
        assert result.rejected == ("Forearm_L",)
        assert np.linalg.norm(result.state.pelvis_origin_m - prior.pelvis_origin_m) < 1e-12
        assert result.max_joint_residual_m < 1e-12


def test_unilateral_orientation_remains_unilateral():
    model = default_body_model(); prior = state(); estimator = ArticulatedFixedLagEstimator(model)
    orientations = dict(prior.rotations_N_from_S)
    orientations["Forearm_L"] = Rotation.from_rotvec([0, .5, 0]).as_matrix()
    result = estimator.update(.04, prior, orientations, tuple(), frozenset({"Forearm_L"}))
    left = Rotation.from_matrix(result.state.rotations_N_from_S["Forearm_L"]).magnitude()
    right = Rotation.from_matrix(result.state.rotations_N_from_S["Forearm_R"]).magnitude()
    assert left > .4 and right < 1e-9
    assert result.max_joint_residual_m < 1e-12


def test_accepted_node_correction_propagates_through_shared_body_state():
    model = default_body_model(); prior = state(); estimator = ArticulatedFixedLagEstimator(model)
    before = model.antennas(prior)
    factor = PositionFactor(
        "Forearm_L", before["Forearm_L"] + np.array([.02, 0, 0]), np.eye(3) * .0025)
    result = estimator.update(.04, prior, prior.rotations_N_from_S, (factor,))
    after = model.antennas(result.state)
    assert result.accepted == ("Forearm_L",)
    assert np.linalg.norm(after["Torso"] - before["Torso"]) > 0
    assert result.max_joint_residual_m < 1e-12


def test_dropout_is_finite_bounded_and_fixed_lag_evicts_old_knots():
    model = default_body_model(); estimator = ArticulatedFixedLagEstimator(model, lag_s=.1)
    prior = state()
    first = estimator.update(.04, prior, prior.rotations_N_from_S, tuple())
    second = estimator.update(.20, first.state, first.state.rotations_N_from_S, tuple())
    assert len(estimator.knots) == 1
    assert np.isfinite(second.state.pelvis_origin_m).all()
    assert np.linalg.norm(second.state.pelvis_origin_m - prior.pelvis_origin_m) < 1e-12
    assert second.max_joint_residual_m < 1e-12


def test_joint_replay_is_deterministic_with_isolated_transient():
    def replay():
        model = default_body_model(); estimator = ArticulatedFixedLagEstimator(model)
        prior = state(); output = []
        for index in range(6):
            orientations = dict(prior.rotations_N_from_S)
            if index == 3:
                orientations["Shank_L"] = Rotation.from_rotvec([0, .25, 0]).as_matrix()
            result = estimator.update((index + 1) * .04, prior, orientations, tuple(),
                                      frozenset({"Shank_L"}) if index == 3 else frozenset())
            output.append(ArticulatedFixedLagEstimator._pack(result.state))
            prior = result.state
        return np.asarray(output)
    left = replay(); right = replay()
    assert left.tobytes() == right.tobytes()
    assert np.linalg.norm(left[3] - left[2]) > 0
