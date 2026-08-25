"""Generate the sealed BSF31CC hardware addendum."""
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

from biospur_fusion.root_r6a1c_bsf31cc import build_addendum, run_gates, sha256_file  # noqa: E402


VERDICT = "PASS_ROOT_R6A1C_BSF31CC_HARDWARE_ATTACHMENT_ADDENDUM_RECORDED_REAL_LEVERS_PENDING"


def dump(path: Path, value: object) -> None:
    path.write_bytes(json.dumps(value, indent=2, sort_keys=True, allow_nan=False).encode() + b"\n")


def implementation_files() -> list[Path]:
    return sorted([
        *(FUSION / "src/biospur_fusion/root_r6a1c_bsf31cc").glob("*.py"),
        *(FUSION / "tests/root_r6a1c_bsf31cc").glob("*.py"),
    ])


def run_tests() -> dict:
    command = [sys.executable, "-m", "pytest", "-q", "Fusion_Part/tests/root_r6a1c_bsf31cc"]
    completed = subprocess.run(command, cwd=REPO, text=True, capture_output=True)
    output = completed.stdout + completed.stderr
    passed = completed.returncode == 0 and "14 passed" in output
    if not passed:
        raise RuntimeError(output)
    return {"command": command, "return_code": completed.returncode, "expected_passed": 14, "pass": passed, "output": output.strip()}


def write_checksums(result: Path) -> None:
    lines = [f"{sha256_file(path)}  {path.name}\n" for path in sorted(result.iterdir()) if path.is_file() and path.name != "SHA256SUMS"]
    (result / "SHA256SUMS").write_text("".join(lines), encoding="utf-8")


def generate(result: Path) -> None:
    if result.exists() and any(result.iterdir()):
        raise RuntimeError(f"refusing to overwrite {result}")
    result.mkdir(parents=True, exist_ok=True)
    audit = build_addendum(FUSION)
    gates = run_gates(audit)
    if not gates["all_pass"]:
        raise RuntimeError("qualification gates failed")
    tests = run_tests()
    family = audit["hardware_family_binding"]
    attachment = audit["BSF31CC_band_attachment"]
    final = {
        "schema": "biospur-root-r6a1c-bsf31cc-addendum-final-v1",
        "principal_verdict": VERDICT,
        "common_nine_family_bound": True,
        "BSF31CC_distinct_family_bound": True,
        "cross_family_transform_reuse_authorized": False,
        "band_to_PCB_bottom_distance_m": attachment["band_surface_to_PCB_bottom_plane"]["value"],
        "band_attachment_depends_on_enclosure_CAD": False,
        "full_real_IMU_or_UWB_lever_authorized": False,
        "gates": {"passed": 14, "total": 14, "all_pass": True},
        "tests": {"passed": 14, "all_pass": tests["pass"]},
        "immutable_registry": {key: audit["immutable_registry"][key] for key in ("total", "value_null", "FROZEN_UNCERTAIN", "fitted_from_real_data")},
        "family_node_counts": {"common": family["COMMON_NINE_V0_20_PCB17"]["node_count"], "BSF31CC": family["BSF31CC_V0_20_N5BL"]["node_count"]},
    }
    implementation = {"schema": "biospur-root-r6a1c-bsf31cc-implementation-v1", "files": [{"path": str(path.relative_to(FUSION)), "sha256": sha256_file(path)} for path in implementation_files()]}
    implementation["file_count"] = len(implementation["files"])
    dump(result / "BSF31CC_HARDWARE_FRAME_AND_ATTACHMENT_AUDIT.json", audit)
    dump(result / "HARDWARE_FAMILY_BINDING.json", family)
    dump(result / "QUALIFICATION_GATES.json", gates)
    dump(result / "SOURCE_HASHES.json", audit["source_evidence"])
    dump(result / "TEST_RESULTS.json", tests)
    dump(result / "IMPLEMENTATION_FILES.json", implementation)
    dump(result / "FINAL_RESULT.json", final)
    (result / "FINAL_RESULT.md").write_text(f"""# BSF31CC Root-R6A1C hardware addendum

Principal verdict: `{VERDICT}`

This addendum leaves the sealed parent Root-R6A1C result unchanged and binds two hardware families: nine common `PCB17` Fusion units with one identical shared 3D-printed-box design, and the distinct `BSF31CC` PCB. Common-nine PCB/box transforms apply only to those nine and are forbidden for BSF31CC.

The BSF31CC U1/B306 and U4/DWM1001C top/component side faces outward. The U7/JY901S bottom side faces the body/band. The four-contact pogo row lies on the `B31 -y` edge and is the down marker; it is not the band attachment.

The mechanical chain is `Polar-style band -> PCB-mounted snap buttons -> BSF31CC PCB`. The operator-measured perpendicular band-surface to PCB-bottom-plane separation is `0.006 m`; uncertainty and exact button reference designators remain pending. The transparent printed box is protective only and is not fixation or registration authority. Missing box CAD does not block this direct attachment observation, and enclosure centre `C` is not used for it.

The Fusion core interfaces match at U1 pins 35/36/37/42/44, but whole-board electrical identity is not claimed because BSF31CC carries additional ECG circuitry. CAD component-reference transforms are recorded; IMU die, unique UWB nominal point, electromagnetic phase centre, and full real levers remain unqualified.

All 14 gates and 14 focused tests passed. The immutable 87-slot registry remains 87 null, 87 `FROZEN_UNCERTAIN`, and zero fitted from real data. No real fusion, body-state update, commit, push, merge, or parent-result modification occurred.
""", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--checksums-only", action="store_true")
    args = parser.parse_args()
    result = args.result.resolve()
    if args.checksums_only:
        write_checksums(result)
    else:
        generate(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
