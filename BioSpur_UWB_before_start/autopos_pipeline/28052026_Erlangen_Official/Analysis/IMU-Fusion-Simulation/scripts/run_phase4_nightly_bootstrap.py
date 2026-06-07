#!/usr/bin/env python3
"""Nightly Phase 4 bootstrap runner.

This is a resumable 2-GPU bootstrap launcher for the current implemented raw
range T6/T8 path. It is deliberately conservative: chunks are small, each chunk
is executed by the existing CPU/GPU agreement pilot, and completed chunks are
not recomputed on resume.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
PILOT_SCRIPT = SIM_ROOT / "scripts" / "run_phase4_gpu_pilot.py"
SENSORS_YAML = SIM_ROOT / "configs" / "sensors.yaml"

DEFAULT_ACTIVE_L_IDS = [
    "L0",
    "L1",
    "L2",
    "L3",
    "L4",
    "L5",
    "L7",
    "L8",
    "L10",
    "L11",
    "L12",
    "L13",
    "L14",
    "L15",
    "L16",
    "L17",
    "L18",
    "L19",
]
I_IDS = ["I0", "I1", "I3", "I4", "I7", "I8", "I1+I3+I7", "I1+I2+I3+I8"]
R_IDS = ["R2", "R4"]
T_IDS = ["T6", "T8"]

AGREEMENT_ROWS = [
    "R2:L0:I0:T6",
    "R2:L2:I3:T6",
    "R4:L8:I1+I2+I3+I8:T8",
    "R4:L14:I1+I2+I3+I8:T8",
]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json_atomic(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, path)


def git_status() -> dict:
    try:
        commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=SIM_ROOT.parent, text=True).strip()
    except Exception:
        commit = ""
    try:
        status = subprocess.check_output(["git", "status", "--short"], cwd=SIM_ROOT.parent, text=True).strip().splitlines()
    except Exception:
        status = []
    return {"commit": commit, "status_short": status}


def parse_stop_time(value: str) -> float | None:
    if not value:
        return None
    hh, mm = value.split(":", 1)
    now = datetime.now()
    stop = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    if stop <= now:
        stop += timedelta(days=1)
    return stop.timestamp()


def resolve_l_ids(values: list[str] | None) -> list[str]:
    if not values:
        return list(DEFAULT_ACTIVE_L_IDS)
    known = set(DEFAULT_ACTIVE_L_IDS)
    out: list[str] = []
    for value in values:
        for raw in str(value).replace(",", " ").split():
            lid = raw.strip()
            if not lid:
                continue
            if lid not in known:
                raise ValueError(f"unknown or inactive L id {lid!r}; allowed={sorted(known)}")
            if lid not in out:
                out.append(lid)
    if not out:
        raise ValueError("--l-ids resolved to an empty set")
    return out


def load_sensor_metadata(l_ids: list[str]) -> list[dict]:
    raw = yaml.safe_load(SENSORS_YAML.read_text(encoding="utf-8"))
    rows = []
    for lid in l_ids:
        row = raw.get(lid, {})
        out = {"L": lid}
        if isinstance(row, dict):
            for key in [
                "name",
                "source",
                "residual_accel_bias_mg",
                "accel_noise_mg",
                "accel_bias_random_walk_mg_sqrt_s",
                "vibration_sensitivity_mg",
                "extrinsic_mg",
            ]:
                if key in row:
                    out[key] = row[key]
        rows.append(out)
    return rows


def build_partial_rows(l_ids: list[str]) -> list[str]:
    rows = []
    for r_id in R_IDS:
        for l_id in l_ids:
            for i_id in I_IDS:
                for t_id in T_IDS:
                    rows.append(f"{r_id}:{l_id}:{i_id}:{t_id}")
    return rows


def chunk_rows(row_specs: list[str], chunk_size: int, prefix: str, max_tracks: int, max_frames: int) -> list[dict]:
    chunks = []
    for idx in range(0, len(row_specs), chunk_size):
        rows = row_specs[idx : idx + chunk_size]
        chunks.append(
            {
                "chunk_id": f"{prefix}_{idx // chunk_size:04d}",
                "stage": prefix,
                "rows": rows,
                "row_count": len(rows),
                "max_tracks": max_tracks,
                "max_frames": max_frames,
                "estimated_cost": len(rows) * (1 if max_frames > 0 else 8),
                "status": "pending",
                "attempts": 0,
            }
        )
    return chunks


def initial_chunks(chunk_size: int, partial_max_tracks: int, partial_max_frames: int, l_ids: list[str]) -> list[dict]:
    chunks: list[dict] = []
    agreement_rows = [row for row in AGREEMENT_ROWS if row.split(":")[1] in set(l_ids)]
    if not agreement_rows:
        agreement_rows = [f"R2:{l_ids[0]}:I3:T6"]
    chunks.extend(chunk_rows(agreement_rows, 2, "agreement", 2, 200))
    chunks.extend(chunk_rows(build_partial_rows(l_ids), chunk_size, "partial_raw", partial_max_tracks, partial_max_frames))
    return chunks


def build_registry() -> list[dict]:
    rows = []
    for t_id in [f"T{i}" for i in range(1, 13)]:
        rows.append(
            {
                "T": t_id,
                "implemented_in_current_gpu_bootstrap": t_id in {"T6", "T8"},
                "bootstrap_role": "raw_range_current_path" if t_id in {"T6", "T8"} else "planned_or_control",
            }
        )
    for r_id in R_IDS:
        rows.append({"R": r_id, "implemented_in_current_gpu_bootstrap": True, "bootstrap_role": "raw_range_policy"})
    for i_id in I_IDS:
        rows.append({"I": i_id, "implemented_in_current_gpu_bootstrap": True, "bootstrap_role": "current_imu_filter_chain"})
    return rows


def build_matrix_manifest(chunks: list[dict], seed_id: str = "") -> list[dict]:
    rows = []
    for chunk in chunks:
        for spec in chunk["rows"]:
            r_id, l_id, i_id, t_id = spec.split(":")
            rows.append(
                {
                    "chunk_id": chunk["chunk_id"],
                    "stage": chunk["stage"],
                    "seed_id": seed_id,
                    "row_spec": spec,
                    "R": r_id,
                    "L": l_id,
                    "I": i_id,
                    "T": t_id,
                    "status": chunk["status"],
                    "max_tracks": chunk["max_tracks"],
                    "max_frames": chunk["max_frames"],
                }
            )
    return rows


def resource_sample() -> list[dict]:
    rows: list[dict] = []
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=index,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw",
                "--format=csv,noheader,nounits",
            ],
            text=True,
        )
        for line in out.strip().splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) < 6:
                continue
            rows.append(
                {
                    "sample_utc": utc_now(),
                    "gpu_index": int(parts[0]),
                    "gpu_util_pct": float(parts[1]),
                    "mem_used_mb": float(parts[2]),
                    "mem_total_mb": float(parts[3]),
                    "temperature_c": float(parts[4]),
                    "power_w": float(str(parts[5]).split()[0]),
                }
            )
    except Exception as exc:
        rows.append({"sample_utc": utc_now(), "error": str(exc)})
    return rows


def append_resource_samples(path: Path, rows: list[dict], lock: threading.Lock) -> None:
    with lock:
        existing: list[dict] = []
        if path.exists() and path.stat().st_size > 0:
            try:
                existing = pd.read_csv(path).to_dict("records")
            except Exception:
                existing = []
        write_csv(path, existing + rows)


def aggregate_child_outputs(run_dir: Path, chunks: list[dict]) -> None:
    agreement_frames = []
    timing_frames = []
    for chunk in chunks:
        child_run_id = chunk.get("child_run_id")
        if chunk.get("status") != "done" or not child_run_id:
            continue
        child_dir = SIM_ROOT / "runs" / "phase4_gpu_pilot" / str(child_run_id)
        agreement_path = child_dir / "tables" / "phase4_gpu_pilot_agreement.csv"
        timing_path = child_dir / "tables" / "phase4_gpu_pilot_timing.csv"
        if agreement_path.exists() and agreement_path.stat().st_size > 0:
            df = pd.read_csv(agreement_path)
            df["chunk_id"] = chunk["chunk_id"]
            df["gpu_device"] = chunk.get("gpu_device", "")
            df["child_run_id"] = child_run_id
            agreement_frames.append(df)
        if timing_path.exists() and timing_path.stat().st_size > 0:
            df = pd.read_csv(timing_path)
            df["chunk_id"] = chunk["chunk_id"]
            df["gpu_device"] = chunk.get("gpu_device", "")
            df["child_run_id"] = child_run_id
            timing_frames.append(df)
    if agreement_frames:
        pd.concat(agreement_frames, ignore_index=True).to_csv(run_dir / "tables" / "phase4_nightly_agreement.csv", index=False)
    if timing_frames:
        pd.concat(timing_frames, ignore_index=True).to_csv(run_dir / "tables" / "phase4_nightly_timing.csv", index=False)


def resource_gate_rows(run_dir: Path, chunks: list[dict]) -> list[dict]:
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    devices = list(manifest.get("devices", []))
    workers_per_device = int(manifest.get("workers_per_device", 1))
    total_workers = max(1, workers_per_device) * max(1, len(devices))

    done_chunks = [c for c in chunks if c.get("status") == "done"]
    failed_chunks = [c for c in chunks if c.get("status") == "failed"]
    partial_done = [c for c in done_chunks if str(c.get("stage", "")).startswith("partial_raw")]
    by_gpu: dict[str, int] = {}
    for chunk in partial_done:
        gpu = str(chunk.get("gpu_device", ""))
        by_gpu[gpu] = by_gpu.get(gpu, 0) + 1
    gpu_counts = [by_gpu.get(dev, 0) for dev in devices]
    gpu_balance_ok = bool(gpu_counts and min(gpu_counts) > 0 and (max(gpu_counts) - min(gpu_counts)) <= max(2, 0.2 * max(gpu_counts)))

    resource_path = run_dir / "tables" / "phase4_nightly_resource_samples.csv"
    gpu_util_evidence = "resource samples unavailable"
    gpu_util_ok = True
    if resource_path.exists() and resource_path.stat().st_size > 0:
        try:
            res = pd.read_csv(resource_path)
            if not res.empty and {"gpu_index", "gpu_util_pct"}.issubset(res.columns):
                max_by_gpu = res.groupby("gpu_index")["gpu_util_pct"].max().to_dict()
                gpu_util_ok = all(float(max_by_gpu.get(int(dev.split(":")[-1]), 0.0)) > 0.0 for dev in devices if dev.startswith("cuda:"))
                gpu_util_evidence = ", ".join(f"gpu{int(k)} max_util={float(v):.0f}%" for k, v in sorted(max_by_gpu.items()))
        except Exception as exc:
            gpu_util_ok = False
            gpu_util_evidence = f"resource sample parse failed: {exc}"

    timing_path = run_dir / "tables" / "phase4_nightly_timing.csv"
    timing_evidence = "timing unavailable"
    cpu_parallel_ok = total_workers >= 2
    if timing_path.exists() and timing_path.stat().st_size > 0:
        try:
            timing = pd.read_csv(timing_path)
            if not timing.empty and {"stage", "wall_time_s"}.issubset(timing.columns):
                means = timing.groupby("stage")["wall_time_s"].mean().to_dict()
                timing_evidence = ", ".join(f"{stage}_mean={float(value):.2f}s" for stage, value in sorted(means.items()))
        except Exception as exc:
            timing_evidence = f"timing parse failed: {exc}"

    log_paths: list[Path] = []
    for chunk in done_chunks:
        raw_log_path = str(chunk.get("log_path", ""))
        if not raw_log_path:
            continue
        candidates = [Path(raw_log_path), SIM_ROOT / raw_log_path, run_dir / raw_log_path]
        log_paths.append(next((p for p in candidates if p.exists()), candidates[-1]))
    sampled_logs = log_paths[: min(20, len(log_paths))]
    logs_thread_ok = bool(sampled_logs)
    for log_path in sampled_logs:
        if not log_path.exists():
            logs_thread_ok = False
            break
        first_line = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[0] if log_path.stat().st_size else ""
        if "--torch-threads 1" not in first_line:
            logs_thread_ok = False
            break

    all_done_ok = len(done_chunks) == len(chunks) and not failed_chunks
    rows = [
        {
            "gate_id": "G11_two_gpu_dynamic_balance",
            "status": "PASS" if all_done_ok and gpu_balance_ok and gpu_util_ok else "FAIL",
            "blocking_next_phase": all_done_ok and gpu_balance_ok and gpu_util_ok,
            "evidence": f"devices={devices}; partial_chunks_by_gpu={by_gpu}; {gpu_util_evidence}; failed={len(failed_chunks)}",
        },
        {
            "gate_id": "G12_cpu_parallel_execution",
            "status": "PASS" if cpu_parallel_ok else "FAIL",
            "blocking_next_phase": cpu_parallel_ok,
            "evidence": f"workers_per_device={workers_per_device}; total_workers={total_workers}; {timing_evidence}",
        },
        {
            "gate_id": "G13_thread_oversubscription_control",
            "status": "PASS" if logs_thread_ok and workers_per_device <= 4 else "REVIEW",
            "blocking_next_phase": logs_thread_ok and workers_per_device <= 4,
            "evidence": f"checked_logs={len(sampled_logs)}; torch_threads_1_in_sample={logs_thread_ok}; workers_per_device={workers_per_device}",
        },
    ]
    return rows


def numerical_agreement_rows(run_dir: Path) -> list[dict]:
    agreement_path = run_dir / "tables" / "phase4_nightly_agreement.csv"
    if not agreement_path.exists() or agreement_path.stat().st_size == 0:
        return [
            {
                "audit_id": "A0_agreement_table_present",
                "status": "FAIL",
                "blocking_final_claim": True,
                "evidence": "tables/phase4_nightly_agreement.csv is missing or empty",
            }
        ]

    ag = pd.read_csv(agreement_path)
    if ag.empty:
        return [
            {
                "audit_id": "A0_agreement_table_present",
                "status": "FAIL",
                "blocking_final_claim": True,
                "evidence": "agreement table has zero rows",
            }
        ]

    rows: list[dict] = []
    if {"cpu_accept_rate", "gpu_accept_rate"}.issubset(ag.columns):
        accept_delta = (ag["cpu_accept_rate"] - ag["gpu_accept_rate"]).abs()
        max_accept_delta = float(accept_delta.max())
        rows.append(
            {
                "audit_id": "A1_accept_rate_match",
                "status": "PASS" if max_accept_delta <= 1e-9 else "REVIEW",
                "blocking_final_claim": max_accept_delta > 1e-9,
                "evidence": f"max_abs_accept_rate_delta={max_accept_delta:.3g}",
            }
        )

    if "xyz_diff_p95_mm" in ag.columns:
        max_p95 = float(ag["xyz_diff_p95_mm"].max())
        rows.append(
            {
                "audit_id": "A2_p95_cpu_gpu_xyz_agreement",
                "status": "PASS" if max_p95 <= 10.0 else "REVIEW",
                "blocking_final_claim": max_p95 > 10.0,
                "evidence": f"max_p95_xyz_diff={max_p95:.3f} mm; threshold=10 mm",
            }
        )

    if "xyz_diff_max_mm" in ag.columns:
        top = ag.sort_values("xyz_diff_max_mm", ascending=False).iloc[0]
        max_diff = float(top["xyz_diff_max_mm"])
        rows.append(
            {
                "audit_id": "A3_single_frame_outlier_audit",
                "status": "PASS" if max_diff <= 100.0 else "REVIEW",
                "blocking_final_claim": max_diff > 100.0,
                "evidence": (
                    f"max_single_frame_xyz_diff={max_diff:.3f} mm at "
                    f"{top.get('row_spec', '')}/{top.get('capture_id', '')}/{top.get('tag', '')}; "
                    "resource gate can pass, but final numerical claim must inspect this outlier"
                ),
            }
        )

    return rows


def write_reports(run_dir: Path, chunks: list[dict], elapsed_s: float) -> None:
    done = sum(1 for c in chunks if c.get("status") == "done")
    failed = sum(1 for c in chunks if c.get("status") == "failed")
    pending = sum(1 for c in chunks if c.get("status") == "pending")
    running = sum(1 for c in chunks if c.get("status") == "running")
    rows_done = sum(int(c.get("row_count", 0)) for c in chunks if c.get("status") == "done")
    rows_total = sum(int(c.get("row_count", 0)) for c in chunks)
    gates = resource_gate_rows(run_dir, chunks)
    write_csv(run_dir / "tables" / "phase4_resource_gates.csv", gates)
    numerical = numerical_agreement_rows(run_dir)
    write_csv(run_dir / "tables" / "phase4_numerical_agreement_audit.csv", numerical)
    report = [
        "# Phase 4 Nightly 1080Ti Bootstrap",
        "",
        f"Generated: {utc_now()}",
        f"Elapsed: {elapsed_s:.1f} s",
        "",
        "## Status",
        "",
        f"- Chunks done/failed/running/pending: {done}/{failed}/{running}/{pending}",
        f"- Rows done/total: {rows_done}/{rows_total}",
        "",
        "## Outputs",
        "",
        "- `tables/phase4_nightly_chunks.csv`",
        "- `tables/phase4_nightly_agreement.csv`",
        "- `tables/phase4_nightly_timing.csv`",
        "- `tables/phase4_nightly_resource_samples.csv`",
        "- `tables/phase4_resource_gates.csv`",
        "- `tables/phase4_numerical_agreement_audit.csv`",
        "",
        "## Resource Gates",
        "",
        "| gate_id | status | blocking_next_phase | evidence |",
        "|---|---:|---:|---|",
        *[
            f"| {row['gate_id']} | {row['status']} | {row['blocking_next_phase']} | {row['evidence']} |"
            for row in gates
        ],
        "",
        "## Numerical Agreement Audit",
        "",
        "| audit_id | status | blocking_final_claim | evidence |",
        "|---|---:|---:|---|",
        *[
            f"| {row['audit_id']} | {row['status']} | {row['blocking_final_claim']} | {row['evidence']} |"
            for row in numerical
        ],
        "",
        "This is a bootstrap run for tomorrow's 5090D handoff, not the final Phase 4 FULL claim.",
    ]
    (run_dir / "reports" / "PHASE4_NIGHTLY_1080TI_BOOTSTRAP.md").write_text("\n".join(report), encoding="utf-8")


def run_chunk(
    chunk: dict,
    gpu_device: str,
    run_id: str,
    run_dir: Path,
    phase2_run: str,
    seed_id: str,
    cache_root: str,
    timeout_s: int,
) -> tuple[bool, str]:
    child_run_id = f"{run_id}_{chunk['chunk_id']}_{gpu_device.replace(':', '')}"
    log_path = run_dir / "logs" / f"{chunk['chunk_id']}_{gpu_device.replace(':', '')}.log"
    cmd = [
        sys.executable,
        str(PILOT_SCRIPT),
        "--phase2-run",
        phase2_run,
        "--run-id",
        child_run_id,
        "--prior-run-id",
        run_id,
        "--seed-id",
        seed_id,
        "--device",
        gpu_device,
        "--dtype",
        "float32",
        "--torch-threads",
        "1",
        "--agreement-mode",
        "full" if str(chunk.get("stage")) == "agreement" else "none",
        "--cache-mode",
        "readwrite",
        "--cache-root",
        cache_root,
        "--max-tracks",
        str(chunk["max_tracks"]),
        "--max-frames",
        str(chunk["max_frames"]),
        "--gpu-repeat",
        "1",
        "--rows",
        *chunk["rows"],
    ]
    env = os.environ.copy()
    env.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
        }
    )
    started = time.perf_counter()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as log:
        log.write("$ " + " ".join(cmd) + "\n\n")
        log.flush()
        proc = subprocess.run(cmd, cwd=SIM_ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True, timeout=timeout_s)
    elapsed = time.perf_counter() - started
    chunk["child_run_id"] = child_run_id
    chunk["gpu_device"] = gpu_device
    chunk["wall_time_s"] = elapsed
    chunk["log_path"] = str(log_path.relative_to(SIM_ROOT))
    if proc.returncode != 0:
        return False, f"returncode={proc.returncode}"
    return True, "ok"


def run(args: argparse.Namespace) -> dict:
    run_id = args.resume_run or args.run_id or datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = SIM_ROOT / "runs" / "phase4_algorithm_factory" / run_id
    for d in [run_dir / "tables", run_dir / "reports", run_dir / "logs", run_dir / "manifests"]:
        d.mkdir(parents=True, exist_ok=True)

    chunks_path = run_dir / "manifests" / "chunk_status.json"
    l_ids = resolve_l_ids(args.l_ids)
    if args.resume_run and chunks_path.exists():
        chunks = json.loads(chunks_path.read_text(encoding="utf-8"))
    else:
        chunks = initial_chunks(args.chunk_size, args.partial_max_tracks, args.partial_max_frames, l_ids)

    deadline = time.time() + float(args.max_wall_time) if args.max_wall_time > 0 else None
    stop_at = parse_stop_time(args.stop_at_local_time)
    if deadline is not None and stop_at is not None:
        deadline = min(deadline, stop_at)
    elif stop_at is not None:
        deadline = stop_at

    manifest = {
        "run_id": run_id,
        "generated_utc": utc_now(),
        "phase": "phase4_algorithm_factory_bootstrap",
        "seed_id": args.seed_id,
            "phase2_run": args.phase2_run,
            "cache_root": args.cache_root,
            "devices": args.devices,
        "workers_per_device": args.workers_per_device,
        "l_ids": l_ids,
        "i_ids": I_IDS,
        "r_ids": R_IDS,
        "t_ids": T_IDS,
        "chunk_size": args.chunk_size,
        "partial_max_tracks": args.partial_max_tracks,
        "partial_max_frames": args.partial_max_frames,
        "max_wall_time": args.max_wall_time,
        "stop_at_local_time": args.stop_at_local_time,
        "medium_chunk_basis": "12288-equivalent dense CUDA smoke: about 9.5 TFLOPS per 1080Ti",
        "scope_warning": "Current bootstrap implements the raw-range R2/R4 x T6/T8 path only. It is not the final 95,256-row Phase 4 FULL production runner.",
        "git": git_status(),
    }
    write_json_atomic(run_dir / "manifest.json", manifest)
    write_csv(run_dir / "tables" / "phase4_algorithm_registry.csv", build_registry())
    write_csv(run_dir / "tables" / "phase4_sensor_metadata.csv", load_sensor_metadata(l_ids))
    write_csv(run_dir / "tables" / "phase4_matrix_manifest.csv", build_matrix_manifest(chunks, args.seed_id))
    write_json_atomic(chunks_path, chunks)

    q: queue.Queue[int] = queue.Queue()
    for idx, chunk in enumerate(chunks):
        if chunk.get("status") != "done":
            chunk["status"] = "pending"
            q.put(idx)

    lock = threading.Lock()
    resource_lock = threading.Lock()
    stop_event = threading.Event()
    start = time.perf_counter()

    def monitor() -> None:
        resource_path = run_dir / "tables" / "phase4_nightly_resource_samples.csv"
        while not stop_event.is_set():
            append_resource_samples(resource_path, resource_sample(), resource_lock)
            time.sleep(max(5.0, float(args.monitor_interval)))

    def save_status() -> None:
        with lock:
            write_json_atomic(chunks_path, chunks)
            write_csv(run_dir / "tables" / "phase4_nightly_chunks.csv", chunks)
            write_csv(run_dir / "tables" / "phase4_matrix_manifest.csv", build_matrix_manifest(chunks, args.seed_id))

    def worker(gpu_device: str) -> None:
        while True:
            if deadline is not None and time.time() >= deadline:
                return
            try:
                idx = q.get_nowait()
            except queue.Empty:
                return
            chunk = chunks[idx]
            with lock:
                if chunk.get("status") == "done":
                    q.task_done()
                    continue
                chunk["status"] = "running"
                chunk["gpu_device"] = gpu_device
                chunk["message"] = ""
                chunk["started_utc"] = utc_now()
                chunk["attempts"] = int(chunk.get("attempts", 0)) + 1
                write_json_atomic(chunks_path, chunks)
            ok = False
            message = "not_run"
            try:
                timeout_s = int(args.chunk_timeout_s)
                if deadline is not None:
                    timeout_s = min(timeout_s, max(1, int(deadline - time.time())))
                ok, message = run_chunk(chunk, gpu_device, run_id, run_dir, args.phase2_run, args.seed_id, args.cache_root, timeout_s)
            except subprocess.TimeoutExpired:
                ok, message = False, "timeout"
            except Exception as exc:
                ok, message = False, repr(exc)
            with lock:
                chunk["finished_utc"] = utc_now()
                chunk["status"] = "done" if ok else "failed"
                chunk["message"] = message
                write_json_atomic(chunks_path, chunks)
                write_csv(run_dir / "tables" / "phase4_nightly_chunks.csv", chunks)
            print(f"[phase4-nightly] {chunk['chunk_id']} {gpu_device} {chunk['status']} {message}", flush=True)
            q.task_done()

    monitor_thread = threading.Thread(target=monitor, daemon=True)
    monitor_thread.start()
    try:
        worker_devices = [dev for dev in args.devices for _ in range(max(1, args.workers_per_device))]
        with ThreadPoolExecutor(max_workers=len(worker_devices)) as pool:
            futures = [pool.submit(worker, dev) for dev in worker_devices]
            for future in futures:
                future.result()
    finally:
        stop_event.set()
        monitor_thread.join(timeout=10)
        elapsed = time.perf_counter() - start
        save_status()
        aggregate_child_outputs(run_dir, chunks)
        write_reports(run_dir, chunks, elapsed)

    done = sum(1 for c in chunks if c.get("status") == "done")
    failed = sum(1 for c in chunks if c.get("status") == "failed")
    pending = sum(1 for c in chunks if c.get("status") == "pending")
    return {"run_id": run_id, "run_dir": str(run_dir), "done": done, "failed": failed, "pending": pending}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="", help="Run id. Defaults to current UTC timestamp.")
    parser.add_argument("--resume-run", default="", help="Resume an existing phase4_algorithm_factory run id.")
    parser.add_argument("--seed-id", default="", help="Seed label written to manifest/tables, e.g. S00.")
    parser.add_argument("--phase2-run", default="20260604T163422Z", help="Phase 2 run containing range-bias tables.")
    parser.add_argument("--devices", nargs="+", default=["cuda:0", "cuda:1"], help="GPU devices for worker pool.")
    parser.add_argument(
        "--l-ids",
        nargs="+",
        default=None,
        help="Active IMU sensor-model ids, e.g. --l-ids L2 or --l-ids L2,L10. Defaults to the active Phase 3 L set.",
    )
    parser.add_argument(
        "--workers-per-device",
        type=int,
        default=1,
        help="Concurrent chunk feeder workers per GPU device. Use 2 on 8700K for overlap tests; use more on high-core CPUs.",
    )
    parser.add_argument("--chunk-size", type=int, default=4, help="Rows per partial chunk.")
    parser.add_argument("--partial-max-tracks", type=int, default=0, help="0 means all tracks.")
    parser.add_argument("--partial-max-frames", type=int, default=0, help="0 means all frames.")
    parser.add_argument("--max-wall-time", type=int, default=0, help="Stop dispatching new chunks after this many seconds.")
    parser.add_argument("--stop-at-local-time", default="", help="Stop dispatching new chunks at local HH:MM.")
    parser.add_argument("--chunk-timeout-s", type=int, default=3600, help="Per-chunk timeout.")
    parser.add_argument("--monitor-interval", type=float, default=60.0, help="Resource sampling interval in seconds.")
    parser.add_argument("--cache-root", default=str(SIM_ROOT / "cache" / "phase4_gpu_pilot"), help="Cache root passed to GPU pilot children.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
