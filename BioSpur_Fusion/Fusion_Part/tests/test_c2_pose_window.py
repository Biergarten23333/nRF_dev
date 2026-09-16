import numpy as np
import pytest
import torch
from test_c2_shared_fit import fixture,geometry
from biospur_fusion.c2_five_calibration.solver import PoseObjective
from biospur_fusion.c2_five_calibration.soft_observation import SoftObservationObjective
from biospur_fusion.c2_five_calibration.pose_window import frozen_pose_window


def objective():
    actions,*_=fixture();q=next(iter(actions.values()))
    q={k:np.concatenate([v]*3) for k,v in q.items()};q['time_s']=np.arange(90)/20
    return SoftObservationObjective(PoseObjective(**q,geometry=geometry()))


def test_window_preserves_global_branch_and_cannot_refresh_it():
    obj=objective()
    # Equivalent unwrapped branch may be selected before the window begins.
    obj.base.initial[:,7:9]+=2*torch.pi;obj.initial[:,:9]=obj.base.initial
    part=frozen_pose_window(obj,40,90)
    torch.testing.assert_close(part.initial,obj.initial[40:])
    torch.testing.assert_close(part.base.target,obj.base.target[40:])
    torch.testing.assert_close(part.base.tracking.positions,obj.base.tracking.positions[40:])
    assert part.base.model is obj.base.model
    with pytest.raises(ValueError,match='whole-tape'):
        part.evaluate(part.initial,torch.zeros(5,3),refresh_projection=True)
    part.initial[:,7]=0
    assert torch.all(obj.initial[:,7]>6.)
    part.base.initial[:,7]=0
    assert torch.all(obj.base.initial[:,7]>6.)


def test_window_rejects_undefined_callback_scope_and_invalid_bounds():
    obj=objective()
    for lo,hi in [(-1,40),(0,91),(40,41),(True,50)]:
        with pytest.raises(ValueError):frozen_pose_window(obj,lo,hi)
    obj.protocol=lambda p,r:p.sum()*0
    with pytest.raises(ValueError,match='callback'):
        frozen_pose_window(obj,0,40)


def test_explicit_reprojection_window_handles_sparse_valid_support_without_refitting_data():
    from biospur_fusion.c2_five_calibration.pose_window import reprojected_pose_window
    obj=objective();part=reprojected_pose_window(obj,30,60)
    _,terms=part.evaluate(part.initial,torch.zeros(5,3,dtype=torch.float64),refresh_projection=True)
    assert torch.isfinite(terms['loss'])
    torch.testing.assert_close(part.observed,obj.observed[30:60])
    assert not getattr(obj.base,'_frozen_window',False)
