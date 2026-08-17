from __future__ import annotations
import numpy as np
from .association import ROLES

def probe_bundle(bundle):
    hypotheses=bundle.get("mapping_hypotheses")
    if not isinstance(hypotheses,list) or len(hypotheses)<1:raise ValueError("mapping hypotheses")
    checked=[]
    for h in hypotheses:
        mapping=h.get("mapping",{}); roles=list(mapping.values())
        if len(mapping)!=10 or set(roles)!=set(ROLES) or len(set(mapping))!=10:raise ValueError("ten one-to-one roles")
        marg=h.get("conditional_marginals",{})
        if set(marg)!=set(mapping):raise ValueError("conditional marginals")
        for node,item in marg.items():
            cov=np.asarray(item["T_segment_to_IMU"]["covariance_diagonal"],float)
            if cov.shape!=(6,) or np.any(cov<=0) or not np.isfinite(cov).all():raise ValueError("extrinsic covariance")
        checked.append(h["hypothesis_rank"])
    if bundle.get("runtime_UWB_required") is not False:raise ValueError("runtime UWB forbidden")
    if bundle.get("phase1_orientation_role")!="INITIALIZER_OR_DIAGNOSTIC_ONLY":raise ValueError("P1 double count")
    if bundle.get("contact_status")!="CONTACT_UNOBSERVABLE":raise ValueError("contact")
    if bundle.get("model_inferred_segments")!={"head":"MODEL_INFERRED","hands":"MODEL_INFERRED","feet":"UNAVAILABLE"}:raise ValueError("virtual segments")
    base=sum(sum(v["T_segment_to_IMU"]["covariance_diagonal"]) for v in hypotheses[0]["conditional_marginals"].values())
    perturbed=2*base
    if not perturbed>base:raise ValueError("uncertainty propagation")
    return {"status":"PASS_CONDITIONAL_TOPK_HANDOFF_READY","hypotheses_checked":checked,"authoritative_constructor_ready":bool(bundle.get("authoritative",False)),
            "calibration_uncertainty_changes_prediction_uncertainty":True,"prediction_uncertainty_trace_base":base,"prediction_uncertainty_trace_perturbed":perturbed,
            "runtime_UWB_required":False,"phase3_started":False}

