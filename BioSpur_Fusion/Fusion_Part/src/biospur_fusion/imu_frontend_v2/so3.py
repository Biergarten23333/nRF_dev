import numpy as np
def skew(v):
 x,y,z=np.asarray(v,float);return np.array([[0,-z,y],[z,0,-x],[-y,x,0]],float)
def qnormalize(q):
 q=np.asarray(q,float);n=np.linalg.norm(q)
 if not np.isfinite(n) or n<1e-15:raise ValueError("invalid quaternion")
 return q/n
def qmul(a,b):
 w,x,y,z=a;W,X,Y,Z=b
 return np.array([w*W-x*X-y*Y-z*Z,w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W])
def qexp(rotvec):
 v=np.asarray(rotvec,float);a=np.linalg.norm(v)
 if a<1e-10:return qnormalize(np.r_[1-a*a/8,v*(0.5-a*a/48)])
 return np.r_[np.cos(a/2),v*np.sin(a/2)/a]
def q_to_R(q):
 w,x,y,z=qnormalize(q)
 return np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
def q_from_two_vectors(a,b):
 a=np.asarray(a,float);b=np.asarray(b,float);a/=np.linalg.norm(a);b/=np.linalg.norm(b);d=float(a@b)
 if d<-1+1e-12:
  axis=np.cross(a,[1.,0,0]) if abs(a[0])<0.9 else np.cross(a,[0.,1,0]);axis/=np.linalg.norm(axis);return np.r_[0.,axis]
 return qnormalize(np.r_[1+d,np.cross(a,b)])
def angle_between(q1,q2):
 d=abs(float(qnormalize(q1)@qnormalize(q2)));return 2*np.arccos(np.clip(d,-1,1))
