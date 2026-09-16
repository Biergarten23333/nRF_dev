import numpy as np
import pytest
from biospur_fusion.c2_five_calibration.marginalization import marginalize_linear_system


def test_factor_matches_joint_least_squares_for_arbitrary_boundary_states():
    rng=np.random.default_rng(5);J=rng.normal(size=(20,8));r=rng.normal(size=20)
    keep=np.array([7,2,4]);other=np.setdiff1d(np.arange(8),keep)
    factor=marginalize_linear_system(J,r,keep)
    for _ in range(10):
        x=rng.normal(size=3);b=J[:,keep]@x+r
        old=np.linalg.lstsq(J[:,other],-b,rcond=None)[0]
        np.testing.assert_allclose(factor.energy(x),np.sum((J[:,other]@old+b)**2),atol=1e-11)


def test_coupled_unobservable_state_is_not_given_conditional_precision():
    # Measuring x-y does not measure x when y remains free.
    J=np.array([[1.,-1.],[2.,-2.]])
    factor=marginalize_linear_system(J,np.array([.3,.6]),[0])
    assert factor.unresolved_dimensions==1
    for x in (-100,0,100):assert factor.energy([x])<1e-20
    assert float(J[:,0]@J[:,0])==5  # Incorrect fixed-y conditional precision.


def test_rank_deficient_history_and_constant_energy_are_preserved():
    J=np.array([[1.,1.,1.],[0.,0.,2.],[0.,0.,0.]])
    factor=marginalize_linear_system(J,[1,2,3],[2])
    assert factor.eliminated_rank==1
    assert factor.energy([-.5])==pytest.approx(10.)
    with pytest.raises(ValueError):marginalize_linear_system(J,[1,2,3],[2,2])


def test_separator_observations_are_not_counted_twice():
    from biospur_fusion.c2_five_calibration.marginalization import separate_history_factors
    J=np.array([[1.,-1.],[2.,0.],[0.,3.]])
    r=np.array([.1,.2,.3]);mask=np.array([True,True,False])
    factor,live,offset=separate_history_factors(J,r,[1],mask)
    for x in (-2.,.1,3.):
        b=J[:,1]*x+r;h=np.linalg.lstsq(J[:,:1],-b,rcond=None)[0]
        expected=np.sum((J[:,:1]@h+b)**2)
        assert factor.energy([x])+np.sum((live@np.array([x])+offset)**2)==pytest.approx(expected)
    with pytest.raises(ValueError,match='depends'):
        separate_history_factors(J,r,[1],np.array([True,False,False]))
