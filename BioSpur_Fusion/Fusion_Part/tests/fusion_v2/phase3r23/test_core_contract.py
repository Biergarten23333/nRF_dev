from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.common_heading_v1.core import (
    circular_axis_mean, dynamic_offset, horizontal_axis_angle, information_rank,
    quat_conjugate, quat_multiply, quat_rotate, rp2_mean, rz, schur_profile,
    wrap_axis_line, wrap_pi,
)


def q_axis(axis, angle):
    axis=np.asarray(axis,float);axis/=np.linalg.norm(axis)
    return np.r_[math.cos(angle/2), axis*math.sin(angle/2)]


def test_identity_quaternion():
    v=np.array([.2,-.3,.9])
    assert np.allclose(quat_rotate([1,0,0,0],v),v)


@pytest.mark.parametrize("axis", ([1,0,0],[0,1,0],[0,0,1]))
@pytest.mark.parametrize("angle", (-math.pi/2, math.pi/2))
def test_plus_minus_ninety(axis,angle):
    q=q_axis(axis,angle);v=np.array([.31,-.47,.826])
    assert np.allclose(quat_rotate(quat_conjugate(q),quat_rotate(q,v)),v,atol=1e-12)


def test_scalar_first_hamilton_composition():
    qx=q_axis([1,0,0],.7);qy=q_axis([0,1,0],-.4);v=np.array([.2,.4,.8])
    assert np.allclose(quat_rotate(quat_multiply(qy,qx),v),quat_rotate(qy,quat_rotate(qx,v)),atol=1e-12)


def test_q_sign_same_rotation():
    q=q_axis([.2,.8,-.1],1.2);v=np.array([.3,.4,-.2])
    assert np.array_equal(quat_rotate(q,v),quat_rotate(-q,v))


def test_left_heading_cannot_be_right_extrinsic_dynamic():
    h=.6;local=np.array([.2,-.3,.9]);motions=[np.eye(3),rz(.4),np.array([[1,0,0],[0,0,-1],[0,1,0]],float)]
    left=np.stack([rz(h)@r@local for r in motions]);right=np.stack([r@rz(h)@local for r in motions])
    assert np.max(np.linalg.norm(left-right,axis=1))>.1


@pytest.mark.parametrize("value,expected", [(0.0,0.0),(math.pi,0.0),(-math.pi,0.0),(3*math.pi/2,-math.pi/2)])
def test_axis_line_wrap(value,expected):
    assert math.isclose(float(wrap_axis_line(value)),expected,abs_tol=1e-12)


def test_axis_line_mean_antipodal():
    mean,concentration=circular_axis_mean([.2,.2+math.pi,.21])
    assert abs(float(wrap_axis_line(mean-.203333)))<.01 and concentration>.99


def test_horizontal_vertical_rejected():
    with pytest.raises(ValueError):horizontal_axis_angle(np.tile([0,0,1.],(10,1)))


def test_rp2_antipodal_mean():
    mean,_=rp2_mean(np.array([[1,0,0],[-1,0,0],[.999,.01,0]]))
    assert abs(mean[0])>.999


def test_information_rank():
    result=information_rank(np.diag([3.,2.,0.]),[1e-8])
    assert result["rank_by_relative_tolerance"]["1e-08"]==2


def test_schur_preserves_unanchored_null():
    j=np.column_stack((np.eye(3),-np.ones(3)))
    profiled=schur_profile(j.T@j,3)
    assert np.linalg.matrix_rank(profiled,tol=1e-9)==2


def test_dynamic_offset_is_fixed():
    assert dynamic_offset(20260819,"06_elbow_left")==dynamic_offset(20260819,"06_elbow_left")
    assert dynamic_offset(20260819,"06_elbow_left") in (0,1,2)


def test_contract_pelvis_and_pipeline_scope():
    root=Path(__file__).resolve().parents[3]
    payload=json.loads((root/"config/fusion_v2/phase3r23/PHASE3R23_CONTRACT.json").read_text())
    assert payload["pelvis_node"]=="BSFC2CC"
    assert payload["opensense_full_input_pipeline_ready"] is False
    assert len(payload["relative_heading_order"])==9
