#!/usr/bin/env python3
"""Run the preregistered five-node v30 decoupling qualification."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import (
    BSFS,
    RecordingAssembler,
    b306_command,
    cleanup,
    collect,
    relay_command,
    run_one,
)
from fusion_session import LineChannel, SessionError, parse_fields, resolve_fusion_port
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list


EXPECTED_B306_MARKER = "b306-imu-relay-v30"
EXPECTED_TAG_MARKER = "tag-fusion-link-v2-relay3"
EXPECTED_DK_MARKER = "dk-fusion-imu-relay-v24"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def parse_hist(text: str) -> list[int]:
    fields = parse_fields(text)
    return [int(value, 0) for value in fields.get("h", "").split(",") if value]


def enqueue_p99_us(hist: list[int]) -> int | None:
    total = sum(hist)
    if total == 0:
        return None
    target = (total * 99 + 99) // 100
    cumulative = 0
    for index, count in enumerate(hist):
        cumulative += count
        if cumulative >= target:
            return None if index == 10 else (index + 1) * 10
    return None


def queue_diagnostics(channel: LineChannel, node: str) -> dict[str, object]:
    summary = b306_command(channel, node, "QUEUE", "QUEUE ")
    enqueue: dict[str, object] = {}
    for queue_name in ("I", "U", "C"):
        reply = b306_command(
            channel, node, f"QUEUE ENQ={queue_name}",
            f"QUEUE ENQ q={queue_name} ",
        )
        hist = parse_hist(str(reply["text"]))
        enqueue[queue_name] = {
            "reply": reply,
            "histogram_10us_bins_last_ge_100us": hist,
            "p99_upper_bound_us": enqueue_p99_us(hist),
        }
    publisher_pages = [
        b306_command(
            channel, node, f"QUEUE PUB HIST={page}",
            f"QUEUE PUB HIST p={page} ",
        )
        for page in range(4)
    ]
    publisher_hist = [
        value
        for page in publisher_pages
        for value in parse_hist(str(page["text"]))
    ]
    return {
        "summary": summary,
        "summary_fields": parse_fields(str(summary["text"])),
        "enqueue": enqueue,
        "publisher_pages": publisher_pages,
        "publisher_histogram": publisher_hist,
    }


def minute_timeline(run: dict[str, object], node: str) -> list[dict[str, object]]:
    timeline = run["per_node"][node]["queue_counter_timeline"]
    if not timeline:
        return []
    start = float(timeline[0]["capture_monotonic"])
    end = float(timeline[-1]["capture_monotonic"])
    rows: list[dict[str, object]] = []
    boundary = start
    while boundary <= end:
        eligible = [
            item for item in timeline
            if float(item["capture_monotonic"]) <= boundary
        ]
        if eligible:
            item = eligible[-1]
            rows.append({
                "elapsed_s": boundary - start,
                "node_ms": item.get("node_ms"),
                "q_drop_imu": item.get("q_drop_imu"),
                "q_drop_uwb": item.get("q_drop_uwb"),
                "q_drop_ctl": item.get("q_drop_ctl"),
                "q_hwm_imu": item.get("q_hwm_imu"),
                "q_hwm_uwb": item.get("q_hwm_uwb"),
                "q_hwm_ctl": item.get("q_hwm_ctl"),
                "publisher_max_us": item.get("publisher_max_us"),
            })
        boundary += 60.0
    return rows


def queue_totals(diagnostics: dict[str, object]) -> dict[str, int]:
    fields = diagnostics["summary_fields"]
    return {
        "imu": int(fields["di"], 0),
        "uwb": int(fields["du"], 0),
        "ctl": int(fields["dc"], 0),
    }


def missed_or_orphan(run: dict[str, object]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for node in BSFS:
        anomalies = run["per_node"][node]["hard_anomaly_deltas"]
        result[node] = {
            key: int(anomalies.get(key, 0))
            for key in (
                "imu_missed_deadlines",
                "orphan_frame",
                "orphan_strobe",
            )
        }
    return result


def strict_v30_verdict(
    run: dict[str, object],
    diagnostics: dict[str, dict[str, object]],
    *,
    queue_drops_gate: bool,
) -> dict[str, object]:
    anomaly = missed_or_orphan(run)
    qdrops = {node: queue_totals(diagnostics[node]) for node in BSFS}
    enqueue_p99 = {
        node: {
            queue: diagnostics[node]["enqueue"][queue]["p99_upper_bound_us"]
            for queue in ("I", "U", "C")
        }
        for node in BSFS
    }
    gates = {
        "base_runner_gates": bool(run["pass"]),
        "missed_deadlines_zero": all(
            values["imu_missed_deadlines"] == 0 for values in anomaly.values()
        ),
        "orphans_zero": all(
            values["orphan_frame"] == 0 and values["orphan_strobe"] == 0
            for values in anomaly.values()
        ),
        "delivered_over_predicted_ge_99pct":
            float(run["aggregate"]["delivered_fraction"]) >= 0.99,
        "enqueue_p99_under_100us": all(
            value is not None and value < 100
            for node in enqueue_p99.values() for value in node.values()
        ),
    }
    if queue_drops_gate:
        gates["all_queue_drops_zero"] = all(
            all(value == 0 for value in node.values())
            for node in qdrops.values()
        )
    return {
        "gates": gates,
        "pass": all(gates.values()),
        "anomalies": anomaly,
        "queue_drops": qdrops,
        "enqueue_p99_upper_bound_us": enqueue_p99,
    }


def run_and_close(
    root: Path,
    channel: LineChannel,
    label: str,
    duration_s: float,
    batch: int,
    generation: int,
    *,
    queue_drops_gate: bool,
) -> dict[str, object]:
    run = run_one(
        root, channel, 5, "C", duration_s, generation, None, label,
        imu_batch=batch, imu_rate_hz=200, uwb_rate_hz=10.0,
        status_rate_hz=2.0,
    )
    diagnostics = {node: queue_diagnostics(channel, node) for node in BSFS}
    verdict = strict_v30_verdict(
        run, diagnostics, queue_drops_gate=queue_drops_gate
    )
    timelines = {node: minute_timeline(run, node) for node in BSFS}
    end_state = cleanup(channel)
    result = {
        "run": run,
        "queue_diagnostics": diagnostics,
        "per_minute_queue_timeline": timelines,
        "v30_verdict": verdict,
        "end_state": end_state,
    }
    write_json(root / label / "v30_result.json", result)
    write_json(root / label / "end_state.json", end_state)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--operator-token", required=True)
    parser.add_argument("--r1-duration-s", type=float, default=600.0)
    parser.add_argument("--r2-duration-s", type=float, default=1800.0)
    args = parser.parse_args()
    if args.operator_token != "POWERED ON":
        raise SessionError("literal operator token POWERED ON is required")
    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "operator_gate": args.operator_token,
        "expected_b306_marker": EXPECTED_B306_MARKER,
        "expected_dk_marker": EXPECTED_DK_MARKER,
        "expected_tag_marker": EXPECTED_TAG_MARKER,
        "preregistered_rules": {
            "R1a": (
                "missed>0 falsifies decoupling and stops the suite; "
                "q_drop is measured, not gated"
            ),
            "R1b_R2": (
                "missed=0, all q_drop=0, orphans=0, delivered/predicted>=99%, "
                "zero disconnects/malformed/CDC drops"
            ),
        },
    }
    write_json(args.output_dir / "summary.json", summary)
    channel = None
    raw = None
    try:
        raw = (args.output_dir / "fusion_raw.log").open(
            "a", buffering=1, encoding="utf-8"
        )
        channel = LineChannel(
            resolve_fusion_port(args.fusion_port), raw, "FUSION"
        )
        channel.send("OUTPUT BINARY")
        assembler = RecordingAssembler()
        collect(channel, assembler, 2.0)
        preflight = request_list(channel, assembler, {}, BSFS)
        strict_link_gate(preflight)
        summary["preflight"] = preflight
        summary["versions"] = {}
        for node in BSFS:
            ping = b306_command(channel, node, "PING", "PONG ")
            if f"fw={EXPECTED_B306_MARKER} " not in f"{ping['text']} ":
                raise SessionError(f"{node} marker mismatch: {ping['text']}")
            version = relay_command(
                channel, node, "VERSION", "VERSION ", attempts=3
            )
            if (
                f"fw={EXPECTED_TAG_MARKER} "
                not in f"{version['reply']['text']} "
            ):
                raise SessionError(
                    f"{node} tag mismatch: {version['reply']['text']}"
                )
            summary["versions"][node] = {"b306": ping, "tag": version}

        generation = int(time.time()) & 0xFF
        r1a = run_and_close(
            args.output_dir, channel, "R1a_batch2", args.r1_duration_s,
            2, generation, queue_drops_gate=False,
        )
        summary["R1a"] = r1a
        if not r1a["v30_verdict"]["gates"]["missed_deadlines_zero"]:
            summary["status"] = "DECOUPLING_FALSIFIED"
            summary["completed_utc"] = utc_now()
            return 3

        qdrop_nodes = [
            node for node, drops in
            r1a["v30_verdict"]["queue_drops"].items()
            if any(value != 0 for value in drops.values())
        ]
        multi_ci_nodes = [
            node for node in BSFS
            if int(
                r1a["queue_diagnostics"][node]["summary_fields"]["pm"], 0
            ) >= 100_000
        ]
        if not qdrop_nodes:
            layer2 = "UNNECESSARY_AT_BATCH2_LOAD"
        elif len(qdrop_nodes) <= 2 and set(qdrop_nodes) <= set(multi_ci_nodes):
            layer2 = "JUSTIFIED_NEXT_BATCH"
        else:
            layer2 = "NOT_JUSTIFIED_BY_PREREGISTERED_PATTERN"
        summary["R1a_layer2_decision"] = {
            "decision": layer2,
            "qdrop_nodes": qdrop_nodes,
            "multi_ci_publisher_block_nodes": multi_ci_nodes,
        }

        r1b = run_and_close(
            args.output_dir, channel, "R1b_batch5", args.r1_duration_s,
            5, (generation + 1) & 0xFF, queue_drops_gate=True,
        )
        summary["R1b"] = r1b
        if r1b["v30_verdict"]["pass"]:
            summary["R2"] = run_and_close(
                args.output_dir, channel, "R2_batch5",
                args.r2_duration_s, 5, (generation + 2) & 0xFF,
                queue_drops_gate=True,
            )
        else:
            summary["R2"] = {"status": "NOT_RUN_R1B_FAILED"}
        summary["status"] = "COMPLETE"
        summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        if channel is not None:
            try:
                summary["exception_cleanup"] = cleanup(channel)
            except Exception as cleanup_exc:
                summary["cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        if raw is not None:
            raw.close()
        write_json(args.output_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
