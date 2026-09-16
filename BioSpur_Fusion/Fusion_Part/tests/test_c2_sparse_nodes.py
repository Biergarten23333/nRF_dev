"""Pure IMU isolation, rigid geometry, rotation conventions and ambiguity."""
from types import SimpleNamespace

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_sparse_nodes import inputs
from biospur_fusion.c2_sparse_nodes.calibration import relative, transverse_axis
from biospur_fusion.c2_sparse_nodes.model import fk, orientations, fit_frame
from biospur_fusion.c2_sparse_nodes.reference_timing import map_reference_times


def test_reference_sync_recovers_identical_hardware_instants_with_different_clocks():
    timer_us = np.array([12000000., 12005000., 12010000.])
    new = dict(a_ns_per_us=1000.03, b_ns=230000000000000.)
    expected = (timer_us*new['a_ns_per_us']+new['b_ns'])*1e-9
    # Per-node C2 offsets must be undone, including their signs.
    for offset in (-.34, .095, 0.):
        actual = map_reference_times(timer_us*1e-6+offset, new, offset_s=offset)
        assert np.allclose(actual, expected, atol=1e-9, rtol=0)
    old = dict(a_ns_per_us=999.97, b_ns=229000000000000.)
    grid_start = 229011999650000.
    elapsed = (timer_us*old['a_ns_per_us']+old['b_ns']-grid_start)*1e-9
    actual = map_reference_times(elapsed, new, grid_start_ns=grid_start, source_clock=old)
    assert np.allclose(actual, expected, atol=1e-9, rtol=0)

LENGTHS = dict(upper_arm=.3175, forearm=.25375, thigh=.48, shank=.43, shoulder_width=.4125)
AXES = np.array([[0.,-1.,0.], [0.,-1.,0.], [0.,1.,0.], [0.,1.,0.]])


def test_removed_nodes_and_all_uwb_payloads_are_excluded(monkeypatch):
    class Excluded:
        @property
        def payload(self):
            raise AssertionError('forbidden payload decoded')
    for node, kind in [('BSF31CC', 3), ('BSFAA61', 3), (inputs.NODES[0], 1)]:
        excluded = Excluded()
        excluded.node_name, excluded.kind = node, kind
        monkeypatch.setattr(inputs, 'decode_frame', lambda _: excluded)
        assert inputs.selected_frame(b'valid envelope') is None
    kept = SimpleNamespace(node_name=inputs.NODES[0], kind=3, payload=b'')
    monkeypatch.setattr(inputs, 'decode_frame', lambda _: kept)
    assert inputs.selected_frame(b'valid envelope') is kept


def test_common_yaw_closure_handles_vectors_and_initial_identity():
    rows = np.zeros((5, 11)); rows[:, 0] = np.linspace(0, 10, 5); rows[:, 1] = 1
    c = dict(initial_sensor_rotations=np.tile(np.eye(3), (5, 1, 1)), initial_time=0.,
             final_time=10., pelvis_closure_rad=.5, functional_yaw_rad=[0.] * 5)
    actual = relative(rows, inputs.NODES[0], c)
    assert np.allclose(actual[0], np.eye(3))
    assert np.allclose(actual[-1], Rotation.from_euler('z', -.5).as_matrix())
    assert actual.shape == (5, 3, 3)


def test_measured_lengths_hold_in_arbitrary_poses_and_change_geometry():
    rng = np.random.default_rng(31)
    for _ in range(20):
        x = rng.normal(size=9)
        r = Rotation.random(5, random_state=rng).as_matrix()
        j = fk(x, r, AXES, LENGTHS, .36, .1)
        for k in range(4):
            a = 1 + 3 * k
            expected = (LENGTHS['upper_arm'], LENGTHS['forearm']) if k < 2 else (LENGTHS['thigh'], LENGTHS['shank'])
            assert np.allclose(np.linalg.norm(np.diff(j[a:a+3], axis=0), axis=1), expected, atol=1e-12)
        j2 = fk(x, r, AXES, {**LENGTHS, 'forearm': .26375}, .36, .1)
        assert np.isclose(np.linalg.norm(j2[3] - j[3]), .01)


def test_distinct_human_poses_have_identical_five_orientation_observations():
    r = np.tile(np.eye(3), (5, 1, 1))
    a, b = np.zeros(9), np.zeros(9)
    b[3] = np.deg2rad(45)
    _, pa = orientations(a, r, AXES)
    _, pb = orientations(b, r, AXES)
    # Both predict exactly the same distal rotations through their hinge chains.
    for x, p in ((a, pa), (b, pb)):
        distal = p @ Rotation.from_rotvec(x[3:7, None] * AXES).as_matrix()
        assert np.allclose(distal, r[1:], atol=1e-12)
    ja, jb = [fk(x, r, AXES, LENGTHS, .36, .1) for x in (a, b)]
    assert np.linalg.norm(ja[2] - jb[2]) > .2
    assert np.linalg.norm(ja[3] - jb[3]) > .2


def test_ik_respects_joint_bounds_and_reports_latent_dofs():
    r = Rotation.from_rotvec(np.array([[0,0,0], [0,-1.4,0], [0,-.8,0], [0,-.5,0], [0,-.2,0]])).as_matrix()
    x, audit = fit_frame(r, AXES)
    assert audit['success']
    assert np.all(x[3:7] >= 0) and np.all(x[3:7] <= np.deg2rad(155))
    assert audit['instantaneous_unobserved_dof'] == 9


def test_pronation_does_not_replace_flexion_axis():
    t = np.linspace(0, 10, 2000)
    omega = np.column_stack((np.zeros(len(t)), np.sin(2*t), 8*np.sin(3*t)))
    axis, audit = transverse_axis(omega, np.full(len(t), .005))
    assert np.allclose(axis, [0,-1,0])
    assert audit['principal_fraction'] > .999
    assert audit['removed_axial_energy_fraction'] > .95


def test_elbow_flexion_and_pronation_recompose_measured_orientation():
    r = Rotation.random(5, random_state=31).as_matrix()
    x = np.array([.1,.2,.1, .3,.5,.1,.4, .7,-.4])
    _, proximal = orientations(x,r,AXES)
    reconstructed = proximal @ Rotation.from_rotvec(x[3:7,None]*AXES).as_matrix()
    reconstructed[:2] = reconstructed[:2] @ Rotation.from_rotvec(np.column_stack((np.zeros((2,2)),x[7:9]))).as_matrix()
    assert np.allclose(reconstructed,r[1:])


def test_elbow_flexes_forward_and_knee_flexes_backward():
    distal = Rotation.from_rotvec(np.pi/2*AXES).as_matrix()
    directions = -distal[:,:,2]
    assert np.allclose(directions[:2], [[1,0,0],[1,0,0]], atol=1e-12)
    assert np.allclose(directions[2:], [[-1,0,0],[-1,0,0]], atol=1e-12)
    # A seated thigh points forward when the shank hangs down at 90 deg knee flexion.
    retained = np.tile(np.eye(3), (5,1,1))
    x = np.r_[np.zeros(3), np.pi/2*np.ones(4), np.zeros(2)]
    _, prox = orientations(x,retained,AXES)
    assert np.allclose(-prox[2:,:,2], [[1,0,0],[1,0,0]],atol=1e-12)
