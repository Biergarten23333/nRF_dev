"""Five retained IMUs: functional sensor mounting and measured segment lengths."""
from __future__ import annotations

import numpy as np
from scipy.spatial.transform import Rotation, Slerp

from .inputs import NODES


def matrices(rows):
    q = rows[:, 1:5]
    if not np.allclose(np.linalg.norm(q, axis=1), 1, atol=1e-6):
        raise ValueError('invalid quaternion')
    return Rotation.from_quat(q[:, [1, 2, 3, 0]])


def robust_still(rows):
    gyro = np.linalg.norm(rows[:, 8:11], axis=1)
    acc = np.linalg.norm(rows[:, 5:8], axis=1)
    acc = abs(acc - np.median(acc))
    mask = (gyro <= np.quantile(gyro, .6)) & (acc <= np.quantile(acc, .8))
    if mask.sum() < 100:
        raise ValueError('insufficient static rows')
    return matrices(rows[mask]).mean().as_matrix()


def transverse_axis(omega, dt):
    """Remove long-axis spin before estimating flexion; do not fit to Hxx."""
    transverse = omega.copy()
    transverse[:, 2] = 0
    valid = (dt < .025) & (np.linalg.norm(transverse, axis=1) > .15)
    if valid.sum() < 50:
        raise ValueError('insufficient transverse hinge excitation')
    vals, vectors = np.linalg.eigh(transverse[valid].T @ transverse[valid])
    axis = vectors[:, -1]
    if axis[1] > 0:
        axis = -axis
    return axis, dict(selected_rows=int(valid.sum()), principal_fraction=float(vals[-1]/vals.sum()),
                     eigenvalues=vals.tolist(), removed_axial_energy_fraction=float(
                         np.sum(omega[:, 2]**2)/np.sum(omega**2)))


def relative(rows, node, calibration):
    i = NODES.index(node)
    c = calibration
    raw = matrices(rows).as_matrix() @ np.asarray(c['initial_sensor_rotations'])[i].T
    if c['pelvis_closure_rad'] == 0.:
        common = np.eye(3)
    else:
        alpha = (rows[:, 0] - c['initial_time']) / (c['final_time'] - c['initial_time'])
        common = Rotation.from_rotvec(np.column_stack((np.zeros((len(alpha), 2)),
                                      -alpha * c['pelvis_closure_rad']))).as_matrix()
    beta = Rotation.from_euler('z', c['functional_yaw_rad'][i]).as_matrix()
    if 'temporal_heading_curves' in c:
        from .heading_transport import temporal_heading
        heading=temporal_heading(rows[:,0],node,c)
        beta=Rotation.from_rotvec(np.column_stack((np.zeros((len(heading),2)),heading))).as_matrix()@beta
    elif 'frozen_heading_correction_rad' in c:
        frozen=c['frozen_heading_correction_rad']
        if set(frozen)!=set(NODES[1:]) or not np.isfinite(list(frozen.values())).all():
            raise ValueError('frozen five-node heading requires four finite retained-limb corrections')
        # Estimator updates are calibration history, not physical rotation.
        # Applying them as steps to a continuous IMU stream manufactures
        # angular velocity and contaminates the recurrent pose prior.
        beta=Rotation.from_euler('z',frozen.get(node,0.)).as_matrix()@beta
    elif node in c.get('heading_factors', {}):
        from biospur_fusion.c2_coupled_progressive.pose_reset_avatar import _factor_interval_replay
        heading = _factor_interval_replay(rows[:,0], c['heading_factors'][node])
        beta = Rotation.from_rotvec(np.column_stack((np.zeros((len(heading),2)),heading))).as_matrix() @ beta
    if 'segment_axes_in_sensor' in c:
        return beta @ common @ matrices(rows).as_matrix() @ np.asarray(c['segment_axes_in_sensor'])[i]
    return beta @ common @ raw @ beta.T


def _gyro_axis_mask(rows, lo, hi):
    elapsed=rows[:,0]-rows[0,0]
    return (elapsed>=lo)&(elapsed<hi)&(np.linalg.norm(rows[:,8:11],axis=1)>.3)


def _gyro_axis(rows, lo, hi):
    omega=rows[_gyro_axis_mask(rows,lo,hi),8:11]
    if len(omega)<100:raise ValueError('insufficient functional axis excitation')
    values,vectors=np.linalg.eigh(omega.T@omega)
    return vectors[:,-1],dict(rows=len(omega),principal_fraction=float(values[-1]/values.sum()),
                             formal_interval_s=[lo,hi])


def _orthogonal_forearm_axes(hinge, long):
    """Reject numerical non-identifiability, not naturally oblique motion."""
    tolerance=64*np.finfo(float).eps
    axes=[]
    for name,axis in (('hinge',hinge),('longitudinal',long)):
        axis=np.asarray(axis,dtype=float)
        norm=float(np.linalg.norm(axis))
        if axis.shape!=(3,) or not np.isfinite(axis).all() or not np.isfinite(norm) or norm<=tolerance:
            raise ValueError('degenerate forearm '+name+' axis')
        axes.append(axis/norm)
    hinge,long=axes
    dot=float(np.clip(hinge@long,-1.,1.))
    transverse=hinge-long*dot
    sine=float(np.linalg.norm(transverse))
    if sine<=tolerance:
        raise ValueError('forearm hinge and longitudinal axes are numerically collinear')
    return transverse/sine,long,dict(raw_axis_dot=dot,orthogonalization_sine=sine,
        axis_pair_condition_number=(1+abs(dot))/sine,numerical_tolerance=tolerance,
        diagnostic_only=True,human_angle_acceptance_gate_added=False)


def fit_forearm_frame(rows, tpose_world_rotations, initial_rotation, *, left):
    """One arrived elbow action plus earlier T-pose; same batch numerical owner.

    The caller supplies prefix-only world rotations and initial reference.
    No opposite arm, pelvis fitting result, or future episode is accessed.
    Direction signs remain conditional on natural standing and protocol.
    """
    hinge,hinge_audit=_gyro_axis(rows,0.,15.)
    long,long_audit=_gyro_axis(rows,15.,30.)
    initial_up_projection=float(np.asarray(initial_rotation)[2]@long)
    if initial_up_projection<0:long=-long
    # A coordinate triad must be orthogonal; the two functional joint axes
    # need not be. Preserve the measured axis before constructing that triad.
    # Its obliquity can also contain parent motion or phase contamination,
    # so it is not automatically a personal carrying-angle measurement.
    measured_hinge=hinge.copy()
    hinge,long,conditioning=_orthogonal_forearm_axes(hinge,long)
    directions=-tpose_world_rotations@long
    weights=np.linalg.norm(directions[:,:2],axis=1)**2
    v=np.average(directions,axis=0,weights=weights)
    target=np.pi/2 if left else -np.pi/2
    yaw=float(target-np.arctan2(v[1],v[0]))
    b=Rotation.from_euler('z',yaw).as_matrix()
    sign_rows=rows[_gyro_axis_mask(rows,0.,15.)]
    axes_world=b@matrices(sign_rows).as_matrix()@hinge
    mean_lateral=float(np.mean(axes_world[:,1]))
    absolute_lateral=float(np.mean(abs(axes_world[:,1])))
    sign_flip=mean_lateral>0
    if sign_flip:
        hinge=-hinge
        measured_hinge=-measured_hinge
    sign_support=dict(rows=len(sign_rows),formal_interval_s=[0.,15.],
        selected_elapsed_span_s=(sign_rows[[0,-1],0]-rows[0,0]).tolist(),
        selection='SAME_TIMESTAMP_AND_ACTIVE_GYRO_MASK_AS_HINGE_FIT',
        mean_lateral_before_sign=mean_lateral,mean_absolute_lateral=absolute_lateral,
        signed_balance=mean_lateral/absolute_lateral if absolute_lateral>0 else 0.,
        sign_flipped=bool(sign_flip),diagnostic_only=True,acceptance_gate_added=False)
    y=-hinge;x=np.cross(y,long)
    mount=np.column_stack((x,y,long))
    evidence=dict(flexion=hinge_audit,pronation=long_audit,
        raw_axis_dot=conditioning['raw_axis_dot'],axis_conditioning=conditioning,
        hinge_sign_support=sign_support,
        longitudinal_sign_support=dict(initial_up_projection_before_sign=initial_up_projection,
            absolute_up_projection=abs(initial_up_projection),sign_flipped=initial_up_projection<0,
            source='INITIAL_NATURAL_STANDING_PROXIMAL_DIRECTION_APPROXIMATION',
            diagnostic_only=True,acceptance_gate_added=False),
        axis_sensor_long=long.tolist(),axis_sensor_hinge=hinge.tolist(),
        measured_axis_sensor_hinge=measured_hinge.tolist(),
        functional_axis_obliquity=dict(
            signed_departure_from_orthogonal_deg=float(np.rad2deg(np.arcsin(
                np.clip(measured_hinge@long,-1.,1.)))),
            frame_axis_is_orthogonalized=True,
            anatomical_carrying_angle_identified=False,
            applied_to_joint_model=False,
            interpretation='functional-axis mismatch; anatomy and coupled motion not separated'),
        initial_long_axis_forced_vertical=False)
    return mount,yaw,evidence


def calibrate_forearm_mounts(episodes,calibration):
    """Separate registered flexion and pronation to recover anatomical bone axes.

Input arrays have already been cropped to ACTION_START/STOP. Their origin is
the formal action start, not the old frontend archive's five-second preroll.
"""
    c=calibration
    mounts=[np.asarray(c['initial_sensor_rotations'])[i].T @
        Rotation.from_euler('z',-c['functional_yaw_rad'][i]).as_matrix() for i in range(5)]
    evidence={}
    for i,name in enumerate(('06_elbow_left','07_elbow_right'),1):
        n=NODES[i];rows=episodes[name][n]['imu']
        tpose=episodes['02_t_pose'][n]['imu']
        zero_yaw={**c,'functional_yaw_rad':[0.]*5}
        raw=relative(tpose,n,zero_yaw)@np.asarray(c['initial_sensor_rotations'])[i]
        mount,yaw,audit=fit_forearm_frame(rows,raw,c['initial_sensor_rotations'][i],left=i==1)
        mounts[i]=mount
        c['functional_yaw_rad'][i]=yaw
        c['hinge_axes'][i-1]=[0.,-1.,0.]
        evidence[n]=audit
    c['segment_axes_in_sensor']=[m.tolist() for m in mounts]
    c['forearm_mount_calibration']=evidence
    c['formal_window_origin']='ACTION_START; the five-second raw preroll was already removed by inputs.read'
    initial=np.zeros(9)
    root=Rotation.from_matrix(relative(episodes['00_initial_still'][NODES[0]]['imu'],NODES[0],c)).mean().as_matrix()
    for i in (1,2):
        distal=Rotation.from_matrix(relative(episodes['00_initial_still'][NODES[i]]['imu'],NODES[i],c)).mean().as_matrix()
        rr=root.T@distal
        initial[2+i]=np.arccos(np.clip(rr[2,2],-1,1))
        initial[6+i]=np.arctan2(-rr[2,1],rr[2,0])
    c['standing_elbow_bend_estimate_deg']=np.rad2deg(initial[3:5]).tolist()
    c['initial_pose_assumption']='upper arms approximately hanging in registered standing; forearm axes and elbow bend recovered from functional calibration, not forced straight'
    return c


def calibrate(episodes, surface, *, heading_closure=True):
    initial = episodes['00_initial_still']
    r0 = np.asarray([robust_still(initial[n]['imu']) for n in NODES])
    final = episodes['17_final_still'] if heading_closure else None
    rf = robust_still(final[NODES[0]]['imu']) if heading_closure else r0[0]
    closure = rf @ r0[0].T
    c = dict(initial_sensor_rotations=r0.tolist(),
        initial_time=float(np.median(initial[NODES[0]]['imu'][:, 0])),
        final_time=float(np.median(final[NODES[0]]['imu'][:, 0])) if heading_closure else None,
        pelvis_closure_rad=float(np.arctan2(closure[1, 0], closure[0, 0])) if heading_closure else 0.,
        functional_yaw_rad=[0.] * 5)
    evidence = {}
    for i, ep in enumerate(('02_t_pose', '02_t_pose', '10_knee_left_seated', '11_knee_right_seated'), 1):
        rows = episodes[ep][NODES[i]]['imu']
        t = rows[:, 0] - rows[0, 0]
        rows = rows[(t >= 0) & (t <= (15 if i > 2 else 30))]
        vec = -relative(rows, NODES[i], c)[:, :, 2]
        horizontal = np.linalg.norm(vec[:, :2], axis=1)
        keep = horizontal >= max(.2, np.quantile(horizontal, .6))
        v = np.mean(vec[keep], axis=0)
        if not np.isfinite(v).all() or np.linalg.norm(v[:2]) < .1:
            raise ValueError(f'functional direction unobservable: {NODES[i]}')
        target = (np.pi / 2 if i == 1 else -np.pi / 2) if i <= 2 else 0.
        c['functional_yaw_rad'][i] = float(target - np.arctan2(v[1], v[0]))
        evidence[NODES[i]] = dict(episode=ep, horizontal_norm=float(np.linalg.norm(v[:2])),
                                  selected_rows=int(keep.sum()), convention='T-pose left/right; seated knee extension forward')
    axes=[[0.,-1.,0.],[0.,-1.,0.],[0.,1.,0.],[0.,1.,0.]]
    hinge_audit={}
    lookup = {m['measurement_id']: m for m in surface['measurements']}
    def readings(key):
        return [o['value_mm'] / 1000 for o in lookup[key]['observations'] if 'value_mm' in o]
    fore_range = lookup['forearm_surface_length_unassigned_second_observer']['observations'][0]['range_mm']
    fore = readings('left_forearm_surface_length') + [float(np.mean(fore_range)) / 1000]
    lengths = dict(upper_arm=float(np.mean(readings('left_upper_arm_surface_length'))),
        forearm=float(np.mean(fore)), thigh=readings('left_thigh_surface_length')[0],
        shank=readings('left_shank_surface_length')[0],
        shoulder_width=float(np.mean(readings('biacromial_breadth'))))
    c.update(lengths=lengths, functional_evidence=evidence, hinge_axes=axes,
        hinge_audit=hinge_audit, surface_measurements=surface,
        geometry_status='TAPE_SURFACE_LENGTH_PROXIES_ONLY; NOT JOINT_CENTRE_MEASUREMENTS',
        torso_display_models_m=[.36, .425, .49], hip_display_half_width_models_m=[.10, .14, .18],
        geometry_used_in_orientation_fit=False,
        assumptions=['pelvis/shank rest-frame approximation; T-pose forearms lateral',
            'forearm long axes identified by pronation, not forced vertical in standing',
            'seated knee extension selects forward heading',
            'elbow flexion plus forearm axial twist; knees use a one-axis hinge approximation',
            'missing torso and proximal orientations selected by explicit generic priors',
            'offline common pelvis yaw closure extrapolated to Hxx; not causal online output',
            'absolute root translation, proximal axial twist and head orientation not measured'],
        heldout_used_for_fit=False, uwb_ranges_or_positions_used=False)
    from .functional_frames import calibrate_functional_frames
    c=calibrate_forearm_mounts(episodes,c)
    return calibrate_functional_frames(episodes,c)


def synchronized(ep, calibration, hz=20):
    lo = max(v['imu'][0, 0] for v in ep.values())
    hi = min(v['imu'][-1, 0] for v in ep.values())
    t = np.arange(lo, hi, 1 / hz)
    rots, valid = [], np.ones(len(t), bool)
    for n in NODES:
        imu = ep[n]['imu']
        rr = relative(imu, n, calibration)
        rots.append(Slerp(imu[:, 0] - lo, Rotation.from_matrix(rr))(t - lo).as_matrix())
        idx = np.clip(np.searchsorted(imu[:, 0], t), 1, len(imu) - 1)
        valid &= (imu[idx, 0] - imu[idx - 1, 0]) <= .025
    return t, np.stack(rots, axis=1), valid
