"""Deterministic multi-hypothesis assignment and proper-rotation estimation."""
from __future__ import annotations
import math
import numpy as np
from scipy.optimize import linear_sum_assignment

class SolveError(ValueError): pass

def proper_rotation(sensor_vectors, segment_vectors):
    a=np.asarray(sensor_vectors,float); b=np.asarray(segment_vectors,float)
    h=a.T@b; u,_s,vt=np.linalg.svd(h); r=vt.T@u.T
    if np.linalg.det(r)<0: vt[-1]*=-1; r=vt.T@u.T
    if np.linalg.det(r)<0.999999: raise SolveError("extrinsic is not a proper rotation")
    return r

def covariance_gate(cov):
    c=np.asarray(cov,float)
    if c.shape[0]!=c.shape[1] or not np.isfinite(c).all(): raise SolveError("covariance non-finite")
    if not np.allclose(c,c.T,atol=1e-10): raise SolveError("covariance non-symmetric")
    try: np.linalg.cholesky(c)
    except np.linalg.LinAlgError as e: raise SolveError("covariance not positive definite") from e

def solve_assignment(node_features, slot_features, *, central_node, central_slot, low_margin=0.05):
    """Enumerate assignments. Features are rotation-invariant labelled motion energies.

    This is deliberately a likelihood kernel, not a forced body model. Production
    callers construct features from fitting phases only and add UWB topology costs.
    """
    nodes=sorted(node_features); slots=sorted(slot_features)
    if len(nodes)!=len(slots): raise SolveError("missing node or slot")
    if central_node not in nodes or central_slot not in slots: raise SolveError("central constraint invalid")
    free_n=[n for n in nodes if n!=central_node]; free_s=[s for s in slots if s!=central_slot]
    def distance(n,s):
        x=np.asarray(node_features[n],float); y=np.asarray(slot_features[s],float)
        if x.shape!=y.shape or not np.isfinite(x).all() or not np.isfinite(y).all(): return math.inf
        return float(np.sum((x-y)**2))
    cost=np.asarray([[distance(n,s) for s in free_s] for n in free_n])
    def hungarian(matrix):
        rows,cols=linear_sum_assignment(matrix)
        if not np.isfinite(matrix[rows,cols]).all():raise SolveError("no finite assignment")
        mapping={central_node:central_slot,**{free_n[r]:free_s[c] for r,c in zip(rows,cols)}}
        return float(distance(central_node,central_slot)+matrix[rows,cols].sum()),mapping,tuple(zip(rows,cols))
    best_cost,best,best_edges=hungarian(cost)
    alternatives=[]
    # Every different assignment omits at least one best edge. Forbid each in
    # turn; the cheapest constrained optimum is therefore the global runner-up.
    for r,c in best_edges:
        candidate=cost.copy();candidate[r,c]=math.inf
        try: alternatives.append(hungarian(candidate)[:2])
        except SolveError: pass
    if not alternatives:raise SolveError("need at least two assignment hypotheses")
    alternatives.sort(key=lambda x:(x[0],tuple(sorted(x[1].items()))));second_cost,second=alternatives[0]
    margin=second_cost-best_cost
    return {"best":best, "best_cost":best_cost, "second":second,
            "second_cost":second_cost, "cost_margin":margin,
            "identifiable":margin>low_margin, "ambiguities":[] if margin>low_margin else ["LOW_ASSIGNMENT_MARGIN"],
            "gauge":["GLOBAL_YAW"], "sufficient_for_ik_fk":margin>low_margin}
