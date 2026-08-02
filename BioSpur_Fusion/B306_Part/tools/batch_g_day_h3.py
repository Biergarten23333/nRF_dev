#!/usr/bin/env python3
"""Batch-G day-run H2 entry and 30-minute waivered G3 qualification."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from batch_g_overnight import (
    LEDGER_FIELDS,
    NODES,
    RATE_GATE_HZ,
    Runner,
    TAG_NUMBER,
    active_cfg,
)
from fusion_session import SessionError


SLOT10 = "BSFC2CC"
SLOT_MAP = {
    "BSF3C79": 1,
    "BSFC2CC": 10,
    "BSF44AD": 3,
    "BSF6C53": 4,
    "BSF8BC4": 5,
    "BSF1120": 6,
    "BSF31CC": 7,
    "BSFAA61": 8,
    "BSFB165": 9,
    "BSFEC35": 2,
}
HARD_LEDGER = (
    "crc",
    "header",
    "ring_drop",
    "duplicate",
    "reorder",
    "drop_err",
    "notify_errno",
    "uart_err",
    "logger_drop",
)


class DayRunner(Runner):
    """Retry the Master-local read-only LIST against transient CDC loss."""

    def list_peers(self) -> dict[str, object]:
        errors: list[str] = []
        for attempt in range(1, 4):
            try:
                result = super().list_peers()
                result["dayrun_list_attempt"] = attempt
                result["dayrun_prior_errors"] = errors
                return result
            except SessionError as exc:
                errors.append(str(exc))
                if attempt < 3:
                    time.sleep(2.0)
        raise SessionError(f"LIST failed after 3 bounded attempts: {errors}")


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def listener_state(status: dict[str, object]) -> dict[str, object]:
    decoded = status.get("decoded") or {}
    lines = decoded.get("post_lines", [])
    return {
        "slaved_seen": any(";SLAVED;" in line for line in lines),
        "tx_records": sum(line.startswith("LBTX;") for line in lines),
        "lines": lines,
    }


def lbstat_counters(status: dict[str, object]) -> dict[str, int] | None:
    decoded = status.get("decoded") or {}
    for line in reversed(decoded.get("post_lines", [])):
        if not line.startswith("LBSTAT;"):
            continue
        fields: dict[str, int] = {}
        for item in line.split(";"):
            if "=" not in item:
                continue
            key, value = item.split("=", 1)
            if key in ("start_fail", "done_timeout", "late"):
                fields[key] = int(value, 0)
        return fields
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()

    runner = DayRunner(args.evidence_root, args.fusion_port, 2.0)
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    result: dict[str, object] = {
        "status": "IN_PROGRESS",
        "slot10": SLOT10,
        "slot10_waiver": (
            "slot-10 rate deficit is a documented relay7 limitation "
            "(structural last-slot geometry; fix scheduled for relay8); "
            "nine of ten gated."
        ),
        "slot10_rationale": (
            "BSFC2CC is an old-five unit with clean command/data history; "
            "BSFEC35 and BSF8BC4 were the first two slot-10 occupants, "
            "while BSFAA61 and BSFB165 are excluded by the prompt."
        ),
        "slot_map": SLOT_MAP,
    }
    try:
        runner.open()
        listing = runner.list_peers()
        aggregate = listing["aggregate"]
        if (
            aggregate.get("count") != "10"
            or aggregate.get("ready") != "10"
            or set(listing["peers"]) != set(NODES)
        ):
            raise SessionError(f"H2 requires 10/10 peers: {listing}")
        result["initial_list_gate"] = listing

        cfg_results: dict[str, object] = {}
        for node in NODES:
            cfg_results[node] = runner.send_tag_cfg(
                node,
                active_cfg(
                    TAG_NUMBER[node], SLOT_MAP[node], count=11,
                    beacon_win_n=1,
                ),
            )
        result["tag_first_cfg"] = cfg_results
        result["cfg_without_ack"] = [
            node
            for node, row in cfg_results.items()
            if row.get("completion") != "cfg_ok"
        ]
        result["period110"] = runner.set_main_period(110, "h2")
        runner.period_us = 110_000
        time.sleep(12.0)

        preflight = runner.snapshot("h2_preflight")
        preflight_fail: dict[str, object] = {}
        gens: set[str] = set()
        for node in NODES:
            row = preflight["beacon_status"].get(node, {})
            fields = row.get("fields", {})
            if fields.get("lock") != "1" or fields.get("sync") != "1":
                preflight_fail[node] = row
            if "gen" in fields:
                gens.add(fields["gen"])
        if len(gens) != 1:
            preflight_fail["generation_set"] = sorted(gens)
        if preflight_fail:
            raise SessionError(f"H2 lock/gen preflight failed: {preflight_fail}")
        sub_preflight = listener_state(preflight["sub"])
        if not sub_preflight["slaved_seen"] or sub_preflight["tx_records"]:
            raise SessionError(f"sub preflight is not SLAVED: {sub_preflight}")
        result["preflight"] = {
            "generation": next(iter(gens)),
            "sub": sub_preflight,
            "snapshot_index": len(runner.snapshots) - 1,
        }

        print("G3-W FORMAL WINDOW START — 1800 s", flush=True)
        measured = runner.measured_window("g3w", 1800.0)
        rows: dict[str, object] = {}
        for node in NODES:
            row = measured["nodes"].get(node, {})
            ledger = row.get("ledger_deltas", {})
            gate_reasons: list[str] = []
            if not row.get("available"):
                gate_reasons.append("measurement unavailable")
            else:
                if node != SLOT10 and row.get("tag_domain_rate_hz", 0.0) < RATE_GATE_HZ:
                    gate_reasons.append("rate below 9.00 Hz")
                for key in ("sweep_missing", "sweep_duplicates", "sweep_reorders"):
                    if node != SLOT10 and row.get(key, 0) != 0:
                        gate_reasons.append(f"{key}={row.get(key)}")
                if row.get("lock_before") != "1" or row.get("lock_after") != "1":
                    gate_reasons.append("lock not held")
                if row.get("gen_before") != row.get("gen_after"):
                    gate_reasons.append("generation changed")
                for key in HARD_LEDGER:
                    if key not in ledger:
                        gate_reasons.append(f"ledger {key} unavailable")
                    elif ledger[key] != 0:
                        gate_reasons.append(f"ledger {key}={ledger[key]}")
            rows[node] = {
                "gated": node != SLOT10,
                "pass": not gate_reasons,
                "gate_reasons": gate_reasons,
                "measurement": row,
            }

        sub_before = listener_state(measured["field_status"]["sub_before"])
        sub_after = listener_state(measured["field_status"]["sub_after"])
        sub_pass = bool(
            sub_before["slaved_seen"]
            and sub_after["slaved_seen"]
            and sub_before["tx_records"] == 0
            and sub_after["tx_records"] == 0
        )
        main_before = lbstat_counters(measured["field_status"]["main_before"])
        main_after = lbstat_counters(measured["field_status"]["main_after"])
        main_delta = None
        if main_before is not None and main_after is not None:
            main_delta = {
                key: main_after.get(key, 0) - main_before.get(key, 0)
                for key in set(main_before) | set(main_after)
            }
        gated_pass = all(
            rows[node]["pass"] for node in NODES if node != SLOT10
        )
        transport_pass = bool(
            measured["capture"]["decoder_errors"] == 0
            and not measured["capture"]["malformed"]
            and not measured["capture"]["disconnects"]
        )
        passed = gated_pass and sub_pass and transport_pass
        result["g3w"] = {
            "pass": passed,
            "nodes": rows,
            "sub_pass": sub_pass,
            "sub_before": sub_before,
            "sub_after": sub_after,
            "main_before": main_before,
            "main_after": main_after,
            "main_delta": main_delta,
            "transport_pass": transport_pass,
            "analysis_file": str(args.evidence_root / "g3w_analysis.json"),
        }
        result["status"] = "G3-W_PASS" if passed else "G3-W_FAIL"
        if not passed:
            result["cleanup"] = runner.cleanup("G3-W gated failure")
        write_json(args.evidence_root / "H2_H3_RESULT.json", result)
        print(f"{result['status']}", flush=True)
        return 0 if passed else 3
    except Exception as exc:
        result["status"] = "FAILED"
        result["error"] = f"{type(exc).__name__}: {exc}"
        try:
            result["cleanup"] = runner.cleanup("H2/H3 exception")
        except Exception as cleanup_exc:
            result["cleanup_error"] = (
                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
            )
        write_json(args.evidence_root / "H2_H3_RESULT.json", result)
        return 2
    finally:
        runner.checkpoint()
        if runner.channel is not None:
            runner.channel.close()
        if runner.raw is not None:
            runner.raw.close()
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
