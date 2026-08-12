#!/usr/bin/env python3
"""One-open interactive 18+4 pose capture for BSF31CC six-axis calibration."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from async_line_channel import ThreadedLineChannel
from fusion_session import parse_fields, parse_reply, resolve_fusion_port
from v47_c2cc_arbitrary_pose import (
    ACCEL_LSB_PER_G, GYRO_LSB_PER_DPS, PREREGISTERED, coverage_metrics,
    distinct_direction, fit_and_select, parse_imu_samples, stability_metrics,
    temperature_model,
)
from v47_c2cc_continuous_capture import LiveCatchupDetector, formal_start_disposition

ROOT = Path(__file__).resolve().parents[2]
NODE = "BSF31CC"
MASTER = "dk-fusion-imu-relay-v36"
MARKER = "b306-imu-relay-v47"
FWID = "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed"
IMAGE = "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98"
POLICY = ROOT / "B306_Part/tools/v47_c2cc_qualification_policy_v2.py"
MATH = ROOT / "B306_Part/tools/v47_c2cc_arbitrary_pose.py"
PRODUCER = ROOT / "B306_Part/firmware/src/imu.c"
SERIALIZER = ROOT / "B306_Part/host/fusion_master/src/main.c"

TRAINING = [
    "主元件面朝上，水平放稳并松手。",
    "主元件面朝下，水平放稳并松手。",
    "用一条长边竖直支撑，放稳并松手。",
    "翻转 180°，改用相对长边竖直支撑。",
    "用一条短边竖直支撑，放稳并松手。",
    "翻转 180°，改用相对短边竖直支撑。",
    "用一个板角和支架形成稳定斜姿态。",
    "换到明显不同的第二个板角斜姿态。",
    "整体翻面，再用一个板角形成斜姿态。",
    "换到翻面后的另一个板角斜姿态。",
    "选择尚未采过的中等倾角，稳定支撑。",
    "朝相反方向倾斜，形成新的稳定姿态。",
    "选择另一条边和支架形成新的斜姿态。",
    "翻转并改变倾角，避免接近此前姿态。",
    "选择一个未覆盖方向的稳定斜姿态。",
    "换另一侧或另一角，扩大三维覆盖。",
    "选择与此前明显不同的新稳定方向。",
    "最后选择一个差异最大的稳定斜姿态。",
]
VALIDATION = [
    "选择一个训练阶段没有用过的新稳定斜姿态。",
    "换到另一个训练阶段没有用过的新稳定姿态。",
    "翻转并采用新的倾角，保持支撑稳定。",
    "选择最后一个未见过的稳定方向。",
]
TRAINING_TOKENS = [f"POSE_{i:02d}_READY" for i in range(1, 19)]
VALIDATION_TOKENS = [f"VALIDATION_{i:02d}_READY" for i in range(1, 5)]


def wall() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic(path: Path, value) -> None:
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    os.replace(temporary, path)


class Inbox:
    def __init__(self):
        self.items = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self):
        for line in sys.stdin:
            self.items.put((line.strip(), time.monotonic(), wall()))


class Recorder:
    def __init__(self, root: Path):
        self.root = root
        self.raw_dir = root / "continuous_raw"
        self.raw_dir.mkdir()
        self.cdc = (self.raw_dir / "fusion_cdc.log").open("x", buffering=1)
        self.raw = (self.raw_dir / "fusion_host_raw.cobs.bin").open("xb", buffering=0)
        self.index = (self.raw_dir / "consumption_index.jsonl").open("x", buffering=1)
        self.channel = None
        self.phase = "COLLECTOR_OPEN"
        self.record_index = 0
        self.last_seq = self.last_n = self.last_base = None
        self.faults = deque()
        self.all_faults = []
        self.samples = []
        self.kind_counts = Counter()
        self.node_counts = Counter()
        self.aborted = False

    def open(self) -> str:
        self.open_monotonic = time.monotonic()
        self.open_wall = wall()
        port = resolve_fusion_port(None)
        self.channel = ThreadedLineChannel(
            port, self.cdc, "FUSION", decoded_queue_records=1048576,
            backlog_red_records=131072, raw_backlog_red_bytes=131072,
            stall_red_s=2, raw_file=self.raw,
        )
        self.channel.transport_mode = "binary"
        self.channel.text_pending.clear()
        return port

    def consume(self, deadline: float):
        line = self.channel.read(deadline)
        if not line:
            return None, []
        now = time.monotonic()
        self.record_index += 1
        kind = line.split(" ", 1)[0]
        self.kind_counts[kind] += 1
        fields = parse_fields(line)
        name = fields.get("name")
        if name and name != "-":
            self.node_counts[name] += 1
        health = self.channel.health_snapshot()
        self.index.write(json.dumps({
            "record_index": self.record_index, "consume_monotonic": now,
            "raw_bytes_submitted": health["raw_bytes_submitted"],
            "phase": self.phase, "line": line,
        }, separators=(",", ":")) + "\n")
        parsed = []
        if line.startswith("FUSION_IMU ") and name == NODE:
            try:
                if fields.get("proto") != "7":
                    raise ValueError(f"required proto=7, observed {fields.get('proto')}")
                parsed = parse_imu_samples(fields, now)
                seq, count, base = int(fields["seq"], 0), int(fields["n"], 0), int(fields["base_us"], 0)
                if self.last_seq is not None:
                    expected = (self.last_seq + self.last_n) & 0xFFFF
                    if seq != expected:
                        self._fault(now, "IMU_SEQUENCE", expected=expected, observed=seq)
                    if base <= self.last_base:
                        self._fault(now, "IMU_TIMESTAMP_REVERSAL", previous=self.last_base, observed=base)
                self.last_seq, self.last_n, self.last_base = seq, count, base
                for sample in parsed:
                    sample["phase"] = self.phase
                    sample["record_index"] = self.record_index
                self.samples.extend(parsed)
            except Exception as exc:
                self._fault(now, "IMU_PARSE", error=str(exc))
        while self.faults and self.faults[0]["monotonic"] < now - 120:
            self.faults.popleft()
        return line, parsed

    def _fault(self, monotonic: float, kind: str, **extra):
        row = {"monotonic": monotonic, "phase": self.phase, "kind": kind, **extra}
        self.faults.append(row)
        self.all_faults.append(row)

    def marker(self, name: str, extra=None) -> dict:
        health = self.channel.health_snapshot()
        return {"name": name, "wall": wall(), "monotonic": time.monotonic(),
                "raw_byte_offset": health["raw_bytes_submitted"],
                "decoded_record_index": health["decoded_records"],
                "consumed_record_index": self.record_index, **(extra or {})}

    def close(self):
        drain = self.channel.quiesce_reader_and_drain("bsf31cc_six_axis_clean_stop")
        self.channel.close()
        health = self.channel.health_snapshot()
        self.index.close(); self.raw.close(); self.cdc.close()
        return drain, health


class Protocol:
    def __init__(self, recorder: Recorder, inbox: Inbox, root: Path):
        self.recorder = recorder
        self.inbox = inbox
        self.instructions = []
        self.actions = []
        self.file = (root / "OPERATOR_ACTIONS.jsonl").open("x", buffering=1)

    def wait(self, instruction: str, token: str, step: str):
        row = {"type": "INSTRUCTION", "step": step, "instruction": instruction,
               "requested_token": token, "wall": wall(), "monotonic": time.monotonic()}
        self.instructions.append(row); self.file.write(json.dumps(row, separators=(",", ":")) + "\n")
        print(instruction, flush=True)
        print(f"请只回复：{token}（或 STOP）", flush=True)
        while not self.recorder.aborted:
            self.recorder.consume(time.monotonic() + .1)
            try:
                observed, monotonic, observed_wall = self.inbox.items.get_nowait()
            except queue.Empty:
                continue
            accepted = observed == token
            action = {"type": "TOKEN", "step": step, "token": observed,
                      "expected": token, "accepted": accepted, "wall": observed_wall,
                      "monotonic": monotonic}
            self.actions.append(action); self.file.write(json.dumps(action, separators=(",", ":")) + "\n")
            if observed == "STOP":
                self.recorder.aborted = True
                return False
            if accepted:
                return True
            print(f"Token 不匹配；当前只接受 {token}（或 STOP）", flush=True)
        return False

    def close(self):
        self.file.close()


def collect_stationary(recorder: Recorder, label: str, accepted_directions: list) -> tuple[dict, list, np.ndarray | None]:
    recorder.phase = label
    start = recorder.marker(label + "_TOKEN_ACCEPTED")
    recent = deque(); stable_since = None; decisions = []
    while not recorder.aborted:
        now = time.monotonic()
        _, new = recorder.consume(now + .1)
        recent.extend(new)
        while recent and recent[0]["host_monotonic"] < now - PREREGISTERED["window_s"]:
            recent.popleft()
        metrics = robust_stationarity_metrics(list(recent))
        accepted_fault = any(x["monotonic"] >= start["monotonic"] for x in recorder.faults)
        stable = bool(metrics.get("stable")) and not accepted_fault
        stable_since = (now - PREREGISTERED["window_s"] if stable_since is None else stable_since) if stable else None
        metrics.update(monotonic=now, phase=label,
                       continuous_stable_s=0.0 if stable_since is None else now - stable_since,
                       sequence_or_time_fault=accepted_fault)
        decisions.append(metrics)
        if stable_since is not None and now - stable_since >= PREREGISTERED["stable_target_s"]:
            segment = [x for x in recorder.samples if stable_since <= x["host_monotonic"] <= now]
            mean = np.mean([x["accel_g"] for x in segment], axis=0)
            direction = mean / np.linalg.norm(mean)
            distinct, angle = distinct_direction(direction, accepted_directions)
            row = {"accepted": bool(distinct),
                   "reason": "STABLE_AND_DISTINCT" if distinct else "DUPLICATE_OR_INSUFFICIENT_DIRECTION",
                   "nearest_angle_deg": angle, "start": start,
                   "end": recorder.marker(label + ("_ACCEPT" if distinct else "_REJECT")),
                   "duration_s": now - stable_since, "samples": len(segment),
                   "stability_final": metrics, "decision_count": len(decisions)}
            return row, segment, direction
    return {"accepted": False, "reason": "OPERATOR_STOP", "start": start,
            "end": recorder.marker(label + "_STOP")}, [], None


def robust_stationarity_metrics(samples: list[dict]) -> dict:
    """Detect physical stillness without letting isolated raw spikes reset dwell.

    Every raw sample remains in the accepted segment.  Only the decision's
    acceleration-spread statistic is replaced by its robust MAD equivalent;
    full-window gyro and gravity-direction tests remain active, and any
    consecutive anomaly makes the window non-stationary.  Runtime containment
    is evaluated later on the unmodified stream.
    """
    metrics = stability_metrics(samples)
    if not samples:
        return metrics
    accel = np.asarray([x["accel_g"] for x in samples], dtype=float)
    norm = np.linalg.norm(accel, axis=1)
    center = float(np.median(norm))
    candidate = np.abs(norm - center) > 0.060
    longest = current = 0
    for observed in candidate:
        current = current + 1 if observed else 0
        longest = max(longest, current)
    raw_std = metrics["accel_norm_std_g"]
    robust_sigma = float(1.4826 * np.median(np.abs(norm - center)))
    metrics.update(raw_accel_norm_std_g=raw_std, accel_norm_robust_sigma_g=robust_sigma,
                   accel_norm_std_g=robust_sigma,
                   raw_transient_candidate_count=int(np.sum(candidate)),
                   maximum_consecutive_raw_transient_samples=longest,
                   stationarity_accel_statistic="1.4826x_MAD_RETAIN_ALL_RAW_SAMPLES")
    thresholds = PREREGISTERED
    metrics["stable"] = bool(
        len(samples) >= thresholds["minimum_samples_per_window"]
        and robust_sigma <= thresholds["accel_norm_std_max_g"]
        and metrics["gyro_centered_rms_dps"] <= thresholds["gyro_centered_rms_max_dps"]
        and metrics["gyro_axis_std_max_dps"] <= thresholds["gyro_axis_std_max_dps"]
        and metrics["gravity_direction_p95_deg"] <= thresholds["gravity_direction_p95_max_deg"]
        and longest <= 1
    )
    return metrics


def observe_identity(recorder: Recorder, ledger: dict) -> None:
    guard = None
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and guard is None:
        line, _ = recorder.consume(deadline)
        if line and line.startswith("FUSION_IMU "):
            fields = parse_fields(line)
            if fields.get("name") == NODE and fields.get("proto") == "7":
                guard = line
    if not guard:
        raise RuntimeError("BLOCKED_REQUIRED_IMU_STREAM_UNAVAILABLE")
    ledger["decode_before_send_guard"] = {"passed": True, "known_record": guard[:300],
        "selected_stream": "FUSION_IMU proto=7", "port": ledger["port"],
        "baud": 115200, "dtr": False, "rts": False}
    observations = []
    for command in ("MASTER STATUS", "LIST", f"{NODE} PING", f"{NODE} BOOT CONFIRM STATUS"):
        recorder.channel.send(command)
        ledger["commands"].append({"command": command, "classification": "READ_ONLY_IDENTITY_OBSERVATION",
                                   "monotonic": time.monotonic()})
    end = time.monotonic() + 6
    while time.monotonic() < end:
        line, _ = recorder.consume(end)
        if line:
            observations.append(line)
    masters = [parse_fields(x) for x in observations if x.startswith("FUSION_MASTER_STATUS ")]
    listings = [parse_fields(x) for x in observations if x.startswith("FUSION_LIST ")]
    peers = [parse_fields(x) for x in observations if x.startswith("FUSION_PEER ")]
    pong = {}; confirm = {}
    for line in observations:
        reply = parse_reply(line)
        if reply:
            fields = parse_fields(reply.text)
            if reply.text.startswith("PONG "):
                pong = fields
            elif reply.text.startswith("BOOT CONFIRM STATUS "):
                confirm = fields
    target_peers = [x for x in peers if x.get("name") == NODE]
    checks = {
        "master_marker": bool(masters) and masters[-1].get("marker") == MASTER,
        "target_present_in_list": bool(listings) and len(target_peers) == 1,
        "target_connected_subscribed": len(target_peers) == 1 and target_peers[0].get("connected") == "1"
            and target_peers[0].get("subscribed") == "1",
        "exact_identity": all(pong.get(k) == v for k, v in
                              {"name": NODE, "fw": MARKER, "fwid": FWID, "image_sha": IMAGE}.items()),
        "confirmed": confirm.get("confirmed") == "1",
        "required_six_axis_proto7_stream": True,
    }
    ledger["identity_observation"] = {"checks": checks, "pass": all(checks.values()),
        "master": masters[-1] if masters else {}, "list": listings[-1] if listings else {},
        "target_peer": target_peers[0] if target_peers else {}, "pong": pong, "confirm": confirm}
    if not all(checks.values()):
        raise RuntimeError("BSF31CC_IDENTITY_OR_REQUIRED_STREAM_GATE_FAILED")


def warmup(recorder: Recorder, ledger: dict, root: Path) -> None:
    recorder.phase = "WARMUP_AND_CDC_CATCHUP"
    detector = LiveCatchupDetector(); next_second = math.floor(time.monotonic()) + 1
    bucket = []; rows = []
    while not recorder.aborted:
        line, new = recorder.consume(min(next_second, time.monotonic() + .1)); now = time.monotonic()
        if line:
            fields = parse_fields(line)
            if new:
                bucket.append(("imu", len(new), now * 1000 - int(fields["master_ms"], 0)))
            elif line.startswith("FUSION_UWB ") and fields.get("name") == NODE:
                bucket.append(("uwb", 1, now * 1000 - int(fields["master_ms"], 0)))
        if now >= next_second:
            offsets = [x[2] for x in bucket]
            health = recorder.channel.health_snapshot()
            row = {"start_monotonic": next_second - 1, "end_monotonic": next_second,
                "imu_hz": sum(x[1] for x in bucket if x[0] == "imu"),
                "uwb_hz": sum(x[1] for x in bucket if x[0] == "uwb"),
                "imu_gap_events": sum(x["monotonic"] >= next_second - 1 for x in recorder.faults),
                "uwb_gap_events": 0, "age_offset_median_ms": float(np.median(offsets)) if offsets else None,
                "decoded_queue_depth": health["decoded_queue_depth"], "raw_queue_depth": health["raw_queue_depth"],
                "serial_input_bytes": health["serial_input_bytes"], "timestamp_jump": False}
            _, detail = detector.update(row); row["live_evidence"] = detail; rows.append(row); bucket = []
            elapsed = next_second - recorder.open_monotonic
            disposition = formal_start_disposition(elapsed, detector.stable_seconds, 30, 180)
            if elapsed >= 60 and disposition:
                ledger["live_catchup"] = recorder.marker("LIVE_CATCHUP", {
                    "disposition": disposition, "stable_seconds": detector.stable_seconds})
                atomic(root / "WARMUP_SECONDLY_EVIDENCE.json", rows)
                print(f"{disposition} — BSF31CC six-axis stream ready", flush=True)
                return
            next_second += 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-root", type=Path, default=ROOT / "B306_Part/logs")
    parser.add_argument("--prior-attempt", type=Path)
    args = parser.parse_args()
    root = args.out_root / ("v47_bsf31cc_six_axis_calibration_" + datetime.now().strftime("%Y%m%d_%H%M%S"))
    root.mkdir(parents=True)
    git_status = subprocess.check_output(["git", "status", "--porcelain=v1", "-z"], cwd=ROOT)
    provenance = {"schema": "biospur-bsf31cc-six-axis-provenance-v1",
        "repository_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "worktree_status_sha256": hashlib.sha256(git_status).hexdigest(), "target_node": NODE,
        "master_marker": MASTER, "selected_stream": "FUSION_IMU proto=7",
        "units": {"accel": f"signed int16 / {ACCEL_LSB_PER_G} g", "gyro": f"signed int16 / {GYRO_LSB_PER_DPS} dps",
                  "node_time": "base_us + unsigned uint16 delta_us", "temperature": "temp_raw / 100 C"},
        "implementation": {str(path.relative_to(ROOT)): sha256(path) for path in (MATH, Path(__file__))},
        "qualification_policy": {"path": str(POLICY.relative_to(ROOT)), "sha256": sha256(POLICY)},
        "producer_and_serializer": {str(path.relative_to(ROOT)): sha256(path) for path in (PRODUCER, SERIALIZER)},
        "bmd101_scope": "EXCLUDED_RETAIN_RAW_DO_NOT_EVALUATE", "hardware_actions": []}
    atomic(root / "PROVENANCE.json", provenance)
    if args.prior_attempt:
        prior = args.prior_attempt.resolve()
        prior_manifest = json.loads((prior / "CAPTURE_MANIFEST.json").read_text())
        attempt = {"directory": str(prior.relative_to(ROOT)),
            "classification": "ZERO_POSE_TOOLING_ABORT",
            "reason": "NON_ROBUST_STATIONARITY_STD_RESET_BY_RETAINED_ISOLATED_ACCELEROMETER_SPIKES",
            "operator_stop_received": False,
            "accepted_training_poses": prior_manifest.get("training_pose_count"),
            "accepted_heldout_poses": prior_manifest.get("heldout_pose_count"),
            "raw_sha256": sha256(prior / "continuous_raw/fusion_host_raw.cobs.bin"),
            "raw_retained": True, "merged_into_formal_run": False}
        atomic(root / "PRECAPTURE_ATTEMPTS.json", {"attempts": [attempt],
            "formal_run_is_new_single_open_timeline": True})
    atomic(root / "PREREGISTERED_THRESHOLDS.json", PREREGISTERED)
    ledger = {"schema": "biospur-bsf31cc-six-axis-capture-v1", "node": NODE,
              "commands": [], "serial_open_count": 0, "phases": [], "start_wall": wall(),
              "hardware_mutations": [], "bmd101_scope": "EXCLUDED"}
    recorder = Recorder(root); protocol = Protocol(recorder, Inbox(), root)
    training_sets = []; validation_sets = []; accepted_directions = []; windows = []; frozen = None
    def stop(_signal, _frame): recorder.aborted = True
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)
    error = None
    try:
        ledger["port"] = recorder.open(); ledger["serial_open_count"] = 1
        ledger["collector_open"] = recorder.marker("COLLECTOR_OPEN")
        print(f"RUN_DIR={root}\nCOLLECTOR_OPEN — complete startup prefix is being retained", flush=True)
        observe_identity(recorder, ledger)
        atomic(root / "CAPTURE_MANIFEST.json", ledger)
        warmup(recorder, ledger, root)
        atomic(root / "CAPTURE_MANIFEST.json", ledger)
        for pose, (instruction, token) in enumerate(zip(TRAINING, TRAINING_TOKENS), 1):
            while not recorder.aborted:
                text = f"训练姿态 {pose}/18：现在可以移动 BSF31CC。{instruction} 完全松手后再确认。"
                if not protocol.wait(text, token, f"TRAINING_{pose:02d}"):
                    break
                row, segment, direction = collect_stationary(recorder, f"TRAINING_POSE_{pose:02d}", accepted_directions)
                row.update(set="TRAINING", pose=pose, requested_token=token)
                windows.append(row); ledger["phases"].append(row)
                if not row["accepted"]:
                    print(f"姿态 {pose} 未接受：{row['reason']}；最近方向夹角 {row.get('nearest_angle_deg')}°。请换一个稳定方向，仍回复同一个 token。", flush=True)
                    continue
                training_sets.append(segment); accepted_directions.append(direction.tolist())
                coverage = coverage_metrics(accepted_directions)
                print(f"POSE {pose:02d} ACCEPTED — {len(segment)} samples; nearest={row.get('nearest_angle_deg')}°; coverage_min_eig={coverage['direction_covariance_min_eigenvalue']:.6f}", flush=True)
                break
        if len(training_sets) == 18 and not recorder.aborted:
            coverage = coverage_metrics(accepted_directions)
            if coverage["direction_covariance_min_eigenvalue"] < PREREGISTERED["coverage_covariance_min_eigenvalue"] or coverage["design_condition"] > PREREGISTERED["coverage_design_condition_max"]:
                raise RuntimeError("TRAINING_COVERAGE_GATE_FAILED")
            recorder.phase = "FIT_AND_FREEZE_TRAINING_ONLY"
            selection = fit_and_select([np.asarray([x["accel_g"] for x in segment]) for segment in training_sets])
            gyro = np.asarray([x["gyro_dps"] for segment in training_sets for x in segment])
            temperatures = [x for segment in training_sets for x in segment]
            gyro_profile = {"bias_dps": np.mean(gyro, axis=0).tolist(), "std_dps": np.std(gyro, axis=0).tolist(),
                            "samples": len(gyro), "frozen_from_training_only": True}
            frozen = {"schema": "biospur-bsf31cc-device-calibration-candidate-v1", "node": NODE,
                "not_bsf_c2cc_profile": True, "frozen_before_heldout": True,
                "freeze_marker": recorder.marker("FREEZE_TRAINING_MODEL"), "parameter_changes_after_freeze": 0,
                "raw_axis_labels": ["a0", "a1", "a2", "g0", "g1", "g2"],
                "coverage": coverage, "model_selection": selection, "gyro_zero_rate": gyro_profile,
                "temperature_model": temperature_model(temperatures), "bmd101_scope": "EXCLUDED"}
            atomic(root / "FROZEN_TRAINING_MODEL.json", frozen)
            print(f"TRAINING MODEL FROZEN — selected {selection['selected_model']}; held-out data has not been observed", flush=True)
        for pose, (instruction, token) in enumerate(zip(VALIDATION, VALIDATION_TOKENS), 1):
            if recorder.aborted or frozen is None:
                break
            while not recorder.aborted:
                text = f"严格 held-out 姿态 {pose}/4：现在可以移动 BSF31CC。{instruction} 完全松手后再确认；该窗口绝不会用于重拟合。"
                if not protocol.wait(text, token, f"VALIDATION_{pose:02d}"):
                    break
                prior = accepted_directions + [np.mean([x["accel_g"] for x in segment], axis=0).tolist()
                                                for segment in validation_sets]
                row, segment, direction = collect_stationary(recorder, f"HELDOUT_POSE_{pose:02d}", prior)
                row.update(set="HELDOUT", pose=pose, requested_token=token)
                windows.append(row); ledger["phases"].append(row)
                if not row["accepted"]:
                    print(f"Held-out {pose} 未接受：{row['reason']}；请换新方向，仍回复同一个 token。", flush=True)
                    continue
                validation_sets.append(segment)
                print(f"VALIDATION {pose:02d} ACCEPTED — {len(segment)} samples; no refit performed", flush=True)
                break
        ledger["stop_reason"] = "PLANNED_18_PLUS_4_COMPLETE" if len(training_sets) == 18 and len(validation_sets) == 4 else "OPERATOR_STOP"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        ledger.update(stop_reason="FAIL_CLOSED", error=error)
        print(error, flush=True)
    finally:
        protocol.close()
        if recorder.channel:
            ledger["clean_stop"] = recorder.marker("CLEAN_STOP_BEGIN")
            drain, health = recorder.close()
        else:
            drain, health = {}, {}
        ledger.update(end_wall=wall(), close_drain=drain, health_final=health,
            training_pose_count=len(training_sets), heldout_pose_count=len(validation_sets),
            raw_sha256=sha256(root / "continuous_raw/fusion_host_raw.cobs.bin"),
            decoded_kind_counts=dict(sorted(recorder.kind_counts.items())),
            observed_node_record_counts=dict(sorted(recorder.node_counts.items())),
            six_axis_faults=recorder.all_faults, bmd101_evaluation="NOT_PERFORMED")
        atomic(root / "CAPTURE_MANIFEST.json", ledger)
        atomic(root / "OPERATOR_ACTIONS.json", {"instructions": protocol.instructions, "tokens": protocol.actions})
        fields = []
        for row in windows:
            for key in row:
                if key not in fields:
                    fields.append(key)
        with (root / "POSE_WINDOWS.csv").open("x", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
            writer.writeheader(); writer.writerows(windows)
        stream_checks = {"one_serial_open": ledger["serial_open_count"] == 1,
            "no_accepted_window_sequence_or_time_fault": not any(x["phase"].startswith(("TRAINING_POSE", "HELDOUT_POSE")) for x in recorder.all_faults),
            "no_queue_drops": all(health.get(key, 0) == 0 for key in ("raw_queue_drops", "decoded_queue_drops", "log_queue_drops")),
            "raw_byte_accounting_closed": health.get("raw_bytes_submitted") == health.get("raw_bytes_written"),
            "no_reader_or_payload_error": health.get("reader_exceptions", 0) == health.get("payload_decode_errors", 0) == 0}
        atomic(root / "STREAM_INTEGRITY.json", {"checks": stream_checks, "pass": all(stream_checks.values()),
            "faults": recorder.all_faults, "health": health, "bmd101_excluded": True})
        if frozen:
            atomic(root / "MODEL_SELECTION.json", frozen["model_selection"])
        print(f"CAPTURE_STOPPED reason={ledger['stop_reason']} training={len(training_sets)} heldout={len(validation_sets)} RUN_DIR={root}", flush=True)
    return 0 if not error and ledger["stop_reason"] == "PLANNED_18_PLUS_4_COMPLETE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
