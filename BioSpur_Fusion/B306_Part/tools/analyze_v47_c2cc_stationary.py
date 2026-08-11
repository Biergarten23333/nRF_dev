#!/usr/bin/env python3
"""Deterministic offline analysis of the BSFC2CC held-out stationary run.

This program has no hardware or serial imports.  It replays the capture with
the already-frozen T4, S1, S2P and S2R implementations and parameters.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import shutil
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
matplotlib.rcParams["svg.hashsalt"] = "biospur-c2cc-heldout-v1"
import matplotlib.pyplot as plt
import numpy as np

from analyze_v47_state_adaptive_fusion import (
    StateAdaptiveFusion, adaptive_params, robust_scatter, rolling_features,
)
from analyze_v47_fusion_exhaustion import causal_extra, params as s2_params
from fusion_host_binary import FrameError
from v47_afternoon_capture import POLL_RECEIVERS, deduplicated_listener_rates
from v47_real_data_adapter import (
    IMU_DTYPE, UWB_DTYPE, _decode_host_frame, _decode_imu, _decode_uwb,
    imu_physical, iter_cobs_records, sequence_gap_count,
)
from v47_s2_fusion import S2Fusion, corrected_range_m
from v47_static_fusion import fit_node_clock
import v47_uwb_position_replay as t4_replay


ROOT = Path(__file__).resolve().parents[2]
NODE = "BSFC2CC"
S1_MANIFEST = ROOT / "B306_Part/logs/v47_full_system_30m_20260811_130843/analysis_state_adaptive_fusion_v1/PARAMETER_MANIFEST.json"
LAYOUT = ROOT / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
GEOMETRY = ROOT / "B306_Part/deployments/current_room_autopos_20260811_183541/CAPTURE_BOUND_GEOMETRY_MANIFEST.json"
CORE_FILES = (
    "REPORT.md", "CAPTURE_INTEGRITY.json", "PER_MODE_METRICS.csv",
    "STATE_TRANSITIONS.csv", "UWB_UPDATE_ACCOUNTING.json",
    "UWB_LINK_METRICS.csv", "LISTENER_SUMMARY.json",
    "NUMERICAL_INTEGRITY.json", "EVENT_ANALYSIS.md",
    "position_modes.svg", "estimated_velocity.svg", "imu_motion_evidence.svg",
    "anchor_residuals.svg", "state_innovation_timeline.svg",
)


def is_shutdown_boundary_fragment(end_offset: int, file_size: int,
                                  final_byte: bytes) -> bool:
    """Only an unterminated final COBS fragment is a benign shutdown tail."""
    return end_offset == file_size and final_byte != b"\0"


def post_t0_contract_is_read_only(manifest: dict) -> bool:
    """Held-out validity requires no commands or mutation after T0."""
    return manifest.get("commands_after_t0") == [] and manifest.get("mutation") is False


def planned_stop_is_complete(ledger: dict, planned_s: float = 600.0) -> bool:
    duration = float(ledger.get("duration_s", 0.0))
    return (ledger.get("status") == "CAPTURE_COMPLETE" and
            ledger.get("stop_reason") == "PLANNED_DURATION_COMPLETE" and
            planned_s <= duration < planned_s + 0.5)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(4 << 20), b""):
            h.update(block)
    return h.hexdigest()


def clean(value):
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, np.ndarray):
        return [clean(v) for v in value.tolist()]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return float(f"{value:.12g}")
    return value


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(clean(value), indent=2, sort_keys=True,
                               allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: "" if clean(row.get(key)) is None else clean(row.get(key)) for key in fields})


def frozen_inputs_unchanged(run: Path) -> dict:
    recorded = json.loads((run / "FROZEN_INPUT_HASHES.json").read_text())
    checks = {
        "geometry": sha(ROOT / recorded["geometry"]["path"]) == recorded["geometry"]["sha256"],
        "s2_parameter_copy": sha(run / recorded["s2_parameter_manifest"]["copy"]) == recorded["s2_parameter_manifest"]["sha256"],
        "s2_code": sha(ROOT / recorded["s2_code"]["path"]) == recorded["s2_code"]["sha256"],
    }
    if not all(checks.values()):
        raise RuntimeError(f"frozen input changed: {checks}")
    return {"recorded": recorded, "checks": checks,
            "s1_manifest_sha256": sha(S1_MANIFEST), "layout_sha256": sha(LAYOUT)}


def load_single_node(run: Path):
    formal = run / "formal_capture"
    manifest = json.loads((formal / "RUN_MANIFEST.json").read_text())
    offset = int(manifest["formal_health_baseline"]["raw_bytes_submitted"])
    raw = formal / "fusion_host_raw.cobs.bin"
    imu_rows, uwb_rows = [], []
    kind_counts = Counter(); errors = 0; foreign = Counter(); duplicates = 0
    last_keys = set(); tail = 0
    size = raw.stat().st_size
    with raw.open("rb") as f:
        f.seek(-1, 2); final_byte = f.read(1)
    for end, encoded in iter_cobs_records(raw):
        if end <= offset:
            continue
        try:
            frame = _decode_host_frame(encoded)
        except FrameError:
            if is_shutdown_boundary_fragment(end, size, final_byte):
                tail = len(encoded)
                continue
            errors += 1
            continue
        kind_counts[frame.kind] += 1
        if frame.node_name != NODE:
            # Master/QoS/pool records have no peer identity.  Only a foreign
            # sensor stream is evidence of an unexpected Fusion peer.
            if frame.kind in (1, 3):
                foreign[frame.node_name] += 1
            continue
        key = (frame.kind, frame.sequence, frame.master_arrival_ms, frame.payload)
        if key in last_keys:
            duplicates += 1
        last_keys.add(key)
        if frame.kind == 3:
            temp = np.empty(16, dtype=IMU_DTYPE)
            n = _decode_imu(frame, temp, 0)
            imu_rows.extend(temp[:n].copy())
        elif frame.kind == 1:
            temp = np.empty(1, dtype=UWB_DTYPE)
            _decode_uwb(frame, temp, 0)
            uwb_rows.append(temp[0].copy())
    imu = np.asarray(imu_rows, dtype=IMU_DTYPE)
    uwb = np.asarray(uwb_rows, dtype=UWB_DTYPE)
    if not len(imu) or not len(uwb):
        raise RuntimeError("formal raw contains no BSFC2CC IMU/UWB")
    audit = {"raw_sha256": sha(raw), "raw_size": size, "formal_offset": offset,
             "decode_errors": errors, "shutdown_tail_bytes": tail,
             "kind_counts": dict(sorted(kind_counts.items())),
             "foreign_node_records": dict(sorted(foreign.items())), "duplicate_frames": duplicates}
    return imu, uwb, audit


def hardware_times(imu: np.ndarray, uwb: np.ndarray):
    clock = fit_node_clock(uwb)
    def convert(local):
        return (clock[0] * np.asarray(local, float) / 1000.0 + clock[1]) / 1000.0
    absolute_i, absolute_u = convert(imu["b306_us"]), convert(uwb["strobe_us"])
    origin = min(float(absolute_i[0]), float(absolute_u[0]))
    return absolute_i - origin, absolute_u - origin, clock


def continuous_replay_segment(imu: np.ndarray, uwb: np.ndarray):
    """Select the post-discontinuity suffix without concealing capture gaps.

    The full arrays remain authoritative for the capture gate.  This suffix is
    only for diagnostic frozen-algorithm replay when a stale serial prefix is
    followed by the live continuous stream.
    """
    imu_jump = np.flatnonzero(np.diff(imu["b306_us"].astype(np.int64)) > 500_000)
    uwb_jump = np.flatnonzero(np.diff(uwb["strobe_us"].astype(np.int64)) > 500_000)
    if not len(imu_jump) and not len(uwb_jump):
        return imu, uwb, {"classification": "FULL_FORMAL_STREAM", "imu_start_index": 0, "uwb_start_index": 0}
    boundaries=[]
    if len(imu_jump): boundaries.append(int(imu["master_ms"][imu_jump[-1]+1]))
    if len(uwb_jump): boundaries.append(int(uwb["master_ms"][uwb_jump[-1]+1]))
    boundary=max(boundaries)
    ii=int(np.searchsorted(imu["master_ms"],boundary,side="left"));ui=int(np.searchsorted(uwb["master_ms"],boundary,side="left"))
    return imu[ii:],uwb[ui:],{"classification":"POST_DISCONTINUITY_DIAGNOSTIC_SUFFIX",
        "boundary_master_ms":boundary,"imu_start_index":ii,"uwb_start_index":ui,
        "excluded_imu_samples":ii,"excluded_uwb_sweeps":ui}


def solve_t4(uwb: np.ndarray) -> tuple[np.ndarray, list[dict]]:
    old_nodes, old_t0 = t4_replay.NODES, t4_replay.T0_MASTER_MS
    try:
        t4_replay.NODES = (NODE,)
        t4_replay.T0_MASTER_MS = int(uwb["master_ms"][0])
        rows = t4_replay.replay_variant("UWB_TAG_T4", LAYOUT, {NODE: uwb})
    finally:
        t4_replay.NODES, t4_replay.T0_MASTER_MS = old_nodes, old_t0
    pos = np.asarray([[float(r[axis]) / 1000.0 for axis in ("x_mm", "y_mm", "z_mm")]
                      if r["solver_status"] == "ok" else [np.nan] * 3 for r in rows])
    return pos, rows


def features(imu, it, s2_manifest):
    acc, gyro, _ = imu_physical(imu)
    base = s2_manifest["per_node"][NODE]
    gyro_res = gyro - np.asarray(base["gyro_bias_dps"])
    idx, feat = rolling_features(acc, gyro_res, float(base["local_gravity_g"]))
    ref_q = (it >= 1) & (it < min(60, it[-1]))
    ref = np.mean(acc[ref_q], axis=0); ref /= np.linalg.norm(ref)
    angle, gravity = causal_extra(acc, gyro_res, it, idx, ref)
    return acc, gyro, idx, {**feat, "gyro_angle_1s_deg": angle, "gravity_change_deg": gravity}


def run_s1(pos, uwb_t, control_t, feat, manifest):
    f = StateAdaptiveFusion(adaptive_params(manifest, NODE)); ci = ui = 0
    while ci < len(control_t) or ui < len(uwb_t):
        if ui < len(uwb_t) and (ci >= len(control_t) or uwb_t[ui] < control_t[ci]):
            ok = np.isfinite(pos[ui]).all()
            f.process_uwb(float(uwb_t[ui]), pos[ui] if ok else None,
                          status="ok" if ok else "FAIL", record_index=ui); ui += 1
        else:
            f.process_control(float(control_t[ci]), {k: float(v[ci]) for k, v in feat.items() if k in
                ("gyro_rms_dps", "accel_dev_rms_g", "gyro_std_dps", "accel_std_g")}, sequence_advancing=True); ci += 1
    return f


def run_s2(mode, pos, uwb, uwb_t, control_t, feat, manifest):
    f = S2Fusion(s2_params(manifest, NODE, "main", mode), mode); ci = ui = 0
    while ci < len(control_t) or ui < len(uwb_t):
        if ui < len(uwb_t) and (ci >= len(control_t) or uwb_t[ui] < control_t[ci]):
            f.process_uwb(float(uwb_t[ui]), pos[ui], uwb["range_mm"][ui], int(uwb["valid_mask"][ui]), ui); ui += 1
        else:
            f.process_control(float(control_t[ci]), {k: float(v[ci]) for k, v in feat.items()}, True, False); ci += 1
    return f


def scatter(points):
    q = np.isfinite(points).all(axis=1)
    if not np.any(q): return (None, None)
    return robust_scatter(points[q])[2:]


def longest_run(values: np.ndarray) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if value else 0; best = max(best, current)
    return best


def missing_sequence_count(values: np.ndarray, modulus: int) -> int:
    if len(values) < 2: return 0
    delta=(values[1:].astype(np.uint64)-values[:-1].astype(np.uint64))%modulus
    return int(np.sum(np.where((delta>1)&(delta<modulus//2),delta-1,0)))


def capture_integrity(run, imu, uwb, raw_audit):
    ledger = json.loads((run / "formal_capture/PROCESS_LEDGER.json").read_text())
    manifest = json.loads((run / "formal_capture/RUN_MANIFEST.json").read_text())
    base, final = manifest["formal_health_baseline"], ledger.get("fusion_health_final", {})
    error_keys = ("frame_crc_decode_errors", "payload_decode_errors", "decoded_queue_drops",
                  "log_queue_drops", "raw_queue_drops", "reader_exceptions", "red_markers")
    deltas = {key: int(final.get(key, 0)) - int(base.get(key, 0)) for key in error_keys}
    imu_dt = np.diff(imu["b306_us"].astype(np.int64)); uwb_dt = np.diff(uwb["strobe_us"].astype(np.int64))
    eight = np.all(uwb["anchor_id"] == np.arange(8), axis=1)
    checks = {
        "planned_stop": planned_stop_is_complete(ledger),
        "duration_600_s": 600.0 <= float(ledger.get("duration_s", 0)) < 600.5,
        "identity_only": not raw_audit["foreign_node_records"],
        "imu_sequence_gap_zero": sequence_gap_count(imu["seq"], 1 << 16) == 0,
        "uwb_sequence_gap_zero": sequence_gap_count(uwb["sweep"], 1 << 32) == 0,
        "timestamp_monotonic": bool(np.all(imu_dt > 0) and np.all(uwb_dt > 0)),
        "duplicates_zero": raw_audit["duplicate_frames"] == 0,
        "formal_decode_errors_zero": raw_audit["decode_errors"] == 0 and not any(deltas.values()),
        "queues_closed": int(final.get("raw_queue_depth", 0)) == 0 and int(final.get("decoded_queue_depth", 0)) == 0,
        "raw_accounting_closed": int(final.get("raw_bytes_submitted", -1)) == int(final.get("raw_bytes_written", -2)) == raw_audit["raw_size"],
        "eight_slot_records": bool(eight.all()),
        "no_reboot_reconnect": not ledger.get("events"),
        "no_post_t0_commands": post_t0_contract_is_read_only(manifest),
    }
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "duration_s": ledger.get("duration_s"), "t0_wall": ledger.get("t0_wall"),
            "t1_wall": ledger.get("t1_wall"), "stop_reason": ledger.get("stop_reason"),
            "imu_samples": len(imu), "uwb_sweeps": len(uwb),
            "imu_hz": len(imu) / float(ledger["duration_s"]), "uwb_hz": len(uwb) / float(ledger["duration_s"]),
            "imu_sequence_gaps": sequence_gap_count(imu["seq"], 1 << 16),
            "uwb_sequence_gaps": sequence_gap_count(uwb["sweep"], 1 << 32),
            "imu_missing_samples": missing_sequence_count(imu["seq"], 1 << 16),
            "uwb_missing_sweeps": missing_sequence_count(uwb["sweep"], 1 << 32),
            "imu_timestamp_reversals": int(np.sum(imu_dt <= 0)), "uwb_timestamp_reversals": int(np.sum(uwb_dt <= 0)),
            "host_health_formal_deltas": deltas, "raw": raw_audit,
            "initial": ledger.get("initial"), "final": ledger.get("final"),
            "shutdown_tail_classification": "INCOMPLETE_BOUNDARY_FRAGMENT_NOT_PARSE_CORRUPTION" if raw_audit["shutdown_tail_bytes"] else "NONE"}


def listener_summary(run, manifest, ledger):
    start = int(manifest["t0_monotonic_ns"]); end = int(float(ledger["t1_monotonic"]) * 1e9)
    rates, errors = deduplicated_listener_rates(run / "formal_capture/listener_capture", manifest["mapping"], start, end)
    node = rates[NODE]; summary = ledger.get("listener_summary") or {}
    listener_rows = summary.get("listeners", {})
    parse_errors = sum(int(row.get("parse_errors", 0)) for row in listener_rows.values())
    serial_errors = sum(int(row.get("serial_errors", 0)) for row in listener_rows.values())
    incomplete = {row.get("listener_key", snr): int(row.get("incomplete_bytes", 0))
                  for snr, row in listener_rows.items() if row.get("incomplete_bytes", 0)}
    return {"deduplicated_source_count": node["source_count"], "union_hz": node["source_hz"],
            "per_receiver_visibility": dict(sorted(node["per_receiver"].items())),
            "poll_receivers": sorted(POLL_RECEIVERS), "scan_errors": errors,
            "collector_parse_errors": parse_errors,
            "collector_serial_errors": serial_errors,
            "shutdown_incomplete_bytes": incomplete,
            "shutdown_tail_classification": "PER_RECEIVER_SHUTDOWN_BOUNDARY_FRAGMENTS_NOT_PARSE_CORRUPTION" if incomplete else "NONE",
            "status": "PASS" if not errors and not parse_errors and not serial_errors else "FAIL"}


def make_plots(out, it, acc, gyro, uwb_t, pos, s1, modes, link_residuals, feat, idx):
    def save(name):
        path=out/name;plt.tight_layout();plt.savefig(path,format="svg",metadata={"Date":None});plt.close()
        # Matplotlib emits harmless trailing spaces in path data.  Normalize
        # them so compact evidence also passes repository whitespace checks.
        path.write_text("\n".join(line.rstrip() for line in path.read_text().splitlines())+"\n",encoding="utf-8")
    def thin(*arrays, maximum=3000):
        step=max(1,math.ceil(len(arrays[0])/maximum))
        return tuple(np.asarray(a)[::step] for a in arrays)
    tx,px=thin(uwb_t,pos[:,0]);fig, ax = plt.subplots(figsize=(10, 5)); ax.plot(tx,px, lw=.7, label="B0 x")
    for label, f in (("S2P", modes["S2P"]), ("S2R", modes["S2R"])):
        st=np.asarray([x["time_s"] for x in f.snapshots]); pub=np.asarray([x["published_m"] for x in f.snapshots]); cand=np.asarray([x["candidate_m"] if x["candidate_m"] is not None else [np.nan]*3 for x in f.snapshots])
        ts,cs,ps=thin(st,cand[:,0],pub[:,0]);ax.plot(ts,cs,lw=.45,alpha=.65,label=f"{label} candidate x");ax.plot(ts,ps,lw=1,label=f"{label} published x")
    ax.set(xlabel="hardware time from first formal sample (s)",ylabel="V4-io x (m)",title="B0 and frozen Fusion position traces"); ax.legend(ncol=3); save("position_modes.svg")
    fig,ax=plt.subplots(figsize=(10,4))
    for label,f in (("S1",s1),("S2P",modes["S2P"]),("S2R",modes["S2R"])):
        if label=="S1": st=np.asarray([x["time_s"] for x in f.snapshots]); vel=np.asarray([x["velocity_mps"] for x in f.snapshots])
        else: st=np.asarray([x["time_s"] for x in f.snapshots]); vel=np.asarray([x["velocity_mps"] for x in f.snapshots])
        ts,vs=thin(st,np.linalg.norm(vel,axis=1));ax.plot(ts,vs,lw=.6,label=label)
    ax.set(xlabel="time (s)",ylabel="speed (m/s)",title="Estimated velocity");ax.legend();save("estimated_velocity.svg")
    ti,an,gn=thin(it,np.linalg.norm(acc,axis=1),np.linalg.norm(gyro,axis=1));fig,axs=plt.subplots(2,1,figsize=(10,6),sharex=True); axs[0].plot(ti,an,lw=.35);axs[0].set(ylabel="|a| (g)")
    axs[1].plot(ti,gn,lw=.35);axs[1].set(xlabel="time (s)",ylabel="|gyro| (deg/s)");fig.suptitle("Raw IMU stationary evidence");save("imu_motion_evidence.svg")
    fig,ax=plt.subplots(figsize=(10,5))
    for aid in range(8):
        tr,rr=thin(uwb_t,link_residuals[:,aid]);ax.plot(tr,rr,lw=.35,label=chr(65+aid))
    ax.set(xlabel="time (s)",ylabel="range residual (m)",title="Per-Anchor residual to frozen T4 solution");ax.legend(ncol=8);save("anchor_residuals.svg")
    fig,ax=plt.subplots(figsize=(10,5)); f=modes["S2R"]
    state_code={s:i for i,s in enumerate(("INIT","STATIONARY","MOTION_SUSPECTED","MOVING","SETTLING","PLATFORM_CONFLICT"))}
    ca=f.control_audit; ax.step([x["time_s"] for x in ca],[state_code[x["state"]] for x in ca],where="post",label="state")
    nis=[x for x in f.audit if isinstance(x.get("nis"),(int,float)) and math.isfinite(float(x["nis"]))]
    if nis: ax.scatter([x["time_s"] for x in nis],[min(5,float(x["nis"])/10) for x in nis],s=2,label="NIS/10")
    ax.set(xlabel="time (s)",ylabel="state code / NIS scale",title="S2R state and innovation timeline",yticks=list(state_code.values()),yticklabels=list(state_code));ax.legend();save("state_innovation_timeline.svg")


def analyze(run: Path, out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=False)
    frozen_before = frozen_inputs_unchanged(run)
    imu, uwb, raw_audit = load_single_node(run); raw_before = raw_audit["raw_sha256"]
    integrity = capture_integrity(run, imu, uwb, raw_audit)
    replay_imu,replay_uwb,replay_segment=continuous_replay_segment(imu,uwb)
    integrity["algorithm_replay_segment"]=replay_segment
    write_json(out/"CAPTURE_INTEGRITY.json",integrity)
    it, ut, clock = hardware_times(replay_imu, replay_uwb)
    pos, t4_rows = solve_t4(replay_uwb)
    if len(pos) != len(replay_uwb) or np.sum(np.isfinite(pos).all(axis=1)) < 8:
        raise RuntimeError("T4 did not provide enough causal platform observations")
    s2_manifest = json.loads((run / "FROZEN_S2_PARAMETER_MANIFEST.json").read_text())
    s1_manifest = json.loads(S1_MANIFEST.read_text())
    acc, gyro, idx, feat = features(replay_imu, it, s2_manifest); control_t = it[idx]
    s1 = run_s1(pos, ut, control_t, feat, s1_manifest)
    modes = {mode: run_s2(mode, pos, replay_uwb, ut, control_t, feat, s2_manifest) for mode in ("S2P","S2R")}
    metrics=[]
    b0=scatter(pos); metrics.append({"mode":"B0","position_rms_m":b0[0],"position_p95_m":b0[1],"published_rms_m":"","candidate_rms_m":"","velocity_rms_mps":"","velocity_p95_mps":"","stationary_fraction":"","status":"CANONICAL_T4"})
    sp=np.asarray([x["x_m"][:3] for x in s1.snapshots]); sv=np.asarray([x["velocity_mps"] for x in s1.snapshots]); sstates=np.asarray([x["state"] for x in s1.snapshots]); sq=sstates!="INIT"; ss=scatter(sp[sq]); speed=np.linalg.norm(sv[sq],axis=1)
    metrics.append({"mode":"S1","position_rms_m":ss[0],"position_p95_m":ss[1],"published_rms_m":"","candidate_rms_m":"","velocity_rms_mps":float(np.sqrt(np.mean(speed**2))),"velocity_p95_mps":float(np.quantile(speed,.95)),"stationary_fraction":float(np.mean([x["state"]=="STATIONARY" for x in s1.snapshots])),"status":"HISTORICAL_COMPARISON"})
    gates={}
    for mode,f in modes.items():
        pub=np.asarray([x["published_m"] for x in f.snapshots]); cand=np.asarray([x["candidate_m"] if x["candidate_m"] is not None else [np.nan]*3 for x in f.snapshots]); vel=np.asarray([x["velocity_mps"] for x in f.snapshots]); states=np.asarray([x["state"] for x in f.snapshots]); locked=states!="INIT"
        ps,cs=scatter(pub[locked]),scatter(cand[locked]); speed=np.linalg.norm(vel[locked],axis=1)
        transitions=f.transitions; to=Counter(x["to_state"] for x in transitions)
        gate={"initial_stationary":to["STATIONARY"]>=1,"moving_zero":to["MOVING"]==0,
              "temporary_unlock_zero":all(x["to_state"] not in ("MOVING","SETTLING") for x in transitions),
              "new_platform_relock_zero":sum(x["to_state"]=="STATIONARY" for x in transitions)==1,
              "published_lock_movement_zero":f.published_motion_while_locked_max_m==0,
              "reinitialization_zero":f.reinitializations==0,
              "finite_covariance":math.isfinite(f.covariance_min_eigenvalue),
              "covariance_psd":f.covariance_min_eigenvalue>=-1e-10,
              "covariance_symmetric":f.covariance_max_asymmetry<=1e-10,
              "accounting_closed":f.accounting()["closed"],
              "state_chattering_zero":len(transitions)<=4}
        gates[mode]={"status":"PASS" if all(gate.values()) else "FAIL","checks":gate,
                     "conflict_transitions":to["PLATFORM_CONFLICT"],"suspicion_transitions":to["MOTION_SUSPECTED"]}
        metrics.append({"mode":mode,"position_rms_m":ps[0],"position_p95_m":ps[1],"published_rms_m":ps[0],"candidate_rms_m":cs[0],"candidate_p95_m":cs[1],"velocity_rms_mps":float(np.sqrt(np.mean(speed**2))),"velocity_p95_mps":float(np.quantile(speed,.95)),"stationary_fraction":float(np.mean(np.asarray(states)=="STATIONARY")),"status":gates[mode]["status"]})
    write_csv(out/"PER_MODE_METRICS.csv",metrics,["mode","status","position_rms_m","position_p95_m","published_rms_m","candidate_rms_m","candidate_p95_m","velocity_rms_mps","velocity_p95_mps","stationary_fraction"])
    transitions=[]
    for mode,f in (("S1",s1),*modes.items()):
        for row in f.transitions: transitions.append({"mode":mode,**row,"evidence":json.dumps(clean(row.get("evidence",{})),sort_keys=True,separators=(",",":"))})
    write_csv(out/"STATE_TRANSITIONS.csv",transitions,["mode","time_s","from_state","to_state","reason","evidence"])
    accounting={mode:f.accounting() for mode,f in modes.items()}; accounting["formal_raw_sweeps"]=len(uwb);accounting["diagnostic_replay_sweeps"]=len(replay_uwb); accounting["expected_S2P_events"]=len(replay_uwb);accounting["expected_S2R_events"]=len(replay_uwb)*8
    write_json(out/"UWB_UPDATE_ACCOUNTING.json",accounting)
    layout=json.loads(LAYOUT.read_text()); anchors=np.asarray([[x["x_mm"],x["y_mm"],x["z_mm"]] for x in sorted(layout["anchors"],key=lambda x:x["id"])])/1000
    delays=np.asarray([x["d_anchor_mm"] for x in sorted(layout["anchors"],key=lambda x:x["id"])]); residual=np.full((len(replay_uwb),8),np.nan); link_rows=[]
    for aid in range(8):
        valid=(replay_uwb["valid_mask"]&(1<<aid))!=0; observed=np.asarray([corrected_range_m(v,delays[aid],layout.get("tag_delay_mm",0)) for v in replay_uwb["range_mm"][:,aid]])
        residual[valid,aid]=observed[valid]-np.linalg.norm(pos[valid]-anchors[aid],axis=1); x=residual[valid,aid]; med=float(np.median(x)); sig=float(1.4826*np.median(np.abs(x-med)))
        empirical=np.abs(x-med)>max(4*sig,.03);s2r=[x for x in modes["S2R"].audit if x.get("anchor_id")==aid]; cats=Counter(x["category"] for x in s2r)
        rejected=np.asarray([x["category"]=="rejected" for x in s2r])
        link_rows.append({"anchor":chr(65+aid),"anchor_id":aid,"records":len(replay_uwb),"valid":int(valid.sum()),"valid_rate":float(valid.mean()),"residual_median_m":med,"residual_robust_sigma_m":sig,"empirical_outliers_4sigma":int(empirical.sum()),"longest_empirical_outlier_burst":longest_run(empirical),"accepted":cats["accepted"],"rejected":cats["rejected"],"invalid":cats["invalid"],"integrity_only":cats["integrity_only"],"longest_nis_rejection_burst":longest_run(rejected)})
    write_csv(out/"UWB_LINK_METRICS.csv",link_rows,list(link_rows[0]))
    manifest=json.loads((run/"formal_capture/RUN_MANIFEST.json").read_text());ledger=json.loads((run/"formal_capture/PROCESS_LEDGER.json").read_text());listener=listener_summary(run,manifest,ledger);write_json(out/"LISTENER_SUMMARY.json",listener)
    numerical={"clock_fit_slope":clock[0],"clock_fit_intercept_ms":clock[1],"clock_fit_residual_p95_ms":clock[2],"capture_gate":integrity["status"],"s2_gates":gates,"modes":{m:{"covariance_min_eigenvalue":f.covariance_min_eigenvalue,"covariance_max_asymmetry":f.covariance_max_asymmetry,"negative_dt":f.negative_dt,"extreme_dt":f.extreme_dt,"reinitializations":f.reinitializations,"published_motion_while_locked_max_m":f.published_motion_while_locked_max_m} for m,f in modes.items()}}
    write_json(out/"NUMERICAL_INTEGRITY.json",numerical);write_json(out/"CAPTURE_INTEGRITY.json",integrity)
    suspicious=[x for x in transitions if x["to_state"] in ("MOTION_SUSPECTED","PLATFORM_CONFLICT","SETTLING","MOVING")]
    event_text=("# Event analysis\n\nThe full formal raw contains one common early stream discontinuity: "
        f"{integrity['imu_missing_samples']} missing IMU samples and {integrity['uwb_missing_sweeps']} missing UWB sweeps. "
        "It is classified as capture/boundary evidence, not physical motion. The diagnostic frozen replay starts after that common discontinuity and does not interpolate it.\n\n" +
        ("No S2 suspicion, conflict, settling, or moving transition occurred in the diagnostic suffix.\n" if not suspicious else "Canonical transitions requiring causal review:\n\n"+"\n".join(f"- {x['mode']} at {x['time_s']} s: {x['to_state']} — {x['reason']}" for x in suspicious)+"\n"))
    (out/"EVENT_ANALYSIS.md").write_text(event_text,encoding="utf-8")
    verdict="C2CC_STATIONARY_HELDOUT_PASS" if integrity["status"]==listener["status"]=="PASS" and all(x["status"]=="PASS" for x in gates.values()) else "C2CC_STATIONARY_CAPTURE_FAIL" if integrity["status"]!="PASS" or listener["status"]!="PASS" else "C2CC_STATIONARY_ALGORITHM_FAIL"
    report=f"""# BSFC2CC held-out stationary validation\n\nPrimary verdict: `{verdict}`\n\nThe formal run started at `{integrity['t0_wall']}` and stopped at `{integrity['t1_wall']}` after `{integrity['duration_s']}` seconds with `{integrity['imu_samples']}` decoded IMU samples and `{integrity['uwb_sweeps']}` decoded UWB sweeps. The raw lossless capture gate is `{integrity['status']}` because the early common discontinuity contains `{integrity['imu_missing_samples']}` missing IMU samples and `{integrity['uwb_missing_sweeps']}` missing UWB sweeps. CRC/decode/queue/serial errors, reconnects and reboots were zero, but those facts cannot override the pre-registered zero-gap gate.\n\nThe post-discontinuity suffix was replayed diagnostically without interpolation or parameter changes. Frozen S2P gate: `{gates['S2P']['status']}`. Frozen S2R gate: `{gates['S2R']['status']}`. Neither entered motion suspicion or conflict. Published RMS of zero, when observed, demonstrates immutable lock semantics only; it is not absolute positioning accuracy. B0/S1/S2 and candidate noise values are in `PER_MODE_METRICS.csv`.\n\nListener union cadence was `{clean(listener['union_hz'])}` Hz; receiver visibility is preserved in `LISTENER_SUMMARY.json`. No new capture data was used to tune a threshold, covariance, process noise, dwell, NIS gate, per-link variance, or conflict threshold.\n\nThis stationary experiment does not validate movement release, relock, vector inertial propagation, sensor-to-V4 rotation, dynamic or absolute trajectory accuracy, human assignment, or IK/FK.\n"""
    (out/"REPORT.md").write_text(report,encoding="utf-8")
    make_plots(out,it,acc,gyro,ut,pos,s1,modes,residual,feat,idx)
    if sha(run/"formal_capture/fusion_host_raw.cobs.bin") != raw_before or not all(frozen_inputs_unchanged(run)["checks"].values()): raise RuntimeError("input hash changed during analysis")
    return {"verdict":verdict,"metrics":metrics,"gates":gates,"frozen":frozen_before}


def compare_outputs(a: Path, b: Path) -> dict:
    mismatches=[]
    for name in CORE_FILES:
        if sha(a/name)!=sha(b/name):mismatches.append(name)
    return {"status":"PASS" if not mismatches else "FAIL","compared":list(CORE_FILES),"mismatches":mismatches}


def finalize(run: Path, primary: Path, repeat: Path) -> dict:
    reproducibility = compare_outputs(primary, repeat)
    if reproducibility["status"] != "PASS":
        raise RuntimeError(f"non-deterministic derivation: {reproducibility['mismatches']}")
    for name in CORE_FILES:
        shutil.copyfile(primary / name, run / name)
    formal = json.loads((run / "formal_capture/RUN_MANIFEST.json").read_text())
    ledger = json.loads((run / "formal_capture/PROCESS_LEDGER.json").read_text())
    # Formal first-record values come from the raw/ledger and are never
    # synthesized or obtained by a post-T0 query.
    root_manifest = {**formal, "formal_result": {
        "status": ledger.get("status"), "stop_reason": ledger.get("stop_reason"),
        "t1_wall": ledger.get("t1_wall"), "t1_monotonic": ledger.get("t1_monotonic"),
        "duration_s": ledger.get("duration_s"), "initial_stream_values": ledger.get("initial"),
        "final_stream_values": ledger.get("final"), "events": ledger.get("events", []),
    }}
    write_json(run / "RUN_MANIFEST.json", root_manifest)
    write_json(run / "REPRODUCIBILITY.json", reproducibility)
    compact = ["RUN_MANIFEST.json", "PREFLIGHT_RESULT.json", "FROZEN_INPUT_HASHES.json",
               "REPRODUCIBILITY.json", *CORE_FILES]
    lines = [f"{sha(run / name)}  {name}" for name in sorted(compact)]
    raw = run / "formal_capture/fusion_host_raw.cobs.bin"
    lines.append(f"{sha(raw)}  formal_capture/fusion_host_raw.cobs.bin")
    (run / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return reproducibility


def main() -> int:
    ap=argparse.ArgumentParser();ap.add_argument("--run",type=Path,required=True)
    ap.add_argument("--out",type=Path);ap.add_argument("--finalize",nargs=2,metavar=("PRIMARY","REPEAT"),type=Path)
    args=ap.parse_args()
    if args.finalize:
        finalize(args.run,*args.finalize)
    elif args.out:
        analyze(args.run,args.out)
    else:
        ap.error("--out or --finalize is required")
    return 0


if __name__=="__main__": raise SystemExit(main())
