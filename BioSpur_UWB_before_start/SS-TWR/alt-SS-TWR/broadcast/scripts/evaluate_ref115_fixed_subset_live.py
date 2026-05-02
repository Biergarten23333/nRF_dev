#!/usr/bin/env python3
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
ANCHOR_TO_ID = {label: idx for idx, label in enumerate("ABCDEFGH")}


def run(cmd, *, cwd=REPO_ROOT):
    print("$", " ".join(str(part) for part in cmd), flush=True)
    env = os.environ.copy()
    current_pythonpath = env.get("PYTHONPATH", "")
    extra_pythonpath = "/usr/lib/python3/dist-packages"
    env["PYTHONPATH"] = (
        f"{extra_pythonpath}:{current_pythonpath}" if current_pythonpath else extra_pythonpath
    )
    subprocess.run([str(part) for part in cmd], cwd=cwd, env=env, check=True)


def build_subset(build_dir: Path, subset_labels: list[str]):
    if build_dir.exists():
        shutil.rmtree(build_dir)

    subset_ids = [ANCHOR_TO_ID[label] for label in subset_labels]
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
        "-DAPP_TAG_FIXED_MODE=1",
        "-DAPP_TAG_FIXED_ANCHOR_COUNT=4",
        f"-DAPP_TAG_FIXED_ANCHOR_0_ID={subset_ids[0]}",
        f"-DAPP_TAG_FIXED_ANCHOR_1_ID={subset_ids[1]}",
        f"-DAPP_TAG_FIXED_ANCHOR_2_ID={subset_ids[2]}",
        f"-DAPP_TAG_FIXED_ANCHOR_3_ID={subset_ids[3]}",
        "-DAPP_TAG_VERBOSE_RANGING=0",
        "-DAPP_TAG_VERBOSE_MEASUREMENTS=0",
    ]
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
    run(
        [
            "python3",
            "scripts/write_build_source.py",
            "--build-dir",
            str(build_dir),
            "--source",
            "scripts/evaluate_ref115_fixed_subset_live.py",
            "--command",
            f"python3 scripts/evaluate_ref115_fixed_subset_live.py --subset {','.join(subset_labels)}",
            "--note",
            f"subset={','.join(subset_labels)}",
        ]
    )


def main():
    parser = argparse.ArgumentParser(description="Flash and evaluate one fixed 4-anchor Ref115 subset live.")
    parser.add_argument("--snr", default="760186115")
    parser.add_argument("--port", default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00")
    parser.add_argument("--subset", required=True, help="Comma-separated labels, e.g. A,D,F,G")
    parser.add_argument("--duration", type=float, default=60.0)
    parser.add_argument("--build-dir", default=None)
    parser.add_argument("--session-name", default=None)
    args = parser.parse_args()

    subset_labels = [item.strip().upper() for item in args.subset.split(",") if item.strip()]
    if len(subset_labels) != 4 or len(set(subset_labels)) != 4:
        raise SystemExit("subset must contain exactly 4 unique anchor labels")

    subset_tag = "".join(subset_labels)
    build_dir = REPO_ROOT / (args.build_dir or f"build-ref115-fixed-{subset_tag.lower()}")
    session_name = args.session_name or f"ref115_fixed_{subset_tag}_live"

    build_subset(build_dir, subset_labels)
    run(["scripts/reset_then_flash.sh", args.snr, str(build_dir / "zephyr" / "merged.hex")])
    run(
        [
            "python3",
            "scripts/capture_tag_session.py",
            args.snr,
            args.port,
            "--duration",
            str(args.duration),
            "--settle",
            "1.0",
            "--skip-sweeps",
            "50",
            "--session-name",
            session_name,
        ]
    )

    summary_path = REPO_ROOT / "logs" / "tag_sessions" / session_name / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    print("")
    print(f"subset: {','.join(subset_labels)}")
    print(f"summary_json: {summary_path}")
    print(f"position_std_mm: {json.dumps(summary['position_std_mm'])}")
    print(f"residual_mean_mm: {json.dumps(summary['residual_mean_mm'])}")


if __name__ == "__main__":
    raise SystemExit(main())
