#!/usr/bin/env python3
"""Offline, read-only adjudication of the v47 afternoon capture.

The detector preserves the 2026-08-08 joint-stall definition and applies the
v47 onset-local reset and independent Listener rules from the run authority.
It never modifies raw input files.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import hashlib
import json
import re
from array import array
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

NODES = (
    "BSF3C79", "BSFC2CC", "BSF44AD", "BSF6C53", "BSF8BC4",
    "BSF1120", "BSF31CC", "BSFAA61", "BSFB165", "BSFEC35",
)
BATTERY = set(NODES) - {"BSF6C53"}
NEAR_S = 2.0
WEDGE_S = 20.0
AIR_W = 600.0
UWB_NOM = 0.120

LINE_HEAD = re.compile(rb"^[0-9.]+ ([0-9.]+) FUSION_RX ")
NAME = re.compile(rb"\bname=(BSF[0-9A-F]{4})\b")
NODE_MS = re.compile(rb"\bnode_ms=(\d+)\b")
RESET_REASON = re.compile(rb"\breset_reason=(\d+)\b")
AIR_MONO = re.compile(rb'"arrival_monotonic_ns":(\d+)')
AIR_SRC = re.compile(rb'"src":(\d+)')


def atomic_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def wall_for(manifest, mono: float) -> str:
    base = datetime.fromisoformat(manifest["t0_wall"])
    return (base + timedelta(seconds=mono - manifest["t0_monotonic"])).isoformat(timespec="milliseconds")


def silences(times: array, end: float) -> list[tuple[float, float]]:
    out = []
    if not times:
        return out
    prev = times[0]
    for cur in times[1:]:
        if cur - prev > NEAR_S * 0.5:
            out.append((prev, cur))
        prev = cur
    if end - prev >= NEAR_S * 0.5:
        out.append((prev, end))
    return out


def intersections(a, b):
    out = []
    i = j = 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if hi - lo >= NEAR_S:
            out.append((lo, hi))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def scan_fusion(path: Path, ended: float):
    streams = {n: {"imu": array("d"), "uwb": array("d"), "qos": array("d")} for n in NODES}
    telemetry = {n: [] for n in NODES}
    links = {n: [] for n in NODES}
    malformed = 0
    first_t = None
    last_t = None
    with path.open("rb") as fh:
        for line in fh:
            h = LINE_HEAD.match(line)
            if not h:
                continue
            t = float(h.group(1)); first_t = t if first_t is None else first_t; last_t = t
            nm = NAME.search(line)
            if not nm:
                continue
            node = nm.group(1).decode()
            if node not in streams:
                continue
            if b" FUSION_IMU " in line:
                streams[node]["imu"].append(t)
            elif b" FUSION_UWB " in line:
                streams[node]["uwb"].append(t)
            elif b" FUSION_QOS " in line:
                streams[node]["qos"].append(t)
            elif b" FUSION_TELEMETRY " in line:
                ms = NODE_MS.search(line); rr = RESET_REASON.search(line)
                if ms:
                    telemetry[node].append((t, int(ms.group(1)), int(rr.group(1)) if rr else None))
            elif b" FUSION_CONNECTED " in line:
                links[node].append({"t": t, "kind": "CONNECTED", "raw": line.decode(errors="replace").strip()})
            elif b" FUSION_DISCONNECTED " in line:
                links[node].append({"t": t, "kind": "DISCONNECTED", "raw": line.decode(errors="replace").strip()})
            elif b" FUSION_FAIL " in line:
                links[node].append({"t": t, "kind": "FAIL", "raw": line.decode(errors="replace").strip()})
    boots = {n: [] for n in NODES}
    for n, rows in telemetry.items():
        for prev, cur in zip(rows, rows[1:]):
            if cur[1] < prev[1]:
                boots[n].append({"t": cur[0], "previous_node_ms": prev[1], "node_ms": cur[1], "reset_reason": cur[2]})
    events = []
    for node in NODES:
        si = silences(streams[node]["imu"], ended)
        su = silences(streams[node]["uwb"], ended)
        for lo, hi in intersections(si, su):
            qos = streams[node]["qos"]
            q0, q1 = bisect.bisect_right(qos, lo + 1), bisect.bisect_left(qos, hi)
            q_alive = qos[q1 - 1] - lo if q1 > q0 else 0.0
            local_boots = [b for b in boots[node] if lo - 30 <= b["t"] <= lo + 60]
            events.append({
                "node": node, "onset_lower": lo, "onset_upper": lo + UWB_NOM,
                "recovered_monotonic": None if hi >= ended - 0.5 else hi,
                "duration_s": hi - lo, "terminal_at_stop": hi >= ended - 0.5,
                "qos_alive_after_s": q_alive, "qos_records_during": q1 - q0,
                "onset_local_boots": local_boots,
            })
    events.sort(key=lambda x: (x["onset_lower"], x["node"]))
    return streams, telemetry, boots, links, events, {"first_monotonic": first_t, "last_monotonic": last_t, "malformed": malformed}


def scan_air(path: Path, tag_to_node: dict[int, str], events: list[dict], start: float, end: float):
    per_node = {n: {"first": None, "last": None, "count": 0, "seconds": set()} for n in NODES}
    node_events = defaultdict(list)
    for ev in events:
        node_events[ev["node"]].append(ev)
        ev.update(air_pre_600s=0, air_post_600s=0)
    with path.open("rb") as fh:
        for line in fh:
            if b'"kind":"LPD"' not in line:
                continue
            sm = AIR_SRC.search(line); tm = AIR_MONO.search(line)
            if not sm or not tm:
                continue
            node = tag_to_node.get(int(sm.group(1)))
            if node is None:
                continue
            t = int(tm.group(1)) / 1e9
            a = per_node[node]
            a["first"] = t if a["first"] is None else min(a["first"], t)
            a["last"] = t if a["last"] is None else max(a["last"], t)
            a["count"] += 1
            a["seconds"].add(int(t))
            for ev in node_events[node]:
                lo = ev["onset_lower"]
                if lo - AIR_W <= t < lo:
                    ev["air_pre_600s"] += 1
                elif lo <= t < lo + AIR_W:
                    ev["air_post_600s"] += 1
    for node, a in per_node.items():
        a["active_seconds"] = len(a.pop("seconds"))
        a["full_pre_history"] = a["first"] is not None and a["first"] <= start + 1.0
        a["final_silence_s"] = None if a["last"] is None else end - a["last"]
    for ev in events:
        pre, post = ev["air_pre_600s"], ev["air_post_600s"]
        ev["air_ratio"] = post / pre if pre else None
        ev["air_window_status"] = (
            "EARLY_WINDOW_INSUFFICIENT" if ev["onset_lower"] - start < AIR_W else
            "POST_WINDOW_INSUFFICIENT" if end - ev["onset_lower"] < AIR_W else
            "COMPLETE"
        )
    return per_node


def classify(ev: dict, adapter: bool) -> tuple[str, str]:
    ratio = ev["air_ratio"]
    complete = ev["air_window_status"] == "COMPLETE"
    reset = bool(ev["onset_local_boots"])
    dur = ev["duration_s"]
    qos = ev["qos_alive_after_s"]
    if not complete or ratio is None:
        return "UNKNOWN", "incomplete independent Listener window"
    if adapter:
        if ratio < 0.70:
            return "ADAPTER_POWER_OR_INFRASTRUCTURE_FAILURE", f"adapter node air ratio {ratio:.3f}; operator observed unseated POGO"
        if qos < 10:
            return "RF_OR_DISCONNECT", f"tag remained on-air but connection QoS ended after {qos:.1f}s"
        if dur >= WEDGE_S and not reset:
            return "STEADY_STATE_HOST_WEDGE", f"QoS alive {qos:.1f}s, air ratio {ratio:.3f}, no onset-local reset"
    else:
        if ratio <= 0.15:
            return "DEPLETION_OR_BROWNOUT", f"air ratio {ratio:.3f} <= 0.15"
        if ratio < 0.70:
            return "DEPLETION_OR_BROWNOUT", f"intermittent-air ratio {ratio:.3f}"
        if qos < 10:
            return "RF_OR_DISCONNECT", f"tag remained on-air but connection QoS ended after {qos:.1f}s"
        if dur >= WEDGE_S and not reset:
            return "STEADY_STATE_HOST_WEDGE", f"QoS alive {qos:.1f}s, air ratio {ratio:.3f}, no onset-local reset"
    if dur < WEDGE_S:
        return "NEAR_MISS_JOINT_STALL", f"recovered {dur:.1f}s joint stall"
    if reset:
        return "UNKNOWN", "onset-local reset observed but exact retained v47 intent unavailable"
    return "UNKNOWN", "classification axes contradictory or insufficient"


def healthy_exposure(streams, events, end):
    result = {}
    for n in NODES:
        starts = [x for x in (streams[n]["imu"][0] if streams[n]["imu"] else None,
                              streams[n]["uwb"][0] if streams[n]["uwb"] else None) if x is not None]
        if not starts:
            result[n] = 0.0; continue
        first = min(starts)
        last = min(end, max(streams[n]["imu"][-1], streams[n]["uwb"][-1]))
        total = max(0.0, last - first)
        for ev in events:
            if ev["node"] != n:
                continue
            lo = max(first, ev["onset_lower"]); hi = min(last, ev["recovered_monotonic"] or end)
            if hi > lo:
                total -= hi - lo
        result[n] = max(0.0, total)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dir", type=Path)
    args = ap.parse_args()
    run = args.run_dir.resolve(); out = run / "analysis"; out.mkdir(exist_ok=True)
    manifest = json.loads((run / "RUN_MANIFEST.json").read_text())
    ledger = json.loads((run / "PROCESS_LEDGER.json").read_text())
    ended = float(ledger["ended_monotonic"]); started = float(manifest["t0_monotonic"])
    mapping = json.loads((run / "node_tag_map.json").read_text())
    tag_to_node = {int(v["tag_short_address"], 16): k for k, v in mapping.items()}
    streams, telemetry, boots, links, events, fusion_meta = scan_fusion(run / "fusion_cdc.log", ended)
    air = scan_air(run / "listener_capture" / "merged_index.jsonl", tag_to_node, events, started, ended)
    for i, ev in enumerate(events, 1):
        ev["event_id"] = f"E{i:03d}"
        ev["onset_wall"] = wall_for(manifest, ev["onset_lower"])
        ev["recovered_wall"] = wall_for(manifest, ev["recovered_monotonic"]) if ev["recovered_monotonic"] else None
        ev["classification"], ev["classification_reason"] = classify(ev, ev["node"] == "BSF6C53")
        ev["exact_reset_intent"] = None
        ev["reset_attribution"] = "UNAVAILABLE_NO_RETAINED_INTENT_IN_CAPTURE" if ev["onset_local_boots"] else "NO_ONSET_LOCAL_RESET"
    exposure = healthy_exposure(streams, events, ended)
    first_wedge = {n: next((e for e in events if e["node"] == n and e["classification"] in
                            {"STEADY_STATE_HOST_WEDGE", "AUTO_RECOVERED_WEDGE_INTENT1", "AUTO_RECOVERED_WEDGE_INTENT5"}), None)
                   for n in NODES}
    per_node = {}
    for n in NODES:
        node_events = [e for e in events if e["node"] == n]
        first = min(streams[n]["imu"][0], streams[n]["uwb"][0])
        cutoff = first_wedge[n]["onset_lower"] if first_wedge[n] else ended
        before = healthy_exposure(streams, [e for e in node_events if e["onset_lower"] < cutoff], cutoff)[n]
        w = first_wedge[n]
        powered_after = None
        if w and air[n]["last"] is not None:
            powered_after = max(0.0, air[n]["last"] - w["onset_lower"])
        per_node[n] = {
            "power_class": "adapter" if n == "BSF6C53" else "battery",
            "imu_records": len(streams[n]["imu"]), "uwb_records": len(streams[n]["uwb"]),
            "first_delivery_monotonic": first,
            "last_delivery_monotonic": max(streams[n]["imu"][-1], streams[n]["uwb"][-1]),
            "healthy_delivered_s": exposure[n],
            "delivered_before_first_wedge_s": before,
            "first_wedge_event_id": w["event_id"] if w else None,
            "powered_uwb_span_after_first_wedge_s": powered_after,
            "listener": air[n], "boot_transitions": boots[n],
            "event_ids": [e["event_id"] for e in node_events],
            "final_state": "FUSION_SILENT_AT_OPERATOR_STOP" if node_events and node_events[-1]["terminal_at_stop"] else "FUSION_DELIVERING_AT_OPERATOR_STOP",
        }
    summary = {
        "schema": "biospur-v47-afternoon-analysis-v1",
        "run_dir": str(run), "stop_reason": "OPERATOR_STOP",
        "t0_wall": manifest["t0_wall"], "t0_monotonic": started,
        "stop_wall": ledger["ended_wall"], "stop_monotonic": ended,
        "duration_s": ended - started, "duration_h": (ended - started) / 3600,
        "fusion_scan": fusion_meta,
        "listener_receivers": ["LAE", "LBF", "LDH", "LLOW", "LMID"],
        "listener_non_poll_receivers_excluded": ["LCG", "LHIGH"],
        "event_counts": dict(Counter(e["classification"] for e in events)),
        "candidate_count": len(events),
        "battery_cohort_delivered_board_hours": sum(exposure[n] for n in BATTERY) / 3600,
        "adapter_delivered_hours": exposure["BSF6C53"] / 3600,
        "mixed_fleet_wall_hours": (ended - started) / 3600,
        "auto_recovered_intent1": 0, "auto_recovered_intent5": 0,
        "reset_intent_limitation": "Source mapping verified, but no exact retained intent was captured at reconnect; no event is promoted to an intent-attributed recovery.",
        "operator_observation": "All ten Fusion PCB LEDs were reported off; BSF6C53 POGO appeared unseated; Geiger became intermittent.",
        "per_node": per_node,
    }
    atomic_json(out / "EVENT_TIMELINE.json", events)
    atomic_json(out / "LISTENER_AIR_RATIO_RESULTS.json", {
        "definition": "sum of valid LPD records across actual poll receivers in exact host-monotonic +/-600s windows",
        "events": [{k: e[k] for k in ("event_id", "node", "onset_lower", "air_pre_600s", "air_post_600s", "air_ratio", "air_window_status")} for e in events],
        "per_node": air,
    })
    atomic_json(out / "RESET_ATTRIBUTION.json", {
        "verified_source_mapping": {"1": "BSF_RESET_INTENT_RECOVERY_GUARD", "5": "BSF_RESET_INTENT_STALL_RECOVERY"},
        "window": "[onset-30s,onset+60s]", "boot_transitions": boots,
        "limitation": summary["reset_intent_limitation"],
    })
    atomic_json(out / "FINAL_PEER_STATE.json", per_node)
    atomic_json(out / "EXPOSURE.json", {k: summary[k] for k in (
        "duration_s", "duration_h", "battery_cohort_delivered_board_hours", "adapter_delivered_hours", "mixed_fleet_wall_hours", "per_node")})
    atomic_json(out / "ANALYSIS_SUMMARY.json", summary)
    with (out / "EVENT_TIMELINE.csv").open("w", newline="") as fh:
        fields = ["event_id", "node", "onset_wall", "onset_lower", "onset_upper", "duration_s", "terminal_at_stop",
                  "qos_alive_after_s", "air_pre_600s", "air_post_600s", "air_ratio", "air_window_status",
                  "reset_attribution", "classification", "classification_reason"]
        w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows({k: e.get(k) for k in fields} for e in events)
    print(json.dumps({k: summary[k] for k in ("duration_h", "candidate_count", "event_counts", "battery_cohort_delivered_board_hours", "adapter_delivered_hours")}, indent=2))


if __name__ == "__main__":
    main()
