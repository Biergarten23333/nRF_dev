from __future__ import annotations

from collections import Counter
from typing import Mapping
import numpy as np

from .observability import svd_scan


def validate_svd_report(matrix: np.ndarray, report: Mapping) -> None:
    recomputed=svd_scan(np.asarray(matrix,float))
    for tol,row in recomputed["tolerance_scan"].items():
        supplied=report["tolerance_scan"][tol]
        if supplied["rank"]!=row["rank"] or supplied["nullity"]!=row["nullity"]:
            raise ValueError(f"SVD report mismatch at tolerance {tol}")
    np.testing.assert_allclose(report["singular_values"],recomputed["singular_values"],rtol=1e-10,atol=1e-12)


def audit_real_master(master: Mapping) -> dict:
    actions=list(master["actions"]);classes=Counter(x["classification"] for x in actions)
    if len(actions)!=22 or classes!={"DEVELOPMENT":19,"CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC":3}:
        raise ValueError("real action count/classification mismatch")
    if master.get("uwb_numeric_decode")!=0:
        raise ValueError("UWB numeric consumption is nonzero")
    factor_names=set(actions[0]["factor_activation"])
    factors={name:{"count":sum(x["factor_activation"][name]["count"] for x in actions),
                   "state_delta_sq":sum(x["factor_activation"][name]["state_delta_sq"] for x in actions),
                   "information_trace":sum(x["factor_activation"][name]["information_trace"] for x in actions)}
             for name in factor_names}
    if any(v["count"]<=0 or v["state_delta_sq"]<=0 or v["information_trace"]<=0 for v in factors.values()):
        raise ValueError("count-only or inactive factor")
    result={
      "action_count":len(actions),"classification_counts":dict(classes),
      "minimum_whole_body_availability":min(float(x["whole_body_availability"]) for x in actions),
      "maximum_bone_length_variation":max(float(x["bone_length_max_variation"]) for x in actions),
      "maximum_aligned_step_ratio":max(max(x["maximum_production_step_deg"].values())/
          max(max(x["maximum_B0_aligned_50hz_step_deg"].values()),1e-12) for x in actions),
      "factors":factors,"uwb_numeric_decode":0,"external_accuracy_claim":False,
    }
    return result
