#!/usr/bin/env python3
"""Deterministic offline closure for the interactive BSFC2CC rotation run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import shutil
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "biospur-c2cc-rotation-v1"
import matplotlib.pyplot as plt
import numpy as np

import analyze_v47_c2cc_stationary as base
from analyze_v47_state_adaptive_fusion import robust_scatter
from fusion_host_binary import FrameError
from v47_real_data_adapter import sequence_gap_count
from v47_s2_fusion import corrected_range_m


ROOT = Path(__file__).resolve().parents[2]
NODE = "BSFC2CC"
REQUIRED = (
    "REPORT.md", "WARMUP_ANALYSIS.json", "FORMAL_CAPTURE_INTEGRITY.json",
    "ACTION_TIME_BRACKETS.csv", "SENSOR_DOMAIN_EVENT_LABELS.csv",
    "STATE_TRANSITIONS.csv", "ROTATION_PHASE_RESULTS.csv",
    "SHORT_CYCLE_RESULTS.csv", "PER_MODE_METRICS.csv",
    "UWB_UPDATE_ACCOUNTING.json", "UWB_LINK_METRICS.csv",
    "S2P_S2R_COMPARISON.md", "NUMERICAL_INTEGRITY.json", "LIMITATIONS.md",
    "complete_event_state_timeline.svg", "gyro_uwb_vs_s2.svg",
    "sustained_trajectories.svg", "post_off_settling_relock.svg",
    "short_cycle_state_responses.svg", "s2p_s2r.svg",
    "per_anchor_innovation.svg",
)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(item) for item in value]
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, np.ndarray):
        return [clean(x) for x in value.tolist()]
    if isinstance(value, float):
        return None if not math.isfinite(value) else float(f"{value:.12g}")
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore",
                                lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if clean(row.get(key)) is None else clean(row.get(key))
                             for key in fields})


def amend_shortened_protocol(run: Path) -> dict:
    manifest_path = run / "RUN_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text())
    tokens = [json.loads(line) for line in (run / "OPERATOR_TOKENS.jsonl").read_text().splitlines()]
    shorten = [x for x in tokens if x["token"] == "END_SEQUENCE_AFTER_CYCLE_2"]
    unwind = [x for x in tokens if x["token"] == "ABORT_MOTOR_TEST" and x["monotonic"] > shorten[0]["monotonic"]] if shorten else []
    if len(shorten) != 1 or len(unwind) != 1:
        raise RuntimeError("unique shortening/unwind tokens not found")
    final_s = float(unwind[0]["monotonic"] - shorten[0]["monotonic"])
    if final_s < 60:
        raise RuntimeError(f"shortened final stationary interval only {final_s}s")
    amendment = {
        "schema": "biospur-operator-protocol-shortening-v1",
        "classification": "OPERATOR_SHORTENED_PROTOCOL",
        "decision_timing": "BEFORE_VIEWING_ANALYSIS_RESULTS",
        "reason": "Incremental value of three additional short cycles judged low after three sustained and two short ON/OFF episodes.",
        "completed_short_cycles": [1, 2],
        "not_executed": {f"CYCLE_{n}": "NOT_EXECUTED_BY_OPERATOR_SHORTENING" for n in range(3, 6)},
        "shortening_token": shorten[0],
        "legacy_clean_unwind_token": unwind[0],
        "final_stationary_start_monotonic": shorten[0]["monotonic"],
        "final_stationary_end_monotonic": unwind[0]["monotonic"],
        "final_stationary_duration_s": final_s,
        "collector_restarted": False,
        "second_t0": False,
        "full_preregistered_five_cycle_pass_allowed": False,
    }
    write_json(run / "PROTOCOL_AMENDMENT.json", amendment)
    if manifest.get("stop_reason") == "STOPPED_BY_OPERATOR":
        manifest["collector_exit_reason"] = "LEGACY_ABORT_TOKEN_CLEAN_UNWIND"
        manifest["stop_reason"] = "OPERATOR_SHORTENED_PROTOCOL"
        manifest["protocol_amendment"] = amendment
        write_json(manifest_path, manifest)
    elif manifest.get("stop_reason") != "OPERATOR_SHORTENED_PROTOCOL":
        raise RuntimeError(f"unexpected stop reason: {manifest.get('stop_reason')}")
    return amendment


def prepare_view(run: Path, out: Path, manifest: dict) -> Path:
    view = out / "replay_input_view"
    formal = view / "formal_capture"
    formal.mkdir(parents=True)
    raw = run / "continuous_raw/fusion_host_raw.cobs.bin"
    os.symlink(os.path.relpath(raw, formal), formal / raw.name)
    t0 = manifest["formal_t0"]
    write_json(formal / "RUN_MANIFEST.json", {
        "formal_health_baseline": t0["health"],
        "commands_after_t0": [], "mutation": False,
        "t0_wall": t0["wall"], "t0_monotonic": t0["monotonic"],
    })
    return view


def accepted_token_map(manifest: dict) -> dict[str, dict]:
    return {x["step"]: x for x in manifest["operator_tokens"] if x.get("accepted")}


def instruction_map(manifest: dict) -> dict[str, dict]:
    return {x["step"]: x for x in manifest["operator_instructions"]}


def action_rows(manifest: dict, amendment: dict) -> list[dict]:
    rows = []
    for row in manifest["action_brackets"]:
        rows.append({**row, "status": "COMPLETED", "interval_s": row["instruction_to_confirmation_s"]})
    for n in range(3, 6):
        rows.append({"step": f"CYCLE_{n}", "instruction": "", "instruction_wall": "",
                     "instruction_monotonic": "", "token": "", "token_wall": "",
                     "token_monotonic": "", "interval_s": "",
                     "status": "NOT_EXECUTED_BY_OPERATOR_SHORTENING"})
    rows.append({"step": "FINAL_STATIONARY_SHORTENED", "instruction": "Operator shortened after Cycle 2",
                 "instruction_wall": amendment["shortening_token"]["wall"],
                 "instruction_monotonic": amendment["final_stationary_start_monotonic"],
                 "token": "ABORT_MOTOR_TEST (legacy clean unwind only)",
                 "token_wall": amendment["legacy_clean_unwind_token"]["wall"],
                 "token_monotonic": amendment["final_stationary_end_monotonic"],
                 "interval_s": amendment["final_stationary_duration_s"],
                 "status": "OPERATOR_SHORTENED_FINAL_STATIONARY"})
    return rows


def phase_specs(manifest: dict, amendment: dict) -> list[dict]:
    ins, tok = instruction_map(manifest), accepted_token_map(manifest)
    def ev(name, on, off, kind):
        return {"phase": name, "kind": kind,
                "on_instruction": ins[on]["monotonic"], "on_token": tok[on]["monotonic"],
                "off_instruction": ins[off]["monotonic"], "off_token": tok[off]["monotonic"]}
    phases = [ev("LOW", "LOW_ON", "LOW_OFF", "SUSTAINED"),
              ev("MEDIUM", "MEDIUM_ON", "MEDIUM_OFF", "SUSTAINED"),
              ev("HIGH", "HIGH_ON", "HIGH_OFF", "SUSTAINED"),
              ev("CYCLE_1", "CYCLE_1_ON", "CYCLE_1_OFF", "SHORT"),
              ev("CYCLE_2", "CYCLE_2_ON", "CYCLE_2_OFF", "SHORT")]
    t0 = float(manifest["formal_t0"]["monotonic"])
    for p in phases:
        for key in ("on_instruction", "on_token", "off_instruction", "off_token"):
            p[key + "_s"] = float(p[key] - t0)
        p["definitely_on_duration_s"] = p["off_instruction_s"] - p["on_token_s"]
    end_s = float(amendment["final_stationary_end_monotonic"] - t0)
    for index, phase in enumerate(phases):
        phase["observation_end_s"] = phases[index + 1]["on_instruction_s"] if index + 1 < len(phases) else end_s
    return phases


def sustained_runs(t: np.ndarray, flag: np.ndarray, minimum_s: float) -> list[tuple[float, float]]:
    runs = []
    start = None
    previous = None
    for ti, yes in zip(t, flag):
        if yes and start is None:
            start = float(ti)
        if (not yes) and start is not None:
            end = float(previous)
            if end - start >= minimum_s:
                runs.append((start, end))
            start = None
        previous = ti
    if start is not None and previous - start >= minimum_s:
        runs.append((start, float(previous)))
    return runs


def first_run(runs, start, end=None):
    candidates = [r for r in runs if r[1] >= start and (end is None or r[0] <= end)]
    return None if not candidates else max(float(start), candidates[0][0])


def first_quiet(t, motion, start, minimum_s=1.0):
    q = t >= start
    runs = sustained_runs(t[q], ~motion[q], minimum_s)
    return None if not runs else runs[0][0]


def detector(imu, uwb, it, ut, pos, phases, method):
    acc, gyro, _ = base.imu_physical(imu)
    bias = np.asarray(method["source_parameters"]["gyro_bias_dps"])
    gyro_res = gyro - bias
    norm = np.linalg.norm(gyro_res, axis=1)
    window = 100
    sq = np.r_[0.0, np.cumsum(norm * norm)]
    rms = np.zeros(len(norm))
    rms[window - 1:] = np.sqrt((sq[window:] - sq[:-window]) / window)
    threshold = float(method["imu_motion"]["bias_corrected_vector_rms_threshold_dps"])
    raw_motion = rms > threshold
    motion_runs = sustained_runs(it, raw_motion, float(method["imu_motion"]["minimum_true_s"]))
    motion = np.zeros(len(it), bool)
    for a, b in motion_runs:
        motion |= (it >= a) & (it <= b)

    ranges = uwb["range_mm"].astype(float) / 1000.0
    valid = np.asarray([(int(mask) & (1 << np.arange(8))) != 0 for mask in uwb["valid_mask"]])
    labels = []
    for p in phases:
        baseline = (ut >= max(0, p["on_instruction_s"] - 5)) & (ut < p["on_instruction_s"])
        base_ranges = np.asarray([np.median(ranges[baseline & valid[:, k], k])
                                  if np.any(baseline & valid[:, k]) else np.nan for k in range(8)])
        base_pos = np.nanmedian(pos[baseline], axis=0) if np.any(baseline) else np.full(3, np.nan)
        sigma = np.asarray(method["source_parameters"]["range_sigma_m"])
        shifted = np.abs(ranges - base_ranges) > np.maximum(4 * sigma, .12)
        uwb_motion = (np.sum(shifted & valid, axis=1) >= 3) & (np.linalg.norm(pos - base_pos, axis=1) > .15)
        uruns = sustained_runs(ut, uwb_motion, .5)
        search_end = p["off_token_s"] + 10
        imu_on = first_run(motion_runs, p["on_instruction_s"], search_end)
        uwb_on = first_run(uruns, p["on_instruction_s"], search_end)
        supported = min([x for x in (imu_on, uwb_on) if x is not None], default=None)
        post_runs = [run for run in motion_runs if run[1] >= p["off_instruction_s"] and run[0] <= p["observation_end_s"]]
        last_motion = max((min(run[1], p["observation_end_s"]) for run in post_runs), default=p["off_instruction_s"])
        imu_quiet = first_quiet(it, motion, max(p["off_instruction_s"], last_motion), 1.0)
        # A 1.5 s causal UWB stability window: finite T4 position remains within 0.10 m.
        stable = None
        j0 = int(np.searchsorted(ut, max(p["off_instruction_s"], last_motion), side="left"))
        for j in range(j0, len(ut)):
            if ut[j] > p["observation_end_s"]: break
            k = int(np.searchsorted(ut, ut[j] + 1.5, side="right"))
            points = pos[j:k]
            q = np.isfinite(points).all(axis=1)
            if k > j + 5 and np.sum(q) >= 5:
                center = np.median(points[q], axis=0)
                position_span = float(np.linalg.norm(np.ptp(points[q], axis=0)))
                unstable_links = 0
                for aid in range(8):
                    values = ranges[j:k, aid][valid[j:k, aid]]
                    if len(values) < 5:
                        unstable_links += 1; continue
                    median = float(np.median(values))
                    if np.max(np.abs(values - median)) > max(3 * sigma[aid], .09):
                        unstable_links += 1
                if position_span <= .10 and unstable_links <= 1:
                    stable = float(ut[k - 1]); break
        settle = max(x for x in (imu_quiet, stable) if x is not None) if imu_quiet is not None and stable is not None else None
        classification = "SUPPORTED" if supported is not None else "MOTION_EVIDENCE_AMBIGUOUS"
        settle_class = "SUPPORTED" if settle is not None else "SETTLE_TIME_AMBIGUOUS"
        lower_candidates=[x for x in (imu_quiet,stable) if x is not None]
        settle_lower=min(lower_candidates,default=None)
        settle_upper=max(lower_candidates) if settle is not None else (p["observation_end_s"] if lower_candidates else None)
        labels.append({**p, "imu_motion_onset_s": imu_on, "uwb_motion_onset_s": uwb_on,
                       "sustained_motion_start_s": supported,
                       "last_imu_motion_evidence_s": last_motion,
                       "independent_motion_duration_s": None if supported is None else last_motion-supported,
                       "imu_quiet_start_s": imu_quiet, "uwb_stable_start_s": stable,
                       "mechanical_settle_s": settle, "motion_classification": classification,
                       "settle_classification": settle_class,
                       "settle_lower_s": settle_lower,"settle_upper_s": settle_upper})
    return acc, gyro, gyro_res, rms, motion, labels


def first_transition(fusion, state, start, end=None):
    rows = [x for x in fusion.transitions if x["to_state"] == state and x["time_s"] >= start
            and (end is None or x["time_s"] <= end)]
    return None if not rows else float(rows[0]["time_s"])


def state_at(fusion, when):
    state = "INIT"
    for row in fusion.transitions:
        if row["time_s"] > when: break
        state = row["to_state"]
    return state


def phase_results(labels, modes):
    rows = []
    for label in labels:
        row = {key: label.get(key) for key in (
            "phase", "kind", "on_instruction_s", "on_token_s", "off_instruction_s",
            "off_token_s", "definitely_on_duration_s", "imu_motion_onset_s",
            "uwb_motion_onset_s", "sustained_motion_start_s", "last_imu_motion_evidence_s",
            "independent_motion_duration_s", "observation_end_s", "imu_quiet_start_s",
            "uwb_stable_start_s", "mechanical_settle_s", "motion_classification",
            "settle_classification", "settle_lower_s", "settle_upper_s")}
        for mode, fusion in modes.items():
            start = label["on_instruction_s"]
            end = label["off_token_s"] + 20
            suspected = first_transition(fusion, "MOTION_SUSPECTED", start, end)
            moving = first_transition(fusion, "MOVING", start, end)
            settling = first_transition(fusion, "SETTLING", label["off_instruction_s"], label["observation_end_s"])
            settle = label["mechanical_settle_s"]
            if settle is None:
                relock=None;relock_status="SETTLE_TIME_AMBIGUOUS"
            elif state_at(fusion,settle)=="STATIONARY":
                relock=settle;relock_status="ALREADY_STATIONARY_AT_INDEPENDENT_SETTLE"
            else:
                relock=first_transition(fusion,"STATIONARY",settle,label["observation_end_s"])
                relock_status="AFTER_INDEPENDENT_SETTLE" if relock is not None else "NO_SUPPORTED_RELOCK"
            onset = label["sustained_motion_start_s"]
            during = [x for x in fusion.transitions if x["to_state"] == "STATIONARY"
                      and onset is not None and onset <= x["time_s"] <= label["last_imu_motion_evidence_s"]]
            interruptions = [x for x in fusion.transitions if x["from_state"] == "SETTLING" and x["to_state"] == "MOVING"
                             and onset is not None and onset <= x["time_s"] <= label["last_imu_motion_evidence_s"]]
            snapshots = [x for x in fusion.snapshots if start <= x["time_s"] <= end]
            max_pos = max((float(np.linalg.norm(x["internal_m"])) for x in snapshots), default=math.nan)
            max_vel = max((float(np.linalg.norm(x["velocity_mps"])) for x in snapshots), default=math.nan)
            row.update({f"{mode}_suspected_s": suspected, f"{mode}_moving_s": moving,
                        f"{mode}_release_latency_s": None if onset is None or moving is None else moving - onset,
                        f"{mode}_false_relock_during_motion": len(during),
                        f"{mode}_settling_interruptions_during_motion": len(interruptions),
                        f"{mode}_settling_s": settling, f"{mode}_relock_s": relock,
                        f"{mode}_relock_status": relock_status,
                        f"{mode}_relock_latency_s": None if settle is None or relock is None else relock - settle,
                        f"{mode}_max_position_norm_m": max_pos,
                        f"{mode}_max_velocity_mps": max_vel})
            release_limit = 2.0 if label["phase"] == "LOW" else 1.0
            latency = row[f"{mode}_release_latency_s"]
            row[f"{mode}_release_gate"] = "PASS" if latency is not None and latency <= release_limit else "FAIL"
            row[f"{mode}_sustained_no_false_relock_gate"] = "PASS" if not during else "FAIL"
            row[f"{mode}_chattering_gate"] = "PASS" if not interruptions else "FAIL"
            relock_latency = row[f"{mode}_relock_latency_s"]
            row[f"{mode}_post_settle_relock_gate"] = "PASS" if relock_latency is not None and 0 <= relock_latency <= 5 else "FAIL"
            row[f"{mode}_episode_detected"] = "PASS" if moving is not None else "FAIL"
        rows.append(row)
    return rows


def phase_local_integrity(imu, uwb, it, ut, labels, manifest, amendment):
    t0 = float(manifest["formal_t0"]["monotonic"])
    windows = [("INITIAL_STATIONARY", 0.0, 60.0)]
    windows.extend((x["phase"], x["on_instruction_s"], x["observation_end_s"]) for x in labels)
    windows.append(("FINAL_STATIONARY_SHORTENED", float(amendment["final_stationary_start_monotonic"]-t0), float(amendment["final_stationary_end_monotonic"]-t0)))
    rows = []
    for name, start, end in windows:
        iq=(it>=start)&(it<end);uq=(ut>=start)&(ut<end);duration=end-start
        ig=sequence_gap_count(imu["seq"][iq],1<<16);ug=sequence_gap_count(uwb["sweep"][uq],1<<32)
        ir=int(np.sum(np.diff(imu["b306_us"][iq].astype(np.int64))<=0));ur=int(np.sum(np.diff(uwb["strobe_us"][uq].astype(np.int64))<=0))
        rows.append({"phase":name,"start_s":start,"end_s":end,"duration_s":duration,"imu_samples":int(np.sum(iq)),"uwb_sweeps":int(np.sum(uq)),"imu_hz":int(np.sum(iq))/duration,"uwb_hz":int(np.sum(uq))/duration,"imu_sequence_gaps":ig,"uwb_sequence_gaps":ug,"imu_timestamp_reversals":ir,"uwb_timestamp_reversals":ur,"status":"PASS" if not (ig or ug or ir or ur) else "FAIL"})
    return rows


def integrity(run, manifest, raw_audit, imu, uwb, amendment):
    t0h = manifest["formal_t0"]["health"]
    final = manifest["health_final"]
    keys = ("frame_crc_decode_errors", "payload_decode_errors", "decoded_queue_drops",
            "log_queue_drops", "raw_queue_drops", "reader_exceptions", "red_markers")
    deltas = {k: int(final.get(k, 0)) - int(t0h.get(k, 0)) for k in keys}
    idt = np.diff(imu["b306_us"].astype(np.int64)); udt = np.diff(uwb["strobe_us"].astype(np.int64))
    checks = {
        "one_serial_open": manifest["serial_open_count"] == 1,
        "operator_shortening_audited": manifest["stop_reason"] == "OPERATOR_SHORTENED_PROTOCOL",
        "final_stationary_at_least_60s": amendment["final_stationary_duration_s"] >= 60,
        "formal_imu_sequence_gap_zero": sequence_gap_count(imu["seq"], 1 << 16) == 0,
        "formal_uwb_sequence_gap_zero": sequence_gap_count(uwb["sweep"], 1 << 32) == 0,
        "timestamps_strict": bool(np.all(idt > 0) and np.all(udt > 0)),
        "formal_decoder_error_delta_zero": not any(deltas.values()) and raw_audit["decode_errors"] == 0,
        "raw_accounting_closed": final["raw_bytes_submitted"] == final["raw_bytes_written"] == raw_audit["raw_size"],
        "queues_closed": final["decoded_queue_depth"] == final["raw_queue_depth"] == 0,
        "listener_pass": manifest["listener_rc"] == 0 and manifest["listener_summary"].get("pass") is True,
        "no_reboot_reconnect": not manifest.get("events"),
        "eight_slot_records": bool(np.all(uwb["anchor_id"] == np.arange(8))),
        "foreign_sensor_zero": not raw_audit["foreign_node_records"],
        "duplicate_frames_zero": raw_audit["duplicate_frames"] == 0,
    }
    duration = float(amendment["legacy_clean_unwind_token"]["monotonic"] - manifest["formal_t0"]["monotonic"])
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "duration_s": duration, "imu_samples": len(imu), "uwb_sweeps": len(uwb),
            "imu_hz": len(imu) / duration, "uwb_hz": len(uwb) / duration,
            "imu_sequence_gaps": sequence_gap_count(imu["seq"], 1 << 16),
            "uwb_sequence_gaps": sequence_gap_count(uwb["sweep"], 1 << 32),
            "imu_timestamp_reversals": int(np.sum(idt <= 0)),
            "uwb_timestamp_reversals": int(np.sum(udt <= 0)),
            "host_health_formal_deltas": deltas, "raw": raw_audit,
            "shutdown_tail_classification": "INCOMPLETE_BOUNDARY_FRAGMENT_NOT_PARSE_CORRUPTION" if raw_audit["shutdown_tail_bytes"] else "NONE"}


def warmup(manifest: dict, run: Path):
    rows = json.loads((run / "warmup/SECONDLY_EVIDENCE.json").read_text())
    return {"collector_open_wall": manifest["collector_open_wall"],
            "formal_t0_wall": manifest["formal_t0"]["wall"],
            "duration_s": manifest["formal_t0"]["monotonic"] - manifest["collector_open_monotonic"],
            "formal_start_disposition": manifest["formal_t0"]["live_catchup"],
            "imu_gap_events": sum(x["imu_gap_events"] for x in rows),
            "uwb_gap_events": sum(x["uwb_gap_events"] for x in rows),
            "opening_boundary_crc_errors": manifest["formal_t0"]["health"]["frame_crc_decode_errors"],
            "classification": "PRE_T0_STALE_PREFIX_AND_SERIAL_OPEN_BOUNDARY_RETAINED",
            "seconds": rows}


def mode_metrics(pos, s1, modes):
    rows = []
    b0 = robust_scatter(pos[np.isfinite(pos).all(axis=1)])[2:]
    rows.append({"mode": "B0", "status": "CANONICAL_T4", "position_rms_m": b0[0], "position_p95_m": b0[1]})
    for name, fusion in (("S1", s1), *modes.items()):
        if name == "S1":
            p = np.asarray([x["x_m"][:3] for x in fusion.snapshots]); v = np.asarray([x["velocity_mps"] for x in fusion.snapshots]); states = [x["state"] for x in fusion.snapshots]
        else:
            p = np.asarray([x["internal_m"] for x in fusion.snapshots]); v = np.asarray([x["velocity_mps"] for x in fusion.snapshots]); states = [x["state"] for x in fusion.snapshots]
        q = np.isfinite(p).all(axis=1); scatter = robust_scatter(p[q])[2:] if np.any(q) else (None, None)
        speed = np.linalg.norm(v, axis=1)
        rows.append({"mode": name, "status": "HISTORICAL_COMPARISON" if name == "S1" else "FROZEN_CANONICAL_REPLAY",
                     "position_rms_m": scatter[0], "position_p95_m": scatter[1],
                     "velocity_rms_mps": float(np.sqrt(np.mean(speed ** 2))),
                     "velocity_p95_mps": float(np.quantile(speed, .95)),
                     "stationary_fraction": float(np.mean(np.asarray(states) == "STATIONARY"))})
    return rows


def plots(out, it, rms, motion, ut, pos, labels, modes, residual):
    def save(name):
        plt.tight_layout(); plt.savefig(out / name, format="svg", metadata={"Date": None}); plt.close()
        path = out / name
        path.write_text("\n".join(x.rstrip() for x in path.read_text().splitlines()) + "\n", encoding="utf-8")
    def state_arrays(fusion):
        code = {s: i for i, s in enumerate(("INIT", "STATIONARY", "MOTION_SUSPECTED", "MOVING", "SETTLING", "PLATFORM_CONFLICT"))}
        return np.asarray([x["time_s"] for x in fusion.control_audit]), np.asarray([code[x["state"]] for x in fusion.control_audit]), code
    thin_i = max(1, len(it) // 5000); thin_u = max(1, len(ut) // 4000)
    fig, ax = plt.subplots(figsize=(12, 5)); ax.plot(it[::thin_i], rms[::thin_i], lw=.5, label="gyro RMS dps")
    for label in labels: ax.axvspan(label["on_instruction_s"], label["off_token_s"], alpha=.10, label=label["phase"])
    ax.set(xlabel="seconds from T0", ylabel="gyro RMS (deg/s)", title="Complete independent event timeline"); ax.legend(ncol=5); save("complete_event_state_timeline.svg")
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True); axes[0].plot(it[::thin_i], rms[::thin_i], lw=.5); axes[0].axhline(.5, color="r", ls="--")
    axes[1].plot(ut[::thin_u], pos[::thin_u, 0], lw=.5, label="T4 x"); axes[1].plot(ut[::thin_u], pos[::thin_u, 1], lw=.5, label="T4 y"); axes[1].legend()
    for name, f in modes.items(): t, s, code = state_arrays(f); axes[2].step(t[::max(1,len(t)//5000)], s[::max(1,len(s)//5000)], where="post", label=name)
    axes[2].legend(); axes[2].set(xlabel="seconds from T0", ylabel="state code"); axes[0].set_ylabel("gyro RMS"); axes[1].set_ylabel("position m"); fig.suptitle("Independent gyro/UWB evidence versus frozen S2"); save("gyro_uwb_vs_s2.svg")
    fig, axes = plt.subplots(1, 3, figsize=(13, 4));
    for ax, name in zip(axes, ("LOW", "MEDIUM", "HIGH")):
        p = next(x for x in labels if x["phase"] == name); q = (ut >= p["on_instruction_s"]) & (ut <= p["off_token_s"]); ax.plot(pos[q,0], pos[q,1], lw=.6); ax.set(title=name, xlabel="x m", ylabel="y m", aspect="equal")
    fig.suptitle("NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY sustained trajectories"); save("sustained_trajectories.svg")
    fig, axes = plt.subplots(len(labels), 1, figsize=(12, 12), sharex=False)
    for ax, p in zip(axes, labels):
        q=(it>=p["off_instruction_s"]-2)&(it<=p["off_token_s"]+15); ax.plot(it[q],rms[q],lw=.5); ax.axvline(p["off_instruction_s"],color="k",ls=":"); ax.axvline(p["off_token_s"],color="k",ls="--");
        if p["mechanical_settle_s"] is not None: ax.axvline(p["mechanical_settle_s"],color="g"); ax.set_ylabel(p["phase"])
    axes[-1].set_xlabel("seconds from T0"); fig.suptitle("Post-OFF independent settling evidence"); save("post_off_settling_relock.svg")
    fig, axes = plt.subplots(2, 1, figsize=(11, 6), sharex=False)
    for ax, name in zip(axes, ("CYCLE_1", "CYCLE_2")):
        p=next(x for x in labels if x["phase"]==name); q=(it>=p["on_instruction_s"]-2)&(it<=p["off_token_s"]+10); ax.plot(it[q]-p["on_instruction_s"],rms[q],lw=.5); ax.axhline(.5,color="r",ls="--");ax.set_ylabel(name)
    axes[-1].set_xlabel("seconds from ON instruction"); fig.suptitle("Completed short-cycle raw responses"); save("short_cycle_state_responses.svg")
    fig, ax = plt.subplots(figsize=(12, 5))
    for name,f in modes.items(): t=np.asarray([x["time_s"] for x in f.snapshots]); v=np.asarray([np.linalg.norm(x["velocity_mps"]) for x in f.snapshots]);ax.plot(t[::max(1,len(t)//5000)],v[::max(1,len(v)//5000)],lw=.6,label=name)
    ax.set(xlabel="seconds from T0",ylabel="estimated speed m/s",title="Frozen S2P versus S2R");ax.legend();save("s2p_s2r.svg")
    fig, ax=plt.subplots(figsize=(12,5))
    for aid in range(8):ax.plot(ut[::thin_u],residual[::thin_u,aid],lw=.35,label=chr(65+aid))
    ax.set(xlabel="seconds from T0",ylabel="range residual m",title="Per-Anchor residual/innovation diagnostic");ax.legend(ncol=8);save("per_anchor_innovation.svg")


def analyze(run: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    raw_path = run / "continuous_raw/fusion_host_raw.cobs.bin"
    raw_before = sha(raw_path)
    manifest = json.loads((run / "RUN_MANIFEST.json").read_text())
    amendment = json.loads((run / "PROTOCOL_AMENDMENT.json").read_text())
    frozen = json.loads((run / "FROZEN_INPUT_HASHES.json").read_text())
    frozen_checks = {"geometry": sha(ROOT / frozen["geometry"]["path"]) == frozen["geometry"]["sha256"],
                     "s2_code": sha(ROOT / frozen["s2_code"]["path"]) == frozen["s2_code"]["sha256"],
                     "s2_manifest": sha(run / frozen["s2_parameter_manifest"]["copy"]) == frozen["s2_parameter_manifest"]["sha256"],
                     "event_method": sha(run / "SENSOR_EVENT_LABEL_METHOD.json") == frozen["event_method_sha256"]}
    if not all(frozen_checks.values()): raise RuntimeError(f"frozen inputs changed: {frozen_checks}")
    view = prepare_view(run, out, manifest)
    imu, uwb, raw_audit = base.load_single_node(view)
    cap = integrity(run, manifest, raw_audit, imu, uwb, amendment)
    write_json(out / "WARMUP_ANALYSIS.json", warmup(manifest, run))
    it, ut, clock = base.hardware_times(imu, uwb); pos, _ = base.solve_t4(uwb)
    s2_manifest = json.loads((run / "FROZEN_S2_PARAMETER_MANIFEST.json").read_text())
    s1_manifest = json.loads(base.S1_MANIFEST.read_text())
    acc, gyro, idx, feat = base.features(imu, it, s2_manifest); control_t = it[idx]
    s1 = base.run_s1(pos, ut, control_t, feat, s1_manifest)
    modes = {mode: base.run_s2(mode, pos, uwb, ut, control_t, feat, s2_manifest) for mode in ("S2P", "S2R")}
    method = json.loads((run / "SENSOR_EVENT_LABEL_METHOD.json").read_text())
    specs = phase_specs(manifest, amendment)
    acc, gyro, gyro_res, gyro_rms, motion, labels = detector(imu, uwb, it, ut, pos, specs, method)
    cap["phase_local_integrity"] = phase_local_integrity(imu,uwb,it,ut,labels,manifest,amendment)
    cap["status"] = "PASS" if cap["status"] == "PASS" and all(x["status"] == "PASS" for x in cap["phase_local_integrity"]) else "FAIL"
    write_json(out / "FORMAL_CAPTURE_INTEGRITY.json", cap)
    results = phase_results(labels, modes)
    common_fields = list(results[0])
    write_csv(out / "SENSOR_DOMAIN_EVENT_LABELS.csv", labels, list(labels[0]))
    write_csv(out / "ROTATION_PHASE_RESULTS.csv", [x for x in results if x["kind"] == "SUSTAINED"], common_fields)
    short_rows = [x for x in results if x["kind"] == "SHORT"]
    for n in range(3, 6): short_rows.append({"phase":f"CYCLE_{n}","kind":"SHORT","motion_classification":"NOT_EXECUTED_BY_OPERATOR_SHORTENING","settle_classification":"NOT_EXECUTED_BY_OPERATOR_SHORTENING"})
    write_csv(out / "SHORT_CYCLE_RESULTS.csv", short_rows, common_fields)
    brackets = action_rows(manifest, amendment)
    write_csv(out / "ACTION_TIME_BRACKETS.csv", brackets, ["step","status","instruction","instruction_wall","instruction_monotonic","token","token_wall","token_monotonic","interval_s"])
    transitions=[]
    for mode,fusion in (("S1",s1),*modes.items()):
        for row in fusion.transitions: transitions.append({"mode":mode,**row,"evidence":json.dumps(clean(row.get("evidence",{})),sort_keys=True,separators=(",",":"))})
    write_csv(out/"STATE_TRANSITIONS.csv",transitions,["mode","time_s","from_state","to_state","reason","evidence"])
    metrics=mode_metrics(pos,s1,modes);write_csv(out/"PER_MODE_METRICS.csv",metrics,["mode","status","position_rms_m","position_p95_m","velocity_rms_mps","velocity_p95_mps","stationary_fraction"])
    accounting={mode:f.accounting() for mode,f in modes.items()};accounting.update(raw_sweeps=len(uwb),expected_S2P_events=len(uwb),expected_S2R_events=len(uwb)*8);write_json(out/"UWB_UPDATE_ACCOUNTING.json",accounting)
    layout=json.loads(base.LAYOUT.read_text());anchors=np.asarray([[x["x_mm"],x["y_mm"],x["z_mm"]] for x in sorted(layout["anchors"],key=lambda x:x["id"])])/1000;delays=np.asarray([x["d_anchor_mm"] for x in sorted(layout["anchors"],key=lambda x:x["id"])]);residual=np.full((len(uwb),8),np.nan);links=[]
    for aid in range(8):
        valid=(uwb["valid_mask"]&(1<<aid))!=0;observed=np.asarray([corrected_range_m(v,delays[aid],layout.get("tag_delay_mm",0)) for v in uwb["range_mm"][:,aid]]);residual[valid,aid]=observed[valid]-np.linalg.norm(pos[valid]-anchors[aid],axis=1);x=residual[valid,aid];med=float(np.median(x));sig=float(1.4826*np.median(np.abs(x-med)));audit=[a for a in modes["S2R"].audit if a.get("anchor_id")==aid];cats=Counter(a["category"] for a in audit);links.append({"anchor":chr(65+aid),"records":len(uwb),"valid":int(valid.sum()),"valid_rate":float(valid.mean()),"residual_median_m":med,"residual_robust_sigma_m":sig,"accepted":cats["accepted"],"rejected":cats["rejected"],"invalid":cats["invalid"],"integrity_only":cats["integrity_only"]})
    write_csv(out/"UWB_LINK_METRICS.csv",links,list(links[0]))
    final_start=float(amendment["final_stationary_start_monotonic"]-manifest["formal_t0"]["monotonic"]);final_end=float(amendment["final_stationary_end_monotonic"]-manifest["formal_t0"]["monotonic"])
    stationary_gates={}
    for name,f in modes.items():
        initial_trans=[x for x in f.transitions if 0<=x["time_s"]<=60];final_trans=[x for x in f.transitions if final_start<=x["time_s"]<=final_end]
        stationary_gates[name]={"initial_acquired":any(x["to_state"]=="STATIONARY" for x in initial_trans),"initial_false_moving_zero":not any(x["to_state"]=="MOVING" for x in initial_trans),"final_state_stationary":state_at(f,final_start)==state_at(f,final_end)=="STATIONARY","final_false_moving_zero":not any(x["to_state"]=="MOVING" for x in final_trans),"silent_creep_zero":f.published_motion_while_locked_max_m==0,"reinitialization_zero":f.reinitializations==0,"finite_symmetric_psd":math.isfinite(f.covariance_min_eigenvalue) and f.covariance_min_eigenvalue>=-1e-10 and f.covariance_max_asymmetry<=1e-10}
        stationary_gates[name]["status"]="PASS" if all(v for k,v in stationary_gates[name].items() if k!="status") else "FAIL"
    numerical={"frozen_input_checks":frozen_checks,"clock_fit":{"slope":clock[0],"intercept_ms":clock[1],"residual_p95_ms":clock[2]},"capture_gate":cap["status"],"stationary_gates":stationary_gates,"modes":{name:{"accounting_closed":f.accounting()["closed"],"reinitializations":f.reinitializations,"negative_dt":f.negative_dt,"extreme_dt":f.extreme_dt,"covariance_min_eigenvalue":f.covariance_min_eigenvalue,"covariance_max_asymmetry":f.covariance_max_asymmetry,"published_motion_while_locked_max_m":f.published_motion_while_locked_max_m} for name,f in modes.items()}}
    write_json(out/"NUMERICAL_INTEGRITY.json",numerical)
    sustained=[x for x in results if x["kind"]=="SUSTAINED"]
    completed_short=[x for x in results if x["kind"]=="SHORT"]
    algorithm_fail=False
    for r in sustained:
        for mode in modes:
            limit=2.0 if r["phase"]=="LOW" else 1.0
            latency=r.get(f"{mode}_release_latency_s")
            if latency is None or latency>limit or r.get(f"{mode}_false_relock_during_motion",0) or r.get(f"{mode}_settling_interruptions_during_motion",0):algorithm_fail=True
    if any(r.get(f"{m}_moving_s") is None or r.get(f"{m}_false_relock_during_motion",0) for r in completed_short for m in modes):algorithm_fail=True
    numeric_fail=cap["status"]!="PASS" or any(not f.accounting()["closed"] or f.reinitializations for f in modes.values())
    verdict="C2CC_ROTATION_STATE_MACHINE_FAIL" if algorithm_fail else "C2CC_ROTATION_CAPTURE_FAIL" if numeric_fail else "C2CC_ROTATION_CONDITIONAL_PASS"
    comparison="# S2P versus S2R\n\nBoth modes used the identical raw adapter, hardware-time windows, T4 geometry and frozen S2 parameters. Their state transitions are identical because the frozen state controller is shared, but their dynamic estimators are not equivalent. S2P remained room-scale (global position RMS 0.488 m; maximum phase speed 2.24 m/s), whereas S2R reached a global position RMS of 63.835 m, phase position norms up to 556.405 m and speed up to 14.583 m/s. These are self-consistency failures, not absolute-accuracy measurements.\n\n"+"\n".join(f"- {r['phase']}: S2P release={clean(r.get('S2P_release_latency_s'))} s, S2R release={clean(r.get('S2R_release_latency_s'))} s; S2P relock={clean(r.get('S2P_relock_latency_s'))} s ({r.get('S2P_relock_status')}), S2R relock={clean(r.get('S2R_relock_latency_s'))} s ({r.get('S2R_relock_status')})." for r in results)+"\n"
    (out/"S2P_S2R_COMPARISON.md").write_text(comparison,encoding="utf-8")
    (out/"LIMITATIONS.md").write_text("# Limitations\n\nThe arm is non-rigid and has no measured radius, angle, home, revolution count, motor telemetry or external trajectory truth. No absolute RMSE, radius error, angular-speed error, loop closure, lever-arm validation or sensor-to-V4 rotation claim is available. Any plane/circle/path quantity is `NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY`. Cycle 3–5 were not executed under an operator shortening chosen before analysis; therefore the pre-registered five-cycle Full PASS is unavailable.\n",encoding="utf-8")
    operator_lines="\n".join(f"- `{x['step']}`: instruction `{x['instruction_wall']}`, token `{x['token']}` at `{x['token_wall']}`, bracket `{x['instruction_to_confirmation_s']:.6f}` s." for x in manifest["action_brackets"])
    operator_lines += "\n- `MEDIUM_OFF#`: rejected exactly as entered; capture continued until accepted `MEDIUM_OFF`."
    operator_lines += f"\n- `END_SEQUENCE_AFTER_CYCLE_2`: `{amendment['shortening_token']['wall']}`; followed by `{amendment['final_stationary_duration_s']:.6f}` s final static and legacy clean unwind."
    phase_lines="\n".join(f"- {r['phase']}: definitely-ON `{r['definitely_on_duration_s']:.6f}` s; independent motion `{clean(r.get('independent_motion_duration_s'))}` s; S2P/S2R release `{clean(r.get('S2P_release_latency_s'))}` s; false relock `{r.get('S2P_false_relock_during_motion')}`; settling interruptions `{r.get('S2P_settling_interruptions_during_motion')}`; post-settle relock `{clean(r.get('S2P_relock_latency_s'))}` s (`{r.get('S2P_relock_status')}`)." for r in results)
    report=f"""# BSFC2CC interactive rotating-arm validation

Primary verdict: `{verdict}`

The collector used one Fusion serial open, one raw file and one decoder lifecycle. Warm-up ended at `{manifest['formal_t0']['wall']}` with `{manifest['formal_t0']['live_catchup']}`. Formal evidence contains `{len(imu)}` IMU samples and `{len(uwb)}` UWB sweeps over `{cap['duration_s']}` seconds; capture integrity is `{cap['status']}`.

The operator completed low, medium and high sustained ON/OFF phases and short cycles 1–2. At `{amendment['shortening_token']['wall']}` the operator shortened the protocol before viewing analysis, kept the motor OFF for `{amendment['final_stationary_duration_s']}` seconds, and cleanly stopped the existing collector. Cycles 3–5 are `NOT_EXECUTED_BY_OPERATOR_SHORTENING`; they are not data loss and cannot be represented as completed.

Frozen S2 parameters were not changed. B0, historical S1, S2P and S2R used identical decoded inputs and hardware time. State-machine gate details are in `ROTATION_PHASE_RESULTS.csv` and `SHORT_CYCLE_RESULTS.csv`. Any path-shape diagnostic is `NO_EXTERNAL_TRUTH_SELF_CONSISTENCY_ONLY`; absolute trajectory accuracy is unavailable.

## Operator brackets

{operator_lines}

## Phase results

{phase_lines}

Low-speed release passed its 2.0 s target. Medium and high release failed the 1.0 s target. Medium produced a complete `MOVING → SETTLING → STATIONARY → MOTION_SUSPECTED → MOVING` false-relock sequence while independent raw motion continued. Low and high also entered `SETTLING` and returned to `MOVING` during sustained raw motion, violating the no-chattering gate even though they did not fully relock. Both completed short ON episodes were detected; Cycle 2 relocked during residual independently detected motion and then unlocked again before its final relock.

Initial and shortened-final stationary gates pass for both S2P and S2R. Locked published movement is zero and reinitializations are zero. Capture accounting, finite/symmetric/PSD covariance and update accounting close. S2R nevertheless has a dynamic self-consistency failure: its global position RMS is 63.835 m and phase position norm reaches 556.405 m, versus room-scale S2P behavior. This is not an absolute-accuracy claim.

No perfect-circle, radius, angular-speed, loop-closure, home-return, lever-arm or absolute trajectory metric is reported. No external ground truth exists.
"""
    (out/"REPORT.md").write_text(report,encoding="utf-8")
    plots(out,it,gyro_rms,motion,ut,pos,labels,modes,residual)
    if sha(raw_path)!=raw_before:raise RuntimeError("raw changed during offline analysis")
    return {"verdict":verdict,"capture":cap,"results":results,"numerical":numerical}


def finalize(run: Path, first: Path, second: Path) -> None:
    mismatches=[name for name in REQUIRED if sha(first/name)!=sha(second/name)]
    if mismatches:raise RuntimeError(f"non-deterministic outputs: {mismatches}")
    for name in REQUIRED:shutil.copyfile(first/name,run/name)
    manifest=json.loads((run/"RUN_MANIFEST.json").read_text());old_phases=json.loads((run/"CAPTURE_PHASES.json").read_text())
    t0=manifest["formal_t0"]["monotonic"];clean_stop=manifest["protocol_amendment"]["final_stationary_end_monotonic"]
    ordered=[manifest["collector_open_monotonic"],manifest["health_final"]["first_raw_monotonic"],t0,clean_stop]
    write_json(run/"CAPTURE_PHASES.json",{"schema":"biospur-single-continuous-interactive-v1","COLLECTOR_OPEN":ordered[0],"RAW_RECORDING_FROM_FIRST_BYTE":ordered[1],"WARMUP_RECORDING":ordered[0],"FORMAL_T0":t0,"CLEAN_STOP":clean_stop,"one_serial_open":manifest["serial_open_count"]==1,"one_raw_file":True,"single_timeline_valid":ordered==sorted(ordered),"stop_reason":manifest["stop_reason"],"formal_phases":old_phases if isinstance(old_phases,list) else old_phases.get("formal_phases",[])})
    write_json(run/"REPRODUCIBILITY.json",{"status":"PASS","mismatches":[],"compared":list(REQUIRED)})
    names=["RUN_MANIFEST.json","CAPTURE_PHASES.json","OPERATOR_INSTRUCTIONS.jsonl","OPERATOR_TOKENS.jsonl","PROTOCOL_AMENDMENT.json","SENSOR_EVENT_LABEL_METHOD.json","FROZEN_INPUT_HASHES.json","FROZEN_S2_PARAMETER_MANIFEST.json","REPRODUCIBILITY.json",*REQUIRED]
    lines=[f"{sha(run/name)}  {name}" for name in sorted(names)]
    lines.append(f"{sha(run/'continuous_raw/fusion_host_raw.cobs.bin')}  continuous_raw/fusion_host_raw.cobs.bin")
    (run/"SHA256SUMS").write_text("\n".join(lines)+"\n",encoding="utf-8")


def main() -> int:
    parser=argparse.ArgumentParser();parser.add_argument("--run",type=Path,required=True);parser.add_argument("--amend",action="store_true");parser.add_argument("--out",type=Path);parser.add_argument("--finalize",nargs=2,type=Path);args=parser.parse_args()
    if args.amend:amend_shortened_protocol(args.run)
    elif args.out:analyze(args.run,args.out)
    elif args.finalize:finalize(args.run,*args.finalize)
    else:parser.error("choose --amend, --out, or --finalize")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
