#!/usr/bin/env python3
"""Resolve the exact Phase 3-R2 source allowlist without directory discovery."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from checkpoint import append_ledger, atomic_json


EVENTS = (
    "REPETITION_START_BOUNDARY", "ACTION_START", "ACTION_STOP",
    "REPETITION_END_BOUNDARY",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(4 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def authorize(ledger: Path, root: Path, path: Path, purpose: str) -> None:
    append_ledger(ledger, {
        "event": "source_open_authorized",
        "stage": "SOURCE_AUDIT",
        "path": str(path.relative_to(root)),
        "purpose": purpose,
        "numeric_decode": 0,
        "uwb_semantic_numeric_decode": 0,
        "uwb_measurement_array_materialization": 0,
        "uwb_statistics_or_plot": 0,
        "uwb_factor_or_initializer_consumption": 0,
    })


def read_json(ledger: Path, root: Path, path: Path, purpose: str) -> dict:
    authorize(ledger, root, path, purpose)
    return json.loads(path.read_text(encoding="utf-8"))


def audit_window(root: Path, ledger: Path, source: dict, classification: str) -> dict:
    manifest_path = Path(source["manifest"])
    if manifest_path.resolve(strict=True) != manifest_path:
        raise RuntimeError("manifest path is not a literal resolved file")
    manifest = read_json(ledger, root, manifest_path, "validate promoted continuous range")
    manifest_hash = sha256(manifest_path)
    if manifest_hash != source["manifest_sha256"]:
        raise RuntimeError(f"manifest hash mismatch for {source['action_id']}")
    event_path = root / source["relative_dir"] / "rep_01/events/ACTION_EVENTS.jsonl"
    authorize(ledger, root, event_path, "resolve exact preparation/formal/recovery byte markers")
    event_hash = sha256(event_path)
    rows = [json.loads(line) for line in event_path.read_text(encoding="utf-8").splitlines() if line]
    by_name = {row["event"]: row for row in rows}
    if tuple(row["event"] for row in rows) != EVENTS or set(by_name) != set(EVENTS):
        raise RuntimeError(f"unexpected marker state machine for {source['action_id']}")
    offsets = {name: int(by_name[name]["continuous_raw_complete_frame_bytes"]) for name in EVENTS}
    if list(offsets.values()) != sorted(offsets.values()):
        raise RuntimeError(f"non-monotonic action byte markers for {source['action_id']}")
    continuous = manifest["continuous_range"]
    if offsets[EVENTS[0]] != int(continuous["start_byte_inclusive"]):
        raise RuntimeError(f"start boundary mismatch for {source['action_id']}")
    if offsets[EVENTS[-1]] != int(continuous["end_byte_exclusive"]):
        raise RuntimeError(f"end boundary mismatch for {source['action_id']}")
    if continuous["slice_sha256"] != source["raw_opaque_sha256"]:
        raise RuntimeError(f"slice hash mismatch for {source['action_id']}")
    return {
        "action_id": source["action_id"],
        "source_action_id": manifest["action_id"],
        "classification": classification,
        "relative_dir": source["relative_dir"],
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_hash,
        "event_ledger": str(event_path),
        "event_ledger_sha256": event_hash,
        "slice": source["raw"],
        "slice_sha256": source["raw_opaque_sha256"],
        "canonical_raw": str(root / continuous["canonical_raw"]),
        "continuous_start_byte_inclusive": offsets[EVENTS[0]],
        "formal_start_byte_inclusive": offsets[EVENTS[1]],
        "formal_stop_byte_exclusive": offsets[EVENTS[2]],
        "continuous_end_byte_exclusive": offsets[EVENTS[3]],
        "collector_sequence_range": [
            int(by_name[EVENTS[0]]["collector_sequence"]),
            int(by_name[EVENTS[-1]]["collector_sequence"]),
        ],
        "phase_partition": {
            "PREPARATION": [offsets[EVENTS[0]], offsets[EVENTS[1]]],
            "FORMAL_ACTION": [offsets[EVENTS[1]], offsets[EVENTS[2]]],
            "RECOVERY_OR_FINAL_REST": [offsets[EVENTS[2]], offsets[EVENTS[3]]],
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--dataset-root", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    root = args.dataset_root.resolve(strict=True)
    development = []
    for source in selection["development_windows"]:
        classification = (
            "VALIDATION_ONLY" if source["action_id"] == "17_final_still"
            else "FIT_BEARING_WITH_SEALED_VALIDATION"
        )
        development.append(audit_window(root, args.ledger, source, classification))
    retrospective = [
        audit_window(root, args.ledger, source, "CONTAMINATED_RETROSPECTIVE_DIAGNOSTIC")
        for source in selection["retrospective_diagnostics"]
    ]
    ordered = sorted(development + retrospective, key=lambda row: row["continuous_start_byte_inclusive"])
    if len(development) != 19 or len(retrospective) != 3:
        raise RuntimeError("exact 19+3 source inventory required")
    if len({row["action_id"] for row in ordered}) != 22:
        raise RuntimeError("action identity collision")
    overlap = [
        (left["action_id"], right["action_id"])
        for left, right in zip(ordered, ordered[1:])
        if left["continuous_end_byte_exclusive"] > right["continuous_start_byte_inclusive"]
    ]
    if overlap:
        raise RuntimeError(f"promoted window overlap: {overlap}")
    calibration_latest = max(row["continuous_end_byte_exclusive"] for row in development if row["action_id"] != "17_final_still")
    h_earliest = min(row["continuous_start_byte_inclusive"] for row in retrospective)
    payload = {
        "schema": "biospur-phase3r2-data-selection-allowlist-v1",
        "dataset_root": str(root),
        "canonical_raw": str(root / "system/fusion_continuous/fusion_host_raw.cobs.bin"),
        "canonical_raw_sha256": "74c1fdbbe7c302bc21b0665bff50137e84537946a347ea11133e1e6751c84268",
        "development_windows": development,
        "retrospective_diagnostics": retrospective,
        "chronological_action_ids": [row["action_id"] for row in ordered],
        "fit_bearing_action_count": 18,
        "controlled_validation_window_count": 19,
        "retrospective_count": 3,
        "invalid_redo_numeric_reads": 0,
        "recursive_discovery": False,
        "h_after_all_calibration_fit_windows": h_earliest >= calibration_latest,
        "calibration_replay_classification": (
            "CAUSAL_CALIBRATION_PRECEDES_H" if h_earliest >= calibration_latest
            else "OFFLINE_SESSION_CALIBRATION_RETROSPECTIVE_REPLAY"
        ),
        "uwb_measurement_policy": "OPAQUE_TRANSIT_ONLY_NO_SEMANTIC_MATERIALIZATION",
    }
    atomic_json(args.output, payload)
    print(json.dumps({
        "output": str(args.output),
        "sha256": sha256(args.output),
        "development": len(development),
        "retrospective": len(retrospective),
        "chronology": payload["calibration_replay_classification"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
