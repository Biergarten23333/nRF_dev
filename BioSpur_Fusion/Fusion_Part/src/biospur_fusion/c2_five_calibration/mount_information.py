"""Structural mounting information supplied by recorded functional axes.

Repeated measurements of one anatomical axis cannot identify rotation about
that axis. Scores below are geometry/quality diagnostics, NOT inverse noise
covariance: the parent segment may move during these exercises.
"""
import numpy as np

from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .phase_contract import recorded_prefix


def axis_information(body_axes, quality):
    axes=np.asarray(body_axes,dtype=float)
    quality=np.asarray(quality,dtype=float)
    if (axes.ndim!=2 or axes.shape[1]!=3 or quality.shape!=(len(axes),)
            or not np.isfinite(axes).all() or not np.isfinite(quality).all()
            or np.any(quality<0) or np.any(np.linalg.norm(axes,axis=1)<1e-12)):
        raise ValueError('finite nonzero axes and nonnegative quality required')
    axes=axes/np.linalg.norm(axes,axis=1)[:,None]
    matrix=np.sum(quality[:,None,None]*(np.eye(3)-axes[:,:,None]*axes[:,None,:]),axis=0)
    values,vectors=np.linalg.eigh(matrix)
    supported=values>max(float(values[-1]),1.)*1e-10
    return dict(structural_rank=int(supported.sum()),eigenvalues=values.tolist(),
                unresolved_body_rotation_axes=vectors[:,~supported].T.tolist(),
                is_posterior_covariance=False)


def mounting_axis_audit(episodes,calibration,*,prefix=False):
    """Use explicit action semantics; no standing-gravity pseudo-measurement."""
    recorded=recorded_prefix(episodes,require_complete=not prefix)
    if any(set(episode)!=set(NODES) for episode in episodes.values()):
        raise ValueError('mount information requires exactly five retained nodes')
    definitions={
        0:[('14_trunk_flex_extend',0.,30.,[0,1,0]),('15_trunk_axial_rotation',0.,30.,[0,0,1])],
        1:[('06_elbow_left',0.,15.,[0,1,0]),('06_elbow_left',15.,30.,[0,0,1])],
        2:[('07_elbow_right',0.,15.,[0,1,0]),('07_elbow_right',15.,30.,[0,0,1])],
        3:[('10_knee_left_seated',0.,30.,[0,1,0]),('16_squat',0.,30.,[0,1,0]),('18_heel_to_butt_left',0.,30.,[0,1,0])],
        4:[('11_knee_right_seated',0.,30.,[0,1,0]),('16_squat',0.,30.,[0,1,0]),('19_heel_to_butt_right',0.,30.,[0,1,0])],
    }
    result={}
    for index,node in enumerate(NODES):
        rows=[];axes=[];quality=[]
        mount=np.asarray(calibration['segment_axes_in_sensor'][index])
        for action,lo,hi,body_axis in definitions[index]:
            if action not in episodes:continue
            try:
                sensor_axis,audit=_gyro_axis(episodes[action][node]['imu'],lo,hi)
            except ValueError as error:
                rows.append(dict(action=action,interval_s=[lo,hi],supported=False,reason=str(error)))
                continue
            predicted=mount@np.asarray(body_axis)
            angle=np.rad2deg(np.arctan2(np.linalg.norm(np.cross(sensor_axis,predicted)),abs(sensor_axis@predicted)))
            rows.append(dict(action=action,interval_s=[lo,hi],supported=True,
                body_axis=body_axis,sensor_axis=sensor_axis.tolist(),current_axis_error_deg=float(angle),**audit))
            axes.append(body_axis);quality.append(audit['principal_fraction'])
        information=axis_information(axes,quality) if axes else dict(
            structural_rank=0,eigenvalues=[0.,0.,0.],unresolved_body_rotation_axes=np.eye(3).tolist(),is_posterior_covariance=False)
        result[node]=dict(**information,observations=rows)
    return dict(nodes=result,recorded_actions=recorded,H_used=False,
        standing_gravity_counted_as_measured_bone_axis=False,
        rank_scope='conditional on exercise-axis semantics; not proof of anatomical accuracy',
        weights='PCA principal fractions; dimensionless quality, not calibrated precision',
        missing_direction_policy='retain explicit uncertainty; repeated hinge motions do not supply a long axis')
