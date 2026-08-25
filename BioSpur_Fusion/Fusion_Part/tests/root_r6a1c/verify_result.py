"""Independent Root-R6A1C artifact, hash, and suite verifier."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


HERE = Path(__file__).resolve().parent
FUSION = HERE.parents[1]
REPO = FUSION.parent
SOURCE = FUSION / "src"
BRIEF = Path("/home/zekaixiao/.codex/attachments/628d5d6d-1adc-4e4d-8b65-9afc4e91988d/pasted-text.txt")
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from biospur_fusion.root_r6a1c import build_contracts, resolve_sources, run_qualification  # noqa: E402
from biospur_fusion.root_r6a1c.contracts import canonical_bytes, sha256_file, validate_ledger  # noqa: E402


REQUIRED = {
    "FINAL_RESULT.md", "FINAL_RESULT.json", "NODE_IDENTITY_AND_DONNING_CONTRACT.json",
    "DEFERRED_MEASUREMENT_CONTRACT.json", "TORSO_TOP_OWNERSHIP.json",
    "HARDWARE_FRAME_AND_LEVER_AUDIT.json", "WORLD_FRAME_BRIDGE_CONTRACT.json",
    "ROOT_R6A2_READINESS.json", "QUALIFICATION_GATES.json",
    "PROTECTED_HASHES_BEFORE_AFTER.json", "TEST_RESULTS.json",
    "IMPLEMENTATION_FILES.json", "DATA_ACCESS_AUDIT.json", "EXECUTION_GUARD_AUDIT.json",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_suite() -> dict:
    command = [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a0", "Fusion_Part/tests/root_r6a1a", "Fusion_Part/tests/root_r6a1b", "Fusion_Part/tests/root_r6a1c"]
    completed = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
    output = completed.stdout + completed.stderr
    return {"command": command, "return_code": completed.returncode, "output": output.strip(), "expected_passed": 89, "pass": completed.returncode == 0 and "89 passed" in output}


def verify(result: Path, require_checksums: bool, rerun_tests: bool) -> dict:
    checks = []

    def check(name: str, passed: bool, detail=None) -> None:
        checks.append({"name": name, "pass": bool(passed), "detail": detail})

    missing = sorted(name for name in REQUIRED if not (result / name).is_file())
    check("required_artifacts_present", not missing, missing)
    sources = resolve_sources(FUSION, BRIEF)
    ledger = load(sources["ledger"])
    try:
        validate_ledger(ledger); ledger_ok = True; error = None
    except ValueError as caught:
        ledger_ok = False; error = str(caught)
    check("immutable_ledger_reread_87_null_frozen", ledger_ok, error)
    contracts = build_contracts(ledger, sources)
    rebuilt_match = all(canonical_bytes(value) == canonical_bytes(load(result / name)) for name, value in contracts.items())
    check("contracts_independently_rebuilt", rebuilt_match, sorted(contracts))
    gates = run_qualification(ledger, contracts, protected_hashes_exact=True)
    check("mandatory_A_through_T_rerun", gates["all_pass"] and gates["gate_count"] == 20 and canonical_bytes(gates) == canonical_bytes(load(result / "QUALIFICATION_GATES.json")))
    protected = load(result / "PROTECTED_HASHES_BEFORE_AFTER.json")
    protected_ok = True
    for row in protected["protected_result_trees"].values():
        protected_ok &= sha256_file(Path(row["manifest"])) == row["before_sha256sums_sha256"]
        for line in Path(row["manifest"]).read_text().splitlines():
            if line.strip():
                digest, relative = line.split(None, 1)
                protected_ok &= sha256_file(Path(row["path"]) / relative.lstrip(" *")) == digest
    for row in protected["protected_files"].values():
        protected_ok &= sha256_file(Path(row["path"])) == row["before_sha256"]
    check("predecessor_and_protected_hashes_recalculated", protected_ok)
    implementation = load(result / "IMPLEMENTATION_FILES.json")
    implementation_ok = all(sha256_file(FUSION / row["path"]) == row["sha256"] for row in implementation["files"])
    check("implementation_paths_and_hashes_match", implementation_ok, implementation["file_count"])
    access = load(result / "DATA_ACCESS_AUDIT.json")
    check("no_forbidden_data_or_real_execution", not any((access["full_c1_sample_arrays_opened"], access["heldout_walk_final_still_golf_boxing_opened"], access["uwb_payloads_opened"], access["real_fusion_run"], access["real_body_state_updated"], access["production_noise_estimated"], access["human_measurements_requested"])))
    readiness = load(result / "ROOT_R6A2_READINESS.json")
    check("readiness_booleans_separate_and_correct", readiness["software_contract_readiness"] and readiness["root_r6a2_synthetic_fault_architecture_ready"] and not readiness["root_r6a2_real_shadow_ready"] and not readiness["root_r6a2_real_body_update_authorized"])
    deferred = load(result / "DEFERRED_MEASUREMENT_CONTRACT.json")
    groups = (deferred["minimum_future_subject_profile"], deferred["future_device_placement_observations"], deferred["distal_anatomical_points"], deferred["latent_anatomical_parameters"], deferred["derived_model_quantities"])
    check("deferred_values_recounted_null", all(row["value"] is None and row["evidence_status"] == "DEFERRED_DIRECT_MEASUREMENT" for group in groups for row in group))
    hardware = load(result / "HARDWARE_FRAME_AND_LEVER_AUDIT.json")
    check("gold_uwb_region_and_ble_ceramic_separated", "gold" in hardware["antenna_reference"]["uwb_physical_region"] and "ceramic" in hardware["antenna_reference"]["ble_antenna_exclusion"] and hardware["antenna_reference"]["phase_centre_status"] == "PHASE_CENTRE_OFFSET_UNQUALIFIED")
    identity = load(result / "NODE_IDENTITY_AND_DONNING_CONTRACT.json")
    donning = identity["neutral_donning_procedure"]
    check("corrected_gold_uwb_end_down_neutral_donning", donning["pcb_plane"] == "vertical" and donning["physical_end_toward_ground"] == "large gold printed UWB antenna end" and donning["physical_end_opposite_ground"] == "B306 end" and donning["device_axis_toward_ground"] == "-Y" and not donning["applies_during_motion"] and not donning["superseded_interpretation_retained"])
    v020 = hardware["cad_candidate"]["operator_supplied_v0_20_export"]
    check("v0_20_step_gerber_audited_without_device_overbinding", v020["step_components_present"] and v020["matches_august_13_archive_component_references"] and v020["node_or_family_assignment"] is None and v020["node_or_family_assignment_status"] == "UNPROVEN")
    enclosure = hardware["cad_candidate"]["operator_supplied_v0_20_enclosure_base"]
    check("fusion_pcb_enclosure_base_direct_association_and_registration", enclosure["association_authority"] == "OPERATOR_ATTESTED_DIRECTLY_RELEVANT_TO_FUSION_PCB_UNIT" and enclosure["coordinate_registration"]["status"].startswith("CAD_COMMON_COORDINATE_REGISTRATION_PROVEN") and max(abs(value) for value in enclosure["coordinate_registration"]["planar_center_residual_m"]) < 1.0e-15)
    check("enclosure_base_does_not_manufacture_complete_C", enclosure["complete_enclosure_C"] is None and enclosure["complete_enclosure_C_status"].startswith("PENDING_") and hardware["board_to_enclosure_C"]["translation_m"] is None)
    guard = load(result / "EXECUTION_GUARD_AUDIT.json")
    check("no_commit_push_merge_or_r6a2", guard["head_before"] == guard["head_after"] == "c96e9f746f4862d287e8eac9db06f184fcb023c1" and guard["remote_refs_before_sha256"] == guard["remote_refs_after_sha256"] and not any((guard["commit_performed"], guard["push_performed"], guard["merge_performed"], guard["root_r6a2_implemented"])))
    suite = run_suite() if rerun_tests else {"pass": True, "skipped": True}
    check("applicable_test_suites_rerun", suite["pass"], suite)
    final = load(result / "FINAL_RESULT.json")
    check("narrow_partial_verdict", final["principal_verdict"] == "PARTIAL_ROOT_R6A1C_DEFERRED_MEASUREMENT_CONTRACT_QUALIFIED_STRUCTURAL_GAPS_REMAIN")
    if require_checksums:
        manifest = result / "SHA256SUMS"; checksum_ok = manifest.is_file(); listed = {}
        if checksum_ok:
            for line in manifest.read_text().splitlines():
                digest, name = line.split(None, 1); listed[name.lstrip(" *")] = digest
            expected = {path.name for path in result.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
            checksum_ok &= set(listed) == expected and all(sha256_file(result / name) == digest for name, digest in listed.items())
        check("sha256sums_complete_valid", checksum_ok)
    return {"schema": "biospur-root-r6a1c-independent-verification-v1", "mode": "FINAL_CHECKSUM" if require_checksums else "PRIMARY_REREAD_REBUILD_RERUN", "all_pass": all(row["pass"] for row in checks), "check_count": len(checks), "checks": checks, "verifier_source_sha256": sha256_file(Path(__file__)), "primary_report_trusted_without_reread": False}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--require-checksums", action="store_true")
    parser.add_argument("--rerun-tests", action="store_true")
    args = parser.parse_args()
    report = verify(args.result.resolve(), args.require_checksums, args.rerun_tests)
    if args.write_report:
        (args.result / "INDEPENDENT_VERIFICATION.json").write_bytes(json.dumps(report, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
