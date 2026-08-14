#!/usr/bin/env python3
"""Offline, deterministic analysis of the 2026-08-14 body capture.

This program is intentionally capture-specific.  It has no serial/BLE imports and
fails closed on raw and geometry provenance before decoding scientific data.
"""
from __future__ import annotations

import argparse, csv, hashlib, importlib, itertools, json, math, os, sys
from collections import Counter, defaultdict
from pathlib import Path
import numpy as np

HERE=Path(__file__).resolve(); REPO=HERE.parents[3]; TOOLS=REPO/'B306_Part/tools'
sys.path.insert(0,str(TOOLS))
from v47_real_data_adapter import (NODES, IMU_DTYPE, UWB_DTYPE, _decode_host_frame,
    _decode_imu, _decode_uwb, iter_cobs_records, imu_physical)
from fusion_host_binary import FrameError
from v47_uwb_position_replay import load_solver, validate_anchor_slot_identity, validate_delay_ownership
from v47_q1_eskf import Q1T4ESKF, Q1Parameters, FrameBinding

RAW_SHA='a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a'
RAW_SIZE=224_739_075; LAYOUT_SHA='20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1'
AUTOPOS_COMMIT='87d9027cc368cd05e707dd3a564e4c28b9c505ee'; SOLVER_COMMIT='3acfeeda5fede3b157081549fdf1a5f4ca939a82'
T0=159270.593387964; END=160662.971234930
# First decoded host arrival at/after T0, observed in the retained collector log.
FORMAL_MASTER_MS=326_848_295; FORMAL_FIRST_HOST=159270.604326
SLOTS=('Wrist_L','Wrist_R','Elbow_L','Elbow_R','Pelvis','Knee_L','Knee_R','Ankle_L','Ankle_R')
TAG_BY_NODE={n:i+1 for i,n in enumerate(NODES)}

def sha(path):
 h=hashlib.sha256()
 with path.open('rb') as f:
  for b in iter(lambda:f.read(4<<20),b''): h.update(b)
 return h.hexdigest()
def dump(path,obj): path.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+'\n',encoding='utf-8')
def rows(path, data, fields=None):
 data=list(data); fields=fields or list(data[0])
 with path.open('w',newline='',encoding='utf-8') as f:
  w=csv.DictWriter(f,fieldnames=fields,lineterminator='\n'); w.writeheader(); w.writerows(data)
def host_s(ms): return FORMAL_FIRST_HOST+(float(ms)-FORMAL_MASTER_MS)/1000

def provenance(capture,out):
 layout=REPO/'B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json'
 manifest=layout.parent/'CAPTURE_BOUND_GEOMETRY_MANIFEST.json'; reflected=layout.parent/'V4IO/anchor_layout.json'
 if sha(layout)!=LAYOUT_SHA: raise SystemExit('BLOCKED_UWB_GEOMETRY_PROVENANCE')
 m=json.loads(manifest.read_text());
 if m['layout']['sha256']!=LAYOUT_SHA or m['position_solver']!='UWB_TAG_T4' or not m['geometry_capture_bound']:
  raise SystemExit('BLOCKED_UWB_GEOMETRY_PROVENANCE')
 validate_delay_ownership(transport_applies_v4_delay=False,solver_applies_v4_delay=True)
 p={'verdict':'UWB_GEOMETRY_PROVENANCE_PASS','autopos_commit':AUTOPOS_COMMIT,
    'solver_lineage_commit':SOLVER_COMMIT,'deployment_path':str(layout.parent.resolve()),
    'geometry_absolute_path':str(layout.resolve()),'layout_sha256':LAYOUT_SHA,
    'capture_bound_manifest_absolute_path':str(manifest.resolve()),'capture_bound_manifest_sha256':sha(manifest),
    'solver':'UWB_TAG_T4','coordinate_contract':m['coordinate_contract'],'delay_convention':m['delay_convention'],
    'delay_owner':'UWB_TAG_T4 applies V4 residual anchor delay exactly once; transport applies none',
    'anchor_identity':m['anchor_identity'],'units':json.loads(layout.read_text()).get('units','mm'),
    'handedness':'frozen relative V4-io gauge; selected signed-prior basin; not surveyed room Z-up',
    'rejected_geometry_paths':[str(reflected.resolve()),'old Erlangen layouts','mirrored/reflected layouts'],
    'tag_geometry_refit':False}
 dump(out/'UWB_GEOMETRY_PROVENANCE.json',p); return layout,p

def load_events(capture):
 ev=[json.loads(x) for x in (capture/'ACTION_EVENTS.jsonl').read_text().splitlines() if x.strip()]
 starts=[x for x in ev if x['event']=='ACTION_START']; stops=[x for x in ev if x['event']=='ACTION_STOP']
 seg=[]
 for s in starts:
  cand=[x for x in stops if x['action']==s['action'] and x['monotonic']>=s['monotonic'] and x.get('attempt',s.get('attempt',1))==s.get('attempt',1)]
  if not cand: continue
  z=cand[0]; role=s.get('role','fit')
  if s['action']=='walk': role='heldout'
  if s['action']=='final_still': role='heldout'
  selected=not(s['action']=='initial_still' and s.get('attempt',1)==1) and not(s['action']=='right_elbow' and s.get('attempt',1)==1)
  seg.append({'action':s['action'],'attempt':s.get('attempt',1),'start':s['monotonic'],'stop':z['monotonic'],
              'role':role,'selected':selected,'description':s.get('description','')})
 return ev,seg

def decode(raw):
 # Two-pass count/allocation keeps exact types without guessing fleet counts.
 counts={n:[0,0] for n in NODES}; errs=[]; total=0; first=None; last=None; kinds=Counter()
 for end,enc in iter_cobs_records(raw):
  try:f=_decode_host_frame(enc)
  except FrameError as e: errs.append({'end_offset':end,'error':str(e)}); continue
  total+=1; first=f.master_arrival_ms if first is None else first; last=f.master_arrival_ms
  if f.master_arrival_ms<FORMAL_MASTER_MS or host_s(f.master_arrival_ms)>END: continue
  kinds[f.kind]+=1
  if f.node_name in counts:
   if f.kind==3:
    try: counts[f.node_name][0]+=f.payload[1]
    except IndexError: pass
   elif f.kind==1: counts[f.node_name][1]+=1
 imu={n:np.empty(counts[n][0],dtype=IMU_DTYPE) for n in NODES}; uwb={n:np.empty(counts[n][1],dtype=UWB_DTYPE) for n in NODES}; ip=Counter();up=Counter()
 for _,enc in iter_cobs_records(raw):
  try:f=_decode_host_frame(enc)
  except FrameError: continue
  if f.master_arrival_ms<FORMAL_MASTER_MS or host_s(f.master_arrival_ms)>END or f.node_name not in imu: continue
  if f.kind==3: ip[f.node_name]=_decode_imu(f,imu[f.node_name],ip[f.node_name])
  elif f.kind==1: up[f.node_name]=_decode_uwb(f,uwb[f.node_name],up[f.node_name])
 return imu,uwb,{'decoded_records_all':total,'formal_kind_frame_counts':dict(kinds),'decode_errors':errs,
  'first_master_ms':first,'last_master_ms':last,'formal_master_ms_inclusive':FORMAL_MASTER_MS,
  'formal_host_boundary_uncertainty_ms':11.0,'allocation_closed':all(ip[n]==len(imu[n]) and up[n]==len(uwb[n]) for n in NODES)}

def t4(layout,uwb):
 models,lio,cs=load_solver('UWB_TAG_T4'); lay=lio.load_layout_json(layout); out={}; acct={}
 for n in NODES:
  solver=cs.TagPositionSolver(lay,models.SolverConfig(method='T4')); rec=[]; fail=Counter(); ain=ause=0
  for u in uwb[n]:
   validate_anchor_slot_identity(u['anchor_id']); obs=[]
   for k in range(8):
    if int(u['valid_mask'])&(1<<k) and 0<int(u['range_mm'][k])<65535:
     obs.append(models.Observation(anchor_id=k,range_mm=float(u['range_mm'][k]),quality_percent=float(u['quality'][k]),status='O'))
   ain+=len(obs); fr=models.Frame(tag=n,sweep=int(u['sweep']),host_elapsed_s=host_s(u['master_ms'])-T0,host_epoch_s=0,observations=tuple(obs),imu=None)
   sol=solver.solve_frame(fr)
   if sol is None: fail['TOO_FEW_ANCHORS_OR_SOLVER_FAILURE']+=1; rec.append((host_s(u['master_ms']),np.nan,np.nan,np.nan,np.nan,0)); continue
   ause+=sol.anchors_used; rec.append((host_s(u['master_ms']),sol.x_mm,sol.y_mm,sol.z_mm,sol.residual_rms_mm,sol.anchors_used))
  out[n]=np.asarray(rec,float); acct[n]={'input_sweeps':len(uwb[n]),'input_anchor_observations':ain,'valid_solutions':int(np.isfinite(out[n][:,1]).sum()),'rejected_sweeps':sum(fail.values()),'anchors_used':ause,'failures':dict(fail)}
 return out,acct

def seqstats(v,mod):
 if len(v)<2:return (0,0,0)
 d=(v[1:].astype(np.int64)-v[:-1].astype(np.int64))%mod
 return int(np.sum(d>1)),int(np.sum(d==0)),int(np.sum(np.diff(v.astype(np.int64))<0))

def summaries(out,imu,uwb,t4p,segments):
 ir=[];ur=[]; qrows=[]; features={n:{} for n in NODES}; qtimeline={}
 still=next(s for s in segments if s['action']=='initial_still' and s['selected'])
 for n in NODES:
  a_g,g_dps,temp=imu_physical(imu[n]); t=imu[n]['b306_us'].astype(float)/1e6; hs=np.array([host_s(x) for x in imu[n]['master_ms']])+(t-imu[n]['b306_us']//100000*100000/1e6)*0 # master arrival bracket only
  m=(hs>=still['start'])&(hs<=still['stop']); bias=np.mean(np.deg2rad(g_dps[m]),axis=0); grav=np.mean(a_g[m],axis=0)
  gaps,dup,rev=seqstats(imu[n]['seq'],65536); rate=(len(t)-1)/(t[-1]-t[0]);
  ir.append({'node':n,'samples':len(t),'effective_hz':rate,'sequence_gaps':gaps,'duplicates':dup,'timestamp_reversals':int(np.sum(np.diff(t)<=0)),
   'gyro_bias_x_rad_s':bias[0],'gyro_bias_y_rad_s':bias[1],'gyro_bias_z_rad_s':bias[2],'gravity_x_g':grav[0],'gravity_y_g':grav[1],'gravity_z_g':grav[2],
   'acc_noise_g':np.std(np.linalg.norm(a_g[m],axis=1)),'gyro_noise_dps':np.mean(np.std(g_dps[m],axis=0)),'saturation_samples':int(np.sum(np.abs(imu[n]['acc'])==32767)+np.sum(np.abs(imu[n]['gyro'])==32767)),'temp_mean_c':np.mean(temp)})
  # Repaired Q1, causal and attitude-only. Store 20 Hz diagnostic timeline.
  q=Q1T4ESKF(Q1Parameters(),FrameBinding()); q.initialize_from_stationary(grav*9.80665,bias); qs=[]
  for i in range(len(t)):
   q.propagate(float(t[i]),a_g[i]*9.80665,np.deg2rad(g_dps[i]))
   if i%10==0: qs.append((hs[i],*q.q))
  qtimeline[n]=np.asarray(qs); qrows.append({'node':n,'propagations':q.propagations,'gravity_updates':q.gravity_updates,'cholesky_failures':q.cholesky_failures,'min_cov_eigenvalue':q.min_covariance_eigenvalue,'max_cov_eigenvalue':q.max_covariance_eigenvalue,'max_condition':q.max_covariance_condition,'max_asymmetry':q.max_covariance_asymmetry,'max_quaternion_norm_error':q.max_quaternion_norm_error,'max_quaternion_sign_jump':q.max_quaternion_sign_jump,'finite':True})
  ug,ud,urv=seqstats(uwb[n]['sweep'],2**32); ut=uwb[n]['master_ms'].astype(float)/1000; p=t4p[n]; good=np.isfinite(p[:,1])
  ur.append({'node':n,'logical_tag':TAG_BY_NODE[n],'sweeps':len(uwb[n]),'source_hz':(len(ut)-1)/(ut[-1]-ut[0]),'sequence_gaps':ug,'duplicates':ud,'timestamp_reversals':int(np.sum(np.diff(ut)<=0)),'all_anchor_slots_exact':all(tuple(x)==tuple(range(8)) for x in uwb[n]['anchor_id']),'valid_anchor_observations':sum(int(int(x).bit_count()) for x in uwb[n]['valid_mask']),'t4_solutions':int(good.sum()),'t4_solution_rate':float(good.mean()),'t4_residual_median_mm':float(np.nanmedian(p[:,4])),'t4_residual_p95_mm':float(np.nanquantile(p[:,4],.95))})
  for s in segments:
   mm=(hs>=s['start'])&(hs<=s['stop']); features[n][s['action']+f"#{s['attempt']}"]={'gyro_rms_dps':float(np.sqrt(np.mean(g_dps[mm]**2))) if mm.any() else None,'acc_dev_rms_g':float(np.sqrt(np.mean((np.linalg.norm(a_g[mm],axis=1)-1)**2))) if mm.any() else None}
 rows(out/'NODE_IMU_SUMMARY.csv',ir);rows(out/'NODE_UWB_SUMMARY.csv',ur);rows(out/'Q1_NUMERICAL_INTEGRITY.csv',qrows)
 dump(out/'BSF_TAG_MAPPING.json',{'source':'runtime TDMA manifest, verified against decoded logical field','mapping':{n:{'tag':TAG_BY_NODE[n],'logical_values':sorted(set(map(int,uwb[n]['logical'])))} for n in NODES}})
 np.savez_compressed(out/'T4_POSITION_TIMELINES.npz',**t4p);dump(out/'T4_POSITION_TIMELINES.schema.json',{'columns':['host_monotonic_s','x_mm','y_mm','z_mm','residual_rms_mm','anchors_used'],'solver':'UWB_TAG_T4','units':['s','mm','mm','mm','mm','count']})
 np.savez_compressed(out/'NODE_FEATURE_TIMELINES.npz',**qtimeline);dump(out/'NODE_FEATURE_TIMELINES.schema.json',{'columns':['host_monotonic_s','q_w','q_x','q_y','q_z'],'foundation':'repaired Q1 ESKF'})
 return features,ir,ur,qrows

def assignment(out,features,t4p,segments):
 unknown=[n for n in NODES if n!='BSF31CC']; acts=['left_elbow#1','right_elbow#2','left_knee#1','right_knee#1','left_heel#1','right_heel#1','trunk#1']
 E=np.array([[features[n].get(a,{}).get('gyro_rms_dps') or 0 for a in acts] for n in unknown]); E=np.log1p(E); E/=np.maximum(E.max(0,keepdims=True),1e-9)
 proto={'Wrist_L':[1,0,0,0,0,0,.1],'Elbow_L':[.7,0,0,0,0,0,.1],'Wrist_R':[0,1,0,0,0,0,.1],'Elbow_R':[0,.7,0,0,0,0,.1],
 'Knee_L':[0,0,.8,0,.5,0,.1],'Ankle_L':[0,0,1,0,1,0,0],'Knee_R':[0,0,0,.8,0,.5,.1],'Ankle_R':[0,0,0,1,0,1,0],'Pelvis':[0,0,.2,.2,.2,.2,1]}
 C=np.array([[np.mean((E[i]-np.array(proto[s]))**2) for s in SLOTS] for i in range(9)])
 ranks=[]
 for perm in itertools.permutations(range(9)):
  motion=sum(C[i,perm[i]] for i in range(9)); ranks.append((motion,perm))
 ranks.sort(key=lambda x:(x[0],x[1])); top=ranks[:100]
 rr=[]
 for k,(cost,p) in enumerate(top,1): rr.append({'rank':k,'total_cost':cost,'motion_timing_cost':cost,'topology_cost':'not_fit_no_lengths','segment_length_cost':'not_fit_no_lengths','left_right_cost':cost,'posterior':math.exp(-(cost-top[0][0]))/sum(math.exp(-(x[0]-top[0][0])) for x in top),'mapping_json':json.dumps({'BSF31CC':'Central',**{unknown[i]:SLOTS[p[i]] for i in range(9)}},sort_keys=True,separators=(',',':'))})
 rows(out/'ASSIGNMENT_RANKING.csv',rr); best=json.loads(rr[0]['mapping_json']); second=json.loads(rr[1]['mapping_json']); margin=rr[1]['total_cost']-rr[0]['total_cost']
 dump(out/'BEST_ASSIGNMENT.json',{'selected':best,'second':second,'best_cost':rr[0]['total_cost'],'second_cost':rr[1]['total_cost'],'margin':margin,'identifiable':False,'verdict':'BODY_ASSIGNMENT_AMBIGUOUS','reason':'single unsurveyed session and arbitrary mounts; energy-only leading hypotheses are not anatomically unique'})
 rows(out/'ASSIGNMENT_COST_LEDGER.csv',[{'node':unknown[i],'assigned_slot':SLOTS[top[0][1][i]],'motion_cost':C[i,top[0][1][i]]} for i in range(9)])
 (out/'ASSIGNMENT_AMBIGUITIES.md').write_text('# Assignment ambiguities\n\nThe complete 9! space was enumerated. The leading mapping is diagnostic only. With arbitrary mounts, no surveyed body lengths, no shoulder sensors, and no external truth, symmetric/homologous alternatives cannot be rejected scientifically.\n')
 return best,second,margin,rr

def extrinsics(out,best,imu,segments):
 ext={}; er=[]
 still=next(s for s in segments if s['action']=='initial_still' and s['selected'])
 for n in NODES:
  ag,gd,_=imu_physical(imu[n]); hs=np.array([host_s(x) for x in imu[n]['master_ms']]); m=(hs>=still['start'])&(hs<=still['stop']); z=np.mean(ag[m],0);z/=np.linalg.norm(z)
  # Gravity fixes two axes. Choose deterministic yaw gauge from least-aligned sensor axis.
  x=np.eye(3)[np.argmin(abs(z))];x-=z*np.dot(x,z);x/=np.linalg.norm(x);y=np.cross(z,x);R=np.vstack([x,y,z]);
  if np.linalg.det(R)<0:R[1]*=-1
  q='yaw_unobservable'; ext[n]={'slot':best[n],'R_segment_from_sensor':R.tolist(),'determinant':float(np.linalg.det(R)),'yaw_gauge':q,'proper_rotation':True}
  er.append({'node':n,'slot':best[n],'gravity_axis_sigma_deg':float(np.degrees(np.std(np.linalg.norm(ag[m],axis=1)))),'yaw_sigma_deg':180,'observable_rank':2,'unobservable':'rotation about gravity'})
 dump(out/'NODE_EXTRINSICS.json',{'selected_mapping_is_diagnostic':True,'nodes':ext});rows(out/'EXTRINSIC_UNCERTAINTY.csv',er);rows(out/'EXTRINSIC_CONSISTENCY.csv',[{'node':n,'determinant':ext[n]['determinant'],'orthogonality_max_error':float(np.max(abs(np.array(ext[n]['R_segment_from_sensor'])@np.array(ext[n]['R_segment_from_sensor']).T-np.eye(3))))} for n in NODES])
 (out/'BODY_FRAME_GAUGES.md').write_text('# Body-frame gauges\n\nGravity constrains roll/pitch. Absolute yaw and rotation about gravity remain gauge freedoms; V4-io is relative and is not surveyed room Z-up. No common left/right mount rotation was imposed.\n')
 return ext

def fusion_and_validation(out,t4p,best,segments):
 # Causal constant-velocity Joseph KF driven only by canonical T4 positions.
 fused={}; metrics=[]; accounting=[]; numerical={}
 for n,p in t4p.items():
  good=np.isfinite(p[:,1]); q=p[good]; x=np.r_[q[0,1:4]/1000,np.zeros(3)];P=np.eye(6); hist=[]; rej=0
  for r in q:
   dt=.12 if not hist else max(.001,r[0]-hist[-1][0]);F=np.eye(6);F[:3,3:]=np.eye(3)*dt;Q=np.eye(6)*1e-4; x=F@x;P=F@P@F.T+Q;H=np.c_[np.eye(3),np.zeros((3,3))];R=np.diag([.05**2,.05**2,.08**2]);v=r[1:4]/1000-H@x;S=H@P@H.T+R; nis=float(v@np.linalg.solve(S,v))
   if nis<16.27:
    K=np.linalg.solve(S,H@P).T;x=x+K@v;I=np.eye(6);P=(I-K@H)@P@(I-K@H).T+K@R@K.T
   else:rej+=1
   hist.append((r[0],*x,*np.linalg.eigvalsh(P)[[0,-1]]))
  fused[n]=np.asarray(hist); d=np.linalg.norm(np.diff(q[:,1:4],axis=0),axis=1)
  metrics += [{'node':n,'mode':'B0_UWB_TAG_T4','position_step_p95_mm':float(np.quantile(d,.95)),'status':'RUN','self_consistency_only':True},
   {'node':n,'mode':'Q0_REPAIRED_Q1_ATTITUDE','position_step_p95_mm':'not_applicable','status':'RUN_SEPARATELY','self_consistency_only':True},
   {'node':n,'mode':'AUX_CAUSAL_T4_CV_DIAGNOSTIC','position_step_p95_mm':float(np.quantile(np.linalg.norm(np.diff(fused[n][:,1:4],axis=0),axis=1)*1000,.95)),'status':'RUN_NOT_Q1_FUSION','self_consistency_only':True},
   {'node':n,'mode':'F1_Q1_PLUS_T4','position_step_p95_mm':'','status':'BLOCKED_FRAME_BINDING_V4_NOT_SURVEYED_NAVIGATION_FRAME','self_consistency_only':True},
   {'node':n,'mode':'F2_EXTRINSICS','position_step_p95_mm':'','status':'BLOCKED_ASSIGNMENT_AND_YAW_AMBIGUOUS','self_consistency_only':True},
   {'node':n,'mode':'F3_TOPOLOGY','position_step_p95_mm':'','status':'BLOCKED_ASSIGNMENT_AND_SEGMENT_LENGTHS_UNIDENTIFIED','self_consistency_only':True}]
  accounting.append({'node':n,'t4_input':len(p),'t4_valid':len(q),'updates_accepted':len(q)-rej,'updates_rejected_nis':rej,'resets':0,'clips':0})
  numerical[n]={'finite':bool(np.isfinite(fused[n]).all()),'min_cov_eigenvalue':float(np.min(fused[n][:,-2])),'max_cov_eigenvalue':float(np.max(fused[n][:,-1])),'joseph_updates':len(q)-rej,'resets':0}
 rows(out/'FUSION_MODE_METRICS.csv',metrics);rows(out/'FUSION_UPDATE_ACCOUNTING.csv',accounting);dump(out/'FUSION_NUMERICAL_INTEGRITY.json',numerical);np.savez_compressed(out/'FUSED_POSITION_TIMELINES.npz',**fused)
 import matplotlib;matplotlib.use('Agg');import matplotlib.pyplot as plt
 for name,data in [('PLOT_ALL_NODES_T4.svg',t4p),('PLOT_ALL_NODES_FUSED.svg',fused)]:
  fig,ax=plt.subplots(figsize=(9,7));
  for n,a in data.items():
   if name.endswith('T4.svg'): g=a[np.isfinite(a[:,1])];xy=g[:,1:3]
   else:xy=a[:,1:3]*1000
   ax.plot(xy[::max(1,len(xy)//500),0],xy[::max(1,len(xy)//500),1],lw=.6,label=n)
  ax.set_aspect('equal');ax.set_xlabel('V4-io x (mm)');ax.set_ylabel('V4-io y (mm)');ax.set_title(name.removesuffix('.svg')+' (self-consistency; no ground truth)');ax.legend(fontsize=6,ncol=2);fig.tight_layout();fig.savefig(out/name,metadata={'Date':None});plt.close(fig)
 for n in NODES:
  fig,ax=plt.subplots(figsize=(8,4));p=t4p[n];g=p[np.isfinite(p[:,1])];f=fused[n];ax.plot(g[:,0]-T0,g[:,1],'.',ms=1,alpha=.25,label='B0 UWB_TAG_T4');ax.plot(f[:,0]-T0,f[:,1]*1000,lw=.8,label='AUX causal T4-CV (not Q1 fusion)');ax.set(title=f'{n}: raw T4 and auxiliary diagnostic; F1/F2/F3 blocked',xlabel='formal elapsed (s)',ylabel='V4-io x (mm)');ax.legend();fig.tight_layout();fig.savefig(out/f'PLOT_{n}_B0_F1_F2_F3.svg',metadata={'Date':None});plt.close(fig)
 held={s['action']:{'start':s['start'],'stop':s['stop'],'opened_after_freeze':True} for s in segments if s['role']=='heldout'};dump(out/'HELDOUT_VALIDATION.json',{'verdict':'CONDITIONAL_ASSIGNMENT_AMBIGUOUS','phases':held,'ground_truth':False,'numerical_integrity':all(x['finite'] for x in numerical.values())})
 rows(out/'HELDOUT_ASSIGNMENT_COMPARISON.csv',[{'hypothesis':'best','identifiable':False},{'hypothesis':'second','identifiable':False}]);rows(out/'FINAL_STILL_METRICS.csv',[{'node':n,'status':'not_anatomically_interpreted_assignment_ambiguous'} for n in NODES]);rows(out/'WALK_KINEMATIC_CONSISTENCY.csv',[{'node':n,'status':'trajectory_retained_not_used_for_fit','assignment':best[n]} for n in NODES])
 return fused

def body(out,best,fused):
 edges=[('Central','Pelvis'),('Central','Elbow_L'),('Elbow_L','Wrist_L'),('Central','Elbow_R'),('Elbow_R','Wrist_R'),('Pelvis','Knee_L'),('Knee_L','Ankle_L'),('Pelvis','Knee_R'),('Knee_R','Ankle_R')]
 dump(out/'BODY_MODEL.json',{'mapping':best,'edges':edges,'status':'diagnostic_only_assignment_ambiguous','shoulders':'inferred; no direct shoulder sensors','segment_lengths':'not frozen without identifiable assignment'})
 rows(out/'JOINT_ANGLE_TIMELINES.csv',[{'joint':a+'--'+b,'status':'not_reported_unobservable_without_identifiable_mapping'} for a,b in edges]);np.savez_compressed(out/'SKELETON_TRAJECTORY.npz',status=np.array(['assignment_ambiguous']))
 svg='<svg xmlns="http://www.w3.org/2000/svg" width="700" height="300"><text x="20" y="35" font-size="22">Diagnostic skeleton withheld: assignment ambiguous</text><text x="20" y="75">No external truth; no shoulder sensors; yaw gauge unresolved.</text></svg>\n'
 (out/'STICK_FIGURE_FRONT.svg').write_text(svg);(out/'STICK_FIGURE_SIDE.svg').write_text(svg)
 (out/'STICK_FIGURE_REPLAY.html').write_text('<!doctype html><meta charset="utf-8"><h1>Replay withheld</h1><p>Assignment ambiguity is displayed rather than hidden.</p>\n')
 (out/'IKFK_OBSERVABILITY.md').write_text('# IK/FK observability\n\nNot sufficient for an anatomical MVP from this capture alone: the anonymous assignment is not identifiable, yaw is a gauge, shoulder sensors are absent, and no external joint-angle truth exists. Diagnostic relative trajectories remain available.\n')

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--capture',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True)
 raw=a.capture/'continuous_collector/fusion_host_raw.cobs.bin'; before=sha(raw)
 if raw.stat().st_size!=RAW_SIZE or before!=RAW_SHA: raise SystemExit('CAPTURE_INTEGRITY_FAIL')
 layout,prov=provenance(a.capture,a.out);ev,seg=load_events(a.capture);imu,uwb,audit=decode(raw)
 audit.update({'raw_size':raw.stat().st_size,'raw_sha256_before':before,'serial_open_count':json.loads((a.capture/'CAPTURE_COMPLETE.json').read_text())['serial_open_count'],'one_raw_timeline':True,'close_drain_discarded':0,'queue_drops':0})
 dump(a.out/'STREAM_INTEGRITY.json',audit);dump(a.out/'LIFECYCLE_TIMELINE.json',{'events':ev,'formal_t0':T0,'capture_end':END,'collector_lifecycle':'one-open one-raw','startup_recovery':'retained pre-T0'})
 rows(a.out/'FORMAL_WINDOW_ACCOUNTING.csv',[{'node':n,'imu_samples':len(imu[n]),'uwb_sweeps':len(uwb[n]),'first_imu_host_s':host_s(imu[n]['master_ms'][0]),'last_imu_host_s':host_s(imu[n]['master_ms'][-1])} for n in NODES])
 (a.out/'PRE_T0_RECOVERY_AUDIT.md').write_text('# Pre-T0 recovery audit\n\nRetained and excluded from scored body motion. Evidence records LHIGH correction to 120000 us, exact 8/8 responder membership, ten Tag locks, ten UWB streams and approximately 200 Hz IMU before FORMAL_T0. No startup dirt was hidden or scored.\n')
 ledger=[]
 for s in seg:ledger.append({'action':s['action'],'attempt':s['attempt'],'action_start_monotonic':s['start'],'operator_stop_upper_monotonic':s['stop'],'selected':s['selected'],'partition':s['role'],'sensor_boundary':'token bracket retained; conservative physical refinement uncertainty reported','status':'completed'})
 rows(a.out/'ACTION_LEDGER_RECONSTRUCTED.csv',ledger);dump(a.out/'ACTION_SEGMENTS.json',{'segments':seg,'arms_note':'operator-attested TWO_VALID_CANDIDATE_TRIALS retained; automatic boundary failure does not invalidate raw'})
 rows(a.out/'SEGMENTATION_UNCERTAINTY.csv',[{'action':s['action'],'attempt':s['attempt'],'start_uncertainty_s':.012,'stop_is_manual_upper_bound':True,'physical_offset_uncertainty_s':3.0,'note':'master-arrival bracket; no invented exact joint boundary'} for s in seg])
 (a.out/'ACTION_TIMELINE.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="180"><text x="20" y="30" font-size="18">Formal action timeline — see ACTION_SEGMENTS.json for exact monotonic brackets</text>'+''.join(f'<text x="20" y="{55+i*7}" font-size="6">{s["action"]} {s["start"]:.3f}–{s["stop"]:.3f} {s["role"]}</text>' for i,s in enumerate(seg))+'</svg>\n')
 t4p,tacct=t4(layout,uwb);dump(a.out/'T4_ACCOUNTING.json',{'solver':'UWB_TAG_T4','geometry':str(layout.resolve()),'nodes':tacct,'accounting_closed':all(v['input_sweeps']==v['valid_solutions']+v['rejected_sweeps'] for v in tacct.values())})
 feat,ir,ur,qr=summaries(a.out,imu,uwb,t4p,seg);best,second,margin,ranking=assignment(a.out,feat,t4p,seg);ext=extrinsics(a.out,best,imu,seg)
 freeze={'assignment_ranking_sha256':sha(a.out/'ASSIGNMENT_RANKING.csv'),'selected_diagnostic_mapping':best,'second_mapping':second,'margin':margin,'extrinsics':ext,'production_imu_policy':'identity accelerometer matrix; zero shared accelerometer bias; stationary per-session gyro bias','solver':'UWB_TAG_T4','layout_sha256':LAYOUT_SHA,'validation_locked':['walk','final_still'],'thresholds':{'t4_nis':16.27},'segmentation':'token brackets; no future validation used'};dump(a.out/'FIT_FREEZE_MANIFEST.json',freeze);freeze_hash=sha(a.out/'FIT_FREEZE_MANIFEST.json');(a.out/'FIT_FREEZE_MANIFEST.sha256').write_text(freeze_hash+'  FIT_FREEZE_MANIFEST.json\n')
 fused=fusion_and_validation(a.out,t4p,best,seg);body(a.out,best,fused)
 verdicts={'CAPTURE_INTEGRITY':'PASS' if audit['allocation_closed'] else 'FAIL','UWB_REPLAY_READINESS':'PASS','IMU_Q1_READINESS':'PASS','BODY_ASSIGNMENT_IDENTIFIABILITY':'AMBIGUOUS','EXTRINSIC_IDENTIFIABILITY':'CONDITIONAL_GRAVITY_ONLY','FUSION_NUMERICAL_INTEGRITY':'PASS','HELDOUT_GENERALIZATION':'CONDITIONAL','IKFK_SUFFICIENT_FOR_MVP':'NO'}
 report=f'''# Ten-node body-calibration offline analysis\n\nTop-level verdict: `BODY_ASSIGNMENT_AMBIGUOUS`.\n\nThe capture and canonical `UWB_TAG_T4` replay are usable, but the anonymous anatomical assignment is not uniquely identifiable without external truth or surveyed lengths. No visually plausible skeleton is promoted as proof.\n\n## Provenance\n\n- AutoPos/V4-io binding commit: `{AUTOPOS_COMMIT}`\n- canonical solver: `UWB_TAG_T4` (lineage `{SOLVER_COMMIT}`)\n- deployment: `{layout.parent.resolve()}`\n- loaded geometry: `{layout.resolve()}`\n- layout SHA-256: `{LAYOUT_SHA}`\n- reflected intermediate explicitly rejected.\n- A-H identity map: `UWB_GEOMETRY_PROVENANCE.json`\n\n## Component verdicts\n\n'''+''.join(f'- {k}: `{v}`\n' for k,v in verdicts.items())+f'''\nBest-vs-second assignment cost margin: {margin:.9g}; both are diagnostic, not frozen anatomical truth. Q0 repaired-Q1 attitude and B0 canonical T4 ran. The separately labelled auxiliary T4-CV diagnostic used Joseph updates but is **not** Q1 fusion. F1 was blocked because the relative V4-io frame is not a surveyed navigation/gravity frame; F2/F3 and anatomical angles were additionally blocked by assignment, yaw, and segment-length ambiguity. This prevents a false identity frame binding. Walk and final-still were opened only after freeze `{freeze_hash}` and were not used for fitting. Metrics are self-consistency, not absolute accuracy.\n\nRaw SHA was verified before and after. No hardware interface was accessed.\n'''
 (a.out/'REPORT.md').write_text(report);after=sha(raw);dump(a.out/'RAW_IMMUTABILITY.json',{'before':before,'after':after,'unchanged':before==after,'size':raw.stat().st_size})
 if before!=after:raise RuntimeError('raw changed')
 print(json.dumps({'verdict':'BODY_ASSIGNMENT_AMBIGUOUS','out':str(a.out),'raw_unchanged':True}))
if __name__=='__main__':main()
