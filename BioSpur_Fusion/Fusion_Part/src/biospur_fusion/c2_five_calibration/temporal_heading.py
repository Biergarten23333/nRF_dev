"""Explicit ablation of C2 arm direction evidence discarded by static yaw.

Prescribed movement planes are conditional anatomical evidence, not a direct
measurement of missing upper-arm/chest sensors. This candidate estimates a
smooth relative-yaw curve from them and freezes its last value for H replay.
It must be checked across C2 and H; curve construction is not acceptance.
"""
import copy
import numpy as np
from scipy.interpolate import PchipInterpolator
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def select_heading_model(times, values, quality):
    """Compare held-factor prediction within C2, then fit ALL C2 factors.

    A low-variation wrist should not acquire a drift curve merely because
    interpolation can pass through every noisy factor. No H score or tuned
    angle threshold enters this model-class decision.
    """
    errors={'constant':[],'curve':[]}
    for i in range(len(times)):
        keep=np.arange(len(times))!=i
        constant=np.average(values[keep],weights=quality[keep])
        curve=float(PchipInterpolator(times[keep],values[keep])(
            np.clip(times[i],times[keep][0],times[keep][-1])))
        errors['constant'].append((constant-values[i])**2)
        errors['curve'].append((curve-values[i])**2)
    scores={k:float(np.average(v,weights=quality)) for k,v in errors.items()}
    selected=min(scores,key=scores.get)
    fitted=values.copy() if selected=='curve' else np.full_like(values,np.average(values,weights=quality))
    return fitted,dict(selected=selected,C2_leave_one_factor_out_mse_rad2=scores,
        final_fit_factor_count=len(times),H_used=False)


def fit_temporal_arm_heading(calibration, *, model_selection=False):
    result=copy.deepcopy(calibration)
    curves={}
    for node in NODES[1:3]:
        factors=result['heading_factors'][node]
        expected=('directed_side','axis_forward','axis_lateral','directed_forward')
        if tuple(f['source_role'] for f in factors)!=expected:
            raise ValueError('complete C2 T-pose/shoulder/elbow arm evidence required')
        if any(f['action'].startswith('H') or f['quality']<=0 for f in factors):
            raise ValueError('positive calibration-only arm evidence required')
        times=np.array([f['measurement_time_s'] for f in factors])
        values=np.array([f['measurement_delta_rad'] for f in factors])
        # Axial signs inherit the directed T-pose branch. Directed late elbow
        # evidence is retained modulo 2*pi, not silently flipped by pi.
        for i in range(1,len(values)):
            period=np.pi if factors[i]['source_role'].startswith('axis_') else 2*np.pi
            values[i]+=period*np.round((values[i-1]-values[i])/period)
        if np.any(np.diff(times)<=0):raise ValueError('unordered C2 arm observations')
        selection=dict(selected='curve',model_selection=False)
        if model_selection:
            values,selection=select_heading_model(times,values,np.array([f['quality'] for f in factors]))
        curves[node]=dict(time_s=times.tolist(),correction_rad=values.tolist(),
            evidence=copy.deepcopy(factors),extrapolation='hold endpoint',
            model_selection=selection,
            interpretation='conditional C2 movement-plane yaw hypothesis, not measured chest orientation')
    result['temporal_heading_curves']=curves
    result['heading_replay_mode']='C2_CONTINUOUS_ARM_YAW_CANDIDATE_FROZEN_ENDPOINT_FOR_H'
    result['calibration_accepted']=False
    result['temporal_heading_audit']=dict(reference_used=False,H_used_for_fit=False,
        retained_nodes=list(NODES),fitted_nodes=list(curves),interpolation='shape-preserving cubic',
        limitations='Motion-plane changes may include unmeasured chest motion; H drift after final knot is uncorrected.')
    return result
