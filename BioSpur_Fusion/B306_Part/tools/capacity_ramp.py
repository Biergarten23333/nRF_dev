#!/usr/bin/env python3
"""Five-node Fusion traffic capacity ramp with fixed A/B/C arms."""

from __future__ import annotations

import argparse
import fcntl
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

from fusion_session import (
    ANOMALY_COUNTERS,
    FusionController,
    LineChannel,
    SessionError,
    imu_sequence_gaps,
    parse_fields,
    parse_reply,
    resolve_fusion_port,
    u32_delta,
)
from phase_c_long_validation import distribution
from phase_e_vc1_validation import linear_latency
from pre_ramp_hardening import (
    TelemetryAssembler,
    request_list,
    request_resources,
)


NODES = (
    ("BSF3C79", "BS065F"),
    ("BSFC2CC", "BSE88E"),
    ("BSF44AD", "BS6F3A"),
    ("BSF6C53", "BSF8E0"),
    ("BSF8BC4", "BSEFD2"),
)
BSFS = tuple(item[0] for item in NODES)
BS_BY_BSF = dict(NODES)
HARD_ANOMALIES = tuple(
    field for field in ANOMALY_COUNTERS if field != "imu_hreset"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (
        ordered[upper] - ordered[lower]
    ) * (position - lower)


class RecordingAssembler(TelemetryAssembler):
    def __init__(self) -> None:
        super().__init__()
        self.history: dict[str, list[dict[str, str]]] = {}

    def observe(self, line: str) -> None:
        previous = self.completed_records
        super().observe(line)
        if self.completed_records == previous:
            return
        fields = parse_fields(line)
        name = fields.get("name")
        if name in self.latest:
            self.history.setdefault(name, []).append(dict(self.latest[name]))


def collect(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    duration_s: float,
    *,
    retain: bool = False,
) -> list[tuple[float, str]]:
    rows: list[tuple[float, str]] = []
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is None:
            continue
        stamp = time.monotonic()
        assembler.observe(line)
        if retain:
            rows.append((stamp, line))
    return rows


def wait_all_telemetry(
    channel: LineChannel,
    assembler: TelemetryAssembler,
    timeout_s: float = 8.0,
) -> dict[str, dict[str, str]]:
    previous = {
        name: assembler.latest.get(name, {}).get("node_ms") for name in BSFS
    }
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        collect(
            channel,
            assembler,
            min(0.25, deadline - time.monotonic()),
        )
        if all(
            name in assembler.latest
            and assembler.latest[name].get("node_ms") != previous[name]
            for name in BSFS
        ):
            return {name: dict(assembler.latest[name]) for name in BSFS}
    missing = [
        name
        for name in BSFS
        if name not in assembler.latest
        or assembler.latest[name].get("node_ms") == previous[name]
    ]
    raise SessionError(f"fresh telemetry missing for {missing}")


def b306_controller(channel: LineChannel, bsf: str) -> FusionController:
    return FusionController(channel, bsf, timeout_s=8.0, max_attempts=3)


def b306_command(
    channel: LineChannel,
    bsf: str,
    command: str,
    prefix: str,
) -> dict[str, object]:
    reply = b306_controller(channel, bsf).command(
        command,
        lambda text: text.startswith(prefix),
        allow_resend_after_tx=False,
    )
    return reply.__dict__


def relay_command(
    channel: LineChannel,
    bsf: str,
    text: str,
    expected_prefix: str,
    attempts: int = 5,
) -> dict[str, object]:
    errors: list[str] = []
    for attempt in range(1, attempts + 1):
        controller = b306_controller(channel, bsf)
        queued = controller.command(
            f"TAG RAW {text}",
            lambda reply: reply.startswith("RELAY_QUEUED"),
            source="B306",
            allow_resend_after_tx=False,
        )
        try:
            reply_line = controller.read_until(
                lambda line: (
                    (reply := parse_reply(line)) is not None
                    and reply.source == "TAG"
                    and reply.correlation == queued.correlation
                ),
                2.6,
                f"{text} TAG reply correlation={queued.correlation}",
            )
        except SessionError as exc:
            errors.append(str(exc))
            continue
        reply = parse_reply(reply_line)
        assert reply is not None
        if reply.text.startswith(expected_prefix):
            return {
                "attempt": attempt,
                "queued": queued.__dict__,
                "reply": reply.__dict__,
            }
        errors.append(reply.text)
    raise SessionError(
        f"{bsf} TAG RAW {text} failed after {attempts} attempts: {errors}"
    )


def direct_tdma_config(
    fusion: LineChannel,
    participants: tuple[str, ...],
    generation: int,
) -> dict[str, object]:
    deadline = time.monotonic() + 20.0
    superframe_base = int(deadline * 10.0) & 0xFFFFFFFF
    results: dict[str, object] = {}
    for slot, bsf in enumerate(participants):
        bs = BS_BY_BSF[bsf]
        remaining_ms = max(1000, int((deadline - time.monotonic()) * 1000.0))
        command = (
            f"CFG TAG={slot + 1} SLOT={slot} COUNT=10 "
            f"MASK=0x{1 << slot:04X} PERIOD=10 ACTIVE=9 "
            f"EPOCH={remaining_ms} SUPERFRAME_BASE={superframe_base} "
            f"GEN={generation & 0xFF} RUN=1 PMODE=0"
        )
        relayed = relay_command(
            fusion, bsf, command, "CFG_OK ", attempts=3
        )
        ack = relayed["reply"]["text"]
        if (
            f"SLOT={slot}/10" not in ack
            or f"MASK=0x{1 << slot:04X}" not in ack
            or "LIVE=1" not in ack
            or "RUN=1" not in ack
        ):
            raise SessionError(
                f"direct TDMA CFG failed for {bsf}/{bs}: "
                f"reply={ack}"
            )
        results[bsf] = {
            "bs": bs,
            "slot": slot,
            "mask": f"0x{1 << slot:04X}",
            "command": command,
            "relay": relayed,
        }
    remaining = deadline - time.monotonic()
    if remaining > 0:
        collect(fusion, TelemetryAssembler(), remaining + 0.25)
    return {
        "generation": generation & 0xFF,
        "deadline_monotonic": deadline,
        "superframe_base": superframe_base,
        "superframe_readback": (
            "not available in relay3 TDMA_STATUS; "
            "relay3 CFG_STATUS exceeds the 191-byte UART relay payload"
        ),
        "nodes": results,
    }


def ensure_imu_stopped(
    fusion: LineChannel,
    bsf: str,
) -> dict[str, object]:
    status: dict[str, object] | None = None
    try:
        status = b306_command(fusion, bsf, "IMU STATUS", "IMU ")
    except SessionError as exc:
        status_error = str(exc)
    else:
        status_error = None
        if "active=0 " in f"{status['text']} ":
            return {"status": status, "stop": None, "already_stopped": True}

    # STOP is intentionally idempotent.  Under batch-2 saturation the first
    # STOP may execute while its control reply is evicted.  Retrying is both
    # safe and necessary to collapse producer load before the read-only
    # confirmation can be expected to get through.
    controller = b306_controller(fusion, bsf)
    stop_error: str | None = None
    try:
        stopped = controller.command(
            "IMU STOP",
            lambda text: text.startswith("IMU STOP OK "),
            allow_resend_after_tx=True,
        ).__dict__
    except SessionError as exc:
        stopped = None
        stop_error = str(exc)

    confirmed = b306_command(fusion, bsf, "IMU STATUS", "IMU ")
    if "active=0 " not in f"{confirmed['text']} ":
        raise SessionError(
            f"{bsf} IMU STOP did not reach inactive state: "
            f"{confirmed['text']}"
        )
    return {
        "status": status,
        "status_error": status_error,
        "stop": stopped,
        "stop_reply_missing": stop_error,
        "already_stopped": False,
        "confirmed": confirmed,
    }


def setup_arm(
    fusion: LineChannel,
    n: int,
    arm: str,
    generation: int,
    imu_batch: int = 5,
    imu_rate_hz: int = 200,
    uwb_rate_hz: float = 10.0,
    status_rate_hz: float = 2.0,
) -> dict[str, object]:
    if uwb_rate_hz != 10.0:
        raise SessionError("direct relay3 TDMA setup currently configures 10 Hz")
    if status_rate_hz != 2.0:
        raise SessionError(
            "v30 periodic status is 1 Hz telemetry + 1 Hz queue counters"
        )
    participants = BSFS[:n]
    result: dict[str, object] = {
        "participants": participants,
        "slots": {},
        "tag_stop": {},
        "imu_stop": {},
        "imu_start": {},
        "counter_clear": {},
        "configured_rates": {
            "imu_rate_hz": imu_rate_hz,
            "imu_batch": imu_batch,
            "uwb_rate_hz": uwb_rate_hz,
            "status_rate_hz": status_rate_hz,
        },
    }

    for bsf in BSFS:
        result["tag_stop"][bsf] = relay_command(
            fusion, bsf, "MODE IDLE", "MODE_OK MODE=IDLE"
        )
    for bsf in BSFS:
        result["imu_stop"][bsf] = ensure_imu_stopped(fusion, bsf)

    if arm in ("A", "C"):
        result["slots"] = direct_tdma_config(
            fusion, participants, generation
        )
        for bsf in participants:
            status = relay_command(
                fusion, bsf, "TDMA_STATUS", "TDMA_SLOT="
            )
            text = status["reply"]["text"]
            slot = participants.index(bsf)
            if (
                f"TDMA_SLOT={slot}/10" not in text
                or f"MASK=0x{1 << slot:04X}" not in text
                or "SOURCE=MASTER" not in text
                or "PERIOD=10" not in text
                or f"GEN={generation & 0xFF}" not in text
            ):
                raise SessionError(f"{bsf} TDMA_STATUS mismatch: {text}")
            result.setdefault("tdma_status", {})[bsf] = status

    if arm in ("B", "C"):
        for bsf in participants:
            b306_command(
                fusion, bsf, f"IMU RATE={imu_rate_hz}", "IMU RATE OK "
            )
            b306_command(
                fusion,
                bsf,
                f"IMU BATCH={imu_batch}",
                "IMU BATCH OK ",
            )

    for bsf in BSFS:
        result["counter_clear"][bsf] = b306_command(
            fusion, bsf, "COUNTERS CLEAR", "COUNTERS CLEARED"
        )
    return result


def start_arm_imus(
    fusion: LineChannel,
    participants: tuple[str, ...],
) -> dict[str, object]:
    result: dict[str, object] = {}
    for bsf in participants:
        start = b306_controller(fusion, bsf).command(
            "IMU START",
            lambda text: (
                text.startswith("IMU START OK ")
                and "61=0001:P" in text
                and "03=000B:P" in text
                and "1F=0002:P" in text
                and "volatile=1" in text
                and "saved=0" in text
            ),
            allow_resend_after_tx=False,
        )
        result[bsf] = start.__dict__
    return result


def delta_fields(
    before: dict[str, str],
    after: dict[str, str],
    fields: tuple[str, ...],
) -> dict[str, int]:
    return {
        field: u32_delta(int(before[field], 0), int(after[field], 0))
        for field in fields
        if field in before and field in after
    }


def analyze_run(
    rows: list[tuple[float, str]],
    assembler: RecordingAssembler,
    baseline: dict[str, dict[str, str]],
    final: dict[str, dict[str, str]],
    duration_s: float,
    n: int,
    arm: str,
    start_list: dict,
    end_list: dict,
    start_resources: dict,
    end_resources: dict,
    predictions: dict,
) -> dict[str, object]:
    participants = BSFS[:n]
    per_node: dict[str, object] = {}
    telemetry_records = 0
    queue_counter_records = 0
    for bsf in BSFS:
        node_rows = [
            (stamp, line)
            for stamp, line in rows
            if parse_fields(line).get("name") == bsf
        ]
        uwb = [
            (stamp, line)
            for stamp, line in node_rows
            if line.startswith("FUSION_UWB ")
        ]
        imu = [
            (stamp, line)
            for stamp, line in node_rows
            if line.startswith("FUSION_IMU ")
        ]
        telemetry_rows = [
            line
            for _, line in node_rows
            if line.startswith("FUSION_TELEMETRY ")
        ]
        # Binary host records are emitted exactly once and do not carry the
        # legacy text-mode ``record=`` identifier.  Requiring that field made
        # every binary telemetry record disappear from the delivered-rate
        # denominator (exactly one notification/s/node).
        telemetry_records += len(telemetry_rows)
        queue_records = [
            (stamp, line)
            for stamp, line in node_rows
            if line.startswith("FUSION_QUEUE ")
        ]
        qos_records = [
            (stamp, line)
            for stamp, line in node_rows
            if line.startswith("FUSION_QOS ")
        ]
        queue_counter_records += len(queue_records)
        gaps, imu_records = imu_sequence_gaps(line for _, line in imu)
        missing_samples = 0
        nonforward_sequence_jumps = 0
        previous_seq: int | None = None
        previous_n = 0
        for _, line in imu:
            fields = parse_fields(line)
            if "seq" not in fields or "n" not in fields:
                continue
            sequence = int(fields["seq"], 0)
            sample_count = int(fields["n"], 0)
            if previous_seq is not None:
                expected_sequence = (previous_seq + previous_n) & 0xFFFF
                sequence_delta = (sequence - expected_sequence) & 0xFFFF
                if sequence_delta >= 0x8000:
                    nonforward_sequence_jumps += 1
                else:
                    missing_samples += sequence_delta
            previous_seq = sequence
            previous_n = sample_count
        imu_samples = sum(
            int(parse_fields(line).get("n", "0"), 0) for _, line in imu
        )
        pair = [
            int(parse_fields(line)["pair_dt_us"], 0)
            for _, line in uwb
            if parse_fields(line).get("pair_dt_us") not in (None, "-")
        ]
        healthy = sum(
            parse_fields(line).get("verdict") == "healthy" for _, line in uwb
        )
        points: list[tuple[int, float]] = []
        for stamp, line in uwb:
            fields = parse_fields(line)
            if "frame_us" in fields:
                points.append((int(fields["frame_us"], 0), stamp))
        for stamp, line in imu:
            fields = parse_fields(line)
            if "base_us" not in fields:
                continue
            deltas = [
                int(encoded.split(",", 1)[0], 0)
                for encoded in fields.get("samples", "").split(";")
                if encoded
            ]
            if deltas:
                points.append((int(fields["base_us"], 0) + max(deltas), stamp))
        anomalies = delta_fields(
            baseline[bsf], final[bsf], HARD_ANOMALIES
        )
        class2_delta = u32_delta(
            int(baseline[bsf].get("imu_hreset", "0"), 0),
            int(final[bsf].get("imu_hreset", "0"), 0),
        )
        event_stamps = []
        previous_reset = int(baseline[bsf].get("imu_hreset", "0"), 0)
        baseline_node_ms = int(baseline[bsf].get("node_ms", "0"), 0)
        final_node_ms = int(final[bsf].get("node_ms", "0"), 0)
        for snapshot in assembler.history.get(bsf, []):
            snapshot_node_ms = int(snapshot.get("node_ms", "0"), 0)
            if not baseline_node_ms <= snapshot_node_ms <= final_node_ms:
                continue
            current_reset = int(snapshot.get("imu_hreset", "0"), 0)
            if u32_delta(previous_reset, current_reset) != 0:
                hwin = snapshot.get("imu_hwin", "")
                event_stamps.append(
                    {
                        "node_ms": snapshot.get("node_ms"),
                        "imu_hwin": hwin,
                        "fault_ts_us": (
                            hwin.split("/")[1]
                            if len(hwin.split("/")) == 3
                            else None
                        ),
                    }
                )
            previous_reset = current_reset
        per_node[bsf] = {
            "participating": bsf in participants,
            "uwb_records": len(uwb),
            "uwb_healthy": healthy,
            "uwb_healthy_ratio": healthy / len(uwb) if uwb else None,
            "imu_records": imu_records,
            "imu_samples": imu_samples,
            "imu_effective_rate_hz": imu_samples / duration_s,
            "imu_sequence_gaps": gaps,
            "imu_missing_samples": missing_samples,
            "imu_missing_records_batch5": (
                missing_samples // 5
                if missing_samples % 5 == 0
                else None
            ),
            "imu_nonforward_sequence_jumps":
                nonforward_sequence_jumps,
            "queue_counter_records": len(queue_records),
            "queue_counter_timeline": [
                {
                    "capture_monotonic": stamp,
                    **parse_fields(line),
                }
                for stamp, line in queue_records
            ],
            "qos_records": len(qos_records),
            "qos_timeline": [
                {
                    "capture_monotonic": stamp,
                    **parse_fields(line),
                }
                for stamp, line in qos_records
            ],
            "strobe_to_frame": distribution(pair),
            "latency": linear_latency(points),
            "hard_anomaly_deltas": anomalies,
            "class2_event_delta": class2_delta,
            "class2_event_timestamps": event_stamps,
            "master_rx_delta": u32_delta(
                int(baseline[bsf].get("master_rx", "0"), 0),
                int(final[bsf].get("master_rx", "0"), 0),
            ),
            "notify_ok_delta": u32_delta(
                int(baseline[bsf].get("notify_ok", "0"), 0),
                int(final[bsf].get("notify_ok", "0"), 0),
            ),
            "logger_drop_delta": u32_delta(
                int(baseline[bsf].get("logger_drop", "0"), 0),
                int(final[bsf].get("logger_drop", "0"), 0),
            ),
        }

    uwb_total = sum(row["uwb_records"] for row in per_node.values())
    imu_total = sum(row["imu_records"] for row in per_node.values())
    delivered = (
        uwb_total + imu_total + telemetry_records + queue_counter_records
    )
    expected = predictions["expected_notifications"]
    disconnects = [
        line for _, line in rows if line.startswith("FUSION_DISCONNECTED ")
    ]
    malformed = [
        line for _, line in rows if line.startswith("FUSION_MALFORMED ")
    ]
    cdc_delta = {
        field: int(end_resources["summary"][field], 0)
        - int(start_resources["summary"][field], 0)
        for field in ("cdc_drop_bytes", "cdc_drop_records")
    }
    logger_clean = all(
        row["logger_drop_delta"] == 0 for row in per_node.values()
    )
    hard_clean = all(
        all(value == 0 for value in row["hard_anomaly_deltas"].values())
        for row in per_node.values()
    )
    imu_gaps_clean = all(
        row["imu_sequence_gaps"] == 0 for row in per_node.values()
    )
    rate_fraction = delivered / expected if expected else 0.0
    participant_latencies = {
        name: row["latency"]
        for name, row in per_node.items()
        if name in participants
    }
    participant_lower = [
        latency.get("lower_envelope_normalized_latency_us", {})
        for latency in participant_latencies.values()
        if "lower_envelope_normalized_latency_us" in latency
    ]
    p95_values = [
        float(lower["p95"])
        for lower in participant_lower
        if lower.get("p95") is not None
    ]
    max_values = [
        float(lower["maximum"])
        for lower in participant_lower
        if lower.get("maximum") is not None
    ]
    p95_us = max(p95_values) if p95_values else None
    max_us = max(max_values) if max_values else None
    latency = {
        "definition": "worst per-node lower-envelope fit; clocks are never pooled",
        "per_node": participant_latencies,
        "lower_envelope_normalized_latency_us": {
            "p95": p95_us,
            "maximum": max_us,
        },
    }
    parameters_stable = all(
        start_list["peers"].get(name, {}).get(field)
        == end_list["peers"].get(name, {}).get(field)
        for name in BSFS
        for field in (
            "interval_units",
            "latency",
            "timeout_units",
            "phy_tx",
            "phy_rx",
        )
    )
    expected_types = {
        "A": (True, False),
        "B": (False, True),
        "C": (True, True),
    }
    expect_uwb, expect_imu = expected_types[arm]
    activity_ok = all(
        (
            (row["uwb_records"] >= duration_s * 8.0)
            if name in participants and expect_uwb
            else row["uwb_records"] <= 2
        )
        and (
            (row["imu_samples"] >= duration_s * 200.0 * 0.98)
            if name in participants and expect_imu
            else row["imu_records"] == 0
        )
        for name, row in per_node.items()
    )
    gates = {
        "five_links_start": (
            start_list["aggregate"].get("count") == "5"
            and start_list["aggregate"].get("ready") == "5"
        ),
        "five_links_end": (
            end_list["aggregate"].get("count") == "5"
            and end_list["aggregate"].get("ready") == "5"
        ),
        "zero_disconnects": not disconnects,
        "zero_malformed": not malformed,
        "zero_logger_drop": logger_clean,
        "zero_cdc_drop": all(value == 0 for value in cdc_delta.values()),
        "zero_hard_device_anomalies": hard_clean,
        "zero_imu_sequence_gaps": imu_gaps_clean,
        "delivered_rate": 0.95 <= rate_fraction <= 1.05,
        "activity_matches_arm": activity_ok,
        "connection_parameters_stable": parameters_stable,
        "latency_p95": (
            p95_us is not None
            and p95_us <= predictions["pass_latency_p95_us"]
        ),
        "latency_max": (
            max_us is not None
            and max_us <= predictions["pass_latency_max_us"]
        ),
    }
    return {
        "duration_s": duration_s,
        "participants": participants,
        "per_node": per_node,
        "aggregate": {
            "uwb_records": uwb_total,
            "imu_records": imu_total,
            "telemetry_records": telemetry_records,
            "queue_counter_records": queue_counter_records,
            "delivered_notifications": delivered,
            "expected_notifications": expected,
            "delivered_notifications_s": delivered / duration_s,
            "expected_notifications_s": expected / duration_s,
            "delivered_fraction": rate_fraction,
            "latency": latency,
            "disconnect_lines": disconnects,
            "malformed_lines": malformed,
            "cdc_drop_delta": cdc_delta,
        },
        "start_list": start_list,
        "end_list": end_list,
        "start_resources": start_resources,
        "end_resources": end_resources,
        "gates": gates,
        "pass": all(gates.values()),
    }


def predictions_for(
    n: int,
    arm: str,
    duration_s: float,
    baseline_latency: dict[str, float] | None,
    imu_batch: int,
    imu_rate_hz: float,
    uwb_rate_hz: float,
    status_rate_hz: float,
    connected_status_nodes: int,
) -> dict[str, object]:
    imu_notifications_s = imu_rate_hz / imu_batch
    status_notifications_s = connected_status_nodes * status_rate_hz
    rates = {
        "A": n * uwb_rate_hz + status_notifications_s,
        "B": n * imu_notifications_s + status_notifications_s,
        "C": n * (uwb_rate_hz + imu_notifications_s) +
             status_notifications_s,
    }
    if baseline_latency is None:
        base_p95 = 52000.0
        base_max = 188600.0
    else:
        base_p95 = baseline_latency["p95_us"]
        base_max = baseline_latency["max_us"]
    return {
        "written_before_run": True,
        "n": n,
        "arm": arm,
        "participants": BSFS[:n],
        "imu_batch": imu_batch,
        "configured_imu_rate_hz": imu_rate_hz,
        "configured_uwb_rate_hz": uwb_rate_hz,
        "configured_status_rate_hz": status_rate_hz,
        "connected_status_nodes": connected_status_nodes,
        "five_connected_telemetry_floor_notifications_s":
            status_notifications_s,
        "predicted_notifications_s": rates[arm],
        "expected_notifications": rates[arm] * duration_s,
        "predicted_sequence_gaps": 0,
        "predicted_disconnects": 0,
        "predicted_malformed": 0,
        "predicted_logger_drop": 0,
        "predicted_latency_p95_us": base_p95 + (n - 1) * 5000.0,
        "predicted_latency_max_us": base_max + (n - 1) * 10000.0,
        "pass_delivered_fraction": [0.95, 1.05],
        "pass_latency_p95_us": max(100000.0, base_p95 + 50000.0),
        "pass_latency_max_us": max(400000.0, base_max + 200000.0),
        "class2_events": "reported separately; never fail a run",
    }


def run_one(
    root: Path,
    fusion: LineChannel,
    n: int,
    arm: str,
    duration_s: float,
    generation: int,
    baseline_latency: dict[str, float] | None,
    label: str,
    imu_batch: int = 5,
    imu_rate_hz: int = 200,
    uwb_rate_hz: float = 10.0,
    status_rate_hz: float = 2.0,
) -> dict[str, object]:
    run_dir = root / label
    run_dir.mkdir(parents=True, exist_ok=False)
    prediction = predictions_for(
        n,
        arm,
        duration_s,
        baseline_latency,
        imu_batch,
        imu_rate_hz,
        uwb_rate_hz,
        status_rate_hz,
        len(BSFS),
    )
    write_json(run_dir / "predictions.json", prediction)
    print(
        f"RUN_START label={label} N={n} arm={arm} "
        f"expected={prediction['predicted_notifications_s']}notif/s",
        flush=True,
    )
    setup = setup_arm(
        fusion,
        n,
        arm,
        generation,
        imu_batch=imu_batch,
        imu_rate_hz=imu_rate_hz,
        uwb_rate_hz=uwb_rate_hz,
        status_rate_hz=status_rate_hz,
    )
    write_json(run_dir / "setup.json", setup)

    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    collect(fusion, assembler, 2.0)
    start_list = request_list(fusion, assembler, counters, BSFS)
    start_resources = request_resources(fusion, assembler, counters)
    # Capture the real Fusion-Master per-peer logger baseline while all IMUs
    # are still stopped. COUNTERS CLEAR only resets B306-local counters.
    baseline = wait_all_telemetry(fusion, assembler)
    if arm in ("B", "C"):
        setup["imu_start"] = start_arm_imus(fusion, BSFS[:n])
        write_json(run_dir / "setup.json", setup)

    started_utc = utc_now()
    decoder_errors_before = fusion.binary_decoder.errors
    start = time.monotonic()
    rows = collect(fusion, assembler, duration_s, retain=True)
    end = time.monotonic()
    write_json(
        run_dir / "window_checkpoint.json",
        {
            "status": "DATA_WINDOW_COMPLETE",
            "started_utc": started_utc,
            "started_monotonic": start,
            "ended_monotonic": end,
            "actual_duration_s": end - start,
            "retained_rows": len(rows),
        },
    )
    post_window_stop: dict[str, object] = {}
    if arm in ("B", "C"):
        for bsf in BSFS[:n]:
            post_window_stop[bsf] = ensure_imu_stopped(fusion, bsf)
    final = wait_all_telemetry(fusion, assembler, timeout_s=12.0)
    end_list = request_list(fusion, assembler, counters, BSFS)
    end_resources = request_resources(fusion, assembler, counters)
    analysis = analyze_run(
        rows,
        assembler,
        baseline,
        final,
        end - start,
        n,
        arm,
        start_list,
        end_list,
        start_resources,
        end_resources,
        prediction,
    )
    analysis["started_monotonic"] = start
    analysis["ended_monotonic"] = end
    analysis["started_utc"] = started_utc
    analysis["post_window_stop"] = post_window_stop
    analysis["host_decoder_errors_delta"] = (
        fusion.binary_decoder.errors - decoder_errors_before
    )
    analysis["gates"]["zero_host_decoder_errors"] = (
        analysis["host_decoder_errors_delta"] == 0
    )
    analysis["pass"] = all(analysis["gates"].values())

    delta_pages: dict[str, object] = {}
    if arm in ("B", "C"):
        for bsf in BSFS[:n]:
            delta_pages[bsf] = {
                str(page): b306_command(
                    fusion,
                    bsf,
                    f"IMU DELTA={page}",
                    f"IMU DELTA p={page} ",
                )
                for page in range(3)
            }
    analysis["chip_delta_pages"] = delta_pages
    write_json(run_dir / "analysis.json", analysis)
    print(
        f"RUN_END label={label} verdict="
        f"{'PASS' if analysis['pass'] else 'FAILED'} "
        f"delivered={analysis['aggregate']['delivered_notifications_s']:.3f}/s "
        f"p95_us={analysis['aggregate']['latency'].get('lower_envelope_normalized_latency_us', {}).get('p95')} "
        f"max_us={analysis['aggregate']['latency'].get('lower_envelope_normalized_latency_us', {}).get('maximum')}",
        flush=True,
    )
    return analysis


def cleanup(fusion: LineChannel) -> dict[str, object]:
    result: dict[str, object] = {"imu": {}, "tag": {}}
    for bsf in BSFS:
        try:
            result["imu"][bsf] = ensure_imu_stopped(fusion, bsf)
        except Exception as exc:
            result["imu"][bsf] = {"error": str(exc)}
    for bsf in BSFS:
        try:
            result["tag"][bsf] = relay_command(
                fusion, bsf, "MODE IDLE", "MODE_OK MODE=IDLE"
            )
        except Exception as exc:
            result["tag"][bsf] = {"error": str(exc)}
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=300.0)
    parser.add_argument("--long-duration-s", type=float, default=1800.0)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "nodes": NODES,
        "nested_order": BSFS,
        "five_connected_floor_notifications_s": 5,
        "decisions": [
            "no application-level fair queueing",
            "IMU batch defaults to N=5; explicit test arms may override it",
            "one Fusion Master only",
            "CFG_STOP never used",
        ],
    }
    write_json(args.output_dir / "summary.json", summary)
    lock_path = args.output_dir.parent / ".capacity_ramp.lock"
    lock = lock_path.open("a+")
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    fusion = None
    try:
        fusion_port = resolve_fusion_port(args.fusion_port)
        summary["fusion_port"] = fusion_port
        summary["master_tag"] = (
            "not used by the traffic ramp; TDMA CFG is targeted through "
            "Fusion Master -> B306 -> tag UART"
        )
        with (args.output_dir / "fusion_raw.log").open(
            "a", buffering=1, encoding="utf-8"
        ) as fusion_raw:
            fusion = LineChannel(fusion_port, fusion_raw, "FUSION")

            pre_assembler = TelemetryAssembler()
            pre_counters: dict[str, int] = {}
            collect(fusion, pre_assembler, 2.0)
            pre_list = request_list(
                fusion, pre_assembler, pre_counters, BSFS
            )
            if (
                pre_list["aggregate"].get("count") != "5"
                or pre_list["aggregate"].get("ready") != "5"
            ):
                raise SessionError(f"Fusion five-link preflight failed: {pre_list}")
            summary["fusion_preflight"] = pre_list

            results: dict[str, object] = {}
            baseline_latency = None
            generation = 40
            for n in range(1, 6):
                for arm in ("A", "B", "C"):
                    label = f"N{n}_{arm}"
                    result = run_one(
                        args.output_dir,
                        fusion,
                        n,
                        arm,
                        args.duration_s,
                        generation,
                        baseline_latency,
                        label,
                    )
                    generation += 1
                    results[label] = result
                    if n == 1 and arm == "C":
                        lower = result["aggregate"]["latency"][
                            "lower_envelope_normalized_latency_us"
                        ]
                        baseline_latency = {
                            "p95_us": float(lower["p95"]),
                            "max_us": float(lower["maximum"]),
                        }
                    summary["runs"] = {
                        name: {
                            "pass": row["pass"],
                            "delivered_notifications_s": row["aggregate"][
                                "delivered_notifications_s"
                            ],
                            "latency": row["aggregate"]["latency"],
                            "participants": row["participants"],
                        }
                        for name, row in results.items()
                    }
                    write_json(args.output_dir / "summary.json", summary)

            passing_n = [
                n
                for n in range(1, 6)
                if all(results[f"N{n}_{arm}"]["pass"] for arm in ("A", "B", "C"))
            ]
            largest = max(passing_n) if passing_n else None
            summary["largest_n_passing_all_arms"] = largest
            if largest is not None:
                long_label = f"N{largest}_C_long"
                long_result = run_one(
                    args.output_dir,
                    fusion,
                    largest,
                    "C",
                    args.long_duration_s,
                    generation,
                    baseline_latency,
                    long_label,
                )
                results[long_label] = long_result
                summary["long_run"] = {
                    "label": long_label,
                    "pass": long_result["pass"],
                    "delivered_notifications_s": long_result["aggregate"][
                        "delivered_notifications_s"
                    ],
                    "latency": long_result["aggregate"]["latency"],
                }
            summary["cleanup"] = cleanup(fusion)
            summary["status"] = "COMPLETE"
            summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        if fusion is not None:
            try:
                with (args.output_dir / "emergency_cleanup.log").open(
                    "a", buffering=1, encoding="utf-8"
                ) as emergency_log:
                    fusion.log_file = emergency_log
                    summary["cleanup"] = cleanup(fusion)
            except Exception as cleanup_exc:
                summary["cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        if fusion is not None:
            fusion.close()
        lock.close()
        write_json(args.output_dir / "summary.json", summary)

    print(
        f"CAPACITY_RAMP_COMPLETE largest_n="
        f"{summary['largest_n_passing_all_arms']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SessionError, OSError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
