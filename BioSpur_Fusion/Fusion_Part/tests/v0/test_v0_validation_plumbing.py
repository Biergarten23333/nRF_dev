from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.v0.contracts import NODES, load_config, load_release_mode, sha256_file
from biospur_fusion.v0.data import load_authorized_action_imu_only
from biospur_fusion.v0.validation import (
    PREDECLARATION_SCHEMA, _execute_variants, create_candidate_lock_manifest,
    validate_predeclaration, verify_candidate_lock,
)
from biospur_fusion.v0.viewer import write_viewer


ROOT = Path(__file__).resolve().parents[2]
PROFILE = ROOT / "logs/biospur_fusion_v0_golden_20260826T173258Z/V0_SESSION_PROFILE.json"
IMU_DTYPE = np.dtype([
    ("boot_epoch", "<u2"), ("sequence", "<u2"),
    ("global_time_ns", "<i8"), ("acc_raw", "<i2", (3,)),
    ("gyro_raw", "<i2", (3,)), ("status", "u1"),
])


def _rows(count: int = 1200) -> np.ndarray:
    rows = np.zeros(count, dtype=IMU_DTYPE)
    rows["boot_epoch"] = 1
    rows["sequence"] = np.arange(count, dtype=np.uint16)
    rows["global_time_ns"] = 10_000_000_000 + np.arange(count, dtype=np.int64) * 5_000_000
    rows["acc_raw"][:, 2] = 2048
    rows["gyro_raw"][:, 2] = round(5.0 * 16.384)
    rows["status"] = 1
    return rows


def _ledger(path: Path) -> Path:
    values = {f"imu_{node}": _rows() for node in NODES}
    values["range_mm_hostile_spatial_member"] = np.full((12, 8), np.nan)
    np.savez(path, **values)
    return path


def test_authorized_action_loader_opens_exact_ten_imu_members(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "fixture.npz")
    rows, audit = load_authorized_action_imu_only(
        ledger, action_label="ordinary_fixture", start_ns=10_100_000_000,
        stop_ns=15_000_000_000, expected_ledger_sha256=sha256_file(ledger),
    )
    assert set(rows) == set(NODES)
    assert audit["imu_only_selection_pass"] is True
    assert audit["spatial_members_opened"] == []
    assert audit["opened_members"] == sorted(f"imu_{node}.npy" for node in NODES)
    assert "range_mm_hostile_spatial_member.npy" not in audit["opened_members"]


def test_authorized_action_loader_rejects_heldout_before_mapping(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "fixture.npz")
    with pytest.raises(ValueError, match="held-out"):
        load_authorized_action_imu_only(
            ledger, action_label="Golf swing", start_ns=10_100_000_000,
            stop_ns=15_000_000_000, expected_ledger_sha256=sha256_file(ledger),
        )


def test_predeclaration_role_and_metadata_hash_guard(tmp_path: Path) -> None:
    role = tmp_path / "roles.json"; role.write_text('{"role":"ordinary"}\n', encoding="utf-8")
    payload = {
        "schema": PREDECLARATION_SCHEMA,
        "capture_identifier": "Capture2", "action_identifier": "walk",
        "attempt_number": 1, "start_global_time_ns": 1, "stop_global_time_ns_exclusive": 2,
        "ledger": "not-opened-by-this-test.npz", "ledger_sha256": "0" * 64,
        "authoritative_role_source": str(role),
        "authoritative_role_source_sha256": sha256_file(role),
        "role": "ORDINARY_DEVELOPMENT_VALIDATION",
        "ordinary_reason": "authoritative metadata role", "not_held_out_reason": "not Golf or Boxing",
        "expected_gross_motion_semantics": ["left and right walking-like limb motion"],
        "candidate_manifest": "manifest.json", "candidate_manifest_sha256": "1" * 64,
        "profile": str(PROFILE), "profile_sha256": sha256_file(PROFILE),
        "selected_from_metadata_before_payload_access": True,
        "complete_ten_node_coverage_declared": True,
    }
    assert validate_predeclaration(ROOT, payload)["pass"] is True
    raw_payload = payload | {
        "sealed_container_sha256": "2" * 64,
        "raw_input": {
            "start_byte_inclusive": 100, "stop_byte_exclusive": 200,
            "start_host_monotonic_ns": 10, "stop_host_monotonic_ns_exclusive": 20,
            "slice_sha256": "3" * 64,
        },
        "common_clock_timing_sources": {
            "fusion_timing_log": "timing.log", "listener_directory": "listeners",
            "capture_identity_authority": "readiness.json",
            "capture_identity_authority_sha256": "4" * 64,
        },
        "forbidden_golf_boxing_raw_byte_ranges": [
            {"start_byte_inclusive": 1000, "stop_byte_exclusive": 2000},
        ],
        "forbidden_golf_boxing_timing_intervals_ns": [
            {"start_host_monotonic_ns": 1000, "stop_host_monotonic_ns_exclusive": 2000},
        ],
        "authoritative_action_event_source": "events.jsonl",
        "authoritative_action_event_source_sha256": "5" * 64,
        "HISTORICAL_GOLF_BOXING_CONTAINER_BYTES_TOUCHED": "YES",
        "CURRENT_GOAL_GOLF_BOXING_BYTES_TOUCHED": "NO",
        "GOLF_BOXING_MEASUREMENTS_DECODED": "NO",
        "GOLF_BOXING_USED_FOR_TUNING": "NO",
        "GOLF_BOXING_USED_FOR_SCORING": "NO",
        "SELECTED_ACTION_PREVIOUSLY_DECODED": "NO",
        "SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED": "NO",
        "SELECTED_ACTION_PREVIOUSLY_USED_FOR_TUNING": "NO",
    }
    assert validate_predeclaration(ROOT, raw_payload)["pass"] is True
    raw_payload["SELECTED_ACTION_PREVIOUSLY_DECODED"] = "YES"
    with pytest.raises(ValueError, match="PREVIOUSLY_DECODED"):
        validate_predeclaration(ROOT, raw_payload)
    raw_payload["execution_mode"] = "RELOCKED_ACCESS_EVIDENCE_RERUN"
    raw_payload["SELECTED_ACTION_PREVIOUSLY_RECONSTRUCTED"] = "YES"
    raw_payload["selection_predeclaration_sha256"] = "6" * 64
    raw_payload["reconstruction_parameters_changed_after_original_execution"] = False
    assert validate_predeclaration(ROOT, raw_payload)["pass"] is True
    payload["role"] = "CALIBRATION"
    with pytest.raises(ValueError, match="role"):
        validate_predeclaration(ROOT, payload)


def test_conservative_variants_share_frontend_and_frozen_profile() -> None:
    profile = json.loads(PROFILE.read_text(encoding="utf-8"))
    config = load_config(ROOT / "config/biospur_fusion_v0/config.json")
    variants, audits = _execute_variants(ROOT, {node: _rows() for node in NODES}, profile, config)
    assert {
        "qmt_off", "qmt_off_no_shared_ik", "always_on_qmt",
        "always_on_qmt_no_shared_ik", "full", "no_shared_ik",
        "no_relative_heading",
    } == set(variants)
    assert audits["initialization_policy"] == "IDENTICAL_ONE_CAUSAL_FRONTEND_TIMELINE_SHARED_BY_ALL_VARIANTS"
    assert audits["profile_writeback"] is False
    assert audits["no_shared_ik"]["shared_ik_feedback_executed"] is False
    assert audits["no_relative_heading"]["relative_heading_executed"] is False
    assert audits["qmt_off"]["relative_heading"]["qmt_bypass_reason"] == "PRODUCT_MODE_QMT_OFF"
    assert audits["qmt_off"]["relative_heading"]["missing_heading_uncertainty_term_preserved"] is True
    assert audits["qmt_off"]["relative_heading"]["attitude_and_heading_confidence_decoupled"] is True
    assert audits["qmt_off_no_shared_ik"]["shared_ik"]["shared_ik_feedback_executed"] is False
    assert np.array_equal(
        variants["qmt_off"]["segment_rotation"],
        variants["no_relative_heading"]["segment_rotation"],
    )
    assert np.all(variants["qmt_off"]["heading_observation_confidence"] == 0.0)
    assert np.all(variants["qmt_off_no_shared_ik"]["heading_observation_confidence"] == 0.0)
    assert float(np.min(variants["qmt_off"]["segment_sigma_rad"])) > 0.0
    ik_audit = audits["qmt_off"]["shared_ik"]
    assert ik_audit["attitude_observation_confidence_max"] == 1.0
    assert ik_audit["heading_evidence_confidence_max"] == 0.0
    assert ik_audit["observation_weight_median"] > 0.0
    for arrays in variants.values():
        assert arrays["segment_rotation"].shape[1:] == (10, 3, 3)
        assert np.isfinite(arrays["segment_rotation"]).all()
        assert np.isfinite(arrays["segment_position"]).all()


def test_release_mode_selects_qmt_off_without_mutating_scientific_config() -> None:
    release = load_release_mode(ROOT / "config/biospur_fusion_v0/release_mode.json")
    assert release["selected_v0_mode"] == "QMT_OFF"
    assert release["selected_variant"] == "qmt_off"
    assert release["confidence_gated_qmt"] == "NOT_JUSTIFIED"
    assert release["heading_evidence_confidence_contract"] == "ZERO_NO_INDEPENDENT_HEADING_OBSERVATION"
    assert release["missing_heading_uncertainty_term"] == "PRESERVED_FOR_EVERY_SEGMENT"
    assert release["attitude_observation_weight_contract"] == "ONE_WHEN_NONDEGRADED_ZERO_WHEN_DEGRADED"
    assert release["attitude_and_heading_confidence"] == "DECOUPLED"
    assert release["historical_profile_sha256"] == sha256_file(PROFILE)


def test_qmt_off_viewer_exposes_motion_health_bypass_and_camera_controls(tmp_path: Path) -> None:
    frames = 3
    segments = (
        "pelvis", "torso", "upper_arm_left", "forearm_left", "upper_arm_right",
        "forearm_right", "thigh_left", "shank_left", "thigh_right", "shank_right",
    )
    positions = np.zeros((frames, len(segments), 3))
    rotations = np.repeat(np.eye(3)[None, None], frames * len(segments), axis=0).reshape(
        frames, len(segments), 3, 3,
    )
    path = tmp_path / "viewer.html"
    manifest = write_viewer(
        path,
        time_ns=np.arange(frames, dtype=np.int64) * 100_000_000,
        window=np.full(frames, "synthetic", dtype="U16"),
        boundary=np.asarray(["WINDOW_START", "CONTINUOUS", "CONTINUOUS"]),
        segment_names=segments,
        segment_position=positions,
        segment_rotation=rotations,
        segment_confidence=np.full((frames, len(segments)), 0.5),
        joint_rotvec=np.zeros((frames, 9, 3)),
        segment_sigma_rad=np.full((frames, len(segments)), 1.0),
        node_by_segment=tuple(f"NODE{i}" for i in range(len(segments))),
        qmt_mode="QMT_OFF",
    )
    html = path.read_text(encoding="utf-8")
    assert manifest["qmt_correction_or_bypass_state_displayed"] is True
    assert manifest["per_node_uncertainty_displayed"] is True
    assert manifest["camera_presets"] == ["front", "side", "top"]
    for marker in ("BYPASS_QMT_OFF", "qmt state", "Max uncertainty", "Front", "Side", "Top"):
        assert marker in html


def test_candidate_lock_is_content_addressed_and_verifiable(tmp_path: Path) -> None:
    path = tmp_path / "CANDIDATE_LOCK_MANIFEST.json"
    manifest = create_candidate_lock_manifest(ROOT, PROFILE, path)
    result = verify_candidate_lock(ROOT, path, sha256_file(path))
    assert manifest["locked"] is True
    assert manifest["final_release_freeze"] is False
    assert result["pass"] is True
    assert result["covered_files"] == len(manifest["files"])
