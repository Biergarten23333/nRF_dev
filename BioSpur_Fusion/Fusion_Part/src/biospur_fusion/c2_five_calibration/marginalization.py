"""Square-root elimination for an already weighted linearized joint system.

No damping or covariance inversion is inserted: unobserved directions retain
zero information. The caller owns noise, prior and coordinate-scale meaning.
"""
from dataclasses import dataclass
import numpy as np


@dataclass(frozen=True)
class LinearBoundaryFactor:
    matrix: np.ndarray
    residual: np.ndarray
    constant_energy: float
    eliminated_rank: int

    def energy(self, delta):
        delta=np.asarray(delta,dtype=float)
        if delta.shape!=(self.matrix.shape[1],) or not np.isfinite(delta).all():
            raise ValueError('finite retained-state increment required')
        return float(np.sum((self.matrix@delta+self.residual)**2)+self.constant_energy)

    @property
    def unresolved_dimensions(self):
        return self.matrix.shape[1]-self.matrix.shape[0]


def marginalize_linear_system(jacobian,residual,retain,*,rtol=1e-10):
    """Profile out all other coordinates in ||J delta + residual||².

    `retain` order defines the output coordinate order. The SVD threshold is
    numerical only, not a calibrated observability or confidence threshold.
    A pseudoinverse covariance would report zero variance in null directions;
    this API instead returns a possibly rank-deficient information factor.
    """
    J=np.asarray(jacobian,dtype=float);r=np.asarray(residual,dtype=float)
    keep=np.asarray(retain)
    if (J.ndim!=2 or not J.size or r.shape!=(len(J),) or not np.isfinite(J).all()
            or not np.isfinite(r).all() or not np.isfinite(rtol) or rtol<=0
            or keep.ndim!=1 or keep.size==0 or not np.issubdtype(keep.dtype,np.integer)
            or len(np.unique(keep))!=len(keep) or np.any(keep<0) or np.any(keep>=J.shape[1])):
        raise ValueError('finite joint system and distinct retained columns required')
    threshold=rtol*np.linalg.norm(J)
    removed=np.setdiff1d(np.arange(J.shape[1]),keep)
    B=J[:,keep].copy();b=r.copy();rank=0
    if len(removed):
        U,s,_=np.linalg.svd(J[:,removed],full_matrices=False)
        rank=int(np.count_nonzero(s>threshold));basis=U[:,:rank]
        B-=basis@(basis.T@B);b-=basis@(basis.T@b)
    U,s,V=np.linalg.svd(B,full_matrices=False)
    supported=s>threshold
    A=s[supported,None]*V[supported]
    offset=U[:,supported].T@b
    constant=max(0.,float(b@b-offset@offset))
    return LinearBoundaryFactor(A,offset,constant,rank)


def separate_history_factors(jacobian,residual,retain,history_rows,*,rtol=1e-10):
    """Marginalize historical factors and leave separator-only rows uncounted.

    The caller supplies STRUCTURAL row ownership; zero numerical derivatives
    cannot identify a factor's support. Returned live rows must be evaluated
    once by the runtime window, never also included in the marginal factor.
    """
    J=np.asarray(jacobian,dtype=float);r=np.asarray(residual,dtype=float)
    mask=np.asarray(history_rows)
    if (J.ndim!=2 or r.shape!=(len(J),) or not np.isfinite(J).all()
            or not np.isfinite(r).all() or mask.shape!=(len(J),)
            or mask.dtype!=bool or not mask.any()):
        raise ValueError('explicit historical factor ownership required')
    factor=marginalize_linear_system(J[mask],r[mask],retain,rtol=rtol)
    removed=np.setdiff1d(np.arange(J.shape[1]),retain)
    if np.any(np.abs(J[~mask][:,removed])>rtol*np.linalg.norm(J)):
        raise ValueError('live factor still depends on eliminated history')
    return factor,J[~mask][:,retain].copy(),r[~mask].copy()
