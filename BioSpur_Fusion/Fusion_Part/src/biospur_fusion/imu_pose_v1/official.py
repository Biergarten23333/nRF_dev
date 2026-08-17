from __future__ import annotations

from dataclasses import dataclass
import contextlib
import io
import time
from typing import Mapping
import warnings
import numpy as np

from .types import ImuSample


@dataclass(frozen=True)
class VqfNodeResult:
    time_s: np.ndarray
    quaternion6D_W_I: np.ndarray
    gyro_bias_rad_s: np.ndarray
    bias_sigma_rad_s: np.ndarray
    rest_detected: np.ndarray
    lineage_sample_uids: tuple[tuple[str, ...], ...]
    runtime_s: float


def uniform_resample(samples: list[ImuSample], rate_hz: float = 200.0) -> tuple[np.ndarray, np.ndarray, np.ndarray, tuple[tuple[str, ...], ...]]:
    if len(samples) < 2 or any(samples[i].time_s > samples[i+1].time_s for i in range(len(samples)-1)):
        raise ValueError("ordered samples required")
    t_src = np.array([x.time_s for x in samples])
    dt = 1.0/rate_hz
    t = np.arange(t_src[0], t_src[-1]+dt*.25, dt)
    gyro = np.column_stack([np.interp(t, t_src, [x.gyro_rad_s[k] for x in samples]) for k in range(3)])
    accel = np.column_stack([np.interp(t, t_src, [x.accel_m_s2[k] for x in samples]) for k in range(3)])
    right = np.searchsorted(t_src, t, side="left").clip(0, len(samples)-1)
    left = np.maximum(right-1, 0)
    lineage = tuple(tuple(dict.fromkeys((samples[l].uid, samples[r].uid))) for l,r in zip(left,right))
    return t, gyro, accel, lineage


def run_official_vqf(samples: list[ImuSample], rate_hz: float = 200.0) -> VqfNodeResult:
    from vqf import VQF
    t, gyro, accel, lineage = uniform_resample(samples, rate_hz)
    start = time.perf_counter()
    result = VQF(gyrTs=1.0/rate_hz, accTs=1.0/rate_hz).updateBatchFullState(gyro, accel)
    runtime = time.perf_counter()-start
    return VqfNodeResult(t, np.asarray(result["quat6D"]), np.asarray(result["bias"]),
                         np.asarray(result["biasSigma"]), np.asarray(result["restDetected"], bool),
                         lineage, runtime)


def run_official_vqf_all(samples_by_node: Mapping[str, list[ImuSample]], rate_hz: float = 200.0) -> dict[str, VqfNodeResult]:
    return {node: run_official_vqf(samples_by_node[node], rate_hz) for node in sorted(samples_by_node)}


@dataclass(frozen=True)
class QmtAxisResult:
    parent_axis_sensor: np.ndarray
    child_axis_sensor: np.ndarray
    confidence: float
    runtime_s: float
    sample_count: int


def run_qmt_hinge_axis(acc_parent: np.ndarray, acc_child: np.ndarray,
                       gyro_parent: np.ndarray, gyro_child: np.ndarray) -> QmtAxisResult:
    import qmt
    if len(acc_parent) > 1600:
        take = np.linspace(0, len(acc_parent)-1, 1600, dtype=int)
        acc_parent, acc_child = acc_parent[take], acc_child[take]
        gyro_parent, gyro_child = gyro_parent[take], gyro_child[take]
    start = time.perf_counter()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        with contextlib.redirect_stdout(io.StringIO()):
            a, b, debug = qmt.jointAxisEstHingeOlsson(acc_parent, acc_child, gyro_parent, gyro_child,
                                                       estSettings={"useSampleSelection": False}, debug=True)
    runtime = time.perf_counter()-start
    a = np.asarray(a).reshape(3); b = np.asarray(b).reshape(3)
    energy = np.linalg.eigvalsh(np.cov((gyro_child-gyro_parent).T))
    confidence = float(np.clip((energy[-1]-energy[-2])/max(energy[-1], 1e-12), 0, 1))
    return QmtAxisResult(a/np.linalg.norm(a), b/np.linalg.norm(b), confidence, runtime, len(acc_parent))


@dataclass(frozen=True)
class QmtHeadingResult:
    corrected_child_quaternion: np.ndarray
    heading_offset_rad: np.ndarray
    filtered_offset_rad: np.ndarray
    rating: np.ndarray
    state: np.ndarray
    confidence: float
    runtime_s: float


def run_qmt_heading(gyro_parent: np.ndarray, gyro_child: np.ndarray,
                    q_parent: np.ndarray, q_child: np.ndarray, t: np.ndarray,
                    child_axis_sensor: np.ndarray) -> QmtHeadingResult:
    import qmt
    start = time.perf_counter()
    corrected, delta, filtered, rating, state = qmt.headingCorrection(
        gyro_parent, gyro_child, q_parent, q_child, t, np.asarray(child_axis_sensor), {},
        estSettings={"windowTime": min(8.0, max(2.0, (t[-1]-t[0])*.4)), "dataRate": 5.0,
                     "estimationRate": 1.0, "alignment": "backward"},
    )
    runtime = time.perf_counter()-start
    rating = np.asarray(rating).reshape(-1)
    confidence = float(np.clip(np.nanmedian(rating[np.isfinite(rating)]) if np.any(np.isfinite(rating)) else 0, 0, 1))
    return QmtHeadingResult(np.asarray(corrected), np.asarray(delta), np.asarray(filtered),
                            rating, np.asarray(state), confidence, runtime)


def run_qmt_reset_alignment(quaternions: np.ndarray, reset_index: int,
                            desired_q_W_S: np.ndarray | None = None) -> np.ndarray:
    import qmt
    if quaternions.ndim != 3 or quaternions.shape[-1] != 4:
        raise ValueError("M x N x 4 expected")
    count = quaternions.shape[1]; desired = np.tile([1.,0,0,0],(quaternions.shape[0],1)) if desired_q_W_S is None else np.asarray(desired_q_W_S,float)
    if desired.shape != (quaternions.shape[0],4): raise ValueError("one desired segment orientation per sensor")
    from . import so3
    aligned=[]
    for sensor in range(quaternions.shape[0]):
        reset=np.zeros(count,bool);reset[reset_index]=True;R=so3.matrix(desired[sensor])
        x=np.tile(R[:,0],(count,1));y=np.zeros((count,3));z=np.tile(R[:,2],(count,1))
        out=qmt.resetAlignment(quaternions[sensor:sensor+1],reset,x=x,xCs=-1,y=y,yCs=-1,z=z,zCs=-1,exactAxis="z")
        aligned.append(np.asarray(out[0]))
    return np.stack(aligned)


def reject_incompatible_six_sensor_checkpoint(*, channels: tuple[str, ...], weights_requested: bool) -> None:
    forbidden = {"head", "synthetic_head", "zero_filled", "copied_sensor"}
    if weights_requested or forbidden.intersection(channels) or len(channels) != 10:
        raise ValueError("PIP/TransPose six-sensor pretrained path is incompatible and disabled")
