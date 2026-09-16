"""Five-node anatomical frames identified from signed calibration motion.

A standing reset supplies gravity, but not a pelvis lateral axis. Registered
trunk pitch supplies that missing direction. No removed-node orientation is
used to identify a retained node's sensor-to-body transformation.
"""
import numpy as np
from scipy.spatial.transform import Rotation
from .inputs import NODES
from .calibration import matrices,_gyro_axis


def signed_functional_frame(rows,initial_rotation,first_motion_sign):
    axis,evidence=_gyro_axis(rows,0.,30.)
    rotations=matrices(rows).as_matrix()
    excursions=Rotation.from_matrix(rotations[0].T@rotations).as_rotvec()@axis
    crossing=np.flatnonzero(abs(excursions)>.15)
    if not len(crossing):raise ValueError('no signed functional excursion')
    index=int(crossing[0])
    axis*=np.sign(excursions[index])*first_motion_sign
    up=np.asarray(initial_rotation).T[:,2]
    lateral=axis-up*(axis@up)
    if np.linalg.norm(lateral)<.8:raise ValueError('functional pitch axis conflicts with gravity')
    lateral/=np.linalg.norm(lateral)
    forward=np.cross(lateral,up)
    mounting=np.column_stack((forward,lateral,up))
    initial_body=np.asarray(initial_rotation)@mounting
    yaw=-np.arctan2(initial_body[1,0],initial_body[0,0])
    evidence.update(first_excursion_s=float(rows[index,0]-rows[0,0]),
        first_excursion_threshold_rad=.15,first_motion_sign=first_motion_sign,
        longitudinal_axis_source='initial natural standing gravity approximation',
        lateral_axis_sensor=lateral.tolist(),forward_axis_sensor=forward.tolist(),
        world_yaw_deg=float(np.rad2deg(yaw)))
    return mounting,float(yaw),evidence


def calibrate_functional_frames(episodes,calibration):
    c=calibration
    evidence={}
    for i,name,first_sign in ((0,'14_trunk_flex_extend',1.),
                             (3,'10_knee_left_seated',-1.),(4,'11_knee_right_seated',-1.)):
        rows=episodes[name][NODES[i]]['imu']
        mount,yaw,audit=signed_functional_frame(rows,c['initial_sensor_rotations'][i],first_sign)
        c['segment_axes_in_sensor'][i]=mount.tolist()
        c['functional_yaw_rad'][i]=yaw
        evidence[NODES[i]]=dict(episode=name,**audit)
        if i>0:c['hinge_axes'][i-1]=[0.,1.,0.]
    c['five_node_functional_frames']=evidence
    return c
