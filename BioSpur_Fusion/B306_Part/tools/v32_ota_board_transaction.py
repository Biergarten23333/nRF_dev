#!/usr/bin/env python3
"""One no-retry B306 OTA transaction through DK SNR 683234364."""

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
FLEET_NODES = {
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
}
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
# V34C campaign: max measured successful upload was 21.437 s.  The updater
# may spend 15 s before starting, and its initial-scan and post-reset
# reacquisition operations each have a 180 s inner deadline.  One additional
# measured-max upload interval is the evidence-tail margin.
V34C_CAPTURE_BOUND_S = 15.0 + 180.0 + 21.437 + 180.0 + 21.437


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


def classify_capture_result(returncode: int, console: str, timeout_s: float) -> str:
    if returncode == 0:
        return "MARKERS_COMPLETE_EARLY_EXIT"
    if "RTT required marker(s) missing before timeout" in console:
        return f"EVIDENCE_GAP_MARKERS_MISSING_{timeout_s:.3f}S_CONTINUE_TO_CONFIRM"
    raise RuntimeError(f"updater RTT explicit failure rc={returncode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--node", required=True,
        choices=("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
                 "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35"),
    )
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument(
        "--deployment-only",
        action="store_true",
        help=(
            "stop after v32 PONG plus PREPARE/COMMIT confirmed=1; "
            "do not run F4, SPACING, redraw, sanity, or IMU commands"
        ),
    )
    parser.add_argument("--source-marker", default=SOURCE_MARKER)
    parser.add_argument("--target-marker", default="b306-imu-relay-v32")
    parser.add_argument("--build-prefix", default="dk-ota-b306-v32-retry1-")
    parser.add_argument("--updater-sha")
    parser.add_argument("--confirm-tool", default="confirm_b306_v32.py")
    parser.add_argument("--master-marker", default="dk-fusion-imu-relay-v28")
    parser.add_argument("--restore-build", default="dk-fusion-imu-relay-v28")
    parser.add_argument("--restore-merged-sha", default=V28_MERGED_SHA)
    parser.add_argument("--restore-bin-sha", default=V28_BIN_SHA)
    parser.add_argument(
        "--skip-preflight", action="store_true",
        help="use a separately archived fleet inventory instead of the legacy idle gate",
    )
    parser.add_argument(
        "--fleet-preflight-result", type=Path,
        help="PASS result.json proving end-to-end PING for every intended fleet target",
    )
    parser.add_argument(
        "--preflight-require", choices=("fleet", "target-only"), default="fleet",
        help=(
            "'fleet' (default, unchanged) demands an end-to-end PING from every "
            "fleet node before this one target may proceed. 'target-only' demands "
            "an end-to-end PING from THIS target and records the rest as "
            "inventory. Trap 6.3: a per-target operation must never wait on a "
            "fleet-wide condition — one quarantined board must not be able to "
            "block, or roll back, every other board's image."
        ),
    )
    parser.add_argument(
        "--capture-timeout-s", type=float, default=V34C_CAPTURE_BOUND_S,
        help="outer RTT evidence bound; must exceed all wrapped updater deadlines",
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

    build = ROOT / "B306_Part" / "builds" / f"{args.build_prefix}{args.node}"
    updater_merged = build / "merged.hex"
    updater_bin = build / "dk_ota" / "zephyr" / "zephyr.bin"
    restore_build = ROOT / "B306_Part" / "builds" / args.restore_build
    v28_merged = restore_build / "merged.hex"
    v28_bin = restore_build / "fusion_master" / "zephyr" / "zephyr.bin"
    try:
        hashes = {
            "updater_merged": sha256(updater_merged),
            "v28_merged": sha256(v28_merged),
            "v28_bin": sha256(v28_bin),
        }
        state["hashes"] = hashes
        expected_updater_sha = args.updater_sha or UPDATER_SHA[args.node]
        if hashes != {
            "updater_merged": expected_updater_sha,
            "v28_merged": args.restore_merged_sha,
            "v28_bin": args.restore_bin_sha,
        }:
            raise RuntimeError(f"artifact hash gate failed: {hashes}")

        if args.skip_preflight:
            if args.fleet_preflight_result is None:
                raise RuntimeError(
                    "--skip-preflight requires --fleet-preflight-result"
                )
            preflight = json.loads(args.fleet_preflight_result.read_text(
                encoding="utf-8"
            ))
            nodes = preflight.get("nodes", {})
            responders = {
                node for node, row in nodes.items()
                if isinstance(row, dict)
                and str(row.get("ping", {}).get("text", "")).startswith(
                    f"PONG name={node} "
                )
            }
            if args.preflight_require == "target-only":
                # This target's own end-to-end PING is the gate. Other nodes are
                # inventory, never a precondition (trap 6.3).
                if args.node not in responders:
                    raise RuntimeError(
                        "preflight lacks an end-to-end PING from this target: "
                        f"node={args.node} responders={sorted(responders)}"
                    )
                state["preflight"] = {
                    "mode": "TARGET_ONLY_END_TO_END_PING",
                    "result": str(args.fleet_preflight_result),
                    "target_responded": True,
                    "fleet_inventory": sorted(responders),
                    "fleet_missing": sorted(FLEET_NODES - responders),
                }
            else:
                if preflight.get("status") != "PASS" or responders != FLEET_NODES:
                    raise RuntimeError(
                        "fleet preflight lacks end-to-end PING from every target: "
                        f"status={preflight.get('status')} responders={sorted(responders)}"
                    )
                state["preflight"] = {
                    "mode": "SEPARATE_END_TO_END_FLEET_PING",
                    "result": str(args.fleet_preflight_result),
                    "responders": sorted(responders),
                }
        else:
            run_logged([
                sys.executable, str(TOOLS / "v32_ota_target_preflight.py"),
                "--node", args.node, "--expected-marker", args.source_marker,
                "--expected-master-marker", args.master_marker,
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
        # as both markers arrive; otherwise use a bound longer than every
        # nested updater operation.  Never shorten this below the updater's
        # own scan/reacquisition deadlines.
        # A missing-marker timeout is recorded and execution still restores
        # v28 and performs application confirmation. Explicit failure markers
        # remain fatal.
        capture_console = args.out_dir / "updater_rtt_console.log"
        capture_rc = run_logged([
            sys.executable, str(TOOLS / "capture_jlink_rtt.py"),
            "--serial-number", SNR, "--device", DEVICE,
            "--address", "0x20002010", "--duration-s", str(args.capture_timeout_s),
            "--until-text", "OTA_ACTION:handoff_app_roundtrip_confirm",
            "--until-text", "OTA_STATE:post_verify_passed",
            "--fail-text", "OTA_STATE:post_verify_failed",
            "--fail-text", "OTA upload failed",
            "--post-match-s", "0.25",
            "--output", str(args.out_dir / "updater_rtt.log"),
        ], capture_console, env=env, check=False)
        console = capture_console.read_text(encoding="utf-8", errors="replace")
        try:
            state["updater_capture"] = classify_capture_result(
                capture_rc, console, args.capture_timeout_s
            )
        except RuntimeError as exc:
            raise RuntimeError(f"{exc}; log={capture_console}") from exc

        flash(restore_script, args.out_dir / "restore_v28_jlink.log")
        restored = True
        # The updater negotiated a 20-second supervision timeout. Let the B306
        # observe the old central disappearing before v28 is asked to connect.
        time.sleep(25.0)
        run_logged([
            sys.executable, str(TOOLS / args.confirm_tool),
            "--node", args.node, "--out-dir", str(args.out_dir / "app_confirm"),
            "--target-only",
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
                or f"fw={args.target_marker}" not in ping_text
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
