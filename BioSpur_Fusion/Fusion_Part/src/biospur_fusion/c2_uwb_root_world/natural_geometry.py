"""Natural endpoint geometry conditioned on an unprojected physical pose.

Limb FK consumes only long axes: below upper ROM the accepted bend-plane map
is exactly the identity on points (and their derivatives), irrespective of
anatomical parent twist. Upper-ROM caps are geometry priors, not certifications
of physical joint angles. No sensor/antenna orientation is returned or stored.
"""
import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_articulated_biomechanics.bend_plane import reconcile_hinge_bend_plane
from biospur_fusion.c2_articulated_biomechanics.bend_plane_linearization import linearize_bend_plane_projection
from biospur_fusion.c2_articulated_biomechanics.model import DOWN, _rotation, _wxyz
from biospur_fusion.c2_uwb_calibration.articulated_range import (
    SEGMENTS, _proxy_points_from_rotations, _corrected_proxy_point_jacobians,
)


def _capped_rows(rotations, hinges, *, derivative=False):
    index = {name: i for i, name in enumerate(SEGMENTS)}
    allowed = {(SEGMENTS[i], SEGMENTS[i+1]) for i in (2, 4, 6, 8)}
    seen = set()
    rows = {}
    for name, joint in hinges.items():
        pair = (joint.parent, joint.child)
        if pair not in allowed or pair in seen:
            raise ValueError('natural geometry requires disjoint elbow/knee pairs')
        if not 0 < joint.maximum_deg < 179.5:
            raise ValueError('natural geometry requires an observable upper-ROM limit')
        seen.add(pair)
        parent, child = index[joint.parent], index[joint.child]
        p, c = rotations[..., parent, :, :] @ DOWN, rotations[..., child, :, :] @ DOWN
        bend = np.degrees(np.arctan2(np.linalg.norm(np.cross(p, c), axis=-1),
                                    np.sum(p*c, axis=-1)))
        capped = bend > joint.maximum_deg
        if np.any(capped & (bend >= 179.5)):
            raise ValueError(f'{name}: capped endpoint bend plane is unobservable')
        # A point derivative is undefined at the cap; the full-map stencil has
        # the same 1e-6-radian step. Fail on either side of that narrow boundary.
        if derivative and np.any(np.abs(bend-joint.maximum_deg) <= np.degrees(2e-6)):
            raise ValueError(f'{name}: point derivative crosses upper-ROM boundary')
        rows[name] = (parent, child, capped)
    return rows


def natural_geometry(rotations, geometry, hinges, *, with_jacobian=True):
    """Single-epoch points/J in the physical right-local tangent and audit."""
    rotations = np.asarray(rotations, float)
    if rotations.shape != (10, 3, 3) or not np.isfinite(rotations).all():
        raise ValueError('natural geometry requires a finite ten-segment pose')
    rows = _capped_rows(rotations, hinges, derivative=with_jacobian)
    capped = {name: hinges[name] for name, (_, _, active) in rows.items() if active}
    if capped:
        if with_jacobian:
            mapped, chain, _ = linearize_bend_plane_projection(rotations, SEGMENTS, capped)
        else:
            mapped = _mapped_batch(rotations[None], hinges, rows=None)[0]
            chain = None
    else:
        mapped, chain = rotations, None
    mapping = dict(zip(SEGMENTS, mapped))
    points = _proxy_points_from_rotations(mapping, geometry)
    jacobian = None
    if with_jacobian:
        jacobian = _corrected_proxy_point_jacobians(
            mapping, {name: np.zeros(3) for name in SEGMENTS}, geometry, SEGMENTS)
        if chain is not None:
            jacobian = {name: value @ chain for name, value in jacobian.items()}
    audit = dict(mode='natural_geometry_only', physical_rotations_modified=False,
                 physical_angles_certified=False, geometry_upper_rom_capped_joints=tuple(capped),
                 endpoint_identity_fast_path=not capped,
                 geometry_projection_valid=True)
    return points, jacobian, audit


def _mapped_batch(rotations, hinges, rows=None):
    rows = _capped_rows(rotations, hinges) if rows is None else rows
    mapped = rotations.copy()
    for name, (parent, child, active) in rows.items():
        if not np.any(active):
            continue
        # Only observable capped rows enter this batch: each independently
        # resolves its plane; there is no held-state propagation across rows.
        parent_q, child_q, _ = reconcile_hinge_bend_plane(
            _wxyz(Rotation.from_matrix(rotations[active, parent])),
            _wxyz(Rotation.from_matrix(rotations[active, child])), hinges[name])
        mapped[active, parent] = _rotation(parent_q).as_matrix()
        mapped[active, child] = _rotation(child_q).as_matrix()
    return mapped


def natural_geometry_points_batch(rotations, geometry, hinges):
    """Batched binding check, using exactly the runtime endpoint map."""
    rotations = np.asarray(rotations, float)
    if rotations.ndim != 4 or rotations.shape[1:] != (10, 3, 3) or not np.isfinite(rotations).all():
        raise ValueError('natural geometry requires finite batched ten-segment poses')
    rows = _capped_rows(rotations, hinges)
    mapped = (_mapped_batch(rotations, hinges, rows)
              if any(np.any(active) for _, _, active in rows.values()) else rotations)
    return _proxy_points_from_rotations(dict(zip(SEGMENTS, mapped.swapaxes(0, 1))), geometry)
