#!/usr/bin/env python3
"""Addendum 5 A1/A3 offline epoch-offset forensics (never opens hardware)."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from analyze_relay8_1_overnight import iter_fusion, robust_listener_epoch_offsets
from batch_g_day_h3 import SLOT_MAP
from batch_g_overnight import NODES, TAG_NUMBER, u32_delta
from fusion_session import parse_fields

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS / "alignment" / "v2"))
import time_aligner_v2 as align  # noqa: E402

RESTART_CAUSES = ("frame", "overrun", "break_idle", "parser", "explicit", "other")


def telemetry_deltas(log: Path, start: float, end: float) -> dict[str, object]:
    first: dict[str, dict[str, int]] = {}
    last: dict[str, dict[str, int]] = {}
    for _, line in iter_fusion(log, start, end):
        if "FUSION_TELEMETRY " not in line:
            continue
        f = parse_fields(line)
        node = f.get("name")
        if node not in NODES:
            continue
        row = {k: int(f[k], 0) for k in ("uart_restarts",) + RESTART_CAUSES if k in f}
        first.setdefault(node, row)
        last[node] = row
    return {
        node: {k: u32_delta(first[node][k], last[node][k]) for k in first.get(node, {}) if k in last.get(node, {})}
        for node in NODES
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("source", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--previous", type=Path)
    args = ap.parse_args()
    capture = json.loads((args.source / "formal_capture.json").read_text())
    start = float(capture["started_monotonic"])
    end = float(capture["ended_monotonic"])
    log = args.source / "fusion_cdc.log"
    listener = args.source / "continuous_listener_capture"
    boards = align.extract_fusion(log, start, end)
    fits = {n: align.fit_board(boards[n]) for n in NODES}
    sources = {n: 0xB100 + TAG_NUMBER[n] for n in NODES}
    polls, listener_audit = align.load_listener_polls(
        listener, start, end, {sources[n]: SLOT_MAP[n] for n in NODES})
    anchors = robust_listener_epoch_offsets(boards, fits, sources, polls)

    rows: dict[str, list[dict[str, object]]] = defaultdict(list)
    telemetry: dict[str, list[dict[str, object]]] = defaultdict(list)
    for host, line in iter_fusion(log, start, end):
        f = parse_fields(line)
        node = f.get("name")
        if node not in NODES:
            continue
        if "FUSION_UWB " in line:
            rows[node].append({
                "host": host, "sweep": int(f["sweep"], 0),
                "valid": int(f.get("sf_valid", "0"), 0),
                "mod": None if f.get("sf_mod16", "-") == "-" else int(f["sf_mod16"], 0),
            })
        elif "FUSION_TELEMETRY " in line:
            telemetry[node].append({"host": host, **{k: int(f[k], 0) for k in f if k in ("uart_restarts", "sweep_drop") + RESTART_CAUSES}})

    result: dict[str, object] = {
        "source": str(args.source), "start": start, "end": end,
        "listener_audit": listener_audit, "nodes": {},
        "current_uart_restart_deltas": telemetry_deltas(log, start, end),
    }
    for node in NODES:
        fit = fits[node]
        offset0 = int(anchors["nodes"][node]["modal_offset"])
        offsets: list[tuple[int, int, dict[str, object]]] = []
        invalid_runs: list[dict[str, object]] = []
        run_start = None
        for i, row in enumerate(rows[node]):
            if row["valid"]:
                off = (int(row["mod"]) - ((int(fit.epoch_index[i]) + offset0) & 15)) & 15
                offsets.append((i, off, row))
                if run_start is not None:
                    invalid_runs.append({"first_index": run_start, "last_index": i - 1,
                                         "count": i - run_start,
                                         "start_host": rows[node][run_start]["host"],
                                         "end_host": rows[node][i - 1]["host"],
                                         "span_s": rows[node][i - 1]["host"] - rows[node][run_start]["host"]})
                    run_start = None
            elif run_start is None:
                run_start = i
        if run_start is not None:
            i = len(rows[node])
            invalid_runs.append({"first_index": run_start, "last_index": i - 1, "count": i - run_start,
                                 "start_host": rows[node][run_start]["host"], "end_host": rows[node][-1]["host"],
                                 "span_s": rows[node][-1]["host"] - rows[node][run_start]["host"]})
        transitions = []
        for (ia, oa, _), (ib, ob, rb) in zip(offsets, offsets[1:]):
            if oa != ob:
                transitions.append({"from": oa, "to": ob, "index": ib, "sweep": rb["sweep"], "host": rb["host"]})
        episodes: list[dict[str, object]] = []
        for transition in transitions:
            if episodes and transition["host"] - episodes[-1]["last_host"] <= 1.0:
                episodes[-1]["path"].append(transition["to"])
                episodes[-1]["last_host"] = transition["host"]
                episodes[-1]["raw"].append(transition)
            else:
                episodes.append({"index": transition["index"], "sweep": transition["sweep"],
                                 "host": transition["host"], "last_host": transition["host"],
                                 "path": [transition["from"], transition["to"]], "raw": [transition]})
        marker_candidates = [{"kind": "sf_valid_zero", "host": row["host"], "index": i}
                             for i, row in enumerate(rows[node]) if not row["valid"]]
        for prior, current in zip(telemetry[node], telemetry[node][1:]):
            if current.get("sweep_drop", 0) != prior.get("sweep_drop", 0):
                marker_candidates.append({"kind": "sweep_drop_increment", "host": current["host"],
                                          "delta": u32_delta(prior["sweep_drop"], current["sweep_drop"])})
        status_series = capture["status_series"][node]
        for prior, current in zip(status_series, status_series[1:]):
            pf, cf = prior["fields"], current["fields"]
            if pf.get("lock") != cf.get("lock"):
                marker_candidates.append({"kind": "beacon_lock_transition", "host": current["received_monotonic"]})
            if pf.get("rx") == cf.get("rx") and pf.get("miss") != cf.get("miss"):
                marker_candidates.append({"kind": "beacon_rx_stall", "host": current["received_monotonic"]})
        for episode in episodes:
            episode["markers"] = [m for m in marker_candidates if abs(m["host"] - episode["host"]) <= 12.0]
            episode["marked"] = bool(episode["markers"])
        result["nodes"][node] = {
            "records": len(rows[node]), "valid_records": len(offsets),
            "sf_invalid_records": len(rows[node]) - len(offsets),
            "offset_distribution": dict(sorted(Counter(x[1] for x in offsets).items())),
            "offset_transitions": transitions, "transition_episodes": episodes,
            "transition_association_window_s": 12.0, "invalid_runs": invalid_runs,
            "listener_anchor": anchors["nodes"][node],
            "sweep_backward_boundaries": [i for i in range(1, len(rows[node])) if rows[node][i]["sweep"] < rows[node][i-1]["sweep"]],
        }
    if args.previous:
        old = json.loads((args.previous / "formal_capture.json").read_text())
        ps, pe = float(old["started_monotonic"]), float(old["ended_monotonic"])
        result["previous"] = {"source": str(args.previous), "start": ps, "end": pe,
                              "uart_restart_deltas": telemetry_deltas(args.previous / "fusion_cdc.log", ps, pe)}
    old_analysis = json.loads((args.source / "S3_RUN_STATE.json").read_text())["analysis"]
    adjudication = {}
    for node in NODES:
        old = old_analysis["nodes"][node]
        forensic = result["nodes"][node]
        gates = {
            "rate_in_band": 8.25 <= float(old["tag_domain_rate_hz"]) <= 8.42,
            "delta_mod16_plus1": float(old["delta_mod16_plus1_fraction"]) >= 0.999,
            "all_transition_episodes_marked": all(x["marked"] for x in forensic["transition_episodes"]),
            "rxarm_zero": int(old["status"]["rxarm_delta"]) == 0,
            "reorder_zero": int(old["telemetry_deltas"].get("reorder", 0)) == 0,
        }
        adjudication[node] = {"pass": all(gates.values()), "gates": gates,
                              "offset_distribution": forensic["offset_distribution"],
                              "sf_valid_zero_count": forensic["sf_invalid_records"]}
    a3 = {
        "diagnostic_only": True, "nodes": adjudication,
        "global_transport_gates": {"disconnects_zero": not capture["disconnects"],
                                   "malformed_zero": not capture["malformed"],
                                   "decoder_errors_zero": int(capture["decoder_errors"]) == 0},
    }
    a3["pass"] = all(x["pass"] for x in adjudication.values()) and all(a3["global_transport_gates"].values())
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "A1_OFFLINE_FORENSICS.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    (args.output / "A3_DIAGNOSTIC_ADJUDICATION.json").write_text(json.dumps(a3, indent=2, sort_keys=True) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
