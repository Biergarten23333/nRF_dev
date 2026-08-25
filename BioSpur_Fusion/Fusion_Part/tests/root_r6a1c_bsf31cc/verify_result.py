"""Independent verifier for the BSF31CC hardware addendum."""
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
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

from biospur_fusion.root_r6a1c_bsf31cc import build_addendum, canonical_bytes, run_gates, sha256_file  # noqa: E402


REQUIRED = {
    "BSF31CC_HARDWARE_FRAME_AND_ATTACHMENT_AUDIT.json", "HARDWARE_FAMILY_BINDING.json",
    "QUALIFICATION_GATES.json", "SOURCE_HASHES.json", "TEST_RESULTS.json",
    "IMPLEMENTATION_FILES.json", "FINAL_RESULT.json", "FINAL_RESULT.md",
}


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def verify(result: Path, rerun_tests: bool, require_checksums: bool) -> dict:
    checks = []
    def check(name: str, passed: bool, detail=None):
        checks.append({"name": name, "pass": bool(passed), "detail": detail})
    check("required_files_present", not (missing := sorted(name for name in REQUIRED if not (result / name).is_file())), missing)
    audit = build_addendum(FUSION)
    check("audit_independently_rebuilt", canonical_bytes(audit) == canonical_bytes(load(result / "BSF31CC_HARDWARE_FRAME_AND_ATTACHMENT_AUDIT.json")))
    gates = run_gates(audit)
    check("fourteen_gates_independently_rerun", gates["all_pass"] and canonical_bytes(gates) == canonical_bytes(load(result / "QUALIFICATION_GATES.json")))
    check("parent_result_unchanged", audit["parent_result"]["sha256sums"]["sha256"] == "0480092da630b6f6c11167d6fb88d4f822a155d80f9c2f2252a46706b6e20ab4" and not audit["parent_result"]["modified"])
    families = audit["hardware_family_binding"]
    check("two_families_no_transform_reuse", families["all_ten_nodes_covered_once"] and families["COMMON_NINE_V0_20_PCB17"]["enclosure_authority"] == "OPERATOR_ATTESTED_IDENTICAL_3D_PRINTED_BOX_ACROSS_COMMON_NINE" and not families["cross_family_transform_reuse_authorized"] and not families["common_nine_box_reuse_for_BSF31CC_authorized"])
    attachment = audit["BSF31CC_band_attachment"]
    check("six_mm_is_direct_button_band_attachment_not_box", attachment["band_surface_to_PCB_bottom_plane"]["value"] == 0.006 and attachment["band_surface_to_PCB_bottom_plane"]["provenance"] == "OPERATOR_MEASURED" and not attachment["missing_box_CAD_blocks_band_to_PCB_attachment"] and not attachment["enclosure_geometric_centre_C_required_for_attachment"])
    check("real_levers_remain_unqualified", not any(audit["real_lever_authority"][key] for key in ("IMU_origin_to_band", "IMU_origin_to_UWB_nominal_point")))
    implementation = load(result / "IMPLEMENTATION_FILES.json")
    check("implementation_hashes_match", all(sha256_file(FUSION / row["path"]) == row["sha256"] for row in implementation["files"]), implementation["file_count"])
    if rerun_tests:
        command = [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1c_bsf31cc"]
        completed = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
        output = completed.stdout + completed.stderr
        check("focused_tests_rerun", completed.returncode == 0 and "14 passed" in output, output.strip())
    if require_checksums:
        manifest = result / "SHA256SUMS"; valid = manifest.is_file(); listed = {}
        if valid:
            for line in manifest.read_text().splitlines():
                digest, name = line.split(None, 1); listed[name.lstrip(" *")] = digest
            expected = {path.name for path in result.iterdir() if path.is_file() and path.name != "SHA256SUMS"}
            valid = set(listed) == expected and all(sha256_file(result / name) == digest for name, digest in listed.items())
        check("checksums_complete_valid", valid)
    return {"schema": "biospur-root-r6a1c-bsf31cc-independent-verification-v1", "all_pass": all(row["pass"] for row in checks), "check_count": len(checks), "checks": checks, "verifier_sha256": sha256_file(Path(__file__))}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--rerun-tests", action="store_true")
    parser.add_argument("--write-report", action="store_true")
    parser.add_argument("--require-checksums", action="store_true")
    args = parser.parse_args()
    report = verify(args.result.resolve(), args.rerun_tests, args.require_checksums)
    if args.write_report:
        (args.result / "INDEPENDENT_VERIFICATION.json").write_bytes(json.dumps(report, indent=2, sort_keys=True).encode() + b"\n")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["all_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
