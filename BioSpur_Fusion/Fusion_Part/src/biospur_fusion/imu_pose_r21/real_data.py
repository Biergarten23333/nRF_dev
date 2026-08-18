from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import struct
from typing import Iterable, Mapping

import numpy as np

from biospur_fusion.calibration_v2.phase2r.decoder import (
    CRC, HEADER, KIND_IMU, KIND_UWB, MAGIC, VERSION, cobs_decode, crc16_ccitt_false,
)
from biospur_fusion.imu_pose_v2.types import ImuObservation


EXPECTED_NODES = frozenset({
    "BSF1120", "BSF31CC", "BSF3C79", "BSF44AD", "BSF6C53",
    "BSF8BC4", "BSFAA61", "BSFB165", "BSFC2CC", "BSFEC35",
})


@dataclass(frozen=True, slots=True)
class CacheRow:
    action_id: str
    phase: str
    split_class: str
    cycle_id: str
    node_id: str
    boot_epoch: int
    timer2_us: int
    common_time_ns: int
    sequence: int
    gyro_rad_s: np.ndarray
    accel_m_s2: np.ndarray
    source_record_offset: int
    source_record_length: int

    @property
    def uid(self) -> str:
        return f"{self.node_id}:{self.boot_epoch}:{self.timer2_us}:{self.sequence}:{self.source_record_offset}"

    def observation(self) -> ImuObservation:
        return ImuObservation(
            self.node_id, self.boot_epoch, self.timer2_us, self.common_time_ns,
            self.sequence, self.gyro_rad_s.copy(), self.accel_m_s2.copy(),
            (0, 5000), self.source_record_offset, self.split_class,
        )


def sha256(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            value.update(chunk)
    return value.hexdigest()


def _clock_models(context: Mapping) -> dict[str, list[dict]]:
    by_node: dict[str, list[dict]] = {}
    for row in context["clock_models"].values():
        by_node.setdefault(row["hardware_node_id"], []).append(row)
    for rows in by_node.values():
        rows.sort(key=lambda row: (row["boot_epoch"], row["first_timer2_us"]))
    return by_node


def _map_time(node: str, timer2_us: int, models: Mapping[str, list[dict]]) -> tuple[int, int, int]:
    candidates = [row for row in models[node] if row["first_timer2_us"] <= timer2_us <= row["last_timer2_us"]]
    if not candidates:
        candidates = sorted(models[node], key=lambda row: min(abs(timer2_us-row["first_timer2_us"]), abs(timer2_us-row["last_timer2_us"])))[:1]
    row = candidates[0]
    value = Fraction(int(row["a_ns_per_us_numerator"])*int(timer2_us), int(row["a_ns_per_us_denominator"]))
    mapped = (value.numerator*2+value.denominator)//(2*value.denominator)+int(row["b_ns"])
    return int(mapped), int(row["boot_epoch"]), int(row["clock_segment"])


def _classify(action: str, phase: str, time_ns: int, formal_interval: tuple[int, int], policy: Mapping) -> tuple[str, str]:
    if phase != "FORMAL_ACTION":
        return "PROPAGATION_ONLY", "NONE"
    start, stop = formal_interval
    if action == policy["final_still"]:
        return "CALIBRATION_VALIDATION", "FINAL"
    duration = stop-start
    if action in policy["static_actions"]:
        fraction = (time_ns-start)/max(duration, 1)
        if fraction < policy["static_fraction"]["fit"]:
            return "CALIBRATION_FIT", "STATIC_FIT"
        if fraction < policy["static_fraction"]["fit"]+policy["static_fraction"]["guard"]:
            return "GUARD", "STATIC_GUARD"
        return "CALIBRATION_VALIDATION", "STATIC_VALIDATION"
    cycle_ns = int(float(policy["dynamic_cycle_seconds"])*1e9)
    guard_ns = int(float(policy["dynamic_boundary_guard_seconds"])*1e9)
    ordinal = max(0, (time_ns-start)//cycle_ns)
    within = (time_ns-start)%cycle_ns
    if within < guard_ns or within >= cycle_ns-guard_ns or time_ns+5_000_000 > stop:
        return "GUARD", f"{action}:cycle:{ordinal:02d}"
    return ("CALIBRATION_FIT" if ordinal % 2 == 0 else "CALIBRATION_VALIDATION"), f"{action}:cycle:{ordinal:02d}"


def decode_window_once(raw_slice: Path, window: Mapping, context: Mapping, policy: Mapping) -> tuple[list[CacheRow], dict]:
    """Decode each IMU payload UID once; UWB stops at the common routing header."""
    models = _clock_models(context)
    payload = raw_slice.read_bytes()
    base_offset = int(window["continuous_start_byte_inclusive"])
    formal_lo = int(window["formal_start_byte_inclusive"])
    formal_hi = int(window["formal_stop_byte_exclusive"])
    decoded: list[tuple] = []
    routing = imu_records = uwb_headers = errors = 0
    cursor = 0
    for encoded in payload.split(b"\0"):
        record_offset = base_offset+cursor
        record_length = len(encoded)+1
        cursor += record_length
        if not encoded:
            continue
        try:
            raw = cobs_decode(encoded)
            if len(raw) < HEADER.size+CRC.size:
                raise ValueError("short")
            body, expected = raw[:-2], CRC.unpack_from(raw, len(raw)-2)[0]
            if crc16_ccitt_false(body) != expected:
                raise ValueError("crc")
            magic, version, kind, node_id, length, _, _arrival_ms = HEADER.unpack_from(body)
            if magic != MAGIC or version != VERSION or len(body)-HEADER.size != length:
                raise ValueError("header")
            routing += 1
            if kind == KIND_UWB:
                uwb_headers += 1
                continue
            if kind != KIND_IMU:
                continue
            imu = memoryview(body)[HEADER.size:]
            imu_version, count, sequence, base_us, _ = struct.unpack_from("<BBHQh", imu)
            if imu_version != 7 or not 1 <= count <= 16 or len(imu) != 14+count*14:
                raise ValueError("imu layout")
            node = f"BSF{node_id:04X}"
            if node not in EXPECTED_NODES:
                raise ValueError("unexpected node")
            imu_records += 1
            for k in range(count):
                delta, ax, ay, az, gx, gy, gz = struct.unpack_from("<Hhhhhhh", imu, 14+k*14)
                timer = int(base_us+delta)
                common, boot, segment = _map_time(node, timer, models)
                decoded.append((record_offset, record_length, node, boot, segment, timer, int(sequence+k),
                                np.array([gx, gy, gz], float)*(2000.0/32768.0)*np.pi/180.0,
                                np.array([ax, ay, az], float)*(16.0/32768.0)*9.80665))
        except (ValueError, struct.error, IndexError):
            errors += 1
    if not decoded:
        raise RuntimeError("real reader returned zero IMU samples")
    formal_times = [row[5:6] for row in decoded if formal_lo <= row[0] < formal_hi]
    mapped_formal = [row for row in decoded if formal_lo <= row[0] < formal_hi]
    if not mapped_formal:
        raise RuntimeError("empty formal partition")
    time_interval = (min(_map_time(row[2], row[5], models)[0] for row in mapped_formal),
                     max(_map_time(row[2], row[5], models)[0] for row in mapped_formal)+1)
    result: list[CacheRow] = []
    seen: set[str] = set()
    for offset, length, node, boot, _segment, timer, sequence, gyro, accel in decoded:
        common, _, _ = _map_time(node, timer, models)
        phase = "PREPARATION" if offset < formal_lo else "FORMAL_ACTION" if offset < formal_hi else "RECOVERY_OR_FINAL_REST"
        split, cycle = _classify(window["action_id"], phase, common, time_interval, policy)
        row = CacheRow(window["action_id"], phase, split, cycle, node, boot, timer, common,
                       sequence, gyro, accel, offset, length)
        if row.uid in seen:
            continue
        seen.add(row.uid); result.append(row)
    nodes = {row.node_id for row in result}
    if nodes != EXPECTED_NODES:
        raise RuntimeError(f"window does not contain exact ten nodes: {sorted(nodes)}")
    result.sort(key=lambda row: (row.common_time_ns, row.node_id, row.sequence, row.source_record_offset))
    return result, {
        "bytes": len(payload), "routing_headers": routing, "imu_records": imu_records,
        "imu_samples": len(result), "imu_numeric_scalars": 6*len(result),
        "uwb_routing_headers": uwb_headers, "uwb_semantic_numeric_decode": 0,
        "uwb_measurement_arrays": 0, "errors": errors, "nodes": sorted(nodes),
        "parser_blob": sha256(Path(__file__)), "source_sha256": sha256(raw_slice),
    }


def _write_cache(path: Path, rows: list[CacheRow], *, sealed: bool) -> dict:
    path.mkdir(parents=True, exist_ok=False)
    strings = lambda values, width: np.asarray(values, dtype=f"U{width}")
    arrays = {
        "action": strings([r.action_id for r in rows], 40), "phase": strings([r.phase for r in rows], 32),
        "split": strings([r.split_class for r in rows], 32), "cycle": strings([r.cycle_id for r in rows], 64),
        "node": strings([r.node_id for r in rows], 8), "boot": np.asarray([r.boot_epoch for r in rows], dtype="<i4"),
        "timer2_us": np.asarray([r.timer2_us for r in rows], dtype="<i8"),
        "common_time_ns": np.asarray([r.common_time_ns for r in rows], dtype="<i8"),
        "sequence": np.asarray([r.sequence for r in rows], dtype="<i8"),
        "gyro": np.asarray([r.gyro_rad_s for r in rows], dtype="<f8").reshape((-1, 3)),
        "accel": np.asarray([r.accel_m_s2 for r in rows], dtype="<f8").reshape((-1, 3)),
        "source_offset": np.asarray([r.source_record_offset for r in rows], dtype="<i8"),
        "source_length": np.asarray([r.source_record_length for r in rows], dtype="<i4"),
    }
    columns = {}
    for name, values in arrays.items():
        target = path/f"{name}.npy"; np.save(target, values, allow_pickle=False)
        columns[name] = {"sha256": sha256(target), "shape": list(values.shape), "dtype": str(values.dtype)}
    closure = hashlib.sha256(json.dumps(columns, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    manifest = {"schema": "biospur-phase3r21-real-imu-cache-v1", "rows": len(rows), "sealed": sealed,
                "columns": columns, "closure_sha256": closure, "real_capture": True, "synthetic": False,
                "uwb_measurement_columns": 0}
    (path/"CACHE_MANIFEST.json").write_text(json.dumps(manifest, indent=2, sort_keys=True)+"\n")
    for item in path.iterdir(): os.chmod(item, 0o444)
    os.chmod(path, 0o555)
    return manifest


def write_split_caches(root: Path, rows: Iterable[CacheRow]) -> dict:
    rows = list(rows)
    groups = {
        "fit": [r for r in rows if r.split_class == "CALIBRATION_FIT"],
        "propagation": [r for r in rows if r.split_class == "PROPAGATION_ONLY"],
        "validation": [r for r in rows if r.split_class == "CALIBRATION_VALIDATION"],
        "guard": [r for r in rows if r.split_class == "GUARD"],
    }
    if any(not value for value in groups.values()):
        raise RuntimeError("one or more real split caches are empty")
    root.mkdir(parents=True, exist_ok=False)
    manifests = {name: _write_cache(root/name, value, sealed=name in {"validation", "guard"}) for name, value in groups.items()}
    uid_sets = {name: {r.uid for r in value} for name, value in groups.items()}
    if any(uid_sets[a] & uid_sets[b] for a in uid_sets for b in uid_sets if a < b):
        raise RuntimeError("split UID overlap")
    summary = {"schema": "biospur-phase3r21-single-pass-cache-closure-v1", "total_rows": len(rows),
               "caches": manifests, "uid_overlap": 0, "numeric_decode_passes_per_uid": 1,
               "validation_main_process_prefreeze_counts": {key: 0 for key in ("decode", "array", "propagation", "initialization", "feature", "factor", "statistic", "plot")}}
    (root/"BROKER_MANIFEST.json").write_text(json.dumps(summary, indent=2, sort_keys=True)+"\n")
    return summary


def load_cache_rows(path: Path) -> list[CacheRow]:
    manifest = json.loads((path/"CACHE_MANIFEST.json").read_text())
    arrays = {}
    for name, info in manifest["columns"].items():
        source = path/f"{name}.npy"
        if sha256(source) != info["sha256"]:
            raise RuntimeError(f"cache hash mismatch: {name}")
        arrays[name] = np.load(source, allow_pickle=False)
    result = []
    for i in range(manifest["rows"]):
        result.append(CacheRow(str(arrays["action"][i]), str(arrays["phase"][i]), str(arrays["split"][i]),
                               str(arrays["cycle"][i]), str(arrays["node"][i]), int(arrays["boot"][i]),
                               int(arrays["timer2_us"][i]), int(arrays["common_time_ns"][i]), int(arrays["sequence"][i]),
                               arrays["gyro"][i].copy(), arrays["accel"][i].copy(), int(arrays["source_offset"][i]),
                               int(arrays["source_length"][i])))
    return result
