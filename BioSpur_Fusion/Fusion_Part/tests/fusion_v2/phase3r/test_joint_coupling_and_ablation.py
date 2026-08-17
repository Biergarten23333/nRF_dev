import dataclasses
import numpy as np

from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.estimator import EstimatorConfig
from biospur_fusion.imu_pose_v1.observability import construct_information,svd_scan
from biospur_fusion.imu_pose_v1.pipeline import run_frontends,run_coupled,calibration_from_known
from conftest import mapping_for


def prepared(data):
    mapping=mapping_for(data);front,_=run_frontends(data.samples_by_node,initial_q_WI={n:so3.inv(q) for n,q in data.q_I_S.items()})
    cal=calibration_from_known(mapping,data.q_I_S)
    targets={k:np.array([1.,0,0,0]) for k in ('elbow_left','elbow_right','knee_left','knee_right')}
    conf={k:.8 for k in targets};axes={k:np.array([1.,0,0]) for k in targets}
    return mapping,front,cal,targets,conf,axes


def test_joint_factors_create_cross_state_and_nonzero_updates(synthetic_short):
    m,f,c,t,h,a=prepared(synthetic_short);frames,e=run_coupled(f,m,c,hinge_axes=a,heading_targets=t,heading_confidence=h)
    assert frames and e.cross_state_norm()>1e-8
    for name in ('sensor_to_segment_measurement','parent_child_articulation','elbow_knee_dominant_axis','multi_dof_soft_rom','relative_heading_correction','calibration_covariance'):
        row=e.activation_report()[name];assert row['count']>0 and row['jacobian_nonzero_blocks']>0 and row['information_trace']>0
        assert row['state_delta_sq']>0


def test_each_joint_ablation_changes_pose(synthetic_short):
    m,f,c,t,h,a=prepared(synthetic_short);base,_=run_coupled(f,m,c,hinge_axes=a,heading_targets=t,heading_confidence=h)
    toggles=('enable_sensor_to_segment','enable_joint_closure','enable_hinge_axis','enable_rom','enable_relative_heading','enable_calibration_covariance')
    for toggle in toggles:
        cfg=dataclasses.replace(EstimatorConfig(),**{toggle:False})
        changed,_=run_coupled(f,m,c,cfg,a,t,h)
        diff=max(so3.geodesic(base[-1].segment_quaternions_W_S[s],changed[-1].segment_quaternions_W_S[s]) for s in base[-1].segment_quaternions_W_S)
        assert diff>1e-8,toggle


def test_information_is_computed_and_factor_removal_changes_spectrum(synthetic_short):
    m,f,c,t,h,a=prepared(synthetic_short);_,full=run_coupled(f,m,c,hinge_axes=a,heading_targets=t,heading_confidence=h)
    data,prior=construct_information(full);report=svd_scan(data);preport=svd_scan(prior)
    assert report['dimension']==30 and report['whitened_symmetry_error']<1e-10
    assert report['tolerance_scan']['0.0001']['nullity']>=1
    assert preport['tolerance_scan']['0.0001']['rank']>=report['tolerance_scan']['0.0001']['rank']
    _,ablated=run_coupled(f,m,c,dataclasses.replace(EstimatorConfig(),enable_hinge_axis=False),a,t,h)
    d2,_=construct_information(ablated)
    assert not np.allclose(np.linalg.svd(data,compute_uv=False),np.linalg.svd(d2,compute_uv=False))


def test_global_yaw_does_not_invalidate_tilt(synthetic_short):
    m,f,c,t,h,a=prepared(synthetic_short);frames,_=run_coupled(f,m,c,hinge_axes=a,heading_targets=t,heading_confidence=h)
    assert 'GLOBAL_YAW_GAUGE_ACTIVE' in frames[-1].gauges
    assert any(v=='USABLE_BODY_RELATIVE_TILT' for v in frames[-1].segment_quality.values())


def test_so3_log_right_jacobian_matches_finite_difference():
    phi=np.array([.7,-.45,.3]);base=so3.exp(phi);eps=1e-7;numeric=np.empty((3,3))
    for k in range(3):
        d=np.zeros(3);d[k]=eps
        numeric[:,k]=(so3.log(so3.mul(base,so3.exp(d)))-phi)/eps
    np.testing.assert_allclose(so3.right_jacobian_inverse(phi),numeric,rtol=2e-6,atol=2e-7)
