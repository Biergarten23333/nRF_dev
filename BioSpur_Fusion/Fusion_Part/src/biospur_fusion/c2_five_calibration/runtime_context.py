"""Keep real boundary samples and C2 state initialization on the runtime grid.

This carries a warm start, not a fabricated posterior covariance or a fixed
pose constraint. Runtime fitting keeps shared calibration parameters frozen.
"""
import numpy as np
import torch
from .operators import WIDTH,HZ


def runtime_pose_warm_start(context,geometry,indices,checkpoint):
    """Keep network targets unchanged; use C2 poses only to initialize solving."""
    from .anatomy import JointModel
    model=JointModel(geometry)
    warm=model.prior_target(torch.as_tensor(context['prior'],dtype=torch.float64),
                            torch.as_tensor(context['observed'],dtype=torch.float64)).numpy()
    warm[indices]=checkpoint
    return warm


def physical_runtime_context(prepared, prior, checkpoint_time, checkpoint_rotation,
                             first_runtime_time):
    time=np.asarray(prepared['time_s'])
    checkpoint_time=np.asarray(checkpoint_time)
    checkpoint_rotation=np.asarray(checkpoint_rotation)
    prior=np.asarray(prior)
    if (time.ndim!=1 or len(time)<3 or not np.isfinite(time).all()
            or not np.allclose(np.diff(time),1/60,atol=1e-6,rtol=0)
            or prior.shape!=(len(time),24,3,3)):
        raise ValueError('original continuous 60Hz prepared stream and prior required')
    if (checkpoint_time.ndim!=1 or len(checkpoint_time)<WIDTH
            or not np.isfinite(checkpoint_time).all()
            or not np.allclose(np.diff(checkpoint_time),1/HZ,atol=1e-6,rtol=0)
            or checkpoint_rotation.shape!=(len(checkpoint_time),24,3,3)
            or not np.isfinite(checkpoint_rotation).all()
            or not np.isfinite(first_runtime_time)
            or checkpoint_time[-1]>=first_runtime_time):
        raise ValueError('ordered C2 checkpoint strictly before runtime required')
    ids=np.arange(0,len(time),3)
    start=checkpoint_time[-WIDTH]
    ids=ids[time[ids]>=start-1e-6]
    t=time[ids]
    if len(t)<WIDTH or abs(t[0]-start)>1e-6:
        raise ValueError('checkpoint and runtime must share the original time grid')
    output=t>=first_runtime_time
    if not output.any():raise ValueError('no runtime samples')
    q=dict(time_s=t,prior=prior[ids],observed=np.asarray(prepared['orientation'])[ids],
           acceleration=np.asarray(prepared['acceleration_mps2'])[ids],
           valid=np.asarray(prepared['input_valid'])[ids])
    if q['valid'].dtype!=bool or q['observed'].shape!=(len(t),5,3,3):
        raise ValueError('five retained observations and boolean validity required')
    target=np.arange(WIDTH)
    np.testing.assert_allclose(t[target],checkpoint_time[-WIDTH:],atol=1e-6,rtol=0)
    return q,output,target,checkpoint_rotation[-WIDTH:].copy(),dict(
        context_frames=int((~output).sum()),checkpoint_warm_start_frames=WIDTH,
        bridge_frames=int(((t>checkpoint_time[-1]+1e-6)&(~output)).sum()),
        original_sample_times_preserved=True,calibration_parameters_updated=False,
        posterior_covariance_transferred=False,context_pose_is_fixed=False,
        state_role='C2 pose warm start plus actual boundary observations; not posterior handover')
