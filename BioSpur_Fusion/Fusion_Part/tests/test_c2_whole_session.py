import copy

import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_five_calibration.whole_session import whole_session_support
from biospur_fusion.c2_five_calibration.geometry import sensor_positions
from biospur_fusion.c2_five_calibration.solver import acceleration_residual
from test_c2_joint_kinematics import geometry


def session():
    contracts={name:dict(lo=i*2.,hi=(i+1)*2.) for i,name in enumerate(EPISODES)}
    t=np.arange(0.,38.,.05)
    return dict(time_s=t,valid=np.ones(len(t),bool)),contracts


def test_full_C2_cannot_be_replaced_by_subset_or_holdout():
    data,contracts=session()
    coverage=whole_session_support(data,contracts)
    assert [c['action'] for c in coverage]==list(EPISODES)
    assert all(c['valid_frames']>=39 for c in coverage)
    for bad in (dict(list(contracts.items())[:12]),{**contracts,'H01_boxing':dict(lo=38.,hi=40.)}):
        with pytest.raises(ValueError):whole_session_support(data,bad)
    truncated={k:v[:-50] for k,v in data.items()}
    with pytest.raises(ValueError,match='cover'):whole_session_support(truncated,contracts)
    invalid=copy.deepcopy(data);invalid['valid'][-40:]=False
    with pytest.raises(ValueError,match='valid C2'):whole_session_support(invalid,contracts)


def test_rotating_front_pelvis_lever_and_common_translation():
    # Independent rigid-body physics: constant angular speed around world Y.
    # A front-mounted pelvis sensor accelerates even with stationary root.
    torch.set_num_threads(1)
    g=geometry();t=np.arange(160)/20.;omega=.8
    r=torch.tensor(np.repeat(Rotation.from_euler('y',(omega*t)[:,None]).as_matrix()[:,None],24,axis=1))
    levers=torch.zeros(5,3,dtype=torch.float64);levers[0]=torch.tensor([.15,0.,0.])
    relative=sensor_positions(r,g,levers)
    acceleration=-omega**2*relative.numpy();acceleration[:,:,1]=0.
    common=np.column_stack((.3*np.sin(t),.5*np.cos(t),.2*t))
    acceleration+=common[:,None]
    residual=acceleration_residual(r,torch.tensor(acceleration),g,levers)
    assert residual.abs().max()<.001
    # Omitting the abdomen-to-root lever makes a measurable wrong prediction.
    missing=acceleration_residual(r,torch.tensor(acceleration),g,torch.zeros_like(levers))
    assert missing.square().mean().sqrt()>.03
    shifted=acceleration_residual(r,torch.tensor(acceleration+7.),g,levers)
    torch.testing.assert_close(residual,shifted,atol=1e-12,rtol=0)
