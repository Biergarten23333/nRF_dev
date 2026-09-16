"""Bounded full-objective pose refinement with box-constrained L-BFGS-B.

Uses the existing objective and body-selection policy. No calibration fields,
observations, action labels, smoothing weights or reference poses are created.
"""
import time
import numpy as np
import torch
from scipy.optimize import Bounds, minimize

from .body_feasibility import selection_key
from .optimization import _snapshot


def optimize_quasi_newton(objective, levers, guess, *, iterations, wall_limit_s,
                          max_evaluations=250):
    if (not isinstance(iterations,int) or iterations<0 or not isinstance(max_evaluations,int)
            or max_evaluations<1 or not np.isfinite(wall_limit_s) or wall_limit_s<=0):
        raise ValueError('finite positive runtime and evaluation budgets required')
    if guess.ndim!=2 or guess.shape[1]<9 or not torch.isfinite(guess).all():
        raise ValueError('finite pose coordinates required')
    lower=np.full(guess.shape,-np.inf);upper=np.full(guess.shape,np.inf)
    lower[:,3:7]=0.;upper[:,3:7]=objective.model.maximum_bend.detach().cpu().numpy()
    start=guess.detach().cpu().double().numpy()
    if np.any(start<lower) or np.any(start>upper):
        raise ValueError('initial pose violates existing bend bounds')
    started=time.monotonic();history=[];selected=guess.detach().clone();best=None

    class BudgetReached(Exception):
        pass

    def evaluate(x):
        nonlocal selected,best
        if history and (time.monotonic()-started>wall_limit_s or len(history)>=max_evaluations):
            raise BudgetReached()
        q=torch.tensor(x.reshape(guess.shape),dtype=guess.dtype,device=guess.device,requires_grad=True)
        _,terms=objective.evaluate(q,levers)
        energy=terms['loss'];gradient=torch.autograd.grad(energy,q)[0]
        if not torch.isfinite(energy) or not torch.isfinite(gradient).all():
            raise ValueError('nonfinite full pose objective or gradient')
        violation=terms.get('body_violation',0.)
        if torch.is_tensor(violation):violation=violation.detach()
        key=selection_key(energy.detach(),violation)
        if best is None or key<best:best=key;selected=q.detach().clone()
        history.append(_snapshot(terms,len(history),'full_quasi_newton',float(energy.detach())))
        return float(energy.detach()),gradient.detach().cpu().double().numpy().ravel()

    if iterations==0:
        evaluate(start.ravel());status='zero-iteration evaluation';converged=False
    else:
        try:
            fit=minimize(evaluate,start.ravel(),jac=True,method='L-BFGS-B',
                bounds=Bounds(lower.ravel(),upper.ravel()),
                options=dict(maxiter=iterations,maxfun=max_evaluations,maxls=8,maxcor=5,ftol=1e-10))
            status=str(fit.message);converged=bool(fit.success)
        except BudgetReached:
            status='wall-time or evaluation budget reached';converged=False
    return selected,history,dict(optimizer='L-BFGS-B full objective',status=status,
        optimizer_converged=converged,evaluations=len(history),wall_s=time.monotonic()-started,
        selected_energy=float(best[-1]),feasible_body_selected=best[0]==0,
        calibration_changed=False,acceptance_claimed=False)
