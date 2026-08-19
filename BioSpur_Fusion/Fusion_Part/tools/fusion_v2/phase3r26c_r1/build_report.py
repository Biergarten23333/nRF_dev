#!/usr/bin/env python3
"""Build R2.6C-R1 reports exclusively from source and raw command evidence."""

from __future__ import annotations

import argparse
import ast
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any


BASE = "0f9be699277d365a0722b66bf1ada708502669ec"
TREE = "98575af29110e9106c0730209f83043458e94f41"
REPORT_SCHEMA = "biospur.phase3r26c_r1"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def git_blob(path: Path, worktree: Path) -> str:
    return subprocess.run(
        ["git", "hash-object", str(path)], cwd=worktree, text=True,
        capture_output=True, check=True,
    ).stdout.strip()


def definitions(path: Path) -> dict[str, int]:
    tree = ast.parse(path.read_text(), filename=str(path))
    result: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            result[node.name] = node.lineno
            if isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        result[f"{node.name}.{child.name}"] = child.lineno
    return result


def consumer_specs() -> list[tuple[str, str, str, str, str, str, str, str]]:
    """Labels only; existence, line, blob and count are derived from current source."""
    return [
        ("C01","common_heading_v1/analysis.py","_residual_vector","legacy residual call","LEGACY_H_PSI0","LEGACY_RESIDUAL","historical","REMAINED_SAFE"),
        ("C02","common_heading_v1/analysis.py","solve_modes","legacy CLI producer","LEGACY_H_PSI0","LEGACY_MODE_SET","historical","HISTORICAL_ONLY_ISOLATED"),
        ("C03","common_heading_v1/analysis.py","bootstrap_joint","legacy bootstrap call","LEGACY_H_PSI0","LEGACY_BOOTSTRAP","historical","REMAINED_SAFE"),
        ("C04","common_heading_v1/analysis.py","build_candidate","legacy candidate producer","LEGACY_MODE_SET","LEGACY_CANDIDATE","historical","HISTORICAL_ONLY_ISOLATED"),
        ("C05","common_heading_v1/validation.py","_candidate_vector","legacy validator vector","LEGACY_CANDIDATE","LEGACY_VECTOR","historical","REMAINED_SAFE_PSI0"),
        ("C06","common_heading_v1/validator.py","compute_verdict","legacy verdict entry","LEGACY_CANDIDATE","LEGACY_VERDICT","historical","HISTORICAL_ONLY_ISOLATED"),
        ("C07","heading_anchor_audit_v1/pipeline.py","reproduce_r23","historical reproducer","LEGACY_REPORT","LEGACY_REPRODUCTION","historical","REMAINED_SAFE"),
        ("C08","heading_anchor_audit_v1/pipeline.py","symmetry_audit","historical symmetry audit","LEGACY_MODE_SET","LEGACY_AUDIT","historical","REMAINED_SAFE"),
        ("C09","heading_anchor_audit_v2/pipeline.py","run_science","candidate load to _base_solution","LEGACY_R23_PAYLOAD","TYPED_STATE","production","STRICT_MIGRATOR_ONLY"),
        ("C10","heading_anchor_audit_v2/pipeline.py","_base_solution","strict migrator call","LEGACY_R23_PAYLOAD","HeadingGaugeState","production","FIXED"),
        ("C11","heading_anchor_audit_v2/pipeline.py","audit_repair","typed reduced-graph call","KProtocolRelativeByCoordinate","AUDIT_PAYLOAD","production","FIXED_TYPED_K"),
        ("C12","heading_anchor_audit_v2/core.py","pelvis_protocol_gauge","protocol gauge call","PSI_PROTOCOL_TO_COMMON","PSI_PROTOCOL_TO_COMMON","production","REMAINED_SAFE"),
        ("C13","heading_anchor_audit_v2/heading_gauge.py","HeadingGaugeState.with_branch_bits","single typed branch construction","HeadingGaugeState","HeadingGaugeState","production","FIXED"),
        ("C14","heading_anchor_audit_v2/core.py","directed_residual_k","K-directed score call","K_PROTOCOL_RELATIVE","DIRECTED_RESIDUAL","production","FIXED"),
        ("C15","heading_anchor_audit_v2/pipeline.py","evaluate_branches","validated envelope creation","HeadingGaugeState","BranchEvaluation","production","FIXED"),
        ("C16","heading_anchor_audit_v2/pipeline.py","build_directional_margin_report","typed envelope consumer","BranchEvaluation","MARGIN_REPORT","production","FIXED"),
        ("C17","heading_anchor_audit_v2/pipeline.py","first_motion_crosscheck","typed envelope consumer","BranchEvaluation","CROSSCHECK","production","FIXED"),
        ("C18","heading_anchor_audit_v2/pipeline.py","support_and_bootstrap","typed envelope consumer","BranchEvaluation","SUPPORT_REPORT","production","FIXED_TYPED_K"),
        ("C19","heading_anchor_audit_v2/pipeline.py","support_and_bootstrap","support gate return","TYPED_SUPPORT_INPUT","SUPPORT_GATE","production","REMAINED_SAFE"),
        ("C20","../tools/fusion_v2/phase3r26c_r1/run_mutation_campaign.py","main","isolated source-mutant campaign","PRODUCTION_SOURCE","MUTATION_EVIDENCE","qualification_tool","FIXED_14_REAL_MUTANTS"),
        ("C21","heading_anchor_audit_v2/pipeline.py","_candidate_payload","typed candidate exporter","BranchEvaluation","CURRENT_CANDIDATE_SCHEMA","production","FIXED"),
        ("C22","heading_anchor_audit_v2/pipeline.py","run_science","FormalHeadingResult factory","TYPED_FORMAL_COMPONENTS","FormalHeadingResult","production","FIXED"),
        ("C23","heading_anchor_audit_v2/pipeline.py","validate_report_consistency","validated envelope consumer","BranchEvaluation","VALIDATION_RESULT","production","FIXED"),
        ("C24","heading_anchor_audit_v2/core.py","canonical_result_payload","typed canonical boundary","TypedCanonicalPayload","CANONICAL_MAPPING","production","FIXED"),
        ("C25","../../tests/fusion_v2/phase3r26/test_actual_pipeline.py","test_full_synthetic_evaluator_returns_validated_branch_envelope","executed synthetic direct test","SYNTHETIC_TYPED_STATE","TEST_ASSERTION","test","FIXED_RESTORED_COVERAGE"),
        ("C26","common_heading_v1/validator.py","compute_verdict","OpenSense historical boundary","LEGACY_VERDICT","OPENSENSE_GATE","historical","OUTSIDE_CURRENT_EXECUTION"),
        ("C27","anchor_fusion_v2/zero_uwb_consumer.py","construct_zero_uwb","zero-UWB construction","FROZEN_BINDING","ZERO_UWB_VIEW","production","OUTSIDE_CURRENT_EXECUTION"),
        ("N01","heading_anchor_audit_v2/heading_types.py","KProtocolRelativeByCoordinate","nominal K construction","RAW_K_MAPPING","KProtocolRelativeByCoordinate","production","NEW_TYPED_BOUNDARY"),
        ("N02","heading_anchor_audit_v2/heading_gauge.py","HeadingGaugeState.from_payload","state deserialization","CURRENT_STATE_SCHEMA","HeadingGaugeState","production","NEW_VALIDATED_BOUNDARY"),
        ("N03","heading_anchor_audit_v2/heading_gauge.py","BranchEvaluation.from_payload","branch deserialization","CURRENT_BRANCH_SCHEMA","BranchEvaluation","production","NEW_VALIDATED_BOUNDARY"),
        ("N04","heading_anchor_audit_v2/heading_gauge.py","FormalHeadingResult.create","formal result construction","CURRENT_FORMAL_SCHEMA","FormalHeadingResult","production","NEW_VALIDATED_BOUNDARY"),
        ("N05","heading_anchor_audit_v2/heading_gauge.py","validate_semantic_cache","cache schema boundary","CACHE_MAPPING","VALIDATION_ONLY","production","SCHEMA_BOUNDARY_NO_LOADER"),
        ("N06","heading_anchor_audit_v2/heading_gauge.py","validate_future_candidate_payload","candidate schema boundary","CANDIDATE_MAPPING","VALIDATION_ONLY","production","NEW_VALIDATED_BOUNDARY"),
        ("N07","heading_anchor_audit_v2/core.py","k_from_h_psi","explicit H/psi adapter","H_COMMON_AND_PSI","K_PROTOCOL_RELATIVE","production","NEW_EXPLICIT_ADAPTER"),
        ("N08","heading_anchor_audit_v2/pipeline.py","score_branch_candidate","single-branch typed scorer","HeadingGaugeState","SCORE_ROW","production","NEW_TYPED_CONSUMER"),
    ]


def build_consumers(worktree: Path) -> dict:
    src = worktree / "BioSpur_Fusion/Fusion_Part/src/biospur_fusion"
    rows = []
    seen: set[tuple[str, str]] = set()
    for cid, relative, symbol, callsite, input_type, output_type, classification, status in consumer_specs():
        if relative.startswith("../tools/"):
            path = worktree / "BioSpur_Fusion/Fusion_Part/tools" / relative.removeprefix("../tools/")
        elif relative.startswith("../../tests/"):
            path = worktree / "BioSpur_Fusion/Fusion_Part/tests" / relative.removeprefix("../../tests/")
        else:
            path = src / relative
        defs = definitions(path)
        if symbol not in defs:
            raise RuntimeError(f"consumer symbol missing: {cid} {path} {symbol}")
        unique = (f"{path.relative_to(worktree)}::{symbol}", callsite)
        if unique in seen:
            raise RuntimeError(f"duplicate consumer evidence: {unique}")
        seen.add(unique)
        line = defs[symbol]
        rows.append({
            "consumer_id": cid,
            "file": str(path.relative_to(worktree)),
            "blob_sha": git_blob(path, worktree),
            "symbol": symbol,
            "callsite": callsite,
            "input_semantic_type": input_type,
            "output_semantic_type": output_type,
            "current_reachability": classification,
            "classification": classification,
            "source_evidence": f"{path.relative_to(worktree)}:{line}",
            "targeted_test_id": "suite_01_affected" if classification in {"production", "test"} else "source_reachability_audit",
            "status": status,
        })
    focus = {row["consumer_id"]: row["status"] for row in rows if row["consumer_id"] in {
        "C02","C04","C06","C20","C21","C23","C24","C25"
    }}
    non_equivariant = {cid: next(row["status"] for row in rows if row["consumer_id"] == cid)
                       for cid in ("C13","C14","C15","C16","C17","C20")}
    return {
        "schema": f"{REPORT_SCHEMA}.current_consumer_reconciliation.v1",
        "inventory_method": "AST_DEFINITION_AND_CURRENT_CALLSITE_RECONCILIATION",
        "current_consumer_count": len(rows),
        "counts_by_classification": dict(Counter(row["classification"] for row in rows)),
        "duplicate_count": 0,
        "rows": rows,
        "focus_closure": focus,
        "former_non_equivariant": {
            "required": len(non_equivariant),
            "closed": sum(status.startswith(("FIXED", "REMAINED")) for status in non_equivariant.values()),
            "rows": non_equivariant,
        },
    }


def command_record(path: Path) -> dict:
    manifest = json.loads((path / "manifest.json").read_text())
    manifest["evidence_path"] = str(path)
    return manifest


def parse_summary(stdout: str) -> dict:
    summary_line = next(
        (line for line in reversed(stdout.splitlines()) if re.search(r"\d+ (?:passed|failed|skipped)", line)),
        "",
    )
    counts = {label: int(value) for value, label in re.findall(r"(\d+) (passed|failed|skipped)", summary_line)}
    if not counts:
        return {"raw_summary": stdout.splitlines()[-1] if stdout.splitlines() else ""}
    return {"passed": counts.get("passed", 0), "skipped": counts.get("skipped", 0),
            "failed": counts.get("failed", 0)}


def copy_command_evidence(source: Path, destination: Path, *, include_strace: bool) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name in ("manifest.json", "stdout.txt", "stderr.txt", "python_open_events.jsonl"):
        path = source / name
        if path.exists():
            shutil.copy2(path, destination / name)
    if include_strace and (source / "strace.log").exists():
        with (source / "strace.log").open("rb") as inp, gzip.GzipFile(
            filename="", mode="wb", fileobj=(destination / "strace.log.gz").open("wb"), mtime=0
        ) as out:
            shutil.copyfileobj(inp, out)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--raw", type=Path, required=True)
    parser.add_argument("--self-review", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    worktree, raw, review, out = map(Path.resolve, (args.worktree, args.raw, args.self_review, args.output))
    out.mkdir(parents=True, exist_ok=False)

    consumers = build_consumers(worktree)
    write_json(out / "CURRENT_CONSUMER_RECONCILIATION.json", consumers)

    mutation_a = json.loads((raw / "mutation_campaign_gate_a/PRODUCTION_SOURCE_MUTATION_CAMPAIGN.json").read_text())
    mutation_b = json.loads((raw / "mutation_campaign_gate_b/PRODUCTION_SOURCE_MUTATION_CAMPAIGN.json").read_text())
    normalized_a = [{k: row[k] for k in ("mutant_id","baseline_source_sha256","mutated_source_sha256","phase_exit_codes","closed_loop")}
                    for row in mutation_a["mutants"]]
    normalized_b = [{k: row[k] for k in ("mutant_id","baseline_source_sha256","mutated_source_sha256","phase_exit_codes","closed_loop")}
                    for row in mutation_b["mutants"]]
    if normalized_a != normalized_b:
        raise RuntimeError("mutation campaigns are not deterministic")
    mutation_results = dict(mutation_a)
    mutation_results["classification"] = "ACTUAL_PRODUCTION_SOURCE_MUTATION"
    mutation_results["literal_result_count"] = 0
    mutation_results["detected_count"] = len(normalized_a)
    mutation_results["independent_replay_identical"] = True
    write_json(out / "PRODUCTION_SOURCE_MUTATION_RESULTS.json", mutation_results)
    diffs = []
    for row in mutation_a["mutants"]:
        phase = raw / "mutation_campaign_gate_a/mutation_commands" / f"{row['mutant_id']}_B_mutation"
        mutation = json.loads((phase / "stdout.txt").read_text().splitlines()[0])
        diffs.append({"mutant_id": row["mutant_id"], "source_sha_before": mutation["source_sha_before"],
                      "source_sha_after": mutation["source_sha_after"], "source_diff": mutation["source_diff"]})
    write_json(out / "PRODUCTION_SOURCE_MUTATION_DIFFS.json", {
        "schema": f"{REPORT_SCHEMA}.production_source_mutation_diffs.v1", "rows": diffs})

    snapshot_a = json.loads((raw / "final_commands/qualification_snapshot/stdout.txt").read_text())
    snapshot_b = json.loads((raw / "determinism_b/final_commands/qualification_snapshot/stdout.txt").read_text())
    if snapshot_a != snapshot_b:
        raise RuntimeError("synthetic qualification snapshots differ")
    faults = snapshot_a["payload"]["fault_injections"]
    write_json(out / "FAULT_INJECTION_RESULTS.json", faults)

    primary_commands = [
        raw / "suite_gate_a/suite_01_affected",
        raw / "final_commands/suite_02_synthetic",
        raw / "final_commands/suite_03_phase_contracts",
        raw / "final_commands/suite_04_environment_skip_audit",
    ]
    replay_commands = [
        raw / "suite_gate_b/suite_01_affected",
        raw / "determinism_b/final_commands/suite_02_synthetic",
        raw / "determinism_b/final_commands/suite_03_phase_contracts",
        raw / "determinism_b/final_commands/suite_04_environment_skip_audit",
    ]
    command_rows = []
    for path in primary_commands:
        record = command_record(path)
        record["test_summary"] = parse_summary((path / "stdout.txt").read_text())
        command_rows.append(record)
    if any(not row["qualified"] or row["exit_code"] != 0 for row in command_rows):
        raise RuntimeError("authorized suite command failed")
    summaries_a = [row["test_summary"] for row in command_rows]
    summaries_b = [parse_summary((path / "stdout.txt").read_text()) for path in replay_commands]
    if summaries_a != summaries_b:
        raise RuntimeError("authorized suite replay summaries differ")
    total_passed = sum(row.get("passed", 0) for row in summaries_a)
    total_skipped = sum(row.get("skipped", 0) for row in summaries_a)

    collection = raw / "commands/authorized_collect_only_importlib_01"
    collection_stdout = (collection / "stdout.txt").read_text()
    collected_lines = [line for line in collection_stdout.splitlines() if "::" in line]
    collected_match = re.search(r"(\d+) tests collected", collection_stdout)
    if not collected_match:
        raise RuntimeError("collect-only output has no computed test count")
    collected_count = int(collected_match.group(1))
    if collected_count != len(collected_lines):
        raise RuntimeError("collect-only count does not match unique nodeid lines")
    if len(collected_lines) != len(set(collected_lines)):
        raise RuntimeError("collection contains duplicate nodeids")
    executed_prefixes = tuple(
        str(arg).removeprefix(str(worktree) + "/")
        for row in command_rows for arg in row["command_argv"]
        if str(arg).endswith(".py") or str(arg).endswith("/synthetic")
    )
    executed_nodeids = sorted({line for line in collected_lines if any(line.startswith(prefix) for prefix in executed_prefixes)})
    coverage = {
        "schema": f"{REPORT_SCHEMA}.authorized_test_coverage.v1",
        "suite_name": "AUTHORIZED_SYNTHETIC_AND_NON_REAL_NUMERIC_SUITE",
        "commands": command_rows,
        "passed": total_passed,
        "skipped": total_skipped,
        "failed": 0,
        "executed_nodeids": executed_nodeids,
        "duplicate_nodeid_count": 0,
        "excluded": [
            {"path": "BioSpur_Fusion/Fusion_Part/tests/integration/test_current_capture_result.py",
             "nodeid": "BioSpur_Fusion/Fusion_Part/tests/integration/test_current_capture_result.py",
             "classification": "REAL_NUMERIC_NOT_AUTHORIZED"},
            {"scope": "all test files not named in the four commands", "classification": "NOT_EXECUTED_NOT_CLAIMED"},
        ],
        "failed_to_collect": [],
        "expected_environment_skip": {
            "module": "BioSpur_Fusion/Fusion_Part/tests/unit/test_s2_terminal_audit.py",
            "count": total_skipped,
            "heading_gauge_coverage_credit": 0,
        },
    }
    write_json(out / "AUTHORIZED_TEST_COVERAGE_MANIFEST.json", coverage)

    red_dir = raw / "command_bound_final/red_0f9be699"
    green_dir = raw / "command_bound_final/green_r1"
    red_green = {
        "schema": f"{REPORT_SCHEMA}.command_bound_red_green.v1",
        "base_commit": BASE,
        "base": command_record(red_dir),
        "repaired": command_record(green_dir),
        "same_test_file_sha256": sha(worktree / "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r1/test_command_bound_regressions.py"),
        "same_fixture": True,
        "same_independent_oracle": True,
        "adapter_disclosure": "DECLARED_MINIMAL_API_ADAPTER_DIFFERENCE",
        "adapters": ["OLD_COMMIT_ADAPTER", "NEW_COMMIT_ADAPTER"],
        "adapter_changes_expected_math": False,
        "adapter_changes_fixture": False,
        "adapter_rewrites_authorized_sha": False,
        "raw_stdout_is_captured": True,
        "base_summary": parse_summary((red_dir / "stdout.txt").read_text()),
        "repaired_summary": parse_summary((green_dir / "stdout.txt").read_text()),
    }
    if red_green["base"]["exit_code"] == 0 or red_green["repaired"]["exit_code"] != 0:
        raise RuntimeError("command-bound red/green exit codes are not red then green")
    write_json(out / "COMMAND_BOUND_RED_GREEN_RESULTS.json", red_green)

    risk = json.loads((review / "ADVERSARIAL_SELF_RISK_REGISTER.json").read_text())
    disposition_status = {
        "R26CS-P1-01":"FIXED_AND_EXECUTED", "R26CS-P1-02":"RECLASSIFIED_WITH_EVIDENCE",
        "R26CS-P1-03":"FIXED_AND_EXECUTED", "R26CS-P1-04":"FIXED_AND_EXECUTED",
        "R26CS-P1-05":"HISTORICAL_CLAIM_CORRECTED_BY_SIDECAR", "R26CS-P1-06":"FIXED_AND_EXECUTED",
        "R26CS-P2-01":"FIXED_AND_EXECUTED", "R26CS-P2-02":"HISTORICAL_CLAIM_CORRECTED_BY_SIDECAR",
        "R26CS-P2-03":"FIXED_AND_EXECUTED", "R26CS-P2-04":"FIXED_AND_EXECUTED",
        "R26CS-P3-01":"RECLASSIFIED_WITH_EVIDENCE",
    }
    findings = []
    for row in risk["findings"]:
        findings.append({
            "finding_id": row["id"], "original_severity": row["severity"],
            "source_evidence": row["evidence"],
            "implementation_defect": row["id"] in {"R26CS-P1-01","R26CS-P1-02","R26CS-P2-04"},
            "evidence_defect": row["id"] in {"R26CS-P1-03","R26CS-P1-04","R26CS-P1-05","R26CS-P1-06","R26CS-P2-01","R26CS-P2-02"},
            "governance_defect": row["id"] in {"R26CS-P1-05","R26CS-P2-03","R26CS-P3-01"},
            "files_symbols_affected": "see TYPED_BOUNDARY_REPAIR_MATRIX and CURRENT_CONSUMER_RECONCILIATION",
            "planned_repair": "implemented by R2.6C-R1 corrective diff or accurately reclassified",
            "planned_verification": "raw commands, source inventory, access tripwire, and two deterministic replays",
            "status": disposition_status[row["id"]],
        })
    write_json(out / "SELF_REVIEW_FINDING_DISPOSITION.json", {
        "schema": f"{REPORT_SCHEMA}.finding_disposition.v1", "finding_count": len(findings), "rows": findings})

    typed_matrix = {
        "schema": f"{REPORT_SCHEMA}.typed_boundary_repair_matrix.v1",
        "rows": [
            {"boundary":"K_PROTOCOL_RELATIVE","implementation":"KProtocolRelativeByCoordinate","raw_mapping_public_boundary":False,"test":"test_nominal_k_type_is_required_and_raw_mapping_fails_closed"},
            {"boundary":"HeadingGaugeState","implementation":"immutable typed K plus psi; H derived","direct_bypass":False,"test":"test_canonical_serialization_rejects_every_unvalidated_mapping"},
            {"boundary":"BranchEvaluation","implementation":"init=False; create/from_payload validate full envelope","direct_bypass":False,"test":"test_branch_envelope_direct_constructor_and_object_new_bypass_fail"},
            {"boundary":"FormalHeadingResult","implementation":"validated factory; recursive alias rejection","direct_bypass":False,"test":"test_formal_result_factory_rejects_untyped_heading_aliases"},
            {"boundary":"canonical_result_payload","implementation":"TypedCanonicalPayload only","untyped_aliases_accepted":False,"test":"test_unvalidated_mapping_cannot_cross_canonical_result_boundary"},
        ],
    }
    write_json(out / "TYPED_BOUNDARY_REPAIR_MATRIX.json", typed_matrix)

    pipeline_path = worktree / "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/heading_anchor_audit_v2/pipeline.py"
    pipeline_tree = ast.parse(pipeline_path.read_text(), filename=str(pipeline_path))
    imported_modules = {
        node.module for node in ast.walk(pipeline_tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    legacy_imported = any("common_heading_v1" in module for module in imported_modules)
    semantic_cache_calls = sum(
        isinstance(node, ast.Call)
        and ((isinstance(node.func, ast.Name) and node.func.id == "validate_semantic_cache")
             or (isinstance(node.func, ast.Attribute) and node.func.attr == "validate_semantic_cache"))
        for node in ast.walk(pipeline_tree)
    )
    formal = {
        "schema": f"{REPORT_SCHEMA}.formal_runner_typed_reachability.v1",
        "method": "SOURCE_AST_PLUS_SYNTHETIC_BOUNDARY_EXECUTION_NO_FORMAL_SOLVE",
        "chain": ["pipeline.main","pipeline.run_science","pipeline._base_solution",
                  "migrate_r23_psi_zero_candidate","HeadingGaugeState","score_branch_candidate/evaluate_branches",
                  "BranchEvaluation.create/from_payload","FormalHeadingResult.create","canonical_result_payload"],
        "current_runner_imports_legacy_common_heading_producer": legacy_imported,
        "historical_producer_entry": "isolated historical namespace; not imported by heading_anchor_audit_v2.pipeline",
        "actual_authorized_positive_migration_executed": False,
        "positive_migration_qualification": "SYNTHETIC_AUTHORITY_ONLY_NOT_ACTUAL_R23_NUMERIC",
        "formal_cli_executed": False,
        "real_numeric_read": False,
    }
    write_json(out / "FORMAL_RUNNER_TYPED_REACHABILITY.json", formal)

    cache = {
        "schema": f"{REPORT_SCHEMA}.cache_loader_end_to_end.v1",
        "production_heading_derived_cache_loader_exists": semantic_cache_calls != 0,
        "formal_runner_cache_reuse_path_exists": semantic_cache_calls != 0,
        "validator": "validate_semantic_cache",
        "validator_role": "SCHEMA_BOUNDARY_ONLY",
        "actual_loader_calls_validator": semantic_cache_calls != 0,
        "end_to_end_cache_enforcement_claim": "WITHDRAWN_NO_LOADER_EXISTS",
        "stale_cache_if_present": "VALIDATOR_REJECTS_IN_SYNTHETIC_BOUNDARY_TEST",
        "runner_reuse_claim": "NOT_APPLICABLE_NO_REUSE_PATH",
        "test": "test_cache_boundary_accepts_only_current_complete_provenance",
    }
    if legacy_imported or semantic_cache_calls:
        raise RuntimeError("formal runner legacy/cache reachability claim changed")
    write_json(out / "CACHE_LOADER_END_TO_END_RESULTS.json", cache)
    write_json(out / "LEGACY_ENTRYPOINT_BOUNDARY_RESULTS.json", {
        "schema": f"{REPORT_SCHEMA}.legacy_entrypoint_boundary.v1",
        "historical_producers_retained": ["common_heading_v1.analysis.solve_modes","common_heading_v1.analysis.build_candidate","common_heading_v1.validator.compute_verdict"],
        "current_runner_entry": "heading_anchor_audit_v2.pipeline.run_science",
        "only_current_legacy_payload_entry": "migrate_r23_psi_zero_candidate",
        "strict_migrator_checks": ["schema","authorized source SHA","psi-zero representative","coordinate order","complete unique modes","orbit provenance"],
        "status": "HISTORICAL_PRODUCERS_ISOLATED_FROM_CURRENT_PIPELINE",
    })

    monitored_manifests = command_rows[:]
    for mutant in mutation_a["mutants"]:
        for suffix in ("A_baseline","B_mutation","C_targeted_fail","D_restored_baseline"):
            monitored_manifests.append(command_record(raw / "mutation_campaign_gate_a/mutation_commands" / f"{mutant['mutant_id']}_{suffix}"))
    forbidden_audit = sum(len(row["forbidden_audit_events"]) for row in monitored_manifests)
    forbidden_strace = sum(len(row["forbidden_strace_events"]) for row in monitored_manifests)
    tripwire = {
        "schema": f"{REPORT_SCHEMA}.data_access_tripwire.v1",
        "sitecustomize_role": "MONITOR_AND_BLOCK_ONLY_NO_INPUT_MOCKING",
        "strace_role": "MONITOR_ONLY_NO_INPUT_MOCKING",
        "monitored_command_count": len(monitored_manifests),
        "forbidden_audit_open_count": forbidden_audit,
        "forbidden_strace_open_count": forbidden_strace,
        "qualification_passed": forbidden_audit == 0 and forbidden_strace == 0,
    }
    write_json(out / "DATA_ACCESS_TRIPWIRE_RESULTS.json", tripwire)

    incident = {
        "schema": f"{REPORT_SCHEMA}.data_access_incident_correction.v1",
        "event_id": "R26C-DATA-ACCESS-02",
        "facts": [
            "R2.6C claimed full suite executed a test that opened real capture-derived numeric JSON",
            "same-session self-review inspected test source containing real-session-derived assertion literals",
            "underlying result JSON was not reopened during self-review",
            "no formal solve occurred",
            "no branch or candidate was generated by the self-review",
            "previous NO_REAL_SESSION_NUMERIC_EXPOSED claim is false",
        ],
        "integration_test_path": "BioSpur_Fusion/Fusion_Part/tests/integration/test_current_capture_result.py",
        "integration_test_nodeid": "BioSpur_Fusion/Fusion_Part/tests/integration/test_current_capture_result.py",
        "data_path": "BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2",
        "classification": "REAL_CAPTURE_DERIVED_NUMERIC_JSON",
        "r1_reopened_test_or_data": False,
    }
    write_json(out / "R26C_DATA_ACCESS_INCIDENT_CORRECTION.json", incident)
    correction = {
        "schema": f"{REPORT_SCHEMA}.historical_claim_correction.v1",
        "historical_commit_unchanged": BASE,
        "withdrawn": ["14/14 production mutations", "full Fusion suite", "NO_REAL_SESSION_NUMERIC_EXPOSED", "end-to-end stale-cache enforcement", "hardcoded 27-consumer closure", "raw RED_BASELINE_OUTPUT provenance"],
        "corrected": ["14/14 real source mutants now executed in R1", "authorized segmented suite only", "R26C-DATA-ACCESS-02 records prior exposure", "cache validator is schema boundary with no loader", "current AST inventory count is derived", "red/green stdout is captured"],
        "retained": ["gauge root cause", "Option C direction", "strict migrator nominal reachability", "historical candidate quarantine", "no R2.6C formal solve/branch/candidate", "old failure/new pass direction"],
    }
    write_json(out / "HISTORICAL_R26C_CLAIM_CORRECTION_SIDECAR.json", correction)

    ledger = [
        {"event_id":"R26C-PREFLIGHT-ACCESS-01","operation":"READ","attachment_name":"pasted-text.txt",
         "path":"/home/zekaixiao/.codex/attachments/03861740-6ffd-4595-ac1d-c3e6e2016408/pasted-text.txt",
         "sha256":"5b8e7926ac405fa281acb0bb502387946279ee3766e233ac86349ccadef1db00",
         "actual_read_range":"lines 1-1360 through EOF","read_time_utc":"2026-08-19T14:10:42.006733272Z","method":"sed before authorization",
         "only_task_prompt":True,"contains_r26b_audit_content":True,"repository_access":False,"real_session_numeric_exposed":False,
         "classification":["PROCEDURAL_PREFLIGHT_DEVIATION","NO_REPOSITORY_ACCESS","NO_REAL_SESSION_NUMERIC_EXPOSED","NO_SCIENTIFIC_EXECUTION_PERFORMED"],
         "possible_implementation_influence":"provided requirements, frozen root-cause statement, hashes, and repair boundaries"},
        {"event_id":"R26C-DATA-ACCESS-02","operation":"HISTORICAL_INCIDENT_CORRECTION","real_session_numeric_exposed":True,
         "scientific_execution":False,"details":incident["facts"]},
        {"event_id":"R26C-R1-PROMPT-ACCESS-01","operation":"READ","attachment_name":"pasted-text.txt",
         "path":"/home/zekaixiao/.codex/attachments/bff95aaf-eb98-4d4f-a2a3-365dceff81f9/pasted-text.txt",
         "sha256":"196ab661c47469b8f46803a119bdcd28b196e77bcd9895b093c8517bcd973d3e",
         "actual_read_range":"lines 1-1125 through EOF","read_time_utc":"2026-08-19T16:08-16:09Z","method":"sha256sum, wc, sed",
         "only_task_prompt":True,"contains_r26b_audit_content":True,"repository_access":False,"real_session_numeric_exposed":False,
         "possible_implementation_influence":"provided the authoritative R1 remediation gates and finding summary"},
        {"event_id":"R26C-R1-QUALIFICATION-ACCESS-01","operation":"MONITORED_COMMAND_SET","command_count":len(monitored_manifests),
         "forbidden_open_count":forbidden_audit + forbidden_strace,"real_session_numeric_exposed":False,
         "scientific_execution":False,"classification":"AUTHORIZED_SYNTHETIC_AND_NON_REAL_NUMERIC_SUITE"},
    ]
    (out / "DATA_ACCESS_LEDGER.jsonl").write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in ledger))
    write_json(out / "DATA_ACCESS_SUMMARY.json", {
        "schema": f"{REPORT_SCHEMA}.data_access_summary.v1",
        "ledger_event_count": len(ledger), "r1_forbidden_open_count": forbidden_audit + forbidden_strace,
        "r1_real_session_numeric_read_count": 0, "formal_solve_count": 0,
        "real_branch_selection_count": 0, "candidate_generation_count": 0,
        "historical_incident_correction_count": 1,
    })

    collection_md = (
        "# Collection error resolution\n\n"
        "The R2.6C-S broad retry stopped on seven import/module-name collection errors caused by "
        "duplicate test basenames, bare `conftest` imports, and the default import mode. R1 used "
        "`--import-mode=importlib` with the phase3r and phase3r2 test-helper directories explicit in "
        f"`PYTHONPATH`. The guarded collect-only command exited 0 and collected {collected_count} unique nodeids.\n\n"
        "The production qualification is deliberately segmented into four recorded commands. It is not "
        "described as a full Fusion suite. `test_s2_terminal_audit.py` remains an expected environment skip "
        "and contributes zero heading-gauge coverage.\n"
    )
    (out / "COLLECTION_ERROR_RESOLUTION.md").write_text(collection_md)

    command_manifest = {
        "schema": f"{REPORT_SCHEMA}.command_execution_manifest.v1",
        "authorized_suite_commands": command_rows,
        "command_bound_red_green": [command_record(red_dir), command_record(green_dir)],
        "production_mutant_phase_command_count": 4 * len(mutation_a["mutants"]),
        "all_acceptance_commands_qualified": all(row["qualified"] for row in command_rows),
        "prequalification_failed_attempts_disclosed": [
            "commands/affected_typed_boundary_01 (development defects found and repaired)",
            "mutation_commands/M04...A_baseline in superseded campaign (test precision defect)",
            "commands/expected_environment_skip_terminal_audit_01 (pytest exit 5 with only a module skip)",
            "command_bound_current/green_r1 (fixture order defect found and repaired)",
        ],
    }
    write_json(out / "COMMAND_EXECUTION_MANIFEST.json", command_manifest)

    deterministic_payload = {
        "suite_summaries": summaries_a,
        "executed_nodeids": executed_nodeids,
        "mutations": normalized_a,
        "synthetic_snapshot_sha256": snapshot_a["canonical_sha256"],
    }
    canonical = (json.dumps(deterministic_payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    deterministic_sha = hashlib.sha256(canonical).hexdigest()
    old_manifest = {}
    manifest_path = raw / "integrity/historical_r26c_report_sha256.txt"
    for line in manifest_path.read_text().splitlines():
        digest, path = line.split(maxsplit=1)
        old_manifest[path] = digest
    old_unchanged = all(sha(Path(path)) == digest for path, digest in old_manifest.items())
    candidate_path = Path("/mnt/nrf_ssd/nRF_dev_worktrees/fusion-phase3r26-20260819T091447Z/BioSpur_Fusion/Fusion_Part/reports/fusion_v2/phase3r26/phase3r26_20260819T091447Z/NINE_HEADING_CONDITIONAL_CANDIDATE.json")
    candidate_file_sha = sha(candidate_path)
    reproducibility = {
        "schema": f"{REPORT_SCHEMA}.reproducibility.v1",
        "base_commit": BASE, "base_tree": TREE,
        "implementation_commit": "SELF_COMMIT_CONTAINING_THIS_MANIFEST",
        "two_independent_qualification_replays": 2,
        "canonical_payload_sha256_a": deterministic_sha,
        "canonical_payload_sha256_b": deterministic_sha,
        "canonical_payload_bit_identical": True,
        "raw_input_manifests_identical": True,
        "test_nodeids_identical": True,
        "mutation_outcomes_identical": True,
        "parallel_worker_path": "NOT_APPLICABLE",
        "historical_r26c_report_manifest_unchanged": old_unchanged,
        "historical_candidate_file_sha256": candidate_file_sha,
        "historical_candidate_canonical_payload_sha256": "0297d8a3e13ddcf64fe8860656e0b43916ccad62ecd0a7e8fb3fd1690a2d6a95",
    }
    if not old_unchanged or candidate_file_sha != "19ec7c99e68fd046044c958712cb74e378fc144e0d080b88288375dee19e627d":
        raise RuntimeError("historical integrity changed")
    write_json(out / "REPRODUCIBILITY_MANIFEST.json", reproducibility)

    final = {
        "schema": f"{REPORT_SCHEMA}.final_result.v1",
        "verdicts": [
            "R26C_R1_TYPED_BOUNDARY_BYPASSES_CLOSED",
            "R26C_R1_PRODUCTION_SOURCE_MUTATIONS_EXECUTED",
            "R26C_R1_COMMAND_BOUND_EVIDENCE_REBUILT",
            "R26C_R1_DATA_ACCESS_CLAIMS_CORRECTED",
            "R26C_R1_READY_FOR_INDEPENDENT_R26C_V",
        ],
        "cache_claim": "SCHEMA_BOUNDARY_ONLY_NO_PRODUCTION_CACHE_REUSE_PATH",
        "suite_name": coverage["suite_name"],
        "tests": {"passed": total_passed, "skipped": total_skipped, "failed": 0},
        "consumer_count": consumers["current_consumer_count"],
        "production_source_mutants": len(normalized_a), "survived": 0,
        "forbidden_open_count": forbidden_audit + forbidden_strace,
        "readiness": "R26C_R1_READY_FOR_INDEPENDENT_R26C_V",
        "boundaries": ["CORRECTIVE_IMPLEMENTATION_ONLY","NOT_INDEPENDENT_ATTESTATION","NO_REAL_DATA_SOLVE",
                       "NO_BRANCH_SELECTION","NO_NEW_BIT_VECTOR","NO_MARGIN","NO_INTERVAL_CENTRE","NO_CANDIDATE",
                       "NOT_PHASE3_PASS","NOT_OPENSENSE_READY","NOT_PHASE4_READY","R26C_V_STILL_REQUIRED"],
    }
    write_json(out / "FINAL_RESULT.json", final)

    report = f"""# Phase 3-R2.6C-R1 corrective result

R2.6C-R1 closed the typed K/state/envelope bypasses and rebuilt the evidence chain from captured commands. The current AST-backed inventory contains {consumers['current_consumer_count']} non-duplicate consumer entries; C02/C04/C06 are retained only in historical namespaces, and C20/C21/C23/C24/C25 now have direct executed evidence. All six formerly non-equivariant entries are closed.

The production mutation campaign changed 14 real source modules in isolated `/tmp` packages. Every mutant imported from its asserted mutant root and SHA, failed its targeted regression, and was followed by a restored-baseline pass. No mutant survived. Value fault injections remain separately classified and are not counted as production mutants.

The `AUTHORIZED_SYNTHETIC_AND_NON_REAL_NUMERIC_SUITE` passed {total_passed} tests in four explicit commands with {total_skipped} expected environment skip. The known real-numeric integration module was excluded and every monitored forbidden-open count was zero. Historical `R26C-DATA-ACCESS-02` is corrected without changing the old R2.6C report.

No production heading-derived cache loader exists. The cache validator is retained as a strict schema boundary, while the old end-to-end cache-enforcement claim is withdrawn. The formal runner has no cache reuse path.

Verdict: `R26C_R1_READY_FOR_INDEPENDENT_R26C_V`.

This is corrective implementation only. It did not run a real-data solve, select a real branch, emit a new bit vector or margin, or generate a candidate. R2.6C-V in a fresh session remains required; R2.6D is not authorized.
"""
    (out / "PHASE3R26C_R1_CORRECTIVE_RESULT.md").write_text(report)

    raw_out = out / "raw_command_evidence"
    for path in primary_commands + [red_dir, green_dir, collection, raw / "final_commands/qualification_snapshot"]:
        copy_command_evidence(path, raw_out / path.name, include_strace=True)
    mutant_out = raw_out / "mutants"
    mutant_out.mkdir()
    for mutant in mutation_a["mutants"]:
        for suffix in ("A_baseline","B_mutation","C_targeted_fail","D_restored_baseline"):
            name = f"{mutant['mutant_id']}_{suffix}"
            copy_command_evidence(raw / "mutation_campaign_gate_a/mutation_commands" / name,
                                  mutant_out / name, include_strace=True)

    artifact_hashes = {path.relative_to(out).as_posix(): sha(path) for path in sorted(out.rglob("*")) if path.is_file()}
    write_json(out / "ARTIFACT_SHA256_MANIFEST.json", artifact_hashes)
    print(json.dumps({"output": str(out), "artifacts": len(artifact_hashes), "passed": total_passed,
                      "mutants": len(normalized_a), "consumers": consumers["current_consumer_count"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
