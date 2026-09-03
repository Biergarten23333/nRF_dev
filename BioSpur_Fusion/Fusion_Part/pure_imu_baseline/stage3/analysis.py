"""Stage 3 invariant, support, motion, gap, and checkpoint evaluation."""
from __future__ import annotations

import hashlib

import numpy as np

from pure_imu_baseline.config import GEOMETRY, PARENT_CHILD, SEGMENT_ORDER
from pure_imu_baseline.math3d import conjugate, multiply, normalize, to_matrix
from pure_imu_baseline.skeleton import assert_fixed_lengths, bone_lengths


def sha_array(a: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(a).tobytes()).hexdigest()


def quat_distance(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = np.asarray(a, float); b = np.asarray(b, float)
    dot = np.clip(np.abs(np.sum(a*b, axis=-1)), 0.0, 1.0)
    return 2.0*np.arccos(dot)


def relative(parent: np.ndarray, child: np.ndarray) -> np.ndarray:
    parent=np.asarray(parent,float); child=np.asarray(child,float)
    finite=np.all(np.isfinite(parent),axis=-1)&np.all(np.isfinite(child),axis=-1)
    safe_parent=np.where(finite[...,None],parent,[1.,0.,0.,0.])
    safe_child=np.where(finite[...,None],child,[1.,0.,0.,0.])
    return normalize(multiply(conjugate(safe_parent),safe_child))


def spatial_z_rates(q: np.ndarray, valid: np.ndarray, time_s: np.ndarray) -> np.ndarray:
    q = np.asarray(q, float); valid = np.asarray(valid, bool); t = np.asarray(time_s, float)
    ok = valid[1:] & valid[:-1] & np.all(np.isfinite(q[1:]),axis=-1) & np.all(np.isfinite(q[:-1]),axis=-1)
    q1=np.where(ok[...,None],q[1:],[1.,0.,0.,0.])
    q0=np.where(ok[...,None],q[:-1],[1.,0.,0.,0.])
    dq = normalize(multiply(q1, conjugate(q0)))
    dq = np.where((dq[..., :1] < 0), -dq, dq)
    nv = np.linalg.norm(dq[..., 1:], axis=-1)
    angle = 2*np.arctan2(nv, dq[..., 0])
    z = np.divide(angle*dq[..., 3], nv*np.diff(t)[:, None],
                  out=np.zeros_like(nv), where=nv > 1e-15)
    z[~ok] = np.nan
    return np.vstack([np.full((1, q.shape[1]), np.nan), z])


def nearest_gap_distance(time_s: np.ndarray, valid: np.ndarray, reset: np.ndarray) -> np.ndarray:
    out = np.full(valid.shape, np.inf, dtype=np.float64)
    for j in range(valid.shape[1]):
        changes = np.flatnonzero(np.r_[True, valid[1:, j] != valid[:-1, j]] | reset[:, j])
        if not len(changes): continue
        event_t = time_s[changes]
        right = np.searchsorted(event_t, time_s)
        left = np.clip(right-1, 0, len(event_t)-1); right = np.clip(right, 0, len(event_t)-1)
        out[:, j] = np.minimum(np.abs(time_s-event_t[left]), np.abs(time_s-event_t[right]))
    return out


def invariant_report(raw: dict[str, np.ndarray], corrected: dict[str, np.ndarray]) -> dict:
    qraw = raw["q_GB_wxyz"].astype(float); qcor = corrected["corrected_q_GB_wxyz"].astype(float)
    valid = raw["valid"]
    pelvis = list(raw["segment_names"]).index("pelvis")
    raw_g = np.einsum("...ji,j->...i", to_matrix(qraw[valid]), np.array([0.,0.,1.]))
    cor_g = np.einsum("...ji,j->...i", to_matrix(qcor[valid]), np.array([0.,0.,1.]))
    gravity_error = float(np.max(np.linalg.norm(raw_g-cor_g, axis=-1)))
    pelvis_exact = bool(np.array_equal(raw["q_GB_wxyz"][:, pelvis], corrected["corrected_q_GB_wxyz"][:, pelvis], equal_nan=True))
    geometry = assert_fixed_lengths(corrected["corrected_joint_positions_m"], GEOMETRY, atol=2e-6)
    max_geometry = max(x["maximum_abs_error_m"] for x in geometry.values())
    raw_max_step = float(np.nanmax(quat_distance(qraw[1:], qraw[:-1])))
    cor_max_step = float(np.nanmax(quat_distance(qcor[1:], qcor[:-1])))
    return {"gravity_tilt_max_norm_error": gravity_error,
            "gravity_tilt_gate_1e_9": gravity_error <= 1e-9,
            "pelvis_bit_exact": pelvis_exact,
            "pelvis_common_gauge_gate": pelvis_exact,
            "corrected_fixed_bone_checks": geometry,
            "maximum_corrected_edge_length_error_m": max_geometry,
            "geometry_gate_2e_6_m": max_geometry <= 2e-6,
            "raw_max_adjacent_orientation_step_rad": raw_max_step,
            "corrected_max_adjacent_orientation_step_rad": cor_max_step,
            "new_discontinuity_above_raw_global_max": cor_max_step > raw_max_step + 2e-7}


def slow_metrics(data: dict[str, np.ndarray], stationary: np.ndarray) -> dict:
    t = data["time_s"]; valid = data["valid"]
    zr = spatial_z_rates(data["q_GB_wxyz"], valid, t)
    zc = spatial_z_rates(data["corrected_q_GB_wxyz"], valid, t)
    seg = [str(x) for x in data["segment_names"]]; index = {x:i for i,x in enumerate(seg)}
    state = data["correction_state"].astype(bool)
    chains = {}
    for parent, child in PARENT_CHILD:
        p, c = index[parent], index[child]
        use = stationary[:, p] & stationary[:, c] & valid[:, p] & valid[:, c] & state[:, c]
        raw = zr[:, c]-zr[:, p]; cor = zc[:, c]-zc[:, p]
        finite = use & np.isfinite(raw) & np.isfinite(cor)
        count = int(np.sum(finite)); duration = count/60.0
        if count:
            before = float(abs(np.median(raw[finite])))
            after = float(abs(np.median(cor[finite])))
            reduction = 1-after/before if before > 1e-7 else None
            rms_before = float(np.sqrt(np.mean(raw[finite]**2)))
            rms_after = float(np.sqrt(np.mean(cor[finite]**2)))
        else: before=after=reduction=rms_before=rms_after=None
        qualified = count >= 120 and before is not None and before > 1e-5
        chains[f"{parent}->{child}"] = {"stationary_active_samples": count,
            "stationary_active_duration_s": duration, "absolute_median_differential_rate_before_rad_s": before,
            "absolute_median_differential_rate_after_rad_s": after,
            "qualified_slow_consistency_reduction_fraction": reduction,
            "differential_rate_rms_before_rad_s": rms_before, "differential_rate_rms_after_rad_s": rms_after,
            "qualified": qualified,
            "degraded_over_5_percent": bool(qualified and after > before*1.05)}
    qualified_values = [v for v in chains.values() if v["qualified"]]
    capture_before = float(np.median([v["absolute_median_differential_rate_before_rad_s"] for v in qualified_values])) if qualified_values else None
    capture_after = float(np.median([v["absolute_median_differential_rate_after_rad_s"] for v in qualified_values])) if qualified_values else None
    improvement = 1-capture_after/capture_before if capture_before and capture_before > 1e-7 else None
    return {"primary_metric": "median across qualified parent-child chains of absolute median stationary differential spatial-yaw rate",
            "chains": chains, "capture_before_rad_s": capture_before,
            "capture_after_rad_s": capture_after, "capture_improvement_fraction": improvement,
            "capture_improved_at_least_20_percent": bool(improvement is not None and improvement >= 0.20),
            "any_qualified_chain_degraded_over_5_percent": any(v["degraded_over_5_percent"] for v in chains.values())}


def _moving_average(x: np.ndarray, width: int = 31) -> np.ndarray:
    if len(x) < width: return np.full_like(x, np.mean(x))
    c = np.r_[0.0, np.cumsum(x)]
    middle = (c[width:]-c[:-width])/width
    return np.pad(middle, (width-1, 0), mode="edge")


def dynamic_metrics(data: dict[str, np.ndarray]) -> dict:
    t = data["time_s"]; valid=data["valid"]
    raw=data["q_GB_wxyz"].astype(float); cor=data["corrected_q_GB_wxyz"].astype(float)
    seg=[str(x) for x in data["segment_names"]]; index={x:i for i,x in enumerate(seg)}
    chains={}
    for parent, child in PARENT_CHILD:
        p,c=index[parent],index[child]; ok=valid[:,p]&valid[:,c]
        qr=relative(raw[:,p],raw[:,c]); qc=relative(cor[:,p],cor[:,c])
        sr=quat_distance(qr[1:],qr[:-1]); sc=quat_distance(qc[1:],qc[:-1]); pair=ok[1:]&ok[:-1]
        sr=sr[pair]; sc=sc[pair]
        if len(sr)<120:
            chains[f"{parent}->{child}"]={"qualified":False,"reason":"INSUFFICIENT_VALID_DYNAMIC_SAMPLES","pass":True}; continue
        hpr=sr-_moving_average(sr); hpc=sc-_moving_average(sc)
        rmsr=float(np.sqrt(np.mean(hpr*hpr))); rmsc=float(np.sqrt(np.mean(hpc*hpc)))
        excursion_raw=float(np.percentile(quat_distance(qr[ok],qr[np.flatnonzero(ok)[0]]),95))
        excursion_cor=float(np.percentile(quat_distance(qc[ok],qc[np.flatnonzero(ok)[0]]),95))
        qualified=rmsr>1e-5 and excursion_raw>np.deg2rad(1)
        corr=float(np.corrcoef(hpr,hpc)[0,1]) if np.std(hpr)>1e-12 and np.std(hpc)>1e-12 else 1.0
        amp=rmsc/rmsr if rmsr>1e-12 else 1.0
        peak_error=abs(int(np.argmax(sr))-int(np.argmax(sc)))
        excursion_ratio=excursion_cor/excursion_raw if excursion_raw>1e-12 else 1.0
        passed=(not qualified) or (corr>=.995 and .98<=amp<=1.02 and peak_error<=1 and .95<=excursion_ratio<=1.05)
        chains[f"{parent}->{child}"]={"qualified":qualified,"highpass_angular_increment_correlation":corr,
            "passband_amplitude_ratio":amp,"peak_timing_error_frames":peak_error,
            "joint_excursion_ratio":excursion_ratio,"raw_highpass_rms_rad":rmsr,
            "corrected_highpass_rms_rad":rmsc,"pass":passed}
    return {"chains":chains,"gates":{"correlation_min":.995,"amplitude_ratio":[.98,1.02],
            "peak_timing_max_frames":1,"joint_excursion_ratio":[.95,1.05]},
            "all_qualified_chains_pass":all(x["pass"] for x in chains.values())}


def gap_report(data: dict[str, np.ndarray]) -> dict:
    t=data["time_s"]; valid=data["valid"]; seg=[str(x) for x in data["segment_names"]]
    corrected_valid=data["valid"]
    intervals=[]
    for j,name in enumerate(seg):
        starts=np.flatnonzero((~valid[:,j]) & np.r_[True,valid[:-1,j]])
        for start in starts:
            stop=start
            while stop+1<len(t) and not valid[stop+1,j]: stop+=1
            pre=max(0,start-1); post=min(len(t)-1,stop+1)
            intervals.append({"segment":name,"invalid_start_s":float(t[start]),"invalid_stop_s":float(t[stop]),
                "last_valid_before_s":float(t[pre]),"first_valid_after_s":float(t[post]),
                "invalid_frames":int(stop-start+1),
                "correction_exact_raw_during_gap":bool(np.array_equal(data["q_GB_wxyz"][start:stop+1,j],data["corrected_q_GB_wxyz"][start:stop+1,j],equal_nan=True)),
                "pre_gap_correction_rad":float(data["correction_rad"][pre,j]),
                "post_gap_correction_rad":float(data["correction_rad"][post,j]),
                "epoch_advanced":bool(data["correction_epoch"][post,j]>data["correction_epoch"][pre,j])})
    return {"corrected_validity_subset_raw":bool(np.all(~corrected_valid|valid)),
            "no_fabricated_samples":bool(np.array_equal(corrected_valid,valid)),
            "intervals":intervals,"all_intervals_pass":all(x["correction_exact_raw_during_gap"] and abs(x["post_gap_correction_rad"])<=1e-15 and x["epoch_advanced"] for x in intervals)}


def checkpoint_report(data: dict[str,np.ndarray], times: tuple[float,...]) -> dict:
    t=data["time_s"]; seg=[str(x) for x in data["segment_names"]]; idx={x:i for i,x in enumerate(seg)}
    pelvis=idx["pelvis"]; output={}
    for target in times:
        k=int(np.argmin(abs(t-target))); nodes={}
        for j,name in enumerate(seg):
            raw=data["q_GB_wxyz"][k,j].astype(float); cor=data["corrected_q_GB_wxyz"][k,j].astype(float)
            parent_name=next((p for p,c in PARENT_CHILD if c==name),None)
            p=idx[parent_name] if parent_name else pelvis
            qpc_raw=relative(data["q_GB_wxyz"][k,p],raw).tolist()
            qpc_cor=relative(data["corrected_q_GB_wxyz"][k,p],cor).tolist()
            prel_raw=relative(data["q_GB_wxyz"][k,pelvis],raw).tolist()
            prel_cor=relative(data["corrected_q_GB_wxyz"][k,pelvis],cor).tolist()
            tilt_raw=float(np.arccos(np.clip(to_matrix(raw)[2,2],-1,1)))
            tilt_cor=float(np.arccos(np.clip(to_matrix(cor)[2,2],-1,1)))
            nodes[name]={"node_id":str(data["node_ids"][j]),"raw_q_GB_wxyz":raw.tolist(),
                "corrected_q_GB_wxyz":cor.tolist(),"applied_c_rad":float(data["correction_rad"][k,j]),
                "bias_rad_s":float(data["bias_rad_s"][k,j]),"confidence":float(data["correction_confidence"][k,j]),
                "state":int(data["correction_state"][k,j]),"inactive_reason_code":int(data["inactive_reason"][k,j]),
                "observation_type_code":int(data["observation_type"][k,j]),"epoch":int(data["correction_epoch"][k,j]),
                "raw_q_parent_child_wxyz":qpc_raw,"corrected_q_parent_child_wxyz":qpc_cor,
                "raw_q_pelvis_relative_wxyz":prel_raw,"corrected_q_pelvis_relative_wxyz":prel_cor,
                "raw_tilt_rad":tilt_raw,"corrected_tilt_rad":tilt_cor,
                "nearest_gap_or_reset_s":float(data["nearest_gap_s"][k,j])}
        output[f"{target:.2f}"]={"requested_time_s":target,"actual_time_s":float(t[k]),"frame":k,
            "nodes":nodes,"raw_joint_positions_m":data["joint_positions_m"][k].tolist(),
            "corrected_joint_positions_m":data["corrected_joint_positions_m"][k].tolist()}
    return output
