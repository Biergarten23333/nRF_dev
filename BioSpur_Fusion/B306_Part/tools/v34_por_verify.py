#!/usr/bin/env python3
"""V34 S3 zero-command POR proof followed by two-board latch checks."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_control import relay_command_patient
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from fusion_session import parse_fields, resolve_fusion_port
from relay8_3_t567_provision import tag_read
from fusion_session import SessionError

ROOT = Path("B306_Part/logs/b306_v34_20260803/V34C_P3_latch_retry1")
SLOTS = {
    "BSF3C79": 1, "BSFC2CC": 2, "BSF44AD": 3, "BSF6C53": 4,
    "BSF8BC4": 5, "BSF1120": 6, "BSF31CC": 7, "BSFAA61": 8,
    "BSFEC35": 9, "BSFB165": 10,
}


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def wait_master_status(channel) -> str:
    channel.send("MASTER STATUS")
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line and line.startswith("FUSION_MASTER_STATUS "):
            if parse_fields(line).get("marker") != "dk-fusion-imu-relay-v30":
                raise SessionError(f"Fusion Master marker mismatch: {line}")
            return line
    raise SessionError("Fusion Master status timed out")


def bounded_tag_read(channel, node: str, command: str, prefix: str) -> dict:
    return relay_command_patient(
        channel, node, command, prefix, attempts=2,
        reply_timeout_s=100.0 if node == "BSFB165" else 25.0,
    )


def observe(channel, seconds: float) -> dict:
    start = time.monotonic()
    rows = {n: {"uwb": 0, "sf_valid": 0, "imu_batches": 0, "imu_samples": 0,
                "first_sf_frame_us": None, "first_imu_base_us": None,
                "last_sf_frame_us": None, "last_imu_sample_us": None,
                "first_sf_host_mono": None, "first_imu_host_mono": None}
            for n in SLOTS}
    while time.monotonic() < start + seconds:
        line = channel.read(min(start + seconds, time.monotonic() + 0.5))
        if not line:
            continue
        f = parse_fields(line)
        node = f.get("name")
        if node not in rows:
            continue
        now = time.monotonic()
        if line.startswith("FUSION_UWB "):
            row = rows[node]
            row["uwb"] += 1
            if f.get("sf_valid") == "1":
                row["sf_valid"] += 1
                if row["first_sf_frame_us"] is None:
                    row["first_sf_frame_us"] = int(f["frame_us"])
                    row["first_sf_host_mono"] = now
                row["last_sf_frame_us"] = int(f["frame_us"])
        elif line.startswith("FUSION_IMU "):
            row = rows[node]
            row["imu_batches"] += 1
            row["imu_samples"] += int(f.get("n", "0"))
            if row["first_imu_base_us"] is None:
                row["first_imu_base_us"] = int(f["base_us"])
                row["first_imu_host_mono"] = now
            sample_offsets = [
                int(encoded.split(",", 1)[0], 0)
                for encoded in f.get("samples", "").split(";") if encoded
            ]
            row["last_imu_sample_us"] = int(f["base_us"]) + max(sample_offsets or [0])
    elapsed = time.monotonic() - start
    for row in rows.values():
        row["duration_s"] = elapsed
        uwb_span_s = (
            (row["last_sf_frame_us"] - row["first_sf_frame_us"]) / 1e6
            if row["uwb"] > 1 else None
        )
        imu_span_s = (
            (row["last_imu_sample_us"] - row["first_imu_base_us"]) / 1e6
            if row["imu_samples"] > 1 else None
        )
        row["uwb_record_span_s"] = uwb_span_s
        row["imu_sample_span_s"] = imu_span_s
        row["uwb_hz"] = ((row["uwb"] - 1) / uwb_span_s) if uwb_span_s else 0.0
        row["imu_sample_hz"] = ((row["imu_samples"] - 1) / imu_span_s) if imu_span_s else 0.0
        if row["first_sf_frame_us"] is not None and row["first_imu_base_us"] is not None:
            row["first_observed_imu_minus_sf_us"] = row["first_imu_base_us"] - row["first_sf_frame_us"]
    return rows


def main() -> int:
    ROOT.mkdir(parents=True, exist_ok=False)
    result = {"status": "IN_PROGRESS", "started": wall(), "commands_before_zero_command_observation": 0}
    channel = None
    with (ROOT / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(
                resolve_fusion_port(None), log, "FUSION",
                decoded_queue_records=262144, backlog_red_records=65536,
                raw_backlog_red_bytes=65536, stall_red_s=2.0,
            )
            channel.transport_mode = "binary"
            channel.text_pending.clear()
            result["decode_guard"] = decode_guard(channel, 15.0)
            result["master"] = wait_master_status(channel)

            # Load-bearing proof: no transmit to any node before this finishes.
            result["zero_command_observation"] = observe(channel, 30.0)
            bad = {}
            for node, row in result["zero_command_observation"].items():
                reasons = []
                if row["sf_valid"] < 100:
                    reasons.append(f"sf_valid={row['sf_valid']}")
                if not 6.5 <= row["uwb_hz"] <= 10.5:
                    reasons.append(f"uwb_hz={row['uwb_hz']:.3f}")
                # Delivery is a 200/s time-grid property. Freshness is judged
                # separately by duplicate-vector detection, never a rate band.
                if row["imu_sample_hz"] <= 0:
                    reasons.append(f"imu_hz={row['imu_sample_hz']:.3f}")
                if reasons:
                    bad[node] = reasons
            if bad:
                raise RuntimeError(f"zero-command behavioural proof failed: {bad}")

            # Readbacks occur only after the autonomous behavioural proof.
            readback = {}
            for node, slot in SLOTS.items():
                ping = b306_command(channel, node, "PING", "PONG ")
                imu = b306_command(channel, node, "IMU STATUS", "IMU ")
                cfg = bounded_tag_read(channel, node, "CFG_STATUS", "CFG ")
                beacon = bounded_tag_read(channel, node, "BEACON_STATUS", "BEACON ")
                pf, imf = parse_fields(ping["text"]), parse_fields(imu["text"])
                cf, bf = parse_fields(cfg["reply"]["text"]), parse_fields(beacon["reply"]["text"])
                expected = {"slot": f"{slot}/12", "period": "10", "sync": "1",
                            "stored": "1", "pslot": f"{slot}/12", "pperiod": "10", "psync": "1"}
                mismatch = {k: [cf.get(k), v] for k, v in expected.items() if cf.get(k) != v}
                if pf.get("fw") != "b306-imu-relay-v34c" or imf.get("active") != "1" or imf.get("rate") != "200" or imf.get("batch") != "10" or mismatch or bf.get("lock") != "1":
                    raise RuntimeError(f"readback mismatch {node}: ping={pf} imu={imf} cfg={mismatch} beacon={bf}")
                readback[node] = {"ping": pf, "imu": imf, "cfg": cf, "beacon": bf}
            result["readback"] = readback

            # Latch A: STOP must remain stopped despite continuing sf_valid frames.
            stop_node = "BSF3C79"
            result["stop_latch"] = {"node": stop_node}
            result["stop_latch"]["command"] = b306_command(channel, stop_node, "IMU STOP", "IMU STOP OK ")
            stop_obs = observe(channel, 6.0)[stop_node]
            stop_status = b306_command(channel, stop_node, "IMU STATUS", "IMU ")
            result["stop_latch"].update({"observation": stop_obs, "status": stop_status})
            sf_after_stop = stop_obs["sf_valid"]
            if sf_after_stop < 20 or stop_obs["imu_batches"] != 0 or parse_fields(stop_status["text"]).get("active") != "0":
                raise RuntimeError(f"STOP latch failed: {result['stop_latch']}")

            # Latch B: RATE=100 must survive continuing sf_valid frames.
            rate_node = "BSFC2CC"
            result["rate_latch"] = {"node": rate_node}
            result["rate_latch"]["command"] = b306_command(channel, rate_node, "IMU RATE=100", "IMU RATE OK ")
            rate_obs = observe(channel, 10.0)[rate_node]
            rate_status = b306_command(channel, rate_node, "IMU STATUS", "IMU ")
            result["rate_latch"].update({"observation": rate_obs, "status": rate_status})
            rf = parse_fields(rate_status["text"])
            if rate_obs["sf_valid"] < 40 or not 85 <= rate_obs["imu_sample_hz"] <= 115 or rf.get("rate") != "100":
                raise RuntimeError(f"RATE latch failed: {result['rate_latch']}")

            # Restore full-load volatile state for the next stage; these are explicit operator controls.
            result["restore"] = {
                stop_node: b306_command(channel, stop_node, "IMU START", "IMU START OK "),
                rate_node: b306_command(channel, rate_node, "IMU RATE=200", "IMU RATE OK "),
            }
            result["restore_observation"] = observe(channel, 8.0)
            for node in (stop_node, rate_node):
                hz = result["restore_observation"][node]["imu_sample_hz"]
                if not 180 <= hz <= 220:
                    raise RuntimeError(f"restore failed {node}: imu_hz={hz}")
            result["status"] = "PASS"
            return 0
        except Exception as exc:
            result["status"] = "FAIL"
            result["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            result["finished"] = wall()
            if channel is not None:
                result["host_health"] = channel.health_snapshot()
                channel.close()
            (ROOT / "result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
