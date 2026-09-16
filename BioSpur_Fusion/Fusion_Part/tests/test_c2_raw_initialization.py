from dataclasses import replace
import numpy as np
import pytest
from biospur_fusion.c2_uwb_root_world.raw_initialization import initialize_raw_batch,row_identity
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig
from biospur_fusion.root_r3.models import RootState
from test_c2_continuous_full_state_feedback import ANCHORS,CLOCK,row_at


def test_joint_initializer_mean_covariance_and_once_identity():
    truth=np.array([1.9,1.3,1.]);observations=[]
    for i in range(10):
        t=.02+i*.01;row=replace(row_at(t,truth),node=f'tag{i}',sequence=i)
        observations.append((row,CLOCK,np.zeros(3),np.zeros(3),t))
    prior=RootState(0.,np.r_[ANCHORS.mean(axis=0),np.zeros(6)],np.diag([1.]*6+[.25]*3))
    out,report=initialize_raw_batch(0.,prior,truth+.2,observations,ANCHORS,RawRangeUpdateConfig())
    assert np.linalg.norm(out.position_m-truth)<.03
    np.testing.assert_array_equal(out.vector[3:],prior.vector[3:])
    np.testing.assert_allclose(out.covariance[3:,3:],prior.covariance[3:,3:])
    assert np.trace(out.covariance[:3,:3])<np.trace(prior.covariance[:3,:3])
    assert np.linalg.norm(out.covariance[:3,3:6])>0
    np.linalg.cholesky(out.covariance)
    assert len({tuple(x) for x in report['identities']})==10
    assert report['startup_ready_epoch_s']>=report['latest_epoch_s']
    assert row_identity(observations[0][0])==tuple(report['identities'][0])


def test_optimizer_seed_is_not_prior_information():
    truth=np.array([1.9,1.3,1.]);obs=[]
    for i in range(10):
        t=.02+i*.01;obs.append((replace(row_at(t,truth),node=f'tag{i}'),CLOCK,np.zeros(3),np.zeros(3),t))
    prior=RootState(0.,np.r_[ANCHORS.mean(axis=0),np.zeros(6)],np.eye(9))
    a,_=initialize_raw_batch(0.,prior,truth+.1,obs,ANCHORS,RawRangeUpdateConfig())
    b,_=initialize_raw_batch(0.,prior,truth-.1,obs,ANCHORS,RawRangeUpdateConfig())
    np.testing.assert_allclose(a.vector,b.vector,atol=1e-6)
    np.testing.assert_allclose(a.covariance,b.covariance,atol=1e-6)


def test_aggregate_initialization_uses_partial_nodes_without_restoring_links():
    truth=np.array([1.9,1.3,1.]);obs=[]
    for i in range(10):
        t=.02+i*.01
        mask=0 if i==9 else 1 << (i%8)
        obs.append((replace(row_at(t,truth),node=f'tag{i}',valid_mask=mask),CLOCK,np.zeros(3),np.zeros(3),t))
    prior=RootState(0.,np.r_[ANCHORS.mean(axis=0),np.zeros(6)],np.eye(9))
    out,report=initialize_raw_batch(0.,prior,truth+.1,obs,ANCHORS,RawRangeUpdateConfig(),aggregate_geometry=True)
    assert report['retained_link_count']==9
    assert report['contributing_nodes']==9
    assert report['retained_masks'][-1]==0
    assert np.linalg.norm(out.position_m-truth)<.1
    np.linalg.cholesky(out.covariance)
    assert 'tag9' not in [x[0] for x in report['identities']]
    singular=[(replace(r,valid_mask=1),c,o,v,t) for r,c,o,v,t in obs]
    with pytest.raises(ValueError,match='aggregate initialization geometry'):
        initialize_raw_batch(0.,prior,truth,singular,ANCHORS,RawRangeUpdateConfig(),aggregate_geometry=True)
