from __future__ import annotations

import ast
import json
from pathlib import Path
import math
import time

import numpy as np
import pytest
import qmt

from biospur_fusion.c2_3b_multi_action_registration.contracts import (
    APPROVED_GATE_SHA256,
    PROFILE_IDS,
    PROFILE_SIGMA_RAD,
    STATE_GATE_SHA256,
    load_approved_contract,
)
from biospur_fusion.c2_3b_multi_action_registration.synthetic_axis_stage import (
    DEPENDENT_COORDINATES,
    DT,
    NODE_ORDER,
    ROOT_TRANSLATIONS,
    UNIQUE_AXIS_VARIANTS,
    OfficialSyntheticState,
    _all_axis_variants_eligible,
    _angular_velocity,
    _cache_dependency_key,
    _canonical_plane,
    _checkpoint_seed_parameters,
    _edge_specs,
    _load,
    _state_frame_checkpoint,
    _substream_seed,
    _truth_line_gate,
    _verify_cache_dependency,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/biospur_fusion/c2_3b_multi_action_registration"


def test_approved_gate_is_content_verified() -> None:
    contract = load_approved_contract(ROOT)
    assert contract["gate_sha256"] == APPROVED_GATE_SHA256
    assert contract["object_count"] == 32
    assert contract["state_gate_sha256"] == STATE_GATE_SHA256
    assert contract["state_object_count"] == 19
    assert tuple(row["id"] for row in contract["static"]["profiles"]) == PROFILE_IDS
    assert tuple(row["sigma_wear_rad"] for row in contract["static"]["profiles"]) == PROFILE_SIGMA_RAD
    assert contract["synthetic"]["run_matrix"]["totals"] == {
        "logical_runs": 13992,
        "opensense_calls": 648,
    }


def test_parameter_substreams_are_path_owned() -> None:
    left = _substream_seed(101, "sensor/wear_cone_cos_beta")
    assert left == _substream_seed(101, "sensor/wear_cone_cos_beta")
    assert left != _substream_seed(101, "sensor/wear_cone_azimuth")
    assert left != _substream_seed(211, "sensor/wear_cone_cos_beta")


def test_canonical_plane_is_proper_and_deterministic() -> None:
    for vector in ([1, 0, 0], [0, 1, 0], [0, 0, 1], [0.2, -0.7, 0.4]):
        unit = np.asarray(vector, dtype=float); unit /= np.linalg.norm(unit)
        first, second = _canonical_plane(unit)
        frame = np.column_stack((first, second, unit))
        assert np.linalg.norm(frame.T @ frame - np.eye(3)) <= 1e-12
        assert abs(np.linalg.det(frame) - 1) <= 1e-12


def test_source_has_no_forbidden_optimizer_or_old_solver_import() -> None:
    forbidden_modules = {
        "scipy.optimize",
        "biospur_fusion.c2_3b_imu_ik",
        "biospur_fusion.imu_multi_action_v1",
        "biospur_fusion.imu_multi_action_s2",
        "biospur_fusion.imu_multi_action_revision_d",
    }
    for path in SOURCE.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(any(name == blocked or name.startswith(blocked + ".") for blocked in forbidden_modules) for name in imported)
        text = path.read_text(encoding="utf-8")
        assert "least_squares(" not in text
        assert "scipy.optimize" not in text


def test_node_order_matches_approved_identity() -> None:
    contract = load_approved_contract(ROOT)
    expected = tuple(segment for _, segment in contract["donning"]["identity_map_ordered"])
    assert NODE_ORDER == expected


def test_central_log_angular_velocity_including_endpoints() -> None:
    rotations = np.asarray([
        np.eye(3),
        qmt.quatToRotMat(qmt.quatFromAngleAxis(0.08, [1, 0, 0])),
        qmt.quatToRotMat(qmt.qmult(
            qmt.quatFromAngleAxis(0.08, [1, 0, 0]),
            qmt.quatFromAngleAxis(-0.13, [0, 1, 0]),
        )),
        qmt.quatToRotMat(qmt.qmult(
            qmt.quatFromAngleAxis(0.11, [0, 0, 1]),
            qmt.quatFromAngleAxis(-0.13, [0, 1, 0]),
        )),
    ], dtype=np.float64)
    observed = _angular_velocity(rotations)
    expected = np.empty_like(observed)
    expected[0] = qmt.quatToRotVec(qmt.quatFromRotMat(rotations[0].T @ rotations[1])) / DT
    expected[-1] = qmt.quatToRotVec(qmt.quatFromRotMat(rotations[-2].T @ rotations[-1])) / DT
    for row in range(1, len(rotations) - 1):
        expected[row] = qmt.quatToRotVec(
            qmt.quatFromRotMat(rotations[row - 1].T @ rotations[row + 1])
        ) / (2 * DT)
    assert np.max(np.abs(observed - expected)) <= 1e-12


def test_truth_line_gate_rejects_stable_but_wrong_attempt_002_witness() -> None:
    assert not _truth_line_gate([1.5386609576, 1.5552061087], [])
    passing_blocks = [{
        "parent_truth_line_error_rad": math.radians(4.0),
        "child_truth_line_error_rad": math.radians(3.0),
    }]
    assert _truth_line_gate(
        [math.radians(2.0), math.radians(4.9)], passing_blocks,
    )
    passing_blocks[0]["child_truth_line_error_rad"] = math.radians(5.1)
    assert not _truth_line_gate([0.0, 0.0], passing_blocks)


def test_all_three_axis_variants_are_mandatory() -> None:
    rows = {
        variant: {"all_edges_eligible": True, "saturated": False}
        for variant in UNIQUE_AXIS_VARIANTS
    }
    assert _all_axis_variants_eligible(rows)
    rows["POS04_HIGH_OOP_EXACT_ROLL"]["all_edges_eligible"] = False
    assert not _all_axis_variants_eligible(rows)
    with pytest.raises(ValueError, match="variant order"):
        _all_axis_variants_eligible(dict(reversed(tuple(rows.items()))))


def test_cache_dependency_mutation_fails_closed() -> None:
    payload = {
        "r4": APPROVED_GATE_SHA256,
        "r5": STATE_GATE_SHA256,
        "seed": 101,
        "variant": "BASE",
    }
    expected = _cache_dependency_key(payload)
    _verify_cache_dependency(payload, expected)
    changed = dict(payload, seed=211)
    with pytest.raises(ValueError, match="stale or mismatched"):
        _verify_cache_dependency(changed, expected)


def test_official_state_owner_uses_25_references_and_preserves_couplers() -> None:
    import opensim as osim

    contract = load_approved_contract(ROOT)
    r2 = ROOT / "logs/c2_3b_multi_action_registration_precode_revision_20260902_180947"
    synthetic = json.loads(
        (r2 / "SYNTHETIC_FIXTURE_SPEC.json").read_text(encoding="utf-8")
    )
    osim.Logger.setLevelString("error")
    model = osim.Model(str(contract["model_path"]))
    owner = OfficialSyntheticState(
        osim, model, tuple(synthetic["model_truth"]["enabled_coordinates"]),
    )
    assert owner.reference_order[-3:] == ROOT_TRANSLATIONS
    assert len(owner.reference_order) == 25
    assert not set(owner.reference_order).intersection(DEPENDENT_COORDINATES)
    result = owner.apply({name: owner.defaults[name] for name in owner.reference_order})
    assert result["pass"]
    assert set(result["coupling_errors"]) == {
        "patellofemoral_knee_angle_r_con",
        "patellofemoral_knee_angle_l_con",
    }


def test_seed101_state_frame_checkpoint_fails_only_fixed_line_applicability() -> None:
    import opensim as osim

    contract = load_approved_contract(ROOT)
    r2 = ROOT / "logs/c2_3b_multi_action_registration_precode_revision_20260902_180947"
    synthetic = _load(r2 / "SYNTHETIC_FIXTURE_SPEC.json")
    static = _load(r2 / "STATIC_REGISTRATION_AND_PHYSICAL_GATES.json")
    mechanism = _load(r2 / "MECHANISM_AND_API_CONTRACT.json")
    edges = _edge_specs(mechanism, static)
    edge_contracts = {
        row["edge"]: row for row in contract["roundtrip"]["edges_in_order"]
    }
    osim.Logger.setLevelString("error")
    model = osim.Model(str(contract["model_path"]))
    owner = OfficialSyntheticState(
        osim, model, tuple(synthetic["model_truth"]["enabled_coordinates"]),
    )
    result = _state_frame_checkpoint(
        owner,
        edges,
        edge_contracts,
        _checkpoint_seed_parameters(101, contract["donning"]),
        synthetic,
        static,
        time.monotonic() + 120.0,
    )
    assert result["dimensions"]["assembly_calls"] == 153
    assert result["dimensions"]["qmt_olsson_calls"] == 0
    assert result["checks"]["state_rows"]
    assert result["checks"]["frame_roundtrip"]
    assert result["checks"]["all_relative_motion"]
    assert not result["checks"]["all_hinge_relations"]
    passing = [row["edge"] for row in result["edges"] if row["hinge_relation_pass"]]
    assert passing == ["elbow_left", "elbow_right"]
    assert result["edges"][2]["coordinate_le_f8_sha256"] == (
        "74b7fe22ce3ea9deabc741d80b1cf25d31554a8e5b41b2b2b5c0c3e476c601e9"
    )
