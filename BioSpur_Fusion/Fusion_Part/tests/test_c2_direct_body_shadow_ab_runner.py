from __future__ import annotations

import ast
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import run_c2_direct_body_shadow_ab_pilot as runner


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_pilot_scope_and_resource_contract_are_exact() -> None:
    assert runner.PILOT_ACTIONS == (
        "04_shoulder_left", "05_shoulder_right",
        "06_elbow_left", "07_elbow_right",
    )
    assert runner.PILOT_HARD_S == 300.0
    assert runner.PILOT_DISK_CAP_BYTES == 50_000_000
    assert runner.PILOT_RSS_CAP_KB == 1_500_000
    assert runner.FEATURE_SWEEP_P99_GATE_MS == 5.0
    assert runner.POSE_CACHE_MAXIMUM == 64
    assert runner.PREFLIGHT_RSS_CAP_KB == 300_000
    assert runner.PREFLIGHT_DISK_CAP_BYTES == 50_000_000
    assert all(not action.startswith("H") for action in runner.PILOT_ACTIONS)


def test_runner_import_graph_excludes_prior_loo_statistics_and_rf_owner() -> None:
    source = Path(runner.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(alias.name for alias in node.names)
    for forbidden in ("HeldRangeLabeler", "body_shadow_study", "rf_shadow_field"):
        assert all(forbidden not in value for value in imported)


def test_hard_feature_timer_includes_pose_fk_and_all_anchor_evidence() -> None:
    source = inspect.getsource(runner._pilot)
    timer_start = source.index("feature_started = time.perf_counter()")
    snapshot = source.index("snapshot = provider.snapshot")
    evidence = source.index("evidence = {")
    timer_stop = source.index(
        "(time.perf_counter() - feature_started) * 1000.0"
    )
    assert timer_start < snapshot < evidence < timer_stop
    assert '"scope": "strict-floor+pose/FK/cache+all-anchor shadow evidence"' in source


def test_pose_unavailable_is_structured_and_skips_measurement_and_solver_work() -> None:
    source = inspect.getsource(runner._pilot)
    caught = source.index("except PoseUnavailableError as exc")
    continued = source.index("continue", caught)
    evidence = source.index("evidence = {", caught)
    ranges = source.index("float(raw.ranges_mm[anchor])", caught)
    solve = source.index("paired = solve_direct_ab", caught)
    assert caught < continued < evidence < ranges < solve
    assert '"range_payload_inspected_for_structural_validity": True' in source
    assert '"range_magnitude_used_in_geometry_or_weights": False' in source
    assert '"range_passed_to_solver": False' in source
    assert '"solver_calls": 0' in source
    assert '"tracker_updated": False' in source
    assert "nonterminal POSE_UNAVAILABLE followed by fresh pose" in source


def test_raw_bindings_are_exactly_four_actions_without_opening_payloads() -> None:
    bindings = runner._raw_bindings()
    assert len(bindings) == 4
    assert {Path(path).parts[-4] for path in bindings} == set(runner.PILOT_ACTIONS)
    assert all(len(digest) == 64 for digest in bindings.values())


def test_reference_v6_binding_is_complete_and_sealed() -> None:
    bindings = runner._reference_v6_bindings()
    assert {key: len(value) for key, value in bindings.items()} == {
        "source_hashes": 41,
        "input_hashes": 8,
        "full_predecode_expected_hashes": 77,
    }
    assert runner._sha256(runner.REFERENCE_FULL_PREFLIGHT / "SHA256SUMS") == (
        runner.REFERENCE_FULL_PREFLIGHT_SHA256
    )


def test_cap75_qualification_binding_freezes_numeric_and_online_axes() -> None:
    result = runner._verified_cap75_qualification()
    assert result["status"] == "BLOCKED_CAP75_QUALIFICATION_GATE"
    assert result["prefix_rows_compared"] == 5_352
    assert all(
        result["gates"][name]
        for name in (
            "prefix_rows_exact",
            "cap50_cap75_prior_results_bitwise_equal",
            "all_prior_nfev_below_50",
            "blocker_cap75_equals_sealed_cap150",
            "rss_below_cap",
        )
    )
    assert result["gates"]["feature_p99_below_5ms"] is False
    assert result["gates"]["online_B_core_p99_below_service_interval"] is False
    assert result["gates"]["online_B_core_max_below_service_interval"] is False
    assert runner._sha256(runner.CAP75_QUALIFICATION / "SHA256SUMS") == (
        runner.CAP75_QUALIFICATION_SHA256
    )


def test_pilot_success_and_failure_outputs_cannot_promote_online_state() -> None:
    expected_timing = {
        "feature_p99": 16.094797315308814,
        "feature_max": 24.550542992074043,
        "feature_gate": 5.0,
        "online_B_core_p99": 21.91983855213032,
        "online_B_core_max": 38.720948970876634,
        "online_B_core_gate": 12.004801920768308,
    }
    statuses = (
        runner._pilot_completion_status(current_feature_timing_pass=True),
        "BLOCKED_DIRECT_AB_PILOT",
    )
    assert statuses[0] == "OFFLINE_DIRECT_AB_PILOT_COMPLETE_ONLINE_BLOCKED"
    for status in statuses:
        result = runner._pilot_outcome(status, {"current_runtime_ms": 0.1})
        assert result["status"] == status
        assert result["execution_class"] == "OFFLINE_ONLY"
        assert result["numeric_status"] == "NUMERICALLY_QUALIFIED_OFFLINE_ONLY"
        assert result["online_status"] == "ONLINE_BLOCKED"
        assert result["qualification_claim_ready"] is False
        assert result["scientific_pass"] is result["product_ready"] is False
        assert result["production_ready"] is result["online_ready"] is False
        assert result["viewer_generated"] is result["viewer_allowed"] is False
        assert result["frozen_online_timing_failure_ms"] == expected_timing
        assert result["cap75_qualification"]["SHA256SUMS_sha256"] == (
            runner.CAP75_QUALIFICATION_SHA256
        )
        assert result["current_runtime_ms"] == 0.1
    for key, value in (
        ("status", "promotion"),
        ("online_status", "ONLINE_READY"),
        ("viewer_allowed", True),
        ("frozen_online_timing_failure_ms", {}),
    ):
        with pytest.raises(ValueError, match=f"one owner: {key}"):
            runner._pilot_outcome("failure", {key: value})
    assert inspect.getsource(runner._pilot).count("_pilot_outcome(") == 2


def test_seal_verification_fails_on_mutation(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "RESULT.json").write_text('{"status":"ok"}\n', encoding="utf-8")
    digest = runner._seal(evidence)
    runner._verify_seal(evidence, digest)
    (evidence / "RESULT.json").write_text('{"status":"changed"}\n', encoding="utf-8")
    with pytest.raises(RuntimeError, match="changed"):
        runner._verify_seal(evidence, digest)


def test_preflight_contract_freezes_equations_without_raw_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource_queries: list[int] = []

    def deterministic_rusage(owner: int) -> SimpleNamespace:
        assert owner == runner.resource.RUSAGE_SELF
        resource_queries.append(owner)
        return SimpleNamespace(ru_maxrss=runner.PREFLIGHT_RSS_CAP_KB - 1)

    monkeypatch.setattr(runner.resource, "getrusage", deterministic_rusage)
    monkeypatch.setattr(
        runner,
        "_verified_cap75_qualification",
        lambda: {"prefix_rows_compared": 5_352},
    )
    monkeypatch.setattr(
        runner,
        "_verified_pose_inputs_preflight",
        lambda: {
            "raw_uwb_opened": False,
            "H01_H02_opened_or_hashed": False,
        },
    )
    monkeypatch.setattr(
        runner,
        "_source_paths",
        lambda: (
            Path(runner.__file__),
            Path(runner.__file__),
        ),
    )
    monkeypatch.setattr(
        runner,
        "_raw_bindings",
        lambda: {f"actions/{action}/raw.bin": "0" * 64 for action in runner.PILOT_ACTIONS},
    )
    output = tmp_path / "preflight"
    args = type("Args", (), {
        "output": output,
        "focused_tests": "pytest unit",
        "focused_tests_passed": 77,
        "focused_tests_wall_s": 1.25,
        "executable_command": "python tool.py preflight --output fresh",
    })()
    result = runner._preflight(args)
    assert result["status"] == "READY_FOR_MONITOR_OFFLINE_DIRECT_AB_PILOT_REVIEW"
    assert result["numeric_status"] == "NUMERICALLY_QUALIFIED_OFFLINE_ONLY"
    assert result["online_status"] == "ONLINE_BLOCKED"
    assert result["qualification_claim_ready"] is False
    assert result["scientific_pass"] is result["product_ready"] is False
    assert result["production_ready"] is result["online_ready"] is False
    assert resource_queries and set(resource_queries) == {runner.resource.RUSAGE_SELF}
    assert result["raw_uwb_opened"] is False
    contract = json.loads((output / "MODEL_CONTRACT.json").read_text())
    assert contract["A_bound"] == [0.05, 0.75]
    assert contract["B_bound"] == [0.025, 0.75]
    assert "chord_length_m" in contract["per_segment"]
    assert "diagnostic only" in contract["per_segment"]["chord_length_m"]
    assert contract["material_support"]["weight_floor"] == 0.05
    assert contract["proxy_owner"].endswith("not anatomy or fitted widths")
    assert contract["qualification_claim_ready"] is False
    causal = json.loads((output / "CAUSAL_CONTRACT.json").read_text())
    assert "deep-identical" in causal["solver_link_identity"]
    assert "maximum_nfev=75" in causal["solver_settings"]
    assert "shared_root global/default remains 50" in causal["solver_settings"]
    assert causal["online_status"] == "ONLINE_BLOCKED"
    assert causal["measured_online_failure_ms"] == {
        "feature_gate": 5.0,
        "feature_max": 24.550542992074043,
        "feature_p99": 16.094797315308814,
        "online_B_core_gate": 12.004801920768308,
        "online_B_core_max": 38.720948970876634,
        "online_B_core_p99": 21.91983855213032,
    }
    pilot = json.loads((output / "PILOT_CONTRACT.json").read_text())
    assert pilot["pose_support_gate"]["minimum_fresh_fraction_per_action_node"] == 0.99
    assert pilot["pose_support_gate"]["minimum_fresh_sweeps_per_action_node"] == 100
    assert pilot["execution_class"] == "OFFLINE_ONLY"
    assert pilot["online_status"] == "ONLINE_BLOCKED"
    assert pilot["cap75_failure_policy"] == "STOP; no higher cap"
    assert pilot["viewer"] is False
    assert pilot["viewer_policy"] == "FORBIDDEN"
    assert pilot["command"] is None
    assert pilot["exact_command_binding"] == {
        "owner": "fresh parent evidence wrapper literal COMMAND.txt",
        "placeholders_forbidden": True,
        "required": True,
    }
    tests = json.loads((output / "TESTS.json").read_text())
    assert tests == {
        "command": "pytest unit", "exit_code": 0, "passed": 77, "wall_s": 1.25,
    }
    assert json.loads((output / "COMMAND.json").read_text())["pilot_status"] == (
        "HOLD_PENDING_MONITOR_GO"
    )
    assert _sha256(output / "SHA256SUMS") == runner._sha256(output / "SHA256SUMS")


def test_preflight_never_materializes_pose_arrays_and_pilot_still_does_before_raw() -> None:
    preflight_source = inspect.getsource(runner._preflight)
    pilot_source = inspect.getsource(runner._pilot)
    assert "_verified_pose_inputs_preflight()" in preflight_source
    assert "_verified_pose_inputs()" not in preflight_source
    full_verify = pilot_source.index("trajectory, pose_clocks, pose_audit = _verified_pose_inputs()")
    raw_decode = pilot_source.index("episode = _load_episode")
    assert full_verify < raw_decode
