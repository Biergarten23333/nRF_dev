#!/usr/bin/env python3
import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import serial


REPO_ROOT = Path(__file__).resolve().parents[1]


def run(cmd, *, cwd=REPO_ROOT, env=None):
    merged_env = os.environ.copy()
    current_pythonpath = merged_env.get("PYTHONPATH", "")
    extra_pythonpath = "/usr/lib/python3/dist-packages:/usr/lib/python3.12/dist-packages"
    merged_env["PYTHONPATH"] = (
        f"{extra_pythonpath}:{current_pythonpath}" if current_pythonpath else extra_pythonpath
    )
    if env:
        merged_env.update(env)

    print("$", " ".join(str(part) for part in cmd), flush=True)
    subprocess.run([str(part) for part in cmd], cwd=cwd, env=merged_env, check=True)


def capture_serial_raw(port: str, duration: float, out_path: Path, baud: int = 115200) -> list[str]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.time() + duration
    pending = ""
    lines = []
    with out_path.open("w", encoding="utf-8") as raw_log:
        while time.time() < deadline:
            try:
                with serial.Serial(port, baud, timeout=0.2) as ser:
                    while time.time() < deadline:
                        chunk = ser.read(ser.in_waiting or 1)
                        if not chunk:
                            continue
                        pending += chunk.decode("utf-8", errors="replace")
                        while "\n" in pending:
                            line, pending = pending.split("\n", 1)
                            text = line.rstrip("\r")
                            if not text:
                                continue
                            raw_log.write(text + "\n")
                            raw_log.flush()
                            lines.append(text)
                    break
            except (serial.SerialException, OSError):
                time.sleep(0.2)

        if pending.strip():
            text = pending.rstrip("\r")
            raw_log.write(text + "\n")
            raw_log.flush()
            lines.append(text)

    return lines


def summarize_ota_log(lines: list[str]) -> dict:
    text = "\n".join(lines)
    return {
        "line_count": len(lines),
        "saw_connected": "Connected:" in text,
        "saw_upload_complete": "OTA upload complete" in text,
        "saw_pending_request": "OTA pending/test request" in text,
        "saw_reset_sent": "OTA reset request" in text,
        "saw_schedule_failed": "OTA schedule failed" in text,
        "saw_upload_failed": "OTA upload failed" in text,
        "saw_state_read_failed": "OTA state read failed" in text,
        "saw_tagsummary": "TagSummary" in text,
    }


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def logical_tag_id(tag_id: int) -> int:
    if tag_id == 115:
        return 1
    if tag_id == 113:
        return 3
    if tag_id == 127:
        return 2
    if tag_id == 886:
        return 4
    if 0 <= tag_id < 10:
        return tag_id
    raise ValueError(f"unsupported motion tag id for logical mapping: {tag_id}")


def motion_tag_args(tag_id: int, device_name: str) -> str:
    # Keep this aligned with the currently validated motion/OTA family.
    logical_id = logical_tag_id(tag_id)
    return (
        f'-DAPP_TAG_ID={logical_id} '
        f'-DAPP_TAG_BLE_TOKEN_ID={tag_id} '
        f'-DAPP_TAG_BLE_ENABLE=1 '
        f'-DCONFIG_BT_DEVICE_NAME="{device_name}" '
        f'-DAPP_TAG_BLE_OTA_ENABLE=1 '
        f'-DAPP_TAG_BLE_SETTINGS_ENABLE=0 '
        f'-DAPP_TAG_BLE_COMPACT_STATUS=1 '
        f'-DAPP_TAG_BLE_PACKET_BUNDLE_RECORDS=3 '
        f'-DAPP_TAG_BLE_PACKET_BUNDLE_FLUSH_MS=250 '
        f'-DAPP_TAG_MCUBOOT_ENABLE=1 '
        f'-DAPP_TAG_EKF_ENABLE=1 '
        f'-DAPP_TAG_EKF_MEAS_STD_MM=35 '
        f'-DAPP_TAG_EKF_RESIDUAL_GAIN_PCT=0 '
        f'-DAPP_TAG_EKF_PROC_ACCEL_MM_S2=500 '
        f'-DAPP_TAG_EKF_INIT_POS_STD_MM=200 '
        f'-DAPP_TAG_EKF_INIT_VEL_STD_MM=1200 '
        f'-DAPP_TAG_EKF_OUTLIER_GATE_MM=120 '
        f'-DAPP_TAG_FIXED_MODE=0 '
        f'-DAPP_TAG_FAST_TRACKING=1 '
        f'-DAPP_TAG_FULL_SWEEP_INTERVAL=4 '
        f'-DAPP_TAG_TRACK_ANCHOR_COUNT=6 '
        f'-DAPP_TAG_IMU_SAMPLE_PERIOD=2 '
        f'-DAPP_TAG_RANGE_SOFT_RESIDUAL_MM=180 '
        f'-DAPP_TAG_RANGE_HARD_RESIDUAL_MM=350 '
        f'-DAPP_TAG_MOTION_FULL_SWEEP_INTERVAL=4 '
        f'-DAPP_TAG_MOTION_SPEED_THRESHOLD_MM_S=250 '
        f'-DAPP_TAG_MOTION_RANGE_SOFT_BONUS_MM=140 '
        f'-DAPP_TAG_MOTION_RANGE_HARD_BONUS_MM=260 '
        f'-DAPP_TAG_MOTION_EKF_MEAS_STD_MM=35 '
        f'-DAPP_TAG_MOTION_EKF_PROC_ACCEL_MM_S2=1800 '
        f'-DAPP_TAG_MOTION_EKF_OUTLIER_GATE_MM=220 '
        f'-DAPP_TAG_MOTION_IMU_DELTA_THRESHOLD_MG=250 '
        f'-DAPP_TAG_MOTION_IMU_GRAVITY_ERR_THRESHOLD_MG=140'
    )


def ota_one_tag(
    *,
    profile_name: str,
    tag_id: int,
    tag_sign_version: str,
    master_snr: str,
    master_port: str,
    ota_capture_duration: float,
    tag_build_dir: str,
    master_ota_build_dir: str,
    tag_device_name: str,
) -> dict:
    tag_cmake_args = motion_tag_args(tag_id, tag_device_name)

    run(
        ["./scripts/build_motion_tag_ota_profile.sh", tag_build_dir, master_ota_build_dir],
        env={
            "TAG_ID": str(tag_id),
            "TAG_DEVICE_NAME": tag_device_name,
            "TAG_SIGN_VERSION": tag_sign_version,
            "TAG_CMAKE_ARGS": tag_cmake_args,
        },
    )

    run(
        [
            "./scripts/reset_then_flash.sh",
            master_snr,
            str((REPO_ROOT / master_ota_build_dir / "zephyr" / "zephyr.hex").resolve()),
        ]
    )

    eval_root = REPO_ROOT / "logs" / "motion_profile_eval" / profile_name
    ota_raw_path = eval_root / f"ota_{tag_id}_master_raw.log"
    ota_lines = capture_serial_raw(master_port, ota_capture_duration, ota_raw_path)
    ota_summary = summarize_ota_log(ota_lines)
    (eval_root / f"ota_{tag_id}_summary.json").write_text(
        json.dumps(ota_summary, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "tag_id": tag_id,
        "tag_device_name": tag_device_name,
        "tag_sign_version": tag_sign_version,
        "tag_build_dir": tag_build_dir,
        "master_ota_build_dir": master_ota_build_dir,
        "ota_raw_log": str(ota_raw_path),
        "ota_summary_json": str(eval_root / f"ota_{tag_id}_summary.json"),
        "ota_summary": ota_summary,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build, OTA-deploy, and evaluate two rotating motion-tag profiles while "
            "passively guarding Ref115."
        )
    )
    parser.add_argument("--profile-name", required=True)
    parser.add_argument("--tag-a-id", type=int, default=113)
    parser.add_argument("--tag-b-id", type=int, default=127)
    parser.add_argument("--tag-a-sign-version", default="0.0.1+113")
    parser.add_argument("--tag-b-sign-version", default="0.0.1+127")
    parser.add_argument("--master-snr", default="683234364")
    parser.add_argument(
        "--master-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000683234364-if00",
    )
    parser.add_argument("--ref-snr", default="760186115")
    parser.add_argument(
        "--ref-port",
        default="/dev/serial/by-id/usb-SEGGER_J-Link_000760186115-if00",
    )
    parser.add_argument(
        "--master-ble-hex",
        default="build-master-ble/merged.hex",
        help="Ordinary BLE master image used after OTA finishes",
    )
    parser.add_argument("--ota-capture-duration", type=float, default=180.0)
    parser.add_argument("--eval-duration", type=float, default=60.0)
    parser.add_argument("--ref-skip-sweeps", type=int, default=10)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-ota", action="store_true")
    args = parser.parse_args()

    profile_name = args.profile_name
    eval_root = REPO_ROOT / "logs" / "motion_profile_eval" / profile_name
    eval_root.mkdir(parents=True, exist_ok=True)

    tag_a_name = "BS_AUTO"
    tag_b_name = "BS_AUTO"
    tag_a_build_dir = f"build-tag-ota-{profile_name}-tag{args.tag_a_id}"
    tag_b_build_dir = f"build-tag-ota-{profile_name}-tag{args.tag_b_id}"
    tag_a_master_ota_build_dir = f"build-master-ota-{profile_name}-tag{args.tag_a_id}"
    tag_b_master_ota_build_dir = f"build-master-ota-{profile_name}-tag{args.tag_b_id}"

    tag_results = []
    if not args.skip_build:
        tag_results.append(
            ota_one_tag(
                profile_name=profile_name,
                tag_id=args.tag_a_id,
                tag_sign_version=args.tag_a_sign_version,
                master_snr=args.master_snr,
                master_port=args.master_port,
                ota_capture_duration=args.ota_capture_duration,
                tag_build_dir=tag_a_build_dir,
                master_ota_build_dir=tag_a_master_ota_build_dir,
                tag_device_name=tag_a_name,
            )
        )
        tag_results.append(
            ota_one_tag(
                profile_name=profile_name,
                tag_id=args.tag_b_id,
                tag_sign_version=args.tag_b_sign_version,
                master_snr=args.master_snr,
                master_port=args.master_port,
                ota_capture_duration=args.ota_capture_duration,
                tag_build_dir=tag_b_build_dir,
                master_ota_build_dir=tag_b_master_ota_build_dir,
                tag_device_name=tag_b_name,
            )
        )
    else:
        for tag_id in (args.tag_a_id, args.tag_b_id):
            tag_results.append(
                load_json(eval_root / f"ota_{tag_id}_summary.json")
                if (eval_root / f"ota_{tag_id}_summary.json").exists()
                else {}
            )

    run(
        [
            "./scripts/reset_then_flash.sh",
            args.master_snr,
            str((REPO_ROOT / args.master_ble_hex).resolve()),
        ]
    )

    motion_session_name = f"{profile_name}_motion_ble"
    ref_session_name = f"{profile_name}_ref_guard"

    master_capture_stdout = eval_root / "motion_capture_stdout.log"
    ref_capture_stdout = eval_root / "ref_capture_stdout.log"
    with master_capture_stdout.open("w", encoding="utf-8") as master_out, \
            ref_capture_stdout.open("w", encoding="utf-8") as ref_out:
        master_proc = subprocess.Popen(
            [
                "python3",
                "scripts/capture_master_ble_session.py",
                args.master_snr,
                args.master_port,
                "--duration",
                str(args.eval_duration),
                "--session-name",
                motion_session_name,
                "--no-reset",
            ],
            cwd=REPO_ROOT,
            stdout=master_out,
            stderr=subprocess.STDOUT,
        )
        ref_proc = subprocess.Popen(
            [
                "python3",
                "scripts/capture_tag_session.py",
                args.ref_snr,
                args.ref_port,
                "--duration",
                str(args.eval_duration),
                "--session-name",
                ref_session_name,
                "--skip-sweeps",
                str(args.ref_skip_sweeps),
                "--no-reset",
            ],
            cwd=REPO_ROOT,
            stdout=ref_out,
            stderr=subprocess.STDOUT,
        )

        master_rc = master_proc.wait()
        ref_rc = ref_proc.wait()
    if master_rc != 0 or ref_rc != 0:
        raise SystemExit(f"capture failed: master_rc={master_rc} ref_rc={ref_rc}")

    motion_summary_path = REPO_ROOT / "logs" / "master_ble_sessions" / motion_session_name / "summary.json"
    ref_summary_path = REPO_ROOT / "logs" / "tag_sessions" / ref_session_name / "summary.json"
    motion_summary = load_json(motion_summary_path)
    ref_summary = load_json(ref_summary_path)

    combined = {
        "profile_name": profile_name,
        "tag_a": {
            "tag_id": args.tag_a_id,
            "tag_sign_version": args.tag_a_sign_version,
            "tag_build_dir": tag_a_build_dir,
            "master_ota_build_dir": tag_a_master_ota_build_dir,
            "ota_summary": tag_results[0] if tag_results else {},
        },
        "tag_b": {
            "tag_id": args.tag_b_id,
            "tag_sign_version": args.tag_b_sign_version,
            "tag_build_dir": tag_b_build_dir,
            "master_ota_build_dir": tag_b_master_ota_build_dir,
            "ota_summary": tag_results[1] if len(tag_results) > 1 else {},
        },
        "motion_summary_json": str(motion_summary_path),
        "ref_summary_json": str(ref_summary_path),
        "motion_summary": motion_summary,
        "ref_summary": ref_summary,
        "score": {
            "motion_rms_mean_mm": motion_summary["residual_mean_mm"]["rms"],
            "motion_max_mean_mm": motion_summary["residual_mean_mm"]["max"],
            "motion_std_sum_mm": (
                (motion_summary["position_std_mm"]["x"] or 0.0)
                + (motion_summary["position_std_mm"]["y"] or 0.0)
                + (motion_summary["position_std_mm"]["z"] or 0.0)
            ),
            "ref_std_sum_mm": (
                (ref_summary["position_std_mm"]["x"] or 0.0)
                + (ref_summary["position_std_mm"]["y"] or 0.0)
                + (ref_summary["position_std_mm"]["z"] or 0.0)
            ),
            "ref_rms_mean_mm": ref_summary["residual_mean_mm"]["rms"],
            "ref_unexpected_frames": ref_summary.get("unexpected_frame_events", 0),
            "ref_solve_pending_events": ref_summary.get("solve_pending_events", 0),
        },
    }
    (eval_root / "summary.json").write_text(json.dumps(combined, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(combined, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
