"""One continuous multi-frame motion state; segmentation is reporting only."""
from __future__ import annotations

import time
import numpy as np

from .inertial_window import solve_window,WINDOW_CONFIG
from .inertial_model import joints as model_joints,orientations,STATE_SIZE


def solve_stream(times,retained,acceleration,valid,calibration,*,
                 initial_state=None,progress=None,wall_limit_s=1200.,config=None):
    cfg=WINDOW_CONFIG if config is None else config
    dt=float(np.median(np.diff(times)));n=len(times)
    if not np.allclose(np.diff(times),dt,rtol=1e-6,atol=1e-8):
        raise ValueError('motion solver requires the declared regular physical time grid')
    width=max(5,int(round(cfg['seconds']/dt))+1)
    states=np.zeros((n,STATE_SIZE))
    if initial_state is not None:states[:2]=initial_state
    successes=np.zeros(n,bool);windows=[];started=time.monotonic()
    for start in range(0,n-3,width-2):
        if time.monotonic()-started>wall_limit_s:
            raise TimeoutError('inertial replay stage budget exceeded')
        stop=min(start+width,n)
        if n-stop<4:stop=n
        guess=np.repeat(states[start:start+1],stop-start,axis=0)
        guess[:2]=states[start:start+2]
        result,audit=solve_window(retained[start:stop],acceleration[start:stop],calibration,guess,
            dt=dt,config=cfg,valid=valid[start:stop])
        states[start:stop]=result;successes[start:stop]=audit['success']
        windows.append(dict(start=start,stop=stop,initial_states=guess[:2].tolist(),**audit))
        if progress is not None:progress(windows[-1])
        if stop==n:break
    # An incomplete trailing block has no acceleration support of its own.
    # Normally the previous window already includes the final samples.
    torso,proximal=orientations(states,retained,np.asarray(calibration['hinge_axes']))
    joints=np.stack([model_joints(states,retained,calibration,h,w)
        for h,w in zip(calibration['torso_display_models_m'],calibration['hip_display_half_width_models_m'])],axis=1)
    bend=np.rad2deg(np.arccos(np.clip(np.sum(proximal[:,:,:,2]*retained[:,1:,:,2],axis=2),-1,1)))
    arrays=dict(time_s=times,states=states,joints_m=joints,retained_rotations=retained,
        torso_rotations=torso,proximal_rotations=proximal,bend_deg=bend,
        optimizer_success=successes,input_valid=valid,acceleration_world_mps2=acceleration)
    return arrays,dict(windows=windows,wall_s=time.monotonic()-started,frames=n,
        optimizer_nonconverged_frames=int(np.sum(~successes)),
        physical_model_geometry_index=1,geometry_other_models='FK sensitivity only; central geometry used in dynamic fit',
        action_label_inputs=False,continuous_state=True,offline_future_window=True)
