#!/usr/bin/env python3
"""Frozen, deterministic BSFC2CC revalidation-v2 gate primitives."""
from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy.stats import beta

TRANSIENT_ABS_RESIDUAL_G = 0.060
SYSTEMATIC_LIMITS = {
    "aggregate_rmse_g_lt": 0.005,
    "aggregate_abs_p95_g_lt": 0.010,
    "aggregate_abs_p99_g_lt": 0.020,
    "per_pose_abs_median_g_lt": 0.010,
    "persistent_directional_abs_residual_g_lt": 0.020,
    "equivalence_rmse_tolerance_g": 0.0005,
}


def exact_binomial_interval(successes: int, trials: int, confidence: float = .95) -> tuple[float, float]:
    """Two-sided Clopper-Pearson exact interval."""
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError("invalid binomial counts")
    alpha = 1-confidence
    lower = 0.0 if successes == 0 else float(beta.ppf(alpha/2, successes, trials-successes+1))
    upper = 1.0 if successes == trials else float(beta.ppf(1-alpha/2, successes+1, trials-successes))
    return lower, upper


def systematic_gate(pose_accel_g: list[np.ndarray], bias_g, correction_matrix) -> tuple[dict, list[dict]]:
    bias=np.asarray(bias_g,float); C=np.asarray(correction_matrix,float); rows=[]; all_raw=[];all_cor=[]
    for pose,a in enumerate(pose_accel_g,1):
        a=np.asarray(a,float); raw=np.linalg.norm(a,axis=1)-1; cor=np.linalg.norm((a-bias)@C.T,axis=1)-1
        raw_rmse=float(np.sqrt(np.mean(raw*raw))); cor_rmse=float(np.sqrt(np.mean(cor*cor)))
        row={"pose":pose,"samples":len(a),"raw_rmse_g":raw_rmse,"corrected_rmse_g":cor_rmse,
             "corrected_median_residual_g":float(np.median(cor)),"corrected_abs_p95_g":float(np.percentile(np.abs(cor),95)),
             "corrected_abs_p99_g":float(np.percentile(np.abs(cor),99)),
             "improved_or_equivalent":bool(cor_rmse <= raw_rmse+SYSTEMATIC_LIMITS["equivalence_rmse_tolerance_g"]),
             "persistent_systematic_pass":bool(abs(float(np.median(cor))) < SYSTEMATIC_LIMITS["persistent_directional_abs_residual_g_lt"])}
        rows.append(row);all_raw.append(raw);all_cor.append(cor)
    raw=np.concatenate(all_raw);cor=np.concatenate(all_cor)
    checks={"aggregate_rmse":float(np.sqrt(np.mean(cor*cor)))<.005,
            "aggregate_p95":float(np.percentile(np.abs(cor),95))<.010,
            "aggregate_p99":float(np.percentile(np.abs(cor),99))<.020,
            "per_pose_median":all(abs(x["corrected_median_residual_g"])<.010 for x in rows),
            "all_improve_or_equivalent":all(x["improved_or_equivalent"] for x in rows),
            "no_persistent_systematic":all(x["persistent_systematic_pass"] for x in rows)}
    result={"schema":"biospur-c2cc-systematic-gate-v2","checks":checks,"pass":all(checks.values()),
            "samples":len(cor),"uncalibrated_rmse_g":float(np.sqrt(np.mean(raw*raw))),
            "corrected_rmse_g":float(np.sqrt(np.mean(cor*cor))),"corrected_bias_g":float(np.mean(cor)),
            "corrected_abs_p95_g":float(np.percentile(np.abs(cor),95)),"corrected_abs_p99_g":float(np.percentile(np.abs(cor),99)),
            "equivalence_definition":"corrected pose RMSE <= uncalibrated pose RMSE + 0.0005 g (frozen engineering tolerance)"}
    return result,rows


def transient_runs(samples: list[dict]) -> list[list[dict]]:
    candidates=[x for x in samples if x.get("transient_candidate")]
    runs=[]
    for x in candidates:
        if runs and x["node_us"]-runs[-1][-1]["node_us"]==5000:
            runs[-1].append(x)
        else: runs.append([x])
    return runs


def sensor_transient_gate(samples: list[dict], q1_audit: list[dict]) -> dict:
    runs=transient_runs(samples); isolated=sum(len(x)==1 for x in runs); n=len(samples)
    lo,hi=exact_binomial_interval(isolated,n) if n else (0.,1.)
    times=[x[0]["node_us"] for x in runs]
    clustered=any(b-a<60_000_000 for a,b in zip(times,times[1:]))
    checks={"no_burst_ge_3":all(len(x)<3 for x in runs),"at_most_one_burst_ge_2":sum(len(x)>=2 for x in runs)<=1,
            "point_rate_below_1_per_10000":isolated/max(n,1)<1/10000,"no_temporal_clustering":not clustered,
            "all_rejected_by_q1":all(not x["accepted"] for x in q1_audit if x.get("transient_candidate")),
            "no_quaternion_covariance_false_motion_failure":all(x.get("numerical_pass",False) for x in q1_audit)}
    exposure_sufficient=hi<1/10000
    return {"schema":"biospur-c2cc-sensor-transient-gate-v2","samples":n,"transient_count":sum(len(x) for x in runs),
            "isolated_count":isolated,"maximum_consecutive":max((len(x) for x in runs),default=0),
            "burst_count_ge_2":sum(len(x)>=2 for x in runs),"rate_per_sample":isolated/max(n,1),
            "exact_clopper_pearson_95_interval":[lo,hi],"rate_confidence_exposure_sufficient":exposure_sufficient,
            "checks":checks,"pass":all(checks.values()) and exposure_sufficient,
            "conditional_only":all(checks.values()) and not exposure_sufficient}
