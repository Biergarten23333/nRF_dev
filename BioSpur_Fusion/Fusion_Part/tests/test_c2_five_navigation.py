"""Independent analytic motion, temporal-error injection and negative controls."""
import numpy as np
import pytest
import torch
from scipy.spatial.transform import Rotation as R

from biospur_fusion.c2_five_calibration.navigation import (
    forearm_yaw_transport, preintegrate_gyro, gyro_increment_residual, bias_yaw_basis,
)


def motion(t):
    yaw=.17*t+.3*np.sin(.7*t); flex=.5*np.sin(1.2*t); pron=.4*np.sin(1.7*t)
    yp=.17+.21*np.cos(.7*t); fp=.6*np.cos(1.2*t); pp=.68*np.cos(1.7*t)
    ry=R.from_rotvec(yaw[:, None]*[0.,1.,0.]).as_matrix()
    rx=R.from_rotvec(flex[:, None]*[1.,0.,0.]).as_matrix()
    rz=R.from_rotvec(pron[:, None]*[0.,0.,1.]).as_matrix()
    rate=(rz.transpose(0,2,1)@rx.transpose(0,2,1)@np.array([0.,1.,0.]))*yp[:,None]
    rate+=(rz.transpose(0,2,1)@np.array([1.,0.,0.]))*fp[:,None]
    rate+=pp[:,None]*[0.,0.,1.]
    return ry@rx@rz,rate


def recover(initializer, increments):
    # Initial yaw is a gauge choice, not observed absolute heading. No pose
    # network, proximal ground truth, VQF yaw likelihood or smoothing term.
    torch.set_num_threads(1)
    q=torch.tensor(initializer,dtype=torch.float64)
    delta=torch.zeros(len(q)-1,dtype=q.dtype,requires_grad=True)
    target=torch.tensor(increments,dtype=q.dtype)
    optimizer=torch.optim.LBFGS([delta],max_iter=300,tolerance_grad=1e-11,
        tolerance_change=1e-13,line_search_fn='strong_wolfe')
    def rotate():
        yaw=torch.cat((delta.new_zeros(1),delta))
        full=q[:,None].repeat(1,5,1,1)
        states=torch.stack((yaw,yaw*0),-1)
        return forearm_yaw_transport(full,torch.zeros(len(q),5,3,dtype=q.dtype),states)[0][:,1]
    def closure():
        optimizer.zero_grad()
        loss=gyro_increment_residual(rotate(),target).square().sum()/1e-6
        loss.backward()
        return loss
    optimizer.step(closure)
    return rotate().detach().numpy(),np.r_[0.,delta.detach().numpy()]


@pytest.mark.parametrize('injected', [False,True])
def test_temporal_error_recovery_does_not_flatten_genuine_yaw_or_pronation(injected):
    raw_t=np.arange(2001)/200;grid=raw_t[::10]
    truth,body_gyro=motion(raw_t)
    mounting=R.from_rotvec([.4,-.3,.6]).as_matrix()
    sensor_gyro=body_gyro@mounting.T
    increments,valid=preintegrate_gyro(raw_t,sensor_gyro,grid,mounting,np.zeros(3))
    assert valid.all()
    error=.035*grid+.07*np.sin(.6*grid) if injected else grid*0
    corrupted=R.from_rotvec(error[:,None]*[0.,1.,0.]).as_matrix()@truth[::10]
    result,correction=recover(corrupted,increments)
    # Analytic continuous rates vs finite-step integration set this numerical
    # tolerance; this is not a real-sensor accuracy acceptance threshold.
    assert np.max(R.from_matrix(result@truth[::10].transpose(0,2,1)).magnitude())<1e-4
    np.testing.assert_allclose(correction,-error,atol=1e-4)
    np.testing.assert_allclose(result.transpose(0,2,1)@[0.,1.,0.],
                               corrupted.transpose(0,2,1)@[0.,1.,0.],atol=1e-12)


def test_common_constant_yaw_is_not_a_gyro_observation():
    t=np.arange(101)/20;q,_=motion(t)
    increments=q[:-1].transpose(0,2,1)@q[1:]
    shifted=R.from_rotvec([0.,.8,0.]).as_matrix()@q
    residual=gyro_increment_residual(torch.tensor(shifted),torch.tensor(increments))
    np.testing.assert_allclose(residual,0.,atol=1e-15)


def test_yaw_motion_and_bias_have_identical_raw_evidence():
    t=np.arange(201)/200;grid=t[::10]
    measured=np.tile([0.,.03,0.],(len(t),1))
    moving,valid=preintegrate_gyro(t,measured,grid,np.eye(3),np.zeros(3))
    static,_=preintegrate_gyro(t,measured,grid,np.eye(3),np.array([0.,.03,0.]))
    r=R.from_rotvec(grid[:,None]*[0.,.03,0.]).as_matrix()
    np.testing.assert_allclose(moving,r[:-1].transpose(0,2,1)@r[1:],atol=1e-15)
    np.testing.assert_allclose(static,np.tile(np.eye(3),(len(grid)-1,1,1)),atol=1e-15)
    np.testing.assert_allclose(r.transpose(0,2,1)@[0.,1.,0.],np.tile([0.,1.,0.],(len(grid),1)),atol=1e-15)
    assert valid.all()  # Both explanations fit; no bias truth is selected.


def test_gaps_are_invalid_not_integrated_at_nominal_cadence():
    t=np.arange(101)/200;keep=(t<=.2)|(t>=.3);grid=np.arange(11)/20
    increments,valid=preintegrate_gyro(t[keep],np.tile([0.,1.,0.],(keep.sum(),1)),
                                     grid,np.eye(3),np.zeros(3))
    assert valid.tolist()==[True,True,True,True,False,False,True,True,True,True]
    np.testing.assert_allclose(increments[valid],np.tile(R.from_rotvec([0.,.05,0.]).as_matrix(),(8,1,1)),atol=1e-15)
    with pytest.raises(ValueError,match='without extrapolation'):
        preintegrate_gyro(t[keep],np.zeros((keep.sum(),3)),np.array([-.1,.5]),np.eye(3),np.zeros(3))


def test_transport_changes_only_forearms_and_preserves_specific_force_coordinates():
    q=torch.tensor(R.from_rotvec(np.arange(45).reshape(15,3)*.01).as_matrix().reshape(3,5,3,3))
    a=torch.arange(45,dtype=q.dtype).reshape(3,5,3)*.1
    yaw=torch.tensor([[.1,-.2],[.3,-.4],[.5,-.6]],dtype=q.dtype)
    saved_q,saved_a=q.clone(),a.clone()
    r,b=forearm_yaw_transport(q,a,yaw)
    torch.testing.assert_close(r[:,[0,3,4]],q[:,[0,3,4]],rtol=0,atol=0)
    torch.testing.assert_close(b[:,[0,3,4]],a[:,[0,3,4]],rtol=0,atol=0)
    torch.testing.assert_close((r.transpose(-1,-2)@b[...,None]),(q.transpose(-1,-2)@a[...,None]),rtol=0,atol=2e-15)
    torch.testing.assert_close(q,saved_q,rtol=0,atol=0)
    torch.testing.assert_close(a,saved_a,rtol=0,atol=0)


def test_irregular_raw_and_output_times_use_actual_duration():
    rng=np.random.default_rng(843)
    raw=np.r_[0.,np.cumsum(rng.uniform(.004,.006,250))]
    grid=np.array([.013,.071,.123,.217,.358,.702,1.01])
    axis=np.array([.3,-.2,.5]);mounting=R.from_rotvec([.6,-.2,.1]).as_matrix()
    # Linear rate along a fixed axis has an exact analytic integral.
    rate=(1+raw[:,None])*axis
    bias=np.array([.01,-.03,.02])
    measured=rate@mounting.T+bias
    increments,valid=preintegrate_gyro(raw,measured,grid,mounting,bias)
    phase=grid+.5*grid**2
    expected=R.from_rotvec(np.diff(phase)[:,None]*axis).as_matrix()
    np.testing.assert_allclose(increments,expected,atol=5e-15)
    assert valid.all()


def test_bias_basis_matches_independent_open_loop_gyro_derivative():
    # Independent analytic motion and midpoint integration, with nontrivial
    # mounting and an interior anchor. Do not use preintegrate_gyro here.
    t=np.arange(1201)/400;anchor=400
    mounting=R.from_rotvec([.4,-.6,.2]).as_matrix()
    truth,_=motion(t);sensor=truth@mounting.T
    up=sensor.transpose(0,2,1)@np.array([0.,1.,0.])
    basis=bias_yaw_basis(t,np.repeat(up[:,None],2,axis=1),anchor)
    _,rates=motion((t[:-1]+t[1:])/2)
    rates=rates@mounting.T
    def integrate(bias):
        steps=R.from_rotvec((rates-bias)*np.diff(t)[:,None]).as_matrix()
        result=np.empty_like(sensor);result[anchor]=sensor[anchor]
        for i in range(anchor,len(steps)):result[i+1]=result[i]@steps[i]
        for i in range(anchor-1,-1,-1):result[i]=result[i+1]@steps[i].T
        return result
    original=integrate(np.zeros(3))
    direction=np.array([.2,-.3,.4]);direction/=np.linalg.norm(direction)
    expected=basis[:,0]@direction
    errors=[]
    for magnitude in (.01,.005,.0025):
        shifted=integrate(magnitude*direction)
        actual=R.from_matrix(shifted@original.transpose(0,2,1)).as_rotvec()[:,1]/magnitude
        errors.append(np.max(abs(actual-expected)))
    assert errors[2]<errors[1]*.6<errors[0]*.36
    assert errors[2]<.003
    np.testing.assert_array_equal(basis[anchor],0.)
    # A world heading rotation cannot create additional bias information.
    turned=R.from_rotvec([0.,.6,0.]).as_matrix()@sensor
    turned_up=turned.transpose(0,2,1)@np.array([0.,1.,0.])
    np.testing.assert_allclose(bias_yaw_basis(t,np.repeat(turned_up[:,None],2,axis=1),anchor),basis,atol=1e-15)


def test_bias_basis_rejects_unknown_gaps_and_acceleration_magnitude():
    t=np.arange(21)/200;up=np.tile([0.,1.,0.],(len(t),2,1))
    np.testing.assert_allclose(bias_yaw_basis(t,up,10)[:,:,1],(.05-t)[:,None]+np.zeros((len(t),2)),atol=1e-15)
    with pytest.raises(ValueError,match='across gap'):
        bias_yaw_basis(t[[0,1,2,5,6]],up[:5],1)
    with pytest.raises(ValueError,match='unit up'):
        bias_yaw_basis(t,up*9.80665,10)
