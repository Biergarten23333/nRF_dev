#!/usr/bin/env python3
"""One-shot bounded real-slice transport gate for Capture2 00/gap/02.

This executable opens only the preregistered byte interval.  It validates and
decodes that bounded interval, then fails closed before estimator construction
when the frozen upstream native-200 publication inventory cannot own a decoded
event.  It never substitutes a held pose for an unowned gap publication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import resource
import time

import numpy as np

from biospur_fusion.c2_coupled_progressive.continuous_streaming_runner import (
    AuthorizedByteWindow,
    IncrementalV47WindowDecoder,
)
from biospur_fusion.c2_coupled_progressive.contracts import EPISODES
from biospur_fusion.c2_uwb_root_world.run_calibration import _clock_models
from biospur_fusion.ingest.events import RecordType


ROOT = Path(__file__).resolve().parents[1]
PREREG_SHA256 = "d05195e331549f3c89d6a3f295f5c5f0a1caa696d22b137af3ed4c43fe3e47bc"
WINDOWS = (
    ("00_initial_still", 213_648_544, 216_084_573, 1_169_193,
     234_836_221_471_621, 234_866_246_815_581,
     "ee08b44c3383e74099dc80a5c92bfaef500fa6bb472bc774655aa5b845485d5b"),
    ("UNASSIGNED_INTER_ACTION_GAP", 216_084_573, 216_469_127, 1_182_568,
     234_866_246_815_581, 234_882_684_960_262, None),
    ("02_t_pose", 216_469_127, 218_914_676, 1_184_677,
     234_882_684_960_262, 234_912_709_352_824,
     "6954b0bd7d9f14db3b811af43cd9ca2494292568e4aead32d2227b6e4e407581"),
)
RAW_RELATIVE = Path(
    "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2/"
    "system/fusion_continuous/fusion_host_raw.cobs.bin"
)
CLOCK_RELATIVE = Path(
    "logs/c2_uwb_beacon_clock_20260903_141552/CLOCK_TABLE_CALIBRATION_ONLY.json"
)
TRAJECTORY_RELATIVE = Path(
    "logs/c2_native200_orientation_constrained_biomechanics_v4_20260904/"
    "ARTICULATED_CALIBRATION_TRAJECTORY.npz"
)
EXPECTED_CLOCK_SHA256 = "b3c18d2d0ece3826498d2adc3cd41f3e4412794557f8525adc2f73bfa4ae3a66"
EXPECTED_TRAJECTORY_SHA256 = "94f9afb088c7f05a7dbcae0c7d6d2c18be76a6ca32e1d9b96861a8deb7962937"
MAXIMUM_BYTES = 5_266_132
CHUNK_BYTES = 1 << 20
MAXIMUM_EVENTS = 150_000


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(CHUNK_BYTES), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path, required=True)
    arguments = parser.parse_args()
    evidence = arguments.evidence.resolve()
    prereg = evidence / "PREREGISTRATION.json"
    if _sha256(prereg) != PREREG_SHA256:
        raise RuntimeError("sealed preregistration digest mismatch")
    raw = ROOT / RAW_RELATIVE
    if raw.stat().st_size != 305_368_868:
        raise RuntimeError("declared raw container size mismatch")
    clock_path = ROOT / CLOCK_RELATIVE
    trajectory_path = ROOT / TRAJECTORY_RELATIVE
    if _sha256(clock_path) != EXPECTED_CLOCK_SHA256:
        raise RuntimeError("clock owner changed")
    if _sha256(trajectory_path) != EXPECTED_TRAJECTORY_SHA256:
        raise RuntimeError("frozen native200 trajectory changed")

    started = time.monotonic()
    combined_hash = hashlib.sha256()
    window_payloads: list[tuple[tuple, bytes, str]] = []
    total_read = 0
    open_count = 0
    with raw.open("rb") as source:
        open_count += 1
        source.seek(WINDOWS[0][1])
        cursor = WINDOWS[0][1]
        for row in WINDOWS:
            name, start, stop, *_rest = row
            if cursor != start:
                raise RuntimeError("preregistered windows are not contiguous")
            digest = hashlib.sha256()
            payload = bytearray()
            remaining = stop - start
            while remaining:
                block = source.read(min(CHUNK_BYTES, remaining))
                if not block:
                    raise RuntimeError("bounded raw window ended early")
                digest.update(block)
                combined_hash.update(block)
                payload.extend(block)
                cursor += len(block)
                total_read += len(block)
                remaining -= len(block)
                if total_read > MAXIMUM_BYTES:
                    raise RuntimeError("bounded raw byte cap exceeded")
            actual = digest.hexdigest()
            expected = row[-1]
            if expected is not None and actual != expected:
                raise RuntimeError(f"{name} frozen slice hash mismatch")
            window_payloads.append((row, bytes(payload), actual))
            del payload
    if open_count != 1 or total_read != MAXIMUM_BYTES:
        raise RuntimeError("raw access was not the exact one-open bounded interval")

    clock_document = json.loads(clock_path.read_text(encoding="utf-8"))
    boots = {
        f"{node}:{kind}": int(model["boot_epoch"])
        for node, model in clock_document["models"].items()
        for kind in (1, 3)
    }
    decoded = []
    window_audit = []
    for row, payload, actual_hash in window_payloads:
        name, start, stop, first_record, start_ns, stop_ns, _expected = row
        authorization = AuthorizedByteWindow(
            str(RAW_RELATIVE),
            "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268",
            start, stop, first_record, actual_hash, start_ns, stop_ns, boots, 4096,
        )
        decoder = IncrementalV47WindowDecoder(authorization)
        rows = []
        for offset in range(0, len(payload), CHUNK_BYTES):
            rows.extend(decoder.feed(
                payload[offset:offset + CHUNK_BYTES], absolute_offset=start + offset,
            ))
        decoder.finish()
        decoded.extend(rows)
        window_audit.append({
            "region": name,
            "start_byte_inclusive": start,
            "stop_byte_exclusive": stop,
            "bytes": stop - start,
            "sha256": actual_hash,
            "decoded_events": len(rows),
            "decoded_imu": sum(item.record_type is RecordType.IMU for item in rows),
            "decoded_uwb": sum(item.record_type is RecordType.UWB for item in rows),
            "decoded_pelvis_imu": sum(
                item.record_type is RecordType.IMU and item.node_id == "BSFC2CC"
                for item in rows
            ),
            "maximum_pending_record_bytes": decoder.maximum_pending_bytes,
        })
    if len(decoded) > MAXIMUM_EVENTS:
        raise RuntimeError("decoded event cap exceeded")

    clocks = _clock_models(clock_path)
    region_owned_counts = {row[0]: {"IMU": 0, "UWB": 0} for row in WINDOWS}
    outside = []
    gap_pelvis = []
    for event in decoded:
        common_ns = int(round(clocks[event.node_id].seconds(event.node_timer_us) * 1e9))
        owners = [row for row in WINDOWS if row[4] <= common_ns < row[5]]
        if len(owners) != 1:
            outside.append((event.node_id, event.sequence, common_ns))
            continue
        region = owners[0][0]
        kind = "IMU" if event.record_type is RecordType.IMU else "UWB"
        region_owned_counts[region][kind] += 1
        if region == "UNASSIGNED_INTER_ACTION_GAP" and kind == "IMU" and event.node_id == "BSFC2CC":
            gap_pelvis.append({
                "sequence": int(event.sequence),
                "timer2_us": int(event.node_timer_us),
                "common_global_ns": common_ns,
                "raw_record_index": int(event.raw.record_index),
                "raw_start_offset": int(event.raw.start_offset),
                "raw_end_offset": int(event.raw.end_offset),
            })

    with np.load(trajectory_path, allow_pickle=False) as trajectory:
        trajectory_keys = sorted({
            key.split("/")[1] for key in trajectory.files if key.startswith("trajectory/")
        })
    expected_keys = [f"{index:02d}" for index in range(len(EPISODES))]
    if trajectory_keys != expected_keys:
        raise RuntimeError("frozen trajectory action inventory changed")

    status = "STOP"
    boundary = "BLOCKED_AT_MISSING_FROZEN_NATIVE200_GAP_POSE_FK_CONTACT_PUBLICATION"
    if not gap_pelvis:
        boundary = "BLOCKED_AT_GAP_HAS_NO_PELVIS_NATIVE200_SOURCE_EVENT"
    result = {
        "schema": "biospur.c2.real_continuous_00_gap_02.result.v1",
        "status": status,
        "scientific_pass": False,
        "exact_boundary": boundary,
        "reason": (
            "The bounded real slice contains pelvis native200 source events in the "
            "separately owned inter-action gap, but the frozen accepted publisher "
            "inventory contains only the 19 acquired action trajectories. The existing "
            "authoritative Action04 publisher requires an acquired action identity and "
            "has no source-owned gap pose/FK/contact publication. Holding either endpoint "
            "or recomputing a gap pose would be inference and is forbidden."
        ),
        "raw_access": {
            "path": str(RAW_RELATIVE),
            "container_declared_sha256_not_recomputed": (
                "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268"
            ),
            "container_size_bytes": raw.stat().st_size,
            "open_count": open_count,
            "start_byte_inclusive": WINDOWS[0][1],
            "stop_byte_exclusive": WINDOWS[-1][2],
            "bytes_read": total_read,
            "combined_bounded_sha256": combined_hash.hexdigest(),
            "full_container_read_or_hash": False,
        },
        "windows": window_audit,
        "region_owned_counts": region_owned_counts,
        "decoded_event_count": len(decoded),
        "events_outside_formal_common_regions": len(outside),
        "outside_examples": outside[:10],
        "gap_pelvis_native200_count": len(gap_pelvis),
        "first_gap_pelvis_native200": gap_pelvis[0] if gap_pelvis else None,
        "last_gap_pelvis_native200": gap_pelvis[-1] if gap_pelvis else None,
        "frozen_trajectory_action_keys": trajectory_keys,
        "frozen_trajectory_gap_key_present": False,
        "estimator_constructed": False,
        "ab_fork_constructed": False,
        "candidate_parity_available": False,
        "uwb_commits_attempted": 0,
        "finish_called": False,
        "viewer_or_hxx": False,
        "wall_seconds": time.monotonic() - started,
        "maximum_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    _write_json(evidence / "RESULT.json", result)
    _write_json(evidence / "RAW_ACCESS_AUDIT.json", result["raw_access"])
    _write_json(evidence / "DECODE_AUDIT.json", {
        "windows": window_audit,
        "region_owned_counts": region_owned_counts,
        "events_outside_formal_common_regions": len(outside),
        "gap_pelvis_native200_count": len(gap_pelvis),
        "first_gap_pelvis_native200": gap_pelvis[0] if gap_pelvis else None,
        "last_gap_pelvis_native200": gap_pelvis[-1] if gap_pelvis else None,
    })
    print(json.dumps(result, sort_keys=True))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
