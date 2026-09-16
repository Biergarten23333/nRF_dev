"""Missing-segment swing coordinates; measured distal rotations stay immutable.

The unknown trajectories are the torso and proximal bone directions, not fast
elbow angles that must cancel measured forearm motion. Axial rotation of an
unmeasured proximal segment is a display convention, not an observed quantity.
"""
import numpy as np
from .model import rotation_vectors

STATE_SIZE=11
LOW=np.r_[-np.array([1.2,1.2,1.5]),np.full(8,-2.5)]
HIGH=-LOW


def orientations(states,retained,axes=None):
    torso=retained[...,0,:,:]@rotation_vectors(states[...,:3])
    swing=np.zeros(states.shape[:-1]+(4,3))
    swing[...,:2]=states[...,3:].reshape(states.shape[:-1]+(4,2))
    base=np.stack((torso,torso,retained[...,0,:,:],retained[...,0,:,:]),axis=-3)
    return torso,base@rotation_vectors(swing)


def joints(states,retained,calibration,height,halfwidth):
    torso,proximal=orientations(states,retained)
    length=calibration['lengths'];result=[np.zeros(states.shape[:-1]+(3,))]
    for k in range(4):
        arm,sign=k<2,1 if k%2==0 else -1
        base=(torso if arm else retained[...,0,:,:])@np.array(
            [0.,sign*(length['shoulder_width']/2 if arm else halfwidth),height if arm else 0.])
        mid=base-length['upper_arm' if arm else 'thigh']*proximal[...,k,:,2]
        tip=mid-length['forearm' if arm else 'shank']*retained[...,k+1,:,2]
        result.extend((base,mid,tip))
    return np.stack(result,axis=-2)


def anatomical_residual(states,retained,calibration):
    _,proximal=orientations(states,retained)
    up=proximal[...,:,2];distal=retained[...,1:,:,2]
    cosine=np.sum(up*distal,axis=-1)
    limit=np.maximum(np.cos(np.deg2rad(155.))-cosine,0.)/.03
    axes=np.asarray(calibration['hinge_axes'])[2:]
    world_axis=np.einsum('nkij,kj->nki',retained[:,3:],axes)
    plane=np.sum(up[:,2:]*world_axis,axis=-1)/.1
    signed=np.sum(np.cross(up[:,2:],distal[:,2:])*world_axis,axis=-1)
    hyperextension=np.minimum(signed,0.)/.1
    return np.column_stack((limit,plane,hyperextension))
