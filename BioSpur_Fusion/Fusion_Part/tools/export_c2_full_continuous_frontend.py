#!/usr/bin/env python3
"""Export the authenticated full stream with fixed initial calibration and VQF.

This is not the action-only progressive posterior or a recalibration result.
Labels never select samples, reset orientation, or overwrite quaternions.
"""
from __future__ import annotations

import argparse
import bisect
from array import array
from collections import Counter
import hashlib
from importlib.metadata import version
import json
from pathlib import Path

import numpy as np
from vqf import VQF

from biospur_fusion.c2_coupled_progressive.continuous_full_session_reader import FullSessionContinuousReader
from biospur_fusion.c2_coupled_progressive.contracts import NODE_TO_SEGMENT, ROOT
from biospur_fusion.v0.c2_progressive.orientation import ACC_SCALE, GYRO_SCALE, contiguous_span_ids
from tools.build_c2_full_session_ten_node_ab import _static_inputs

INITIAL = ROOT / "logs/c2_basis_progressive_20260829T102836Z/P1_FRONTEND/P1_INITIAL_STILL_STOCHASTIC_STATE.json"
INITIAL_SHA = "b8bc725303e67eb9cad38e11627739a4774fd7832d5f4b0b21e09f4c1f9df377"
FIELDS = ("time_us", "boot_epoch", "common_global_ns", "availability_global_ns",
          "sequence", "record_index", "start_offset", "end_offset", "sample_index")


def raw_region_index(regions, starts, raw):
    index = bisect.bisect_right(starts, raw.start_offset) - 1
    if index < 0:
        raise ValueError("raw record precedes source regions")
    region = regions[index]
    if not region.start_offset <= raw.start_offset < raw.end_offset <= region.stop_offset:
        raise ValueError("raw record is outside a complete source region")
    return index


def orient_continuous(time_us, boot, acc_raw, gyro_raw, gyro_bias):
    """One VQF instance; missing ticks produce span metadata, never fake updates."""
    time_us = np.asarray(time_us, dtype=np.int64)
    boot = np.asarray(boot, dtype=np.int64)
    if len(time_us) == 0 or np.any(np.diff(time_us) <= 0) or len(np.unique(boot)) != 1:
        raise ValueError("continuous export requires nonempty monotonic single-boot input")
    acc = np.ascontiguousarray(np.asarray(acc_raw, float) * ACC_SCALE)
    gyro = np.ascontiguousarray(np.asarray(gyro_raw, float) * GYRO_SCALE - gyro_bias)
    if acc.shape != (len(time_us), 3) or gyro.shape != acc.shape:
        raise ValueError("IMU array shape mismatch")
    if not np.isfinite(acc).all() or not np.isfinite(gyro).all():
        raise ValueError("nonfinite IMU input")
    spans = contiguous_span_ids(time_us, boot)
    vqf = VQF(0.005, magDistRejectionEnabled=False)
    quaternion = np.empty((len(time_us), 4))
    boundaries = np.r_[0, np.flatnonzero(np.diff(spans)) + 1, len(time_us)]
    for left, right in zip(boundaries[:-1], boundaries[1:]):
        quaternion[left:right] = vqf.updateBatch(gyro[left:right], acc[left:right])["quat6D"]
    return acc, gyro, quaternion, spans


def export(output: Path) -> dict:
    output = output.resolve()
    if ROOT not in output.parents or output.exists():
        raise ValueError("output must be a new directory beneath Fusion_Part")
    initial_bytes = INITIAL.read_bytes()
    if hashlib.sha256(initial_bytes).hexdigest() != INITIAL_SHA:
        raise RuntimeError("fixed initial calibration identity changed")
    initial = json.loads(initial_bytes)
    clock, _, _, _, _ = _static_inputs()
    reader = FullSessionContinuousReader(root=ROOT, clock_owner=clock)
    output.mkdir()
    buffers = {node: {**{name: array("q") for name in FIELDS},
                       "acc_raw": array("h"), "gyro_raw": array("h")}
               for node in NODE_TO_SEGMENT}
    counts = Counter()
    uwb_count = 0
    region_starts = tuple(region.start_offset for region in reader.inventory.regions)
    last_region = None
    with (output / "UWB.jsonl").open("x") as uwb:
        def consume(events):
            nonlocal uwb_count, last_region
            for event in events:
                row, raw = event.payload_owner, event.payload_owner.raw
                region_index = raw_region_index(reader.inventory.regions, region_starts, raw)
                region = reader.inventory.regions[region_index]
                if region.region_id != last_region:
                    last_region = region.region_id
                    print(f"SOURCE_REGION {region_index + 1}/37 {last_region}", flush=True)
                counts[(region.region_id, event.kind, row.node_id)] += 1
                metadata = (row.node_timer_us, row.boot_epoch, event.common_global_ns,
                            event.availability_global_ns, row.sequence, raw.record_index,
                            raw.start_offset, raw.end_offset, raw.sample_index)
                if event.kind == "IMU":
                    target = buffers[row.node_id]
                    for key, value in zip(FIELDS, metadata):
                        target[key].append(value)
                    target["acc_raw"].extend(row.payload["acc_raw"])
                    target["gyro_raw"].extend(row.payload["gyro_raw"])
                else:
                    uwb.write(json.dumps({"node": row.node_id, **dict(zip(FIELDS, metadata)),
                                          "encoded_sha256": raw.encoded_sha256,
                                          "region_id": region.region_id,
                                          "payload": dict(row.payload)}, separators=(",", ":")) + "\n")
                    uwb_count += 1
        audit = reader.consume_record_batches(lambda ticket: ticket.deliver(consume))
    node_counts = {}
    for node, buffers_for_node in buffers.items():
        values = {name: np.asarray(value) for name, value in buffers_for_node.items()}
        for name in ("acc_raw", "gyro_raw"):
            values[name] = values[name].reshape(-1, 3)
        bias = np.asarray(initial["nodes"][node]["gyro_bias_rad_s"], float)
        acc, gyro, quat, spans = orient_continuous(values["time_us"], values["boot_epoch"],
            values["acc_raw"], values["gyro_raw"], bias)
        np.savez(output / f"{node}.npz", **values, acc_mps2=acc, gyro_rads=gyro,
                 quat_vqf_sensor_wxyz=quat, contiguous_span_id=spans)
        node_counts[node] = {"imu_samples": len(acc), "true_timer_gap_edges": int(np.count_nonzero(np.diff(spans)))}
    result = {"schema": "biospur.c2.full-continuous-fixed-initial-frontend.v1",
              "role": "DIAGNOSTIC_FIXED_INITIAL_GYRO_BIAS_SI_ACCEL_CONTINUOUS_VQF",
              "calibration": {"path": str(INITIAL), "sha256": INITIAL_SHA,
                              "progressive_posterior_applied": False},
              "action_labels_control_updates": False, "pose_archive_consumed": False,
              "acceleration_convention": "SI_SPECIFIC_FORCE_GRAVITY_RETAINED_NO_ACCEL_BIAS_REMOVAL",
              "orientation_convention": "VQF_QUAT6D_SENSOR_TO_INERTIAL_WXYZ_FREE_YAW",
              "vqf_version": version("vqf"), "vqf_default_residual_bias_estimation": True,
              "vqf_states_per_node": 1, "source": dict(audit.access_audit),
              "nodes": node_counts, "uwb_records": uwb_count,
              "regions": [{"region_id": region.region_id, "kind": region.kind,
                            "action_id": region.action_id, "action_index": region.action_index,
                            "start_offset": region.start_offset, "stop_offset": region.stop_offset,
                            "start_ns": region.start_ns, "stop_ns": region.stop_ns,
                            "counts": {node: {kind: counts[(region.region_id, kind, node)]
                                              for kind in ("IMU", "UWB")}
                                       for node in NODE_TO_SEGMENT}}
                           for region in reader.inventory.regions]}
    (output / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    print(json.dumps(export(arguments.output), indent=2))
