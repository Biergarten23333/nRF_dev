"""Shared native-200 pelvis orientation and ankle-contact input adapters.

These helpers are action-neutral.  They decode already-loaded events and build
pose-derived ankle interpolation; they do not own or open a dataset.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import math
from typing import Any

import numpy as np
import qmt
from scipy.spatial.transform import Rotation

from biospur_fusion.ingest.events import RecordType
from biospur_fusion.c2_uwb_root_world.authoritative_articulated_fusion import (
    Native200ClockMappingOwner,
)


PELVIS_NODE = "BSFC2CC"
ANKLE_NODE_TO_SIDE: Mapping[str, str] = {
    "BSF6C53": "left",
    "BSF8BC4": "right",
}
GRAVITY_MPS2 = 9.80665


def _yaw_rotation(angle: float) -> np.ndarray:
    cosine, sine = math.cos(angle), math.sin(angle)
    return np.array([
        [cosine, -sine, 0.0],
        [sine, cosine, 0.0],
        [0.0, 0.0, 1.0],
    ])


def pelvis_imu(
    events: list[Any],
    clock: Any,
    start_ns: int,
    yaw_offset_deg: float,
    *,
    include_source_ticks: bool = False,
    clock_mapping_owner: Native200ClockMappingOwner | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Decode the pelvis IMU through one continuous native-200 VQF owner."""

    source_rows = [
        event for event in events
        if event.node_id == PELVIS_NODE and event.record_type is RecordType.IMU
    ]
    if include_source_ticks:
        if (
            type(clock_mapping_owner) is not Native200ClockMappingOwner
            or clock_mapping_owner.node != PELVIS_NODE
            or clock_mapping_owner.clock_domain != "B306_TIMER2"
        ):
            raise RuntimeError("exact pelvis decode requires its clock mapping owner")
        timer_order = [int(event.node_timer_us) for event in source_rows]
        if any(right <= left for left, right in zip(timer_order, timer_order[1:])):
            raise RuntimeError("pelvis source ticks are duplicate or reordered")
    rows = sorted(source_rows, key=lambda event: int(event.node_timer_us))
    if len(rows) < 100:
        raise RuntimeError("insufficient pelvis IMU")
    block = qmt.OriEstVQFBlock(0.005)
    decoded = []
    for event in rows:
        acceleration = (
            np.asarray(event.payload["acc_raw"], float) / 2048.0 * GRAVITY_MPS2
        )
        gyroscope = np.deg2rad(
            np.asarray(event.payload["gyro_raw"], float) / 16.384
        )
        quaternion = np.asarray(block.step(gyroscope, acceleration, None), float)
        rotation = Rotation.from_quat(quaternion[[1, 2, 3, 0]]).as_matrix()
        timer_us = int(event.node_timer_us)
        mapped_global_ns = (
            clock_mapping_owner.global_ns(timer_us)
            if include_source_ticks else None
        )
        decoded_row = {
            "time_s": (
                mapped_global_ns * 1e-9
                if include_source_ticks else clock.seconds(timer_us)
            ),
            "acceleration": acceleration,
            "rotation_vqf": rotation,
            "sequence": int(event.sequence),
        }
        if include_source_ticks:
            if isinstance(event.boot_epoch, bool):
                raise RuntimeError("pelvis source tick lacks typed clock ownership")
            boot_epoch = int(event.boot_epoch)
            if (
                boot_epoch != clock_mapping_owner.boot_epoch
                or (
                    event.global_time_ns is not None
                    and (
                        isinstance(event.global_time_ns, bool)
                        or int(event.global_time_ns) != mapped_global_ns
                    )
                )
            ):
                raise RuntimeError("pelvis source tick/clock mapping mismatch")
            decoded_row.update({
                "source_node": PELVIS_NODE,
                "source_boot_epoch": boot_epoch,
                "source_timer_us": timer_us,
                "source_global_ns": mapped_global_ns,
                "source_clock_domain": "B306_TIMER2",
                "source_clock_mapping_digest": clock_mapping_owner.digest,
            })
        decoded.append(decoded_row)

    preparation = [
        row
        for row in decoded
        if start_ns * 1e-9 - 2.0 <= row["time_s"] < start_ns * 1e-9
    ]
    if len(preparation) < 100:
        raise RuntimeError("insufficient pre-action IMU for the yaw gauge")
    forward = np.median(
        np.stack([
            row["rotation_vqf"] @ np.array([0.0, 0.0, -1.0])
            for row in preparation
        ]),
        axis=0,
    )
    if np.linalg.norm(forward[:2]) < 0.25:
        raise RuntimeError("pelvis -Z lacks a stable horizontal heading projection")
    measured_angle = math.atan2(forward[1], forward[0])
    yaw_delta = -math.pi / 2.0 - measured_angle + math.radians(float(yaw_offset_deg))
    navigation_from_vqf = _yaw_rotation(yaw_delta)
    for row in decoded:
        row["rotation_world"] = navigation_from_vqf @ row.pop("rotation_vqf")
    audit = {
        "orientation_filter": "qmt.OriEstVQFBlock",
        "vqf_instances": 1,
        "vqf_resets": 0,
        "sample_period_argument_s": 0.005,
        "initial_yaw_role": "SOFT_GAUGE_NOT_ABSOLUTE_YAW_CALIBRATION",
        "initial_body_forward_target_world": [0.0, -1.0, 0.0],
        "pelvis_sensor_forward_proxy": "sensor_minus_Z_qualitative",
        "yaw_delta_rad": yaw_delta,
        "yaw_sensitivity_offset_deg": float(yaw_offset_deg),
    }
    if include_source_ticks:
        audit["source_tick_owner"] = {
            "node": PELVIS_NODE,
            "clock_domain": "B306_TIMER2",
            "boot_epoch": clock_mapping_owner.boot_epoch,
            "clock_owner_sha256": clock_mapping_owner.clock_owner_sha256,
            "mapping_digest": clock_mapping_owner.digest,
            "first_timer_us": int(decoded[0]["source_timer_us"]),
            "last_timer_us": int(decoded[-1]["source_timer_us"]),
            "source_global_ns_checked": True,
        }
    return decoded, audit


def ankle_imu_rows(
    events: list[Any],
    clocks: Mapping[str, Any],
    lo_ns: int,
    hi_ns: int,
) -> list[dict[str, Any]]:
    """Decode left/right ankle IMU rows on their common-global clocks."""

    rows = []
    for event in events:
        side = ANKLE_NODE_TO_SIDE.get(event.node_id)
        if side is None or event.record_type is not RecordType.IMU:
            continue
        time_s = clocks[event.node_id].seconds(int(event.node_timer_us))
        if lo_ns * 1e-9 <= time_s < hi_ns * 1e-9:
            rows.append({
                "side": side,
                "time_s": time_s,
                "acceleration": (
                    np.asarray(event.payload["acc_raw"], float)
                    / 2048.0
                    * GRAVITY_MPS2
                ),
                "gyro": np.deg2rad(
                    np.asarray(event.payload["gyro_raw"], float) / 16.384
                ),
                "sequence": int(event.sequence),
            })
    rows.sort(key=lambda row: (row["time_s"], row["side"]))
    return rows


def ankle_proxy_interpolator(
    proxy_at_fraction: Callable[
        [float], tuple[dict[str, np.ndarray], dict[str, np.ndarray], int]
    ],
    action_start_s: float,
    action_stop_s: float,
    *,
    pose_time_grid_s: np.ndarray | None = None,
) -> Callable[[float], tuple[dict[str, np.ndarray], dict[str, np.ndarray]]]:
    """Build the existing analytic ankle offset/velocity interpolator."""

    duration = action_stop_s - action_start_s
    if pose_time_grid_s is None:
        count = max(2, int(np.ceil(duration * 10.0)) + 1)
        fractions = np.linspace(0.0, 1.0, count)
    else:
        pose_time = np.asarray(pose_time_grid_s, dtype=float)
        if (
            pose_time.ndim != 1
            or len(pose_time) < 2
            or not np.all(np.diff(pose_time) > 0.0)
        ):
            raise ValueError("pose time grid must be strictly increasing")
        fractions = (pose_time - pose_time[0]) / (pose_time[-1] - pose_time[0])
    times = action_start_s + duration * fractions
    offsets = {"left": [], "right": []}
    for fraction in fractions:
        body_offsets, _, _ = proxy_at_fraction(float(fraction))
        offsets["left"].append(np.asarray(body_offsets["BSF6C53"], float))
        offsets["right"].append(np.asarray(body_offsets["BSF8BC4"], float))
    offset_arrays = {side: np.stack(values) for side, values in offsets.items()}
    velocity_arrays = {
        side: np.gradient(values, times, axis=0, edge_order=1)
        for side, values in offset_arrays.items()
    }

    def interpolate(
        time_s: float,
    ) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
        query = float(np.clip(time_s, times[0], times[-1]))
        position = {
            side: np.asarray([
                np.interp(query, times, offset_arrays[side][:, axis])
                for axis in range(3)
            ])
            for side in ("left", "right")
        }
        velocity = {
            side: np.asarray([
                np.interp(query, times, velocity_arrays[side][:, axis])
                for axis in range(3)
            ])
            for side in ("left", "right")
        }
        return position, velocity

    return interpolate
