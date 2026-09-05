#!/usr/bin/env python3
"""Run bounded Phase-0 UWB coverage audit on Capture2 calibration episodes."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
from statistics import median
import time
from typing import Any

from biospur_fusion.c2_uwb_calibration.coverage import (
    decode_bounded_uwb,
    summarize_beacon_clock_health,
    summarize_episode_coverage,
)
from biospur_fusion.c2_uwb_root_world.run_calibration import (
    _beacon_boundary_bridges,
    _clock_models,
)
from biospur_fusion.time.common_clock import SUPERFRAME_US
from biospur_fusion.v0.dual_capture import (
    build_protocol_ledger,
    load_protocol,
)


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAPTURE = (
    ROOT / "datasets/phase2_calibration/"
    "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
)
DEFAULT_CLOCK_TABLE = (
    ROOT / "logs/c2_uwb_beacon_clock_20260903_141552/"
    "CLOCK_TABLE_CALIBRATION_ONLY.json"
)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def run(
    output: Path,
    *,
    clock_table: Path,
    maximum_wall_s: float,
) -> dict[str, Any]:
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=False)
    episode_dir = output / "episodes"
    episode_dir.mkdir()
    protocol = load_protocol(ROOT)
    spec = protocol["captures"]["CAPTURE2"]
    capture = build_protocol_ledger(ROOT)["captures"]["CAPTURE2"]
    rows = [row for row in capture["actions"] if row["contributes_to_profile"]]
    if len(rows) != 19 or any("HXX" in row["protocol_roles"] for row in rows):
        raise RuntimeError("Phase 0 input is not the nineteen Hxx-free calibration episodes")
    expected_nodes = tuple(sorted(spec["identity"]))
    raw_path = Path(capture["raw_container"]["path"])
    clock_table = Path(clock_table).resolve()
    _clock_models(clock_table)  # Fail closed on source hash and clock contract.
    clock_document = json.loads(clock_table.read_text(encoding="utf-8"))
    clock_health = summarize_beacon_clock_health(clock_document)
    clock_models = clock_document["models"]
    if set(clock_models) != set(expected_nodes):
        raise RuntimeError("Beacon clock table and Capture2 identity differ")
    bridges = _beacon_boundary_bridges(clock_table)

    def map_host_ns(host_ns: int) -> int:
        host_s = int(host_ns) * 1e-9
        return int(round(median(
            slope * host_s + intercept for slope, intercept in bridges
        ) * 1_000.0))

    formal_t0 = int(json.loads(
        (DEFAULT_CAPTURE / "system/FORMAL_T0.json").read_text(encoding="utf-8")
    )["host_monotonic_ns"])
    calibration_t0 = int(rows[0]["episode_bounds"]["start_host_monotonic_ns"])

    summaries: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        if time.perf_counter() - started >= maximum_wall_s:
            raise TimeoutError("Phase-0 aggregate wall budget exhausted before next episode")
        episode_started = time.perf_counter()
        action = row["action"]
        print(f"PHASE0 {index:02d}/19 {action} beacon+coverage begin", flush=True)
        bracket = row["read_bracket"]
        events, decode = decode_bounded_uwb(
            raw_path,
            int(bracket["start_byte_inclusive"]),
            int(bracket["stop_byte_exclusive"]),
            expected_slice_sha256=str(bracket["slice_sha256"]),
        )
        episode = row["episode_bounds"]
        episode_start_global_ns = map_host_ns(
            int(episode["start_host_monotonic_ns"])
        )
        episode_stop_global_ns = map_host_ns(
            int(episode["stop_host_monotonic_ns_exclusive"])
        )
        coverage = summarize_episode_coverage(
            events,
            expected_nodes=expected_nodes,
            clock_models=clock_models,
            episode_start_global_ns=episode_start_global_ns,
            episode_stop_global_ns_exclusive=episode_stop_global_ns,
            superframe_s=float(SUPERFRAME_US) * 1e-6,
        )
        summary = {
            "schema": "biospur.c2.uwb_calibration.phase0_episode.v1",
            "action": action,
            "chronological_index": index - 1,
            "start_from_formal_t0_min": (
                int(episode["start_host_monotonic_ns"]) - formal_t0
            ) / 60e9,
            "start_from_calibration_t0_min": (
                int(episode["start_host_monotonic_ns"]) - calibration_t0
            ) / 60e9,
            "elapsed_wall_s": time.perf_counter() - episode_started,
            "common_clock": {
                "source": "BEACON_ONLY_B306_TIMER2",
                "clock_owner": str(clock_table),
                "clock_owner_sha256": _sha256(clock_table),
                "node_count": len(clock_models),
                **clock_health,
                "lpd_lrd_consumed": False,
                "measurement_time_source": "B306_TIMER2",
            },
            "decode": decode,
            "coverage": coverage,
            "hxx_payload_opened": False,
            "old_solved_position_consumed": False,
        }
        _write_json(episode_dir / f"{index - 1:02d}_{action}.json", summary)
        summaries.append(summary)
        print(
            f"PHASE0 {index:02d}/19 {action} complete "
            f"uwb={coverage['uwb_updates']} links={coverage['valid_links']} "
            f"wall={summary['elapsed_wall_s']:.2f}s",
            flush=True,
        )

    node_rows = []
    for episode in summaries:
        for node, values in episode["coverage"]["nodes"].items():
            node_rows.append({
                "action": episode["action"],
                "node": node,
                "uwb_updates": values["uwb_updates"],
                "observed_rate_hz": values["observed_rate_hz"],
                "valid_links": values["valid_links"],
                "fraction_8_links": values["fraction_updates_with_8_links"],
                "fraction_at_least_7_links": values[
                    "fraction_updates_with_at_least_7_links"
                ],
                "fraction_direct_3d_candidate": values[
                    "fraction_direct_3d_candidate_updates"
                ],
                "fraction_partial_or_missing": values[
                    "fraction_partial_or_missing_updates"
                ],
            })
    with (output / "EPISODE_NODE_COVERAGE.csv").open(
        "w", newline="", encoding="utf-8"
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(node_rows[0]))
        writer.writeheader()
        writer.writerows(node_rows)

    total_updates = sum(row["coverage"]["uwb_updates"] for row in summaries)
    total_links = sum(row["coverage"]["valid_links"] for row in summaries)
    degraded = clock_health["degraded_nodes"]
    strict_clock_pass = not degraded
    final = {
        "schema": "biospur.c2.uwb_calibration.phase0.v1",
        "status": (
            "PHASE0_BEACON_ALIGNMENT_AND_COVERAGE_COMPLETE"
            if strict_clock_pass
            else "PHASE0_BEACON_ALIGNMENT_COMPLETE_WITH_DEGRADED_NODE_CLOCK"
        ),
        "scientific_pass": False,
        "purpose": "COVERAGE_CLOCK_AND_PROPAGATION_AVAILABILITY_ONLY",
        "calibration_episode_count": len(summaries),
        "calibration_action_span_min": (
            int(rows[-1]["episode_bounds"]["stop_host_monotonic_ns_exclusive"])
            - calibration_t0
        ) / 60e9,
        "session_age": {
            "episode_00_start_min_from_formal_t0": summaries[0][
                "start_from_formal_t0_min"
            ],
            "episode_19_start_min_from_formal_t0": summaries[-1][
                "start_from_formal_t0_min"
            ],
        },
        "total_uwb_updates": total_updates,
        "total_valid_links": total_links,
        "clock_owner": str(clock_table),
        "clock_owner_sha256": _sha256(clock_table),
        "clock_source": "BEACON_ONLY_B306_TIMER2",
        "lpd_lrd_consumed": False,
        "every_clock_gate_safe": clock_health["strict_residual_gate"],
        "every_strict_node_clock_gate_safe": strict_clock_pass,
        "degraded_node_clocks": degraded,
        "maximum_clock_residual_p95_us": max(
            node["residual_p95_us"] for node in clock_health["nodes"].values()
        ),
        "maximum_clock_residual_us": max(
            node["residual_max_us"] for node in clock_health["nodes"].values()
        ),
        "minimum_fraction_bins_with_any_direct_node": min(
            row["coverage"]["propagation_availability"][
                "fraction_bins_with_any_direct_node"
            ]
            for row in summaries
        ),
        "minimum_fraction_bins_with_at_least_5_direct_nodes": min(
            row["coverage"]["propagation_availability"][
                "fraction_bins_with_at_least_5_direct_nodes"
            ]
            for row in summaries
        ),
        "hxx_payload_opened": False,
        "old_solved_position_consumed": False,
        "raw_valid_mask_interpreted_as_los": False,
        "calibration_or_fusion_executed": False,
        "supersedes_invalid_clock_conclusion": (
            "logs/c2_uwb_calibration_phase0_strict_20260903_171601"
        ),
        "elapsed_wall_s": time.perf_counter() - started,
        "episode_artifacts": [
            str(path.relative_to(output)) for path in sorted(episode_dir.glob("*.json"))
        ],
    }
    _write_json(output / "PHASE0_RESULT.json", final)
    artifacts = [output / "PHASE0_RESULT.json", output / "EPISODE_NODE_COVERAGE.csv"]
    artifacts.extend(sorted(episode_dir.glob("*.json")))
    (output / "SHA256SUMS").write_text(
        "".join(f"{_sha256(path)}  {path.relative_to(output)}\n" for path in artifacts),
        encoding="utf-8",
    )
    return final


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--clock-table", type=Path, default=DEFAULT_CLOCK_TABLE)
    parser.add_argument("--maximum-wall-s", type=float, default=600.0)
    args = parser.parse_args()
    output = args.output or (
        ROOT / "logs" / f"c2_uwb_calibration_phase0_{datetime.now():%Y%m%d_%H%M%S}"
    )
    result = run(
        output.resolve(),
        clock_table=args.clock_table,
        maximum_wall_s=args.maximum_wall_s,
    )
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
