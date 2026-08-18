#!/usr/bin/env python3
"""Build the current-session R2 timing context from selective timing fields."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from biospur_fusion.io_v2.phase3r2_selective import TimingRoutingRecord, iter_binary_timing_records
from biospur_fusion.time.phase3r2_context import ListenerTimingPoll, fit_clock_models, load_listener_timing, model_payload
from checkpoint import append_ledger, atomic_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authorize(ledger: Path, dataset: Path, path: Path, purpose: str) -> None:
    append_ledger(ledger, {
        "event": "source_open_authorized",
        "stage": "TIME_AND_ROUTING_HEADERS",
        "path": str(path.relative_to(dataset)),
        "purpose": purpose,
        "access_level": "LEVEL_0_OR_LEVEL_1",
        "co_located_transport_record_exposure": 0,
        "uwb_semantic_numeric_decode": 0,
        "uwb_measurement_array_materialization": 0,
        "uwb_statistics_or_plot": 0,
        "uwb_factor_or_initializer_consumption": 0,
    })


def write_column_cache(cache: Path, records, reader_audit) -> dict:
    cache.mkdir(parents=True, exist_ok=False)
    columns = {
        "record_kind": np.asarray([r.record_kind for r in records], dtype=np.uint8),
        "hardware_node_id": np.asarray([r.hardware_node_id for r in records], dtype="U8"),
        "boot_epoch": np.asarray([r.boot_epoch for r in records], dtype=np.uint16),
        "sweep_id": np.asarray([r.sweep_id for r in records], dtype=np.uint32),
        "frame_timer2_us": np.asarray([r.frame_timer2_us for r in records], dtype=np.uint64),
        "strobe_timer2_us": np.asarray([r.strobe_timer2_us for r in records], dtype=np.uint64),
        "superframe_valid": np.asarray([r.superframe_valid for r in records], dtype=np.bool_),
        "superframe_mod16": np.asarray([-1 if r.superframe_mod16 is None else r.superframe_mod16 for r in records], dtype=np.int8),
        "required_transport_flags": np.asarray([r.required_transport_flags for r in records], dtype=np.uint8),
        "source_byte_offset": np.asarray([r.source_byte_offset for r in records], dtype=np.uint64),
        "source_record_length": np.asarray([r.source_record_length for r in records], dtype=np.uint32),
    }
    manifest = {}
    for name, values in columns.items():
        path = cache / f"{name}.npy"
        np.save(path, values, allow_pickle=False)
        manifest[name] = {
            "path": path.name,
            "dtype": str(values.dtype),
            "shape": list(values.shape),
            "sha256": sha256(path),
        }
    closure = hashlib.sha256()
    for name in sorted(manifest):
        closure.update(name.encode())
        closure.update(manifest[name]["sha256"].encode())
    payload = {
        "schema": "biospur-phase3r2-timing-routing-cache-v1",
        "rows": len(records),
        "columns": manifest,
        "forbidden_measurement_columns": 0,
        "selective_reader_audit": {
            key: getattr(reader_audit, key) for key in reader_audit.__dataclass_fields__
        },
        "closure_sha256": closure.hexdigest(),
    }
    atomic_json(cache / "CACHE_MANIFEST.json", payload)
    for path in cache.iterdir():
        os.chmod(path, 0o444)
    os.chmod(cache, 0o555)
    return payload


def load_column_cache(cache: Path) -> tuple[tuple[TimingRoutingRecord, ...], SimpleNamespace, dict]:
    manifest = json.loads((cache / "CACHE_MANIFEST.json").read_text(encoding="utf-8"))
    names = (
        "record_kind", "hardware_node_id", "boot_epoch", "sweep_id",
        "frame_timer2_us", "strobe_timer2_us", "superframe_valid",
        "superframe_mod16", "required_transport_flags", "source_byte_offset",
        "source_record_length",
    )
    columns = {}
    for name in names:
        info = manifest["columns"][name]
        path = cache / info["path"]
        if sha256(path) != info["sha256"]:
            raise RuntimeError(f"timing cache column hash mismatch: {name}")
        columns[name] = np.load(path, mmap_mode="r", allow_pickle=False)
    count = int(manifest["rows"])
    records = tuple(TimingRoutingRecord(
        int(columns["record_kind"][i]), str(columns["hardware_node_id"][i]),
        int(columns["boot_epoch"][i]), int(columns["sweep_id"][i]),
        int(columns["frame_timer2_us"][i]), int(columns["strobe_timer2_us"][i]),
        bool(columns["superframe_valid"][i]),
        None if int(columns["superframe_mod16"][i]) < 0 else int(columns["superframe_mod16"][i]),
        int(columns["required_transport_flags"][i]),
        int(columns["source_byte_offset"][i]), int(columns["source_record_length"][i]),
    ) for i in range(count))
    return records, SimpleNamespace(**manifest["selective_reader_audit"]), manifest


def write_listener_cache(cache: Path, polls, audit: dict) -> dict:
    cache.mkdir(parents=True, exist_ok=False)
    columns = {
        "listener": np.asarray([p.listener for p in polls], dtype="U32"),
        "src": np.asarray([p.src for p in polls], dtype=np.uint16),
        "sequence": np.asarray([p.sequence for p in polls], dtype=np.uint8),
        "absolute_epoch": np.asarray([p.absolute_epoch for p in polls], dtype=np.int64),
        "phase_us": np.asarray([p.phase_us for p in polls], dtype=np.float64),
    }
    manifest = {}
    for name, values in columns.items():
        path = cache / f"{name}.npy"
        np.save(path, values, allow_pickle=False)
        manifest[name] = {"path": path.name, "dtype": str(values.dtype), "shape": list(values.shape), "sha256": sha256(path)}
    closure = hashlib.sha256()
    for name in sorted(manifest):
        closure.update(name.encode()); closure.update(manifest[name]["sha256"].encode())
    payload = {
        "schema": "biospur-phase3r2-listener-timing-cache-v1",
        "rows": len(polls), "columns": manifest, "listener_audit": audit,
        "forbidden_measurement_columns": 0, "closure_sha256": closure.hexdigest(),
    }
    atomic_json(cache / "CACHE_MANIFEST.json", payload)
    for path in cache.iterdir(): os.chmod(path, 0o444)
    os.chmod(cache, 0o555)
    return payload


def load_listener_cache(cache: Path):
    manifest = json.loads((cache / "CACHE_MANIFEST.json").read_text(encoding="utf-8"))
    columns = {}
    for name, info in manifest["columns"].items():
        path = cache / info["path"]
        if sha256(path) != info["sha256"]: raise RuntimeError(f"listener cache hash mismatch: {name}")
        columns[name] = np.load(path, mmap_mode="r", allow_pickle=False)
    polls = tuple(ListenerTimingPoll(
        str(columns["listener"][i]), int(columns["src"][i]), int(columns["sequence"][i]),
        int(columns["absolute_epoch"][i]), float(columns["phase_us"][i]),
    ) for i in range(int(manifest["rows"])))
    return polls, manifest["listener_audit"], manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    dataset = args.dataset_root.resolve(strict=True)
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    raw = Path(selection["canonical_raw"])
    readiness = dataset / "system/readiness/SYSTEM_READINESS_REPORT.json"
    listener_dir = dataset / "system/listeners/passive_5"
    listener_summary = listener_dir / "summary.json"
    authorize(args.ledger, dataset, readiness, "read frozen node-to-TDMA-slot routing authority")
    authorize(args.ledger, dataset, listener_summary, "read exact passive Listener inventory")
    summary = json.loads(listener_summary.read_text(encoding="utf-8"))
    listener_files = []
    for snr in sorted(summary["listeners"]):
        path = listener_dir / "listeners" / f"{snr}.jsonl"
        authorize(args.ledger, dataset, path, "select LBD/LPD timing records; non-timing lines remain opaque bytes")
        listener_files.append(path)
    if args.cache_root.exists():
        records, reader_audit, cache_manifest = load_column_cache(args.cache_root)
        raw_hash = selection["canonical_raw_sha256"]
        append_ledger(args.ledger, {
            "event": "cache_read",
            "stage": "TIME_AND_ROUTING_HEADERS",
            "cache": str(args.cache_root),
            "cache_closure_sha256": cache_manifest["closure_sha256"],
            "purpose": "retry time fit after implementation repair",
            "uwb_semantic_numeric_decode": 0,
            "uwb_measurement_array_materialization": 0,
        })
    else:
        authorize(args.ledger, dataset, raw, "selective UWB timing/routing projection; measurements remain opaque")
        records, reader_audit = iter_binary_timing_records(raw)
        raw_hash = sha256(raw)
        if raw_hash != selection["canonical_raw_sha256"]:
            raise RuntimeError("canonical raw identity mismatch")
        # Persist the safe field projection before fitting.  A reproducible model
        # failure must not force another mixed-container pass, and this cache has
        # no measurement columns by construction.
        cache_manifest = write_column_cache(args.cache_root, records, reader_audit)
        append_ledger(args.ledger, {
            "event": "timing_routing_projection_cache_written",
            "stage": "TIME_AND_ROUTING_HEADERS",
            "cache": str(args.cache_root),
            "cache_closure_sha256": cache_manifest["closure_sha256"],
            "timing_records": len(records),
            "uwb_semantic_numeric_decode": 0,
            "uwb_measurement_array_materialization": 0,
        })
    readiness_payload = json.loads(readiness.read_text(encoding="utf-8"))
    slots = {node: int(row["slot"]) for node, row in readiness_payload["tdma_verify"]["mapping"].items()}
    src_slots = {0xB100 + slot: slot for slot in slots.values()}
    listener_cache = args.cache_root.parent / "listener_timing_cache"
    if listener_cache.exists():
        polls, listener_audit, listener_cache_manifest = load_listener_cache(listener_cache)
        append_ledger(args.ledger, {
            "event": "cache_read", "stage": "TIME_AND_ROUTING_HEADERS",
            "cache": str(listener_cache), "purpose": "retry time fit from LBD/LPD-only cache",
            "uwb_semantic_numeric_decode": 0, "uwb_measurement_array_materialization": 0,
        })
    else:
        polls, listener_audit = load_listener_timing(listener_dir, src_slots)
        listener_cache_manifest = write_listener_cache(listener_cache, polls, listener_audit)
        append_ledger(args.ledger, {
            "event": "listener_timing_cache_written", "stage": "TIME_AND_ROUTING_HEADERS",
            "cache": str(listener_cache), "cache_closure_sha256": listener_cache_manifest["closure_sha256"],
            "timing_records": len(polls), "uwb_semantic_numeric_decode": 0,
            "uwb_measurement_array_materialization": 0,
        })
    models, details = fit_clock_models(records, polls, slots)
    context = model_payload(models, details, listener_audit)
    context["sources"] = {
        "canonical_raw": {"path": str(raw), "sha256": raw_hash},
        "readiness": {"path": str(readiness), "sha256": sha256(readiness)},
        "listener_summary": {"path": str(listener_summary), "sha256": sha256(listener_summary)},
        "listener_files": [{"path": str(path), "sha256": sha256(path)} for path in listener_files],
    }
    context["selective_reader_audit"] = {
        key: getattr(reader_audit, key)
        for key in (
            reader_audit.__dataclass_fields__ if hasattr(reader_audit, "__dataclass_fields__")
            else vars(reader_audit)
        )
    }
    context["access_counters"] = {
        "co_located_transport_record_exposure": 1,
        "uwb_semantic_numeric_decode": 0,
        "uwb_measurement_array_materialization": 0,
        "uwb_statistics_or_plot": 0,
        "uwb_factor_or_initializer_consumption": 0,
        "uwb_influence_on_config_or_threshold": 0,
    }
    context["timing_cache"] = {
        "path": str(args.cache_root),
        "closure_sha256": cache_manifest["closure_sha256"],
        "rows": cache_manifest["rows"],
    }
    atomic_json(args.output, context)
    output_hash = sha256(args.output)
    append_ledger(args.ledger, {
        "event": "timing_context_and_cache_written",
        "stage": "TIME_AND_ROUTING_HEADERS",
        "output": str(args.output),
        "output_sha256": output_hash,
        "cache": str(args.cache_root),
        "cache_closure_sha256": cache_manifest["closure_sha256"],
        "timing_records": len(records),
        "uwb_semantic_numeric_decode": 0,
        "uwb_measurement_array_materialization": 0,
        "uwb_statistics_or_plot": 0,
        "uwb_factor_or_initializer_consumption": 0,
        "gate_pass": context["gate"]["pass"],
    })
    print(json.dumps({
        "context": str(args.output),
        "sha256": output_hash,
        "timing_records": len(records),
        "polls": len(polls),
        "models": len(models),
        "gate": context["gate"],
        "reader_audit": context["selective_reader_audit"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
