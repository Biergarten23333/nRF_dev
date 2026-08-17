import numpy as np

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.official import (run_official_vqf,run_qmt_heading,run_qmt_hinge_axis,
    run_qmt_reset_alignment,reject_incompatible_six_sensor_checkpoint)
from biospur_fusion.imu_pose_v1.types import ImuSample


def hinge_case(n=800,dt=.01):
    t=np.arange(n)*dt
    qp=np.array([so3.exp([.12*np.sin(.4*x),.08*np.cos(.3*x),.25*np.sin(.2*x)]) for x in t])
    rel=np.array([so3.exp([.9*np.sin(.8*x),0,0]) for x in t]);qc=so3.mul(qp,rel)
    def gyro(q):
        out=np.zeros((len(q),3));out[1:]=so3.log(so3.mul(so3.inv(q[:-1]),q[1:]))/dt;return out
    gp,gc=gyro(qp),gyro(qc);g=np.array([0,0,9.80665])
    ap=np.einsum('nij,j->ni',np.swapaxes(so3.matrix(qp),1,2),g)
    ac=np.einsum('nij,j->ni',np.swapaxes(so3.matrix(qc),1,2),g)
    return t,qp,qc,gp,gc,ap,ac


def test_official_vqf_executes_and_is_deterministic():
    t,_,_,_,gyr,_,acc=hinge_case(600)
    samples=[ImuSample("BSF0001",float(x),int(x*1e6),i,gyr[i],acc[i],0) for i,x in enumerate(t)]
    a=run_official_vqf(samples,100);b=run_official_vqf(samples,100)
    assert a.quaternion6D_W_I.shape==(600,4);assert a.gyro_bias_rad_s.shape==(600,3)
    assert a.bias_sigma_rad_s.shape==(600,);assert a.rest_detected.shape==(600,)
    np.testing.assert_array_equal(a.quaternion6D_W_I,b.quaternion6D_W_I)
    assert len(a.lineage_sample_uids)==600 and a.runtime_s>0


def test_official_qmt_reset_axis_and_heading_execute():
    t,qp,qc,gp,gc,ap,ac=hinge_case()
    reset=run_qmt_reset_alignment(np.stack((qp,qc)),20);assert reset.shape==(2,len(t),4)
    axis=run_qmt_hinge_axis(ap,ac,gp,gc);assert abs(axis.parent_axis_sensor[0])>.95 and abs(axis.child_axis_sensor[0])>.95
    assert axis.confidence>.8 and axis.runtime_s>0
    bad=so3.mul(so3.exp([0,0,.4]),qc);heading=run_qmt_heading(gp,gc,qp,bad,t,axis.child_axis_sensor)
    assert heading.corrected_child_quaternion.shape==qc.shape and heading.confidence>.5
    assert abs(np.nanmedian(heading.filtered_offset_rad)+.4)<.08


def test_incompatible_pip_transpose_paths_fail_closed():
    for channels,weights in ((('pelvis','head'),False),(tuple('abcdefghij'),True),(('zero_filled',)*10,False)):
        try:reject_incompatible_six_sensor_checkpoint(channels=channels,weights_requested=weights)
        except ValueError:pass
        else:raise AssertionError("incompatible pretrained path accepted")
