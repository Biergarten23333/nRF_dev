#!/usr/bin/env python3
from __future__ import annotations
import argparse,dataclasses,hashlib,json,sys,time
from pathlib import Path
from types import MappingProxyType
import numpy as np

REPO=Path(__file__).resolve().parents[5];BASE=REPO/'BioSpur_Fusion/Fusion_Part';sys.path.insert(0,str(BASE/'src'))
from biospur_fusion.imu_pose_v1 import so3
from biospur_fusion.imu_pose_v1.calibration import CalibrationBundle,SegmentCalibration
from biospur_fusion.imu_pose_v1.estimator import EstimatorConfig
from biospur_fusion.imu_pose_v1.frontend import FrontendConfig
from biospur_fusion.imu_pose_v1.joints import JOINTS
from biospur_fusion.imu_pose_v1.mapping import FrozenOperatorMapping
from biospur_fusion.imu_pose_v1.metrics import synthetic_errors
from biospur_fusion.imu_pose_v1.observability import construct_information,svd_scan
from biospur_fusion.imu_pose_v1.pipeline import calibration_from_known,run_coupled,run_frontends
from biospur_fusion.imu_pose_v1.qualification import audit_real_master,validate_svd_report
from biospur_fusion.imu_pose_v1.synthetic import generate

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def mapping(d):return FrozenOperatorMapping.from_payload({'mapping':d.mapping,'binding_authority':'OPERATOR_RECORDED_POST_CAPTURE'},capture_id='Capture_2_with_JOINT_LABEL',session_id='capture_2_with_joint_label',donning_id='capture_2_with_joint_label_donning_01')
def run(d,cal=None,fc=None,cfg=None,priors=False):
 m=mapping(d);front,fr=run_frontends(d.samples_by_node,fc,initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()})
 names=('elbow_left','elbow_right','knee_left','knee_right');axes={k:np.array([1.,0,0]) for k in names} if priors else None;targets={k:np.array([1.,0,0,0]) for k in names} if priors else None;conf={k:.8 for k in names} if priors else None
 frames,e=run_coupled(front,m,cal or calibration_from_known(m,d.q_I_S),cfg,axes,conf,targets,conf);return m,front,fr,frames,e
def metrics(frames,d):
 x=synthetic_errors(frames,d.truth_time_s,d.truth_q_W_S);return {'orientation_max_deg':float(np.rad2deg(x['orientation_rad'].max())),'relative_joint_p95_deg':float(np.rad2deg(np.percentile(x['relative_joint_rad'],95))),'static_tilt_rms_deg':float(np.rad2deg(np.sqrt(np.mean(x['static_tilt_rad']**2)))),'bone_length_max_variation':x['bone_length_max_variation']}

def main():
 p=argparse.ArgumentParser();p.add_argument('--evidence',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--implementation-sha',required=True);a=p.parse_args();started=time.perf_counter()
 gates=json.loads((BASE/'config/fusion_v2/phase3r/PHASE3R_SYNTHETIC_GATES.json').read_text())
 dn=generate(seed=2,duration_s=6,noise=False,irregular=False,gaps=False,biases=False,transients=False,outliers=False)
 fc=FrontendConfig(gyro_noise_rad_s_sqrt_hz=1e-7,gyro_bias_walk_rad_s2_sqrt_hz=1e-9,accel_bias_walk_m_s3_sqrt_hz=1e-9,accel_noise_m_s2=.001,initial_orientation_sigma_rad=1e-5,initial_gyro_bias_sigma_rad_s=1e-6,initial_accel_bias_sigma_m_s2=1e-6)
 cfg=EstimatorConfig(measurement_floor_sigma_rad=np.deg2rad(.001),temporal_relative_sigma_rad=np.deg2rad(60),hinge_orthogonal_sigma_rad=np.deg2rad(30),multi_rom_sigma=.001)
 mn,_,_,fn,en=run(dn,calibration_from_known(mapping(dn),dn.q_I_S,np.deg2rad(.001)),fc,cfg);noiseless=metrics(fn,dn)
 d=generate(seed=12,duration_s=5.5,noise=True,irregular=True,gaps=False,biases=True,transients=False,outliers=False);m,front,fr,frames,e=run(d,priors=True);normal=metrics(frames,d)
 bg_error=[np.linalg.norm(np.asarray(fr[n]['final_gyro_bias'])-d.gyro_bias[n]) for n in fr]
 data,prior=construct_information(e);data_svd=svd_scan(data);prior_svd=svd_scan(prior);validate_svd_report(data,data_svd);validate_svd_report(prior,prior_svd)
 base=frames[-1];ablations={}
 for toggle in ('enable_gyro_bias_estimation','enable_accel_bias_estimation','enable_gravity_update'):
  f2,_=run_frontends(d.samples_by_node,dataclasses.replace(FrontendConfig(),**{toggle:False}),initial_q_WI={n:so3.inv(q) for n,q in d.q_I_S.items()});names=('elbow_left','elbow_right','knee_left','knee_right');x2,_=run_coupled(f2,m,calibration_from_known(m,d.q_I_S),hinge_axes={k:np.array([1.,0,0]) for k in names},hinge_confidence={k:.8 for k in names},heading_targets={k:np.array([1.,0,0,0]) for k in names},heading_confidence={k:.8 for k in names});ablations[toggle]=max(float(so3.geodesic(base.segment_quaternions_W_S[s],x2[-1].segment_quaternions_W_S[s])) for s in base.segment_quaternions_W_S)
 for toggle in ('enable_sensor_to_segment','enable_joint_closure','enable_hinge_axis','enable_rom','enable_relative_heading','enable_calibration_covariance'):
  names=('elbow_left','elbow_right','knee_left','knee_right');x2,_=run_coupled(front,m,calibration_from_known(m,d.q_I_S),dataclasses.replace(EstimatorConfig(),**{toggle:False}),{k:np.array([1.,0,0]) for k in names},{k:.8 for k in names},{k:np.array([1.,0,0,0]) for k in names},{k:.8 for k in names});ablations[toggle]=max(float(so3.geodesic(base.segment_quaternions_W_S[s],x2[-1].segment_quaternions_W_S[s])) for s in base.segment_quaternions_W_S)
 coverage=[]
 for seed in range(30,40):
  dt=generate(seed=seed,duration_s=5.5,noise=True,irregular=True,gaps=False,biases=True,transients=False,outliers=False);mt=mapping(dt);rng=np.random.default_rng(1000+seed)
  rows={n:SegmentCalibration(n,mt.segment_for(n),so3.mul(q,so3.exp(rng.normal(0,np.deg2rad(1),3))),np.eye(3)*np.deg2rad(1)**2,'MC_PERTURBED',('independent_trial',),'C2CC_DISTINCT' if n=='BSFC2CC' else 'H9') for n,q in dt.q_I_S.items()};_,_,_,ft,_=run(dt,CalibrationBundle(MappingProxyType(rows)),priors=True);hit=[]
  for frame in ft:
   i=int(np.argmin(abs(dt.truth_time_s-frame.time_s)))
   for j in JOINTS:
    err=so3.geodesic(so3.between(frame.segment_quaternions_W_S[j.parent],frame.segment_quaternions_W_S[j.child]),so3.between(dt.truth_q_W_S[j.parent][i],dt.truth_q_W_S[j.child][i]));hit.append(err<=2.795*frame.joint_relative_sigma_rad[j.name])
  coverage.append(float(np.mean(hit)))
 master=json.loads((a.evidence/'REAL_ACTION_MASTER_SUMMARY.json').read_text());real=audit_real_master(master)
 checks={'noiseless_orientation':noiseless['orientation_max_deg']<=gates['noiseless_gauge_aligned_orientation_max_deg'],'noisy_relative_joint':normal['relative_joint_p95_deg']<=gates['noisy_normal_relative_joint_p95_deg'],'static_tilt':normal['static_tilt_rms_deg']<=gates['static_tilt_rms_deg'],'fixed_bone':max(noiseless['bone_length_max_variation'],normal['bone_length_max_variation'])<=gates['bone_length_max_variation_normalized'],'coverage':gates['coverage_point_bounds'][0]<=float(np.mean(coverage))<=gates['coverage_point_bounds'][1],'all_ablations_nonzero':all(x>1e-10 for x in ablations.values())}
 out={'schema':'biospur-phase3r-qualification-raw-v1','implementation_sha':a.implementation_sha,'inputs':{'gate_config_sha256':digest(BASE/'config/fusion_v2/phase3r/PHASE3R_SYNTHETIC_GATES.json'),'solver_config_sha256':digest(BASE/'config/fusion_v2/phase3r/PHASE3R_SOLVER_CONFIG.json'),'real_master_sha256':digest(a.evidence/'REAL_ACTION_MASTER_SUMMARY.json')},'noiseless':noiseless,'normal_noisy':normal,'gyro_bias_error_rad_s':{'per_node':bg_error,'median':float(np.median(bg_error)),'p95':float(np.percentile(bg_error,95))},'coverage':{'independent_unit':'Monte Carlo trial','seeds':list(range(30,40)),'per_trial':coverage,'mean':float(np.mean(coverage)),'std':float(np.std(coverage,ddof=1)),'standard_error':float(np.std(coverage,ddof=1)/np.sqrt(len(coverage)))},'observability':{'data_only':data_svd,'prior_inclusive':prior_svd},'ablations_final_pose_change_rad':ablations,'real':real,'checks':checks,'pass':all(checks.values()),'runtime_s':time.perf_counter()-started,'claims':'synthetic correctness and engineering replay only; no human accuracy'}
 a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,indent=2,sort_keys=True)+'\n');print(json.dumps({'pass':out['pass'],'checks':checks,'coverage_mean':out['coverage']['mean'],'output':str(a.output)}))
 return 0 if out['pass'] else 2
if __name__=='__main__':raise SystemExit(main())
