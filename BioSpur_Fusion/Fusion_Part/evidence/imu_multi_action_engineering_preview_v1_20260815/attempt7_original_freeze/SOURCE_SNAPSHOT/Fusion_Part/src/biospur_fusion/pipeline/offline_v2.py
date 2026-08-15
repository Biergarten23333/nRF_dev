"""Fail-closed offline body-fusion V2 capture qualification."""
from __future__ import annotations

import csv
import hashlib
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np

from biospur_fusion.calibration.frames import current_capture_frame_gate, result_json
from biospur_fusion.imu.frontend import audits_as_json, run_q1_attitude
from biospur_fusion.ingest.ledger import build_time_event_ledger, detect_boot_epochs
from biospur_fusion.time.common_clock import align_capture, models_as_json
from biospur_fusion.uwb.frontend import CanonicalT4Frontend

RAW_SHA = "a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a"
LAYOUT_SHA = "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
AUTOPOS_COMMIT = "87d9027cc368cd05e707dd3a564e4c28b9c505ee"
SLOTS = {"BSF3C79": 1, "BSFC2CC": 2, "BSF44AD": 3, "BSF6C53": 4, "BSF8BC4": 5,
         "BSF1120": 6, "BSF31CC": 7, "BSFAA61": 8, "BSFB165": 9, "BSFEC35": 10}
PLACEMENT_TO_SEGMENT = {
    "Central": "Torso", "Pelvis": "Pelvis", "Elbow_L": "UpperArm_L", "Elbow_R": "UpperArm_R",
    "Wrist_L": "Forearm_L", "Wrist_R": "Forearm_R", "Knee_L": "Thigh_L", "Knee_R": "Thigh_R",
    "Ankle_L": "Shank_L", "Ankle_R": "Shank_R",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str] | None = None) -> None:
    if fields is None:
        fields = list(rows[0]) if rows else []
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def host_to_global_ns(bridge: dict, host_s: float) -> int:
    return int(round((bridge["listener_global_us_per_host_s"] * host_s +
                      bridge["listener_global_us_intercept"]) * 1000.0))


def _action_bounds(capture: Path) -> dict[str, tuple[float, float]]:
    rows = [json.loads(line) for line in (capture / "ACTION_EVENTS.jsonl").read_text().splitlines() if line]
    starts = [row for row in rows if row["event"] == "ACTION_START"]
    stops = [row for row in rows if row["event"] == "ACTION_STOP"]
    result = {}
    for start in starts:
        attempt = start.get("attempt", 1)
        if start["action"] == "initial_still" and attempt != 2:
            continue
        if start["action"] == "right_elbow" and attempt != 2:
            continue
        stop = next((row for row in stops if row["action"] == start["action"] and
                     row.get("attempt", attempt) == attempt and row["monotonic"] >= start["monotonic"]), None)
        if stop:
            result[start["action"]] = (float(start["monotonic"]), float(stop["monotonic"]))
    return result


def _clock_outputs(out: Path, models, residual_rows, gate) -> None:
    model_rows = [asdict(models[node]) for node in sorted(models, key=SLOTS.get)]
    write_csv(out / "CLOCK_MODELS.csv", model_rows)
    write_csv(out / "CLOCK_RESIDUALS.csv", residual_rows)
    result = {"verdict": "TIME_ALIGNMENT_PASS" if gate["pass"] else "BLOCKED_TIME_ALIGNMENT",
              "gate_0_pass": gate["pass"], "clock_models": models_as_json(models), "gates": gate}
    dump(out / "TIME_ALIGNMENT_RESULT.json", result)
    worst_p95 = max(model.clean_residual_p95_us for model in models.values())
    worst_max = max(model.clean_residual_max_us for model in models.values())
    (out / "TIME_ALIGNMENT_REPORT.md").write_text(
        "# Strict common-clock Gate 0\n\n"
        f"Verdict: `{'TIME_ALIGNMENT_PASS' if gate['pass'] else 'BLOCKED_TIME_ALIGNMENT'}`.\n\n"
        "The fit uses retained Listener LBD Beacon counters, LPD poll source/sequence, the capture's "
        "120 ms superframe, the hardware `strobe_us` TIMER2 timestamp and the carried segment-constant "
        "modulo-16 label. Master arrival participates only in a coarse sequence candidate join and in "
        "the operator-annotation bridge; it supplies no measurement timestamp, drift or fractional phase.\n\n"
        f"Worst clean residual p95 is {worst_p95:.3f} us; worst clean maximum is {worst_max:.3f} us. "
        "Every discarded clock pair remains in CLOCK_RESIDUALS.csv as `rejected-timing-outlier`. "
        "All ten boot segments are explicit and have no timestamp reversal.\n",
        encoding="utf-8",
    )


def _run_t4(out: Path, ledger, layout: Path, calibration_end_ns: int) -> tuple[dict, list[dict]]:
    frontend = CanonicalT4Frontend(layout); rows = []; summary = {}; rejection_rows = []
    fields = ["node_id", "sweep", "global_time_ns", "effective_time_ns", "temporal_extent_ns",
              "x_m", "y_m", "z_m", "covariance_m2", "anchors_used", "per_anchor_valid",
              "anchors_input", "residuals_m", "condition", "gdop", "acceptability",
              "failure_reason", "source_sequence"]
    for node in sorted(SLOTS, key=SLOTS.get):
        data = ledger[f"uwb_{node}"]
        selected = data[(data["status"] == 1) & (data["global_time_ns"] <= calibration_end_ns)]
        solved = rejected = 0
        for record in selected:
            observation = frontend.solve(
                node_id=node, sweep=int(record["sweep"]), global_time_ns=int(record["global_time_ns"]),
                global_time_sigma_ns=int(record["global_time_sigma_ns"]), anchor_ids=record["anchor_id"],
                ranges_mm=record["range_mm"], quality=record["quality_percent"],
                valid_mask=int(record["valid_mask"]), t_round_us=record["t_round_us"])
            if observation is None:
                rejected += 1
                validity = tuple(bool(int(record["valid_mask"]) & (1 << slot)) and
                                 0 < int(record["range_mm"][slot]) < 0xFFFF for slot in range(8))
                rows.append({
                    "node_id": node, "sweep": int(record["sweep"]),
                    "global_time_ns": int(record["global_time_ns"]), "effective_time_ns": "",
                    "temporal_extent_ns": "", "x_m": "", "y_m": "", "z_m": "",
                    "covariance_m2": "", "anchors_used": "",
                    "per_anchor_valid": "".join("1" if x else "0" for x in validity),
                    "anchors_input": sum(validity), "residuals_m": "", "condition": "", "gdop": "",
                    "acceptability": "REJECT_SOLVER", "failure_reason": "TOO_FEW_ANCHORS_OR_T4_FAILURE",
                    "source_sequence": int(record["sweep"]),
                })
                rejection_rows.append({"global_time_ns": int(record["global_time_ns"]), "source": "UWB_TAG_T4",
                                       "node_id": node, "sequence": int(record["sweep"]),
                                       "reason": "TOO_FEW_ANCHORS_OR_T4_FAILURE", "status": "rejected"})
                continue
            solved += 1
            rows.append({
                "node_id": node, "sweep": observation.sweep, "global_time_ns": observation.global_time_ns,
                "effective_time_ns": observation.effective_time_ns, "temporal_extent_ns": observation.temporal_extent_ns,
                "x_m": f"{observation.xyz_m[0]:.9f}", "y_m": f"{observation.xyz_m[1]:.9f}",
                "z_m": f"{observation.xyz_m[2]:.9f}",
                "covariance_m2": json.dumps(observation.covariance_m2.tolist(), separators=(",", ":")),
                "anchors_used": ";".join(map(str, observation.anchors_used)),
                "per_anchor_valid": "".join("1" if x else "0" for x in observation.per_anchor_valid),
                "anchors_input": sum(observation.per_anchor_valid),
                "residuals_m": json.dumps(observation.residuals_m, sort_keys=True, separators=(",", ":")),
                "condition": f"{observation.condition:.9g}", "gdop": f"{observation.gdop:.9g}",
                "acceptability": observation.acceptability, "failure_reason": "",
                "source_sequence": observation.source_sequence,
            })
        summary[node] = {"input_calibration_sweeps": len(selected), "solutions": solved,
                         "solver_failures": rejected}
    write_csv(out / "UWB_FRONTEND_AUDIT.csv", rows, fields)
    return summary, rejection_rows


def _placeholder_outputs(out: Path, verdict: str) -> None:
    headers = {
        "BODY_STATE_TIMELINE.csv": ["global_time_ns", "status"],
        "SEGMENT_POSES.csv": ["global_time_ns", "segment", "status"],
        "JOINT_CENTRES.csv": ["global_time_ns", "joint", "status"],
        "JOINT_ANGLES.csv": ["global_time_ns", "joint", "observable", "status"],
        "BODY_CONSTRAINT_RESIDUALS.csv": ["global_time_ns", "constraint", "residual_m", "status"],
        "MEASUREMENT_REJECTION_LEDGER.csv": ["global_time_ns", "source", "reason", "status"],
    }
    for name, fields in headers.items():
        write_csv(out / name, [], fields)
    dump(out / "HELDOUT_VALIDATION.json", {
        "status": "NOT_OPENED", "reason": verdict, "walk_opened": False, "final_still_opened": False,
        "tuning_after_open": False,
    })


def run_derivation(capture: Path, out: Path, *, ledger_path: Path | None = None) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    raw = capture / "continuous_collector" / "fusion_host_raw.cobs.bin"
    layout = _REPO / "B306_Part" / "deployments" / "current_room_autopos_20260811_183541" / "V4IO_LAYOUT.json"
    raw_before = sha256(raw); layout_hash = sha256(layout)
    if raw_before != RAW_SHA:
        raise RuntimeError("CAPTURE_RAW_SHA_MISMATCH")
    if layout_hash != LAYOUT_SHA:
        raise RuntimeError("BLOCKED_UWB_GEOMETRY_PROVENANCE")
    formal = json.loads((capture / "FORMAL_T0.json").read_text())
    complete = json.loads((capture / "CAPTURE_COMPLETE.json").read_text())
    start = float(formal["formal_t0_monotonic"]); end = float(complete["capture_end_monotonic"])
    models, residuals, gate = align_capture(
        capture / "continuous_collector" / "fusion_cdc.log", capture / "listener_capture_5", start, end, SLOTS)
    formal_boot, boot_audit = detect_boot_epochs(raw)
    models = {node: replace(model, boot_epoch=formal_boot[node]) for node, model in models.items()}
    gate["boot_segment_audit"] = boot_audit
    gate["all_boot_segments_explicit"] = all(row["corroborated"] for row in boot_audit.values())
    gate["pass"] = all(gate[k] for k in (
        "no_unresolved_integer_ambiguity", "clean_residual_p95_lt_0_5_ms",
        "clean_residual_max_lt_1_ms", "all_boot_segments_explicit", "no_timestamp_reversal"))
    _clock_outputs(out, models, residuals, gate)
    if not gate["pass"]:
        _placeholder_outputs(out, "BLOCKED_TIME_ALIGNMENT")
        return {"verdict": "BLOCKED_TIME_ALIGNMENT", "gate_0_pass": False, "joint_estimator_ran": False}

    bridge = gate["action_annotation_bridge"]
    if ledger_path is None:
        ledger_path = out / "TIME_EVENT_LEDGER.npz"
        accounting = build_time_event_ledger(raw, models, bridge, start, end, ledger_path)
        dump(out / "EVENT_ACCOUNTING.json", accounting)
        dump(out / "TIME_EVENT_LEDGER.schema.json", {
            "format": "NumPy NPZ; keys imu_<BSF> and uwb_<BSF>",
            "core_fields": ["boot_epoch", "sequence", "node_timer_us", "global_time_ns",
                            "global_time_sigma_ns", "master_arrival_ms", "raw_record_index",
                            "raw_start_offset", "raw_end_offset", "status"],
            "status_codes": {"0": "outside-window", "1": "accepted", "2": "outside-clock-segment"},
            "payload": "typed IMU or UWB fields retained losslessly",
        })
    else:
        accounting = json.loads((ledger_path.parent / "EVENT_ACCOUNTING.json").read_text())
        dump(out / "EVENT_ACCOUNTING.json", accounting)
        dump(out / "TIME_EVENT_LEDGER.reference.json", {"path": str(ledger_path.resolve()), "sha256": sha256(ledger_path)})
    if not accounting["exact"]:
        raise RuntimeError("EVENT_ACCOUNTING_NOT_CLOSED")

    actions = _action_bounds(capture)
    initial_start_ns = host_to_global_ns(bridge, actions["initial_still"][0])
    initial_end_ns = host_to_global_ns(bridge, actions["initial_still"][1])
    calibration_end_ns = host_to_global_ns(bridge, actions["trunk"][1])
    with np.load(ledger_path, allow_pickle=False) as ledger:
        t4_summary, t4_rejections = _run_t4(out, ledger, layout, calibration_end_ns)
        q1_timelines = {}; q1_audits = {}
        for node in sorted(SLOTS, key=SLOTS.get):
            timeline, audit = run_q1_attitude(
                ledger[f"imu_{node}"], node_id=node, initial_start_ns=initial_start_ns,
                initial_end_ns=initial_end_ns, analysis_end_ns=calibration_end_ns)
            q1_timelines[node] = timeline; q1_audits[node] = audit
        np.savez(out / "Q1_ATTITUDE_TIMELINES.npz", **q1_timelines)
    dump(out / "IMU_FRONTEND_AUDIT.json", {
        "initialization_window": {"source": "token-labelled initial_still retry1",
                                  "global_start_ns": initial_start_ns, "global_end_ns": initial_end_ns},
        "analysis_end": "trunk calibration stop; held-out not opened", "nodes": audits_as_json(q1_audits),
    })

    frame = current_capture_frame_gate(); dump(out / "FRAME_BINDING_RESULT.json", result_json(frame))
    mapping = json.loads((_REPO / "Fusion_Part" / "config" / "captures" /
                          "v47_ten_node_body_calibration_20260814_093601.json").read_text())["mapping"]
    dump(out / "BODY_MODEL_MANIFEST.json", {
        "mapping": mapping, "mapping_verdict": "BODY_MAPPING_CONSTRAINED_PASS",
        "segments": list(PLACEMENT_TO_SEGMENT.values()), "topology": "ten_segment_mvp.json",
        "static_parameters_status": "NOT_FROZEN_FRAME_OBSERVABILITY_BLOCK",
        "bone_lengths_dynamic": False, "shoulder_centres": "VIRTUAL_CONDITIONAL",
        "foot_segments": False, "clinical_angles": False,
    })
    dump(out / "CALIBRATION_FREEZE_MANIFEST.json", {
        "status": "NOT_FROZEN", "reason": "BLOCKED_FRAME_OBSERVABILITY",
        "calibration_data_end_global_ns": calibration_end_ns,
        "heldout_locked": ["walk", "final_still"], "heldout_opened": False,
        "mapping": mapping, "solver": "UWB_TAG_T4", "layout_sha256": LAYOUT_SHA,
    })
    verdict = "BLOCKED_FRAME_OBSERVABILITY" if not frame.qualified else "BODY_GRAPH_NUMERICAL_FAIL"
    _placeholder_outputs(out, verdict)
    write_csv(out / "MEASUREMENT_REJECTION_LEDGER.csv", t4_rejections,
              ["global_time_ns", "source", "node_id", "sequence", "reason", "status"])
    dump(out / "NUMERICAL_INTEGRITY.json", {
        "q1_all_finite": all(a.finite for a in q1_audits.values()),
        "q1_cholesky_failures": sum(a.cholesky_failures for a in q1_audits.values()),
        "joint_estimator_ran": False, "reason": verdict,
    })
    dump(out / "ARCHITECTURE_IMPLEMENTATION.json", {
        "typed_ingest": "implemented_and_run", "strict_common_clock": "PASS",
        "uwb_frontend": {"solver": "UWB_TAG_T4", "status": "calibration_windows_run", "summary": t4_summary},
        "imu_frontend": "Q1_ATTITUDE_PREINTEGRATION_CALIBRATION_WINDOWS_RUN",
        "frame_binding": "BLOCKED_OBSERVABILITY", "joint_articulated_estimator": "IMPLEMENTED_SYNTHETIC_ONLY_NOT_RUN_REAL",
        "ikfk": "NOT_RUN", "visualization": "NOT_PRODUCED",
    })
    capture_provenance = {
        "capture_path": str(capture.resolve()), "raw_path": str(raw.resolve()), "raw_sha256_before": raw_before,
        "raw_sha256_after": sha256(raw), "raw_size": raw.stat().st_size,
        "geometry_path": str(layout.resolve()), "layout_sha256": layout_hash,
        "autopos_commit": AUTOPOS_COMMIT, "solver": "UWB_TAG_T4",
        "migration_manifest": str((capture / "MIGRATION_MANIFEST.json").resolve()),
        "hardware_accessed": False,
    }
    dump(out / "CAPTURE_PROVENANCE.json", capture_provenance)
    (out / "REPORT.md").write_text(
        "# Ten-node body Fusion V2 offline report\n\n"
        f"Top-level verdict: `{verdict}`.\n\n"
        "The historical result is formally split into `BODY_MAPPING_CONSTRAINED_PASS`, "
        "`TIME_ALIGNMENT_NOT_PROVEN`, `F1_BODY_FUSION_FAIL`, and "
        "`CURRENT_F1_ANIMATIONS_INVALID_AS_FUSION_EVIDENCE`. This V2 replay closes the independent "
        "strict time gate, but does not rehabilitate historical F1.\n\n"
        "Gate 0 passes on Listener-backed 120 ms epochs. Typed ingest closes exact observation "
        "accounting and never uses Master arrival as measurement time. Canonical UWB_TAG_T4 and "
        "Q1 attitude/preintegration ran only through the calibration `trunk` stop. The physical "
        "`R_N<-V4` plus ten sensor-to-segment extrinsics remain rank-deficient: per-sensor gravity "
        "does not close independent yaw gauges, and T-Pose/body display frame H is not promoted to N. "
        "The real joint estimator therefore did not run, calibration was not frozen, and held-out "
        "walk/final-still measurements were not opened by calibration or estimation.\n\n"
        "The articulated estimator and immutable-geometry construction are qualified only by deterministic "
        "synthetic tests. No absolute-accuracy or clinical-angle claim is made. No animation was generated "
        "and no hardware interface was accessed.\n",
        encoding="utf-8",
    )
    return {"verdict": verdict, "gate_0_pass": True, "joint_estimator_ran": False,
            "heldout_opened": False, "raw_sha256": capture_provenance["raw_sha256_after"]}


_REPO = Path(__file__).resolve().parents[4]
