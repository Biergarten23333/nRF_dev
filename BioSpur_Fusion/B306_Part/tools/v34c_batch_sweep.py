#!/usr/bin/env python3
"""V34C fixed-placement batch 10..16 sweep with one predeclared offline node."""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import SessionError, imu_sequence_gaps, parse_fields, resolve_fusion_port, u32_delta

ROOT = Path("B306_Part/logs/batch_sweep_20260803/CONTROL_RETURN_10")
NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFEC35", "BSFB165")
OFFLINE = {}
ACTIVE = tuple(n for n in NODES if n not in OFFLINE)


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def collect(channel, seconds: float) -> list[tuple[float, str]]:
    end = time.monotonic() + seconds
    rows = []
    while time.monotonic() < end:
        line = channel.read(min(end, time.monotonic() + 0.5))
        if line is not None:
            rows.append((time.monotonic(), line))
    return rows


def read_imu_status(channel, node: str) -> dict:
    errors = []
    for _ in range(3):
        try:
            return b306_command(channel, node, "IMU STATUS", "IMU ")
        except SessionError as exc:
            errors.append(str(exc))
    raise SessionError(f"{node} bounded IMU STATUS failed: {errors}")


def set_batch_once(channel, node: str, batch: int) -> dict:
    before = read_imu_status(channel, node)
    if parse_fields(before["text"]).get("batch") == str(batch):
        return {"before": before, "write": "SKIPPED_ALREADY_SET", "verified": before}
    try:
        command = b306_command(channel, node, f"IMU BATCH={batch}", "IMU BATCH OK ")
    except SessionError as exc:
        # Never retransmit a possibly executed write. Verify its effect using
        # a bounded, read-only status query instead.
        command = {"reply_missing": str(exc)}
    after = read_imu_status(channel, node)
    fields = parse_fields(after["text"])
    if fields.get("batch") != str(batch) or fields.get("active") != "1":
        raise SessionError(f"{node} batch write not verified: {after}")
    return {"before": before, "write": command, "verified": after}


def span_rate(count: int, first: int | None, last: int | None) -> tuple[float | None, float | None]:
    if count < 2 or first is None or last is None or last <= first:
        return None, None
    span = (last - first) / 1e6
    return span, (count - 1) / span


def analyze(rows: list[tuple[float, str]], batch: int, anchors: dict[str, float] | None = None) -> dict:
    out = {}
    for node in NODES:
        lines = [(t, s) for t, s in rows if parse_fields(s).get("name") == node]
        if anchors is not None and node in anchors:
            start = anchors[node] + 2.0
            lines = [(t, s) for t, s in lines if start <= t <= start + 60.0]
        imu = [(t, s, parse_fields(s)) for t, s in lines if s.startswith("FUSION_IMU ")]
        uwb = [(t, s, parse_fields(s)) for t, s in lines if s.startswith("FUSION_UWB ")]
        queue = [parse_fields(s) for _, s in lines if s.startswith("FUSION_QUEUE ")]
        qos = [parse_fields(s) for _, s in lines if s.startswith("FUSION_QOS ")]
        imu_count = sum(int(f.get("n", "0"), 0) for _, _, f in imu)
        imu_first = int(imu[0][2]["base_us"], 0) if imu else None
        imu_last = None
        if imu:
            f = imu[-1][2]
            offsets = [int(x.split(",", 1)[0], 0) for x in f.get("samples", "").split(";") if x]
            imu_last = int(f["base_us"], 0) + max(offsets or [0])
        imu_span, imu_rate = span_rate(imu_count, imu_first, imu_last)
        valid_uwb = [f for _, _, f in uwb if f.get("sf_valid") == "1"]
        uwb_first = int(valid_uwb[0]["frame_us"], 0) if valid_uwb else None
        uwb_last = int(valid_uwb[-1]["frame_us"], 0) if valid_uwb else None
        uwb_span, uwb_rate = span_rate(len(valid_uwb), uwb_first, uwb_last)
        gaps, _ = imu_sequence_gaps(s for _, s, _ in imu)
        deltas = None
        if len(queue) >= 2:
            deltas = {k: u32_delta(int(queue[0].get(k, "0"), 0), int(queue[-1].get(k, "0"), 0))
                      for k in ("q_drop_imu", "q_drop_uwb", "q_drop_ctl")}
        sums = {k: sum(int(f.get(k, "0"), 0) for f in qos)
                for k in ("crc_ok", "crc_error", "rx_timeout", "event_gaps", "reports")}
        denominator = sums["crc_ok"] + sums["crc_error"]
        sums["crc_error_ratio"] = sums["crc_error"] / denominator if denominator else None
        host_span = lines[-1][0] - lines[0][0] if len(lines) > 1 else None
        serviced = ((len(imu) + len(valid_uwb) - 1) / host_span
                    if host_span and len(imu) + len(valid_uwb) > 1 else None)
        out[node] = {
            "offline": node in OFFLINE, "imu_records": len(imu), "imu_samples": imu_count,
            "imu_span_s": imu_span, "delivered_imu_samples_s": imu_rate,
            "implied_imu_notifications_s": (imu_rate / batch if imu_rate is not None else None),
            "imu_sequence_gaps": gaps, "queue_records": len(queue), "queue_deltas": deltas,
            "uwb_records": len(valid_uwb), "uwb_span_s": uwb_span, "uwb_delivered_hz": uwb_rate,
            "valid_link_count_histogram": dict(Counter(str(int(f.get("valid", "0"), 0).bit_count()) for f in valid_uwb)),
            "qos": sums, "demanded_notifications_s": 200.0 / batch + 1000.0 / 120.0,
            "serviced_imu_plus_uwb_notifications_s": serviced,
        }
    return out


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=False)
    result = {"status": "IN_PROGRESS", "started": wall(), "offline_before_start": OFFLINE,
              "active_nodes": ACTIVE, "settle_exclusion_s": 5.0, "steps": {}}
    channel = None
    with (ROOT / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(resolve_fusion_port(None), log, "FUSION",
                                          decoded_queue_records=262144,
                                          backlog_red_records=65536,
                                          raw_backlog_red_bytes=65536, stall_red_s=2.0)
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_guard"] = decode_guard(channel, 15.0)
            channel.send("MASTER STATUS")
            status_deadline = time.monotonic() + 5.0
            while time.monotonic() < status_deadline:
                line = channel.read(status_deadline)
                if line and line.startswith("FUSION_MASTER_STATUS "):
                    result["master_preflight"] = line
                    f = parse_fields(line)
                    if f.get("marker") != "dk-fusion-imu-relay-v30" or f.get("count") != "10" or f.get("ready") != "10":
                        raise RuntimeError(f"expected exactly ten ready: {line}")
                    break
            else:
                raise RuntimeError("MASTER STATUS timeout")

            for batch in range(10, 11):
                step = {"status": "IN_PROGRESS", "started": wall(), "batch": batch, "commands": {}}
                result["steps"][str(batch)] = step
                write(ROOT / "checkpoint.json", result)
                transition_start = time.monotonic()
                for node in ACTIVE:
                    step["commands"][node] = set_batch_once(channel, node, batch)
                transition_rows = collect(channel, 30.0)
                first_matching = {}
                for node in ACTIVE:
                    matches = [(t, parse_fields(s)) for t, s in transition_rows
                               if s.startswith("FUSION_IMU ") and parse_fields(s).get("name") == node
                               and parse_fields(s).get("n") == str(batch)]
                    first_matching[node] = matches[0][0] - transition_start if matches else None
                anchors = {node: transition_start + delay for node, delay in first_matching.items()
                           if delay is not None}
                step["transition"] = {"exclusion_basis": "first target-batch record plus 2.0 s",
                                      "settle_margin_s": 2.0, "first_matching_batch_host_delay_s": first_matching,
                                      "retained_records": len(transition_rows)}
                rows = transition_rows + collect(channel, 65.0)
                step["analysis"] = analyze(rows, batch, anchors if len(anchors) == len(ACTIVE) else None)
                step["protocol_target_records_complete"] = len(anchors) == len(ACTIVE)
                step["status"] = "COMPLETE"
                step["finished"] = wall()
                write(ROOT / f"batch_{batch}.json", step)
                write(ROOT / "checkpoint.json", result)
            result["terminal_restore"] = {
                node: set_batch_once(channel, node, 10) for node in ACTIVE
            }
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            if channel is not None and result.get("status") != "PASS":
                restore = {}
                for node in ACTIVE:
                    try:
                        restore[node] = set_batch_once(channel, node, 10)
                    except Exception as restore_exc:
                        restore[node] = {"error": f"{type(restore_exc).__name__}: {restore_exc}"}
                result["emergency_restore_batch10"] = restore
            result["finished"] = wall()
            if channel is not None:
                result["host_health"] = channel.health_snapshot()
                channel.close()
            write(ROOT / "result.json", result)


if __name__ == "__main__":
    raise SystemExit(main())
