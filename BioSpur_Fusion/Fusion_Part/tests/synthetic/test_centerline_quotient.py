import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.anthropometry import Anthropometry, REQUIRED_OFFSETS
from biospur_fusion.calibration.articulated_batch import CalibrationSamples, SEGMENTS
from biospur_fusion.calibration.centerline_quotient import (
    LIMB_SEGMENTS, CenterlineQuotientProblem, QuotientStatic, pack,
    predict_antennas, quotient_observability,
)


def anthropometry():
    scalars = {
        "upper_arm_L_m": .31, "upper_arm_R_m": .305, "forearm_L_m": .255, "forearm_R_m": .26,
        "thigh_L_m": .425, "thigh_R_m": .43, "shank_L_m": .395, "shank_R_m": .40,
        "biacromial_width_m": .34, "hip_width_m": .19, "hip_vertical_offset_m": -.045,
        "c7_to_pelvis_m": .42, "foot_length_L_m": .26, "foot_length_R_m": .26,
        "ankle_height_L_m": .09, "ankle_height_R_m": .09,
    }
    return Anthropometry("synthetic", scalars, {k: .003 for k in scalars},
                         {k: np.zeros(3) for k in REQUIRED_OFFSETS},
                         {k: .003 for k in REQUIRED_OFFSETS}, "BAREFOOT")


def fixture(exciting=True):
    rng = np.random.default_rng(8204); count = 48
    r_nv = Rotation.from_euler("yx", [.16, -.11]).as_matrix()
    pelvis = Rotation.from_rotvec([.12, -.08, .06]).as_matrix()
    torso = Rotation.from_rotvec([-.09, .10, -.04]).as_matrix()
    limb_axes = {name: rng.normal(size=3) for name in LIMB_SEGMENTS}
    limb_axes = {name: value/np.linalg.norm(value) for name, value in limb_axes.items()}
    truth = QuotientStatic(r_nv, pelvis, torso, limb_axes)
    q = np.empty((count, len(SEGMENTS), 3, 3))
    for knot in range(count):
        phase = 2*np.pi*knot/count if exciting else 0.
        for segment, _ in enumerate(SEGMENTS):
            q[knot, segment] = Rotation.from_euler(
                "xyz", [.5*np.sin(phase + .21*segment), .35*np.cos(2*phase + .13*segment),
                        .25*np.sin(3*phase + .17*segment)]).as_matrix()
    blank = CalibrationSamples(np.arange(count, dtype=np.int64)*100_000_000,
                               np.asarray(["arms" if k < count/2 else "squats" for k in range(count)]),
                               np.zeros((count, len(SEGMENTS), 3)),
                               np.tile(np.eye(3)*4e-6, (count, len(SEGMENTS), 1, 1)), q,
                               np.ones((count, len(SEGMENTS)), bool))
    prediction, _ = predict_antennas(blank, truth, anthropometry())
    root = np.c_[.2*np.sin(np.linspace(0, 2, count)), .1*np.cos(np.linspace(0, 1, count)),
                 np.ones(count)]
    p_v4 = np.einsum("ij,ntj->nti", r_nv.T, prediction + root[:, None])
    samples = CalibrationSamples(blank.time_ns, blank.action, p_v4, blank.covariance_v4_m2,
                                 q, blank.valid_position)
    return samples, truth


def test_quotient_recovers_centerline_and_keeps_axial_twist_unavailable():
    samples, truth = fixture(); problem = CenterlineQuotientProblem(samples, anthropometry())
    initial = pack(truth) + np.random.default_rng(44).normal(0, .01, len(pack(truth)))
    candidate, result = problem.solve(initial, max_nfev=120)
    assert result.success
    assert Rotation.from_matrix(candidate.R_N_from_V4 @ truth.R_N_from_V4.T).magnitude() < 2e-3
    for segment in LIMB_SEGMENTS:
        assert np.linalg.norm(candidate.limb_axis_sensor[segment] - truth.limb_axis_sensor[segment]) < 3e-3
    gates = {
        "null_perturbation_norm": .001,
        "maximum_segment_axis_angular_change_rad": 1e-4,
        "maximum_joint_centre_displacement_m": 1e-4,
        "maximum_antenna_displacement_m": 1e-4,
    }
    report = quotient_observability(problem, result.x, gates)
    assert report["centerline_observable"]
    assert not report["full_segment_pose_observable"]
    assert len(report["unavailable_full_pose_dofs"]) == 8


def test_stationary_quotient_is_rank_deficient_and_reports_physical_units():
    samples, truth = fixture(exciting=False); problem = CenterlineQuotientProblem(samples, anthropometry())
    gates = {"null_perturbation_norm": .001,
             "maximum_segment_axis_angular_change_rad": 1e-4,
             "maximum_joint_centre_displacement_m": 1e-4,
             "maximum_antenna_displacement_m": 1e-4}
    report = quotient_observability(problem, pack(truth), gates)
    assert report["nullity"] > 0
    assert all("maximum_segment_axis_angular_change_deg" in row for row in report["null_directions"])
    assert all("maximum_joint_centre_displacement_mm" in row for row in report["null_directions"])
