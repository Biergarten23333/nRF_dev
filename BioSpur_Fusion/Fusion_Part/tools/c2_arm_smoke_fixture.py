"""Independent analytic arm rotations for a conditional mounting smoke test.

This is not raw six-axis navigation, whole-body FK, or an IMUCoCo benchmark.
Only five retained orientations and wrist gyros are exposed to the estimator.
True mounts are returned separately for assessment only.
"""
import numpy as np
from scipy.spatial.transform import Rotation
from biospur_fusion.c2_sparse_nodes.inputs import NODES


def fixture(coupled=False, seed=41):
    rng = np.random.default_rng(seed)
    t = np.arange(6000)/200
    # Standing convention: sensor -Y points down, left/right sensor -Z
    # points left/right. Columns map body forward/left/up into sensor axes.
    nominal = np.array([[[1,0,0],[0,0,1],[0,-1,0]],
                        [[-1,0,0],[0,0,1],[0,1,0]]])
    mounts = nominal@Rotation.from_euler('xyz', [[12,-8,5],[-9,11,-7]], degrees=True).as_matrix()
    heading = np.deg2rad([14,-11])
    names = ('00_initial_still','02_t_pose','04_shoulder_left','05_shoulder_right',
             '06_elbow_left','07_elbow_right')
    actions = {}
    for k,name in enumerate(names):
        root = Rotation.from_euler('z', (.05*np.sin(.6*t))[:,None]).as_matrix()
        chest = root.copy()
        if coupled:
            movement = np.column_stack((.06*np.sin(.7*t+k), .05*np.sin(.5*t),
                                         .12*np.sin(.4*t+k)+.08))
            chest = root@Rotation.from_euler('xyz', movement).as_matrix()
        rotations = np.repeat(root[:,None],5,axis=1)
        gyros = np.zeros((len(t),5,3))
        for limb in range(2):
            arm = np.tile(np.eye(3),(len(t),1,1))
            side = 1 if limb==0 else -1
            if name == '02_t_pose':
                angle = side*(np.pi/2-(.07+.03*np.sin(.4*t) if coupled else 0.))
                arm = Rotation.from_euler('x', np.broadcast_to(angle,t.shape)[:,None]).as_matrix()
            elif name == ('04_shoulder_left' if limb==0 else '05_shoulder_right'):
                angle = side*(.7-.7*np.cos(2*np.pi*t/6))
                arm = Rotation.from_euler('x',angle[:,None]).as_matrix()
            elif name == ('06_elbow_left' if limb==0 else '07_elbow_right'):
                early = t<15
                bend = .8-.7*np.cos(2*np.pi*t/3)
                arm[early] = Rotation.from_euler('y',-bend[early,None]).as_matrix()
                late_bend = np.pi/2+(.07*np.sin(.5*t[~early]) if coupled else 0.)
                arm[~early] = (Rotation.from_euler('y',-np.broadcast_to(late_bend,t[~early].shape)[:,None]).as_matrix()
                              @Rotation.from_euler('z',(.9*np.sin(2*np.pi*(t[~early]-15)/3))[:,None]).as_matrix())
            physical = chest@arm@mounts[limb].T
            omega = Rotation.from_matrix(physical[:-1].transpose(0,2,1)@physical[1:]).as_rotvec()*200
            gyros[:-1,limb+1] = omega
            gyros[-1,limb+1] = omega[-1]
            gyros[:,limb+1] += rng.normal(0,.003,(len(t),3))
            rotations[:,limb+1] = Rotation.from_euler('z',-heading[limb]).as_matrix()@physical
        episode = {}
        for i,node in enumerate(NODES):
            quat = Rotation.from_matrix(rotations[:,i]).as_quat()[:,[3,0,1,2]]
            rows = np.column_stack((t,quat,np.zeros((len(t),3)),gyros[:,i]))
            episode[node] = {'imu':rows}
        actions[name] = episode
    return actions, dict(mounts=mounts, heading=heading)
