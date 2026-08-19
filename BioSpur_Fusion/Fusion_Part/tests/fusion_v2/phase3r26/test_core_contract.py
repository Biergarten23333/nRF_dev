import math

import numpy as np
import pytest

from biospur_fusion.heading_anchor_audit_v2.core import (
    COORDINATE_ORDER, circular_sector_distance, directed_residual,
    directed_structural_rows, evaluate_reduced_graph, gf2_rank,
    matrix_rank, pelvis_protocol_gauge, point_distances,
    quat_to_matrix_wxyz, reference_axis, sector_distances, wrap_2pi,
)


def test_correct_pelvis_sign_golden_and_finite_difference():
    theta=0.73;eps=1e-7
    assert pelvis_protocol_gauge(np.array([theta])) == pytest.approx(theta)
    derivative=float(wrap_2pi(pelvis_protocol_gauge(np.array([theta+eps]))-theta))/eps
    assert derivative == pytest.approx(1.0,abs=1e-6)


def test_wrong_pelvis_sign_fails():
    theta=0.73
    assert abs(float(wrap_2pi(pelvis_protocol_gauge(np.array([theta]),sign=-1)-theta))) > 1


def test_point_target_antipode_and_boundary():
    assert point_distances(0.0)[2] > 3
    assert point_distances(math.pi)[2] < -3
    assert point_distances(math.pi/2)[2] == pytest.approx(0,abs=1e-12)


def test_sector_and_antipodal_sector():
    p,a,m=sector_distances(3*math.pi/4,math.pi/2,math.pi)
    assert p==0 and a>0 and m>0
    p,a,m=sector_distances(-math.pi/4,math.pi/2,math.pi)
    assert a==0 and m<0


def test_vertical_projection_is_degenerate():
    q=np.array([[1.,0.,0.,0.]])
    axis=reference_axis(q)
    assert np.linalg.norm(axis[0,:2]) == 0


def test_q_and_minus_q_same_rotation():
    q=np.array([[.5,.5,.5,.5]])
    assert np.allclose(quat_to_matrix_wxyz(q),quat_to_matrix_wxyz(-q),atol=1e-14)
    assert np.allclose(reference_axis(q),reference_axis(-q),atol=1e-14)


def test_active_passive_inverse_and_xyzw_mutations_change_generic_axis():
    q=np.array([[.8,.2,.3,.45]])
    q=q/np.linalg.norm(q,axis=1,keepdims=True)
    active=reference_axis(q)
    assert not np.allclose(active,reference_axis(q,convention="passive"))
    assert not np.allclose(active,reference_axis(q,convention="transpose"))
    assert not np.allclose(active,reference_axis(q,convention="xyzw_as_wxyz"))


def test_directed_wrap_breaks_pi_while_line_wrap_does_not():
    r0=directed_residual(.1,.2,.3,.4,wrap="2pi")
    r1=directed_residual(.1+math.pi,.2,.3,.4,wrap="2pi")
    l0=directed_residual(.1,.2,.3,.4,wrap="mod_pi")
    l1=directed_residual(.1+math.pi,.2,.3,.4,wrap="mod_pi")
    assert abs(float(wrap_2pi(r1-r0))) > 3
    assert l1 == pytest.approx(l0,abs=1e-12)


def test_structural_rows_are_real_edge_rows_not_identity_fixture():
    names=["pelvis",*COORDINATE_ORDER]
    rows=directed_structural_rows(COORDINATE_ORDER,names)
    assert len(rows)==10 and all(row[-1]==-1 for row in rows)
    assert matrix_rank(np.asarray(rows,float))==10
    for removed in COORDINATE_ORDER:
        kept=directed_structural_rows(COORDINATE_ORDER,[x for x in names if x!=removed])
        assert matrix_rank(np.asarray(kept,float))==9


def test_empty_graph_constraint_rank_zero_not_forced_nine():
    assert matrix_rank(np.empty((0,10)))==0
    assert gf2_rank([[0]*9],9)==0
