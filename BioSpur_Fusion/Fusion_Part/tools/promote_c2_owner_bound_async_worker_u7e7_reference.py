#!/usr/bin/env python3
"""Pointer-only promotion of the qualified U7E7 current-contract reference."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import resource
import subprocess
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "logs/c2_owner_bound_async_worker_u7e7_reference_promotion_preflight_20260907T001500Z"
PREFLIGHT_SEAL = "724e863f6d0a62ed356b30a06a9627d20e6f90b254f2c3011ea0ef946e8ae3ab"
REFERENCE = PREFLIGHT / "CURRENT_CONTRACT_REFERENCE.jsonl"
REFERENCE_SHA256 = "7ae933e119e28f8c1fde319d316d4ce33e462b2852b53e65701008ab71c7de17"
MANIFEST = PREFLIGHT / "REFERENCE_MANIFEST.json"
MANIFEST_SHA256 = "c37beb315ee7fa2104f443f0c0295f3762ae3c8b68dcb23a19810d0115bb92cb"
PREFLIGHT_TOOL = ROOT / "tools/preflight_c2_owner_bound_async_worker_u7e7_reference_promotion.py"
PREFLIGHT_TOOL_SHA256 = "36e45459878f9ee2e636c1d266769eb79ecc000f0bf937d9c3bdf642b172840f"
RSS_CAP_KIB = 300_000
EVIDENCE_CAP_BYTES = 5_000_000


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_seal(path: Path, expected: str) -> None:
    if sha256(path / "SHA256SUMS") != expected:
        raise RuntimeError("promotion preflight seal digest mismatch")
    for line in (path / "SHA256SUMS").read_text().splitlines():
        digest, relative = line.split("  ", 1)
        if sha256(path / relative) != digest:
            raise RuntimeError(f"promotion preflight member mismatch: {relative}")


def seal(path: Path) -> str:
    members = sorted(item for item in path.iterdir() if item.is_file() and item.name != "SHA256SUMS")
    (path / "SHA256SUMS").write_text("".join(f"{sha256(item)}  {item.name}\n" for item in members))
    return sha256(path / "SHA256SUMS")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True); started = time.perf_counter()
    command = ("timeout --signal=TERM --kill-after=5s 60s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"tools/promote_c2_owner_bound_async_worker_u7e7_reference.py --output {args.output}")
    (args.output / "COMMAND.txt").write_text(command + "\n")
    base = {"schema":"biospur.c2.u7e7.reference-promotion.v1", "command":command,
        "promotion_class":"CURRENT_CONTRACT_DIAGNOSTIC_REFERENCE", "raw_opened":False,
        "dataset_opened":False, "action04_capture_opened":False, "HXX_opened":False,
        "async_worker_used":False, "solver_run":False, "estimator_changed":False,
        "candidate_promoted":False, "calibrated_R":False, "scientific_pass":False,
        "product_ready":False, "production_ready":False,
        "limits":{"wall_s":60,"rss_kib":RSS_CAP_KIB,"evidence_bytes":EVIDENCE_CAP_BYTES}}
    try:
        verify_seal(PREFLIGHT, PREFLIGHT_SEAL)
        if sha256(REFERENCE) != REFERENCE_SHA256:
            raise RuntimeError("current-contract reference hash mismatch")
        if sha256(MANIFEST) != MANIFEST_SHA256:
            raise RuntimeError("current-contract reference manifest hash mismatch")
        if sha256(PREFLIGHT_TOOL) != PREFLIGHT_TOOL_SHA256:
            raise RuntimeError("promotion preflight tool hash mismatch")
        manifest = json.loads(MANIFEST.read_text())
        if (manifest.get("candidate_promoted") is not False
                or manifest.get("reference_sha256") != REFERENCE_SHA256
                or manifest.get("counts") != {"groups":41,"root_states":41,"nodes":410,
                    "node_weight_vectors":410,"weights":3280}):
            raise RuntimeError("promotion preflight manifest qualification mismatch")
        tests = [".venv-v0/bin/python", "-m", "pytest", "-q",
            "tests/test_c2_owner_bound_async_worker.py",
            "tests/test_c2_owner_bound_async_worker_u7e7_runner.py"]
        completed = subprocess.run(tests, cwd=ROOT, text=True, capture_output=True, timeout=55)
        (args.output / "TEST_STDOUT.txt").write_text(completed.stdout)
        (args.output / "TEST_STDERR.txt").write_text(completed.stderr)
        if completed.returncode != 0:
            raise RuntimeError(f"focused no-raw tests failed: {completed.returncode}")
        pointer = {"schema":"biospur.c2.u7e7.current-contract-reference-pointer.v1",
            "reference_path":str(REFERENCE.relative_to(ROOT)), "reference_sha256":REFERENCE_SHA256,
            "immutable_preflight_path":str(PREFLIGHT.relative_to(ROOT)),
            "immutable_preflight_seal":PREFLIGHT_SEAL,
            "reference_manifest_path":str(MANIFEST.relative_to(ROOT)),
            "reference_manifest_sha256":MANIFEST_SHA256,
            "promotion_class":"CURRENT_CONTRACT_DIAGNOSTIC_REFERENCE",
            "candidate_promoted":True, "historical_numeric_references":{
                "old_u3_numeric":"HISTORICAL_PRE_CONTEXT_TAIL_NON_PROMOTED",
                "old_u5b_numeric":"HISTORICAL_PRE_CONTEXT_TAIL_NON_PROMOTED"},
            "calibrated_R":False, "scientific_pass":False, "product_ready":False,
            "production_ready":False}
        (args.output / "REFERENCE_POINTER.json").write_text(json.dumps(pointer,indent=2,sort_keys=True)+"\n")
        wall = time.perf_counter()-started
        parent_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        child_rss = int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)
        gates = {"preflight_seal":True,"reference_hash":True,"manifest_hash":True,
            "preflight_tool_hash":True,"manifest_qualification":True,"pointer_only":True,
            "focused_no_raw_tests":True,"wall":wall<60,
            "rss":parent_rss<RSS_CAP_KIB and child_rss<RSS_CAP_KIB}
        if not all(gates.values()):
            raise RuntimeError(f"reference promotion gate failed: {gates}")
        test_count_match = re.search(r"(\d+) passed", completed.stdout)
        if test_count_match is None:
            raise RuntimeError("focused test count missing")
        result = {**base,"status":"CURRENT_CONTRACT_DIAGNOSTIC_REFERENCE_PROMOTED",
            "candidate_promoted":True,"gates":gates,"pointer":pointer,"wall_s":wall,
            "parent_rss_kib":parent_rss,"test_child_rss_kib":child_rss,
            "test_count":int(test_count_match.group(1))}
        (args.output / "RESULT.json").write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
        (args.output / "REPORT.md").write_text(
            "# U7E7 current-contract diagnostic reference promotion\n\n"
            "The immutable preflight reference is promoted by pointer only. The reference file was not copied, moved, or rewritten. "
            "This promotion applies only to `CURRENT_CONTRACT_DIAGNOSTIC_REFERENCE`; scientific, product, production, and calibrated-R claims remain false.\n")
        if (args.output / "CURRENT_CONTRACT_REFERENCE.jsonl").exists():
            raise RuntimeError("pointer-only promotion copied the reference")
        if sum(item.stat().st_size for item in args.output.iterdir() if item.is_file()) >= EVIDENCE_CAP_BYTES:
            raise RuntimeError("promotion evidence cap exceeded")
        digest = seal(args.output)
        print(json.dumps({"status":result["status"],"seal_sha256":digest,"wall_s":wall,
            "parent_rss_kib":parent_rss,"test_child_rss_kib":child_rss},sort_keys=True))
        return 0
    except BaseException as exc:
        failure = {**base,"status":"BLOCKED_CURRENT_CONTRACT_REFERENCE_PROMOTION",
            "failure":f"{type(exc).__name__}: {exc}","traceback":traceback.format_exc(),
            "wall_s":time.perf_counter()-started,"parent_rss_kib":int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
            "child_rss_kib":int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss)}
        (args.output / "FAILURE.json").write_text(json.dumps(failure,indent=2,sort_keys=True)+"\n")
        digest = seal(args.output)
        print(json.dumps({"status":failure["status"],"failure":failure["failure"],"seal_sha256":digest},sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
