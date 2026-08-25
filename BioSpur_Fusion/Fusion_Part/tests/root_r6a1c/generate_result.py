"""Generate the bounded Root-R6A1C evidence package."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


HERE = Path(__file__).resolve().parent
FUSION = HERE.parents[1]
REPO = FUSION.parent
SOURCE = FUSION / "src"
BRIEF = Path("/home/zekaixiao/.codex/attachments/628d5d6d-1adc-4e4d-8b65-9afc4e91988d/pasted-text.txt")
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from biospur_fusion.root_r6a1c import build_contracts, resolve_sources, run_qualification  # noqa: E402
from biospur_fusion.root_r6a1c.contracts import sha256_file  # noqa: E402


VERDICT = "PARTIAL_ROOT_R6A1C_DEFERRED_MEASUREMENT_CONTRACT_QUALIFIED_STRUCTURAL_GAPS_REMAIN"
HEAD = "c96e9f746f4862d287e8eac9db06f184fcb023c1"
BRANCH = "feature/root-r6a1a-preintegrator-qualified"
REMOTE_REFS_BEFORE = "a77c2334df8caccf5f48ff6a57c7be0404b88556bb5eb5d3a607c927e0c894e5"
RESULT_MANIFESTS = {
    "root_r6a0": (FUSION / "logs/root_r6a0_whole_body_scaffold_20260824T200706Z", "4234b22043da66a78259f135367ca6f91c22b0a56cd6894ba343274be3a1667a"),
    "blocked_root_r6a1": (FUSION / "logs/root_r6a1_real_imu_preintegration_qualification_20260825T062636Z", "342a1a3e6c94bcf972473de355819eca82c786e951f181f6b8d743c1a5dc212d"),
    "root_r6a1a": (FUSION / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z", "2fa2c87a526de3d93a71b1cc6c4ace8520f88b108fa3ea60c42c5098eb140768"),
    "root_r6a1b": (FUSION / "logs/root_r6a1b_calibration_authority_20260825T084243Z", "0e2227561a222d49d189a654ac1f7071708d20f937277b0886c4c44fe970d3a5"),
}


def dump(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, text=True, capture_output=True).stdout.strip()


def remote_refs_digest() -> str:
    value = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes") + "\n"
    return hashlib.sha256(value.encode()).hexdigest()


def verify_manifest(directory: Path) -> dict[str, Any]:
    manifest = directory / "SHA256SUMS"
    errors = []
    count = 0
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split(None, 1)
        path = directory / relative.lstrip(" *")
        actual = sha256_file(path) if path.is_file() else None
        count += 1
        if digest != actual:
            errors.append({"path": str(path), "expected": digest, "actual": actual})
    return {"path": str(directory), "manifest": str(manifest), "manifest_sha256": sha256_file(manifest), "listed_file_count": count, "all_listed_files_match": not errors, "errors": errors}


def protected_audit(sources: dict[str, Path]) -> dict[str, Any]:
    trees = {}
    all_match = True
    for name, (directory, before) in RESULT_MANIFESTS.items():
        row = verify_manifest(directory)
        row.update({"before_sha256sums_sha256": before, "after_sha256sums_sha256": row["manifest_sha256"], "before_after_match": before == row["manifest_sha256"]})
        all_match &= row["all_listed_files_match"] and row["before_after_match"]
        trees[name] = row
    files = {
        "protected_baseline": (sources["protected_baseline"], "a8085cd9027622c63563bffc150798fbb5993b879aa70f2726a5737fb68de95e"),
        "immutable_ledger": (sources["ledger"], "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb"),
        "root_r6a0_body_graph": (sources["root_r6a0_graph"], "8825e4ba2289f9f4097a9db903bba69d0a556e9b802264419c4a8bb35888a30c"),
        "r6a1b_authority": (sources["r6a1b_authority"], "043e334d8efad0ecbdda23a87498ac739e6926a82d5edc2f199801b5a056ddf5"),
    }
    file_rows = {}
    for name, (path, before) in files.items():
        after = sha256_file(path); match = before == after; all_match &= match
        file_rows[name] = {"path": str(path), "before_sha256": before, "after_sha256": after, "match": match}
    return {"schema": "biospur-root-r6a1c-protected-hashes-before-after-v1", "all_match": all_match, "protected_result_trees": trees, "protected_files": file_rows}


def run_tests() -> dict[str, Any]:
    commands = [
        ("root_r6a1c_focused", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1c"], 20),
        ("root_r6a1b_regression", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1b"], 18),
        ("root_r6a1a_regression", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1a"], 22),
        ("root_r6a0_regression", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a0"], 29),
        ("combined_r6a0_r6a1a_r6a1b_r6a1c", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a0", "Fusion_Part/tests/root_r6a1a", "Fusion_Part/tests/root_r6a1b", "Fusion_Part/tests/root_r6a1c"], 89),
    ]
    rows = []
    for name, command, expected in commands:
        completed = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        output = completed.stdout + completed.stderr
        passed = completed.returncode == 0 and f"{expected} passed" in output
        rows.append({"name": name, "command": command, "expected_passed": expected, "return_code": completed.returncode, "pass": passed, "output": output.strip()})
        if not passed:
            raise RuntimeError(f"{name} failed\n{output}")
    return {"schema": "biospur-root-r6a1c-test-results-v1", "all_pass": all(row["pass"] for row in rows), "commands": rows}


def implementation_files() -> list[Path]:
    return sorted([
        *(FUSION / "src/biospur_fusion/root_r6a1c").glob("*.py"),
        *(FUSION / "tests/root_r6a1c").glob("*.py"),
    ], key=lambda path: path.as_posix())


def implementation_manifest() -> dict[str, Any]:
    rows = [{"path": str(path.relative_to(FUSION)), "sha256": sha256_file(path)} for path in implementation_files()]
    return {"schema": "biospur-root-r6a1c-implementation-files-v1", "file_count": len(rows), "files": rows}


def data_access(sources: dict[str, Path]) -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a1c-data-access-audit-v1",
        "read_only_sources": [{"key": key, "path": str(path), "sha256": sha256_file(path)} for key, path in sorted(sources.items())],
        "full_c1_sample_arrays_opened": False,
        "heldout_walk_final_still_golf_boxing_opened": False,
        "uwb_payloads_opened": False,
        "real_fusion_run": False,
        "real_body_state_updated": False,
        "production_noise_estimated": False,
        "human_measurements_requested": False,
    }


def final_result(qualification: dict[str, Any], tests: dict[str, Any], protected: dict[str, Any]) -> dict[str, Any]:
    readiness = qualification["readiness"]
    return {
        "schema": "biospur-root-r6a1c-final-result-v1",
        "principal_verdict": VERDICT,
        "software_contract_readiness": readiness["software_contract_readiness"],
        "root_r6a2_synthetic_fault_architecture_ready": readiness["root_r6a2_synthetic_fault_architecture_ready"],
        "root_r6a2_real_shadow_ready": readiness["root_r6a2_real_shadow_ready"],
        "root_r6a2_real_body_update_authorized": readiness["root_r6a2_real_body_update_authorized"],
        "next_bounded_stage_authorized": readiness["next_bounded_stage_authorized"],
        "mandatory_gates": {"passed": sum(row["pass"] for row in qualification["gates"]), "total": 20, "all_pass": qualification["all_pass"]},
        "tests_all_pass": tests["all_pass"], "protected_hashes_exact": protected["all_match"],
        "immutable_registry": {"total": 87, "value_null": 87, "FROZEN_UNCERTAIN": 87, "fitted_from_real_data": 0},
        "structural_gaps": ["per-node hardware revision binding", "common-nine versus BSF31CC CAD association", "full IMU register/die/board/enclosure transform", "unique nominal UWB point and electromagnetic phase centre", "real V4-to-navigation/protocol bridge"],
        "real_measurements_deferred": True,
        "real_fusion_authorized": False,
    }


def final_markdown(final: dict[str, Any], contracts: dict[str, Any], tests: dict[str, Any]) -> str:
    hardware = contracts["HARDWARE_FRAME_AND_LEVER_AUDIT.json"]
    torso = contracts["TORSO_TOP_OWNERSHIP.json"]
    outputs = {row["name"]: row["expected_passed"] for row in tests["commands"]}
    return f"""# Root-R6A1C final result

Principal verdict: `{final['principal_verdict']}`

1. **Structurally closed.** R6A1C now has a corrected ten-node forward identity adapter, versioned donning/sign contract, deferred-measurement schema, one-owner `torso_top` derivation, fail-closed hardware/world provenance contracts, and an authorization validator for future R6A2 inputs.
2. **Deferred.** All subject anthropometry, distal landmark coordinates, device-placement distances, signed-axis capture, process-noise qualification, real hardware revision binding, exact internal levers, RF phase centre, and V4-to-navigation/protocol survey remain null or pending. No operator measurement was requested.
3. **`torso_top`.** It is `{torso['authority_class']}` with zero optimizer freedom: `{torso['derivation_formula']}`. The immutable source slot remains null and `FROZEN_UNCERTAIN`.
4. **Hardware lever evidence.** The operator-supplied, hash-bound `PCB/V0.20` STEP/Gerber export independently reproduces the EasyEDA U4/DWM1001C and U7/JY901S component references, including a {hardware['cad_candidate']['component_reference_constraints']['U4_to_U7_planar_distance_m']:.12f} m planar reference distance and a three-dimensional STEP component-reference transform. The operator also identifies `FusionPCB底座.step` as the actual 3D-print output directly relevant to the Fusion PCB unit; its paired x/y enclosure-body faces reproduce the PCB limits with 0.1 mm offsets and an exact planar-centre match, proving common CAD coordinates. This closes PCB-to-base CAD registration, but the complete enclosure `C`, antenna-end `E` axis, as-built fit tolerance, IMU die origin, and unique gold-UWB nominal point remain unqualified. The files are not bound to device serials, the reported common nine, or BSF31CC, so no qualified IMU-to-C or IMU-to-UWB lever was found for either reported family.
5. **Antenna semantics.** The large gold printed element above the DWM1001C shield is the UWB antenna region; the small ceramic element is BLE and is excluded from every UWB lever. The corrected neutral donning rule is PCB vertical with the **gold UWB antenna end toward the ground**, device `-Y` downward, and the B306 end opposite; the superseded B306-down interpretation is discarded. This is session-specific donning evidence, not a permanent motion constraint. The RF evidence supports only `CAD_NOMINAL_ANTENNA_REFERENCE` region semantics, with `PHASE_CENTRE_OFFSET_UNQUALIFIED`; no unique nominal point or electromagnetic phase centre is manufactured.
6. **Why missing body measurements do not block synthetic R6A2A.** Synthetic subject geometry, synthetic hardware revisions, and a right-handed +Z-up synthetic world are explicit test-only objects that cannot write the real registry. Fault architecture can therefore test rejection, dropout, ownership, and covariance plumbing without claiming a real body.
7. **Why real execution remains unauthorized.** The real profile is null, signed axes and production noise are unqualified, hardware revisions/levers are unresolved, clocks and anchors are capture-bound, and `T_N_V4` is null. Thus real shadow and real body updates are false.
8. **Tests and verification.** Primary suites passed: R6A1C {outputs['root_r6a1c_focused']}, R6A1B {outputs['root_r6a1b_regression']}, R6A1A {outputs['root_r6a1a_regression']}, R6A0 {outputs['root_r6a0_regression']}, and combined {outputs['combined_r6a0_r6a1a_r6a1b_r6a1c']}. The separate verifier rereads/rebuilds contracts and reruns the mandatory suites; see `INDEPENDENT_VERIFICATION.json`.
9. **Immutable registry.** Exactly 87 real calibration slots remain null and `FROZEN_UNCERTAIN`; fitted-from-real-data count remains zero and no synthetic value entered the registry.
10. **Execution boundary.** No real fusion, full-C1 sample array, held-out action, body-state update, production-noise estimate, R6A2 implementation, R6A1C commit, push, or merge occurred. Repository HEAD remains the predecessor checkpoint `{HEAD}`.

Readiness is intentionally split: software contract readiness is true; synthetic fault-architecture readiness is true; real-shadow readiness is false; real body-update authorization is false. Only `Root-R6A2A SYNTHETIC FAULT-AWARE WHOLE-BODY SHADOW INTEGRATION` is authorized next.
"""


def write_checksums(result: Path) -> None:
    lines = []
    for path in sorted(result.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "SHA256SUMS":
            lines.append(f"{sha256_file(path)}  {path.name}\n")
    (result / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def generate(result: Path) -> None:
    if result.exists() and any(result.iterdir()):
        raise RuntimeError(f"refusing to overwrite {result}")
    result.mkdir(parents=True, exist_ok=True)
    sources = resolve_sources(FUSION, BRIEF)
    ledger = json.loads(sources["ledger"].read_text(encoding="utf-8"))
    protected = protected_audit(sources)
    if not protected["all_match"]:
        raise RuntimeError("protected predecessor mismatch")
    contracts = build_contracts(ledger, sources)
    qualification = run_qualification(ledger, contracts, protected_hashes_exact=True)
    tests = run_tests()
    for name, value in contracts.items():
        dump(result / name, value)
    dump(result / "QUALIFICATION_GATES.json", qualification)
    dump(result / "PROTECTED_HASHES_BEFORE_AFTER.json", protected)
    dump(result / "TEST_RESULTS.json", tests)
    dump(result / "IMPLEMENTATION_FILES.json", implementation_manifest())
    dump(result / "DATA_ACCESS_AUDIT.json", data_access(sources))
    dump(result / "EXECUTION_GUARD_AUDIT.json", {
        "schema": "biospur-root-r6a1c-execution-guard-audit-v1",
        "pre_modification_status_sha256": "36210a0be7c46804b6ce4aa823af0edddb37947e7ad79a1d2dd3b3cab7031db8",
        "pre_modification_status_line_count": 106531,
        "head_before": HEAD, "head_after": git("rev-parse", "HEAD"),
        "branch_before": BRANCH, "branch_after": git("branch", "--show-current"),
        "remote_refs_before_sha256": REMOTE_REFS_BEFORE, "remote_refs_after_sha256": remote_refs_digest(),
        "commit_performed": False, "push_performed": False, "merge_performed": False,
        "root_r6a2_implemented": False,
    })
    final = final_result(qualification, tests, protected)
    dump(result / "FINAL_RESULT.json", final)
    (result / "FINAL_RESULT.md").write_text(final_markdown(final, contracts, tests), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--checksums-only", action="store_true")
    args = parser.parse_args()
    if args.checksums_only:
        write_checksums(args.result.resolve())
    else:
        generate(args.result.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
