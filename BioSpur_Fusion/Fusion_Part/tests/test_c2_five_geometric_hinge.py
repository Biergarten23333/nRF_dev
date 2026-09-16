"""Asset-free inverse-hinge checks against the mature forward owner.

Expected poses originate in an independently declared DOWN/+X canonical
hinge, not in JointModel.rotation. SMPL rest directions deliberately differ.
"""
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_articulated_biomechanics.model import HingeJoint
from biospur_fusion.c2_articulated_biomechanics.orientation_ik import (
    reconstruct_distal_orientation,
)
from biospur_fusion.c2_five_calibration.anatomy import JointModel


PARENTS = [16, 17, 1, 2]
CHILDREN = [18, 19, 4, 5]
TIPS = [20, 21, 7, 8]
OBSERVED = [0, *CHILDREN]
DOWN = np.array([0., 0., -1.])
HINGE = np.array([1., 0., 0.])
LIMITS = np.array([150., 150., 120., 120.])


def unit(v):
    return np.asarray(v) / np.linalg.norm(v)


def wxyz(matrix):
    q = Rotation.from_matrix(matrix).as_quat()
    return np.concatenate((q[..., 3:4], q[..., :3]), axis=-1)


@pytest.fixture
def rig():
    distal = np.array([unit(v) for v in (
        [1., .12, .08], [-1., .07, .12], [.05, -1., .06], [-.07, -1., .03],
    )])
    proximal = np.array([unit(d + shift) for d, shift in zip(distal, (
        [.01, -.18, .13], [.02, .16, -.11], [.12, .01, -.09], [-.10, .03, .14],
    ))])
    ideal = np.array([[1., 0., 0.], [-1., 0., 0.], [0., -1., 0.], [0., -1., 0.]])
    # Functional -Y for forearms maps through left/right T-pose rotations
    # to SMPL -Y/+Y; functional +Y for knees maps to SMPL +X.
    positive = np.array([[0., -1., 0.], [0., 1., 0.], [1., 0., 0.], [1., 0., 0.]])
    offsets = np.zeros((24, 3))
    correction = [np.eye(3)]
    child_basis, parent_basis = [], []
    for i, (a, d) in enumerate(zip(proximal, distal)):
        f = Rotation.align_vectors(ideal[i:i+1], d[None])[0].as_matrix()
        k = unit(f.T @ positive[i])
        n = Rotation.align_vectors(d[None], a[None])[0].as_matrix()
        bd = np.column_stack((k, np.cross(-d, k), -d))
        bp = n.T @ bd
        np.testing.assert_allclose(bd @ DOWN, d, atol=1e-14)
        np.testing.assert_allclose(bp @ DOWN, a, atol=1e-14)
        offsets[CHILDREN[i]] = .31 * a
        offsets[TIPS[i]] = .27 * d
        correction.append(f)
        child_basis.append(bd)
        parent_basis.append(bp)
    geometry = dict(rest_offsets_m=offsets, bone_frame_correction=np.array(correction))
    return dict(model=JointModel(geometry), a=proximal, d=distal,
                bd=np.array(child_basis), bp=np.array(parent_basis))


def canonical_pose(rig, angles, pronation):
    angles = np.asarray(angles)
    frames = len(angles)
    prior = np.tile(np.eye(3), (frames, 24, 1, 1))
    observed = np.tile(np.eye(3), (frames, 5, 1, 1))
    for frame in range(frames):
        for limb in range(4):
            parent = Rotation.from_rotvec([.23 + .07*frame, -.34 + .11*limb, .19 - .09*frame])
            child = (parent * Rotation.from_rotvec(HINGE * np.deg2rad(angles[frame, limb]))
                     * Rotation.from_rotvec(DOWN * pronation[frame, limb]))
            prior[frame, PARENTS[limb]] = parent.as_matrix() @ rig['bp'][limb].T
            observed[frame, limb+1] = child.as_matrix() @ rig['bd'][limb].T
    prior[:, OBSERVED] = observed
    return torch.from_numpy(prior), torch.from_numpy(observed)


def assert_forward_oracle(rig, actual, observed, angles):
    assert torch.equal(actual[:, OBSERVED], observed)
    for limb in range(4):
        parent = actual[:, PARENTS[limb]].numpy() @ rig['bp'][limb]
        child = observed[:, limb+1].numpy() @ rig['bd'][limb]
        joint = HingeJoint(str(limb), 'parent', 'child', 'synthetic',
                           (1., 0., 0.), (1., 0., 0.), (0., 0., 0., 1.),
                           1., 0., LIMITS[limb], 0, 0)
        corrected, audit = reconstruct_distal_orientation(
            wxyz(parent), wxyz(child), np.asarray(angles)[:, limb], joint)
        corrected_matrix = Rotation.from_quat(corrected[:, [1, 2, 3, 0]]).as_matrix()
        np.testing.assert_allclose(corrected_matrix, child, atol=2e-12)
        assert audit['fk_direction_residual_maximum_deg'] < 3e-6
        u, v = parent @ DOWN, child @ DOWN
        signed = np.arctan2(np.sum(np.cross(u, v) * (parent @ HINGE), axis=1),
                            np.sum(u*v, axis=1))
        np.testing.assert_allclose(np.rad2deg(signed), np.asarray(angles)[:, limb], atol=1e-10)


def test_independent_forward_inverse_roundtrip_with_pronation(rig):
    angles = np.array([[0., 15., 5., 0.], [35., 85., 55., 90.], [145., 130., 115., 110.]])
    pronation = np.array([[.4, -.7, 0., 0.], [.9, .2, 0., 0.], [-.5, .8, 0., 0.]])
    prior, observed = canonical_pose(rig, angles, pronation)
    parameters = rig['model'].initial(prior, observed)
    actual = rig['model'].rotation(prior, observed, parameters)
    np.testing.assert_allclose(np.rad2deg(parameters[:, 3:7].numpy()), angles, atol=1e-10)
    np.testing.assert_allclose(parameters[:, 7:9], -pronation[:, :2], atol=1e-12)
    np.testing.assert_allclose(actual[:, PARENTS], prior[:, PARENTS], atol=2e-12)
    assert_forward_oracle(rig, actual, observed, angles)


def test_mirrored_parent_is_repaired_without_rotating_trusted_child(rig):
    angles = np.array([[40., 70., 55., 85.], [90., 100., 100., 110.]])
    pronation = np.array([[.6, -.8, 0., 0.], [-.4, .3, 0., 0.]])
    true_parent, observed = canonical_pose(rig, angles, pronation)
    mirrored = true_parent.clone()
    for limb in range(4):
        kp = rig['bp'][limb] @ HINGE
        flip = Rotation.from_rotvec(np.deg2rad(2*angles[:, limb])[:, None] * kp).as_matrix()
        mirrored[:, PARENTS[limb]] = true_parent[:, PARENTS[limb]] @ torch.from_numpy(flip)
    projected = rig['model'].prior_target(mirrored, observed)
    np.testing.assert_allclose(projected[:, PARENTS], true_parent[:, PARENTS], atol=2e-12)
    assert_forward_oracle(rig, projected, observed, angles)
    np.testing.assert_allclose(rig['model'].prior_target(projected, observed), projected, atol=2e-12)


def test_rom_projection_uses_mature_limits_and_is_idempotent(rig):
    prior, observed = canonical_pose(rig, np.array([[170., 160., 145., 135.]]),
                                     np.array([[.4, -.6, 0., 0.]]))
    projected = rig['model'].prior_target(prior, observed)
    assert_forward_oracle(rig, projected, observed, LIMITS[None])
    np.testing.assert_allclose(rig['model'].prior_target(projected, observed), projected, atol=2e-12)


def test_smpl_equal_frame_neutral_is_not_claimed_a_geometric_fixed_point(rig):
    prior = torch.eye(3, dtype=torch.float64).repeat(1, 24, 1, 1)
    observed = torch.eye(3, dtype=torch.float64).repeat(1, 5, 1, 1)
    parameters = rig['model'].initial(prior, observed)
    expected = np.arctan2(np.linalg.norm(np.cross(rig['a'], rig['d']), axis=1),
                          np.sum(rig['a']*rig['d'], axis=1))
    np.testing.assert_allclose(parameters[0, 3:7], expected, atol=1e-12)
    assert np.all(expected > .05)
    projected = rig['model'].prior_target(prior, observed)
    assert not torch.allclose(projected[:, PARENTS], prior[:, PARENTS], atol=1e-8)
    assert_forward_oracle(rig, projected, observed, np.rad2deg(expected)[None])


def test_parallel_estimated_hinge_has_finite_declared_functional_fallback(rig):
    prior = torch.eye(3, dtype=torch.float64).repeat(1, 24, 1, 1)
    observed = torch.eye(3, dtype=torch.float64).repeat(1, 5, 1, 1)
    for limb in range(2):
        kp = rig['bp'][limb] @ HINGE
        prior[0, PARENTS[limb]] = torch.from_numpy(
            Rotation.align_vectors(rig['d'][limb:limb+1], kp[None])[0].as_matrix())
    parameters = rig['model'].initial(prior, observed)
    assert torch.isfinite(parameters).all()
    np.testing.assert_allclose(parameters[0, 7:9], 0., atol=1e-12)
    actual = rig['model'].rotation(prior, observed, parameters)
    assert_forward_oracle(rig, actual, observed, np.rad2deg(parameters[:, 3:7].numpy()))
