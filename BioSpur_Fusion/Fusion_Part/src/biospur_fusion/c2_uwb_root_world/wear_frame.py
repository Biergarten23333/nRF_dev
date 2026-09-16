"""Bind root inertial propagation to the existing physical wear-heading owner."""
from __future__ import annotations

import numpy as np

from biospur_fusion.c2_uwb_calibration.antenna_los import (
    NODE_OUTWARD_MINUS_Z_IN_SEGMENT, horizontal_yaw_alignment,
)


WORLD_FROM_WEAR = np.column_stack(([0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]))


def common_anatomical_world_yaw(initial_right_world):
    """One proper scene yaw from anatomical right to attested world minus-X.

    Unlike independent wear-normal registration this preserves all calibrated
    inter-segment rotations. It is a heading prior, not measured world heading.
    """
    right=np.asarray(initial_right_world,float)
    if right.shape!=(3,) or not np.isfinite(right).all() or np.linalg.norm(right[:2])<1e-6:
        raise ValueError('anatomical right must have a finite observable horizontal direction')
    return horizontal_yaw_alignment(right,np.array([-1.,0.,0.]))


def initial_wear_yaw_registration(node, initial_normal_world):
    """Bind this physical initial -Z to the existing attested wear direction.

    One proper world-Z yaw only; no range fitting, tilt adjustment, anatomical
    reflection, or reuse of another physical stream's registration matrix.
    The returned target is an engineering wear prior, not measured heading.
    """
    normal=np.asarray(initial_normal_world,float)
    if (normal.shape!=(3,) or not np.isfinite(normal).all()
            or not np.isclose(np.linalg.norm(normal),1.,atol=1e-9,rtol=0)):
        raise ValueError('initial physical wear normal must be a finite unit vector')
    target=WORLD_FROM_WEAR@NODE_OUTWARD_MINUS_Z_IN_SEGMENT[node]
    if np.linalg.norm(normal[:2])<1e-6 or np.linalg.norm(target[:2])<1e-6:
        raise ValueError('initial wear yaw cannot be determined from a vertical normal')
    return horizontal_yaw_alignment(normal,target), target.copy()


def registered_pelvis_rotations(pose, registration):
    """Return separate proper rotations; never transform force, bias or FK.

    The stored registration acts in world coordinates (left multiplication).
    This establishes consistency with the attested wear prior, not independent
    heading truth or sensor-to-root lever-arm calibration.
    """
    if (not np.array_equal(pose['time_s'], registration['time_s'])
            or not np.array_equal(pose['node_names'], registration['node_names'])):
        raise ValueError('wear registration must share exact pose time and node identities')
    nodes=list(map(str,pose['node_names']))
    if nodes.count('BSFC2CC') != 1:
        raise ValueError('requires the unique sealed pelvis node BSFC2CC')
    index=nodes.index('BSFC2CC')
    yaw=np.asarray(registration['yaw_registration_world'],float)[index]
    rotations=np.asarray(pose['pelvis_rotation_world_sensor'],float)
    if (yaw.shape != (3,3) or not np.isfinite(yaw).all()
            or not np.allclose(yaw.T@yaw,np.eye(3),atol=1e-10,rtol=0)
            or not np.isclose(np.linalg.det(yaw),1.,atol=1e-10,rtol=0)
            or not np.allclose(yaw[2],[0.,0.,1.],atol=1e-10,rtol=0)):
        raise ValueError('wear root registration must be a proper gravity-preserving yaw')
    if (rotations.shape != (len(pose['time_s']),3,3)
            or not np.isfinite(rotations).all()
            or not np.allclose(np.swapaxes(rotations,1,2)@rotations,np.eye(3),atol=1e-8,rtol=0)
            or not np.allclose(np.linalg.det(rotations),1.,atol=1e-8,rtol=0)):
        raise ValueError('root sensor rotations must be proper and finite')
    result=yaw@rotations
    normals=np.asarray(registration['node_normals_world'],float)[:,index]
    if normals.shape != (len(rotations),3) or not np.allclose(-result[:,:,2],normals,atol=1e-9,rtol=0):
        raise ValueError('registered root sensor minus-Z does not match physical wear normals')
    return result, yaw.copy()
