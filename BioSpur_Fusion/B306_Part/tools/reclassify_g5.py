#!/usr/bin/env python3
"""Replay a Fusion archive with explicit boot/join evidence for G5."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from sweep_counter_rebase import (
    RebootAwareSweepCounter,
    reclassify_legacy_b306_delta,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs", required=True, type=Path)
    parser.add_argument("--analysis", required=True, type=Path)
    parser.add_argument("--events", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    analysis = json.loads(args.analysis.read_text(encoding="utf-8"))
    event_doc = json.loads(args.events.read_text(encoding="utf-8"))
    events = event_doc["events"]
    decoder = RebootAwareSweepCounter()
    event_by_name = {event["name"].upper(): event for event in events}
    for name, event in event_by_name.items():
        if event.get("qualifying", False):
            decoder.note_tag_boot_or_join(name, event["reason"])

    with args.epochs.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            decoder.observe(
                row["peer_name"],
                int(row["sweep"]),
                int(row["node_uptime_ms"]),
            )

    host = decoder.snapshot()
    raw = analysis["g5"]["fusion"]["telemetry_delta"]
    results: dict[str, object] = {}
    for name in sorted(raw):
        state = host.get(name, {})
        event = event_by_name.get(name, {})
        results[name] = {
            "event_evidence": event,
            "host_stream": state,
            "legacy_b306": reclassify_legacy_b306_delta(
                raw_reorder_delta=int(raw[name]["reorder"]),
                raw_duplicate_delta=int(raw[name]["duplicate"]),
                host_state=state,
                qualifying_boot_or_join=bool(
                    event.get("qualifying", False)
                ),
            ),
        }

    pass_reclassified = all(
        int(row["legacy_b306"]["effective_reorder"]) == 0
        and int(row["legacy_b306"]["effective_duplicate"]) == 0
        and int(row["host_stream"].get("reorders", 0)) == 0
        and int(row["host_stream"].get("duplicates", 0)) == 0
        for row in results.values()
    )
    output = {
        "source_epochs": str(args.epochs),
        "source_analysis": str(args.analysis),
        "event_ledger": str(args.events),
        "policy": (
            "Only an independently recorded tag boot/join or a B306 uptime "
            "restart arms one REBASE. Backward movement without that fact "
            "remains REORDER. Raw B306 counters are retained."
        ),
        "nodes": results,
        "reclassified_g5_pass": pass_reclassified,
        "b306_firmware_debt_remains": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0 if pass_reclassified else 2


if __name__ == "__main__":
    raise SystemExit(main())
