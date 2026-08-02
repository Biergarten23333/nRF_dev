#!/usr/bin/env python3
"""Apply the cold-start prompt's S5b gates to a closed capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4")
TRANSPORT_FIELDS = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "notify_errno",
    "uart_restarts",
    "uart_err",
    "logger_drop",
    "imu_missed_deadlines",
)
START_TOKENS = (
    "61=0001:P",
    "03=000B:P",
    "1F=0002:P",
    "volatile=1",
    "saved=0",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", required=True, type=Path)
    parser.add_argument("--start", required=True, type=Path)
    parser.add_argument("--stop", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    capture = json.loads(args.capture.read_text(encoding="utf-8"))
    start = json.loads(args.start.read_text(encoding="utf-8"))
    stop = json.loads(args.stop.read_text(encoding="utf-8"))

    per_node = {}
    all_pass = True
    for node in NODES:
        row = capture["imu_summary"][node]
        start_text = start["result"]["imu_start"][node]["start"]["text"]
        stop_text = stop["result"]["imu_stop"][node]["status"]["text"]
        transport = {
            field: row["telemetry_delta"][field] for field in TRANSPORT_FIELDS
        }
        node_pass = (
            all(token in start_text for token in START_TOKENS)
            and "active=0 " in f"{stop_text} "
            and 190.0 <= float(row["imu_effective_rate_hz"]) <= 210.0
            and int(row["imu_sequence_gaps"]) == 0
            and 9.5 <= float(row["uwb_rate_hz"]) <= 10.5
            and all(value == 0 for value in transport.values())
        )
        all_pass = all_pass and node_pass
        per_node[node] = {
            "imu_rate_hz": row["imu_effective_rate_hz"],
            "imu_records": row["imu_records"],
            "imu_samples": row["imu_samples"],
            "imu_sequence_gaps": row["imu_sequence_gaps"],
            "uwb_rate_hz": row["uwb_rate_hz"],
            "transport_delta": transport,
            "recovered_jy61p_i2c_events": row["telemetry_delta"][
                "imu_i2c_err"
            ],
            "start_verified": all(token in start_text for token in START_TOKENS),
            "stopped_active_zero": "active=0 " in f"{stop_text} ",
            "pass": node_pass,
        }

    all_pass = (
        all_pass
        and capture["status"] == "COMPLETE"
        and capture["fresh_boundary_guard"]["status"] == "PASS"
        and int(capture["decoder_errors"]) == 0
        and not capture["disconnects"]
        and not capture["malformed"]
        and start["status"] == "PASS"
        and stop["status"] == "PASS"
    )
    result = {
        "scope": "Cold-start S5b attempt 3, acceptance semantics from PROMPT.md",
        "duration_s": capture["duration_s"],
        "fresh_boundary_guard": capture["fresh_boundary_guard"],
        "per_node": per_node,
        "decoder_errors": capture["decoder_errors"],
        "disconnects": capture["disconnects"],
        "malformed": capture["malformed"],
        "recovery_rule": (
            "JY61P recovery events are reported separately; they do not fail "
            "S5b when sequence gaps and transport/missed-deadline deltas are zero"
        ),
        "pass": all_pass,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if all_pass else 2


if __name__ == "__main__":
    raise SystemExit(main())
