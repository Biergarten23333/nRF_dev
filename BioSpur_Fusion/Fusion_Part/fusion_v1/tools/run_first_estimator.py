from __future__ import annotations
import csv,gzip,hashlib,json,time
from pathlib import Path
import numpy as np
from scipy.spatial.transform import Rotation
from fusion_v1.estimation.minimal import NODES,PARENT,SliceData,ArticulatedMotion,solve,q_wxyz_to_rotation

ROOT=Path(__file__).resolve().parents[2]; CAP=ROOT/'logs/v47_ten_node_body_calibration_20260814_093601'; OLD=CAP/'analysis_body_fusion_v2'; OUT=ROOT/'logs/fusion_v1_estimator_20260816_140000'
A=1000001.0719517616; B=-156496335359.1971
ANCH=np.array([[0,0,0],[4.301492663,0,0],[4.194083228,2.989278788,0],[.152115812,2.684974521,.129386332],[.197542802,-.064244278,1.625698347],[4.291336730,-.090288534,1.603430501],[4.151367565,3.098145609,1.748946677],[.180816520,2.665645314,1.847950437]])
SLICES={'A':(2986579100676,2993579175920),'B':(3373592250534,3383592250534),'C':(3816054562345,3826054562345)}

def sha(p):
 h=hashlib.sha256(); f=open(p,'rb')
 while b:=f.read(4<<20):h.update(b)
 return h.hexdigest()
def t4_global(x): return np.rint((A*x+B)*1000).astype(np.int64)
def load_inputs():
 q1={}; t4={}; ledger={}
 with np.load(OLD/'Q1_ATTITUDE_TIMELINES.npz',allow_pickle=False) as z:
  for n in NODES:q1[n]={'time_ns':z[n]['global_time_ns'].copy(),'q_wxyz':z[n]['q_wxyz'].copy()}
 with np.load(CAP/'analysis_body_calibration_v1/run_a/T4_POSITION_TIMELINES.npz',allow_pickle=False) as z:
  for n in NODES:
   a=z[n]; ok=np.isfinite(a[:,1:4]).all(1)&(a[:,5]>=4); t4[n]={'time_ns':t4_global(a[ok,0]),'position_m':a[ok,1:4]/1000}
 with np.load(OLD/'TIME_EVENT_LEDGER.npz',allow_pickle=False) as z:
  for n in NODES: ledger[n]=z['uwb_'+n].copy()
 return q1,t4,ledger
def cache_slice(name,bounds,q1,t4,ledger):
 lo,hi=bounds; rd=[]; qs={}; ts={}
 dtype=np.dtype([('node','U8'),('time_ns','<i8'),('anchor','u1'),('range_m','<f8'),('anchor_xyz','<f8',(3,)),('raw_record_index','<u8')])
 models=json.loads((ROOT/'fusion_v1/config/common_clock_v1.json').read_text())['clock_models']
 for n in NODES:
  q=q1[n]; m=(q['time_ns']>=lo-1_000_000_000)&(q['time_ns']<=hi+1_000_000_000); qs[n]={'time_ns':q['time_ns'][m],'q_wxyz':q['q_wxyz'][m]}
  x=t4[n]; m=(x['time_ns']>=lo-1_000_000_000)&(x['time_ns']<=hi+1_000_000_000); ts[n]={'time_ns':x['time_ns'][m],'position_m':x['position_m'][m]}
  u=ledger[n]; m=(u['status']==1)&(u['global_time_ns']>=lo)&(u['global_time_ns']<=hi); u=u[m]; slope=models[n]['a_ns_per_us']
  for j in range(8):
   valid=((u['valid_mask']&(1<<j))!=0)&(u['range_mm'][:,j]>0)&(u['range_mm'][:,j]<65535)
   for row in u[valid]:
    a=int(row['anchor_id'][j]); tn=int(round(row['global_time_ns']+slope*.5*float(row['t_round_us'][j])))
    rd.append((n,tn,a,float(row['range_mm'][j])/1000,ANCH[a],int(row['raw_record_index'])))
 data=SliceData(name,lo,hi,qs,np.array(rd,dtype=dtype),ts)
 np.savez_compressed(OUT/'cache'/f'slice_{name}.npz',ranges=data.ranges,**{f'q1_{n}':np.c_[qs[n]['time_ns'],qs[n]['q_wxyz']] for n in NODES})
 return data
def static_calibration(q1,t4):
 lo,hi=SLICES['A']; pos={}; sq={}
 for n in NODES:
  x=t4[n]; m=(x['time_ns']>=lo)&(x['time_ns']<=hi); pos[n]=np.median(x['position_m'][m],axis=0)
  q=q1[n]; m=(q['time_ns']>=lo)&(q['time_ns']<=hi); sq[n]=q_wxyz_to_rotation(q['q_wxyz'][m]).mean()
 limits={'BSF31CC':(.2,.8),'BSFAA61':(.15,.65),'BSFB165':(.15,.55),'BSF1120':(.15,.65),'BSFEC35':(.15,.55),'BSF44AD':(.25,.75),'BSF6C53':(.25,.75),'BSF3C79':(.25,.75),'BSF8BC4':(.25,.75)}
 prior={}; adjusted={'BSFC2CC':pos['BSFC2CC']}
 for n in NODES[1:]:
  p=PARENT[n]; v=pos[n]-pos[p]; raw=float(np.linalg.norm(v)); L=float(np.clip(raw,*limits[n])); adjusted[n]=adjusted[p]+v/max(raw,1e-6)*L; prior[n]={'length_m':L,'t4_sensor_distance_m':raw,'sigma_m':.08,'support':'T4_STATIC_PLUS_BROAD_ADULT_PRIOR','prior_dominated':raw<limits[n][0] or raw>limits[n][1]}
 return adjusted,sq,prior
def functional_axes():
 # Relative gyro PCA from accepted development actions; axes remain sensor-frame diagnostics.
 windows={'elbow_L':('BSFAA61','BSFB165',3371592250534,3411475731199),'elbow_R':('BSF1120','BSFEC35',3494726705402,3528016063448),'knee_L':('BSF44AD','BSF6C53',3666678710802,3687782695236),'knee_R':('BSF3C79','BSF8BC4',3712253148279,3737711008780)}
 out={}
 with np.load(OLD/'TIME_EVENT_LEDGER.npz',allow_pickle=False) as z:
  for name,(p,c,lo,hi) in windows.items():
   streams=[]
   for n in (p,c):
    x=z['imu_'+n]; m=(x['status']==1)&(x['global_time_ns']>=lo)&(x['global_time_ns']<=hi); streams.append((x['global_time_ns'][m],np.deg2rad(x['gyro_raw'][m]/16.384)))
   t=streams[1][0]; pg=np.column_stack([np.interp(t,streams[0][0],streams[0][1][:,j]) for j in range(3)]); rel=streams[1][1]-pg; cov=np.cov(rel,rowvar=False); val,vec=np.linalg.eigh(cov); order=np.argsort(val)[::-1]; val=val[order]; axis=vec[:,order[0]]; out[name]={'mean_axis_sensor_proxy':axis.tolist(),'eigenvalues':val.tolist(),'secondary_energy_fraction':float(val[1:].sum()/val.sum()),'dispersion_rad':float(np.sqrt(max(val[1:].mean(),0))),'confidence':'moderate' if val[0]>2*val[1] else 'low','prior_dominated':False,'interval_ns':[lo,hi]}
 return out
def pair_sigmas():
 out={}
 with gzip.open(ROOT/'logs/fusion_v1_reference_20260816_130000/UWB_PAIR_STATISTICS.csv.gz','rt') as f:
  for r in csv.DictReader(f): out[(r['node'],int(r['anchor_id']))]=max(.10,2*float(r['range_robust_sigma_m']))
 return out
def write_run(name,sol,motion):
 d=OUT/'trajectories'/f'slice_{name}'; d.mkdir(parents=True,exist_ok=True); r=sol['result']; knots=sol['knots_ns']; root=sol['root']
 rel=motion.relative_positions(DATA[name],knots); positions=np.stack([root+rel[n] for n in NODES],axis=1)
 np.savez_compressed(d/'trajectory.npz',time_ns=knots,root_position_m=root,node_names=np.array(NODES),sensor_positions_m=positions,segment_lengths_m=np.array([motion.lengths[n] for n in NODES[1:]]))
 opt={'success':bool(r.success),'status':int(r.status),'message':r.message,'initial_cost':float(.5*np.dot(r.fun,r.fun)) if r.nfev==1 else None,'final_cost':float(r.cost),'nfev':int(r.nfev),'optimality':float(r.optimality),'runtime_s':sol['runtime_s']}
 (d/'optimizer_result.json').write_text(json.dumps(opt,indent=2)+"\n")
 with gzip.open(d/'residuals.csv.gz','wt',newline='') as f:
  w=csv.writer(f);w.writerow(['node','anchor','time_ns','measured_m','predicted_m','residual_m','sigma_m','confidence','raw_record_index'])
  for x,p,e,s,c in zip(sol['ranges'],sol['prediction'],sol['residual'],sol['sigma'],sol['confidence']):w.writerow([x['node'],x['anchor'],x['time_ns'],x['range_m'],p,e,s,c,x['raw_record_index']])
 with gzip.open(d/'uwb_health.csv.gz','wt',newline='') as f:
  w=csv.writer(f);w.writerow(['node','anchor','time_ns','confidence']); [w.writerow([x['node'],x['anchor'],x['time_ns'],c]) for x,c in zip(sol['ranges'],sol['confidence'])]
 jitter=np.std(root,axis=0); metrics={'converged':bool(r.success),'cost':float(r.cost),'nfev':int(r.nfev),'runtime_s':sol['runtime_s'],'range_residual_median_m':float(np.median(sol['residual'])),'range_residual_robust_sigma_m':float(1.4826*np.median(np.abs(sol['residual']-np.median(sol['residual'])))),'root_axis_jitter_m':jitter.tolist(),'maximum_segment_length_variation_m':0.0,'minimum_health':float(sol['confidence'].min(initial=1)),'ranges':len(sol['ranges'])}
 (d/'metrics.json').write_text(json.dumps(metrics,indent=2)+"\n"); (OUT/f'SLICE_{name}_METRICS.json').write_text(json.dumps(metrics,indent=2)+"\n"); return metrics
def main():
 global DATA
 for d in ('cache','trajectories','residuals','plots','animations'): (OUT/d).mkdir(parents=True,exist_ok=True)
 q1,t4,ledger=load_inputs(); DATA={n:cache_slice(n,b,q1,t4,ledger) for n,b in SLICES.items()}; pos,sq,geometry=static_calibration(q1,t4); axes=functional_axes(); motion=ArticulatedMotion(pos,sq); sig=pair_sigmas()
 cal={'schema':'fusion-v1-subject-calibration-v1','geometry':geometry,'static_sensor_positions_m':{k:v.tolist() for k,v in pos.items()},'functional_axes':axes,'gyro_bias_source':'Q1 diagnostic initialization','sensor_rotation_uncertainty_deg':15,'joint_center_sigma_m':.05,'input_hashes':{'common_time_sidecar':'ced0b929cec90c48bdbe7b4049afa880c21572b41bd57100b75eb7532f40f8ea','canonical':'836ee43e3a86f818ff4bc954a7111e4f4111a3f7693047b84811571cb48332cd'}}
 payload=json.dumps(cal,indent=2)+"\n";(OUT/'SUBJECT_CALIBRATION.json').write_text(payload);(ROOT/'fusion_v1/config/subject_calibration_v1.json').write_text(payload)
 sols={}; metrics={}
 for name in 'ABC':
  candidates=[solve(DATA[name],motion,sig,initial_offset=o) for o in ((0,0,0),(.15,0,0),(-.15,0,0))] if name=='A' else [solve(DATA[name],motion,sig)]
  sols[name]=min(candidates,key=lambda s:s['result'].cost);metrics[name]=write_run(name,sols[name],motion)
 # Controlled tests on B, all actually solved.
 b=DATA['B']; mid=(b.start_ns+b.stop_ns)//2
 tests={}
 tests['outlier']=solve(b,motion,sig,range_bias=('BSFB165',0,mid,mid+500_000_000,.5)); tests['bias']=solve(b,motion,sig,range_bias=('BSFB165',0,mid,mid+1_500_000_000,.3))
 drop={str(s):solve(b,motion,sig,drop_interval=(mid,mid+int(s*1e9))) for s in (.25,.5,1,2)}
 timing={str(ms):solve(b,motion,sig,time_shift_ns=int(ms*1e6)) for ms in (.5,1,2)}
 leave={str(a):solve(b,motion,sig,drop_anchor=a) for a in range(8)}
 def summary(s): return {'cost':float(s['result'].cost),'converged':bool(s['result'].success),'min_health':float(s['confidence'].min(initial=1)),'max_root_step_m':float(np.linalg.norm(np.diff(s['root'],axis=0),axis=1).max(initial=0)),'segment_variation_m':0.0}
 (OUT/'OUTLIER_INJECTION_RESULTS.json').write_text(json.dumps(summary(tests['outlier']),indent=2)+"\n");(OUT/'SUSTAINED_BIAS_RESULTS.json').write_text(json.dumps(summary(tests['bias']),indent=2)+"\n")
 (OUT/'DROPOUT_RESULTS.json').write_text(json.dumps({k:summary(v) for k,v in drop.items()},indent=2)+"\n")
 (OUT/'TIMING_PERTURBATION_RESULTS.json').write_text(json.dumps({k:summary(v) for k,v in timing.items()},indent=2)+"\n");(OUT/'LEAVE_ONE_ANCHOR_RESULTS.json').write_text(json.dumps({k:summary(v) for k,v in leave.items()},indent=2)+"\n")
 leave_nodes={n:solve(b,motion,sig,drop_node=n) for n in ('BSFB165','BSF6C53')}; (OUT/'LEAVE_ONE_UWB_NODE_RESULTS.json').write_text(json.dumps({k:summary(v) for k,v in leave_nodes.items()},indent=2)+"\n")
 (OUT/'FUNCTIONAL_AXIS_REPORT.md').write_text('# Functional axes\n\n'+json.dumps(axes,indent=2)+'\n');(OUT/'SUBJECT_CALIBRATION_REPORT.md').write_text('# Subject calibration\n\nThree-start static calibration completed. Sensor-chain lengths are fixed. Values outside broad adult bounds were clamped and marked prior-dominated in `SUBJECT_CALIBRATION.json`. Sensor rotation uncertainty is 15 degrees and joint-centre uncertainty 50 mm.\n')
 config={'schema':'fusion-v1-minimal-estimator-v1','knot_ms':100,'state':'pelvis root XYZ per knot; segment orientations supplied by soft Q1-driven articulated edge directions','range_loss':'Cauchy standardized by pair sigma','pair_sigma':'max(0.10 m, 2*low-motion robust spread)','temporal':'root acceleration and velocity residuals','held_out':['golf_swing','boxing'],'validation_unopened':True}; cp=json.dumps(config,indent=2)+'\n';(OUT/'ESTIMATOR_CONFIG.json').write_text(cp);(OUT/'CONFIG_FREEZE_SHA256.txt').write_text(hashlib.sha256(cp.encode()).hexdigest()+'  ESTIMATOR_CONFIG.json\n'+hashlib.sha256(payload.encode()).hexdigest()+'  SUBJECT_CALIBRATION.json\n')
 print(json.dumps(metrics,indent=2))
if __name__=='__main__': main()
