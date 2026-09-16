"""Sparse pose Jacobians by grouping residuals with disjoint frame support.

Only per-frame pose coordinates are differentiated here. Shared calibration
columns cannot be recovered from grouped sums and must be computed separately.
"""
import heapq
import time
import numpy as np
from scipy.sparse import csr_matrix
import torch


def disjoint_row_groups(start, stop, frame_count):
    start=np.asarray(start);stop=np.asarray(stop)
    if (start.ndim!=1 or stop.shape!=start.shape or not len(start)
            or not np.issubdtype(start.dtype,np.integer) or not np.issubdtype(stop.dtype,np.integer)
            or np.any(start<0) or np.any(stop>frame_count) or np.any(stop<=start)):
        raise ValueError('nonempty valid structural frame intervals required')
    groups=[];heap=[]
    for row in np.argsort(start,kind='stable'):
        if heap and heap[0][0]<=start[row]:
            _,group=heapq.heappop(heap)
        else:
            group=len(groups);groups.append([])
        groups[group].append(int(row));heapq.heappush(heap,(int(stop[row]),group))
    return groups


def linearize_temporal_residual(function, point, start, stop, *, group_batch=16,
                                max_bytes=256*1024**2, deadline=None):
    """Return CSR d(residual)/d(flattened pose), preserving each original row.

    Structural support must be correct independently of this calculation.
    Finite-difference/ungrouped controls are needed when adding an owner.
    Memory cap estimates sparse output plus row pointers, not the AD graph.
    """
    if point.ndim!=2 or not torch.isfinite(point).all() or group_batch<1:
        raise ValueError('finite per-frame coordinate matrix required')
    start=np.asarray(start);stop=np.asarray(stop)
    groups=disjoint_row_groups(start,stop,len(point));width=point.shape[1]
    lengths=(stop-start)*width;indptr=np.r_[0,np.cumsum(lengths)]
    if int(indptr[-1])*16+indptr.nbytes>max_bytes:
        raise ValueError('sparse Jacobian exceeds configured memory budget')
    x=point.detach().clone().requires_grad_();residual=function(x)
    if residual.shape!=(len(start),) or not torch.isfinite(residual).all():
        raise ValueError('residual rows must match declared support')
    indices=np.concatenate([np.arange(a*width,b*width) for a,b in zip(start,stop)])
    data=np.empty(len(indices))
    for lo in range(0,len(groups),group_batch):
        if deadline is not None and time.monotonic()>deadline:
            raise TimeoutError('temporal linearization deadline reached')
        batch=groups[lo:lo+group_batch]
        seed=residual.new_zeros((len(batch),len(residual)))
        for j,rows in enumerate(batch):seed[j,rows]=1.
        gradient,=torch.autograd.grad(residual,x,grad_outputs=seed,is_grads_batched=True,
                                      retain_graph=lo+group_batch<len(groups))
        values=gradient.detach().cpu().numpy()
        for j,rows in enumerate(batch):
            for row in rows:
                data[indptr[row]:indptr[row+1]]=values[j,start[row]:stop[row]].reshape(-1)
    if not np.isfinite(data).all():raise ValueError('nonfinite sparse Jacobian')
    return csr_matrix((data,indices,indptr),shape=(len(start),point.numel())),residual.detach().cpu().numpy(),len(groups)
