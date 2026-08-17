from __future__ import annotations

import json
from pathlib import Path
import numpy as np

from .types import PoseFrame


def _list(x): return np.asarray(x, float).tolist()


def frame_dict(frame: PoseFrame) -> dict:
    return {
        "schema":"biospur-imu-pose-v1-frame",
        "time_s":frame.time_s, "measurement_cutoff_time_s":frame.cutoff_time_s,
        "active_modality":frame.active_modality,
        "segment_quaternion_W_S":{k:_list(v) for k,v in frame.segment_quaternions_W_S.items()},
        "joint_quaternion_parent_child":{k:_list(v) for k,v in frame.joint_quaternions_parent_child.items()},
        "normalized_joint_position_L0":{k:_list(v) for k,v in frame.normalized_joint_positions.items()},
        "segment_tilt_sigma_rad":dict(frame.segment_tilt_sigma_rad),
        "joint_relative_sigma_rad":dict(frame.joint_relative_sigma_rad),
        "segment_quality":dict(frame.segment_quality), "joint_quality":dict(frame.joint_quality),
        "whole_body_available":frame.whole_body_available,
        "degraded_reasons":list(frame.degraded_reasons), "gauges":list(frame.gauges),
        "root_position_L0":[0,0,0], "root_world_position":"UNAVAILABLE",
        "scale":"MODEL_INFERRED_SCALE_CONDITIONAL", "head":"MODEL_INFERRED",
        "hands":"MODEL_INFERRED", "feet":"UNAVAILABLE",
        "external_accuracy_claim":False,
    }


def write_jsonl(path: Path, frames: list[PoseFrame]) -> None:
    with Path(path).open("w", encoding="utf-8") as out:
        for frame in frames:
            out.write(json.dumps(frame_dict(frame), sort_keys=True, separators=(",",":"))+"\n")
