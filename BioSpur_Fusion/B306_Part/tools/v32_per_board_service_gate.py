#!/usr/bin/env python3
"""Prepare and prove one v32 batch link before its 120-second sanity run.

The D5/F4 thresholds are valid only in the production central schedule.  This
runner therefore performs the authorized OFF->ON/5000 rebuild first, verifies
the current generation on LIST and every kind-7 record, then measures and
redraws only the named link.  It never performs OTA or starts an IMU stream.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from fusion_session import SessionError
from v32_service_gate import ServiceGate


REMAINING_NINE = (
    "BSF3C79",
    "BSFC2CC",
    "BSF44AD",
    "BSF6C53",
    "BSF1120",
    "BSF31CC",
    "BSFAA61",
    "BSFEC35",
    "BSFB165",
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--node", required=True, choices=REMAINING_NINE)
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--max-redraws", type=int, default=3)
    parser.add_argument("--force-redraw-first", action="store_true")
    parser.add_argument("--measure-s", type=float, default=12.0)
    args = parser.parse_args()
    if not 0 <= args.max_redraws <= 3:
        raise SystemExit("max redraws must be in 0..3")
    if args.measure_s < 10.0:
        raise SystemExit("F4 measurement must be at least 10 seconds")

    gate = ServiceGate(args.out_dir, args.fusion_port, REMAINING_NINE)
    passed = False
    try:
        gate.open()
        gate.evidence["target"] = args.node
        gate.evidence["max_redraws_for_target"] = args.max_redraws
        gate.evidence["force_redraw_first"] = args.force_redraw_first
        gate.evidence["fleet_initial"] = gate.fleet_ready()
        gate.rebuild_production_spacing()

        if args.force_redraw_first:
            if args.max_redraws < 1:
                raise SessionError("forced redraw requires max-redraws >= 1")
            gate.evidence["forced_redraw"] = gate.redraw(args.node)
            gate.evidence["fleet_after_forced_redraw"] = gate.fleet_ready()

        current = gate.measure("service_check_0", args.measure_s)
        while (
            current["table"][args.node]["class"] != "FULL"
            and int(gate.evidence["redraws"][args.node]) < args.max_redraws
        ):
            gate.redraw(args.node)
            gate.fleet_ready()
            redraw_count = int(gate.evidence["redraws"][args.node])
            current = gate.measure(
                f"service_check_after_redraw_{redraw_count}", args.measure_s
            )

        target = current["table"][args.node]
        gate.evidence["final_table"] = current["table"]
        gate.evidence["target_final"] = target
        passed = target["class"] == "FULL"
        gate.evidence["verdict"] = "PASS" if passed else "STOP"
        gate.checkpoint()
        return 0 if passed else 2
    except Exception as exc:
        gate.evidence["verdict"] = "STOP"
        gate.evidence["error"] = f"{type(exc).__name__}: {exc}"
        if gate.root.exists():
            gate.checkpoint()
        return 2
    finally:
        gate.close()
        if gate.root.exists():
            gate.checkpoint()


if __name__ == "__main__":
    raise SystemExit(main())
