#!/usr/bin/env python3
"""Detect and redraw half-serviced Fusion links before the v32 R4 run.

This tool is deliberately separate from the OTA updater.  It operates only
after all ten application images have been verified and the fleet has been
deployed for the formal window.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from async_line_channel import ThreadedLineChannel
from batch_g_overnight import NODES
from coldstart_fusion_control import decode_guard
from fusion_session import FusionController, SessionError, parse_fields, resolve_fusion_port
from pre_ramp_hardening import request_list
from capacity_ramp import RecordingAssembler


MASTER_MARKER = "dk-fusion-imu-relay-v28"
FULL_MIN_REPORTS_PER_S = 19.0
HALF_MIN_REPORTS_PER_S = 8.0
HALF_MAX_REPORTS_PER_S = 15.0
MEASURE_MIN_S = 10.0
MAX_REDRAWS_PER_LINK = 3


def require_production_regime(
    *, spacing: str, spacing_us: int, spacing_generation: int,
    current_generation: int,
) -> None:
    """Refuse to apply D5/F4 thresholds outside their measured regime."""
    if (
        spacing != "ON"
        or spacing_us != 5000
        or spacing_generation != current_generation
    ):
        raise SessionError(
            "F4 classifier regime mismatch: "
            f"spacing={spacing} spacing_us={spacing_us} "
            f"spacing_generation={spacing_generation} "
            f"current_generation={current_generation}; expected ON/5000 "
            "at the current generation"
        )


def classify_service(
    rate_hz: float, *, spacing: str, spacing_us: int,
    spacing_generation: int, current_generation: int,
) -> str:
    """Classify the D5 signature only after the production-regime guard."""
    require_production_regime(
        spacing=spacing,
        spacing_us=spacing_us,
        spacing_generation=spacing_generation,
        current_generation=current_generation,
    )
    if rate_hz >= FULL_MIN_REPORTS_PER_S:
        return "FULL"
    if HALF_MIN_REPORTS_PER_S <= rate_hz <= HALF_MAX_REPORTS_PER_S:
        return "HALF"
    return "DEGRADED"


def summarize_qos(
    lines: list[str], duration_s: float, *, current_generation: int,
    nodes: tuple[str, ...] = tuple(NODES),
) -> dict[str, dict[str, object]]:
    """Use the device-reported QoS windows, not host arrival timestamps."""
    totals = {
        node: {
            "reports": 0,
            "window_ms": 0,
            "records": 0,
            "event_gaps": 0,
            "crc_ok": 0,
            "crc_error": 0,
            "nak": 0,
            "rx_timeout": 0,
        }
        for node in nodes
    }
    for line in lines:
        if not line.startswith("FUSION_QOS "):
            continue
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in totals:
            continue
        require_production_regime(
            spacing=fields.get("spacing", ""),
            spacing_us=int(fields.get("spacing_us", "-1"), 0),
            spacing_generation=int(fields.get("spacing_generation", "-1"), 0),
            current_generation=current_generation,
        )
        totals[node]["reports"] += int(fields.get("reports", "0"), 0)
        totals[node]["window_ms"] += int(fields.get("window_ms", "0"), 0)
        totals[node]["records"] += 1
        for key in ("event_gaps", "crc_ok", "crc_error", "nak", "rx_timeout"):
            totals[node][key] += int(fields.get(key, "0"), 0)

    result: dict[str, dict[str, object]] = {}
    for node, row in totals.items():
        window_ms = int(row["window_ms"])
        rate = float(row["reports"]) * 1000.0 / window_ms if window_ms else 0.0
        result[node] = {
            **row,
            "host_measure_s": duration_s,
            "reports_per_s": rate,
            "crc_error_ratio": (
                float(row["crc_error"])
                / (float(row["crc_ok"]) + float(row["crc_error"]))
                if int(row["crc_ok"]) + int(row["crc_error"]) else 0.0
            ),
            "rx_timeout_per_s": (
                float(row["rx_timeout"]) * 1000.0 / window_ms
                if window_ms else 0.0
            ),
            "class": classify_service(
                rate,
                spacing="ON",
                spacing_us=5000,
                spacing_generation=current_generation,
                current_generation=current_generation,
            ) if window_ms else "NO_DATA",
            "regime": {
                "spacing": "ON",
                "spacing_us": 5000,
                "spacing_generation": current_generation,
            },
        }
    return result


class ServiceGate:
    def __init__(
        self, root: Path, port: str | None,
        nodes: tuple[str, ...] = tuple(NODES),
        expected_master_marker: str = MASTER_MARKER,
        max_redraws_per_link: int = MAX_REDRAWS_PER_LINK,
    ) -> None:
        self.root = root
        self.port = port
        self.nodes = nodes
        self.expected_master_marker = expected_master_marker
        self.max_redraws_per_link = max_redraws_per_link
        self.raw = None
        self.channel = None
        self.evidence: dict[str, object] = {
            "master_marker_expected": expected_master_marker,
            "thresholds": {
                "full_min_reports_per_s": FULL_MIN_REPORTS_PER_S,
                "half_range_reports_per_s": [HALF_MIN_REPORTS_PER_S, HALF_MAX_REPORTS_PER_S],
                "measure_min_s": MEASURE_MIN_S,
                "max_redraws_per_link": max_redraws_per_link,
                "fleet_spacing_rebuilds": 1,
            },
            "placement": {
                "all_ten_powered": True,
                "bench_deployed": True,
                "stacked": False,
                "operator_confirmed": True,
            },
            "rounds": [],
            "nodes": list(nodes),
            "regime_contract": "spacing=ON spacing_us=5000 current generation",
            "redraws": {node: 0 for node in nodes},
        }

    def open(self) -> None:
        self.root.mkdir(parents=True, exist_ok=False)
        self.raw = (self.root / "fusion_cdc.log").open("x", encoding="utf-8", buffering=1)
        self.channel = ThreadedLineChannel(
            resolve_fusion_port(self.port), self.raw, "FUSION",
            backlog_red_records=8192, stall_red_s=1.0,
        )
        self.channel.transport_mode = "binary"
        self.channel.text_pending.clear()
        self.evidence["fusion_port"] = self.channel.port
        self.evidence["decode_before_send"] = decode_guard(self.channel, 15.0)
        self.channel.send("MASTER STATUS")
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line and line.startswith("FUSION_MASTER_STATUS "):
                fields = parse_fields(line)
                if fields.get("marker") != self.expected_master_marker:
                    raise SessionError(f"master marker mismatch: {line}")
                self.evidence["master_status"] = line
                return
        raise SessionError("missing Fusion Master status")

    def close(self) -> None:
        if self.channel is not None:
            self.evidence["host_drain"] = self.channel.health_snapshot()
            self.channel.close()
        if self.raw is not None:
            self.raw.close()

    def checkpoint(self) -> None:
        (self.root / "service_gate.json").write_text(
            json.dumps(self.evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def fleet_ready(self, timeout_s: float = 180.0) -> dict[str, object]:
        assert self.channel is not None
        deadline = time.monotonic() + timeout_s
        history: list[dict[str, object]] = []
        while time.monotonic() < deadline:
            listing = request_list(self.channel, RecordingAssembler(), {}, self.nodes)
            aggregate = listing["aggregate"]
            peers = listing["peers"]
            ready = {
                node for node, row in peers.items()
                if row.get("connected") == "1"
                and row.get("subscribed") == "1"
                and row.get("link_contract") == "PASS"
            }
            history.append({"aggregate": aggregate, "ready": sorted(ready)})
            if set(peers) == set(self.nodes) and ready == set(self.nodes):
                return {"listing": listing, "history": history}
            time.sleep(1.0)
        raise SessionError(f"fleet not 10/10 full link contract: {history[-3:]}")

    def measure(self, label: str, duration_s: float = 12.0) -> dict[str, object]:
        if duration_s < MEASURE_MIN_S:
            raise ValueError(f"measurement must be >= {MEASURE_MIN_S:.1f} s")
        assert self.channel is not None
        regime_listing = request_list(
            self.channel, RecordingAssembler(), {}, self.nodes
        )
        aggregate = regime_listing["aggregate"]
        current_generation = int(aggregate.get("spacing_generation", "-1"), 0)
        require_production_regime(
            spacing=aggregate.get("spacing", ""),
            spacing_us=int(aggregate.get("spacing_us", "-1"), 0),
            spacing_generation=current_generation,
            current_generation=current_generation,
        )
        # Discard complete old decoded records so only kind-7 windows observed
        # during this formal interval enter the table.
        boundary = self.channel.discard_pending(f"{label}_start")
        deadline = time.monotonic() + duration_s
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if line is not None:
                lines.append(line)
        table = summarize_qos(
            lines, duration_s,
            current_generation=current_generation,
            nodes=self.nodes,
        )
        row = {
            "label": label,
            "boundary": boundary,
            "regime_listing": regime_listing,
            "current_generation": current_generation,
            "table": table,
        }
        self.evidence["rounds"].append(row)
        self.checkpoint()
        return row

    def redraw(self, node: str) -> dict[str, object]:
        """Targeted B306 REBOOT creates one disconnect/reconnect redraw."""
        assert self.channel is not None
        count = int(self.evidence["redraws"][node])
        if count >= self.max_redraws_per_link:
            raise SessionError(f"redraw bound exhausted for {node}")
        controller = FusionController(self.channel, node, timeout_s=8.0, max_attempts=1)
        controller.ensure_bridge()
        telemetry = controller.reboot_preflight()
        self.evidence["redraws"][node] = count + 1
        self.checkpoint()
        return {"node": node, "redraw": count + 1, "telemetry": telemetry}

    def spacing(self, mode: str) -> str:
        assert self.channel is not None
        self.channel.send(f"SPACING {mode}")
        deadline = time.monotonic() + 120.0
        expected = "5000" if mode == "ON" else "7500"
        while time.monotonic() < deadline:
            line = self.channel.read(deadline)
            if not line or not line.startswith("FUSION_SPACING "):
                continue
            fields = parse_fields(line)
            if fields.get("state") == "FAILED":
                raise SessionError(line)
            if fields.get("state") in ("APPLIED", "UNCHANGED") and fields.get("mode") == mode and fields.get("applied_us") == expected:
                return line
        raise SessionError(f"SPACING {mode} timeout")

    def rebuild_production_spacing(self) -> dict[str, object]:
        """Perform the authorized OFF->ON rebuild and prove ON/5000."""
        off = self.spacing("OFF")
        fleet_off = self.fleet_ready()
        on = self.spacing("ON")
        fleet_on = self.fleet_ready()
        aggregate = fleet_on["listing"]["aggregate"]
        generation = int(aggregate.get("spacing_generation", "-1"), 0)
        require_production_regime(
            spacing=aggregate.get("spacing", ""),
            spacing_us=int(aggregate.get("spacing_us", "-1"), 0),
            spacing_generation=generation,
            current_generation=generation,
        )
        result = {
            "off": off,
            "fleet_off": fleet_off,
            "on": on,
            "fleet_on": fleet_on,
            "current_generation": generation,
        }
        self.evidence["production_spacing_rebuild"] = result
        self.checkpoint()
        return result

    def run(self) -> bool:
        self.evidence["fleet_initial"] = self.fleet_ready()
        current = self.measure("initial")
        for node in self.nodes:
            while current["table"][node]["class"] != "FULL" and int(self.evidence["redraws"][node]) < self.max_redraws_per_link:
                self.redraw(node)
                self.fleet_ready()
                current = self.measure(f"after_redraw_{node}_{self.evidence['redraws'][node]}")

        if any(row["class"] != "FULL" for row in current["table"].values()):
            self.evidence["spacing_rebuild"] = {
                "off": self.spacing("OFF"),
                "fleet_off": self.fleet_ready(),
                "on": self.spacing("ON"),
                "fleet_on": self.fleet_ready(),
            }
            current = self.measure("after_single_spacing_rebuild")

        passed = all(row["class"] == "FULL" for row in current["table"].values())
        self.evidence["final_table"] = current["table"]
        self.evidence["verdict"] = "PASS" if passed else "STOP"
        self.checkpoint()
        return passed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--port")
    parser.add_argument("--placement-confirmed", action="store_true", required=True)
    parser.add_argument("--master-marker", default=MASTER_MARKER)
    parser.add_argument("--max-redraws-per-link", type=int, default=MAX_REDRAWS_PER_LINK)
    args = parser.parse_args()
    gate = ServiceGate(
        args.evidence_root,
        args.port,
        expected_master_marker=args.master_marker,
        max_redraws_per_link=args.max_redraws_per_link,
    )
    try:
        gate.open()
        passed = gate.run()
        return 0 if passed else 2
    except Exception as exc:
        gate.evidence["verdict"] = "STOP"
        gate.evidence["error"] = f"{type(exc).__name__}: {exc}"
        if gate.root.exists():
            gate.checkpoint()
        return 2
    finally:
        gate.close()


if __name__ == "__main__":
    raise SystemExit(main())
