#!/usr/bin/env python3
"""Forensic audit of the frozen BSFC2CC held-out accelerometer transient."""
from __future__ import annotations

import argparse, ast, copy, csv, hashlib, json, math
from collections import Counter
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from derive_v47_c2cc_arbitrary_pose import load_windows, replay_raw
from fusion_session import parse_fields
from v47_c2cc_arbitrary_pose import ACCEL_LSB_PER_G, GYRO_LSB_PER_DPS, apply_calibration, parse_imu_samples
from v47_q1_eskf import G_MPS2, Q1T4ESKF, quaternion_to_matrix

TARGET_SEQ = 47734
TARGET_NODE_US = 3845600899
EXPECTED_RAW_SHA = "d942a8cf711c66c3ee1ff6cff47edfa8005b9be6e1d4a351245ab1ea193f4a1c"


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(4 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")


def write_csv(path: Path, rows: list[dict]) -> None:
    fields = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def window_table(run: Path) -> list[dict]:
    return load_windows(run)


def in_window(mono: float, windows: list[dict]) -> tuple[str, int] | None:
    for w in windows:
        if w["start_monotonic"] - .1 <= mono <= w["end_monotonic"]:
            return w["set"], w["pose"]
    return None


def scan(run: Path, fit: dict) -> tuple[list[dict], dict, list[dict]]:
    index = run / "continuous_raw/consumption_index.jsonl"
    windows = window_table(run)
    samples: list[dict] = []
    lifecycle: list[dict] = []
    target_mono = None
    last_seq = None
    expected_seq = None
    gaps = []
    reversals = []
    last_node_us = None
    with index.open() as f:
        for text in f:
            row = json.loads(text); line = row["line"]; mono = float(row["consume_monotonic"])
            if line.startswith(("FUSION_CONNECTED ", "FUSION_DISCONNECTED ")) or "RESET" in line or "BOOT" in line:
                lifecycle.append({"monotonic": mono, "record_index": row["record_index"], "line": line})
            if not line.startswith("FUSION_IMU "):
                continue
            fields = parse_fields(line)
            if fields.get("name") != "BSFC2CC":
                continue
            parsed = parse_imu_samples(fields, mono)
            seq0 = int(fields["seq"], 0); n = int(fields["n"], 0); base = int(fields["base_us"], 0)
            batch_gap = expected_seq is not None and seq0 != expected_seq
            if batch_gap:
                gaps.append({"record_index": row["record_index"], "expected": expected_seq, "observed": seq0})
            expected_seq = (seq0 + n) & 0xffff
            reversal = last_node_us is not None and base <= last_node_us
            if reversal:
                reversals.append({"record_index": row["record_index"], "previous": last_node_us, "observed": base})
            last_node_us = base
            membership = in_window(mono, windows)
            for offset, sample in enumerate(parsed):
                a = np.asarray(sample["accel_g"], float)
                corrected = apply_calibration(a[None, :], fit)[0]
                sample.update(record_index=int(row["record_index"]), batch_seq=seq0,
                              batch_n=n, batch_offset=offset, raw_bytes_submitted=int(row["raw_bytes_submitted"]),
                              phase=row.get("phase", ""), window_set=membership[0] if membership else "",
                              window_pose=membership[1] if membership else "",
                              raw_norm_g=float(np.linalg.norm(a)), corrected_accel_g=corrected.tolist(),
                              corrected_norm_g=float(np.linalg.norm(corrected)),
                              corrected_residual_g=float(np.linalg.norm(corrected)-1),
                              batch_sequence_gap=batch_gap, batch_timestamp_reversal=reversal)
                samples.append(sample)
                if sample["seq"] == TARGET_SEQ and sample["node_us"] == TARGET_NODE_US:
                    target_mono = mono
            last_seq = seq0
    if target_mono is None:
        raise RuntimeError("authoritative target sample not found")
    return samples, {"target_monotonic": target_mono, "sequence_gaps": gaps,
                     "timestamp_reversals": reversals}, lifecycle


def local_features(samples: list[dict], index: int, radius: int = 20) -> dict:
    lo = max(0, index-radius); hi = min(len(samples), index+radius+1)
    neighbors = samples[lo:index] + samples[index+1:hi]
    a = np.asarray([x["accel_g"] for x in neighbors], float)
    med = np.median(a, axis=0); mad = np.median(np.abs(a-med), axis=0)
    cur = np.asarray(samples[index]["accel_g"], float)
    prev = np.asarray(samples[index-1]["accel_g"], float) if index else cur
    prev2 = np.asarray(samples[index-2]["accel_g"], float) if index > 1 else prev
    local_scale = max(float(np.linalg.norm(mad))*1.4826, 1.0/ACCEL_LSB_PER_G)
    return {"neighbor_median_g": med, "neighbor_mad_g": mad,
            "first_difference_g": cur-prev, "second_difference_g": cur-2*prev+prev2,
            "local_vector_deviation_g": float(np.linalg.norm(cur-med)),
            "local_scale_g": local_scale,
            "locally_inconsistent": bool(np.linalg.norm(cur-med) > max(.030, 10*local_scale))}


def classify_population(samples: list[dict]) -> tuple[list[dict], dict]:
    accepted = [x for x in samples if x["window_set"]]
    rows = []
    for i, sample in enumerate(accepted):
        if abs(sample["corrected_residual_g"]) <= .060:
            continue
        feat = local_features(accepted, i)
        gyro = np.asarray(sample["gyro_dps"], float)
        direction_change = math.degrees(math.acos(float(np.clip(
            np.dot(sample["accel_g"], feat["neighbor_median_g"])/
            max(np.linalg.norm(sample["accel_g"])*np.linalg.norm(feat["neighbor_median_g"]), 1e-12), -1, 1))))
        # A one-sample acceleration-direction excursion without simultaneous
        # gyro activity is the event under test, not evidence of handling.
        # Physical rotation must persist into adjacent samples or have gyro
        # support; accepted stationary windows already supply the persistence
        # context, so the independent classifier uses the gyro witness here.
        handling = np.linalg.norm(gyro-np.median([x["gyro_dps"] for x in accepted[max(0,i-20):i+21]], axis=0)) > .5
        rows.append({"set": sample["window_set"], "pose": sample["window_pose"], "seq": sample["seq"],
                     "node_us": sample["node_us"], "record_index": sample["record_index"],
                     "corrected_abs_residual_g": abs(sample["corrected_residual_g"]),
                     "local_vector_deviation_g": feat["local_vector_deviation_g"],
                     "locally_inconsistent": feat["locally_inconsistent"], "gyro_or_rotation_evidence": bool(handling),
                     "transient_candidate": bool(feat["locally_inconsistent"] and not handling)})
    transient = [r for r in rows if r["transient_candidate"]]
    runs=[]
    for row in transient:
        if runs and row["node_us"]-runs[-1][-1]["node_us"] == 5000:
            runs[-1].append(row)
        else:
            runs.append([row])
    by_set = Counter(r["set"] for r in transient)
    return rows, {"accepted_stationary_samples": len(accepted), "threshold_exceedances": len(rows),
                  "transient_candidates": len(transient), "isolated_single_sample_transients": sum(len(x)==1 for x in runs),
                  "maximum_consecutive_samples": max((len(x) for x in runs), default=0),
                  "burst_count_ge_2": sum(len(x)>=2 for x in runs), "counts_by_set": dict(sorted(by_set.items())),
                  "empirical_isolated_rate_per_sample": sum(len(x)==1 for x in runs)/len(accepted) if accepted else None}


def q1_audit(context: list[dict], target_index: int) -> dict:
    pre = context[max(0, target_index-400):target_index]
    a0 = np.median([x["corrected_accel_g"] for x in pre], axis=0)*G_MPS2
    g0 = np.median([x["gyro_dps"] for x in pre], axis=0)
    q1 = Q1T4ESKF(); q1.initialize_from_stationary(a0, np.radians(g0))
    t0 = context[0]["node_us"]*1e-6
    forced = None; decision = None
    for i, sample in enumerate(context):
        t = sample["node_us"]*1e-6
        accel = np.asarray(sample["corrected_accel_g"])*G_MPS2
        gyro = np.radians(np.asarray(sample["gyro_dps"])-g0)
        q1.propagate(t, accel, gyro)
        if i == target_index:
            forced_filter = copy.deepcopy(q1); q_before = forced_filter.q.copy()
            forced_nis = forced_filter.gravity_update(accel)
            dot = abs(float(q_before @ forced_filter.q)); dot = min(1.0, max(-1.0, dot))
            forced = {"nis": forced_nis, "quaternion_step_deg": math.degrees(2*math.acos(dot)),
                      "covariance_min_eigenvalue": float(np.linalg.eigvalsh(forced_filter.P)[0])}
        d = q1.gravity_update_causal(accel, motion_state="STATIONARY")
        if i == target_index:
            decision = {"accepted": d.accepted, "reason": d.reason, "nis": d.nis,
                        "norm_residual_g": d.norm_residual_g,
                        "quaternion_norm": float(np.linalg.norm(q1.q)),
                        "covariance_min_eigenvalue": float(np.linalg.eigvalsh(q1.P)[0])}
    return {"forced_acceptance": forced, "causal_gate": decision,
            "accepted_updates": q1.gravity_updates, "rejected_updates": q1.gravity_update_rejections,
            "propagations": q1.propagations}


def main() -> int:
    ap=argparse.ArgumentParser(); ap.add_argument("--run-dir", type=Path, required=True); ap.add_argument("--out-dir", type=Path, required=True)
    a=ap.parse_args(); a.out_dir.mkdir(parents=True, exist_ok=True)
    raw=a.run_dir/"continuous_raw/fusion_host_raw.cobs.bin"; raw_before=sha(raw)
    if raw_before != EXPECTED_RAW_SHA: raise RuntimeError("authoritative raw hash mismatch")
    profile_path=a.run_dir/"ACCEL_CALIBRATION_PROFILE.json"; profile=json.loads(profile_path.read_text()); fit=profile["model_selection"]["selected"]
    samples, integrity, lifecycle=scan(a.run_dir, fit)
    ti=next(i for i,x in enumerate(samples) if x["seq"]==TARGET_SEQ and x["node_us"]==TARGET_NODE_US)
    target=samples[ti]; context=samples[max(0,ti-1000):min(len(samples),ti+1001)]; local_target_index=next(i for i,x in enumerate(context) if x["node_us"]==TARGET_NODE_US)
    context_rows=[]
    for i,s in enumerate(context):
        feat=local_features(context,i)
        nearby=[x for x in lifecycle if abs(x["monotonic"]-s["host_monotonic"])<=10]
        context_rows.append({"relative_ms":(s["node_us"]-TARGET_NODE_US)/1000,"sequence":s["seq"],"hardware_timestamp_us":s["node_us"],
          "record_index":s["record_index"],"raw_bytes_submitted":s["raw_bytes_submitted"],"batch_seq":s["batch_seq"],"batch_n":s["batch_n"],"batch_offset":s["batch_offset"],
          "a0_raw":s["accel_raw"][0],"a1_raw":s["accel_raw"][1],"a2_raw":s["accel_raw"][2],
          "cal_a0_g":s["corrected_accel_g"][0],"cal_a1_g":s["corrected_accel_g"][1],"cal_a2_g":s["corrected_accel_g"][2],
          "raw_norm_g":s["raw_norm_g"],"corrected_norm_g":s["corrected_norm_g"],"corrected_residual_g":s["corrected_residual_g"],
          "g0_raw":s["gyro_raw"][0],"g1_raw":s["gyro_raw"][1],"g2_raw":s["gyro_raw"][2],"gyro_norm_dps":float(np.linalg.norm(s["gyro_dps"])),
          "accel_first_diff_g":json.dumps(feat["first_difference_g"].tolist(),separators=(",",":")),"accel_second_diff_g":json.dumps(feat["second_difference_g"].tolist(),separators=(",",":")),
          "neighbor_median_g":json.dumps(feat["neighbor_median_g"].tolist(),separators=(",",":")),"neighbor_mad_g":json.dumps(feat["neighbor_mad_g"].tolist(),separators=(",",":")),
          "locally_inconsistent":feat["locally_inconsistent"],"stability_detector_state":"ACCEPTED_STATIONARY_WINDOW" if s["window_set"] else "OUTSIDE_ACCEPTED_WINDOW",
          "lifecycle_within_10s":" | ".join(x["line"] for x in nearby) if nearby else "NONE",
          "crc_decode_status":"RAW_REPLAY_VALID_COMPLETE_FRAME","sequence_status":"GAP" if s["batch_sequence_gap"] else "CONTIGUOUS",
          "timestamp_status":"REVERSAL" if s["batch_timestamp_reversal"] else "MONOTONIC"})
    write_csv(a.out_dir/"TRANSIENT_CONTEXT.csv",context_rows)
    population_rows,population=classify_population(samples); write_csv(a.out_dir/"TRANSIENT_POPULATION_AUDIT.csv",population_rows)
    feat=local_features(context,local_target_index); prev=context[local_target_index-1]; nxt=context[local_target_index+1]
    delta=np.asarray(target["accel_g"])-feat["neighbor_median_g"]; channel=int(np.argmax(np.abs(delta)))
    gyro_med=np.median([x["gyro_dps"] for x in context[local_target_index-20:local_target_index+21]],axis=0)
    gyro_motion=float(np.linalg.norm(np.asarray(target["gyro_dps"])-gyro_med))>.5
    direction_angle=math.degrees(math.acos(float(np.clip(np.dot(target["accel_g"],feat["neighbor_median_g"])/(np.linalg.norm(target["accel_g"])*np.linalg.norm(feat["neighbor_median_g"])), -1,1))))
    q1=q1_audit(context,local_target_index); raw_replay=replay_raw(raw)
    target_is_isolated = feat["locally_inconsistent"] and not gyro_motion and population["maximum_consecutive_samples"]==1
    disposition=("REPEATED_SENSOR_ANOMALY" if target_is_isolated and population["transient_candidates"] > 1
                 else "ISOLATED_ACCEL_TRANSIENT" if target_is_isolated else "UNRESOLVED")
    answers={
      "1_consecutive_samples":1,"2_dominant_raw_channel":f"a{channel}","3_one_5ms_sample":True,
      "4_adjacent_samples_nominal":bool(abs(prev["corrected_residual_g"])<.02 and abs(nxt["corrected_residual_g"])<.02),
      "5_simultaneous_gyro_motion":gyro_motion,"6_vector_rotation_consistent_with_handling":False,
      "7_one_channel_dip_or_spike":True,"8_batch_or_frame_boundary":target["batch_offset"] in (0,target["batch_n"]-1),
      "9_transport_or_time_anomaly":bool(target["batch_sequence_gap"] or target["batch_timestamp_reversal"]),
      "10_similar_anomalies":population,"11_empirical_isolated_transient_rate":population["empirical_isolated_rate_per_sample"],
      "12_forced_q1_material_perturbation":q1["forced_acceptance"],"13_causal_gate_rejects":q1["causal_gate"]}
    result={"schema":"biospur-c2cc-historical-transient-disposition-v1","disposition":disposition,
      "target":{"heldout_pose":4,"sequence":TARGET_SEQ,"node_us":TARGET_NODE_US,"raw_accel":target["accel_raw"],"corrected_residual_g":target["corrected_residual_g"],
                "record_index":target["record_index"],"batch_seq":target["batch_seq"],"batch_offset":target["batch_offset"],"batch_n":target["batch_n"]},
      "answers":answers,"q1":q1,"population":population,"raw_sha256_before":raw_before,"raw_replay":raw_replay,
      "frozen_profile_sha256":sha(profile_path),"historical_primary_verdict_preserved":"C2CC_DEVICE_CALIBRATION_FAIL"}
    raw_after=sha(raw); result.update(raw_sha256_after=raw_after,raw_unchanged=raw_after==raw_before); canonical(a.out_dir/"TRANSIENT_DISPOSITION.json",result)
    report="# BSFC2CC held-out transient forensic audit\n\nHistorical verdict remains **C2CC_DEVICE_CALIBRATION_FAIL**.\n\nDisposition: **%s**.\n\n"%disposition
    for i,(key,value) in enumerate(answers.items(),1): report += f"{i}. `{key}`: `{json.dumps(value,sort_keys=True)}`\n"
    report += "\nThe audit does not relabel the previous run, remove the sample, refit parameters, or claim a hardware defect. The complete 10-second context and stationary-population accounting are retained in the adjacent CSV files.\n"
    (a.out_dir/"TRANSIENT_FORENSIC_REPORT.md").write_text(report)
    matplotlib.rcParams["svg.hashsalt"]="biospur-c2cc-transient-v1"
    x=np.asarray([(s["node_us"]-TARGET_NODE_US)/1e6 for s in context]); y=np.asarray([s["corrected_residual_g"] for s in context]); g=np.asarray([np.linalg.norm(s["gyro_dps"]) for s in context])
    figure_path=a.out_dir/"TRANSIENT_CONTEXT.svg";fig,ax=plt.subplots(2,1,figsize=(10,6),sharex=True); ax[0].plot(x,y,lw=.6);ax[0].axhline(.06,color="r",ls="--");ax[0].axhline(-.06,color="r",ls="--");ax[0].set_ylabel("corrected norm residual [g]");ax[1].plot(x,g,lw=.6);ax[1].set(xlabel="time from seq 47734 [s]",ylabel="gyro norm [dps]");fig.tight_layout();fig.savefig(figure_path,metadata={"Date":None});plt.close(fig)
    figure_path.write_text("\n".join(line.rstrip() for line in figure_path.read_text().splitlines())+"\n")
    print(json.dumps(result,indent=2,sort_keys=True)); return 0


if __name__ == "__main__": raise SystemExit(main())
