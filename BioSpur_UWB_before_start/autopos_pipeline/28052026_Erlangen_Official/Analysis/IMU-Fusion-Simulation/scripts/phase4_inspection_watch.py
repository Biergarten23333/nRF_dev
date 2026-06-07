#!/usr/bin/env python3
import argparse
import datetime as dt
import glob
import os
import subprocess
import time


def run_text(cmd):
    try:
        return subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL).strip()
    except Exception as exc:
        return f"ERR: {exc}"


def tail_lines(path, n):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        return "".join(lines[-n:]).rstrip()
    except FileNotFoundError:
        return "(missing)"
    except Exception as exc:
        return f"ERR: {exc}"


def latest(pattern):
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def count_processes():
    out = run_text(["ps", "-eo", "cmd="])
    counts = {"gpu_pilot": 0, "truefull": 0, "stress": 0}
    for line in out.splitlines():
        if "run_phase4_gpu_pilot.py" in line:
            counts["gpu_pilot"] += 1
        if "run_phase4_l2_singleI_full_factory.py" in line:
            counts["truefull"] += 1
        if "run_phase4_truefull_all_sensors_all_seeds_5090d.py" in line:
            counts["truefull"] += 1
        if "run_phase4_lx_stress_candidates.py" in line:
            counts["stress"] += 1
    return counts


def sample(run_dir, log_path):
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    gpu = run_text([
        "nvidia-smi",
        "--query-gpu=utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,power.limit,temperature.gpu",
        "--format=csv,noheader,nounits",
    ])
    mem = run_text(["free", "-h"])
    shm = run_text(["df", "-h", "/dev/shm"])
    counts = count_processes()

    truefull_log = latest(os.path.join(run_dir, "logs", "phase4_L*_TRUEFULL_*.log"))
    gpu_log = latest(os.path.join(run_dir, "logs", "phase4_GPU_RAW_ALLL*_DUALLANE_*.log"))
    stress_log = os.path.join(
        run_dir,
        "logs",
        "phase4_FULLSTRESS_ALLL_18L_5seed_allP_allI_allT_DUALLANE_20260606T221157Z.log",
    )
    lane_status = os.path.join(run_dir, "tables", "lane_status.csv")

    with open(log_path, "a", encoding="utf-8") as fh:
        fh.write(f"===== {now} =====\n")
        fh.write(f"GPU raw: {gpu}\n")
        fh.write("-- memory --\n")
        fh.write(mem + "\n")
        fh.write("-- /dev/shm --\n")
        fh.write(shm + "\n")
        fh.write(
            "proc gpu_pilot={gpu_pilot} truefull={truefull} stress={stress}\n".format(**counts)
        )
        fh.write("-- lane_status --\n")
        fh.write(tail_lines(lane_status, 10) + "\n")
        fh.write("-- truefull tail --\n")
        fh.write((truefull_log or "(missing)") + "\n")
        fh.write(tail_lines(truefull_log, 5) if truefull_log else "(missing)")
        fh.write("\n-- gpu tail --\n")
        fh.write((gpu_log or "(missing)") + "\n")
        fh.write(tail_lines(gpu_log, 5) if gpu_log else "(missing)")
        fh.write("\n-- stress tail --\n")
        fh.write(tail_lines(stress_log, 5) + "\n\n")
        fh.flush()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--interval", type=float, default=60.0)
    parser.add_argument("--log-name", default="inspection_watch.log")
    args = parser.parse_args()

    log_path = os.path.join(args.run_dir, "logs", args.log_name)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    while True:
        sample(args.run_dir, log_path)
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
