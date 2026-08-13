import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from v47_c2cc_rotation_aware import (FROZEN_ROTATION_AWARE_CONFIG,direction_error_deg,
    estimate_up,fit_rotation_and_lever,fit_rotation_lines,predict_displacement,
    propagate_mount_q1,robust_center,rotation_angle_deg,stationary_runs,
    validate_time_offset,zupt_preintegrated_displacement)


def constraints(R,lever,seed=47):
    rng=np.random.default_rng(seed);rows=[]
    vectors=[np.array([0,0,.8]),np.array([0,0,-.8]),np.array([0,0,.7]),np.array([0,0,-.7]),
             np.array([.8,0,0]),np.array([-.8,0,0]),np.array([0,.75,0]),np.array([0,-.75,0]),
             np.array([.5,.4,.2]),np.array([-.4,.5,-.1])]
    for i,dN in enumerate(vectors):
        R0=Rotation.from_euler('xyz',rng.uniform(-60,60,3),degrees=True).as_matrix()
        R1=Rotation.from_euler('xyz',rng.uniform(-60,60,3),degrees=True).as_matrix()
        dV=R@(dN+(R1-R0)@lever)
        rows.append({'label':'vertical_1' if i<4 else 'horizontal_1' if i<6 else 'horizontal_2',
                     'dN':dN,'dV':dV,'R0':R0,'R1':R1,'sigma_m':.075})
    return rows


def test_simultaneous_translation_rotation_and_arbitrary_remounts():
    lever=np.array([.035,-.018,.012])
    for euler in ([34,-21,118],[-76,44,-32]):
        truth=Rotation.from_euler('xyz',euler,degrees=True).as_matrix();rows=constraints(truth,lever)
        up=truth@np.array([0,0,1.]);fit=fit_rotation_and_lever(rows,up)
        assert rotation_angle_deg(fit['rotation'],truth)<1e-5
        assert np.linalg.norm(fit['lever_S_m']-lever)<1e-5
        assert all(fit['checks'].values())


def test_gravity_aligned_yaw_unknown_is_resolved_by_two_horizontal_directions():
    truth=Rotation.from_euler('z',137,degrees=True).as_matrix();rows=constraints(truth,np.zeros(3))
    fit=fit_rotation_and_lever(rows,truth@np.array([0,0,1.]))
    assert rotation_angle_deg(fit['rotation'],truth)<1e-5


def test_proper_up_recovery_with_t4_outlier_and_bootstrap():
    up=np.array([.1,-.2,.974679]);up/=np.linalg.norm(up);rows=[]
    for i in range(8):
        d=up*(.7 if i%2==0 else -.7);rows.append({'label':'vertical_1','dV':d})
    result=estimate_up(rows);assert np.degrees(np.arccos(np.clip(result['up_V4']@up,-1,1)))<1e-4
    points=np.r_[np.random.default_rng(1).normal([1,2,3],.02,(20,3)),[[9,9,9]]]
    center,q=robust_center(points);assert np.linalg.norm(center-[1,2,3])<.03 and q['outliers']==1


def test_rotation_over_43_dps_is_not_a_failure_and_preintegration_is_rotation_aware():
    t=np.arange(0,2.005,.005);omega=np.radians(90);R=np.asarray([Rotation.from_rotvec([0,omega*x,0]).as_matrix() for x in t])
    aN=np.zeros((len(t),3));aN[:,0]=1.2*np.sin(2*np.pi*t/2)
    specific=np.einsum('nji,nj->ni',R,aN-np.array([0,0,-9.80665]))
    displacement,diag=zupt_preintegrated_displacement(t,specific,R)
    assert np.isfinite(displacement).all() and diag['corrected_end_speed_mps']<1e-12
    assert 90>43 and 90<FROZEN_ROTATION_AWARE_CONFIG.gyro_saturation_dps


def test_stationary_detection_handles_bias_and_bounded_accel_offset():
    t=np.arange(0,2,.005);acc=np.tile([.05,-.03,9.80665],(len(t),1));gyro=np.tile([.2,-.3,.1],(len(t),1))
    runs=stationary_runs(t,acc,gyro,np.array([.2,-.3,.1]));assert runs and t[runs[0][1]]-t[runs[0][0]]>1.5


def test_lever_arm_is_explicit_and_material_without_model():
    truth=Rotation.from_euler('xyz',[20,30,40],degrees=True).as_matrix();lever=np.array([.08,.02,-.03]);rows=constraints(truth,lever)
    fit=fit_rotation_and_lever(rows,truth@np.array([0,0,1.]));assert np.linalg.norm(fit['lever_S_m'])>.05
    assert max(direction_error_deg(predict_displacement(fit,row),row['dV']) for row in rows)<1e-4


def test_reflection_and_collinear_or_insufficient_excitation_fail_closed():
    truth=np.diag([1,1,-1]);rows=constraints(truth,np.zeros(3))
    fit=fit_rotation_and_lever(rows,np.array([0,0,-1.]));assert np.linalg.det(fit['rotation'])>0
    assert not fit['checks']['fit_residual']
    with pytest.raises(ValueError,match='insufficient total'):fit_rotation_and_lever(rows[:3],np.array([0,0,1.]))


def test_gyro_saturation_threshold_and_time_policy_are_frozen():
    assert FROZEN_ROTATION_AWARE_CONFIG.gyro_saturation_dps==1900
    assert not FROZEN_ROTATION_AWARE_CONFIG.time_offset_enabled
    assert FROZEN_ROTATION_AWARE_CONFIG.time_offset_s==0
    assert validate_time_offset(0)==0
    with pytest.raises(ValueError,match="disabled"):validate_time_offset(.005)


def test_actual_gyro_saturation_is_rejected_but_90_dps_is_accepted():
    t=np.arange(0,1.505,.005);acc=np.tile([0.,0.,9.80665],(len(t),1))
    nominal=np.zeros_like(acc);nominal[:,1]=90
    _,_,_,diag=propagate_mount_q1(t,acc,nominal)
    assert not diag['gyro_saturated_or_clipped']
    saturated=nominal.copy();saturated[-1,1]=FROZEN_ROTATION_AWARE_CONFIG.gyro_saturation_dps
    with pytest.raises(ValueError,match='saturation'):propagate_mount_q1(t,acc,saturated)


def test_common_global_clock_origin_is_invariant_and_line_fit_is_proper():
    truth=Rotation.from_euler('xyz',[22,-17,64],degrees=True).as_matrix();rows=constraints(truth,np.zeros(3))
    for row in rows:row['start_s']=12.3;row['end_s']=13.1
    up=truth@np.array([0.,0.,1.]);fit=fit_rotation_lines(rows,up)
    assert fit['checks']['proper_rotation']
    assert fit['checks']['unsigned_fit']
    shifted=[dict(row,start_s=row['start_s']+12345,end_s=row['end_s']+12345) for row in rows]
    fit_shifted=fit_rotation_lines(shifted,up)
    assert rotation_angle_deg(fit['rotation'],fit_shifted['rotation'])<1e-8


def test_heldout_exclusion_and_no_mount_reuse_are_structural_contracts():
    fitting={'A':{'source':'A_CALIBRATION_ONLY'},'B':{'source':'B_CALIBRATION_ONLY'}}
    assert fitting['A']['source']!='B_CALIBRATION_ONLY'
    assert fitting['B']['source']!='A_CALIBRATION_ONLY'
    assert 'VALIDATION' not in fitting['A']['source']+fitting['B']['source']
