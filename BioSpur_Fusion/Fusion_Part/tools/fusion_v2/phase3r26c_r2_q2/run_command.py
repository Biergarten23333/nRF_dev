#!/usr/bin/env python3
"""Run one named Q3 command in its frozen, single-layer traced environment."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET

from command_specs import COMMANDS, spec_for
from common import (
    CANONICAL_BRANCH,
    CANONICAL_HEAD,
    CANONICAL_TREE,
    canonical_bytes,
    read_json,
    roots_from_tool,
    sha256_file,
    write_json,
)
from environment_gate import assert_contract_matches


TRACE_MODE = "single_outer_strace_ff_ttt_file_process_plus_sitecustomize_audit"
HARD_LIMIT_BYTES = 512 * 1024 * 1024
EARLY_STOP_BYTES = 384 * 1024 * 1024


def _next_attempt(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    index = 1
    while (root / f"attempt_{index:03d}").exists():
        index += 1
    target = root / f"attempt_{index:03d}"
    target.mkdir()
    return target


def _run(argv: list[str], cwd: Path) -> bytes:
    completed = subprocess.run(argv, cwd=cwd, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(f"contract command failed {argv}: {completed.stderr.decode(errors='replace')}")
    return completed.stdout


def _sealed_files(fusion: Path) -> dict[str, str]:
    roots = (
        fusion / "src/biospur_fusion/heading_anchor_audit_v2",
        fusion / "tests/fusion_v2/phase3r26c_r1",
        fusion / "tests/fusion_v2/phase3r26c_r2",
        fusion / "tools/fusion_v2/phase3r26c_r1",
        fusion / "tools/fusion_v2/phase3r26c_r2",
        fusion / "tools/fusion_v2/phase3r26c_r2_q2",
    )
    files = []
    for root in roots:
        files.extend(path for path in root.rglob("*") if path.is_file() and "__pycache__" not in path.parts)
    return {
        str(path.relative_to(fusion)): sha256_file(path)
        for path in sorted(set(files))
    }


def _environment_contract(label: str, phase: str) -> dict:
    wrapper = Path(__file__).resolve()
    tool_root = wrapper.parent
    worktree, fusion, report = roots_from_tool(wrapper)
    dispatch = tool_root / "command_dispatch.py"
    sitecustomize = tool_root / "sitecustomize.py"
    spec = spec_for(label)
    python = str(Path(sys.executable).resolve())
    execute_argv = [python, "-B", str(dispatch), "--command", spec["command"], "--mode", "execute"]
    actual_argv = [python, "-B", str(dispatch), "--command", spec["command"], "--mode", "preflight" if phase == "preflight" else "execute"]
    pythonpath = [str(tool_root), str(fusion / "tools/fusion_v2/phase3r26c_r2")]
    if spec.get("source_mode") == "clean_base_capsule":
        pythonpath.append(str(report / "capsules/frozen_red_source"))
    pythonpath.extend((str(worktree), str(fusion), str(fusion / "src")))
    branch = _run(["git", "branch", "--show-current"], worktree).decode().strip()
    revisions = _run(["git", "rev-parse", "HEAD", "HEAD^{tree}"], worktree).decode().splitlines()
    identity = [branch, *revisions]
    if identity != [CANONICAL_BRANCH, CANONICAL_HEAD, CANONICAL_TREE]:
        raise RuntimeError(f"canonical identity changed: {identity}")
    registry = _run(["git", "worktree", "list", "--porcelain"], worktree)
    non_fusion_status = _run([
        "git", "status", "--porcelain=v2", "--untracked-files=all", "--", ".",
        ":(exclude)BioSpur_Fusion/Fusion_Part",
        ":(exclude)BioSpur_Fusion/Fusion_Part/**",
    ], worktree)
    sealed = _sealed_files(fusion)
    source_capsule = {}
    if spec.get("source_mode") == "clean_base_capsule":
        capsule = report / "capsules/frozen_red_source"
        source_capsule = {
            str(path.relative_to(capsule)): sha256_file(path)
            for path in sorted(capsule.rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts
        }
    core = {
        "absolute_python_executable": python,
        "python_version": sys.version,
        "argv": execute_argv,
        "cwd": str(worktree),
        "REPO_ROOT": str(worktree),
        "FUSION_ROOT": str(fusion),
        "PYTHONPATH": os.pathsep.join(pythonpath),
        "expected_sys_path_prefix": pythonpath,
        "sitecustomize_origin": str(sitecustomize),
        "sitecustomize_sha256": sha256_file(sitecustomize),
        "import_targets": list(spec["imports"]),
        "pytest_rootdir": str(worktree),
        "collection_targets": list(spec.get("collect", ())),
        "wrapper_path": str(wrapper),
        "wrapper_sha256": sha256_file(wrapper),
        "dispatcher_sha256": sha256_file(dispatch),
        "command_specs_sha256": sha256_file(tool_root / "command_specs.py"),
        "tracing_mode": TRACE_MODE,
        "report_early_stop_bytes": EARLY_STOP_BYTES,
        "report_hard_limit_bytes": HARD_LIMIT_BYTES,
        "canonical_branch": identity[0],
        "canonical_head": identity[1],
        "canonical_tree": identity[2],
        "worktree_registry_sha256": hashlib.sha256(registry).hexdigest(),
        "non_fusion_status_sha256": hashlib.sha256(non_fusion_status).hexdigest(),
        "sealed_file_sha256": sealed,
        "source_capsule_sha256": source_capsule,
        "source_mode": spec.get("source_mode", "canonical_production"),
        "pytest_disable_plugin_autoload": "1",
    }
    core["environment_sha256"] = hashlib.sha256(canonical_bytes(core)).hexdigest()
    return {
        **core,
        "label": label,
        "actual_phase_argv": actual_argv,
        "expected_exit_semantics": spec["exit"],
        "phase": phase,
        "report_root": str(report),
    }


def _parse_junit(path: Path) -> dict:
    if not path.exists():
        return {"present": False, "tests": 0, "passes": 0, "failures": [], "errors": [], "skipped": []}
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    cases = [case for suite in suites for case in suite.findall("testcase")]
    failures = []
    errors = []
    skipped = []
    for case in cases:
        nodeid = f"test_frozen_contract.py::{case.attrib.get('name', '')}"
        failure = case.find("failure")
        error = case.find("error")
        skip = case.find("skipped")
        if failure is not None:
            failures.append({"nodeid": nodeid, "text": (failure.text or "") + (failure.attrib.get("message") or "")})
        if error is not None:
            errors.append({"nodeid": nodeid, "text": (error.text or "") + (error.attrib.get("message") or "")})
        if skip is not None:
            skipped.append(nodeid)
    return {
        "present": True,
        "tests": len(cases),
        "passes": len(cases) - len(failures) - len(errors) - len(skipped),
        "failures": failures,
        "errors": errors,
        "skipped": skipped,
    }


def _short_nodeid(nodeid: str) -> str:
    return "test_frozen_contract.py::" + nodeid.split("::", 1)[1]


def _diagnostic_matches(expected: dict, failure_text: str) -> bool:
    diagnostic = expected.get("diagnostic", {})
    kind = diagnostic.get("kind")
    if kind == "assertion_marker":
        marker = diagnostic.get("text")
        return isinstance(marker, str) and marker in failure_text
    if kind == "pytest_did_not_raise":
        exception = diagnostic.get("exception")
        return (
            isinstance(exception, str)
            and "Failed: DID NOT RAISE" in failure_text
            and exception in failure_text
        )
    return False


def _red_collection_nodeids(report: Path) -> list[str]:
    manifest = report / "COMMAND_ENVIRONMENT_MANIFEST.json"
    if manifest.exists():
        return read_json(manifest)["commands"]["frozen_red"]["runtime_preflight"]["pytest"]["collected_nodeids"]
    attempts = sorted(
        (report / "development/preflight/frozen_red").glob("attempt_*/COMMAND_RESULT.json")
    )
    if not attempts:
        raise RuntimeError("frozen RED comparison requires a qualified collection preflight")
    result = read_json(attempts[-1])
    if not result.get("qualified") or not result.get("runtime_preflight"):
        raise RuntimeError("latest frozen RED collection preflight is not qualified")
    return result["runtime_preflight"]["pytest"]["collected_nodeids"]


def _red_matches(report: Path, junit: dict) -> tuple[bool, dict]:
    fusion = report.parents[3]
    expected = read_json(fusion / "tests/fusion_v2/phase3r26c_r2/expected_red.json")["failures"]
    expected_ids = {row["nodeid"] for row in expected}
    actual_ids = {row["nodeid"] for row in junit.get("failures", [])}
    by_id = {row["nodeid"]: row for row in junit.get("failures", [])}
    diagnostics = {
        row["nodeid"]: _diagnostic_matches(
            row, by_id.get(row["nodeid"], {}).get("text", "")
        )
        for row in expected
    }
    collected = {_short_nodeid(item) for item in _red_collection_nodeids(report)}
    expected_pass = collected - expected_ids
    actual_pass = collected - actual_ids - {row["nodeid"] for row in junit.get("errors", [])} - set(junit.get("skipped", []))
    details = {
        "expected_failure_nodeids": sorted(expected_ids),
        "actual_failure_nodeids": sorted(actual_ids),
        "expected_pass_nodeids": sorted(expected_pass),
        "actual_pass_nodeids": sorted(actual_pass),
        "failure_classification": {row["nodeid"]: row["classification"] for row in expected},
        "semantic_failure_ids": {row["nodeid"]: row["semantic_failure_id"] for row in expected},
        "expected_diagnostics": {row["nodeid"]: row["diagnostic"] for row in expected},
        "diagnostic_matches": diagnostics,
        "exact_failure_set": expected_ids == actual_ids,
        "exact_pass_set": expected_pass == actual_pass,
        "exact_collected_nodeids": len(collected) == junit.get("tests"),
        "tests": junit.get("tests"),
        "passes": junit.get("passes"),
        "error_count": len(junit.get("errors", [])),
        "skip_count": len(junit.get("skipped", [])),
    }
    ok = (
        details["exact_failure_set"]
        and details["exact_pass_set"]
        and details["exact_collected_nodeids"]
        and all(diagnostics.values())
        and not junit.get("errors")
        and not junit.get("skipped")
    )
    return ok, details


def _assert_frozen_environment(label: str, contract: dict, report: Path) -> None:
    manifest_path = report / "COMMAND_ENVIRONMENT_MANIFEST.json"
    seal_path = report / "FORMAL_FREEZE_SEAL.json"
    sentinel = report / "FORMAL_FREEZE_SEALED"
    if not sentinel.exists() or not manifest_path.exists() or not seal_path.exists():
        raise RuntimeError("formal command refused before FORMAL_FREEZE_SEALED")
    seal = read_json(seal_path)
    if seal.get("status") != "FORMAL_FREEZE_SEALED":
        raise RuntimeError("formal freeze seal is not valid")
    frozen = read_json(manifest_path)["commands"][label]
    assert_contract_matches(frozen, contract)


def _report_size(report: Path) -> int:
    completed = subprocess.run(
        ["du", "-x", "-B1", "-s", str(report)],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(completed.stdout.split()[0])


def _preflight_payload(stdout: bytes) -> dict | None:
    lines = [line for line in stdout.decode(errors="replace").splitlines() if line.strip()]
    if not lines:
        return None
    try:
        value = json.loads(lines[-1])
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", choices=tuple(COMMANDS), required=True)
    parser.add_argument("--phase", choices=("preflight", "development", "formal"), required=True)
    args = parser.parse_args()
    contract = _environment_contract(args.label, args.phase)
    worktree = Path(contract["REPO_ROOT"])
    fusion = Path(contract["FUSION_ROOT"])
    report = Path(contract["report_root"])
    starting_size = _report_size(report)
    if starting_size >= EARLY_STOP_BYTES:
        raise RuntimeError("report 384 MiB early-stop reserve reached before command")
    if args.phase == "formal":
        _assert_frozen_environment(args.label, contract, report)
        evidence = report / "raw" / args.label
        if evidence.exists():
            raise RuntimeError(f"formal evidence directory already exists: {evidence}")
        evidence.mkdir(parents=True)
    elif args.phase == "preflight":
        evidence = _next_attempt(report / "development" / "preflight" / args.label)
    else:
        evidence = _next_attempt(report / "development" / "execution" / args.label)
    audit = evidence / "audit.jsonl"
    audit.write_text("", encoding="utf-8")
    tmp_root = report / "scratch/runtime_tmp"
    mpl_root = report / "scratch/mplconfig"
    tmp_root.mkdir(parents=True, exist_ok=True)
    mpl_root.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.update({
        "PYTHONPATH": contract["PYTHONPATH"],
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "MPLBACKEND": "Agg",
        "MPLCONFIGDIR": str(mpl_root),
        "TMPDIR": str(tmp_root),
        "R26C_Q2_AUDIT_LOG": str(audit),
        "R26C_Q2_ACTIVE_ROOT": str(worktree),
        "R26C_Q2_FUSION_ROOT": str(fusion),
        "R26C_Q2_REPORT_ROOT": str(report),
        "R26C_Q2_EVIDENCE_DIR": str(evidence),
        "R26C_Q2_LABEL": args.label,
        "R26C_Q2_ENVIRONMENT_SHA256": contract["environment_sha256"],
        "R26C_Q2_FORMAL": "1" if args.phase == "formal" else "0",
    })
    trace_prefix = evidence / "strace"
    wrapped = [
        "strace", "-ff", "-ttt", "-qq", "-e", "trace=%file,%process",
        "-o", str(trace_prefix), *contract["actual_phase_argv"],
    ]
    start = datetime.now(timezone.utc).isoformat()
    completed = subprocess.run(wrapped, cwd=worktree, env=env, capture_output=True)
    end = datetime.now(timezone.utc).isoformat()
    (evidence / "stdout.txt").write_bytes(completed.stdout)
    (evidence / "stderr.txt").write_bytes(completed.stderr)
    trace_files = sorted(evidence.glob("strace.*"))
    forbidden_fragments = (
        "/biospur_fusion/fusion_part/datasets/", "/biospur_fusion/fusion_part/logs/",
        "17_final_still", "_walk/", "_boxing/", "_golf/", "/vicon/", "/opensense/",
        "/sealed_holdout/", "/tmp/biospur_", "/mnt/nrf_ssd/nrf_dev_worktrees/",
        "/fusion-phase3r26c-r2-psi-free-20260820t124251z/",
        "/mnt/datenbankhdd/biospur_archive/fusion_worktree_cold_",
    )
    forbidden_trace = []
    nested_ptrace = []
    for path in trace_files:
        for line in path.read_text(errors="replace").splitlines():
            lower = line.lower()
            if any(token in lower for token in forbidden_fragments) and any(syscall in lower for syscall in ("open(", "openat(", "openat2(", "creat(")):
                forbidden_trace.append(line)
            if ("strace" in lower or "ptrace(" in lower) and "execve" in lower:
                nested_ptrace.append(line)
    audit_rows = [json.loads(line) for line in audit.read_text().splitlines() if line]
    forbidden_audit = [row for row in audit_rows if row.get("event") == "forbidden_open_blocked"]
    nested_audit = [row for row in audit_rows if row.get("event") == "nested_ptrace_blocked"]
    unauthorized_write = [row for row in audit_rows if row.get("event") == "unauthorized_write_blocked"]
    active_pids = sorted({row["pid"] for row in audit_rows if row.get("event") == "audit_hook_active"})
    junit = _parse_junit(evidence / "junit.xml")
    expected = contract["expected_exit_semantics"]
    semantic_details = None
    if args.phase == "preflight":
        exit_ok = completed.returncode == 0
    elif expected == "zero":
        exit_ok = completed.returncode == 0
    elif expected == "negative_controls":
        semantic_details = {
            "expected_forbidden_audit_events": 1,
            "actual_forbidden_audit_events": len(forbidden_audit),
            "expected_nested_ptrace_audit_events": 1,
            "actual_nested_ptrace_audit_events": len(nested_audit),
        }
        exit_ok = completed.returncode == 0 and len(forbidden_audit) == 1 and len(nested_audit) == 1
    elif expected == "frozen_red":
        red_ok, semantic_details = _red_matches(report, junit)
        exit_ok = completed.returncode == 1 and red_ok
    else:
        raise RuntimeError(expected)
    expected_negative = args.phase in {"development", "formal"} and expected == "negative_controls"
    audit_ok = (
        not forbidden_trace
        and not nested_ptrace
        and not unauthorized_write
        and ((len(forbidden_audit) == 1 and len(nested_audit) == 1) if expected_negative else (not forbidden_audit and not nested_audit))
    )
    post_size = _report_size(report)
    qualified = bool(exit_ok and trace_files and active_pids and audit_ok and post_size < EARLY_STOP_BYTES)
    runtime_preflight = _preflight_payload(completed.stdout) if args.phase == "preflight" else None
    record = {
        "schema": "biospur.phase3r26c_r2_q4.command_result.v1",
        "label": args.label,
        "phase": args.phase,
        "command_environment": contract,
        "runtime_preflight": runtime_preflight,
        "wrapped_argv": wrapped,
        "wrapped_shell_escaped": shlex.join(wrapped),
        "start_utc": start,
        "end_utc": end,
        "exit_code": completed.returncode,
        "expected_exit_semantics": expected,
        "exit_semantics_matched": exit_ok,
        "stdout_sha256": hashlib.sha256(completed.stdout).hexdigest(),
        "stderr_sha256": hashlib.sha256(completed.stderr).hexdigest(),
        "trace_files": [str(path) for path in trace_files],
        "trace_file_sha256": {path.name: sha256_file(path) for path in trace_files},
        "audit_sha256": sha256_file(audit),
        "audit_active_pids": active_pids,
        "forbidden_trace_events": forbidden_trace,
        "forbidden_audit_events": forbidden_audit,
        "nested_ptrace_events": [*nested_ptrace, *nested_audit],
        "unauthorized_write_events": unauthorized_write,
        "junit": junit,
        "semantic_details": semantic_details,
        "report_size_bytes_after": post_size,
        "report_early_stop_bytes": EARLY_STOP_BYTES,
        "report_hard_limit_bytes": HARD_LIMIT_BYTES,
        "qualified": qualified,
    }
    write_json(evidence / "COMMAND_RESULT.json", record)
    if args.phase == "formal" and args.label == "frozen_red":
        write_json(report / "FROZEN_RED_RESULT.json", {
            "schema": "biospur.phase3r26c_r2_q4.frozen_red.v1",
            "status": "PASS" if qualified else "FAIL",
            "source_mode": "git_object_clean_base_capsule",
            "junit": junit,
            "comparison": semantic_details,
            "expected_nonzero_exit": 1,
            "observed_exit": completed.returncode,
            "command_result_sha256": sha256_file(evidence / "COMMAND_RESULT.json"),
        })
    if args.phase == "formal" and args.label == "frozen_green":
        frozen_count = len(read_json(report / "COMMAND_ENVIRONMENT_MANIFEST.json")["commands"]["frozen_green"]["runtime_preflight"]["pytest"]["collected_nodeids"])
        green_ok = qualified and junit["tests"] == frozen_count and junit["passes"] == frozen_count and not junit["failures"] and not junit["errors"] and not junit["skipped"]
        write_json(report / "FROZEN_GREEN_RESULT.json", {
            "schema": "biospur.phase3r26c_r2_q4.frozen_green.v1",
            "status": "PASS" if green_ok else "FAIL",
            "exact_nodeids_unchanged": junit["tests"] == frozen_count,
            "junit": junit,
            "command_result_sha256": sha256_file(evidence / "COMMAND_RESULT.json"),
        })
        qualified = green_ok
    print(json.dumps({
        "label": args.label,
        "phase": args.phase,
        "exit_code": completed.returncode,
        "environment_sha256": contract["environment_sha256"],
        "qualified": qualified,
        "evidence": str(evidence),
    }, sort_keys=True))
    return 0 if qualified else 1


if __name__ == "__main__":
    raise SystemExit(main())
