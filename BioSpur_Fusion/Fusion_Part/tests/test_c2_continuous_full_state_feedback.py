"""IMU-primary root feedback invariants, independent of display/publication."""
import numpy as np

from biospur_fusion.c2_uwb_root_world.tight_range import (
    RawRangeUpdateConfig, linearize_raw_range_factors, update_raw_ranges,
)
from biospur_fusion.c2_uwb_root_world.u0 import ClockModel, UwbRow
from biospur_fusion.root_r3.estimator import RootFilterConfig, propagate_inertial
from biospur_fusion.root_r3.models import RootState


ANCHORS = np.array([[0,0,0],[4,0,0],[4,3,0],[0,3,0],
                    [0,0,2],[4,0,2],[4,3,2],[0,3,2]], float)
CLOCK = ClockModel(0,1000.,0.,0.)


def row_at(time_s, position, outlier=False):
    ranges = np.linalg.norm(position-ANCHORS,axis=1)
    if outlier:
        ranges[3] += 3.0
    return UwbRow('BSFC2CC',0,int(round(time_s*1000)),1,
        int(round(time_s*1e6))-1000,int(round(time_s*1e6))+9000,
        tuple(range(8)),tuple(np.rint(ranges*1000).astype(int)),
        (2000,)*8,(100,)*8,255)


def test_full_feedback_matches_joseph_at_final_robust_linearization():
    x = np.r_[[1.9,1.3,.95], [.05,-.03,.02], np.zeros(3)]
    seed = RootState(0.,x,np.diag([.5]*3+[.2]*3+[.1]*3))
    prior,_ = propagate_inertial(seed,.8,np.array([0.,0.,9.80665]),np.eye(3),RootFilterConfig())
    config = RawRangeUpdateConfig()
    row = row_at(.8,np.array([2.,1.3,1.]))
    posterior,decision = update_raw_ranges(prior,row,anchors_m=ANCHORS,clock=CLOCK,config=config)
    assert decision.accepted
    factors = linearize_raw_range_factors(posterior,row,anchors_m=ANCHORS,clock=CLOCK,config=config)
    h = factors.state_jacobian
    r = np.diag(np.square(decision.sigma_m)/decision.robust_weights)
    k = np.linalg.solve(h@prior.covariance@h.T+r,h@prior.covariance).T
    ikh = np.eye(9)-k@h
    joseph = ikh@prior.covariance@ikh.T+k@r@k.T
    np.testing.assert_allclose(posterior.covariance,joseph,rtol=2e-9,atol=2e-11)
    # Velocity and bias are observable through temporal cross covariance.
    assert np.linalg.norm(posterior.vector[3:6]-prior.vector[3:6]) > 1e-4
    assert np.linalg.norm(posterior.vector[6:9]-prior.vector[6:9]) > 1e-4
    assert np.linalg.eigvalsh(posterior.covariance).min() > 0


def test_no_bias_update_without_propagated_cross_covariance():
    prior = RootState(1.,np.r_[[1.8,1.3,1.],np.zeros(6)],np.eye(9))
    posterior,decision = update_raw_ranges(prior,row_at(1.,np.array([2.,1.3,1.])),
        anchors_m=ANCHORS,clock=CLOCK)
    assert decision.accepted
    np.testing.assert_array_equal(posterior.vector[6:],prior.vector[6:])


def test_single_bad_link_is_robustly_weighted_without_position_clipping():
    truth = np.array([2.,1.3,1.])
    prior = RootState(1.,np.r_[truth+[-.4,.3,-.2],np.zeros(6)],np.eye(9))
    posterior,decision = update_raw_ranges(prior,row_at(1.,truth,True),anchors_m=ANCHORS,
        clock=CLOCK,config=RawRangeUpdateConfig(positive_nlos_cauchy_scale_m=.12))
    assert decision.accepted and len(decision.anchors)==8
    assert decision.robust_weights[3] < .01
    assert np.linalg.norm(posterior.position_m-truth)<.05
    assert np.linalg.norm(posterior.position_m-prior.position_m)>.02


def test_constant_sensor_bias_is_learned_by_raw_range_feedback():
    def truth(t):
        p = np.array([2.+.2*np.sin(.4*t),1.4+.15*np.cos(.3*t),1.+.1*np.sin(.2*t)])
        v = np.array([.08*np.cos(.4*t),-.045*np.sin(.3*t),.02*np.cos(.2*t)])
        acc = np.array([-.032*np.sin(.4*t),-.0135*np.cos(.3*t),-.004*np.sin(.2*t)])
        return p,v,acc
    bias = np.array([.06,-.04,.08])
    p,v,_ = truth(0.)
    initial = RootState(0.,np.r_[p,v,np.zeros(3)],np.diag([.1]*6+[.25]*3))
    fused = initial
    imu_only = initial
    config = RootFilterConfig()
    bias_deltas = []
    for tick in range(1,6001):
        t = tick*.005
        force = truth(t-.005)[2]+bias+[0.,0.,9.80665]
        fused,_ = propagate_inertial(fused,t,force,np.eye(3),config)
        imu_only,_ = propagate_inertial(imu_only,t,force,np.eye(3),config)
        if tick%24==0:
            before = fused.vector.copy()
            fused,decision = update_raw_ranges(fused,row_at(t,truth(t)[0]),anchors_m=ANCHORS,clock=CLOCK)
            assert decision.accepted
            bias_deltas.append(fused.vector[6:]-before[6:])
    assert np.linalg.norm(fused.accelerometer_bias_mps2-bias)<.01
    assert np.linalg.norm(fused.position_m-truth(30.)[0])<.02
    assert np.linalg.norm(fused.velocity_mps-truth(30.)[1])<.02
    assert np.linalg.norm(imu_only.position_m-truth(30.)[0])>40.
    # Correct sign: estimated bias is subtracted before rotating the force.
    assert np.dot(fused.accelerometer_bias_mps2,bias)>0
    assert np.count_nonzero(np.linalg.norm(bias_deltas,axis=1)>1e-8)>200


def test_no_observations_preserves_identical_inertial_trajectories():
    a = RootState(0.,np.zeros(9),np.eye(9))
    b = RootState(0.,np.zeros(9),np.eye(9))
    for tick in range(1,201):
        force = np.array([.1*np.sin(tick*.005),-.02,9.82665])
        a,_ = propagate_inertial(a,tick*.005,force,np.eye(3),RootFilterConfig())
        # Scheduling a range-epoch boundary without an observation must not
        # alter the IMU mean. This is distinct from executing the same call twice.
        b,_ = propagate_inertial(b,tick*.005-.002,force,np.eye(3),RootFilterConfig())
        b,_ = propagate_inertial(b,tick*.005,force,np.eye(3),RootFilterConfig())
    np.testing.assert_allclose(a.vector,b.vector,atol=1e-13,rtol=1e-13)
