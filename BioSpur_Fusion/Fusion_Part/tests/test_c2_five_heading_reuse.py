"""Retained-node heading correction reuses C2 and preserves force/frame consistency."""
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from biospur_fusion.c2_coupled_progressive import estimator, pose_reset_avatar
from biospur_fusion.c2_five_calibration import heading
from biospur_fusion.c2_sparse_nodes.calibration import relative
from biospur_fusion.c2_sparse_nodes.inertial import acceleration
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def test_mature_state_and_axis_statistics_are_used_directly():
    assert heading._PersistentHeadingState is estimator._PersistentHeadingState
    assert heading._axial_heading_delta is pose_reset_avatar._axial_heading_delta
    assert heading._weighted_circular_mean is pose_reset_avatar._weighted_circular_mean


def test_holdout_cannot_update_calibration_heading():
    with pytest.raises(ValueError,match='H cannot'):
        heading.fit_registered_heading({'H01_boxing':{}},{})


def test_unmeasured_parent_motion_cannot_overwrite_directed_arm_heading(monkeypatch):
    names=['02_t_pose','04_shoulder_left','05_shoulder_right','06_elbow_left',
           '07_elbow_right','10_knee_left_seated','11_knee_right_seated',
           '16_squat','18_heel_to_butt_left','19_heel_to_butt_right']
    rows=np.zeros((120,11)); rows[:,0]=np.arange(120)/20
    episodes={name:{'name':name,**{node:{'imu':rows} for node in NODES}} for name in names}
    root=np.tile(np.eye(3),(len(rows),1,1))
    def aligned(episode,node,calibration,**kwargs):
        angle=.2 if episode['name']=='02_t_pose' else 1.1
        sensor=np.tile(Rotation.from_euler('z',angle).as_matrix(),(len(rows),1,1))
        return rows,root,sensor
    monkeypatch.setattr(heading,'_aligned',aligned)
    monkeypatch.setattr(heading,'_gyro_axis',lambda *args:(np.array([1.,0.,0.]),{'principal_fraction':1.}))
    c={'forearm_mount_calibration':{NODES[1]:{'axis_sensor_long':[0.,-1.,0.]},
                                   NODES[2]:{'axis_sensor_long':[0.,1.,0.]}}}
    result=heading.fit_registered_heading(episodes,c)
    for node in NODES[1:3]:
        factors=result['factors'][node]
        np.testing.assert_allclose(factors[0]['measurement_delta_rad'],-.2,atol=1e-12)
        assert result['frozen_correction_rad'][node] == factors[0]['filtered_delta_rad']
        assert [r['used_for_frozen_heading'] for r in factors]==[True,False,False,False]
        assert [r['phase_id'] for r in factors[-2:]]==['flexion','pronation']
        assert len(factors)==4  # Both elbow phases remain recorded, not deleted.
        assert abs(factors[1]['measurement_delta_rad']+.2)>.3
    assert all(r['used_for_frozen_heading'] for node in NODES[3:] for r in result['factors'][node])


def test_shared_heading_fit_cannot_reintroduce_conditional_arm_planes():
    import torch
    from biospur_fusion.c2_five_calibration.shared_fit import RegisteredHeadingPrior
    factors={node:[dict(action='02_t_pose',source_role='directed_side',
        measurement_delta_rad=0.,quality=1.,base_sigma_deg=25.)] for node in NODES[1:]}
    baseline={node:0. for node in NODES[1:]}
    factors[NODES[1]].append(dict(action='06_elbow_left',source_role='axis_lateral',
        measurement_delta_rad=1.,quality=1.,base_sigma_deg=25.,used_for_frozen_heading=False))
    objective=RegisteredHeadingPrior(factors,baseline)
    assert float(objective.energy(torch.zeros(4,dtype=torch.float64)))==0.
    assert '06_elbow_left' not in objective.information[NODES[1]]


def test_replay_rotates_orientation_and_acceleration_in_the_same_frame():
    times=np.array([0.,5.,10.,15.,20.,30.,1000.])
    rows=np.zeros((len(times),11));rows[:,0]=times;rows[:,1]=1.
    rows[:,5]=1.;rows[:,7]=9.80665
    factors=[dict(start_time_s=5.,stop_time_s=10.,filtered_delta_rad=.2),
             dict(start_time_s=20.,stop_time_s=30.,filtered_delta_rad=.5)]
    c=dict(pelvis_closure_rad=0.,functional_yaw_rad=[0.]*5,
        initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist(),
        heading_factors={NODES[1]:factors})
    # Mature C2 holds the last completed state between factors and through H.
    expected=Rotation.from_rotvec(np.array([[0.,0.,a] for a in [.2,.2,.2,.2,.5,.5,.5]])).as_matrix()
    np.testing.assert_allclose(relative(rows,NODES[1],c),expected,atol=1e-12)
    np.testing.assert_allclose(acceleration(rows,NODES[1],c),expected@np.array([1.,0.,0.]),atol=1e-12)
    np.testing.assert_allclose(expected@np.array([0.,0.,1.]),np.tile([0.,0.,1.],(len(times),1)))
    np.testing.assert_array_equal(rows[:,0],times)
    np.testing.assert_allclose(relative(rows,NODES[0],c),np.tile(np.eye(3),(len(times),1,1)))


def test_frozen_five_node_calibration_does_not_turn_estimator_updates_into_motion():
    times=np.array([0.,5.,10.,15.,19.995,20.,30.,1000.])
    rows=np.zeros((len(times),11));rows[:,0]=times;rows[:,1]=1.
    rows[:,5]=1.;rows[:,7]=9.80665
    c=dict(pelvis_closure_rad=0.,functional_yaw_rad=[0.]*5,
        initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
        segment_axes_in_sensor=np.tile(np.eye(3),(5,1,1)).tolist(),
        heading_factors={NODES[1]:[
            dict(start_time_s=5.,stop_time_s=10.,filtered_delta_rad=.2),
            dict(start_time_s=20.,stop_time_s=30.,filtered_delta_rad=.5)]},
        frozen_heading_correction_rad={n:(.5 if n==NODES[1] else 0.) for n in NODES[1:]})
    rotation=relative(rows,NODES[1],c)
    expected=np.repeat(Rotation.from_euler('z',.5).as_matrix()[None],len(times),axis=0)
    np.testing.assert_allclose(rotation,expected,atol=1e-12)
    increments=np.swapaxes(rotation[:-1],1,2)@rotation[1:]
    np.testing.assert_allclose(Rotation.from_matrix(increments).magnitude(),0.,atol=1e-12)
    np.testing.assert_allclose(acceleration(rows,NODES[1],c),expected@np.array([1.,0.,0.]),atol=1e-12)
    np.testing.assert_array_equal(rows[:,0],times)


def test_incomplete_frozen_heading_fails_instead_of_falling_back_to_update_history():
    rows=np.zeros((4,11));rows[:,1]=1.
    c=dict(pelvis_closure_rad=0.,functional_yaw_rad=[0.]*5,
           initial_sensor_rotations=np.tile(np.eye(3),(5,1,1)).tolist(),
           frozen_heading_correction_rad={})
    with pytest.raises(ValueError,match='four finite'):
        relative(rows,NODES[1],c)
