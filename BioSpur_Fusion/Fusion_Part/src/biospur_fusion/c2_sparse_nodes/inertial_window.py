"""Offline multi-frame IK with measured IMU acceleration and segment lengths.

Unlike the legacy frame prior, this objective compares FK's second derivative
at the *sensor* against specific force. Window boundaries carry pose and
velocity information; action labels never reset the state or select a pose.
"""
from __future__ import annotations

import time
import numpy as np
from scipy import sparse
from scipy.optimize import least_squares
from scipy.interpolate import BSpline

from .inertial_model import orientations, anatomical_residual, STATE_SIZE, LOW, HIGH

WINDOW_CONFIG = dict(acceleration_sigma_mps2=.35, proximal_velocity_sigma_rads=3.,
    torso_sigma_rad=.6, proximal_direction_sigma=1.5,
    boundary_sigma_rad=.005, seconds=10., max_nfev=100, normal_solver=True,
    control_period_s=.1, acceleration_bias_sigma_mps2=.2)


def sensor_positions(states, retained, calibration, height=.425, halfwidth=.14):
    torso, proximal = orientations(states,retained,np.asarray(calibration['hinge_axes']))
    lengths = calibration['lengths']
    result = []
    for k in range(4):
        arm, sign = k<2, 1 if k%2==0 else -1
        base = ((torso if arm else retained[:,0]) @
            np.array([0., sign*(lengths['shoulder_width']/2 if arm else halfwidth), height if arm else 0.]))
        joint = base - lengths['upper_arm' if arm else 'thigh']*proximal[:,k,:,2]
        sensor = joint + retained[:,k+1] @ np.asarray(calibration['imu_levers_from_joint_m'])[k]
        result.append(sensor)
    return np.stack(result,axis=1)


def difference_matrix(n, order, dt):
    coefficients = (-1.,1.) if order==1 else (1.,-2.,1.)
    return sparse.diags([np.full(n-order,c/dt**order) for c in coefficients],
                        list(range(order+1)),shape=(n-order,n),format='csr')


def solve_window(retained, acceleration_world, calibration, initial_states, *,
                 dt=.05, height=.425, halfwidth=.14, config=None, valid=None):
    cfg = WINDOW_CONFIG if config is None else config
    n = len(retained)
    if n<4 or initial_states.shape!=(n,STATE_SIZE):
        raise ValueError('window requires at least four frames and eleven states per frame')
    d2 = sparse.kron(difference_matrix(n,2,dt),sparse.eye(12),format='csr')
    d1 = sparse.kron(difference_matrix(n,1,dt),sparse.eye(45),format='csr') / (np.sqrt(2)*cfg['proximal_velocity_sigma_rads'])
    columns = (np.arange(n)[:,None]*STATE_SIZE+np.arange(3)).ravel()
    torso_prior = sparse.csr_matrix((np.full(n*3,1/cfg['torso_sigma_rad']),
        (np.arange(n*3),columns)),shape=(n*3,n*STATE_SIZE))
    boundary = sparse.eye(2*STATE_SIZE,n*STATE_SIZE,format='csr')/cfg['boundary_sigma_rad']
    linear = sparse.vstack((torso_prior,boundary),format='csr')
    target = (acceleration_world[1:-1,1:]-acceleration_world[1:-1,:1]).ravel()
    valid=np.ones(n,bool) if valid is None else np.asarray(valid,bool)
    active=np.repeat(valid[:-2]&valid[1:-1]&valid[2:],12).astype(float)
    d2=sparse.diags(active)@d2
    target=target*active
    linear_target = np.r_[np.zeros(torso_prior.shape[0]),
                          initial_states[:2].ravel()/cfg['boundary_sigma_rad']]
    cache = {}

    def position(x):
        return sensor_positions(x.reshape(n,STATE_SIZE),retained,calibration,height,halfwidth)

    def posture(x):
        torso,prox=orientations(x.reshape(n,STATE_SIZE),retained,np.asarray(calibration['hinge_axes']))
        reference=np.stack((torso,torso,retained[:,0],retained[:,0]),axis=1)
        # Generic hanging/rest preference acts on the missing segment swing.
        # It never substitutes a nominal elbow angle for the measured forearm.
        return ((prox-reference)/(np.sqrt(2)*cfg['proximal_direction_sigma'])).reshape(n,36)

    def proximal_pose(x):
        torso,prox=orientations(x.reshape(n,STATE_SIZE),retained,np.asarray(calibration['hinge_axes']))
        both=np.concatenate((torso[:,None],prox),axis=1)
        return (np.swapaxes(retained[:,0],1,2)[:,None]@both).reshape(n,45)

    def residual(x):
        p = position(x)
        post=posture(x)
        motion=proximal_pose(x)
        anatomy=anatomical_residual(x.reshape(n,STATE_SIZE),retained,calibration)
        cache.update(x=x.copy(),p=p,post=post,motion=motion,anatomy=anatomy)
        return np.r_[(d2@p.ravel()-target)/cfg['acceleration_sigma_mps2'],
                     linear@x-linear_target,post.ravel(),d1@motion.ravel(),anatomy.ravel()]

    def jacobian(x):
        p = cache['p'] if np.array_equal(cache.get('x'),x) else position(x)
        post=cache['post'] if np.array_equal(cache.get('x'),x) else posture(x)
        motion=cache['motion'] if np.array_equal(cache.get('x'),x) else proximal_pose(x)
        blocks = np.empty((n,12,STATE_SIZE)); post_blocks=np.empty((n,36,STATE_SIZE)); eps=1e-6
        motion_blocks=np.empty((n,45,STATE_SIZE))
        anatomy=anatomical_residual(x.reshape(n,STATE_SIZE),retained,calibration)
        anatomy_blocks=np.empty((n,8,STATE_SIZE))
        for k in range(STATE_SIZE):
            shifted=x.copy();shifted[k::STATE_SIZE]+=eps
            blocks[:,:,k]=((position(shifted)-p)/eps).reshape(n,12)
            post_blocks[:,:,k]=((posture(shifted)-post)/eps).reshape(n,36)
            motion_blocks[:,:,k]=((proximal_pose(shifted)-motion)/eps).reshape(n,45)
            anatomy_blocks[:,:,k]=(anatomical_residual(shifted.reshape(n,STATE_SIZE),retained,calibration)-anatomy)/eps
        local=sparse.block_diag(list(blocks),format='csr')
        return sparse.vstack((d2@local/cfg['acceleration_sigma_mps2'],linear,
                              sparse.block_diag(list(post_blocks)),
                              d1@sparse.block_diag(list(motion_blocks)),
                              sparse.block_diag(list(anatomy_blocks))),format='csr')

    low,high = LOW,HIGH
    guess=initial_states.copy()
    x0=np.clip(guess,np.tile(low,(n,1))+1e-7,np.tile(high,(n,1))-1e-7).ravel()
    # Cubic control trajectories remove the poorly conditioned per-frame
    # integration modes. Bounds on controls also bound the entire trajectory.
    times=np.arange(n)*dt
    knots=np.r_[np.repeat(0.,4),np.arange(cfg['control_period_s'],times[-1],cfg['control_period_s']),np.repeat(times[-1],4)]
    basis=BSpline.design_matrix(times,knots,3).toarray()
    parameter_map=sparse.kron(sparse.csr_matrix(basis),sparse.eye(STATE_SIZE),format='csr')
    nc=basis.shape[1]
    control0=np.linalg.lstsq(basis,x0.reshape(n,STATE_SIZE),rcond=None)[0]
    control0=np.clip(control0,low+1e-7,high-1e-7).ravel()
    count=len(target)
    bias_map=sparse.vstack((sparse.diags(active)@sparse.kron(np.ones((n-2,1)),sparse.eye(12))/cfg['acceleration_sigma_mps2'],
        sparse.csr_matrix((linear.shape[0]+n*44+d1.shape[0],12))),format='csr')

    def all_residual(v):
        return np.r_[residual(parameter_map@v[:-12])+bias_map@v[-12:],
                     v[-12:]/cfg['acceleration_bias_sigma_mps2']]

    def all_jacobian(v):
        pose=jacobian(parameter_map@v[:-12])@parameter_map
        matrix=sparse.bmat([[pose,bias_map],
            [None,sparse.eye(12)/cfg['acceleration_bias_sigma_mps2']]],format='csr')
        return matrix if cfg.get('sparse_solver',False) or cfg.get('normal_solver',False) else matrix.toarray()

    before=residual(parameter_map@control0);started=time.monotonic()
    bounds=(np.r_[np.tile(low,nc),np.full(12,-2.)],np.r_[np.tile(high,nc),np.full(12,2.)])
    if cfg.get('normal_solver',False):
        from .inertial_optimizer import solve
        result=solve(all_residual,all_jacobian,np.r_[control0,np.zeros(12)],bounds,
                     max_nfev=cfg['max_nfev'])
    else:
        result=least_squares(all_residual,np.r_[control0,np.zeros(12)],jac=all_jacobian,
            bounds=(np.r_[np.tile(low,nc),np.full(12,-2.)],np.r_[np.tile(high,nc),np.full(12,2.)]),
            x_scale='jac',tr_solver='lsmr' if cfg.get('sparse_solver',False) or cfg.get('normal_solver',False) else 'exact',
            tr_options=dict(atol=1e-6,btol=1e-6,maxiter=400) if cfg.get('sparse_solver',False) or cfg.get('normal_solver',False) else {},
            max_nfev=cfg['max_nfev'],ftol=1e-4,xtol=1e-4,gtol=1e-5)
    return (parameter_map@result.x[:-12]).reshape(n,STATE_SIZE),dict(success=bool(result.success),status=int(result.status),
        acceleration_bias_mps2=result.x[-12:].reshape(4,3).tolist(),
        control_points=nc,
        nfev=result.nfev,wall_s=time.monotonic()-started,
        before_acceleration_rms_mps2=float(np.sqrt(np.mean(before[:count]**2))*cfg['acceleration_sigma_mps2']),
        after_acceleration_rms_mps2=float(np.sqrt(np.mean(result.fun[:count]**2))*cfg['acceleration_sigma_mps2']),
        optimality=float(result.optimality),cost=float(result.cost),
        acceleration_rows=count,angular_velocity_regularizer=True,
        active_acceleration_rows=int(active.sum()),
        neutral_elbow_angle_prior=False,offline_future_frames=True)
