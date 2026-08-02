#!/usr/bin/env python3
"""Run the preregistered v31/v25 five-node Layer-2 qualification."""

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
from layer2_ledger import imu_missing_record_causes, ledger_between, u32_delta
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list
from validate_notify_fix_v30 import queue_diagnostics, queue_totals


EXPECTED_B306_MARKER = "b306-imu-relay-v31"
EXPECTED_DK_MARKER = "dk-fusion-imu-relay-v25"
EXPECTED_TAG_MARKER = "tag-fusion-link-v2-relay3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def wait_spacing(
    channel: LineChannel,
    mode: str,
    timeout_s: float = 90.0,
    bsfs: tuple[str, ...] = tuple(BSFS),
) -> dict[str, object]:
    channel.send(f"SPACING {mode}")
    lines: list[str] = []
    deadline = time.monotonic() + timeout_s
    applied: dict[str, str] | None = None
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is None:
            continue
        lines.append(line)
        if not line.startswith("FUSION_SPACING "):
            continue
        fields = parse_fields(line)
        if (
            fields.get("state") in ("APPLIED", "UNCHANGED")
            and fields.get("mode") == mode
            and fields.get("applied_us") == (
                "10000" if mode == "ON" else "7500"
            )
        ):
            applied = fields
            break
        if fields.get("state") == "FAILED":
            raise SessionError(f"spacing transition failed: {line}")
    if applied is None:
        raise SessionError(f"SPACING {mode} did not apply in {timeout_s}s")

    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    while time.monotonic() < deadline:
        collect(channel, assembler, min(1.0, deadline - time.monotonic()))
        listing = request_list(channel, assembler, counters, bsfs)
        aggregate = listing["aggregate"]
        if aggregate.get("count") == "5" and aggregate.get("ready") == "5":
            if aggregate.get("spacing") != mode:
                raise SessionError(
                    f"LIST spacing mismatch after {mode}: {aggregate}"
                )
            return {
                "applied": applied,
                "list": listing,
                "transition_lines": lines,
            }
    raise SessionError(f"five peers did not become ready after SPACING {mode}")


def request_master_status(channel: LineChannel) -> dict[str, str]:
    channel.send("MASTER STATUS")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is not None and line.startswith("FUSION_MASTER_STATUS "):
            return parse_fields(line)
    raise SessionError("MASTER STATUS produced no response")


def qos_summary(run: dict[str, object]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for node in BSFS:
        timeline = run["per_node"][node].get("qos_timeline", [])
        result[node] = {
            "records": len(timeline),
            "reports": sum(int(row.get("reports", "0"), 0) for row in timeline),
            "event_gaps": sum(
                int(row.get("event_gaps", "0"), 0) for row in timeline
            ),
            "crc_ok": sum(int(row.get("crc_ok", "0"), 0) for row in timeline),
            "crc_error": sum(
                int(row.get("crc_error", "0"), 0) for row in timeline
            ),
            "nak": sum(int(row.get("nak", "0"), 0) for row in timeline),
            "rx_timeout": sum(
                int(row.get("rx_timeout", "0"), 0) for row in timeline
            ),
            "spacing_states": sorted(
                {row.get("spacing") for row in timeline if row.get("spacing")}
            ),
            "spacing_values_us": sorted(
                {
                    int(row["spacing_us"], 0)
                    for row in timeline
                    if row.get("spacing_us")
                }
            ),
            "channels": [
                sum(
                    int(row.get("channels", "").split(",")[channel] or 0)
                    for row in timeline
                    if len(row.get("channels", "").split(",")) == 37
                )
                for channel in range(37)
            ],
        }
    return result


def run_ledger(run: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for node in BSFS:
        timeline = run["per_node"][node]["queue_counter_timeline"]
        if len(timeline) < 2:
            result[node] = {
                "balanced": False,
                "error": f"only {len(timeline)} queue snapshots",
            }
            continue
        before = timeline[0]
        after = timeline[-1]
        ledger = ledger_between(before, after)
        ledger["window"] = {
            "first_capture_monotonic": before["capture_monotonic"],
            "last_capture_monotonic": after["capture_monotonic"],
            "first_node_ms": before.get("node_ms"),
            "last_node_ms": after.get("node_ms"),
        }
        ledger["imu_missing_causes"] = imu_missing_record_causes(
            before, after
        )
        result[node] = ledger
    return result


def layer2_verdict(
    run: dict[str, object],
    diagnostics: dict[str, dict[str, object]],
    spacing: str,
    *,
    require_zero_qdrop: bool,
) -> dict[str, object]:
    ledger = run_ledger(run)
    qos = qos_summary(run)
    drops = {node: queue_totals(diagnostics[node]) for node in BSFS}
    anomaly = {
        node: run["per_node"][node]["hard_anomaly_deltas"] for node in BSFS
    }
    imu_p99 = {
        node: diagnostics[node]["enqueue"]["I"]["p99_upper_bound_us"]
        for node in BSFS
    }
    gaps = {
        node: int(run["per_node"][node]["imu_sequence_gaps"])
        for node in BSFS
    }
    missing_records = {
        node: run["per_node"][node]["imu_missing_records_batch5"]
        for node in BSFS
    }
    unexplained_gap_nodes = []
    for node in BSFS:
        causes = ledger[node].get("imu_missing_causes", {})
        counted = sum(int(value) for value in causes.values())
        if gaps[node] != 0 and counted == 0:
            unexplained_gap_nodes.append(node)

    common = {
        "missed_deadlines_zero": all(
            int(values.get("imu_missed_deadlines", 0)) == 0
            for values in anomaly.values()
        ),
        "orphans_zero": all(
            int(values.get("orphan_frame", 0)) == 0
            and int(values.get("orphan_strobe", 0)) == 0
            for values in anomaly.values()
        ),
        "ledger_residual_zero": all(
            bool(ledger[node].get("balanced")) for node in BSFS
        ),
        "producer_aborts_zero": all(
            int(row["producer_aborted"]) == 0
            for node in BSFS
            for row in ledger[node].get("classes", {}).values()
        ),
        "imu_epoch_defer_zero": all(
            int(ledger[node].get("imu_epoch_defer_drop", -1)) == 0
            for node in BSFS
        ),
        "no_unattributed_host_gap": not unexplained_gap_nodes,
        "imu_enqueue_p99_under_100us": all(
            value is not None and int(value) < 100
            for value in imu_p99.values()
        ),
        "zero_disconnects": bool(run["gates"]["zero_disconnects"]),
        "zero_malformed": bool(run["gates"]["zero_malformed"]),
        "zero_logger_drop": bool(run["gates"]["zero_logger_drop"]),
        "zero_cdc_drop": bool(run["gates"]["zero_cdc_drop"]),
        "zero_host_decoder_errors": bool(
            run["gates"].get("zero_host_decoder_errors", False)
        ),
        "qos_present_all_links": all(qos[node]["reports"] > 0 for node in BSFS),
        "spacing_proof": all(
            qos[node]["spacing_states"] == [spacing]
            and qos[node]["spacing_values_us"]
            == ([10000] if spacing == "ON" else [7500])
            for node in BSFS
        ),
    }
    if require_zero_qdrop:
        common["all_queue_drops_zero"] = all(
            all(value == 0 for value in per_class.values())
            for per_class in drops.values()
        )
        common["host_imu_sequence_gaps_zero"] = all(
            value == 0 for value in gaps.values()
        )
        common["host_imu_missing_records_zero"] = all(
            value == 0 for value in missing_records.values()
        )
        common["delivered_over_predicted_ge_99pct"] = (
            float(run["aggregate"]["delivered_fraction"]) >= 0.99
        )
    else:
        common["priority_allocation_ctl_uwb_lossless"] = all(
            values["ctl"] == 0 and values["uwb"] == 0
            for values in drops.values()
        )
    return {
        "pass": all(common.values()),
        "gates": common,
        "ledger": ledger,
        "qos": qos,
        "queue_drops": drops,
        "imu_sequence_gap_events": gaps,
        "imu_missing_records_batch5": missing_records,
        "unexplained_gap_nodes": unexplained_gap_nodes,
        "imu_enqueue_p99_upper_bound_us": imu_p99,
        "victim": max(
            BSFS,
            key=lambda node: sum(drops[node].values()),
        )
        if any(sum(drops[node].values()) for node in BSFS)
        else "none",
    }


def run_and_close(
    root: Path,
    channel: LineChannel,
    label: str,
    duration_s: float,
    generation: int,
    spacing: str,
    *,
    require_zero_qdrop: bool,
) -> dict[str, object]:
    run = run_one(
        root,
        channel,
        5,
        "C",
        duration_s,
        generation,
        None,
        label,
        imu_batch=5,
        imu_rate_hz=200,
        uwb_rate_hz=10.0,
        status_rate_hz=2.0,
    )
    diagnostics = {node: queue_diagnostics(channel, node) for node in BSFS}
    verdict = layer2_verdict(
        run,
        diagnostics,
        spacing,
        require_zero_qdrop=require_zero_qdrop,
    )
    end_state = cleanup(channel)
    result = {
        "run": run,
        "queue_diagnostics": diagnostics,
        "layer2_verdict": verdict,
        "end_state": end_state,
    }
    write_json(root / label / "layer2_result.json", result)
    write_json(root / label / "end_state.json", end_state)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--operator-token", required=True)
    parser.add_argument("--r0-duration-s", type=float, default=300.0)
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
        "preregistered_runs": {
            "R0": "spacing OFF, 300 s; q_drop measured, ctl/UWB must remain lossless",
            "R1c": "spacing ON, 600 s; all acceptance counters zero",
            "R2": "spacing ON, 1800 s; only after R1c passes",
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
        master_status = request_master_status(channel)
        if master_status.get("marker") != EXPECTED_DK_MARKER:
            raise SessionError(
                f"DK marker mismatch: {master_status.get('marker')}"
            )
        if (
            preflight["aggregate"].get("spacing") != "OFF"
            or preflight["aggregate"].get("spacing_us") != "7500"
        ):
            raise SessionError(
                f"R0 requires boot spacing OFF/7500: {preflight['aggregate']}"
            )
        summary["preflight"] = preflight
        summary["master_status"] = master_status
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
        summary["R0"] = run_and_close(
            args.output_dir,
            channel,
            "R0_spacing_off",
            args.r0_duration_s,
            generation,
            "OFF",
            require_zero_qdrop=False,
        )
        summary["spacing_on_transition"] = wait_spacing(channel, "ON")
        summary["R1c"] = run_and_close(
            args.output_dir,
            channel,
            "R1c_spacing_on",
            args.r1_duration_s,
            (generation + 1) & 0xFF,
            "ON",
            require_zero_qdrop=True,
        )
        if summary["R1c"]["layer2_verdict"]["pass"]:
            summary["R2"] = run_and_close(
                args.output_dir,
                channel,
                "R2_spacing_on",
                args.r2_duration_s,
                (generation + 2) & 0xFF,
                "ON",
                require_zero_qdrop=True,
            )
        else:
            summary["R2"] = {"status": "NOT_RUN_R1C_FAILED"}
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
