#!/usr/bin/env python3
"""Sequential relay8 OTA for the eight online O2 nodes after O1 amendment.

Operator-facing output deliberately uses BSF identities only.  The internal
tag advertising identity remains confined to this machine-readable tool and
its raw OTA evidence.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from fusion_session import resolve_fusion_port


ROOT = Path(__file__).resolve().parents[2]
CONTROL = ROOT / "B306_Part/tools/relay8_tag_control.py"
OTA = ROOT / "UWB_Part/logs/night_20260730/morning/tools/ota_single_tag_stable_guarded.py"
TAG_MASTER = "/dev/serial/by-id/usb-Master_Tag_Master_Tag_Control_6918E0384172A49F-if00"
OLD_HASH = "f60652e54842719e11bd373aee8039c1f6fdd1ed0e6f303a0c49cac444073c82"
NEW_HASH = "69f8b6a1e4718d84156c8dbceb630fa578bf6d3d78ccec82da9cac5b6859bb26"
ORDER = (
    ("BSFC2CC", "BSE88E"),
    ("BSF44AD", "BS6F3A"),
    ("BSF6C53", "BSF8E0"),
    ("BSF8BC4", "BSEFD2"),
    ("BSF1120", "BSB10B"),
    ("BSF31CC", "BS8251"),
    ("BSFAA61", "BSF572"),
    ("BSFB165", "BS1150"),
)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def run(command: list[str], stdout_path: Path, timeout: float) -> None:
    with stdout_path.open("x", encoding="utf-8", buffering=1) as output:
        result = subprocess.run(
            command, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT,
            text=True, timeout=timeout, check=False,
        )
    if result.returncode != 0:
        raise RuntimeError(
            f"subprocess rc={result.returncode}; evidence={stdout_path}"
        )


def load_result(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("status") != "PASS":
        raise RuntimeError(f"result not PASS: {path}")
    return value


def wait_target_uwb(node: str, out_dir: Path, timeout_s: float = 35.0) -> None:
    """Read-only readiness gate: the tag UART path has emitted a full UWB frame."""
    out_dir.mkdir()
    result: dict[str, object] = {
        "node": node, "started": now(), "status": "IN_PROGRESS",
        "read_only": True,
    }
    channel: ThreadedLineChannel | None = None
    with (out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION", decoded_queue_records=65536,
                backlog_red_records=8192, raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            deadline = time.monotonic() + timeout_s
            prefix = f"FUSION_UWB proto=7 name={node} "
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if line and line.startswith(prefix):
                    result["guard_record"] = line
                    result["status"] = "PASS"
                    break
            if result["status"] != "PASS":
                raise RuntimeError(f"{node} emitted no UWB readiness frame")
            result["host_drain"] = channel.health_snapshot()
            if result["host_drain"]["red_markers"]:
                raise RuntimeError(f"{node} readiness drain RED")
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            result["ended"] = now()
            (out_dir / "result.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )


def witness_no_target_uwb(node: str, out_dir: Path, duration_s: float = 6.0) -> None:
    """Read-only behavioral idle proof while requiring the Fusion stream live."""
    out_dir.mkdir()
    result: dict[str, object] = {
        "node": node, "started": now(), "status": "IN_PROGRESS",
        "read_only": True, "duration_s": duration_s,
    }
    channel: ThreadedLineChannel | None = None
    with (out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION", decoded_queue_records=65536,
                backlog_red_records=8192, raw_backlog_red_bytes=8192, stall_red_s=1.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            deadline = time.monotonic() + duration_s
            target_prefix = f"FUSION_UWB proto=7 name={node} "
            target_uwb = 0
            decoded = 0
            while time.monotonic() < deadline:
                line = channel.read(deadline)
                if not line:
                    continue
                decoded += 1
                if line.startswith(target_prefix):
                    target_uwb += 1
            result.update({"decoded_records": decoded, "target_uwb": target_uwb})
            if decoded < 20:
                raise RuntimeError(f"{node} behavioral witness stream not live")
            if target_uwb:
                raise RuntimeError(f"{node} idle failed: {target_uwb} UWB records")
            result["status"] = "PASS"
            result["host_drain"] = channel.health_snapshot()
            if result["host_drain"]["red_markers"]:
                raise RuntimeError(f"{node} idle witness drain RED")
        finally:
            if channel is not None:
                result.setdefault("host_drain", channel.health_snapshot())
                channel.close()
            result["ended"] = now()
            (out_dir / "result.json").write_text(
                json.dumps(result, indent=2) + "\n", encoding="utf-8"
            )


def idle_with_fallback(node: str, board: Path) -> str:
    out_dir = board / "postota_idle"
    stdout_path = board / "postota_idle.stdout.log"
    with stdout_path.open("x", encoding="utf-8", buffering=1) as output:
        completed = subprocess.run(
            [
                sys.executable, str(CONTROL), "idle", "--node", node,
                "--allow-offline", "BSFEC35", "--out-dir", str(out_dir),
            ],
            cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, text=True,
            timeout=60.0, check=False,
        )
    if completed.returncode == 0:
        load_result(out_dir / "result.json")
        return "echo_verified"
    witness_no_target_uwb(node, board / "postota_idle_behavioral_witness")
    return "behaviorally_verified_reply_missing"


def control(action: str, node: str, out_dir: Path) -> dict[str, object]:
    run(
        [
            sys.executable, str(CONTROL), action, "--node", node,
            "--allow-offline", "BSFEC35", "--out-dir", str(out_dir),
        ],
        out_dir.parent / f"{out_dir.name}.stdout.log",
        240.0 if node == "BSFB165" else 60.0,
    )
    return load_result(out_dir / "result.json")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence_root", type=Path)
    parser.add_argument("--start-at", choices=tuple(node for node, _ in ORDER))
    args = parser.parse_args()
    root = args.evidence_root
    root.mkdir(parents=True, exist_ok=False)
    ledger: list[dict[str, object]] = []
    selected = list(ORDER)
    if args.start_at:
        selected = selected[
            next(i for i, row in enumerate(selected) if row[0] == args.start_at):
        ]
    try:
        ordinal_by_node = {row[0]: i for i, row in enumerate(ORDER, start=2)}
        for node, target in selected:
            ordinal = ordinal_by_node[node]
            board = root / f"{ordinal:02d}_{node.lower()}"
            board.mkdir()
            row: dict[str, object] = {
                "node": node, "started": now(), "status": "IN_PROGRESS"
            }
            ledger.append(row)
            print(f"{node} START", flush=True)

            before = control("query", node, board / "preota_query")
            before_version = before["query"]["version"]["reply"]["text"]
            before_imgstat = before["query"]["imgstat"]["reply"]["text"]
            if "fw=tag-fusion-link-relay7" not in before_version:
                raise RuntimeError(f"{node} unexpected pre-OTA marker")
            if OLD_HASH not in before_imgstat or "confirmed=1" not in before_imgstat:
                raise RuntimeError(f"{node} unexpected pre-OTA IMGSTAT")

            ota_dir = board / "ota"
            run(
                [
                    sys.executable, str(OTA), "--port", TAG_MASTER,
                    "--target-name", target, "--out-dir", str(ota_dir),
                    "--timeout-s", "300", "--reconnect-timeout-s", "45",
                ],
                board / "ota_launcher.stdout.log", 420.0,
            )
            ota_summary = json.loads(
                (ota_dir / "summary.json").read_text(encoding="utf-8")
            )
            if not ota_summary.get("ota_success_seen"):
                raise RuntimeError(f"{node} OTA success not observed")

            wait_target_uwb(node, board / "postota_uart_ready")
            after = control("verify-relay8", node, board / "postota_verify")
            after_version = after["query"]["version"]["reply"]["text"]
            after_imgstat = after["query"]["imgstat"]["reply"]["text"]
            if "fw=tag-fusion-link-relay8" not in after_version:
                raise RuntimeError(f"{node} relay8 marker absent")
            if NEW_HASH not in after_imgstat or "confirmed=1" not in after_imgstat:
                raise RuntimeError(f"{node} relay8 confirmation absent")

            idle_proof = idle_with_fallback(node, board)
            row.update({
                "ended": now(), "status": "COMPLETE",
                "marker": "tag-fusion-link-relay8",
                "imgstat_hash": NEW_HASH, "confirmed": 1,
                "safe_state": "composed_idle", "idle_proof": idle_proof,
            })
            (root / "ledger.json").write_text(
                json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
            )
            print(f"{node} COMPLETE", flush=True)
        return 0
    except Exception as exc:
        if ledger:
            ledger[-1].update({"ended": now(), "status": "FAIL", "error": str(exc)})
        (root / "ledger.json").write_text(
            json.dumps(ledger, indent=2) + "\n", encoding="utf-8"
        )
        print(f"BATCH STOP — {exc}", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
