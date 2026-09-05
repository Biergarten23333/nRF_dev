"""Beacon-only common clock for the C2 UWB/IMU fusion path.

Passive Listener poll and response records are RF diagnostics.  They are not
fusion measurements and are deliberately neither decoded nor accepted here.
Only main-beacon records provide the absolute TDMA epoch.  A beacon/host fit is
used solely to resolve the discrete superframe integer; once resolved, the
continuous clock fit uses B306 TIMER2 UWB strobes and the deterministic TDMA
phase.  The resulting affine model applies unchanged to TIMER2-stamped UWB and
IMU records from the same node and boot.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import numpy as np

from biospur_fusion.time.common_clock import (
    SLOT_US,
    SUPERFRAME_US,
    UwbClockAnchor,
    parse_fusion_anchors,
    reconstruct_local_epochs,
)

MAIN_BEACON_INDEX = 0
POLL_TX_DONE_PHASE_US = 3_900.0
_BEACON_MARKERS = (b'"kind":"LBD"', b'"kind": "LBD"')


@dataclass(frozen=True)
class BeaconObservation:
    listener: str
    host_monotonic_s: float
    superframe_counter: int
    schedule_generation: int
    cycle_period_us: int
    tx_offset_us: int

    @property
    def global_us(self) -> float:
        return self.superframe_counter * self.cycle_period_us + self.tx_offset_us


@dataclass(frozen=True)
class BeaconBridge:
    listener: str
    global_us_per_host_s: float
    global_us_intercept: float
    records: int
    retained: int
    residual_p95_us: float
    residual_max_us: float

    def global_us(self, host_s: float) -> float:
        return self.global_us_per_host_s * host_s + self.global_us_intercept


@dataclass(frozen=True)
class BeaconClockModel:
    node_id: str
    boot_epoch: int
    a_ns_per_us: float
    b_ns: float
    sigma_ns: float
    first_timer_us: int
    last_timer_us: int
    integer_epoch_offset: int
    integer_choice_margin_votes: int
    sf_mod16_agreement_fraction: float
    residual_p95_us: float
    residual_max_us: float
    drift_ppm: float
    timestamp_reversals: int

    def map_ns(self, timer_us: int) -> int:
        return int(round(self.a_ns_per_us * int(timer_us) + self.b_ns))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _robust_affine(x: Sequence[float], y: Sequence[float]) -> tuple[float, float, np.ndarray]:
    xx = np.asarray(x, float)
    yy = np.asarray(y, float)
    if len(xx) < 10 or len(xx) != len(yy) or np.ptp(xx) <= 0:
        raise ValueError("beacon clock fit requires ten nondegenerate observations")
    keep = np.ones(len(xx), dtype=bool)
    for _ in range(12):
        design = np.column_stack((xx[keep], np.ones(int(keep.sum()))))
        slope, intercept = np.linalg.lstsq(design, yy[keep], rcond=None)[0]
        residual = yy - (slope * xx + intercept)
        centre = float(np.median(residual[keep]))
        mad = 1.4826 * float(np.median(np.abs(residual[keep] - centre)))
        limit = max(1_000.0, 6.0 * mad)
        updated = np.abs(residual - centre) <= limit
        if int(updated.sum()) < 10:
            raise ValueError("beacon clock fit rejected too many observations")
        if np.array_equal(updated, keep):
            break
        keep = updated
    design = np.column_stack((xx[keep], np.ones(int(keep.sum()))))
    slope, intercept = np.linalg.lstsq(design, yy[keep], rcond=None)[0]
    return float(slope), float(intercept), keep


def build_beacon_index(listener_dir: Path, output: Path) -> dict:
    """Extract only LBD rows; LPD/LRD JSON is never decoded."""
    listener_dir = Path(listener_dir).resolve()
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(output)
    summary = json.loads((listener_dir / "summary.json").read_text(encoding="utf-8"))
    source_rows = parsed_beacons = 0
    source_manifest: list[dict] = []
    with output.open("x", encoding="utf-8") as sink:
        for snr, info in sorted(summary["listeners"].items()):
            source = listener_dir / "listeners" / f"{snr}.jsonl"
            digest = hashlib.sha256()
            listener_beacons = 0
            with source.open("rb", buffering=4 << 20) as stream:
                for line in stream:
                    source_rows += 1
                    digest.update(line)
                    if not any(marker in line for marker in _BEACON_MARKERS):
                        continue
                    row = json.loads(line)
                    if row.get("kind") != "LBD" or not row.get("parsed_ok", False):
                        continue
                    fields = row.get("fields", {})
                    if int(fields.get("beacon_index", -1)) != MAIN_BEACON_INDEX:
                        continue
                    record = {
                        "listener_key": str(row["listener_key"]),
                        "listener_snr": str(row["listener_snr"]),
                        "arrival_monotonic_ns": int(row["arrival_monotonic_ns"]),
                        "superframe_counter": int(fields["superframe_counter"]),
                        "schedule_generation": int(fields["schedule_generation"]),
                        "cycle_period_us": int(fields["cycle_period_us"]),
                        "tx_offset_us": int(fields["tx_offset_us"]),
                        "beacon_index": int(fields["beacon_index"]),
                    }
                    sink.write(json.dumps(record, separators=(",", ":")) + "\n")
                    listener_beacons += 1
                    parsed_beacons += 1
            source_manifest.append({
                "path": str(source),
                "sha256": digest.hexdigest(),
                "bytes": source.stat().st_size,
                "beacons": listener_beacons,
            })
    return {
        "schema": "biospur.c2.listener_beacon_index.v1",
        "index_path": str(output),
        "index_sha256": _sha256(output),
        "source_rows_seen": source_rows,
        "beacon_rows_written": parsed_beacons,
        "non_beacon_json_rows_decoded": 0,
        "accepted_input_kind": "LBD",
        "forbidden_input_kinds": ["LPD", "LRD"],
        "sources": source_manifest,
    }


def load_beacon_index(index_path: Path, start_s: float, end_s: float) -> list[BeaconObservation]:
    rows: list[BeaconObservation] = []
    with Path(index_path).open("r", encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            if set(row).intersection({"kind", "src", "dst", "poll_seq", "resp_seq"}):
                raise ValueError("beacon index contains forbidden poll/response fields")
            host_s = int(row["arrival_monotonic_ns"]) * 1e-9
            if start_s - 2.0 <= host_s <= end_s + 2.0:
                rows.append(BeaconObservation(
                    listener=str(row["listener_key"]),
                    host_monotonic_s=host_s,
                    superframe_counter=int(row["superframe_counter"]),
                    schedule_generation=int(row["schedule_generation"]),
                    cycle_period_us=int(row["cycle_period_us"]),
                    tx_offset_us=int(row["tx_offset_us"]),
                ))
    if not rows:
        raise ValueError("no main-beacon observations in requested interval")
    return rows


def fit_beacon_bridges(rows: Iterable[BeaconObservation]) -> dict[str, BeaconBridge]:
    grouped: dict[str, list[BeaconObservation]] = defaultdict(list)
    for row in rows:
        if row.cycle_period_us != int(SUPERFRAME_US):
            raise ValueError("beacon cycle period disagrees with capture TDMA period")
        grouped[row.listener].append(row)
    bridges: dict[str, BeaconBridge] = {}
    for listener, group in sorted(grouped.items()):
        generations = {row.schedule_generation for row in group}
        if len(generations) != 1:
            raise ValueError(f"beacon generation changes inside interval for {listener}")
        x = np.asarray([row.host_monotonic_s for row in group], float)
        y = np.asarray([row.global_us for row in group], float)
        slope, intercept, keep = _robust_affine(x, y)
        residual = y - (slope * x + intercept)
        values = np.abs(residual[keep])
        bridges[listener] = BeaconBridge(
            listener, slope, intercept, len(group), int(keep.sum()),
            float(np.percentile(values, 95)), float(np.max(values)),
        )
    if not bridges:
        raise ValueError("no usable beacon bridges")
    return bridges


def _nearest_mod16(value: float, residue: int) -> int:
    centre = int(round(value))
    candidates = [centre + delta for delta in range(-16, 17)
                  if (centre + delta) & 0x0F == int(residue) & 0x0F]
    return min(candidates, key=lambda item: (abs(item - value), item))


def _fit_node(
    node: str,
    anchors: Sequence[UwbClockAnchor],
    slot: int,
    bridges: Mapping[str, BeaconBridge],
) -> tuple[BeaconClockModel, dict]:
    if len(anchors) < 10:
        raise ValueError(f"{node}: insufficient B306 UWB strobes")
    local_epochs, local_period_us = reconstruct_local_epochs(row.strobe_us for row in anchors)
    phase_us = float(slot) * SLOT_US + POLL_TX_DONE_PHASE_US
    votes: Counter[int] = Counter()
    mod_rows = 0
    for row, local_epoch in zip(anchors, local_epochs):
        if not row.sf_valid or row.sf_mod16 is None:
            continue
        predicted_global_us = float(np.median([
            bridge.global_us(row.host_monotonic_s) for bridge in bridges.values()
        ]))
        predicted_counter = (predicted_global_us - phase_us) / SUPERFRAME_US
        counter = _nearest_mod16(predicted_counter, row.sf_mod16)
        votes[counter - int(local_epoch)] += 1
        mod_rows += 1
    if not votes:
        raise ValueError(f"{node}: no carried sf_mod16 rows")
    ranked = votes.most_common(2)
    offset, count = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0
    if count <= runner_up:
        raise ValueError(f"{node}: ambiguous beacon integer epoch offset")
    target_us = (local_epochs.astype(float) + offset) * SUPERFRAME_US + phase_us
    timer_us = np.asarray([row.strobe_us for row in anchors], float)
    slope, intercept, keep = _robust_affine(timer_us, target_us)
    residual = target_us - (slope * timer_us + intercept)
    centred = residual - float(np.median(residual[keep]))
    clean = np.abs(centred[keep])
    mapped = slope * timer_us + intercept
    agreement = sum(
        bool(row.sf_valid and row.sf_mod16 is not None
             and ((int(local_epoch) + offset) & 0x0F) == row.sf_mod16)
        for row, local_epoch in zip(anchors, local_epochs)
        if row.sf_valid and row.sf_mod16 is not None
    ) / mod_rows
    sigma_us = max(1.0, float(np.percentile(clean, 95)))
    model = BeaconClockModel(
        node_id=node,
        boot_epoch=0,
        a_ns_per_us=slope * 1000.0,
        b_ns=intercept * 1000.0,
        sigma_ns=sigma_us * 1000.0,
        first_timer_us=int(timer_us[0]),
        last_timer_us=int(timer_us[-1]),
        integer_epoch_offset=int(offset),
        integer_choice_margin_votes=int(count - runner_up),
        sf_mod16_agreement_fraction=float(agreement),
        residual_p95_us=float(np.percentile(clean, 95)),
        residual_max_us=float(np.max(clean)),
        drift_ppm=float((local_period_us / SUPERFRAME_US - 1.0) * 1e6),
        timestamp_reversals=int(np.sum(np.diff(mapped) <= 0)),
    )
    return model, {
        "slot": int(slot),
        "phase_us": phase_us,
        "sf_mod16_rows": mod_rows,
        "offset_vote_counts": {str(key): value for key, value in sorted(votes.items())},
        "retained_strobes": int(keep.sum()),
    }


def align_capture_beacon_only(
    fusion_log: Path,
    beacon_index: Path,
    readiness_path: Path,
    start_s: float,
    end_s: float,
    expected_nodes: Iterable[str],
) -> tuple[dict[str, BeaconClockModel], dict]:
    """Fit one TIMER2 clock per node using LBD plus carried TDMA metadata only."""
    nodes = tuple(expected_nodes)
    readiness = json.loads(Path(readiness_path).read_text(encoding="utf-8"))
    mapping = readiness.get("tdma_verify", {}).get("mapping", {})
    if set(mapping) != set(nodes):
        raise ValueError("readiness TDMA fleet does not exactly match expected nodes")
    slots = {node: int(mapping[node]["slot"]) for node in nodes}
    beacons = load_beacon_index(beacon_index, start_s, end_s)
    bridges = fit_beacon_bridges(beacons)
    anchors = parse_fusion_anchors(Path(fusion_log), start_s - 0.25, end_s + 0.25)
    if set(anchors) != set(nodes):
        raise ValueError("fusion timing fleet does not exactly match expected nodes")
    models: dict[str, BeaconClockModel] = {}
    node_audit = {}
    for node in sorted(nodes, key=slots.get):
        model, audit = _fit_node(node, anchors[node], slots[node], bridges)
        models[node] = model
        node_audit[node] = audit
    gate = {
        "accepted_listener_record_kinds": ["LBD"],
        "forbidden_listener_record_kinds": ["LPD", "LRD"],
        "poll_or_response_measurements_consumed": False,
        "beacon_host_time_role": "DISCRETE_SUPERFRAME_INTEGER_ASSOCIATION_ONLY",
        "measurement_time_source": "B306_TIMER2",
        "same_model_applies_to": ["uwb.strobe_us", "uwb.frame_us", "imu.base_us", "imu.trigger_us"],
        "all_sf_mod16_exact": all(model.sf_mod16_agreement_fraction == 1.0 for model in models.values()),
        "no_timestamp_reversal": all(model.timestamp_reversals == 0 for model in models.values()),
        "residual_p95_lt_0_5_ms": all(model.residual_p95_us < 500.0 for model in models.values()),
        "residual_max_lt_1_ms": all(model.residual_max_us < 1_000.0 for model in models.values()),
    }
    gate["pass"] = all(value for key, value in gate.items() if key in {
        "all_sf_mod16_exact", "no_timestamp_reversal", "residual_p95_lt_0_5_ms",
        "residual_max_lt_1_ms",
    })
    return models, {
        "gate": gate,
        "bridges": {key: asdict(value) for key, value in bridges.items()},
        "nodes": node_audit,
        "beacon_records": len(beacons),
    }


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fusion-log", type=Path, required=True)
    parser.add_argument("--listener-dir", type=Path, required=True)
    parser.add_argument("--readiness", type=Path, required=True)
    parser.add_argument("--beacon-index", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start-s", type=float, required=True)
    parser.add_argument("--end-s", type=float, required=True)
    parser.add_argument("--nodes", nargs="+", required=True)
    args = parser.parse_args()
    index_audit = build_beacon_index(args.listener_dir, args.beacon_index)
    models, audit = align_capture_beacon_only(
        args.fusion_log, args.beacon_index, args.readiness,
        args.start_s, args.end_s, args.nodes,
    )
    source = Path(__file__).resolve()
    document = {
        "schema": "biospur.c2.beacon_only_clock_table.v1",
        "source_function": "biospur_fusion.c2_uwb_root_world.beacon_clock.align_capture_beacon_only",
        "source_sha256": _sha256(source),
        "clock_contract": audit["gate"],
        "models": {node: asdict(model) for node, model in sorted(models.items())},
        "audit": audit,
        "beacon_index": index_audit,
        "inputs": {
            "fusion_log": str(args.fusion_log.resolve()),
            "readiness": str(args.readiness.resolve()),
            "listener_directory": str(args.listener_dir.resolve()),
            "start_s": args.start_s,
            "end_s": args.end_s,
        },
    }
    args.output.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"gate": audit["gate"], "models": len(models), "beacons": len(load_beacon_index(args.beacon_index, args.start_s, args.end_s))}, indent=2))


if __name__ == "__main__":
    _main()
