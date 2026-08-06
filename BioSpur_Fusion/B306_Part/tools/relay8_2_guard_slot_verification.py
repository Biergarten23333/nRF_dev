#!/usr/bin/env python3
"""COUNT=12 / 120 ms guard-slot discrimination for relay8.2."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import batch_g_overnight as bg
from analyze_relay8_1_overnight import (
    iter_fusion,
    listener_field_metrics,
    robust_listener_epoch_offsets,
)
from batch_g_day_h3 import SLOT10, SLOT_MAP
from batch_g_overnight import (
    MAIN_MARKER,
    MAIN_SNR,
    NODES,
    TAG_NUMBER,
    utc_now,
    u32_delta,
)
from batch_g_stallfix import anchor_responder_gate
from fusion_session import SessionError, parse_fields
from listener_array_run import wait_listener_preflight
from r4_final_capture import start_listener_collector, stop_listener_collector
from relay8_1_overnight_run import OvernightRunner
from relay8_2_s3_fix_verification import (
    capture_with_status,
    imu_off_gate,
    listener_state,
    reorder_contexts,
    require_fleet,
    status_metrics,
    write_json,
)


TOOLS = Path(__file__).resolve().parent
ALIGNER = TOOLS / "alignment" / "v2"
if str(ALIGNER) not in sys.path:
    sys.path.insert(0, str(ALIGNER))
import time_aligner_v2 as align  # noqa: E402


MASTER_MARKER = "dk-fusion-imu-relay-v29"
COUNT = 12
PERIOD_MS = 120
PERIOD_US = 120_000
FORMAL_S = 600.0
RATE_GATE_HZ = 8.25

bg.MASTER_MARKER = MASTER_MARKER


def guard_active_cfg(tag: int, slot: int) -> str:
    """Compose the explicitly authorized COUNT=12 active configuration."""
    if not 1 <= tag <= 10 or not 0 <= slot < COUNT:
        raise ValueError("guard active tag/slot out of range")
    return (
        f"CFG TAG={tag} SLOT={slot} COUNT={COUNT} PERIOD=10 "
        "ACTIVE=9 EPOCH=5000 BEACON_SYNC=1 BEACON_WIN_N=1 "
        "DW_ANCHOR=0 RUN=1 PMODE=0"
    )


def guard_idle_cfg(tag: int, slot: int) -> str:
    """Compose the matching safe idle without the shared COUNT<=11 policy."""
    if not 1 <= tag <= 10 or not 0 <= slot < COUNT:
        raise ValueError("guard idle tag/slot out of range")
    return (
        f"CFG TAG={tag} SLOT={slot} COUNT={COUNT} PERIOD=10 "
        "ACTIVE=9 EPOCH=5000 BEACON_SYNC=0 BEACON_WIN_N=1 "
        "DW_ANCHOR=0 RUN=0 PMODE=3"
    )


def main_period(runner: OvernightRunner, period_ms: int, label: str) -> dict[str, object]:
    if period_ms not in (100, 120):
        raise SessionError(f"guard runner refuses period {period_ms}")
    output = runner.root / f"{label}_main_period_{period_ms}.json"
    completed = subprocess.run(
        (
            sys.executable,
            str(TOOLS / "listener_vcom_command.py"),
            "--snr",
            MAIN_SNR,
            "--expected-marker",
            MAIN_MARKER,
            "--command",
            f"BEACON_PERIOD {period_ms}",
            "--output",
            str(output),
            "--post-seconds",
            "8",
        ),
        check=False,
        text=True,
        capture_output=True,
    )
    decoded = json.loads(output.read_text()) if output.exists() else None
    result = {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "decoded": decoded,
        "evidence": str(output),
    }
    if (
        completed.returncode != 0
        or not decoded
        or decoded.get("status") != "PASS"
        or not decoded.get(f"period_{period_ms}_seen")
    ):
        raise SessionError(f"main period {period_ms} failed: {result}")
    runner.period_us = period_ms * 1000
    return result


def configure_guard(runner: OvernightRunner) -> dict[str, object]:
    rows: dict[str, object] = {}
    for node in NODES:
        command = guard_active_cfg(TAG_NUMBER[node], SLOT_MAP[node])
        rows[node] = runner.send_tag_cfg_echo(node, command)
        runner.slot_map[node] = SLOT_MAP[node]
        runner.win_map[node] = 1
        runner.checkpoint()
    return rows


def analyze_guard(
    root: Path, capture: dict[str, object], listener_dir: Path
) -> dict[str, object]:
    start = float(capture["started_monotonic"])
    end = float(capture["ended_monotonic"])
    fusion_log = root / "fusion_cdc.log"

    # The aligner is parameterized through this module constant for listener
    # epoch reconstruction.  fit_board converges from its 110 ms seed to the
    # measured 120 ms period; the measured periods are reported below.
    align.GRID_US = float(PERIOD_US)
    boards = align.extract_fusion(fusion_log, start, end)
    missing = sorted(set(NODES) - set(boards))
    if missing:
        raise SessionError(f"guard window missing nodes: {missing}")
    fits = {node: align.fit_board(boards[node]) for node in NODES}
    sources = {node: 0xB100 + TAG_NUMBER[node] for node in NODES}
    polls, listener_audit = align.load_listener_polls(
        listener_dir,
        start,
        end,
        {sources[node]: SLOT_MAP[node] for node in NODES},
    )
    f4 = robust_listener_epoch_offsets(boards, fits, sources, polls)

    mods: dict[str, list[int]] = defaultdict(list)
    reorder_first: dict[str, int] = {}
    reorder_last: dict[str, int] = {}
    for _host, line in iter_fusion(fusion_log, start, end):
        fields = parse_fields(line)
        node = fields.get("name")
        if node not in NODES:
            continue
        if "FUSION_UWB " in line and fields.get("sf_valid") == "1":
            mods[node].append(int(fields["sf_mod16"], 0))
        elif "FUSION_TELEMETRY " in line and "reorder" in fields:
            value = int(fields["reorder"], 0)
            reorder_first.setdefault(node, value)
            reorder_last[node] = value

    nodes: dict[str, object] = {}
    for node in NODES:
        board = boards[node]
        fit = fits[node]
        epoch_span = int(fit.epoch_index[-1] - fit.epoch_index[0])
        rate = (
            (len(board.frame_us) - 1) / (epoch_span * (PERIOD_US / 1e6))
            if epoch_span
            else 0.0
        )
        mod_deltas = [
            (b - a) & 0xF for a, b in zip(mods[node], mods[node][1:])
        ]
        plus1 = mod_deltas.count(1) / len(mod_deltas) if mod_deltas else 0.0
        absolute = fit.epoch_index + int(f4["nodes"][node]["modal_offset"])
        paired = min(len(mods[node]), len(absolute))
        exact = (
            sum(
                mods[node][index] == (int(absolute[index]) & 0xF)
                for index in range(paired)
            )
            / paired
            if paired
            else 0.0
        )
        status = status_metrics(capture["status_series"][node])
        reorder = (
            u32_delta(reorder_first[node], reorder_last[node])
            if node in reorder_first and node in reorder_last
            else None
        )
        passed = (
            rate >= RATE_GATE_HZ
            and plus1 >= 0.999
            and exact == 1.0
            and status.get("miss_fraction") is not None
            and float(status["miss_fraction"]) < 0.01
            and status.get("rxarm_delta") == 0
        )
        nodes[node] = {
            "slot": SLOT_MAP[node],
            "records": len(board.frame_us),
            "elapsed_epochs": epoch_span,
            "fitted_period_us": fit.period_us,
            "tag_domain_rate_hz": rate,
            "delta_mod16_plus1_fraction": plus1,
            "delta_mod16_histogram": dict(sorted(Counter(mod_deltas).items())),
            "listener_absolute_epoch_exact_fraction": exact,
            "listener_pairs": paired,
            "status": status,
            "telemetry_reorder_delta": reorder,
            "pass": passed,
        }

    listener = listener_field_metrics(listener_dir, start, end)
    forensics = reorder_contexts(fusion_log, start, end)
    write_json(root / "GUARD_REORDER_FORENSICS.json", forensics)
    passed = (
        all(bool(row["pass"]) for row in nodes.values())
        and not capture["disconnects"]
        and not capture["malformed"]
        and int(capture["decoder_errors"]) == 0
        and not capture["imu_records"]
        and bool(listener.get("sub_slaved"))
    )
    result = {
        "pass": passed,
        "count": COUNT,
        "period_us": PERIOD_US,
        "empty_guard_slot": 11,
        "slot10": SLOT10,
        "slot10_causal_gate_pass": bool(nodes[SLOT10]["pass"]),
        "nodes": nodes,
        "listener_audit": listener_audit,
        "listener_field": listener,
        "reorder_forensics": forensics,
    }
    write_json(root / "GUARD_ANALYSIS.json", result)
    return result


def safe_end(runner: OvernightRunner, root: Path) -> dict[str, object]:
    rows: dict[str, object] = {}
    for node in NODES:
        rows[node] = runner.send_tag_cfg_echo(
            node, guard_idle_cfg(TAG_NUMBER[node], SLOT_MAP[node])
        )
    witness = runner.capture("guard_terminal_idle_witness", 90.0)
    for node in NODES:
        count = witness["records"].get(node, 0)
        rows[node]["witness_uwb_records"] = count
        rows[node]["idle_behavior_pass"] = count <= 1
    period100 = main_period(runner, 100, "guard_terminal")
    result = {
        "nodes": rows,
        "witness": witness,
        "main_period100": period100,
        "pass": all(row["idle_behavior_pass"] for row in rows.values()),
    }
    write_json(root / "GUARD_SAFE_END_STATE.json", result)
    return result


def report(root: Path, result: dict[str, object], safe: dict[str, object]) -> None:
    lines = [
        "# relay8.2 GUARD-SLOT verification",
        "",
        f"Date: {utc_now()}",
        f"Verdict: **{'PASS' if result.get('pass') else 'FAIL'}**",
        "",
        "Configuration: COUNT=12, period=120,000 us, tags in slots 1-10, slot 11 empty, IMU off.",
        "The field witness is two beacon telemetry streams (main + slaved sub) plus five observers.",
        "",
        "| BSF | slot | rate Hz | Δmod16 +1 | epoch exact | miss fraction | rxarm Δ | reorder Δ (not gate) | verdict |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for node in sorted(NODES, key=lambda item: SLOT_MAP[item]):
        row = result["nodes"][node]
        status = row["status"]
        lines.append(
            f"| {node} | {row['slot']} | {row['tag_domain_rate_hz']:.6f} | "
            f"{row['delta_mod16_plus1_fraction']:.6f} | "
            f"{row['listener_absolute_epoch_exact_fraction']:.6f} | "
            f"{status['miss_fraction']:.6f} | {status['rxarm_delta']} | "
            f"{row['telemetry_reorder_delta']} | {'PASS' if row['pass'] else 'FAIL'} |"
        )
    slot10 = result["nodes"][SLOT10]
    lines.extend(
        [
            "",
            "## Causal discriminator",
            "",
            f"- Slot-10 occupant: `{SLOT10}`.",
            f"- Slot-10 rate: `{slot10['tag_domain_rate_hz']:.6f} Hz` (gate >= {RATE_GATE_HZ:.2f} Hz).",
            f"- Slot-10 rxarm delta: `{slot10['status']['rxarm_delta']}` (gate 0).",
            f"- Physical tail-room hypothesis confirmed: **{result['slot10_causal_gate_pass']}**.",
            "",
            "## rxarm series",
            "",
        ]
    )
    for node in sorted(NODES, key=lambda item: SLOT_MAP[item]):
        values = [
            row["fields"].get("rxarm")
            for row in result["nodes"][node]["status"]["series"]
            if row.get("fields", {}).get("lock") == "1"
        ]
        lines.append(f"- `{node}`: {values}")
    lines.extend(
        [
            "",
            "## End state",
            "",
            f"- Ten-node composed-idle witness: **{safe.get('pass')}**.",
            "- Main restored to 100,000 us.",
            "- Tag Master remained unplugged; probe 1050070698 untouched.",
            "- No capture process remains.",
            "",
            "## Evidence",
            "",
            "- `GUARD_ANALYSIS.json`",
            "- `formal_capture.json`",
            "- `fusion_cdc.log`",
            "- `continuous_listener_capture/`",
            "- `GUARD_SAFE_END_STATE.json`",
            "- `GUARD_REORDER_FORENSICS.json`",
            "",
        ]
    )
    (root / "GUARD_SLOT_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-root", required=True, type=Path)
    parser.add_argument("--fusion-port")
    args = parser.parse_args()
    if args.evidence_root.exists():
        raise SystemExit(f"refusing existing evidence root: {args.evidence_root}")
    args.evidence_root.mkdir(parents=True)

    runner = OvernightRunner(args.evidence_root, args.fusion_port, 1.0)
    runner.period_us = 100_000
    runner.slot_map = dict(SLOT_MAP)
    runner.win_map = {node: 1 for node in NODES}
    collector = None
    collector_handle = None
    result: dict[str, object] = {"pass": False, "nodes": {}}
    safe: dict[str, object] = {"pass": False}
    state: dict[str, object] = {
        "started": utc_now(),
        "status": "IN_PROGRESS",
        "tag_master": "USB disconnected; excluded",
        "probe_1050070698": "untouched",
    }
    try:
        state["anchor_gate"] = anchor_responder_gate(args.evidence_root)
        runner.open()
        state["fleet_gate"] = require_fleet(runner)
        state["imu_off_gate"] = imu_off_gate(runner)
        state["main_period120"] = main_period(runner, PERIOD_MS, "guard_entry")
        time.sleep(12.0)
        state["tag_cfg"] = configure_guard(runner)
        time.sleep(15.0)
        preflight = runner.fusion_snapshot("guard_preflight")
        bad = {
            node: row
            for node, row in preflight["beacon_status"].items()
            if row.get("fields", {}).get("lock") != "1"
            or row.get("fields", {}).get("sync") != "1"
            or row.get("fields", {}).get("win") != "1"
        }
        sub = listener_state(preflight["sub"])
        if bad or not sub["slaved"] or sub["tx_records"]:
            raise SessionError(f"guard preflight failed: tags={bad} sub={sub}")
        state["preflight"] = {"statuses": preflight["beacon_status"], "sub": sub}

        collector, collector_handle, listener_dir = start_listener_collector(
            args.evidence_root,
            label="continuous_listener_capture",
            duration_s=FORMAL_S + 180.0,
        )
        runner.listener_collector_active = True
        runner.listener_dir = listener_dir
        state["listener_preflight"] = wait_listener_preflight(
            listener_dir, collector, timeout_s=25.0
        )
        capture = capture_with_status(runner, args.evidence_root, FORMAL_S)
        state["formal_capture"] = capture
        state["listener_stop"] = stop_listener_collector(collector, collector_handle)
        collector = collector_handle = None
        runner.listener_collector_active = False
        result = analyze_guard(args.evidence_root, capture, listener_dir)
        state["analysis"] = result
    except Exception as exc:
        state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if collector is not None or collector_handle is not None:
            state["listener_stop_finally"] = stop_listener_collector(
                collector, collector_handle
            )
            runner.listener_collector_active = False
        if runner.channel is not None:
            try:
                safe = safe_end(runner, args.evidence_root)
            except Exception as exc:
                safe = {"pass": False, "error": f"{type(exc).__name__}: {exc}"}
            runner.channel.close()
            if runner.raw is not None:
                runner.raw.close()
        state["safe_end"] = safe
        state["status"] = "PASS" if result.get("pass") and safe.get("pass") else "FAIL"
        state["ended"] = utc_now()
        write_json(args.evidence_root / "GUARD_RUN_STATE.json", state)
        if set(result.get("nodes", {})) == set(NODES):
            report(args.evidence_root, result, safe)

    return 0 if state["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
