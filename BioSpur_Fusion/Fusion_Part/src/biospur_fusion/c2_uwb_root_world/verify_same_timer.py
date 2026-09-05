"""Verify that decoded C2 UWB and IMU share one B306 TIMER2 clock model."""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from biospur_fusion.ingest.events import RecordType
from biospur_fusion.ingest.v47 import decode_measurements


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_same_timer(raw_path: Path, clock_table: Path) -> dict:
    raw_path = Path(raw_path).resolve()
    clock_table = Path(clock_table).resolve()
    clock_document = json.loads(clock_table.read_text())
    contract = clock_document.get("clock_contract", {})
    if contract.get("measurement_time_source") != "B306_TIMER2":
        raise ValueError("clock table is not owned by B306 TIMER2")
    required = {"uwb.strobe_us", "uwb.frame_us", "imu.base_us", "imu.trigger_us"}
    if set(contract.get("same_model_applies_to", [])) != required:
        raise ValueError("clock table does not bind UWB and IMU to one model")
    if contract.get("accepted_listener_record_kinds") != ["LBD"]:
        raise ValueError("non-beacon Listener records entered the clock table")

    events, decode_audit = decode_measurements(raw_path)
    grouped = defaultdict(lambda: {RecordType.IMU: [], RecordType.UWB: []})
    for event in events:
        if event.record_type in (RecordType.IMU, RecordType.UWB):
            grouped[event.node_id][event.record_type].append(event)

    nodes = {}
    for node, streams in sorted(grouped.items()):
        if node not in clock_document["models"]:
            raise ValueError(f"clock model missing for {node}")
        model = clock_document["models"][node]
        a_ns_per_us = float(model["a_ns_per_us"])
        b_ns = float(model["b_ns"])
        imu = sorted(streams[RecordType.IMU], key=lambda row: row.node_timer_us)
        uwb = sorted(streams[RecordType.UWB], key=lambda row: row.node_timer_us)
        imu_timer = [int(row.node_timer_us) for row in imu]
        uwb_timer = [int(row.node_timer_us) for row in uwb]
        if not imu_timer or not uwb_timer:
            raise ValueError(f"{node} lacks one measurement stream")
        mapped_imu = [round(a_ns_per_us * value + b_ns) for value in imu_timer]
        mapped_uwb = [round(a_ns_per_us * value + b_ns) for value in uwb_timer]
        nearest_us = []
        identity_error_ns = []
        for local_uwb, global_uwb in zip(uwb_timer, mapped_uwb):
            position = bisect.bisect_left(imu_timer, local_uwb)
            candidates = [index for index in (position - 1, position)
                          if 0 <= index < len(imu_timer)]
            nearest = min(candidates, key=lambda index: abs(imu_timer[index] - local_uwb))
            local_delta = local_uwb - imu_timer[nearest]
            global_delta = global_uwb - mapped_imu[nearest]
            nearest_us.append(abs(local_delta))
            identity_error_ns.append(abs(global_delta - a_ns_per_us * local_delta))
        boot_epochs = sorted({row.boot_epoch for row in imu + uwb})
        gap_count = sum(value > 2_600 for value in nearest_us)
        covered_sweeps = sum(min(imu_timer) <= value <= max(imu_timer) for value in uwb_timer)
        nodes[node] = {
            "imu_samples": len(imu),
            "uwb_sweeps": len(uwb),
            "boot_epochs": boot_epochs,
            "boot_zero_only": boot_epochs == [0],
            "uwb_sweeps_inside_imu_timer_coverage": int(covered_sweeps),
            "uwb_inside_imu_timer_coverage_fraction": covered_sweeps / len(uwb_timer),
            "mapped_time_strictly_monotonic": (
                all(left < right for left, right in zip(mapped_imu, mapped_imu[1:]))
                and all(left < right for left, right in zip(mapped_uwb, mapped_uwb[1:]))
            ),
            "nearest_imu_local_delta_p95_us": float(np.percentile(nearest_us, 95)),
            "nearest_imu_local_delta_max_us": int(max(nearest_us)),
            "uwb_sweeps_over_2_6ms_from_an_imu_sample": int(gap_count),
            "same_affine_map_identity_max_abs_ns": float(max(identity_error_ns)),
        }

    gate = {
        "all_10_nodes_present": len(nodes) == 10,
        "all_nodes_have_imu_and_uwb": all(
            row["imu_samples"] > 0 and row["uwb_sweeps"] > 0 for row in nodes.values()
        ),
        "all_boot_epoch_zero": all(row["boot_zero_only"] for row in nodes.values()),
        "uwb_inside_imu_timer_coverage_ge_99pct": all(
            row["uwb_inside_imu_timer_coverage_fraction"] >= 0.99 for row in nodes.values()
        ),
        "all_mapped_streams_strictly_monotonic": all(
            row["mapped_time_strictly_monotonic"] for row in nodes.values()
        ),
        "same_affine_map_identity_max_le_1_1ns": all(
            row["same_affine_map_identity_max_abs_ns"] <= 1.1 for row in nodes.values()
        ),
        "nearest_imu_p95_le_half_sample_period": all(
            row["nearest_imu_local_delta_p95_us"] <= 2_600 for row in nodes.values()
        ),
    }
    gate["pass"] = all(gate.values())
    return {
        "schema": "biospur.c2.uwb_imu_same_timer_verification.v1",
        "input": {"path": str(raw_path), "sha256": _sha256(raw_path)},
        "clock_table": {"path": str(clock_table), "sha256": _sha256(clock_table)},
        "decode_audit": decode_audit.__dict__,
        "contract": {
            "uwb_measurement_timer": "B306 TIMER2 hardware-captured READY strobe_us",
            "imu_measurement_timer": "B306 TIMER2 hardware-trigger base_us + delta_us",
            "mapping": "identical per-node/boot global_ns = a*timer_us+b",
            "host_receipt_time_used_as_measurement_time": False,
            "listener_kinds_used": ["LBD"],
            "listener_host_time_role": "discrete superframe integer association only",
            "sampling_gap_policy": "report separately; never retime either stream",
        },
        "nodes": nodes,
        "gate": gate,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--clock-table", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    document = verify_same_timer(args.raw, args.clock_table)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(json.dumps(document["gate"], indent=2))
    if not document["gate"]["pass"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
