"""Guard against confusing masked uncertainty with accumulated information."""
import numpy as np
from biospur_fusion.root_r3.models import RootState
from biospur_fusion.c2_uwb_root_world.support_velocity import update_support_velocity


def test_position_consider_preserves_uncertainty_ordinary_gain_conditions_it():
    p=np.eye(9)
    p[0,3]=p[3,0]=.3
    state=RootState(0.,np.zeros(9),p)
    masked,_,noise=update_support_velocity(state,[[0.,0.,0.]],[1.],.005,consider_position=True)
    ordinary,_,_=update_support_velocity(state,[[0.,0.,0.]],[1.],.005)
    assert masked.covariance[0,0] == p[0,0]
    np.testing.assert_allclose(ordinary.covariance[0,0],p[0,0]-.3**2/(p[3,3]+noise[0,0]))
    for result in (masked,ordinary):
        np.testing.assert_array_equal(result.vector,state.vector)
        np.linalg.cholesky(result.covariance)


def test_ordinary_contact_emits_actual_cross_covariance_transition():
    p=np.eye(9);p[0,3]=p[3,0]=.3
    state=RootState(0.,np.zeros(9),p)
    observed=[]
    result,_,noise=update_support_velocity(state,[[.2,0.,0.]],[1.],.005,transition_observer=observed.append)
    h=np.eye(9)[3:6]
    gain=np.linalg.solve(h@p@h.T+noise,h@p).T
    np.testing.assert_allclose(observed[0],np.eye(9)-gain@h)
    assert result.position_m[0]>0
