from __future__ import annotations

import numpy as np
import pytest
from vqf import VQF

from biospur_fusion.v0.c2_progressive.orientation import (
    ContinuousVQFState,
    VQFTiltDiagnosticProvenance,
)
from biospur_fusion.v0.c2_progressive.pipeline_runtime import (
    _owner_authenticated_orientation_replay_arrays,
    _runtime_vqf_tilt_authority,
)
from biospur_fusion.v0.c2_progressive.range_reader import DecodedAction, IMU_DTYPE


class _Guard:
    capture_id = "C2"

    def bind_vqf_instance(self, node, instance):
        pass

    def begin_episode(self, chronological_index, action):
        pass


def _rows(start: int, count: int) -> np.ndarray:
    rows = np.zeros(count, dtype=IMU_DTYPE)
    rows["derived_boot_epoch"] = 7
    rows["imu_sample_sequence"] = np.arange(start, start + count, dtype=np.uint16)
    rows["node_timer_us"] = 1_000_000 + np.arange(start, start + count) * 5_000
    rows["acc_raw"][:, 2] = 2048
    rows["raw_start_offset"] = 10_000 + np.arange(start, start + count) * 32
    rows["raw_end_offset"] = rows["raw_start_offset"] + 32
    rows["raw_sample_index"] = np.arange(count, dtype=np.uint8) % 20
    rows["decode_acceptance_status"] = 1
    return rows


def _state(*, issued: bool = True, source_owned: bool = True) -> ContinuousVQFState:
    initial = {"nodes": {"node": {
        "gyro_bias_rad_s": [0.0, 0.0, 0.0],
        "gyro_bias_covariance_rad2_s2": (np.eye(3) * 1e-8).tolist(),
        "gyro_observation_covariance_rad2_s2": (np.eye(3) * 1e-7).tolist(),
        "accelerometer_norm_mps2": 9.80665,
        "accelerometer_observation_covariance_m2_s4": (np.eye(3) * 1e-4).tolist(),
    }}}
    kwargs = {}
    if issued:
        settings = {"execution_contract": {
            "initial_stochastic_state_relative_path": "initial.json",
        }}
        sources = {"initial.json": "6" * 64} if source_owned else {"other": "7" * 64}
        authority, capability = _runtime_vqf_tilt_authority(
            seal_authority={"seal_sha256": "5" * 64, "qualified_source_hashes": sources},
            settings=settings,
            initial_semantic_sha256="3" * 64,
            settings_semantic_sha256="4" * 64,
        )
        kwargs = {
            "tilt_diagnostic_runtime_authority": authority,
            "_tilt_provenance_capability": capability,
        }
    return ContinuousVQFState(
        initial, execution_guard=_Guard(), sample_period_s=0.005,
        unknown_boot_orientation_sigma_rad=1.0,
        unknown_unusable_episode_orientation_sigma_rad=0.5,
        **kwargs,
    )


def _action(index: int, rows: np.ndarray, *, source: str) -> DecodedAction:
    return DecodedAction(
        action=f"label-{index}", chronological_index=index, interval=(100, 200),
        rows_by_node={"node": rows},
        access_audit={"sealed_slice_sha256": source, "exact_interval": [100, 200]},
        decode_audit={"decoder": "synthetic-production-shape"},
    )


def test_persistent_vqf_diagnostics_are_chunk_and_prefix_invariant():
    source = "a" * 64
    one = _state().process(_action(0, _rows(0, 400), source=source))
    chunked_owner = _state()
    first = chunked_owner.process(_action(0, _rows(0, 180), source=source))
    second = chunked_owner.process(_action(1, _rows(180, 220), source=source))

    for field in (
        "quat_world_sensor_wxyz_by_node",
        "vqf_residual_bias_sigma_rad_s_by_node",
        "vqf_rest_detected_by_node",
        "vqf_relative_rest_deviation_by_node",
        "acceleration_norm_residual_mps2_by_node",
        "world_tilt_innovation_rad_by_node",
    ):
        whole = getattr(one, field)["node"]
        split = np.concatenate((getattr(first, field)["node"], getattr(second, field)["node"]))
        assert np.array_equal(whole, split)
        assert np.array_equal(whole[:180], getattr(first, field)["node"])
    assert chunked_owner.audit()["vqf_instances_per_node"] == 1
    assert chunked_owner.audit()["episode_reset_count"] == 0

    reference = VQF(0.005, magDistRejectionEnabled=False).updateBatch(
        np.zeros((400, 3)), np.tile([0.0, 0.0, 9.80665], (400, 1)),
    )["quat6D"]
    assert np.array_equal(one.quat_world_sensor_wxyz_by_node["node"], reference)


def test_replay_export_persists_exact_diagnostic_and_row_identity():
    oriented = _state().process(_action(0, _rows(0, 400), source="b" * 64))
    arrays = _owner_authenticated_orientation_replay_arrays(oriented)
    prefix = "orientation/00/node"
    assert np.array_equal(arrays[f"{prefix}/imu_sample_sequence"], np.arange(400))
    assert np.all(arrays[f"{prefix}/raw_end_offset"] > arrays[f"{prefix}/raw_start_offset"])
    assert arrays[f"{prefix}/vqf_relative_rest_deviation"].shape == (400, 2)
    assert arrays[f"{prefix}/world_tilt_innovation_rad"].shape == (400,)
    diagnostic = oriented.audit["nodes"]["node"]["vqf_tilt_diagnostic"]
    assert diagnostic["production_ready"] is False
    assert diagnostic["common_clock_binding"].startswith("REQUIRED_DOWNSTREAM")
    assert len(diagnostic["source_binding_digest"]) == 64
    provenance = oriented.vqf_tilt_diagnostic_provenance
    assert provenance is not None and provenance.product_ready is False
    assert provenance.initial_stochastic_state_source_status == "SEAL_OWNED"
    with pytest.raises(ValueError):
        oriented.world_tilt_innovation_rad_by_node["node"][0] = 1.0


def test_source_binding_digest_changes_with_inherited_access_owner():
    left = _state().process(_action(0, _rows(0, 30), source="c" * 64))
    right = _state().process(_action(0, _rows(0, 30), source="d" * 64))
    left_digest = left.audit["nodes"]["node"]["vqf_tilt_diagnostic"]["source_binding_digest"]
    right_digest = right.audit["nodes"]["node"]["vqf_tilt_diagnostic"]["source_binding_digest"]
    assert left_digest != right_digest


def test_public_forgery_and_missing_runtime_capability_fail_closed():
    with pytest.raises(ValueError, match="invalid VQF tilt-diagnostic provenance"):
        VQFTiltDiagnosticProvenance(
            prefit_seal_sha256="1" * 64,
            qualified_source_closure_digest="2" * 64,
            initial_stochastic_state_semantic_sha256="3" * 64,
            initial_stochastic_state_source_status="SEAL_OWNED",
            initial_stochastic_state_source_sha256="4" * 64,
            settings_semantic_sha256="5" * 64,
            timer_domain="B306_TIMER2_US_NODE_LOCAL",
            vqf_version="2.0.1",
            vqf_parameters_digest="6" * 64,
        )
    authority, _ = _runtime_vqf_tilt_authority(
        seal_authority={"seal_sha256": "1" * 64, "qualified_source_hashes": {}},
        settings={"execution_contract": {"initial_stochastic_state_relative_path": "missing"}},
        initial_semantic_sha256="2" * 64,
        settings_semantic_sha256="3" * 64,
    )
    with pytest.raises(ValueError, match="validated runtime issuance"):
        ContinuousVQFState(
            {"nodes": {"node": {"gyro_bias_rad_s": [0, 0, 0]}}},
            execution_guard=_Guard(), unknown_boot_orientation_sigma_rad=1.0,
            unknown_unusable_episode_orientation_sigma_rad=0.5,
            tilt_diagnostic_runtime_authority=authority,
        )
    missing = _state(source_owned=False).process(_action(0, _rows(0, 30), source="e" * 64))
    assert missing.vqf_tilt_diagnostic_provenance.initial_stochastic_state_source_status == (
        "MISSING_FROM_QUALIFIED_SOURCE_CLOSURE"
    )
    unissued = _state(issued=False).process(_action(0, _rows(0, 30), source="f" * 64))
    with pytest.raises(RuntimeError, match="runtime-issued provenance"):
        _owner_authenticated_orientation_replay_arrays(unissued)
