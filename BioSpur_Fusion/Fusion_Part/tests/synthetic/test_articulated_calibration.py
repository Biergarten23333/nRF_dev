import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.articulated_batch import (
    CalibrationSamples, Geometry, SEGMENTS, ArticulatedCalibrationProblem,
    forward_antenna_positions, observability_report, pack_static,
    segment_rotations, unpack_static,
)


def synthetic_samples(exciting=True, seed=4711):
    rng = np.random.default_rng(seed); count = 36
    r_nv = Rotation.from_euler("yx", [.22, -.17]).as_matrix()
    extrinsics = {name: Rotation.from_rotvec(rng.normal(0, .25, 3)).as_matrix() for name in SEGMENTS}
    geometry = Geometry(.31, .17, .18, .095, -.045, .31, .255, .305, .26,
                        .425, .395, .43, .40)
    r_ns = np.empty((count, len(SEGMENTS), 3, 3)); actions = []
    for k in range(count):
        phase = 2 * np.pi * k / count if exciting else 0.0
        root = Rotation.from_euler("zyx", [.25*np.sin(phase), .08*np.cos(phase), 0]).as_matrix()
        rotations = {name: root.copy() for name in SEGMENTS}
        rotations["UpperArm_L"] = root @ Rotation.from_euler(
            "xyz", [.7*np.sin(phase), .35*np.cos(2*phase), .2*np.sin(3*phase)]).as_matrix()
        rotations["Forearm_L"] = rotations["UpperArm_L"] @ Rotation.from_euler("y", .8*np.sin(2*phase)).as_matrix()
        rotations["UpperArm_R"] = root @ Rotation.from_euler(
            "xyz", [-.6*np.cos(phase), .3*np.sin(2*phase), -.25*np.cos(3*phase)]).as_matrix()
        rotations["Forearm_R"] = rotations["UpperArm_R"] @ Rotation.from_euler("y", .7*np.cos(2*phase)).as_matrix()
        rotations["Thigh_L"] = root @ Rotation.from_euler(
            "xyz", [.5*np.sin(phase), .3*np.cos(2*phase), .18*np.sin(3*phase)]).as_matrix()
        rotations["Shank_L"] = rotations["Thigh_L"] @ Rotation.from_euler("y", .7*np.sin(2*phase)).as_matrix()
        rotations["Thigh_R"] = root @ Rotation.from_euler(
            "xyz", [-.5*np.cos(phase), .28*np.sin(2*phase), -.16*np.cos(3*phase)]).as_matrix()
        rotations["Shank_R"] = rotations["Thigh_R"] @ Rotation.from_euler("y", .65*np.cos(2*phase)).as_matrix()
        for i, name in enumerate(SEGMENTS): r_ns[k, i] = rotations[name]
        actions.append("arms" if k < count//2 else "squats")
    q_nb = np.empty_like(r_ns)
    for i, name in enumerate(SEGMENTS): q_nb[:, i] = r_ns[:, i] @ extrinsics[name]
    rel = forward_antenna_positions(r_ns, geometry)
    root_position = np.c_[.2*np.sin(np.linspace(0, 2, count)), np.zeros(count), np.ones(count)]
    p_v4 = np.einsum("ij,ntj->nti", r_nv.T, rel + root_position[:, None])
    covariance = np.tile(np.eye(3) * 4e-6, (count, len(SEGMENTS), 1, 1))
    samples = CalibrationSamples(np.arange(count, dtype=np.int64)*100_000_000,
                                 np.asarray(actions), p_v4, covariance, q_nb,
                                 np.ones((count, len(SEGMENTS)), bool))
    truth = pack_static(r_nv, extrinsics, geometry)
    return samples, truth


def test_exciting_batch_recovers_frame_extrinsics_and_dimensions():
    samples, truth = synthetic_samples(); problem = ArticulatedCalibrationProblem(samples)
    rng = np.random.default_rng(9); initial = truth + rng.normal(0, .01, len(truth))
    candidate, result = problem.solve(initial, max_nfev=100)
    _, fitted_extrinsics, fitted_geometry = unpack_static(result.x)
    r_truth, true_extrinsics, true_geometry = unpack_static(truth)
    assert Rotation.from_matrix(candidate.R_N_from_V4 @ r_truth.T).magnitude() < 2e-3
    assert np.max(abs(fitted_geometry.vector() - true_geometry.vector())) < 3e-3
    for name in ("Pelvis", "Torso", "Forearm_L", "Forearm_R", "Shank_L", "Shank_R"):
        error = Rotation.from_matrix(fitted_extrinsics[name] @ true_extrinsics[name].T).magnitude()
        assert error < 3e-3
    for name in ("UpperArm_L", "UpperArm_R", "Thigh_L", "Thigh_R"):
        fitted_axis = fitted_extrinsics[name].T @ np.array([0., 0., 1.])
        true_axis = true_extrinsics[name].T @ np.array([0., 0., 1.])
        assert np.linalg.norm(fitted_axis - true_axis) < 6e-3
    report = observability_report(problem, result.x)
    assert report["columns"] - report["rank"] == 4
    assert all(row["classification"] == "LONGITUDINAL_SEGMENT_TWIST_STICK_FIGURE_INVARIANT"
               for row in report["nullspace_vectors"])
    assert all(row["stick_figure_axis_observable"] for row in report["per_segment"].values())


def test_stationary_only_motion_is_numerically_degenerate():
    samples, truth = synthetic_samples(exciting=False)
    report = observability_report(ArticulatedCalibrationProblem(samples), truth)
    assert report["rank"] < report["columns"] and report["nullity"] > 0
