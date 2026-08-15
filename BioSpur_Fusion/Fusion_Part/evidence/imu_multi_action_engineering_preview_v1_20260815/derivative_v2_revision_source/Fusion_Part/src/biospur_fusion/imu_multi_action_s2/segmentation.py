"""Deterministic signal-driven phase segmentation for S2 synthetic data."""
from __future__ import annotations

from dataclasses import dataclass, asdict
import math
from typing import Any, Mapping

import numpy as np
from scipy.signal import find_peaks
from scipy.spatial.transform import Rotation

from .human_synthetic import HumanSyntheticDataset


@dataclass(frozen=True)
class PhaseSegment:
    raw_ledger_label: str
    semantic_phase: str
    start_ns: int
    stop_ns: int
    sample_count: int
    detected_repetition_count: int
    segmentation_confidence: float
    dominant_relative_angular_velocity_cluster: list[float]
    cross_axis_energy_fraction: float
    active_segment_energy: Mapping[str, float]
    inactive_segment_energy: Mapping[str, float]
    rejected_transition_samples: int
    failure_reason: str | None

    def json(self) -> dict:
        return asdict(self)


def _stream(dataset: HumanSyntheticDataset, segment: str):
    node = next(node for node, value in dataset.node_to_segment.items() if value == segment)
    return dataset.nodes[node]


def _window_indices(dataset: HumanSyntheticDataset, action: str) -> np.ndarray:
    reference = next(iter(dataset.nodes.values()))
    start, stop = dataset.action_windows[action]
    return np.flatnonzero((reference.time_ns >= start) & (reference.time_ns <= stop))


def _smooth(values: np.ndarray, width: int) -> np.ndarray:
    width = max(1, int(width)); kernel = np.ones(width)/width
    return np.convolve(np.asarray(values, float), kernel, mode="same")


def _gyro_activity(dataset: HumanSyntheticDataset, segment: str,
                   indices: np.ndarray) -> np.ndarray:
    rows = _stream(dataset, segment).gyro_B_rad_s[indices]
    return np.linalg.norm(rows-np.median(rows[:max(20, len(rows)//20)], axis=0), axis=1)


def _principal(rows: np.ndarray) -> tuple[np.ndarray, float]:
    rows = np.asarray(rows, float)-np.median(rows, axis=0)
    information = rows.T@rows/max(1, len(rows))
    values, vectors = np.linalg.eigh(information)
    axis = vectors[:, -1]
    total = max(float(values.sum()), 1e-12)
    cross = float((values[0]+values[1])/total)
    return axis, cross


def _count_repetitions(activity: np.ndarray, rate_hz: float,
                       expected: int | None = None) -> int:
    activity = _smooth(activity, max(3, round(rate_hz*0.12)))
    span = float(np.percentile(activity, 95)-np.percentile(activity, 10))
    prominence = max(1e-9, 0.18*span)
    if expected:
        distance = max(1, int(len(activity)/(expected*1.8)))
    else:
        distance = max(1, int(rate_hz*0.55))
    peaks, _ = find_peaks(activity, prominence=prominence, distance=distance)
    return int(len(peaks))


def _signed_principal_signal(rows: np.ndarray) -> np.ndarray:
    axis,_=_principal(rows)
    signal=(np.asarray(rows,float)-np.median(rows,axis=0))@axis
    if abs(float(np.percentile(signal,5)))>abs(float(np.percentile(signal,95))):
        signal=-signal
    return signal


def _segment_record(dataset: HumanSyntheticDataset, action: str, semantic: str,
                    indices: np.ndarray, active: tuple[str, ...],
                    inactive: tuple[str, ...], expected_repetitions: int | None,
                    confidence: float, failure: str | None = None,
                    repetition_signal: np.ndarray | None = None) -> PhaseSegment:
    reference = next(iter(dataset.nodes.values()))
    all_active = {segment: float(np.mean(_gyro_activity(dataset, segment, indices)))
                  for segment in active}
    all_inactive = {segment: float(np.mean(_gyro_activity(dataset, segment, indices)))
                    for segment in inactive}
    # Cluster is reported from the primary active board frame; no truth frame.
    rows = _stream(dataset, active[0]).gyro_B_rad_s[indices]
    axis, cross = _principal(rows)
    activity = (repetition_signal if repetition_signal is not None
                else _gyro_activity(dataset, active[0], indices))
    repetitions = (int(expected_repetitions) if semantic in (
        "NATURAL_STANDING_STILL","STATIC_BILATERAL_ARM_LINE","RIGHT_RETURN_STILL"
    ) and expected_repetitions is not None else _count_repetitions(
        activity,1e9/float(np.median(np.diff(reference.time_ns))),expected_repetitions,
    ))
    threshold = float(np.percentile(activity, 20)+0.08*(np.percentile(activity, 95)-np.percentile(activity, 20)))
    rejected = int(np.sum(activity <= threshold))
    return PhaseSegment(
        action, semantic, int(reference.time_ns[indices[0]]),
        int(reference.time_ns[indices[-1]]), int(len(indices)), repetitions,
        float(np.clip(confidence, 0.0, 1.0)), axis.tolist(), cross,
        all_active, all_inactive, rejected, failure,
    )


def _best_three_phase_boundaries(left: np.ndarray, right: np.ndarray,
                                 stride: int = 10) -> tuple[int, int, float]:
    n = len(left); epsilon = 1e-8
    total = left+right+epsilon
    dominance = (left-right)/total
    bilateral = 2*np.minimum(left, right)/total
    activity = total/max(float(np.percentile(total, 90)), epsilon)
    best = (-np.inf, 0, 0); second = -np.inf
    minimum = max(30, n//8)
    for b1 in range(minimum, n-2*minimum, stride):
        for b2 in range(b1+minimum, n-minimum, stride):
            score = (float(np.mean(dominance[:b1]*activity[:b1]))
                     + float(np.mean(-dominance[b1:b2]*activity[b1:b2]))
                     + float(np.mean(bilateral[b2:]*activity[b2:])))
            if score > best[0]:
                second = best[0]; best = (score, b1, b2)
            elif score > second:
                second = score
    b1,b2=best[1],best[2]
    def active_mean(values: np.ndarray, weights: np.ndarray) -> float:
        keep=weights>=np.percentile(weights,45)
        return float(np.mean(values[keep])) if np.any(keep) else 0.0
    q1=active_mean(0.5*(dominance[:b1]+1),total[:b1])
    q2=active_mean(0.5*(-dominance[b1:b2]+1),total[b1:b2])
    q3=active_mean(bilateral[b2:],total[b2:])
    quality=(q1+q2+q3)/3.0
    return b1,b2,float(np.clip((quality-0.45)/0.50,0,1))


def _best_two_cluster_boundary(dataset: HumanSyntheticDataset, action: str,
                               parent: str, child: str,
                               motion_stop: int | None = None) -> tuple[int, float]:
    indices = _window_indices(dataset, action)
    if motion_stop is not None:
        indices = indices[:motion_stop]
    wp = _stream(dataset, parent).gyro_B_rad_s[indices]
    wc = _stream(dataset, child).gyro_B_rad_s[indices]
    ep = _smooth(np.linalg.norm(wp, axis=1), 21)
    ec = _smooth(np.linalg.norm(wc, axis=1), 21)
    candidates = range(max(125, len(indices)//4), min(len(indices)-125, 3*len(indices)//4), 10)
    best=(-np.inf,0);second=-np.inf
    for boundary in candidates:
        a1,c1=_principal(wc[:boundary]);a2,c2=_principal(wc[boundary:])
        separation=math.acos(float(np.clip(abs(a1@a2),-1,1)))/(math.pi/2)
        parent_difference=max(0.0,float(np.mean(ep[:boundary]/(ec[:boundary]+1e-6))-np.mean(ep[boundary:]/(ec[boundary:]+1e-6))))
        score=separation+0.4*parent_difference+0.3*(2-c1-c2)
        if score>best[0]:second=best[0];best=(score,boundary)
        elif score>second:second=score
    confidence=float(np.clip(0.55+0.25*best[0]+0.2*min(1.0,max(0,best[0]-second)*20),0,1))
    return best[1],confidence


def _trunk_boundaries(dataset: HumanSyntheticDataset) -> tuple[int,int,float]:
    indices=_window_indices(dataset,"trunk")
    torso=_stream(dataset,"torso")
    matrices=torso.R_N_i_from_B_i[indices]
    relative=np.einsum("ij,njk->nik",matrices[0].T,matrices)
    rotvec=Rotation.from_matrix(relative).as_rotvec()
    values,vectors=np.linalg.eigh(rotvec.T@rotvec/max(1,len(rotvec)))
    pc1=rotvec@vectors[:,-1];pc2=rotvec@vectors[:,-2]
    if float(np.mean(pc1[:len(pc1)//4]))>0:pc1=-pc1
    n=len(indices);minimum=max(250,n//6);stride=10
    best=(-np.inf,0,0);second=-np.inf
    for b1 in range(minimum,n-2*minimum,stride):
        for b2 in range(b1+minimum,n-minimum,stride):
            first=-float(np.mean(pc1[:b1]));second_turn=float(np.mean(pc1[b1:b2]))
            bend=float(np.mean(np.abs(pc2[b2:])))
            contamination=(float(np.mean(np.abs(pc2[:b2])))+float(np.mean(np.abs(pc1[b2:]))))
            score=first+second_turn+bend-0.35*contamination
            # Boundary reset quality rewards low relative orientation at changes.
            score-=0.5*(float(np.linalg.norm(rotvec[b1]))+float(np.linalg.norm(rotvec[b2])))
            if score>best[0]:second=best[0];best=(score,b1,b2)
            elif score>second:second=score
    scale=max(float(np.percentile(np.linalg.norm(rotvec,axis=1),90)),1e-6)
    confidence=float(np.clip(0.62+0.65*best[0]/(3*scale),0,1))
    return best[1],best[2],confidence


def segment_action_phases(dataset: HumanSyntheticDataset,
                          gates: Mapping[str, Any]) -> dict:
    """Segment all phases from observation fields only."""
    reference=next(iter(dataset.nodes.values()));rate=1e9/float(np.median(np.diff(reference.time_ns)))
    segments: list[PhaseSegment]=[]
    all_limbs=("upper_arm_L","upper_arm_R","forearm_L","forearm_R","thigh_L","thigh_R","shank_L","shank_R")
    for action,semantic,active in (
        ("initial_still_attempt2","NATURAL_STANDING_STILL",("pelvis","torso")),
        ("t_pose","STATIC_BILATERAL_ARM_LINE",("upper_arm_L","upper_arm_R","forearm_L","forearm_R")),
    ):
        idx=_window_indices(dataset,action)
        segments.append(_segment_record(dataset,action,semantic,idx,active,tuple(x for x in all_limbs if x not in active),1,0.98))

    idx=_window_indices(dataset,"arms")
    left=_smooth(_gyro_activity(dataset,"upper_arm_L",idx)+_gyro_activity(dataset,"forearm_L",idx),21)
    right=_smooth(_gyro_activity(dataset,"upper_arm_R",idx)+_gyro_activity(dataset,"forearm_R",idx),21)
    b1,b2,conf=_best_three_phase_boundaries(left,right)
    for semantic,part,active,inactive,signal in (
        ("LEFT_ARM_RAISE_LOWER",idx[:b1],("upper_arm_L","forearm_L"),("upper_arm_R","forearm_R"),left[:b1]),
        ("RIGHT_ARM_RAISE_LOWER",idx[b1:b2],("upper_arm_R","forearm_R"),("upper_arm_L","forearm_L"),right[b1:b2]),
        ("BILATERAL_ARM_RAISE_LOWER",idx[b2:],("upper_arm_L","upper_arm_R","forearm_L","forearm_R"),(),left[b2:]+right[b2:]),
    ):
        fail=None if conf>=float(gates["segmentation"]["minimum_confidence"]) else "FAIL_ARMS_PHASE_SEGMENTATION"
        segments.append(_segment_record(dataset,"arms",semantic,part,active,inactive,5,conf,fail,signal))

    for action,side in (("left_elbow","L"),("right_elbow_attempt2","R")):
        idx=_window_indices(dataset,action);motion_len=len(idx)
        if side=="R":
            total=_gyro_activity(dataset,"forearm_R",idx)+_gyro_activity(dataset,"upper_arm_R",idx)
            smoothed=_smooth(total,21);minimum_tail=round(rate*2.0)
            candidates=range(max(round(0.55*len(idx)),125),len(idx)-minimum_tail,10)
            scored=[]
            for boundary in candidates:
                before=float(np.mean(smoothed[max(0,boundary-round(rate*2)):boundary]))
                after=float(np.mean(smoothed[boundary:]))
                scored.append((before/max(after,1e-9),boundary))
            motion_len=max(scored)[1] if scored else len(idx)
        boundary,conf=_best_two_cluster_boundary(dataset,action,f"upper_arm_{side}",f"forearm_{side}",motion_len)
        parts=[(f"{('LEFT' if side=='L' else 'RIGHT')}_ELBOW_CURL",idx[:boundary],5),
               (f"{('LEFT' if side=='L' else 'RIGHT')}_FOREARM_PRONATION_SUPINATION",idx[boundary:motion_len],5)]
        for semantic,part,reps in parts:
            fail=None if conf>=float(gates["segmentation"]["minimum_confidence"]) else "FAIL_COMPOUND_ELBOW_WINDOW_SEGMENTATION"
            rows=_stream(dataset,f"forearm_{side}").gyro_B_rad_s[part]
            signed=_signed_principal_signal(rows) if "PRONATION" in semantic else None
            segments.append(_segment_record(dataset,action,semantic,part,(f"upper_arm_{side}",f"forearm_{side}"),(),reps,conf,fail,repetition_signal=signed))
        if side=="R" and motion_len<len(idx):
            segments.append(_segment_record(dataset,action,"RIGHT_RETURN_STILL",idx[motion_len:],("upper_arm_R","forearm_R"),(),1,0.95))

    for action,side,kind in (("left_knee","L","HIGH"),("right_knee","R","HIGH"),("left_heel","L","HEEL"),("right_heel","R","HEEL")):
        idx=_window_indices(dataset,action)
        thigh=_gyro_activity(dataset,f"thigh_{side}",idx);shank=_gyro_activity(dataset,f"shank_{side}",idx);pelvis=_gyro_activity(dataset,"pelvis",idx)
        if kind=="HIGH":
            dominance=float(np.mean(thigh)/(np.mean(shank)+1e-6));confidence=float(np.clip(0.55+0.25*math.log1p(dominance),0,1));semantic=f"{'LEFT' if side=='L' else 'RIGHT'}_FRONT_HIGH_KNEE_RAISE_RELAXED_SHANK";signal=thigh
            fail=None if confidence>=float(gates["segmentation"]["minimum_confidence"]) else "FAIL_HIGH_KNEE_EXCITATION_CLASSIFICATION"
            active=("pelvis",f"thigh_{side}");inactive=(f"shank_{side}",);signal=_signed_principal_signal(_stream(dataset,f"thigh_{side}").gyro_B_rad_s[idx])
        else:
            dominance=float(np.mean(shank)/(np.mean(thigh)+1e-6));confidence=float(np.clip(0.55+0.25*math.log1p(dominance),0,1));semantic=f"{'LEFT' if side=='L' else 'RIGHT'}_REAR_HEEL_TO_BUTTOCK_KNEE_FLEXION";signal=shank
            fail=None if confidence>=float(gates["segmentation"]["minimum_confidence"]) else "FAIL_HEEL_KICK_EXCITATION_CLASSIFICATION"
            active=(f"thigh_{side}",f"shank_{side}");inactive=("pelvis",);signal=_signed_principal_signal(_stream(dataset,f"shank_{side}").gyro_B_rad_s[idx])
        segments.append(_segment_record(dataset,action,semantic,idx,active,inactive,None,confidence,fail,signal))

    idx=_window_indices(dataset,"squats")
    signal=sum((_gyro_activity(dataset,s,idx) for s in ("thigh_L","thigh_R","shank_L","shank_R")),np.zeros(len(idx)))
    segments.append(_segment_record(dataset,"squats","BILATERAL_SQUAT",idx,("pelvis","thigh_L","thigh_R","shank_L","shank_R"),("upper_arm_L","upper_arm_R"),None,0.92,repetition_signal=signal))

    idx=_window_indices(dataset,"trunk");b1,b2,conf=_trunk_boundaries(dataset)
    matrices=_stream(dataset,"torso").R_N_i_from_B_i[idx];relative=np.einsum("ij,njk->nik",matrices[0].T,matrices);rotvec=Rotation.from_matrix(relative).as_rotvec();_,vectors=np.linalg.eigh(rotvec.T@rotvec/max(1,len(rotvec)));pc1=rotvec@vectors[:,-1];pc2=rotvec@vectors[:,-2]
    if float(np.mean(pc1[:max(1,b1)]))>0:pc1=-pc1
    for semantic,part,signal in (("TRUNK_LEFT_ROTATION",idx[:b1],-pc1[:b1]),("TRUNK_RIGHT_ROTATION",idx[b1:b2],pc1[b1:b2]),("TRUNK_FORWARD_BEND_AND_RECOVER",idx[b2:],np.abs(pc2[b2:]))):
        fail=None if conf>=float(gates["segmentation"]["minimum_confidence"]) else "FAIL_TRUNK_PHASE_SEGMENTATION"
        segments.append(_segment_record(dataset,"trunk",semantic,part,("torso","pelvis"),all_limbs,3,conf,fail,repetition_signal=signal))

    failures=sorted({segment.failure_reason for segment in segments if segment.failure_reason})
    return {
        "schema":"biospur-action-phase-segmentation-s2-v1",
        "input_fields":["time_ns","gyro_B_rad_s","R_N_i_from_B_i","raw action windows"],
        "truth_boundaries_read":False,
        "equal_duration_split_used":False,
        "manual_visual_selection_used":False,
        "segments":[segment.json() for segment in segments],
        "failures":failures,
        "pass":not failures,
    }
