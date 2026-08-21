"""Small source-capsule GREEN and closure rerun; never a Git worktree."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

from common import read_json, sha256_file, write_json
from float_semantic_equivalence import compare_results, semantic_projection
from mutation_runner import PRODUCTION_RELATIVE


WRAPPER_SOURCE = '''#!/usr/bin/env python3
from __future__ import annotations
import argparse
from pathlib import Path
import runpy
import subprocess
import sys

root = Path(__file__).resolve().parent
parser = argparse.ArgumentParser()
parser.add_argument("mode", choices=("green", "closure"))
parser.add_argument("--junit")
args = parser.parse_args()
if args.mode == "green":
    target = root / "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r2/test_frozen_contract.py"
    raise SystemExit(subprocess.run([
        sys.executable, "-B", "-m", "pytest", "-q", "-p", "no:cacheprovider",
        "--rootdir", str(root), "--import-mode=importlib", f"--junitxml={args.junit}", str(target),
    ]).returncode)
runpy.run_path(str(root / "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r26c_r2/closure_probe.py"), run_name="__main__")
'''


def _copy_tree_files(source: Path, target: Path) -> None:
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)


def _build_capsule(fusion: Path, report: Path, capsule: Path) -> dict:
    if capsule.exists():
        raise RuntimeError(f"isolated capsule already exists: {capsule}")
    heading_source = fusion / "src/biospur_fusion/heading_anchor_audit_v2"
    heading_target = capsule / "biospur_fusion/heading_anchor_audit_v2"
    heading_target.mkdir(parents=True)
    shutil.copy2(fusion / "src/biospur_fusion/__init__.py", capsule / "biospur_fusion/__init__.py")
    for relative in PRODUCTION_RELATIVE:
        shutil.copy2(heading_source / relative, heading_target / relative)
    tests_source = fusion / "tests/fusion_v2/phase3r26c_r2"
    tests_target = capsule / "BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r2"
    _copy_tree_files(tests_source, tests_target)
    closure_source = fusion / "tools/fusion_v2/phase3r26c_r2/closure_probe.py"
    closure_target = capsule / "BioSpur_Fusion/Fusion_Part/tools/fusion_v2/phase3r26c_r2/closure_probe.py"
    closure_target.parent.mkdir(parents=True)
    shutil.copy2(closure_source, closure_target)
    qualification_sources = (
        "tools/fusion_v2/phase3r26c_r1/mutants.py",
        "tools/fusion_v2/phase3r26c_r1/mutation_probe.py",
        "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py",
        "tools/fusion_v2/phase3r26c_r2_q2/r2_mutation_probe.py",
        "tools/fusion_v2/phase3r26c_r2_q2/float_semantic_equivalence.py",
        "tools/fusion_v2/phase3r26c_r2_q2/float_field_registry.json",
    )
    for relative in qualification_sources:
        target = capsule / "BioSpur_Fusion/Fusion_Part" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fusion / relative, target)
    site_source = fusion / "tools/fusion_v2/phase3r26c_r2_q2/sitecustomize.py"
    shutil.copy2(site_source, capsule / "sitecustomize.py")
    environment_manifest = report / "COMMAND_ENVIRONMENT_MANIFEST.json"
    if environment_manifest.exists():
        shutil.copy2(environment_manifest, capsule / "COMMAND_ENVIRONMENT_MANIFEST.json")
    else:
        write_json(capsule / "COMMAND_ENVIRONMENT_MANIFEST.json", {
            "mode": "DEVELOPMENT_PREFLIGHT_NOT_FORMAL_EVIDENCE"
        })
    classification_inputs = capsule / "qualification_inputs"
    classification_inputs.mkdir()
    input_candidates = {
        "r1.json": (
            report / "R1_MUTATION_REPLAY_RESULTS.json"
            if (report / "R1_MUTATION_REPLAY_RESULTS.json").exists()
            else report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json"
        ),
        "r2.json": (
            report / "R2_PRODUCTION_MUTATION_RESULTS.json"
            if (report / "R2_PRODUCTION_MUTATION_RESULTS.json").exists()
            else report / "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json"
        ),
    }
    for name, source in input_candidates.items():
        if source.exists():
            shutil.copy2(source, classification_inputs / name)
    (capsule / "run_isolated.py").write_text(WRAPPER_SOURCE, encoding="utf-8")
    files = [path for path in capsule.rglob("*") if path.is_file()]
    size = sum(path.stat().st_size for path in files)
    return {
        "file_count": len(files),
        "size_bytes": size,
        "under_5mb": size < 5 * 1024 * 1024,
        "files": {
            str(path.relative_to(capsule)): sha256_file(path)
            for path in sorted(files)
        },
        "contains_git_metadata": any(path.name == ".git" for path in capsule.rglob("*")),
    }


def _junit(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    cases = [case for suite in suites for case in suite.findall("testcase")]
    failed = [case.attrib.get("name", "") for case in cases if case.find("failure") is not None]
    errors = [case.attrib.get("name", "") for case in cases if case.find("error") is not None]
    skipped = [case.attrib.get("name", "") for case in cases if case.find("skipped") is not None]
    return {
        "tests": len(cases),
        "passes": len(cases) - len(failed) - len(errors) - len(skipped),
        "nodeids": [f"test_frozen_contract.py::{case.attrib.get('name', '')}" for case in cases],
        "failures": failed,
        "errors": errors,
        "skipped": skipped,
    }


def _capsule_env(capsule: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(capsule)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["TMPDIR"] = os.environ["TMPDIR"]
    env["R26C_Q2_AUDIT_LOG"] = os.environ["R26C_Q2_AUDIT_LOG"]
    return env


def _run_capsule(fusion: Path, report: Path, capsule: Path, evidence: Path) -> dict:
    inventory = _build_capsule(fusion, report, capsule)
    if not inventory["under_5mb"] or inventory["contains_git_metadata"]:
        raise RuntimeError(f"isolated capsule contract failed: {inventory}")
    env = _capsule_env(capsule)
    attest_code = (
        "import importlib,json,pathlib,sitecustomize;"
        "names=['biospur_fusion.heading_anchor_audit_v2.core',"
        "'biospur_fusion.heading_anchor_audit_v2.heading_gauge',"
        "'biospur_fusion.heading_anchor_audit_v2.pipeline',"
        "'BioSpur_Fusion.Fusion_Part.tests.fusion_v2.phase3r26c_r2.helpers',"
        "'BioSpur_Fusion.Fusion_Part.tools.fusion_v2.phase3r26c_r2.closure_probe'];"
        "rows={n:str(pathlib.Path(importlib.import_module(n).__file__).resolve()) for n in names};"
        "rows['sitecustomize']=str(pathlib.Path(sitecustomize.__file__).resolve());"
        "print(json.dumps(rows,sort_keys=True))"
    )
    attest = subprocess.run([sys.executable, "-B", "-c", attest_code], cwd=capsule, env=env, capture_output=True, text=True)
    if attest.returncode != 0:
        raise RuntimeError(f"isolated module attestation failed: {attest.stderr}")
    origins = json.loads(attest.stdout)
    origins_in_capsule = all(Path(value).resolve().is_relative_to(capsule.resolve()) for value in origins.values())
    junit = evidence / "isolated_green.xml"
    green = subprocess.run([sys.executable, "-B", str(capsule / "run_isolated.py"), "green", "--junit", str(junit)], cwd=capsule, env=env, capture_output=True, text=True)
    closure = subprocess.run([sys.executable, "-B", str(capsule / "run_isolated.py"), "closure"], cwd=capsule, env=env, capture_output=True, text=True)
    (evidence / "isolated_green_stdout.txt").write_text(green.stdout, encoding="utf-8")
    (evidence / "isolated_green_stderr.txt").write_text(green.stderr, encoding="utf-8")
    (evidence / "isolated_closure_stdout.txt").write_text(closure.stdout, encoding="utf-8")
    (evidence / "isolated_closure_stderr.txt").write_text(closure.stderr, encoding="utf-8")
    junit_result = _junit(junit) if junit.exists() else {"tests": 0, "passes": 0, "nodeids": [], "failures": [], "errors": ["missing junit"], "skipped": []}
    closure_payload = json.loads(closure.stdout) if closure.returncode == 0 else {}
    isolated_projection = {
        "k_consumer_count": closure_payload.get("k_consumer_count"),
        "k_consumers_psi_free": sum(row.get("qualified", False) for row in closure_payload.get("k_consumers", [])),
        "formal_executed": closure_payload.get("formal", {}).get("executed"),
        "formal_passed": closure_payload.get("formal", {}).get("passed"),
        "h_transport_max_error": closure_payload.get("h_transport_max_error"),
    }
    if (report / "FORMAL_CLOSURE_RESULT.json").exists():
        canonical_closure = read_json(report / "FORMAL_CLOSURE_RESULT.json")
        canonical_projection = {
            "k_consumer_count": canonical_closure["sections"]["consumer_closure"]["k_consumer_count"],
            "k_consumers_psi_free": canonical_closure["sections"]["consumer_closure"]["k_consumers_psi_free"],
            "formal_executed": canonical_closure["sections"]["formal_schema_closure"]["formal_schema_matrix"]["executed"],
            "formal_passed": canonical_closure["sections"]["formal_schema_closure"]["formal_schema_matrix"]["passed"],
            "h_transport_max_error": canonical_closure["sections"]["h_boundary_closure"]["H_transport_max_error"],
        }
        expected_green = read_json(report / "FROZEN_GREEN_RESULT.json")["junit"]
        reference_mode = "formal_canonical_evidence"
    else:
        canonical_projection = dict(isolated_projection)
        expected_green = {"tests": 15, "passes": 15}
        reference_mode = "development_contract_preflight"
    green_consistent = (
        green.returncode == 0
        and junit_result["tests"] == expected_green["tests"]
        and junit_result["passes"] == expected_green["passes"]
        and not junit_result["failures"] and not junit_result["errors"] and not junit_result["skipped"]
    )
    r1_path = report / "R1_MUTATION_REPLAY_RESULTS.json"
    if not r1_path.exists():
        r1_path = report / "DEVELOPMENT_R1_PREFLIGHT_RESULTS.json"
    r2_path = report / "R2_PRODUCTION_MUTATION_RESULTS.json"
    if not r2_path.exists():
        r2_path = report / "DEVELOPMENT_R2_PREFLIGHT_RESULTS.json"
    if not r1_path.exists() or not r2_path.exists():
        raise RuntimeError("isolated typed comparison requires development or formal R1/R2 classifications")
    r1 = read_json(r1_path)
    r2_source = read_json(r2_path)
    r2 = r2_source
    if "mutants" not in r2 and "results" in r2:
        r2 = {**r2, "mutants": r2["results"]}
    canonical_nodeids = []
    manifest = report / "COMMAND_ENVIRONMENT_MANIFEST.json"
    if manifest.exists():
        canonical_nodeids = read_json(manifest)["commands"]["frozen_green"]["runtime_preflight"]["pytest"]["collected_nodeids"]
    else:
        attempts = sorted((report / "development/preflight/frozen_green").glob("attempt_*/COMMAND_RESULT.json"))
        if not attempts:
            raise RuntimeError("isolated comparison requires the frozen GREEN collection preflight")
        canonical_nodeids = read_json(attempts[-1])["runtime_preflight"]["pytest"]["collected_nodeids"]
    canonical_nodeids = [f"test_frozen_contract.py::{item.split('::')[-1]}" for item in canonical_nodeids]
    production_dir = fusion / "src/biospur_fusion/heading_anchor_audit_v2"
    source_hashes_canonical = {f"production/{name}": sha256_file(production_dir / name) for name in PRODUCTION_RELATIVE}
    source_hashes_isolated = {f"production/{name}": inventory["files"][f"biospur_fusion/heading_anchor_audit_v2/{name}"] for name in PRODUCTION_RELATIVE}
    tool_relatives = (
        "tools/fusion_v2/phase3r26c_r1/mutants.py",
        "tools/fusion_v2/phase3r26c_r1/mutation_probe.py",
        "tools/fusion_v2/phase3r26c_r2_q2/mutation_runner.py",
        "tools/fusion_v2/phase3r26c_r2_q2/r2_mutation_probe.py",
        "tools/fusion_v2/phase3r26c_r2_q2/float_semantic_equivalence.py",
        "tools/fusion_v2/phase3r26c_r2_q2/float_field_registry.json",
    )
    for relative in tool_relatives:
        source_hashes_canonical[f"qualification/{relative}"] = sha256_file(fusion / relative)
        source_hashes_isolated[f"qualification/{relative}"] = inventory["files"][f"BioSpur_Fusion/Fusion_Part/{relative}"]
    fixture_hashes_canonical = {}
    fixture_hashes_isolated = {}
    fixture_root = fusion / "tests/fusion_v2/phase3r26c_r2"
    for path in sorted(fixture_root.iterdir()):
        if not path.is_file():
            continue
        key = path.name
        fixture_hashes_canonical[key] = sha256_file(path)
        fixture_hashes_isolated[key] = inventory["files"][f"BioSpur_Fusion/Fusion_Part/tests/fusion_v2/phase3r26c_r2/{key}"]
    canonical_green = {
        "tests": expected_green["tests"], "passes": expected_green["passes"],
        "failure_count": 0, "skip_count": 0, "error_count": 0,
        "nodeids": canonical_nodeids,
    }
    isolated_green = {
        "tests": junit_result["tests"], "passes": junit_result["passes"],
        "failure_count": len(junit_result["failures"]), "skip_count": len(junit_result["skipped"]),
        "error_count": len(junit_result["errors"]), "nodeids": junit_result["nodeids"],
    }
    canonical_closure_typed = {**canonical_projection, "formal": {"executed": canonical_projection["formal_executed"], "passed": canonical_projection["formal_passed"]}}
    isolated_closure_typed = {**isolated_projection, "formal": {"executed": isolated_projection["formal_executed"], "passed": isolated_projection["formal_passed"]}}
    canonical_typed = semantic_projection(
        report=report, fusion=fusion, green=canonical_green, closure=canonical_closure_typed,
        r1=r1, r2=r2, source_hashes=source_hashes_canonical,
        fixture_hashes=fixture_hashes_canonical,
        exit_codes={"green": 0, "closure": 0, "r1": 0, "r2": 0},
    )
    isolated_typed = semantic_projection(
        report=report, fusion=fusion, green=isolated_green, closure=isolated_closure_typed,
        r1=r1, r2=r2, source_hashes=source_hashes_isolated,
        fixture_hashes=fixture_hashes_isolated,
        exit_codes={"green": green.returncode, "closure": closure.returncode, "r1": 0, "r2": 0},
    )
    typed_comparison = compare_results(canonical_typed, isolated_typed)
    closure_consistent = (
        closure.returncode == 0
        and typed_comparison["status"] == "PASS"
        and isolated_projection["k_consumer_count"] == 4
        and isolated_projection["k_consumers_psi_free"] == 4
        and isolated_projection["formal_executed"] == 35
        and isolated_projection["formal_passed"] == 35
    )
    return {
        "inventory": inventory,
        "module_origins": origins,
        "module_origins_all_within_capsule": origins_in_capsule,
        "green_exit_code": green.returncode,
        "green_junit": junit_result,
        "green_consistent": green_consistent,
        "closure_exit_code": closure.returncode,
        "canonical_closure_projection": canonical_projection,
        "isolated_closure_projection": isolated_projection,
        "closure_consistent": closure_consistent,
        "reference_mode": reference_mode,
        "typed_comparison": typed_comparison,
        "canonical_typed_projection": canonical_typed,
        "isolated_typed_projection": isolated_typed,
        "raw_result_digests": typed_comparison["raw_result_digests"],
        "semantic_normalized_digests": typed_comparison["semantic_normalized_digests"],
        "normalized_result_digest": typed_comparison["semantic_normalized_digests"]["candidate"],
    }


def isolated_capsule_preflight(root: Path, fusion: Path, report: Path, base: Path) -> dict:
    del root
    base.mkdir(parents=True, exist_ok=True)
    index = 1
    while (base / f"attempt_{index:03d}").exists():
        index += 1
    attempt = base / f"attempt_{index:03d}"
    attempt.mkdir()
    result = _run_capsule(fusion, report, attempt / "capsule", attempt)
    result["status"] = "PASS" if result["green_consistent"] and result["closure_consistent"] and result["module_origins_all_within_capsule"] else "FAIL"
    write_json(attempt / "RESULT.json", result)
    if result["status"] != "PASS":
        raise RuntimeError(f"isolated capsule preflight failed: {result}")
    return result


def execute_isolated_rerun(root: Path, fusion: Path, report: Path) -> None:
    del root
    evidence = Path(os.environ["R26C_Q2_EVIDENCE_DIR"])
    result = _run_capsule(fusion, report, report / "capsules/isolated_rerun", evidence)
    canonical_digest = result["semantic_normalized_digests"]["canonical"]
    result.update({
        "schema": "biospur.phase3r26c_r2_q4.isolated_capsule_rerun.v1",
        "canonical_normalized_result_digest": canonical_digest,
        "normalized_result_digest_consistent": canonical_digest == result["normalized_result_digest"],
    })
    result["status"] = "PASS" if all((
        result["green_consistent"], result["closure_consistent"],
        result["module_origins_all_within_capsule"], result["inventory"]["under_5mb"],
        result["normalized_result_digest_consistent"],
    )) else "FAIL"
    write_json(report / "ISOLATED_CAPSULE_RERUN_RESULT.json", result)
    write_json(report / "RAW_VS_NORMALIZED_RESULTS.json", {
        "schema": "biospur.phase3r26c_r2_q7.raw_vs_normalized.v1",
        "status": result["typed_comparison"]["status"],
        "typed_comparison": result["typed_comparison"],
        "canonical_raw_projection": result["canonical_typed_projection"],
        "isolated_raw_projection": result["isolated_typed_projection"],
    })
    write_json(report / "RAW_RESULT_DIGESTS.json", {
        "schema": "biospur.phase3r26c_r2_q7.raw_result_digests.v1",
        **result["raw_result_digests"],
        "raw_digest_difference_documented_field": "h_transport_max_error",
    })
    write_json(report / "SEMANTIC_NORMALIZED_DIGESTS.json", {
        "schema": "biospur.phase3r26c_r2_q7.semantic_normalized_digests.v1",
        **result["semantic_normalized_digests"],
        "identical": result["normalized_result_digest_consistent"],
    })
    if result["status"] != "PASS":
        raise RuntimeError(f"isolated rerun failed: {result}")
