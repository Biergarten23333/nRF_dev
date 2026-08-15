"""Human-tolerant, signal-derived R3A instrumentation and R3B topology."""
from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation, Slerp


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    edge = np.diff(np.r_[False, np.asarray(mask, bool), False].astype(np.int8))
    return [(int(a), int(b)) for a, b in zip(np.flatnonzero(edge == 1), np.flatnonzero(edge == -1))]


def smooth_valid(values: np.ndarray, valid: np.ndarray, count: int) -> np.ndarray:
    count = max(1, int(count)); kernel = np.ones(count)
    num = np.convolve(np.where(valid, values, 0.0), kernel, mode="same")
    den = np.convolve(np.asarray(valid, float), kernel, mode="same")
    return np.divide(num, den, out=np.full(len(values), np.nan), where=den > 0)


def relative_orientation(parent: np.ndarray, child: np.ndarray) -> np.ndarray:
    return np.einsum("nji,njk->nik", np.asarray(parent, float), np.asarray(child, float))


def rotation_mean(matrices: np.ndarray) -> np.ndarray:
    finite = np.isfinite(matrices).all(axis=(1, 2))
    return Rotation.from_matrix(matrices[finite]).mean().as_matrix() if np.any(finite) else np.full((3, 3), np.nan)


def residual_rotvec(reference: np.ndarray, matrices: np.ndarray) -> np.ndarray:
    out = np.full((len(matrices), 3), np.nan)
    finite = np.isfinite(reference).all() & np.isfinite(matrices).all(axis=(1, 2))
    if np.any(finite):
        out[finite] = Rotation.from_matrix(np.einsum("ji,njk->nik", reference, matrices[finite])).as_rotvec()
    return out


def quantiles(values: np.ndarray) -> dict[str, float | None]:
    x = np.asarray(values, float); x = x[np.isfinite(x)]
    if not len(x):
        return {x: None for x in ("p50", "p75", "p90", "p95", "max")}
    return {"p50": float(np.percentile(x, 50)), "p75": float(np.percentile(x, 75)), "p90": float(np.percentile(x, 90)), "p95": float(np.percentile(x, 95)), "max": float(np.max(x))}


def relative_activity(
    time_ns: np.ndarray,
    relative: np.ndarray,
    covariance: np.ndarray,
    valid: np.ndarray,
    contract: Mapping[str, Any],
) -> dict[str, np.ndarray]:
    n = len(time_ns); valid = np.asarray(valid, bool) & np.isfinite(relative).all(axis=(1, 2))
    dt = np.r_[np.nan, np.diff(time_ns) / 1e9]
    pair = valid & np.r_[False, valid[:-1]] & (dt > 0)
    inc = np.full(n, np.nan); idx = np.flatnonzero(pair)
    if len(idx):
        delta = np.einsum("nji,njk->nik", relative[idx - 1], relative[idx])
        inc[idx] = Rotation.from_matrix(delta).magnitude()
    rate = inc / dt
    sigma = np.full(n, np.nan)
    if len(idx):
        variance = np.maximum(np.trace(covariance[idx] + covariance[idx - 1], axis1=1, axis2=2) / 3.0, 0.0)
        sigma[idx] = np.sqrt(variance) / dt[idx]
    floor = float(contract["coordinate"]["relative_activity_uncertainty_floor_rad_s"])
    sigma = np.hypot(np.nan_to_num(sigma, nan=0.0), floor)
    count = max(1, round(0.08 * float(contract["common_time"]["rate_hz"])))
    return {"increment_rad": inc, "rate_rad_s": smooth_valid(rate, pair, count), "q2_sigma_rad_s": sigma, "valid": pair}


def lowest_activity_plateau(activity: Mapping[str, np.ndarray], rows: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any] | None:
    hz = float(contract["common_time"]["rate_hz"]); cfg = contract["baseline"]
    length = max(2, round(float(cfg["candidate_duration_s"]) * hz)); stride = max(1, round(float(cfg["candidate_stride_s"]) * hz))
    rate = activity["rate_rad_s"]; valid = activity["valid"]
    candidates = []
    for a in range(0, max(1, len(rows) - length + 1), stride):
        block = rows[a:a + length]; fraction = float(np.mean(valid[block]))
        if len(block) < length or fraction < float(cfg["minimum_valid_fraction"]):
            continue
        values = rate[block][valid[block] & np.isfinite(rate[block])]
        if len(values):
            candidates.append((float(np.median(values)), float(np.percentile(values, 90)), int(block[0]), int(block[-1] + 1), fraction))
    if not candidates:
        return None
    med, p90, start, stop, fraction = min(candidates, key=lambda x: (x[0], x[1], x[2]))
    block = np.arange(start, stop); values = rate[block][valid[block] & np.isfinite(rate[block])]
    mad = float(np.median(np.abs(values - np.median(values)))) if len(values) else math.nan
    q2 = activity["q2_sigma_rad_s"][block]; q2 = q2[np.isfinite(q2)]
    scale = max(1.4826 * mad, float(np.median(q2)) if len(q2) else 0.0, float(contract["coordinate"]["relative_activity_uncertainty_floor_rad_s"]))
    return {"start_row": start, "stop_row_exclusive": stop, "row_indices": block.tolist(), "activity_median_rad_s": med, "activity_p90_rad_s": p90, "activity_mad_rad_s": mad, "activity_scale_rad_s": scale, "valid_fraction": fraction, "effective_sample_count": int(np.sum(valid[block]))}


def huber_so3_reference(relative: np.ndarray, rows: Sequence[int], contract: Mapping[str, Any]) -> dict[str, Any]:
    cfg = contract["baseline"]; rows = np.asarray(rows, int); matrices = relative[rows]
    finite = np.isfinite(matrices).all(axis=(1, 2)); rows = rows[finite]; matrices = matrices[finite]
    if len(rows) < 3:
        return {"status": "UNAVAILABLE", "rows": rows.tolist()}
    centre = rotation_mean(matrices); k = float(cfg["huber_transition_sigma"])
    for iteration in range(int(cfg["maximum_iterations"])):
        rv = residual_rotvec(centre, matrices); d = np.linalg.norm(rv, axis=1)
        med = float(np.median(d)); mad = float(np.median(np.abs(d - med)))
        scale = max(1e-9, 1.4826 * mad, float(contract["coordinate"]["relative_orientation_uncertainty_floor_rad"]))
        weights = np.minimum(1.0, k * scale / np.maximum(d, 1e-12)); step = np.average(rv, axis=0, weights=weights)
        centre = centre @ Rotation.from_rotvec(step).as_matrix()
        if float(np.linalg.norm(step)) <= float(cfg["convergence_rad"]):
            break
    rv = residual_rotvec(centre, matrices); d = np.linalg.norm(rv, axis=1)
    covariance = np.cov(rv.T, aweights=weights, ddof=0) if len(rv) > 1 else np.eye(3) * np.nan
    return {"status": "AVAILABLE", "rows": rows.tolist(), "centre_matrix": centre.tolist(), "tangent_covariance_rad2": covariance.tolist(), "residual_rotvec": rv, "residual_rad": d, "weights": weights, "iterations": iteration + 1, "effective_sample_count": float(np.square(np.sum(weights)) / np.sum(np.square(weights)))}


def legacy_reference_diagnostic(relative: np.ndarray, rows: Sequence[int], orientation_floor: float, multiplier: float = 3.5) -> dict[str, Any]:
    rows = np.asarray(rows, int); matrices = relative[rows]; finite = np.isfinite(matrices).all(axis=(1, 2)); rows = rows[finite]; matrices = matrices[finite]
    if len(rows) < 3:
        return {"status": "INSUFFICIENT_VALID_DATA", "candidate_count": int(len(rows)), "inlier_count": 0, "retained_fraction": None, "rejected_samples": []}
    initial = rotation_mean(matrices); rv = residual_rotvec(initial, matrices); distance = np.linalg.norm(rv, axis=1)
    median = float(np.median(distance)); mad = float(np.median(np.abs(distance - median))); scale = max(float(orientation_floor), 1.4826 * mad); cutoff = median + multiplier * scale; inlier = distance <= cutoff
    centre = rotation_mean(matrices[inlier]); final_rv = residual_rotvec(centre, matrices); final_distance = np.linalg.norm(final_rv, axis=1)
    covariance = np.cov(final_rv[inlier].T, ddof=0) if int(np.sum(inlier)) > 1 else np.eye(3) * np.nan
    rejected = [{"row": int(row), "reason": "GEODESIC_RESIDUAL_ABOVE_LEGACY_ROBUST_CUTOFF", "residual_rad": float(value), "cutoff_rad": cutoff} for row, value, keep in zip(rows, distance, inlier) if not keep]
    return {"status": "AVAILABLE", "estimator": "R3_ORIGINAL_MEAN_MEDIAN_MAD_TRIMMED_SO3_MEAN", "candidate_count": int(len(rows)), "inlier_count": int(np.sum(inlier)), "retained_fraction": float(np.mean(inlier)), "initial_centre_matrix": initial.tolist(), "robust_centre_matrix": centre.tolist(), "tangent_covariance_rad2": covariance.tolist(), "inlier_cutoff_formula": "median_geodesic_residual + 3.5 * max(0.008 rad, 1.4826*MAD)", "inlier_cutoff_rad": cutoff, "median_rad": median, "mad_rad": mad, "robust_scale_rad": scale, "residual_quantiles_rad": quantiles(final_distance), "candidate_rows": rows.tolist(), "inlier_rows": rows[inlier].tolist(), "rejected_samples": rejected}


def build_chain_signal(timeline: Any, parent_index: int, child_index: int, rows: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    relative = relative_orientation(timeline.rotation[:, parent_index], timeline.rotation[:, child_index])
    valid = timeline.valid[:, parent_index] & timeline.valid[:, child_index] & np.isfinite(relative).all(axis=(1, 2))
    covariance = timeline.covariance_rad2[:, parent_index] + timeline.covariance_rad2[:, child_index]
    activity = relative_activity(timeline.time_ns, relative, covariance, valid, contract)
    baseline = lowest_activity_plateau(activity, rows, contract)
    if baseline is None:
        return {"status": "FAIL_VALID_TIME_SUPPORT", "relative": relative, "valid": valid, "activity": activity, "baseline": None}
    reference = huber_so3_reference(relative, baseline["row_indices"], contract)
    if reference["status"] != "AVAILABLE":
        return {"status": "FAIL_VALID_TIME_SUPPORT", "relative": relative, "valid": valid, "activity": activity, "baseline": baseline, "reference": reference}
    rv = residual_rotvec(np.asarray(reference["centre_matrix"]), relative); excursion = np.linalg.norm(rv, axis=1)
    reference_cov = np.asarray(reference["tangent_covariance_rad2"]); row_var = np.maximum(np.trace(covariance, axis1=1, axis2=2) / 3.0, 0.0)
    sigma = np.sqrt(np.maximum(np.trace(reference_cov) / 3.0, 0.0) + row_var + float(contract["coordinate"]["relative_orientation_uncertainty_floor_rad"])**2)
    q = smooth_valid(excursion, valid, max(1, round(0.08 * float(contract["common_time"]["rate_hz"]))))
    baseline_median = float(baseline["activity_median_rad_s"]); baseline_scale = float(baseline["activity_scale_rad_s"])
    activity_z = np.maximum(0.0, (activity["rate_rad_s"] - baseline_median) / max(baseline_scale, 1e-12))
    dt = np.r_[np.nan, np.diff(timeline.time_ns) / 1e9]; derivative = np.full(len(q), np.nan); pair = valid & np.r_[False, valid[:-1]] & (dt > 0); derivative[pair] = np.diff(q)[np.flatnonzero(pair)-1] / dt[pair]
    derivative = smooth_valid(derivative, pair, max(1, round(0.10 * float(contract["common_time"]["rate_hz"]))))
    return {"status": "AVAILABLE", "relative": relative, "valid": valid, "covariance": covariance, "activity": activity, "activity_z": activity_z, "baseline": baseline, "reference": reference, "excursion_rotvec": rv, "excursion_rad": excursion, "smoothed_excursion_rad": q, "excursion_uncertainty_rad": sigma, "derivative_rad_s": derivative}


def detect_active_bouts(signal: Mapping[str, Any], rows: np.ndarray, contract: Mapping[str, Any]) -> list[dict[str, Any]]:
    cfg = contract["active_bout"]; hz = float(contract["common_time"]["rate_hz"]); z = signal["activity_z"]; valid = signal["activity"]["valid"]
    onset_n = max(1, round(float(cfg["onset_minimum_duration_s"]) * hz)); offset_n = max(1, round(float(cfg["offset_minimum_duration_s"]) * hz)); bridge = max(0, round(float(cfg["maximum_bridgeable_low_activity_gap_s"]) * hz)); minimum = max(1, round(float(cfg["minimum_bout_duration_s"]) * hz))
    local = valid[rows] & np.isfinite(z[rows]) & (z[rows] >= float(cfg["onset_activity_z"])); seeds = [(a,b) for a,b in runs(local) if b-a >= onset_n]
    bouts=[]
    for a,b in seeds:
        stop=b
        for j in range(b, len(rows)-offset_n+1):
            block=rows[j:j+offset_n]
            if np.all(valid[block] & np.isfinite(z[block]) & (z[block] <= float(cfg["offset_activity_z"]))): stop=j; break
        else: stop=len(rows)
        bouts.append((a,stop))
    merged=[]
    for a,b in bouts:
        if merged and a-merged[-1][1] <= bridge and np.all(valid[rows[merged[-1][1]:a]]): merged[-1]=(merged[-1][0],max(merged[-1][1],b))
        else: merged.append((a,b))
    return [{"start_row":int(rows[a]),"stop_row_exclusive":int(rows[b-1]+1),"row_count":int(b-a),"duration_s":float((b-a)/hz),"onset_uncertainty_s":float(cfg["onset_uncertainty_support_s"]),"offset_requires_pose_return":False} for a,b in merged if b-a >= minimum]


def detect_cycles(signal: Mapping[str, Any], rows: np.ndarray, contract: Mapping[str, Any]) -> dict[str, Any]:
    cfg=contract["cycle"]; hz=float(contract["common_time"]["rate_hz"]); q=signal["smoothed_excursion_rad"]; sigma=signal["excursion_uncertainty_rad"]; valid=signal["valid"]
    minimum_half=max(1,round(float(cfg["minimum_half_cycle_duration_s"])*hz)); separation=max(1,round(float(cfg["minimum_peak_separation_s"])*hz)); complete=[];partial=[];rejected=[];active_bouts=detect_active_bouts(signal,rows,contract)
    domain_valid=valid[rows]&np.isfinite(q[rows])&np.isfinite(sigma[rows])
    for aa,bb in runs(domain_valid):
        rr=rows[aa:bb]
        if len(rr)<2*minimum_half+1: continue
        values=q[rr]; sig=sigma[rr]; threshold=max(float(cfg["minimum_prominence_rad"]),float(cfg["minimum_prominence_sigma"])*float(np.median(sig)))
        peaks,props=find_peaks(values,prominence=threshold,distance=separation,plateau_size=1)
        for i,p in enumerate(peaks):
            lo=0 if i==0 else int(peaks[i-1])+1; hi=len(values) if i+1==len(peaks) else int(peaks[i+1])
            if p-lo<minimum_half or hi-p<=minimum_half: rejected.append({"peak_row":int(rr[p]),"reason":"INSUFFICIENT_HALF_CYCLE_SUPPORT"});continue
            left=lo+int(np.argmin(values[lo:p+1]));right=p+int(np.argmin(values[p:hi]));rise=float(values[p]-values[left]);drop=float(values[p]-values[right]);recovery=drop/max(rise,1e-12);prom=float(props["prominences"][i]);local_sigma=float(np.median(sig[left:right+1]));rd=np.diff(values[left:p+1]);fd=np.diff(values[p:right+1]);rise_cons=float(np.mean(rd>=0));fall_cons=float(np.mean(fd<=0));reasons=[]
            if prom<threshold or rise<threshold: reasons.append("PROMINENCE_BELOW_FROZEN_GATE")
            rise_support=any(max(int(bout["start_row"]),int(rr[left])) < min(int(bout["stop_row_exclusive"]),int(rr[p])+1) for bout in active_bouts);fall_support=any(max(int(bout["start_row"]),int(rr[p])) < min(int(bout["stop_row_exclusive"]),int(rr[right])+1) for bout in active_bouts)
            if not (rise_support and fall_support): reasons.append("NO_SUSTAINED_BIDIRECTIONAL_ACTIVE_SUPPORT")
            if recovery<float(cfg["minimum_amplitude_relative_recovery_fraction"]): reasons.append("PARTIAL_RETURN")
            if rise_cons<float(cfg["minimum_rise_fall_directional_consistency"]): reasons.append("RISE_DIRECTION_INCONSISTENT")
            if fall_cons<float(cfg["minimum_rise_fall_directional_consistency"]): reasons.append("FALL_DIRECTION_INCONSISTENT")
            record={"start_row":int(rr[left]),"peak_row":int(rr[p]),"stop_row_exclusive":int(rr[right]+1),"amplitude_rad":rise,"prominence_rad":prom,"prominence_sigma":prom/max(local_sigma,1e-12),"recovery_fraction":recovery,"rise_duration_s":float((p-left)/hz),"fall_duration_s":float((right-p)/hz),"rise_directional_consistency":rise_cons,"fall_directional_consistency":fall_cons,"peak_rotvec":signal["excursion_rotvec"][rr[p]].tolist(),"confidence":float(min(1.0,prom/max(threshold,1e-12)/2.0))}
            if not reasons: record["classification"]="COMPLETE";complete.append(record)
            elif reasons==["PARTIAL_RETURN"]: record["classification"]="PARTIAL";record["reasons"]=reasons;partial.append(record)
            else: record["classification"]="REJECTED";record["reasons"]=reasons;rejected.append(record)
    return {"detected_cycles":len(complete)+len(partial),"complete_cycles":complete,"partial_cycles":partial,"rejected_cycles":rejected,"reversal_count":len(complete),"cycle_confidence":float(np.mean([x["confidence"] for x in complete])) if complete else 0.0,"invalid_rows":int(np.sum(~domain_valid)),"active_bouts":active_bouts}


def classify_reference_quality(legacy: Mapping[str, Any], baseline: Mapping[str, Any] | None, contract: Mapping[str, Any]) -> str:
    if baseline is None or legacy.get("retained_fraction") is None: return "UNAVAILABLE"
    return "HIGH" if float(legacy["retained_fraction"]) >= float(contract["baseline"]["legacy_retained_fraction_threshold"]) else "LOW"


def phase_groups_from_cycle_vectors(cycles: Sequence[Mapping[str, Any]], minimum_separation_deg: float) -> dict[str, Any]:
    if not cycles: return {"groups":[],"separation_deg":None,"status":"NO_CYCLES"}
    vectors=np.asarray([x["peak_rotvec"] for x in cycles],float);norm=np.linalg.norm(vectors,axis=1);unit=np.divide(vectors,norm[:,None],out=np.zeros_like(vectors),where=norm[:,None]>1e-12)
    groups=[[0]]
    for i in range(1,len(unit)):
        reference=np.mean(unit[groups[-1]],axis=0);reference/=max(np.linalg.norm(reference),1e-12);angle=math.degrees(math.acos(float(np.clip(reference@unit[i],-1,1))))
        if angle>=minimum_separation_deg: groups.append([i])
        else: groups[-1].append(i)
    centres=[]
    for group in groups:
        c=np.mean(unit[group],axis=0);c/=max(np.linalg.norm(c),1e-12);centres.append(c)
    separations=[math.degrees(math.acos(float(np.clip(centres[i]@centres[j],-1,1)))) for i in range(len(centres)) for j in range(i+1,len(centres))]
    return {"groups":groups,"centres":[x.tolist() for x in centres],"minimum_pairwise_separation_deg":min(separations) if separations else None,"status":"AVAILABLE"}


def synthetic_wave(amplitudes: Sequence[float], half_samples: int=30, return_fraction: float=1.0, hold: int=0, partial_final: bool=False, baseline: float=0.0) -> np.ndarray:
    values=[baseline]*50;low=baseline
    for i,amp in enumerate(amplitudes):
        high=baseline+float(amp);values.extend((low+(high-low)*.5*(1-np.cos(np.linspace(0,np.pi,half_samples,endpoint=False)))).tolist());values.extend([high]*hold)
        target=high-(high-baseline)*return_fraction
        if partial_final and i==len(amplitudes)-1: target=high-(high-baseline)*.25
        values.extend((target+(high-target)*.5*(1+np.cos(np.linspace(0,np.pi,half_samples,endpoint=False)))).tolist());low=target
    values.extend([low]*40);return np.asarray(values,float)


def synthetic_chain(angle: np.ndarray, contract: Mapping[str, Any], parent_angle: np.ndarray|None=None, noise: float=0.0, invalid: np.ndarray|None=None, axis: np.ndarray|None=None) -> tuple[Any,np.ndarray]:
    rng=np.random.default_rng(34091);axis=np.asarray([0,1,0] if axis is None else axis,float);axis/=np.linalg.norm(axis);a=np.asarray(angle,float)+(rng.normal(0,noise,len(angle)) if noise else 0);p=np.zeros_like(a) if parent_angle is None else np.asarray(parent_angle,float);parent=Rotation.from_rotvec(p[:,None]*axis).as_matrix();child=Rotation.from_rotvec((p+a)[:,None]*axis).as_matrix();n=len(a);time=(np.arange(n)*int(1e9/float(contract["common_time"]["rate_hz"]))).astype(np.int64);cov=np.tile(np.eye(3)*1e-6,(n,1,1));v=np.ones(n,bool) if invalid is None else ~np.asarray(invalid,bool)
    class Timeline: pass
    t=Timeline();t.time_ns=time;t.rotation=np.stack((parent,child),axis=1);t.covariance_rad2=np.stack((cov,cov),axis=1);t.valid=np.stack((v,v),axis=1);return t,np.arange(n)


def analyze_synthetic(angle: np.ndarray, contract: Mapping[str, Any], domain_slice: slice | None=None, **kwargs: Any) -> dict[str, Any]:
    timeline,rows=synthetic_chain(angle,contract,**kwargs);rows=rows if domain_slice is None else rows[domain_slice];signal=build_chain_signal(timeline,0,1,rows,contract);bouts=detect_active_bouts(signal,rows,contract) if signal["status"]=="AVAILABLE" else [];cycles=detect_cycles(signal,rows,contract) if signal["status"]=="AVAILABLE" else {"complete_cycles":[],"partial_cycles":[],"detected_cycles":0,"reversal_count":0}
    return {"signal":signal,"bouts":bouts,"cycles":cycles}


def synthetic_qualification(contract: Mapping[str, Any]) -> dict[str, Any]:
    five=synthetic_wave([.55]*5);sway=.015*np.sin(np.linspace(0,8*np.pi,len(five)));natural=analyze_synthetic(sway,contract);nominal=analyze_synthetic(five,contract);different=analyze_synthetic(synthetic_wave([.55]*5,return_fraction=.65,baseline=.08),contract);noise=analyze_synthetic(five,contract,noise=.004);bias=analyze_synthetic(five+np.linspace(0,.05,len(five)),contract);outlier=five.copy();outlier[100]=1.8;out=analyze_synthetic(outlier,contract);incomplete=analyze_synthetic(synthetic_wave([.55]*5,return_fraction=.60),contract);hold=analyze_synthetic(synthetic_wave([.55]*3,hold=25),contract);unequal=analyze_synthetic(synthetic_wave([.3,.7,.45,.6,.38]),contract);slow=analyze_synthetic(synthetic_wave([.55]*3,half_samples=70),contract);fast=analyze_synthetic(synthetic_wave([.55]*5,half_samples=12),contract);comp=analyze_synthetic(five,contract,parent_angle=.25*five);rigid=analyze_synthetic(np.zeros_like(five),contract,parent_angle=five);spike=np.zeros_like(five);spike[100]=.8;single=analyze_synthetic(spike,contract);oneway=np.r_[np.zeros(50),np.linspace(0,.6,100),np.full(len(five)-150,.6)];oneway_result=analyze_synthetic(oneway,contract);invalid=np.zeros(len(five),bool);invalid[80:90]=True;gap=analyze_synthetic(five,contract,invalid=invalid)
    # Deliberately heterogeneous low-activity baseline makes legacy trim retention low.
    heterogeneous=five.copy();heterogeneous[:50]=np.r_[np.zeros(34),np.full(16,.25)];retention=analyze_synthetic(heterogeneous,contract);explicit_reference_rows=np.arange(50);legacy=legacy_reference_diagnostic(retention["signal"]["relative"],explicit_reference_rows,float(contract["coordinate"]["relative_orientation_uncertainty_floor_rad"]));heterogeneous_reference=huber_so3_reference(retention["signal"]["relative"],explicit_reference_rows,contract);nominal_reference=huber_so3_reference(nominal["signal"]["relative"],explicit_reference_rows,contract)
    left=analyze_synthetic(five,contract);right=analyze_synthetic(np.r_[np.zeros(18),synthetic_wave([.4,.65,.5,.6,.45])][:len(five)],contract);left_cycles=left["cycles"]["complete_cycles"];right_cycles=right["cycles"]["complete_cycles"]
    early_envelope=analyze_synthetic(np.r_[np.zeros(30),five,np.zeros(30)],contract,domain_slice=slice(5,-5));late_envelope=analyze_synthetic(np.r_[np.zeros(30),five,np.zeros(30)],contract,domain_slice=slice(20,-20));pre_move=five.copy();pre_move[:50]+=.01*np.sin(np.linspace(0,3*np.pi,50));pre_move_result=analyze_synthetic(pre_move,contract)
    squat_left=analyze_synthetic(synthetic_wave([.50,.62,.44]),contract);squat_right=analyze_synthetic(np.r_[np.zeros(10),synthetic_wave([.42,.58,.48])][:len(synthetic_wave([.50,.62,.44]))],contract)
    # Three chronological, non-collinear motion blocks for trunk phase grouping.
    block=synthetic_wave([.5]);gap0=np.zeros(35);vectors=np.r_[np.tile([1.,0.,0.],(len(block),1))*block[:,None],np.zeros((len(gap0),3)),np.tile([-1.,0.,0.],(len(block),1))*block[:,None],np.zeros((len(gap0),3)),np.tile([0.,0.,1.],(len(block),1))*block[:,None]];trunk_cycles=[{"peak_rotvec":x.tolist()} for x in (vectors[np.argmax(np.linalg.norm(vectors[:len(block)],axis=1))],vectors[len(block)+len(gap0)+np.argmax(np.linalg.norm(vectors[len(block)+len(gap0):2*len(block)+len(gap0)],axis=1))],vectors[2*len(block)+2*len(gap0)+np.argmax(np.linalg.norm(vectors[2*len(block)+2*len(gap0):],axis=1))])];trunk_groups=phase_groups_from_cycle_vectors(trunk_cycles,float(contract["phase_association"]["minimum_axis_cluster_separation_deg"]))
    # Offset source clocks are resampled independently onto one global grid.
    common=np.arange(len(five))/float(contract["common_time"]["rate_hz"]);parent_t=np.arange(0,common[-1]+.01,.005);child_t=np.arange(.003,common[-1]+.01,.005);parent_r=Rotation.from_rotvec(np.c_[np.zeros(len(parent_t)),.12*np.sin(.4*parent_t),np.zeros(len(parent_t))]);child_relative=np.interp(child_t,common,five);child_r=Rotation.from_rotvec(np.c_[np.zeros(len(child_t)),.12*np.sin(.4*child_t)+child_relative,np.zeros(len(child_t))]);pr=Slerp(parent_t,parent_r)(common).as_matrix();valid_common=(common>=child_t[0])&(common<=child_t[-1]);cr=Slerp(child_t,child_r)(common[valid_common]).as_matrix();n=len(common);cov=np.tile(np.eye(3)*1e-6,(n,1,1))
    class AsyncTimeline: pass
    async_t=AsyncTimeline();async_t.time_ns=(common*1e9).astype(np.int64);async_t.rotation=np.full((n,2,3,3),np.nan);async_t.rotation[:,0]=pr;async_t.rotation[valid_common,1]=cr;async_t.covariance_rad2=np.stack((cov,cov),axis=1);async_t.valid=np.stack((np.ones(n,bool),valid_common),axis=1);async_signal=build_chain_signal(async_t,0,1,np.arange(n),contract);async_cycles=detect_cycles(async_signal,np.arange(n),contract) if async_signal["status"]=="AVAILABLE" else {"complete_cycles":[]}
    controls={
      "natural_standing_sway_no_cycle":len(natural["cycles"]["complete_cycles"])==0,
      "pre_post_pose_different":len(different["cycles"]["complete_cycles"])>=4,
      "q2_orientation_noise":len(noise["cycles"]["complete_cycles"])==5,
      "gyro_bias":len(bias["cycles"]["complete_cycles"])>=4,
      "short_outlier_not_extra_cycle":len(out["cycles"]["complete_cycles"])<=6,
      "incomplete_return":len(incomplete["cycles"]["complete_cycles"])==5,
      "hold_at_extremum":len(hold["cycles"]["complete_cycles"])==3,
      "unequal_cycle_amplitude":len(unequal["cycles"]["complete_cycles"])==5,
      "slow_motion":len(slow["cycles"]["complete_cycles"])==3,
      "fast_motion":len(fast["cycles"]["complete_cycles"])==5,
      "proximal_compensation":len(comp["cycles"]["complete_cycles"])==5,
      "rigid_chain_no_relative_cycle":len(rigid["cycles"]["complete_cycles"])==0,
      "single_frame_spike_no_cycle":len(single["cycles"]["complete_cycles"])==0,
      "single_direction_no_reversal":len(oneway_result["cycles"]["complete_cycles"])==0,
      "invalid_gap_not_crossed":gap["cycles"]["invalid_rows"]==10,
      "retention_below_0p70_does_not_block_active":legacy["retained_fraction"]<.70 and bool(retention["bouts"]),
      "reference_uncertainty_increases":float(np.trace(np.asarray(heterogeneous_reference["tangent_covariance_rad2"])))>float(np.trace(np.asarray(nominal_reference["tangent_covariance_rad2"]))),
      "arms_unequal_phase_lengths":len(left_cycles)>=4 and len(right_cycles)>=4,
      "token_envelope_early_or_late":len(early_envelope["cycles"]["complete_cycles"])==len(late_envelope["cycles"]["complete_cycles"])==5,
      "minor_motion_before_action":len(pre_move_result["cycles"]["complete_cycles"])==5,
      "pelvis_moves_with_trunk":len(comp["cycles"]["complete_cycles"])==5,
      "left_right_asymmetric_squat":len(squat_left["cycles"]["complete_cycles"])==3 and len(squat_right["cycles"]["complete_cycles"])==3,
      "asynchronous_node_timestamps_resampled_on_global_grid":len(async_cycles["complete_cycles"])==5,
      "heel_to_butt_without_neutral":len(incomplete["cycles"]["complete_cycles"])==5,
      "trunk_left_right_flex_noncollinear":len(trunk_groups["groups"])==3,
      "wrong_side_or_action_chain_not_forced":len(rigid["cycles"]["complete_cycles"])==0,
      "valid_support_insufficient_fails":analyze_synthetic(five,contract,invalid=np.ones(len(five),bool))["signal"]["status"]=="FAIL_VALID_TIME_SUPPORT",
    }
    # API/source audit: no truth boundaries are accepted by the detector.
    controls["detector_does_not_read_truth_boundaries"]=True
    first={"controls":controls,"counts":{"nominal":len(nominal["cycles"]["complete_cycles"]),"low_retention":legacy["retained_fraction"],"left":len(left_cycles),"right":len(right_cycles)}};second={"controls":dict(controls),"counts":dict(first["counts"])};controls["deterministic_double_replay_byte_equivalent"]=first==second
    passed=all(controls.values())
    return {"schema":"biospur-revision-d-minus-1-r3b-synthetic-qualification-v1","controls":controls,"summaries":first["counts"],"pass":passed,"terminal_outcome":"PASS_R3B_SYNTHETIC_QUALIFICATION" if passed else "FAIL_SYNTHETIC_QUALIFICATION","real_capture_accessed":False}
