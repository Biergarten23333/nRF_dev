#!/usr/bin/env python3
"""Import-audited dispatcher shared by preflight and formal Q2 commands."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

from command_specs import COMMANDS, spec_for
from common import read_json, sha256_file
from environment_gate import assert_runtime_matches


def _within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _import_origin(name: str, root: Path) -> dict:
    spec = importlib.util.find_spec(name)
    if spec is None:
        raise RuntimeError(f"find_spec failed for {name}")
    module = importlib.import_module(name)
    origin = getattr(module, "__file__", None) or spec.origin
    locations = [str(Path(item).resolve()) for item in (spec.submodule_search_locations or ())]
    if origin and origin not in {"built-in", "frozen"}:
        qualified = _within(Path(origin), root)
    else:
        qualified = bool(locations) and all(_within(Path(item), root) for item in locations)
    if not qualified:
        raise RuntimeError(f"project module origin outside active worktree: {name}: {origin}: {locations}")
    return {
        "target": name,
        "find_spec_origin": spec.origin,
        "module_file": origin,
        "submodule_search_locations": locations,
        "sha256": hashlib.sha256(Path(origin).read_bytes()).hexdigest()
        if origin and origin not in {"built-in", "frozen"} else None,
        "qualified": True,
    }


def _collect(root: Path, targets: tuple[str, ...]) -> dict:
    if not targets:
        return {"pytest_rootdir": str(root), "collected_nodeids": [], "exit_code": None}
    command = [
        sys.executable, "-B", "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
        "--rootdir", str(root), "--import-mode=importlib", *targets,
    ]
    completed = subprocess.run(command, cwd=root, capture_output=True, text=True)
    nodeids = sorted({line.strip() for line in completed.stdout.splitlines() if "::" in line})
    if completed.returncode != 0:
        raise RuntimeError(
            f"collection failed rc={completed.returncode}: {completed.stdout}\n{completed.stderr}"
        )
    return {
        "pytest_rootdir": str(root),
        "collected_nodeids": nodeids,
        "collected_count": len(nodeids),
        "exit_code": completed.returncode,
    }


def _environment_preflight(label: str, *, include_runner: bool) -> dict:
    if os.environ.get("R26C_Q2_AUDIT_HOOK_ACTIVE") != "1":
        raise RuntimeError("parent sitecustomize audit hook inactive")
    root = Path(os.environ["R26C_Q2_ACTIVE_ROOT"]).resolve()
    fusion = Path(os.environ["R26C_Q2_FUSION_ROOT"]).resolve()
    report = Path(os.environ["R26C_Q2_REPORT_ROOT"]).resolve()
    expected = os.environ["PYTHONPATH"].split(os.pathsep)
    actual_resolved = [str(Path(item or ".").resolve()) for item in sys.path]
    cursor = -1
    for item in expected:
        resolved = str(Path(item).resolve())
        try:
            cursor = actual_resolved.index(resolved, cursor + 1)
        except ValueError as exc:
            raise RuntimeError(f"PYTHONPATH root absent or out of order in sys.path: {resolved}") from exc
    site = importlib.import_module("sitecustomize")
    site_origin = str(Path(site.__file__).resolve())
    expected_site = str((fusion / "tools/fusion_v2/phase3r26c_r2_q2/sitecustomize.py").resolve())
    if site_origin != expected_site:
        raise RuntimeError(f"wrong sitecustomize origin: {site_origin}")
    spec = spec_for(label)
    origins = [_import_origin(name, root) for name in spec["imports"]]
    child_code = (
        "import json,os,sitecustomize,sys;"
        "assert os.environ.get('R26C_Q2_AUDIT_HOOK_ACTIVE')=='1';"
        "print(json.dumps({'pid':os.getpid(),'sitecustomize':sitecustomize.__file__,'sys_path':sys.path},sort_keys=True))"
    )
    child = subprocess.run([sys.executable, "-B", "-c", child_code], capture_output=True, text=True)
    if child.returncode != 0:
        raise RuntimeError(f"child audit-hook preflight failed: {child.stderr}")
    collection = _collect(root, tuple(spec.get("collect", ())))
    runner = None
    runner_kind = spec.get("runner_preflight") if include_runner else None
    if runner_kind == "r2_structural_mutants":
        from mutation_runner import structural_mutation_preflight

        runner = structural_mutation_preflight(
            root, fusion, report / "development" / "structural_mutation_preflight" / label
        )
    elif runner_kind == "r1_replay":
        from mutation_runner import r1_replay_preflight

        runner = r1_replay_preflight(
            root, fusion, report / "development" / "r1_replay_preflight" / label
        )
    elif runner_kind == "isolated_capsule":
        from isolated_rerun import isolated_capsule_preflight

        runner = isolated_capsule_preflight(
            root, fusion, report,
            report / "development" / "isolated_capsule_preflight" / label,
        )
    return {
        "schema": "biospur.phase3r26c_r2_q2.command_preflight.v1",
        "label": label,
        "absolute_python_executable": str(Path(sys.executable).resolve()),
        "python_version": sys.version,
        "cwd": str(Path.cwd().resolve()),
        "REPO_ROOT": str(root),
        "FUSION_ROOT": str(fusion),
        "PYTHONPATH": os.environ["PYTHONPATH"],
        "sys_path": sys.path,
        "sitecustomize_origin": site_origin,
        "sitecustomize_sha256": sha256_file(Path(site_origin)),
        "module_origins": origins,
        "pytest": collection,
        "child_audit": json.loads(child.stdout),
        "runner_preflight": runner,
        "nested_ptrace_count": 0,
        "status": "PASS",
    }


def _pytest(root: Path, targets: list[str]) -> int:
    import pytest

    evidence = Path(os.environ["R26C_Q2_EVIDENCE_DIR"])
    args = [
        "-q", "--rootdir", str(root), "--import-mode=importlib",
        f"--junitxml={evidence / 'junit.xml'}", *targets,
    ]
    return int(pytest.main(args))


def _execute(command: str) -> int:
    root = Path(os.environ["R26C_Q2_ACTIVE_ROOT"]).resolve()
    fusion = Path(os.environ["R26C_Q2_FUSION_ROOT"]).resolve()
    report = Path(os.environ["R26C_Q2_REPORT_ROOT"]).resolve()
    harness = fusion / "tests/fusion_v2/phase3r26c_r2"
    if command in {"q7_root_cause", "float_comparator_controls", "environment_replication"}:
        from q7_float_audit import run_controls, run_replication, write_root_cause

        {
            "q7_root_cause": write_root_cause,
            "float_comparator_controls": run_controls,
            "environment_replication": run_replication,
        }[command](root, fusion, report)
        return 0
    if command == "harness_lint":
        from tools.fusion_v2.phase3r26c_r2.harness_lint import lint_root

        errors = lint_root(harness)
        print(json.dumps({"status": "PASS" if not errors else "FAIL", "errors": errors}, sort_keys=True))
        return 0 if not errors else 1
    if command == "harness_self_tests":
        return _pytest(root, [str(harness / "test_harness_self.py")])
    if command == "harness_mutation_tests":
        from tools.fusion_v2.phase3r26c_r2 import harness_mutation_selftest

        scratch = Path(os.environ["R26C_Q2_EVIDENCE_DIR"]) / "harness_mutations"
        old = sys.argv
        try:
            sys.argv = [str(Path(harness_mutation_selftest.__file__)), str(harness), str(scratch)]
            return int(harness_mutation_selftest.main())
        finally:
            sys.argv = old
    if command == "frozen_contract":
        return _pytest(root, [str(harness / "test_frozen_contract.py")])
    if command in {"consumer_closure", "h_boundary_closure", "formal_schema_closure"}:
        from closure_checks import run_consumer_closure, run_formal_schema_closure, run_h_boundary_closure

        function = {
            "consumer_closure": run_consumer_closure,
            "h_boundary_closure": run_h_boundary_closure,
            "formal_schema_closure": run_formal_schema_closure,
        }[command]
        function(root, fusion, report)
        return 0
    if command in {"r2_mutation_runner", "r1_mutation_replay"}:
        from mutation_runner import run_r1_replay, run_r2_campaign

        (run_r2_campaign if command == "r2_mutation_runner" else run_r1_replay)(root, fusion, report)
        return 0
    if command == "authorized_suite":
        from authorized_suite import run_authorized_suite

        run_authorized_suite(root, report)
        return 0
    if command == "negative_controls":
        from negative_controls import run_negative_controls

        run_negative_controls(root, fusion, report)
        return 0
    if command == "qualification_report_generation":
        from report_builder import deterministic_payload

        print(json.dumps(deterministic_payload(root, fusion, report), sort_keys=True, separators=(",", ":")))
        return 0
    if command == "isolated_rerun":
        from isolated_rerun import execute_isolated_rerun

        execute_isolated_rerun(root, fusion, report)
        return 0
    raise RuntimeError(command)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--command", required=True)
    parser.add_argument("--mode", choices=("preflight", "execute"), required=True)
    args = parser.parse_args()
    label = os.environ["R26C_Q2_LABEL"]
    expected = spec_for(label)["command"]
    if args.command != expected:
        raise RuntimeError(f"command/label mismatch: {args.command} != {expected}")
    preflight = _environment_preflight(label, include_runner=args.mode == "preflight")
    if args.mode == "preflight":
        print(json.dumps(preflight, sort_keys=True))
        return 0
    if os.environ.get("R26C_Q2_FORMAL") == "1":
        manifest = read_json(Path(os.environ["R26C_Q2_REPORT_ROOT"]) / "COMMAND_ENVIRONMENT_MANIFEST.json")
        assert_runtime_matches(manifest["commands"][label]["runtime_preflight"], preflight)
    return _execute(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
