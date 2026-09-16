"""Conditional joint mount/heading probe, isolated from production fitting.

Reuses C2 gyro-axis extraction. No missing-node rotations, pose network,
reference trajectory or true parameters enter the fit. Pelvis-referenced
motion directions are approximate protocol assumptions, not observations of
the chest. This narrow probe deliberately tests their sensitivity to coupling.
"""
import numpy as np
from biospur_fusion.c2_sparse_nodes.calibration import _gyro_axis, matrices
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_five_calibration.progressive.arm_model import (
    initial_parameters, solve_one, wear_branch)


def prepare(actions, limb, swap_phases=False):
    required = {'00_initial_still','02_t_pose','04_shoulder_left','05_shoulder_right',
                '06_elbow_left','07_elbow_right'}
    if set(actions)!=required or any(set(q)!=set(NODES) for q in actions.values()):
        raise ValueError('exact five nodes and upper-arm smoke action set required; no H')
    node = NODES[limb+1]
    elbow = actions['06_elbow_left' if limb==0 else '07_elbow_right']
    intervals = [(15,30),(0,15)] if swap_phases else [(0,15),(15,30)]
    axes = [_gyro_axis(elbow[node]['imu'],*span)[0] for span in intervals]
    factors = []
    specs = [('00_initial_still',0,30,'long',[0,0,-1],False),
             ('02_t_pose',0,30,'long',[0,1 if limb==0 else -1,0],False),
             ('04_shoulder_left' if limb==0 else '05_shoulder_right',0,30,'gyro',[1,0,0],True),
             ('06_elbow_left' if limb==0 else '07_elbow_right',*intervals[0],'gyro',[0,1,0],True),
             ('06_elbow_left' if limb==0 else '07_elbow_right',*intervals[1],'long',[1,0,0],False)]
    for name,lo,hi,kind,target,axial in specs:
        rows = actions[name][node]['imu']; root = actions[name][NODES[0]]['imu']
        if not np.array_equal(rows[:,0],root[:,0]):
            raise ValueError('smoke fixture must share original sample times')
        t = rows[:,0]-rows[0,0]
        # Exclude switch boundaries where the analytic fixture is not a
        # continuous transition; no fabricated transition samples are fitted.
        selected = np.flatnonzero((t>=lo+.1)&(t<hi-.1))[::40]
        axis = _gyro_axis(rows,lo,hi)[0] if kind=='gyro' else None
        factors.append((name,kind,matrices(rows[selected]).as_matrix(),
                        matrices(root[selected]).as_matrix()@np.asarray(target),axis,axial))
    return axes, factors


def fit(actions, limb, initial, swap_phases=False):
    axes, factors = prepare(actions,limb,swap_phases)
    records=[dict(id='flexion_axis',kind='sensor_axis',column=1,axis=axes[0]),
             dict(id='pronation_axis',kind='sensor_axis',column=2,axis=axes[1])]
    records += [dict(id=name+':'+kind,kind='direction',observed=observed,target=target,
                     axis=axis,axial=axial) for name,kind,observed,target,axis,axial in factors]
    result=solve_one(records,initial_parameters(limb,initial))
    # Preserve the original smoke tool API while sharing the numerical owner.
    for key in ('parameters','mount'):result[key]=np.asarray(result[key])
    return result
