#!/usr/bin/env python3
"""Extract Phase C-F timing evidence from a Phase-C raw CDC capture."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fusion_session import parse_fields
from phase_c_long_validation import distribution, imu_timing


def capture_lines(raw_log: Path) -> list[str]:
    active = False
    result: list[str] = []
    for raw in raw_log.read_text(errors="replace").splitlines():
        if " FUSION_RX " in raw:
            payload = raw.split(" FUSION_RX ", 1)[1]
            if payload.startswith("FUSION_REPLY ") and (
                "text=IMU START OK " in payload
            ):
                active = True
                continue
            if active:
                result.append(payload)
        elif active and " FUSION_TX " in raw and raw.endswith(" IMU STOP"):
            break
    if not result:
        raise ValueError("capture window not found")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw_log", type=Path)
    parser.add_argument("--node-elapsed-s", type=float)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    lines = capture_lines(args.raw_log)
    node_elapsed_s = args.node_elapsed_s
    if node_elapsed_s is None:
        analysis_path = args.raw_log.with_name("analysis.json")
        analysis = json.loads(analysis_path.read_text())
        node_elapsed_s = float(analysis["node_elapsed_s"])

    uwb = [line for line in lines if line.startswith("FUSION_UWB ")]
    accepted_deltas: list[int] = []
    attributable_deltas: list[int] = []
    derived_scheduler_deltas: list[int] = []
    for line in uwb:
        fields = parse_fields(line)
        encoded = fields.get("pair_dt_us")
        if encoded not in (None, "-"):
            delta = int(encoded, 0)
            accepted_deltas.append(delta)
            attributable_deltas.append(delta)
        elif (
            fields.get("verdict") == "b306_missed_edge"
            and fields.get("last_orphan_us") not in (None, "-")
        ):
            delta = (
                int(fields["frame_us"], 0)
                - int(fields["last_orphan_us"], 0)
            )
            attributable_deltas.append(delta)
            derived_scheduler_deltas.append(delta)

    result = {
        "source": str(args.raw_log),
        "capture_uwb_records": len(uwb),
        "accepted_pair_delta": distribution(accepted_deltas),
        "full_attributable_pair_delta": distribution(attributable_deltas),
        "derived_parser_scheduler_outliers_us": derived_scheduler_deltas,
        "imu_timing": imu_timing(lines),
        "health_check": {
            "period_us": 50000,
            "measured_read_duration_us": 383,
            "estimated_checks_over_capture": round(node_elapsed_s / 0.050),
            "estimated_bus_time_s": (
                round(node_elapsed_s / 0.050) * 383 / 1_000_000
            ),
            "estimated_wall_fraction_percent": (
                round(node_elapsed_s / 0.050)
                * 383
                / 1_000_000
                / node_elapsed_s
                * 100
            ),
        },
        "pair_window_decision": {
            "old_window_us": 20000,
            "new_window_us": 50000,
            "sweep_period_us": 100000,
            "p99_headroom_us": (
                50000
                - float(distribution(accepted_deltas)["p99_us"] or 0)
            ),
            "adjacent_sweep_ambiguity": (
                "50 ms is one half of the measured 100 ms sweep period; "
                "an adjacent sweep cannot enter the pairing window"
            ),
        },
    }
    rendered = json.dumps(result, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.write_text(rendered)
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
