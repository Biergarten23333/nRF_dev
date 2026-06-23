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

import serial


def timestamped_out_dir(base: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if base.name.endswith(stamp):
        return base
    return base.parent / f"{base.name}_{stamp}"


def run_stream(cmd: list[str], log_path: Path, env: dict[str, str] | None = None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[DUAL] run: {' '.join(cmd)}", flush=True)
    print(f"[DUAL] log: {log_path}", flush=True)
    with log_path.open("w", encoding="utf-8", buffering=1) as logf:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=env,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="", flush=True)
            logf.write(line)
        return proc.wait()


def load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": f"failed_to_read_json:{exc}", "path": str(path)}


def restore_anchor_responder_state(port: str, log_path: Path, timeout_s: float = 24.0) -> dict:
    result = {
        "attempted": True,
        "success": False,
        "port": port,
        "command": "anchor role all responder cir 0",
        "timeout_s": timeout_s,
        "error": "",
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[DUAL] anchor responder restore: {result['command']}", flush=True)
    print(f"[DUAL] log: {log_path}", flush=True)
    try:
        with log_path.open("w", encoding="utf-8", buffering=1) as logf:
            with serial.Serial(port, 115200, timeout=0.2, write_timeout=2, exclusive=True) as ser:
                ser.reset_input_buffer()
                ser.write((result["command"] + "\n").encode("utf-8"))
                ser.flush()
                deadline = time.monotonic() + timeout_s
                while time.monotonic() < deadline:
                    raw = ser.readline()
                    if not raw:
                        continue
                    line = raw.decode("utf-8", "replace").rstrip()
                    print(f"[ANCHOR_RESTORE] {line}", flush=True)
                    logf.write(line + "\n")
                    if "anchor role rc=0 target=all role=responder cir=0" in line:
                        result["success"] = True
                        return result
                result["error"] = "timeout_waiting_for_anchor_role_rc"
                return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Dual-master capture: Master_Anchor handles anchors; Master_Tag handles tags."
    )
    parser.add_argument("--anchor-port", default=os.environ.get("BIOSPUR_ANCHOR_PORT", ""))
    parser.add_argument("--anchor-snr", default=os.environ.get("BIOSPUR_ANCHOR_SNR", "960148546"))
    parser.add_argument("--tag-port", default=os.environ.get("BIOSPUR_TAG_PORT", ""))
    parser.add_argument("--tag-snr", default=os.environ.get("BIOSPUR_TAG_SNR", "1050070698"))
    parser.add_argument("--duration", type=float, default=180.0)
    parser.add_argument("--targets", default="BSF66F,BS2DCE,BSDC91")
    parser.add_argument("--tr-hz", type=int, default=10)
    parser.add_argument(
        "--tdma-profile",
        choices=["motion"],
        default="motion",
        help="Deprecated compatibility option. Capture scenes always use motion TDMA/PMODE=0.",
    )
    parser.add_argument(
        "--tag-cir",
        choices=["off", "compact", "full"],
        default="off",
        help="Runtime CIR output requested from target Tags.",
    )
    parser.add_argument(
        "--full-cir-duration-s",
        type=float,
        default=float(os.environ.get("BIOSPUR_FULL_CIR_DURATION_S", "30")),
        help="Seconds for deferred USB full-CIR capture when --tag-cir full.",
    )
    parser.add_argument(
        "--full-cir-ports",
        default=os.environ.get("BIOSPUR_CIR_FULL_USB_PORTS", ""),
        help="Comma-separated LABEL=/dev/... USB CDC ports for deferred full-CIR capture.",
    )
    parser.add_argument("--profiles", default="", help="Deprecated compatibility option; ignored.")
    parser.add_argument("--cm-probe-target", default="BSF66F")
    parser.add_argument("--out-dir", default="logs/dual_master_tdma_capture")
    parser.add_argument("--skip-anchor-preflight", action="store_true")
    parser.add_argument(
        "--skip-anchor-responder-restore",
        action="store_true",
        help="Do not force anchors to responder CIR=0 before Tag capture.",
    )
    parser.add_argument("--anchor-preflight-timeout-s", type=float, default=30.0)
    parser.add_argument("--anchor-preflight-retries", type=int, default=3)
    parser.add_argument("--anchor-preflight-launch-retries", type=int, default=2)
    parser.add_argument("--anchor-responder-settle-s", type=float, default=10.0)
    parser.add_argument("--tag-link-timeout-s", type=float, default=30.0)
    parser.add_argument("--reuse-tag-links", action="store_true")
    parser.add_argument(
        "--known-bs-tags",
        default="BSF66F,BS2DCE,BSDC91,BS9336,BS955A,BSCCF4",
        help="Known BS tags used for optional targeted non-target idle before capture.",
    )
    parser.add_argument("--no-silence-non-target-tags", action="store_true")
    parser.add_argument("--non-target-silence-settle-s", type=float, default=1.0)
    parser.add_argument("--with-listener", action="store_true")
    parser.add_argument(
        "--listener-port",
        default=os.environ.get(
            "BIOSPUR_LISTENER_PORT",
            "/dev/serial/by-id/usb-SEGGER_J-Link_000760185886-if00",
        ),
    )
    parser.add_argument("--listener-extra-s", type=float, default=240.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if not args.anchor_port:
        raise SystemExit("--anchor-port is required")
    if not args.tag_port:
        raise SystemExit("--tag-port is required")
    if args.anchor_port == args.tag_port:
        raise SystemExit("anchor and tag ports must be different")
    if args.anchor_snr == args.tag_snr:
        raise SystemExit("anchor and tag SNRs must be different")

    out_dir = timestamped_out_dir(Path(args.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "success": False,
        "anchor_master": {
            "name": "Master_Anchor",
            "snr": args.anchor_snr,
            "port": args.anchor_port,
        },
        "tag_master": {
            "name": "Master_Tag",
            "snr": args.tag_snr,
            "port": args.tag_port,
        },
        "tdma_profile": args.tdma_profile,
        "tr_hz": args.tr_hz,
        "out_dir": str(out_dir),
    }

    listener_proc: subprocess.Popen | None = None
    if args.with_listener:
        listener_cmd = [
            sys.executable,
            "scripts/capture_uwb_listener.py",
            "--port",
            args.listener_port,
            "--duration",
            str(args.duration + args.listener_extra_s),
            "--out-dir",
            str(out_dir / "listener"),
        ]
        summary["listener_cmd"] = listener_cmd
        print(f"[DUAL] listener: {' '.join(listener_cmd)}", flush=True)
        listener_proc = subprocess.Popen(listener_cmd)
        time.sleep(1.0)

    try:
        if not args.skip_anchor_preflight:
            anchor_dir = out_dir / "anchor_preflight"
            anchor_cmd = [
                sys.executable,
                "scripts/verify_all_anchor_responder_runtime.py",
                "--port",
                args.anchor_port,
                "--command-timeout-s",
                str(args.anchor_preflight_timeout_s),
                "--retry-count",
                str(args.anchor_preflight_retries),
                "--out-dir",
                str(anchor_dir),
                "--live-output",
            ]
            rc = run_stream(anchor_cmd, out_dir / "anchor_preflight.console.log")
            summary["anchor_preflight_returncode"] = rc
            summaries = sorted(anchor_dir.glob("*/summary.json"))
            if not summaries:
                summaries = sorted(out_dir.glob("anchor_preflight_*/summary.json"))
            if summaries:
                summary["anchor_preflight"] = load_json(summaries[-1])
            else:
                summary["anchor_preflight"] = {"success": False, "error": "missing_summary"}
            if rc != 0 or not summary["anchor_preflight"].get("success"):
                summary["error"] = "anchor_preflight_failed"
                (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                print(json.dumps(summary, indent=2), flush=True)
                return 2

        if args.skip_anchor_responder_restore:
            summary["anchor_responder_restore"] = {
                "attempted": False,
                "success": True,
                "reason": "disabled_by_flag",
            }
        else:
            restore = restore_anchor_responder_state(
                args.anchor_port,
                out_dir / "anchor_responder_restore.console.log",
            )
            summary["anchor_responder_restore"] = restore
            if not restore.get("success"):
                summary["error"] = "anchor_responder_restore_failed"
                (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
                print(json.dumps(summary, indent=2), flush=True)
                return 2

        recv_dir = out_dir / "tag_capture"
        tag_cmd = [
            sys.executable,
            "scripts/run_recv_tdma_capture.py",
            "--port",
            args.tag_port,
            "--controller-reset-snr",
            args.tag_snr,
            "--duration",
            str(args.duration),
            "--targets",
            args.targets,
            "--tr-hz",
            str(args.tr_hz),
            "--tdma-profile",
            args.tdma_profile,
            "--tag-cir",
            args.tag_cir,
            "--full-cir-duration-s",
            str(args.full_cir_duration_s),
            "--full-cir-ports",
            args.full_cir_ports,
            "--full-cir-anchor-control-port",
            args.anchor_port,
            "--skip-anchor-preflight",
            "--anchor-preflight-port",
            args.anchor_port,
            "--anchor-responder-settle-s",
            str(args.anchor_responder_settle_s),
            "--tag-link-timeout-s",
            str(args.tag_link_timeout_s),
            "--known-bs-tags",
            args.known_bs_tags,
            "--non-target-silence-settle-s",
            str(args.non_target_silence_settle_s),
            "--out-dir",
            str(recv_dir),
        ]
        if args.reuse_tag_links:
            tag_cmd.append("--reuse-tag-links")
        if args.no_silence_non_target_tags:
            tag_cmd.append("--no-silence-non-target-tags")
        rc = run_stream(tag_cmd, out_dir / "tag_capture.console.log")
        summary["tag_capture_returncode"] = rc
        summaries = sorted(out_dir.glob("tag_capture_*/summary.json"))
        if not summaries:
            summaries = sorted(recv_dir.glob("*/summary.json"))
        if summaries:
            summary["tag_capture"] = load_json(summaries[-1])
        else:
            summary["tag_capture"] = {"success": False, "error": "missing_summary"}

        summary["success"] = rc == 0 and bool(summary["tag_capture"].get("success"))
        if not summary["success"]:
            summary["error"] = "tag_capture_failed"
    finally:
        if listener_proc is not None and listener_proc.poll() is None:
            listener_proc.terminate()
            try:
                listener_proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                listener_proc.kill()
                listener_proc.wait(timeout=5.0)
        if listener_proc is not None:
            summary["listener_returncode"] = listener_proc.returncode

    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2), flush=True)
    return 0 if summary["success"] else 3


if __name__ == "__main__":
    raise SystemExit(main())
