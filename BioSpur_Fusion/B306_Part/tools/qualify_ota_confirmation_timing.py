#!/usr/bin/env python3
"""Ten-board reboot-only timing qualification; never writes an image slot."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from async_line_channel import ThreadedLineChannel
from capacity_ramp import b306_command
from coldstart_fusion_control import decode_guard
from confirm_b306_v32 import wait_master_status
from fusion_session import SessionError, parse_fields, resolve_fusion_port

MASTER_MARKER = "dk-fusion-imu-relay-v36"
NODES = ("BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
         "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35")
EXPECTED = frozenset(NODES)
RETRYABLE = ("bridge_not_ready", "not_connected", "reason=syntax", "truncated")


def percentile95(values: list[float]) -> float:
    ordered = sorted(values)
    rank = .95 * (len(ordered) - 1)
    low = int(rank)
    fraction = rank - low
    return ordered[low] + fraction * (ordered[min(low + 1, len(ordered) - 1)] - ordered[low])


def evaluate_inventory(master_line: str, aggregate_line: str,
                       peer_lines: list[str], pings: dict[str, dict],
                       expected_marker: str = MASTER_MARKER) -> dict:
    """Evaluate one exact inventory sample without substring comparisons."""
    master = parse_fields(master_line)
    aggregate = parse_fields(aggregate_line)
    parsed_peers = [parse_fields(line) for line in peer_lines]
    names = [row.get("name") for row in parsed_peers if row.get("name")]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    peers = {row.get("name"): row for row in parsed_peers if row.get("name")}
    unexpected = sorted(set(peers) - EXPECTED)
    rows = {}
    for node in NODES:
        peer = peers.get(node, {})
        ping = pings.get(node, {})
        text = str(ping.get("text", ""))
        ping_name = parse_fields(text).get("name") if text else None
        rows[node] = {
            "present": node in peers,
            "connected": peer.get("connected") == "1",
            "subscribed": peer.get("subscribed") == "1",
            "ping_ok": ping_name == node,
            "ping": ping,
            "last_error": ping.get("error"),
        }
    exact_peers = not duplicates and set(peers) == EXPECTED and len(parsed_peers) == 10
    ok = (
        master.get("marker") == expected_marker
        and master.get("count") == "10"
        and master.get("ready") == "10"
        and aggregate.get("count") == "10"
        and aggregate.get("ready") == "10"
        and exact_peers
        and all(row["connected"] and row["subscribed"] and row["ping_ok"]
                for row in rows.values())
    )
    return {"ok": ok, "master": master, "aggregate": aggregate,
            "nodes": rows, "unexpected_peers": unexpected,
            "duplicate_peers": duplicates}


def stable_gate_passes(samples: list[dict], required: int = 10) -> bool:
    return len(samples) == required and all(sample.get("ok") is True for sample in samples)


def qualification_summary(samples: list[dict], restore_max_s: float,
                          prepare_confirm_max_s: float) -> dict:
    valid = [sample for sample in samples if sample.get("valid")]
    exact_nodes = len(valid) == 10 and {row.get("node") for row in valid} == EXPECTED
    if not exact_nodes:
        return {"valid_samples": len(valid), "invalid_samples": len(samples) - len(valid),
                "gate": "BLOCKED", "reason": "exactly ten unique valid nodes required"}
    totals = [row["components_s"]["reboot_to_status"] for row in valid]
    routes = [row["components_s"]["route_to_pong"] for row in valid]
    statuses = [row["components_s"]["status"] for row in valid]
    upper = restore_max_s + max(routes) + max(statuses) + prepare_confirm_max_s
    margin = max(30.0, .25 * upper)
    return {
        "valid_samples": 10, "invalid_samples": len(samples) - 10,
        "reboot_to_status_max_s": max(totals),
        "reboot_to_status_p95_s": percentile95(totals),
        "component_max_s": {"archived_master_restore": restore_max_s,
                            "route_to_pong": max(routes), "status": max(statuses),
                            "archived_prepare_to_confirm": prepare_confirm_max_s},
        "conservative_upper_s": upper, "margin_policy": "max(30 seconds, 25%)",
        "margin_s": margin, "upper_plus_margin_s": upper + margin,
        "gate": "PASS" if upper + margin < 180.0 else "BLOCKED",
    }


def collect_list(channel, timeout_s: float = 3.0) -> tuple[str, list[str]]:
    channel.send("LIST")
    deadline = time.monotonic() + timeout_s
    aggregate = ""
    peers: list[str] = []
    while time.monotonic() < deadline:
        line = channel.read(deadline)
        if line is None:
            break
        if line.startswith("FUSION_LIST "):
            aggregate = line
            peers = []
        elif aggregate and line.startswith("FUSION_PEER "):
            peers.append(line)
            if len(peers) >= 10:
                break
    if not aggregate:
        raise SessionError("LIST produced no FUSION_LIST row")
    return aggregate, peers


def read_ping(channel, node: str) -> dict:
    try:
        reply = b306_command(channel, node, "PING", "PONG ")
        return {"text": str(reply["text"]), "reply": reply}
    except Exception as exc:  # evidence collection must retain every peer
        return {"text": "", "error": f"{type(exc).__name__}: {exc}"}


def inventory_sample(channel) -> dict:
    observed = time.monotonic()
    master_line = wait_master_status(channel)
    aggregate, peers = collect_list(channel)
    pings = {node: read_ping(channel, node) for node in NODES}
    value = evaluate_inventory(master_line, aggregate, peers, pings)
    value.update({"observed_monotonic": observed, "master_line": master_line,
                  "aggregate_line": aggregate, "peer_lines": peers})
    return value


def update_diagnostics(diagnostics: dict, sample: dict) -> None:
    when = sample["observed_monotonic"]
    for node, row in sample["nodes"].items():
        dst = diagnostics["nodes"][node]
        if row["present"]:
            dst["first_seen"] = dst["first_seen"] or when
            dst["last_seen"] = when
        dst.update({key: row[key] for key in
                    ("present", "connected", "subscribed", "ping_ok", "last_error")})
    diagnostics["unexpected_peers"] = sorted(set(diagnostics["unexpected_peers"]) |
                                              set(sample["unexpected_peers"]))


def wait_stable(channel, timeout_s: float, evidence: list[dict], diagnostics: dict) -> bool:
    deadline = time.monotonic() + timeout_s
    consecutive: list[dict] = []
    while time.monotonic() < deadline:
        started = time.monotonic()
        sample = inventory_sample(channel)
        evidence.append(sample)
        update_diagnostics(diagnostics, sample)
        consecutive = consecutive + [sample] if sample["ok"] else []
        if stable_gate_passes(consecutive):
            return True
        remaining = 1.0 - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(remaining)
    return False


def status_read(channel, node: str) -> dict:
    return b306_command(channel, node, "STATUS", "STATUS ")


def run_one_reboot(channel, node: str, master_before: dict,
                   per_node_timeout_s: float, expected_marker: str | None = None,
                   expected_fwid: str | None = None,
                   expected_image_sha: str | None = None) -> dict:
    sample: dict = {"node": node, "valid": False, "master_before": master_before,
                    "retry_errors": []}
    try:
        before = b306_command(channel, node, "PING", "PONG ")
        before_status = status_read(channel, node)
        before_up = int(parse_fields(str(before_status["text"])).get("up_ms", "-1"), 0)
        before_fields = parse_fields(str(before["text"]))
        if before_fields.get("name") != node:
            raise SessionError(f"pre-reboot wrong node: {before}")
        for key, expected in (("fw", expected_marker), ("fwid", expected_fwid),
                              ("image_sha", expected_image_sha)):
            if expected is not None and before_fields.get(key) != expected:
                raise SessionError(f"pre-reboot {key} mismatch: {before}")
        sample.update({"pre_reboot_pong": before, "pre_reboot_status": before_status})
        t0 = time.monotonic(); t1 = t0; t2 = t0
        sample["reboot_reply"] = b306_command(channel, node, "REBOOT", "REBOOT QUEUED ")
        deadline = t0 + per_node_timeout_s
        after = None; disconnect = False; reconnect = False
        while time.monotonic() < deadline:
            try:
                aggregate, peers = collect_list(channel, 1.0)
                peer = {parse_fields(line).get("name"): parse_fields(line) for line in peers}.get(node)
                if peer is None or peer.get("connected") != "1" or peer.get("subscribed") != "1":
                    disconnect = True
                    time.sleep(.25)
                    continue
                if not disconnect:
                    # The old route can remain visible briefly after REBOOT
                    # QUEUED. A PING here can land in the reboot window and
                    # the send guard then correctly forbids a duplicate.
                    time.sleep(.25)
                    continue
                reconnect = True
                candidate = b306_command(channel, node, "PING", "PONG ")
                if parse_fields(str(candidate["text"])).get("name") == node and disconnect:
                    # A retryable route/response failure is disconnect evidence;
                    # this requested-node PONG is the matching reconnect witness.
                    reconnect = True
                    after = candidate
                    break
            except Exception as exc:
                message = f"{type(exc).__name__}: {exc}"
                sample["retry_errors"].append(message)
                if any(token in message for token in RETRYABLE):
                    disconnect = True
            time.sleep(.25)
        if after is None:
            raise SessionError("no PONG after observed disconnect/reconnect")
        t3 = time.monotonic()
        after_status = status_read(channel, node)
        after_up = int(parse_fields(str(after_status["text"])).get("up_ms", "-1"), 0)
        confirm = b306_command(channel, node, "BOOT CONFIRM STATUS", "BOOT CONFIRM STATUS ")
        t4 = time.monotonic()
        freshness = disconnect and reconnect and before_up >= 0 and after_up >= 0 and after_up < before_up
        sample.update({"post_reboot_pong": after, "post_reboot_status": after_status,
                       "confirmation_status": confirm,
                       "freshness": {"disconnect": disconnect, "reconnect": reconnect,
                                     "before_up_ms": before_up, "after_up_ms": after_up,
                                     "uptime_reset": after_up < before_up},
                       "t0": t0, "t1": t1, "t2": t2, "t3": t3, "t4": t4,
                       "components_s": {"master_restore_live": t1-t0,
                                        "cdc_ready_live": t2-t1,
                                        "route_to_pong": t3-t2,
                                        "status": t4-t3,
                                        "reboot_to_status": t4-t0}})
        if not freshness:
            raise SessionError("freshness requires disconnect, reconnect, and reset uptime")
        if parse_fields(str(confirm["text"])).get("confirmed") != "1":
            raise SessionError(f"confirmed image lost confirmation: {confirm}")
        after_fields = parse_fields(str(after["text"]))
        for key, expected in (("fw", expected_marker), ("fwid", expected_fwid),
                              ("image_sha", expected_image_sha)):
            if expected is not None and after_fields.get(key) != expected:
                raise SessionError(f"post-reboot {key} mismatch: {after}")
        sample["valid"] = True
    except Exception as exc:
        sample["error"] = f"{type(exc).__name__}: {exc}"
    return sample


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True, type=Path)
    parser.add_argument("--fusion-port")
    parser.add_argument("--inventory-only", action="store_true")
    parser.add_argument("--nodes", nargs="+", choices=NODES, default=list(NODES),
                        help="subset needing new samples; readiness still requires all ten")
    parser.add_argument("--per-node-timeout-s", type=float, default=60)
    parser.add_argument("--ready-timeout-s", type=float, default=180)
    parser.add_argument("--restore-max-s", type=float, required=True)
    parser.add_argument("--prepare-confirm-max-s", type=float, required=True)
    parser.add_argument("--expected-marker")
    parser.add_argument("--expected-fwid")
    parser.add_argument("--expected-image-sha")
    args = parser.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=False)
    result = {"schema": "biospur-ota-timing-rehearsal-v2", "status": "IN_PROGRESS",
              "started": datetime.now(timezone.utc).astimezone().isoformat(),
              "inventory_samples": [], "timing_samples": []}
    diagnostics = {"nodes": {node: {"present": False, "connected": False,
                                     "subscribed": False, "ping_ok": False,
                                     "last_error": None, "first_seen": None,
                                     "last_seen": None} for node in NODES},
                   "unexpected_peers": []}
    result["readiness_diagnostics"] = diagnostics
    channel = None
    with (args.out_dir / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1) as log:
        try:
            channel = ThreadedLineChannel(resolve_fusion_port(args.fusion_port), log, "FUSION",
                decoded_queue_records=65536, backlog_red_records=8192,
                raw_backlog_red_bytes=8192, stall_red_s=1)
            channel.transport_mode = "binary"; channel.text_pending.clear()
            result["port"] = channel.port
            result["decode_before_send"] = decode_guard(channel, 15)
            first_master = wait_master_status(channel)
            result["initial_master_status"] = first_master
            if parse_fields(first_master).get("marker") != MASTER_MARKER:
                raise SessionError(f"production Master mismatch: {first_master}")
            if not wait_stable(channel, args.ready_timeout_s,
                               result["inventory_samples"], diagnostics):
                raise SessionError("exact ten-peer gate was not stable for ten samples")
            result["readiness_gate"] = "PASS"
            if args.inventory_only:
                result["status"] = "INVENTORY_PASS"
                return 0
            for node in args.nodes:
                # A prior invalid reboot cannot authorize the next one. Wait for
                # the exact stable fleet again before any subsequent REBOOT.
                node_gate: list[dict] = []
                if not wait_stable(channel, args.ready_timeout_s, node_gate, diagnostics):
                    result["inventory_samples"].extend(node_gate)
                    result["timing_samples"].append({"node": node, "valid": False,
                        "error": "fleet did not regain exact stable state; REBOOT not sent"})
                    break
                result["inventory_samples"].extend(node_gate)
                sample = run_one_reboot(channel, node, node_gate[-1], args.per_node_timeout_s,
                                        args.expected_marker, args.expected_fwid,
                                        args.expected_image_sha)
                result["timing_samples"].append(sample)
            result["summary"] = qualification_summary(result["timing_samples"],
                args.restore_max_s, args.prepare_confirm_max_s)
            result["status"] = result["summary"]["gate"]
            return 0 if result["status"] == "PASS" else 2
        except Exception as exc:
            result["status"] = "BLOCKED"
            result["error"] = f"{type(exc).__name__}: {exc}"
            result["summary"] = qualification_summary(result["timing_samples"],
                args.restore_max_s, args.prepare_confirm_max_s)
            return 2
        finally:
            if channel:
                channel.close()
            result["ended"] = datetime.now(timezone.utc).astimezone().isoformat()
            (args.out_dir / "result.json").write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
