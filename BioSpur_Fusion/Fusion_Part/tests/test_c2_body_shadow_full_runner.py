from __future__ import annotations

from argparse import Namespace
import hashlib
import json
from pathlib import Path

import pytest

import run_c2_causal_body_shadow_o2_full as runner


def test_full_runner_split_resources_and_allowlist_are_frozen() -> None:
    assert runner.ACTION_ORDER == runner.TRAIN_ACTIONS + runner.VALIDATION_ACTIONS
    assert len(runner.TRAIN_ACTIONS) == 11
    assert len(runner.VALIDATION_ACTIONS) == 8
    assert runner.PERF2_MAX_WORKERS == 4
    assert runner.FULL_HARD_S == 2700.0
    assert runner.FULL_DISK_CAP_BYTES == 400_000_000
    assert runner.FULL_RSS_CAP_KB == 1_500_000
    assert runner.QUALIFICATION_CEILING == "PREDICTIVE_UTILITY_QUALIFIED"
    assert runner.EXTRACTION_STOP_S == 2400.0
    assert runner.POST_EXTRACTION_RESERVE_S == 300.0
    assert all(runner.os.environ[name] == "1" for name in runner.THREAD_LIMIT_ENVIRONMENT)
    assert all("rf_shadow_field" not in path for path in runner._source_hashes())
    assert "tools/evaluate_c2_pair_bias_gate.py" in runner._source_hashes()
    assert "tests/test_c2_body_shadow_full_runner.py" in runner._source_hashes()
    required_dependencies = {
        "src/biospur_fusion/c2_3a_kinematics/__init__.py",
        "src/biospur_fusion/c2_3a_kinematics/interface.py",
        "src/biospur_fusion/c2_3a_kinematics/provenance.py",
        "src/biospur_fusion/c2_coupled_progressive/contracts.py",
        "src/biospur_fusion/c2_coupled_progressive/renderer.py",
        "src/biospur_fusion/c2_uwb_root_world/run_calibration.py",
        "src/biospur_fusion/c2_uwb_root_world/beacon_clock.py",
        "src/biospur_fusion/c2_uwb_root_world/calibration.py",
        "src/biospur_fusion/c2_uwb_root_world/u0.py",
        "src/biospur_fusion/uwb/frontend.py",
        "src/biospur_fusion/uwb/canonical_t4.py",
        "src/biospur_fusion/ingest/v47.py",
        "../B306_Part/tools/fusion_host_binary.py",
    }
    assert required_dependencies <= set(runner._source_hashes())


def test_frozen_capture_binding_selects_only_canonical_actions() -> None:
    binding = runner._frozen_calibration_capture_bindings()
    assert tuple(binding["actions"]) == runner.ACTION_ORDER
    assert binding["H01_H02_members_selected"] is False
    assert all(
        len(row["metadata"]) == 3 for row in binding["actions"].values()
    )
    for action, row in binding["actions"].items():
        expected_path = (
            runner.CALIBRATION_DATASET / "actions" / runner.PHYSICAL_DIRECTORY[action]
            / "rep_01/raw/fusion_host_raw.cobs.bin"
        )
        assert runner.ROOT / row["decoded_slice_path"] == expected_path
        assert (
            binding["full_predecode_expected_hashes"][row["decoded_slice_path"]]
            == row["decoded_slice_sha256"]
        )
        continuous_range = next(
            item for item in row["metadata"]
            if item["path"].endswith("/manifest/CONTINUOUS_RANGE.json")
        )
        owner = json.loads((runner.ROOT / continuous_range["path"]).read_text())
        assert row["decoded_slice_sha256"] == owner["slice_sha256"]
        assert row["decoded_slice_bytes"] == owner["slice_bytes"]
    assert sum(
        path.endswith("/raw/fusion_host_raw.cobs.bin")
        for path in binding["full_predecode_expected_hashes"]
    ) == 19
    assert sum(
        path.endswith("/system/fusion_continuous/fusion_host_raw.cobs.bin")
        for path in binding["full_predecode_expected_hashes"]
    ) == 1
    assert not any(
        token in path
        for path in binding["full_predecode_expected_hashes"]
        for token in ("H01", "H02", "h01", "h02")
    )


def test_perf2_feature_owner_delta_is_contract_text_only() -> None:
    audit = runner._perf2_feature_owner_delta_audit()
    assert audit["reverse_patch_reconstructed_sha256"] == runner.PERF2_CAUSAL_MODULE_SHA256
    assert audit["changed_byte_span_count"] == 3
    assert audit["causal_shadow_features_changed"] is False
    assert audit["compact_feature_bytes_changed"] is False
    assert audit["held_label_solver_cap_changed"] is True
    assert audit["global_shared_root_default_changed"] is False


def test_allowlist_mutation_fails_before_raw_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner = tmp_path / "owner.txt"
    owner.write_text("sealed\n", encoding="utf-8")
    digest = hashlib.sha256(owner.read_bytes()).hexdigest()
    preflight = tmp_path / "preflight"
    preflight.mkdir()
    (preflight / "ALLOWLIST.json").write_text(json.dumps({
        "source_hashes": {"owner.txt": digest},
        "input_hashes": {},
        "full_predecode_expected_hashes": {},
    }), encoding="utf-8")
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    assert runner._verify_full_allowlist(preflight)["verified_entries"] == 1
    owner.write_text("mutated\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="allowlist hash mismatch"):
        runner._verify_full_allowlist(preflight)


def test_preflight_existing_output_fails_before_metadata_or_resource_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner, "_metadata_input_hashes",
        lambda: (_ for _ in ()).throw(AssertionError("metadata opened")),
    )
    monkeypatch.setattr(
        runner, "_resource_projection",
        lambda: (_ for _ in ()).throw(AssertionError("benchmark ran")),
    )
    with pytest.raises(FileExistsError):
        runner._preflight(Namespace(output=tmp_path, focused_tests="not-run"))


def test_full_existing_output_fails_before_seal_or_raw_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner, "_verify_sealed_directory",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("seal read")),
    )
    monkeypatch.setattr(
        runner, "_verified_inputs",
        lambda: (_ for _ in ()).throw(AssertionError("pose/raw opened")),
    )
    with pytest.raises(FileExistsError):
        runner._run_full(Namespace(
            output=tmp_path, preflight=tmp_path,
            expected_preflight_sha256="0" * 64,
        ))


def test_resource_projection_is_bounded_columnar_and_includes_statistics() -> None:
    audit = runner._resource_projection()
    assert audit["fixture_replicates"] == 1000
    assert audit["sealed_perf2_extraction_projection_s"] == 2215.077
    assert audit["projected_evidence_bytes"] < runner.FULL_DISK_CAP_BYTES
    assert audit["projected_total_s"] <= runner.FULL_HARD_S
    assert audit["pass"]


def test_structured_row_budget_avoids_full_python_dict_materialization() -> None:
    projected = runner.FULL_ROW_CAP * runner.FULL_ROW_DTYPE.itemsize
    assert projected < 100_000_000
    assert runner.FULL_ROW_DTYPE.hasobject is False
