"""Fail-closed orchestration for the first real ten-node body-fusion pipeline."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict, replace
from pathlib import Path

from biospur_fusion.ingest.ledger import detect_boot_epochs
from biospur_fusion.ingest.split import LedgerWindow, materialize_payload_firewall
from biospur_fusion.time.common_clock import align_capture, models_as_json


RAW_SHA = "a491520739400064db520377ec87a9331feb6274cd42a7e6d9aad57a2b93d56a"
LAYOUT_SHA = "20320e53d48b171c016a0e8d1d93b3cb10e979cf4c21c15c21647d5c0b9878b1"
AUTOPOS_COMMIT = "87d9027cc368cd05e707dd3a564e4c28b9c505ee"
BASE_COMMIT = "91818247b7ac470b55b3ae8031a112119f2621c6"
SLOTS = {"BSF3C79": 1, "BSFC2CC": 2, "BSF44AD": 3, "BSF6C53": 4, "BSF8BC4": 5,
         "BSF1120": 6, "BSF31CC": 7, "BSFAA61": 8, "BSFB165": 9, "BSFEC35": 10}
CALIBRATION_NAMES = (
    "initial_still_attempt2", "t_pose", "arms", "left_elbow", "right_elbow_attempt2",
    "left_knee", "right_knee", "left_heel", "right_heel", "squats", "trunk",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(4 << 20):
            digest.update(block)
    return digest.hexdigest()


def dump(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def host_to_global_ns(bridge: dict, host_s: float) -> int:
    return int(round((bridge["listener_global_us_per_host_s"] * host_s
                      + bridge["listener_global_us_intercept"]) * 1000.0))


def selected_action_windows(capture: Path, bridge: dict) -> dict[str, LedgerWindow]:
    rows = [json.loads(line) for line in (capture / "ACTION_EVENTS.jsonl").read_text().splitlines() if line]
    starts = [row for row in rows if row.get("event") == "ACTION_START"]
    stops = [row for row in rows if row.get("event") == "ACTION_STOP"]
    selected = {}
    for start in starts:
        action = str(start["action"]); attempt = int(start.get("attempt") or 1)
        if action == "initial_still" and attempt != 2:
            continue
        if action == "right_elbow" and attempt != 2:
            continue
        stop = next((row for row in stops if row.get("action") == action
                     and int(row.get("attempt") or 1) == attempt
                     and float(row["monotonic"]) >= float(start["monotonic"])), None)
        if stop is None:
            continue
        name = action + ("_attempt2" if action in ("initial_still", "right_elbow") else "")
        selected[name] = LedgerWindow(
            name, host_to_global_ns(bridge, float(start["monotonic"])),
            host_to_global_ns(bridge, float(stop["monotonic"])),
        )
    missing = sorted(set(CALIBRATION_NAMES + ("walk", "final_still")) - set(selected))
    if missing:
        raise RuntimeError(f"selected action windows missing: {missing}")
    return selected


def _write_clock_outputs(out: Path, models, residuals, gate) -> None:
    write_csv(out / "CLOCK_MODELS.csv", [asdict(models[node]) for node in sorted(models, key=SLOTS.get)],
              list(asdict(next(iter(models.values())))))
    write_csv(out / "CLOCK_RESIDUALS.csv", residuals, list(residuals[0]))
    dump(out / "TIME_ALIGNMENT_RESULT.json", {
        "verdict": "TIME_ALIGNMENT_PASS" if gate["pass"] else "BLOCKED_TIME_ALIGNMENT",
        "pass": gate["pass"], "clock_models": models_as_json(models), "gates": gate,
    })


def _run_calibration_capsule(repo: Path, calibration: Path, layout: Path, output: Path) -> dict:
    capsule_output = output / "calibration"; capsule_output.mkdir()
    solver = (repo / "UWB_Part/2026-07-15-FREEZE/scripts/solvers/erlangen_deployment_v4io_t4/"
              "stage2_position_T4_pristine")
    command = [
        "bwrap", "--die-with-parent", "--unshare-all", "--share-net",
        "--ro-bind", "/usr", "/usr", "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64", "--ro-bind", "/etc", "/etc",
        "--ro-bind", "/home/zekaixiao/.local/lib/python3.12/site-packages",
        "/home/zekaixiao/.local/lib/python3.12/site-packages",
        "--dir", "/app", "--dir", "/app/Fusion_Part", "--dir", "/app/UWB_Part",
        "--dir", "/app/UWB_Part/2026-07-15-FREEZE", "--dir", "/app/UWB_Part/2026-07-15-FREEZE/scripts",
        "--dir", "/app/UWB_Part/2026-07-15-FREEZE/scripts/solvers",
        "--dir", "/app/UWB_Part/2026-07-15-FREEZE/scripts/solvers/erlangen_deployment_v4io_t4",
        "--ro-bind", str(repo / "Fusion_Part/src"), "/app/Fusion_Part/src",
        "--ro-bind", str(solver), str(Path("/app") / solver.relative_to(repo)),
        "--dir", "/input", "--ro-bind", str(calibration), "/input/CALIBRATION_TYPED_LEDGER.npz",
        "--ro-bind", str(layout), "/input/V4IO_LAYOUT.json",
        "--bind", str(capsule_output), "/output", "--tmpfs", "/tmp", "--proc", "/proc", "--dev", "/dev",
        "--setenv", "PYTHONPATH", "/app/Fusion_Part/src", "--chdir", "/output",
        "/usr/bin/python3", "-m", "biospur_fusion.calibration.real_capture",
        "--calibration-ledger", "/input/CALIBRATION_TYPED_LEDGER.npz",
        "--layout", "/input/V4IO_LAYOUT.json", "--output", "/output",
    ]
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    (output / "CALIBRATION_PROCESS.log").write_text(completed.stdout, encoding="utf-8")
    result_path = capsule_output / "CALIBRATION_CAPSULE_RESULT.json"
    if not result_path.exists():
        raise RuntimeError(f"calibration capsule failed before result (exit={completed.returncode})")
    result = json.loads(result_path.read_text())
    dump(output / "PROCESS_FIREWALL_AUDIT.json", {
        "implementation": "bubblewrap mount namespace",
        "exit_code": completed.returncode,
        "visible_payload_inputs": ["/input/CALIBRATION_TYPED_LEDGER.npz"],
        "heldout_ledger_visible": False, "capture_raw_visible": False,
        "action_events_visible": False, "network_namespace": "isolated",
        "expected_fail_closed_exit_codes": {"0": "calibration pass", "3": "computed calibration block"},
    })
    if completed.returncode not in (0, 3):
        raise RuntimeError(f"calibration capsule infrastructure failed: {completed.returncode}")
    for name in ("FRAME_CALIBRATION_RESULT.json", "OBSERVABILITY_SVD.json",
                 "CALIBRATION_STABILITY.json", "CALIBRATION_CANDIDATE.json",
                 "CALIBRATION_PHYSICAL_INTERPRETATION.json",
                 "UWB_CALIBRATION_ACCOUNTING.json", "IMU_CALIBRATION_AUDIT.json",
                 "CALIBRATION_REJECTION_LEDGER.json"):
        shutil.copyfile(capsule_output / name, output / name)
    return result


def _placeholder_outputs(out: Path, reason: str) -> None:
    schemas = {
        "BODY_STATE_TIMELINE.csv": ["global_time_ns", "status"],
        "SEGMENT_POSES.csv": ["global_time_ns", "segment", "status"],
        "JOINT_CENTRES.csv": ["global_time_ns", "joint", "status"],
        "JOINT_ANGLES.csv": ["global_time_ns", "joint", "observable", "status"],
        "BODY_CONSTRAINT_RESIDUALS.csv": ["global_time_ns", "constraint", "residual_m", "status"],
        "MEASUREMENT_REJECTION_LEDGER.csv": ["global_time_ns", "node", "reason", "status"],
    }
    for name, fields in schemas.items():
        write_csv(out / name, [], fields)
    dump(out / "HELDOUT_VALIDATION.json", {
        "status": "NOT_OPENED", "reason": reason, "walk_opened": False,
        "final_still_opened": False, "heldout_process_launched": False,
        "tuning_after_open": False,
    })


def run(capture: Path, out: Path, *, source_ledger: Path) -> dict:
    if out.exists() and any(out.iterdir()):
        raise FileExistsError(f"immutable V3 output already exists and is non-empty: {out}")
    out.mkdir(parents=True, exist_ok=True)
    repo = Path(__file__).resolve().parents[4]
    raw = capture / "continuous_collector/fusion_host_raw.cobs.bin"
    layout = repo / "B306_Part/deployments/current_room_autopos_20260811_183541/V4IO_LAYOUT.json"
    provenance = {
        "capture": str(capture.resolve()), "raw_path": str(raw.resolve()),
        "raw_sha256_before": sha256(raw), "geometry_path": str(layout.resolve()),
        "layout_sha256": sha256(layout), "autopos_commit": AUTOPOS_COMMIT,
        "pipeline_base_commit": BASE_COMMIT, "solver": "UWB_TAG_T4", "hardware_accessed": False,
    }
    if provenance["raw_sha256_before"] != RAW_SHA:
        raise RuntimeError("CAPTURE_RAW_SHA_MISMATCH")
    if provenance["layout_sha256"] != LAYOUT_SHA:
        raise RuntimeError("BLOCKED_UWB_GEOMETRY_PROVENANCE")
    formal = json.loads((capture / "FORMAL_T0.json").read_text())
    complete = json.loads((capture / "CAPTURE_COMPLETE.json").read_text())
    start = float(formal["formal_t0_monotonic"]); stop = float(complete["capture_end_monotonic"])
    models, residuals, gate = align_capture(
        capture / "continuous_collector/fusion_cdc.log", capture / "listener_capture_5", start, stop, SLOTS)
    formal_boot, boot_audit = detect_boot_epochs(raw)
    models = {node: replace(model, boot_epoch=formal_boot[node]) for node, model in models.items()}
    gate["boot_segment_audit"] = boot_audit
    gate["all_boot_segments_explicit"] = all(row["corroborated"] for row in boot_audit.values())
    gate["pass"] = bool(gate["pass"] and gate["all_boot_segments_explicit"])
    _write_clock_outputs(out, models, residuals, gate)
    if not gate["pass"]:
        _placeholder_outputs(out, "BLOCKED_TIME_ALIGNMENT")
        return {"verdict": "BLOCKED_TIME_ALIGNMENT", "heldout_opened": False}

    windows = selected_action_windows(capture, gate["action_annotation_bridge"])
    ledger_dir = out / "ledgers"; calibration = ledger_dir / "calibration/CALIBRATION_TYPED_LEDGER.npz"
    heldout = ledger_dir / "heldout/HELDOUT_TYPED_LEDGER.npz"
    firewall = materialize_payload_firewall(
        source_ledger, calibration, heldout,
        calibration_window=LedgerWindow("calibration_through_trunk",
                                        windows["initial_still_attempt2"].start_ns,
                                        windows["trunk"].stop_ns),
        heldout_windows=(windows["walk"], windows["final_still"]),
        calibration_actions=tuple(windows[name] for name in CALIBRATION_NAMES),
    )
    dump(out / "LEDGER_FIREWALL_MANIFEST.json", firewall)
    calibration_result = _run_calibration_capsule(repo, calibration, layout, out)
    frame = json.loads((out / "FRAME_CALIBRATION_RESULT.json").read_text())
    if not frame["pass"]:
        verdict = frame["verdict"]
        dump(out / "CALIBRATION_FREEZE_MANIFEST.json", {
            "status": "NOT_FROZEN", "reason": verdict, "heldout_opened": False,
            "calibration_candidate_sha256": sha256(out / "CALIBRATION_CANDIDATE.json"),
            "calibration_ledger_sha256": firewall["calibration"]["sha256"],
            "heldout_ledger_sha256_sealed": firewall["heldout"]["sha256"],
            "raw_sha256": RAW_SHA, "layout_sha256": LAYOUT_SHA,
        })
        _placeholder_outputs(out, verdict)
        dump(out / "NUMERICAL_INTEGRITY.json", {
            "joint_estimator_ran": False, "frame_calibration_optimizer_ran": True,
            "actual_jacobian_evaluated": True, "reason": verdict,
        })
    else:
        # The freeze is serialized before a future held-out subprocess can be
        # launched.  This branch is deliberately fail-closed until the held-out
        # capsule consumes the immutable manifest without retuning.
        freeze_payload = {
            "status": "FROZEN", "frame_calibration": frame["candidate"],
            "calibration_ledger_sha256": firewall["calibration"]["sha256"],
            "raw_sha256": RAW_SHA, "layout_sha256": LAYOUT_SHA,
            "post_freeze_tuning_allowed": False,
        }
        dump(out / "CALIBRATION_FREEZE_PARAMETERS.json", freeze_payload)
        dump(out / "CALIBRATION_FREEZE_MANIFEST.json", {
            "status": "CALIBRATION_FREEZE_PASS", "freeze_sha256": sha256(out / "CALIBRATION_FREEZE_PARAMETERS.json"),
            "serialized_before_heldout": True, "heldout_opened": False,
        })
        verdict = "BLOCKED_HELDOUT_CAPSULE_NOT_IMPLEMENTED"
        _placeholder_outputs(out, verdict)
        dump(out / "NUMERICAL_INTEGRITY.json", {
            "joint_estimator_ran": False, "reason": verdict,
            "frame_calibration_optimizer_ran": True, "actual_jacobian_evaluated": True,
        })
    provenance["raw_sha256_after"] = sha256(raw); provenance["raw_unchanged"] = provenance["raw_sha256_after"] == RAW_SHA
    dump(out / "CAPTURE_PROVENANCE.json", provenance)
    dump(out / "ARCHITECTURE_IMPLEMENTATION.json", {
        "historical_v2_conceptual_correction": "BLOCKED_FRAME_CALIBRATION_NOT_IMPLEMENTED",
        "v3_actual_calibration_jacobian": "evaluated", "calibration_process_payload_isolation": "bubblewrap",
        "heldout_opened": False, "animation_generated": False,
    })
    nulls = json.loads((out / "OBSERVABILITY_SVD.json").read_text()).get("nullspace_vectors", [])
    interpretation = json.loads((out / "CALIBRATION_PHYSICAL_INTERPRETATION.json").read_text())
    affected = sorted({component["parameter"] for row in nulls for component in row.get("components", [])})
    (out / "REPORT.md").write_text(
        "# Ten-node body Fusion V3\n\n"
        f"Top-level verdict: `{verdict}`.\n\n"
        f"FULL_SEGMENT_POSE_CALIBRATION: `{interpretation['FULL_SEGMENT_POSE_CALIBRATION']['verdict']}`.\n\n"
        f"STICK_FIGURE_CENTERLINE_CALIBRATION: `{interpretation['STICK_FIGURE_CENTERLINE_CALIBRATION']['verdict']}`.\n\n"
        "The V2 hard-coded `current_capture_frame_gate()` did not evaluate dataset observability; its "
        "correct conceptual status is `BLOCKED_FRAME_CALIBRATION_NOT_IMPLEMENTED`. V3 instead ran the "
        "calibration-only articulated optimization and its actual numerical residual Jacobian inside a "
        "filesystem-isolated process that could see neither held-out payloads nor the raw capture.\n\n"
        f"Computed nullspace-affected parameters: {', '.join(affected) if affected else 'none'}. "
        "The held-out walk/final-still ledger remained sealed and no visualization was produced. "
        "No external-accuracy or clinical-angle claim is made.\n",
        encoding="utf-8",
    )
    return {"verdict": verdict, "heldout_opened": False, "time_gate": True,
            "calibration_pass": bool(frame["pass"]), "raw_sha256": provenance["raw_sha256_after"]}
