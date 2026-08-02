#!/usr/bin/env python3
"""Analyze the sub-v10.2 true-POR, 30-minute zero-TX soak."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


SUB = "760181725"
MAIN = "760184545"


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    summary = json.loads((args.raw / "summary.json").read_text(encoding="utf-8"))
    sub = load_rows(args.raw / "listeners" / f"{SUB}.jsonl")
    main_rows = load_rows(args.raw / "listeners" / f"{MAIN}.jsonl")

    sub_lbt = [row for row in sub if row.get("kind") == "LBTX"]
    sub_lbd = [row for row in sub if row.get("kind") == "LBD"]
    sub_stat = [row for row in sub if row.get("kind") == "LSTAT"]
    main_lbt = [row for row in main_rows if row.get("kind") == "LBTX"]
    main_stat = [row for row in main_rows if row.get("kind") == "LSTAT"]
    roles = Counter(row.get("fields", {}).get("role") for row in sub_stat)
    markers = Counter(row.get("fields", {}).get("marker") for row in sub_stat)

    accepted_start = (
        int(sub_stat[0]["fields"].get("beacons", 0)) if sub_stat else None
    )
    accepted_end = (
        int(sub_stat[-1]["fields"].get("beacons", 0)) if sub_stat else None
    )
    duration_s = float(summary.get("actual_duration_s", 0.0))
    main_rate = len(main_lbt) / duration_s if duration_s else 0.0
    sub_listener_summary = summary.get("listeners", {}).get(SUB, {})
    main_listener_summary = summary.get("listeners", {}).get(MAIN, {})

    transport_pass = all(
        int(item.get(key, 0)) == 0
        for item in (sub_listener_summary, main_listener_summary)
        for key in ("parse_errors", "serial_errors")
    ) and all(
        int(item.get("firmware_counter_delta", {}).get(key, 0)) == 0
        for item in (sub_listener_summary, main_listener_summary)
        for key in ("ring_drops", "self_recover", "rx_enable_failures")
    ) and all(
        item.get("error") is None
        for item in (sub_listener_summary, main_listener_summary)
    )
    pass_gate = (
        duration_s >= 1799.0
        and not sub_lbt
        and bool(sub_lbd)
        and bool(sub_stat)
        and accepted_start is not None
        and accepted_end is not None
        and accepted_end > accepted_start
        and roles.get("SLAVED", 0) == len(sub_stat)
        and markers.get("listener-beacon-sub-v10.2", 0) == len(sub_stat)
        and 9.5 <= main_rate <= 10.5
        and all(
            row.get("fields", {}).get("role") == "MAIN" for row in main_stat
        )
        and transport_pass
    )

    result = {
        "scope": "sub-v10.2 true-POR 30-minute zero-TX soak",
        "duration_s": duration_s,
        "sub": {
            "snr": SUB,
            "marker_counts": dict(markers),
            "role_counts": dict(roles),
            "lbd_records": len(sub_lbd),
            "lbtx_records": len(sub_lbt),
            "accepted_beacons_start": accepted_start,
            "accepted_beacons_end": accepted_end,
            "accepted_beacons_delta": (
                accepted_end - accepted_start
                if accepted_start is not None and accepted_end is not None
                else None
            ),
            "listener_summary": sub_listener_summary,
        },
        "main": {
            "snr": MAIN,
            "lbtx_records": len(main_lbt),
            "tx_rate_hz": main_rate,
            "role_counts": dict(
                Counter(row.get("fields", {}).get("role") for row in main_stat)
            ),
            "listener_summary": main_listener_summary,
        },
        "gate": (
            "duration >=1799 s; sub marker v10.2; every reported role SLAVED; "
            "accepted-main advances; sub LBTX=0; main MAIN 9.5..10.5 Hz; "
            "collector transport clean"
        ),
        "pass": pass_gate,
        "post_pass_mode": "MODE_LISTEN remains; cold standby restored"
        if pass_gate
        else "MODE_IDLE required if any sub TX occurred",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "pass": pass_gate,
                "sub_tx": len(sub_lbt),
                "sub_roles": dict(roles),
                "main_rate_hz": main_rate,
            },
            indent=2,
        )
    )
    return 0 if pass_gate else 2


if __name__ == "__main__":
    raise SystemExit(main())
