import numpy as np
import pytest
import torch

from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.heading_transport import temporal_heading
from biospur_fusion.c2_five_calibration.session_heading import SessionHeadingParameters
from test_c2_whole_session import session


def test_late_C2_has_independent_support_and_raw_replay_agrees():
    data,contracts=session()
    base=dict(temporal_heading_curves={n:dict(time_s=[1.,5.],correction_rad=[.1,.2]) for n in NODES[1:3]},
              frozen_heading_correction_rad={n:0. for n in NODES[1:]})
    model=SessionHeadingParameters(base,data['time_s'],contracts)
    coeff=torch.zeros(model.size,dtype=torch.float64,requires_grad=True)
    with torch.no_grad():coeff[model.slices[0].stop-1]=.2
    delta=model.increments(coeff)
    # Old parameterization held its last value after t=5; late C2 now has a
    # separate latent correction without changing those original anchors.
    assert delta[data['time_s']<5].abs().max()==0
    assert delta[-1,0]==.2
    delta[-1,0].backward();assert coeff.grad[model.slices[0].stop-1]==1
    fresh=model.frontend(coeff.detach().numpy())
    for i,node in enumerate(NODES[1:]):
        np.testing.assert_allclose(temporal_heading(data['time_s'],node,fresh)-temporal_heading(data['time_s'],node,base),delta[:,i].detach(),atol=1e-12)
    np.testing.assert_array_equal(temporal_heading([100.],NODES[0],fresh),[0.])
    assert temporal_heading([100.],NODES[1],fresh)[0]==pytest.approx(.4)
    assert 'session_heading_increments' not in base
    with pytest.raises(ValueError,match='immutable'):
        SessionHeadingParameters(fresh,data['time_s'],contracts)


def test_smoothness_is_rate_based_and_not_an_extra_absolute_heading_anchor():
    data,contracts=session();base=dict(frozen_heading_correction_rad={n:0. for n in NODES[1:]})
    model=SessionHeadingParameters(base,data['time_s'],contracts)
    constant=torch.full((model.size,),.3,dtype=torch.float64)
    assert model.regularization(constant)==0
    rate=model.rate_scale_rad_s
    ramp=torch.tensor(np.tile(model.knots[0]*rate,4),dtype=torch.float64,requires_grad=True)
    torch.testing.assert_close(model.regularization(ramp),torch.tensor(1.,dtype=torch.float64))
    model.regularization(ramp).backward();assert torch.isfinite(ramp.grad).all()
    with pytest.raises(ValueError):SessionHeadingParameters(base,data['time_s'],dict(list(contracts.items())[:12]))


def test_session_parameters_participate_in_physical_optimizer():
    import time
    from test_c2_shared_fit import fixture, geometry
    from biospur_fusion.c2_five_calibration.solver import PoseObjective
    from biospur_fusion.c2_five_calibration.shared_fit import _optimize
    from biospur_fusion.c2_five_calibration.temporal_parameters import TemporalRegisteredHeadingPrior
    torch.set_num_threads(1)
    actions,factors,heading,*_=fixture()
    q=next(iter(actions.values()))
    _,contracts=session()
    # Compress the synthetic session and scale its rate prior by the same factor.
    contracts={n:{k:v*1.45/38 for k,v in row.items()} for n,row in contracts.items()}
    for rows in factors.values():
        for factor in rows:factor['measurement_time_s']=.7
    base=dict(heading_factors=factors,frozen_heading_correction_rad=heading)
    model=SessionHeadingParameters(base,q['time_s'],contracts,rate_scale_deg_s=100.)
    prior=TemporalRegisteredHeadingPrior(base,q['time_s'])
    objective=PoseObjective(**q,geometry=geometry())
    levers=torch.zeros(5,3,dtype=torch.float64)
    result=_optimize({'_continuous':objective},{'_continuous':objective.initial},
        levers,torch.zeros(30,4,dtype=torch.float64),levers,prior,
        iterations=2,deadline=time.monotonic()+30,shared=True,heading_model=model)
    assert np.isfinite(result['energy'])
    assert torch.isfinite(result['heading_coefficients']).all()
    assert result['heading_coefficients'].abs().max()>0


def test_full_session_raw_filter_transport():
    from test_c2_phase_registration import raw_fixture
    from biospur_fusion.c2_five_calibration.frontend import prepare
    from biospur_fusion.c2_five_calibration.temporal_transport_check import check_raw_temporal_transport
    episodes,base,*_=raw_fixture()
    episode=episodes['06_elbow_left']
    geometry=dict(bone_frame_correction=np.tile(np.eye(3),(5,1,1)).tolist())
    def pack(value):
        return dict(time_s=value['time_s'][::3],observed=value['orientation'][::3],
                    acceleration=value['acceleration_mps2'][::3])
    old=pack(prepare(episode,base,geometry))
    _,contracts=session()
    lo,hi=old['time_s'][[0,-1]]
    contracts={n:{k:lo+v*(hi-lo)/38 for k,v in row.items()} for n,row in contracts.items()}
    model=SessionHeadingParameters(base,old['time_s'],contracts)
    coefficients=.2*np.sin(np.arange(model.size))
    fresh=pack(prepare(episode,model.frontend(coefficients),geometry))
    audit=check_raw_temporal_transport(episode,base,geometry,coefficients,old,fresh,heading_model=model)
    assert audit['raw_time_transport_verified']
    assert audit['acceleration_max_error_mps2']<1e-9
