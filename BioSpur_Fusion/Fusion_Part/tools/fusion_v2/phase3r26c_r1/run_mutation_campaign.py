#!/usr/bin/env python3
"""Execute and verify all 14 four-phase production-source mutant loops."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from mutants import MUTANTS


TARGET_MODULE = {
    mutant: ("pipeline.py" if relative == "pipeline.py" else "core.py" if relative == "core.py" else "heading_gauge.py")
    for mutant, (relative, _old, _new) in MUTANTS.items()
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def invoke(command: list[str], env: dict[str, str]) -> None:
    completed = subprocess.run(command, env=env, text=True, capture_output=True)
    sys.stdout.write(completed.stdout)
    sys.stderr.write(completed.stderr)
    if completed.returncode != 0:
        raise RuntimeError(f"evidence wrapper failed: {command}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    args = parser.parse_args()
    worktree = args.worktree.resolve()
    raw = args.raw_root.resolve()
    tool = Path(__file__).resolve().parent
    src = worktree / "BioSpur_Fusion/Fusion_Part/src"
    package = src / "biospur_fusion"
    evidence = raw / "mutation_commands"
    mutant_parent = raw / "mutant_packages"
    evidence.mkdir(parents=True, exist_ok=True)
    mutant_parent.mkdir(parents=True, exist_ok=True)
    wrapper = tool / "run_evidenced_command.py"
    probe = tool / "mutation_probe.py"
    apply_script = tool / "apply_mutant.py"
    forbidden = [
        worktree / "BioSpur_Fusion/Fusion_Part/tests/integration/test_current_capture_result.py",
        worktree / "BioSpur_Fusion/Fusion_Part/logs/v47_ten_node_body_calibration_20260814_093601/analysis_body_fusion_v2",
    ]
    common = [sys.executable, str(wrapper), "--evidence-dir", str(evidence), "--cwd", str(worktree)]
    for path in forbidden:
        common += ["--forbid", str(path)]
    summaries = []
    for mutant in MUTANTS:
        module_name = TARGET_MODULE[mutant]
        baseline_module = package / "heading_anchor_audit_v2" / module_name
        baseline_sha = sha(baseline_module)
        mutant_root = mutant_parent / mutant
        base_env = os.environ.copy()
        base_env.update({
            "PYTHONPATH": str(src),
            "EXPECTED_PACKAGE_ROOT": str(src),
            "EXPECTED_MODULE_SHA256": baseline_sha,
            "EXPECT_MUTANT": "0",
        })
        invoke(common + ["--label", f"{mutant}_A_baseline", "--expect", "pass", "--",
                         sys.executable, str(probe), "--case", mutant], base_env)
        apply_env = os.environ.copy()
        apply_env["PYTHONPATH"] = str(tool)
        invoke(common + ["--label", f"{mutant}_B_mutation", "--expect", "pass", "--",
                         sys.executable, str(apply_script), "--package", str(package),
                         "--mutant-root", str(mutant_root), "--mutant", mutant], apply_env)
        mutated_module = mutant_root / "biospur_fusion/heading_anchor_audit_v2" / module_name
        mutated_sha = sha(mutated_module)
        if mutated_sha == baseline_sha:
            raise RuntimeError(f"{mutant} source SHA did not change")
        mutant_env = os.environ.copy()
        mutant_env.update({
            "PYTHONPATH": str(mutant_root),
            "EXPECTED_PACKAGE_ROOT": str(mutant_root),
            "EXPECTED_MODULE_SHA256": mutated_sha,
            "EXPECT_MUTANT": "1",
        })
        invoke(common + ["--label", f"{mutant}_C_targeted_fail", "--expect", "fail", "--",
                         sys.executable, str(probe), "--case", mutant], mutant_env)
        if sha(baseline_module) != baseline_sha:
            raise RuntimeError(f"{mutant} baseline source changed")
        invoke(common + ["--label", f"{mutant}_D_restored_baseline", "--expect", "pass", "--",
                         sys.executable, str(probe), "--case", mutant], base_env)
        phases = {}
        for suffix in ("A_baseline", "B_mutation", "C_targeted_fail", "D_restored_baseline"):
            manifest = json.loads((evidence / f"{mutant}_{suffix}" / "manifest.json").read_text())
            if not manifest["qualified"]:
                raise RuntimeError(f"{mutant} {suffix} did not qualify")
            phases[suffix] = manifest
        attestation_lines = (evidence / f"{mutant}_C_targeted_fail" / "stdout.txt").read_text().splitlines()
        attestation = json.loads(attestation_lines[0])
        expected_file = str(mutated_module.resolve())
        if attestation != {
            "imported_module.__file__": expected_file,
            "imported_module_source_sha256": mutated_sha,
            "mutant_root_path": str(mutant_root.resolve()),
        }:
            raise RuntimeError(f"{mutant} import attestation mismatch: {attestation}")
        summaries.append({
            "mutant_id": mutant,
            "baseline_source_sha256": baseline_sha,
            "mutated_source_sha256": mutated_sha,
            "mutant_root_path": str(mutant_root),
            "import_attestation": attestation,
            "phase_exit_codes": {name: item["exit_code"] for name, item in phases.items()},
            "closed_loop": True,
        })
    result = {
        "schema": "biospur.phase3r26c_r1.production_source_mutation_campaign.v1",
        "mutant_count": len(summaries),
        "surviving_mutant_count": 0,
        "all_four_phase_loops_closed": True,
        "mutants": summaries,
    }
    (raw / "PRODUCTION_SOURCE_MUTATION_CAMPAIGN.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps({"mutant_count": len(summaries), "surviving_mutant_count": 0}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
