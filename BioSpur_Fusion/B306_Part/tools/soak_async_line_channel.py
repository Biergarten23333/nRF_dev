#!/usr/bin/env python3
"""Real-time PTY soak for the threaded Fusion CDC drain."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pty
import threading
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--rate", type=float, default=700.0)
    args = parser.parse_args()
    if args.duration_s < 600.0:
        raise SystemExit("formal soak requires duration >= 600 s")
    if args.rate < 700.0:
        raise SystemExit("formal soak requires rate >= 700 records/s")
    args.out_dir.mkdir(parents=True, exist_ok=False)

    master, slave = pty.openpty()
    log_path = args.out_dir / "synthetic_drain.log"
    log_file = log_path.open("x", encoding="utf-8", buffering=1)
    channel = ThreadedLineChannel(
        os.ttyname(slave),
        log_file,
        "SYNTH",
        backlog_red_records=8192,
    )
    channel.transport_mode = "text"
    producer_done = threading.Event()
    produced = 0
    producer_error: str | None = None
    started = time.monotonic()

    def produce() -> None:
        nonlocal produced, producer_error
        try:
            tick_s = 0.01
            while True:
                elapsed = time.monotonic() - started
                target = min(
                    int(elapsed * args.rate),
                    int(args.duration_s * args.rate),
                )
                if target > produced:
                    payload = "".join(
                        "FUSION_UWB name=BSFTEST "
                        f"sweep={index} payload={'A' * 96}\n"
                        for index in range(produced, target)
                    )
                    os.write(master, payload.encode("ascii"))
                    produced = target
                if elapsed >= args.duration_s:
                    break
                time.sleep(tick_s)
        except Exception as exc:
            producer_error = f"{type(exc).__name__}: {exc}"
        finally:
            producer_done.set()

    producer = threading.Thread(
        target=produce, name="synthetic-producer", daemon=True
    )
    producer.start()
    consumed = 0
    expected = 0
    sequence_errors = 0
    progress_next = started + 60.0
    while True:
        deadline = time.monotonic() + 0.25
        line = channel.read(deadline)
        if line is not None:
            fields = dict(
                token.split("=", 1)
                for token in line.split()
                if "=" in token
            )
            sequence = int(fields["sweep"], 0)
            if sequence != expected:
                sequence_errors += 1
                expected = sequence
            expected += 1
            consumed += 1
        now = time.monotonic()
        if now >= progress_next:
            health = channel.health_snapshot()
            print(
                json.dumps(
                    {
                        "elapsed_s": now - started,
                        "produced": produced,
                        "consumed": consumed,
                        "health": health,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
            progress_next += 60.0
        if (
            producer_done.is_set()
            and consumed >= produced
            and channel.health_snapshot()["decoded_queue_depth"] == 0
        ):
            break
        if now - started > args.duration_s + 30.0:
            producer_error = producer_error or "consumer drain timeout"
            break

    producer.join(timeout=5.0)
    ended = time.monotonic()
    health = channel.health_snapshot()
    channel.close()
    log_file.close()
    os.close(master)
    os.close(slave)
    logged_records = 0
    with log_path.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            logged_records += "SYNTH_RX FUSION_UWB" in line

    passed = bool(
        producer_error is None
        and produced >= int(args.duration_s * args.rate)
        and consumed == produced
        and logged_records == produced
        and sequence_errors == 0
        and health["decoded_queue_drops"] == 0
        and health["log_queue_drops"] == 0
        and health["red_markers"] == 0
        and health["reader_exceptions"] == 0
    )
    result = {
        "status": "PASS" if passed else "FAIL",
        "requested_duration_s": args.duration_s,
        "actual_duration_s": ended - started,
        "requested_rate_records_s": args.rate,
        "produced_records": produced,
        "consumed_records": consumed,
        "logged_records": logged_records,
        "effective_rate_records_s": produced / args.duration_s,
        "sequence_errors": sequence_errors,
        "producer_error": producer_error,
        "health": health,
        "log": str(log_path),
        "log_sha256": sha256(log_path),
        "log_bytes": log_path.stat().st_size,
    }
    result_path = args.out_dir / "SOAK_RESULT.json"
    result_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
