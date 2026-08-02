#!/usr/bin/env python3
"""Run one Batch-A Fusion capture with seven passive listener archives."""

from __future__ import annotations

import argparse
import json
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SLOT_RUNNER = (
    ROOT
    / "B306_Part"
    / "logs"
    / "tdma_slots_20260728"
    / "phase_tc"
    / "run_nrf52840_slot_run.py"
)
COLLECTOR = ROOT / "B306_Part" / "host" / "listener_array_collector.py"
LISTENER_SNRS = (
    "760184753",
    "760184548",
    "760181725",
    "760184784",
    "760184964",
    "760184767",
    "760184545",
)
RUN_SPECS = {
    # Revised, pre-registered order: dispersed primary, dispersed redraw,
    # adjacent comparison.  T2/T1 are the existing verified CFG grammars.
    "A1": {"slot_run": "T2", "slots": [0, 2, 4, 6, 8], "duration_s": 600.0},
    "A2": {"slot_run": "T2", "slots": [0, 2, 4, 6, 8], "duration_s": 300.0},
    "A3": {"slot_run": "T1", "slots": [0, 1, 2, 3, 4], "duration_s": 300.0},
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def listeners_have_lstat(listener_dir: Path) -> tuple[bool, list[str]]:
    missing: list[str] = []
    for snr in LISTENER_SNRS:
        path = listener_dir / "listeners" / f"{snr}.jsonl"
        if not path.exists():
            missing.append(f"{snr}: archive not created")
            continue
        found = False
        try:
            with path.open(encoding="utf-8") as handle:
                for line in handle:
                    if '"kind":"LSTAT"' in line and '"parsed_ok":true' in line:
                        found = True
                        break
        except OSError as exc:
            missing.append(f"{snr}: {exc}")
            continue
        if not found:
            missing.append(f"{snr}: no parsed LSTAT")
    return not missing, missing


def wait_listener_preflight(
    listener_dir: Path, process: subprocess.Popen[str], timeout_s: float = 15.0
) -> dict[str, object]:
    started = time.monotonic()
    last_missing: list[str] = []
    while time.monotonic() - started < timeout_s:
        if process.poll() is not None:
            raise RuntimeError(
                f"listener collector exited during preflight rc={process.returncode}"
            )
        ready, last_missing = listeners_have_lstat(listener_dir)
        if ready:
            return {
                "status": "PASS",
                "criterion": "parsed LSTAT from every listener while tags idle",
                "elapsed_s": time.monotonic() - started,
                "snrs": LISTENER_SNRS,
            }
        time.sleep(0.25)
    raise RuntimeError(f"listener preflight timeout: {last_missing}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", choices=tuple(RUN_SPECS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--anchor-summary", type=Path, required=True)
    parser.add_argument("--generation", type=int, required=True)
    args = parser.parse_args()

    spec = RUN_SPECS[args.run]
    if args.output_dir.exists():
        raise SystemExit(f"refusing existing output directory: {args.output_dir}")
    args.output_dir.mkdir(parents=True)
    listener_dir = args.output_dir / "listener_capture"
    fusion_dir = args.output_dir / "fusion_capture"
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "run": args.run,
        "configuration": spec,
        "generation": args.generation & 0xFF,
        "anchor_summary": str(args.anchor_summary.resolve()),
        "forbidden_operations": [
            "CFG_STOP",
            "firmware build",
            "flash/OTA/SWD write",
            "J-Link operation on 1050070698",
        ],
    }
    write_json(args.output_dir / "summary.json", summary)

    collector_cmd = [
        sys.executable,
        str(COLLECTOR),
        "--out-dir",
        str(listener_dir),
        "--duration",
        str(float(spec["duration_s"]) + 300.0),
        "--require-kind",
        "LSTAT",
        "--require-kind",
        "LPD",
        "--require-kind",
        "LRD",
    ]
    slot_cmd = [
        sys.executable,
        str(SLOT_RUNNER),
        "--run",
        str(spec["slot_run"]),
        "--output-dir",
        str(fusion_dir),
        "--anchor-summary",
        str(args.anchor_summary),
        "--generation",
        str(args.generation & 0xFF),
        "--duration-s",
        str(spec["duration_s"]),
    ]
    summary["collector_command"] = collector_cmd
    summary["fusion_command"] = slot_cmd
    write_json(args.output_dir / "summary.json", summary)

    collector_log = (args.output_dir / "collector_process.log").open(
        "w", encoding="utf-8", buffering=1
    )
    fusion_log = (args.output_dir / "fusion_process.log").open(
        "w", encoding="utf-8", buffering=1
    )
    collector: subprocess.Popen[str] | None = None
    try:
        collector = subprocess.Popen(
            collector_cmd,
            cwd=ROOT,
            stdout=collector_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        summary["listener_preflight"] = wait_listener_preflight(
            listener_dir, collector
        )
        summary["fusion_process_started_utc"] = utc_now()
        write_json(args.output_dir / "summary.json", summary)
        fusion_result = subprocess.run(
            slot_cmd,
            cwd=ROOT,
            stdout=fusion_log,
            stderr=subprocess.STDOUT,
            text=True,
        )
        summary["fusion_return_code"] = fusion_result.returncode
        summary["fusion_process_ended_utc"] = utc_now()
        if fusion_result.returncode != 0:
            raise RuntimeError(
                f"Fusion capture failed return_code={fusion_result.returncode}"
            )
    except BaseException as exc:
        summary["status"] = (
            "ABORTED" if isinstance(exc, KeyboardInterrupt) else "FAILED"
        )
        summary["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if collector is not None and collector.poll() is None:
            collector.send_signal(signal.SIGINT)
        if collector is not None:
            try:
                summary["collector_return_code"] = collector.wait(timeout=30.0)
            except subprocess.TimeoutExpired:
                collector.terminate()
                summary["collector_return_code"] = collector.wait(timeout=10.0)
                summary["collector_forced_terminate"] = True
        collector_log.close()
        fusion_log.close()

    listener_summary_path = listener_dir / "summary.json"
    if listener_summary_path.exists():
        summary["listener_summary"] = json.loads(
            listener_summary_path.read_text(encoding="utf-8")
        )
    if (
        summary.get("status") == "IN_PROGRESS"
        and summary.get("collector_return_code") == 0
    ):
        summary["status"] = "COMPLETE"
    elif summary.get("status") == "IN_PROGRESS":
        summary["status"] = "FAILED"
        summary["error"] = (
            f"listener collector failed rc={summary.get('collector_return_code')}"
        )
    summary["ended_utc"] = utc_now()
    write_json(args.output_dir / "summary.json", summary)
    print(
        json.dumps(
            {
                "status": summary["status"],
                "run": args.run,
                "fusion_return_code": summary.get("fusion_return_code"),
                "collector_return_code": summary.get("collector_return_code"),
                "listener_failures": summary.get("listener_summary", {}).get(
                    "acceptance_failures"
                ),
            },
            indent=2,
        )
    )
    return 0 if summary["status"] == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
