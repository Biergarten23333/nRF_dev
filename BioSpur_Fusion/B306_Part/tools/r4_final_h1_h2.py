#!/usr/bin/env python3
"""dk-v29 connection-create proof and bounded ten-link service gate."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from batch_g_overnight import NODES
from fusion_session import SessionError, parse_fields
from v32_service_gate import ServiceGate


EXPECTED_MASTER = "dk-fusion-imu-relay-v29"


def extract_ci_proof(raw_path: Path) -> dict[str, object]:
    lines = raw_path.read_text(encoding="utf-8", errors="replace").splitlines()
    boundaries = [
        index for index, line in enumerate(lines)
        if "FUSION_TX SPACING ON" in line
    ]
    if not boundaries:
        raise SessionError("missing archived SPACING ON boundary")
    start = boundaries[-1]
    events: list[dict[str, object]] = []
    for line in lines[start + 1 :]:
        if "FUSION_RX FUSION_CONNECTED " in line:
            fields = parse_fields(line.split("FUSION_RX ", 1)[1])
            events.append(
                {
                    "name": fields.get("name", ""),
                    "connected_line": line,
                    "ci_current_line": None,
                    "interval_units": None,
                    "interval_us": None,
                    "disconnect_line": None,
                    "outcome": "PENDING",
                }
            )
            continue
        if "FUSION_RX FUSION_DISCONNECTED " in line:
            decoded = line.split("FUSION_RX ", 1)[1]
            fields = parse_fields(decoded)
            name = fields.get("name", "")
            target = next(
                (
                    event for event in reversed(events)
                    if event["name"] == name and event["outcome"] == "PENDING"
                ),
                None,
            )
            if target is not None:
                target["disconnect_line"] = line
                target["outcome"] = (
                    "DISCONNECTED_AFTER_CI"
                    if target["ci_current_line"] is not None
                    else "FAILED_BEFORE_GATT"
                )
            continue
        if "FUSION_RX FUSION_CI_CURRENT " not in line:
            continue
        decoded = line.split("FUSION_RX ", 1)[1]
        fields = parse_fields(decoded)
        name = fields.get("name", "")
        target = next(
            (
                event for event in reversed(events)
                if event["name"] == name and event["ci_current_line"] is None
            ),
            None,
        )
        if target is None:
            raise SessionError(f"orphan CI_CURRENT after SPACING ON: {line}")
        target["ci_current_line"] = line
        target["interval_units"] = int(fields.get("interval_units", "-1"), 0)
        target["interval_us"] = int(fields.get("interval_us", "-1"), 0)
        target["outcome"] = "CI_PROVEN"

    successful_events = [
        event for event in events if event["ci_current_line"] is not None
    ]
    failed_before_gatt = [
        event for event in events if event["outcome"] == "FAILED_BEFORE_GATT"
    ]
    pending_events = [
        event for event in events if event["outcome"] == "PENDING"
    ]
    initial_events = successful_events[: len(NODES)]
    names = [str(event["name"]) for event in initial_events]
    errors: list[str] = []
    if len(initial_events) != len(NODES):
        errors.append(f"initial establishment count={len(initial_events)} expected=10")
    if set(names) != set(NODES):
        errors.append(f"initial establishment names={sorted(names)}")
    for index, event in enumerate(successful_events, 1):
        if event["interval_units"] != 40 or event["interval_us"] != 50000:
            errors.append(
                f"successful event {index} {event['name']} CI={event['interval_units']}/"
                f"{event['interval_us']}"
            )
    if pending_events:
        errors.append(f"unresolved connection attempts={len(pending_events)}")
    forbidden = [
        line for line in lines[start + 1 :]
        if (
            ("FUSION_CI_CURRENT " in line or "FUSION_CI_UPDATED " in line)
            and ("interval_units=24 " in line or "interval_us=30000 " in line)
        )
    ]
    if forbidden:
        errors.append(f"30 ms phase lines={len(forbidden)}")
    result = {
        "spacing_on_boundary_line": lines[start],
        "initial_establishment_count": len(initial_events),
        "all_establishment_count": len(events),
        "successful_establishment_count": len(successful_events),
        "failed_before_gatt_count": len(failed_before_gatt),
        "initial_names": names,
        "events": events,
        "failed_before_gatt_attempts": failed_before_gatt,
        "forbidden_30ms_lines": forbidden,
        "verdict": "PASS" if not errors else "FAIL",
        "errors": errors,
    }
    if errors:
        raise SessionError("CI-from-creation proof failed: " + "; ".join(errors))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--ci-proof-source", type=Path)
    args = parser.parse_args()
    gate = ServiceGate(
        args.evidence_root,
        args.port,
        nodes=tuple(NODES),
        expected_master_marker=EXPECTED_MASTER,
        max_redraws_per_link=1,
    )
    try:
        gate.open()
        gate.evidence["preregistration"] = {
            "expected": "ten FULL links near 20 reports/s with zero redraws",
            "allowed_remedy": "one redraw per non-FULL link",
            "stop": "any non-FULL link after its one redraw stops before R4",
        }
        gate.evidence["fleet_initial"] = gate.fleet_ready(timeout_s=240.0)
        if args.ci_proof_source is None:
            gate.rebuild_production_spacing()
            time.sleep(0.75)
            ci_source = args.evidence_root / "fusion_cdc.log"
        else:
            ci_source = args.ci_proof_source
        gate.evidence["ci_from_creation_source"] = str(ci_source)
        gate.evidence["ci_from_creation"] = extract_ci_proof(ci_source)
        gate.checkpoint()

        current = gate.measure("initial_v29_service", duration_s=15.0)
        while True:
            non_full = [
                node for node in NODES
                if current["table"][node]["class"] != "FULL"
            ]
            eligible = [
                node for node in non_full
                if int(gate.evidence["redraws"][node]) == 0
            ]
            if not eligible:
                break
            for node in eligible:
                gate.redraw(node)
                gate.fleet_ready(timeout_s=120.0)
            current = gate.measure("after_single_allowed_redraws", duration_s=15.0)

        time.sleep(0.75)
        if args.ci_proof_source is None:
            gate.evidence["ci_from_creation_final"] = extract_ci_proof(
                args.evidence_root / "fusion_cdc.log"
            )
        else:
            gate.evidence["ci_from_creation_final"] = gate.evidence["ci_from_creation"]
        gate.evidence["final_table"] = current["table"]
        failures = {
            node: row for node, row in current["table"].items()
            if row["class"] != "FULL"
        }
        gate.evidence["verdict"] = "PASS" if not failures else "STOP"
        gate.evidence["failures"] = failures
        gate.checkpoint()
        print(json.dumps({
            "verdict": gate.evidence["verdict"],
            "redraws": gate.evidence["redraws"],
            "service": {
                node: {
                    "reports_per_s": row["reports_per_s"],
                    "class": row["class"],
                }
                for node, row in current["table"].items()
            },
            "ci_initial": gate.evidence["ci_from_creation"],
        }, indent=2, sort_keys=True))
        return 0 if not failures else 2
    except Exception as exc:
        gate.evidence["verdict"] = "STOP"
        gate.evidence["error"] = f"{type(exc).__name__}: {exc}"
        if gate.root.exists():
            gate.checkpoint()
        print(gate.evidence["error"])
        return 2
    finally:
        gate.close()


if __name__ == "__main__":
    raise SystemExit(main())
