"""Separate anatomical axial coordinates from physical sensor orientation.

The distal direction must not be redirected merely because the independently
estimated proximal axial coordinate disagrees with the observed bend plane.
This boundary adapter changes the anatomical parent twist, not either sensor
measurement. Consumers of antenna normals must retain the physical stream.
"""
import numpy as np
from scipy.spatial.transform import Rotation
from .model import DOWN, _rotation, _wxyz
from .orientation_ik import reconstruct_distal_orientation


def reconcile_hinge_bend_plane(parent_q, child_q, joint, *, initial_twist_rad=0.,
                               extension_threshold_deg=.5):
    """Preserve observed endpoints below ROM, with a held near-extension branch.

    The half-degree threshold regularizes an unobservable plane; it does not
    zero the measured flexion or move endpoints. Returned twist state allows
    continuous chunks without resetting at an action boundary.
    """
    parent_q, child_q = np.asarray(parent_q), np.asarray(child_q)
    if (parent_q.ndim != 2 or parent_q.shape[1] != 4 or parent_q.shape != child_q.shape
            or len(parent_q) == 0 or not np.isfinite(parent_q).all()
            or not np.isfinite(child_q).all()):
        raise ValueError('finite matching nonempty Nx4 orientation streams required')
    if not 0 <= extension_threshold_deg < 90 or not np.isfinite(initial_twist_rad):
        raise ValueError('invalid extension threshold or initial axial state')
    parent, child = _rotation(parent_q), _rotation(child_q)
    p, c = parent.apply(DOWN), child.apply(DOWN)
    normal = np.cross(p, c)
    sine = np.linalg.norm(normal, axis=1)
    bend = np.degrees(np.arctan2(sine, np.sum(p*c, axis=1)))
    axis = parent.apply(np.broadcast_to(joint.positive_sign*np.asarray(joint.parent_axis), p.shape).copy())
    reliable = (bend > extension_threshold_deg) & (bend < 180-extension_threshold_deg)
    target = normal / np.maximum(sine[:, None], 1e-15)
    angle = np.arctan2(np.sum(np.cross(axis, target)*p, axis=1), np.sum(axis*target, axis=1))
    # Hold the *relative anatomical twist*, not an absolute world heading.
    indices = np.maximum.accumulate(np.where(reliable, np.arange(len(angle)), -1))
    twist = np.where(indices >= 0, angle[np.maximum(indices, 0)], initial_twist_rad)
    twist = np.unwrap(np.r_[initial_twist_rad, twist])[1:]
    corrected_parent = Rotation.from_rotvec(p*twist[:, None])*parent
    corrected_child = np.array(child_q, copy=True)
    above = bend > joint.maximum_deg
    if np.any(above):
        replacement, _ = reconstruct_distal_orientation(
            _wxyz(corrected_parent)[above], np.asarray(child_q)[above],
            np.full(int(above.sum()), joint.maximum_deg), joint)
        corrected_child[above] = replacement
    return _wxyz(corrected_parent), corrected_child, {
        'last_twist_rad': float(twist[-1]),
        'parent_axial_correction_max_deg': float(np.degrees(abs(twist)).max()),
        'near_extension_held_rows': int((~reliable).sum()),
        'upper_rom_capped_rows': int(above.sum()),
        'physical_sensor_orientation_modified': False,
        'extension_threshold_deg': extension_threshold_deg,
    }
