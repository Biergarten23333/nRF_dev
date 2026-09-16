#!/usr/bin/env python3
"""Run one reader-only pass and stop at the first availability regression."""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import resource
import time

from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import (
    FullSessionAvailabilityRegression,
    FullSessionContinuousReader,
    SOURCE_SHA256,
)
from build_c2_full_session_ten_node_ab import _static_inputs


ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_new(path: Path, document: dict[str, object]) -> None:
    payload = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC, 0o444)
    try:
        if os.write(fd, payload) != len(payload):
            raise RuntimeError("short diagnostic evidence write")
        os.fsync(fd)
    finally:
        os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT / "logs"):
        raise ValueError("diagnostic output must be under workspace logs")
    output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    delivered = 0
    clock_owner, _clocks, _anchors, _delays, _tag_delay = _static_inputs()
    reader = FullSessionContinuousReader(root=ROOT, clock_owner=clock_owner)

    def consume(ticket) -> None:
        nonlocal delivered
        child = ticket.dispatch()
        child.deliver(lambda _event: None)
        delivered += 1

    try:
        reader.consume(consume)
    except FullSessionAvailabilityRegression as error:
        document = {
            "schema": "biospur.c2.full_session_availability_regression.v1",
            "status": "FIRST_REGRESSION_CAPTURED",
            "reader_only": True,
            "coordinator_fk_ik_uwb_solve_run": False,
            "events_delivered_before_failure": delivered,
            "diagnostic": asdict(error.diagnostic),
            "source_sha256": SOURCE_SHA256,
            "reader_sha256": _sha256(
                ROOT / "src/biospur_fusion/c2_coupled_progressive/continuous_full_session_reader.py"
            ),
            "tool_sha256": _sha256(Path(__file__).resolve()),
            "wall_s": time.monotonic() - started,
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        }
        _write_new(output / "RESULT.json", document)
        return 0
    raise RuntimeError("reader completed without the expected availability regression")


if __name__ == "__main__":
    raise SystemExit(main())
