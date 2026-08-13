import dataclasses

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from v47_c2cc_frame_binding import (
    FROZEN_CONFIG, fit_mount, heldout_direction_errors, proper_wahba,
    regularized_trajectory, rotation_angle_deg, validate_time_offset,
)


def action(direction, rotation, *, seed=1, noise=0.0):
    rng=np.random.default_rng(seed);t=np.arange(0.,12.,.12)
    # Begins by travelling in the requested positive direction and repeats.
    phase=2*np.pi*t/3.;position=(0.55*(1-np.cos(phase)))[:,None]*np.asarray(direction)[None,:]
    noisy=position+rng.normal(0,noise,position.shape)
    trajectory=regularized_trajectory(t,noisy)
    ti=np.arange(0.,12.,.005)
    target=np.column_stack([np.interp(ti,t,trajectory["acceleration_mps2"][:,i]) for i in range(3)])
    up=np.array([0.,0.,1.]);gravity_sensor=rotation.T@(up*9.80665)
    accel=(rotation.T@target.T).T+gravity_sensor
    gyro=np.zeros_like(accel)
    return {"trajectory":trajectory,"imu_t_s":ti,"accel_mps2":accel,"gyro_dps":gyro},gravity_sensor


def mount(rotation, noise=0.0):
    directions=([0,0,1],[1,0,0],[0,1,0]);blocks={};gravity=None
    for key,d,seed in zip(("vertical","horizontal_1","horizontal_2"),directions,(1,2,3)):
        blocks[key],gravity=action(d,rotation,seed=seed,noise=noise)
    stationary=np.repeat(gravity[None,:],400,axis=0)
    return fit_mount(stationary_accel_mps2=stationary,blocks=blocks)


def test_arbitrary_signed_mounts_and_independent_remounting():
    ra=Rotation.from_euler("xyz",[37,-51,123],degrees=True).as_matrix()
    rb=Rotation.from_euler("xyz",[-92,18,-41],degrees=True).as_matrix()
    a=mount(ra);b=mount(rb)
    assert rotation_angle_deg(a["rotation"],ra)<3e-6
    assert rotation_angle_deg(b["rotation"],rb)<3e-6
    assert rotation_angle_deg(a["rotation"],b["rotation"])>30
    assert a["policy"]["mount_reuse"] is b["policy"]["mount_reuse"] is False


def test_proper_fit_rejects_reflection_correspondence():
    s=np.vstack([np.eye(3),-np.eye(3)])
    with pytest.raises(ValueError,match="invalid or unobservable"):
        proper_wahba(s,(np.diag([1,1,-1])@s.T).T)


def test_collinear_and_insufficient_excitation_rejected():
    r=np.eye(3);blocks={};gravity=None
    for key,d in (("vertical",[0,0,1]),("horizontal_1",[1,0,0]),("horizontal_2",[1,.01,0])):
        blocks[key],gravity=action(d,r)
    with pytest.raises(ValueError,match="insufficient trajectory excitation"):
        fit_mount(stationary_accel_mps2=np.repeat(gravity[None,:],300,axis=0),blocks=blocks)


def test_heldout_exclusion_is_structural():
    blocks={}
    for key,d in (("vertical",[0,0,1]),("horizontal_1",[1,0,0]),("horizontal_2",[0,1,0])):
        blocks[key],gravity=action(d,np.eye(3))
    with pytest.raises(ValueError,match="held-out"):
        fit_mount(stationary_accel_mps2=np.repeat(gravity[None,:],300,axis=0),blocks=blocks,
                  dataset_role="HELDOUT")
    blocks["heldout"] = blocks["vertical"]
    with pytest.raises(ValueError,match="exactly three"):
        fit_mount(stationary_accel_mps2=np.repeat(gravity[None,:],300,axis=0),blocks=blocks)


def test_time_offset_is_bounded_and_disabled_on_common_clock():
    assert validate_time_offset(0.)==0.
    with pytest.raises(ValueError,match="disabled"): validate_time_offset(.005)
    with pytest.raises(ValueError,match="outside"): validate_time_offset(.081)


def test_t4_noise_robustness_and_determinism():
    r=Rotation.from_euler("zyx",[71,-23,49],degrees=True).as_matrix()
    a=mount(r,noise=.015);b=mount(r,noise=.015)
    assert rotation_angle_deg(a["rotation"],r)<8
    assert np.array_equal(a["rotation"],b["rotation"])


def test_identity_policy_is_frozen():
    result=mount(np.eye(3))
    assert result["policy"]=={"accelerometer_matrix":"IDENTITY","accelerometer_bias_mps2":[0,0,0],
                              "heldout_used":False,"mount_reuse":False}
    assert FROZEN_CONFIG.identity_accelerometer_matrix


def test_heldout_direction_metric():
    rng=np.random.default_rng(47);s=rng.normal(size=(200,3));r=Rotation.random(random_state=rng).as_matrix()
    errors=heldout_direction_errors(r,s,(r@s.T).T)
    assert np.max(errors)<1e-5
