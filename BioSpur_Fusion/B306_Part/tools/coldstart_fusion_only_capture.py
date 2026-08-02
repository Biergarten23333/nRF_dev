#!/usr/bin/env python3
"""Read-only five-node Fusion-plane capture without opening listener VCOMs."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

from e2e_relay_t4 import RelayedUwbArchive
from fusion_host_binary import FrameStreamDecoder
from fusion_session import (
    LineChannel,
    imu_sequence_gaps,
    parse_fields,
    resolve_fusion_port,
    u32_delta,
)


DEFAULT_EXPECTED_NODES = (
    "BSF3C79",
    "BSFC2CC",
    "BSF44AD",
    "BSF6C53",
    "BSF8BC4",
)
TELEMETRY_FIELDS = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "notify_errno",
    "uart_restarts",
    "uart_err",
    "logger_drop",
    "imu_i2c_err",
    "imu_missed_deadlines",
)
TRANSPORT_GATE_FIELDS = tuple(
    field for field in TELEMETRY_FIELDS if field != "imu_i2c_err"
)


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class GatedDecoder(FrameStreamDecoder):
    def __init__(self, archive: RelayedUwbArchive) -> None:
        super().__init__()
        self.archive = archive
        self.enabled = False

    def feed(self, data: bytes):
        frames = super().feed(data)
        if self.enabled:
            for frame in frames:
                self.archive.observe_host_frame(frame)
        return frames


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--duration", required=True, type=float)
    parser.add_argument("--fusion-port")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--expect-imu", action="store_true")
    parser.add_argument(
        "--expected-nodes",
        default=",".join(DEFAULT_EXPECTED_NODES),
        help="comma-separated list of exactly five BSF identities",
    )
    args = parser.parse_args()
    if args.duration <= 0:
        raise SystemExit("--duration must be positive")
    expected_nodes = tuple(
        item.strip() for item in args.expected_nodes.split(",") if item.strip()
    )
    if len(expected_nodes) != 5 or len(set(expected_nodes)) != 5:
        raise SystemExit("--expected-nodes requires exactly five unique names")
    args.out_dir.mkdir(parents=True, exist_ok=False)

    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "phase": args.phase,
        "duration_requested_s": args.duration,
        "expected_nodes": expected_nodes,
        "writes": [],
        "transport": "native CDC, read-only, DTR/RTS disabled",
    }
    write_json(args.out_dir / "summary.json", summary)

    archive = RelayedUwbArchive(args.out_dir)
    decoder = GatedDecoder(archive)
    channel = None
    raw = (args.out_dir / "fusion_cdc.log").open(
        "x", encoding="utf-8", buffering=1
    )
    line_kinds: Counter[str] = Counter()
    source_kinds: Counter[str] = Counter()
    imu_lines: dict[str, list[str]] = {name: [] for name in expected_nodes}
    telemetry: dict[str, list[dict[str, str]]] = {
        name: [] for name in expected_nodes
    }
    disconnects: list[str] = []
    malformed: list[str] = []
    try:
        channel = LineChannel(resolve_fusion_port(args.fusion_port), raw, "FUSION")
        channel.transport_mode = "binary"
        channel.binary_decoder = decoder

        seen: set[str] = set()
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline and seen != set(expected_nodes):
            line = channel.read(min(deadline, time.monotonic() + 0.25))
            if line and line.startswith("FUSION_UWB "):
                name = parse_fields(line).get("name")
                if name in expected_nodes:
                    seen.add(name)
        if seen != set(expected_nodes):
            raise RuntimeError(
                "decode-before-capture guard missing "
                f"{sorted(set(expected_nodes) - seen)}"
            )
        summary["preflight"] = {
            "status": "PASS",
            "seen_nodes": sorted(seen),
            "binary_resync_errors": decoder.errors,
        }

        if args.expect_imu:
            # The five-node 200 Hz stream can leave several seconds of
            # already-decoded IMU records behind the UWB-only preflight.
            # Starting the formal sequence check on that stale prefix creates
            # one artificial jump per node when the reader reaches live data.
            # Drop only the host-side preflight backlog, reset the COBS
            # decoder, and establish one fresh IMU record per node before the
            # formal boundary.  This sends no device command and discards no
            # byte inside the formal window.
            channel.device.reset_input_buffer()
            channel.decoded_lines.clear()
            channel.text_pending.clear()
            decoder = GatedDecoder(archive)
            channel.binary_decoder = decoder
            warm_started = time.monotonic()
            warm_deadline = warm_started + 15.0
            last_imu: dict[str, tuple[int, int]] = {}
            consecutive: dict[str, int] = {
                name: 0 for name in expected_nodes
            }
            while time.monotonic() < warm_deadline:
                line = channel.read(
                    min(warm_deadline, time.monotonic() + 0.25)
                )
                if line and line.startswith("FUSION_IMU "):
                    fields = parse_fields(line)
                    name = fields.get("name")
                    if name in expected_nodes:
                        seq = int(fields["seq"], 0)
                        count = int(fields["n"], 0)
                        previous = last_imu.get(name)
                        if (
                            previous is not None
                            and seq
                            == ((previous[0] + previous[1]) & 0xFFFF)
                        ):
                            consecutive[name] += 1
                        else:
                            consecutive[name] = 1
                        last_imu[name] = (seq, count)
                if (
                    time.monotonic() - warm_started >= 5.0
                    and all(
                        consecutive[name] >= 50 for name in expected_nodes
                    )
                ):
                    break
            else:
                raise RuntimeError(
                    "fresh-boundary continuity guard failed: "
                    f"{consecutive}"
                )
            summary["fresh_boundary_guard"] = {
                "status": "PASS",
                "method": (
                    "host input backlog cleared after preflight; fresh COBS "
                    "decoder; >=5 s warmup and >=50 consecutive gap-free "
                    "IMU batches observed per node"
                ),
                "consecutive_batches": consecutive,
                "warmup_s": time.monotonic() - warm_started,
            }

        decoder.errors = 0
        decoder.enabled = True
        started_epoch_ns = time.time_ns()
        started_monotonic_ns = time.monotonic_ns()
        deadline = time.monotonic() + args.duration
        while time.monotonic() < deadline:
            line = channel.read(min(deadline, time.monotonic() + 0.5))
            if not line:
                continue
            kind = line.split(" ", 1)[0]
            line_kinds[kind] += 1
            fields = parse_fields(line)
            name = fields.get("name")
            if name:
                source_kinds[f"{kind}:{name}"] += 1
            if kind == "FUSION_IMU" and name in imu_lines:
                imu_lines[name].append(line)
            elif kind == "FUSION_TELEMETRY" and name in telemetry:
                telemetry[name].append(fields)
            elif kind == "FUSION_DISCONNECTED":
                disconnects.append(line)
            elif kind == "FUSION_MALFORMED":
                malformed.append(line)
        ended_monotonic_ns = time.monotonic_ns()
        ended_epoch_ns = time.time_ns()
        decoder.enabled = False
        archive_snapshot = archive.snapshot(expected_nodes)
        archive.close()
        duration_s = (ended_monotonic_ns - started_monotonic_ns) / 1e9
        imu_summary: dict[str, object] = {}
        imu_gate = True
        if args.expect_imu:
            for name in expected_nodes:
                gaps, records = imu_sequence_gaps(imu_lines[name])
                samples = sum(
                    int(parse_fields(line).get("n", "0"), 0)
                    for line in imu_lines[name]
                )
                first = telemetry[name][0] if telemetry[name] else {}
                last = telemetry[name][-1] if telemetry[name] else {}
                deltas = {
                    field: (
                        u32_delta(
                            int(first.get(field, "0"), 0),
                            int(last.get(field, "0"), 0),
                        )
                        if first and last
                        else None
                    )
                    for field in TELEMETRY_FIELDS
                }
                rate = samples / duration_s
                uwb_records = int(source_kinds.get(f"FUSION_UWB:{name}", 0))
                uwb_rate = uwb_records / duration_s
                node_pass = (
                    190.0 <= rate <= 210.0
                    and gaps == 0
                    and 9.5 <= uwb_rate <= 10.5
                    and all(
                        deltas[field] == 0 for field in TRANSPORT_GATE_FIELDS
                    )
                )
                imu_gate = imu_gate and node_pass
                imu_summary[name] = {
                    "imu_records": records,
                    "imu_samples": samples,
                    "imu_effective_rate_hz": rate,
                    "imu_sequence_gaps": gaps,
                    "uwb_records": uwb_records,
                    "uwb_rate_hz": uwb_rate,
                    "telemetry_samples": len(telemetry[name]),
                    "telemetry_delta": deltas,
                    "recovered_jy61p_events": deltas["imu_i2c_err"],
                    "pass": node_pass,
                }
            imu_gate = (
                imu_gate
                and decoder.errors == 0
                and not disconnects
                and not malformed
            )
        summary.update(
            {
                "status": "COMPLETE",
                "started_epoch_ns": started_epoch_ns,
                "started_monotonic_ns": started_monotonic_ns,
                "ended_epoch_ns": ended_epoch_ns,
                "ended_monotonic_ns": ended_monotonic_ns,
                "duration_s": duration_s,
                "line_kinds": dict(line_kinds),
                "source_kinds": dict(source_kinds),
                "decoder_errors": decoder.errors,
                "archive_summary": archive_snapshot,
                "disconnects": disconnects,
                "malformed": malformed,
                "expect_imu": args.expect_imu,
                "imu_summary": imu_summary,
                "imu_gate_pass": imu_gate if args.expect_imu else None,
            }
        )
        write_json(args.out_dir / "summary.json", summary)
        return 0
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        write_json(args.out_dir / "summary.json", summary)
        return 1
    finally:
        try:
            archive.close()
        except Exception:
            pass
        if channel is not None:
            channel.close()
        raw.close()


if __name__ == "__main__":
    raise SystemExit(main())
