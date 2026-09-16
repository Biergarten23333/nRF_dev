"""C2-owned piecewise-linear increments to a frozen heading hypothesis.

The baseline curve is not treated as observed torso motion. Knot corrections
are latent calibration parameters; acceptance requires a fresh neural replay.
"""
import copy
import numpy as np
import torch
from biospur_fusion.c2_sparse_nodes.inputs import NODES
from .calibration_prior import RegisteredHeadingPrior


def linear_basis(query, knots):
    query=np.asarray(query,float);knots=np.asarray(knots,float)
    if knots.ndim!=1 or not len(knots) or not np.isfinite(knots).all() or np.any(np.diff(knots)<=0):
        raise ValueError('ordered finite calibration knots required')
    if query.ndim!=1 or not np.isfinite(query).all():raise ValueError('finite time vector required')
    if len(knots)==1:return np.ones((len(query),1))
    clipped=np.clip(query,knots[0],knots[-1])
    right=np.searchsorted(knots,clipped,side='right').clip(1,len(knots)-1)
    left=right-1;weight=(clipped-knots[left])/(knots[right]-knots[left])
    result=np.zeros((len(query),len(knots)));rows=np.arange(len(query))
    result[rows,left]=1-weight;result[rows,right]=weight
    return result


class TemporalHeadingParameters:
    def __init__(self, baseline, time_s):
        if 'shared_orientation_proposal' in baseline:
            raise ValueError('an immutable baseline, not an unaccepted proposal, is required')
        self.baseline=copy.deepcopy(baseline);self.time_s=np.asarray(time_s,float)
        if (self.time_s.ndim!=1 or len(self.time_s)<2 or not np.isfinite(self.time_s).all()
                or np.any(np.diff(self.time_s)<=0)):
            raise ValueError('strictly increasing finite calibration time grid required')
        self.knots=[];self.slices=[];count=0
        curves=baseline.get('temporal_heading_curves',{})
        for node in NODES[1:]:
            curve=curves.get(node)
            if curve is not None and 'joint_increment_rad' in curve:
                raise ValueError('increments must be measured from the original baseline')
            knots=np.asarray(curve['time_s'] if curve is not None else [self.time_s[0]],float)
            self.knots.append(knots);self.slices.append(slice(count,count+len(knots)));count+=len(knots)
        self.size=count
        self.basis=self.basis_at(self.time_s)

    def basis_at(self, times):
        result=np.zeros((len(times),4,self.size))
        for i,(knots,section) in enumerate(zip(self.knots,self.slices)):
            result[:,i,section]=linear_basis(times,knots)
        return result

    def increments(self, coefficients, times=None):
        if coefficients.shape!=(self.size,):raise ValueError('wrong temporal calibration parameter count')
        basis=self.basis if times is None else self.basis_at(times)
        return torch.as_tensor(basis,dtype=coefficients.dtype,device=coefficients.device)@coefficients

    def frontend(self, coefficients):
        c=np.asarray(coefficients,float)
        if c.shape!=(self.size,) or not np.isfinite(c).all():raise ValueError('finite curve coefficients required')
        result=copy.deepcopy(self.baseline)
        for node,section in zip(NODES[1:],self.slices):
            curve=result.get('temporal_heading_curves',{}).get(node)
            if curve is None:result['frozen_heading_correction_rad'][node]+=float(c[section][0])
            else:curve['joint_increment_rad']=c[section].tolist()
        result['joint_temporal_proposal']=dict(coefficients_rad=c.tolist(),H_used=False,
            extrapolation='hold endpoint',accepted=False,
            interpretation='latent C2 movement-plane correction jointly conditioned on inferred body; not measured heading')
        result['calibration_accepted']=False
        return result


class TemporalRegisteredHeadingPrior(RegisteredHeadingPrior):
    """The same raw scalar factors, evaluated at their measurement timestamps."""
    def __init__(self, baseline, time_s):
        super().__init__(baseline['heading_factors'],baseline['frozen_heading_correction_rad'],baseline_calibration=baseline)
        times=[f['measurement_time_s'] for n in NODES[1:] for f in baseline['heading_factors'][n] if f.get('used_for_frozen_heading',True)]
        self.factor_basis=linear_basis(times,time_s)
        assert len(times)==len(self.rows)

    def energy(self, delta, *, residual_blocks=None):
        if delta.ndim==1:return super().energy(delta,residual_blocks=residual_blocks)
        sampled=torch.as_tensor(self.factor_basis,dtype=delta.dtype,device=delta.device)@delta
        total=delta.sum()*0.
        for row,(i,z,multiple,information) in enumerate(self.rows):
            difference=multiple*(self.baseline[i]+sampled[row,i]-z)
            residual=torch.atan2(torch.sin(difference),torch.cos(difference))/multiple
            from .residual_blocks import record_weighted
            record_weighted(residual_blocks, f'heading/{row}', residual, information)
            total=total+information*residual.square()
        return total
