from dataclasses import replace
import numpy as np
import pytest
from scipy.stats import chi2
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.root_r3.estimator import RootFilterConfig
from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update,inherited_raw_nis_limit
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig,linearize_raw_range_factors,update_raw_ranges
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def fixture(count):
    state=RootState(1.,np.r_[[1.95,1.3,1.],np.zeros(6)],np.eye(9)*.01)
    row=replace(row_at(1.,state.position_m+np.array([.03,0,0])),valid_mask=(1<<count)-1)
    cfg=RawRangeUpdateConfig(nominal_sigma_m=.25,positive_nlos_cauchy_scale_m=.12,
                             symmetric_corrected_discrepancy=True,partial_tracking=True)
    return state,row,dict(anchors_m=ANCHORS,clock=CLOCK,config=cfg,reference_epoch_s=1.,
        tag_offset_world_m=np.zeros(3),tag_offset_velocity_world_mps=np.zeros(3))


@pytest.mark.parametrize('count',[1,2,3])
def test_directional_information_and_count_specific_nis(count):
    state,row,args=fixture(count)
    factors=linearize_raw_range_factors(state,row,**args)
    maps=[]
    new,decision,nis,limit=guarded_raw_update(state,row,**args,transition_observer=maps.append)
    assert decision.accepted and decision.rank<=count and len(maps)==1
    assert limit==chi2.ppf(chi2.cdf(RootFilterConfig().nis_limit_3d,3),count)
    h=factors.state_jacobian;r=np.diag(np.diag(factors.r_prior_m2)/factors.robust_weights)
    information=h.T@np.linalg.solve(r,h)
    assert np.linalg.matrix_rank(information,tol=1e-8)<=count
    np.testing.assert_allclose(np.linalg.inv(new.covariance)-np.linalg.inv(state.covariance),information,atol=1e-10)
    assert np.linalg.eigvalsh(new.covariance).min()>0


@pytest.mark.parametrize('sign',[-1,1])
def test_bad_single_range_still_rejected(sign):
    state,row,args=fixture(1)
    ranges=list(row.ranges_mm);ranges[0]+=sign*1500
    row=replace(row,ranges_mm=tuple(ranges))
    out,decision,nis,limit=guarded_raw_update(state,row,**args)
    assert out is state and not decision.accepted and nis>limit


def test_default_strict_and_no_link_dropout():
    state,row,args=fixture(1)
    strict={**args,'config':replace(args['config'],partial_tracking=False)}
    unchanged,decision=update_raw_ranges(state,row,**strict)
    assert unchanged is state and decision.reason=='FEWER_THAN_FOUR_LINKS'
    with pytest.raises(ValueError,match='fewer than four'):
        linearize_raw_range_factors(state,row,**strict)
    empty=replace(row,valid_mask=0)
    unchanged,decision,_,_=guarded_raw_update(state,empty,**args)
    assert unchanged is state and decision.reason=='NO_VALID_LINKS'
    recovered,decision,_,_=guarded_raw_update(state,row,**args)
    assert decision.accepted and np.linalg.norm(recovered.vector-state.vector)>0
    from biospur_fusion.c2_uwb_root_world.raw_initialization import initialize_raw_batch
    with pytest.raises(ValueError,match='cannot initialize'):
        initialize_raw_batch(1.,state,state.position_m,[],ANCHORS,args['config'])


def test_correlated_velocity_feedback_and_consider_joseph():
    state,row,args=fixture(1);p=state.covariance.copy();p[0,3]=p[3,0]=.003
    state=RootState(1.,state.vector,p)
    new,decision,_,_=guarded_raw_update(state,row,**args,consider_position=True)
    assert decision.accepted and new.vector[3]!=state.vector[3]
    np.testing.assert_array_equal(new.position_m,state.position_m)
    assert np.linalg.eigvalsh(new.covariance).min()>0
