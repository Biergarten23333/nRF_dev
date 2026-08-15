"""Calibration-only, non-clinical rendering-geometry feasibility audit.

This module has deliberately narrow inputs: a typed calibration ledger, the
canonical V4-io layout and a predeclared gate document.  Operator measurements
and held-out payloads are not accepted by the API.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from scipy.optimize import least_squares
from scipy.spatial.transform import Rotation

from biospur_fusion.calibration.real_capture import NODE_TO_SEGMENT, _solve_t4
from biospur_fusion.imu.frontend import audits_as_json, run_q1_attitude
from biospur_fusion.imu.q1 import quaternion_to_matrix


DIMENSIONS = (
    "rendering_forearm_length_L", "rendering_forearm_length_R",
    "rendering_shank_length_L", "rendering_shank_length_R",
    "C7Proxy_to_PelvisProxy_separation",
    "rendering_upper_arm_length_L", "rendering_upper_arm_length_R",
    "graphical_shoulder_width", "rendering_thigh_length_L",
    "rendering_thigh_length_R", "graphical_hip_width", "graphical_hip_depth",
)
PAIR_SPECS = {
    "rendering_forearm_length_L": ("BSFAA61", "BSFB165"),
    "rendering_forearm_length_R": ("BSF1120", "BSFEC35"),
    "rendering_shank_length_L": ("BSF44AD", "BSF6C53"),
    "rendering_shank_length_R": ("BSF3C79", "BSF8BC4"),
    "C7Proxy_to_PelvisProxy_separation": ("BSFC2CC", "BSF31CC"),
}
OPERATOR_MATCH = {
    "rendering_forearm_length_L": "lateral_epicondyle_to_wrist_styloid_midpoint_L",
    "rendering_forearm_length_R": "lateral_epicondyle_to_wrist_styloid_midpoint_R",
    "rendering_shank_length_L": "lateral_knee_landmark_to_malleolar_midpoint_L",
    "rendering_shank_length_R": "lateral_knee_landmark_to_malleolar_midpoint_R",
    "C7Proxy_to_PelvisProxy_separation": "C7_to_mid_PSIS",
    "rendering_upper_arm_length_L": "acromion_to_lateral_epicondyle_L",
    "rendering_upper_arm_length_R": "acromion_to_lateral_epicondyle_R",
    "graphical_shoulder_width": "biacromial_breadth",
    "rendering_thigh_length_L": "greater_trochanter_to_lateral_knee_landmark_L",
    "rendering_thigh_length_R": "greater_trochanter_to_lateral_knee_landmark_R",
    "graphical_hip_width": "ASIS_breadth",
    "graphical_hip_depth": "pelvis_anterior_posterior_depth",
}
GRAPHICAL_DEFINITION = {
    "rendering_forearm_length_L": "left lateral-epicondyle proxy to left wrist-styloid-midpoint proxy surface chord",
    "rendering_forearm_length_R": "right lateral-epicondyle proxy to right wrist-styloid-midpoint proxy surface chord",
    "rendering_shank_length_L": "left lateral-knee proxy to left malleolar-midpoint proxy surface chord",
    "rendering_shank_length_R": "right lateral-knee proxy to right malleolar-midpoint proxy surface chord",
    "C7Proxy_to_PelvisProxy_separation": "C7 proxy to mid-PSIS pelvis proxy surface chord",
    "rendering_upper_arm_length_L": "left acromion proxy to left lateral-epicondyle proxy surface chord",
    "rendering_upper_arm_length_R": "right acromion proxy to right lateral-epicondyle proxy surface chord",
    "graphical_shoulder_width": "left-to-right acromion graphical breadth",
    "rendering_thigh_length_L": "left greater-trochanter proxy to left lateral-knee proxy surface chord",
    "rendering_thigh_length_R": "right greater-trochanter proxy to right lateral-knee proxy surface chord",
    "graphical_hip_width": "left-to-right ASIS graphical breadth",
    "graphical_hip_depth": "pelvis anterior-to-posterior graphical depth",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dump_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _nearest(times: np.ndarray, targets: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    at = np.searchsorted(times, targets)
    hi = np.clip(at, 0, len(times) - 1)
    lo = np.clip(at - 1, 0, len(times) - 1)
    choose_hi = np.abs(times[hi] - targets) < np.abs(times[lo] - targets)
    indices = np.where(choose_hi, hi, lo)
    return indices, np.abs(times[indices] - targets)


def _action_windows(ledger: np.lib.npyio.NpzFile, gates: dict) -> dict[str, tuple[int, int]]:
    windows = {str(row["name"]): (int(row["start_ns"]), int(row["stop_ns"]))
               for row in ledger["action_windows"]}
    expected = list(gates["calibration_actions"])
    if set(windows) != set(expected):
        raise ValueError(f"calibration action firewall mismatch: {sorted(windows)}")
    return {name: windows[name] for name in expected}


def _q1(ledger, windows: dict[str, tuple[int, int]]) -> tuple[dict, dict]:
    initial_start, initial_stop = windows["initial_still_attempt2"]
    analysis_end = max(stop for _, stop in windows.values())
    timelines = {}; audits = {}
    for node in NODE_TO_SEGMENT:
        timelines[node], audits[node] = run_q1_attitude(
            ledger[f"imu_{node}"], node_id=node, initial_start_ns=initial_start,
            initial_end_ns=initial_stop, analysis_end_ns=analysis_end,
        )
    return timelines, audits_as_json(audits)


def _rotation_rows(timeline: np.ndarray, times: np.ndarray, maximum_gap_ns: int) -> tuple[np.ndarray, np.ndarray]:
    indices, gaps = _nearest(timeline["global_time_ns"], times)
    rotations = np.asarray([quaternion_to_matrix(q) for q in timeline["q_wxyz"][indices]])
    return rotations, gaps <= maximum_gap_ns


def _radial_sigma(delta: np.ndarray, covariance: np.ndarray, floor_m: float) -> np.ndarray:
    norm = np.linalg.norm(delta, axis=1)
    unit = delta / np.maximum(norm[:, None], 1e-9)
    variance = np.einsum("ni,nij,nj->n", unit, covariance, unit)
    return np.sqrt(np.maximum(variance, floor_m * floor_m))


@dataclass
class PairData:
    action: np.ndarray
    time_ns: np.ndarray
    proximal_position: np.ndarray
    distal_position: np.ndarray
    covariance: np.ndarray
    proximal_rotation_q1: np.ndarray
    distal_rotation_q1: np.ndarray

    def subset(self, mask: np.ndarray) -> "PairData":
        return PairData(*(getattr(self, name)[mask] for name in self.__dataclass_fields__))


def _pair_data(spec: tuple[str, str], observations: dict, q1: dict, windows: dict,
               model: dict) -> tuple[PairData, dict]:
    proximal, distal = spec
    p = observations[proximal]; d = observations[distal]
    rows = []; action_stats = {}
    for action, (start, stop) in windows.items():
        mask = (p["time_ns"] >= start) & (p["time_ns"] <= stop)
        pidx = np.flatnonzero(mask)
        if not len(pidx):
            action_stats[action] = {"matched_count": 0, "median_mm": None}
            continue
        didx, gap = _nearest(d["time_ns"], p["time_ns"][pidx])
        keep = gap <= int(model["maximum_pair_time_gap_ns"])
        pidx, didx = pidx[keep], didx[keep]
        rp, okp = _rotation_rows(q1[proximal], p["time_ns"][pidx], int(model["maximum_q1_time_gap_ns"]))
        rd, okd = _rotation_rows(q1[distal], p["time_ns"][pidx], int(model["maximum_q1_time_gap_ns"]))
        ok = okp & okd; pidx, didx, rp, rd = pidx[ok], didx[ok], rp[ok], rd[ok]
        distances = np.linalg.norm(d["position"][didx] - p["position"][pidx], axis=1) * 1000.0
        action_stats[action] = {"matched_count": int(len(pidx)),
                                "median_mm": float(np.median(distances)) if len(distances) else None}
        rows.extend((action, int(p["time_ns"][pi]), p["position"][pi], d["position"][di],
                     p["covariance"][pi] + d["covariance"][di], rpi, rdi)
                    for pi, di, rpi, rdi in zip(pidx, didx, rp, rd))
    if not rows:
        raise ValueError(f"no calibration-only matched observations for {spec}")
    action = np.asarray([r[0] for r in rows]); time_ns = np.asarray([r[1] for r in rows], dtype=np.int64)
    data = PairData(action, time_ns, *[np.asarray([r[i] for r in rows]) for i in range(2, 7)])
    raw_delta = data.distal_position - data.proximal_position
    raw_distance = np.linalg.norm(raw_delta, axis=1)
    sigma = _radial_sigma(raw_delta, data.covariance, float(model["radial_sigma_floor_m"]))
    audit = {
        "matched_count": int(len(data.time_ns)), "covariance_weighted_count": float(np.sum((.02 / sigma) ** 2)),
        "covariance_weighted_availability": float(np.mean(np.isfinite(sigma))),
        "raw_distance_mm": {"median": float(np.median(raw_distance) * 1000),
            "p05": float(np.percentile(raw_distance, 5) * 1000),
            "p95": float(np.percentile(raw_distance, 95) * 1000),
            "robust_spread_mad_sigma": float(1.4826 * np.median(np.abs(raw_distance - np.median(raw_distance))) * 1000)},
        "action_dependence": action_stats,
        "warning": "RAW_ANTENNA_DISTANCE_NOT_INTERPRETED_AS_BONE_LENGTH",
    }
    return data, audit


class PairProblem:
    def __init__(self, dimension: str, nodes: tuple[str, str], data: PairData, gates: dict):
        self.dimension = dimension; self.nodes = nodes; self.data = data; self.gates = gates
        self.parameter_names = [dimension]
        for role in ("proximal", "distal"):
            self.parameter_names += [f"{role}_placement_{axis}_m" for axis in "xyz"]
            self.parameter_names += [f"{role}_q1_to_v4_rotvec_{axis}_rad" for axis in "xyz"]
        lo, hi = gates["dimension_bounds_m"][dimension]
        b = float(gates["measurement_model"]["placement_component_bound_m"])
        self.lower = np.asarray([lo] + [-b] * 3 + [-np.pi] * 3 + [-b] * 3 + [-np.pi] * 3)
        self.upper = np.asarray([hi] + [b] * 3 + [np.pi] * 3 + [b] * 3 + [np.pi] * 3)

    @property
    def placement_indices(self): return list(range(1, 4)) + list(range(7, 10))
    @property
    def dimension_indices(self): return [0]

    def initial(self) -> np.ndarray:
        length = float(np.median(np.linalg.norm(self.data.distal_position - self.data.proximal_position, axis=1)))
        x = np.zeros(13); x[0] = np.clip(length, self.lower[0] + .001, self.upper[0] - .001)
        return x

    def residual(self, x: np.ndarray, data: PairData | None = None) -> np.ndarray:
        d = self.data if data is None else data
        ap = Rotation.from_rotvec(x[4:7]).as_matrix(); ad = Rotation.from_rotvec(x[10:13]).as_matrix()
        gp = d.proximal_position + np.einsum("ij,njk,k->ni", ap, d.proximal_rotation_q1, x[1:4])
        gd = d.distal_position + np.einsum("ij,njk,k->ni", ad, d.distal_rotation_q1, x[7:10])
        delta = gd - gp
        sigma = _radial_sigma(delta, d.covariance, float(self.gates["measurement_model"]["radial_sigma_floor_m"]))
        return (np.linalg.norm(delta, axis=1) - x[0]) / sigma

    def physical_mm(self, x, data=None):
        d = self.data if data is None else data
        ap = Rotation.from_rotvec(x[4:7]).as_matrix(); ad = Rotation.from_rotvec(x[10:13]).as_matrix()
        gp = d.proximal_position + np.einsum("ij,njk,k->ni", ap, d.proximal_rotation_q1, x[1:4])
        gd = d.distal_position + np.einsum("ij,njk,k->ni", ad, d.distal_rotation_q1, x[7:10])
        return np.abs(np.linalg.norm(gd-gp, axis=1)-x[0]) * 1000

    def subset(self, mask):
        return PairProblem(self.dimension, self.nodes, self.data.subset(mask), self.gates)


@dataclass
class ShoulderData:
    action: np.ndarray; time_ns: np.ndarray
    central_position: np.ndarray; elbow_l_position: np.ndarray; elbow_r_position: np.ndarray
    covariance_l: np.ndarray; covariance_r: np.ndarray
    central_rotation_q1: np.ndarray; elbow_l_rotation_q1: np.ndarray; elbow_r_rotation_q1: np.ndarray
    def subset(self, mask): return ShoulderData(*(getattr(self, n)[mask] for n in self.__dataclass_fields__))


class ShoulderProblem:
    dims = ("rendering_upper_arm_length_L", "rendering_upper_arm_length_R", "graphical_shoulder_width")
    nodes = ("BSF31CC", "BSFAA61", "BSF1120")
    def __init__(self, data: ShoulderData, gates: dict):
        self.data = data; self.gates = gates; self.parameter_names = list(self.dims)
        for node in ("central", "elbow_l", "elbow_r"):
            self.parameter_names += [f"{node}_placement_{a}_m" for a in "xyz"]
            self.parameter_names += [f"{node}_q1_to_v4_rotvec_{a}_rad" for a in "xyz"]
        bounds = [gates["dimension_bounds_m"][d] for d in self.dims]
        b = float(gates["measurement_model"]["placement_component_bound_m"])
        self.lower = np.asarray([x[0] for x in bounds] + ([-b]*3+[-np.pi]*3)*3)
        self.upper = np.asarray([x[1] for x in bounds] + ([b]*3+[np.pi]*3)*3)
    @property
    def dimension_indices(self): return [0, 1, 2]
    @property
    def placement_indices(self): return [3,4,5,9,10,11,15,16,17]
    def initial(self):
        dl = np.median(np.linalg.norm(self.data.elbow_l_position-self.data.central_position, axis=1))
        dr = np.median(np.linalg.norm(self.data.elbow_r_position-self.data.central_position, axis=1))
        w = np.median(np.linalg.norm(self.data.elbow_l_position-self.data.elbow_r_position, axis=1))
        x = np.zeros(21); x[:3] = np.clip([dl, dr, w], self.lower[:3]+.001, self.upper[:3]-.001); return x
    def residual(self, x, data=None):
        d = self.data if data is None else data
        mats = [Rotation.from_rotvec(x[i+3:i+6]).as_matrix() for i in (3,9,15)]
        placements = (x[3:6], x[9:12], x[15:18])
        rotations = (d.central_rotation_q1, d.elbow_l_rotation_q1, d.elbow_r_rotation_q1)
        positions = (d.central_position, d.elbow_l_position, d.elbow_r_position)
        g = [p + np.einsum("ij,njk,k->ni", a, r, off) for p,a,r,off in zip(positions,mats,rotations,placements)]
        rcentral = np.einsum("ij,njk->nik", mats[0], rotations[0])
        left_shoulder = g[0] + np.einsum("nij,j->ni", rcentral, np.array([-.5*x[2],0,0]))
        right_shoulder = g[0] + np.einsum("nij,j->ni", rcentral, np.array([.5*x[2],0,0]))
        dl = g[1]-left_shoulder; dr = g[2]-right_shoulder
        sl = _radial_sigma(dl, d.covariance_l, float(self.gates["measurement_model"]["radial_sigma_floor_m"]))
        sr = _radial_sigma(dr, d.covariance_r, float(self.gates["measurement_model"]["radial_sigma_floor_m"]))
        return np.r_[(np.linalg.norm(dl,axis=1)-x[0])/sl, (np.linalg.norm(dr,axis=1)-x[1])/sr]
    def physical_mm(self, x, data=None):
        d = self.data if data is None else data
        mats = [Rotation.from_rotvec(x[i+3:i+6]).as_matrix() for i in (3,9,15)]
        placements = (x[3:6], x[9:12], x[15:18]); rotations = (d.central_rotation_q1,d.elbow_l_rotation_q1,d.elbow_r_rotation_q1)
        positions = (d.central_position,d.elbow_l_position,d.elbow_r_position)
        g=[p+np.einsum("ij,njk,k->ni",a,r,off) for p,a,r,off in zip(positions,mats,rotations,placements)]
        rcentral=np.einsum("ij,njk->nik",mats[0],rotations[0])
        ls=g[0]+np.einsum("nij,j->ni",rcentral,np.array([-.5*x[2],0,0]));rs=g[0]+np.einsum("nij,j->ni",rcentral,np.array([.5*x[2],0,0]))
        return np.r_[np.abs(np.linalg.norm(g[1]-ls,axis=1)-x[0]),np.abs(np.linalg.norm(g[2]-rs,axis=1)-x[1])]*1000
    def subset(self, mask): return ShoulderProblem(self.data.subset(mask), self.gates)


def _shoulder_data(observations, q1, windows, model) -> ShoulderData:
    nodes = ShoulderProblem.nodes; c, l, r = (observations[n] for n in nodes); rows=[]
    for action,(start,stop) in windows.items():
        ci=np.flatnonzero((c["time_ns"]>=start)&(c["time_ns"]<=stop))
        if len(ci)>int(model["maximum_fit_samples_per_action"]):
            ci=ci[np.linspace(0,len(ci)-1,int(model["maximum_fit_samples_per_action"]),dtype=int)]
        li,lg=_nearest(l["time_ns"],c["time_ns"][ci]); ri,rg=_nearest(r["time_ns"],c["time_ns"][ci])
        rc,okc=_rotation_rows(q1[nodes[0]],c["time_ns"][ci],int(model["maximum_q1_time_gap_ns"]))
        rl,okl=_rotation_rows(q1[nodes[1]],c["time_ns"][ci],int(model["maximum_q1_time_gap_ns"]))
        rr,okr=_rotation_rows(q1[nodes[2]],c["time_ns"][ci],int(model["maximum_q1_time_gap_ns"]))
        ok=(lg<=int(model["maximum_pair_time_gap_ns"]))&(rg<=int(model["maximum_pair_time_gap_ns"]))&okc&okl&okr
        for j in np.flatnonzero(ok):
            rows.append((action,int(c["time_ns"][ci[j]]),c["position"][ci[j]],l["position"][li[j]],r["position"][ri[j]],
                c["covariance"][ci[j]]+l["covariance"][li[j]],c["covariance"][ci[j]]+r["covariance"][ri[j]],rc[j],rl[j],rr[j]))
    if not rows: raise ValueError("no matched shoulder calibration observations")
    return ShoulderData(np.asarray([x[0] for x in rows]),np.asarray([x[1] for x in rows],dtype=np.int64),
        *[np.asarray([x[i] for x in rows]) for i in range(2,10)])


def _fit(problem, initial=None):
    model=problem.gates["measurement_model"]
    return least_squares(problem.residual, problem.initial() if initial is None else initial,
        bounds=(problem.lower,problem.upper),method="trf",loss=model["least_squares_loss"],
        f_scale=float(model["least_squares_f_scale"]),x_scale="jac",
        max_nfev=int(model["optimizer_max_function_evaluations"]))


def _fixed_fit(problem, fixed_index, fixed_value, start):
    free=np.asarray([i for i in range(len(start)) if i!=fixed_index]); lo=problem.lower[free]; hi=problem.upper[free]
    def residual(y):
        x=start.copy();x[fixed_index]=fixed_value;x[free]=y;return problem.residual(x)
    result=least_squares(residual,np.clip(start[free],lo+1e-10,hi-1e-10),bounds=(lo,hi),method="trf",
        loss=problem.gates["measurement_model"]["least_squares_loss"],f_scale=float(problem.gates["measurement_model"]["least_squares_f_scale"]),x_scale="jac",
        max_nfev=int(problem.gates["measurement_model"]["optimizer_max_function_evaluations"]))
    x=start.copy();x[fixed_index]=fixed_value;x[free]=result.x;return result,x


def _profile(problem, result, index):
    model=problem.gates["measurement_model"]; target=result.cost+float(model["profile_delta_cost_95_one_parameter"])
    bounds=[]
    for direction,bound in ((-1,problem.lower[index]),(1,problem.upper[index])):
        inner=float(result.x[index]); endpoint,start=_fixed_fit(problem,index,float(bound),result.x)
        if endpoint.cost < target:
            outer=float(bound); reached=True
        else:
            outer=float(bound); reached=False
            a,b=(outer,inner) if direction<0 else (inner,outer)
            for _ in range(int(model["profile_bisection_iterations"])):
                mid=.5*(a+b);prof,_=_fixed_fit(problem,index,mid,result.x)
                if (prof.cost>=target)==(direction<0): a=mid
                else: b=mid
            outer=.5*(a+b)
        bounds.append({"value_m":float(outer),"parameter_bound_reached":bool(reached)})
    return {"lower_m":bounds[0]["value_m"],"upper_m":bounds[1]["value_m"],
            "full_width_mm":float((bounds[1]["value_m"]-bounds[0]["value_m"])*1000),
            "parameter_bound_reached":bool(bounds[0]["parameter_bound_reached"] or bounds[1]["parameter_bound_reached"])}


def _solve_diagnostics(problem, gates):
    result=_fit(problem); x=result.x; residual=problem.residual(x); jac=result.jac
    singular=np.linalg.svd(jac,compute_uv=False); threshold=float(gates["acceptance_gates"]["observability_relative_singular_value_threshold"])
    rank=int(np.sum(singular>singular[0]*threshold)) if len(singular) and singular[0]>0 else 0
    gram=np.linalg.pinv(jac.T@jac,rcond=threshold); scale=2*result.cost/max(1,len(residual)-rank); cov=gram*scale
    std=np.sqrt(np.maximum(np.diag(cov),0)); denom=np.outer(std,std); corr=np.divide(cov,denom,out=np.zeros_like(cov),where=denom>0)
    distance=np.minimum(x-problem.lower,problem.upper-x); hits=[problem.parameter_names[i] for i in np.flatnonzero(distance<=1e-5)]
    rng=np.random.default_rng(int(gates["determinism"]["random_seed"])); starts=[]
    for k in range(int(gates["determinism"]["multistart_count"])):
        init=problem.initial().copy()
        if k: init += rng.normal(0,.025,len(init))
        init=np.clip(init,problem.lower+1e-7,problem.upper-1e-7); starts.append(_fit(problem,init).x)
    even=_fit(problem.subset(np.arange(len(problem.data.action))%2==0)).x
    odd=_fit(problem.subset(np.arange(len(problem.data.action))%2==1)).x
    removals={}
    for action in gates["calibration_actions"]:
        mask=problem.data.action!=action
        removals[action]=_fit(problem.subset(mask),x).x
    physical=problem.physical_mm(x)
    common={"optimizer_success":bool(result.success),"optimizer_message":str(result.message),"cost":float(result.cost),
        "sample_count":int(len(residual)),"jacobian":{"shape":list(jac.shape),"singular_values":singular.tolist(),"relative_threshold":threshold,"rank":rank},
        "fitted_parameters":{name:float(value) for name,value in zip(problem.parameter_names,x)},
        "bound_hits_disclosed":hits,"normalized_residual":{"median":float(np.median(np.abs(residual))),"p95":float(np.percentile(np.abs(residual),95))},
        "physical_residual_mm":{"median":float(np.median(physical)),"p95":float(np.percentile(physical,95))}}
    per={}; mandatory=set(gates["mandatory_actions"])
    for di in problem.dimension_indices:
        name=problem.parameter_names[di]; vals=np.asarray([s[di] for s in starts]); optional=[v[di] for a,v in removals.items() if a not in mandatory]; mandatory_v=[v[di] for a,v in removals.items() if a in mandatory]
        correlations={problem.parameter_names[pi]:float(corr[di,pi]) for pi in problem.placement_indices}
        per[name]={"value_m":float(x[di]),"value_mm":float(x[di]*1000),"profile_interval":_profile(problem,result,di),
            "jacobian_linearized_standard_uncertainty_mm":float(std[di]*1000),"placement_correlations":correlations,
            "maximum_absolute_placement_correlation":float(max((abs(v) for v in correlations.values()),default=0)),
            "multistart_values_mm":(vals*1000).tolist(),"multistart_spread_mm":float(np.ptp(vals)*1000),
            "interleaved_even_odd_values_mm":[float(even[di]*1000),float(odd[di]*1000)],"interleaved_spread_mm":float(abs(even[di]-odd[di])*1000),
            "optional_action_removal_values_mm":{a:float(v[di]*1000) for a,v in removals.items() if a not in mandatory},
            "optional_action_removal_spread_mm":float(np.ptp(optional)*1000) if optional else 0.0,
            "mandatory_action_removal_values_mm":{a:float(v[di]*1000) for a,v in removals.items() if a in mandatory},
            "mandatory_action_removal_spread_mm":float(np.ptp(mandatory_v)*1000) if mandatory_v else 0.0,
            "relevant_bound_hits":[h for h in hits if h==name or "placement" in h],"left_right_identity_fixed":True}
    return common,per


def _verdict(evidence, common, gates):
    a=gates["acceptance_gates"]; reasons=[]
    if not common["optimizer_success"] or evidence["profile_interval"]["parameter_bound_reached"] or evidence["profile_interval"]["full_width_mm"]>a["maximum_profile_interval_full_width_mm"]:
        reasons.append("profile/Jacobian observability gate")
        return "FAIL_UNOBSERVABLE",reasons
    if evidence["maximum_absolute_placement_correlation"]>a["maximum_absolute_dimension_placement_correlation"] or any("placement" in h for h in evidence["relevant_bound_hits"]):
        reasons.append("dimension-placement coupling gate");return "FAIL_PLACEMENT_COUPLING",reasons
    if evidence["multistart_spread_mm"]>a["maximum_multistart_dimension_spread_mm"] or evidence["interleaved_spread_mm"]>a["maximum_interleaved_dimension_spread_mm"] or evidence["optional_action_removal_spread_mm"]>a["maximum_optional_action_removal_spread_mm"]:
        reasons.append("repeatability/action-dependence gate");return "FAIL_ACTION_DEPENDENCE",reasons
    if common["normalized_residual"]["median"]>a["maximum_normalized_residual_median"] or common["normalized_residual"]["p95"]>a["maximum_normalized_residual_p95"]:
        reasons.append("physical model-mismatch gate");return "FAIL_MODEL_MISMATCH",reasons
    return "PASS_CAPTURE_DERIVED",["all predeclared gates passed"]


def _diagnose_problem(arguments):
    problem, gates = arguments
    return _solve_diagnostics(problem, gates)


def run_audit(calibration_ledger: Path, layout: Path, gates_path: Path, output: Path) -> dict:
    gates=json.loads(gates_path.read_text());
    if gates.get("operator_measurements")!="SEALED_AND_FORBIDDEN" or gates["sealed_future_comparison_contract"]["enabled_during_this_audit"]:
        raise ValueError("operator measurement firewall is not sealed")
    if sha256(layout)!=gates["canonical_frontends"]["geometry_sha256"]: raise ValueError("canonical geometry SHA mismatch")
    if any(token in str(calibration_ledger).lower() for token in ("heldout","walk","final_still","raw")): raise ValueError("payload firewall rejection")
    output.mkdir(parents=True,exist_ok=False)
    with np.load(calibration_ledger,allow_pickle=False) as ledger:
        if Path(calibration_ledger).name!="CALIBRATION_TYPED_LEDGER.npz" or any(k.startswith("heldout") for k in ledger.files): raise ValueError("calibration ledger firewall rejection")
        windows=_action_windows(ledger,gates); observations,t4_accounting,t4_rejections=_solve_t4(ledger,layout);q1,q1_audits=_q1(ledger,windows)
        direct={};problems=[]
        for dimension,spec in PAIR_SPECS.items():
            data,audit=_pair_data(spec,observations,q1,windows,gates["measurement_model"]);direct[dimension]=audit
            # Fit cap is separate from the full raw-pair audit.
            keep=np.zeros(len(data.action),bool)
            for action in windows:
                idx=np.flatnonzero(data.action==action); cap=int(gates["measurement_model"]["maximum_fit_samples_per_action"])
                keep[idx if len(idx)<=cap else idx[np.linspace(0,len(idx)-1,cap,dtype=int)]]=True
            problems.append(PairProblem(dimension,spec,data.subset(keep),gates))
        problems.append(ShoulderProblem(_shoulder_data(observations,q1,windows,gates["measurement_model"]),gates))
        dimensions={}; model_groups={}
        with ProcessPoolExecutor(max_workers=int(gates["determinism"]["independent_problem_worker_count"])) as executor:
            diagnostics = list(executor.map(_diagnose_problem, ((problem, gates) for problem in problems)))
        for problem,(common,per) in zip(problems,diagnostics):
            group="direct_pair_"+problem.dimension if isinstance(problem,PairProblem) else "upper_arm_shoulder_joint"
            model_groups[group]=common
            for name,evidence in per.items():
                verdict,reasons=_verdict(evidence,common,gates);dimensions[name]={"tier":1 if name in PAIR_SPECS else 2,"verdict":verdict,"reasons":reasons,"evidence":evidence,
                    "graphical_surface_chord_definition":GRAPHICAL_DEFINITION[name],"future_operator_field":OPERATOR_MATCH[name],
                    "provenance":"CAPTURE_DERIVED_RENDERING_LENGTH" if verdict=="PASS_CAPTURE_DERIVED" else "NOT_FROZEN"}
        for name,reason in gates["tier_2_layout_unsupported"].items():
            dimensions[name]={"tier":2,"verdict":"NOT_DIRECTLY_SUPPORTED_BY_NODE_LAYOUT","reasons":[reason],"evidence":None,
                "graphical_surface_chord_definition":GRAPHICAL_DEFINITION[name],"future_operator_field":OPERATOR_MATCH[name],"provenance":"NOT_FROZEN"}
        passes=[n for n in DIMENSIONS if dimensions[n]["verdict"]=="PASS_CAPTURE_DERIVED"]
        outcome="CAPTURE_DERIVED_GEOMETRY_COMPLETE" if len(passes)==len(DIMENSIONS) else ("CAPTURE_DERIVED_GEOMETRY_PARTIAL_OPERATOR_INPUT_REQUIRED" if passes else "CAPTURE_DERIVED_GEOMETRY_UNOBSERVABLE")
        result={"schema":"biospur-capture-derived-rendering-geometry-audit-result-v1","outcome":outcome,"product":"NON_CLINICAL_VISUALIZATION_ONLY",
            "operator_measurement_firewall":"SEALED_NOT_READ","heldout":{"walk":"SEALED_NOT_OPENED","final_still":"SEALED_NOT_OPENED"},
            "inputs":{"calibration_ledger":{"absolute_path":str(calibration_ledger.resolve()),"sha256":sha256(calibration_ledger)},"layout":{"absolute_path":str(layout.resolve()),"sha256":sha256(layout)},"gates":{"absolute_path":str(gates_path.resolve()),"sha256":sha256(gates_path)}},
            "frontends":{"position":"UWB_TAG_T4","attitude":"Q1_ATTITUDE_ONLY","t4_accounting":t4_accounting,"t4_rejection_count":len(t4_rejections),"q1_audits":q1_audits},
            "direct_tracked_pair_audits":direct,"model_groups":model_groups,"dimensions":{n:dimensions[n] for n in DIMENSIONS},
            "frozen_dimensions":{n:{"value_mm":dimensions[n]["evidence"]["value_mm"],"profile_interval":dimensions[n]["evidence"]["profile_interval"],"provenance":"CAPTURE_DERIVED_RENDERING_LENGTH","immutable":True} for n in passes},
            "scientific_boundary":"NOT_ANATOMICAL_GROUND_TRUTH_NOT_CLINICAL_JOINT_CENTRES_OR_LENGTHS",
            "future_external_comparison":gates["sealed_future_comparison_contract"]}
    dump_json(output/"AUDIT_RESULT.json",result);dump_json(output/"FROZEN_CAPTURE_DERIVED_DIMENSIONS.json",result["frozen_dimensions"])
    failed=[n for n in DIMENSIONS if n not in passes]
    lines=["# Operator measurements minimum required","","This request is generated only from dimensions that did not pass the sealed capture-derived audit.","It does not treat surface measurements as anatomical ground truth. The measurements were not opened or used by this audit.","","| Failed visualization dimension | Audit verdict | Matching direct surface chord to collect |","|---|---|---|"]
    lines += [f"| `{n}` | `{dimensions[n]['verdict']}` | `{OPERATOR_MATCH[n]}` |" for n in failed]
    lines += ["","Future comparison remains a separate, post-freeze stage. Its predeclared visualization agreement gate is `absolute difference <= max(20 mm, 2 × combined standard uncertainty)`. Estimation failure and external-reference disagreement are distinct outcomes.",""]
    (output/"OPERATOR_MEASUREMENTS_MINIMUM_REQUIRED.md").write_text("\n".join(lines),encoding="utf-8")
    report=["# Capture-derived rendering geometry audit","",f"**{result['outcome']}**","","Operator measurements: **SEALED / NOT READ**. Walk and final-still: **SEALED / NOT OPENED**.","","This is a non-clinical visualization feasibility result; passing values are immutable graphical rendering lengths, not measured bone lengths or anatomical joint-centre distances.","","| Dimension | Tier | Verdict | Value (mm) |","|---|---:|---|---:|"]
    for n in DIMENSIONS:
        e=dimensions[n]["evidence"];report.append(f"| `{n}` | {dimensions[n]['tier']} | `{dimensions[n]['verdict']}` | {e['value_mm']:.3f} |" if e else f"| `{n}` | {dimensions[n]['tier']} | `{dimensions[n]['verdict']}` | — |")
    report += ["","See `AUDIT_RESULT.json` for raw-pair distributions, T4/Q1 accounting, actual Jacobians, profile intervals, all placement correlations, multistart/interleaved/action-removal results, bound hits and model-mismatch residuals.",""]
    (output/"REPORT.md").write_text("\n".join(report),encoding="utf-8")
    hashes=[]
    for path in sorted(output.iterdir()):
        if path.name!="SHA256SUMS":hashes.append(f"{sha256(path)}  {path.name}")
    (output/"SHA256SUMS").write_text("\n".join(hashes)+"\n",encoding="utf-8")
    return result
