#!/usr/bin/env python3
"""Preregistered five-node E2E capture with a 60-second validity branch."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from capacity_ramp import (
    BSFS,
    RecordingAssembler,
    analyze_run,
    b306_command,
    cleanup,
    collect,
    ensure_imu_stopped,
    predictions_for,
    relay_command,
    setup_arm,
    start_arm_imus,
    wait_all_telemetry,
)
from e2e_relay_t4 import RelayedUwbArchive
from fusion_host_binary import FrameStreamDecoder
from fusion_session import LineChannel, SessionError, resolve_fusion_port
from post_capacity_qualification import strict_link_gate
from pre_ramp_hardening import request_list, request_resources
from validate_layer2_v31 import (
    EXPECTED_B306_MARKER,
    EXPECTED_DK_MARKER,
    EXPECTED_TAG_MARKER,
    layer2_verdict,
    request_master_status,
    wait_spacing,
)
from validate_notify_fix_v30 import queue_diagnostics


LITERAL_OPERATOR_TOKEN = "Fusion PCB PWR ON"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


class ObservingDecoder(FrameStreamDecoder):
    def __init__(self, observer) -> None:
        super().__init__()
        self.observer = observer
        self.enabled = False

    def feed(self, data: bytes):
        frames = super().feed(data)
        if self.enabled:
            for frame in frames:
                self.observer(frame)
        return frames


def preflight(channel: LineChannel) -> dict[str, object]:
    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    collect(channel, assembler, 2.0)
    listing = request_list(channel, assembler, counters, BSFS)
    strict_link_gate(listing)
    master = request_master_status(channel)
    if master.get("marker") != EXPECTED_DK_MARKER:
        raise SessionError(f"DK marker mismatch: {master}")

    spacing = None
    if (
        listing["aggregate"].get("spacing") != "ON"
        or listing["aggregate"].get("spacing_us") != "10000"
    ):
        spacing = wait_spacing(channel, "ON")
        assembler = RecordingAssembler()
        collect(channel, assembler, 1.0)
        listing = request_list(channel, assembler, counters, BSFS)
        strict_link_gate(listing)
    if (
        listing["aggregate"].get("spacing") != "ON"
        or listing["aggregate"].get("spacing_us") != "10000"
    ):
        raise SessionError(
            f"five-node E2E run requires accepted spacing ON/10000: {listing}"
        )

    versions: dict[str, object] = {}
    for node in BSFS:
        ping = b306_command(channel, node, "PING", "PONG ")
        if f"fw={EXPECTED_B306_MARKER} " not in f"{ping['text']} ":
            raise SessionError(f"{node} B306 marker mismatch: {ping['text']}")
        tag = relay_command(channel, node, "VERSION", "VERSION ", attempts=3)
        if (
            f"fw={EXPECTED_TAG_MARKER} "
            not in f"{tag['reply']['text']} "
        ):
            raise SessionError(
                f"{node} tag marker mismatch: {tag['reply']['text']}"
            )
        imu = b306_command(channel, node, "IMU STATUS", "IMU ")
        if "active=0 " not in f"{imu['text']} ":
            raise SessionError(f"{node} IMU not stopped: {imu['text']}")
        idle = relay_command(
            channel, node, "MODE IDLE", "MODE_OK MODE=IDLE", attempts=3
        )
        versions[node] = {
            "b306": ping,
            "tag": tag,
            "imu": imu,
            "idle_confirmation": idle,
        }
    return {
        "list": listing,
        "master": master,
        "spacing_transition": spacing,
        "versions": versions,
    }


def run_capture(
    output_dir: Path,
    channel: LineChannel,
    archive: RelayedUwbArchive,
    observer: ObservingDecoder,
    duration_s: float,
    validity_gate_s: float,
    placement_note: str,
) -> dict[str, object]:
    run_dir = output_dir / "five_node_static_600s"
    run_dir.mkdir(parents=True, exist_ok=False)
    generation = int(time.time()) & 0xFF
    prediction = predictions_for(
        5, "C", duration_s, None, 5, 200, 10.0, 2.0, len(BSFS)
    )
    prediction["validity_gate"] = {
        "time_s": validity_gate_s,
        "branch": (
            "abort if any expected node has zero captured UWB records with "
            "at least one usable range"
        ),
    }
    prediction["static_cluster_sanity"] = {
        "nonoverlap": (
            "pairwise cluster-center distance exceeds the sum of the two "
            "3D RMS scatters"
        ),
        "plausible_room_bounds": (
            "cluster mean lies within the anchor-layout axis-aligned bounds "
            "padded by 1000 mm"
        ),
    }
    write_json(run_dir / "predictions.json", prediction)
    write_json(
        run_dir / "capture_metadata.json",
        {
            "operator_gate": LITERAL_OPERATOR_TOKEN,
            "placement_note": placement_note,
            "expected_nodes": BSFS,
            "imu_rate_hz": 200,
            "imu_batch": 5,
            "uwb_rate_hz": 10,
            "firmware_changes": "none",
        },
    )

    setup = setup_arm(
        channel,
        5,
        "C",
        generation,
        imu_batch=5,
        imu_rate_hz=200,
        uwb_rate_hz=10.0,
        status_rate_hz=2.0,
    )
    write_json(run_dir / "setup.json", setup)

    assembler = RecordingAssembler()
    counters: dict[str, int] = {}
    collect(channel, assembler, 2.0)
    start_list = request_list(channel, assembler, counters, BSFS)
    strict_link_gate(start_list)
    start_resources = request_resources(channel, assembler, counters)
    baseline = wait_all_telemetry(channel, assembler)
    setup["imu_start"] = start_arm_imus(channel, BSFS)
    write_json(run_dir / "setup.json", setup)

    rows: list[tuple[float, str]] = []
    decoder_errors_before = observer.errors
    started_utc = utc_now()
    started = time.monotonic()
    validity_checked = False
    early_abort_nodes: list[str] = []
    observer.enabled = True
    try:
        while True:
            elapsed = time.monotonic() - started
            if elapsed >= duration_s:
                break
            line = channel.read(
                min(time.monotonic() + 0.5, started + duration_s)
            )
            if line is not None:
                stamp = time.monotonic()
                assembler.observe(line)
                rows.append((stamp, line))
            elapsed = time.monotonic() - started
            if not validity_checked and elapsed >= validity_gate_s:
                validity_checked = True
                snapshot = archive.snapshot(BSFS)
                write_json(run_dir / "validity_at_60s.json", snapshot)
                early_abort_nodes = [
                    node
                    for node in BSFS
                    if snapshot[node]["records_with_at_least_one_valid"] == 0
                ]
                if early_abort_nodes:
                    break
    finally:
        observer.enabled = False
    ended = time.monotonic()
    actual_duration = ended - started
    validity = archive.snapshot(BSFS)
    write_json(run_dir / "validity_final.json", validity)
    write_json(
        run_dir / "window_checkpoint.json",
        {
            "status": (
                "EARLY_ABORT_VALIDITY"
                if early_abort_nodes
                else "DATA_WINDOW_COMPLETE"
            ),
            "started_utc": started_utc,
            "started_monotonic": started,
            "ended_monotonic": ended,
            "actual_duration_s": actual_duration,
            "retained_rows": len(rows),
            "validity_gate_checked": validity_checked,
            "early_abort_nodes": early_abort_nodes,
        },
    )

    post_window_stop = {}
    for node in BSFS:
        post_window_stop[node] = ensure_imu_stopped(channel, node)
    final = wait_all_telemetry(channel, assembler, timeout_s=12.0)
    end_list = request_list(channel, assembler, counters, BSFS)
    end_resources = request_resources(channel, assembler, counters)

    analysis = analyze_run(
        rows,
        assembler,
        baseline,
        final,
        actual_duration,
        5,
        "C",
        start_list,
        end_list,
        start_resources,
        end_resources,
        prediction,
    )
    analysis.update(
        {
            "started_monotonic": started,
            "ended_monotonic": ended,
            "started_utc": started_utc,
            "post_window_stop": post_window_stop,
            "host_decoder_errors_delta": observer.errors
            - decoder_errors_before,
            "validity": validity,
            "validity_early_abort_nodes": early_abort_nodes,
        }
    )
    analysis["gates"]["zero_host_decoder_errors"] = (
        analysis["host_decoder_errors_delta"] == 0
    )
    analysis["gates"]["validity_gate"] = not early_abort_nodes
    analysis["pass"] = all(analysis["gates"].values())

    diagnostics = {node: queue_diagnostics(channel, node) for node in BSFS}
    if early_abort_nodes:
        layer2 = {
            "status": "NOT_EVALUATED_VALIDITY_BRANCH_FIRED",
            "reason": (
                "the preregistered measurement-validity branch ended the "
                "window at 60 seconds"
            ),
        }
    else:
        layer2 = layer2_verdict(
            analysis, diagnostics, "ON", require_zero_qdrop=True
        )
    result = {
        "status": (
            "EARLY_ABORT_VALIDITY" if early_abort_nodes else "COMPLETE"
        ),
        "capture_analysis": analysis,
        "queue_diagnostics": diagnostics,
        "layer2_verdict": layer2,
        "validity": validity,
        "early_abort_nodes": early_abort_nodes,
        "archive": {
            "epochs_csv": str(archive.epochs_path),
            "tr_all_csv": str(archive.tr_path),
            "kind1_cobs": str(archive.binary_path),
        },
    }
    write_json(run_dir / "capture_result.json", result)
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--operator-token", required=True)
    parser.add_argument("--placement-note", required=True)
    parser.add_argument("--duration-s", type=float, default=600.0)
    parser.add_argument("--validity-gate-s", type=float, default=60.0)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.operator_token != LITERAL_OPERATOR_TOKEN:
        raise SessionError(
            f"literal operator token {LITERAL_OPERATOR_TOKEN!r} is required"
        )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    capture_dir = args.output_dir / "capture"
    capture_dir.mkdir(parents=True, exist_ok=False)
    summary: dict[str, object] = {
        "status": "IN_PROGRESS",
        "started_utc": utc_now(),
        "operator_token": args.operator_token,
        "placement_note": args.placement_note,
    }
    write_json(capture_dir / "summary.json", summary)

    channel = None
    archive = RelayedUwbArchive(capture_dir)
    raw = None
    try:
        raw = (capture_dir / "fusion_raw.log").open(
            "a", buffering=1, encoding="utf-8"
        )
        channel = LineChannel(resolve_fusion_port(args.fusion_port), raw, "FUSION")
        channel.send("OUTPUT BINARY")
        observer = ObservingDecoder(archive.observe_host_frame)
        channel.binary_decoder = observer
        summary["preflight"] = preflight(channel)
        write_json(capture_dir / "summary.json", summary)
        summary["capture"] = run_capture(
            capture_dir,
            channel,
            archive,
            observer,
            args.duration_s,
            args.validity_gate_s,
            args.placement_note,
        )
        summary["cleanup"] = cleanup(channel)
        summary["status"] = summary["capture"]["status"]
        summary["completed_utc"] = utc_now()
    except Exception as exc:
        summary["status"] = "FAILED"
        summary["error"] = f"{exc.__class__.__name__}: {exc}"
        summary["completed_utc"] = utc_now()
        if channel is not None:
            try:
                summary["exception_cleanup"] = cleanup(channel)
            except Exception as cleanup_exc:
                summary["cleanup_error"] = str(cleanup_exc)
        raise
    finally:
        archive.close()
        if channel is not None:
            channel.close()
        if raw is not None:
            raw.close()
        write_json(capture_dir / "summary.json", summary)
        write_json(capture_dir / "end_state.json", summary.get("cleanup", {}))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, SessionError, ValueError) as exc:
        print(f"ERROR: {exc}", flush=True)
        raise SystemExit(2)
