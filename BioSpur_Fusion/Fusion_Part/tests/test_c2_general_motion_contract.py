"""Motion-preservation contracts, not validation of real skiing/contact truth."""
from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.c2_uwb_root_world.root_input_safety import CausalImuHold
from biospur_fusion.c2_uwb_root_world.tight_range import RawRangeUpdateConfig, linearize_raw_range_factors
from biospur_fusion.root_r3.models import RootState
from test_c2_contact_conditional_heading import heading_owner
from test_c2_articulated_contact import ankles
from test_c2_continuous_full_state_feedback import ANCHORS, CLOCK, row_at


@pytest.mark.parametrize('velocity', ([20.,0.,0.], [0.,-20.,0.], [10.,0.,5.]))
def test_unsupported_high_speed_propagation_does_not_require_acceleration(velocity):
    j=heading_owner(raw_structural_heading=True)
    x=j.state.root.vector.copy();x[3:6]=velocity
    j._install(x,j.state.rotations,j.state.covariance)
    origin=x[:3].copy()
    # Zero world acceleration is compatible with arbitrary existing velocity.
    for i in range(20):
        t=i*.005
        force=j.sensor_rotation().T@np.array([0.,0.,9.80665])
        j.propagate_safe(CausalImuHold(t,force,j.sensor_rotation()),t+.005)
    np.testing.assert_allclose(j.state.root.position_m,origin+np.asarray(velocity)*.1,atol=1e-12)
    np.testing.assert_allclose(j.state.root.velocity_mps,velocity,atol=1e-12)
    assert not j.contacts.sides
    np.linalg.cholesky(j.state.covariance)


def test_free_flight_retains_vertical_motion_without_ground_height_prior():
    j=heading_owner(raw_structural_heading=True)
    x=j.state.root.vector.copy();x[3:6]=[15.,0.,4.]
    j._install(x,j.state.rotations,j.state.covariance)
    origin=x[:3].copy()
    for i in range(100):
        t=i*.005
        j.propagate_safe(CausalImuHold(t,np.zeros(3),j.sensor_rotation()),t+.005)
    np.testing.assert_allclose(j.state.root.position_m,origin+[7.5,0.,2.-.5*9.80665*.5**2],atol=2e-12)
    assert not j.contacts.sides


def test_high_speed_raw_factor_uses_each_measured_link_epoch():
    j=heading_owner(raw_structural_heading=True)
    x=j.state.root.vector.copy();x[3:6]=[20.,0.,0.]
    j._install(x,j.state.rotations,j.state.covariance)
    node='BSF31CC';offset=j.tags()[node]
    row=replace(row_at(0.,x[:3]+offset),node=node,t_round_us=tuple(range(2000,10000,1000)))
    epochs=np.array([CLOCK.seconds(row.strobe_us+.5*q) for q in row.t_round_us])
    distances=np.linalg.norm(x[:3]+offset+epochs[:,None]*x[3:6]-ANCHORS,axis=1)
    row=replace(row,ranges_mm=tuple(np.rint(distances*1000).astype(int)))
    factors=linearize_raw_range_factors(j.state.root,row,anchors_m=ANCHORS,clock=CLOCK,
        tag_offset_world_m=offset,reference_epoch_s=0.,config=RawRangeUpdateConfig())
    assert np.max(abs(factors.innovations_m))<=.000501
    assert np.max(np.linalg.norm(epochs[:,None]*x[3:6],axis=1))>.05
    before=j.state.root.vector.copy()
    assert j.update_ranges(row,anchors_m=ANCHORS,clock=CLOCK,reference_epoch_s=0.).accepted
    # A large physical displacement during the sweep is not an outlier.
    assert np.linalg.norm(j.state.root.position_m-before[:3])<.002
    assert j.state.root.velocity_mps[0]>19.99


def test_contact_birth_allows_different_heights_and_reentry_does_not_restore_old_height():
    j=heading_owner(raw_structural_heading=True)
    offsets=ankles(j);offsets[0,2]+=.2
    j.update_contact(offsets,[True,True],[False,False],[1,1],.005,[False,False])
    np.testing.assert_allclose(j.contacts.means.reshape(2,3),j.state.root.position_m+offsets)
    oldroot=j.state.root.vector.copy()
    j.update_contact(offsets,[False,True],[False,False],[1,1],.005,[False,False])
    offsets[0,2]+=.2
    j.update_contact(offsets,[True,True],[False,False],[1,1],.005,[False,False])
    k=j.contacts.sides.index(0)
    np.testing.assert_allclose(j.contacts.means.reshape(2,3)[k],j.state.root.position_m+offsets[0])
    np.testing.assert_array_equal(j.state.root.vector,oldroot)
    np.linalg.cholesky(j.contacts.covariance(j.tangent()))
