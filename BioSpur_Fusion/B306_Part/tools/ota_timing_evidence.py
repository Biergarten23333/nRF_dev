#!/usr/bin/env python3
"""Versioned offline OTA timing evidence evaluation and registry helpers."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path

SCHEMA = "biospur-ota-timing-offline-v1"
REGISTRY_SCHEMA = "biospur-ota-timing-registry-v1"
LINE = re.compile(r"^(\d+\.\d+) (\d+\.\d+) (FUSION_(?:TX|RX)) (.*)$")


@dataclass
class Record:
    epoch: float
    monotonic: float
    direction: str
    payload: str
    raw: str


def records(path: Path) -> list[Record]:
    result = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = LINE.match(raw)
        if match:
            result.append(Record(float(match[1]), float(match[2]), match[3], match[4], raw))
    return result


def field(text: str, name: str) -> str | None:
    match = re.search(rf"(?:^| ){re.escape(name)}=([^ ]+)", text)
    return match.group(1) if match else None


def first(rows: list[Record], start: int, predicate) -> tuple[int, Record] | None:
    for index in range(start, len(rows)):
        if predicate(rows[index]):
            return index, rows[index]
    return None


def evaluate_reboot(path: Path, node: str) -> dict:
    rows = records(path)
    reboot = first(rows, 0, lambda r: r.payload == f"{node} REBOOT" and r.direction == "FUSION_TX")
    support: dict[str, str] = {}
    missing = []
    if reboot is None:
        return {"schema": SCHEMA, "node": node, "verdict": "INVALID_MISSING_REBOOT",
                "support": support}
    index, command = reboot
    support["t0_reboot_command"] = command.raw
    queued = first(rows, index + 1, lambda r: f"name={node} " in r.payload and
                   "text=REBOOT QUEUED" in r.payload)
    if queued:
        support["reboot_queued"] = queued[1].raw
    else:
        missing.append("REBOOT_QUEUED")
    disconnect = first(rows, index + 1, lambda r:
        (r.payload.startswith(f"FUSION_DISCONNECTED name={node} ") or
         ("FUSION_COMMAND_REJECT" in r.payload and f"line={node} " in r.payload)))
    if disconnect:
        support["disconnect_or_route_failure"] = disconnect[1].raw
    else:
        missing.append("DISCONNECT")
    pong = first(rows, (disconnect or (index, command))[0] + 1, lambda r:
                 f"FUSION_REPLY " in r.payload and f"name={node} " in r.payload and
                 f"text=PONG name={node} " in r.payload)
    if pong:
        support["post_reboot_pong"] = pong[1].raw
    else:
        missing.append("PONG")
    before_statuses = [r for r in rows[:index] if f"name={node} " in r.payload and
                       "text=STATUS " in r.payload and field(r.payload, "up_ms")]
    after_status = first(rows, (pong or (index, command))[0] + 1, lambda r:
                         f"name={node} " in r.payload and "text=STATUS " in r.payload and
                         field(r.payload, "up_ms") is not None)
    if before_statuses:
        support["pre_reboot_status"] = before_statuses[-1].raw
    if after_status:
        support["post_reboot_status"] = after_status[1].raw
    if not before_statuses or not after_status:
        missing.append("UPTIME")
    elif int(field(after_status[1].payload, "up_ms")) >= int(field(before_statuses[-1].payload, "up_ms")):
        missing.append("UPTIME_RESET")
    confirmed = first(rows, (after_status or pong or (index, command))[0] + 1, lambda r:
                      f"name={node} " in r.payload and
                      "text=BOOT CONFIRM STATUS confirmed=1" in r.payload)
    if confirmed:
        support["confirmed"] = confirmed[1].raw
    else:
        missing.append("CONFIRMATION")
    ordered = [command.monotonic]
    for item in (queued, disconnect, pong, after_status, confirmed):
        if item:
            ordered.append(item[1].monotonic)
    if ordered != sorted(ordered):
        missing.append("MONOTONIC")
    verdict = "VALID_SALVAGED" if not missing else "INVALID_MISSING_" + "_".join(dict.fromkeys(missing))
    return {"schema": SCHEMA, "node": node, "verdict": verdict,
            "evidence_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "support": support}


def classify_control(*, command_tx: bool, tx_err: int | None, rejected: bool,
                     ctrl_before: int | None, ctrl_after: int | None,
                     raw_reply: bool, correlation_matches: bool,
                     all_peers_fail: bool = False) -> str:
    if all_peers_fail:
        return "MASTER_GLOBAL_FAILURE"
    if not command_tx:
        return "HOST_MASTER_ADMISSION_FAILURE"
    if rejected or tx_err not in (0, None):
        return "MASTER_ROUTING_LINK_STATE_FAILURE"
    if ctrl_before is not None and ctrl_after == ctrl_before:
        return "DOWNLINK_DID_NOT_REACH_B306"
    if ctrl_before is not None and ctrl_after is not None and ctrl_after > ctrl_before and not raw_reply:
        return "B306_CONTROL_WORKER_OR_RESPONSE_FAILURE"
    if raw_reply and not correlation_matches:
        return "MASTER_CORRELATION_OR_HOST_FILTER_FAILURE"
    if raw_reply and correlation_matches:
        return "CONTROL_PATH_HEALTHY"
    return "INCOMPLETE_EVIDENCE"


def registry_add(registry: dict, sample: dict) -> dict:
    identity = {key: sample[key] for key in
                ("master_firmware", "b306_firmware", "tool_schema", "configuration")}
    if registry.get("schema") not in (None, REGISTRY_SCHEMA):
        raise ValueError("unsupported registry schema")
    expected = registry.get("configuration_identity")
    if expected is not None and expected != identity:
        raise ValueError("mixed firmware/tool configuration")
    result = dict(registry)
    result.update({"schema": REGISTRY_SCHEMA, "configuration_identity": identity})
    samples = dict(result.get("samples", {}))
    node = sample["node"]
    if node in samples and samples[node].get("evidence_sha256") != sample.get("evidence_sha256"):
        raise ValueError("node already has different evidence")
    samples[node] = sample
    result["samples"] = samples
    return result


def targeted_recovery_pass(evidence: dict, expected_nodes: set[str]) -> bool:
    peers = evidence.get("peers", {})
    return (set(peers) == expected_nodes and
            all(row.get("connected") == "1" and row.get("subscribed") == "1"
                for row in peers.values()) and
            evidence.get("target_ping_successes") == 3 and
            evidence.get("target_status") is True and
            evidence.get("target_streaming") is True and
            evidence.get("other_peer_failures") == 0)


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--nodes", nargs="+", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    value = {"schema": SCHEMA, "source": str(args.log),
             "source_sha256": hashlib.sha256(args.log.read_bytes()).hexdigest(),
             "nodes": [evaluate_reboot(args.log, node) for node in args.nodes]}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
