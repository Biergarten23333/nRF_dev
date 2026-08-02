#!/usr/bin/env python3
"""One no-retry v31->v32 B306 OTA transaction through DK SNR 683234364."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "B306_Part" / "tools"
SNR = "683234364"
DEVICE = "NRF52840_XXAA"
SOURCE_MARKER = "b306-imu-relay-v31"
UPDATER_SHA = {
    "BSFC2CC": "57d21879fdd7767a8a6265b202b5c195236f564d4e852b4f46a67714ab5e4330",
    "BSF44AD": "fd4b9006832bdd219e7c23a40281fbee3f8259c77bbdd23a796e40882e094f70",
    "BSF6C53": "7227f7016495957c3c9953a3f9ec5717df946dbab652d908b2c02be9d80efe7a",
    "BSF1120": "869c7475bf13446b4cabe100fa1bab71556c781b583180c605b51ad08ed727ca",
    "BSF31CC": "d444f28b1daf11f67e1d82012b68c57823b7b853bf12ba9847e6291c012a0ba6",
    "BSFAA61": "99bf708d33032d3a969773ce37d8f8373b2c9474bd23aba95757fbe45ba7d4f5",
    "BSFEC35": "5633793fe39de994b6a27071eb0f8ccbab83b31c8cdb54e1ad5f35ce27160903",
    "BSFB165": "946d53b5ed0ea3e4bd8639c2f0e78ad610b9a56104b938b4de6ad36181564b69",
}
V28_MERGED_SHA = "abb24e44ec010fb25e7945ba31fa90dbaab90b24379b2e3c74fbc3256ac8dd3b"
V28_BIN_SHA = "110dcbe5c8580d060f9b89e4d63d06d4e0ed28cced73a83397c23155dc07a97f"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def run_logged(
    command: list[str], log_path: Path, *, env: dict[str, str] | None = None,
    check: bool = True,
) -> int:
    print(f"RUN {command[0]} step_log={log_path}", flush=True)
    with log_path.open("xb") as log:
        completed = subprocess.run(
            command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
            check=False,
        )
    if check and completed.returncode != 0:
        raise RuntimeError(f"command failed rc={completed.returncode}: {command}; log={log_path}")
    return completed.returncode


def jlink_script(path: Path, merged: Path, verify_bin: Path) -> None:
    path.write_text(
        "r\nh\nerase\n"
        f"loadfile {merged}\n"
        f"verifybin {verify_bin},0x00000000\n"
        "r\ng\nq\n",
        encoding="utf-8",
    )


def flash(script: Path, log: Path) -> None:
    run_logged([
        "/usr/bin/JLinkExe", "-NoGui", "1", "-SelectEmuBySN", SNR,
        "-Device", DEVICE, "-If", "SWD", "-Speed", "4000",
        "-CommanderScript", str(script),
    ], log)


def classify_capture_result(returncode: int, console: str) -> str:
    if returncode == 0:
        return "MARKERS_COMPLETE_EARLY_EXIT"
    if "RTT required marker(s) missing before timeout" in console:
        return "EVIDENCE_GAP_MARKERS_MISSING_60S_CONTINUE_TO_CONFIRM"
    raise RuntimeError(f"updater RTT explicit failure rc={returncode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=tuple(UPDATER_SHA))
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--deployment-only",
        action="store_true",
        help=(
            "stop after v32 PONG plus PREPARE/COMMIT confirmed=1; "
            "do not run F4, SPACING, redraw, sanity, or IMU commands"
        ),
    )
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    state: dict[str, object] = {
        "status": "IN_PROGRESS",
        "node": args.node,
        "snr": SNR,
        "deployment_only": args.deployment_only,
    }
    restored = False
    updater_flushed = False
    env = dict(os.environ)
    env["PYTHONPATH"] = str(TOOLS)

    build = ROOT / "B306_Part" / "builds" / f"dk-ota-b306-v32-retry1-{args.node}"
    updater_merged = build / "merged.hex"
    updater_bin = build / "dk_ota" / "zephyr" / "zephyr.bin"
    v28 = ROOT / "B306_Part" / "builds" / "dk-fusion-imu-relay-v28"
    v28_merged = v28 / "merged.hex"
    v28_bin = v28 / "fusion_master" / "zephyr" / "zephyr.bin"
    try:
        hashes = {
            "updater_merged": sha256(updater_merged),
            "v28_merged": sha256(v28_merged),
            "v28_bin": sha256(v28_bin),
        }
        state["hashes"] = hashes
        if hashes != {
            "updater_merged": UPDATER_SHA[args.node],
            "v28_merged": V28_MERGED_SHA,
            "v28_bin": V28_BIN_SHA,
        }:
            raise RuntimeError(f"artifact hash gate failed: {hashes}")

        run_logged([
            sys.executable, str(TOOLS / "v32_ota_target_preflight.py"),
            "--node", args.node, "--expected-marker", SOURCE_MARKER,
            "--out-dir", str(args.out_dir / "preflight"),
        ], args.out_dir / "preflight_console.log", env=env)

        updater_script = args.out_dir / f"flash_updater_{SNR}.jlink"
        restore_script = args.out_dir / f"restore_v28_{SNR}.jlink"
        jlink_script(updater_script, updater_merged, updater_bin)
        jlink_script(restore_script, v28_merged, v28_bin)
        flash(updater_script, args.out_dir / "flash_updater_jlink.log")
        updater_flushed = True

        # This is evidence collection, never a prerequisite that may consume
        # the application's 180-second confirmation deadline.  Exit as soon
        # as both markers arrive; otherwise cap the evidence window at 60 s.
        # A missing-marker timeout is recorded and execution still restores
        # v28 and performs application confirmation. Explicit failure markers
        # remain fatal.
        capture_console = args.out_dir / "updater_rtt_console.log"
        capture_rc = run_logged([
            sys.executable, str(TOOLS / "capture_jlink_rtt.py"),
            "--serial-number", SNR, "--device", DEVICE,
            "--address", "0x20002010", "--duration-s", "60",
            "--until-text", "OTA_ACTION:handoff_app_roundtrip_confirm",
            "--until-text", "OTA_STATE:post_verify_passed",
            "--fail-text", "OTA_STATE:post_verify_failed",
            "--fail-text", "OTA upload failed",
            "--post-match-s", "0.25",
            "--output", str(args.out_dir / "updater_rtt.log"),
        ], capture_console, env=env, check=False)
        console = capture_console.read_text(encoding="utf-8", errors="replace")
        try:
            state["updater_capture"] = classify_capture_result(capture_rc, console)
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}; log={capture_console}") from exc

        flash(restore_script, args.out_dir / "restore_v28_jlink.log")
        restored = True
        # The updater negotiated a 20-second supervision timeout. Let the B306
        # observe the old central disappearing before v28 is asked to connect.
        time.sleep(25.0)
        run_logged([
            sys.executable, str(TOOLS / "confirm_b306_v32.py"),
            "--node", args.node, "--out-dir", str(args.out_dir / "app_confirm"),
        ], args.out_dir / "app_confirm_console.log", env=env)
        if args.deployment_only:
            confirm_result = json.loads(
                (args.out_dir / "app_confirm" / "result.json").read_text(
                    encoding="utf-8"
                )
            )
            ping_text = str(confirm_result.get("ping", {}).get("text", ""))
            after_text = str(confirm_result.get("after", {}).get("text", ""))
            if (
                confirm_result.get("status") != "PASS"
                or f"name={args.node}" not in ping_text
                or "fw=b306-imu-relay-v32" not in ping_text
                or "confirmed=1" not in after_text
            ):
                raise RuntimeError(
                    "deployment-only readback contract failed: "
                    f"status={confirm_result.get('status')} "
                    f"ping={ping_text!r} after={after_text!r}"
                )
            state["deployment_readback"] = {
                "pong": ping_text,
                "confirm": after_text,
                "verdict": "PASS",
            }
            state["deferred_by_operator"] = [
                "F4 service gate",
                "SPACING rebuild",
                "redraw",
                "120-second sanity",
                "IMU commands",
            ]
            state["status"] = "PASS"
            print(f"{args.node} COMPLETE", flush=True)
            return 0
        # F4 thresholds are meaningful only after the production central
        # schedule has been rebuilt.  Prove ON/5000 at the current generation,
        # then reject/redraw a bad link before the sanity window can measure it.
        run_logged([
            sys.executable, str(TOOLS / "v32_per_board_service_gate.py"),
            "--node", args.node,
            "--max-redraws", "3",
            "--out-dir", str(args.out_dir / "service_gate_on5000"),
        ], args.out_dir / "service_gate_console.log", env=env)
        run_logged([
            sys.executable, str(TOOLS / "v32_batch_board_sanity.py"),
            "--node", args.node, "--duration-s", "120",
            "--out-dir", str(args.out_dir / "sanity_120s"),
        ], args.out_dir / "sanity_console.log", env=env)
        state["status"] = "PASS"
        print(f"{args.node} COMPLETE", flush=True)
        return 0
    except Exception as exc:
        state["status"] = "FAIL"
        state["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        if updater_flushed and not restored:
            # Master rollback only. This never retries a B306 write operation.
            try:
                restore_script = args.out_dir / f"restore_v28_{SNR}.jlink"
                if not restore_script.exists():
                    jlink_script(restore_script, v28_merged, v28_bin)
                flash(restore_script, args.out_dir / "emergency_restore_v28_jlink.log")
                state["emergency_master_restore"] = "PASS"
            except Exception as restore_exc:
                state["emergency_master_restore"] = f"FAIL: {restore_exc}"
        (args.out_dir / "transaction.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
