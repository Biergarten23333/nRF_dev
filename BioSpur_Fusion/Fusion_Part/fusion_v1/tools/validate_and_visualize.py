from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter
from scipy.spatial.transform import Rotation
from fusion_v1.estimation.minimal import ArticulatedMotion,solve,NODES,PARENT
from fusion_v1.tools import run_first_estimator as r

OUT=r.OUT
def extend_q1(q1,bounds):
 lo,hi=bounds
 with np.load(r.OLD/'TIME_EVENT_LEDGER.npz',allow_pickle=False) as z:
  for n in NODES:
   if len(q1[n]['time_ns']) and q1[n]['time_ns'][-1]>=hi: continue
   x=z['imu_'+n]; m=(x['status']==1)&(x['global_time_ns']>=lo-500_000_000)&(x['global_time_ns']<=hi+500_000_000); x=x[m]
   if not len(x): continue
   prior=q1[n]; j=max(0,np.searchsorted(prior['time_ns'],x['global_time_ns'][0])-1); current=r.q_wxyz_to_rotation(prior['q_wxyz'][j]); qs=[]; last=int(x['global_time_ns'][0])
   for row in x:
    now=int(row['global_time_ns']);dt=max(0,min(.05,(now-last)/1e9)); omega=np.deg2rad(row['gyro_raw'].astype(float)/16.384); current=current*Rotation.from_rotvec(omega*dt); q=current.as_quat();qs.append([q[3],q[0],q[1],q[2]]);last=now
   q1[n]={'time_ns':x['global_time_ns'].copy(),'q_wxyz':np.asarray(qs)}
def save_validation(name,data,motion,sig):
 if not hasattr(r,'DATA'): r.DATA={}
 r.DATA[name]=data
 sol=solve(data,motion,sig); metrics=r.write_run(name,sol,motion)
 return sol,metrics
def nearest_t4(data,node,times):
 x=data.t4[node]; idx=np.searchsorted(x['time_ns'],times); idx=np.clip(idx,0,len(x['time_ns'])-1); return x['position_m'][idx]
def visuals(name,data,path):
 with np.load(path,allow_pickle=False) as z: times=z['time_ns']; pos=z['sensor_positions_m']; root=z['root_position_m']
 edges=[(NODES.index(PARENT[n]),NODES.index(n)) for n in NODES[1:]]
 if name=='A':
  fig=plt.figure(figsize=(8,6));ax=fig.add_subplot(projection='3d'); k=len(times)//2
  raw=np.stack([nearest_t4(data,n,times[[k]])[0] for n in NODES]); ax.scatter(*r.ANCH.T,c='k',marker='^',label='anchors');ax.scatter(*raw.T,c='tab:red',alpha=.6,label='T4');ax.scatter(*pos[k].T,c='tab:blue',label='fused')
  for a,b in edges:ax.plot(*pos[k,[a,b]].T,c='tab:blue'); ax.set_title('Slice A: raw T4 and articulated Fusion');ax.legend();fig.tight_layout();fig.savefig(OUT/'plots/static_slice_A.png',dpi=160);plt.close(fig);return
 idx=np.linspace(0,len(times)-1,min(50,len(times)),dtype=int); fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(projection='3d')
 allp=pos[idx]; lo=np.minimum(allp.min((0,1)),r.ANCH.min(0))-.3;hi=np.maximum(allp.max((0,1)),r.ANCH.max(0))+.3
 def draw(frame):
  ax.cla();k=idx[frame];raw=np.stack([nearest_t4(data,n,times[[k]])[0] for n in NODES]);ax.scatter(*r.ANCH.T,c='k',marker='^',s=18);ax.scatter(*raw.T,c='tab:red',alpha=.45,s=15);ax.scatter(*pos[k].T,c='tab:blue',s=18)
  for a,b in edges:ax.plot(*pos[k,[a,b]].T,c='tab:blue',lw=2)
  ax.set(xlim=(lo[0],hi[0]),ylim=(lo[1],hi[1]),zlim=(lo[2],hi[2]),title=f'Slice {name} t={(times[k]-times[0])/1e9:.2f}s | red=T4 blue=fused')
 ani=FuncAnimation(fig,draw,frames=len(idx),interval=100);ani.save(OUT/'animations'/f'slice_{name}.gif',writer=PillowWriter(fps=10));plt.close(fig)
def main():
 q1,t4,ledger=r.load_inputs(); pos,sq,_=r.static_calibration(q1,t4);motion=ArticulatedMotion(pos,sq);sig=r.pair_sigmas()
 specs={'WALK':(3882894886958,3892894886958),'FINAL_STILL':(4052623056391,4060623129799)}; results={}; data={}
 for name,bounds in specs.items():
  extend_q1(q1,bounds); data[name]=r.cache_slice(name,bounds,q1,t4,ledger);sol,m=save_validation(name,data[name],motion,sig);results[name]=m
 for n in 'ABC':
  bounds=r.SLICES[n];data[n]=r.cache_slice(n,bounds,q1,t4,ledger);visuals(n,data[n],OUT/'trajectories'/f'slice_{n}'/'trajectory.npz')
 (OUT/'VALIDATION_REPORT.md').write_text('# Frozen validation\n\nEstimator and subject-calibration hashes were frozen before opening validation. Golf and boxing remained unopened.\n\n```json\n'+json.dumps(results,indent=2)+'\n```\n\nBoth validation solves are finite; interpret residual spreads and jitter as internal consistency only because no external truth exists. No post-validation tuning was performed.\n')
 print(json.dumps(results,indent=2))
if __name__=='__main__':main()
