#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def ts() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def run(cmd: list[str], log_path: Path, *, env: dict[str, str] | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w", encoding="utf-8") as logf:
        logf.write("$ " + " ".join(cmd) + "\n")
        logf.flush()
        cp = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=env,
            text=True,
            stdout=logf,
            stderr=subprocess.STDOUT,
            check=False,
        )
        logf.write(f"\n[exit] rc={cp.returncode}\n")
    if check and cp.returncode != 0:
        raise subprocess.CalledProcessError(cp.returncode, cmd)
    return cp


def tag_timing_for_delay(delay_uus: int, rx_lead_uus: int, rx_timeout_uus: int) -> tuple[int, int]:
    tx_to_rx = max(0, delay_uus - rx_lead_uus)
    return tx_to_rx, rx_timeout_uus


def build_tag_profile(delay_uus: int, marker: str, out_dir: Path, args: argparse.Namespace, sign_build_number: int) -> tuple[str, str]:
    tag_tx_to_rx, tag_timeout = tag_timing_for_delay(
        delay_uus, args.tag_rx_lead_uus, args.tag_rx_timeout_uus
    )
    tag_build = f"build-tag-uwb-ota-{marker}"
    master_ota_build = f"build-master-ota-{marker}"
    env = os.environ.copy()
    # The delay sweep intentionally goes 1000 -> 900 -> 800.  MCUboot may
    # reject lower image versions, so keep the sign version monotonic and put
    # the actual timing value in the fw marker.
    env["TAG_SIGN_VERSION"] = f"0.0.3+{sign_build_number}"
    env["TAG_CMAKE_ARGS"] = " ".join(
        [
            "-DAPP_TAG_BLE_ENABLE=1",
            "-DAPP_TAG_BLE_OTA_ENABLE=1",
            "-DAPP_TAG_BLE_SETTINGS_ENABLE=1",
            "-DAPP_TAG_BLE_COMPACT_STATUS=1",
            "-DAPP_TAG_MCUBOOT_ENABLE=1",
            f'-DAPP_TAG_FW_MARKER="{marker}"',
            f"-DAPP_TAG_TX_TO_RX_DLY_UUS={tag_tx_to_rx}",
            f"-DAPP_TAG_RESP_RX_TIMEOUT_UUS={tag_timeout}",
            "-DAPP_TAG_CALIBRATION_MODE=0",
            "-DAPP_TAG_TDMA_ENABLE=0",
            "-DAPP_TAG_FIXED_MODE=0",
            "-DAPP_TAG_FAST_TRACKING=1",
            "-DAPP_TAG_TRACK_ANCHOR_COUNT=8",
            "-DAPP_TAG_FULL_SWEEP_INTERVAL=1",
            "-DAPP_TAG_EKF_ENABLE=0",
            "-DAPP_TAG_RANGE_CONTINUITY_ENABLE=0",
            "-DAPP_TAG_VERBOSE_RANGING=0",
            "-DAPP_TAG_VERBOSE_MEASUREMENTS=0",
            "-DAPP_TAG_VERBOSE_PERF=0",
            "-DAPP_TAG_USB_MIRROR_BLE_STATUS=0",
        ]
    )
    run(
        ["scripts/build_uwb_tag_ota_test.sh", tag_build, master_ota_build],
        out_dir / "build_tag_ota.log",
        env=env,
    )
    return tag_build, master_ota_build


def build_anchor_profile(delay_uus: int, marker: str, out_dir: Path, sign_build_number: int) -> str:
    anchor_marker = f"anchor-{marker}"
    anchor_build = f"build-anchor-unified-ota-{anchor_marker}"
    control_build = f"build-master-control-anchor-ota-{anchor_marker}"
    env = os.environ.copy()
    env["ANCHOR_EXTRA_CMAKE_ARGS"] = " ".join(
        [
            f"-DAPP_ANCHOR_RESP_DELAY_UUS={delay_uus}",
            f"-DCONFIG_MCUBOOT_IMGTOOL_SIGN_VERSION=\"0.0.3+{sign_build_number}\"",
            "-DAPP_ANCHOR_RESPONDER_ADV_INT_MIN_MS=5000",
            "-DAPP_ANCHOR_RESPONDER_ADV_INT_MAX_MS=10000",
            "-DAPP_ANCHOR_RESPONDER_DIAG_PERIOD_MS=5000",
            "-DAPP_ANCHOR_VERBOSE_RESPONDER=0",
            "-DAPP_ANCHOR_VERBOSE_RESPONDER_ERRORS=0",
            "-DAPP_ANCHOR_RESPONDER_PRINTK_ENABLE=0",
            "-DAPP_ANCHOR_RESPONDER_PROFILE_ENABLE=1",
        ]
    )
    run(
        ["scripts/build_anchor_ota_control_bundle.sh", anchor_build, control_build, anchor_marker],
        out_dir / "build_anchor_ota.log",
        env=env,
    )
    return anchor_build


def flash_b120(build_dir: str, out_dir: Path) -> None:
    env = os.environ.copy()
    env["B120_SNR"] = env.get("B120_SNR", "960148546")
    env["BIOSPUR_FLASH_PREFER_NRFJPROG"] = "0"
    run(
        ["scripts/flash_master_control_b120_m1_noninteractive.sh", f"{build_dir}/zephyr/merged_domains.hex"],
        out_dir / f"flash_b120_{Path(build_dir).name}.log",
        env=env,
    )


def build_flash_b120(marker: str, out_dir: Path) -> str:
    build_dir = f"build-master-control-b120-m1-{marker}"
    run(["scripts/build_master_control_b120_m1.sh", build_dir], out_dir / "build_b120.log")
    flash_b120(build_dir, out_dir)
    return build_dir


def flash_anchors(anchor_build: str, out_dir: Path) -> None:
    env = os.environ.copy()
    env["BIOSPUR_FLASH_PREFER_NRFJPROG"] = "0"
    run(["scripts/flash_all_anchors.sh", anchor_build], out_dir / "flash_anchors.log", env=env)


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def deploy_tags(marker: str, out_dir: Path, args: argparse.Namespace) -> dict[str, Any]:
    ota_dir = out_dir / "ota_tags"
    cp = run(
        [
            sys.executable,
            "scripts/ota_deploy_tag_set.py",
            "--port", args.port,
            "--targets", args.targets,
            "--out-dir", str(ota_dir),
            "--timeout-s", str(args.ota_timeout_s),
            "--expected-fw-marker", marker,
        ],
        out_dir / "ota_tags_driver.log",
        check=False,
    )
    deploy_summary = load_json(ota_dir / "deploy_summary.json")
    targets = [x.strip() for x in args.targets.split(",") if x.strip()]
    rounds = deploy_summary.get("rounds", {}) if isinstance(deploy_summary, dict) else {}
    failed_targets: list[str] = []
    for target in targets:
        target_summary = rounds.get(target, {})
        single = target_summary.get("summary", {}) if isinstance(target_summary, dict) else {}
        if target_summary.get("returncode", 1) != 0 or not single.get("ota_success_seen", False):
            failed_targets.append(target)
    post_versions = {
        target: rounds.get(target, {}).get("post_version", {})
        for target in targets
        if isinstance(rounds.get(target, {}), dict)
    }
    version_mismatches = [
        target
        for target, version in post_versions.items()
        if version and version.get("match") is False
    ]
    result = {
        "returncode": cp.returncode,
        "summary_path": str(ota_dir / "deploy_summary.json"),
        "failed_targets": failed_targets,
        "version_mismatches": version_mismatches,
        "post_versions": post_versions,
    }
    if failed_targets:
        raise subprocess.CalledProcessError(cp.returncode or 1, [
            sys.executable,
            "scripts/ota_deploy_tag_set.py",
            "--targets",
            args.targets,
        ])
    if cp.returncode != 0:
        # ota_deploy_tag_set returns rc=3 when post-version query misses a
        # target. If every single-shot OTA stage succeeded, keep profiling and
        # record the verify gap instead of throwing away the whole delay point.
        result["warning"] = "tag_ota_post_version_verify_incomplete"
    return result


def capture(delay_uus: int, out_dir: Path, args: argparse.Namespace) -> subprocess.CompletedProcess[str]:
    return run(
        [
            sys.executable,
            "scripts/run_recv_tdma_capture.py",
            "--port", args.port,
            "--duration", str(args.duration),
            "--targets", args.targets,
            "--profiles", args.profiles,
            "--static-hz", str(args.static_hz),
            "--roto-hz", str(args.roto_hz),
            "--motion-hz", str(args.motion_hz),
            "--cm-probe-target", args.cm_probe_target,
            "--out-dir", str(out_dir / "capture"),
        ],
        out_dir / "capture_driver.log",
        check=False,
    )


def latest_capture_summary(out_dir: Path) -> dict[str, Any]:
    sessions = sorted(out_dir.glob("capture_*"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        return {}
    summary = sessions[-1] / "summary.json"
    if not summary.exists():
        return {"session_dir": str(sessions[-1])}
    data = json.loads(summary.read_text(encoding="utf-8"))
    data["session_dir"] = str(sessions[-1])
    return data


def run_delay(delay_uus: int, args: argparse.Namespace, base: Path, index: int) -> dict[str, Any]:
    tag_tx_to_rx, tag_timeout = tag_timing_for_delay(
        delay_uus, args.tag_rx_lead_uus, args.tag_rx_timeout_uus
    )
    marker = f"joint-resp{delay_uus}-tagrx{tag_tx_to_rx}to{tag_timeout}-{ts()}"
    out_dir = base / f"resp{delay_uus}_{ts()}"
    out_dir.mkdir(parents=True, exist_ok=True)
    row: dict[str, Any] = {
        "delay_uus": delay_uus,
        "marker": marker,
        "tag_tx_to_rx_uus": tag_tx_to_rx,
        "tag_rx_timeout_uus": tag_timeout,
        "out_dir": str(out_dir),
    }
    sign_build_number = args.sign_base + index
    row["sign_build_number"] = sign_build_number
    tag_build, master_ota_build = build_tag_profile(delay_uus, marker, out_dir, args, sign_build_number)
    row["tag_build"] = tag_build
    row["master_ota_build"] = master_ota_build
    b120_tag = build_flash_b120(f"tag-{marker}", out_dir / "b120_tag")
    row["b120_tag_build"] = b120_tag
    row["tag_ota"] = deploy_tags(marker, out_dir, args)
    anchor_build = build_anchor_profile(delay_uus, marker, out_dir, sign_build_number)
    row["anchor_build"] = anchor_build
    flash_anchors(anchor_build, out_dir)
    b120_anchor = build_flash_b120(f"anchor-{marker}", out_dir / "b120_anchor")
    row["b120_anchor_build"] = b120_anchor
    cp = capture(delay_uus, out_dir, args)
    row["capture_returncode"] = cp.returncode
    row["capture_summary"] = latest_capture_summary(out_dir)
    return row


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Joint anchor+tag response-delay profile test")
    p.add_argument("--port", default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00")
    p.add_argument("--delays", default="1000,900,800")
    p.add_argument("--tag-rx-lead-uus", type=int, default=350)
    p.add_argument("--tag-rx-timeout-uus", type=int, default=900)
    p.add_argument("--targets", default="BSF66F,BS2DCE,BSDC91")
    p.add_argument("--profiles", default="BSF66F:static,BS2DCE:roto,BSDC91:roto")
    p.add_argument("--static-hz", type=int, default=5)
    p.add_argument("--roto-hz", type=int, default=10)
    p.add_argument("--motion-hz", type=int, default=5)
    p.add_argument("--cm-probe-target", default="BSF66F")
    p.add_argument("--duration", type=int, default=180)
    p.add_argument("--ota-timeout-s", type=int, default=420)
    p.add_argument("--sign-base", type=int, default=int(time.time()) % 1000000)
    p.add_argument("--out-dir", default=None)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    base = Path(args.out_dir or f"logs/joint_resp_delay_profile_{ts()}")
    base.mkdir(parents=True, exist_ok=True)
    delays = [int(x.strip()) for x in args.delays.split(",") if x.strip()]
    rows: list[dict[str, Any]] = []
    for index, delay in enumerate(delays):
        row: dict[str, Any] = {"delay_uus": delay, "status": "running"}
        rows.append(row)
        (base / "summary.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
        try:
            row.update(run_delay(delay, args, base, index))
            row["status"] = "ok" if row.get("capture_returncode") == 0 else f"capture_rc_{row.get('capture_returncode')}"
        except subprocess.CalledProcessError as exc:
            row["status"] = "fail"
            row["error"] = f"rc={exc.returncode} cmd={' '.join(exc.cmd)}"
        except Exception as exc:
            row["status"] = "fail"
            row["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            row["finished_at"] = datetime.now().isoformat(timespec="seconds")
            (base / "summary.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(base)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
