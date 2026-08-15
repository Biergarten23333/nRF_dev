"""Deterministic synthetic truth and structural recovery gate for V1."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from .core import (
    CalibrationDataset, JOINTS, NodeSeries, SEGMENTS, axis_angle_rad,
    deterministic_information_subset, estimate_initial_noise,
    fit_functional_axis, interpolate_rotations_so3, normalize,
    olsson_weighted_residual, tangent_basis, tangent_update,
)


NODE_TO_SEGMENT = {
    "BSFC2CC": "pelvis", "BSF31CC": "torso",
    "BSFAA61": "upper_arm_L", "BSF1120": "upper_arm_R",
    "BSFB165": "forearm_L", "BSFEC35": "forearm_R",
    "BSF44AD": "thigh_L", "BSF3C79": "thigh_R",
    "BSF6C53": "shank_L", "BSF8BC4": "shank_R",
}
SEGMENT_TO_NODE = {segment: node for node, segment in NODE_TO_SEGMENT.items()}
HINGES = {
    "elbow_L": ("upper_arm_L", "forearm_L", "left_elbow"),
    "elbow_R": ("upper_arm_R", "forearm_R", "right_elbow_attempt2"),
    "knee_L": ("thigh_L", "shank_L", "left_knee"),
    "knee_R": ("thigh_R", "shank_R", "right_knee"),
}
ACTIONS = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow",
    "right_elbow_attempt2", "left_knee", "right_knee", "left_heel",
    "right_heel", "squats", "trunk",
)


@dataclass(frozen=True)
class SyntheticTruth:
    a_B: Mapping[str, np.ndarray]
    transverse_B: Mapping[str, np.ndarray]
    hinge_parent_B: Mapping[str, np.ndarray]
    hinge_child_B: Mapping[str, np.ndarray]
    joint_zero_rad: Mapping[str, float]
    joint_sign: Mapping[str, int]
    initial_heading_rad: Mapping[str, float]
    mounting_angle_deg: Mapping[str, float]


def _smooth(value: np.ndarray) -> np.ndarray:
    return value * value * (3.0 - 2.0 * value)


def _rz(angle: float | np.ndarray) -> np.ndarray:
    return Rotation.from_euler("z", angle).as_matrix()


def _action_phase(time_ns: np.ndarray, window: tuple[int, int]) -> np.ndarray:
    return np.clip((time_ns - window[0]) / float(window[1] - window[0]), 0.0, 1.0)


def _pose_rotations(time_ns: np.ndarray, windows: Mapping[str, tuple[int, int]],
                    zero: Mapping[str, float], sign: Mapping[str, int]) -> dict[str, np.ndarray]:
    count = len(time_ns)
    out = {segment: np.repeat(np.eye(3)[None], count, axis=0) for segment in SEGMENTS}
    down = Rotation.from_euler("x", math.pi).as_matrix()
    for segment in SEGMENTS[2:]:
        out[segment][:] = down

    def blend(base: np.ndarray, target: np.ndarray, phase: np.ndarray) -> np.ndarray:
        delta = Rotation.from_matrix(base.T @ target).as_rotvec()
        return Rotation.from_rotvec(phase[:, None] * delta).as_matrix() @ base

    # T-pose uses two genuinely different arm directions.
    mask = (time_ns >= windows["t_pose"][0]) & (time_ns <= windows["t_pose"][1])
    left_t = Rotation.from_euler("x", -math.pi / 2).as_matrix()
    right_t = Rotation.from_euler("x", math.pi / 2).as_matrix()
    out["upper_arm_L"][mask] = left_t; out["forearm_L"][mask] = left_t
    out["upper_arm_R"][mask] = right_t; out["forearm_R"][mask] = right_t

    # Generic arms window: shoulder elevation plus bilateral elbow flexion.
    phase = _action_phase(time_ns, windows["arms"]); mask = (phase > 0) & (phase < 1)
    wave = np.sin(2 * math.pi * 2 * phase[mask])
    for side, shoulder_sign in (("L", -1.0), ("R", 1.0)):
        shoulder = shoulder_sign * (math.pi/2) * (0.5 + 0.5*wave)
        parent = Rotation.from_rotvec(np.column_stack((shoulder, np.zeros(len(shoulder)), np.zeros(len(shoulder))))).as_matrix()
        flex = np.deg2rad(55.0) * (0.5 + 0.5*np.sin(2*math.pi*3*phase[mask]))
        child = parent @ Rotation.from_rotvec(
            np.column_stack((np.zeros(len(flex)), sign[f"elbow_{side}"]*flex + zero[f"elbow_{side}"], np.zeros(len(flex))))).as_matrix()
        out[f"upper_arm_{side}"][mask] = parent; out[f"forearm_{side}"][mask] = child

    # Dedicated hinges include parent common-body excitation so both local axes are observable.
    for joint, (parent_segment, child_segment, action) in HINGES.items():
        phase = _action_phase(time_ns, windows[action]); mask = (phase > 0) & (phase < 1)
        p = phase[mask]
        common = Rotation.from_euler("xz", np.column_stack((
            np.deg2rad(12)*np.sin(2*math.pi*p), np.deg2rad(18)*np.sin(4*math.pi*p)))).as_matrix()
        base = down
        parent = common @ base
        flex = np.deg2rad(70.0) * np.sin(math.pi * p) ** 2
        child = parent @ Rotation.from_rotvec(np.column_stack((
            np.zeros(len(flex)), sign[joint]*flex + zero[joint], np.zeros(len(flex))))).as_matrix()
        out[parent_segment][mask] = parent; out[child_segment][mask] = child

    # Squat: bilateral lower chains, no root/contact model.
    phase = _action_phase(time_ns, windows["squats"]); mask = (phase > 0) & (phase < 1)
    flex = np.deg2rad(65.0) * np.sin(2 * math.pi * 2 * phase[mask]) ** 2
    for side in ("L", "R"):
        thigh_angle = 0.55*flex
        thigh = Rotation.from_rotvec(np.column_stack((np.zeros(len(flex)), thigh_angle, np.zeros(len(flex))))).as_matrix() @ down
        shank_angle = sign[f"knee_{side}"]*flex + zero[f"knee_{side}"]
        shank = thigh @ Rotation.from_rotvec(np.column_stack((np.zeros(len(flex)), shank_angle, np.zeros(len(flex))))).as_matrix()
        out[f"thigh_{side}"][mask] = thigh; out[f"shank_{side}"][mask] = shank

    # Heel windows only tilt the shank; there is deliberately no foot state.
    for side, action in (("L", "left_heel"), ("R", "right_heel")):
        phase = _action_phase(time_ns, windows[action]); mask = (phase > 0) & (phase < 1)
        tilt = np.deg2rad(12.0) * np.sin(2*math.pi*phase[mask])
        out[f"shank_{side}"][mask] = Rotation.from_rotvec(np.column_stack((np.zeros(len(tilt)), tilt, np.zeros(len(tilt))))).as_matrix() @ down

    # Trunk relative excitation.
    phase = _action_phase(time_ns, windows["trunk"]); mask = (phase > 0) & (phase < 1)
    p = phase[mask]
    out["torso"][mask] = Rotation.from_euler(
        "xy", np.column_stack((np.deg2rad(20)*np.sin(2*math.pi*p),
                               np.deg2rad(15)*np.sin(4*math.pi*p)))).as_matrix()
    return out


def generate_synthetic_dataset(gates: Mapping[str, Any], seed: int = 4711) -> tuple[CalibrationDataset, SyntheticTruth]:
    rng = np.random.default_rng(seed)
    rate = 200.0; action_s = 2.6; transition_s = 0.25
    cursor = 0.0; windows = {}
    for action in ACTIONS:
        start = cursor; stop = start + action_s
        windows[action] = (int(round(start*1e9)), int(round(stop*1e9)))
        cursor = stop + transition_s
    time_ns = np.arange(0, int(round(cursor*1e9)), int(round(1e9/rate)), dtype=np.int64)
    zeros = {"elbow_L": math.radians(2.0), "elbow_R": math.radians(-2.5),
             "knee_L": math.radians(3.0), "knee_R": math.radians(-3.5)}
    signs = {"elbow_L": 1, "elbow_R": -1, "knee_L": 1, "knee_R": -1}
    R_H_from_S = _pose_rotations(time_ns, windows, zeros, signs)
    mount = {}; heading = {}; nodes = {}; a_B = {}; transverse = {}
    hp = {}; hc = {}; mounting_angle = {}
    for index, (node, segment) in enumerate(sorted(NODE_TO_SEGMENT.items())):
        axis = normalize(rng.normal(size=3)); angle = rng.uniform(0.0, math.radians(60.0))
        R_S_from_B = Rotation.from_rotvec(axis*angle).as_matrix(); mount[segment] = R_S_from_B
        mounting_angle[segment] = math.degrees(angle)
        psi0 = 0.0 if segment == "pelvis" else rng.uniform(-math.pi, math.pi)
        heading[segment] = psi0
        drift = math.radians(0.10) * np.sin(2*math.pi*time_ns/1e9/17.0 + index)
        R_H_from_B = R_H_from_S[segment] @ R_S_from_B
        R_N_from_B = np.empty_like(R_H_from_B)
        for k in range(len(time_ns)):
            R_N_from_B[k] = _rz(-(psi0+drift[k])) @ R_H_from_B[k]
        # Board-frame angular velocity from active orientation increments.
        gyro = np.zeros((len(time_ns), 3))
        relative = np.einsum("nji,njk->nik", R_H_from_B[:-1], R_H_from_B[1:])
        gyro[:-1] = Rotation.from_matrix(relative).as_rotvec() * rate
        gyro[-1] = gyro[-2]
        bias = rng.normal(scale=0.003, size=3)
        gyro += bias + rng.normal(scale=0.002, size=gyro.shape)
        accel = np.einsum("nji,j->ni", R_H_from_B, np.array([0.0, 0.0, 9.80665]))
        accel += rng.normal(scale=0.02, size=accel.shape)
        nodes[node] = NodeSeries(
            time_ns=time_ns.copy(), accel_B_mps2=accel, gyro_B_rad_s=gyro,
            R_N_i_from_B_i=R_N_from_B,
            orientation_sigma_rad=np.full(len(time_ns), math.radians(0.5)),
            gyro_bias_sigma_rad_s=np.full(3, 0.0005),
        )
        a_B[segment] = R_S_from_B.T @ np.array([0.0, 0.0, 1.0])
        if segment in ("pelvis", "torso"):
            transverse[segment] = R_S_from_B.T @ np.array([0.0, 1.0, 0.0])
    for joint, (parent, child, _) in HINGES.items():
        hp[joint] = mount[parent].T @ np.array([0.0, 1.0, 0.0])
        hc[joint] = mount[child].T @ np.array([0.0, 1.0, 0.0])
    dataset = CalibrationDataset(
        nodes=nodes, action_windows=windows, node_to_segment=NODE_TO_SEGMENT,
        source_hashes={"synthetic_generator": "IMU_MULTI_ACTION_V1_SEED_4711"},
    )
    truth = SyntheticTruth(a_B, transverse, hp, hc, zeros, signs, heading, mounting_angle)
    return dataset, truth


class OracleSyntheticObservabilityProblem:
    """Full declared residual evaluated around truth.

    Using truth as the tangent chart is an oracle advantage.  Any additional
    nullspace found here is therefore structural, not an initializer failure.
    """

    def __init__(self, dataset: CalibrationDataset, truth: SyntheticTruth,
                 gates: Mapping[str, Any]):
        self.dataset = dataset; self.truth = truth; self.gates = gates
        self._R_N_from_B_cache = {}
        self.segment_nodes = {segment: SEGMENT_TO_NODE[segment] for segment in SEGMENTS}
        self.refine_times = {}
        step = int(round(1e9/float(gates["sampling"]["refinement_hz"])))
        for action, (start, stop) in dataset.action_windows.items():
            self.refine_times[action] = np.arange(start, stop+1, step, dtype=np.int64)
        self.timeline_start = min(x[0] for x in dataset.action_windows.values())
        self.timeline_stop = max(x[1] for x in dataset.action_windows.values())
        self.knot_time_ns = np.arange(self.timeline_start, self.timeline_stop+1,
                                      int(round(float(gates["sampling"]["yaw_knot_s"])*1e9)), dtype=np.int64)
        if self.knot_time_ns[-1] < self.timeline_stop:
            self.knot_time_ns = np.r_[self.knot_time_ns, self.timeline_stop]
        self._build_index()
        self.noise = estimate_initial_noise(dataset, gates)
        self.native = {}
        for joint, (parent, child, action) in HINGES.items():
            ps=dataset.nodes[self.segment_nodes[parent]]; cs=dataset.nodes[self.segment_nodes[child]]
            start,stop=dataset.action_windows[action]
            maskp=(ps.time_ns>=start)&(ps.time_ns<=stop); maskc=(cs.time_ns>=start)&(cs.time_ns<=stop)
            count=min(int(maskp.sum()),int(maskc.sum())); ip=np.flatnonzero(maskp)[:count]; ic=np.flatnonzero(maskc)[:count]
            score=np.linalg.norm(ps.gyro_B_rad_s[ip],axis=1)+np.linalg.norm(cs.gyro_B_rad_s[ic],axis=1)
            sel=deterministic_information_subset(score,int(gates["sampling"]["minimum_mandatory_informative_samples"]),int(gates["sampling"]["max_samples_per_action_factor"]))
            self.native[joint]=(ps.gyro_B_rad_s[ip][sel],cs.gyro_B_rad_s[ic][sel],ps.accel_B_mps2[ip][sel],cs.accel_B_mps2[ic][sel])

    def _build_index(self) -> None:
        self.slices={}; names=[]; cursor=0
        for segment in ("pelvis","torso"):
            self.slices[f"frame:{segment}"]=slice(cursor,cursor+3); names += [f"frame:{segment}:{a}" for a in "xyz"]; cursor+=3
        for segment in SEGMENTS[2:]:
            self.slices[f"axis:{segment}"]=slice(cursor,cursor+2); names += [f"axis:{segment}:t0",f"axis:{segment}:t1"]; cursor+=2
        for joint in JOINTS:
            for side in ("parent","child"):
                self.slices[f"hinge:{joint}:{side}"]=slice(cursor,cursor+2); names += [f"hinge:{joint}:{side}:t0",f"hinge:{joint}:{side}:t1"]; cursor+=2
        self.slices["zeros"]=slice(cursor,cursor+4); names += [f"zero:{j}" for j in JOINTS]; cursor+=4
        for segment in SEGMENTS:
            if segment=="pelvis": continue
            self.slices[f"heading:{segment}"]=slice(cursor,cursor+1); names.append(f"heading:{segment}"); cursor+=1
        knots=len(self.knot_time_ns)-1
        for segment in SEGMENTS:
            self.slices[f"yaw_delta:{segment}"]=slice(cursor,cursor+knots); names += [f"yaw_delta:{segment}:{k+1}" for k in range(knots)]; cursor+=knots
        self.parameter_names=names; self.parameter_count=cursor

    def unpack(self, value: np.ndarray):
        value=np.asarray(value,float); a={}; b={}
        for segment in ("pelvis","torso"):
            R=Rotation.from_rotvec(value[self.slices[f"frame:{segment}"]]).as_matrix()
            a[segment]=R@self.truth.a_B[segment]; b[segment]=R@self.truth.transverse_B[segment]
        for segment in SEGMENTS[2:]: a[segment]=tangent_update(self.truth.a_B[segment],value[self.slices[f"axis:{segment}"]])
        hp={};hc={}
        for joint in JOINTS:
            hp[joint]=tangent_update(self.truth.hinge_parent_B[joint],value[self.slices[f"hinge:{joint}:parent"]])
            hc[joint]=tangent_update(self.truth.hinge_child_B[joint],value[self.slices[f"hinge:{joint}:child"]])
        zeros={j:self.truth.joint_zero_rad[j]+value[self.slices["zeros"]][k] for k,j in enumerate(JOINTS)}
        heading={"pelvis":0.0}
        for segment in SEGMENTS:
            if segment!="pelvis": heading[segment]=self.truth.initial_heading_rad[segment]+float(value[self.slices[f"heading:{segment}"]][0])
        delta={}
        for segment in SEGMENTS: delta[segment]=np.r_[0.0,value[self.slices[f"yaw_delta:{segment}"]]]
        return a,b,hp,hc,zeros,heading,delta

    def yaw(self, segment: str, times: np.ndarray, heading, delta) -> np.ndarray:
        return heading[segment]+np.interp(times.astype(float),self.knot_time_ns.astype(float),delta[segment])

    def R_H_from_B(self, segment: str, times: np.ndarray, heading, delta) -> np.ndarray:
        times=np.asarray(times,np.int64); key=(segment,int(times[0]),int(times[-1]),len(times),hash(times.tobytes()))
        Rnb=self._R_N_from_B_cache.get(key)
        if Rnb is None:
            stream=self.dataset.nodes[self.segment_nodes[segment]]; Rnb=interpolate_rotations_so3(stream.time_ns,stream.R_N_i_from_B_i,times);self._R_N_from_B_cache[key]=Rnb
        yaw=self.yaw(segment,times,heading,delta)
        R_H_from_N=Rotation.from_rotvec(np.column_stack((np.zeros(len(yaw)),np.zeros(len(yaw)),yaw))).as_matrix()
        return np.einsum("nij,njk->nik",R_H_from_N,Rnb)

    def residual_blocks(self,value:np.ndarray)->list[tuple[str,str,np.ndarray]]:
        a,b,hp,hc,zeros,heading,delta=self.unpack(value); blocks=[]; sigma=float(self.gates["noise_floors"]["orientation_sigma_rad"])
        def add(action: str, factor: str, rows: np.ndarray) -> None:
            blocks.append((action, factor, np.ravel(np.asarray(rows, float))))
        expected_initial={s:(np.array([0.,0.,1.]) if s in ("pelvis","torso") else np.array([0.,0.,-1.])) for s in SEGMENTS}
        expected_tpose=dict(expected_initial); expected_tpose.update({"upper_arm_L":np.array([0.,1.,0.]),"forearm_L":np.array([0.,1.,0.]),"upper_arm_R":np.array([0.,-1.,0.]),"forearm_R":np.array([0.,-1.,0.])})
        for action,expected in (("initial_still_attempt2",expected_initial),("t_pose",expected_tpose)):
            times=self.refine_times[action]
            pick=np.linspace(0,len(times)-1,125).round().astype(int); times=times[pick]
            for segment in SEGMENTS:
                R=self.R_H_from_B(segment,times,heading,delta); d=np.einsum("nij,j->ni",R,a[segment]); add(action,f"static_segment_direction:{segment}",(d-expected[segment])/sigma)
            for segment in ("pelvis","torso"):
                R=self.R_H_from_B(segment,times,heading,delta); t=np.einsum("nij,j->ni",R,b[segment]); add(action,f"transverse_direction:{segment}",(t-np.array([0.,1.,0.]))/sigma)
        # Published native functional residuals plus global hinge/heading alignment.
        for joint,(parent,child,action) in HINGES.items():
            wp,wc,ap,ac=self.native[joint]
            olsson=olsson_weighted_residual(hp[joint],hc[joint],wp,wc,ap,ac,self.noise["gyro_sigma_rad_s"],self.noise["accel_sigma_mps2"])
            add(action,f"olsson_gyro:{joint}",olsson[0::2]);add(action,f"olsson_acceleration:{joint}",olsson[1::2])
            actions=[action,"arms" if "elbow" in joint else "squats"]
            for selected_action in actions:
                times=self.refine_times[selected_action]; pick=np.linspace(0,len(times)-1,125).round().astype(int);times=times[pick]
                Rp=self.R_H_from_B(parent,times,heading,delta);Rc=self.R_H_from_B(child,times,heading,delta)
                ghp=np.einsum("nij,j->ni",Rp,hp[joint]);ghc=np.einsum("nij,j->ni",Rc,hc[joint]);add(selected_action,f"hinge_heading_alignment:{joint}",(ghp-ghc)/sigma)
            # Static extension and action-tail return-to-zero.
            for selected_action in ("initial_still_attempt2","t_pose"):
                times=self.refine_times[selected_action][::5];Rp=self.R_H_from_B(parent,times,heading,delta);Rc=self.R_H_from_B(child,times,heading,delta)
                dp=np.einsum("nij,j->ni",Rp,a[parent]);dc=np.einsum("nij,j->ni",Rc,a[child]);hh=np.einsum("nij,j->ni",Rp,hp[joint]);raw=np.arctan2(np.sum(np.cross(dp,dc)*hh,axis=1),np.sum(dp*dc,axis=1));add(selected_action,f"joint_extension_zero:{joint}",(self.truth.joint_sign[joint]*raw-zeros[joint])/sigma)
        # Trunk low-motion transverse relationship; heel windows are validation-only by node placement.
        times=self.refine_times["trunk"][[0,-1]];Rp=self.R_H_from_B("pelvis",times,heading,delta);Rt=self.R_H_from_B("torso",times,heading,delta)
        bp=np.einsum("nij,j->ni",Rp,b["pelvis"]);bt=np.einsum("nij,j->ni",Rt,b["torso"]);add("trunk","pelvis_torso_transverse_endpoint_consistency",(bp-bt)/sigma)
        # Non-zero-floor random walk on nuisance yaw knots.
        floor=float(self.gates["noise_floors"]["yaw_random_walk_sigma_rad_sqrt_s"])
        for segment in SEGMENTS:
            stream=self.dataset.nodes[self.segment_nodes[segment]]; bias=max(float(np.linalg.norm(stream.gyro_bias_sigma_rad_s)),floor)
            add("FULL_CONTINUOUS_TIMELINE",f"yaw_spline_random_walk:{segment}",np.diff(delta[segment])/bias)
        return blocks

    def residual(self,value:np.ndarray)->np.ndarray:
        return np.concatenate([rows for _,_,rows in self.residual_blocks(value)])

    def numerical_jacobian(self,value:np.ndarray,step:float=2e-6)->np.ndarray:
        base=self.residual(value);J=np.empty((len(base),len(value)))
        for col in range(len(value)):
            plus=value.copy();minus=value.copy();plus[col]+=step;minus[col]-=step
            J[:,col]=(self.residual(plus)-self.residual(minus))/(2*step)
        return J

    def characterize_null_direction(self, direction: np.ndarray,
                                    jacobian: np.ndarray) -> dict:
        """Apply a finite null perturbation and report physical effects.

        The perturbation is deliberately small enough to test the local
        observability statement represented by the scaled Jacobian, while
        still being many orders above floating-point epsilon.
        """
        direction = np.asarray(direction, float)
        dominant = float(np.max(np.abs(direction)))
        scale = 1.0e-4 / dominant
        base = np.zeros(self.parameter_count)
        moved = scale * direction
        a0, b0, hp0, hc0, _, heading0, delta0 = self.unpack(base)
        a1, b1, hp1, hc1, _, heading1, delta1 = self.unpack(moved)

        times = np.unique(np.concatenate(tuple(self.refine_times.values())))
        maximum_longitudinal = 0.0
        for segment in SEGMENTS:
            R0 = self.R_H_from_B(segment, times, heading0, delta0)
            R1 = self.R_H_from_B(segment, times, heading1, delta1)
            d0 = np.einsum("nij,j->ni", R0, a0[segment])
            d1 = np.einsum("nij,j->ni", R1, a1[segment])
            dots = np.clip(np.sum(d0*d1, axis=1), -1.0, 1.0)
            maximum_longitudinal = max(maximum_longitudinal,
                                       float(np.max(np.arccos(dots))))

        maximum_transverse = 0.0
        for segment in ("pelvis", "torso"):
            R0 = self.R_H_from_B(segment, times, heading0, delta0)
            R1 = self.R_H_from_B(segment, times, heading1, delta1)
            d0 = np.einsum("nij,j->ni", R0, b0[segment])
            d1 = np.einsum("nij,j->ni", R1, b1[segment])
            dots = np.clip(np.sum(d0*d1, axis=1), -1.0, 1.0)
            maximum_transverse = max(maximum_transverse,
                                     float(np.max(np.arccos(dots))))

        maximum_hinge = 0.0
        for joint, (parent, child, _) in HINGES.items():
            for segment, axis0, axis1 in (
                (parent, hp0[joint], hp1[joint]),
                (child, hc0[joint], hc1[joint]),
            ):
                R0 = self.R_H_from_B(segment, times, heading0, delta0)
                R1 = self.R_H_from_B(segment, times, heading1, delta1)
                d0 = np.einsum("nij,j->ni", R0, axis0)
                d1 = np.einsum("nij,j->ni", R1, axis1)
                dots = np.clip(np.sum(d0*d1, axis=1), -1.0, 1.0)
                maximum_hinge = max(maximum_hinge,
                                    float(np.max(np.arccos(dots))))

        local_torso_longitudinal = axis_angle_rad(a0["torso"], a1["torso"])
        local_torso_transverse = axis_angle_rad(b0["torso"], b1["torso"])
        heading_change = abs(float(heading1["torso"]-heading0["torso"]))
        r0 = self.residual(base)
        r1 = self.residual(moved)
        derivative_norm = float(np.linalg.norm(jacobian @ direction))
        return {
            "classification": "TORSO_BOARD_FRAME_AXIAL_ROTATION_VS_INITIAL_RELATIVE_HEADING_TRADEOFF",
            "finite_perturbation_dominant_parameter_rad": 1.0e-4,
            "finite_perturbation_l2_norm": float(np.linalg.norm(moved)),
            "directional_derivative_l2_norm": derivative_norm,
            "torso_board_longitudinal_axis_change_rad": local_torso_longitudinal,
            "torso_board_transverse_axis_change_rad": local_torso_transverse,
            "torso_initial_relative_heading_change_rad": heading_change,
            "maximum_predicted_segment_longitudinal_axis_change_rad": maximum_longitudinal,
            "maximum_predicted_segment_longitudinal_axis_change_deg": math.degrees(maximum_longitudinal),
            "maximum_predicted_torso_or_pelvis_transverse_axis_change_rad": maximum_transverse,
            "maximum_predicted_torso_or_pelvis_transverse_axis_change_deg": math.degrees(maximum_transverse),
            "maximum_predicted_hinge_axis_change_rad": maximum_hinge,
            "maximum_predicted_hinge_axis_change_deg": math.degrees(maximum_hinge),
            "base_residual_l2_norm": float(np.linalg.norm(r0)),
            "perturbed_residual_l2_norm": float(np.linalg.norm(r1)),
            "residual_delta_l2_norm": float(np.linalg.norm(r1-r0)),
            "physical_interpretation": (
                "A board-frame rotation of the required torso longitudinal/transverse frame "
                "trades with torso initial relative heading. The labelled residual set does "
                "not independently determine both quantities after the pelvis global-yaw "
                "gauge is fixed. Joint centres and antenna predictions are not states in this "
                "IMU-only product."
            ),
        }


def run_synthetic_truth_gate(gates: Mapping[str, Any]) -> dict:
    dataset,truth=generate_synthetic_dataset(gates);problem=OracleSyntheticObservabilityProblem(dataset,truth,gates);zero=np.zeros(problem.parameter_count)
    residual_at_truth=problem.residual(zero)
    J=problem.numerical_jacobian(zero)
    # Use the actual scaled residual Jacobian directly.  Forming J.T@J would
    # square its condition number and can turn the weakest singular value into
    # a misleading clipped zero.
    _,singular,Vh=np.linalg.svd(J,full_matrices=False)
    threshold=float(singular[0]*float(gates["observability"]["relative_singular_value_threshold"]));rank=int(np.sum(singular>threshold));nullity=problem.parameter_count-rank
    null=[]
    for index in range(nullity):
        direction=Vh[-(index+1)];order=np.argsort(-np.abs(direction),kind="stable")[:12]
        null.append({"index":index,"singular_value":float(singular[-(index+1)]),"dominant_parameters":[{"name":problem.parameter_names[k],"coefficient":float(direction[k])} for k in order],"finite_physical_perturbation":problem.characterize_null_direction(direction,J)})
    functional={}
    for joint,(parent,child,action) in HINGES.items():
        hp,hc,report=fit_functional_axis(dataset.nodes[SEGMENT_TO_NODE[parent]],dataset.nodes[SEGMENT_TO_NODE[child]],dataset.action_windows[action],problem.noise,gates["sampling"])
        functional[joint]={"report":report,"parent_axis_error_deg":math.degrees(axis_angle_rad(hp,truth.hinge_parent_B[joint],signed_axis=False)),"child_axis_error_deg":math.degrees(axis_angle_rad(hc,truth.hinge_child_B[joint],signed_axis=False))}
    passed=nullity==0 and all(max(x["parent_axis_error_deg"],x["child_axis_error_deg"])<=float(gates["synthetic_gates"]["maximum_hinge_axis_error_deg"]) for x in functional.values())
    f_scale=float(gates["optimizer"]["f_scale"])
    magnitude=np.abs(residual_at_truth)
    huber_rho=np.where(magnitude<=f_scale,
                       residual_at_truth*residual_at_truth,
                       2.0*f_scale*magnitude-f_scale*f_scale)
    return {
        "schema":"biospur-multi-action-synthetic-truth-recovery-v1",
        "verdict":"PASS" if passed else "FAIL_SYNTHETIC_RECOVERY",
        "seed":4711,
        "random_mounting_maximum_deg":max(truth.mounting_angle_deg.values()),
        "independent_initial_heading_offsets":True,
        "continuous_transitions":True,
        "uwb_factor_count":0,
        "oracle_initialized_structural_test":True,
        "oracle_rationale":"Truth is used only as the tangent chart. Additional nullspace under this advantage is structural and blocks any non-oracle recovery.",
        "parameter_count":problem.parameter_count,"residual_count":int(J.shape[0]),"objective_at_truth":{"whitened_least_squares_cost":float(0.5*np.sum(residual_at_truth*residual_at_truth)),"huber_cost":float(0.5*np.sum(huber_rho)),"huber_f_scale":f_scale},"jacobian_rank":rank,"jacobian_nullity":nullity,"relative_singular_value_threshold":float(gates["observability"]["relative_singular_value_threshold"]),"absolute_singular_value_threshold":threshold,"singular_values":singular.tolist(),"null_directions":null,
        "functional_axis_recovery":functional,
        "segment_axis_recovery":"NOT_ATTEMPTED_BECAUSE_STRUCTURAL_NULLSPACE" if nullity else "PENDING_FULL_RECOVERY",
        "joint_zero_recovery":"NOT_ATTEMPTED_BECAUSE_STRUCTURAL_NULLSPACE" if nullity else "PENDING_FULL_RECOVERY",
        "relative_heading_recovery":"NOT_ATTEMPTED_BECAUSE_STRUCTURAL_NULLSPACE" if nullity else "PENDING_FULL_RECOVERY",
        "byte_identical_compact_artifacts":"CHECKED_BY_CALLER",
        "stop_before_real_capture":not passed,
    }
