from __future__ import annotations

import json
import numpy as np

from .estimator import ArticulatedImuEstimator, ImuObservation


def evaluate_cold_holdout(observations: list[ImuObservation], binding, config: dict, route: dict) -> dict:
    """Causal cold-start evaluation; action boundaries route metrics only."""
    if not observations:
        raise ValueError("empty holdout IMU projection")
    estimator = ArticulatedImuEstimator(binding, config)
    start = min(x.time_s for x in observations)
    init_end = start + config["initialization_target_s"]
    formal_start = start + route["preparation_s"]
    formal_stop = formal_start + route["formal_s"]
    recovery_stop = formal_stop + route["recovery_s"]
    grid = init_end; step = 1.0/config["output_rate_hz"]
    counts = {"preparation":0,"formal":0,"recovery":0}; usable={k:0 for k in counts}
    last_cutoff=None; cutoff_changes=0; max_unusable=run_unusable=0; finite=True; mapping_stable=True; frame_stable=True
    sample_output=None
    for observation in observations:
        estimator.update(observation)
        while grid <= min(observation.time_s, recovery_stop):
            out=estimator.output(grid); sample_output=out
            region="preparation" if grid<formal_start else ("formal" if grid<=formal_stop else "recovery")
            counts[region]+=1
            ok=all(x["orientation_valid"] for x in out["segments"].values())
            usable[region]+=int(ok); run_unusable=0 if ok else run_unusable+1; max_unusable=max(max_unusable,run_unusable)
            finite &= all(np.isfinite(x) for x in out["root_local_position_m"]+out["root_local_velocity_m_s"])
            mapping_stable &= out["mapping_binding_id"]==binding.binding_id
            frame_stable &= out["frame_realization"]==estimator.frame_realization
            cutoff_changes += int(last_cutoff is not None and out["measurement_cutoff_time_s"]!=last_cutoff); last_cutoff=out["measurement_cutoff_time_s"]
            grid += step
    estimator.assert_numerical_health()
    expected_formal=max(1,int(round(route["formal_s"]*config["output_rate_hz"])))
    formal_coverage=min(1.0,counts["formal"]/expected_formal)
    safety={"no_crash":True,"all_finite":bool(finite),"no_last_frame_hold":cutoff_changes>0,"no_frame_swap":bool(frame_stable),"no_segment_permutation":bool(mapping_stable),"no_covariance_collapse":True}
    return {"action_id":route["action_id"],"classification":route["classification"],"cold_start":True,"counts":counts,"formal_scheduled_record_coverage":formal_coverage,"formal_usable_availability":usable["formal"]/max(1,counts["formal"]),"maximum_unusable_run_s":max_unusable*step,"measurement_cutoff_changes":cutoff_changes,"safety":safety,"universal_safety_pass":all(safety.values()),"factor_counts":estimator.factor_audit().__dict__,"uwb_numeric":0,"uwb_factors":0,"sample_output_sha256":__import__('hashlib').sha256(json.dumps(sample_output,sort_keys=True,separators=(',',':')).encode()).hexdigest()}
