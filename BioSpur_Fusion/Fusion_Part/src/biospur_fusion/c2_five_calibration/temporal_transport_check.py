"""Independent raw-time transport check for a temporal heading proposal.

Time-varying rotations do not commute with anti-alias filtering or SLERP.
Validate the fresh observations by transporting original raw observations
before those operators, never by increasing a post-filter tolerance.
"""
import numpy as np
import torch
from scipy.ndimage import uniform_filter1d
from scipy.spatial.transform import Rotation,Slerp
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from biospur_fusion.c2_sparse_nodes.calibration import relative
from biospur_fusion.c2_sparse_nodes.inertial import acceleration
from biospur_fusion.c2_imucoco.preprocessing import WORLD_TO_SMPL,TPOSE_SEGMENTS
from .temporal_parameters import TemporalHeadingParameters
from .shared_orientation import transport_heading


def check_raw_temporal_transport(episode,baseline,geometry,coefficients,old,new,*,heading_model=None):
    model=TemporalHeadingParameters(baseline,old['time_s']) if heading_model is None else heading_model
    coefficients=torch.as_tensor(coefficients,dtype=torch.float64)
    expected_r=[];expected_a=[];time=np.asarray(new['time_s'])
    for i,node in enumerate(NODES):
        rows=episode[node]['imu'];raw_time=rows[:,0]
        angles=np.zeros(len(rows)) if i==0 else model.increments(coefficients,raw_time)[:,i-1].numpy()
        turn=Rotation.from_euler('y',angles[:,None]).as_matrix()
        rr=WORLD_TO_SMPL@relative(rows,node,baseline)@TPOSE_SEGMENTS[i].T@WORLD_TO_SMPL.T
        expected_r.append(Slerp(raw_time-time[0],Rotation.from_matrix(turn@rr))(time-time[0]).as_matrix()@np.asarray(geometry['bone_frame_correction'])[i])
        aa=acceleration(rows,node,baseline)@WORLD_TO_SMPL.T
        aa=np.einsum('nij,nj->ni',turn,aa)
        aa=uniform_filter1d(aa,size=7,axis=0,mode='nearest')
        expected_a.append(np.column_stack([np.interp(time,raw_time,aa[:,j]) for j in range(3)]))
    r=np.stack(expected_r,1);a=np.stack(expected_a,1)
    np.testing.assert_allclose(new['observed'],r,atol=1e-9,rtol=0)
    np.testing.assert_allclose(new['acceleration'],a,atol=1e-9,rtol=0)
    _,approx=transport_heading(torch.tensor(old['observed']),torch.tensor(old['acceleration']),model.increments(coefficients))
    return dict(raw_time_transport_verified=True,rotation_max_error=float(np.max(abs(new['observed']-r))),
                acceleration_max_error_mps2=float(np.max(abs(new['acceleration']-a))),
                surrogate_filter_commutator_max_mps2=float(np.max(abs(new['acceleration']-approx.numpy()))),
                fresh_objective_uses_exact_raw_time_transport=True,
                inner_surrogate='post-filter rotation is approximate; never used for final acceptance energy')
