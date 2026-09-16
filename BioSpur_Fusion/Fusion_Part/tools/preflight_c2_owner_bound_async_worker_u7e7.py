#!/usr/bin/env python3
"""Fixture/reference and resource preflight for U7E7; never opens raw data."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import time
import traceback


ROOT = Path(__file__).resolve().parents[1]
SEALS = {
    ROOT / "logs/c2_owner_bound_async_worker_u7e5_revision_002_20260906T194500Z": "3d3a5d8b70de957b31826c7b6ade8049f3d207896f8054249471dc1873b32db6",
    ROOT / "logs/c2_owner_bound_async_worker_u7e6_20260906T201000Z": "77c92b86d7e294398df94fe3114a0c912be2fb439aec6b7b134beda540b7ef55",
    ROOT / "logs/c2_owner_bound_async_worker_u7e6_action04_20260906T202300Z": "89dcedae8016423f1af1ab2512420d053186d2f707de391414b23b6f72ac1431",
    ROOT / "logs/c2_owner_bound_async_worker_u7e7_resource_revision_002_20260906T205000Z": "d22ca9600de72ee3fa4f547aafb451b2c293ce46f1e14b017e7b939e4cda4a29",
    ROOT / "logs/c2_owner_bound_async_worker_u7e7_action04_revision_003_20260906T221500Z": "7a9984a77b3dc8745bfa69d624ca5dac4a4e645eec523051043c4169ef20a5f8",
}
FILES = (
    ROOT / "src/biospur_fusion/c2_uwb_calibration/direct_body_shadow_ab.py",
    ROOT / "src/biospur_fusion/c2_uwb_root_world/owner_bound_async_worker.py",
    ROOT / "tests/test_c2_owner_bound_async_worker.py",
    ROOT / "tests/test_c2_owner_bound_async_worker_u7e7_runner.py",
    ROOT / "tools/run_c2_owner_bound_async_worker_u7e6_action04.py",
    ROOT / "tools/run_c2_owner_bound_async_worker_u7e7_action04.py",
    Path(__file__).resolve(),
)
SUITES = (
    "tests/test_c2_owner_bound_async_worker.py", "tests/test_c2_direct_body_shadow_ab.py",
    "tests/test_c2_owner_bound_async_worker_u7e7_runner.py",
    "tests/test_c2_root_worker_event_codec.py", "tests/test_c2_async_root_worker.py",
    "tests/test_c2_root_worker_owner_wiring.py", "tests/test_c2_causal_update_guard.py",
    "tests/test_c2_causal_update_transaction.py",
)
CAP_KIB = 300_000


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def verify(path: Path, digest: str) -> None:
    if sha(path / "SHA256SUMS") != digest:
        raise RuntimeError(f"seal digest mismatch: {path}")
    for line in (path / "SHA256SUMS").read_text().splitlines():
        expected, relative = line.split("  ", 1)
        if sha(path / relative) != expected:
            raise RuntimeError(f"seal member mismatch: {relative}")


def proc_memory() -> dict[str, int]:
    values = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:", "VmHWM:")):
            key, value, _ = line.split()
            values[key[:-1].lower() + "_kib"] = int(value)
    return values


def benchmark_child(path: Path) -> int:
    import numpy as np
    from biospur_fusion.c2_uwb_root_world.owner_bound_async_worker import AsyncOwnerWorker
    from test_c2_owner_bound_async_worker import PublicReference, assert_same, sequence

    owner, items = sequence()
    direct = PublicReference(owner)
    expected = [direct.process(item) for item in items]
    preworker = proc_memory()
    worker = AsyncOwnerWorker(owner, capacity=64)
    for item in items:
        worker.submit(item)
    actual, final = worker.close_and_collect(len(items))
    for left, right in zip(actual, expected):
        assert_same(left, right)
    result = {"events": len(items), "groups": sum(x.kind == "UWB" for x in actual),
        "parity": True, "preworker": preworker, "final": proc_memory(),
        "rusage_self_kib": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss),
        "rusage_children_kib": int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss),
        "worker_kib": int(final["rss"]), "queue_drained": bool(final["qsize"] == 0 and not final["alive"]),
        "preworker_headroom_kib": CAP_KIB - int(preworker["vmhwm_kib"]),
        "raw_opened": False, "HXX_opened": False}
    path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0


def seal(path: Path) -> str:
    members = sorted(x for x in path.iterdir() if x.is_file() and x.name != "SHA256SUMS")
    (path / "SHA256SUMS").write_text("".join(f"{sha(x)}  {x.name}\n" for x in members))
    return sha(path / "SHA256SUMS")


def external_peak(path: Path) -> int:
    for line in path.read_text().splitlines():
        if "Maximum resident set size" in line:
            return int(line.rsplit(":", 1)[1])
    raise RuntimeError("external RSS unavailable")


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--output", type=Path); parser.add_argument("--benchmark-child", type=Path)
    args = parser.parse_args()
    if args.benchmark_child is not None:
        return benchmark_child(args.benchmark_child)
    if args.output is None or args.output.exists():
        raise FileExistsError(args.output)
    args.output.mkdir(parents=True); started = time.perf_counter()
    command = ("timeout --signal=TERM --kill-after=5s 300s env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 "
        "MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1 PYTHONPATH=src:tools:. .venv-v0/bin/python "
        f"tools/preflight_c2_owner_bound_async_worker_u7e7.py --output {args.output}")
    (args.output / "COMMAND.txt").write_text(command + "\n")
    base = {"schema": "biospur.c2.u7e7.preflight.v1", "raw_opened": False, "action04_opened": False,
        "HXX_opened": False, "scientific_pass": False, "calibrated_R": False, "online_ready": False,
        "production_ready": False, "prior_blocked_non_promoted": True,
        "bound_seals": {str(k.relative_to(ROOT)): v for k, v in SEALS.items()}}
    try:
        for path, digest in SEALS.items(): verify(path, digest)
        before = {str(path.relative_to(ROOT)): sha(path) for path in FILES}
        env = {**os.environ, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1", "PYTHONPATH": "src:tools:tests:."}
        test_command = ["/usr/bin/time", "-v", "-o", str(args.output / "TEST_TIME.txt"), str(ROOT / ".venv-v0/bin/python"), "-m", "pytest", "-q", *SUITES]
        test = subprocess.run(test_command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=180)
        (args.output / "TEST_STDOUT.txt").write_text(test.stdout); (args.output / "TEST_STDERR.txt").write_text(test.stderr)
        if test.returncode: raise RuntimeError(f"focused tests failed: {test.returncode}")
        match = re.search(r"(\d+) passed", test.stdout)
        if match is None: raise RuntimeError("focused test count unavailable")
        test_count = int(match.group(1))
        child = args.output / "BENCHMARK.json"; bench_command = ["/usr/bin/time", "-v", "-o", str(args.output / "BENCHMARK_TIME.txt"),
            str(ROOT / ".venv-v0/bin/python"), str(Path(__file__).resolve()), "--benchmark-child", str(child)]
        bench = subprocess.run(bench_command, cwd=ROOT, env=env, text=True, capture_output=True, timeout=60)
        (args.output / "BENCHMARK_STDOUT.txt").write_text(bench.stdout); (args.output / "BENCHMARK_STDERR.txt").write_text(bench.stderr)
        if bench.returncode: raise RuntimeError(f"benchmark child failed: {bench.returncode}")
        metrics = json.loads(child.read_text()); external = external_peak(args.output / "BENCHMARK_TIME.txt")
        after = {str(path.relative_to(ROOT)): sha(path) for path in FILES}
        gates = {"tests": True, "source_unchanged": before == after, "parity": metrics["parity"],
            "queue_drained": metrics["queue_drained"], "absolute_rss": max(external, metrics["rusage_self_kib"], metrics["worker_kib"]) < CAP_KIB,
            "preworker_headroom": metrics["preworker_headroom_kib"] >= 20_000,
            "no_raw_hxx": not metrics["raw_opened"] and not metrics["HXX_opened"]}
        status = "READY_FOR_MONITOR_U7E7_FIXTURE_RESOURCE_REVIEW" if all(gates.values()) else "BLOCKED_U7E7_FIXTURE_RESOURCE"
        result = {**base, "status": status, "gates": gates, "test_count": test_count, "source_hashes": before,
            "resources": {**metrics, "external_maximum_rss_kib": external}, "wall_s": time.perf_counter() - started}
        (args.output / "RESULT.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        (args.output / "REPORT.md").write_text(f"# U7E7 fixture/reference preflight\n\nStatus: `{status}`. No raw/action04/HXX input was opened. Prior action failure remains non-promoted.\n")
        if sum(x.stat().st_size for x in args.output.iterdir() if x.is_file()) >= 20_000_000: raise RuntimeError("evidence cap")
        digest = seal(args.output); print(json.dumps({"status": status, "seal_sha256": digest}, sort_keys=True)); return 0 if all(gates.values()) else 2
    except BaseException as exc:
        failure = {**base, "status": "BLOCKED_U7E7_FIXTURE_RESOURCE", "failure": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc(), "wall_s": time.perf_counter() - started}
        (args.output / "FAILURE.json").write_text(json.dumps(failure, indent=2, sort_keys=True) + "\n"); digest = seal(args.output)
        print(json.dumps({"status": failure["status"], "seal_sha256": digest, "failure": str(exc)}, sort_keys=True)); return 2


if __name__ == "__main__":
    raise SystemExit(main())
