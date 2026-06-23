#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import serial


def read_for(ser: serial.Serial, seconds: float) -> str:
    end = time.monotonic() + max(0.0, seconds)
    chunks: list[str] = []
    while time.monotonic() < end:
        data = ser.read(4096)
        if data:
            chunks.append(data.decode("utf-8", "ignore"))
        else:
            time.sleep(0.02)
    return "".join(chunks)


def send_commands(port: str, commands: list[tuple[str, float]]) -> str:
    transcript: list[str] = []
    with serial.Serial(port, 115200, timeout=0.2, write_timeout=2.0) as ser:
        time.sleep(0.35)
        try:
            ser.reset_input_buffer()
        except Exception:
            read_for(ser, 0.4)
        for cmd, wait_s in commands:
            transcript.append(f">>> {cmd}\n")
            ser.write((cmd + "\n").encode("utf-8"))
            ser.flush()
            text = read_for(ser, wait_s)
            if text:
                transcript.append(text)
    return "".join(transcript)


def require_port(label: str, path: str, best_effort: bool) -> bool:
    if path and Path(path).exists():
        return True
    msg = f"[CIRCTL] missing {label} port: {path or '-'}"
    if best_effort:
        print(msg, flush=True)
        return False
    raise SystemExit(msg)


def normalize_mode(mode: str) -> str:
    value = mode.strip().lower()
    if value in {"0", "none", "disable", "disabled"}:
        return "off"
    if value in {"1"}:
        return "compact"
    if value in {"2"}:
        return "full"
    if value in {"off", "compact", "full", "status", "skip"}:
        return value
    raise SystemExit(f"[CIRCTL] invalid CIR mode: {mode}")


def tag_commands(mode: str, oneshot: bool, ready_wait_s: float) -> list[tuple[str, float]]:
    if mode == "skip":
        return []
    cmds: list[tuple[str, float]] = [
        ("device kind tag", ready_wait_s),
    ]
    if mode == "status":
        cmds.extend([
            ("tag cir all status", 2.0),
            ("oneshot show", 1.0),
        ])
        return cmds
    if mode == "off":
        cmds.extend([
            ("oneshot clear", 1.0),
            ("tag cir all off", 2.0),
        ])
        return cmds
    payload = f"CIR {mode.upper()}"
    if oneshot:
        cmds.append((f"oneshot {payload}", 1.0))
    cmds.extend([
        (f"tag cir all {mode}", 3.0),
        ("tag cir all status", 3.0),
    ])
    return cmds


def anchor_commands(role: str, mode: str, wait_s: float) -> list[tuple[str, float]]:
    if mode == "skip":
        return []
    cmds: list[tuple[str, float]] = [
        ("device kind anchor", 2.0),
    ]
    if mode == "status":
        cmds.append(("anchor version all", wait_s))
        return cmds
    cir = "0" if mode == "off" else mode
    cmds.append((f"anchor role all {role} cir {cir}", wait_s))
    return cmds


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Set BioSpur runtime CIR modes through Master CDC ports."
    )
    ap.add_argument("--tag-port", default=os.environ.get("BIOSPUR_TAG_PORT", ""))
    ap.add_argument("--tag-mode", default="skip")
    ap.add_argument("--tag-oneshot", action="store_true")
    ap.add_argument("--anchor-port", default=os.environ.get("BIOSPUR_ANCHOR_PORT", ""))
    ap.add_argument("--anchor-mode", default="skip")
    ap.add_argument("--anchor-role", choices=["matrix", "responder"], default="responder")
    ap.add_argument("--anchor-wait-s", type=float, default=50.0)
    ap.add_argument("--tag-ready-wait-s", type=float, default=8.0)
    ap.add_argument("--best-effort", action="store_true")
    args = ap.parse_args()

    tag_mode = normalize_mode(args.tag_mode)
    anchor_mode = normalize_mode(args.anchor_mode)
    rc = 0

    if tag_mode != "skip" and require_port("Master_Tag", args.tag_port, args.best_effort):
        try:
            print(
                f"[CIRCTL] tag mode={tag_mode} oneshot={int(args.tag_oneshot)} port={args.tag_port}",
                flush=True,
            )
            print(
                send_commands(
                    args.tag_port,
                    tag_commands(tag_mode, args.tag_oneshot, args.tag_ready_wait_s),
                ),
                end="",
            )
        except Exception as exc:
            print(f"[CIRCTL] tag control error: {exc}", flush=True)
            if not args.best_effort:
                return 2
            rc = 2

    if anchor_mode != "skip" and require_port("Master_Anchor", args.anchor_port, args.best_effort):
        try:
            print(
                f"[CIRCTL] anchor role={args.anchor_role} mode={anchor_mode} port={args.anchor_port}",
                flush=True,
            )
            print(
                send_commands(
                    args.anchor_port,
                    anchor_commands(args.anchor_role, anchor_mode, args.anchor_wait_s),
                ),
                end="",
            )
        except Exception as exc:
            print(f"[CIRCTL] anchor control error: {exc}", flush=True)
            if not args.best_effort:
                return 3
            rc = 3

    if args.best_effort and rc != 0:
        print(f"[CIRCTL] best-effort continuing after rc={rc}", flush=True)
        return 0
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
