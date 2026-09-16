"""Bounded square-root elimination at one fixed joint linearization point."""
import time
from dataclasses import replace
import numpy as np
import torch
from .marginalization import marginalize_linear_system


def linearize_residual(function, point, *, row_batch=16, max_bytes=256*1024**2,
                       deadline=None):
    """Bound reverse-mode batches instead of materializing all output seeds.

    The result is a local residual Jacobian, not an exact posterior Hessian.
    The memory bound covers the returned dense Jacobian, not PyTorch's graph.
    """
    if point.ndim!=1 or not torch.isfinite(point).all() or row_batch<1:
        raise ValueError('finite coordinate vector and positive row batch required')
    x=point.detach().clone().requires_grad_();r=function(x)
    if r.ndim!=1 or not r.numel() or not torch.isfinite(r).all():
        raise ValueError('finite nonempty residual vector required')
    if r.numel()*x.numel()*8>max_bytes:
        raise ValueError('Jacobian exceeds configured memory budget')
    result=np.empty((r.numel(),x.numel()),dtype=float)
    for start in range(0,r.numel(),row_batch):
        if deadline is not None and time.monotonic()>deadline:
            raise TimeoutError('linearization deadline reached')
        stop=min(start+row_batch,r.numel())
        seeds=r.new_zeros((stop-start,r.numel()))
        seeds[torch.arange(stop-start),torch.arange(start,stop)]=1.
        grad,=torch.autograd.grad(r,x,grad_outputs=seeds,is_grads_batched=True,
                                 retain_graph=stop<r.numel())
        result[start:stop]=grad.detach().cpu().numpy()
    if not np.isfinite(result).all():raise ValueError('nonfinite residual Jacobian')
    return result,r.detach().cpu().numpy()


class StreamingInformation:
    """Retain shared coordinates and a caller-declared temporal separator.

    Each input row must be owned exactly once. Historical coordinates cannot
    reappear, and retained coordinates cannot change linearization point.
    Noise interpretation and residual ownership remain caller responsibilities.
    """
    def __init__(self, shared_columns, *, max_columns=1024, rtol=1e-10):
        self.shared=frozenset(shared_columns);self.max_columns=max_columns;self.rtol=rtol
        self.columns=();self.point=np.empty(0);self.factor=None;self.eliminated=set()

    def append(self, jacobian, residual, columns, point, retain):
        columns=tuple(columns);retain=tuple(retain);point=np.asarray(point,float)
        J=np.asarray(jacobian,float);r=np.asarray(residual,float)
        if (len(set(columns))!=len(columns) or len(set(retain))!=len(retain)
                or point.shape!=(len(columns),) or not np.isfinite(point).all()
                or J.shape!=(len(r),len(columns)) or r.ndim!=1
                or set(columns)&self.eliminated):
            raise ValueError('unique fresh/current coordinate identities and matching linearization required')
        union=tuple(dict.fromkeys((*self.columns,*columns)))
        if len(union)>self.max_columns:raise ValueError('active coordinate budget exceeded')
        if not self.shared<=set(retain) or not set(retain)<=set(union):
            raise ValueError('retain every shared parameter and only active coordinates')
        old=dict(zip(self.columns,self.point));new=dict(zip(columns,point))
        if any(old[k]!=new[k] for k in old.keys()&new.keys()):
            raise ValueError('cannot change the linearization point of retained history')
        locations={k:i for i,k in enumerate(union)}
        nold=0 if self.factor is None else len(self.factor.residual)
        matrix=np.zeros((nold+len(r),len(union)))
        offset=np.empty(nold+len(r));constant=0.
        if self.factor is not None:
            matrix[:nold,[locations[k] for k in self.columns]]=self.factor.matrix
            offset[:nold]=self.factor.residual;constant=self.factor.constant_energy
        matrix[nold:,[locations[k] for k in columns]]=J;offset[nold:]=r
        factor=marginalize_linear_system(matrix,offset,np.array([locations[k] for k in retain]),rtol=self.rtol)
        factor=replace(factor,constant_energy=factor.constant_energy+constant)
        self.eliminated.update(set(union)-set(retain))
        self.columns=retain;self.point=np.array([{**old,**new}[k] for k in retain])
        self.factor=factor
        return factor
