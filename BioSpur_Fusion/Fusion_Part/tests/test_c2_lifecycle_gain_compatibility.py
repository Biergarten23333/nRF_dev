"""The existing gain budget and current support owners share one covariance."""
import numpy as np
from biospur_fusion.c2_uwb_root_world.support_points import SupportPoints
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity
from biospur_fusion.c2_uwb_root_world.root_input_safety import guarded_raw_update
from biospur_fusion.c2_uwb_root_world.tight_range import linearize_raw_range_factors
from test_c2_correction_budget import fixture,args


def test_scaled_raw_and_stationary_velocity_joint_joseph_cross():
    state,row=fixture();points=SupportPoints()
    state=points.update(state,np.array([[0,0,-.8],[.2,0,-.8]]),
        [True,True],[False,False],[1,1],.005,[False,False])
    full=points.covariance(state);means=points.means.copy();position=state.position_m.copy()
    factors=linearize_raw_range_factors(state,row,**args())
    h=np.zeros((len(factors.anchors),len(full)));h[:,:9]=factors.state_jacobian
    r=np.diag(np.diag(factors.r_prior_m2)/factors.robust_weights)
    k=np.linalg.solve(h@full@h.T+r,h@full).T;k[:3]=0.;k[9:]=0.;k*=.1
    transition=np.eye(len(full))-k@h
    expected=transition@full@transition.T+k@r@k.T
    seen=[]
    def observe(a):
        seen.append(a.copy());points.root_transition(a)
    output,decision,_,_=guarded_raw_update(state,row,**args(),
        consider_position=True,gain_scale=.1,transition_observer=observe)
    assert decision.accepted and len(seen)==1
    np.testing.assert_allclose(points.covariance(output),expected,atol=1e-13)
    np.testing.assert_array_equal(output.position_m,position)
    np.testing.assert_array_equal(points.means,means)
    assert np.linalg.norm(output.vector[3:]-state.vector[3:])>0
    full=points.covariance(output)
    updated,innovation,noise=update_support_velocity(output,[[.1,.02,-.01]],[1],.005,
        consider_position=True,transition_observer=observe)
    h=np.zeros((3,len(full)));h[:,3:6]=np.eye(3)
    k=np.linalg.solve(h@full@h.T+noise,h@full).T;k[:3]=0.;k[9:]=0.
    a=np.eye(len(full))-k@h
    np.testing.assert_allclose(points.covariance(updated),a@full@a.T+k@noise@k.T,atol=1e-13)
    np.testing.assert_array_equal(updated.position_m,position)
    np.testing.assert_array_equal(points.means,means)
    np.linalg.cholesky(points.covariance(updated))
    assert len(seen)==2
