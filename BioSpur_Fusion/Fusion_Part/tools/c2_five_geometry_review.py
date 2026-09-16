"""Evaluation-only angles from the same 13 Cartesian joints shown by the viewer.

Order: pelvis; left shoulder/elbow/wrist; right shoulder/elbow/wrist;
left hip/knee/ankle; right hip/knee/ankle. No pose fitting occurs here.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.math_utils import array_binding, rotation_to_qmt_wxyz
from biospur_fusion.c2_coupled_progressive.output_coordinates import (
    _validated_reflection, freeze_capture_wide_lateral_reflection,
)


LIMBS = {'elbow_left': (1, 2, 3), 'elbow_right': (4, 5, 6),
         'knee_left': (7, 8, 9), 'knee_right': (10, 11, 12)}
PLANE_MIN_BEND_DEG = 10.
ZERO_LENGTH_M = 1e-12
# C2 local [right, forward, up] columns in SMPL [left, up, forward].
C2_TO_SMPL_BODY = np.array([[-1., 0., 0.], [0., 0., 1.], [0., 1., 0.]])


def freeze_five_output_coordinates(initial_pelvis_smpl, world_to_smpl):
    """Adapt measured initial pelvis to the mature post-FK reflection owner.

    Every finite initial frame is supplied; no reference mask is accepted.
    The transient trajectory contains only this measured pelvis, no inferred IMUs.
    """
    rotations = np.asarray(initial_pelvis_smpl, dtype=float)
    world_to_smpl = np.asarray(world_to_smpl, dtype=float)
    if rotations.ndim != 3 or rotations.shape[1:] != (3, 3):
        raise ValueError('initial pelvis must be frame x 3 x 3')
    finite = np.isfinite(rotations).all(axis=(1, 2))
    if not finite.any():
        raise ValueError('no finite own initial pelvis frames')
    for value in (world_to_smpl, rotations[finite]):
        if (value.shape[-2:] != (3, 3) or not np.isfinite(value).all() or
                not np.allclose(value.swapaxes(-1, -2) @ value, np.eye(3), atol=1e-6, rtol=0) or
                not np.allclose(np.linalg.det(value), 1., atol=1e-6, rtol=0)):
            raise ValueError('proper input rotations and proper world adapter required')
    adapted = world_to_smpl.T @ rotations[finite] @ C2_TO_SMPL_BODY
    trajectory = {'trajectory': {'00': {'pelvis': {
        'quat_world_segment_wxyz': rotation_to_qmt_wxyz(Rotation.from_matrix(adapted))}}}}
    convention = freeze_capture_wide_lateral_reflection(trajectory)
    result = {key: value.tolist() if isinstance(value, np.ndarray) else value
              for key, value in convention.items()}
    result.update(initial_pelvis_input_binding=array_binding(rotations),
                  initial_frame_count=len(rotations), finite_initial_frame_count=int(finite.sum()),
                  c2_to_smpl_body=C2_TO_SMPL_BODY.tolist(),
                  c2_to_smpl_body_determinant=float(np.linalg.det(C2_TO_SMPL_BODY)),
                  world_to_smpl=world_to_smpl.tolist(),
                  input_scope='OWN_FIVE_INITIAL_PELVIS_ONLY; NO_REFERENCE_VALID_MASK',
                  role='OUTPUT_COORDINATE_COMPARISON_ONLY; NOT_AN_ALGORITHM_REPAIR')
    return result


def five_fk_to_output(points_smpl, convention, *, source_space):
    """One Cartesian boundary; caller must identify untransformed SMPL FK.

    Arrays do not carry coordinate metadata: this guard prevents declared output
    points being reapplied, but cannot detect a caller falsely labeling an array.
    """
    if source_space != 'SMPL_FK':
        raise ValueError('output reflection requires untransformed SMPL_FK points')
    matrix = _validated_reflection(convention['matrix_world_output_from_internal'])
    return np.asarray(points_smpl) @ np.asarray(convention['world_to_smpl']) @ matrix.T


def _angle_deg(a, b):
    return np.degrees(np.arctan2(np.linalg.norm(np.cross(a, b), axis=-1),
                                np.sum(a * b, axis=-1)))


def _summary(values):
    if not len(values):
        return {'mean_deg': None, 'p95_deg': None, 'maximum_deg': None}
    return dict(mean_deg=float(np.mean(values)), p95_deg=float(np.quantile(values, .95)),
                maximum_deg=float(np.max(values)))


def compare_display_geometry(candidate, reference, valid):
    """Compare bone directions, bend magnitude and oriented limb-plane normals.

The normal is proximal-vector cross distal-vector, in anatomical chain order.
Its sign is retained: a mirrored 90-degree bend produces a 180-degree error.
Near-straight or near-folded planes are excluded if either sine is below
sin(10 degrees). This defines metric availability, not an acceptance gate.
"""
    candidate, reference = np.asarray(candidate, dtype=float), np.asarray(reference, dtype=float)
    valid = np.asarray(valid)
    if (candidate.ndim != 3 or candidate.shape[1:] != (13, 3) or
            reference.shape != candidate.shape or valid.shape != (len(candidate),) or
            valid.dtype != np.bool_):
        raise ValueError('matching frame x 13 x 3 positions and a boolean frame mask required')
    candidate, reference = candidate[valid], reference[valid]
    if not np.isfinite(candidate).all() or not np.isfinite(reference).all():
        raise ValueError('valid displayed joint positions must be finite')
    threshold = float(np.sin(np.deg2rad(PLANE_MIN_BEND_DEG)))
    report = dict(valid_frames=int(valid.sum()), plane_min_bend_sine=threshold,
                  plane_min_bend_deg=PLANE_MIN_BEND_DEG, zero_length_tolerance_m=ZERO_LENGTH_M,
                  normal_convention='proximal vector cross distal vector; oriented, no absolute dot',
                  acceptance_threshold_added=False, limbs={})
    for name, (a, b, c) in LIMBS.items():
        up, down = candidate[:, b] - candidate[:, a], candidate[:, c] - candidate[:, b]
        rp, rd = reference[:, b] - reference[:, a], reference[:, c] - reference[:, b]
        lengths = np.stack([np.linalg.norm(v, axis=-1) for v in (up, down, rp, rd)], axis=1)
        usable = np.all(lengths > ZERO_LENGTH_M, axis=1)
        up, down, rp, rd = [v[usable] / lengths[usable, i, None]
                            for i, v in enumerate((up, down, rp, rd))]
        normal, ref_normal = np.cross(up, down), np.cross(rp, rd)
        sine, ref_sine = np.linalg.norm(normal, axis=-1), np.linalg.norm(ref_normal, axis=-1)
        plane = (sine >= threshold) & (ref_sine >= threshold)
        normal, ref_normal = normal[plane] / sine[plane, None], ref_normal[plane] / ref_sine[plane, None]
        report['limbs'][name] = dict(
            compared_bone_frames=int(usable.sum()), degenerate_bone_frames=int((~usable).sum()),
            compared_plane_frames=int(plane.sum()), excluded_weak_plane_frames=int((~plane).sum()),
            proximal_direction_error=_summary(_angle_deg(up, rp)),
            distal_direction_error=_summary(_angle_deg(down, rd)),
            unsigned_bend_error=_summary(np.abs(_angle_deg(up, down) - _angle_deg(rp, rd))),
            oriented_plane_normal_error=_summary(_angle_deg(normal, ref_normal)))
    return report
