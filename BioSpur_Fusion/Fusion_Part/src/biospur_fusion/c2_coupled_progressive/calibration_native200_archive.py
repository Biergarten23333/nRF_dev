"""Read-only native-200 pose/IMU archive for the full acquired calibration.

The protocol numbers actions 00--19, but action 01 was explicitly skipped by
the operator and has no capture payload.  This owner therefore exposes the 19
acquired actions (00 and 02--19) without inventing an action or resetting state
at action labels.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np

from biospur_fusion.c2_uwb_calibration.articulated_range import SEGMENTS

from .contracts import EPISODES, ROOT


PELVIS_NODE = "BSFC2CC"
ACTION_KEYS = MappingProxyType({
    action: f"{index:02d}" for index, action in enumerate(EPISODES)
})
FRONTEND = (
    ROOT / "logs/c2_basis_progressive_20260829T102836Z/CONTINUATION_SPRINT/"
    "C2_NONHINGE_TRAINING_REPLAY_001/FRONTEND_RECONSTRUCTION_INPUTS.npz"
)
FRONTEND_MANIFEST = FRONTEND.with_suffix(".json")
TRAJECTORY = (
    ROOT / "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "ARTICULATED_CALIBRATION_TRAJECTORY.npz"
)
EXPECTED_SHA256 = MappingProxyType({
    FRONTEND: "58f88f9fb59d64a20c9c3c1f29db2309eb2a38e6fb3bd62d982969b51bf54cd7",
    FRONTEND_MANIFEST: "db1ed458cbc80ad07cd1a885d5ac42498ab6057ea560d6535524ae244b5e7a22",
    TRAJECTORY: "94f9afb088c7f05a7dbcae0c7d6d2c18be76a6ca32e1d9b96861a8deb7962937",
})


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _readonly(value: object, *, shape: tuple[int, ...] | None = None) -> np.ndarray:
    result = np.asarray(value).copy()
    if shape is not None and result.shape != shape:
        raise ValueError(f"array must have shape {shape}, got {result.shape}")
    if result.dtype.kind in "fc" and not np.isfinite(result).all():
        raise ValueError("archive array is non-finite")
    result.setflags(write=False)
    return result


@dataclass(frozen=True)
class CalibrationNative200Action:
    action_id: str
    key: str
    time_us: np.ndarray
    boot: np.ndarray
    span: np.ndarray
    calibrated_acc_mps2: np.ndarray
    sensor_quat_wxyz: np.ndarray
    trajectory: Mapping[str, Mapping[str, np.ndarray]]

    def __post_init__(self) -> None:
        if ACTION_KEYS.get(self.action_id) != self.key:
            raise ValueError("calibration action/key ownership mismatch")
        time = _readonly(self.time_us)
        count = len(time)
        arrays = {
            "boot": _readonly(self.boot, shape=(count,)),
            "span": _readonly(self.span, shape=(count,)),
            "calibrated_acc_mps2": _readonly(
                self.calibrated_acc_mps2, shape=(count, 3)
            ),
            "sensor_quat_wxyz": _readonly(
                self.sensor_quat_wxyz, shape=(count, 4)
            ),
        }
        if time.shape != (count,) or count < 2 or np.any(np.diff(time) <= 0):
            raise ValueError("invalid native-200 time axis")
        same_span = arrays["span"][1:] == arrays["span"][:-1]
        if np.any(np.diff(time)[same_span] != 5000):
            raise ValueError("calibration source violates 5 ms cadence inside a span")
        if np.any(np.diff(time)[~same_span] <= 5000):
            raise ValueError("calibration span boundary does not own a real data gap")
        frozen_trajectory = {}
        relative_time_s = (time - time[0]).astype(float) * 1e-6
        reference_time = None
        for segment in SEGMENTS:
            row = self.trajectory[segment]
            root_time = _readonly(row["time_root_s"], shape=(count,))
            quaternion = _readonly(
                row["quat_world_segment_wxyz"], shape=(count, 4)
            )
            mask = _readonly(row["mask"], shape=(count,))
            if mask.dtype != bool or not np.all(mask):
                raise ValueError("full calibration archive contains a masked pose frame")
            if np.max(np.abs((root_time - root_time[0]) - relative_time_s)) > 1e-9:
                raise ValueError("pose and IMU native-200 axes differ")
            if reference_time is not None and not np.array_equal(root_time, reference_time):
                raise ValueError("segment pose time axes differ")
            reference_time = root_time
            frozen_trajectory[segment] = MappingProxyType({
                "time_root_s": root_time,
                "quat_world_segment_wxyz": quaternion,
                "mask": mask,
            })
        object.__setattr__(self, "time_us", time)
        for name, value in arrays.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "trajectory", MappingProxyType(frozen_trajectory))


class CalibrationNative200Archive:
    """Exact 19-action source inventory; labels never imply state resets."""

    def __init__(self, actions: Mapping[str, CalibrationNative200Action]) -> None:
        if tuple(actions) != EPISODES:
            raise ValueError("native-200 archive requires exact acquired-action order")
        self.actions = MappingProxyType(dict(actions))

    @classmethod
    def from_sealed_archives(cls) -> "CalibrationNative200Archive":
        for path, expected in EXPECTED_SHA256.items():
            if _sha256(path) != expected:
                raise RuntimeError(f"sealed calibration archive changed: {path}")
        manifest = json.loads(FRONTEND_MANIFEST.read_text(encoding="utf-8"))
        if manifest.get("frontend_reconstruction_role") != (
            "CHRONOLOGICAL_RECONSTRUCTION_OF_ESTABLISHED_CAPTURE_WIDE_POSTERIOR"
        ):
            raise RuntimeError("frontend archive role changed")
        actions = {}
        with np.load(FRONTEND, allow_pickle=False) as frontend, np.load(
            TRAJECTORY, allow_pickle=False
        ) as trajectory:
            for action_id in EPISODES:
                key = ACTION_KEYS[action_id]
                base = f"orientation/{key}/{PELVIS_NODE}"
                pose = {
                    segment: {
                        "time_root_s": trajectory[
                            f"trajectory/{key}/{segment}/time_root_s"
                        ],
                        "quat_world_segment_wxyz": trajectory[
                            f"trajectory/{key}/{segment}/quat_world_segment_wxyz"
                        ],
                        "mask": trajectory[f"trajectory/{key}/{segment}/mask"],
                    }
                    for segment in SEGMENTS
                }
                actions[action_id] = CalibrationNative200Action(
                    action_id=action_id,
                    key=key,
                    time_us=frontend[f"{base}/time_us"],
                    boot=frontend[f"{base}/derived_boot_epoch"],
                    span=frontend[f"{base}/contiguous_span_id"],
                    calibrated_acc_mps2=frontend[f"{base}/acc_mps2"],
                    sensor_quat_wxyz=frontend[f"{base}/quat_world_sensor_wxyz"],
                    trajectory=pose,
                )
        return cls(actions)
