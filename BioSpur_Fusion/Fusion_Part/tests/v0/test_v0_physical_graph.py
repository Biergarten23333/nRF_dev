from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from biospur_fusion.v0.physical_graph import (
    LENGTH_INDICES,
    STATE_DIMENSION,
    PhysicalGraphSpec,
    bounds,
    decode_state,
    real_subject_spec,
    structural_audit,
)


def _legal_state() -> np.ndarray:
    state = np.zeros(STATE_DIMENSION)
    spec = real_subject_spec()
    for segment, index in LENGTH_INDICES.items():
        state[index] = spec.segment_lengths_m[segment]
    return state


def test_two_joint_segments_have_one_structural_length_and_no_edge_lever_state() -> None:
    spec = real_subject_spec()
    decoded = decode_state(_legal_state(), spec)
    for segment in ("upper_arm_left", "upper_arm_right", "thigh_left", "thigh_right"):
        row = decoded["segment_geometry"][segment]
        assert np.isclose(
            np.linalg.norm(row["distal"] - row["proximal"]),
            _legal_state()[LENGTH_INDICES[segment]],
        )
    audit = structural_audit(_legal_state(), spec)
    assert audit["independent_edge_lever_state_dimension"] == 0
    assert audit["anatomical_segment_length_state_dimension"] == 4
    assert audit["lower_segment_full_length_state_dimension"] == 0
    assert audit["edge_connection_set_exact"] is True
    assert audit["pass"] is True


def test_collapsed_segment_is_rejected_by_state_bounds_and_bad_prior_gate() -> None:
    source = real_subject_spec()
    lengths = dict(source.segment_lengths_m)
    lengths["thigh_left"] = 0.0
    mutant = PhysicalGraphSpec(
        segment_lengths_m=lengths,
        segment_length_sigma_m=source.segment_length_sigma_m,
        segment_length_source=source.segment_length_source,
    )
    with pytest.raises(ValueError, match="non-collapse"):
        mutant.validate()
    state = _legal_state()
    state[LENGTH_INDICES["thigh_left"]] = 0.0
    low, high = bounds(source)
    assert not np.all((state >= low) & (state <= high))


def test_bilateral_lengths_are_independent_facts_not_an_equality_constraint() -> None:
    source = real_subject_spec()
    lengths = dict(source.segment_lengths_m)
    lengths["upper_arm_left"] = 0.27
    lengths["upper_arm_right"] = 0.29
    asymmetric = PhysicalGraphSpec(
        segment_lengths_m=lengths,
        segment_length_sigma_m=source.segment_length_sigma_m,
        segment_length_source=source.segment_length_source,
    )
    assert asymmetric.validate()["bilateral_equality_constraint"] is False
    state = _legal_state()
    state[LENGTH_INDICES["upper_arm_left"]] = 0.27
    state[LENGTH_INDICES["upper_arm_right"]] = 0.29
    decoded = decode_state(state, asymmetric)
    assert decoded["segment_geometry"]["upper_arm_left"]["length_m"] == 0.27
    assert decoded["segment_geometry"]["upper_arm_right"]["length_m"] == 0.29


def test_lower_distal_endpoints_are_retained_as_nonmeasurement_proxies() -> None:
    decoded = decode_state(_legal_state(), real_subject_spec())
    for segment in ("forearm_left", "forearm_right", "shank_left", "shank_right"):
        row = decoded["segment_geometry"][segment]
        assert row["length_m"] is None
        assert row["full_length_observable_in_graph"] is False
        assert row["distal_endpoint_evidence_class"] == "C"
        assert np.isfinite(row["distal_proxy"]).all()


def test_pelvis_template_offsets_are_explicit_without_changing_legacy_default() -> None:
    state = _legal_state()
    default = real_subject_spec()
    default_points = decode_state(state, default)["segment_geometry"]["pelvis"]["points"]
    assert np.allclose(default_points["hip_left"], [-0.12, 0.0, -0.06])
    assert np.allclose(default_points["hip_right"], [0.12, 0.0, -0.06])
    assert np.allclose(default_points["pelvis_torso"], [0.0, 0.0, 0.06])

    audited = replace(
        default,
        pelvis_width_m=0.30,
        pelvis_hip_vertical_offset_m=-0.10,
        pelvis_torso_vertical_offset_m=0.08,
    )
    points = decode_state(state, audited)["segment_geometry"]["pelvis"]["points"]
    assert np.allclose(points["hip_left"], [-0.15, 0.0, -0.10])
    assert np.allclose(points["hip_right"], [0.15, 0.0, -0.10])
    assert np.allclose(points["pelvis_torso"], [0.0, 0.0, 0.08])


def test_real_subject_spec_uses_20260828_surface_measurements_as_soft_priors() -> None:
    spec = real_subject_spec()
    assert spec.segment_lengths_m["upper_arm_left"] == 0.3175
    assert spec.segment_lengths_m["upper_arm_right"] == 0.3175
    assert spec.segment_lengths_m["forearm_left"] == 0.255
    assert spec.segment_lengths_m["forearm_right"] == 0.255
    assert spec.segment_lengths_m["thigh_left"] == 0.48
    assert spec.segment_lengths_m["thigh_right"] == 0.48
    assert spec.segment_lengths_m["shank_left"] == 0.43
    assert spec.segment_lengths_m["shank_right"] == 0.43
    assert spec.segment_length_sigma_m["upper_arm_left"] == 0.03
    assert "SURFACE_CHORD" in spec.segment_length_source["upper_arm_left"]
