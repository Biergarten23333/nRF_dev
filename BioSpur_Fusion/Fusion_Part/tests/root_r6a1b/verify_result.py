"""Independent, fail-closed verifier for a Root-R6A1B result directory."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
FUSION = HERE.parents[1]
REPO = FUSION.parent
SOURCE = FUSION / "src"
BRIEF = Path("/home/zekaixiao/.codex/attachments/bea65fa4-48e2-4e7a-a765-b44d2dd83868/pasted-text.txt")
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from biospur_fusion.root_r6a1b import build_contracts, resolve_sources, run_synthetic_qualification  # noqa: E402
from biospur_fusion.root_r6a1b.contracts import canonical_bytes, sha256_file, validate_source_ledger  # noqa: E402


REQUIRED = {
    "FINAL_RESULT.md", "FINAL_RESULT.json", "R6A1A_CHECKPOINT_AUDIT.json",
    "CALIBRATION_AUTHORITY_PLAN.json", "CALIBRATION_DEPENDENCY_GRAPH.json",
    "MINIMAL_IDENTIFIABLE_STATE.json", "FRAME_CHAIN_CONTRACT.json",
    "SESSION_DONNING_CONTRACT.json", "SKIN_SLIP_STATE_CONTRACT.json",
    "IMPORT_PROVENANCE_AUDIT.json", "NOISE_CAPTURE_PROTOCOL.json",
    "NOISE_CAPTURE_PROTOCOL.md", "DONNING_AND_SKIN_SLIP_CAPTURE_PROTOCOL.json",
    "DONNING_AND_SKIN_SLIP_CAPTURE_PROTOCOL.md", "SYNTHETIC_OBSERVABILITY_AUDIT.json",
    "TEST_RESULTS.json", "PROTECTED_HASHES_BEFORE_AFTER.json",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, text=True, capture_output=True).stdout.strip()


def verify(result: Path, require_checksums: bool) -> dict:
    checks = []

    def check(name: str, passed: bool, detail=None) -> None:
        checks.append({"name": name, "pass": bool(passed), "detail": detail})

    missing = sorted(name for name in REQUIRED if not (result / name).is_file())
    check("required_artifacts_present", not missing, missing)
    sources = resolve_sources(FUSION, BRIEF)
    ledger = load(sources["ledger"])
    try:
        validate_source_ledger(ledger)
        immutable = True
    except Exception as error:  # fail-closed evidence
        immutable = False
        ledger_error = str(error)
    check("immutable_ledger_reread_exact_87_null_frozen", immutable, None if immutable else ledger_error)
    plan = load(result / "CALIBRATION_AUTHORITY_PLAN.json")
    ledger_ids = {row["slot_id"] for row in ledger["slots"]}
    plan_ids = [row["slot_id"] for row in plan["slots"]]
    check("all_87_slots_exactly_once", len(plan_ids) == len(set(plan_ids)) == 87 and set(plan_ids) == ledger_ids)
    check("exact_category_counts", plan["per_category_counts"] == ledger["per_category_counts"] and sum(plan["per_category_counts"].values()) == 87)
    check("source_values_and_statuses_unchanged", all(row["source_value"] is None and row["source_status"] == "FROZEN_UNCERTAIN" for row in plan["slots"]))
    graph = load(result / "CALIBRATION_DEPENDENCY_GRAPH.json")
    graph_nodes = {row["id"] for row in graph["nodes"]}
    references_ok = all(edge["dependent"] in graph_nodes and edge["prerequisite"] in graph_nodes for edge in graph["edges"])
    references_ok &= all(item in graph_nodes for row in plan["slots"] for item in row["derived_from"])
    check("dependency_and_derivation_references_resolve", references_ok)

    rebuilt = build_contracts(ledger, FUSION, sources)
    deterministic_files = set(rebuilt)
    artifacts_match = all(canonical_bytes(rebuilt[name]) == canonical_bytes(load(result / name)) for name in deterministic_files)
    check("primary_contracts_independently_rebuilt", artifacts_match, sorted(deterministic_files))
    rerun = run_synthetic_qualification(ledger, rebuilt, protected_hashes_match=True)
    stored = load(result / "SYNTHETIC_OBSERVABILITY_AUDIT.json")
    check("synthetic_tests_rerun_all_18", rerun["all_pass"] and rerun["gate_count"] == 18 and canonical_bytes(rerun) == canonical_bytes(stored))
    protected = load(result / "PROTECTED_HASHES_BEFORE_AFTER.json")
    protected_now = True
    for row in protected["protected_result_trees"].values():
        protected_now &= sha256_file(Path(row["manifest"])) == row["before_sha256sums_sha256"]
        for line in Path(row["manifest"]).read_text().splitlines():
            if not line.strip():
                continue
            digest, relative = line.split(None, 1)
            protected_now &= sha256_file(Path(row["path"]) / relative.lstrip(" *")) == digest
    for row in protected["protected_files"].values():
        protected_now &= sha256_file(Path(row["path"])) == row["before_sha256"]
    check("protected_hashes_independently_verified", protected_now)
    access = load(result / "DATA_ACCESS_AUDIT.json")
    check("no_forbidden_dataset_opened", not access["uwb_payloads_opened"] and not access["full_c1_opened"] and not access["heldout_opened"] and not access["raw_imu_samples_opened"])
    executable_guard_ok = True
    forbidden_functions = {"update_state", "optimize", "fit_calibration", "run_fusion", "load_c1", "load_heldout"}
    forbidden_import_fragments = {"root_r6a2", ".data", ".factors", "datasets"}
    for path in (FUSION / "src/biospur_fusion/root_r6a1b").glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in forbidden_functions:
                executable_guard_ok = False
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                executable_guard_ok &= not any(fragment in module for fragment in forbidden_import_fragments)
            if isinstance(node, ast.Import):
                executable_guard_ok &= all(not any(fragment in alias.name for fragment in forbidden_import_fragments) for alias in node.names)
    check("no_real_state_update", not access["real_body_state_updated"] and executable_guard_ok)
    r6a1b_stage_paths = [
        FUSION / "src/biospur_fusion/root_r6a1b",
        FUSION / "tests/root_r6a1b",
        result,
    ]
    check("no_root_r6a2_code_added", not access["root_r6a2_started"] and all("root_r6a2" not in str(path).lower() for path in r6a1b_stage_paths))
    guard = load(result / "EXECUTION_GUARD_AUDIT.json")
    check("no_push_or_merge", guard["checkpoint_head_unchanged"] and guard["remote_refs_unchanged"] and not guard["push_performed"] and not guard["merge_performed"] and git("rev-parse", "HEAD") == guard["head"])
    final = load(result / "FINAL_RESULT.json")
    check("principal_verdict_is_permitted_partial", final["principal_verdict"] == "PARTIAL_ROOT_R6A1B_AUTHORITY_QUALIFIED_PROVENANCE_GAPS_REMAIN")
    if require_checksums:
        manifest = result / "SHA256SUMS"
        checksum_ok = manifest.is_file()
        if checksum_ok:
            listed = {}
            for line in manifest.read_text().splitlines():
                digest, name = line.split(None, 1)
                listed[name.lstrip(" *")] = digest
            expected_names = {path.name for path in result.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
            checksum_ok &= set(listed) == expected_names
            checksum_ok &= all(sha256_file(result / name) == digest for name, digest in listed.items())
        check("sha256sums_complete_and_valid", checksum_ok)
    return {
        "schema": "biospur-root-r6a1b-independent-verification-v1",
        "mode": "CHECK_WITH_FINAL_SHA256SUMS" if require_checksums else "PRIMARY_REREAD_AND_REBUILD",
        "all_pass": all(row["pass"] for row in checks),
        "check_count": len(checks),
        "checks": checks,
        "verifier_source_sha256": sha256_file(Path(__file__)),
        "primary_report_trusted_without_reread": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--require-checksums", action="store_true")
    args = parser.parse_args()
    report = verify(args.result.resolve(), args.require_checksums)
    if args.write_report:
        (args.result / "INDEPENDENT_VERIFICATION.json").write_bytes(json.dumps(report, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
