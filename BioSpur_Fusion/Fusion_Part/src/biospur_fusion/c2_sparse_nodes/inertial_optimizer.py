"""Sparse damped Gauss–Newton for the banded offline trajectory objective."""
import time
from types import SimpleNamespace
import numpy as np
from scipy import sparse
from scipy.sparse.linalg import spsolve


def solve(fun,jac,x0,bounds,max_nfev=100,wall_limit_s=120):
    lower,upper=bounds;x=np.clip(x0,lower,upper);r=fun(x)
    cost=.5*r@r;damping=1e-3;started=time.monotonic();nfev=1
    success=False;status=0;optimality=np.inf
    for iteration in range(max_nfev):
        if time.monotonic()-started>wall_limit_s:break
        j=sparse.csr_matrix(jac(x));g=np.asarray(j.T@r).ravel()
        projected=g.copy()
        projected[((x<=lower+1e-8)&(g>0))|((x>=upper-1e-8)&(g<0))]=0.
        optimality=float(np.max(abs(projected)))
        if optimality<1e-5:success=True;status=1;break
        normal=(j.T@j).tocsc();diag=np.maximum(normal.diagonal(),1e-6)
        accepted=False
        for attempt in range(12):
            if nfev>=max_nfev:break
            step=spsolve(normal+sparse.diags(damping*diag,format='csc'),-g)
            trial=np.clip(x+step,lower,upper);step=trial-x
            candidate=fun(trial);nfev+=1
            new_cost=.5*candidate@candidate
            predicted=-g@step-.5*np.linalg.norm(j@step)**2
            ratio=(cost-new_cost)/max(predicted,1e-30)
            if np.isfinite(new_cost) and new_cost<cost:
                change=cost-new_cost;x,r,cost=trial,candidate,new_cost
                damping=max(damping*(.3 if ratio>.75 else 1.),1e-9)
                accepted=True
                if change<1e-4*max(cost,1e-12) and ratio>.25:
                    success=True;status=2
                break
            damping=min(damping*10,1e12)
        if success or not accepted:break
    return SimpleNamespace(x=x,fun=r,cost=float(cost),success=success,status=status,
                           nfev=nfev,optimality=optimality)
