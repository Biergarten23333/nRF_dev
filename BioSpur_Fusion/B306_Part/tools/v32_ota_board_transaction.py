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

from ota_updater_handoff import capture_updater_terminal


ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "B306_Part" / "tools"
SNR = "683234364"
DEVICE = "NRF52840_XXAA"
FLEET_NODES = {
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
}
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


def read_dk_marker(timeout_s: float = 20.0):
    """The marker the DK is running RIGHT NOW, from its own CDC.

    Read live rather than inferred from a build directory: the failure this
    guards against is a command line that has drifted from the rig, and
    inferring it from the argument under test would be a checker answering its
    own question.

    USES THE PROJECT'S CHANNEL, NOT A RAW readline(). The Fusion Master CDC
    streams the BINARY data plane by default -- a naive line read returns
    thousands of binary records and never sees FUSION_MASTER_STATUS, which is
    exactly how the first version of this failed. decode_guard() puts the
    channel into the mode where the text status is emitted.
    """
    try:
        from async_line_channel import ThreadedLineChannel
        from coldstart_fusion_control import decode_guard
        from confirm_b306_v32 import wait_master_status
        from fusion_session import resolve_fusion_port
        import re

        import tempfile
        port = resolve_fusion_port(None)
        channel = None
        with tempfile.NamedTemporaryFile("w", suffix=".log", delete=False) as log:
            try:
                channel = ThreadedLineChannel(
                    port, log, "RESTORE_GATE",
                    decoded_queue_records=65536, backlog_red_records=8192,
                    raw_backlog_red_bytes=8192, stall_red_s=1.0,
                )
                channel.transport_mode = "binary"
                channel.text_pending.clear()
                decode_guard(channel, 15.0)
                status = wait_master_status(channel)
                m = re.search(r"marker=(\S+)", str(status))
                return m.group(1) if m else None
            finally:
                if channel is not None:
                    channel.close()
    except Exception as exc:
        print(f"read_dk_marker failed: {exc}", flush=True)
        return None


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



def restore_master(restore_script, flash_log, out_dir, state, tag):
    """Flash the DK back to canonical; do no schedule work here."""
    flash(restore_script, flash_log)
    deadline = time.monotonic() + 30.0
    observations = []
    while time.monotonic() < deadline:
        marker = read_dk_marker(timeout_s=5.0)
        observations.append({"monotonic": time.monotonic(), "marker": marker})
        if marker == "dk-fusion-imu-relay-v36":
            state[f"master_ready_{tag}"] = observations
            return
        time.sleep(0.25)
    state[f"master_ready_{tag}"] = observations
    raise RuntimeError("restored v36 image but production Master did not enumerate")


def rebuild_spacing_after_confirm(out_dir, state, tag):
    """Optional schedule work, permitted only after TARGET_CONFIRMED."""
    try:
        from fusion_spacing import ensure_spacing
        r = ensure_spacing(out_dir / f"spacing_{tag}", timeout_s=120.0)
        state[f"spacing_{tag}"] = {
            "status": r.get("status"), "action": r.get("action"),
            "expected_us": r.get("expected_us"),
            "after": (r.get("after") or {}).get("raw"),
            "error": r.get("error"),
        }
        if r.get("status") != "PASS":
            print(f"WARNING: spacing not rebuilt after {tag} restore: "
                  f"{r.get('error')}", flush=True)
    except Exception as exc:  # noqa: BLE001
        state[f"spacing_{tag}"] = {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}
        print(f"WARNING: spacing rebuild raised after {tag} restore: {exc}", flush=True)


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
    parser.add_argument("--source-marker", required=True)
    parser.add_argument("--target-marker", required=True)
    parser.add_argument("--identity-manifest", required=True, type=Path)
    parser.add_argument("--source-identity-manifest", type=Path)
    parser.add_argument("--confirmation-deadline-s", required=True, type=float)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--reserved-post-updater-s", type=float,
                        default=61.193245916)
    parser.add_argument("--build-prefix", required=True)
    parser.add_argument("--updater-sha", required=True)
    parser.add_argument("--confirm-tool", default="confirm_b306_v32.py")
    parser.add_argument("--master-marker", required=True)
    #
    # NO DEFAULT. This is the ninth "checker answering a different question"
    # in this project.
    #
    # The default was `dk-fusion-imu-relay-v28`, two generations behind the
    # rig's live image. SNR 683234364 is not a spare -- it IS the Fusion
    # Master, currently running v36. The preflight checks the MASTER MARKER on
    # the CDC and passes; it never checks what the restore is about to write.
    # So the transaction would pass preflight, OTA the B306 correctly, then
    # quietly flash the live master back two generations and report success.
    # The verification it performs (`--restore-merged-sha`) would also pass,
    # because it verifies against the very v28 hashes it was told to expect.
    #
    # Required and explicit, or the run is refused.
    parser.add_argument("--restore-build", required=True,
                        help="canonical DK image to restore. REQUIRED -- there "
                             "is deliberately no default; see the note in the "
                             "source. Must match the marker the DK is running.")
    parser.add_argument("--restore-merged-sha", required=True)
    parser.add_argument("--restore-bin-sha", required=True)
    parser.add_argument("--restore-marker", required=True,
                        help="marker string expected inside the restore image "
                             "AND currently reported by the DK. Both are "
                             "checked before the updater is flashed.")
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
    args = parser.parse_args()
    if not 0 < args.confirmation_deadline_s <= 180.0:
        parser.error("--confirmation-deadline-s must be positive and at most 180 s")
    if not 0 < args.reserved_post_updater_s < args.confirmation_deadline_s:
        parser.error("invalid reserved post-updater budget")
    args.out_dir.mkdir(parents=True, exist_ok=False)
    state: dict[str, object] = {
        "status": "IN_PROGRESS",
        "node": args.node,
        "snr": SNR,
        "deployment_only": args.deployment_only,
        "run_id": args.run_id,
    }
    restored = False
    updater_flushed = False
    rescue_attempted = False
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
        expected_updater_sha = args.updater_sha
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
            if not nodes and preflight.get("status") == "INVENTORY_PASS":
                samples = preflight.get("inventory_samples") or []
                if not samples or not all(row.get("ok") is True for row in samples):
                    raise RuntimeError("inventory preflight lacks a complete stable gate")
                nodes = samples[-1].get("nodes", {})
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
                if preflight.get("status") not in {"PASS", "INVENTORY_PASS"} or responders != FLEET_NODES:
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

        # ------------------------------------------------------------------
        # RESTORE GATE. Runs BEFORE the updater is flashed, because after that
        # the DK no longer carries the image we are checking.
        #
        # Two independent checks, because either alone is a way to be wrong:
        #   1. the restore IMAGE contains the marker we were told to expect
        #      -- catches "--restore-build points somewhere stale";
        #   2. the DK is CURRENTLY REPORTING that same marker -- catches
        #      "the rig moved on and nobody updated the command line".
        # ------------------------------------------------------------------
        # Checked against the RAW BIN, not merged.hex: the latter is Intel HEX
        # text, so an ASCII marker can never appear in it and this gate would
        # reject every correct image. (Caught on first use, by the runbook rule
        # that a new checker is hand-verified against real data once.)
        want = args.restore_marker.encode()
        if want not in Path(v28_bin).read_bytes():
            raise RuntimeError(
                f"restore image {v28_bin} does not contain marker "
                f"{args.restore_marker!r}; refusing to flash an image whose "
                "identity cannot be confirmed")

        live = read_dk_marker()
        if live is None:
            raise RuntimeError(
                "could not read the DK's current marker; refusing to restore "
                "an image that cannot be compared against the live one")
        if live != args.restore_marker:
            raise RuntimeError(
                f"DK is running {live!r} but the restore image is "
                f"{args.restore_marker!r}. Restoring would change the DK's "
                "generation as a side effect of a B306 OTA. Refusing.")
        state["restore_gate"] = {"dk_marker": live,
                                 "restore_marker": args.restore_marker,
                                 "image": str(v28_merged)}
        print(f"RESTORE GATE ok: image and DK both {live}", flush=True)

        updater_script = args.out_dir / f"flash_updater_{SNR}.jlink"
        restore_script = args.out_dir / f"restore_{SNR}.jlink"
        jlink_script(updater_script, updater_merged, updater_bin)
        jlink_script(restore_script, v28_merged, v28_bin)
        # Conservative T0: timestamp before the updater is released so
        # even a target reset during J-Link process teardown is inside budget.
        # One absolute deadline covers restore, CDC,
        # routing, identity, and confirmation; the confirmer cannot restart it.
        state["critical_t0_monotonic"] = time.monotonic()
        state["absolute_confirm_deadline"] = (
            state["critical_t0_monotonic"] + args.confirmation_deadline_s)
        state["updater_cutoff"] = (state["absolute_confirm_deadline"]
                                   - args.reserved_post_updater_s)
        state["reserved_post_updater_s"] = args.reserved_post_updater_s
        flash(updater_script, args.out_dir / "flash_updater_jlink.log")
        updater_flushed = True

        identity = json.loads(args.identity_manifest.read_text(encoding="utf-8"))
        state["updater_capture"] = capture_updater_terminal(
            run_id=args.run_id, node=args.node,
            expected_image_sha=str(identity["mcuboot_image_sha256"]),
            updater_cutoff=state["updater_cutoff"],
            raw_path=args.out_dir / "updater_raw_rtt.bin",
            parsed_path=args.out_dir / "updater_stages.json",
        )

        restore_master(restore_script,
                       args.out_dir / "restore_v28_jlink.log",
                       args.out_dir, state, "restore")
        restored = True
        state["production_master_restored"] = True
        rescue_attempted = True
        run_logged([
            sys.executable, str(TOOLS / args.confirm_tool),
            "--node", args.node, "--out-dir", str(args.out_dir / "app_confirm"),
            "--identity-manifest", str(args.identity_manifest),
            "--expected-master-marker", args.master_marker,
            "--absolute-deadline", str(state["absolute_confirm_deadline"]),
            "--run-id", args.run_id,
            *(["--source-identity-manifest", str(args.source_identity_manifest)]
              if args.source_identity_manifest else []),
        ], args.out_dir / "app_confirm_console.log", env=env)
        confirm_result = json.loads(
            (args.out_dir / "app_confirm" / "result.json").read_text(
                encoding="utf-8"))
        samples = confirm_result.get("samples", [])
        if (confirm_result.get("status") != "PASS"
                or confirm_result.get("board_state") != "TARGET_CONFIRMED"
                or not samples):
            raise RuntimeError(
                "durable readback contract failed: "
                f"status={confirm_result.get('status')} "
                f"state={confirm_result.get('board_state')!r}")
        rebuild_spacing_after_confirm(args.out_dir, state, "post_confirm")
        if args.deployment_only:
            state["deployment_readback"] = {
                "samples": samples,
                "payload_sha256": confirm_result.get("expected_payload_sha256"),
                "verdict": "PASS",
            }
            state["deferred_by_operator"] = [
                "F4 service gate",
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
                restore_script = args.out_dir / f"restore_{SNR}.jlink"
                if not restore_script.exists():
                    jlink_script(restore_script, v28_merged, v28_bin)
                restore_master(restore_script,
                               args.out_dir / "emergency_restore_v28_jlink.log",
                               args.out_dir, state, "emergency_restore")
                state["emergency_master_restore"] = "PASS"
                state["production_master_restored"] = True
            except Exception as restore_exc:
                state["emergency_master_restore"] = f"FAIL: {restore_exc}"
        if updater_flushed and state.get("production_master_restored") and not rescue_attempted:
            # Even a capture/parser/host exception may occur after the target
            # accepted pending or reboot. Always spend the remaining original
            # deadline on a fresh production-Master verifier; never re-upload.
            rescue_attempted = True
            state["exception_rescue_rc"] = run_logged([
                sys.executable, str(TOOLS / args.confirm_tool),
                "--node", args.node,
                "--out-dir", str(args.out_dir / "exception_confirm_rescue"),
                "--identity-manifest", str(args.identity_manifest),
                "--expected-master-marker", args.master_marker,
                "--absolute-deadline", str(state["absolute_confirm_deadline"]),
                "--run-id", args.run_id,
                *(["--source-identity-manifest", str(args.source_identity_manifest)]
                  if args.source_identity_manifest else []),
            ], args.out_dir / "exception_confirm_rescue_console.log",
               env=env, check=False)
        (args.out_dir / "transaction.json").write_text(
            json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
