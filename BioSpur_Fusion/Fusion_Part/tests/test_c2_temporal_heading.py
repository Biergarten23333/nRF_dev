import copy
import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_sparse_nodes.heading_transport import temporal_heading
from biospur_fusion.c2_sparse_nodes.calibration import relative
from biospur_fusion.c2_sparse_nodes.inertial import acceleration
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_five_calibration.temporal_heading import fit_temporal_arm_heading,select_heading_model


def calibration():
    return dict(initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist(),pelvis_closure_rad=0.,
        functional_yaw_rad=[0.]*5,frozen_heading_correction_rad=dict.fromkeys(NODES[1:],0.),
        temporal_heading_curves={NODES[1]:dict(time_s=[0.,1.,2.],correction_rad=[0.,.2,.4])})


def test_drift_transport_keeps_orientation_and_acceleration_in_same_frame():
    t=np.linspace(0.,2.,41)
    raw=Rotation.from_rotvec(np.column_stack((t*0,t*0,-.2*t)))
    world_force=np.tile([1.,2.,9.80665],(len(t),1))
    # The physical sensor stays fixed; only its estimated yaw drifts.
    # Specific force therefore stays in the unchanged physical sensor axes.
    rows=np.column_stack((t,raw.as_quat()[:,[3,0,1,2]],world_force,np.zeros((len(t),3))))
    c=calibration();before=copy.deepcopy(c)
    np.testing.assert_allclose(relative(rows,NODES[1],c),np.tile(np.eye(3),(len(t),1,1)),atol=1e-12)
    np.testing.assert_allclose(acceleration(rows,NODES[1],c),np.tile([1.,2.,0.],(len(t),1)),atol=1e-12)
    assert c==before


def test_no_post_calibration_extrapolation_or_episode_reset():
    c=calibration();t=np.array([-10.,0.,.5,1.,1.5,2.,10.,100.])
    all_values=temporal_heading(t,NODES[1],c)
    np.testing.assert_array_equal(all_values,np.r_[temporal_heading(t[:4],NODES[1],c),temporal_heading(t[4:],NODES[1],c)])
    np.testing.assert_allclose(all_values[[0,1]],0.)
    np.testing.assert_allclose(all_values[-3:],.4)
    np.testing.assert_allclose(temporal_heading(t,NODES[0],c),0.)


def test_curve_builder_rejects_holdout_evidence():
    c=calibration();c['heading_factors']={}
    for n in NODES[1:3]:
        c['heading_factors'][n]=[dict(action='H01_boxing',source_role=role,quality=1.,measurement_time_s=i,measurement_delta_rad=0.)
            for i,role in enumerate(('directed_side','axis_forward','axis_lateral','directed_forward'))]
    with pytest.raises(ValueError,match='calibration-only'):fit_temporal_arm_heading(c)


def test_model_selection_does_not_turn_stationary_noise_into_drift():
    times=np.arange(4,dtype=float)
    value,audit=select_heading_model(times,np.array([.02,-.02,.02,-.02]),np.ones(4))
    assert audit['selected']=='constant'
    np.testing.assert_allclose(value,0.)
    value,audit=select_heading_model(times,np.array([0.,.2,.4,.6]),np.ones(4))
    assert audit['selected']=='curve'
    np.testing.assert_allclose(value,[0.,.2,.4,.6])
    assert audit['final_fit_factor_count']==4


def test_shared_calibration_increment_reaches_curve_and_scalar_replay_once():
    from biospur_fusion.c2_five_calibration.shared_orientation import with_heading_increment
    c=calibration();before=copy.deepcopy(c);delta=np.array([.13,-.27,.08,-.04])
    proposed=with_heading_increment(c,delta)
    t=np.array([-1.,0.,.35,1.4,2.,100.])
    for i,node in enumerate(NODES[1:]):
        np.testing.assert_allclose(temporal_heading(t,node,proposed)-temporal_heading(t,node,c),delta[i],atol=1e-12)
    assert c==before
    # Existing raw-to-world transport must see the same increment on both
    # measured orientation and gravity-removed acceleration.
    rows=np.column_stack((t,np.tile([1.,0.,0.,0.],(len(t),1)),np.tile([1.,2.,9.80665],(len(t),1)),np.zeros((len(t),3))))
    for i,node in enumerate(NODES[1:]):
        yaw=Rotation.from_euler('z',delta[i]).as_matrix()
        np.testing.assert_allclose(relative(rows,node,proposed),yaw@relative(rows,node,c),atol=1e-12)
        np.testing.assert_allclose(acceleration(rows,node,proposed),acceleration(rows,node,c)@yaw.T,atol=1e-12)
