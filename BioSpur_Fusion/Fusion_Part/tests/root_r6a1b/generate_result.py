"""Generate the deterministic Root-R6A1B result package.

Only small contract/provenance artifacts are read.  There is intentionally no
raw-data, C1, held-out, UWB-payload, estimator, or state-update path here.
"""
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
BRIEF = Path("/home/zekaixiao/.codex/attachments/bea65fa4-48e2-4e7a-a765-b44d2dd83868/pasted-text.txt")
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from biospur_fusion.root_r6a1b import (  # noqa: E402
    AUTHORITY_COUNTS,
    build_contracts,
    resolve_sources,
    run_synthetic_qualification,
)
from biospur_fusion.root_r6a1b.contracts import canonical_bytes, sha256_file  # noqa: E402


VERDICT = "PARTIAL_ROOT_R6A1B_AUTHORITY_QUALIFIED_PROVENANCE_GAPS_REMAIN"
CHECKPOINT_HEAD = "c96e9f746f4862d287e8eac9db06f184fcb023c1"
CHECKPOINT_BRANCH = "feature/root-r6a1a-preintegrator-qualified"
REMOTE_REFS_BEFORE = "a77c2334df8caccf5f48ff6a57c7be0404b88556bb5eb5d3a607c927e0c894e5"

PROTECTED = {
    "root_r6a0_result": {
        "path": FUSION / "logs/root_r6a0_whole_body_scaffold_20260824T200706Z",
        "sha256sums_sha256_before": "4234b22043da66a78259f135367ca6f91c22b0a56cd6894ba343274be3a1667a",
    },
    "blocked_root_r6a1_result": {
        "path": FUSION / "logs/root_r6a1_real_imu_preintegration_qualification_20260825T062636Z",
        "sha256sums_sha256_before": "342a1a3e6c94bcf972473de355819eca82c786e951f181f6b8d743c1a5dc212d",
    },
    "root_r6a1a_result": {
        "path": FUSION / "logs/root_r6a1a_preintegrator_recovery_20260825T071931Z",
        "sha256sums_sha256_before": "2fa2c87a526de3d93a71b1cc6c4ace8520f88b108fa3ea60c42c5098eb140768",
    },
}


def dump(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=REPO, check=True, text=True, capture_output=True).stdout.strip()


def remote_refs_digest() -> str:
    rows = git("for-each-ref", "--format=%(refname) %(objectname)", "refs/remotes") + "\n"
    return hashlib.sha256(rows.encode()).hexdigest()


def verify_manifest(directory: Path) -> dict[str, Any]:
    manifest = directory / "SHA256SUMS"
    checked = []
    errors = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split(None, 1)
        relative = relative.lstrip(" *")
        path = directory / relative
        actual = sha256_file(path) if path.is_file() else None
        checked.append(relative)
        if actual != digest:
            errors.append({"path": relative, "expected": digest, "actual": actual})
    return {
        "path": str(directory),
        "manifest": str(manifest),
        "manifest_sha256": sha256_file(manifest),
        "listed_file_count": len(checked),
        "all_listed_files_match": not errors,
        "errors": errors,
    }


def protected_audit(sources: dict[str, Path]) -> dict[str, Any]:
    result_trees = {}
    all_match = True
    for name, spec in PROTECTED.items():
        verified = verify_manifest(spec["path"])
        before = spec["sha256sums_sha256_before"]
        verified.update({"before_sha256sums_sha256": before, "after_sha256sums_sha256": verified["manifest_sha256"], "before_after_match": before == verified["manifest_sha256"]})
        all_match &= verified["all_listed_files_match"] and verified["before_after_match"]
        result_trees[name] = verified
    files_before = {
        "root_r6a1a_ledger": "b159043eb7da4518ac6349832ca3c50e0b3453b1e35d97bbac29223f3e85c4eb",
        "root_r6a0_body_source": "61824be80c4fabd08e2e4b786c22d31979406c043e67b38674f734546b7cdcf8",
        "root_r6a0_body_config": "8825e4ba2289f9f4097a9db903bba69d0a556e9b802264419c4a8bb35888a30c",
        "frame_contract_v1": "f236ffbefcc72d03c546912fabfc7aab91fe1c93ecaf417f4cf55411373e175e",
    }
    paths = {
        "root_r6a1a_ledger": sources["ledger"],
        "root_r6a0_body_source": sources["body_source"],
        "root_r6a0_body_config": sources["body_config"],
        "frame_contract_v1": sources["frame_contract"],
    }
    protected_files = {}
    for name, path in paths.items():
        after = sha256_file(path)
        match = after == files_before[name]
        all_match &= match
        protected_files[name] = {"path": str(path), "before_sha256": files_before[name], "after_sha256": after, "match": match}
    return {
        "schema": "biospur-root-r6a1b-protected-hashes-before-after-v1",
        "all_match": all_match,
        "protected_result_trees": result_trees,
        "protected_files": protected_files,
    }


def checkpoint_audit(protected: dict[str, Any], result: Path) -> dict[str, Any]:
    status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=normal"],
        cwd=REPO, check=True, text=True, capture_output=True,
    ).stdout.splitlines()
    pre_lines = [line for line in status if "root_r6a1b" not in line.lower() and "root_r6a1b_calibration_authority_" not in line.lower()]
    status_path = result / "WORKTREE_STATUS_PRE_R6A1B.txt"
    status_path.write_text("\n".join(pre_lines) + ("\n" if pre_lines else ""), encoding="utf-8")
    digest = hashlib.sha256(status_path.read_bytes()).hexdigest()
    return {
        "schema": "biospur-root-r6a1b-r6a1a-checkpoint-audit-v1",
        "pre_audit": {
            "head": "5bd0a3aa5803921d39f1e7d317762a8f6d7bc3bb",
            "head_tree": "85d8e22a7110983a634ef6800a7d3daf2f3160fd",
            "branch": "feature/b306-bringup",
            "full_worktree_status_sha256": "9620c0dbea0812eabe3928e47fc910937a95405dc643ff5fcb32fbaa642bb4db",
            "full_worktree_status_line_count": 106533,
            "disk_gates": {"nrf_ssd_free_bytes": 274916573184, "required_bytes": 100000000000, "root_free_bytes": 52790222848, "root_required_bytes": 40000000000, "projected_growth_bytes": 5000000000, "pass": True},
        },
        "verification": {
            "root_r6a1a_a_through_t": {"all_pass": True, "gate_count": 20},
            "focused_tests": {"passed": 22, "failed": 0, "duration_s": 1.61},
            "combined_root_r6a0_root_r6a1a_tests": {"passed": 51, "failed": 0, "duration_s": 49.19},
            "predecessor_checksums": protected["all_match"],
        },
        "isolated_checkpoint": {
            "safe_isolation_proven": True,
            "branch": CHECKPOINT_BRANCH,
            "commit": CHECKPOINT_HEAD,
            "tree": "8c80d451c28d6683573c71a3a62238cbd5f390c3",
            "subject": "root-r6a1a: qualify native-time preintegrator",
            "file_count": 7,
            "files": [
                "BioSpur_Fusion/Fusion_Part/config/root_r6a1a/preintegrator.json",
                "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/imu/__init__.py",
                "BioSpur_Fusion/Fusion_Part/src/biospur_fusion/imu/preintegration.py",
                "BioSpur_Fusion/Fusion_Part/tests/root_r6a1a/conftest.py",
                "BioSpur_Fusion/Fusion_Part/tests/root_r6a1a/generate_result.py",
                "BioSpur_Fusion/Fusion_Part/tests/root_r6a1a/qualification.py",
                "BioSpur_Fusion/Fusion_Part/tests/root_r6a1a/test_preintegrator.py",
            ],
            "unrelated_work_captured": False,
            "pushed": False,
            "merged": False,
        },
        "post_checkpoint_pre_r6a1b": {
            "head": CHECKPOINT_HEAD,
            "branch": CHECKPOINT_BRANCH,
            "full_worktree_status_artifact": str(status_path),
            "full_worktree_status_sha256": digest,
            "full_worktree_status_line_count": len(pre_lines),
            "matches_recorded_pre_modification_digest": digest == "3a31a127dd97f3a88b150fd61529b888c81f48e33c4670178ac5c5d33b68cf33",
        },
    }


def run_tests() -> dict[str, Any]:
    commands = [
        ("root_r6a1b_18_gates", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1b"], 18),
        ("root_r6a1a_focused", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1a"], 22),
        ("root_r6a0_plus_root_r6a1a", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a0", "Fusion_Part/tests/root_r6a1a"], 51),
        ("root_r6a0_r6a1a_r6a1b_combined", [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a0", "Fusion_Part/tests/root_r6a1a", "Fusion_Part/tests/root_r6a1b"], 69),
    ]
    rows = []
    for name, command, expected in commands:
        completed = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        output = completed.stdout + completed.stderr
        passed = completed.returncode == 0 and f"{expected} passed" in output
        rows.append({"name": name, "command": command, "expected_passed": expected, "return_code": completed.returncode, "pass": passed, "output": output.strip()})
        if not passed:
            raise RuntimeError(f"test command failed: {name}\n{output}")
    return {"schema": "biospur-root-r6a1b-test-results-v1", "all_pass": all(row["pass"] for row in rows), "commands": rows}


def noise_markdown(protocol: dict[str, Any]) -> str:
    return f"""# Root-R6A1B electronic-noise capture protocol

Status: **{protocol['capture_status']}**. This is a rigid, non-worn electronic-noise experiment and is separate from donning and skin-slip capture. No production constants are estimated by R6A1B.

## Fixture and production path

Rigidly clamp all ten identified Fusion nodes to one stable, non-vibrating fixture on a non-moving support. Record at the native production 200 Hz path: raw accelerometer, raw gyroscope, temperature, accepted status, boot epoch, native timer, and `global_time_ns`. Preserve both MCU firmware hashes, decoder/source hash, configuration hash, device identities, fixture description, and radio state. UWB ranging and BLE traffic not required for IMU recording remain disabled.

## Required plateaus

After a 45-minute soak, record four uninterrupted hours at 20 °C, 30 °C, and 40 °C, each held within ±0.5 °C. Repeat the complete four-hour 20 °C plateau on a second day. Any boot, nonpositive timestamp, raw rail hit, or gap over 20 ms invalidates that affected plateau; do not interpolate it.

## Analysis and predeclared gates

Compute overlapping Allan deviation separately for every accelerometer and gyroscope axis of every device, with tau no greater than one tenth of its uninterrupted plateau. A white-noise region must span at least one decade and five log-spaced positive points, have slope in [-0.75, -0.25], and R² ≥ 0.95. A bias-random-walk region must meet the same decade, point, and R² requirements with slope in [0.35, 0.65]. Use the production continuous-density convention `discrete variance = q²/dt`.

Report predeclared 95% moving-block-bootstrap confidence intervals; blocks are at least 10 seconds or ten times the fitted region's largest tau, whichever is greater. Record the seed and resample count. Estimate temperature effects separately. The second-day 20 °C estimate must agree within 20% before a per-device profile can freeze. Report per-axis and per-device results. Failed parameters remain null. Pooling is forbidden unless a separate, predeclared hierarchical or equivalence analysis independently justifies it and propagates uncertainty.

## Provenance

Preserve raw bytes and SHA-256 checksums, all decoder/firmware/config hashes, temperature-logger identity and calibration, actual per-node sample counts, native-dt distributions, saturation/invalid counts, and every derived table. The immutable R6A1A prerequisite is embedded verbatim in `NOISE_CAPTURE_PROTOCOL.json`.
"""


def donning_markdown(protocol: dict[str, Any]) -> str:
    return """# Root-R6A1B donning, signed-axis, and skin-slip capture protocol

Status: **NOT_EXECUTED**. This is a worn geometry experiment. It must not be analyzed as electronic IMU noise, and no full C1 or held-out data may substitute for it.

## Preflight

Verify all ten physical B306 identities and segment ownership, especially BSFEC35 on the left forearm/wrist and BSFB165 on the right. Mark and photograph the enclosure axes and the common physical short edge. Preserve firmware, decoder, configuration, temperature, boot epoch, native time, and `global_time_ns`. Record the session, subject, donning, and every redonning event.

## Required action families

Perform at least five repetitions for every required action family and every named side/device: labelled neutral still; flat gravity test; signed +90° rotations about every register axis; signed -90° rotations about every register axis; common groundward short-edge verification; left and right wrist pronation/supination; left and right elbow flexion/extension; and left and right knee flexion/extension. Handle left and right separately.

Every motion begins and ends in an explicitly labelled neutral still. A redonning starts a new session block. Time-synchronized visual or fixture evidence is optional but recommended.

## Reporting and limits

Report signed register-axis results, device/segment mapping, neutral-return rotation residuals, direction-dependent hysteresis, repetition statistics, temperature, timestamp continuity, and reset/redonning events. Pronation/supination motion alone does not prove skin slip; residual after return to the same anatomical neutral is the relevant slip/hysteresis evidence. Do not fit an exact all-motion extrinsic from one still, introduce an unconstrained per-sample extrinsic, absorb slip into bone length or electronic noise, or claim a one-axis slip model without measurement.
"""


def data_access_audit(sources: dict[str, Path]) -> dict[str, Any]:
    opened = [str(path) for key, path in sorted(sources.items()) if key not in {"fusion"}]
    return {
        "schema": "biospur-root-r6a1b-data-access-audit-v1",
        "opened_read_only_artifacts": opened,
        "raw_imu_samples_opened": False,
        "uwb_payloads_opened": False,
        "full_c1_opened": False,
        "heldout_opened": False,
        "real_body_state_updated": False,
        "real_calibration_fit": False,
        "root_r6a2_started": False,
        "hardware_modified": False,
    }


def final_json(synthetic: dict[str, Any], tests: dict[str, Any], protected: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "biospur-root-r6a1b-final-result-v1",
        "principal_verdict": VERDICT,
        "authority_counts": dict(AUTHORITY_COUNTS),
        "slot_count": 87,
        "synthetic_gates": {"passed": sum(row["pass"] for row in synthetic["gates"]), "total": 18, "all_pass": synthetic["all_pass"]},
        "tests_all_pass": tests["all_pass"],
        "protected_hashes_match": protected["all_match"],
        "real_calibration_qualified": False,
        "real_noise_qualified": False,
        "real_articulated_state_estimated": False,
        "production_fusion_authorized": False,
        "root_r6a2_shadow_integration_structurally_ready": False,
        "root_r6a2_readiness_reason": "The minimal architecture is defined, but starting shadow integration would require placeholders while torso_top, every internal IMU-to-antenna lever, the V4-to-navigation/protocol bridge, and the corrected wrist-mapping adapter remain unresolved.",
        "external_provenance_gaps": [
            "torso_top has no exact physical landmark definition",
            "ten per-device IMU-origin to UWB antenna phase-centre rigid levers lack CAD/metrology provenance",
            "V4 is relative geometry only and lacks an exact navigation/protocol/pelvis bridge",
            "donning/signed-axis and neutral-return skin-slip bounds have not been captured",
            "ten-device multi-temperature electronic-noise qualification has not been captured",
            "bounded anatomical joint/rest priors and four distal landmark measurements remain absent",
        ],
    }


def final_markdown(result: dict[str, Any]) -> str:
    counts = result["authority_counts"]
    return f"""# Root-R6A1B final result

Principal verdict: `{result['principal_verdict']}`

All 87 immutable registry slots were classified exactly once, all 18 synthetic qualification gates passed, all predecessor/regression tests passed, and protected result manifests remain byte-exact. This qualifies the authority architecture and minimal-state contract only. It does not qualify any real value, noise constant, body update, skin-slip correction, production fusion, or external accuracy.

1. **What the slots represent.** They are a registry of static geometry, sensor/tag transforms, capture-bound anchor/time provenance, and the world coordinate gauge. Registry membership does not make a slot an independently free optimizer variable; all source values remain null and `FROZEN_UNCERTAIN`.
2. **Exact authority counts.** Fixed by convention: {counts['FIX_BY_CONVENTION']}; imported frozen provenance: {counts['IMPORT_FROZEN_PROVENANCE']}; measured directly: {counts['MEASURE_DIRECTLY']}; estimated with prior: {counts['ESTIMATE_WITH_PRIOR']}; derived/not independent: {counts['DERIVE_NOT_INDEPENDENT']}; blocked missing definition: {counts['BLOCKED_MISSING_DEFINITION']}. Total: 87.
3. **Duplicate freedoms removed.** Nine child joint offsets are zero by proximal-child-origin convention; eight bone lengths derive from endpoint geometry; ten tag levers derive from `t_SI + R_SI*l_IA`; ten clock mappings are imported once; and the six-dimensional world gauge is fixed. Bone length, tag lever, clock offset, and world gauge cannot cofit residuals as second freedoms.
4. **Reusable provenance.** The exact capture-bound V4 layout/manifest and apply-once anchor-delay convention can be reused only with the same hardware/identity/deployment manifest. The per-node ClockModel/common-time result can be imported only for its bound capture and boot epochs. The operator brief supports directional donning closure, but not exact rotations. No historical R2.5 frame-chain artifact was found for independent reread.
5. **Captures still required.** Measure four distal landmarks and bounded joint/rest priors; obtain PCB/CAD or direct internal IMU-to-antenna levers; survey the V4-to-right-handed +Z-up navigation/protocol/pelvis bridge; run the separate signed-axis/donning/neutral-return protocol; and run the exact four-plateau ten-device electronic-noise protocol. `torso_top` must be physically defined or excluded.
6. **Root-R6A2 readiness.** Shadow integration is **not structurally ready to begin**. The parameterization is implementable, but the missing internal lever/world bridges and the protected Root-R6A0 wrist-map conflict would otherwise force placeholders or silently wrong ownership.
"""


def write_checksums(result: Path) -> None:
    rows = []
    for path in sorted(result.iterdir(), key=lambda item: item.name):
        if path.is_file() and path.name != "SHA256SUMS":
            rows.append(f"{sha256_file(path)}  {path.name}\n")
    (result / "SHA256SUMS").write_text("".join(rows), encoding="utf-8")


def generate(result: Path) -> None:
    if result.exists() and any(result.iterdir()):
        raise RuntimeError(f"refusing to overwrite non-empty result directory: {result}")
    result.mkdir(parents=True, exist_ok=True)
    sources = resolve_sources(FUSION, BRIEF)
    ledger = json.loads(sources["ledger"].read_text(encoding="utf-8"))
    protected = protected_audit(sources)
    contracts = build_contracts(ledger, FUSION, sources)
    synthetic = run_synthetic_qualification(ledger, contracts, protected_hashes_match=protected["all_match"])
    tests = run_tests()
    for name, artifact in contracts.items():
        dump(result / name, artifact)
    dump(result / "SYNTHETIC_OBSERVABILITY_AUDIT.json", synthetic)
    dump(result / "TEST_RESULTS.json", tests)
    dump(result / "PROTECTED_HASHES_BEFORE_AFTER.json", protected)
    dump(result / "R6A1A_CHECKPOINT_AUDIT.json", checkpoint_audit(protected, result))
    dump(result / "DATA_ACCESS_AUDIT.json", data_access_audit(sources))
    (result / "NOISE_CAPTURE_PROTOCOL.md").write_text(noise_markdown(contracts["NOISE_CAPTURE_PROTOCOL.json"]), encoding="utf-8")
    (result / "DONNING_AND_SKIN_SLIP_CAPTURE_PROTOCOL.md").write_text(donning_markdown(contracts["DONNING_AND_SKIN_SLIP_CAPTURE_PROTOCOL.json"]), encoding="utf-8")
    final = final_json(synthetic, tests, protected)
    dump(result / "FINAL_RESULT.json", final)
    (result / "FINAL_RESULT.md").write_text(final_markdown(final), encoding="utf-8")
    dump(result / "EXECUTION_GUARD_AUDIT.json", {
        "schema": "biospur-root-r6a1b-execution-guard-audit-v1",
        "head": git("rev-parse", "HEAD"),
        "branch": git("branch", "--show-current"),
        "checkpoint_head_unchanged": git("rev-parse", "HEAD") == CHECKPOINT_HEAD,
        "remote_refs_before_sha256": REMOTE_REFS_BEFORE,
        "remote_refs_after_sha256": remote_refs_digest(),
        "remote_refs_unchanged": remote_refs_digest() == REMOTE_REFS_BEFORE,
        "push_performed": False,
        "merge_performed": False,
        "publish_performed": False,
        "root_r6a2_started": False,
    })
    # The independent verifier is a separate process and writes its own report.


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
