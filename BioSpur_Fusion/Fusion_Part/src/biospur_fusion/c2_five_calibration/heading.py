"""Five-sensor observations for the mature C2 persistent-heading machinery.

The state update, circular/axial statistics and interval replay are imported
from the ten-node implementation. Missing torso/proximal IMUs are never
fabricated. Arm movement planes without a measured parent are conditional
kinematic evidence, not independent observations of a constant wrist yaw.
"""
import math

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from biospur_fusion.c2_coupled_progressive.estimator import _PersistentHeadingState
from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import (
    _axial_heading_delta, _circular_concentration, _signed_horizontal_angle,
    _weighted_circular_mean,
)
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis, matrices, relative
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .phase_contract import direction_target

# Same broad 25-degree movement-plane likelihood used by mature C2 hip
# updates. The absent torso/proximal measurement does not justify a tight
# 10-degree two-sensor hinge factor.
PLANE_SIGMA_DEG = 25.


def _aligned(episode, node, calibration, start_s=0., stop_s=30.):
    child=episode[node]['imu']; root=episode[NODES[0]]['imu']
    elapsed=child[:,0]-child[0,0]
    keep=(elapsed>=start_s)&(elapsed<stop_s)&(child[:,0]>=root[0,0])&(child[:,0]<=root[-1,0])
    rows=child[keep]
    if len(rows)<100:raise ValueError('insufficient complete functional interval')
    pelvis=relative(root,NODES[0],calibration)
    root_rotation=Slerp(root[:,0]-root[0,0],Rotation.from_matrix(pelvis))(rows[:,0]-root[0,0]).as_matrix()
    beta=Rotation.from_rotvec([0.,0.,calibration['functional_yaw_rad'][NODES.index(node)]]).as_matrix()
    sensor=beta@matrices(rows).as_matrix()
    return rows,root_rotation,sensor


def fit_registered_heading(episodes, calibration, *, prefix=False):
    if any(n.startswith('H') for n in episodes):
        raise ValueError('H cannot supply calibration heading observations')
    if prefix:
        from .phase_contract import recorded_prefix
        recorded_prefix(episodes)
    factors_by_node={}
    evidence={}
    for index,node in enumerate(NODES[1:],1):
        state=_PersistentHeadingState()
        factors=[]
        if index<=2:
            elbow='06_elbow_left' if index==1 else '07_elbow_right'
            names=[('02_t_pose','directed_side',0.,30.,None),
                   ('04_shoulder_left' if index==1 else '05_shoulder_right','axis_forward',0.,30.,None),
                   (elbow,'axis_lateral',0.,15.,'flexion'),
                   (elbow,'directed_forward',15.,30.,'pronation')]
        else:
            names=[('10_knee_left_seated' if index==3 else '11_knee_right_seated','axis_lateral',0.,30.,None),
                   ('16_squat','axis_lateral',0.,30.,None),
                   ('18_heel_to_butt_left' if index==3 else '19_heel_to_butt_right','axis_lateral',0.,30.,None)]
        for name,kind,start,stop,phase_id in names:
            if prefix and name not in episodes:
                continue
            rows,root,sensor=_aligned(episodes[name],node,calibration,start_s=start,stop_s=stop)
            axis_audit=None
            target_axis,_=direction_target(kind,index-1)
            target=root@target_axis
            if kind in ('directed_side','directed_forward'):
                long=np.asarray(calibration['forearm_mount_calibration'][node]['axis_sensor_long'])
                direction=-sensor@long
                support=np.linalg.norm(direction[:,:2],axis=1)*np.linalg.norm(target[:,:2],axis=1)
                weights=support**2
                samples=_signed_horizontal_angle(direction,target)
                measurement=_weighted_circular_mean(samples,weights)
                concentration=_circular_concentration(samples,weights)
                quality=float(np.mean(weights)*concentration)
            else:
                axis,axis_audit=_gyro_axis(episodes[name][node]['imu'],start,stop)
                direction=sensor@axis
                measurement,stats=_axial_heading_delta(direction,target,state.delta_rad)
                concentration=stats['double_angle_concentration']
                quality=float(stats['mean_horizontal_support']**2*concentration*axis_audit['principal_fraction'])
                axis_audit.update(stats)
            used_for_heading = index > 2 or kind == 'directed_side'
            if used_for_heading:
                filtered,variance=state.update(float(np.median(rows[:,0])),float(measurement),
                                               math.radians(PLANE_SIGMA_DEG)**2,quality)
            else:
                # Both early axes and the late directed sagittal phase
                # depend on unmeasured chest/upper-arm motion. Preserve their
                # distinct time support for conditional C2 fitting; their
                # pelvis-referenced diagnostic means are not wrist-yaw truth.
                filtered,variance=state.delta_rad,state.variance_rad2
            factors.append(dict(action=name,source_role=kind,
                phase_id=phase_id,formal_interval_s=[start,stop],
                start_time_s=float(rows[0,0]),stop_time_s=float(rows[-1,0]),
                measurement_time_s=float(np.median(rows[:,0])),measurement_delta_rad=float(measurement),
                filtered_delta_rad=float(filtered),posterior_variance_rad2=float(variance),
                quality=quality,circular_concentration=float(concentration),
                used_for_frozen_heading=used_for_heading,
                calibration_role='heading_anchor' if used_for_heading else 'conditional_arm_direction_prior',
                base_sigma_deg=PLANE_SIGMA_DEG,row_count=len(rows),axis_audit=axis_audit))
        if not factors:
            raise ValueError('retained heading has no arrived functional evidence')
        factors_by_node[node]=factors
        evidence[node]=dict(final_heading_correction_deg=float(np.degrees(state.delta_rad)),
                            final_variance_rad2=float(state.variance_rad2),state_instances=1,episode_resets=0)
    return dict(factors=factors_by_node,states=evidence,
        frozen_correction_rad={node:rows[-1]['filtered_delta_rad']
                               for node,rows in factors_by_node.items()},
        state_owner='c2_coupled_progressive.estimator._PersistentHeadingState (unmodified)',
        statistic_owner='c2_coupled_progressive.pose_reset_avatar axial/circular primitives (unmodified)',
        replay_owner='c2_sparse_nodes.calibration.relative: one frozen calibration frame per retained IMU',
        parameter_history_is_motion=False,
        heading_drift_model='Raw continuous VQF remains; factor update history is not a measured drift trajectory.',
        adaptation='Forearm scalar heading remains T-pose anchored. Shoulder and early elbow axes plus late directed pronation are conditional thorax priors with distinct phase support. Shank broad plane factors unchanged. Time-dependent heading/torso inference remains unresolved.',
        absent_IMUs_fabricated=False,removed_node_parameters_used=False,H_used_for_fit=False,
        exact_pose_angles_used=False,initial_and_final_yaw_forced_equal=False,
        other_C2_actions='All recorded C2 actions remain in shared physical-parameter fitting; no unsupported heading evidence is invented.')
