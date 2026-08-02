#!/usr/bin/env python3
"""Offline gate analysis for the cold-start S5a main-only formal window."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = (
    ROOT
    / "UWB_Part/logs/night_20260730/morning/final_relay7_1800s/"
    "analyze_final_relay7.py"
)


def load_reference():
    spec = importlib.util.spec_from_file_location("relay7_analysis", REFERENCE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load reference analysis: {REFERENCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--t4", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    ref = load_reference()
    ref.RAW = args.raw
    ref.phase_m.RAW = args.raw
    summary = json.loads((args.raw / "summary.json").read_text())
    t4 = json.loads(args.t4.read_text())
    start_ns = int(summary["formal_started_monotonic_ns"])
    end_ns = int(summary["formal_ended_monotonic_ns"])
    start_epoch_ns = int(summary["formal_started_epoch_ns"])
    end_epoch_ns = int(summary["formal_ended_epoch_ns"])
    duration_s = (end_ns - start_ns) / 1e9

    rows_by_listener = {}
    listener_analysis = {}
    for path in sorted((args.raw / "listeners/listeners").glob("*.jsonl")):
        snr = path.stem
        rows_by_listener[snr] = ref.load_rows(path, start_ns, end_ns)
        listener_analysis[snr] = ref.analyze_listener(
            path, ref.SLOTS, start_ns / 1e9, end_ns / 1e9
        )

    measurements = {}
    plot_samples = {}
    for snr in ref.OBSERVERS:
        measurements[snr] = {}
        plot_samples[snr] = {}
        for tag_id, name in ref.TAG_TO_BSF.items():
            row, samples = ref.lock_measurement(
                rows_by_listener[snr],
                start_ns,
                tag_id,
                ref.SLOTS[tag_id - 1],
            )
            measurements[snr][name] = row
            plot_samples[snr][name] = samples

    cross_tag = {}
    spreads = []
    for snr in ref.OBSERVERS:
        medians = {
            name: measurements[snr][name]["median_residual_us"]
            for name in ref.TAG_TO_BSF.values()
            if measurements[snr][name]["status"] == "OK"
        }
        spread = (
            max(medians.values()) - min(medians.values())
            if len(medians) == 5
            else None
        )
        if spread is not None:
            spreads.append(spread)
        cross_tag[snr] = {
            "median_residual_us": medians,
            "spread_us": spread,
        }
    g3_pass = bool(spreads) and max(spreads) <= 1000.0

    validity = ref.validity(0.0, duration_s)

    def validity_gate(window: dict) -> bool:
        return (
            window["fleet_zero_anchor_fraction"] == 0.0
            and window["fleet_eight_of_eight_fraction"] >= 0.99
            and all(
                row["zero_anchor_fraction"] == 0.0
                and row["eight_of_eight_fraction"] >= 0.99
                for row in window["per_node"].values()
            )
        )

    g4_pass = validity_gate(validity["full"]) and validity_gate(
        validity["minute4"]
    )

    transport = ref.phase_m.fusion_transport(start_epoch_ns, end_epoch_ns)
    transport_deltas = [
        value
        for source in ("telemetry_delta", "queue_delta")
        for node in transport[source].values()
        for key, value in node.items()
        if key != "samples"
    ]
    listener_transport = {
        snr: row["transport"] for snr, row in listener_analysis.items()
    }
    listener_clean = all(
        row["parse_errors"] == 0
        and (
            row["lstat_delta"] is None
            or row["lstat_delta"]["ring_drops"] == 0
        )
        for row in listener_transport.values()
    ) and all(
        row["serial_errors"] == 0 and row["error"] is None
        for row in summary["listener_summary"]["listeners"].values()
    )
    sub_silent = len(rows_by_listener.get(ref.SUB_SNR, [])) == 0
    sweep_clean = all(
        all(
            node["sweep_counter"].get(key, 0) == 0
            for key in ("gaps", "missing_sweeps", "duplicates", "reorders")
        )
        for node in summary["fusion_uwb_snapshot"].values()
    )
    disconnects = [
        line.strip()
        for line in (args.raw / "fusion/fusion_cdc.log")
        .read_text(encoding="utf-8", errors="replace")
        .splitlines()
        if " FUSION_DISCONNECTED " in line
        and start_epoch_ns
        <= round(float(line.split()[0]) * 1e9)
        <= end_epoch_ns
    ]
    g5_pass = (
        summary["fusion_decoder_errors"] == 0
        and not transport["decoder_error_log_mentions"]
        and all(value == 0 for value in transport_deltas)
        and listener_clean
        and sub_silent
        and sweep_clean
        and not disconnects
    )

    solve_rates = {
        name: row["fraction_epochs_solvable"]
        for name, row in t4["per_tag"].items()
    }
    clusters_pass = (
        len(t4["per_tag"]) == 5
        and t4["sanity"]["five_clusters_present"]
        and all(rate > 0 for rate in solve_rates.values())
    )
    complete_observers = [
        snr
        for snr in ref.OBSERVERS
        if all(
            measurements[snr][name]["status"] == "OK"
            for name in ref.TAG_TO_BSF.values()
        )
    ]
    chosen_observer = (
        max(
            complete_observers,
            key=lambda snr: min(
                len(plot_samples[snr][name])
                for name in ref.TAG_TO_BSF.values()
            ),
        )
        if complete_observers
        else None
    )
    plot_path = (
        args.output_dir / "g3_five_line_phase.png"
        if chosen_observer is not None
        else None
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    if chosen_observer is not None and plot_path is not None:
        ref.make_plot(plot_samples, chosen_observer, plot_path)

    result = {
        "scope": "Cold-start S5a 900 s, main-only beacon field",
        "formal_duration_s": duration_s,
        "t4": {
            "per_node_solve_rate": solve_rates,
            "fleet_solve_rate": (
                sum(row["frames_solved"] for row in t4["per_tag"].values())
                / sum(
                    row["epochs_captured"] for row in t4["per_tag"].values()
                )
            ),
            "five_clusters_present": t4["sanity"]["five_clusters_present"],
            "pass": clusters_pass,
        },
        "g3": {
            "per_observer": cross_tag,
            "worst_complete_observer_spread_us": max(spreads)
            if spreads
            else None,
            "gate_us": 1000.0,
            "plot_observer": chosen_observer,
            "plot": str(plot_path) if plot_path is not None else None,
            "pass": g3_pass,
        },
        "g4": {
            **validity,
            "gate": (
                "full and minute-4, fleet and every node: 8/8 >=99%; "
                "zero-anchor exactly 0"
            ),
            "pass": g4_pass,
        },
        "g5": {
            "host_decoder_errors": summary["fusion_decoder_errors"],
            "host_sweep_counter": {
                name: row["sweep_counter"]
                for name, row in summary["fusion_uwb_snapshot"].items()
            },
            "fusion_transport": transport,
            "listener_transport": listener_transport,
            "sub_vcom_silent": sub_silent,
            "disconnects": disconnects,
            "pass": g5_pass,
        },
        "overall_pass": clusters_pass and g3_pass and g4_pass and g5_pass,
        "field_asterisk": (
            "main-only; redundant-field POR remains unproven until S1prime "
            "after sub-v10.2 deployment"
        ),
    }
    write_json(args.output_dir / "s5a_analysis.json", result)
    print(
        json.dumps(
            {
                "T4": result["t4"]["pass"],
                "G3": g3_pass,
                "G4": g4_pass,
                "G5": g5_pass,
                "overall": result["overall_pass"],
                "g3_spread_us": result["g3"][
                    "worst_complete_observer_spread_us"
                ],
                "fleet_8of8_full": validity["full"][
                    "fleet_eight_of_eight_fraction"
                ],
                "fleet_8of8_minute4": validity["minute4"][
                    "fleet_eight_of_eight_fraction"
                ],
            },
            indent=2,
        )
    )
    return 0 if result["overall_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
