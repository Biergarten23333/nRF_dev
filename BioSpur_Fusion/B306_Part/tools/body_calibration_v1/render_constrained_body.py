#!/usr/bin/env python3
"""Post-freeze diagnostic renderer; visual interpolation never enters analysis."""
from __future__ import annotations
import argparse,json,math,subprocess,sys
from pathlib import Path
import numpy as np
from PIL import Image,ImageDraw,ImageFont

W,H=960,540; FPS=30; FULL_SPEED=20.0
COLORS=['#ff5a5f','#00a8e8','#ffbf00','#7bd389','#b388eb','#f78fb3','#70a1ff','#eccc68','#2ed573','#ff7f50']
EDGES=[('Central','Pelvis',False),('Central','Elbow_L',True),('Elbow_L','Wrist_L',False),('Central','Elbow_R',True),('Elbow_R','Wrist_R',False),('Pelvis','Knee_L',False),('Knee_L','Ankle_L',False),('Pelvis','Knee_R',False),('Knee_R','Ankle_R',False)]

def load_npz(p):
 z=np.load(p);return {k:z[k] for k in z.files}
def interp(a,t,cols):
 x=a[:,0];out=[]
 for c in cols:
  m=np.isfinite(x)&np.isfinite(a[:,c]);out.append(np.interp(t,x[m],a[m,c]))
 return np.array(out)
def quat_R(q):
 q=np.asarray(q,float);q/=np.linalg.norm(q);w,x,y,z=q
 return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
def dashed(draw,a,b,fill,width=2):
 a=np.array(a,float);b=np.array(b,float)
 for i in range(0,12,2):
  p=a+(b-a)*i/12;q=a+(b-a)*(i+1)/12;draw.line((*p,*q),fill=fill,width=width)

class Scene:
 def __init__(self,root):
  self.root=root;self.t4=load_npz(root/'B0_UWB_TAG_T4_TIMELINES.npz');self.f1=load_npz(root/'F1_Q1_T4_BODY_TRAJECTORIES.npz')
  qref=json.loads((root/'Q0_REPAIRED_Q1_REFERENCE.json').read_text());self.q0=load_npz(Path(qref['path']))
  mp=json.loads((root/'CONSTRAINED_MAPPING.json').read_text())['mapping'];self.node_slot={n:v['slot'] for n,v in mp.items()};self.slot_node={v:k for k,v in self.node_slot.items()}
  bf=json.loads((root/'SESSION_BODY_FRAME_MANIFEST.json').read_text());self.R=np.array(bf['R_body_from_V4']);self.origin=np.array(bf['origin_V4_mm'])
  self.segments=json.loads((root/'ACTION_SEGMENTS.json').read_text())['segments'];self.nodes=sorted(self.t4)
  pts=np.concatenate([self.body_t4(n,a[np.isfinite(a[:,1]),1:4]) for n,a in self.t4.items()]);lo=np.quantile(pts,.01,axis=0);hi=np.quantile(pts,.99,axis=0);c=(lo+hi)/2;span=max(np.max(hi-lo),1800);self.lo=c-span*.62;self.hi=c+span*.62
 def body_t4(self,n,p):return (self.R@(np.asarray(p)-self.origin).T).T
 def positions(self,t,source='t4'):
  out={}
  for n in self.nodes:
   if source=='t4':v=interp(self.t4[n],t,[1,2,3]);out[self.node_slot[n]]=self.body_t4(n,v)
   else:out[self.node_slot[n]]=interp(self.f1[n],t,[1,2,3])*1000
  return out
 def project(self,p,panel=(0,0,W,H)):
  # Fixed isometric camera in session-body coordinates.
  az,el=math.radians(-55),math.radians(22);x,y,z=p;u=math.cos(az)*x-math.sin(az)*y;v=-math.sin(el)*(math.sin(az)*x+math.cos(az)*y)+math.cos(el)*z
  scale=min(panel[2],panel[3])/(self.hi[0]-self.lo[0]);return (panel[0]+panel[2]/2+u*scale,panel[1]+panel[3]/2-v*scale)
 def frame(self,t,mode,title):
  im=Image.new('RGB',(W,H),'#08111f');d=ImageDraw.Draw(im);d.text((18,12),title,fill='white');d.text((18,32),f'host-monotonic {t:.3f} s  |  visual interpolation only',fill='#aab7c4')
  source='f1' if mode=='comparison' else 't4';pos=self.positions(t,source)
  if mode in ('stick','comparison'):
   for a,b,approx in EDGES:
    pa,pb=self.project(pos[a]),self.project(pos[b]);dashed(d,pa,pb,'#ffcc66',3) if approx else d.line((*pa,*pb),fill='#b9c6d3',width=3)
   d.text((18,H-42),'Central->Elbow dashed: UNMEASURED_SHOULDER_APPROXIMATION',fill='#ffcc66')
  for i,n in enumerate(self.nodes):
   slot=self.node_slot[n];p=self.project(pos[slot]);c=COLORS[i];d.ellipse((p[0]-5,p[1]-5,p[0]+5,p[1]+5),fill=c);d.text((p[0]+7,p[1]-7),slot,fill=c)
   if mode=='raw':
    a=self.t4[n];m=(a[:,0]>=t-4)&(a[:,0]<=t)&np.isfinite(a[:,1]);tail=self.body_t4(n,a[m,1:4]);xy=[self.project(x) for x in tail]
    if len(xy)>1:d.line(xy,fill=c,width=2)
  if mode=='comparison':
   # Q0 attitude triads are diagnostic and not used to place nodes.
   for n in self.nodes:
    slot=self.node_slot[n];p=pos[slot];q=interp(self.q0[n],t,[1,2,3,4]);axis=quat_R(q)[:,0]*120
    d.line((*self.project(p),*self.project(p+axis)),fill='#ffffff',width=2)
   d.text((18,54),'B0 T4: input | stick: frozen topology | white arrows: Q0 attitude | positions: F1 Q1+T4',fill='#d8e2eb')
  d.text((W-300,H-22),'SELF-CONSISTENCY — NO EXTERNAL TRUTH',fill='#ff8c8c');return im

def encode(scene,path,start,stop,mode,title,speed=1.0):
 path.parent.mkdir(parents=True,exist_ok=True);duration=(stop-start)/speed;n=max(1,int(duration*FPS));cmd=['ffmpeg','-y','-loglevel','error','-f','rawvideo','-pix_fmt','rgb24','-s',f'{W}x{H}','-r',str(FPS),'-i','-','-vf','scale=1920:1080:flags=lanczos','-c:v','libx264','-preset','veryfast','-crf','22','-pix_fmt','yuv420p','-movflags','+faststart',str(path)]
 p=subprocess.Popen(cmd,stdin=subprocess.PIPE)
 try:
  for i in range(n):p.stdin.write(scene.frame(start+i/FPS*speed,mode,title).tobytes())
 finally:p.stdin.close();rc=p.wait()
 if rc:raise RuntimeError(f'ffmpeg failed {path}')
def gif(scene,path,start,stop,mode,title,speed=4.0):
 frames=[];n=min(80,max(12,int((stop-start)/speed*8)))
 for i in range(n):frames.append(scene.frame(start+(stop-start)*i/max(1,n-1),mode,title).resize((480,270)))
 frames[0].save(path,save_all=True,append_images=frames[1:],duration=125,loop=0,optimize=True)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--analysis',type=Path,required=True);a=ap.parse_args();s=Scene(a.analysis);out=a.analysis/'visualizations';start=min(x[0,0] for x in s.t4.values());stop=max(x[-1,0] for x in s.t4.values())
 mains=[('BODY_T4_ALL_NODES_3D','raw'),('BODY_T4_STICK_FIGURE_3D','stick'),('BODY_TRAJECTORY_COMPARISON','comparison')]
 for name,mode in mains:encode(s,out/(name+'.mp4'),start,stop,mode,name,FULL_SPEED);gif(s,out/(name+'_PREVIEW.gif'),start,min(stop,start+120),mode,name,8)
 wanted=[('T_POSE','t_pose'),('LEFT_ARM','left_elbow'),('RIGHT_ARM','right_elbow'),('LEFT_LEG','left_knee'),('RIGHT_LEG','right_knee'),('WALK','walk'),('FINAL_STILL','final_still')]
 clips=[]
 for label,act in wanted:
  candidates=[x for x in s.segments if x['action']==act and x['selected']];seg=candidates[-1];p=out/f'ACTION_{label}.mp4';encode(s,p,seg['start'],seg['stop'],'comparison',f'{label} — frozen analysis',1);clips.append({'label':label,'path':p.name,'start':seg['start'],'stop':seg['stop']})
 # Highlights: selected 4 s excerpts, concatenated without changing timestamps displayed.
 tmp=[]
 for i,c in enumerate(clips):
  seg=next(x for x in s.segments if x['action']==wanted[i][1] and x['selected']);p=out/f'.highlight_{i}.mp4';encode(s,p,seg['start'],min(seg['stop'],seg['start']+4),'comparison',f'HIGHLIGHT {c["label"]}',1);tmp.append(p)
 listfile=out/'.highlights.txt';listfile.write_text(''.join(f"file '{p.name}'\n" for p in tmp));subprocess.run(['ffmpeg','-y','-loglevel','error','-f','concat','-safe','0','-i',str(listfile),'-c','copy',str(out/'BODY_CALIBRATION_HIGHLIGHTS.mp4')],check=True);gif(s,out/'BODY_CALIBRATION_HIGHLIGHTS_PREVIEW.gif',start,min(stop,start+60),'comparison','BODY CALIBRATION HIGHLIGHTS',6)
 for p in tmp:p.unlink()
 listfile.unlink()
 manifest={'diagnostic_visualization_only':True,'analysis_freeze_sha256':__import__('hashlib').sha256((a.analysis/'CONSTRAINED_FIT_FREEZE_MANIFEST.json').read_bytes()).hexdigest(),'resolution':[1920,1080],'fps':30,'codec':'H.264','pixel_format':'yuv420p','fixed_view':True,'visual_interpolation_only':True,'visual_missing_solution_rule':'interpolate only between finite canonical T4 solutions; never written back to analysis','full_capture_playback_speed':FULL_SPEED,'clips':clips,'shoulder_contract':'Central->Elbow dashed; UNMEASURED_SHOULDER_APPROXIMATION','files':sorted(p.name for p in out.iterdir() if p.suffix in ('.mp4','.gif'))};(out/'VISUALIZATION_MANIFEST.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n');print(json.dumps({'videos':len([x for x in out.iterdir() if x.suffix=='.mp4']),'out':str(out)}))
if __name__=='__main__':main()
