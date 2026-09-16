"""Buffered common-root initialization with explicit once-only raw evidence.

The prior mean is independent of the fitting rows. The position optimizer may
start from their median, but that median supplies no extra prior information.
"""
from dataclasses import replace
import numpy as np
from .tight_range import linearize_raw_range_factors
from biospur_fusion.root_r3.models import RootState


def row_identity(row):
    return (row.node,int(row.boot),int(row.sequence),int(row.sweep),int(row.strobe_us))


def initialize_raw_batch(t0,prior,initial_position,observations,anchors,config,*,aggregate_geometry=False):
    """Fit position, considering uncertain initial velocity; retain v/b means.

    Observations are (row,clock,offset,offset_velocity,reference_epoch).
    Crosscovariance is obtained with the actual position-only gain/Joseph.
    """
    if config.partial_tracking:
        raise ValueError('partial tracking cannot initialize a world position')
    if len(observations)!=10 or len({r.node for r,_,_,_,_ in observations})!=10:
        raise ValueError('initialization requires one sweep from each of ten nodes')
    epochs=np.array([r[-1] for r in observations])
    if epochs.min()<t0 or epochs.max()-t0>.25:
        raise ValueError('initialization prefix exceeds bounded startup interval')
    p=prior.covariance;mean=prior.vector.copy();estimate=np.asarray(initial_position,float).copy()
    if np.any(mean[3:]!=0) or np.any(p[:3,3:]!=0):
        raise ValueError('initializer requires independent zero-mean inertial nuisance prior')
    def factors(position):
        hs=[];innovations=[];variances=[];identities=[]
        for row,clock,offset,velocity,epoch in observations:
            if aggregate_geometry and row.valid_mask == 0:
                continue
            x=mean.copy();x[:3]=position
            local=RootState(epoch,x,p)
            f=linearize_raw_range_factors(local,row,anchors_m=anchors,clock=clock,
                tag_offset_world_m=offset,tag_offset_velocity_world_mps=velocity,
                reference_epoch_s=epoch,config=replace(config,partial_tracking=True) if aggregate_geometry else config,_enforce_geometry=False)
            if not aggregate_geometry and len(f.anchors)<4:raise ValueError('initialization node lacks four retained links')
            h=f.state_jacobian.copy();h[:,3:6]=h[:,:3]*(f.link_epochs_s-t0)[:,None]
            hs.append(h);innovations.extend(f.innovations_m)
            variances.extend(np.diag(f.r_prior_m2)/f.robust_weights)
            identities.append(row_identity(row))
        if not hs:raise ValueError('initialization has no retained links')
        h=np.vstack(hs);var=np.array(variances)
        if aggregate_geometry:
            weighted=h[:,:3]/np.sqrt(var[:,None])
            if len(var)<4 or np.linalg.matrix_rank(weighted)<3 or np.linalg.cond(weighted)>1e10:
                raise ValueError('aggregate initialization geometry is insufficient')
        return h,np.array(innovations),var,identities
    prior_info=np.linalg.inv(p[:3,:3])
    inflation=1.
    for outer in range(2):
        for iteration in range(20):
            h,innovation,var,identities=factors(estimate)
            r=np.diag(var*inflation)+h[:,3:]@p[3:,3:]@h[:,3:].T
            weighted=np.linalg.solve(r,h[:,:3]);rhs=np.linalg.solve(r,innovation)
            information=prior_info+h[:,:3].T@weighted
            delta=np.linalg.solve(information,h[:,:3].T@rhs-prior_info@(estimate-mean[:3]))
            estimate+=delta
            if np.linalg.norm(delta)<1e-6:break
        else:raise ValueError('raw initialization did not converge within20iterations')
        h,innovation,var,identities=factors(estimate)
        if outer==0:
            inflation=max(1.,float(np.sum(innovation**2/var)/max(1,len(var)-3)))
    # Recompute the final actual gain from the independent prior and fixed
    # linearization; velocity/bias remain consider states, crosscov is retained.
    r=np.diag(var*inflation)
    gain=np.linalg.solve(h@p@h.T+r,h@p).T;gain[3:]=0.
    residual=np.eye(9)-gain@h
    covariance=residual@p@residual.T+gain@r@gain.T
    covariance=(covariance+covariance.T)*.5
    vector=mean.copy();vector[:3]=estimate
    state=RootState(t0,vector,covariance)
    report={'identities':[list(i) for i in identities],
        'earliest_epoch_s':float(epochs.min()),'latest_epoch_s':float(epochs.max()),
        'startup_ready_epoch_s':max(float(clock.seconds(row.strobe_us+.5*row.t_round_us[slot]))
            for row,clock,_,_,_ in observations for slot in range(8) if row.valid_mask&(1<<slot)),
        'retained_masks':[int(row.valid_mask) for row,_,_,_,_ in observations],
        'aggregate_geometry':aggregate_geometry,
        'contributing_nodes':len(identities),
        'initial_output_is_retrospective':True,
        'independent_prior_mean':mean.tolist(),'independent_prior_covariance':p.tolist(),
        'posterior_covariance':covariance.tolist(),'residual_inflation':inflation,
        'retained_link_count':len(var),'residual_rms_m':float(np.sqrt(np.mean(innovation**2))),
        'geometry_rank':int(np.linalg.matrix_rank(h[:,:3])),
        'uncertainty_scope':'CONDITIONAL_ON_FIXED_FK_OFFSETS_ANCHORS_RADIO_MODEL_NOT_GLOBAL_TRUTH',
        'velocity_bias_policy':'UNCHANGED_MEANS_AND_MARGINALS_ACTUAL_JOSEPH_CROSS_COVARIANCE'}
    return state,report
