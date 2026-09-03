from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pytest

from biospur_fusion.c2_3b_multi_action_registration.joint_type_contract import (
    JOINT_TYPE_GATE_SHA256,
    PREFIT_BOUNDARY,
    load_joint_type_contract,
)
from biospur_fusion.c2_3b_multi_action_registration.joint_type_fixtures import (
    OfficialStateCache,
    _constant_point,
    _qmt_termination,
    run_jtf05,
    run_jtf06,
)
from biospur_fusion.c2_3b_multi_action_registration.joint_type_runtime import RuntimeBound
from biospur_fusion.c2_3b_multi_action_registration.joint_type_runtime import PinnedImtRuntime


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src/biospur_fusion/c2_3b_multi_action_registration"


def _native(step: int, diff: float) -> dict:
    xtraj = np.full((4, 300), np.nan)
    xtraj[:, :step] = 0.0
    updates = np.linspace(1.0, 0.2, step)
    updates[-2] = 0.2 + diff
    updates[-1] = 0.2
    ftraj = np.concatenate((updates, [0.2]))[:, None]
    return {
        "xtraj": xtraj,
        "ftraj": ftraj,
        "f0": np.asarray(1.0),
        "f": np.asarray(0.2),
        "Hessian": np.eye(4),
    }


def test_literal_approved_gate_is_deeply_bound() -> None:
    contract = load_joint_type_contract(ROOT)
    assert contract["gate_sha256"] == JOINT_TYPE_GATE_SHA256
    assert contract["prefit_boundary"] == PREFIT_BOUNDARY
    assert contract["runtime"]["official_call_totals"]["opensim_assembly"] == 2624
    assert contract["runtime"]["disk"]["projected_peak_transient_bytes"] == 1610612736


@pytest.mark.parametrize(
    ("step", "diff", "classification"),
    [
        (17, 1e-6, "TOL_BEFORE_MAX"),
        (299, 2e-5, "MAX_STEPS_ONLY"),
        (299, 1e-6, "MAX_STEPS_AND_TOL_SAME_FINAL_ITERATION"),
    ],
)
def test_qmt_termination_reconstructs_erased_terminal_column(
    step: int, diff: float, classification: str,
) -> None:
    result = _qmt_termination(_native(step, diff))
    assert result["step_count"] == step
    assert result["ftraj_length"] == step + 1
    assert result["reconstructed_termination"] == classification
    assert result["terminal_recompute_delta_report_only"] == 0.0


def test_qmt_termination_fails_on_impossible_nonmax_nontolerance() -> None:
    with pytest.raises(RuntimeBound, match="QMT_TERMINATION_RECONSTRUCTION"):
        _qmt_termination(_native(17, 2e-5))


def test_official_state_cache_keys_preserve_explicit_owner() -> None:
    cache = OfficialStateCache.__new__(OfficialStateCache)
    cache.dependency_hashes = {"model_sha256": "m", "state_lifecycle_sha256": "s"}
    back = cache.key((("lumbar_extension", 0.0), ("lumbar_bending", 0.0), ("lumbar_rotation", 0.0)))
    back_repeat = cache.key((("lumbar_extension", 0.0), ("lumbar_bending", 0.0), ("lumbar_rotation", 0.0)))
    knee = cache.key((("knee_angle_l", 0.0),))
    assert back == back_repeat
    assert back != knee


def test_pinned_runtime_removes_launcher_python_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", "caller-site-packages")
    monkeypatch.setenv("PYTHONHOME", "caller-home")
    monkeypatch.setenv("VIRTUAL_ENV", "caller-venv")
    env = PinnedImtRuntime.clean_environment()
    assert "PYTHONPATH" not in env
    assert "PYTHONHOME" not in env
    assert "VIRTUAL_ENV" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_constant_point_control_and_inconsistent_witness() -> None:
    def snapshot(angle: float, offset: float):
        rz = np.array([
            [np.cos(angle), -np.sin(angle), 0.0],
            [np.sin(angle), np.cos(angle), 0.0],
            [0.0, 0.0, 1.0],
        ])
        half = 0.47 * angle
        ry = np.array([
            [np.cos(half), 0.0, np.sin(half)],
            [0.0, 1.0, 0.0],
            [-np.sin(half), 0.0, np.cos(half)],
        ])
        third = -0.31 * angle
        rx = np.array([
            [1.0, 0.0, 0.0],
            [0.0, np.cos(third), -np.sin(third)],
            [0.0, np.sin(third), np.cos(third)],
        ])
        rotation = rz @ ry @ rx
        return type("Snapshot", (), {
            "transforms": {
                "parent": (np.eye(3), np.zeros(3)),
                "child": (rotation, np.array([offset, 0.0, 0.0])),
            }
        })()

    control = _constant_point([snapshot(value, 0.0) for value in np.linspace(-1.0, 1.0, 9)], "parent", "child")
    assert control["maximum_residual_m"] <= control["numerical_tolerance_m"]
    inconsistent = _constant_point(
        [snapshot(value, 0.01 * value**2) for value in np.linspace(0.0, 2.0, 13)],
        "parent", "child",
    )
    assert inconsistent["maximum_residual_m"] > inconsistent["numerical_tolerance_m"]


def test_analytic_seel_negatives_fail_before_optimizer() -> None:
    stationary = run_jtf05()
    assert stationary["pass"]
    assert stationary["first_gate"] == "SEEL_INSUFFICIENT_EXCITATION"
    assert stationary["imt_calls"] == stationary["scipy_calls"] == 0
    identical = run_jtf06()
    assert identical["pass"]
    assert identical["first_gate"] == "SEEL_IDENTICAL_PAIR_COMMON_LEVER_NULLSPACE"
    assert identical["analytic_rank"] <= 3
    assert all(row["rank"] <= 3 for row in identical["central_difference_report_only"])
    assert identical["imt_calls"] == identical["scipy_calls"] == 0


def test_new_source_respects_execution_firewalls() -> None:
    forbidden_imports = {
        "scipy.optimize",
        "biospur_fusion.c2_3b_imu_ik",
        "biospur_fusion.imu_multi_action_v1",
        "biospur_fusion.imu_multi_action_revision_d",
    }
    for path in SOURCE.glob("joint_type_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not any(
            name == blocked or name.startswith(blocked + ".")
            for name in imported for blocked in forbidden_imports
        )
        source = path.read_text(encoding="utf-8")
        assert "least_squares(" not in source
        assert "IMUInverseKinematicsTool" not in source
