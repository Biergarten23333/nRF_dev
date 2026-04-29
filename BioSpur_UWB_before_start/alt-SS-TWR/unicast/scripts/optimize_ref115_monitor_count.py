#!/usr/bin/env python3
import argparse
import json
import math
import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd, *, cwd=REPO_ROOT):
    print("$", " ".join(str(part) for part in cmd), flush=True)
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    extra_pythonpath = "/usr/lib/python3/dist-packages"
    env["PYTHONPATH"] = (
        f"{extra_pythonpath}:{current_pythonpath}" if current_pythonpath else extra_pythonpath
    )
    subprocess.run([str(part) for part in cmd], cwd=cwd, env=env, check=True)


def build_tag(build_dir: Path, anchor_count: int):
    if build_dir.exists():
        shutil.rmtree(build_dir)

    cmake_args = [
        "-DAPP_TAG_ID=1",
        "-DAPP_TAG_TDMA_ENABLE=1",
        "-DAPP_TAG_TDMA_SLOT_INDEX=1",
        "-DAPP_TAG_TDMA_SLOT_COUNT=10",
        "-DAPP_TAG_TDMA_SLOT_PERIOD_MS=10",
        "-DAPP_TAG_TDMA_SLOT_ACTIVE_MS=9",
        "-DAPP_TAG_EKF_ENABLE=1",
        "-DAPP_TAG_EKF_MEAS_STD_MM=200",
        "-DAPP_TAG_EKF_RESIDUAL_GAIN_PCT=0",
        "-DAPP_TAG_EKF_PROC_ACCEL_MM_S2=1",
        "-DAPP_TAG_EKF_INIT_POS_STD_MM=200",
        "-DAPP_TAG_EKF_INIT_VEL_STD_MM_S=1000",
        "-DAPP_TAG_EKF_OUTLIER_GATE_MM=35",
        "-DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=140",
        "-DAPP_TAG_RANGE_HARD_RESIDUAL_MM=260",
        "-DAPP_TAG_VERBOSE_RANGING=0",
        "-DAPP_TAG_VERBOSE_MEASUREMENTS=0",
    ]

    if anchor_count == 4:
        cmake_args.extend(
            [
                "-DAPP_TAG_FIXED_MODE=1",
                "-DAPP_TAG_FIXED_ANCHOR_COUNT=4",
                "-DAPP_TAG_FIXED_ANCHOR_0_ID=1",
                "-DAPP_TAG_FIXED_ANCHOR_1_ID=2",
                "-DAPP_TAG_FIXED_ANCHOR_2_ID=5",
                "-DAPP_TAG_FIXED_ANCHOR_3_ID=6",
            ]
        )
    else:
        cmake_args.extend(
            [
                "-DAPP_TAG_FIXED_MODE=0",
                "-DAPP_TAG_FULL_SWEEP_INTERVAL=8",
                f"-DAPP_TAG_TRACK_ANCHOR_COUNT={anchor_count}",
            ]
        )

    run(
        [
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
            *cmake_args,
        ]
    )


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


def score_summary(summary: dict, z_weight: float, residual_weight: float) -> float:
    std = summary["position_std_mm"]
    residual = summary["residual_mean_mm"]
    x = float(std["x"] or 0.0)
    y = float(std["y"] or 0.0)
    z = float(std["z"] or 0.0)
    rms = float(residual["rms"] or 0.0)
    geom = math.sqrt(x * x + y * y + (z_weight * z) * (z_weight * z))
    return geom + residual_weight * rms


def summarize_result(anchor_count: int, summary: dict, score: float) -> dict:
    return {
        "anchor_count": anchor_count,
        "score": score,
        "position_std_mm": summary["position_std_mm"],
        "position_mean_mm": summary["position_mean_mm"],
        "residual_mean_mm": summary["residual_mean_mm"],
        "position_samples_used_in_summary": summary["position_samples_used_in_summary"],
        "session_dir": summary["session_dir"],
        "anchor_subset_frequency_summary": summary["anchor_subset_frequency_summary"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare Ref115 monitor mode across anchor counts.")
    parser.add_argument("--snr", default="760186115")
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00",
    )
    parser.add_argument("--duration", type=float, default=90.0)
    parser.add_argument("--confirm-duration", type=float, default=150.0)
    parser.add_argument("--z-weight", type=float, default=2.0)
    parser.add_argument("--residual-weight", type=float, default=0.05)
    parser.add_argument("--run-stamp", default=None)
    parser.add_argument(
        "--result-dir",
        default="logs/tag_sessions/ref115_monitor_opt",
        help="Directory for optimization metadata",
    )
    args = parser.parse_args()

    result_dir = REPO_ROOT / args.result_dir
    result_dir.mkdir(parents=True, exist_ok=True)
    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")

    all_results = []
    best_result = None

    for anchor_count in (8, 7, 6, 5, 4):
        build_dir = REPO_ROOT / f"build-ref115-monitor-{anchor_count}"
        session_name = f"ref115_monitor_{run_stamp}_{anchor_count}a"
        summary_path = REPO_ROOT / "logs" / "tag_sessions" / session_name / "summary.json"
        if not summary_path.exists():
            build_tag(build_dir, anchor_count)
            flash_tag(args.snr, build_dir / "zephyr" / "merged.hex")
            summary_path = capture_tag(args.snr, args.port, args.duration, session_name)
        summary = load_summary(summary_path)
        score = score_summary(summary, args.z_weight, args.residual_weight)
        result = summarize_result(anchor_count, summary, score)
        all_results.append(result)
        if best_result is None or score < best_result["score"]:
            best_result = result

    if best_result is None:
        raise RuntimeError("No monitor-count results were produced.")

    best_build_dir = REPO_ROOT / f"build-ref115-monitor-{best_result['anchor_count']}"
    best_hex = best_build_dir / "zephyr" / "merged.hex"
    confirm_session_name = f"ref115_monitor_{run_stamp}_confirm_best"
    confirm_summary_path = REPO_ROOT / "logs" / "tag_sessions" / confirm_session_name / "summary.json"
    if not confirm_summary_path.exists():
        flash_tag(args.snr, best_hex)
        confirm_summary_path = capture_tag(
            args.snr, args.port, args.confirm_duration, confirm_session_name
        )
    confirm_summary = load_summary(confirm_summary_path)
    confirm_score = score_summary(confirm_summary, args.z_weight, args.residual_weight)

    payload = {
        "run_stamp": run_stamp,
        "snr": args.snr,
        "port": args.port,
        "duration_s": args.duration,
        "confirm_duration_s": args.confirm_duration,
        "z_weight": args.z_weight,
        "residual_weight": args.residual_weight,
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
    print(f"best_anchor_count: {best_result['anchor_count']}")
    print(f"best_score: {best_result['score']:.3f}")
    print(f"confirm_score: {confirm_score:.3f}")
    print(f"confirm_summary: {confirm_summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
