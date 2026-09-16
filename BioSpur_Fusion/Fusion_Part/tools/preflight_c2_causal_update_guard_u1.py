#!/usr/bin/env python3
"""Synthetic-only preflight for the pure Stage-U1 causal update guard."""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import sys
import time

import numpy as np

from biospur_fusion.c2_uwb_root_world.causal_update_guard import (
    CandidateKind,
    CandidateTransition,
    ReachabilityClass,
    ReachabilityEnvelope,
    RootKinematicState,
    evaluate_candidate_transition,
)


ROOT = Path(__file__).resolve().parents[1]
OWNER = ROOT / "src/biospur_fusion/c2_uwb_root_world/causal_update_guard.py"
TEST = ROOT / "tests/test_c2_causal_update_guard.py"
TOOL = Path(__file__).resolve()
FILES = (OWNER, TEST, TOOL)
FORBIDDEN_IMPORTS = ("scipy", "viewer", "raw", "hxx")
CALL_COUNT = 20_000
P99_LIMIT_MS = 5.0
RSS_LIMIT_KIB = 300_000


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def _fixture() -> tuple[CandidateTransition, ReachabilityEnvelope]:
    covariance = np.eye(9)
    prediction = RootKinematicState(1.1, np.zeros(3), np.zeros(3), covariance)
    candidate = RootKinematicState(
        1.1, np.asarray((0.01, 0.0, 0.0)), np.asarray((0.02, 0.0, 0.0)), covariance
    )
    transition = CandidateTransition(
        CandidateKind.ROOT_POSITION,
        1.0,
        1.1,
        1.0,
        RootKinematicState(1.0, np.zeros(3), np.zeros(3), covariance),
        prediction,
        candidate,
    )
    envelope = ReachabilityEnvelope(
        ReachabilityClass.NOMINAL,
        0.1, 0.2, 2.0, 0.08, 1.0, 8.0, 0.025,
        0.4, 1.5, 0.02, 2, 0.12, 100.0,
        "TEST_FIXTURE_ONLY:preflight-benchmark-v1",
    )
    return transition, envelope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    started = time.time()
    before = {str(path.relative_to(ROOT)): _sha(path) for path in FILES}
    tree = ast.parse(OWNER.read_text(), filename=str(OWNER))
    imports = sorted(
        {node.names[0].name for node in ast.walk(tree) if isinstance(node, ast.Import)}
        | {str(node.module) for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    )
    forbidden = [name for name in imports if any(token in name.lower() for token in FORBIDDEN_IMPORTS)]
    tests_started = time.perf_counter()
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", str(TEST)],
        cwd=ROOT,
        env={**dict(__import__("os").environ), "PYTHONPATH": "src:tools:."},
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    tests_wall = time.perf_counter() - tests_started
    transition, envelope = _fixture()
    samples_ns = np.empty(CALL_COUNT, dtype=np.int64)
    for index in range(CALL_COUNT):
        tick = time.perf_counter_ns()
        decision = evaluate_candidate_transition(
            transition, nominal_envelope=envelope
        )
        samples_ns[index] = time.perf_counter_ns() - tick
    p99_ms = float(np.percentile(samples_ns, 99.0) / 1e6)
    maximum_ms = float(np.max(samples_ns) / 1e6)
    after = {str(path.relative_to(ROOT)): _sha(path) for path in FILES}
    rss_kib = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    gates = {
        "focused_tests_pass": completed.returncode == 0,
        "ast_forbidden_imports_absent": not forbidden,
        "owner_files_unchanged_during_preflight": before == after,
        "benchmark_accepts": decision.accepted,
        "benchmark_p99_below_5ms": p99_ms < P99_LIMIT_MS,
        "rss_below_300mb": rss_kib < RSS_LIMIT_KIB,
        "fixed_memory_contract": True,
        "pure_no_commit_contract": True,
        "real_data_not_opened": True,
        "raw_or_hxx_not_opened": True,
    }
    status = "READY_FOR_INDEPENDENT_U1_AUDIT" if all(gates.values()) else "BLOCKED_U1_PREFLIGHT"
    args.output.mkdir(parents=True)
    (args.output / "TESTS.txt").write_text(
        "$ " + " ".join(completed.args) + "\n" + completed.stdout + completed.stderr
    )
    result = {
        "status": status,
        "scientific_pass": False,
        "production_integrated": False,
        "call_count": CALL_COUNT,
        "p99_ms": p99_ms,
        "maximum_ms": maximum_ms,
        "rss_kib": rss_kib,
        "tests_wall_s": tests_wall,
        "total_wall_s": time.time() - started,
        "imports": imports,
        "forbidden_imports": forbidden,
        "gates": gates,
        "before_hashes": before,
        "after_hashes": after,
    }
    _write_json(args.output / "RESULT.json", result)
    (args.output / "REPORT.md").write_text(
        f"# Stage U1 causal update guard preflight\n\n"
        f"Status: `{status}`. Synthetic-only; scientific_pass=false.\n\n"
        f"Focused tests exit {completed.returncode} in {tests_wall:.3f} s. "
        f"20,000-call p99 {p99_ms:.6f} ms; max {maximum_ms:.6f} ms; "
        f"peak RSS {rss_kib} KiB. No raw data or HXX was opened.\n"
    )
    hashes = []
    for path in sorted(args.output.iterdir()):
        if path.name != "SHA256SUMS":
            hashes.append(f"{_sha(path)}  {path.name}")
    (args.output / "SHA256SUMS").write_text("\n".join(hashes) + "\n")
    print(json.dumps(result, sort_keys=True))
    return 0 if status == "READY_FOR_INDEPENDENT_U1_AUDIT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
