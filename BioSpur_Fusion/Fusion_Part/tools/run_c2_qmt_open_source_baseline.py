#!/usr/bin/env python3
"""Thin, diagnostic-only QMT adapter for the sealed Capture-2 raw IMU stream.

This entry point deliberately does not import ``biospur_fusion.v0.c2_basis``.
It owns only lifecycle sealing, bounded COBS decoding through the stable V0
transport/time utilities, uniform-grid diagnostics, four QMT hinge edges, and
an explicitly non-scientific ten-segment surface-chord viewer.
"""
from __future__ import annotations

import argparse
import binascii
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import struct
import sys
from typing import Any

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import qmt
from scipy.spatial.transform import Rotation


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from biospur_fusion.time.common_clock import align_capture_bounded, models_as_json


RUN_DIR = ROOT / "logs/c2_qmt_open_source_baseline_20260829T073154Z"
C2_ID = "phase2_targeted_calibration_20260817t130918z_capture_2_with_joint_label_c8645eb2"
C2_ROOT = ROOT / "datasets/phase2_calibration" / C2_ID
IDENTITY = {
    "BSFEC35": "forearm_left", "BSFB165": "forearm_right",
    "BSFAA61": "upper_arm_left", "BSF1120": "upper_arm_right",
    "BSF31CC": "torso", "BSFC2CC": "pelvis",
    "BSF44AD": "thigh_left", "BSF3C79": "thigh_right",
    "BSF6C53": "shank_left", "BSF8BC4": "shank_right",
}
NODES = tuple(IDENTITY)
PARENTS = {
    "pelvis": None, "torso": "pelvis",
    "upper_arm_left": "torso", "forearm_left": "upper_arm_left",
    "upper_arm_right": "torso", "forearm_right": "upper_arm_right",
    "thigh_left": "pelvis", "shank_left": "thigh_left",
    "thigh_right": "pelvis", "shank_right": "thigh_right",
}
EDGES = tuple((parent, child) for child, parent in PARENTS.items() if parent)
EPISODES = (
    ("00_initial_still", 2), ("02_t_pose", 3),
    ("03_pelvis_hula_circle", 2), ("04_shoulder_left", 3),
    ("05_shoulder_right", 3), ("06_elbow_left", 2),
    ("07_elbow_right", 1), ("08_hip_left", 1),
    ("09_hip_right", 1), ("10_knee_left_seated", 1),
    ("11_knee_right_seated", 1), ("12_heel_raise_left", 1),
    ("13_heel_raise_right", 1), ("14_trunk_flex_extend", 1),
    ("15_trunk_axial_rotation", 1), ("16_squat", 1),
    ("17_final_still", 1), ("18_heel_to_butt_left", 1),
    ("19_heel_to_butt_right", 1),
)
AUTHORITIES = {
    "sealed_identity": C2_ROOT / "identity/SEALED_NODE_TO_BODY_GROUND_TRUTH.json",
    "wear_amendment": C2_ROOT / "identity/POST_SEAL_WEAR_DIRECTION_AMENDMENT_004.json",
    "frame_amendment": C2_ROOT / "identity/POST_SEAL_FRAME_SEMANTICS_AMENDMENT_005.json",
    "anthropometry": ROOT / "config/body_calibration_v4_1/v47_subject_surface_anthropometry_20260828.json",
}
CONTRACT_FILES = tuple(
    ROOT / "config/biospur_fusion_v0_c2_progressive_contract" / name
    for name in (
        "README.md", "ARCHITECTURE_CONTRACT.md", "COMPLIANCE_MATRIX.json",
        "RUN_START_CONTRACT.template.json",
    )
)
STABLE_SOURCES = (
    Path(__file__).resolve(),
    ROOT / "src/biospur_fusion/time/common_clock.py",
    ROOT / "src/biospur_fusion/imu/preintegration.py",
    ROOT / "src/biospur_fusion/imu/frontend.py",
)
UPSTREAM = RUN_DIR / "upstream/qmt-v0.2.4"
UPSTREAM_REVISION = "0fa8d32eb461e14d78e9ddbd569664ea59bcea19"
GRID_NS = 5_000_000
MAX_INTERPOLATION_GAP_NS = 12_500_000
MAX_RECOVERABLE_FACTOR_GAP_NS = 80_000_000
G = 9.80665
ENVELOPE_HEADER = struct.Struct("<HBBHHIQ")
IMU_SAMPLE = struct.Struct("<Hhhhhhh")
ALLOWED_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"),
    ("node_timer_us", "<u8"), ("global_time_ns", "<i8"),
    ("global_time_sigma_ns", "<u8"), ("delta_us", "<u2"),
    ("acc_raw", "<i2", (3,)), ("gyro_raw", "<i2", (3,)),
    ("raw_start_offset", "<u8"), ("raw_end_offset", "<u8"),
    ("raw_sample_index", "u1"), ("status", "u1"),
])


class PiecewiseClock:
    """One continuous TIMER2-to-shared-time map with explicit gap covariance."""

    LFRC_BOUND_PPM = 500.0

    def __init__(self, node: str, models: list[Any]):
        self.node = node
        self.models = sorted(models, key=lambda value: value.first_timer_us)
        if not self.models or any(value.node_id != node for value in self.models):
            raise ValueError(f"{node}: invalid piecewise model set")
        if any(value.boot_epoch != 0 for value in self.models):
            raise ValueError(f"{node}: boot epoch change")
        for left, right in zip(self.models, self.models[1:]):
            if left.last_timer_us >= right.first_timer_us:
                raise ValueError(f"{node}: overlapping or reversed model domains")
            if left.map_ns(left.last_timer_us) >= right.map_ns(right.first_timer_us):
                raise ValueError(f"{node}: non-monotone shared-time endpoints")
        self.sigma_ns = float(max(value.sigma_ns for value in self.models))

    def _position(self, timer_us: int) -> tuple[str, int]:
        timer = int(timer_us)
        for index, model in enumerate(self.models):
            if model.first_timer_us <= timer <= model.last_timer_us:
                return "anchor", index
            if index + 1 < len(self.models) and model.last_timer_us < timer < self.models[index + 1].first_timer_us:
                return "gap", index
        return ("extrapolate_before", 0) if timer < self.models[0].first_timer_us else ("extrapolate_after", len(self.models) - 1)

    def map_ns(self, timer_us: int) -> int:
        timer = int(timer_us); kind, index = self._position(timer)
        model = self.models[index]
        if kind != "gap":
            return model.map_ns(timer)
        right = self.models[index + 1]
        left_timer = model.last_timer_us; right_timer = right.first_timer_us
        left_ns = model.map_ns(left_timer); right_ns = right.map_ns(right_timer)
        alpha = (timer - left_timer) / (right_timer - left_timer)
        return int(round(left_ns + alpha * (right_ns - left_ns)))

    def sigma_at_ns(self, timer_us: int) -> float:
        timer = int(timer_us); kind, index = self._position(timer)
        model = self.models[index]
        if kind == "anchor":
            return float(model.sigma_ns)
        if kind == "gap":
            right = self.models[index + 1]
            nearest_us = min(timer - model.last_timer_us, right.first_timer_us - timer)
            endpoint_sigma = max(float(model.sigma_ns), float(right.sigma_ns))
        elif kind == "extrapolate_before":
            nearest_us = model.first_timer_us - timer; endpoint_sigma = float(model.sigma_ns)
        else:
            nearest_us = timer - model.last_timer_us; endpoint_sigma = float(model.sigma_ns)
        lfrc_ns = nearest_us * self.LFRC_BOUND_PPM * 1e-3
        return float(np.hypot(endpoint_sigma, lfrc_ns))

    def anchored_time_ranges(self) -> list[tuple[int, int]]:
        return [(value.map_ns(value.first_timer_us), value.map_ns(value.last_timer_us)) for value in self.models]

    def audit(self) -> dict[str, Any]:
        bridges = []
        for left, right in zip(self.models, self.models[1:]):
            left_ns = left.map_ns(left.last_timer_us); right_ns = right.map_ns(right.first_timer_us)
            gap_us = right.first_timer_us - left.last_timer_us
            bridges.append({
                "left_timer_us": left.last_timer_us, "right_timer_us": right.first_timer_us,
                "left_shared_ns": left_ns, "right_shared_ns": right_ns,
                "timer_gap_us": gap_us, "shared_gap_ns": right_ns - left_ns,
                "positive_bridge_slope": bool(right_ns > left_ns and gap_us > 0),
                "left_boundary_discontinuity_ns": self.map_ns(left.last_timer_us) - left_ns,
                "right_boundary_discontinuity_ns": self.map_ns(right.first_timer_us) - right_ns,
                "midpoint_covariance_sigma_ns": self.sigma_at_ns((left.last_timer_us + right.first_timer_us) // 2),
                "clean_anchor_gap_gate_relaxed": False,
            })
        probe = np.linspace(self.models[0].first_timer_us, self.models[-1].last_timer_us, 20001).round().astype(np.int64)
        mapped = np.array([self.map_ns(int(value)) for value in probe], dtype=np.int64)
        discontinuities = [abs(row[key]) for row in bridges for key in ("left_boundary_discontinuity_ns", "right_boundary_discontinuity_ns")]
        return {
            "node": self.node, "boot_epoch": 0, "local_model_count": len(self.models),
            "mapping": "qualified affine domains plus continuous linear endpoint bridges",
            "lfrc_covariance_bound_ppm": self.LFRC_BOUND_PPM,
            "strictly_monotone_20001_point_proof": bool(np.all(np.diff(mapped) > 0)),
            "maximum_boundary_discontinuity_ns": int(max([0] + discontinuities)),
            "qualified_anchor_ranges_shared_ns": self.anchored_time_ranges(),
            "unanchored_bridges": bridges,
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def array_hash(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def write_json(path: Path, value: Any, *, immutable: bool = False) -> None:
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    path.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n")
    if immutable:
        path.chmod(0o444)


def json_file(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_layout() -> None:
    if ROOT != Path("/mnt/nrf_ssd/nRF_dev/BioSpur_Fusion/Fusion_Part"):
        raise RuntimeError("canonical root changed")
    if RUN_DIR.parent != ROOT / "logs" or not RUN_DIR.name.startswith(
        "c2_qmt_open_source_baseline_"
    ):
        raise RuntimeError("run directory violates path binding")
    if not RUN_DIR.is_dir():
        raise RuntimeError("authorized run directory is absent")
    if shutil.disk_usage("/mnt/nrf_ssd").free < 100 * 1024**3:
        raise RuntimeError("nrf_ssd disk gate failed")
    if shutil.disk_usage("/").free < 40 * 1024**3:
        raise RuntimeError("root disk gate failed")


def source_hashes() -> dict[str, str]:
    paths = (*CONTRACT_FILES, *STABLE_SOURCES,
             UPSTREAM / "examples/full_body_tracking_advanced_example.py",
             UPSTREAM / "LICENSES/MIT.txt")
    return {str(path.relative_to(ROOT)): sha256_file(path) for path in paths}


def authority_hashes() -> dict[str, dict[str, Any]]:
    return {
        name: {"path": str(path.relative_to(ROOT)), "sha256": sha256_file(path)}
        for name, path in AUTHORITIES.items()
    }


def validate_authorities() -> None:
    identity = json_file(AUTHORITIES["sealed_identity"])
    observed = {row["hardware_id"]: row["body_segment"] for row in identity["rows"]}
    if observed != IDENTITY:
        raise RuntimeError("sealed identity differs from diagnostic identity")
    wear = json_file(AUTHORITIES["wear_amendment"])
    frame = json_file(AUTHORITIES["frame_amendment"])
    if wear.get("append_only") is not True or frame.get("append_only") is not True:
        raise RuntimeError("authority amendment is not append-only")
    if wear.get("capture_session_id") != C2_ID or frame.get("capture_session_id") != C2_ID:
        raise RuntimeError("authority capture mismatch")
    if wear["uncertainty_contract"].get("not_exact_vectors") is not True:
        raise RuntimeError("wear regions were exactized")


def seal() -> int:
    assert_layout(); validate_authorities()
    for name in (
        "RUN_START_CONTRACT.json", "CONTRACT_COMPLIANCE.json",
        "METADATA_PRESELECTION.json", "RAW_ACCESS_AUDIT_PRE.json",
        "PATH_CORRECTION_AUDIT.json", "UPSTREAM_RUNS.json",
    ):
        if (RUN_DIR / name).exists():
            raise FileExistsError(f"seal artifact already exists: {name}")
    sources = source_hashes(); authorities = authority_hashes()
    common = {
        "task_class": "DIAGNOSTIC_OPEN_SOURCE_COMPATIBILITY_ONLY",
        "capture_id": C2_ID, "identity": IDENTITY,
        "parents": PARENTS, "edges": [list(edge) for edge in EDGES],
        "episode_selection": [
            {"action": action, "attempt": attempt} for action, attempt in EPISODES
        ],
        "scientific_qualification": False,
        "synthetic_qualification_status": "NOT_RUN_NOT_AUTHORIZED_FOR_THIS_BASELINE",
        "allowed_verdicts": [
            "RUNNABLE_BASELINE", "DIAGNOSTIC_BASELINE", "BLOCKED_AT_BOUNDARY",
        ],
        "scientific_PASS_forbidden": True, "progressive_PASS_forbidden": True,
        "source_config_hashes": sources, "metadata_authorities": authorities,
    }
    correction = {
        "schema": "biospur-c2-qmt-path-correction-audit-v1",
        "original_path": "logs/qmt_c2_bootstrap_20260829_093154",
        "original_creation_time": "2026-08-29T09:32:00+02:00_APPROXIMATE_NOT_CAPTURED_BEFORE_RENAME",
        "rename_command": (
            "mv logs/qmt_c2_bootstrap_20260829_093154 "
            "logs/c2_qmt_open_source_baseline_20260829T073154Z"
        ),
        "rename_time": "2026-08-29T09:35:37+02:00_APPROXIMATE_COMMAND_OBSERVATION",
        "destination": str(RUN_DIR.relative_to(ROOT)),
        "payload_access_before_correction": False,
        "historical_overwrite": False,
        "provenance_contamination": False,
        "preserved_not_erased": True,
        "upstream_evidence_hashes": {
            str(path.relative_to(RUN_DIR)): sha256_file(path) for path in (
                UPSTREAM / "examples/full_body_tracking_advanced_example.py",
                UPSTREAM / "examples/full_body_example_data.mat",
                UPSTREAM / "examples/example_output/full_body_results_advanced.mat",
                RUN_DIR / "upstream_heading_correction_summary.json",
                RUN_DIR / "upstream_heading_correction_trajectories.npz",
            )
        },
    }
    write_json(RUN_DIR / "PATH_CORRECTION_AUDIT.json", correction, immutable=True)
    upstream_runs = {
        "schema": "biospur-qmt-upstream-two-run-evidence-v1",
        "revision": UPSTREAM_REVISION, "version": "0.2.4", "license": "MIT",
        "upstream_source_immutable": True,
        "run_a": {
            "classification": "LITERAL_UPSTREAM_DEFAULT_REPRODUCTION",
            "useHeadingCorrection": False,
            "must_not_be_described_as_heading_corrected": True,
            "result_path": "upstream/qmt-v0.2.4/examples/example_output/full_body_results_advanced.mat",
            "result_sha256": sha256_file(UPSTREAM / "examples/example_output/full_body_results_advanced.mat"),
        },
        "run_b": {
            "classification": "IMMUTABLE_SOURCE_EXTERNAL_CONFIG_WRAPPER_MECHANISM_EXERCISE",
            "config_diff": {"useHeadingCorrection": {"upstream_default": False, "wrapper_exercise": True}},
            "wrapper_path": "upstream_heading_correction_harness.py",
            "wrapper_sha256": sha256_file(RUN_DIR / "upstream_heading_correction_harness.py"),
            "summary_path": "upstream_heading_correction_summary.json",
            "summary_sha256": sha256_file(RUN_DIR / "upstream_heading_correction_summary.json"),
            "trajectories_path": "upstream_heading_correction_trajectories.npz",
            "trajectories_sha256": sha256_file(RUN_DIR / "upstream_heading_correction_trajectories.npz"),
            "time_varying_delta_filt_executed": True,
            "corrected_trajectory_consumer": "upstream_quat_seg_deltaCorr_saved_in_trajectory_archive",
        },
    }
    write_json(RUN_DIR / "UPSTREAM_RUNS.json", upstream_runs, immutable=True)
    contract = {
        "schema": "biospur-c2-qmt-diagnostic-run-start-contract-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "immutable_before_c2_payload": True,
        "original_wall_start": "2026-08-29T09:31:54+02:00",
        "hard_stop": "2026-08-29T10:31:54+02:00",
        **common,
        "input_firewall": {
            "allowed_root": str(C2_ROOT), "other_dataset_roots": "FORBIDDEN",
            "allowed_fields": ["acc_raw", "gyro_raw", "timestamp", "status", "boot_epoch"],
            "magnetometer": False, "uwb_spatial": False, "vendor_quaternion": False,
            "action_label_pose_truth": False, "cross_capture": False,
        },
        "timebase": {
            "one_capture_wide_state": True, "episode_filter_reset": False,
            "uniform_grid_period_ns": GRID_NS,
            "maximum_interpolation_gap_ns": MAX_INTERPOLATION_GAP_NS,
            "arbitrary_gap_interpolation": False,
            "large_gap_result": "BLOCKED_AT_TIMEBASE_ADAPTER",
        },
        "qmt": {
            "version": importlib.metadata.version("qmt"),
            "revision": UPSTREAM_REVISION, "license": "MIT",
            "run_a_default_useHeadingCorrection": False,
            "run_b_wrapper_useHeadingCorrection": True,
            "upstream_source_immutable": True,
        },
        "joint_policy": {
            "hinges": ["elbow_left", "elbow_right", "knee_left", "knee_right"],
            "non_hinges": ["pelvis_torso", "shoulder_left", "shoulder_right", "hip_left", "hip_right"],
            "fabricated_rom_or_manual_sign": False,
        },
    }
    write_json(RUN_DIR / "RUN_START_CONTRACT.json", contract, immutable=True)
    compliance = {
        "schema": "biospur-c2-qmt-diagnostic-contract-compliance-v1",
        **common,
        "predicates": [
            {"id": "D-ACCESS", "class": "A", "state": "PASS_PREACCESS", "rule": "seal before C2 payload"},
            {"id": "D-FIREWALL", "class": "A", "state": "PASS_PREACCESS", "rule": "C2 raw IMU only"},
            {"id": "D-TIMEBASE", "class": "A", "state": "PENDING_RUNTIME", "rule": "bounded common grid or block"},
            {"id": "D-HINGE", "class": "C", "state": "PENDING_RUNTIME", "rule": "noise-aware QMT ratings"},
            {"id": "D-NONHINGE", "class": "D", "state": "KNOWN_INCOMPATIBILITY", "rule": "no fabricated ROM/sign"},
            {"id": "D-VISUAL", "class": "B", "state": "PENDING_RUNTIME", "rule": "gross defects reject candidate only"},
        ],
    }
    write_json(RUN_DIR / "CONTRACT_COMPLIANCE.json", compliance, immutable=True)
    preselection = {
        "schema": "biospur-c2-qmt-diagnostic-metadata-preselection-v1",
        "record_status": "SEALED_BEFORE_ANY_C2_PAYLOAD_OPEN_OR_HASH",
        "payload_opened_before_seal": False, "payload_hashed_before_seal": False,
        **common,
        "raw_selection_rule": "continuous byte span from first selected repetition boundary through last selected repetition boundary; Hxx overlap forbidden",
        "qmt_row_selection": {
            "result_independent": True, "uniform_time_decimation": True,
            "maximum_rows_per_hinge": 7500,
            "knee_left": ["10_knee_left_seated", "16_squat", "18_heel_to_butt_left"],
            "knee_right": ["11_knee_right_seated", "16_squat", "19_heel_to_butt_right"],
            "elbow_left": ["06_elbow_left"], "elbow_right": ["07_elbow_right"],
        },
    }
    write_json(RUN_DIR / "METADATA_PRESELECTION.json", preselection, immutable=True)
    preaccess = {
        "schema": "biospur-c2-qmt-pre-access-firewall-v1",
        "seal_completed": True, "c2_payload_opened": False,
        "c2_payload_hashed": False, "non_c2_dataset_accessed": False,
        "allowed_c2_root": str(C2_ROOT), "source_hashes": sources,
    }
    write_json(RUN_DIR / "RAW_ACCESS_AUDIT_PRE.json", preaccess, immutable=True)
    print(json.dumps({"sealed": True, "run_dir": str(RUN_DIR)}, indent=2))
    return 0


def verify_seal() -> dict[str, Any]:
    required = (
        "RUN_START_CONTRACT.json", "CONTRACT_COMPLIANCE.json",
        "METADATA_PRESELECTION.json", "RAW_ACCESS_AUDIT_PRE.json",
        "PATH_CORRECTION_AUDIT.json",
        "UPSTREAM_RUNS.json",
    )
    for name in required:
        path = RUN_DIR / name
        if not path.is_file() or path.stat().st_mode & 0o222:
            raise RuntimeError(f"missing or writable seal artifact: {name}")
    seal_record = json_file(RUN_DIR / "METADATA_PRESELECTION.json")
    if seal_record["payload_opened_before_seal"] or seal_record["payload_hashed_before_seal"]:
        raise RuntimeError("preselection admits pre-seal payload access")
    amendments = []
    for name in (
        "METADATA_PRESELECTION_AMENDMENT_001.json",
        "METADATA_PRESELECTION_AMENDMENT_002.json",
        "METADATA_PRESELECTION_AMENDMENT_003.json",
        "METADATA_PRESELECTION_AMENDMENT_004.json",
        "METADATA_PRESELECTION_AMENDMENT_005.json",
        "METADATA_PRESELECTION_AMENDMENT_006.json",
        "METADATA_PRESELECTION_AMENDMENT_007.json",
        "METADATA_PRESELECTION_AMENDMENT_008.json",
        "METADATA_PRESELECTION_AMENDMENT_009.json",
        "METADATA_PRESELECTION_AMENDMENT_010.json",
    ):
        amendment_path = RUN_DIR / name
        if amendment_path.exists() and amendment_path.stat().st_mode & 0o222:
            raise RuntimeError(f"preselection amendment is writable: {name}")
        if amendment_path.is_file():
            amendments.append(json_file(amendment_path))
    for relative, expected in seal_record["source_config_hashes"].items():
        observed = sha256_file(ROOT / relative)
        chained = expected
        if relative == str(Path(__file__).resolve().relative_to(ROOT)):
            for amendment in amendments:
                if amendment["original_source_sha256"] != chained:
                    raise RuntimeError("preselection amendment source chain is broken")
                if "raw_cobs_payload_opened_before_amendment" in amendment:
                    if amendment["raw_cobs_payload_opened_before_amendment"] is not False:
                        raise RuntimeError("preselection amendment followed raw payload access")
                elif not str(amendment.get("schema", "")).startswith(
                    "biospur-c2-qmt-diagnostic-post-access-causal-amendment"
                ):
                    raise RuntimeError("unclassified post-access amendment")
                chained = amendment["amended_source_sha256"]
        if observed != chained:
            raise RuntimeError(f"sealed source changed without append-only amendment: {relative}")
    return seal_record


def amend() -> int:
    """Bind the bounded timing-ceiling repair without altering the first seal."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_001.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    original = json_file(RUN_DIR / "METADATA_PRESELECTION.json")
    relative = str(Path(__file__).resolve().relative_to(ROOT))
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-preselection-amendment-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only": True,
        "original_preselection_path": "METADATA_PRESELECTION.json",
        "original_preselection_sha256": sha256_file(RUN_DIR / "METADATA_PRESELECTION.json"),
        "original_source_sha256": original["source_config_hashes"][relative],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "failure_evidence": {
            "stdout_path": "c2_run_stdout.log",
            "stdout_sha256": sha256_file(RUN_DIR / "c2_run_stdout.log"),
            "exit_code_path": "c2_run_exit_code.txt",
            "exit_code_sha256": sha256_file(RUN_DIR / "c2_run_exit_code.txt"),
            "boundary": "COMMON_CLOCK_FRACTIONAL_SEARCH_CEILING_BEFORE_RAW_COBS_OPEN",
        },
        "causal_correction": (
            "replace cross-file raw-size fraction with per-timing-file binary-searched "
            "pre-Hxx byte seeds; retain common_clock audited bounded reads"
        ),
        "raw_cobs_payload_opened_before_amendment": False,
        "raw_cobs_payload_hashed_before_amendment": False,
        "c2_timing_metadata_accessed_before_amendment": True,
        "threshold_relaxed": False,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend2() -> int:
    """Bind the final local field-firewalled decoder before raw payload access."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_002.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous = json_file(RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_001.json")
    provenance_paths = (
        ROOT / "src/biospur_fusion/time/common_clock.py",
        ROOT / "src/biospur_fusion/imu/preintegration.py",
        ROOT / "src/biospur_fusion/imu/frontend.py",
        AUTHORITIES["frame_amendment"],
    )
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-preselection-amendment-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "append_only": True,
        "previous_amendment_path": "METADATA_PRESELECTION_AMENDMENT_001.json",
        "previous_amendment_sha256": sha256_file(RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_001.json"),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "causal_correction": (
            "remove private raw_validation decoder import; locally decode only accel/gyro "
            "plus timing/status and structurally skip two temperature header bytes"
        ),
        "decoded_field_manifest_frozen_in_source": list(ALLOWED_DTYPE.names),
        "temperature_exposed_or_consumed": False,
        "transitive_local_source_and_scale_provenance_hashes": {
            str(source.relative_to(ROOT)): sha256_file(source) for source in provenance_paths
        },
        "common_clock_transitive_local_imports": [],
        "raw_cobs_payload_opened_before_amendment": False,
        "raw_cobs_payload_hashed_before_amendment": False,
        "c2_timing_metadata_accessed_before_amendment": True,
        "future_adapter_edits_fail_closed": True,
        "threshold_relaxed": False,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend3() -> int:
    """Bind the continuous piecewise common-clock recovery before raw access."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_003.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_002.json"
    previous = json_file(previous_path)
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-preselection-amendment-v3",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "failure_evidence": {
            "stdout_path": "c2_run_attempt2_stdout.log",
            "stdout_sha256": sha256_file(RUN_DIR / "c2_run_attempt2_stdout.log"),
            "boundary": "WHOLE_ADMINISTRATIVE_SPAN_MAXIMUM_CLEAN_ANCHOR_GAP_BEFORE_RAW_COBS_OPEN",
        },
        "causal_correction": (
            "retain the unchanged 60 s gate for each independently evidenced local clock domain; "
            "construct one capture-wide monotone piecewise TIMER2 mapping by continuous endpoint "
            "bridges with explicit 500 ppm LFRC covariance inflation and unanchored gap masks"
        ),
        "per_episode_clock_use": "CLOCK_EVIDENCE_DOMAINS_ONLY_NOT_ORIENTATION_RESETS_OR_YAW_GAUGES",
        "orientation_and_heading_state_reset_at_episode_boundaries": False,
        "clean_anchor_gap_gate_relaxed": False,
        "raw_cobs_payload_opened_before_amendment": False,
        "raw_cobs_payload_hashed_before_amendment": False,
        "c2_timing_metadata_accessed_before_amendment": True,
        "future_adapter_edits_fail_closed": True,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend4() -> int:
    """Bind the gate-audit key correction after attempt 3, still pre-raw."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_004.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_003.json"
    previous = json_file(previous_path)
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-preselection-amendment-v4",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "failure_evidence": {
            "stdout_path": "c2_run_attempt3_stdout.log",
            "stdout_sha256": sha256_file(RUN_DIR / "c2_run_attempt3_stdout.log"),
            "boundary": "LOCAL_GATE_AUDIT_KEY_ERROR_BEFORE_RAW_COBS_OPEN",
        },
        "causal_correction": "record the emitted per_node gate field instead of nonexistent model_quality",
        "raw_cobs_payload_opened_before_amendment": False,
        "raw_cobs_payload_hashed_before_amendment": False,
        "c2_timing_metadata_accessed_before_amendment": True,
        "future_adapter_edits_fail_closed": True,
        "threshold_relaxed": False,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend5() -> int:
    """Bind exact common-clock gate serialization after attempt 4, pre-raw."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_005.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_004.json"
    previous = json_file(previous_path)
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-preselection-amendment-v5",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "failure_evidence": {
            "stdout_path": "c2_run_attempt4_stdout.log",
            "stdout_sha256": sha256_file(RUN_DIR / "c2_run_attempt4_stdout.log"),
            "boundary": "SECOND_LOCAL_GATE_AUDIT_KEY_ERROR_BEFORE_RAW_COBS_OPEN",
        },
        "causal_correction": "serialize the complete emitted gate object without guessed diagnostic keys",
        "raw_cobs_payload_opened_before_amendment": False,
        "raw_cobs_payload_hashed_before_amendment": False,
        "c2_timing_metadata_accessed_before_amendment": True,
        "future_adapter_edits_fail_closed": True, "threshold_relaxed": False,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend6() -> int:
    """Bind post-access causal diagnostics; do not relax the timebase."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_006.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_005.json"
    previous = json_file(previous_path)
    observed_log = RUN_DIR / "timebase_gap_diagnostic_stdout.log"
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-post-access-causal-amendment-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "payload_access_state": "BOUNDED_ACCEL_GYRO_PAYLOAD_ACCESSED_IN_ATTEMPT_5",
        "observed_payload_facts": {
            "attempt5_invalid_common_grid_rows": 765,
            "original_bound_ns": MAX_INTERPOLATION_GAP_NS,
            "largest_gap_node": "BSFC2CC",
            "largest_gap_ns": 1454982437,
            "diagnostic_log_path": observed_log.name,
            "diagnostic_log_sha256": sha256_file(observed_log),
        },
        "causal_revision": (
            "emit exact per-node gap intervals, masks, sequence/status context, bounded raw-access "
            "audit, and covariance consequence before stopping at the timebase boundary"
        ),
        "independent_recovery_class": {
            "source": "version-7 wire envelope sample_count range 1..16 and nominal 5 ms cadence",
            "maximum_single_missing_envelope_duration_ns": 80000000,
            "applies_identically_to_all_ten_nodes": True,
            "observed_pelvis_gap_exceeds_class": True,
        },
        "resampling_threshold_changed": False,
        "qmt_selection_sign_rom_mount_or_image_choice_changed": False,
        "visual_or_outcome_tuning": False,
        "future_adapter_edits_fail_closed": True,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend7() -> int:
    """Bind verifier support for the explicit post-access amendment schema."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_007.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_006.json"
    previous = json_file(previous_path)
    attempt_path = RUN_DIR / "c2_run_attempt6_stdout.log"
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-post-access-causal-amendment-v2",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "payload_access_state": "BOUNDED_ACCEL_GYRO_PAYLOAD_PREVIOUSLY_ACCESSED",
        "failure_evidence": {"path": attempt_path.name, "sha256": sha256_file(attempt_path)},
        "causal_revision": "verify an explicitly classified post-access amendment without applying the pre-access-only boolean assertion",
        "resampling_threshold_changed": False,
        "qmt_selection_sign_rom_mount_or_image_choice_changed": False,
        "visual_or_outcome_tuning": False,
        "future_adapter_edits_fail_closed": True,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend8() -> int:
    """Bind the user-authorized post-deadline gap-consequence correction."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_008.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_007.json"
    previous = json_file(previous_path)
    authority_path = RUN_DIR / "TIMEBASE_CAUSAL_CORRECTION_AMENDMENT_001.json"
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-post-access-causal-amendment-v3",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "corrective_authority_path": authority_path.name,
        "corrective_authority_sha256": sha256_file(authority_path),
        "original_deadline_local": "2026-08-29T10:31:54+02:00",
        "explicit_corrective_authorization_received_after_deadline": True,
        "scope": "EPISODE_OVERLAP_CLASSIFICATION_QMT_C2_ATTEMPT_PNG_INSPECTION_APPEND_ONLY_HANDOFF",
        "payload_access_state": "BOUNDED_ACCEL_GYRO_PAYLOAD_PREVIOUSLY_ACCESSED",
        "causal_revision": (
            "classify common-grid gaps against preregistered episode masks before consequence; "
            "hold one QMT VQF state without fabricated measurements across long non-factor gaps; "
            "use the fixed 80 ms recovery class only for necessary selected-window frontend rows"
        ),
        "fixed_recovery_class_ns": MAX_RECOVERABLE_FACTOR_GAP_NS,
        "applies_identically_to_all_ten_nodes": True,
        "orientation_or_heading_reset_at_episode_boundaries": False,
        "qmt_selection_sign_rom_mount_or_image_choice_tuned": False,
        "scientific_PASS_forbidden": True, "progressive_PASS_forbidden": True,
        "future_adapter_edits_fail_closed": True,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend9() -> int:
    """Exclude recovered factor rows from heading factors before corrected access."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_009.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_008.json"
    previous = json_file(previous_path)
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-post-access-causal-amendment-v4",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "payload_access_state": "NO_CORRECTED_RUN_PAYLOAD_ACCESS_AFTER_AMENDMENT_008",
        "causal_revision": (
            "exclude every recovered selected-factor row from both hinge-axis selection and "
            "headingCorrection; keep bounded reconstruction only in the single continuous "
            "orientation frontend; long non-factor rows remain no-update everywhere"
        ),
        "fixed_recovery_class_ns": MAX_RECOVERABLE_FACTOR_GAP_NS,
        "threshold_changed": False, "row_selection_result_tuned": False,
        "scientific_PASS_forbidden": True, "progressive_PASS_forbidden": True,
        "future_adapter_edits_fail_closed": True,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def amend10() -> int:
    """Bind final input and upstream gap-mask consequences before QMT use."""
    assert_layout()
    path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_010.json"
    if path.exists():
        raise FileExistsError(f"refusing to overwrite {path}")
    previous_path = RUN_DIR / "METADATA_PRESELECTION_AMENDMENT_009.json"
    previous = json_file(previous_path)
    amendment = {
        "schema": "biospur-c2-qmt-diagnostic-post-access-causal-amendment-v5",
        "created_utc": datetime.now(timezone.utc).isoformat(), "append_only": True,
        "previous_amendment_path": previous_path.name,
        "previous_amendment_sha256": sha256_file(previous_path),
        "original_source_sha256": previous["amended_source_sha256"],
        "amended_source_sha256": sha256_file(Path(__file__).resolve()),
        "payload_access_state": "CORRECTED_ATTEMPT_1_INTERRUPTED_BEFORE_INPUT_CONTRACT_OR_QMT",
        "authority": "DIRECT_USER_VERIFICATION_OF_B306_V47_AND_OFFICIAL_WIT_PROTOCOL",
        "capture_firmware": {
            "fw": "b306-imu-relay-v47",
            "fwid": "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed",
            "image_sha": "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98",
            "capture_readiness_sha256": "77053bce68e255357ceeccdcced3977bce73e62d23052797e67abc14fd856d3e",
        },
        "verified_scale": {
            "acceleration_lsb_per_g": 2048.0,
            "gyroscope_lsb_per_deg_s": 16.384,
            "range_register_write_required": False,
        },
        "causal_revision": (
            "replace obsolete scale-unknown limitation with verified fixed protocol output scaling; "
            "bind raw JY61P register axes while retaining soft sensor-to-anatomical extrinsics; "
            "do not call upstream headingCorrection because its equidistant-time API has no "
            "missing-measurement mask and compressing excluded rows would concatenate gaps"
        ),
        "raw_register_axis_order_and_sign_bound": True,
        "sensor_to_anatomical_extrinsics": "FUNCTIONAL_ESTIMATE_WITHIN_QUALITATIVE_WEAR_CONES",
        "new_hardware_axis_experiment_required": False,
        "heading_correction": {
            "upstream_source_sha256": "2bf43206a0ae04efda88183b3c96325f915283ed80506384e6fdbc48c3720cc2",
            "constant_time_assertion_lines": [146, 148],
            "missing_measurement_mask_or_no_update_api": False,
            "c2_call_status": "NOT_RUN_UPSTREAM_GAP_MASK_INCOMPATIBILITY",
            "compressed_time_substitution_forbidden": True,
            "viewer_label": "UNHEADING_CORRECTED_DIAGNOSTIC",
        },
        "scientific_PASS_forbidden": True, "progressive_PASS_forbidden": True,
        "future_adapter_edits_fail_closed": True,
    }
    write_json(path, amendment, immutable=True)
    print(json.dumps(amendment, indent=2))
    return 0


def timing_seed(path: Path, target_ns: int, *, jsonl: bool) -> int:
    """Find a byte seed before target without traversing a forbidden suffix."""
    size = path.stat().st_size
    low = 0; high = size
    with path.open("rb") as stream:
        for _ in range(48):
            if high - low < 4096:
                break
            middle = (low + high) // 2
            stream.seek(middle)
            if middle:
                stream.readline()
            row_start = stream.tell(); line = stream.readline()
            if not line:
                high = middle; continue
            try:
                if jsonl:
                    timestamp = int(json.loads(line)["arrival_monotonic_ns"])
                else:
                    timestamp = int(round(float(line.split(maxsplit=2)[1]) * 1e9))
            except (ValueError, KeyError, IndexError, json.JSONDecodeError):
                low = stream.tell(); continue
            if timestamp < target_ns:
                low = row_start
            else:
                high = row_start
    return max(1, low)


def load_plan() -> tuple[list[dict[str, Any]], list[tuple[int, int]], list[tuple[int, int]]]:
    plan_path = C2_ROOT / "CAPTURE_PLAN_FINAL.json"
    plan = json_file(plan_path)
    selected_attempts = dict(EPISODES)
    selected = []
    excluded_intervals = []; excluded_bytes = []
    for row in plan["actions"]:
        action = str(row["action_id"])
        action_root = C2_ROOT / row["relative_dir"] / "rep_01"
        event_path = action_root / "events/ACTION_EVENTS.jsonl"
        manifest_path = action_root / "manifest/CAPTURE_MANIFEST.json"
        if not event_path.is_file() or not manifest_path.is_file():
            continue
        events = [json.loads(line) for line in event_path.read_text().splitlines()]
        by_event = {event["event"]: event for event in events}
        manifest = json_file(manifest_path)
        start = by_event["REPETITION_START_BOUNDARY"]
        stop = by_event["REPETITION_END_BOUNDARY"]
        record = {
            "action": action, "attempt": int(by_event["ACTION_START"]["attempt_id"]),
            "episode_start_host_ns": int(start["host_monotonic_ns"]),
            "episode_stop_host_ns": int(stop["host_monotonic_ns"]),
            "formal_start_host_ns": int(by_event["ACTION_START"]["host_monotonic_ns"]),
            "formal_stop_host_ns": int(by_event["ACTION_STOP"]["host_monotonic_ns"]),
            "start_byte": int(start["continuous_raw_complete_frame_bytes"]),
            "stop_byte": int(stop["continuous_raw_complete_frame_bytes"]),
            "slice_sha256": manifest["continuous_range"]["slice_sha256"],
            "event_path": str(event_path), "event_sha256": sha256_file(event_path),
            "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
        }
        if action in selected_attempts:
            if record["attempt"] != selected_attempts[action] or manifest.get("status") != "ACCEPTED":
                raise RuntimeError(f"{action}: selected attempt/status mismatch")
            selected.append(record)
        else:
            excluded_intervals.append((record["episode_start_host_ns"], record["episode_stop_host_ns"]))
            excluded_bytes.append((record["start_byte"], record["stop_byte"]))
    selected.sort(key=lambda row: row["episode_start_host_ns"])
    if [row["action"] for row in selected] != [row[0] for row in EPISODES]:
        raise RuntimeError("selected episode chronology differs from seal")
    return selected, excluded_intervals, excluded_bytes


def cobs_decode(encoded: bytes) -> bytes:
    output = bytearray(); cursor = 0
    while cursor < len(encoded):
        code = encoded[cursor]; cursor += 1
        if code == 0 or cursor + code - 1 > len(encoded):
            raise ValueError("invalid COBS")
        output.extend(encoded[cursor:cursor + code - 1]); cursor += code - 1
        if code != 0xFF and cursor < len(encoded):
            output.append(0)
    return bytes(output)


def envelope(encoded: bytes) -> tuple[int, str, bytes]:
    raw = cobs_decode(encoded)
    if len(raw) < ENVELOPE_HEADER.size + 2:
        raise ValueError("short envelope")
    body = raw[:-2]
    expected = struct.unpack_from("<H", raw, len(raw) - 2)[0]
    if binascii.crc_hqx(body, 0xFFFF) != expected:
        raise ValueError("CRC")
    magic, version, kind, node_id, length, _sequence, _master_ms = ENVELOPE_HEADER.unpack_from(body)
    payload = body[ENVELOPE_HEADER.size:]
    if magic != 0x5342 or version != 1 or len(payload) != length:
        raise ValueError("envelope contract")
    return int(kind), f"BSF{node_id:04X}", payload


def decode_accel_gyro_only(
    raw_path: Path, start_byte: int, stop_byte: int, models: dict[str, Any],
    annotation_start_ns: int, annotation_stop_ns: int,
    forbidden_ranges: list[tuple[int, int]],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    """Decode only accel/gyro and timing/status; skip temperature structurally."""
    size = raw_path.stat().st_size
    if not 0 <= start_byte < stop_byte <= size:
        raise ValueError("invalid bounded raw bracket")
    if any(max(start_byte, left) < min(stop_byte, right) for left, right in forbidden_ranges):
        raise ValueError("raw bracket intersects excluded payload")
    with raw_path.open("rb", buffering=0) as stream:
        stream.seek(start_byte)
        payload = stream.read(stop_byte - start_byte)
    if len(payload) != stop_byte - start_byte:
        raise IOError("short bounded raw read")
    rows: dict[str, list[tuple[Any, ...]]] = {node: [] for node in NODES}
    counts: Counter[str] = Counter(); errors: Counter[str] = Counter()
    boots: Counter[str] = Counter(); last_timer: dict[str, int] = {}
    cursor = 0
    for encoded in payload.split(b"\0")[:-1]:
        raw_start = start_byte + cursor; raw_end = raw_start + len(encoded) + 1
        cursor += len(encoded) + 1
        if not encoded:
            continue
        try:
            kind, node, imu = envelope(encoded)
            if node not in rows:
                counts["non_candidate_node_envelopes_skipped"] += 1; continue
            if kind != 3:
                counts[f"payload_kind_{kind}_skipped_without_payload_decode"] += 1; continue
            if len(imu) < 14:
                raise ValueError("short IMU header")
            # The wire header is <BBHQh>. Decode the first 12 bytes only. The
            # final two temperature bytes are advanced over, never unpacked,
            # materialized, exposed, converted, hashed separately, or consumed.
            version, sample_count, sequence, base_us = struct.unpack_from("<BBHQ", imu, 0)
            sample_offset = 14
            if version != 7 or not 1 <= sample_count <= 16 or len(imu) != sample_offset + sample_count * IMU_SAMPLE.size:
                raise ValueError("IMU contract")
            counts["temperature_header_bytes_structurally_skipped"] += 2
            for sample_index in range(sample_count):
                delta, ax, ay, az, gx, gy, gz = IMU_SAMPLE.unpack_from(
                    imu, sample_offset + sample_index * IMU_SAMPLE.size,
                )
                timer_us = int(base_us + delta)
                previous = last_timer.get(node)
                if previous is not None and timer_us < previous:
                    boots[node] += 1
                last_timer[node] = timer_us
                absolute_ns = models[node].map_ns(timer_us)
                rows[node].append((
                    int(boots[node]), (int(sequence) + sample_index) & 0xFFFF,
                    timer_us, absolute_ns - annotation_start_ns,
                    int(round(models[node].sigma_at_ns(timer_us))), int(delta),
                    (ax, ay, az), (gx, gy, gz), raw_start, raw_end,
                    sample_index, int(annotation_start_ns <= absolute_ns < annotation_stop_ns),
                ))
            counts["imu_envelopes_decoded"] += 1
            counts["imu_samples_decoded"] += sample_count
        except (ValueError, struct.error, IndexError) as exc:
            errors[f"{type(exc).__name__}:{exc}"] += 1
    output = {node: np.asarray(value, dtype=ALLOWED_DTYPE) for node, value in rows.items()}
    missing = [node for node, value in output.items() if np.count_nonzero(value["status"] == 1) < 2]
    if missing:
        raise RuntimeError(f"raw span lacks accepted IMU rows: {missing}")
    return output, {
        "decoder": "LOCAL_BOUNDED_ACCEL_GYRO_ONLY_V1",
        "decoded_field_manifest": list(ALLOWED_DTYPE.names),
        "scientific_measurement_fields": ["acc_raw", "gyro_raw"],
        "allowed_timing_status_fields": [
            "boot_epoch", "sequence", "node_timer_us", "global_time_ns",
            "global_time_sigma_ns", "delta_us", "status",
        ],
        "audit_only_raw_location_fields": [
            "raw_start_offset", "raw_end_offset", "raw_sample_index",
        ],
        "temperature_field_exposed": False,
        "temperature_field_materialized": False,
        "temperature_field_consumed": False,
        "temperature_header_bytes_structurally_skipped": int(counts["temperature_header_bytes_structurally_skipped"]),
        "magnetometer_fields_decoded": [], "uwb_spatial_fields_decoded": [],
        "range_values_consumed": False, "anchor_geometry_consumed": False,
        "decode_counts": dict(counts), "decode_errors": dict(errors),
        "raw_access": {
            "requested_intervals": [[start_byte, stop_byte]],
            "actual_read_intervals": [[start_byte, stop_byte]],
            "slice_sha256": hashlib.sha256(payload).hexdigest(),
            "complete_container_hash_recalculated": False,
            "complete_container_scan_attempted": False,
            "container_bytes": size, "actual_read_bytes": len(payload),
            "forbidden_intervals": [list(row) for row in forbidden_ranges],
            "forbidden_interval_bytes_touched": False,
        },
        "nodes": {
            node: {
                "accepted_rows": int(np.count_nonzero(value["status"] == 1)),
                "strictly_increasing": bool(np.all(np.diff(value[value["status"] == 1]["global_time_ns"]) > 0)),
            } for node, value in output.items()
        },
    }


def interpolate_node(rows: np.ndarray, grid: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    accepted = rows[rows["status"] == 1]
    times = accepted["global_time_ns"].astype(np.int64)
    unique, index = np.unique(times, return_index=True)
    duplicate_count = int(len(times) - len(unique))
    accepted = accepted[index]; times = unique
    acc = accepted["acc_raw"].astype(float) / 2048.0 * G
    gyr = np.deg2rad(accepted["gyro_raw"].astype(float) / 16.384)
    right = np.searchsorted(times, grid, side="left")
    right = np.clip(right, 1, len(times) - 1); left = right - 1
    gap = times[right] - times[left]
    valid = (times[left] <= grid) & (grid <= times[right]) & (gap <= MAX_INTERPOLATION_GAP_NS)
    alpha = ((grid - times[left]) / np.maximum(gap, 1)).reshape(-1, 1)
    out_acc = acc[left] + alpha * (acc[right] - acc[left])
    out_gyr = gyr[left] + alpha * (gyr[right] - gyr[left])
    dt = np.diff(times)
    gap_records = []
    for gap_index in np.flatnonzero(dt > MAX_INTERPOLATION_GAP_NS):
        left = accepted[gap_index]; right_row = accepted[gap_index + 1]
        duration = int(dt[gap_index])
        gap_records.append({
            "left_global_time_ns": int(times[gap_index]),
            "right_global_time_ns": int(times[gap_index + 1]),
            "duration_ns": duration,
            "estimated_missing_5ms_samples": max(0, int(round(duration / GRID_NS)) - 1),
            "left_right_status": [int(left["status"]), int(right_row["status"])],
            "left_right_boot_epoch": [int(left["boot_epoch"]), int(right_row["boot_epoch"])],
            "left_right_sequence": [int(left["sequence"]), int(right_row["sequence"])],
            "sequence_delta_mod65536": int((int(right_row["sequence"]) - int(left["sequence"])) & 0xFFFF),
            "node_timer_delta_us": int(right_row["node_timer_us"] - left["node_timer_us"]),
            "raw_byte_interval_between_bracketing_samples": [int(left["raw_end_offset"]), int(right_row["raw_start_offset"])],
            "observed_status_cause": "NO_REJECT_STATUS_AT_GAP_BOUNDARIES;SAMPLE_ABSENCE_IN_BOUNDED_RAW_STREAM",
        })
    return out_acc, out_gyr, {
        "native_rows": int(len(rows)), "accepted_rows": int(len(accepted)),
        "duplicate_timestamps_removed": duplicate_count,
        "strictly_monotone_after_dedup": bool(np.all(dt > 0)),
        "native_dt_ns": {"median": float(np.median(dt)), "q99": float(np.quantile(dt, .99)), "maximum": int(np.max(dt))},
        "grid_valid_fraction": float(np.mean(valid)), "invalid_grid_rows": int(np.count_nonzero(~valid)),
        "maximum_interpolation_gap_ns": MAX_INTERPOLATION_GAP_NS,
        "arbitrary_gap_interpolation": False,
        "all_row_status_counts": {str(int(key)): int(value) for key, value in zip(*np.unique(rows["status"], return_counts=True))},
        "gaps_exceeding_original_bound": gap_records,
        "gap_count_exceeding_original_bound": len(gap_records),
        "gap_duration_sum_ns": int(sum(row["duration_ns"] for row in gap_records)),
        "_valid": valid,
        "_bracketing_gap_ns": gap,
    }


def continuous_qmt_vqf(
    acc: np.ndarray, gyr: np.ndarray, consume_measurement: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    """One QMT VQF state; masked rows hold state and consume no fake sample."""
    block = qmt.OriEstVQFBlock(GRID_NS * 1e-9)
    quat = np.empty((len(acc), 4), dtype=float)
    last = np.array([1.0, 0.0, 0.0, 0.0])
    for index in range(len(acc)):
        if consume_measurement[index]:
            last = np.asarray(block.step(gyr[index], acc[index], None), dtype=float)
        quat[index] = last
    return quat, {
        "implementation": "qmt.OriEstVQFBlock",
        "single_state_instances": 1,
        "state_resets_after_construction": 0,
        "measurement_updates": int(np.count_nonzero(consume_measurement)),
        "no_update_uncertainty_rows": int(np.count_nonzero(~consume_measurement)),
        "no_update_mask_sha256": array_hash(np.asarray(~consume_measurement, dtype=np.uint8)),
    }


def quat_to_matrix(quat: np.ndarray) -> np.ndarray:
    return Rotation.from_quat(np.asarray(quat)[:, [1, 2, 3, 0]]).as_matrix()


def episode_mask(grid: np.ndarray, row: dict[str, Any], bridges: dict[str, dict[str, Any]]) -> np.ndarray:
    bridge = bridges[row["action"]]
    def mapped(host_ns: int) -> int:
        host_s = host_ns * 1e-9
        absolute = (bridge["listener_global_us_per_host_s"] * host_s + bridge["listener_global_us_intercept"]) * 1000
        return int(round(absolute))
    return (grid >= mapped(row["episode_start_host_ns"])) & (grid < mapped(row["episode_stop_host_ns"]))


def select_uniform(mask: np.ndarray, limit: int = 7500) -> np.ndarray:
    index = np.flatnonzero(mask)
    if len(index) <= limit:
        return index
    return index[np.linspace(0, len(index) - 1, limit).round().astype(int)]


def run() -> int:
    assert_layout(); seal_record = verify_seal()
    for name in (
        "RAW_ACCESS_AUDIT_CORRECTED_001.json",
        "BASELINE_DIAGNOSTICS_UNHEADING_CORRECTED_001.json",
        "C2_TO_QMT_INPUT_CONTRACT.json",
        "C2_VQF_UNHEADING_CORRECTED_TRAJECTORIES_001.npz",
        "C2_FRONT_SIDE_TOP_UNHEADING_CORRECTED_DIAGNOSTIC_001",
    ):
        if (RUN_DIR / name).exists():
            raise FileExistsError(f"refusing to overwrite existing run artifact: {name}")
    selected, excluded_time, excluded_bytes = load_plan()
    first, last = selected[0], selected[-1]
    raw_path = C2_ROOT / "system/fusion_continuous/fusion_host_raw.cobs.bin"
    raw_start, raw_stop = first["start_byte"], last["stop_byte"]
    overlaps = [row for row in excluded_bytes if max(raw_start, row[0]) < min(raw_stop, row[1])]
    if overlaps:
        raise RuntimeError(f"continuous selected span overlaps excluded payload intervals: {overlaps}")
    timing_log = C2_ROOT / "system/fusion_continuous/fusion_cdc.log"
    listener_dir = C2_ROOT / "system/listeners/passive_5"
    readiness = C2_ROOT / "system/readiness/SYSTEM_READINESS_REPORT.json"
    listener_summary = json_file(listener_dir / "summary.json")
    local_models: dict[str, list[Any]] = {node: [] for node in NODES}
    local_models_json: dict[str, dict[str, Any]] = {}
    local_gates: dict[str, Any] = {}
    bridges: dict[str, dict[str, Any]] = {}
    residual_count = 0
    for episode in selected:
        target_seed_ns = episode["episode_stop_host_ns"] - 1_000_000_000
        timing_seeds = {str(timing_log.resolve()): timing_seed(timing_log, target_seed_ns, jsonl=False)}
        for snr, info in listener_summary["listeners"].items():
            kinds = info.get("kinds", {})
            if info.get("first_lstat", {}).get("role") != "OBSERVER" or not kinds.get("LPD") or not kinds.get("LBD"):
                continue
            path = listener_dir / "listeners" / f"{snr}.jsonl"
            timing_seeds[str(path.resolve())] = timing_seed(path, target_seed_ns, jsonl=True)
        episode_models, episode_residuals, episode_gate = align_capture_bounded(
            timing_log, listener_dir, readiness,
            episode["episode_start_host_ns"] * 1e-9, episode["episode_stop_host_ns"] * 1e-9,
            NODES, expected_readiness_sha256=sha256_file(readiness),
            safe_ceiling_seed_offsets=timing_seeds,
            forbidden_time_intervals_ns=excluded_time,
        )
        if not episode_gate["reconstruction_safe"]:
            raise RuntimeError(
                f"BLOCKED_AT_TIMEBASE_ADAPTER:{episode['action']}:"
                f"{episode_gate['reconstruction_safety_failures']}"
            )
        if not episode_gate["maximum_clean_anchor_gap"]:
            raise RuntimeError(f"BLOCKED_AT_TIMEBASE_ADAPTER:{episode['action']}:60_SECOND_GATE")
        for node in NODES:
            local_models[node].append(episode_models[node])
        local_models_json[episode["action"]] = models_as_json(episode_models)
        bridges[episode["action"]] = episode_gate["action_annotation_bridge"]
        residual_count += len(episode_residuals)
        local_gates[episode["action"]] = episode_gate
    models = {node: PiecewiseClock(node, values) for node, values in local_models.items()}
    piecewise_audit = {node: model.audit() for node, model in models.items()}
    if not all(row["strictly_monotone_20001_point_proof"] and row["maximum_boundary_discontinuity_ns"] == 0 for row in piecewise_audit.values()):
        raise RuntimeError("BLOCKED_AT_TIMEBASE_ADAPTER:PIECEWISE_CONTINUITY_PROOF")
    def map_host(host_ns: int, action: str) -> int:
        bridge = bridges[action]
        return int(round((bridge["listener_global_us_per_host_s"] * host_ns * 1e-9 + bridge["listener_global_us_intercept"]) * 1000))
    absolute_start = map_host(first["episode_start_host_ns"], first["action"])
    absolute_stop = map_host(last["episode_stop_host_ns"], last["action"])
    rows, decode = decode_accel_gyro_only(
        raw_path, raw_start, raw_stop, models, absolute_start, absolute_stop,
        excluded_bytes,
    )
    # _decode_imu_only normalizes to absolute_start. Restore absolute common time
    # so event masks and every node share the same capture-wide axis.
    for value in rows.values():
        value["global_time_ns"] += absolute_start
    grid_start = max(int(value[value["status"] == 1]["global_time_ns"][0]) for value in rows.values())
    grid_stop = min(int(value[value["status"] == 1]["global_time_ns"][-1]) for value in rows.values())
    grid = np.arange((grid_start // GRID_NS + 1) * GRID_NS, grid_stop, GRID_NS, dtype=np.int64)
    episode_masks = {row["action"]: episode_mask(grid, row, bridges) for row in selected}
    selected_factor_mask = np.zeros(len(grid), dtype=bool)
    for value in episode_masks.values():
        selected_factor_mask |= value
    acc = {}; gyr = {}; time_audit = {}; invalid_masks = {}; consume_masks = {}
    recovered_factor_masks = {}; common_consumable = np.ones(len(grid), bool)
    for node in NODES:
        acc[node], gyr[node], time_audit[node] = interpolate_node(rows[node], grid)
        node_valid = time_audit[node].pop("_valid")
        bracketing_gap_ns = time_audit[node].pop("_bracketing_gap_ns")
        invalid_masks[node] = ~node_valid
        recovered_factor = (
            (~node_valid) & selected_factor_mask
            & (bracketing_gap_ns <= MAX_RECOVERABLE_FACTOR_GAP_NS)
        )
        unrecoverable_factor = (
            (~node_valid) & selected_factor_mask
            & (bracketing_gap_ns > MAX_RECOVERABLE_FACTOR_GAP_NS)
        )
        if np.any(unrecoverable_factor):
            raise RuntimeError(
                f"BLOCKED_AT_TIMEBASE_ADAPTER:{node}:SELECTED_FACTOR_GAP_EXCEEDS_80MS"
            )
        consume_masks[node] = node_valid | recovered_factor
        recovered_factor_masks[node] = recovered_factor
        common_consumable &= consume_masks[node]
        time_audit[node]["invalid_grid_mask_sha256"] = array_hash(np.asarray(~node_valid, dtype=np.uint8))
        time_audit[node].update({
            "selected_factor_rows": int(np.count_nonzero(selected_factor_mask)),
            "selected_factor_recovered_rows": int(np.count_nonzero(recovered_factor)),
            "selected_factor_recovered_mask_sha256": array_hash(np.asarray(recovered_factor, dtype=np.uint8)),
            "long_nonfactor_no_update_rows": int(np.count_nonzero((~node_valid) & (~selected_factor_mask))),
            "long_nonfactor_rows_consumed_as_measurements": 0,
            "fixed_recovery_class_ns": MAX_RECOVERABLE_FACTOR_GAP_NS,
        })
    affected_factor_rows = np.zeros(len(grid), dtype=bool)
    for value in recovered_factor_masks.values():
        affected_factor_rows |= value
    if np.any((~common_consumable) & selected_factor_mask):
        raise RuntimeError("BLOCKED_AT_TIMEBASE_ADAPTER:UNRECOVERED_SELECTED_FACTOR_ROW")
    clock_gap_audit = {}
    for node, model in models.items():
        anchored = np.zeros(len(grid), dtype=bool)
        for left, right in model.anchored_time_ranges():
            anchored |= (grid >= left) & (grid <= right)
        clock_gap_audit[node] = {
            "qualified_anchor_grid_rows": int(np.count_nonzero(anchored)),
            "unanchored_covariance_inflated_grid_rows": int(np.count_nonzero(~anchored)),
            "unanchored_gap_mask_sha256": array_hash(np.asarray(~anchored, dtype=np.uint8)),
            "unanchored_rows_used_as_episode_factor_rows": False,
        }
    initial_mask = episode_mask(grid, selected[0], bridges)
    unit_audit = {}
    for node in NODES:
        acc_norm = np.linalg.norm(acc[node][initial_mask], axis=1)
        gyro_norm = np.linalg.norm(gyr[node][initial_mask], axis=1)
        unit_audit[node] = {
            "conversion": "acc_raw/2048*9.80665_mps2;gyro_raw/16.384_deg_s_then_rad_s",
            "scale_provenance": {
                "packet_order_source": "local wire contract <Hhhhhhh> copied from version-7 production capture decoder",
                "acceleration_lsb_per_g": 2048.0,
                "gyroscope_lsb_per_deg_s": 16.384,
                "local_code_sources": [
                    "src/biospur_fusion/imu/preintegration.py",
                    "src/biospur_fusion/imu/frontend.py",
                ],
                "capture_firmware": "b306-imu-relay-v47",
                "capture_fwid": "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed",
                "capture_image_sha": "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98",
                "capture_readiness_sha256": "77053bce68e255357ceeccdcced3977bce73e62d23052797e67abc14fd856d3e",
                "scale_status": "VERIFIED_FIXED_JY61P_WT61P_PROTOCOL_OUTPUT",
                "axis_semantics": (
                    "signed little-endian JY61P AX,AY,AZ,GX,GY,GZ register order is bound and "
                    "B306 v47 applies no axis permutation/sign flip; sensor-to-anatomical "
                    "extrinsics remain functional estimates within qualitative wear cones"
                ),
                "axis_semantics_authority": str(AUTHORITIES["frame_amendment"].relative_to(ROOT)),
            },
            "still_acc_norm_median_mps2": float(np.median(acc_norm)),
            "still_acc_norm_q05_q95_mps2": [float(np.quantile(acc_norm, .05)), float(np.quantile(acc_norm, .95))],
            "still_gyro_norm_median_deg_s": float(np.degrees(np.median(gyro_norm))),
            "still_gyro_component_std_rad_s": np.std(gyr[node][initial_mask], axis=0).tolist(),
            "still_gyro_component_bias_deg_s": np.degrees(np.mean(gyr[node][initial_mask], axis=0)).tolist(),
            "finite_acceleration_si": bool(np.isfinite(acc[node]).all()),
            "finite_gyroscope_si": bool(np.isfinite(gyr[node]).all()),
            "raw_acceleration_saturation_count": int(np.count_nonzero(np.abs(rows[node]["acc_raw"].astype(np.int32)) >= 32767)),
            "raw_gyroscope_saturation_count": int(np.count_nonzero(np.abs(rows[node]["gyro_raw"].astype(np.int32)) >= 32767)),
            "raw_acceleration_full_scale_margin_counts": int(32767 - np.max(np.abs(rows[node]["acc_raw"].astype(np.int32)))),
            "raw_gyroscope_full_scale_margin_counts": int(32767 - np.max(np.abs(rows[node]["gyro_raw"].astype(np.int32)))),
            "plausible_gravity": bool(7.0 <= np.median(acc_norm) <= 12.5),
            "plausible_still_gyro": bool(np.degrees(np.median(gyro_norm)) < 15.0),
        }
    if not all(
        row["plausible_gravity"] and row["plausible_still_gyro"]
        and row["finite_acceleration_si"] and row["finite_gyroscope_si"]
        and row["raw_acceleration_saturation_count"] == 0
        and row["raw_gyroscope_saturation_count"] == 0
        for row in unit_audit.values()
    ):
        raise RuntimeError("BLOCKED_AT_UNIT_AXIS_VALIDATION")

    q_test = qmt.quatFromAngleAxis(np.deg2rad(37.0), qmt.normalized([1.0, 2.0, 3.0]))
    r_qmt = np.asarray(qmt.quatToRotMat(q_test), dtype=float)
    vector_test = np.array([0.3, -0.4, 0.5])
    r_viewer = quat_to_matrix(np.asarray(q_test).reshape(1, 4))[0]
    q_scipy_xyzw = Rotation.from_matrix(r_qmt).as_quat()
    q_scipy_wxyz = q_scipy_xyzw[[3, 0, 1, 2]]
    quaternion_test = {
        "qmt_order": "wxyz",
        "qmt_rotation_direction": "qmt.rotate(q,v)==qmt.quatToRotMat(q)@v; sensor/body vector to global frame",
        "qmt_rotate_vs_matrix_max_abs_error": float(np.max(np.abs(qmt.rotate(q_test, vector_test) - r_qmt @ vector_test))),
        "viewer_matrix_vs_qmt_matrix_max_abs_error": float(np.max(np.abs(r_viewer - r_qmt))),
        "qmt_scipy_roundtrip_abs_dot": float(abs(np.dot(q_test, q_scipy_wxyz))),
        "pass": bool(
            np.allclose(qmt.rotate(q_test, vector_test), r_qmt @ vector_test, atol=1e-12)
            and np.allclose(r_viewer, r_qmt, atol=1e-12)
            and abs(np.dot(q_test, q_scipy_wxyz)) > 1 - 1e-12
        ),
    }
    if not quaternion_test["pass"]:
        raise RuntimeError("BLOCKED_AT_QUATERNION_CONVENTION")

    input_contract = {
        "schema": "biospur-c2-to-qmt-input-contract-v1",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "scientific_qualification": False,
        "qmt_interface": {
            "gyr": "Nx3 rad/s", "acc": "Nx3 m/s^2 specific force with gravity retained",
            "constant_Ts_seconds": GRID_NS * 1e-9, "quaternion": quaternion_test,
            "source_hashes": {
                "qmt/functions/oriest.py": "13524b792f7dbfbedc4079079a469e4539324121988d9167a133ed15c6a674f9",
                "qmt/functions/quaternion.py": "0f698376e0a11acb16a9efaef30fc299bc62994390a809d302d7ce3eaa3c959e",
                "qmt/functions/heading_correction.py": "2bf43206a0ae04efda88183b3c96325f915283ed80506384e6fdbc48c3720cc2",
                "qmt/blocks/oriest.py": "83019d4fef7324ee8f314b2d2bb72bf388290a47d4770adfbde84cf47e439a96"
            },
        },
        "wire_decode": {
            "version": 7, "header": "<BBHQh>", "sample": "<Hhhhhhh>",
            "sample_order": ["delta_us", "ax", "ay", "az", "gx", "gy", "gz"],
            "status_boot_sequence_semantics_preserved": True,
            "temperature_structurally_skipped_not_exposed_or_consumed": True,
            "protocol_source_sha256": "307687f266367a0aa8565bf1a560a6bff861daaa76496b9216de343b2ecd8281",
        },
        "scale": {
            "conversion": "acc_raw/2048*9.80665 m/s^2; gyro_raw/16.384 deg/s then rad/s",
            "status": "VERIFIED",
            "direct_user_authority": (
                "B306 v47 reads signed little-endian AX/AY/AZ/GX/GY/GZ from 0x34 without remap; "
                "write-verifies RRATE=200 Hz and BW=98 Hz; official WIT protocol fixes output "
                "decoding at int16/32768*16 g and gyro at +/-2000 deg/s"
            ),
            "capture_firmware": {
                "fw": "b306-imu-relay-v47",
                "fwid": "f7436728c36efdd28f848e7ef59c7c422437afb8c6ee07dd8924e31967046eed",
                "image_sha": "90ef063b227feb4c70499cc186df866c24da658fba98773eacc40da73a0abf98",
                "readiness_path": "system/readiness/SYSTEM_READINESS_REPORT.json",
                "readiness_sha256": "77053bce68e255357ceeccdcced3977bce73e62d23052797e67abc14fd856d3e"
            },
            "driver_reference_not_opened_due_workspace_firewall": "B306_Part/firmware/src/imu.c",
            "manufacturer_reference": "WIT Standard Communication Protocol / JY61P-WT61P documentation",
            "acceleration_protocol": "ACCRANGE 0x21 not configurable; adaptive internal range; output always int16/32768*16 g",
            "gyroscope_protocol": "fixed +/-2000 deg/s output",
            "capture_rate_authority": "system/readiness/TEN_NODE_IMU_RUNTIME_RECOVERY.json",
            "capture_rate_authority_sha256": "61e79ac9f1c7299407af83c02cc4e5590fd1c3b3bde11cc60429a9ce0aee11d2",
            "range_register_write_required_for_output_scale": False,
            "scale_precedent_config": "config/imu_relative_orientation_preview_v0/gates_v0.json",
            "scale_precedent_sha256": "573afa5a1138a51055e305bd7fa43f362635a0ea6049fd1d9676f181496c8497",
            "qualification": "VERIFIED_INPUT_UNIT_CONVERSION;BASELINE_REMAINS_DIAGNOSTIC_FOR_OTHER_REASONS",
        },
        "axis_and_specific_force": {
            "axis_order": "bound signed JY61P register axes x,y,z unchanged",
            "raw_axis_handed_convention_status": "BOUND_BY_MANUFACTURER_REGISTER_DEFINITION_AND_B306_DIRECT_DECODE",
            "permutation_or_sign_remap": None,
            "gravity_subtracted_before_qmt": False,
            "frame_authority_sha256": "989215949e7d33b6939258067d4dd3b9fc18826ca210efb3ad0f6a1d212e175d",
            "sensor_to_anatomical_extrinsic_status": "FUNCTIONAL_ESTIMATE_WITHIN_QUALITATIVE_WEAR_CONES",
            "wear_prior_use": (
                "sensor -Y approximately down and per-node sensor -Z direction are soft "
                "right-handed nominal-frame/hemisphere guards, never exact mount vectors"
            ),
            "new_six_face_or_90_degree_capture_required": False,
        },
        "timing": {
            "uniform_frontend_Ts_ns": GRID_NS,
            "original_native_interpolation_bound_ns": MAX_INTERPOLATION_GAP_NS,
            "fixed_selected_factor_recovery_class_ns": MAX_RECOVERABLE_FACTOR_GAP_NS,
            "long_interepisode_policy": "single QMT VQF state, no measurement update, explicit uncertainty mask",
            "episode_resets": 0, "new_yaw_gauges": 0,
            "affected_factor_rows_mask_sha256": array_hash(np.asarray(affected_factor_rows, dtype=np.uint8)),
            "affected_factor_rows": int(np.count_nonzero(affected_factor_rows)),
        },
        "heading_correction_compatibility": {
            "upstream_api_requires_equidistant_t": True,
            "upstream_constant_time_assertion_lines": [146, 148],
            "supported_measurement_mask_or_no_update_api_found": False,
            "dense_invalid_rows_may_not_be_fabricated": True,
            "valid_row_time_compression_forbidden": True,
            "c2_headingCorrection_called": False,
            "c2_deltaFilt_or_quat2Corr_claimed": False,
            "limitation": "UPSTREAM_HEADING_CORRECTION_GAP_MASK_INCOMPATIBILITY",
        },
        "per_node_empirical_checks": unit_audit,
        "viewer_consumes_same_unreset_qmt_vqf_trajectory": True,
        "diagnostic_limitations": [
            "sensor-to-anatomical extrinsics remain functional estimates within soft wear cones",
            "upstream headingCorrection cannot represent missing/no-update rows on the constant grid",
        ],
    }
    write_json(RUN_DIR / "C2_TO_QMT_INPUT_CONTRACT.json", input_contract, immutable=True)

    quat = {}; orientation_audit = {}
    for node in NODES:
        quat[node], orientation_audit[node] = continuous_qmt_vqf(
            acc[node], gyr[node], consume_masks[node],
        )
    by_action = {row["action"]: row for row in selected}
    hinge_specs = {
        "elbow_left": ("BSFAA61", "BSFEC35", ["06_elbow_left"]),
        "elbow_right": ("BSF1120", "BSFB165", ["07_elbow_right"]),
        "knee_left": ("BSF44AD", "BSF6C53", ["10_knee_left_seated", "16_squat", "18_heel_to_butt_left"]),
        "knee_right": ("BSF3C79", "BSF8BC4", ["11_knee_right_seated", "16_squat", "19_heel_to_butt_right"]),
    }
    viewer_trajectory = {node: value.copy() for node, value in quat.items()}
    extrinsic_audit = {}
    for node in ("BSF31CC", "BSFC2CC"):
        mean_acc = np.mean(acc[node][initial_mask], axis=0)
        qbs = qmt.quatFrom2Axes(
            x=np.array([0.0, 0.0, -1.0]), z=mean_acc, exactAxis="z",
        )
        viewer_trajectory[node] = qmt.qmult(quat[node], qbs)
        extrinsic_audit[node] = {
            "method": "INITIAL_STILL_GRAVITY_PLUS_QUALITATIVE_SENSOR_MINUS_Z_FORWARD_NOMINAL_FRAME",
            "exact_gravity_observation_used": True,
            "wear_minus_z_is_soft_cone_not_exact_vector": True,
            "qbs_wxyz": np.asarray(qbs).tolist(),
        }
    hinge_report = {}
    for name, (parent, child, actions) in hinge_specs.items():
        mask = np.zeros(len(grid), bool)
        for action in actions:
            mask |= episode_mask(grid, by_action[action], bridges)
        mask &= ~affected_factor_rows
        mask &= common_consumable
        chosen = select_uniform(mask)
        sigma_acc = max(float(np.mean(np.std(acc[parent][initial_mask], axis=0))), 1e-6)
        sigma_gyr = max(float(np.mean(np.std(gyr[parent][initial_mask], axis=0))), 1e-7)
        j1, j2, debug = qmt.jointAxisEstHingeOlsson(
            acc[parent][chosen], acc[child][chosen], gyr[parent][chosen], gyr[child][chosen],
            estSettings={
                "wa": 1.0 / sigma_acc, "wg": 1.0 / sigma_gyr,
                "useSampleSelection": True, "dataSize": min(7500, len(chosen)),
                "winSize": 41, "angRateEnergyThreshold": max(1.0, 25 * sigma_gyr**2),
                "tol": 1e-8, "quiet": True,
            }, debug=True,
        )
        j1 = np.asarray(j1).reshape(3); j2 = np.asarray(j2).reshape(3)
        wear_cone_branch_flip = bool(np.dot(j2, np.array([0.0, 0.0, -1.0])) < 0)
        if wear_cone_branch_flip:
            j1 = -j1
            j2 = -j2
        mean_acc_parent = np.mean(acc[parent][initial_mask], axis=0)
        mean_acc_child = np.mean(acc[child][initial_mask], axis=0)
        qbs_parent = qmt.quatFrom2Axes(z=j1, y=mean_acc_parent, exactAxis="y")
        qbs_child = qmt.quatFrom2Axes(z=j2, y=mean_acc_child, exactAxis="y")
        qseg_parent = qmt.qmult(quat[parent], qbs_parent)
        qseg_child = qmt.qmult(quat[child], qbs_child)
        viewer_trajectory[parent] = qseg_parent
        viewer_trajectory[child] = qseg_child
        extrinsic_audit[parent] = {
            "method": f"QMT_HINGE_AXIS_{name}_PLUS_INITIAL_STILL_GRAVITY",
            "wear_minus_z_is_soft_hemisphere_guard": True,
            "qbs_wxyz": np.asarray(qbs_parent).tolist(),
        }
        extrinsic_audit[child] = {
            "method": f"QMT_HINGE_AXIS_{name}_PLUS_INITIAL_STILL_GRAVITY",
            "wear_minus_z_is_soft_hemisphere_guard": True,
            "qbs_wxyz": np.asarray(qbs_child).tolist(),
        }
        hinge_report[name] = {
            "parent_node": parent, "child_node": child, "episodes": actions,
            "preregistered_selected_grid_indices": chosen.tolist(),
            "selected_rows": int(len(chosen)), "effective_rows": int(len(np.unique(chosen))),
            "recovered_or_downweighted_factor_rows_excluded": int(np.count_nonzero(affected_factor_rows & np.logical_or.reduce([episode_masks[action] for action in actions]))),
            "heading_correction_status": "NOT_RUN_UPSTREAM_GAP_MASK_INCOMPATIBILITY",
            "heading_correction_measurement_rows": 0,
            "heading_no_update_rows": int(len(grid)),
            "recovered_factor_rows_passed_to_heading_correction": 0,
            "axis_parent_sensor": j1.tolist(), "axis_child_sensor": j2.tolist(),
            "manual_sign_flip": False,
            "wear_cone_hemisphere_branch_flip": wear_cone_branch_flip,
            "fabricated_rom": False,
            "delta_filt_spread_deg": None,
            "delta_filt_sha256": None,
            "qmt_heading_rating": None,
            "time_varying_heading_consumed_by_viewer": False,
            "unreset_qmt_vqf_consumed_by_viewer": True,
            "qmt_debug_keys": sorted(debug),
        }
    trajectory_path = RUN_DIR / "C2_VQF_UNHEADING_CORRECTED_TRAJECTORIES_001.npz"
    np.savez_compressed(
        trajectory_path,
        grid_ns=grid,
        affected_factor_rows=affected_factor_rows,
        common_consumable=common_consumable,
        **{f"quat_{node}": viewer_trajectory[node] for node in NODES},
    )
    trajectory_path.chmod(0o444)
    lengths = {
        "torso": .280, "upper_arm_left": .3175, "forearm_left": .245,
        "upper_arm_right": .3175, "forearm_right": .245,
        "thigh_left": .480, "shank_left": .430,
        "thigh_right": .480, "shank_right": .430,
    }
    directions = {
        "torso": np.array([0., 0., 1.]),
        **{name: np.array([0., -1., 0.]) for name in lengths if name != "torso"},
    }
    scene_specs = (
        ("initial_standing", "00_initial_still"),
        ("representative_upper", "06_elbow_left"),
        ("representative_lower", "16_squat"),
    )
    image_rows = []
    image_dir = RUN_DIR / "C2_FRONT_SIDE_TOP_UNHEADING_CORRECTED_DIAGNOSTIC_001"
    image_dir.mkdir(exist_ok=False)
    for scene, action in scene_specs:
        indices = np.flatnonzero(episode_mask(grid, by_action[action], bridges) & common_consumable & ~affected_factor_rows)
        index = int(indices[len(indices) // 2])
        rotations = {
            IDENTITY[node]: quat_to_matrix(viewer_trajectory[node][index:index+1])[0]
            for node in NODES
        }
        points = {"pelvis": np.array([0., 0., 1.0])}
        for child, parent in PARENTS.items():
            if parent is None:
                continue
            points[child] = points[parent] + rotations[child] @ (directions[child] * lengths[child])
        for view, dims in (("front", (1, 2)), ("side", (0, 2)), ("top", (1, 0))):
            fig, axis = plt.subplots(figsize=(6, 7), constrained_layout=True)
            for parent, child in EDGES:
                values = np.vstack((points[parent], points[child]))
                axis.plot(values[:, dims[0]], values[:, dims[1]], "k-o", lw=2, ms=4)
            for name, point in points.items():
                axis.text(point[dims[0]], point[dims[1]], name, fontsize=7)
            axis.set_aspect("equal", adjustable="datalim"); axis.grid(True, alpha=.25)
            axis.set_title(
                f"C2 QMT UNHEADING_CORRECTED_DIAGNOSTIC | {scene} | {view}\n"
                "10 segments/9 edges; no IK/rebase/repair"
            )
            path = image_dir / f"{scene}_{view}.png"
            fig.savefig(path, dpi=180); plt.close(fig); path.chmod(0o444)
            image_rows.append({
                "scene": scene, "action": action, "view": view, "grid_index": index,
                "time_ns": int(grid[index]), "path": str(path), "sha256": sha256_file(path),
                "trajectory_hashes": {
                    segment: array_hash(np.asarray(viewer_trajectory[node], dtype="<f8"))
                    for node, segment in IDENTITY.items()
                },
            })
    access = {
        "schema": "biospur-c2-qmt-diagnostic-raw-access-audit-v1",
        "seal": {name: sha256_file(RUN_DIR / name) for name in (
            "RUN_START_CONTRACT.json", "CONTRACT_COMPLIANCE.json", "METADATA_PRESELECTION.json", "RAW_ACCESS_AUDIT_PRE.json")},
        "only_capture_root": str(C2_ROOT), "raw_path": str(raw_path),
        "full_container_hash_recomputed": False,
        "bounded_continuous_byte_span": [raw_start, raw_stop],
        "bounded_slice_sha256": decode["raw_access"]["slice_sha256"],
        "decode": decode,
        "whole_span_gate_failure_preserved": {
            "failure": "maximum_clean_anchor_gap", "threshold_seconds": 60.0,
            "evidence_path": "c2_run_attempt2_stdout.log", "threshold_relaxed": False,
        },
        "local_clock_gates": local_gates,
        "local_common_clock_models": local_models_json,
        "piecewise_continuity_and_covariance_proof": piecewise_audit,
        "residual_row_count": residual_count,
        "non_c2_dataset_accessed": False, "hxx_payload_opened": False,
        "uwb_spatial_fields_decoded": [], "range_values_consumed": False,
        "episode_metadata": selected,
    }
    access["gap_consequence_correction"] = {
        "selected_factor_mask_sha256": array_hash(np.asarray(selected_factor_mask, dtype=np.uint8)),
        "affected_factor_rows_mask_sha256": array_hash(np.asarray(affected_factor_rows, dtype=np.uint8)),
        "affected_factor_rows": int(np.count_nonzero(affected_factor_rows)),
        "common_no_update_rows": int(np.count_nonzero(~common_consumable)),
        "common_no_update_mask_sha256": array_hash(np.asarray(~common_consumable, dtype=np.uint8)),
        "fixed_recovery_class_ns": MAX_RECOVERABLE_FACTOR_GAP_NS,
        "long_nonfactor_measurement_rows_consumed": 0,
    }
    write_json(RUN_DIR / "RAW_ACCESS_AUDIT_CORRECTED_001.json", access, immutable=True)
    diagnostics = {
        "schema": "biospur-c2-qmt-open-source-diagnostic-v1",
        "scientific_qualification": False,
        "synthetic_qualification_status": "NOT_RUN_NOT_AUTHORIZED_FOR_THIS_BASELINE",
        "scientific_PASS_forbidden": True, "progressive_PASS_forbidden": True,
        "grid": {"rows": len(grid), "period_ns": GRID_NS, "first_ns": int(grid[0]), "last_ns": int(grid[-1]), "all_selected_factor_rows_recoverable": True},
        "timebase_by_node": time_audit, "clock_gap_masks": clock_gap_audit,
        "piecewise_clock_proof": piecewise_audit,
        "orientation_filter_resets_at_episode_boundaries": 0,
        "heading_gauge_resets_at_episode_boundaries": 0,
        "orientation_state_audit": orientation_audit,
        "sensor_to_segment_extrinsic_audit": extrinsic_audit,
        "input_contract_path": "C2_TO_QMT_INPUT_CONTRACT.json",
        "input_contract_sha256": sha256_file(RUN_DIR / "C2_TO_QMT_INPUT_CONTRACT.json"),
        "gap_consequence": {
            "selected_factor_rows": int(np.count_nonzero(selected_factor_mask)),
            "affected_factor_rows": int(np.count_nonzero(affected_factor_rows)),
            "affected_factor_rows_excluded_from_hinge_axis_selection": True,
            "common_no_update_rows": int(np.count_nonzero(~common_consumable)),
            "long_nonfactor_samples_fabricated": 0,
        },
        "units_and_still_statistics": unit_audit,
        "hinges": hinge_report,
        "heading_correction": {
            "status": "NOT_RUN_UPSTREAM_GAP_MASK_INCOMPATIBILITY",
            "delta_filt_or_quat2Corr_produced": False,
            "qmt_heading_ratings_available": False,
            "source_requires_dense_constant_time": True,
            "supported_gap_mask_found": False,
        },
        "trajectory_archive": {
            "path": trajectory_path.name,
            "sha256": sha256_file(trajectory_path),
            "classification": "UNHEADING_CORRECTED_DIAGNOSTIC",
        },
        "trajectory_consumers": ["DIRECT_FIXED_SURFACE_CHORD_FK_VIEWER"],
        "non_hinge_incompatibilities": {
            "pelvis_torso_shoulders_hips": "QMT upstream general constraints require exact joint axes/ROM or alignment assumptions unavailable under qualitative wear authority",
            "remaining_heading_gauges": 9,
            "fabricated_rom_or_manual_sign_used": False,
        },
        "geometry": {
            "role": "DIAGNOSTIC_SURFACE_CHORD_PROXY_NOT_INTERNAL_JOINT_CENTER_TRUTH",
            "lengths_m": lengths, "hip_spacing_invented": False, "shoulder_spacing_invented": False,
        },
        "images": image_rows,
        "verdict": "DIAGNOSTIC_BASELINE",
        "scientific_visual_classification": "UNHEADING_CORRECTED_DIAGNOSTIC",
    }
    diagnostic_path = RUN_DIR / "BASELINE_DIAGNOSTICS_UNHEADING_CORRECTED_001.json"
    write_json(diagnostic_path, diagnostics, immutable=True)
    print(json.dumps({"diagnostics": str(diagnostic_path), "images": [row["path"] for row in image_rows]}, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("seal", "amend", "amend2", "amend3", "amend4", "amend5", "amend6", "amend7", "amend8", "amend9", "amend10", "run"))
    args = parser.parse_args()
    if args.command == "seal":
        return seal()
    if args.command == "amend":
        return amend()
    if args.command == "amend2":
        return amend2()
    if args.command == "amend3":
        return amend3()
    if args.command == "amend4":
        return amend4()
    if args.command == "amend5":
        return amend5()
    if args.command == "amend6":
        return amend6()
    if args.command == "amend7":
        return amend7()
    if args.command == "amend8":
        return amend8()
    if args.command == "amend9":
        return amend9()
    if args.command == "amend10":
        return amend10()
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
