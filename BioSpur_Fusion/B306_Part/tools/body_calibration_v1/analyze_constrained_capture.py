#!/usr/bin/env python3
"""Constrained continuation of the ten-node body calibration, offline only."""
from __future__ import annotations
import argparse,csv,hashlib,itertools,json,math,sys
from pathlib import Path
import numpy as np
HERE=Path(__file__).resolve(); REPO=HERE.parents[3]; TOOLS=REPO/'B306_Part/tools';sys.path.insert(0,str(TOOLS));sys.path.insert(0,str(HERE.parent))
import analyze_completed_capture as base
from v47_real_data_adapter import imu_physical
from v47_q1_eskf import Q1T4ESKF,Q1Parameters,FrameBinding

PAIRS=(('elbow','BSFAA61','BSF1120'),('wrist','BSFB165','BSFEC35'),('ankle','BSF6C53','BSF8BC4'))
FIXED={'BSF31CC':'Central','BSFC2CC':'Pelvis','BSF44AD':'Knee_L','BSF3C79':'Knee_R'}
ACTIONS={'elbow':('left_elbow#1','right_elbow#2','arms#1'), 'wrist':('left_elbow#1','right_elbow#2','arms#1'),
         'ankle':('left_knee#1','right_knee#1','left_heel#1','right_heel#1','squats#1')}
PROV={'BSF31CC':'OPERATOR_CONFIRMED','BSFC2CC':'OPERATOR_CONFIRMED','BSF44AD':'OPERATOR_CONFIRMED','BSF3C79':'OPERATOR_CONFIRMED',
      'BSFAA61':'SEGMENT_OPERATOR_CONFIRMED_LR_DATA_INFERRED','BSF1120':'SEGMENT_OPERATOR_CONFIRMED_LR_DATA_INFERRED',
      'BSFB165':'SEGMENT_OPERATOR_CONFIRMED_LR_DATA_INFERRED','BSFEC35':'SEGMENT_OPERATOR_CONFIRMED_LR_DATA_INFERRED',
      'BSF6C53':'SEGMENT_CONSTRAINED_ELIMINATION_LR_DATA_INFERRED','BSF8BC4':'SEGMENT_CONSTRAINED_ELIMINATION_LR_DATA_INFERRED'}
RAW_SHA=base.RAW_SHA

def dump(p,o):base.dump(p,o)
def rows(p,d,f=None):base.rows(p,d,f)
def norm(v):
 v=np.asarray(v,float);q=np.linalg.norm(v)
 if q<1e-9:raise ValueError('BODY_FRAME_DEGENERATE')
 return v/q
def medpos(p,start,stop):
 m=(p[:,0]>=start)&(p[:,0]<=stop)&np.isfinite(p[:,1]);return np.median(p[m,1:4],axis=0)

def action_energy(imu,segments):
 out={n:{} for n in base.NODES}
 for n in base.NODES:
  _,g,_=imu_physical(imu[n]);hs=np.array([base.host_s(x) for x in imu[n]['master_ms']])
  for s in segments:
   if not s['selected'] or s['role']!='fit':continue
   m=(hs>=s['start'])&(hs<=s['stop']);out[n][s['action']+f"#{s['attempt']}"]=float(np.sqrt(np.mean(g[m]**2))) if m.any() else 0
 return out

def score_hypotheses(energy,drop=None):
 ranked=[]
 for bits in itertools.product((0,1),repeat=3):
  mapping=dict(FIXED); ledger=[];total=0
  for bit,(name,a,b) in zip(bits,PAIRS):
   left,right=(a,b) if bit==0 else (b,a);mapping[left]=name.title()+'_L';mapping[right]=name.title()+'_R'
   for act in ACTIONS[name]:
    if act==drop:continue
    if act.startswith('left_'): c=max(0.,energy[right][act]-energy[left][act]) + .25*max(0.,energy[left][act]-energy[right][act])*-1
    elif act.startswith('right_'): c=max(0.,energy[left][act]-energy[right][act]) + .25*max(0.,energy[right][act]-energy[left][act])*-1
    else:c=-.05*(energy[left][act]+energy[right][act])
    ledger.append({'pair':name,'action':act,'contribution':c});total+=c
  ranked.append({'bits':bits,'mapping':mapping,'cost':total,'ledger':ledger})
 return sorted(ranked,key=lambda x:(x['cost'],x['bits']))

def body_frame(t4p,mapping,segments):
 tp=next(s for s in segments if s['action']=='t_pose' and s['selected']);st=next(s for s in segments if s['action']=='initial_still' and s['selected'])
 pos={mapping[n]:medpos(t4p[n],tp['start'],tp['stop']) for n in mapping}
 lateral=[]
 for l,r in [('Wrist_L','Wrist_R'),('Elbow_L','Elbow_R'),('Knee_L','Knee_R'),('Ankle_L','Ankle_R')]:lateral.append(pos[r]-pos[l])
 x=norm(np.mean([norm(v) for v in lateral],axis=0));ank=.5*(pos['Ankle_L']+pos['Ankle_R']);vertical=[pos['Pelvis']-ank,pos['Central']-pos['Pelvis']];z0=norm(np.mean([norm(v) for v in vertical],axis=0));z=norm(z0-x*np.dot(x,z0));y=norm(np.cross(z,x))
 # Labelled knee raise versus heel-to-butt resolves forward sign.
 def disp(slot,action):
  s=next(q for q in segments if q['action']==action and q['selected']);node=next(n for n,v in mapping.items() if v==slot);p=t4p[node];return medpos(p,s['start'],s['stop'])-medpos(p,st['start'],st['stop'])
 sign_signal=np.dot(.5*(disp('Knee_L','left_knee')+disp('Knee_R','right_knee'))-.5*(disp('Ankle_L','left_heel')+disp('Ankle_R','right_heel')),y)
 # With left->right x and anatomical-up z fixed, right-handed forward y is
 # unique.  A labelled-motion sign is consistency evidence, not permission to
 # flip y alone (which would also invert z on re-orthogonalisation).
 R=np.vstack([x,y,z]);orth=float(np.max(abs(R@R.T-np.eye(3))));det=float(np.linalg.det(R))
 lr_angles=[math.degrees(math.acos(np.clip(np.dot(norm(v),x),-1,1))) for v in lateral];v_angles=[math.degrees(math.acos(np.clip(np.dot(norm(v),z),-1,1))) for v in vertical]
 return R,{'R_body_from_V4':R.tolist(),'R_V4_from_body':R.T.tolist(),'determinant':det,'orthogonality_max_error':orth,'origin_V4_mm':pos['Pelvis'].tolist(),
  'lateral_pair_angle_rms_deg':float(np.sqrt(np.mean(np.square(lr_angles)))),'vertical_chain_angle_rms_deg':float(np.sqrt(np.mean(np.square(v_angles)))),
  'forward_motion_consistency_signal_mm':float(sign_signal),'construction':'shared rigid frame; x=aggregate left-to-right, z=ankle-midpoint→Pelvis→Central orthogonalized to x, y=z×x; labelled knee-versus-heel motion is reported as consistency evidence and cannot flip one axis alone',
  'external_accuracy':False,'geometry_refit':False}

def replay_f1(imu,t4p,R,origin,out):
 trajectories={};qonly={};account=[];numerical={}
 # 24 samples = one 120 ms TDMA period: exact repaired discretization at observation cadence.
 pars=Q1Parameters(covariance_period_samples=24,t4_position_sigma_m=np.array([.08,.08,.10]))
 bind=FrameBinding(R_V4_N=R.T,origin_V4_m=origin/1000,provenance='SESSION_BODY_FRAME_FROM_FROZEN_TPOSE_STATIC',v4_navigation_rotation_valid=True,spatial_dynamics_enabled=True,yaw_gauge='DATA_RESOLVED_WITH_UNCERTAINTY',yaw_sigma_rad=math.radians(20))
 for n in base.NODES:
  ag,gd,_=imu_physical(imu[n]);t=imu[n]['b306_us'].astype(float)/1e6;hs=np.array([base.host_s(x) for x in imu[n]['master_ms']]);init=hs<hs[0]+5;f=Q1T4ESKF(pars,bind);f.initialize_from_stationary(np.mean(ag[init],0)*9.80665,np.mean(np.deg2rad(gd[init]),0));u=t4p[n];ui=0;hist=[];qh=[]
  for i in range(len(t)):
   f.propagate(t[i],ag[i]*9.80665,np.deg2rad(gd[i]));
   while ui<len(u) and u[ui,0]<=hs[i]:
    if np.isfinite(u[ui,1]):f.t4_position_update(u[ui,1:4]/1000)
    ui+=1
   if i%10==0:hist.append((hs[i],*f.p,*f.v,*f.q));qh.append((hs[i],*f.q))
  trajectories[n]=np.asarray(hist);qonly[n]=np.asarray(qh);numerical[n]={'finite':bool(np.isfinite(trajectories[n]).all()),'cholesky_failures':f.cholesky_failures,'min_covariance_eigenvalue':f.min_covariance_eigenvalue,'max_condition':f.max_covariance_condition,'t4_updates':f.t4_updates,'resets':f.reinitializations}
  account.append({'node':n,'imu_samples':len(t),'t4_input':len(u),'t4_valid':int(np.isfinite(u[:,1]).sum()),'t4_updates':f.t4_updates,'unused_t4_after_last_imu':len(u)-ui,'resets':f.reinitializations})
 np.savez_compressed(out/'F1_Q1_T4_BODY_TRAJECTORIES.npz',**trajectories);dump(out/'F1_NUMERICAL_INTEGRITY.json',numerical);rows(out/'F1_OBSERVATION_ACCOUNTING.csv',account)
 return trajectories,numerical

def heldout(out,traj,t4p,mapping,segments,freeze_hash):
 res=[]
 for phase in ('walk','final_still'):
  s=next(x for x in segments if x['action']==phase and x['selected'])
  for n,a in traj.items():
   m=(a[:,0]>=s['start'])&(a[:,0]<=s['stop']);speed=np.linalg.norm(a[m,4:7],axis=1)
   res.append({'phase':phase,'node':n,'samples':int(m.sum()),'speed_median_mps':float(np.median(speed)),'speed_p95_mps':float(np.quantile(speed,.95)),'external_accuracy':False})
 rows(out/'HELDOUT_METRICS.csv',res);dump(out/'HELDOUT_VALIDATION.json',{'freeze_sha256':freeze_hash,'opened_after_freeze':True,'windows':['walk','final_still'],'tuning_after_open':False,'metrics':'self-consistency only; no external ground truth'})

def plots(out,t4p,traj):
 import matplotlib;matplotlib.use('Agg');matplotlib.rcParams['svg.hashsalt']='biospur-constrained-body-v1';import matplotlib.pyplot as plt
 for mode,data in [('B0_UWB_TAG_T4',t4p),('F1_Q1_T4_BODY',traj)]:
  fig,ax=plt.subplots(figsize=(9,7))
  for n,a in data.items():
   if mode.startswith('B0'):m=np.isfinite(a[:,1]);xy=a[m,1:3]
   else:xy=a[:,1:3]*1000
   ax.plot(xy[::max(1,len(xy)//600),0],xy[::max(1,len(xy)//600),1],lw=.6,label=n)
  ax.set_aspect('equal');ax.set(title=mode+' — self-consistency, not external accuracy',xlabel='x (mm)',ylabel='y (mm)');ax.legend(fontsize=6,ncol=2);fig.tight_layout();fig.savefig(out/(mode+'.svg'),metadata={'Date':None});plt.close(fig)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--capture',type=Path,required=True);ap.add_argument('--out',type=Path,required=True);a=ap.parse_args();a.out.mkdir(parents=True,exist_ok=True);raw=a.capture/'continuous_collector/fusion_host_raw.cobs.bin';before=base.sha(raw)
 if before!=RAW_SHA:raise SystemExit('CAPTURE_INTEGRITY_FAIL')
 layout,provenance=base.provenance(a.capture,a.out);events,segments=base.load_events(a.capture);imu,uwb,audit=base.decode(raw);t4p,tacct=base.t4(layout,uwb);energy=action_energy(imu,segments)
 np.savez_compressed(a.out/'B0_UWB_TAG_T4_TIMELINES.npz',**t4p);dump(a.out/'ACTION_SEGMENTS.json',{'segments':segments})
 ranked=score_hypotheses(energy);best=ranked[0];second=ranked[1];loo=[]
 for act in sorted(set(sum((list(v) for v in ACTIONS.values()),[]))):
  r=score_hypotheses(energy,act);loo.append({'removed_action':act,'best_bits':''.join(map(str,r[0]['bits'])),'best_cost':r[0]['cost'],'same_as_full_best':r[0]['bits']==best['bits']})
 rr=[]
 for i,h in enumerate(ranked,1):rr.append({'rank':i,'bits_elbow_wrist_ankle':''.join(map(str,h['bits'])),'cost':h['cost'],'margin_from_best':h['cost']-best['cost'],'mapping_json':json.dumps(h['mapping'],sort_keys=True,separators=(',',':'))})
 rows(a.out/'LR_HYPOTHESIS_RANKING.csv',rr);rows(a.out/'LR_ACTION_CONTRIBUTIONS.csv',[{'hypothesis_bits':''.join(map(str,h['bits'])),**x} for h in ranked for x in h['ledger']]);rows(a.out/'LR_LEAVE_ONE_ACTION_OUT.csv',loo)
 mapping=best['mapping'];pair_stability={name:all((row['best_bits'][i]==str(best['bits'][i])) for row in loo if row['removed_action'] in ACTIONS[name]) for i,(name,_,_) in enumerate(PAIRS)}
 constrained={n:{'slot':mapping[n],'provenance':PROV[n],'lr_status':'DATA_INFERRED_STABLE' if '_LR_DATA_INFERRED' in PROV[n] and pair_stability[mapping[n].split('_')[0].lower()] else ('LR_AMBIGUOUS' if '_LR_DATA_INFERRED' in PROV[n] else 'NOT_APPLICABLE')} for n in base.NODES}
 dump(a.out/'CONSTRAINED_MAPPING.json',{'mapping':constrained,'anonymous_assignment_validated':False,'best_cost':best['cost'],'second_cost':second['cost'],'margin':second['cost']-best['cost'],'complete_space':8,'pair_stability':pair_stability})
 if not all(pair_stability.values()):verdict='BODY_LR_ASSIGNMENT_AMBIGUOUS'
 else:verdict='BODY_MAPPING_CONSTRAINED_PASS_FUSION_READY'
 R,bf=body_frame(t4p,mapping,segments);dump(a.out/'SESSION_BODY_FRAME_MANIFEST.json',bf)
 if bf['vertical_chain_angle_rms_deg']>35 or bf['lateral_pair_angle_rms_deg']>35:verdict='BODY_FRAME_DEGENERATE'
 q0_path=a.capture/'analysis_body_calibration_v1/run_c/NODE_FEATURE_TIMELINES.npz';q0_ref={'path':str(q0_path.resolve()),'sha256':base.sha(q0_path),'foundation':'repaired Q1 attitude-only; mapping-independent historical derivation'};dump(a.out/'Q0_REPAIRED_Q1_REFERENCE.json',q0_ref)
 freeze={'mapping':constrained,'lr_ranking_sha256':base.sha(a.out/'LR_HYPOTHESIS_RANKING.csv'),'body_frame':bf,'operator_constraints':'post-capture attestation','validation_locked':['walk','final_still'],'f1_parameters':{'covariance_period_samples':24,'t4_sigma_m':[.08,.08,.10]},'q0_reference':q0_ref,'solver':'UWB_TAG_T4','layout_sha256':base.LAYOUT_SHA};dump(a.out/'CONSTRAINED_FIT_FREEZE_MANIFEST.json',freeze);fh=base.sha(a.out/'CONSTRAINED_FIT_FREEZE_MANIFEST.json')
 traj,num=replay_f1(imu,t4p,R,np.array(bf['origin_V4_mm']),a.out);finite=all(x['finite'] and x['cholesky_failures']==0 for x in num.values());
 if not finite:verdict='FUSION_NUMERICAL_FAIL'
 heldout(a.out,traj,t4p,mapping,segments,fh);plots(a.out,t4p,traj)
 # F2/F3 remain preliminary: topology has no approved anthropometric lengths.
 joints=[('Central','Elbow_L','MVP_APPROX_NO_SHOULDER'),('Central','Elbow_R','MVP_APPROX_NO_SHOULDER'),('Elbow_L','Wrist_L','OBSERVABLE_RELATIVE'),('Elbow_R','Wrist_R','OBSERVABLE_RELATIVE'),('Pelvis','Knee_L','OBSERVABLE_RELATIVE'),('Pelvis','Knee_R','OBSERVABLE_RELATIVE'),('Knee_L','Ankle_L','OBSERVABLE_RELATIVE'),('Knee_R','Ankle_R','OBSERVABLE_RELATIVE')]
 rows(a.out/'JOINT_ANGLE_OBSERVABILITY.csv',[{'parent':x,'child':y,'status':z,'clinical_validity':False} for x,y,z in joints]);dump(a.out/'F2_F3_STATUS.json',{'F2':'CONDITIONAL_NO_APPROVED_SEGMENT_LENGTHS','F3':'PRELIMINARY_OBSERVABLE_JOINTS_ONLY','shoulder_nodes':False,'direct_shoulder_measurement':False,'clinical_angles':False})
 report=f'''# Constrained body-calibration continuation\n\nTop-level verdict: `{verdict}`.\n\nThis result preserves the earlier anonymous analysis unchanged. Operator evidence fixes segment classes; only three binary left/right swaps were optimized over all eight combinations. Inferred sides are not operator-confirmed.\n\nBest bits (elbow,wrist,ankle; 0=expected ordering): `{''.join(map(str,best['bits']))}`; best cost `{best['cost']:.9g}`, second `{second['cost']:.9g}`, margin `{second['cost']-best['cost']:.9g}`. Pair leave-one-action stability: `{pair_stability}`.\n\nCanonical `UWB_TAG_T4` uses `{layout.resolve()}` SHA `{base.LAYOUT_SHA}` and AutoPos binding commit `{base.AUTOPOS_COMMIT}`. A single proper `R_body←V4` was estimated from frozen T-Pose/static geometry; anchors were not refit and trajectories were not independently rotated. B0, Q0 and F1 ran separately. F2 remains conditional without approved lengths. F3 is preliminary only for observable relative joints; shoulders are approximated through Central→Elbow and are neither directly measured nor clinically valid. Held-out walk/final_still opened only after freeze `{fh}`. All reported metrics are self-consistency, not external accuracy.\n\nRaw SHA verified before and after; no hardware accessed.\n''';(a.out/'REPORT.md').write_text(report);after=base.sha(raw);dump(a.out/'RAW_IMMUTABILITY.json',{'before':before,'after':after,'unchanged':before==after});print(json.dumps({'verdict':verdict,'out':str(a.out)}))
if __name__=='__main__':main()
