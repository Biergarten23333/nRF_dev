#!/usr/bin/env python3
"""Independent covariance verifier for Q1.

This module deliberately does not call either production discretization
helper.  SciPy's matrix exponential is applied directly to the Van Loan block
matrix and serves only as an offline reference.
"""
from __future__ import annotations

import numpy as np
from scipy.linalg import expm


def van_loan_reference(F: np.ndarray, L: np.ndarray, dt_s: float) -> tuple[np.ndarray, np.ndarray]:
    F=np.asarray(F,dtype=float);L=np.asarray(L,dtype=float);n=F.shape[0]
    if F.shape!=(n,n) or L.shape!=(n,n):raise ValueError("square F/L required")
    block=np.block([[F,L],[np.zeros_like(F),-F.T]])
    result=expm(block*float(dt_s));phi=result[:n,:n]
    qd=result[:n,n:]@phi.T
    return phi,.5*(qd+qd.T)


def compose_discrete_map(phi: np.ndarray, qd: np.ndarray, repetitions: int) -> tuple[np.ndarray, np.ndarray]:
    """Binary composition of P -> Phi P Phi.T + Q for long constant cases."""
    if repetitions<0:raise ValueError("negative repetitions")
    n=len(phi);a=np.eye(n);q=np.zeros((n,n));base_a=np.asarray(phi,float);base_q=np.asarray(qd,float);k=int(repetitions)
    while k:
        if k&1:
            q=base_a@q@base_a.T+base_q;a=base_a@a
        base_q=base_a@base_q@base_a.T+base_q;base_a=base_a@base_a;k>>=1
    return a,.5*(q+q.T)


def scale_aware_psd(P: np.ndarray) -> dict:
    P=np.asarray(P,float);sym=.5*(P+P.T);eig=np.linalg.eigvalsh(sym);scale=max(float(eig[-1]),1.)
    tolerance=64*len(P)*np.finfo(float).eps*scale
    try:np.linalg.cholesky(sym);chol=True
    except np.linalg.LinAlgError:chol=False
    return {"min_eigenvalue":float(eig[0]),"max_eigenvalue":float(eig[-1]),
        "relative_min_eigenvalue":float(eig[0]/scale),"condition":float(eig[-1]/eig[0]) if eig[0]>0 else float("inf"),
        "roundoff_bound":float(tolerance),"materially_negative":bool(eig[0]<-tolerance),
        "max_asymmetry":float(np.max(np.abs(P-P.T))),"cholesky_success":chol,
        "diagonal_min":float(np.min(np.diag(P))),"diagonal_max":float(np.max(np.diag(P)))}
