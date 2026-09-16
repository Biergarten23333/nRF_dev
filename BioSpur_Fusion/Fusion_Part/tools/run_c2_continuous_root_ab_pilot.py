#!/usr/bin/env python3
"""Bounded root-only Capture2 Action00 -> gap -> Action02 A/B pilot."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import struct
import time

import numpy as np

from biospur_fusion.c2_uwb_root_world.continuous_root_ab import (
    ContinuousRootAB, PelvisContinuousVQF, admit_uwb_timestamp, bootstrap_action00_root,
    uwb_row_from_event,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import LAYOUT, _clock_models
from biospur_fusion.c2_uwb_root_world.pelvis_monotone_merge import (
    CompleteWindowBarrier, PelvisTwoStreamMonotoneMerge,
)
from biospur_fusion.ingest.v47 import _imu_events, _uwb_event

try:
    from fusion_host_binary import decode_frame
except ImportError:
    from B306_Part.tools.fusion_host_binary import decode_frame


ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "datasets/phase2_calibration/phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/system/fusion_continuous/fusion_host_raw.cobs.bin"
CLOCK = ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
PELVIS = "BSFC2CC"
WINDOWS = (
    ("00_initial_still", 213_648_544, 216_084_573, 234_836_221_471_621, 234_866_246_815_581),
    ("UNASSIGNED_INTER_ACTION_GAP", 216_084_573, 216_469_127, 234_866_246_815_581, 234_882_684_960_262),
    ("02_t_pose", 216_469_127, 218_914_676, 234_882_684_960_262, 234_912_709_352_824),
)
RAW_CONTAINER_SHA256_DECLARED = "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268"


def _anchors() -> np.ndarray:
    rows = sorted(json.loads(LAYOUT.read_text())["anchors"], key=lambda row: int(row["id"]))
    if [int(row["id"]) for row in rows] != list(range(8)):
        raise RuntimeError("canonical A-H anchor inventory changed")
    return np.asarray([[row["x_mm"], row["y_mm"], row["z_mm"]] for row in rows], float) / 1000.0


def run(output: Path) -> dict:
    if output.exists():
        raise FileExistsError(output)
    output.mkdir(parents=True)
    started = time.monotonic()
    clocks = _clock_models(CLOCK)
    clock = clocks[PELVIS]
    clock_doc = json.loads(CLOCK.read_text())
    boots = {node: int(value["boot_epoch"]) for node, value in clock_doc["models"].items()}
    anchors = _anchors()
    vqf = PelvisContinuousVQF()
    ab = None
    merge = PelvisTwoStreamMonotoneMerge(
        node=PELVIS, boot_epoch=clock.boot_epoch,
        source_sha256=RAW_CONTAINER_SHA256_DECLARED,
        window_end_offset=WINDOWS[-1][2], capacity=10_000,
    )
    records = decoded = pelvis_imu = pelvis_uwb = 0
    pending = bytearray()
    pending_start = WINDOWS[0][1]
    hashes = [hashlib.sha256() for _ in WINDOWS]
    positions_a, positions_b, times = [], [], []
    decisions = []
    ineligible_uwb_reasons = {}

    def dispatch(item):
        nonlocal ab, pelvis_imu, pelvis_uwb
        event, measurement_ns = item
        if event.node_id != PELVIS:
            return
        if event.record_type.value == "IMU":
            pelvis_imu += 1
            oriented = vqf.step(
                boot=event.boot_epoch, timer_us=int(event.node_timer_us),
                acc_raw=event.payload["acc_raw"], gyro_raw=event.payload["gyro_raw"],
                preparation=measurement_ns < WINDOWS[0][3],
            )
            if oriented is None or ab is None:
                return
            acceleration, rotation = oriented
            time_s = measurement_ns * 1e-9
            if time_s <= ab.a.time_s + 1e-12:
                return
            ab.add_imu(time_s=time_s, force_sensor_mps2=acceleration,
                       rotation_world_from_sensor=rotation)
            times.append(time_s); positions_a.append(ab.a.position_m.copy()); positions_b.append(ab.b.position_m.copy())
        elif event.record_type.value == "UWB":
            pelvis_uwb += 1
            row = uwb_row_from_event(event)
            admission = admit_uwb_timestamp(row, clock)
            if not admission.eligible_for_range_solver:
                reason = admission.reason
                ineligible_uwb_reasons[reason] = ineligible_uwb_reasons.get(reason, 0) + 1
                decisions.append({"time_ns": measurement_ns, "accepted": False, "reason": reason})
                return
            if measurement_ns < WINDOWS[0][3]:
                return
            if ab is None:
                bootstrap = bootstrap_action00_root(
                    row, measurement_time_ns=measurement_ns,
                    anchors_m=anchors, clock=clock,
                )
                ab = ContinuousRootAB(bootstrap)
                decisions.append({"time_ns": measurement_ns, "accepted": True, "reason": "COMMON_DIAGNOSTIC_BOOTSTRAP"})
            else:
                decision = ab.add_uwb(
                    row, measurement_time_ns=measurement_ns,
                    anchors_m=anchors, clock=clock,
                )
                if decision is not None:
                    decisions.append({"time_ns": measurement_ns, "accepted": bool(decision.accepted), "reason": decision.reason})

    with RAW.open("rb") as source:
        source.seek(WINDOWS[0][1])
        cursor = WINDOWS[0][1]
        while cursor < WINDOWS[-1][2]:
            data = source.read(min(1 << 20, WINDOWS[-1][2] - cursor))
            if not data:
                raise RuntimeError("pilot window ended early")
            for index, window in enumerate(WINDOWS):
                left, right = max(cursor, window[1]), min(cursor + len(data), window[2])
                if right > left:
                    hashes[index].update(data[left - cursor:right - cursor])
            pending.extend(data); cursor += len(data)
            while (boundary := pending.find(0)) >= 0:
                encoded = bytes(pending[:boundary]); record_start = pending_start
                del pending[:boundary + 1]; pending_start = record_start + boundary + 1
                if not encoded:
                    continue
                records += 1
                frame = decode_frame(encoded)
                if frame.kind not in (1, 3):
                    continue
                boot = boots[frame.node_name]
                provenance = (records, record_start, pending_start, encoded)
                events = tuple(_imu_events(frame, boot, provenance)) if frame.kind == 3 else (_uwb_event(frame, boot, provenance),)
                for event in events:
                    decoded += 1
                    if event.node_id != PELVIS:
                        continue
                    common_ns = int(round(clocks[event.node_id].seconds(int(event.node_timer_us)) * 1e9))
                    progress_ns = common_ns
                    if event.record_type.value == "UWB":
                        row = uwb_row_from_event(event)
                        admission = admit_uwb_timestamp(row, clock)
                        common_ns = admission.dispatch_measurement_ns
                        progress_ns = admission.stream_progress_ns
                    if progress_ns < WINDOWS[0][3] - 5_100_000_000 or progress_ns >= WINDOWS[-1][4]:
                        continue
                    for ready in merge.submit(
                        event, measurement_time_ns=common_ns,
                        stream_progress_ns=progress_ns,
                    ):
                        dispatch((ready.event, ready.measurement_time_ns))
            if len(pending) > 4096:
                raise RuntimeError("bounded streaming capacity exceeded")
    if pending:
        raise RuntimeError("pilot bound ends inside a COBS record")
    for ready in merge.finish(CompleteWindowBarrier(
        RAW_CONTAINER_SHA256_DECLARED, WINDOWS[-1][2], True,
    )):
        dispatch((ready.event, ready.measurement_time_ns))
    if ab is None or len(times) < 100:
        raise RuntimeError("pilot produced no qualified root trajectory")
    pa, pb = np.stack(positions_a), np.stack(positions_b)
    np.savez_compressed(output / "ROOT_AB_PILOT.npz", time_s=np.asarray(times), root_a_world_m=pa, root_b_world_m=pb, anchors_world_m=anchors)
    metrics = ab.metrics()
    result = {
        "schema": "biospur.c2.continuous_root_ab.pilot.v1", "status": "DIAGNOSTIC_PASS",
        "scientific_pass": False, "raw_container_sha256_declared_not_recomputed": RAW_CONTAINER_SHA256_DECLARED,
        "window_hashes": {row[0]: digest.hexdigest() for row, digest in zip(WINDOWS, hashes)},
        "records": records, "decoded_events": decoded, "pelvis_imu": pelvis_imu, "pelvis_uwb": pelvis_uwb,
        "vqf_instances": 1, "vqf_resets": 0, "vqf_gap_count": len(vqf.gaps),
        "maximum_vqf_gap_us": max((right-left for left, right in vqf.gaps), default=0),
        "merge_submitted": merge.submitted, "merge_dispatched": merge.dispatched,
        "merge_pending_after_barrier": merge.pending,
        "a_uwb_commits": metrics.a_uwb_commits, "b_uwb_accepted": metrics.b_uwb_accepted,
        "b_uwb_rejected": metrics.b_uwb_rejected, "decision_reasons": decisions,
        "ineligible_uwb_reasons": ineligible_uwb_reasons,
        "a_endpoint_displacement_m": float(np.linalg.norm(pa[-1]-pa[0])),
        "b_endpoint_displacement_m": float(np.linalg.norm(pb[-1]-pb[0])),
        "ab_endpoint_separation_m": float(np.linalg.norm(pb[-1]-pa[-1])),
        "wall_s": time.monotonic()-started,
        "rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    (output / "RESULT.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(); parser.add_argument("--output", required=True, type=Path)
    print(json.dumps(run(parser.parse_args().output), indent=2))
