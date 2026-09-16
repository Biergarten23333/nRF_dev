"""C2 pelvis frame proposal from pitch and axial functional excitation.

Both axes are conditional exercise evidence: natural coupled movement is not
silently declared a pure joint motion. Standing gravity chooses only the
axial sign, rather than forcing the whole pelvis longitudinal axis vertical.
"""
import copy
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis
from biospur_fusion.c2_sparse_nodes.functional_frames import signed_functional_frame
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def frame_from_axes(lateral, longitudinal):
    lateral=np.asarray(lateral,dtype=float);longitudinal=np.asarray(longitudinal,dtype=float)
    if lateral.shape!=(3,) or longitudinal.shape!=(3,) or not np.isfinite([lateral,longitudinal]).all():
        raise ValueError('two finite functional axes required')
    nl=np.linalg.norm(lateral);nz=np.linalg.norm(longitudinal)
    if min(nl,nz)<1e-12:raise ValueError('nonzero functional axes required')
    y=lateral/nl;z=longitudinal/nz
    # Symmetric closest orthogonal axis pair; neither motion gets an invented
    # statistical weight. Polar projection preserves an already orthogonal pair.
    x=np.cross(y,z)
    if np.linalg.norm(x)<1e-8:raise ValueError('independent functional axes required')
    basis=np.column_stack((x/np.linalg.norm(x),y,z))
    u,_,vt=np.linalg.svd(basis)
    frame=u@vt
    if np.linalg.det(frame)<0:raise ValueError('functional axes produced reflection')
    return frame


def pelvis_two_axis_proposal(episodes, calibration):
    if any(n.startswith('H') for n in episodes) or any(set(e)!=set(NODES) for e in episodes.values()):
        raise ValueError('only five-node C2 evidence permitted')
    node=NODES[0];initial=np.asarray(calibration['initial_sensor_rotations'])[0]
    pitch=episodes['14_trunk_flex_extend'][node]['imu']
    axial=episodes['15_trunk_axial_rotation'][node]['imu']
    # Reuse the established signed pitch convention, but recover the original
    # measured pitch axis rather than its standing-gravity projection.
    _,_,pitch_sign=signed_functional_frame(pitch,initial,1.)
    lateral,pitch_audit=_gyro_axis(pitch,0.,30.)
    if lateral@np.asarray(pitch_sign['lateral_axis_sensor'])<0:lateral=-lateral
    longitudinal,axial_audit=_gyro_axis(axial,0.,30.)
    gravity_up=initial.T[:,2]
    if longitudinal@gravity_up<0:longitudinal=-longitudinal
    mount=frame_from_axes(lateral,longitudinal)
    start=initial@mount;yaw=float(-np.arctan2(start[1,0],start[0,0]))
    result=copy.deepcopy(calibration)
    old=np.asarray(calibration['segment_axes_in_sensor'])[0]
    result['segment_axes_in_sensor'][0]=mount.tolist()
    result['functional_yaw_rad'][0]=yaw
    result.pop('mount_axis_information',None)
    result['calibration_accepted']=False
    audit=dict(pitch=pitch_audit,axial=axial_audit,
        measured_axis_dot=float(lateral@longitudinal),
        axis_pair_sine=float(np.linalg.norm(np.cross(lateral,longitudinal))),
        mount_change_deg=float(np.rad2deg(Rotation.from_matrix(old.T@mount).magnitude())),
        standing_long_axis_tilt_deg=float(np.rad2deg(np.arccos(np.clip(start[2,2],-1,1)))),
        pitch_sign_source='existing registered pitch first-excursion convention',
        axial_sign_source='initial gravity hemisphere only',
        axis_direction_source='measured gyro PCA from actual actions14 and15',
        uncertainty='exercise-conditional axes; no covariance or anatomical accuracy claim',
        parameter_scope='pelvis fixed mounting and corresponding initial forward yaw only',
        H_used=False,ten_used=False,learned_features_require_refresh=True)
    previous=result.get('five_node_functional_frames',{}).get(node)
    if previous is not None:
        result['previous_pelvis_functional_frame']=copy.deepcopy(previous)
    result.setdefault('five_node_functional_frames',{})[node]=dict(
        owner='pelvis_two_axis_proposal',episodes=['14_trunk_flex_extend','15_trunk_axial_rotation'],
        lateral_axis_sensor=mount[:,1].tolist(),longitudinal_axis_sensor=mount[:,2].tolist(),
        forward_axis_sensor=mount[:,0].tolist(),world_yaw_deg=float(np.rad2deg(yaw)),
        longitudinal_axis_source='action15 measured axial excitation; gravity hemisphere sign only')
    result['pelvis_two_axis_proposal']=audit
    return result,audit
