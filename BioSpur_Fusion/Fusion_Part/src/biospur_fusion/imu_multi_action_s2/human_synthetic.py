"""Contract-matched human-like ten-node synthetic IMU generator.

Truth fields are separated from the observation-only view consumed by the
segmenter and estimator.  No real-capture reader exists in this namespace.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

import numpy as np
from scipy.spatial.transform import Rotation

from biospur_fusion.imu_multi_action_v1.core import NodeSeries, normalize
from biospur_fusion.imu_multi_action_v1.synthetic import NODE_TO_SEGMENT, SEGMENT_TO_NODE


SEGMENTS = tuple(SEGMENT_TO_NODE)
ACTIONS = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow",
    "right_elbow_attempt2", "left_knee", "right_knee", "left_heel",
    "right_heel", "squats", "trunk",
)


@dataclass(frozen=True)
class PhaseTruth:
    raw_action: str
    semantic_phase: str
    start_ns: int
    stop_ns: int
    repetitions: int | None


@dataclass(frozen=True)
class HumanSyntheticTruth:
    R_W_from_S: Mapping[str, np.ndarray]
    R_S_from_B_nominal: Mapping[str, np.ndarray]
    a_B: Mapping[str, np.ndarray]
    transverse_B: Mapping[str, np.ndarray]
    hinge_axis_B: Mapping[str, tuple[np.ndarray, np.ndarray]]
    hip_axis_B: Mapping[str, tuple[np.ndarray, np.ndarray]]
    initial_heading_rad: Mapping[str, float]
    sensor_to_proximal_joint_B_m: Mapping[str, np.ndarray]
    sensor_position_W_m: Mapping[str, np.ndarray]
    graphical_nodes_W_m: Mapping[str, np.ndarray]
    root_position_W_m: np.ndarray
    phase_truth: tuple[PhaseTruth, ...]
    scenario: Mapping[str, Any]


@dataclass(frozen=True)
class ObservationOnlyDataset:
    """Estimator-visible synthetic input with truth fields physically absent."""

    nodes: Mapping[str, NodeSeries]
    action_windows: Mapping[str, tuple[int, int]]
    node_to_segment: Mapping[str, str]
    source_hashes: Mapping[str, str]


@dataclass(frozen=True)
class HumanSyntheticDataset:
    nodes: Mapping[str, NodeSeries]
    action_windows: Mapping[str, tuple[int, int]]
    node_to_segment: Mapping[str, str]
    source_hashes: Mapping[str, str]
    truth: HumanSyntheticTruth

    def observation_view(self) -> ObservationOnlyDataset:
        """Return only fields permitted to segmentation/inverse estimation."""
        return ObservationOnlyDataset(
            self.nodes, self.action_windows, self.node_to_segment,
            self.source_hashes,
        )


def _frame_from_longitudinal(direction: np.ndarray,
                             lateral_hint: np.ndarray = np.array([0.0, 1.0, 0.0])) -> np.ndarray:
    z = normalize(direction)
    y = np.asarray(lateral_hint, float)-z*float(z@lateral_hint)
    if np.linalg.norm(y) < 1e-8:
        y = np.array([1.0, 0.0, 0.0])-z*float(z@np.array([1.0, 0.0, 0.0]))
    y = normalize(y)
    x = normalize(np.cross(y, z))
    y = normalize(np.cross(z, x))
    return np.column_stack((x, y, z))


def _pulse(phase: np.ndarray, repetitions: int) -> np.ndarray:
    return 0.5*(1.0-np.cos(2.0*math.pi*repetitions*phase))


def _signed_cycle(phase: np.ndarray, repetitions: int) -> np.ndarray:
    return np.sin(2.0*math.pi*repetitions*phase)


def _phase(time_ns: np.ndarray, start_ns: int, stop_ns: int) -> tuple[np.ndarray, np.ndarray]:
    mask = (time_ns >= start_ns) & (time_ns <= stop_ns)
    p = np.clip((time_ns[mask]-start_ns)/float(stop_ns-start_ns), 0.0, 1.0)
    return mask, p


def _apply_long_axis(R: np.ndarray, mask: np.ndarray, direction: np.ndarray,
                     axial_twist_rad: np.ndarray | float = 0.0) -> None:
    direction = np.asarray(direction, float)
    if direction.ndim == 1:
        direction = np.repeat(direction[None], int(mask.sum()), axis=0)
    twist = np.broadcast_to(np.asarray(axial_twist_rad, float), (len(direction),))
    rows = np.empty((len(direction), 3, 3))
    for index, vector in enumerate(direction):
        rows[index] = _frame_from_longitudinal(vector) @ Rotation.from_rotvec(
            np.array([0.0, 0.0, twist[index]])
        ).as_matrix()
    R[mask] = rows


def _schedule(rate_hz: float) -> tuple[np.ndarray, dict[str, tuple[int, int]], list[PhaseTruth]]:
    duration = {
        "initial_still_attempt2": 3.0, "t_pose": 4.0, "arms": 18.0,
        "left_elbow": 12.0, "right_elbow_attempt2": 15.0,
        "left_knee": 8.0, "right_knee": 8.0,
        "left_heel": 8.0, "right_heel": 8.0,
        "squats": 8.0, "trunk": 18.0,
    }
    transition = 0.5
    cursor = 0.0
    windows: dict[str, tuple[int, int]] = {}
    phases: list[PhaseTruth] = []
    for action in ACTIONS:
        start = cursor; stop = start+duration[action]
        windows[action] = (round(start*1e9), round(stop*1e9))
        cursor = stop+transition
    def add(action: str, semantic: str, fraction0: float, fraction1: float,
            repetitions: int | None) -> None:
        start, stop = windows[action]
        phases.append(PhaseTruth(
            action, semantic,
            int(round(start+(stop-start)*fraction0)),
            int(round(start+(stop-start)*fraction1)), repetitions,
        ))
    add("initial_still_attempt2", "NATURAL_STANDING_STILL", 0, 1, 1)
    add("t_pose", "STATIC_BILATERAL_ARM_LINE", 0.15, 0.85, 1)
    add("arms", "LEFT_ARM_RAISE_LOWER", 0, 1/3, 5)
    add("arms", "RIGHT_ARM_RAISE_LOWER", 1/3, 2/3, 5)
    add("arms", "BILATERAL_ARM_RAISE_LOWER", 2/3, 1, 5)
    add("left_elbow", "LEFT_ELBOW_CURL", 0, 0.5, 5)
    add("left_elbow", "LEFT_FOREARM_PRONATION_SUPINATION", 0.5, 1, 5)
    add("right_elbow_attempt2", "RIGHT_ELBOW_CURL", 0, 0.4, 5)
    add("right_elbow_attempt2", "RIGHT_FOREARM_PRONATION_SUPINATION", 0.4, 0.8, 5)
    add("right_elbow_attempt2", "RIGHT_RETURN_STILL", 0.8, 1, 1)
    # Counts are scenario truth, not operator inputs. They intentionally vary.
    add("left_knee", "LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK", 0, 1, 4)
    add("right_knee", "RIGHT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK", 0, 1, 5)
    add("left_heel", "LEFT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION", 0, 1, 5)
    add("right_heel", "RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION", 0, 1, 4)
    add("squats", "BILATERAL_SQUAT", 0, 1, 5)
    add("trunk", "TRUNK_LEFT_ROTATION", 0, 1/3, 3)
    add("trunk", "TRUNK_RIGHT_ROTATION", 1/3, 2/3, 3)
    add("trunk", "TRUNK_FORWARD_BEND_AND_RECOVER", 2/3, 1, 3)
    time_ns = np.arange(0, round(cursor*1e9), round(1e9/rate_hz), dtype=np.int64)
    return time_ns, windows, phases


def _build_segment_orientations(time_ns: np.ndarray, phases: list[PhaseTruth],
                                seed: int, scenario: Mapping[str, Any]) -> tuple[dict[str, np.ndarray], np.ndarray]:
    count = len(time_ns)
    down = _frame_from_longitudinal(np.array([0.0, 0.0, -1.0]))
    relative = {segment: np.repeat((np.eye(3) if segment in ("pelvis", "torso") else down)[None], count, axis=0)
                for segment in SEGMENTS}
    seconds = time_ns/1e9
    # Natural common motion is never exactly zero outside the strict still unit.
    pelvis_rotvec = np.column_stack((
        np.deg2rad(0.6)*np.sin(2*math.pi*seconds/13.0),
        np.deg2rad(0.8)*np.sin(2*math.pi*seconds/17.0+0.3),
        np.deg2rad(0.5)*np.sin(2*math.pi*seconds/19.0+0.7),
    ))
    initial = (time_ns >= phases[0].start_ns) & (time_ns <= phases[0].stop_ns)
    pelvis_rotvec[initial] *= 0.08
    R_WP = Rotation.from_rotvec(pelvis_rotvec).as_matrix()
    phase_by_name = {p.semantic_phase: p for p in phases}

    # T-pose: asymmetric height and non-perfect horizontality, with smooth ramps.
    p = phase_by_name["STATIC_BILATERAL_ARM_LINE"]
    mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
    envelope = np.minimum(np.clip(q/0.18,0,1),np.clip((1-q)/0.18,0,1))
    left_target = np.array([0.025,0.999,0.035]);right_target=np.array([-0.020,-0.998,-0.052])
    for side, target in (("L", left_target),("R",right_target)):
        direction=(1-envelope[:,None])*np.array([0.,0.,-1.])+envelope[:,None]*target
        _apply_long_axis(relative[f"upper_arm_{side}"], mask, direction)
        _apply_long_axis(relative[f"forearm_{side}"], mask, direction+np.array([0.01, 0.0, -0.01]))

    # Arms: unknown, variable raise plane; inactive side has small non-zero leakage.
    arm_plane = float(scenario.get("arm_plane_rad", math.radians(38.0)))
    for semantic, active in (("LEFT_ARM_RAISE_LOWER", ("L",)),
                             ("RIGHT_ARM_RAISE_LOWER", ("R",)),
                             ("BILATERAL_ARM_RAISE_LOWER", ("L", "R"))):
        p = phase_by_name[semantic]; mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
        wave = _pulse(q, int(p.repetitions or 1))
        for side, sign in (("L", 1.0), ("R", -1.0)):
            amplitude = np.deg2rad(120.0 if side in active else 4.0)
            elevation = amplitude*wave*(1.0+(0.035 if side == "L" else -0.025)*np.sin(2*math.pi*q))
            plane = arm_plane+np.deg2rad(8.0)*np.sin(2*math.pi*q+sign)
            direction = np.column_stack((
                np.sin(elevation)*np.cos(plane),
                sign*np.sin(elevation)*np.sin(plane),
                -np.cos(elevation),
            ))
            _apply_long_axis(relative[f"upper_arm_{side}"], mask, direction)
            flex = np.deg2rad(12.0)*wave
            fore = direction+np.column_stack((0.12*np.sin(flex), np.zeros(len(q)), 0.08*np.sin(flex)))
            _apply_long_axis(relative[f"forearm_{side}"], mask, fore)

    # Elbow compound phases: curl and pronation are physically different.
    for side, prefix in (("L", "LEFT"), ("R", "RIGHT")):
        curl = phase_by_name[f"{prefix}_ELBOW_CURL"]
        mask, q = _phase(time_ns, curl.start_ns, curl.stop_ns)
        wave = _pulse(q, 5)
        leak = np.deg2rad(7.0)*wave
        upper = np.column_stack((0.08*np.sin(leak), np.zeros(len(q)), -np.cos(leak)))
        _apply_long_axis(relative[f"upper_arm_{side}"], mask, upper)
        flex = np.deg2rad(125.0)*wave
        fore = np.column_stack((np.sin(flex), 0.035*np.sin(4*math.pi*q), -np.cos(flex)))
        _apply_long_axis(relative[f"forearm_{side}"], mask, fore,
                         np.deg2rad(3.0)*np.sin(2*math.pi*q))
        pro = phase_by_name[f"{prefix}_FOREARM_PRONATION_SUPINATION"]
        mask, q = _phase(time_ns, pro.start_ns, pro.stop_ns)
        envelope=np.minimum(np.clip(q/0.12,0,1),np.clip((1-q)/0.12,0,1))
        target=np.column_stack((np.full(len(q),0.96),0.07*np.sin(2*math.pi*q),np.full(len(q),-0.27)))
        fore=(1-envelope[:,None])*np.array([0.,0.,-1.])+envelope[:,None]*target
        twist = envelope*np.deg2rad(75.0)*_signed_cycle(q, 5)
        _apply_long_axis(relative[f"forearm_{side}"], mask, fore, twist)
        upper = np.column_stack((0.03*np.sin(2*math.pi*q), np.zeros(len(q)), -np.ones(len(q))))
        _apply_long_axis(relative[f"upper_arm_{side}"], mask, upper)

    # Front high-knee: pelvis-thigh/hip-dominant; relaxed shank not locked.
    for side, semantic in (("L", "LEFT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK"),
                           ("R", "RIGHT_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK")):
        p = phase_by_name[semantic]; mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
        wave = _pulse(q, int(p.repetitions or 4))
        hip = np.deg2rad(82.0+(4.0 if side == "L" else -3.0))*wave
        direction = np.column_stack((np.sin(hip),
                                     (0.035 if side == "L" else -0.035)*np.sin(2*math.pi*q),
                                     -np.cos(hip)))
        _apply_long_axis(relative[f"thigh_{side}"], mask, direction)
        relaxed = np.deg2rad(9.0)*wave+np.deg2rad(4.0)*np.sin(2*math.pi*q)
        shank = np.column_stack((0.12*np.sin(relaxed),
                                 0.025*np.sin(4*math.pi*q),
                                 -np.cos(relaxed)))
        _apply_long_axis(relative[f"shank_{side}"], mask, shank)
        pelvis_rotvec[mask, 1] += -np.deg2rad(6.0)*wave

    # Rear heel-to-butt: thigh-shank/knee-dominant with hip/pelvis compensation.
    for side, semantic in (("L", "LEFT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION"),
                           ("R", "RIGHT_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION")):
        p = phase_by_name[semantic]; mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
        wave = _pulse(q, int(p.repetitions or 4))
        thigh_tilt = np.deg2rad(7.0)*wave+np.deg2rad(2.0)*np.sin(2*math.pi*q)
        thigh = np.column_stack((np.sin(thigh_tilt),
                                 (0.025 if side == "L" else -0.025)*np.sin(2*math.pi*q),
                                 -np.cos(thigh_tilt)))
        _apply_long_axis(relative[f"thigh_{side}"], mask, thigh)
        knee = np.deg2rad(130.0)*wave
        shank = np.column_stack((-np.sin(knee),
                                 (0.045 if side == "L" else -0.045)*np.sin(4*math.pi*q),
                                 -np.cos(knee)))
        _apply_long_axis(relative[f"shank_{side}"], mask, shank)
        pelvis_rotvec[mask, 0] += np.deg2rad(3.0)*wave

    # Squat: full bilateral chain plus torso/pelvis compensation.
    p = phase_by_name["BILATERAL_SQUAT"]; mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
    wave = _pulse(q, int(p.repetitions or 5))
    for side, sign in (("L", 1.0), ("R", -1.0)):
        hip = np.deg2rad(58.0)*wave*(1.0+0.03*sign)
        knee = np.deg2rad(78.0)*wave*(1.0-0.02*sign)
        thigh = np.column_stack((np.sin(hip), sign*0.02*np.sin(2*math.pi*q), -np.cos(hip)))
        shank = np.column_stack((-0.75*np.sin(knee), sign*0.025*np.sin(4*math.pi*q), -np.cos(knee)))
        _apply_long_axis(relative[f"thigh_{side}"], mask, thigh)
        _apply_long_axis(relative[f"shank_{side}"], mask, shank)
    pelvis_rotvec[mask, 1] += -np.deg2rad(12.0)*wave
    relative["torso"][mask] = Rotation.from_euler("y", (np.deg2rad(15.0)*wave)[:,None]).as_matrix()

    # Trunk: noncommuting full-SO(3) compositions with pelvis compensation.
    for semantic, twist_sign in (("TRUNK_LEFT_ROTATION", 1.0),
                                 ("TRUNK_RIGHT_ROTATION", -1.0)):
        p = phase_by_name[semantic]; mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
        wave = _pulse(q, 3)
        twist = twist_sign*np.deg2rad(float(scenario.get("trunk_turn_deg", 48.0)))*wave
        flex = np.deg2rad(6.0)*wave*np.sin(2*math.pi*q)
        lateral = np.deg2rad(4.0)*wave*np.sin(4*math.pi*q+0.4)
        relative["torso"][mask] = (
            Rotation.from_euler("z", twist[:,None]).as_matrix()
            @ Rotation.from_euler("x", lateral[:,None]).as_matrix()
            @ Rotation.from_euler("y", flex[:,None]).as_matrix()
        )
        pelvis_rotvec[mask, 2] += -0.16*twist
        pelvis_rotvec[mask, 1] += 0.12*flex
    p = phase_by_name["TRUNK_FORWARD_BEND_AND_RECOVER"]; mask, q = _phase(time_ns, p.start_ns, p.stop_ns)
    wave = _pulse(q, 3)
    flex = -np.deg2rad(float(scenario.get("trunk_bend_deg", 58.0)))*wave
    twist = np.deg2rad(7.0)*wave*np.sin(2*math.pi*q+0.3)
    lateral = np.deg2rad(5.0)*wave*np.sin(4*math.pi*q)
    relative["torso"][mask] = (
        Rotation.from_euler("z", twist[:,None]).as_matrix()
        @ Rotation.from_euler("x", lateral[:,None]).as_matrix()
        @ Rotation.from_euler("y", flex[:,None]).as_matrix()
    )
    pelvis_rotvec[mask, 1] += -0.18*flex
    pelvis_rotvec[mask, 2] += -0.10*twist

    R_WP = Rotation.from_rotvec(pelvis_rotvec).as_matrix()
    absolute = {segment: np.einsum("nij,njk->nik", R_WP, rows)
                for segment, rows in relative.items()}
    absolute["pelvis"] = R_WP
    return absolute, pelvis_rotvec


def _graphical_nodes(R_W_from_S: Mapping[str, np.ndarray], root: np.ndarray,
                     dimensions: Mapping[str, float]) -> dict[str, np.ndarray]:
    axes = {segment: R[:, :, 2] for segment, R in R_W_from_S.items()}
    lateral_t = R_W_from_S["torso"][:, :, 1]
    lateral_p = R_W_from_S["pelvis"][:, :, 1]
    pelvis = root
    c7 = pelvis+float(dimensions["C7Proxy_to_PelvisProxy_m"])*axes["torso"]
    nodes = {"PelvisProxy": pelvis, "C7Proxy": c7, "Central": c7}
    for side, sign in (("L", 1.0), ("R", -1.0)):
        shoulder = c7+sign*0.5*float(dimensions["graphical_shoulder_width_m"])*lateral_t
        elbow = shoulder+float(dimensions[f"rendering_upper_arm_length_{side}_m"])*axes[f"upper_arm_{side}"]
        wrist = elbow+float(dimensions[f"rendering_forearm_length_{side}_m"])*axes[f"forearm_{side}"]
        hip = pelvis+sign*0.5*float(dimensions["graphical_hip_width_m"])*lateral_p
        knee = hip+float(dimensions[f"rendering_thigh_length_{side}_m"])*axes[f"thigh_{side}"]
        ankle = knee+float(dimensions[f"rendering_shank_length_{side}_m"])*axes[f"shank_{side}"]
        nodes.update({f"ShoulderProxy_{side}":shoulder,f"Elbow_{side}":elbow,
                      f"Wrist_{side}":wrist,f"HipProxy_{side}":hip,
                      f"Knee_{side}":knee,f"Ankle_{side}":ankle})
    return nodes


def generate_human_motion_synthetic(gates: Mapping[str, Any], template: Mapping[str, Any],
                                    seed: int = 2201,
                                    scenario: Mapping[str, Any] | None = None) -> HumanSyntheticDataset:
    scenario = dict(scenario or {})
    rng = np.random.default_rng(seed)
    rate = float(gates["native_rate_hz"])
    time_ns, windows, phases = _schedule(rate)
    R_W_from_S, _ = _build_segment_orientations(time_ns, phases, seed, scenario)
    seconds = time_ns/1e9
    root = np.column_stack((
        0.018*np.sin(2*math.pi*seconds/11.0),
        0.014*np.sin(2*math.pi*seconds/13.0+0.4),
        0.010*np.sin(2*math.pi*seconds/7.0+0.2),
    ))
    dims = template["dimensions"]
    graph = _graphical_nodes(R_W_from_S, root, dims)
    proximal_name = {
        "pelvis":"PelvisProxy","torso":"PelvisProxy",
        "upper_arm_L":"ShoulderProxy_L","upper_arm_R":"ShoulderProxy_R",
        "forearm_L":"Elbow_L","forearm_R":"Elbow_R",
        "thigh_L":"HipProxy_L","thigh_R":"HipProxy_R",
        "shank_L":"Knee_L","shank_R":"Knee_R",
    }
    length = {
        "pelvis":0.10,"torso":float(dims["C7Proxy_to_PelvisProxy_m"]),
        "upper_arm_L":float(dims["rendering_upper_arm_length_L_m"]),
        "upper_arm_R":float(dims["rendering_upper_arm_length_R_m"]),
        "forearm_L":float(dims["rendering_forearm_length_L_m"]),
        "forearm_R":float(dims["rendering_forearm_length_R_m"]),
        "thigh_L":float(dims["rendering_thigh_length_L_m"]),
        "thigh_R":float(dims["rendering_thigh_length_R_m"]),
        "shank_L":float(dims["rendering_shank_length_L_m"]),
        "shank_R":float(dims["rendering_shank_length_R_m"]),
    }
    nodes={};mount={};a_B={};transverse={};headings={};lever={};positions={}
    hinge={};hip={}
    dt=1.0/rate; g_W=np.array([0.0,0.0,-9.80665])
    R_W_from_B_truth={}
    for index,(node,segment) in enumerate(sorted(NODE_TO_SEGMENT.items())):
        axis=normalize(rng.normal(size=3));angle=rng.uniform(0.0,math.radians(60.0))
        R_S_from_B=Rotation.from_rotvec(axis*angle).as_matrix();mount[segment]=R_S_from_B
        a_B[segment]=R_S_from_B.T@np.array([0.0,0.0,1.0])
        if segment in ("pelvis","torso"): transverse[segment]=R_S_from_B.T@np.array([0.0,1.0,0.0])
        heading=0.0 if segment=="pelvis" else rng.uniform(-math.pi,math.pi);headings[segment]=heading
        strap=(np.deg2rad(float(gates["human_motion"]["strap_perturbation_deg"]))
               * float(scenario.get("strap_perturbation_scale", 1.0)))
        strap_rotvec=np.column_stack((
            strap*np.sin(2*math.pi*seconds/(23.0+index)),
            0.6*strap*np.sin(2*math.pi*seconds/(29.0+index)+0.3),
            0.4*strap*np.sin(2*math.pi*seconds/(31.0+index)+0.6),
        ))
        R_strap=Rotation.from_rotvec(strap_rotvec).as_matrix()
        R_WB=np.einsum("nij,jk,nkl->nil",R_W_from_S[segment],R_S_from_B,R_strap)
        R_W_from_B_truth[segment]=R_WB
        r_S=np.array([0.012*(-1 if index%2 else 1),0.008*((index%3)-1),-0.48*length[segment]])
        r_B=R_S_from_B.T@r_S;lever[segment]=r_B
        q=graph[proximal_name[segment]]
        p_sensor=q-np.einsum("nij,j->ni",R_WB,r_B);positions[segment]=p_sensor
        velocity=np.gradient(p_sensor,dt,axis=0,edge_order=2)
        acceleration=np.gradient(velocity,dt,axis=0,edge_order=2)
        relative=np.einsum("nji,njk->nik",R_WB[:-1],R_WB[1:])
        gyro=np.zeros((len(time_ns),3));gyro[:-1]=Rotation.from_matrix(relative).as_rotvec()/dt;gyro[-1]=gyro[-2]
        f_B=np.einsum("nji,nj->ni",R_WB,acceleration-g_W)
        gyro_bias=rng.normal(scale=0.004,size=3);accel_bias=rng.normal(scale=0.025,size=3)
        drift_scale=float(scenario.get("correlated_drift_scale",1.0))
        noise_scale=float(scenario.get("white_noise_scale",1.0))
        correlated=np.column_stack([0.0012*drift_scale*np.sin(2*math.pi*seconds/(37+j+index)+j) for j in range(3)])
        gyro_obs=gyro+gyro_bias+correlated+rng.normal(scale=noise_scale*float(gates["noise_floors"]["gyro_rad_s"]),size=gyro.shape)
        accel_obs=f_B+accel_bias+rng.normal(scale=noise_scale*float(gates["noise_floors"]["accel_mps2"]),size=f_B.shape)
        yaw_drift=(float(scenario.get("yaw_drift_scale",1.0))*np.deg2rad(0.15)
                   *np.sin(2*math.pi*seconds/(41.0+index)+index))
        R_NB=np.empty_like(R_WB)
        for k in range(len(time_ns)):
            R_NB[k]=Rotation.from_euler("z",-(heading+yaw_drift[k])).as_matrix()@R_WB[k]
        nodes[node]=NodeSeries(time_ns.copy(),accel_obs,gyro_obs,R_NB,
                               np.full(len(time_ns),float(gates["noise_floors"]["orientation_rad"])),
                               np.full(3,0.0007))
    for side in ("L","R"):
        hinge[f"elbow_{side}"]=(mount[f"upper_arm_{side}"].T@np.array([0.,1.,0.]),mount[f"forearm_{side}"].T@np.array([0.,1.,0.]))
        hinge[f"knee_{side}"]=(mount[f"thigh_{side}"].T@np.array([0.,1.,0.]),mount[f"shank_{side}"].T@np.array([0.,1.,0.]))
        hip[f"hip_{side}"]=(mount["pelvis"].T@np.array([0.,1.,0.]),mount[f"thigh_{side}"].T@np.array([0.,1.,0.]))
    truth=HumanSyntheticTruth(R_W_from_S,mount,a_B,transverse,hinge,hip,headings,lever,positions,graph,root,tuple(phases),{"seed":seed,"high_knee_counts":{"L":4,"R":5},"heel_kick_counts":{"L":5,"R":4},**scenario})
    return HumanSyntheticDataset(nodes,windows,NODE_TO_SEGMENT,{"generator":"HUMAN_MOTION_SYNTHETIC_S2_V1","seed":str(seed)},truth)
