from __future__ import annotations

import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "logs" / "imu_only_multi_action_centerline_calibration_v1_s2_terminal_audit"

if not AUDIT.exists():
    pytest.skip("terminal audit evidence is intentionally gitignored", allow_module_level=True)


def load(name: str) -> dict:
    return json.loads((AUDIT / name).read_text())


def test_terminal_artifact_set_is_complete() -> None:
    required = {
        "REPORT.md", "FAILURE_PRECEDENCE.json", "S0_TO_S2_PARAMETER_CROSSWALK.json",
        "FULL_AND_REDUCED_STATE_INVENTORY.json", "TRUTH_FIREWALL_AUDIT.json",
        "SOLVER_ATTEMPT_A_AUDIT.json", "DATA_ACCESS_AUDIT.json",
        "TEST_RESULTS.md", "SHA256_MANIFEST.json",
    }
    assert required <= {path.name for path in AUDIT.iterdir()}


def test_371_to_55_crosswalk_closes_exactly() -> None:
    result = load("S0_TO_S2_PARAMETER_CROSSWALK.json")
    assert len(result["rows"]) == 371
    assert [row["s0_column"] for row in result["rows"]] == list(range(371))
    assert len(result["new_s2_parameters"]) == 8
    assert result["count_closure"] == {
        "new_s2": 8, "reparameterized": 16, "removed": 324,
        "retained": 31, "s0_total": 371, "s2_total": 55,
        "schur_profiled": 0,
    }
    assert all(result["machine_assertions"].values())


def test_failure_precedence_is_fail_closed() -> None:
    result = load("FAILURE_PRECEDENCE.json")
    assert result["TOP_LEVEL"] == "FAIL_SYNTHETIC_RECOVERY"
    assert result["PRIMARY_BLOCKER"] == "FAIL_REDUCED_STATE_INVALID"
    assert result["SECONDARY_BLOCKER"] == "SOLVER_NOT_CONVERGED"
    assert result["TORSO_OBSERVABILITY"] == "NOT_QUALIFIED"


def test_truth_firewall_is_byte_identical_but_scope_limited() -> None:
    result = load("TRUTH_FIREWALL_AUDIT.json")
    assert result["pass"] is True
    assert all(result["byte_identity_checks"].values())
    assert result["truth_accidentally_used_by_estimator"] is False
    assert "does not rehabilitate" in result["scope_limit"]


def test_writer_fix_and_serialization_failure_are_audited() -> None:
    result = load("SOLVER_ATTEMPT_A_AUDIT.json")
    assert result["serialization_failure"]["exception"].startswith("TypeError")
    assert result["writer_only_fix"]["after"] == "fit_x: fit.x.tolist()"
    assert result["writer_only_fix"]["changes_residual_or_solver"] is False
    assert result["all_starts_converged"] is False


def test_all_real_and_held_out_inputs_remain_sealed() -> None:
    result = load("DATA_ACCESS_AUDIT.json")
    for key in ("real_calibration_payload", "UWB_T4", "Anchor_geometry",
                "operator_measurements", "walk_final_still", "golf_boxing"):
        assert result[key] == "SEALED"
    assert result["commit_push"] == "FORBIDDEN"


def test_attempt_a_runner_cannot_emit_a_calibration_pass() -> None:
    runner = ROOT / "tools" / "run_multi_action_calibration_s2_human_observability.py"
    source = runner.read_text()
    assert 'verdict = "FAIL_SYNTHETIC_RECOVERY"' in source
    assert "PASS_SYNTHETIC_HUMAN_MULTI_ACTION_OBSERVABILITY" not in source
    assert '"PRIMARY_BLOCKER": "FAIL_REDUCED_STATE_INVALID"' in source
    assert '"TORSO_OBSERVABILITY": "NOT_QUALIFIED"' in source
