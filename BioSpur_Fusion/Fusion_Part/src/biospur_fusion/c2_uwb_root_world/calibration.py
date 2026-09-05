"""Calibration-only lineage for the approved C2 UWB U0 model."""
from __future__ import annotations

import dataclasses
import math
from collections import defaultdict
from pathlib import Path
from typing import Mapping, Sequence

import numpy as np

from biospur_fusion.uwb.frontend import CanonicalT4Frontend, T4Observation

from .u0 import ClockModel, UwbRow, predict_state, solve_u0_row

CALIBRATION_ORDER = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle", "04_shoulder_left",
    "05_shoulder_right", "06_elbow_left", "07_elbow_right", "08_hip_left",
    "09_hip_right", "10_knee_left_seated", "11_knee_right_seated", "12_heel_raise_left",
    "13_heel_raise_right", "14_trunk_flex_extend", "15_trunk_axial_rotation",
    "16_squat", "17_final_still", "18_heel_to_butt_left", "19_heel_to_butt_right",
)


def held_measurement_valid(row: UwbRow, held: int) -> bool:
    return (0 <= held < 8 and bool(row.valid_mask & (1 << held))
            and 0 < row.ranges_mm[held] < 0xffff
            and math.isfinite(float(row.t_round_us[held])))


def acceleration_mad(times_s: np.ndarray, positions_m: np.ndarray) -> np.ndarray | None:
    if len(times_s) < 3:
        return None
    dt0 = times_s[1:-1] - times_s[:-2]
    dt1 = times_s[2:] - times_s[1:-1]
    good = np.isfinite(dt0) & np.isfinite(dt1) & (dt0 > 0) & (dt1 > 0)
    if not np.any(good):
        return None
    acceleration = 2.0 * (
        (positions_m[2:] - positions_m[1:-1]) / dt1[:, None]
        - (positions_m[1:-1] - positions_m[:-2]) / dt0[:, None]
    ) / (dt0 + dt1)[:, None]
    acceleration = acceleration[good]
    median = np.median(acceleration, axis=0)
    return 1.4826 * np.median(np.abs(acceleration - median), axis=0)


def aggregate_q(blocks: Sequence[np.ndarray]) -> np.ndarray:
    valid = np.asarray([x for x in blocks if x is not None and np.all(np.isfinite(x)) and np.all(x > 0)])
    if len(valid) < 10:
        raise ValueError(f"q_accel has {len(valid)} valid episode blocks, requires 10")
    return np.median(valid, axis=0)


def t4_episode(rows: Sequence[UwbRow], *, node: str, held: int | None,
               clock: ClockModel, frontend: CanonicalT4Frontend) -> list[T4Observation | None]:
    output: list[T4Observation | None] = []
    mask_clear = 0 if held is None else 1 << held
    for row in rows:
        if row.node != node:
            continue
        global_ns = int(round(clock.a_ns_per_us * row.strobe_us + clock.b_ns))
        output.append(frontend.solve(
            node_id=row.node, sweep=row.sweep, global_time_ns=global_ns,
            global_time_sigma_ns=int(round(clock.sigma_ns)),
            anchor_ids=row.anchor_ids, ranges_mm=row.ranges_mm,
            quality=row.quality, valid_mask=row.valid_mask & ~mask_clear,
            t_round_us=row.t_round_us,
        ))
    return output


def q_from_t4(episode_rows: Mapping[str, Sequence[UwbRow]], *, node: str,
              held: int | None, clock: ClockModel, layout_path: Path) -> tuple[np.ndarray, dict[str, list[T4Observation | None]]]:
    blocks: list[np.ndarray] = []
    trajectories: dict[str, list[T4Observation | None]] = {}
    frontend = CanonicalT4Frontend(layout_path)
    for episode in CALIBRATION_ORDER:
        all_rows = episode_rows[episode]
        node_rows = [row for row in all_rows if row.node == node]
        observations = t4_episode(node_rows, node=node, held=held, clock=clock, frontend=frontend)
        trajectories[episode] = observations
        valid = [item for item in observations if item is not None and item.acceptability == "ACCEPTED"
                 and np.all(np.isfinite(item.xyz_m))]
        if len(valid) >= 3:
            blocks.append(acceleration_mad(
                np.asarray([item.effective_time_ns for item in valid], float) * 1e-9,
                np.asarray([item.xyz_m for item in valid], float),
            ))
    return aggregate_q(blocks), trajectories


def held_link_task(
    episode_rows: Mapping[str, Sequence[UwbRow]], *, node: str, held: int,
    clock: ClockModel, layout_path: Path, anchors_m: np.ndarray,
) -> dict:
    q_accel, t4_by_episode = q_from_t4(
        episode_rows, node=node, held=held, clock=clock, layout_path=layout_path)
    episode_dt = []
    for episode in CALIBRATION_ORDER:
        times = []
        for row in episode_rows[episode]:
            if row.node != node:
                continue
            valid_epochs = [clock.seconds(row.strobe_us + 0.5 * row.t_round_us[s])
                            for s in range(8) if row.valid_mask & (1 << s)
                            and 0 < row.ranges_mm[s] < 0xffff
                            and math.isfinite(float(row.t_round_us[s]))]
            if valid_epochs:
                times.append(float(np.median(valid_epochs)))
        positive = [x for x in np.diff(times) if math.isfinite(x) and x > 0]
        if positive:
            episode_dt.append(float(np.median(positive)))
    if len(episode_dt) < 10:
        raise ValueError("median calibration dt has fewer than ten episode owners")
    median_dt = float(np.median(episode_dt))
    episode_new: dict[str, list[float]] = defaultdict(list)
    episode_t4: dict[str, list[float]] = defaultdict(list)
    event_ids: dict[str, list[list]] = defaultdict(list)
    counts = defaultdict(int)
    for episode in CALIBRATION_ORDER:
        all_rows = episode_rows[episode]
        rows = [row for row in all_rows if row.node == node]
        t4_rows = t4_by_episode[episode]
        state = covariance = None
        previous_time = None
        previous_t4: T4Observation | None = None
        previous_t4_velocity = np.zeros(3)
        for row, t4 in zip(rows, t4_rows):
            row_fit = dataclasses.replace(row, valid_mask=row.valid_mask & ~(1 << held))
            available_slots = [
                slot for slot in range(8)
                if row_fit.valid_mask & (1 << slot)
                and 0 < row.ranges_mm[slot] < 0xffff
                and math.isfinite(float(row.t_round_us[slot]))
            ]
            if len(available_slots) < 4:
                counts["skipped_fewer_than_four_links"] += 1
                continue
            reference_time = float(np.median([
                clock.seconds(row.strobe_us + 0.5 * row.t_round_us[slot])
                for slot in available_slots
            ]))
            if state is None:
                if (t4 is not None and t4.acceptability == "ACCEPTED" and held not in t4.anchors_used
                        and np.all(np.isfinite(t4.xyz_m)) and np.all(np.isfinite(t4.covariance_m2))):
                    state = np.r_[t4.xyz_m, np.zeros(3)]
                    covariance = np.zeros((6, 6))
                    covariance[:3, :3] = t4.covariance_m2
                    covariance[3:, 3:] = np.diag(q_accel ** 2) * median_dt ** 2
                else:
                    available = [row.anchor_ids[slot] for slot in available_slots]
                    directions = anchors_m[available] - np.mean(anchors_m[available], axis=0)
                    if len(available) < 4 or np.linalg.matrix_rank(directions) < 3:
                        raise ValueError("held fallback anchor subset is rank deficient")
                    state = np.r_[np.mean(anchors_m[available], axis=0), np.zeros(3)]
                    covariance = np.zeros((6, 6))
                    span = np.ptp(anchors_m[available], axis=0)
                    covariance[:3, :3] = np.diag(span ** 2)
                    covariance[3:, 3:] = np.eye(3) * (np.linalg.norm(span) / median_dt) ** 2
            elif reference_time > previous_time:
                state, covariance = predict_state(state, covariance, reference_time - previous_time, q_accel)
            else:
                raise ValueError("nonpositive calibration U0 interval")
            solved = solve_u0_row(
                row_fit, anchors_m=anchors_m, clock=clock, predicted_state=state,
                predicted_covariance=covariance, bias_m={}, sigma_history_m={}, sigma_bias_m={},
                calibration_zero_uncertainty=True)
            previous_time = reference_time
            if solved.success:
                state, covariance = solved.state, solved.covariance
            t4_point = None
            if t4 is not None and t4.acceptability == "ACCEPTED" and held not in t4.anchors_used:
                t4_time = t4.effective_time_ns * 1e-9
                if previous_t4 is None:
                    velocity = np.zeros(3)
                else:
                    interval = t4_time - previous_t4.effective_time_ns * 1e-9
                    if interval <= 0:
                        previous_t4 = t4
                        continue
                    velocity = (t4.xyz_m - previous_t4.xyz_m) / interval
                previous_t4, previous_t4_velocity = t4, velocity
                t4_point = (t4.xyz_m, velocity, t4_time)
            if not held_measurement_valid(row, held) or not solved.success or t4_point is None:
                continue
            held_time = clock.seconds(row.strobe_us + 0.5 * row.t_round_us[held])
            held_range = row.ranges_mm[held] / 1000.0
            t4_position, t4_velocity, t4_time = t4_point
            point = t4_position + t4_velocity * (held_time - t4_time)
            t4_residual = held_range - float(np.linalg.norm(anchors_m[held] - point))
            new_point = solved.state[:3] + (held_time - solved.reference_epoch_s) * solved.state[3:]
            episode_new[episode].append(held_range - float(np.linalg.norm(anchors_m[held] - new_point)))
            episode_t4[episode].append(t4_residual)
            event_ids[episode].append([episode, node, row.boot, row.sweep, held])
            counts["eligible"] += 1
    medians = {episode: float(np.median(values)) for episode, values in episode_new.items() if values}
    scales = {episode: float(1.4826 * np.median(np.abs(np.asarray(values) - np.median(values))))
              for episode, values in episode_new.items() if values}
    return {
        "node": node, "anchor": held, "q_accel": q_accel.tolist(), "median_dt_s": median_dt,
        "episode_new": {key: value for key, value in episode_new.items()},
        "episode_t4": {key: value for key, value in episode_t4.items()},
        "event_ids": {key: value for key, value in event_ids.items()},
        "episode_medians": medians, "episode_scales": scales, "counts": dict(counts),
    }


def bias_from_held(result: Mapping, rng: np.random.Generator) -> dict:
    medians = np.asarray(list(result["episode_medians"].values()), float)
    scales = np.asarray(list(result["episode_scales"].values()), float)
    sigma_history = float(np.median(scales)) if len(scales) else math.nan
    if (len(medians) < 10 or not np.all(np.isfinite(medians))
            or not math.isfinite(sigma_history) or sigma_history <= 0):
        return {"available": False, "reason": "INSUFFICIENT_EPISODE_BLOCKS"}
    bootstrap = np.median(medians[rng.integers(0, len(medians), size=(2000, len(medians)))], axis=1)
    q025, q975 = np.quantile(bootstrap, [0.025, 0.975], method="linear")
    mad = 1.4826 * np.median(np.abs(medians - np.median(medians)))
    sigma_bias = max(float(mad), float((q975 - q025) / (2 * 1.96)))
    all_residuals = np.concatenate([np.asarray(x, float) for x in result["episode_new"].values()])
    return {
        "available": True, "bias_m": max(0.0, float(np.median(all_residuals))) if q025 > 0 else 0.0,
        "sigma_bias_m": sigma_bias, "sigma_history_m": sigma_history,
        "bootstrap_q025_m": float(q025), "bootstrap_q975_m": float(q975),
        "positive_bias_enabled": bool(q025 > 0), "episode_blocks": int(len(medians)),
        "design_rank": 1,
    }
