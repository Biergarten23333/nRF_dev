#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

from run_autopos_round import UUIDS
from run_autopos_sweep_loop import (
    anchor_role_counts,
    collect_for_text,
    emit,
    ensure_autopos_maps,
    open_port,
    send_cmd_collect_text,
)


def runtime_responder_ack_info(text: str) -> tuple[bool, dict[str, int]]:
    info: dict[str, int] = {}
    command_ok = (
        "anchor role rc=0 target=all role=responder" in text
        or "anchor role all responder runtime sent=" in text
        or "anchor role all responder runtime repeat sent=" in text
        or "anchor role all responder runtime final sent=" in text
    )
    matches = re.findall(
        r"anchor role all responder runtime (?:repeat |final )?sent=(\d+) ready=(\d+)/(\d+)",
        text,
    )
    if matches:
        sent, ready, target = map(int, matches[-1])
        info["sent_count"] = sent
        info["ready_count"] = ready
        info["ready_target"] = target
        ack_ok = command_ok and sent >= len(UUIDS) and ready >= len(UUIDS) and target >= len(UUIDS)
        return ack_ok, info
    return False, info


def read_log_from_offset(log_path: Path, offset: int) -> str:
    try:
        with log_path.open("rb") as f:
            f.seek(offset)
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def anchor_ctrl_ready_uuids_from_log(log_path: Path) -> set[str]:
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return set()
    return {
        m.group(1).upper()
        for m in re.finditer(
            r"ANCHOR_CTRL\[\d+\] link ready[^\n]*uuid=([0-9A-Fa-f]{32})",
            text,
        )
    }


def latest_anchor_conn_count_from_log(log_path: Path) -> int:
    try:
        text = log_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0
    matches = re.findall(r"conn_count=(\d+)[^\n]*target=anchor", text)
    if not matches:
        return 0
    return int(matches[-1])


def wait_anchor_ctrl_ready(
    ser: serial.Serial,
    logf,
    log_path: Path,
    port: str,
    live_output: bool,
    verbose: int,
    timeout_s: float = 60.0,
) -> tuple[serial.Serial, bool]:
    deadline = time.time() + timeout_s
    last_count = -1
    gate_reached_at: float | None = None
    while time.time() < deadline:
        ready = anchor_ctrl_ready_uuids_from_log(log_path)
        conn_count = latest_anchor_conn_count_from_log(log_path)
        effective_count = max(len(ready), conn_count)
        if effective_count != last_count:
            emit(
                logf,
                f"VERIFY: anchor ctrl ready gate ready_log={len(ready)}/{len(UUIDS)} conn_count={conn_count}/{len(UUIDS)}\n",
                live_output,
                verbose,
            )
            last_count = effective_count
        if effective_count >= len(UUIDS):
            if gate_reached_at is None:
                gate_reached_at = time.time()
                emit(
                    logf,
                    "VERIFY: anchor ctrl ready/conn gate reached; settling for 2.0s\n",
                    live_output,
                    verbose,
                )
            if (time.time() - gate_reached_at) >= 2.0:
                return ser, True
        else:
            gate_reached_at = None
        ser, _, _ = collect_for_text(
            ser,
            logf,
            0.5,
            port,
            live_output,
            verbose,
        )
    ready = anchor_ctrl_ready_uuids_from_log(log_path)
    emit(
        logf,
        f"VERIFY WARN: anchor ctrl ready gate timed out {len(ready)}/{len(UUIDS)}; final runtime ack will be authoritative\n",
        live_output,
        verbose,
    )
    return ser, False


def rebuild_anchor_control_links(
    ser: serial.Serial,
    logf,
    log_path: Path,
    port: str,
    live_output: bool,
    verbose: int,
    context: dict,
    timeout_s: float,
) -> tuple[serial.Serial, bool]:
    """Force a fresh anchor-control discovery pass before runtime role commands.

    Matrix anchors advertise slowly, so a stale AUTOPOS session can sit in
    scan=1/conn_count=0 forever from the script's point of view.  Bounce only
    the Master_Anchor control plane back through RECV, reassert the anchor
    target and UUID map, then wait for real A-H control links before sending
    role changes.
    """

    emit(
        logf,
        "VERIFY: anchor ctrl ready gate missed; force rebuild anchor control links\n",
        live_output,
        verbose,
    )
    for attempt in range(1, 4):
        emit(logf, f"VERIFY: anchor ctrl rebuild attempt={attempt}/3\n", live_output, verbose)
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "mode recv",
            3.0,
            live_output,
            verbose,
            resend_after_reopen=True,
        )
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "device kind anchor",
            2.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "mode recv",
            2.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, _, _ = collect_for_text(
            ser,
            logf,
            1.0,
            port,
            live_output,
            verbose,
        )
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "mode autopos",
            8.0,
            live_output,
            verbose,
            resend_after_reopen=True,
        )
        ser, _, _ = collect_for_text(
            ser,
            logf,
            1.0,
            port,
            live_output,
            verbose,
        )
        context["autopos_initialized"] = False
        ser = ensure_autopos_maps(
            ser,
            logf,
            port,
            live_output,
            verbose,
            context=context,
        )
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "conn",
            5.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        ser, ready_ok = wait_anchor_ctrl_ready(
            ser,
            logf,
            log_path,
            port,
            live_output,
            verbose,
            timeout_s=timeout_s,
        )
        if ready_ok:
            emit(logf, "VERIFY: anchor ctrl rebuild succeeded\n", live_output, verbose)
            return ser, True

    emit(logf, "VERIFY ERROR: anchor ctrl rebuild failed; no runtime responder command sent\n", live_output, verbose)
    return ser, False


def timestamped_out_dir(base: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if base.name.endswith(stamp):
        return base
    return base.parent / f"{base.name}_{stamp}"


def prepare_anchor_autopos_control_plane(
    ser: serial.Serial,
    logf,
    port: str,
    live_output: bool,
    verbose: int,
) -> serial.Serial:
    """Enter AUTOPOS with the anchor device model selected.

    Do not bounce through RECV here.  In practice that can reconnect tags while
    this verifier is trying to build the anchor-control plane, which produces a
    false responder preflight and then poisons the capture session.
    """

    emit(logf, "VERIFY: prepare anchor target before AUTOPOS preflight\n", live_output, verbose)
    ser, status_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "status",
        1.0,
        live_output,
        verbose,
        resend_after_reopen=False,
    )
    ser, device_text = send_cmd_collect_text(
        ser,
        logf,
        port,
        "device show",
        1.0,
        live_output,
        verbose,
        resend_after_reopen=False,
    )
    if "System target: kind=anchor" not in device_text:
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "device kind anchor",
            6.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )

    for attempt in range(1, 4):
        emit(logf, f"VERIFY: enter AUTOPOS attempt={attempt}/3\n", live_output, verbose)
        if attempt == 1 and "Control status: mode=AUTOPOS" in status_text:
            ser, status_reply = send_cmd_collect_text(
                ser,
                logf,
                port,
                "status",
                1.0,
                live_output,
                verbose,
                resend_after_reopen=False,
            )
            if "Control status: mode=AUTOPOS" in status_reply:
                emit(logf, "VERIFY: AUTOPOS anchor control plane already active\n", live_output, verbose)
                return ser
        ser, text = send_cmd_collect_text(
            ser,
            logf,
            port,
            "mode autopos",
            10.0 if attempt == 1 else 6.0,
            live_output,
            verbose,
            resend_after_reopen=True,
        )
        ser, _, status_text = collect_for_text(
            ser,
            logf,
            1.0,
            port,
            live_output,
            verbose,
        )
        ser, status_reply = send_cmd_collect_text(
            ser,
            logf,
            port,
            "status",
            1.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )
        if (
            "Control status: mode=AUTOPOS" in text
            or "Control status: mode=AUTOPOS" in status_text
            or "Control status: mode=AUTOPOS" in status_reply
        ):
            emit(logf, "VERIFY: AUTOPOS anchor control plane active\n", live_output, verbose)
            return ser

        # If a stale-link reboot happened, reassert anchor target before retry.
        ser, _ = send_cmd_collect_text(
            ser,
            logf,
            port,
            "device kind anchor",
            3.0,
            live_output,
            verbose,
            resend_after_reopen=False,
        )

    emit(logf, "VERIFY WARN: AUTOPOS mode not confirmed; continuing best-effort\n", live_output, verbose)
    return ser


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify all 8 anchors reach runtime responder via 8/8 control-link ready + runtime ack."
    )
    parser.add_argument(
        "--port",
        default="/dev/serial/by-id/usb-BioSpur_BioSpur_BLE_Control_87EA2F4A526C5A02-if00",
        help="B120 master control serial port",
    )
    parser.add_argument("--live-output", action="store_true", help="Stream serial output while running")
    parser.add_argument("--verbose", type=int, default=2)
    parser.add_argument("--command-timeout-s", type=float, default=30.0)
    parser.add_argument("--scan-timeout-s", type=float, default=20.0)
    parser.add_argument("--retry-count", type=int, default=3)
    parser.add_argument(
        "--out-dir",
        default="logs/verify_all_anchor_responder_runtime",
        help="Base output directory; timestamp suffix is appended automatically",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    out_dir = timestamped_out_dir(Path(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "verify.log"
    summary_path = out_dir / "summary.json"

    result = {
        "success": False,
        "port": args.port,
        "log_path": str(log_path),
        "command_sent": False,
        "attempts": [],
        "final_ack": {},
        "final_role_counts": {},
        "error": "",
    }

    ser: serial.Serial | None = None
    try:
        ser = open_port(args.port, 60.0)
        with log_path.open("w", buffering=1, encoding="utf-8") as logf:
            emit(logf, f"PORT={args.port}\n", args.live_output, args.verbose)
            emit(logf, f"START={time.strftime('%Y-%m-%d %H:%M:%S')}\n", args.live_output, args.verbose)
            emit(logf, "VERIFY: prepare AUTOPOS control plane for all-responder runtime verification\n", args.live_output, args.verbose)

            context = {"autopos_initialized": False}
            ser = prepare_anchor_autopos_control_plane(
                ser,
                logf,
                args.port,
                args.live_output,
                args.verbose,
            )
            ser = ensure_autopos_maps(
                ser,
                logf,
                args.port,
                args.live_output,
                args.verbose,
                context=context,
            )
            emit(logf, "VERIFY: start anchor discovery/control links\n", args.live_output, args.verbose)
            ser, _ = send_cmd_collect_text(
                ser,
                logf,
                args.port,
                "conn",
                5.0,
                args.live_output,
                args.verbose,
                resend_after_reopen=False,
            )
            ser, ctrl_ready_ok = wait_anchor_ctrl_ready(
                ser,
                logf,
                log_path,
                args.port,
                args.live_output,
                args.verbose,
                timeout_s=max(60.0, args.scan_timeout_s),
            )
            if not ctrl_ready_ok:
                ser, ctrl_ready_ok = rebuild_anchor_control_links(
                    ser,
                    logf,
                    log_path,
                    args.port,
                    args.live_output,
                    args.verbose,
                    context,
                    timeout_s=max(90.0, args.scan_timeout_s),
                )
            if not ctrl_ready_ok:
                result["error"] = "anchor_ctrl_ready_failed"
                result["final_role_counts"] = scan_anchor_role_counts(timeout_s=min(8.0, args.scan_timeout_s))
                summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
                print(json.dumps(result, indent=2))
                return 1

            runtime_command_timeout_s = min(float(args.command_timeout_s), 30.0)
            if runtime_command_timeout_s < float(args.command_timeout_s):
                emit(
                    logf,
                    (
                        "VERIFY: cap runtime responder command collect window "
                        f"from {float(args.command_timeout_s):.1f}s to {runtime_command_timeout_s:.1f}s; "
                        "ready=8/8 ack is authoritative\n"
                    ),
                    args.live_output,
                    args.verbose,
                )

            for attempt in range(1, args.retry_count + 1):
                emit(logf, f"VERIFY: responder runtime attempt={attempt}/{args.retry_count}\n", args.live_output, args.verbose)
                logf.flush()
                try:
                    attempt_log_offset = log_path.stat().st_size
                except Exception:
                    attempt_log_offset = 0
                ser, text = send_cmd_collect_text(
                    ser,
                    logf,
                    args.port,
                    "anchor role all responder",
                    runtime_command_timeout_s,
                    args.live_output,
                    args.verbose,
                    resend_after_reopen=False,
                )
                logf.flush()
                attempt_text = text + "\n" + read_log_from_offset(log_path, attempt_log_offset)
                result["command_sent"] = result["command_sent"] or (
                    "anchor role rc=0 target=all role=responder" in attempt_text
                    or "anchor role all responder runtime sent=" in attempt_text
                    or "anchor role all responder runtime repeat sent=" in attempt_text
                    or "anchor role all responder runtime final sent=" in attempt_text
                )
                ack_ok, ack_info = runtime_responder_ack_info(attempt_text)
                role_counts = anchor_role_counts(attempt_text)
                attempt_result = {
                    "attempt": attempt,
                    "ack_ok": ack_ok,
                    **ack_info,
                    "role_counts": role_counts,
                }
                result["attempts"].append(attempt_result)
                result["final_ack"] = ack_info
                result["final_role_counts"] = role_counts
                emit(
                    logf,
                    (
                        f"VERIFY: attempt={attempt} "
                        f"ack_ok={int(ack_ok)} "
                        f"sent={ack_info.get('sent_count', 0)} "
                        f"ready={ack_info.get('ready_count', 0)}/{ack_info.get('ready_target', 0)} "
                        f"scan=matrix={role_counts.get('matrix', 0)} "
                        f"responder={role_counts.get('responder', 0)} "
                        f"master={role_counts.get('master', 0)} "
                        f"other={role_counts.get('other', 0)}\n"
                    ),
                    args.live_output,
                    args.verbose,
                )
                if ack_ok:
                    result["success"] = True
                    break
                time.sleep(args.scan_timeout_s)

            if not result["success"] and not result["error"]:
                result["error"] = "runtime_responder_ack_failed"

        summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, indent=2))
        return 0 if result["success"] else 1
    except Exception as exc:
        result["error"] = f"{exc.__class__.__name__}: {exc}"
        try:
            summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        except Exception:
            pass
        print(json.dumps(result, indent=2))
        return 1
    finally:
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
