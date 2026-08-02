#!/usr/bin/env python3
"""Strict five-node binary-egress qualification and superframe analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_superframe_alignment import analyze as analyze_superframe
from capacity_ramp import (
    BSFS,
    TelemetryAssembler,
    b306_command,
    cleanup,
    collect,
    relay_command,
    run_one,
    utc_now,
)
from fusion_session import (
    LineChannel,
    SessionError,
    parse_fields,
    resolve_fusion_port,
)
from pre_ramp_hardening import request_list


EXPECTED_TAG_MARKER = "tag-fusion-link-v2-relay3"
EXPECTED_B306_MARKER = "b306-imu-relay-v27"
EXPECTED_INTERVAL_UNITS = "40"
EXPECTED_LATENCY = "0"
EXPECTED_TIMEOUT_UNITS = "400"
EXPECTED_PHY = "2"


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def strict_link_gate(preflight: dict) -> None:
    aggregate = preflight["aggregate"]
    peers = preflight["peers"]
    errors: list[str] = []
    if aggregate.get("count") != "5" or aggregate.get("ready") != "5":
        errors.append(f"aggregate={aggregate}")
    if set(peers) != set(BSFS):
        errors.append(f"peer_set={sorted(peers)}")
    for node in BSFS:
        peer = peers.get(node, {})
        expected = {
            "interval_units": EXPECTED_INTERVAL_UNITS,
            "latency": EXPECTED_LATENCY,
            "timeout_units": EXPECTED_TIMEOUT_UNITS,
            "phy_tx": EXPECTED_PHY,
            "phy_rx": EXPECTED_PHY,
        }
        mismatches = {
            key: {"actual": peer.get(key), "expected": value}
            for key, value in expected.items()
            if peer.get(key) != value
        }
        if mismatches:
            errors.append(f"{node}={mismatches}")
    if errors:
        raise SessionError("strict five-link gate failed: " + "; ".join(errors))


def readback_superframe_by_stopping(
    channel: LineChannel,
    setup_path: Path,
) -> dict[str, object]:
    """Read relay3's configured base from the safe epoch-valid stop reply."""
    setup = json.loads(setup_path.read_text())
    slots = setup["slots"]
    expected_base = int(slots["superframe_base"])
    readback: dict[str, object] = {}
    for node in BSFS:
        status = relay_command(
            channel, node, "CFG_STOP", "CFG_STOP_OK ", attempts=1
        )
        parsed = {
            key.lower(): value
            for key, value in parse_fields(
                status["reply"]["text"]
            ).items()
        }
        if (
            parsed.get("superframe_base") != str(expected_base)
            or parsed.get("sf_valid") != "1"
            or parsed.get("run") != "0"
            or parsed.get("live") != "1"
        ):
            raise SessionError(
                f"{node} epoch-valid CFG_STOP readback mismatch: "
                f"{status['reply']['text']}"
            )
        slots["nodes"][node]["cfg_stop_status"] = status
        readback[node] = status
    write_json(setup_path, setup)
    return {
        "method": (
            "epoch-valid CFG_STOP after the formal window; relay3 "
            "CFG_STATUS is longer than the 191-byte UART relay payload"
        ),
        "configured_superframe_base": expected_base,
        "nodes": readback,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=1800.0)
    parser.add_argument("--fusion-port")
    parser.add_argument("--operator-token", required=True)
    parser.add_argument("--charge-confirmed", action="store_true")
    args = parser.parse_args()
    if args.operator_token != "POWERED ON":
        raise SessionError("literal operator token POWERED ON is required")
    if not args.charge_confirmed:
        raise SessionError("30-minute charge sufficiency was not confirmed")

    args.output_dir.mkdir(parents=True, exist_ok=False)
    run_label = "N5_C_batch5_30min"
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "operator_gate": {
            "token": args.operator_token,
            "charge_confirmed": args.charge_confirmed,
        },
        "expected_nodes": BSFS,
        "expected_tag_marker": EXPECTED_TAG_MARKER,
        "expected_b306_marker": EXPECTED_B306_MARKER,
        "prediction": {
            "sequence_gaps": 0,
            "disconnects": 0,
            "malformed": 0,
            "logger_drops": 0,
            "cdc_drops": 0,
            "imu_rate_hz_per_node": [199.0, 201.0],
            "latency_reference_p95_us": 97_600,
            "latency_reference_max_us": 207_400,
            "class2_events_expected_aggregate": 15,
            "superframe_base": "all five equal",
            "sweep_indices": "one common global timeline",
        },
    }
    write_json(args.output_dir / "summary.json", summary)
    channel = None
    try:
        with (args.output_dir / "fusion_raw.log").open(
            "a", buffering=1, encoding="utf-8"
        ) as raw:
            channel = LineChannel(
                resolve_fusion_port(args.fusion_port), raw, "FUSION"
            )
            assembler = TelemetryAssembler()
            counters: dict[str, int] = {}
            collect(channel, assembler, 2.0)
            preflight = request_list(channel, assembler, counters, BSFS)
            strict_link_gate(preflight)
            summary["strict_preflight"] = preflight

            versions: dict[str, object] = {}
            b306_versions: dict[str, object] = {}
            for node in BSFS:
                b306_ping = b306_command(channel, node, "PING", "PONG ")
                if (
                    f"fw={EXPECTED_B306_MARKER} " not in
                    f"{b306_ping['text']} "
                ):
                    raise SessionError(
                        f"{node} B306 marker mismatch: {b306_ping['text']}"
                    )
                b306_versions[node] = b306_ping
                version = relay_command(
                    channel, node, "VERSION", "VERSION ", attempts=3
                )
                text = version["reply"]["text"]
                if f"fw={EXPECTED_TAG_MARKER} " not in f"{text} ":
                    raise SessionError(
                        f"{node} tag marker mismatch: {text}"
                    )
                versions[node] = version
            summary["tag_versions"] = versions
            summary["b306_versions"] = b306_versions
            write_json(args.output_dir / "summary.json", summary)

            result = run_one(
                args.output_dir,
                channel,
                5,
                "C",
                args.duration_s,
                90,
                {
                    "p95_us": 97_600.0,
                    "max_us": 207_400.0,
                },
                run_label,
                imu_batch=5,
            )
            setup_path = args.output_dir / run_label / "setup.json"
            superframe_readback = readback_superframe_by_stopping(
                channel, setup_path
            )
            alignment = analyze_superframe(
                args.output_dir / "fusion_raw.log",
                setup_path,
                BSFS,
                float(result["started_monotonic"]),
                float(result["ended_monotonic"]),
            )
            write_json(
                args.output_dir / run_label / "superframe_alignment.json",
                alignment,
            )
            summary["qualification"] = {
                "pass": result["pass"],
                "gates": result["gates"],
                "aggregate": result["aggregate"],
                "per_node": result["per_node"],
            }
            summary["superframe_readback"] = superframe_readback
            summary["superframe_alignment"] = alignment
            summary["one_master_supported_nodes_with_30min_evidence"] = (
                5 if result["pass"] and alignment["pass"] else None
            )
            summary["cleanup"] = cleanup(channel)
            summary["status"] = "COMPLETE"
            summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = str(exc)
        summary["completed_utc"] = utc_now()
        if channel is not None:
            try:
                with (args.output_dir / "emergency_cleanup.log").open(
                    "a", buffering=1, encoding="utf-8"
                ) as emergency_log:
                    channel.log_file = emergency_log
                    summary["cleanup"] = cleanup(channel)
            except Exception as cleanup_exc:
                summary["cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        if channel is not None:
            channel.close()
        write_json(args.output_dir / "summary.json", summary)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (SessionError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
