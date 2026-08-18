#!/usr/bin/env python3
from __future__ import annotations

import os

for variable in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "1"

import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
from pathlib import Path
import resource
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

import numpy as np

from biospur_fusion.imu_pose_v2.calibration import fit_joint_calibration
from biospur_fusion.imu_pose_v2.estimator import ContinuousArticulatedEstimator
from biospur_fusion.imu_pose_v2.frontend import ContinuousNodeFrontend
from biospur_fusion.imu_pose_v2.synthetic import frontend_frame, synthetic_calibration_rows, synthetic_imu_stream


MAPPING = {
    "BSFEC35": "forearm_left", "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left", "BSF1120": "upper_arm_right",
    "BSF31CC": "torso", "BSFC2CC": "pelvis", "BSF44AD": "thigh_left",
    "BSF3C79": "thigh_right", "BSF6C53": "shank_left", "BSF8BC4": "shank_right",
}
ACTIONS = (
    "00_initial_still", "02_t_pose", "03_pelvis_hula_circle", "04_shoulder_left",
    "05_shoulder_right", "06_elbow_left", "07_elbow_right", "08_hip_left",
    "09_hip_right", "10_knee_left_seated", "11_knee_right_seated",
    "12_heel_raise_left", "13_heel_raise_right", "14_trunk_flex_extend",
    "15_trunk_axial_rotation", "16_squat", "18_heel_to_butt_left", "19_heel_to_butt_right",
)
MASTER_SEED = "biospur-phase3r2-20260818-v1"


def task_seed(task_id: str) -> int:
    return int.from_bytes(hashlib.sha256(f"{MASTER_SEED}|{task_id}".encode()).digest()[:8], "little")


def _task(task_index: int) -> dict:
    cpu_start = time.process_time()
    task_id = f"fit-synthetic-{task_index:03d}"
    seed = task_seed(task_id); rng = np.random.default_rng(seed)
    node = sorted(MAPPING)[task_index % len(MAPPING)]
    gyro = np.array([.025, -.015, .02]) + rng.normal(0, .001, 3)
    frontend = ContinuousNodeFrontend(node)
    for row in synthetic_imu_stream(node, samples=350, gyro=gyro):
        frontend.update(row, sample_age_us=(500, 1000, 2000, 5000)[task_index % 4])
    bundle = fit_joint_calibration(synthetic_calibration_rows(MAPPING, ACTIONS), MAPPING, ACTIONS)
    estimator = ContinuousArticulatedEstimator(bundle)
    pose_bytes = bytearray(frontend.serialize())
    for tick_index in range(8):
        scheduled = 40_000_000_000 + tick_index * 20_000_000
        frames = {
            n: frontend_frame(n, index, scheduled, yaw_rad=(task_index + 1) * (index + 1) * tick_index * 1e-5)
            for index, n in enumerate(sorted(MAPPING))
        }
        pose = estimator.update(scheduled, frames)
        for segment in sorted(pose.segment_quaternions_W_S):
            pose_bytes.extend(np.ascontiguousarray(pose.segment_quaternions_W_S[segment], dtype="<f8").tobytes())
        pose_bytes.extend(np.ascontiguousarray(pose.segment_covariance_rad2, dtype="<f8").tobytes())
    return {
        "task_id": task_id,
        "seed": seed,
        "core_sha256": hashlib.sha256(pose_bytes).hexdigest(),
        "peak_rss_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "worker_cpu_seconds": time.process_time() - cpu_start,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, choices=(1, 4, 6), required=True)
    parser.add_argument("--tasks", type=int, default=24)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    wall_start = time.perf_counter(); cpu_start = time.process_time()
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        rows = list(pool.map(_task, range(args.tasks), chunksize=1))
    rows.sort(key=lambda row: row["task_id"])
    core = hashlib.sha256("".join(row["core_sha256"] for row in rows).encode()).hexdigest()
    wall_seconds = time.perf_counter() - wall_start
    worker_cpu_seconds = sum(row["worker_cpu_seconds"] for row in rows)
    payload = {
        "schema": "biospur-phase3r2-cpu-benchmark-v1",
        "workers": args.workers, "task_count": args.tasks,
        "master_seed": MASTER_SEED,
        "task_seed_derivation": "SHA256(master_seed|stable_task_id) first uint64 little-endian",
        "core_output_sha256": core,
        "task_core_sha256": {row["task_id"]: row["core_sha256"] for row in rows},
        "wall_seconds": wall_seconds,
        "parent_cpu_seconds": time.process_time() - cpu_start,
        "worker_cpu_seconds": worker_cpu_seconds,
        "aggregate_cpu_utilization_percent": 100.0 * worker_cpu_seconds / max(wall_seconds, 1e-12),
        "peak_worker_rss_kib": max(row["peak_rss_kib"] for row in rows),
        "blas_thread_environment": {name: os.environ[name] for name in (
            "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS")},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(temp, args.output)
    print(json.dumps({key: payload[key] for key in ("workers", "wall_seconds", "core_output_sha256")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
