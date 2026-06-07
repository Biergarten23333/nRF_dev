#!/usr/bin/env python3
"""One-command Phase 4 TRUEFULL launcher for the 9950X + 5090D machine.

This is the clear top-level entrypoint. It runs the per-sensor/per-seed
TRUEFULL factory across all requested sensors and seeds, with CUDA forced for
the raw-range branch and IMU exports enabled by default.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import platform
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml


THIS = Path(__file__).resolve()
SIM_ROOT = THIS.parents[1]
FACTORY = SIM_ROOT / "scripts" / "run_phase4_l2_singleI_full_factory.py"
SENSORS_YAML = SIM_ROOT / "configs" / "sensors.yaml"
RUN_ROOT = SIM_ROOT / "runs" / "phase4_algorithm_factory"


DEFAULT_SEEDS = ["S00", "S01", "S02", "S03", "S04"]
LEGACY_ACTIVE_18 = ["L0", "L1", "L2", "L3", "L4", "L5", "L7", "L8", "L10", "L11", "L12", "L13", "L14", "L15", "L16", "L17", "L18", "L19"]


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def sensor_sort_key(sensor_id: str) -> tuple[int, str]:
    match = re.match(r"^L(\d+)$", sensor_id)
    if match:
        return int(match.group(1)), sensor_id
    return 9999, sensor_id


def load_yaml_sensors() -> list[str]:
    raw = yaml.safe_load(SENSORS_YAML.read_text(encoding="utf-8"))
    return sorted([str(k).upper() for k in raw if re.match(r"^L\d+$", str(k).upper())], key=sensor_sort_key)


def parse_list(value: str, *, kind: str) -> list[str]:
    item = str(value).strip()
    if kind == "sensor" and item.upper() in {"ALL", "YAML", "YAML-ALL"}:
        return load_yaml_sensors()
    if kind == "sensor" and item.upper() in {"ACTIVE18", "LEGACY18", "PREVIOUS18"}:
        return LEGACY_ACTIVE_18[:]
    if kind == "seed" and item.upper() in {"DEFAULT", "5SEED", "S00-S04"}:
        return DEFAULT_SEEDS[:]
    out = [p.strip().upper() for p in item.replace(";", ",").split(",") if p.strip()]
    if not out:
        raise ValueError(f"empty {kind} list")
    return out


def write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        if fields:
            writer.writeheader()
            writer.writerows(rows)
    os.replace(tmp, path)


def write_json(path: Path, obj: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def run_text(cmd: list[str]) -> str:
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception as exc:
        return f"ERR:{exc}"


def sample_machine() -> dict:
    row = {"sample_utc": datetime.now(UTC).isoformat()}
    gpu = run_text(
        [
            "nvidia-smi",
            "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw,power.limit",
            "--format=csv,noheader,nounits",
        ]
    )
    parts = [p.strip() for p in gpu.splitlines()[0].split(",")] if gpu and not gpu.startswith("ERR:") else []
    keys = ["gpu_util_pct", "gpu_mem_util_pct", "gpu_mem_used_mb", "gpu_mem_total_mb", "gpu_temp_c", "gpu_power_w", "gpu_power_limit_w"]
    for key, value in zip(keys, parts):
        row[key] = value
    mem = run_text(["free", "-m"])
    for line in mem.splitlines():
        fields = line.split()
        if fields and fields[0] == "Mem:":
            row["mem_used_mb"] = fields[2]
            row["mem_total_mb"] = fields[1]
        if fields and fields[0] == "Swap:":
            row["swap_used_mb"] = fields[2]
    row["cpu_tctl_c"] = run_text(["bash", "-lc", "sensors 2>/dev/null | awk -F'[+°]' '/Tctl:/ {print $2; exit}'"])
    proc = run_text(["bash", "-lc", "ps -eo pcpu=,rss=,args= | awk '/run_phase4_l2_singleI_full_factory.py/ {n++; cpu+=$1; rss+=$2} END {printf \"%d,%.1f,%.1f\", n, cpu, rss/1024/1024}'"])
    proc_parts = proc.split(",")
    if len(proc_parts) == 3:
        row["truefull_proc_count"] = proc_parts[0]
        row["truefull_cpu_sum_pct"] = proc_parts[1]
        row["truefull_rss_gib"] = proc_parts[2]
    return row


def completed(run_id: str) -> bool:
    manifest = RUN_ROOT / run_id / "manifest.json"
    if not manifest.exists():
        return False
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    return data.get("phase_status") == "complete"


def build_jobs(args: argparse.Namespace, sensors: list[str], seeds: list[str], stamp: str) -> list[dict]:
    jobs: list[dict] = []
    for sensor in sensors:
        for seed in seeds:
            run_id = f"phase4_{sensor}_TRUEFULL_{seed}_5090D_FULL_{stamp}"
            jobs.append({"sensor": sensor, "seed": seed, "run_id": run_id})
    return jobs


def launch_job(job: dict, args: argparse.Namespace, master_dir: Path, env: dict[str, str]) -> subprocess.Popen:
    log_path = master_dir / "logs" / f"{job['run_id']}.log"
    cmd = [
        sys.executable,
        str(FACTORY),
        "--run-id",
        str(job["run_id"]),
        "--phase2-run",
        str(args.phase2_run),
        "--seed-id",
        str(job["seed"]),
        "--sensor-id",
        str(job["sensor"]),
        "--workers",
        str(args.workers_per_run),
        "--raw-backend",
        str(args.raw_backend),
        "--raw-device",
        str(args.raw_device),
        "--raw-gpu-workers",
        str(args.raw_gpu_workers),
        "--corrected-imu-rows",
        str(args.corrected_imu_rows),
    ]
    if not args.export_imu_streams:
        cmd.append("--no-export-imu-streams")
    if not args.export_corrected_imu:
        cmd.append("--no-export-corrected-imu")
    fh = log_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(cmd, cwd=SIM_ROOT, env=env, stdout=fh, stderr=subprocess.STDOUT)
    proc._phase4_log_fh = fh  # type: ignore[attr-defined]
    proc._phase4_cmd = cmd  # type: ignore[attr-defined]
    return proc


def close_proc_log(proc: subprocess.Popen) -> None:
    fh = getattr(proc, "_phase4_log_fh", None)
    if fh:
        fh.close()


def run(args: argparse.Namespace) -> dict:
    sensors = parse_list(args.sensors, kind="sensor")
    seeds = parse_list(args.seeds, kind="seed")
    stamp = args.stamp or utc_stamp()
    master_run = args.run_id or f"phase4_TRUEFULL_ALLSENSORS_ALLSEEDS_5090D_{stamp}"
    master_dir = RUN_ROOT / master_run
    for sub in ["logs", "tables", "manifests"]:
        (master_dir / sub).mkdir(parents=True, exist_ok=True)

    jobs = build_jobs(args, sensors, seeds, stamp)
    env = os.environ.copy()
    env.update(
        {
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )

    status_rows: list[dict] = []
    samples: list[dict] = []
    pending = jobs[:]
    running: list[tuple[dict, subprocess.Popen, float]] = []
    done = 0
    failed = 0

    write_json(
        master_dir / "manifest.json",
        {
            "run_id": master_run,
            "phase_status": "running",
            "phase": "phase4_truefull_all_sensors_all_seeds_5090d",
            "created_utc": datetime.now(UTC).isoformat(),
            "sensors": sensors,
            "seeds": seeds,
            "job_count": len(jobs),
            "max_parallel": int(args.max_parallel),
            "workers_per_run": int(args.workers_per_run),
            "raw_backend": args.raw_backend,
            "raw_device": args.raw_device,
            "raw_gpu_workers": int(args.raw_gpu_workers),
            "export_imu_streams": bool(args.export_imu_streams),
            "export_corrected_imu": bool(args.export_corrected_imu),
            "corrected_imu_rows": str(args.corrected_imu_rows),
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        },
    )

    print(f"[phase4-truefull] master={master_run}", flush=True)
    print(f"[phase4-truefull] sensors={','.join(sensors)} seeds={','.join(seeds)} jobs={len(jobs)}", flush=True)
    print(f"[phase4-truefull] max_parallel={args.max_parallel} workers/run={args.workers_per_run} raw={args.raw_backend}:{args.raw_device} gpu_workers={args.raw_gpu_workers}", flush=True)

    last_sample = 0.0
    while pending or running:
        while pending and len(running) < int(args.max_parallel):
            job = pending.pop(0)
            if args.resume and completed(str(job["run_id"])):
                status_rows.append(
                    {
                        "sensor": job["sensor"],
                        "seed": job["seed"],
                        "run_id": job["run_id"],
                        "status": "skipped_complete",
                        "start_utc": "",
                        "end_utc": datetime.now(UTC).isoformat(),
                        "log_path": "",
                    }
                )
                done += 1
                continue
            start = time.time()
            proc = launch_job(job, args, master_dir, env)
            running.append((job, proc, start))
            print(f"[phase4-truefull] launch {job['run_id']} pid={proc.pid}", flush=True)

        now = time.time()
        if now - last_sample >= float(args.monitor_interval):
            samples.append(sample_machine())
            write_csv(master_dir / "tables" / "machine_stress_samples.csv", samples)
            last_sample = now

        still_running: list[tuple[dict, subprocess.Popen, float]] = []
        for job, proc, start in running:
            rc = proc.poll()
            if rc is None:
                still_running.append((job, proc, start))
                continue
            close_proc_log(proc)
            end_iso = datetime.now(UTC).isoformat()
            status = "done" if rc == 0 else f"failed_{rc}"
            if rc == 0:
                done += 1
            else:
                failed += 1
            status_rows.append(
                {
                    "sensor": job["sensor"],
                    "seed": job["seed"],
                    "run_id": job["run_id"],
                    "status": status,
                    "start_utc": datetime.fromtimestamp(start, UTC).isoformat(),
                    "end_utc": end_iso,
                    "elapsed_s": f"{time.time() - start:.2f}",
                    "log_path": str(master_dir / "logs" / f"{job['run_id']}.log"),
                }
            )
            write_csv(master_dir / "tables" / "lane_status.csv", status_rows)
            print(f"[phase4-truefull] {job['run_id']} {status} ({done + failed}/{len(jobs)})", flush=True)
        running = still_running
        time.sleep(float(args.poll_interval))

    phase_status = "complete" if failed == 0 else "complete_with_failures"
    write_csv(master_dir / "tables" / "lane_status.csv", status_rows)
    write_csv(master_dir / "tables" / "machine_stress_samples.csv", samples)
    write_json(
        master_dir / "manifest.json",
        {
            "run_id": master_run,
            "phase_status": phase_status,
            "phase": "phase4_truefull_all_sensors_all_seeds_5090d",
            "completed_utc": datetime.now(UTC).isoformat(),
            "sensors": sensors,
            "seeds": seeds,
            "job_count": len(jobs),
            "done": done,
            "failed": failed,
            "max_parallel": int(args.max_parallel),
            "workers_per_run": int(args.workers_per_run),
            "raw_backend": args.raw_backend,
            "raw_device": args.raw_device,
            "raw_gpu_workers": int(args.raw_gpu_workers),
            "export_imu_streams": bool(args.export_imu_streams),
            "export_corrected_imu": bool(args.export_corrected_imu),
            "corrected_imu_rows": str(args.corrected_imu_rows),
            "outputs": {
                "lane_status": "tables/lane_status.csv",
                "machine_stress_samples": "tables/machine_stress_samples.csv",
                "logs": "logs/",
            },
            "host": {"platform": platform.platform(), "cpu_count": os.cpu_count()},
        },
    )
    return {"run_id": master_run, "run_dir": str(master_dir), "status": phase_status, "jobs": len(jobs), "done": done, "failed": failed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run true Phase 4 FULL across sensors and seeds on 5090D.")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--stamp", default="")
    parser.add_argument("--phase2-run", default="20260604T163422Z")
    parser.add_argument("--sensors", default="ALL", help="ALL, ACTIVE18, or comma list such as L16,L19,L20.")
    parser.add_argument("--seeds", default="S00-S04", help="S00-S04 or comma list.")
    parser.add_argument("--max-parallel", type=int, default=2, help="Concurrent per-sensor/per-seed TRUEFULL jobs.")
    parser.add_argument("--workers-per-run", type=int, default=8, help="CPU workers inside each TRUEFULL job.")
    parser.add_argument("--raw-backend", choices=["cuda", "auto", "cpu"], default="cuda", help="Use cuda to fail fast if GPU is not active.")
    parser.add_argument("--raw-device", default="cuda:0")
    parser.add_argument("--raw-gpu-workers", type=int, default=16)
    parser.add_argument("--export-imu-streams", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--export-corrected-imu", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--corrected-imu-rows", default="A0:U4:P4:I5:T5")
    parser.add_argument("--monitor-interval", type=float, default=10.0)
    parser.add_argument("--poll-interval", type=float, default=2.0)
    parser.add_argument("--resume", action="store_true", help="Skip jobs whose child manifest is already complete.")
    args = parser.parse_args()
    result = run(args)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    main()
