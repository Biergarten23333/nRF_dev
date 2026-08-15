from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest


ROOT = Path(__file__).resolve().parents[3]
TOOL = ROOT / "Fusion_Part/tools/export_d0b_synthetic_nullspace_evidence.py"
SPEC = importlib.util.spec_from_file_location("d0b_evidence_exporter", TOOL)
assert SPEC and SPEC.loader
exporter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(exporter)


def test_exporter_invokes_supplied_frozen_production_evaluator_once() -> None:
    calls = []

    def fake(*args):
        calls.append(args)
        return {"terminal_outcome": "FAIL_D0B_SYNTHETIC_NULLSPACE"}

    module = SimpleNamespace(
        qualify_d0_synthetic=lambda *_: None,
        generate_synthetic_dataset=lambda *_: None,
        D0SyntheticObjective=lambda *_: None,
        finite_difference_jacobian=lambda *_: None,
    )
    result, captured = exporter.invoke_frozen_production_evaluator(
        module, {"r3d": 1}, {"chain": 2}, {"d0": 3}, evaluator=fake
    )
    assert len(calls) == 1
    assert result["terminal_outcome"] == "FAIL_D0B_SYNTHETIC_NULLSPACE"
    assert captured["jacobians"] == []


def test_semantic_hash_detects_one_entry_change() -> None:
    array = np.arange(12, dtype=np.float64).reshape(3, 4)
    before = exporter.semantic_hash("J_data", array)
    changed = array.copy()
    changed[1, 2] += 1.0
    assert exporter.semantic_hash("J_data", changed) != before


def test_semantic_hash_detects_row_and_column_reordering() -> None:
    array = np.arange(20, dtype=np.float64).reshape(4, 5)
    original = exporter.semantic_hash("J_data", array)
    assert exporter.semantic_hash("J_data", array[[1, 0, 2, 3]]) != original
    assert exporter.semantic_hash("J_data", array[:, [1, 0, 2, 3, 4]]) != original


def _parameter(index: int, name: str) -> dict:
    return {
        "column_index": index,
        "coordinate_name": name,
        "unit": "rad",
        "numerical_coordinate_scale": 1.0,
        "ambient_dimension": 1,
        "intrinsic_dof": 1,
        "publishable": True,
        "nuisance": False,
        "lifecycle": "SUBJECT",
        "frame_convention": "ACTIVE",
        "replay_consumer": "FORWARD_MODEL",
        "gauge_or_convention_status": "NONE_DECLARED",
    }


def test_missing_parameter_unit_or_scale_fails_schema_validation() -> None:
    item = _parameter(0, "x")
    del item["unit"]
    with pytest.raises(ValueError):
        exporter.validate_parameter_inventory([item], 1)
    item = _parameter(0, "x")
    del item["numerical_coordinate_scale"]
    with pytest.raises(ValueError):
        exporter.validate_parameter_inventory([item], 1)


def test_soft_protocol_prior_cannot_be_called_manifold_constraint() -> None:
    row = {
        "row_index": 0,
        "action": "t_pose",
        "factor": "protocol_pose_prior:t_pose:torso",
        "classification": "NONDATA",
        "nondata_class": "MANIFOLD_CONSTRAINT",
    }
    with pytest.raises(ValueError):
        exporter.validate_row_manifest([row])


def test_row_and_column_order_bindings_fail_when_reordered() -> None:
    rows = [
        {"row_index": 0, "action": "a", "factor": "f"},
        {"row_index": 1, "action": "b", "factor": "g"},
    ]
    inventory = [_parameter(0, "x"), _parameter(1, "y")]
    row_sha = hashlib.sha256(exporter.canonical(rows)).hexdigest()
    column_sha = hashlib.sha256(exporter.canonical(["x", "y"])).hexdigest()
    exporter.validate_order_bindings(rows, inventory, row_sha, column_sha)
    with pytest.raises(ValueError):
        exporter.validate_order_bindings(list(reversed(rows)), inventory, row_sha, column_sha)
    with pytest.raises(ValueError):
        exporter.validate_order_bindings(rows, list(reversed(inventory)), row_sha, column_sha)


def test_reload_only_rank_reconstruction_uses_persisted_matrices() -> None:
    data = np.diag([4.0, 1.0, 1e-9])
    arrays = {
        "J_data": data,
        "J_nuisance": data[:, :2],
        "J_eff": data[:, :2],
        "J_full": np.vstack([data, np.diag([0.0, 0.0, 2.0])]),
    }
    assert exporter.reload_rank_summary(arrays, 1e-7) == {
        "data_only_full_rank": 2,
        "data_only_nuisance_rank": 2,
        "data_only_profiled_product_rank": 2,
        "data_plus_prior_rank": 3,
    }


def test_exporter_has_no_forbidden_real_data_imports() -> None:
    tree = ast.parse(TOOL.read_text())
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.append(node.module or "")
    forbidden = ("uwb", "anchor", "operator_measurement", "final_still", "walk", "golf", "boxing")
    assert not any(any(token in name.lower() for token in forbidden) for name in imports)
