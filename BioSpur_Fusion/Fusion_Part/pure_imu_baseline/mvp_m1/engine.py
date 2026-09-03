"""Shared raw/working/global-gauge pose engine.

The engine has no capture, action-label, camera, confidence, final-pose, or UWB
input. Gauge state changes only through an explicit command supplied by the
operator-facing adapter.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from pure_imu_baseline.config import PARENT_CHILD, SEGMENT_ORDER
from pure_imu_baseline.math3d import conjugate, multiply, normalize, rotate

from . import PRODUCT_ID

QUALITY_VALID = 0
QUALITY_RECENTLY_RESET = 1
QUALITY_STALE = 2
QUALITY_UNAVAILABLE = 3
QUALITY_NAMES = np.array(["VALID", "RECENTLY_RESET", "STALE", "UNAVAILABLE"])

GAUGE_RAW = 0
GAUGE_OPERATOR = 1
GAUGE_SOURCE_NAMES = np.array(["RAW", "EXPLICIT_OPERATOR_COMMAND"])

RESET_NONE = 0
RESET_FILTER = 1
RESET_REASON_NAMES = np.array(["NONE", "FILTER_RESET"])

COMMAND_RECENTER = "RECENTER_YAW"
COMMAND_CLEAR = "CLEAR_RECENTER"


def qz(angle: np.ndarray | float) -> np.ndarray:
    """Scalar-first quaternion for a pure global-Z gauge rotation."""
    value = np.asarray(angle, dtype=np.float64)
    out = np.zeros(value.shape + (4,), dtype=np.float64)
    out[..., 0] = np.cos(value/2.0); out[..., 3] = np.sin(value/2.0)
    return out


@dataclass(frozen=True)
class InputPacket:
    timestamp_us: int
    frame_index: int
    raw_q_GB_wxyz: np.ndarray
    validity_mask: np.ndarray
    filter_reset: np.ndarray
    raw_joint_positions_m: np.ndarray
    joint_available: np.ndarray


@dataclass(frozen=True)
class GaugeCommand:
    frame_index: int
    command: str


def normalized_working_view(raw_q: np.ndarray, valid: np.ndarray) -> np.ndarray:
    raw = np.asarray(raw_q)
    valid = np.asarray(valid, dtype=bool)
    out = raw.astype(np.float64, copy=True)
    finite = valid & np.all(np.isfinite(out), axis=-1)
    out[finite] = normalize(out[finite])
    out[~finite] = np.nan
    return out


def relative_quaternions_float64(q: np.ndarray, valid: np.ndarray,
                                 segment_names: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    names = [str(value) for value in segment_names]
    index = {name: position for position, name in enumerate(names)}
    values = []; masks = []
    for parent_name, child_name in PARENT_CHILD:
        parent, child = index[parent_name], index[child_name]
        mask = valid[:, parent] & valid[:, child]
        safe_parent = np.where(mask[:, None], q[:, parent], [1.0, 0.0, 0.0, 0.0])
        safe_child = np.where(mask[:, None], q[:, child], [1.0, 0.0, 0.0, 0.0])
        relative = normalize(multiply(conjugate(safe_parent), safe_child))
        relative[~mask] = np.nan
        values.append(relative); masks.append(mask)
    return np.stack(values, axis=1), np.stack(masks, axis=1)


def epoch_and_health(time_s: np.ndarray, valid: np.ndarray, reset: np.ndarray,
                     config: dict) -> dict[str, np.ndarray]:
    t = np.asarray(time_s, dtype=np.float64)
    valid = np.asarray(valid, dtype=bool)
    reset = np.asarray(reset, dtype=bool)
    n, m = valid.shape
    epoch = np.cumsum(reset.astype(np.uint32), axis=0, dtype=np.uint32)
    quality = np.full((n, m), QUALITY_UNAVAILABLE, dtype=np.uint8)
    age = np.full((n, m), np.inf, dtype=np.float64)
    sample_age = np.full((n, m), np.inf, dtype=np.float64)
    since_reset = np.full((n, m), np.inf, dtype=np.float64)
    reset_reason = np.zeros((n, m), dtype=np.uint8)
    recent = float(config["recently_reset_duration_s"])
    stale = float(config["stale_duration_s"])
    for node in range(m):
        last_valid = None; last_reset = None; last_reason = RESET_NONE
        for frame in range(n):
            if reset[frame, node]:
                last_reset = float(t[frame]); last_reason = RESET_FILTER
            if last_reset is not None:
                since_reset[frame, node] = float(t[frame]) - last_reset
            reset_reason[frame, node] = last_reason
            if valid[frame, node]:
                last_valid = float(t[frame]); age[frame, node] = 0.0; sample_age[frame, node] = 0.0
                quality[frame, node] = QUALITY_RECENTLY_RESET if since_reset[frame, node] <= recent else QUALITY_VALID
            elif last_valid is not None:
                value = float(t[frame]) - last_valid
                age[frame, node] = value; sample_age[frame, node] = value
                quality[frame, node] = QUALITY_STALE if value <= stale else QUALITY_UNAVAILABLE
    return {"epoch_per_node": epoch, "quality_state": quality,
            "time_since_last_valid_s": age, "sample_age_s": sample_age,
            "time_since_last_reset_s": since_reset, "last_reset_reason": reset_reason}


def recenter_gamma(q_pelvis: np.ndarray, config: dict) -> tuple[bool, float, float]:
    q = normalize(np.asarray(q_pelvis, dtype=np.float64))
    forward = rotate(q, np.asarray(config["pelvis_body_forward_axis"], dtype=np.float64))
    horizontal_norm = float(np.hypot(forward[0], forward[1]))
    if not np.isfinite(horizontal_norm) or horizontal_norm <= float(config["horizontal_projection_minimum_norm"]):
        return False, 0.0, horizontal_norm
    return True, float(-np.arctan2(forward[1], forward[0])), horizontal_norm


def apply_global_gauge(q_work: np.ndarray, raw_positions: np.ndarray,
                       valid: np.ndarray, joint_available: np.ndarray,
                       gamma: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    q_work = np.asarray(q_work, dtype=np.float64)
    raw_positions = np.asarray(raw_positions)
    valid = np.asarray(valid, dtype=bool)
    joint_available = np.asarray(joint_available, dtype=bool)
    gamma = np.asarray(gamma, dtype=np.float64)
    display_q = q_work.copy()
    apply = valid & np.all(np.isfinite(q_work), axis=-1)
    rotations = np.broadcast_to(qz(gamma)[:, None, :], q_work.shape)
    display_q[apply] = normalize(multiply(rotations[apply], q_work[apply]))
    display_q[~apply] = np.nan
    display_positions = raw_positions.astype(np.float64, copy=True)
    root = raw_positions[:, :1, :].astype(np.float64)
    relative = display_positions - root
    cos = np.cos(gamma)[:, None]; sin = np.sin(gamma)[:, None]
    x = relative[..., 0].copy(); y = relative[..., 1].copy()
    relative[..., 0] = cos*x - sin*y
    relative[..., 1] = sin*x + cos*y
    display_positions = root + relative
    display_positions[~joint_available] = np.nan
    return display_q, display_positions


class PoseEngine:
    """Deterministic batch/replay core with explicit-only gauge commands."""

    def __init__(self, config: dict):
        if config.get("product_id") != PRODUCT_ID:
            raise ValueError("wrong product configuration")
        self.config = config
        self.reset_stream()

    def reset_stream(self) -> None:
        """Reset runtime state; this never changes any input evidence."""
        nodes = len(SEGMENT_ORDER)
        self._stream_last_timestamp_us = None
        self._stream_last_frame = None
        self._stream_epoch = np.zeros(nodes, dtype=np.uint32)
        self._stream_last_valid_s = np.full(nodes, np.nan, dtype=np.float64)
        self._stream_last_reset_s = np.full(nodes, np.nan, dtype=np.float64)
        self._stream_reset_reason = np.zeros(nodes, dtype=np.uint8)
        self._stream_gamma = 0.0
        self._stream_gauge_epoch = 0
        self._stream_gauge_source = GAUGE_RAW
        self._stream_last_recenter_s = None

    def process_packet(self, packet: InputPacket, commands: list[str] | None = None) -> dict:
        """Process one replay/live packet through the same product mathematics.

        The adapter owns acquisition. This method accepts no camera, confidence,
        action-label, final-still, correction, or UWB fields.
        """
        timestamp_us = int(packet.timestamp_us); frame = int(packet.frame_index)
        if self._stream_last_timestamp_us is not None and timestamp_us <= self._stream_last_timestamp_us:
            raise ValueError("stream timestamps must be strictly increasing")
        if self._stream_last_frame is not None and frame != self._stream_last_frame + 1:
            raise ValueError("stream frame indices must be contiguous")
        self._stream_last_timestamp_us = timestamp_us; self._stream_last_frame = frame
        time_s = timestamp_us / 1e6
        valid = np.asarray(packet.validity_mask, dtype=bool)
        reset = np.asarray(packet.filter_reset, dtype=bool)
        if valid.shape != (len(SEGMENT_ORDER),) or reset.shape != valid.shape:
            raise ValueError("invalid stream validity/reset shape")
        q_work = normalized_working_view(np.asarray(packet.raw_q_GB_wxyz)[None], valid[None])[0]
        self._stream_epoch += reset.astype(np.uint32)
        self._stream_last_reset_s[reset] = time_s
        self._stream_reset_reason[reset] = RESET_FILTER
        quality = np.full(len(SEGMENT_ORDER), QUALITY_UNAVAILABLE, dtype=np.uint8)
        sample_age = np.full(len(SEGMENT_ORDER), np.inf, dtype=np.float64)
        since_reset = np.where(np.isfinite(self._stream_last_reset_s), time_s-self._stream_last_reset_s, np.inf)
        recent = float(self.config["recently_reset_duration_s"]); stale = float(self.config["stale_duration_s"])
        for node in range(len(SEGMENT_ORDER)):
            if valid[node]:
                self._stream_last_valid_s[node] = time_s; sample_age[node] = 0.0
                quality[node] = QUALITY_RECENTLY_RESET if since_reset[node] <= recent else QUALITY_VALID
            elif np.isfinite(self._stream_last_valid_s[node]):
                sample_age[node] = time_s-self._stream_last_valid_s[node]
                quality[node] = QUALITY_STALE if sample_age[node] <= stale else QUALITY_UNAVAILABLE
        events = []
        pelvis = SEGMENT_ORDER.index("pelvis")
        for command in commands or []:
            if command == COMMAND_RECENTER:
                if not valid[pelvis]:
                    events.append({"frame_index":frame,"timestamp_us":timestamp_us,"command":command,"accepted":False,"reason":"PELVIS_UNAVAILABLE"})
                    continue
                accepted, value, horizontal = recenter_gamma(q_work[pelvis], self.config)
                if not accepted:
                    events.append({"frame_index":frame,"timestamp_us":timestamp_us,"command":command,"accepted":False,"reason":"DEGENERATE_HORIZONTAL_FORWARD","horizontal_projection_norm":horizontal})
                    continue
                self._stream_gamma=value; self._stream_gauge_epoch+=1; self._stream_gauge_source=GAUGE_OPERATOR; self._stream_last_recenter_s=time_s
                events.append({"frame_index":frame,"timestamp_us":timestamp_us,"command":command,"accepted":True,"gamma_rad":value,"global_yaw_gauge_epoch":self._stream_gauge_epoch,"classification":"OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE"})
            elif command == COMMAND_CLEAR:
                self._stream_gamma=0.0; self._stream_gauge_epoch+=1; self._stream_gauge_source=GAUGE_RAW; self._stream_last_recenter_s=time_s
                events.append({"frame_index":frame,"timestamp_us":timestamp_us,"command":command,"accepted":True,"gamma_rad":0.0,"global_yaw_gauge_epoch":self._stream_gauge_epoch,"classification":"OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE"})
            else:
                raise ValueError(f"unknown explicit gauge command: {command}")
        display_q, display_positions = apply_global_gauge(q_work[None], np.asarray(packet.raw_joint_positions_m)[None], valid[None], np.asarray(packet.joint_available,dtype=bool)[None], np.array([self._stream_gamma]))
        work_pc, relative_valid = relative_quaternions_float64(q_work[None], valid[None], np.asarray(SEGMENT_ORDER))
        display_pc, display_relative_valid = relative_quaternions_float64(display_q, valid[None], np.asarray(SEGMENT_ORDER))
        if not np.array_equal(relative_valid, display_relative_valid):
            raise RuntimeError("display changed stream relative validity")
        since_recenter = np.inf if self._stream_last_recenter_s is None else time_s-self._stream_last_recenter_s
        return {"timestamp_us":timestamp_us,"frame_index":frame,"raw_q_GB_wxyz":packet.raw_q_GB_wxyz,
                "working_q_GB_wxyz":q_work,"display_q_GB_wxyz":display_q[0],
                "raw_q_PC_wxyz":work_pc[0],"display_q_PC_wxyz":display_pc[0],
                "raw_joint_positions_m":packet.raw_joint_positions_m,"display_joint_positions_m":display_positions[0],
                "validity_mask":valid,"epoch_per_node":self._stream_epoch.copy(),"reset_state_per_node":reset,
                "quality_state":quality,"sample_age_s":sample_age,"time_since_last_valid_s":sample_age.copy(),
                "time_since_last_reset_s":since_reset,"last_reset_reason":self._stream_reset_reason.copy(),
                "global_yaw_gauge_rad":self._stream_gamma,"global_yaw_gauge_epoch":self._stream_gauge_epoch,
                "global_yaw_gauge_source":str(GAUGE_SOURCE_NAMES[self._stream_gauge_source]),
                "root_mode":self.config["root_mode"],"time_since_manual_recenter_s":since_recenter,
                "manual_recenter_events":events}

    def process(self, raw: dict[str, np.ndarray], commands: list[GaugeCommand] | None = None) -> dict:
        time_s = np.asarray(raw["time_s"], dtype=np.float64)
        if np.any(~np.isfinite(time_s)) or np.any(np.diff(time_s) <= 0):
            raise ValueError("timestamps must be finite and strictly increasing")
        valid = np.asarray(raw["valid"], dtype=bool)
        q_work = normalized_working_view(raw["q_GB_wxyz"], valid)
        health = epoch_and_health(time_s, valid, raw["filter_reset"], self.config)
        gamma = np.zeros(len(time_s), dtype=np.float64)
        gauge_epoch = np.zeros(len(time_s), dtype=np.uint32)
        gauge_source = np.zeros(len(time_s), dtype=np.uint8)
        time_since_recenter = np.full(len(time_s), np.inf, dtype=np.float64)
        command_map: dict[int, list[str]] = {}
        for command in commands or []:
            command_map.setdefault(int(command.frame_index), []).append(command.command)
        current_gamma = 0.0; current_epoch = 0; current_source = GAUGE_RAW; last_command_time = None
        events = []
        pelvis = [str(value) for value in raw["segment_names"]].index("pelvis")
        for frame in range(len(time_s)):
            for command in command_map.get(frame, []):
                if command == COMMAND_RECENTER:
                    if not valid[frame, pelvis]:
                        events.append({"frame_index": frame, "timestamp_us": int(round(time_s[frame]*1e6)),
                                       "command": command, "accepted": False, "reason": "PELVIS_UNAVAILABLE"})
                        continue
                    accepted, value, horizontal_norm = recenter_gamma(q_work[frame, pelvis], self.config)
                    if not accepted:
                        events.append({"frame_index": frame, "timestamp_us": int(round(time_s[frame]*1e6)),
                                       "command": command, "accepted": False, "reason": "DEGENERATE_HORIZONTAL_FORWARD",
                                       "horizontal_projection_norm": horizontal_norm})
                        continue
                    current_gamma = value; current_epoch += 1; current_source = GAUGE_OPERATOR; last_command_time = float(time_s[frame])
                    events.append({"frame_index": frame, "timestamp_us": int(round(time_s[frame]*1e6)),
                                   "command": command, "accepted": True, "gamma_rad": value,
                                   "global_yaw_gauge_epoch": current_epoch,
                                   "classification": "OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE"})
                elif command == COMMAND_CLEAR:
                    current_gamma = 0.0; current_epoch += 1; current_source = GAUGE_RAW; last_command_time = float(time_s[frame])
                    events.append({"frame_index": frame, "timestamp_us": int(round(time_s[frame]*1e6)),
                                   "command": command, "accepted": True, "gamma_rad": 0.0,
                                   "global_yaw_gauge_epoch": current_epoch,
                                   "classification": "OPERATOR_REQUESTED_GLOBAL_GAUGE_CHANGE"})
                else:
                    raise ValueError(f"unknown explicit gauge command: {command}")
            gamma[frame] = current_gamma; gauge_epoch[frame] = current_epoch; gauge_source[frame] = current_source
            if last_command_time is not None:
                time_since_recenter[frame] = float(time_s[frame]) - last_command_time
        display_q, display_positions = apply_global_gauge(q_work, raw["joint_positions_m"], valid,
                                                          raw["joint_available"], gamma)
        work_pc, relative_valid = relative_quaternions_float64(q_work, valid, raw["segment_names"])
        display_pc, display_relative_valid = relative_quaternions_float64(display_q, valid, raw["segment_names"])
        if not np.array_equal(relative_valid, display_relative_valid):
            raise RuntimeError("display changed relative validity")
        return {
            "timestamp_us": np.rint(time_s*1e6).astype(np.int64),
            "frame_index": np.arange(len(time_s), dtype=np.int64),
            "working_q_GB_wxyz": q_work,
            "display_q_GB_wxyz": display_q,
            "working_q_PC_wxyz": work_pc,
            "display_q_PC_wxyz": display_pc,
            "display_joint_positions_m": display_positions,
            **health,
            "global_yaw_gauge_rad": gamma,
            "global_yaw_gauge_epoch": gauge_epoch,
            "global_yaw_gauge_source_code": gauge_source,
            "global_yaw_gauge_source": GAUGE_SOURCE_NAMES[gauge_source],
            "root_mode": np.full(len(time_s), self.config["root_mode"]),
            "time_since_manual_recenter_s": time_since_recenter,
            "manual_recenter_events": events,
            "relative_valid": relative_valid,
        }
