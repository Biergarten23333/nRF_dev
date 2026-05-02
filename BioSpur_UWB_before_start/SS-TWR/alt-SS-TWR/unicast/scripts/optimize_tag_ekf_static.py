#!/usr/bin/env python3
import argparse
import json
import math
import os
import subprocess
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd, *, cwd=REPO_ROOT):
    print("$", " ".join(str(part) for part in cmd), flush=True)
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    extra_pythonpath = "/usr/lib/python3/dist-packages"
    if current_pythonpath:
        env["PYTHONPATH"] = f"{extra_pythonpath}:{current_pythonpath}"
    else:
        env["PYTHONPATH"] = extra_pythonpath
    subprocess.run([str(part) for part in cmd], cwd=cwd, env=env, check=True)


def build_tag(build_dir: Path, config: dict):
    cmd = [
        "west",
        "build",
        "-b",
        "decawave_dwm1001_dev/nrf52832",
        "-s",
        "apps/tag",
        "-d",
        str(build_dir),
        "--no-sysbuild",
        "--pristine=always",
        "--",
        "-DAPP_TAG_ID=1",
        "-DAPP_TAG_FIXED_MODE=1",
        "-DAPP_TAG_FIXED_ANCHOR_COUNT=4",
        "-DAPP_TAG_FIXED_ANCHOR_0_ID=1",
        "-DAPP_TAG_FIXED_ANCHOR_1_ID=2",
        "-DAPP_TAG_FIXED_ANCHOR_2_ID=5",
        "-DAPP_TAG_FIXED_ANCHOR_3_ID=6",
        "-DAPP_TAG_TDMA_ENABLE=1",
        "-DAPP_TAG_TDMA_SLOT_INDEX=1",
        "-DAPP_TAG_TDMA_SLOT_COUNT=10",
        "-DAPP_TAG_TDMA_SLOT_PERIOD_MS=10",
        "-DAPP_TAG_TDMA_SLOT_ACTIVE_MS=9",
        f"-DAPP_TAG_EKF_ENABLE={config['ekf_enable']}",
        f"-DAPP_TAG_EKF_MEAS_STD_MM={config['meas_std_mm']}",
        f"-DAPP_TAG_EKF_RESIDUAL_GAIN_PCT={config['residual_gain_pct']}",
        f"-DAPP_TAG_EKF_PROC_ACCEL_MM_S2={config['proc_accel_mm_s2']}",
        f"-DAPP_TAG_EKF_INIT_POS_STD_MM={config['init_pos_std_mm']}",
        f"-DAPP_TAG_EKF_INIT_VEL_STD_MM_S={config['init_vel_std_mm_s']}",
        f"-DAPP_TAG_EKF_OUTLIER_GATE_MM={config['outlier_gate_mm']}",
    ]
    run(cmd)


def flash_tag(snr: str, hex_path: Path):
    run(["scripts/reset_then_flash.sh", snr, str(hex_path)])


def capture_tag(snr: str, port: str, duration_s: float, session_name: str) -> Path:
    run(
        [
            "python3",
            "scripts/capture_tag_session.py",
            snr,
            port,
            "--duration",
            str(duration_s),
            "--settle",
            "1.0",
            "--skip-sweeps",
            "50",
            "--session-name",
            session_name,
        ]
    )
    return REPO_ROOT / "logs" / "tag_sessions" / session_name / "summary.json"


def load_summary(summary_path: Path) -> dict:
    return json.loads(summary_path.read_text(encoding="utf-8"))


def score_summary(summary: dict, z_weight: float) -> float:
    std = summary["position_std_mm"]
    x = float(std["x"] or 0.0)
    y = float(std["y"] or 0.0)
    z = float(std["z"] or 0.0)
    return math.sqrt(x * x + y * y + (z_weight * z) * (z_weight * z))


def make_config(name: str, *, ekf_enable: int, meas_std_mm: int,
                proc_accel_mm_s2: int, residual_gain_pct: int = 0,
                init_pos_std_mm: int = 200, init_vel_std_mm_s: int = 1000,
                outlier_gate_mm: int = 0):
    return {
        "name": name,
        "ekf_enable": ekf_enable,
        "meas_std_mm": meas_std_mm,
        "proc_accel_mm_s2": proc_accel_mm_s2,
        "residual_gain_pct": residual_gain_pct,
        "init_pos_std_mm": init_pos_std_mm,
        "init_vel_std_mm_s": init_vel_std_mm_s,
        "outlier_gate_mm": outlier_gate_mm,
    }


def coarse_configs():
    yield make_config("baseline_raw", ekf_enable=0, meas_std_mm=25, proc_accel_mm_s2=250)
    for meas in (20, 40, 80):
        for proc in (1, 5, 20):
            yield make_config(
                f"coarse_m{meas}_q{proc}",
                ekf_enable=1,
                meas_std_mm=meas,
                proc_accel_mm_s2=proc,
            )


def refine_configs(best: dict):
    meas_values = sorted(
        {
            max(5, int(round(best["meas_std_mm"] * 0.6))),
            int(best["meas_std_mm"]),
            int(round(best["meas_std_mm"] * 1.4)),
        }
    )
    proc_values = sorted(
        {
            max(1, int(round(best["proc_accel_mm_s2"] * 0.5))),
            int(best["proc_accel_mm_s2"]),
            int(round(best["proc_accel_mm_s2"] * 2.0)),
        }
    )
    configs = []
    for meas in meas_values:
        for proc in proc_values:
            configs.append(
                make_config(
                    f"refine_m{meas}_q{proc}",
                    ekf_enable=1,
                    meas_std_mm=meas,
                    proc_accel_mm_s2=proc,
                )
            )
    return configs


def targeted_static_configs():
    configs = []
    for meas, proc, gate in (
        (160, 1, 35),
        (180, 1, 35),
        (200, 1, 35),
        (220, 1, 35),
        (250, 1, 35),
        (300, 1, 35),
        (160, 2, 35),
        (200, 2, 35),
        (220, 2, 35),
        (250, 2, 35),
        (180, 1, 40),
        (200, 1, 40),
        (220, 1, 40),
        (250, 1, 40),
    ):
        configs.append(
            make_config(
                f"target_m{meas}_q{proc}_g{gate}",
                ekf_enable=1,
                meas_std_mm=meas,
                proc_accel_mm_s2=proc,
                outlier_gate_mm=gate,
            )
        )
    return configs


def summarize_result(config: dict, summary: dict, score: float) -> dict:
    return {
        "config": deepcopy(config),
        "score": score,
        "position_std_mm": summary["position_std_mm"],
        "position_mean_mm": summary["position_mean_mm"],
        "residual_mean_mm": summary["residual_mean_mm"],
        "position_samples_used_in_summary": summary["position_samples_used_in_summary"],
        "session_dir": summary["session_dir"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Optimize 115 static-tag EKF parameters.")
    parser.add_argument("--snr", default="760186115")
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00",
    )
    parser.add_argument("--duration", type=float, default=90.0)
    parser.add_argument("--confirm-duration", type=float, default=300.0)
    parser.add_argument("--z-weight", type=float, default=2.0)
    parser.add_argument("--run-stamp", default=None)
    parser.add_argument(
        "--result-dir",
        default="logs/tag_sessions/ekf_opt_115",
        help="Directory for optimization metadata",
    )
    args = parser.parse_args()

    result_dir = REPO_ROOT / args.result_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    all_results = []
    tested_names = set()

    best_result = None
    best_enabled = None

    for config in coarse_configs():
        build_dir = REPO_ROOT / f"build-ekf-115-{config['name']}"
        session_name = f"ekf115_{run_stamp}_{config['name']}"
        summary_path = REPO_ROOT / "logs" / "tag_sessions" / session_name / "summary.json"
        if not summary_path.exists():
            build_tag(build_dir, config)
            flash_tag(args.snr, build_dir / "zephyr" / "merged.hex")
            summary_path = capture_tag(args.snr, args.port, args.duration, session_name)
        summary = load_summary(summary_path)
        score = score_summary(summary, args.z_weight)
        result = summarize_result(config, summary, score)
        all_results.append(result)
        tested_names.add(config["name"])
        if best_result is None or score < best_result["score"]:
            best_result = result
        if config["ekf_enable"] and (best_enabled is None or score < best_enabled["score"]):
            best_enabled = result

    if best_enabled is not None:
        for config in refine_configs(best_enabled["config"]):
            if config["name"] in tested_names:
                continue
            build_dir = REPO_ROOT / f"build-ekf-115-{config['name']}"
            session_name = f"ekf115_{run_stamp}_{config['name']}"
            summary_path = REPO_ROOT / "logs" / "tag_sessions" / session_name / "summary.json"
            if not summary_path.exists():
                build_tag(build_dir, config)
                flash_tag(args.snr, build_dir / "zephyr" / "merged.hex")
                summary_path = capture_tag(args.snr, args.port, args.duration, session_name)
            summary = load_summary(summary_path)
            score = score_summary(summary, args.z_weight)
            result = summarize_result(config, summary, score)
            all_results.append(result)
            tested_names.add(config["name"])
            if score < best_result["score"]:
                best_result = result
            if score < best_enabled["score"]:
                best_enabled = result

    for config in targeted_static_configs():
        if config["name"] in tested_names:
            continue
        build_dir = REPO_ROOT / f"build-ekf-115-{config['name']}"
        session_name = f"ekf115_{run_stamp}_{config['name']}"
        summary_path = REPO_ROOT / "logs" / "tag_sessions" / session_name / "summary.json"
        if not summary_path.exists():
            build_tag(build_dir, config)
            flash_tag(args.snr, build_dir / "zephyr" / "merged.hex")
            summary_path = capture_tag(args.snr, args.port, args.duration, session_name)
        summary = load_summary(summary_path)
        score = score_summary(summary, args.z_weight)
        result = summarize_result(config, summary, score)
        all_results.append(result)
        tested_names.add(config["name"])
        if score < best_result["score"]:
            best_result = result
        if config["ekf_enable"] and (best_enabled is None or score < best_enabled["score"]):
            best_enabled = result

    if best_result is None:
        raise RuntimeError("No EKF results were produced.")

    best_build_dir = REPO_ROOT / f"build-ekf-115-{best_result['config']['name']}"
    best_hex = best_build_dir / "zephyr" / "merged.hex"
    confirm_session_name = f"ekf115_{run_stamp}_confirm_best"
    confirm_summary_path = (
        REPO_ROOT / "logs" / "tag_sessions" / confirm_session_name / "summary.json"
    )
    if not confirm_summary_path.exists():
        flash_tag(args.snr, best_hex)
        confirm_summary_path = capture_tag(
            args.snr, args.port, args.confirm_duration, confirm_session_name
        )
    confirm_summary = load_summary(confirm_summary_path)
    confirm_score = score_summary(confirm_summary, args.z_weight)

    payload = {
        "run_stamp": run_stamp,
        "snr": args.snr,
        "port": args.port,
        "duration_s": args.duration,
        "confirm_duration_s": args.confirm_duration,
        "z_weight": args.z_weight,
        "tested_results": sorted(all_results, key=lambda item: item["score"]),
        "best_result": best_result,
        "confirmation": {
            "score": confirm_score,
            "summary_path": str(confirm_summary_path),
            "summary": confirm_summary,
            "hex_path": str(best_hex),
        },
    }

    output_path = result_dir / f"result_{run_stamp}.json"
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print("")
    print(f"result_json: {output_path}")
    print(f"best_config: {json.dumps(best_result['config'], sort_keys=True)}")
    print(f"best_score: {best_result['score']:.3f}")
    print(f"confirm_score: {confirm_score:.3f}")
    print(f"confirm_summary: {confirm_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
