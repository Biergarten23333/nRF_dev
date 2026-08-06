#!/usr/bin/env python3
"""Extract connection-epoch queue deltas and per-link QoS from S3 evidence."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fusion_session import parse_fields

LOG = Path("B306_Part/logs/b306_v34_20260803/S3_verify/fusion_cdc.log")
OUT = Path("B306_Part/logs/b306_v34_20260803/S3_QUEUE_QOS_ANALYSIS.json")
START, END = 338049.50, 338079.55


def longest_epoch(rows: list[dict]) -> list[dict]:
    epochs: list[list[dict]] = [[]]
    for row in rows:
        if epochs[-1] and row["node_ms"] <= epochs[-1][-1]["node_ms"]:
            epochs.append([])
        epochs[-1].append(row)
    return max(epochs, key=len)


def main() -> None:
    queues: dict[str, list[dict]] = {}
    qos: dict[str, list[dict]] = {}
    for raw in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = raw.split(maxsplit=2)
        if len(parts) < 3:
            continue
        try:
            host = float(parts[1])
        except ValueError:
            continue
        if not START <= host <= END:
            continue
        match = re.search(r"FUSION_(QUEUE|QOS) ", raw)
        if not match:
            continue
        line = raw[match.start():]
        f = parse_fields(line)
        node = f.get("name")
        if not node:
            continue
        if match.group(1) == "QUEUE":
            queues.setdefault(node, []).append({
                "host_mono": host, "node_ms": int(f["node_ms"]),
                **{k: int(f[k]) for k in ("q_drop_imu", "q_drop_uwb", "q_drop_ctl",
                                           "q_hwm_imu", "q_hwm_uwb", "q_hwm_ctl")},
            })
        else:
            qos.setdefault(node, []).append({
                "host_mono": host,
                **{k: int(f[k]) for k in ("window_ms", "reports", "event_gaps", "crc_ok",
                                           "crc_error", "nak", "rx_timeout")},
            })

    nodes = sorted(set(queues) | set(qos))
    result = {"window_host_mono": [START, END], "nodes": {}}
    for node in nodes:
        epoch = longest_epoch(queues.get(node, []))
        qrow = {}
        if epoch:
            qrow = {
                "records": len(epoch), "first": epoch[0], "last": epoch[-1],
                "q_drop_imu_delta": (epoch[-1]["q_drop_imu"] - epoch[0]["q_drop_imu"]) & 0xffffffff,
                "q_drop_uwb_delta": (epoch[-1]["q_drop_uwb"] - epoch[0]["q_drop_uwb"]) & 0xffffffff,
                "q_drop_ctl_delta": (epoch[-1]["q_drop_ctl"] - epoch[0]["q_drop_ctl"]) & 0xffffffff,
            }
        qrows = qos.get(node, [])
        sums = {k: sum(r[k] for r in qrows) for k in
                ("window_ms", "reports", "event_gaps", "crc_ok", "crc_error", "nak", "rx_timeout")}
        crc_total = sums["crc_ok"] + sums["crc_error"]
        sums["crc_error_ratio"] = sums["crc_error"] / crc_total if crc_total else None
        sums["qos_windows"] = len(qrows)
        result["nodes"][node] = {"queue": qrow, "qos": sums}
    OUT.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
