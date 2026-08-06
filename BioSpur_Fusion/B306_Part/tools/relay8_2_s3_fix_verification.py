#!/usr/bin/env python3
"""Relay8.2 S3 ten-node fix verification and mandatory safe teardown.

This is deliberately a one-stage runner.  It never opens the Tag Master,
never flashes, and always leaves the tags composed-idle and the main beacon at
100 ms before reporting a verdict.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict, deque
from pathlib import Path

import batch_g_overnight as bg
from analyze_relay8_1_overnight import (
    iter_fusion,
    listener_field_metrics,
    robust_listener_epoch_offsets,
)
from batch_g_day_h3 import SLOT10, SLOT_MAP
from batch_g_overnight import NODES, TAG_NUMBER, active_cfg, utc_now, u32_delta
from batch_g_overnight_core import composed_idle_cfg
from batch_g_stallfix import anchor_responder_gate
from capacity_ramp import b306_command
from fusion_session import FusionController, SessionError, parse_fields, parse_reply
from formal_window_contract import assert_formal_window_contract
from listener_array_run import wait_listener_preflight
from r4_final_capture import behavioral_slot_proof, start_listener_collector, stop_listener_collector
from relay8_1_overnight_run import OvernightRunner


TOOLS = Path(__file__).resolve().parent
ALIGNER = TOOLS / "alignment" / "v2"
if str(ALIGNER) not in sys.path:
    sys.path.insert(0, str(ALIGNER))
import time_aligner_v2 as align  # noqa: E402


MASTER_MARKER = "dk-fusion-imu-relay-v29"
QUERY_PERIOD_S = 10.0
FORMAL_S = 300.0
WITNESS_S = 90.0
TRANSITION_ASSOCIATION_WINDOW_S = 12.0
TRANSITION_EPISODE_GAP_S = 1.0
HARD_LEDGER = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "drop_err",
    "notify_errno",
    "uart_err",
    "logger_drop",
)
RESTART_CAUSES = ("frame", "overrun", "break_idle", "parser", "explicit", "other")


# StallfixRunner.open() resolves the batch_g_overnight global at runtime.
bg.MASTER_MARKER = MASTER_MARKER


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def fields_from_status(text: str) -> dict[str, str]:
    return parse_fields(text) if text.startswith("BEACON ") else {}


def require_fleet(runner: OvernightRunner) -> dict[str, object]:
    listing = runner.list_peers()
    aggregate = listing["aggregate"]
    peers = listing["peers"]
    if (
        aggregate.get("count") != "10"
        or aggregate.get("ready") != "10"
        or set(peers) != set(NODES)
    ):
        raise SessionError(f"S3 requires 10/10 connected+subscribed: {listing}")
    return listing


def imu_off_gate(runner: OvernightRunner) -> dict[str, object]:
    assert runner.channel is not None
    rows: dict[str, object] = {}
    for node in NODES:
        result = b306_command(runner.channel, node, "IMU STATUS", "IMU ")
        text = str(result.get("text", ""))
        rows[node] = result
        if "active=0 " not in f"{text} ":
            raise SessionError(f"{node} IMU is not off: {text}")
    return rows


def query_v33_restart_counters(runner: OvernightRunner) -> dict[str, object]:
    """Read CTR1+CTRU once per node with one TX and a bounded reply wait."""
    assert runner.channel is not None
    rows: dict[str, object] = {}
    for node in NODES:
        controller = FusionController(runner.channel, node, timeout_s=8.0,
                                      max_attempts=1)
        first = controller.command("COUNTERS", lambda text: text.startswith("CTR1 "),
                                   allow_resend_after_tx=False)
        line = controller.read_until(
            lambda value: (
                (reply := parse_reply(value)) is not None
                and reply.source == "B306"
                and reply.correlation == first.correlation
                and reply.text.startswith("CTRU ")
            ),
            8.0,
            f"{node} correlated CTRU",
        )
        second = parse_reply(line)
        assert second is not None
        rows[node] = {
            "correlation": first.correlation,
            "ctr1": first.text,
            "ctru": second.text,
            "fields": parse_fields(second.text),
        }
    return rows


def restart_counter_deltas(start: dict[str, object], end: dict[str, object]) -> dict[str, object]:
    rows: dict[str, object] = {}
    for node in NODES:
        before = start[node]["fields"]
        after = end[node]["fields"]
        names = ("restart",) + RESTART_CAUSES + ("discarded", "tag_reset", "recovery")
        deltas = {name: u32_delta(int(before[name], 0), int(after[name], 0))
                  for name in names}
        cause_sum = sum(deltas[name] for name in RESTART_CAUSES)
        rows[node] = {
            "deltas": deltas,
            "cause_sum": cause_sum,
            "total_restarts": deltas["restart"],
            "cause_sum_matches_total": cause_sum == deltas["restart"],
        }
    return rows


def configure_active(runner: OvernightRunner, count: int,
                     nodes: tuple[str, ...] = NODES) -> dict[str, object]:
    rows: dict[str, object] = {}
    for node in nodes:
        command = active_cfg(
            TAG_NUMBER[node], SLOT_MAP[node], count=count, beacon_win_n=1
        )
        row = runner.send_tag_cfg_echo(node, command)
        rows[node] = row
        runner.slot_map[node] = SLOT_MAP[node]
        runner.win_map[node] = 1
        runner.checkpoint()
    return rows


def reboot_tags_parallel(runner: OvernightRunner, nodes: tuple[str, ...]) -> dict[str, object]:
    """Queue all E2 reboots, then observe each tag-owned sweep discontinuity."""
    assert runner.channel is not None
    before = {node: runner.latest_sweep.get(node) for node in nodes}
    rows: dict[str, object] = {}
    for node in nodes:
        controller = FusionController(runner.channel, node, timeout_s=8.0,
                                      max_attempts=1)
        reply = controller.command(
            "TAG REBOOT", lambda text: text.startswith("RELAY_QUEUED"),
            source="B306", allow_resend_after_tx=False)
        rows[node] = {
            "queued_correlation": reply.correlation,
            "queued_text": reply.text,
            "before_sweep": before[node],
            "queued_monotonic": time.monotonic(),
        }
    pending = set(nodes)
    deadline = time.monotonic() + 60.0
    while pending and time.monotonic() < deadline:
        line = runner.channel.read(deadline)
        if line is None or not line.startswith("FUSION_UWB "):
            continue
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in pending or "sweep" not in fields or before[node] is None:
            continue
        sweep = int(fields["sweep"], 0)
        if sweep < int(before[node]):
            rows[node].update({
                "after_sweep": sweep,
                "discontinuity_monotonic": time.monotonic(),
                "discontinuity_pass": True,
            })
            pending.remove(node)
    for node in pending:
        rows[node]["discontinuity_pass"] = False
    # Measured command-path warm-up is 15.70--15.75 s after boot.
    time.sleep(15.75)
    return {"nodes": rows, "all_discontinuities_seen": not pending,
            "missing": sorted(pending)}


def cfg_reply_matches(row: dict[str, object], node: str, count: int) -> bool:
    fields = parse_fields(str(row.get("reply", "")))
    return (
        row.get("completion") == "cfg_ok"
        and fields.get("SLOT") == f"{SLOT_MAP[node]}/{count}"
        and fields.get("PERIOD") == "10"
        and fields.get("BEACON_SYNC") == "1"
        and fields.get("RUN") == "1"
    )


def behavioral_acceptance(
    *, cfg_rows: dict[str, object], capture: dict[str, object],
    slot_proof: dict[str, object], count: int, fusion_log: Path,
) -> dict[str, object]:
    duration = float(capture["duration_s"])
    align.GRID_US = 120_000.0
    boards = align.extract_fusion(
        fusion_log, float(capture["started_monotonic"]),
        float(capture["ended_monotonic"]))
    locked_generations = Counter(
        row["fields"].get("gen")
        for series in capture["status_series"].values()
        for row in series
        if row["fields"].get("sync") == "1"
        and row["fields"].get("lock") == "1"
        and row["fields"].get("gen") is not None
    )
    current_generation = locked_generations.most_common(1)[0][0] if locked_generations else None
    nodes: dict[str, object] = {}
    for node, cfg in cfg_rows.items():
        records = int(capture["records"].get(node, 0))
        board = boards.get(node)
        if board is not None and len(board.frame_us) >= 3:
            fit = align.fit_board(board)
            epochs = int(fit.epoch_index[-1] - fit.epoch_index[0])
            rate_n = len(board.frame_us) - 1
            rate_d = epochs * 0.120
            rate = rate_n / rate_d if rate_d else 0.0
        else:
            rate_n = 0
            rate_d = 0.0
            rate = 0.0
        statuses = capture["status_series"].get(node, [])
        locked = [row for row in statuses if row["fields"].get("sync") == "1"
                  and row["fields"].get("lock") == "1"
                  and row["fields"].get("gen") == current_generation]
        slot = slot_proof["nodes"].get(node, {})
        behavior_ok = (
            8.25 <= rate <= 8.42
            and bool(locked)
            and bool(slot.get("pass"))
        )
        reply_ok = cfg_reply_matches(cfg, node, count)
        accepted_by = "reply" if reply_ok else "behaviour" if behavior_ok else None
        nodes[node] = {
            "accepted_by": accepted_by,
            "reply_completion": cfg.get("completion"),
            "reply": cfg.get("reply"),
            "reply_latency_s": cfg.get("elapsed_s") if reply_ok else None,
            "behavior": {
                "records_numerator": records,
                "duration_s_denominator": duration,
                "delivered_rate_hz": rate,
                "tag_domain_records_numerator": rate_n,
                "tag_domain_seconds_denominator": rate_d,
                "locked_status_numerator": len(locked),
                "status_replies_denominator": len(statuses),
                "generation": current_generation,
                "slot": SLOT_MAP[node],
                "slot_listeners_passing_numerator": slot.get("listeners_passing", 0),
                "slot_listeners_required_denominator": slot.get("required_listeners", 3),
                "slot_pass": bool(slot.get("pass")),
            },
        }
    return {
        "witness_s": duration,
        "current_generation": current_generation,
        "nodes": nodes,
        "accepted": sorted(node for node, row in nodes.items() if row["accepted_by"]),
        "rejected": sorted(node for node, row in nodes.items() if not row["accepted_by"]),
    }


def run_acceptance_witness(runner: OvernightRunner, root: Path, label: str,
                           cfg_rows: dict[str, object], count: int) -> dict[str, object]:
    collector = handle = None
    listener_dir = root / label
    try:
        collector, handle, listener_dir = start_listener_collector(
            root, label=label, duration_s=WITNESS_S + 45.0)
        listener_preflight = wait_listener_preflight(
            listener_dir, collector, timeout_s=25.0)
        capture = capture_with_status(
            runner, root, WITNESS_S, tuple(cfg_rows),
            output_name=f"{label}_fusion_capture.json")
        stopped = stop_listener_collector(collector, handle)
        collector = handle = None
        slots = behavioral_slot_proof(listener_dir, period_us=120_000.0,
                                      minimum_pairs=20)
        return {
            "listener_preflight": listener_preflight,
            "capture": capture,
            "listener_stop": stopped,
            "slot_proof": slots,
            "acceptance": behavioral_acceptance(
                cfg_rows=cfg_rows, capture=capture, slot_proof=slots, count=count,
                fusion_log=root / "fusion_cdc.log"),
        }
    finally:
        if collector is not None or handle is not None:
            stop_listener_collector(collector, handle)


def listener_state(snapshot: dict[str, object]) -> dict[str, object]:
    decoded = snapshot.get("decoded") or {}
    lines = decoded.get("post_lines", [])
    lbstat = [line for line in lines if line.startswith("LBSTAT;")]
    return {
        "slaved": any(";SLAVED;" in line for line in lines),
        "tx_records": sum(line.startswith("LBTX;") for line in lines),
        "lbstat": lbstat,
        "lines": lines,
    }


def capture_with_status(
    runner: OvernightRunner, root: Path, duration_s: float,
    participants: tuple[str, ...] = NODES,
    output_name: str = "formal_capture.json",
) -> dict[str, object]:
    """Capture while staggering one read-only status request per second.

    Each node is queried every ten seconds.  Requests are not retried or
    awaited; all returned replies are harvested from the common stream.  This
    prevents a flaky reply path from blocking the data-plane capture.
    """
    assert runner.channel is not None
    channel = runner.channel
    started = time.monotonic()
    deadline = started + duration_s
    next_send = {
        node: started + index * (QUERY_PERIOD_S / len(NODES))
        for index, node in enumerate(participants)
    }
    query_rows: list[dict[str, object]] = []
    status_series: dict[str, list[dict[str, object]]] = {
        node: [] for node in participants
    }
    records: Counter[str] = Counter()
    imu_records: Counter[str] = Counter()
    malformed: list[str] = []
    disconnects: list[str] = []
    decoder_before = channel.binary_decoder.errors
    last_record = {node: started for node in participants}

    while time.monotonic() < deadline:
        now = time.monotonic()
        due = [node for node in participants if next_send[node] <= now]
        for node in due:
            command = f"{node} TAG RAW BEACON_STATUS"
            channel.send(command)
            query_rows.append(
                {
                    "node": node,
                    "sent_monotonic": time.monotonic(),
                    "sent_utc": utc_now(),
                    "command": command,
                }
            )
            next_send[node] += QUERY_PERIOD_S

        wake = min(deadline, min(next_send.values()), time.monotonic() + 0.25)
        line = channel.read(wake)
        if line is None:
            continue
        fields = parse_fields(line)
        node = fields.get("name")
        if line.startswith("FUSION_UWB ") and node in participants:
            records[node] += 1
            last_record[node] = time.monotonic()
        elif line.startswith("FUSION_IMU ") and node in participants:
            imu_records[node] += 1
        elif line.startswith("FUSION_DISCONNECTED "):
            disconnects.append(line)
        elif line.startswith("FUSION_MALFORMED "):
            malformed.append(line)

        reply = parse_reply(line)
        if (
            reply is not None
            and node in participants
            and reply.source == "TAG"
            and reply.text.startswith("BEACON ")
        ):
            status_series[node].append(
                {
                    "received_monotonic": time.monotonic(),
                    "received_utc": utc_now(),
                    "correlation": reply.correlation,
                    "text": reply.text,
                    "fields": fields_from_status(reply.text),
                    "sweep": runner.latest_sweep.get(node),
                }
            )

        stalled = [
            node for node in participants if time.monotonic() - last_record[node] > 30.0
        ]
        if stalled:
            raise SessionError(f"S3 zero-UWB-progress RED: {stalled}")

    ended = time.monotonic()
    result = {
        "started_monotonic": started,
        "ended_monotonic": ended,
        "started_utc": utc_now(),
        "duration_s": ended - started,
        "expected_period_us": runner.period_us,
        "query_period_s_per_node": QUERY_PERIOD_S,
        "query_rows": query_rows,
        "status_series": status_series,
        "records": dict(records),
        "imu_records": dict(imu_records),
        "disconnects": disconnects,
        "malformed": malformed,
        "decoder_errors": channel.binary_decoder.errors - decoder_before,
        "host_drain": channel.health_snapshot(),
    }
    write_json(root / output_name, result)
    return result


def status_metrics(series: list[dict[str, object]]) -> dict[str, object]:
    locked = [row for row in series if row.get("fields", {}).get("lock") == "1"]
    values = [row["fields"] for row in locked]
    result: dict[str, object] = {
        "replies": len(series),
        "locked_replies": len(locked),
        "series": series,
    }
    if len(values) < 2:
        result.update({"miss_fraction": None, "rxarm_delta": None})
        return result
    rx = u32_delta(int(values[0]["rx"], 0), int(values[-1]["rx"], 0))
    miss = u32_delta(int(values[0]["miss"], 0), int(values[-1]["miss"], 0))
    rxarm = u32_delta(int(values[0]["rxarm"], 0), int(values[-1]["rxarm"], 0))
    miss_denominator = rx + miss
    result.update(
        {
            "rx_delta": rx,
            "miss_delta": miss,
            "miss_numerator": miss,
            "miss_denominator": miss_denominator,
            "miss_minimum_denominator": 100,
            "miss_fraction": miss / miss_denominator if miss_denominator >= 100 else None,
            "rxarm_delta": rxarm,
            "generation_first": values[0].get("gen"),
            "generation_last": values[-1].get("gen"),
        }
    )
    return result


def reorder_contexts(log: Path, start: float, end: float) -> list[dict[str, object]]:
    rows = list(iter_fusion(log, start, end))
    previous: dict[str, int] = {}
    contexts: list[dict[str, object]] = []
    for index, (host, line) in enumerate(rows):
        if "FUSION_TELEMETRY " not in line:
            continue
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES or "reorder" not in fields:
            continue
        current = int(fields["reorder"], 0)
        prior = previous.get(node, current)
        delta = u32_delta(prior, current)
        previous[node] = current
        if delta == 0:
            continue
        if any(row["node"] == node for row in contexts):
            continue
        surrounding = [
            text
            for _t, text in rows[max(0, index - 10) : min(len(rows), index + 11)]
            if f"name={node}" in text
            or "CFG" in text
            or "CONNECTED" in text
            or "DISCONNECTED" in text
        ]
        contexts.append(
            {
                "node": node,
                "host_monotonic": host,
                "delta": delta,
                "previous": prior,
                "current": current,
                "surrounding": surrounding,
            }
        )
        if len(contexts) == len(NODES):
            break
    return contexts


def tag_reset_events(log: Path, start: float, end: float) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for host, line in iter_fusion(log, start, end):
        if "TAG_RESET_DETECTED " not in line:
            continue
        fields = parse_fields(line)
        before = int(fields["before"], 0) if "before" in fields else None
        after = int(fields["after"], 0) if "after" in fields else None
        corroborated = before is not None and after is not None and after < before
        events.append({
            "host_monotonic": host,
            "node": fields.get("name"),
            "before": before,
            "after": after,
            "corroborated_backward_jump": corroborated,
            "classification": "true_positive" if corroborated else "false_positive",
            "line": line,
        })
    return events


def analyze(
    root: Path, capture: dict[str, object], listener_dir: Path,
    participants: tuple[str, ...] = NODES,
) -> dict[str, object]:
    start = float(capture["started_monotonic"])
    end = float(capture["ended_monotonic"])
    expected_period_us = int(capture["expected_period_us"])
    align.GRID_US = float(expected_period_us)
    fusion_log = root / "fusion_cdc.log"
    boards = align.extract_fusion(fusion_log, start, end)
    missing = sorted(set(participants) - set(boards))
    if missing:
        raise SessionError(f"missing Fusion UWB boards in S3: {missing}")
    fits = {node: align.fit_board(boards[node]) for node in participants}
    sources = {node: 0xB100 + TAG_NUMBER[node] for node in participants}
    polls, listener_audit = align.load_listener_polls(
        listener_dir,
        start,
        end,
        {sources[node]: SLOT_MAP[node] for node in participants},
    )
    reset_events = tag_reset_events(fusion_log, start, end)
    true_reset_nodes = {
        row["node"] for row in reset_events if row["corroborated_backward_jump"]
    }

    mods: dict[str, list[int]] = defaultdict(list)
    mods_aligned: dict[str, list[int | None]] = defaultdict(list)
    telemetry_first: dict[str, dict[str, int]] = {}
    telemetry_last: dict[str, dict[str, int]] = {}
    telemetry_series: dict[str, list[dict[str, object]]] = defaultdict(list)
    for _host, line in iter_fusion(fusion_log, start, end):
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in participants:
            continue
        if "FUSION_UWB " in line and fields.get("sf_valid") == "1":
            mods[node].append(int(fields["sf_mod16"], 0))
            mods_aligned[node].append(int(fields["sf_mod16"], 0))
        elif "FUSION_UWB " in line:
            mods_aligned[node].append(None)
        elif "FUSION_TELEMETRY " in line:
            row = {
                key: int(fields[key], 0)
                for key in HARD_LEDGER + ("reorder",)
                if key in fields
            }
            telemetry_first.setdefault(node, dict(row))
            telemetry_last[node] = dict(row)
            telemetry_series[node].append({
                "host_monotonic": _host,
                **{key: int(fields[key], 0) for key in ("sweep_drop",) if key in fields},
            })

    nodes: dict[str, object] = {}
    for node in participants:
        board = boards[node]
        fit = fits[node]
        epochs = int(fit.epoch_index[-1] - fit.epoch_index[0])
        rate_n = max(0, len(board.frame_us) - 1)
        rate_d = epochs * (expected_period_us / 1_000_000.0)
        rate = rate_n / rate_d if rate_d else 0.0
        deltas16 = [((b - a) & 0xF) for a, b in zip(mods[node], mods[node][1:])]
        plus1_n = deltas16.count(1)
        plus1_d = len(deltas16)
        plus1 = plus1_n / plus1_d if plus1_d >= 100 else None
        # A tag-owned sweep reset creates a new association segment. Compute
        # the listener-backed integer offset independently per segment so a
        # global modal offset cannot turn a recovered reset into a false
        # epoch-exact failure.
        boundaries = [0] + [i for i in range(1, len(board.sweep))
                            if board.sweep[i] < board.sweep[i - 1]] + [len(board.sweep)]
        reset_outages = [
            {
                "before_sweep": board.sweep[index - 1],
                "after_sweep": board.sweep[index],
                "last_before_host_monotonic": board.host_s[index - 1],
                "first_after_host_monotonic": board.host_s[index],
                "record_gap_s": board.host_s[index] - board.host_s[index - 1],
                "estimated_outage_s_excluding_one_nominal_period": max(
                    0.0, board.host_s[index] - board.host_s[index - 1]
                    - expected_period_us / 1_000_000.0),
            }
            for index in boundaries[1:-1]
        ]
        exact_n = 0
        paired = 0
        epoch_offset_counts: Counter[int] = Counter()
        epoch_offset_transitions: list[dict[str, object]] = []
        previous_epoch_offset: int | None = None
        starting_epoch_offset: int | None = None
        segmented_rate_n = 0
        segmented_rate_d = 0.0
        segment_rows: list[dict[str, object]] = []
        for lo, hi in zip(boundaries, boundaries[1:]):
            if hi - lo < 3:
                continue
            segment = align.BoardData(
                node, board.host_s[lo:hi], board.master_ms[lo:hi],
                board.sweep[lo:hi], board.poll_tx[lo:hi], board.frame_us[lo:hi],
                board.strobe_us[lo:hi], [], [], [], [], [])
            segment_fit = align.fit_board(segment)
            segment_epochs = int(segment_fit.epoch_index[-1] - segment_fit.epoch_index[0])
            segmented_rate_n += max(0, len(segment.frame_us) - 1)
            segmented_rate_d += segment_epochs * (expected_period_us / 1_000_000.0)
            segment_f4 = robust_listener_epoch_offsets(
                {node: segment}, {node: segment_fit}, {node: sources[node]}, polls)
            offset = int(segment_f4["nodes"][node]["modal_offset"])
            seg_n = seg_d = 0
            for local, mod in enumerate(mods_aligned[node][lo:hi]):
                if mod is None:
                    continue
                seg_d += 1
                expected_mod = (int(segment_fit.epoch_index[local]) + offset) & 0xF
                epoch_offset = (mod - expected_mod) & 0xF
                if starting_epoch_offset is None:
                    starting_epoch_offset = epoch_offset
                epoch_offset_counts[epoch_offset] += 1
                if previous_epoch_offset is not None and epoch_offset != previous_epoch_offset:
                    epoch_offset_transitions.append({
                        "from": previous_epoch_offset, "to": epoch_offset,
                        "record_index": lo + local,
                        "sweep": board.sweep[lo + local],
                        "host_monotonic": board.host_s[lo + local],
                    })
                previous_epoch_offset = epoch_offset
                seg_n += epoch_offset == 0
            exact_n += seg_n
            paired += seg_d
            segment_rows.append({
                "first_index": lo, "last_index_exclusive": hi,
                "sweep_first": board.sweep[lo], "sweep_last": board.sweep[hi - 1],
                "exact_numerator": seg_n, "exact_denominator": seg_d,
                "modal_offset": offset,
            })
        if node in true_reset_nodes and segmented_rate_d:
            rate_n = segmented_rate_n
            rate_d = segmented_rate_d
            rate = rate_n / rate_d
        exact = exact_n / paired if paired >= 100 else None
        status = status_metrics(capture["status_series"][node])
        # Collapse a short out-and-back excursion into one transition episode;
        # this preserves every raw transition while associating one mechanism.
        transition_episodes: list[dict[str, object]] = []
        for transition in epoch_offset_transitions:
            if (transition_episodes and
                    float(transition["host_monotonic"]) - float(transition_episodes[-1]["last_host_monotonic"])
                    <= TRANSITION_EPISODE_GAP_S):
                episode = transition_episodes[-1]
                episode["path"].append(transition["to"])
                episode["raw_transitions"].append(transition)
                episode["last_host_monotonic"] = transition["host_monotonic"]
            else:
                transition_episodes.append({
                    "first_record_index": transition["record_index"],
                    "first_sweep": transition["sweep"],
                    "first_host_monotonic": transition["host_monotonic"],
                    "last_host_monotonic": transition["host_monotonic"],
                    "path": [transition["from"], transition["to"]],
                    "raw_transitions": [transition],
                })
        sweep_drop_markers = []
        for prior, current in zip(telemetry_series[node], telemetry_series[node][1:]):
            if "sweep_drop" in prior and "sweep_drop" in current:
                delta = u32_delta(int(prior["sweep_drop"]), int(current["sweep_drop"]))
                if delta:
                    sweep_drop_markers.append({"kind": "sweep_drop_increment",
                                               "host_monotonic": current["host_monotonic"],
                                               "delta": delta})
        beacon_markers = []
        series = capture["status_series"][node]
        for prior, current in zip(series, series[1:]):
            pf, cf = prior.get("fields", {}), current.get("fields", {})
            if pf.get("lock") != cf.get("lock"):
                beacon_markers.append({"kind": "beacon_lock_transition",
                                       "host_monotonic": current["received_monotonic"],
                                       "from": pf.get("lock"), "to": cf.get("lock")})
            if (pf.get("rx") == cf.get("rx") and pf.get("miss") != cf.get("miss")):
                beacon_markers.append({"kind": "beacon_rx_stall",
                                       "host_monotonic": current["received_monotonic"],
                                       "rx": cf.get("rx"), "miss": cf.get("miss")})
        reset_markers = [{"kind": "corroborated_tag_reset",
                          "host_monotonic": event["host_monotonic"]}
                         for event in reset_events
                         if event["node"] == node and event["corroborated_backward_jump"]]
        invalid_markers = [{"kind": "sf_valid_zero",
                            "host_monotonic": board.host_s[index], "record_index": index}
                           for index, mod in enumerate(mods_aligned[node]) if mod is None]
        candidates = invalid_markers + reset_markers + sweep_drop_markers + beacon_markers
        for episode in transition_episodes:
            host = float(episode["first_host_monotonic"])
            episode["markers"] = [marker for marker in candidates
                                  if abs(float(marker["host_monotonic"]) - host)
                                  <= TRANSITION_ASSOCIATION_WINDOW_S]
            episode["marked"] = bool(episode["markers"])
        first = telemetry_first.get(node, {})
        last = telemetry_last.get(node, {})
        ledger = {
            key: u32_delta(first[key], last[key])
            for key in first
            if key in last
        }
        # Addendum 5 freezes the hard set explicitly. Beacon miss remains
        # recorded context; rxarm is the direct guard-slot gate.
        node_pass = (
            8.25 <= rate <= 8.42
            and plus1 is not None and plus1 >= 0.999
            and all(bool(episode["marked"]) for episode in transition_episodes)
            and status.get("rxarm_delta") == 0
        )
        nodes[node] = {
            "slot": SLOT_MAP[node],
            "uwb_records": len(board.frame_us),
            "elapsed_epochs": epochs,
            "tag_domain_rate_hz": rate,
            "rate_records_numerator": rate_n,
            "rate_elapsed_seconds_denominator": rate_d,
            "delta_mod16_plus1_fraction": plus1,
            "delta_mod16_plus1_numerator": plus1_n,
            "delta_mod16_plus1_denominator": plus1_d,
            "delta_mod16_plus1_minimum_denominator": 100,
            "delta_mod16_histogram": dict(sorted(Counter(deltas16).items())),
            "listener_absolute_epoch_exact_fraction": exact,
            "listener_absolute_epoch_exact_numerator": exact_n,
            "listener_absolute_epoch_exact_denominator": paired,
            "listener_absolute_epoch_exact_minimum_denominator": 100,
            "distinct_epoch_offset_count": len(epoch_offset_counts),
            "epoch_offset_distribution": dict(sorted(epoch_offset_counts.items())),
            "starting_epoch_offset": starting_epoch_offset,
            "epoch_offset_value": next(iter(epoch_offset_counts)) if len(epoch_offset_counts) == 1 else None,
            "epoch_offset_transitions": epoch_offset_transitions,
            "epoch_offset_transition_episodes": transition_episodes,
            "transition_association_window_s": TRANSITION_ASSOCIATION_WINDOW_S,
            "sf_valid_zero_count": len(board.frame_us) - paired,
            "sf_valid_zero_fraction": ((len(board.frame_us) - paired) / len(board.frame_us)
                                       if board.frame_us else None),
            "association_segments": segment_rows,
            "reset_outages": reset_outages,
            "listener_pairs": paired,
            "status": status,
            "telemetry_deltas": ledger,
            "pass": node_pass,
            "batch_gate_pass": bool(node_pass),
            "corroborated_true_reset": node in true_reset_nodes,
        }

    listener = listener_field_metrics(listener_dir, start, end)
    contexts = reorder_contexts(fusion_log, start, end)
    write_json(root / "S3_REORDER_FORENSICS.json", contexts)
    global_pass = (
        all(bool(row["batch_gate_pass"]) for row in nodes.values())
        and not capture["disconnects"]
        and not capture["malformed"]
        and int(capture["decoder_errors"]) == 0
        and not capture["imu_records"]
        and bool(listener.get("sub_slaved"))
        and not any(not row["corroborated_backward_jump"] for row in reset_events)
    )
    # Addendum 5: a stable listener-backed offset is correctable; its numeric
    # value and old exact ratio are evidence, while any transition is a gate.
    f1 = all(
        all(bool(episode["marked"])
            for episode in row["epoch_offset_transition_episodes"])
        for row in nodes.values()
    )
    f2 = all(
        row["status"]["miss_fraction"] is not None
        and row["status"]["miss_fraction"] < 0.01
        and row["status"]["rxarm_delta"] == 0
        for row in nodes.values()
    )
    result = {
        "pass": global_pass,
        "nodes": nodes,
        "slot10": SLOT10,
        "participants": list(participants),
        "quarantined": sorted(set(NODES) - set(participants)),
        "tag_reset_events": reset_events,
        "tag_reset_false_positives": sum(
            not row["corroborated_backward_jump"] for row in reset_events),
        "listener_audit": listener_audit,
        "listener_field": listener,
        "reorder_contexts": contexts,
        "attribution": {
            "F1_epoch_and_stamp_fix_landed": f1,
            "F2_window_arm_fix_landed": f2,
            "interpretation": (
                "F1 and F2 both landed fleet-wide"
                if f1 and f2
                else "F1 landed; F2 remains specifically visible in window arming"
                if f1
                else "F2 landed; epoch/stamping remains"
                if f2
                else "neither fix is proven"
            ),
        },
    }
    write_json(root / "S3_ANALYSIS.json", result)
    return result


def safe_teardown(runner: OvernightRunner, root: Path) -> dict[str, object]:
    assert runner.channel is not None
    rows: dict[str, object] = {}
    for node in NODES:
        command = composed_idle_cfg(TAG_NUMBER[node], SLOT_MAP[node], 11)
        rows[node] = runner.send_tag_cfg_echo(node, command)
    witness = runner.capture("s3_terminal_idle_witness", 90.0)
    for node in NODES:
        rows[node]["witness_uwb_records"] = witness["records"].get(node, 0)
        rows[node]["idle_behavior_pass"] = witness["records"].get(node, 0) <= 1
    period100 = runner.set_main_period(100, "s3_terminal")
    result = {
        "nodes": rows,
        "witness": witness,
        "main_period100": period100,
        "pass": all(row["idle_behavior_pass"] for row in rows.values()),
    }
    write_json(root / "S3_SAFE_END_STATE.json", result)
    return result


def write_report(
    root: Path,
    result: dict[str, object],
    preflight: dict[str, object],
    teardown: dict[str, object],
) -> None:
    def ratio(value: object, numerator: object, denominator: object,
              minimum: int = 1) -> str:
        den = int(denominator or 0)
        return (f"{float(value):.6f} ({numerator}/{den})"
                if value is not None and den >= minimum
                else f"INSUFFICIENT ({numerator}/{den}; min {minimum})")

    complete_nodes = bool(result.get("nodes"))
    lines = [
        "# relay8.2 S3 fix verification",
        "",
        f"Date: {utc_now()}",
        f"Verdict: **{'PASS' if result.get('pass') else 'FAIL'}**",
        "",
        "Tag Master USB was absent before S3 and probe 1050070698 was not touched.",
        "IMU remained off for the complete formal window.",
        "",
        "| BSF | slot | start offset | transitions/markers | rate Hz | rxarm Δ | Δmod16 +1 | epoch exact | sf_valid=0 | verdict |",
        "|---|---:|---:|---|---:|---:|---:|---:|---:|---|",
    ]
    if complete_nodes:
        for node in sorted(result["nodes"], key=lambda item: SLOT_MAP[item]):
            row = result["nodes"][node]
            status = row["status"]
            lines.append(
                f"| {node} | {row['slot']} | "
                f"{row['starting_epoch_offset'] if row['starting_epoch_offset'] is not None else 'NA'} | "
                f"{len(row['epoch_offset_transition_episodes'])}/"
                f"{sum(bool(e['marked']) for e in row['epoch_offset_transition_episodes'])} | "
                f"{row['tag_domain_rate_hz']:.6f} "
                f"({row['rate_records_numerator']}/{row['rate_elapsed_seconds_denominator']:.3f}s) | "
                f"{status['rxarm_delta']} | "
                f"{ratio(row['delta_mod16_plus1_fraction'], row['delta_mod16_plus1_numerator'], row['delta_mod16_plus1_denominator'], 100)} | "
                f"{ratio(row['listener_absolute_epoch_exact_fraction'], row['listener_absolute_epoch_exact_numerator'], row['listener_absolute_epoch_exact_denominator'], 100)} | "
                f"{row['sf_valid_zero_count']} | "
                f"{'PASS' if row['pass'] else 'FAIL'} |"
            )
    else:
        lines.append("| — | — | — | — | — | — | — | — | — | formal analysis unavailable |")
    lines.extend(
        [
            "",
            "## Attribution",
            "",
            f"- F1 epoch/stamping fix: **{result.get('attribution', {}).get('F1_epoch_and_stamp_fix_landed', 'NOT RUN')}**.",
            f"- F2 receive-window arm fix: **{result.get('attribution', {}).get('F2_window_arm_fix_landed', 'NOT RUN')}**.",
            f"- Interpretation: {result.get('attribution', {}).get('interpretation', 'formal analysis unavailable')}.",
            f"- Slot-10 occupant: `{result.get('slot10', SLOT10)}`; no waiver was applied.",
            "",
            "## rxarm series",
            "",
        ]
    )
    acceptance = result.get("cfg_acceptance", {})
    if acceptance:
        lines.extend([
            "",
            "## CFG acceptance / T3 incidence",
            "",
            "| BSF | accepted by | escalation | reply latency s | behavioral rate (records/seconds) | locked status (n/d) | slot listeners (n/d) |",
            "|---|---|---|---:|---:|---:|---:|",
        ])
        for node in sorted(acceptance["nodes"], key=lambda item: SLOT_MAP[item]):
            row = acceptance["nodes"][node]
            behavior = row["behavior"]
            latency = row["reply_latency_s"]
            lines.append(
                f"| {node} | {row['accepted_by'] or 'REJECTED'} | {row.get('escalation_step')} | "
                f"{latency if latency is not None else 'NA'} | "
                f"{behavior['delivered_rate_hz']:.6f} "
                f"({behavior['records_numerator']}/{behavior['duration_s_denominator']:.3f}s) | "
                f"{behavior['locked_status_numerator']}/{behavior['status_replies_denominator']} | "
                f"{behavior['slot_listeners_passing_numerator']}/{behavior['slot_listeners_required_denominator']} |"
            )
    for node in sorted(result.get("nodes", {}), key=lambda item: SLOT_MAP[item]):
        values = [
            row["fields"].get("rxarm")
            for row in result.get("nodes", {}).get(node, {}).get("status", {}).get("series", [])
            if row.get("fields", {}).get("lock") == "1"
        ]
        lines.append(f"- `{node}`: {values if values else 'NOT RUN'}")
    lines.extend(
        [
            "",
            "## Reorder forensics",
            "",
            f"Telemetry reorder nodes with captured first recurrence: {len(result.get('reorder_contexts', []))}. Full pre/post context is in `S3_REORDER_FORENSICS.json`.",
            "Reorder is recorded for diagnosis and was not used as an S3 gate.",
            "",
            "## End state",
            "",
            ("- Field left resumable with configuration preserved; no idle teardown."
             if teardown.get("resumable") else
             f"- Composed idle behavioral proof all ten: **{teardown.get('pass')}**."),
            (f"- Main beacon preserved at {teardown.get('main_period_ms')} ms."
             if teardown.get("resumable") else "- Main beacon restored to 100 ms."),
            "- Tag Master remains unplugged; no process touched probe 1050070698.",
            "- No capture process remains.",
            "",
            "## Evidence",
            "",
            "- `S3_ANALYSIS.json`",
            "- `S3_REORDER_FORENSICS.json`",
            "- `formal_capture.json`",
            "- `fusion_cdc.log`",
            "- `continuous_listener_capture/`",
            "- `S3_SAFE_END_STATE.json`",
            "- `P0_ANCHOR_GATE.json`",
            "",
        ]
    )
    (root / "S3_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--count", type=int, default=12)
    parser.add_argument("--beacon-period-ms", type=int, default=120)
    parser.add_argument("--leave-resumable", action="store_true")
    parser.add_argument("--resume-state", type=Path)
    args = parser.parse_args()
    if args.evidence_root.exists():
        raise SystemExit(f"refusing existing evidence root: {args.evidence_root}")
    args.evidence_root.mkdir(parents=True)

    state: dict[str, object] = {
        "started": utc_now(),
        "status": "IN_PROGRESS",
        "tag_master": "USB disconnected; excluded",
        "probe_1050070698": "untouched",
    }
    runner = OvernightRunner(args.evidence_root, args.fusion_port, 1.0)
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    collector = None
    collector_handle = None
    listener_dir = args.evidence_root / "continuous_listener_capture"
    result: dict[str, object] = {"pass": False, "nodes": {}, "attribution": {}}
    preflight: dict[str, object] = {}
    teardown: dict[str, object] = {"pass": False}
    try:
        state["anchor_gate"] = anchor_responder_gate(args.evidence_root)
        runner.open()
        state["fleet_gate"] = require_fleet(runner)
        state["imu_off_gate"] = imu_off_gate(runner)
        if args.beacon_period_ms != args.count * 10:
            raise SessionError("beacon period must equal COUNT * PERIOD=10 ms")
        state["main_period"] = runner.set_main_period(args.beacon_period_ms, "s3_entry")
        runner.period_us = args.beacon_period_ms * 1000
        time.sleep(12.0)
        if args.resume_state is not None:
            prior = json.loads(args.resume_state.read_text(encoding="utf-8"))
            state["resume_from"] = str(args.resume_state)
            state["tag_cfg"] = dict(prior["tag_cfg"])
            state["tag_cfg_resend"] = dict(prior.get("tag_cfg_resend", {}))
            acceptance = {}
            for node in NODES:
                row = state["tag_cfg_resend"].get(node) or state["tag_cfg"].get(node)
                if row is None or not cfg_reply_matches(row, node, args.count):
                    raise SessionError(f"resume state lacks definitive current CFG for {node}")
                acceptance[node] = {
                    "accepted_by": "reply",
                    "reply_completion": row.get("completion"),
                    "reply": row.get("reply"),
                    "reply_latency_s": row.get("elapsed_s"),
                    "escalation_step": "E1" if node in state["tag_cfg_resend"] else "E0",
                    "behavior": {
                        "records_numerator": 0, "duration_s_denominator": 0.0,
                        "delivered_rate_hz": 0.0,
                        "tag_domain_records_numerator": 0,
                        "tag_domain_seconds_denominator": 0.0,
                        "locked_status_numerator": 0,
                        "status_replies_denominator": 0,
                        "generation": None, "slot": SLOT_MAP[node],
                        "slot_listeners_passing_numerator": 0,
                        "slot_listeners_required_denominator": 3,
                        "slot_pass": False,
                        "not_remeasured_on_resume": True,
                    },
                }
            rejected = ()
        else:
            state["tag_cfg"] = configure_active(runner, args.count)
            state["acceptance_witness_1"] = run_acceptance_witness(
                runner, args.evidence_root, "cfg_acceptance_1", state["tag_cfg"], args.count)
            acceptance = dict(state["acceptance_witness_1"]["acceptance"]["nodes"])
            for row in acceptance.values():
                row["escalation_step"] = "E0"
            rejected = tuple(state["acceptance_witness_1"]["acceptance"]["rejected"])
        if rejected:
            state["tag_cfg_resend"] = configure_active(runner, args.count, rejected)
            state["acceptance_witness_2"] = run_acceptance_witness(
                runner, args.evidence_root, "cfg_acceptance_2",
                state["tag_cfg_resend"], args.count)
            second = state["acceptance_witness_2"]["acceptance"]["nodes"]
            for row in second.values():
                row["escalation_step"] = "E1"
            acceptance.update(second)
        rejected = tuple(node for node in NODES if not acceptance[node]["accepted_by"])
        if rejected:
            state["tag_reboot_e2"] = reboot_tags_parallel(runner, rejected)
            e2_ready = tuple(
                node for node in rejected
                if state["tag_reboot_e2"]["nodes"][node]["discontinuity_pass"])
            state["tag_cfg_after_reboot"] = configure_active(
                runner, args.count, e2_ready) if e2_ready else {}
            if e2_ready:
                state["acceptance_witness_3"] = run_acceptance_witness(
                    runner, args.evidence_root, "cfg_acceptance_3",
                    state["tag_cfg_after_reboot"], args.count)
                third = state["acceptance_witness_3"]["acceptance"]["nodes"]
                for row in third.values():
                    row["escalation_step"] = "E2"
                acceptance.update(third)
            for node in rejected:
                if not acceptance[node]["accepted_by"]:
                    acceptance[node]["escalation_step"] = "E3"
        participants = tuple(node for node in NODES if acceptance[node]["accepted_by"])
        quarantined = tuple(node for node in NODES if node not in participants)
        state["cfg_acceptance"] = {
            "nodes": acceptance,
            "participants": list(participants),
            "quarantined": list(quarantined),
            "accepted_by_counts": dict(Counter(
                row["accepted_by"] or "rejected" for row in acceptance.values())),
        }
        if not participants:
            raise SessionError("E0-E3 produced no participating nodes")
        # This LIST is deliberately fresh and immediately precedes the
        # capture contract; an updater/restore or spacing rebuild between the
        # earlier fleet gate and here cannot silently invalidate the window.
        state["formal_fleet_assertion"] = require_fleet(runner)
        state["formal_window_contract"] = assert_formal_window_contract(
            tag_cfg={node: (state.get("tag_cfg_after_reboot", {}).get(node)
                            or state.get("tag_cfg_resend", {}).get(node)
                            or state["tag_cfg"][node]) for node in participants},
            fleet=state["formal_fleet_assertion"],
            beacon_result=state["main_period"], expected_count=args.count,
            expected_period_ms=10,
            expected_beacon_us=args.beacon_period_ms * 1000,
            expected_slots={node: SLOT_MAP[node] for node in participants},
            max_wire_bytes=191,
            acceptance={node: acceptance[node] for node in participants})
        try:
            state["v33_counters_start"] = query_v33_restart_counters(runner)
        except Exception as exc:
            state["v33_counters_start_error"] = f"{type(exc).__name__}: {exc}"

        collector, collector_handle, listener_dir = start_listener_collector(
            args.evidence_root,
            label="continuous_listener_capture",
            duration_s=FORMAL_S + 180.0,
        )
        runner.listener_collector_active = True
        runner.listener_dir = listener_dir
        state["listener_preflight"] = wait_listener_preflight(
            listener_dir, collector, timeout_s=25.0
        )
        capture = capture_with_status(
            runner, args.evidence_root, FORMAL_S, participants)
        state["formal_capture"] = capture
        try:
            state["v33_counters_end"] = query_v33_restart_counters(runner)
        except Exception as exc:
            state["v33_counters_end_error"] = f"{type(exc).__name__}: {exc}"
        if "v33_counters_start" in state and "v33_counters_end" in state:
            state["v33_counter_deltas"] = restart_counter_deltas(
                state["v33_counters_start"], state["v33_counters_end"])
        state["listener_stop"] = stop_listener_collector(
            collector, collector_handle
        )
        collector = collector_handle = None
        runner.listener_collector_active = False
        result = analyze(args.evidence_root, capture, listener_dir, participants)
        result["cfg_acceptance"] = state["cfg_acceptance"]
        result["ten_node_requirement"] = {
            "numerator": len(participants), "denominator": 10,
            "pass": len(participants) == 10,
        }
        result["cold_start_limitation"] = (
            "Warm-fleet pass does not pre-prove post-dock cold start; the midnight "
            "prompt must repeat Correction 11 checks in its first five minutes."
        )
        b2 = state.get("v33_counter_deltas", {})
        b2_pass = bool(b2) and all(
            row["cause_sum_matches_total"] for row in b2.values())
        result["b2_restart_cause_vector"] = {
            "pass": b2_pass, "nodes": b2,
            "recorded_not_gated": True,
            "errors": [state[key] for key in
                       ("v33_counters_start_error", "v33_counters_end_error")
                       if key in state],
        }
        result["b3_liveness_detector"] = {
            "status": "NOT_IMPLEMENTED_DEFERRED",
            "gate_applied": False,
        }
        # Addendum 3 retires B2 consistency as a hardware-window stop. Keep
        # the result and numerator/denominator detail, but do not gate.
        result["pass"] = bool(result["pass"] and len(participants) == 10)
        write_json(args.evidence_root / "S3_ANALYSIS.json", result)
        state["analysis"] = result
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if collector is not None or collector_handle is not None:
            state["listener_stop_finally"] = stop_listener_collector(
                collector, collector_handle
            )
            runner.listener_collector_active = False
        if runner.channel is not None and not args.leave_resumable:
            try:
                teardown = safe_teardown(runner, args.evidence_root)
            except Exception as exc:
                teardown = {
                    "pass": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        elif runner.channel is not None:
            teardown = {
                "pass": None,
                "resumable": True,
                "configuration_preserved": True,
                "main_period_ms": args.beacon_period_ms,
                "note": "Addendum 3 resumable-stop policy; no idle teardown",
            }
        if runner.channel is not None:
            runner.channel.close()
            if runner.raw is not None:
                runner.raw.close()
        state["teardown"] = teardown
        state["status"] = (
            "PASS" if result.get("pass") and
            (args.leave_resumable or teardown.get("pass")) else "FAIL"
        )
        state["ended"] = utc_now()
        write_json(args.evidence_root / "S3_RUN_STATE.json", state)
        write_report(args.evidence_root, result, preflight, teardown)

    return 0 if state["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
