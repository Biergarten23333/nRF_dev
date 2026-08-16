import numpy as np
from scipy.spatial.transform import Rotation
def uwb_range(sensor,anchor,measured): return float(np.linalg.norm(np.asarray(sensor)-anchor)-measured)
def orientation(predicted:Rotation,observed:Rotation): return (predicted.inv()*observed).as_rotvec()
def joint_center(parent_point,child_point,sigma_m): return (np.asarray(parent_point)-child_point)/sigma_m
def dominant_axis(omega,axis,sigma):
 axis=np.asarray(axis)/np.linalg.norm(axis); return (np.asarray(omega)-np.dot(omega,axis)*axis)/sigma

